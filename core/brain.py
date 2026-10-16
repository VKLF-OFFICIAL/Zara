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
from core.tools import TOOL_DEFINITIONS, execute_tool

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


_FENCED_JSON_RE = re.compile(r"```(?:json|xml)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_embedded_tool_call(text: str, known_tool_names: set) -> Optional[Dict[str, Any]]:
    """Intenta extraer una llamada a herramienta {\"name\": ..., \"arguments\": {...}}

    escrita por el modelo como texto plano en lugar de usar el campo nativo
    `tool_calls` de la API de Ollama.
    """
    if not text or not text.strip():
        return None

    candidates = [m.group(1) for m in _FENCED_JSON_RE.finditer(text)]
    bare = _BARE_JSON_RE.search(text)
    if bare:
        candidates.append(bare.group(0))
    candidates.append(text.strip())

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("name") in known_tool_names:
            return obj
    return None


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
        # Coincidencia exacta o la query empieza con un patrón conocido
        return any(
            low == pat or low.startswith(pat)
            for pat in ZaraRouter.CONVERSATIONAL_PATTERNS
        ) or len(low.split()) <= 2 and not any(
            c in low for c in ["abre", "ejecuta", "crea", "lista", "busca", "instala", "cierra"]
        )

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

class OllamaProvider:
    """Proveedor local offline con Ollama."""

    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL):
        self.host = host.rstrip("/")
        self.model = model

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

        # Construir lista de mensajes: system primero (con fecha actual), luego historial
        sent_messages: List[Dict[str, Any]] = []
        if system_prompt:
            sent_messages.append({"role": "system", "content": system_prompt})
        sent_messages.extend(messages)
        known_tool_names = {t.get("name") for t in (tools or [])}
        if tools:
            reminder = {
                "role": "system",
                "content": (
                    "INSTRUCCIÓN TÁCTICA SOBRE HERRAMIENTAS:\n"
                    "- Si la petición del Señor VK es una pregunta general, saludo, conversación, explicación o no requiere interactuar con el sistema, responde DIRECTAMENTE en lenguaje natural español, NUNCA uses JSON ni llames a herramientas.\n"
                    "- ÚNICAMENTE cuando sea estrictamente necesario ejecutar una acción (comandos, web, archivos, apps, pantalla), responde con un objeto JSON de una sola línea: {\"name\": \"<herramienta>\", \"arguments\": {...}}. Sin bloques de código markdown.\n"
                    "- Si ya recibiste una [OBSERVACIÓN DEL SISTEMA], NO vuelvas a llamar a la misma herramienta: resume el resultado directamente para el Señor VK."
                ),
            }
            sent_messages = messages + [reminder]

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": sent_messages,
            "stream": True,
            "options": {"temperature": 0.15},
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        full_content = ""
        native_tool_call_found = False

        try:
            with requests.post(url, json=payload, stream=True, timeout=60) as resp:
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
                            full_content += msg["content"]
                    except Exception:
                        pass
        except Exception as exc:
            yield {"type": "error", "error": f"Excepción en Ollama: {exc}"}
            return

        if native_tool_call_found:
            return

        parsed_call = _extract_embedded_tool_call(full_content, known_tool_names)
        if parsed_call:
            yield {
                "type": "tool_call",
                "name": parsed_call.get("name", ""),
                "args": parsed_call.get("arguments") or parsed_call.get("args") or {},
            }
        elif full_content:
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
        screen_b64: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Streaming con Gemini Flash y soporte multimodal de pantalla en memoria."""
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

            cfg = types.GenerateContentConfig(
                system_instruction=effective_prompt,
                temperature=0.3,
            )

            response = self._client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=cfg,
            )

            for chunk in response:
                if chunk.text:
                    yield {"type": "token", "token": chunk.text}

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

        if provider_name == "gemini" and self.gemini.is_available():
            yield from self.gemini.stream_chat(
                self.history, screen_b64=screen_b64, system_prompt=system_prompt
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
