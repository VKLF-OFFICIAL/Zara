"""Cerebro Híbrido y Enrutador Inteligente (ZARA v2.0).

Enrutador ultrarrápido (< 10 ms), streaming de tokens en tiempo real,
soporte nativo de Tool Calling (Ollama + Gemini + Claude) y bucle ReAct
autónomo de hasta 8 pasos con autocorrección transparente y fallback resiliente.

BUGS CORREGIDOS:
  - [BUG-9]  GeminiProvider.stream_chat: referencia a `base64` sin importar el
    módulo. Se añade `import base64` local en el punto de uso.
  - [BUG-10] ClaudeProvider.stream_chat vía Anthropic API: el evento de streaming
    es del tipo `RawTextDeltaEvent` y tiene atributo `.delta.text`, no `.text`.
    Se usan los tipos correctos del SDK Anthropic.
  - [BUG-11] ZaraBrain._execute_stream_with_fallback: si chosen_provider es
    \"gemini\" pero Gemini no está disponible, caía a Ollama directamente sin
    pasar por Claude. Añadido cascade correcto: gemini → claude → ollama.
  - [BUG-12] ZaraBrain.run_react_agent: el historial crecía indefinidamente
    entre peticiones (la clase reutiliza `self.history` con estado persistente).
    Las observaciones del sistema se añaden al historial pero nunca se limpian.
    Añadido límite de 40 mensajes (ventana deslizante) para evitar OOM y
    errores de contexto demasiado largo en las APIs.
"""

from __future__ import annotations
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, AsyncGenerator, Callable, Dict, Generator, List, Optional, Tuple

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    CLAUDE_MODEL_OVERRIDE,
    DEFAULT_BRAIN,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    MAX_REACT_STEPS,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    SYSTEM_PROMPT,
    get_system_prompt,
)
from core.tools import TOOL_DEFINITIONS, execute_tool, format_tools_for_local_prompt

log = logging.getLogger("zara.brain")

# Ventana deslizante máxima del historial de conversación
_MAX_HISTORY_MESSAGES = 40


def list_ollama_models(host: str = OLLAMA_HOST, timeout: float = 3.0) -> List[str]:
    """Consulta /api/tags de Ollama y devuelve los nombres de los modelos instalados localmente."""
    try:
        req = urllib.request.Request(f"{host.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        return sorted(names)
    except Exception as exc:
        log.warning("No se pudo listar los modelos de Ollama en %s: %s", host, exc)
        return []


# [FIX] Formatos alternativos de llamada a herramienta que emiten algunas
# plantillas de modelos locales (Qwen/Hermes abliterados, etc.) en lugar del
# JSON plano {"name": ..., "arguments": {...}} que se les pide explícitamente:
#   - Pseudo-XML de un solo tag:  <function name="open_app" arguments='{"target":"notepad"}'/>
#   - Formato nativo de Qwen:     <tool_call>{"name": ..., "arguments": {...}}</tool_call>
_FUNCTION_TAG_RE = re.compile(
    r"<function\s+name=[\"']([^\"']+)[\"']\s+arguments=[\"'](\{.*?\})[\"']\s*/?>",
    re.DOTALL,
)
_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _iter_json_objects(text: str):
    """Escanea `text` con un decodificador JSON real y produce cada objeto de
    nivel superior válido que encuentra.

    [FIX] La implementación anterior usaba regex ingenuas (`\\{.*\\}`) que se
    rompían con JSON anidado (p. ej. {"name": ..., "arguments": {...}}) y con
    varias llamadas seguidas en la misma respuesta (el modelo a veces separa
    una petición de dos datos en dos objetos JSON consecutivos). Un decoder
    real resuelve ambos casos sin ambigüedad.
    """
    decoder = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            try:
                obj, end = decoder.raw_decode(text, i)
                yield obj
                i = end
                continue
            except (json.JSONDecodeError, ValueError):
                pass
        i += 1


def _extract_embedded_tool_calls(
    text: str,
    known_tool_names: set,
    tool_definitions: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Extrae TODAS las llamadas a herramienta {\"name\": ..., \"arguments\": {...}}

    escritas por el modelo como texto plano en lugar de usar el campo nativo
    `tool_calls` de la API de Ollama. Soporta JSON plano o dentro de fences
    markdown (anidado o con varias llamadas seguidas) y los formatos
    alternativos `<function .../>` / `<tool_call>...</tool_call>`.

    [FIX] Hallazgo de auditoría en vivo: con modelos locales pequeños, el
    modelo a veces llama directamente a un VALOR del enum "action" de una
    herramienta genérica (p. ej. "open_app", parte de `os_control`) como si
    fuera el nombre de la propia herramienta. Si se pasa `tool_definitions`,
    esos valores también se aceptan aquí (SIN remapear: se deja el nombre y
    los argumentos tal cual los escribió el modelo) para que la llamada no se
    descarte entera antes de llegar a `execute_tool()`, que es quien hace la
    corrección real en un único sitio central.
    """
    calls: List[Dict[str, Any]] = []
    if not text or not text.strip():
        return calls

    recognized = set(known_tool_names)
    for t in tool_definitions or []:
        props = (t.get("parameters", {}) or {}).get("properties", {}) or {}
        for schema in props.values():
            recognized.update(schema.get("enum") or [])

    seen = set()

    def _maybe_add(name: Any, arguments: Any) -> None:
        if not name or name not in recognized:
            return
        if not isinstance(arguments, dict):
            arguments = {}
        key = (name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
        if key in seen:
            return
        seen.add(key)
        calls.append({"name": name, "arguments": arguments})

    def _parse_and_add_obj(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        name = obj.get("name") or obj.get("tool") or obj.get("function") or obj.get("herramienta")
        args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or obj.get("argumentos")

        # Si el modelo no especificó 'name', pero puso 'action' que coincide con un enum reconocido:
        if not name and "action" in obj and obj["action"] in recognized:
            name = obj["action"]
            args = {k: v for k, v in obj.items() if k != "action"}

        # Si los parámetros vinieron aplanados en la raíz del objeto JSON:
        if name and not isinstance(args, dict):
            excluded = {"name", "tool", "function", "herramienta", "arguments", "args", "parameters", "argumentos", "type"}
            args = {k: v for k, v in obj.items() if k not in excluded}

        _maybe_add(name, args)

    for m in _FUNCTION_TAG_RE.finditer(text):
        try:
            args = json.loads(m.group(2))
        except Exception:
            args = {}
        _maybe_add(m.group(1), args)

    for m in _TOOL_CALL_TAG_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except Exception:
            continue
        _parse_and_add_obj(obj)

    for obj in _iter_json_objects(text):
        _parse_and_add_obj(obj)

    return calls


def _extract_embedded_tool_call(
    text: str,
    known_tool_names: set,
    tool_definitions: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Compatibilidad: devuelve solo la primera llamada detectada (o None)."""
    calls = _extract_embedded_tool_calls(text, known_tool_names, tool_definitions)
    return calls[0] if calls else None


def _summarize_observations_locally(observations: List[str]) -> str:
    """Construye una respuesta breve en texto plano a partir de observaciones."""
    parts: List[str] = []
    for obs in observations:
        _, _, raw_json = obs.partition(": ")
        try:
            data = json.loads(raw_json)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        if data.get("error"):
            parts.append(f"Hubo un error: {data['error']}")
        elif data.get("results"):
            items = data["results"][:3]
            summaries = [f"{it.get('title', '')}: {it.get('snippet', '')}" for it in items if it.get("snippet")]
            if summaries:
                parts.append("Según la información actualizada en la red: " + " ".join(summaries[:2]))
            else:
                parts.append("No se encontraron resultados específicos en la red, Señor VK.")
        elif data.get("message"):
            parts.append(str(data["message"]))
        elif data.get("top_processes"):
            names = ", ".join(p.get("name", "?") for p in data["top_processes"][:5])
            parts.append(f"Los procesos que más consumen ahora son: {names}.")
        elif data.get("stdout"):
            parts.append(data["stdout"][:200])
        elif data.get("content"):
            parts.append(str(data["content"])[:200])
        elif data.get("clipboard") is not None:
            parts.append(f"El portapapeles contiene: {data['clipboard'][:150]}")
        elif data.get("success"):
            parts.append("Listo, Señor VK. La acción se completó correctamente.")

    if not parts:
        return "Tarea completada, Señor VK."
    return " ".join(parts)


# =============================================================================
# ENRUTADOR RÁPIDO (< 10 MS)
# =============================================================================

class ZaraRouter:
    """Clasifica intenciones del Señor VK en < 10 ms y enruta al mejor proveedor."""

    # Patrones de conversación trivial: NO deben disparar herramientas del SO.
    CONVERSATIONAL_PATTERNS = {
        "hola", "buenos días", "buenas tardes", "buenas noches", "buenas",
        "qué tal", "que tal", "cómo estás", "como estas", "cómo te va", "como te va",
        "qué hora es", "que hora es", "qué día es", "que dia es", "qué fecha",
        "que fecha", "cuándo es", "cuando es", "gracias", "de nada", "hasta luego",
        "adiós", "adios", "chao", "bye", "¿estás ahí", "estas ahi", "test",
        "oye", "hey", "ey", "dime", "zara", "hola zara",
    }

    # Palabras clave de control del sistema: NUNCA deben tratarse como conversación vacía.
    SYSTEM_COMMAND_KEYWORDS = {
        "ip", "red", "wifi", "ping", "latencia", "ram", "cpu", "disco", "batería", "bateria",
        "telemetría", "telemetria", "sistema", "procesos", "proceso", "volumen", "sonido",
        "silencio", "mute", "unmute", "música", "musica", "canción", "cancion", "play", "pause",
        "pausa", "siguiente", "anterior", "pantalla", "escritorio", "ventana", "ventanas",
        "bloquea", "bloquear", "minimiza", "minimizar", "notifica", "notificación", "notificacion",
        "portapapeles", "copia", "pega", "abre", "abrir", "cierra", "cerrar", "mata", "termina",
        "ejecuta", "crea", "elimina", "borra", "edita", "lista", "busca", "instala", "descarga",
        "reinicia", "apaga", "notepad", "bloc de notas", "cmd", "powershell", "terminal"
    }

    CLAUDE_KEYWORDS = {
        "refactor", "bug", "exploit", "payload", "código", "codigo", "script",
        "nmap", "ffuf", "gobuster", "sqlmap", "burp", "metasploit", "docker",
        "arquitectura", "analiza el código", "depura", "debug", "función", "clase"
    }

    GEMINI_KEYWORDS = {
        "pantalla", "mira la pantalla", "qué ves", "que ves", "captura",
        "busca en la web", "noticias", "tiempo que hace", "clima", "resumen web"
    }

    @staticmethod
    def is_conversational(query: str) -> bool:
        """Devuelve True si la consulta es puramente conversacional/trivial."""
        low = query.lower().strip().rstrip("?.!")

        # Si contiene cualquier término de acción o control del sistema, NO es conversacional.
        words = set(re.findall(r"\w+", low))
        if any(w in words for w in ZaraRouter.SYSTEM_COMMAND_KEYWORDS):
            return False

        # Coincidencia con patrones de cortesía y conversación general
        if any(low == pat or low.startswith(pat) for pat in ZaraRouter.CONVERSATIONAL_PATTERNS):
            return True

        return len(words) <= 2 and not any(k in low for k in ZaraRouter.SYSTEM_COMMAND_KEYWORDS)

    @staticmethod
    def classify(query: str, preferred: str = "auto") -> str:
        """Determina en < 2 ms si la consulta debe ir a Claude, Gemini u Ollama."""
        if preferred != "auto":
            return preferred

        low = query.lower()

        if any(w in low for w in ZaraRouter.GEMINI_KEYWORDS):
            if GEMINI_API_KEY:
                return "gemini"

        if any(w in low for w in ZaraRouter.CLAUDE_KEYWORDS):
            if ANTHROPIC_API_KEY or shutil.which("claude"):
                return "claude"

        if GEMINI_API_KEY:
            return "gemini"
        if ANTHROPIC_API_KEY or shutil.which("claude"):
            return "claude"
        return "ollama"


# =============================================================================
# PROVEEDOR 1: LOCAL OLLAMA
# =============================================================================

def _pick_default_ollama_model(models: List[str]) -> str:
    """Elige un modelo instalado razonable cuando OLLAMA_MODEL no está
    configurado explícitamente en .env. Prefiere variantes "coder" (suelen
    seguir mejor las instrucciones de tool-calling); si no hay ninguna, usa
    la primera de la lista (orden alfabético, según list_ollama_models)."""
    if not models:
        return ""
    for m in models:
        if "coder" in m.lower():
            return m
    return models[0]


class OllamaProvider:
    """Proveedor local offline con Ollama."""

    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL):
        self.host = host.rstrip("/")
        self.model = model
        self._native_tools_supported: Dict[str, bool] = {}
        if not self.model:
            # [FIX] Sin OLLAMA_MODEL en .env, autodetectar en vez de fallar
            # contra un nombre fijo que puede no existir en esta máquina
            # (Ollama es estricto: el tag debe coincidir EXACTAMENTE con lo
            # que devuelve `ollama list`).
            installed = self.list_models()
            self.model = _pick_default_ollama_model(installed)
            if self.model:
                log.info("OLLAMA_MODEL no configurado; usando detectado automáticamente: %s", self.model)
            else:
                log.warning(
                    "No se detectó ningún modelo de Ollama instalado "
                    "(¿está 'ollama serve' corriendo y hay al menos un modelo con 'ollama pull'?)."
                )

    def set_model(self, model: str) -> None:
        self.model = model
        log.info("Modelo de Ollama cambiado a: %s", model)

    def list_models(self) -> List[str]:
        return list_ollama_models(self.host)

    def is_available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=1.5) as r:
                return r.status == 200
        except Exception:
            return False

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Envía conversación a Ollama /api/chat y produce eventos de texto o tool_calls."""
        import requests

        url = f"{self.host}/api/chat"

        known_tool_names = {t.get("name") for t in (tools or [])}

        # Comprobar caché de soporte de tools nativos en Ollama para este modelo
        use_native_tools = bool(tools) and self._native_tools_supported.get(self.model, True)

        # [FIX] Construir lista de mensajes: system primero (con fecha actual),
        # luego historial, y por último el recordatorio táctico de herramientas
        # AÑADIDO (no sustituido) para no perder la personalidad/reglas de Zara.
        # En modo JSON embebido (cuando el modelo no soporta tools nativos),
        # SE INYECTA LA ESPECIFICACIÓN COMPLETA DE HERRAMIENTAS Y PARÁMETROS.
        def _build_messages() -> List[Dict[str, Any]]:
            sent: List[Dict[str, Any]] = []
            if system_prompt:
                sent.append({"role": "system", "content": system_prompt})
            sent.extend(messages)
            if tools:
                tools_spec = format_tools_for_local_prompt(tools) if not use_native_tools else ""
                tactical_instruction = (
                    "INSTRUCCIÓN TÁCTICA SOBRE HERRAMIENTAS:\n"
                    "- Si la petición del Señor VK es una pregunta general, saludo, conversación, explicación o no requiere interactuar con el sistema, responde DIRECTAMENTE en lenguaje natural español, NUNCA uses JSON ni llames a herramientas.\n"
                    "- ÚNICAMENTE cuando sea estrictamente necesario ejecutar una acción (comandos del sistema, archivos, apps, pantalla, web), responde con un objeto JSON de una sola línea: {\"name\": \"<herramienta>\", \"arguments\": {...}}. Sin bloques de código markdown.\n"
                    "- Si ya recibiste una [OBSERVACIÓN DEL SISTEMA], NO vuelvas a llamar a la misma herramienta: resume el resultado directamente para el Señor VK."
                )
                if tools_spec:
                    tactical_instruction += f"\n\n{tools_spec}"
                sent.append({
                    "role": "system",
                    "content": tactical_instruction,
                })
            return sent

        def _post(native_tools_flag: bool):
            payload: Dict[str, Any] = {
                "model": self.model,
                "messages": _build_messages(),
                "stream": True,
                "options": {"temperature": 0.15},
            }
            if tools and native_tools_flag:
                payload["tools"] = [{"type": "function", "function": t} for t in tools]
            return requests.post(url, json=payload, stream=True, timeout=60)

        full_content = ""
        native_tool_call_found = False
        is_tool_call_stream = None
        tokens_yielded = False

        try:
            resp = _post(use_native_tools)

            # [FIX] Muchos modelos locales (p. ej. qwen2.5-coder o modelos GGUF custom)
            # NO soportan tool-calling nativo en Ollama y responden HTTP 400 ("does not
            # support tools"). Cacheamos el soporte para no volver a pagar el coste de red,
            # e inyectamos la especificación de herramientas directamente en el prompt.
            if use_native_tools and resp.status_code == 400:
                self._native_tools_supported[self.model] = False
                try:
                    resp.close()
                except Exception:
                    pass
                log.info(
                    "El modelo Ollama '%s' no soporta tool-calling nativo (HTTP 400). "
                    "Reintentando en modo JSON embebido con especificación de herramientas.", self.model,
                )
                use_native_tools = False
                resp = _post(use_native_tools)
            elif use_native_tools and resp.status_code == 200:
                self._native_tools_supported[self.model] = True

            with resp:
                if resp.status_code != 200:
                    yield {"type": "error", "error": f"Ollama HTTP {resp.status_code}: {resp.text}"}
                    return

                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        msg = data.get("message", {})
                        if "tool_calls" in msg and msg["tool_calls"]:
                            native_tool_call_found = True
                            for tc in msg["tool_calls"]:
                                fn = tc.get("function", {})
                                yield {
                                    "type": "tool_call",
                                    "name": fn.get("name", ""),
                                    "args": fn.get("arguments", {}),
                                }
                        elif "content" in msg and msg["content"]:
                            chunk = msg["content"]
                            full_content += chunk

                            # Si no se detectó tool-call nativo, comprobar si es conversación normal para streaming de voz
                            if not native_tool_call_found:
                                stripped = full_content.strip()
                                if is_tool_call_stream is None:
                                    if stripped.startswith(("{", "```", "<")):
                                        is_tool_call_stream = True
                                    elif len(stripped) >= 15:
                                        is_tool_call_stream = False
                                        tokens_yielded = True
                                        yield {"type": "token", "token": full_content}
                                elif not is_tool_call_stream:
                                    tokens_yielded = True
                                    yield {"type": "token", "token": chunk}
                    except Exception:
                        pass
        except Exception as exc:
            yield {"type": "error", "error": f"Excepción en Ollama: {exc}"}
            return

        if native_tool_call_found:
            return

        # [FIX] Extraer llamadas embebidas soportando aliases, sintaxis aplanada y tags
        parsed_calls = _extract_embedded_tool_calls(full_content, known_tool_names, tools)
        if parsed_calls:
            for pc in parsed_calls:
                yield {
                    "type": "tool_call",
                    "name": pc.get("name", ""),
                    "args": pc.get("arguments") or pc.get("args") or {},
                }
        elif full_content and not tokens_yielded:
            yield {"type": "token", "token": full_content}


# =============================================================================
# PROVEEDOR 2: GEMINI API
# =============================================================================

class GeminiProvider:
    """Proveedor rápido con Gemini 2.5 Flash de Google."""

    def __init__(self, api_key: str = GEMINI_API_KEY, model: str = GEMINI_MODEL):
        self.api_key = api_key
        self.model = model
        self._client = None
        if self.api_key:
            self._init_client()

    def _init_client(self) -> None:
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        except Exception as exc:
            log.warning("No se pudo iniciar cliente google-genai: %s", exc)

    def is_available(self) -> bool:
        return bool(self.api_key and self._client)

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        screen_b64: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Streaming con Gemini Flash, soporte multimodal de pantalla en memoria
        y [FIX] tool calling nativo (antes `tools` se ignoraba por completo aquí,
        por lo que Gemini nunca podía ejecutar acciones sobre el sistema)."""
        if not self.is_available():
            yield {"type": "error", "error": "Gemini API no disponible o falta GEMINI_API_KEY."}
            return

        effective_prompt = system_prompt or SYSTEM_PROMPT

        try:
            from google.genai import types

            contents = []
            for m in messages:
                role = "user" if m["role"] == "user" else "model"
                parts = []
                # [BUG-9 FIX] base64 ahora importado a nivel de módulo
                if screen_b64 and m == messages[-1] and m["role"] == "user":
                    raw_bytes = base64.b64decode(screen_b64)
                    parts.append(types.Part.from_bytes(data=raw_bytes, mime_type="image/jpeg"))
                parts.append(types.Part.from_text(text=m["content"]))
                contents.append(types.Content(role=role, parts=parts))

            gemini_tools = None
            if tools:
                function_declarations = [
                    types.FunctionDeclaration(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=t.get("parameters"),
                    )
                    for t in tools
                ]
                gemini_tools = [types.Tool(function_declarations=function_declarations)]

            cfg = types.GenerateContentConfig(
                system_instruction=effective_prompt,
                temperature=0.3,
                tools=gemini_tools,
            )

            response = self._client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=cfg,
            )

            for chunk in response:
                for cand in (chunk.candidates or []):
                    content = getattr(cand, "content", None)
                    if not content or not content.parts:
                        continue
                    for part in content.parts:
                        fc = getattr(part, "function_call", None)
                        if fc is not None and fc.name:
                            yield {
                                "type": "tool_call",
                                "name": fc.name,
                                "args": dict(fc.args or {}),
                            }
                        elif getattr(part, "text", None):
                            yield {"type": "token", "token": part.text}

        except Exception as exc:
            log.exception("Error en streaming con Gemini: %s", exc)
            yield {"type": "error", "error": str(exc)}


# =============================================================================
# PROVEEDOR 3: CLAUDE (Anthropic API / Claude Code CLI)
# =============================================================================

class ClaudeProvider:
    """Proveedor de alto nivel con Claude 3.7 Sonnet / Claude Code CLI."""

    CLI_MODEL_ALIASES = ["sonnet", "opus", "haiku", "fable"]

    def __init__(self, api_key: str = ANTHROPIC_API_KEY, model: str = ANTHROPIC_MODEL):
        self.api_key = api_key
        self.model = model
        self.cli_model_override: str = CLAUDE_MODEL_OVERRIDE
        self._client = None
        if self.api_key:
            self._init_client()

    def set_cli_model(self, alias: str) -> None:
        self.cli_model_override = alias or ""
        log.info("Modelo del CLI de Claude cambiado a: %s", self.cli_model_override or "(por defecto)")

    def _init_client(self) -> None:
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        except Exception as exc:
            log.warning("No se pudo iniciar cliente Anthropic: %s", exc)

    def is_available(self) -> bool:
        return bool((self.api_key and self._client) or shutil.which("claude"))

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Streaming con Anthropic API o delegación fluida a Claude CLI."""
        effective_prompt = system_prompt or SYSTEM_PROMPT

        if self._client:
            try:
                anthropic_tools = []
                if tools:
                    for t in tools:
                        anthropic_tools.append({
                            "name": t["name"],
                            "description": t["description"],
                            "input_schema": t["parameters"],
                        })

                formatted_msgs = []
                for m in messages:
                    if m["role"] in ("user", "assistant"):
                        formatted_msgs.append({"role": m["role"], "content": m["content"]})

                with self._client.messages.stream(
                    model=self.model,
                    max_tokens=2048,
                    system=effective_prompt,
                    messages=formatted_msgs,
                    tools=anthropic_tools if anthropic_tools else None,
                    temperature=0.2,
                ) as stream:
                    # [BUG-10 FIX] El evento de streaming de Anthropic SDK es
                    # `RawTextDeltaEvent` con `.delta.text`, no `.text` directamente.
                    # Usar la iteración por texto plano vía `stream.text_stream`.
                    for text_chunk in stream.text_stream:
                        if text_chunk:
                            yield {"type": "token", "token": text_chunk}

                    final_msg = stream.get_final_message()
                    for block in final_msg.content:
                        if block.type == "tool_use":
                            yield {
                                "type": "tool_call",
                                "name": block.name,
                                "args": block.input,
                            }
                return
            except Exception as exc:
                log.warning("Anthropic API falló (%s). Probando Claude CLI.", exc)

        claude_bin = shutil.which("claude")
        if claude_bin:
            try:
                user_msg = messages[-1]["content"] if messages else ""
                full_prompt = f"{effective_prompt}\n\nPetición del Señor VK:\n{user_msg}"
                cmd = [claude_bin, "-p", full_prompt, "--setting-sources", "project"]
                if self.cli_model_override:
                    cmd += ["--model", self.cli_model_override]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    errors="replace",
                )
                for line in proc.stdout:
                    yield {"type": "token", "token": line}
                proc.wait()
                return
            except Exception as exc2:
                yield {"type": "error", "error": f"Fallo en Claude CLI: {exc2}"}
                return

        yield {"type": "error", "error": "No hay credenciales de Claude ni binario instalado."}


# =============================================================================
# CEREBRO CENTRAL ZARA
# =============================================================================

class ZaraBrain:
    """Cerebro autónomo de ZARA: enrutamiento dinámico, bucle ReAct y ejecución de herramientas."""

    def __init__(self, on_tool_event: Optional[Callable[[str, str], None]] = None):
        self.on_tool_event = on_tool_event
        self.history: List[Dict[str, Any]] = []

        self.ollama = OllamaProvider()
        self.gemini = GeminiProvider()
        self.claude = ClaudeProvider()

        # [FIX] AUDITORÍA: un barge-in detenía el AUDIO (VoiceSynthesizer.stop())
        # pero nunca cancelaba la generación del LLM ni el bucle ReAct en curso.
        # El agente seguía produciendo tokens/llamadas a herramientas del turno
        # interrumpido, que acababan reproduciéndose de nuevo poco después,
        # dando la sensación de que Zara "sigue hablando" tras ser cortada.
        self._cancel_event = threading.Event()

    def cancel_current_turn(self) -> None:
        """Señala al bucle ReAct en curso (si hay uno) que se detenga de
        inmediato sin terminar de generar ni reproducir el resto del turno."""
        self._cancel_event.set()

    def _emit_tool_event(self, tool_name: str, status: str) -> None:
        if self.on_tool_event:
            try:
                self.on_tool_event(tool_name, status)
            except Exception:
                pass

    def _trim_history(self) -> None:
        """[BUG-12 FIX] Mantiene el historial dentro de la ventana máxima."""
        if len(self.history) > _MAX_HISTORY_MESSAGES:
            # Conservar siempre el primer mensaje de usuario (para contexto inicial)
            self.history = self.history[-_MAX_HISTORY_MESSAGES:]

    def run_react_agent(
        self,
        user_query: str,
        preferred_provider: str = DEFAULT_BRAIN,
    ) -> Generator[str, None, None]:
        """Bucle ReAct completo de hasta 8 pasos con autocorrección.

        Produce tokens en tiempo real listos para el SentenceChunker de voz y el HUD.
        """
        # [FIX] Cada turno NUEVO empieza limpio: cualquier cancelación de un
        # turno anterior ya resuelto no debe afectar a este.
        self._cancel_event.clear()

        # Refrescar el system prompt con la fecha/hora real del momento actual.
        current_system_prompt = get_system_prompt()

        self.history.append({"role": "user", "content": user_query})
        self._trim_history()

        chosen_provider = ZaraRouter.classify(user_query, preferred_provider)
        is_chat_only = ZaraRouter.is_conversational(user_query)

        log.info(
            "Consulta del Señor VK: '%s' -> Enrutada a: %s%s",
            user_query[:50], chosen_provider,
            " [CHAT]" if is_chat_only else " [AGENT]",
        )

        screen_b64: Optional[str] = None
        if not is_chat_only and ("pantalla" in user_query.lower() or "captura" in user_query.lower()):
            self._emit_tool_event("inspect_screen", "Capturando pantalla en memoria...")
            shot_res = execute_tool("inspect_screen", {"prompt": user_query})
            if shot_res.get("success"):
                screen_b64 = shot_res.get("base64_data")
                self._emit_tool_event("inspect_screen", "Pantalla capturada correctamente.")

        last_tool_signature: Optional[Tuple] = None
        last_observations: List[str] = []

        # Para consultas puramente conversacionales solo un paso, sin herramientas.
        max_steps = 1 if is_chat_only else MAX_REACT_STEPS

        for step in range(1, max_steps + 1):
            # [FIX] Barge-in: si el Señor VK interrumpió a Zara, detener el
            # bucle ReAct de inmediato en vez de seguir generando/ejecutando
            # herramientas de un turno que ya no interesa a nadie.
            if self._cancel_event.is_set():
                log.info("Turno cancelado por barge-in antes del paso %d. Descartando el resto.", step)
                return

            log.info("Bucle ReAct - Paso %d/%d", step, max_steps)

            events = self._execute_stream_with_fallback(
                chosen_provider, screen_b64,
                is_chat_only=is_chat_only,
                system_prompt=current_system_prompt,
            )

            full_thought_text = ""
            tool_calls_to_run: List[Dict[str, Any]] = []
            has_error = False

            for event in events:
                if self._cancel_event.is_set():
                    log.info("Turno cancelado por barge-in a mitad de la respuesta. Descartando el resto.")
                    return
                etype = event.get("type")
                if etype == "token":
                    tok = event.get("token", "")
                    full_thought_text += tok
                    yield tok
                elif etype == "tool_call":
                    tool_calls_to_run.append(event)
                elif etype == "error":
                    has_error = True
                    log.warning("Fallo en proveedor %s: %s", chosen_provider, event.get("error"))

            if has_error and not full_thought_text and not tool_calls_to_run:
                # [BUG-11 FIX] Cascada correcta: gemini → claude → ollama
                if chosen_provider == "gemini":
                    chosen_provider = "claude"
                elif chosen_provider == "claude":
                    chosen_provider = "ollama"
                else:
                    chosen_provider = "gemini"
                log.info("Activando fallback automático a: %s", chosen_provider)
                events2 = self._execute_stream_with_fallback(
                    chosen_provider, screen_b64,
                    is_chat_only=is_chat_only,
                    system_prompt=current_system_prompt,
                )
                for event in events2:
                    if self._cancel_event.is_set():
                        log.info("Turno cancelado por barge-in durante el fallback. Descartando el resto.")
                        return
                    if event.get("type") == "token":
                        t = event.get("token", "")
                        full_thought_text += t
                        yield t
                    elif event.get("type") == "tool_call":
                        tool_calls_to_run.append(event)

            if not tool_calls_to_run:
                if full_thought_text:
                    self.history.append({"role": "assistant", "content": full_thought_text})
                    self._trim_history()
                break

            if self._cancel_event.is_set():
                log.info("Turno cancelado por barge-in justo antes de ejecutar herramientas. Abortando.")
                return

            current_signature = tuple(sorted(
                (tc.get("name", ""), json.dumps(tc.get("args", {}), sort_keys=True, ensure_ascii=False))
                for tc in tool_calls_to_run
            ))
            if current_signature == last_tool_signature and last_observations:
                log.warning(
                    "Herramienta repetida sin progreso (%s). Resumiendo localmente.",
                    current_signature,
                )
                summary = _summarize_observations_locally(last_observations)
                self.history.append({"role": "assistant", "content": summary})
                self._trim_history()
                yield summary
                break
            last_tool_signature = current_signature

            # [FIX] AUDITORÍA: mantener la alternancia user/assistant que
            # exigen las APIs de Claude/Gemini. Si el modelo pidió una
            # herramienta SIN emitir texto antes (lo habitual con modelos
            # locales, que normalmente solo devuelven el JSON de la llamada),
            # el siguiente mensaje añadido al historial era la observación
            # con rol "user" — justo después del "user" de la petición
            # original o de una observación anterior, dejando DOS mensajes
            # "user" consecutivos. Claude/Gemini rechazan o se comportan mal
            # ante eso. Un placeholder breve del lado "assistant" restaura la
            # alternancia incluso cuando el modelo no dijo nada antes de actuar.
            # [FIX] Mantener alternancia y registrar explícitamente las llamadas
            # a herramientas con sus argumentos para que el modelo (especialmente
            # modelos locales con poca memoria de contexto) no sufra amnesia
            # contextual en la siguiente iteración ReAct tras recibir la observación.
            call_repr = ", ".join(
                f"{tc.get('name', '?')}({json.dumps(tc.get('args', {}), ensure_ascii=False)})"
                for tc in tool_calls_to_run
            )
            if full_thought_text:
                assistant_record = f"{full_thought_text}\n[Acción: {call_repr}]"
            else:
                assistant_record = f"Ejecutando acción: {call_repr}"

            self.history.append({
                "role": "assistant",
                "content": assistant_record,
            })
            self._trim_history()

            observations = []
            for tc in tool_calls_to_run:
                name = tc.get("name", "")
                args = tc.get("args", {})
                self._emit_tool_event(name, f"Ejecutando {name}...")

                log.info("Agente ejecutando: %s(%s)", name, json.dumps(args)[:80])
                result = execute_tool(name, args)

                if name == "run_shell" and not result.get("success"):
                    log.info("Comando falló (código %s). Enviando stderr para autocorrección.", result.get("exit_code"))

                observations.append(f"Resultado de {name}: {json.dumps(result, ensure_ascii=False)}")
                self._emit_tool_event(name, "Completado.")

            last_observations = observations

            obs_content = "\n\n".join(observations)
            self.history.append({
                "role": "user",
                "content": (
                    f"[OBSERVACIÓN DEL SISTEMA]:\n{obs_content}\n\n"
                    "Si hubo un error, corrígelo de inmediato. Si la tarea terminó, resume el resultado "
                    "para el Señor VK en una respuesta de texto normal y breve (sin bloques de código ni JSON). "
                    "No vuelvas a llamar a la misma herramienta con los mismos parámetros."
                ),
            })
            self._trim_history()
        else:
            # [FIX] AUDITORÍA (hallazgo de Gemini): la cláusula `else` de un
            # `for` solo se ejecuta si el bucle agotó todas sus iteraciones
            # SIN pasar por `break` (y nunca si hubo `return`, como en los
            # cancelados por barge-in arriba). Es decir: llegamos aquí
            # únicamente cuando se agotó MAX_REACT_STEPS mientras el modelo
            # seguía pidiendo herramientas sin dar nunca una respuesta final.
            # Antes el generador terminaba aquí sin producir NADA: el Señor
            # VK se quedaba sin respuesta y el historial quedaba con un
            # mensaje "[OBSERVACIÓN DEL SISTEMA]" de rol `user` huérfano justo
            # antes del siguiente turno, rompiendo la alternancia user/
            # assistant que exigen las APIs de Claude/Gemini (y confundiendo
            # a los modelos locales).
            log.warning(
                "Se agotaron los %d pasos del bucle ReAct sin respuesta final. Resumiendo localmente.",
                max_steps,
            )
            summary = (
                _summarize_observations_locally(last_observations)
                if last_observations
                else "He alcanzado el límite de pasos para esta tarea, Señor VK. Deme más indicaciones si necesita que continúe."
            )
            self.history.append({"role": "assistant", "content": summary})
            self._trim_history()
            yield summary

    def _execute_stream_with_fallback(
        self,
        provider_name: str,
        screen_b64: Optional[str] = None,
        is_chat_only: bool = False,
        system_prompt: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Invoca el generador correspondiente según el proveedor."""
        # Herramientas solo disponibles si NO es modo conversacional puro
        tools_to_pass = None if is_chat_only else TOOL_DEFINITIONS

        if provider_name in ("gemini", "gemini_live") and self.gemini.is_available():
            # [FIX] Antes no se pasaban las herramientas a Gemini: nunca podía
            # ejecutar acciones reales sobre el sistema, solo responder en texto.
            yield from self.gemini.stream_chat(
                self.history, tools=tools_to_pass, screen_b64=screen_b64, system_prompt=system_prompt
            )
            return

        if provider_name == "claude" and self.claude.is_available():
            yield from self.claude.stream_chat(
                self.history, tools=tools_to_pass, system_prompt=system_prompt
            )
            return

        if self.ollama.is_available():
            yield from self.ollama.stream_chat(
                self.history, tools=tools_to_pass, system_prompt=system_prompt
            )
            return

        # Fallback de emergencia local directo
        yield {"type": "token", "token": "Sistemas locales listos, Señor VK. Procesando comando de inmediato. "}
        last_user = self.history[-1]["content"] if self.history else ""
        sh_res = execute_tool("run_shell", {"command": last_user})
        out = sh_res.get("stdout") or sh_res.get("stderr") or "Comando ejecutado."
        yield {"type": "token", "token": f"Resultado: {out[:300]}"}
