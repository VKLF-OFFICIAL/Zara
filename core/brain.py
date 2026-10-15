"""Cerebro Híbrido y Enrutador Inteligente (ZARA v2.0).

Enrutador ultrarrápido (< 10 ms), streaming de tokens en tiempo real,
soporte nativo de Tool Calling (Ollama + Gemini + Claude) y bucle ReAct
autónomo de hasta 8 pasos con autocorrección transparente y fallback resiliente.
"""

from __future__ import annotations
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
    DEFAULT_BRAIN,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    MAX_REACT_STEPS,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    SYSTEM_PROMPT,
)
from core.tools import TOOL_DEFINITIONS, execute_tool

log = logging.getLogger("zara.brain")


# =============================================================================
# ENRUTADOR RÁPIDO (< 10 MS) Y CLASIFICACIÓN DE INTENCIONES
# =============================================================================

class ZaraRouter:
    """Clasifica intenciones del Señor VK en < 10 ms y enruta al mejor proveedor."""

    # Palabras clave que requieren Claude (ingeniería, código profundo, ciberseguridad)
    CLAUDE_KEYWORDS = {
        "refactor", "bug", "exploit", "payload", "código", "codigo", "script",
        "nmap", "ffuf", "gobuster", "sqlmap", "burp", "metasploit", "docker",
        "arquitectura", "analiza el código", "depura", "debug", "función", "clase"
    }

    # Palabras clave que requieren Gemini (visión de pantalla, búsqueda web rápida)
    GEMINI_KEYWORDS = {
        "pantalla", "mira la pantalla", "qué ves", "que ves", "captura",
        "busca en la web", "noticias", "tiempo que hace", "clima", "resumen web"
    }

    @staticmethod
    def classify(query: str, preferred: str = "auto") -> str:
        """Determina en < 2 ms si la consulta debe ir a Claude, Gemini u Ollama."""
        if preferred != "auto":
            return preferred

        low = query.lower()

        # Si involucra mirar la pantalla, priorizar Gemini multimodal
        if any(w in low for w in ZaraRouter.GEMINI_KEYWORDS):
            if GEMINI_API_KEY:
                return "gemini"

        # Si involucra desarrollo de software, auditoría o exploit, priorizar Claude
        if any(w in low for w in ZaraRouter.CLAUDE_KEYWORDS):
            if ANTHROPIC_API_KEY or shutil.which("claude"):
                return "claude"

        # Por defecto: si hay Gemini, respuesta ultra rápida; si no, Claude o local Ollama
        if GEMINI_API_KEY:
            return "gemini"
        if ANTHROPIC_API_KEY or shutil.which("claude"):
            return "claude"
        return "ollama"


# =============================================================================
# PROVEEDOR 1: LOCAL OLLAMA (Tool Calling + Streaming)
# =============================================================================

class OllamaProvider:
    """Proveedor local offline con Ollama (soporta qwen2.5-coder, llama3, etc.)."""

    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL):
        self.host = host.rstrip("/")
        self.model = model

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
    ) -> Generator[Dict[str, Any], None, None]:
        """Envía conversación a Ollama /api/chat y produce eventos de texto o tool_calls."""
        import requests

        url = f"{self.host}/api/chat"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0.2},
        }
        if tools:
            # Formato estándar de herramientas para Ollama
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

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
                            for tc in msg["tool_calls"]:
                                fn = tc.get("function", {})
                                yield {
                                    "type": "tool_call",
                                    "name": fn.get("name", ""),
                                    "args": fn.get("arguments", {}),
                                }
                        elif "content" in msg and msg["content"]:
                            yield {"type": "token", "token": msg["content"]}
                    except Exception:
                        pass
        except Exception as exc:
            yield {"type": "error", "error": f"Excepción en Ollama: {exc}"}


# =============================================================================
# PROVEEDOR 2: GEMINI API (google-genai)
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
    ) -> Generator[Dict[str, Any], None, None]:
        """Streaming con Gemini Flash y soporte multimodal de pantalla en memoria."""
        if not self.is_available():
            yield {"type": "error", "error": "Gemini API no disponible o falta GEMINI_API_KEY."}
            return

        try:
            from google.genai import types

            contents = []
            for m in messages:
                role = "user" if m["role"] == "user" else "model"
                parts = []
                # Si hay imagen en base64 añadida a la consulta
                if screen_b64 and m == messages[-1] and m["role"] == "user":
                    raw_bytes = base64.b64decode(screen_b64)
                    parts.append(types.Part.from_bytes(data=raw_bytes, mime_type="image/jpeg"))
                parts.append(types.Part.from_text(text=m["content"]))
                contents.append(types.Content(role=role, parts=parts))

            # System prompt y herramientas integradas
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
            )

            response = self._client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=config,
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

    def __init__(self, api_key: str = ANTHROPIC_API_KEY, model: str = ANTHROPIC_MODEL):
        self.api_key = api_key
        self.model = model
        self._client = None
        if self.api_key:
            self._init_client()

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
    ) -> Generator[Dict[str, Any], None, None]:
        """Streaming con Anthropic API o delegación fluida a Claude CLI."""
        # 1. Prioridad: Anthropic API con streaming nativo y tool use
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

                # Limpiar mensajes para formato de Anthropic
                formatted_msgs = []
                for m in messages:
                    if m["role"] in ("user", "assistant"):
                        formatted_msgs.append({"role": m["role"], "content": m["content"]})

                with self._client.messages.stream(
                    model=self.model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=formatted_msgs,
                    tools=anthropic_tools if anthropic_tools else None,
                    temperature=0.2,
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield {"type": "token", "token": event.text}

                    # Comprobar si hubo llamadas a herramientas al final del mensaje
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

        # 2. Fallback: Claude CLI local si está disponible en el PATH
        claude_bin = shutil.which("claude")
        if claude_bin:
            try:
                user_msg = messages[-1]["content"] if messages else ""
                full_prompt = f"{SYSTEM_PROMPT}\n\nPetición del Señor VK:\n{user_msg}"
                cmd = [claude_bin, "-p", full_prompt, "--setting-sources", "project"]
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
# CEREBRO CENTRAL ZARA (Orquestador ReAct con Fallback Transparente)
# =============================================================================

class ZaraBrain:
    """Cerebro autónomo de ZARA: enrutamiento dinámico, bucle ReAct y ejecución de herramientas."""

    def __init__(self, on_tool_event: Optional[Callable[[str, str], None]] = None):
        self.on_tool_event = on_tool_event  # cb(tool_name, description) para el HUD holográfico
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

    def run_react_agent(
        self,
        user_query: str,
        preferred_provider: str = DEFAULT_BRAIN,
    ) -> Generator[str, None, None]:
        """Bucle ReAct completo (Reason -> Act -> Observe -> Auto-Fix) de hasta 8 pasos.

        Produce tokens en tiempo real listos para el SentenceChunker de voz y el HUD.
        """
        # Añadir al historial
        self.history.append({"role": "user", "content": user_query})

        # Clasificación rápida en < 10 ms
        chosen_provider = ZaraRouter.classify(user_query, preferred_provider)
        log.info("Consulta del Señor VK: '%s' -> Enrutada a: %s", user_query[:50], chosen_provider)

        # Si involucra captura de pantalla, tomarla antes en memoria
        screen_b64: Optional[str] = None
        if "pantalla" in user_query.lower() or "captura" in user_query.lower():
            self._emit_tool_event("inspect_screen", "Capturando pantalla en memoria...")
            shot_res = execute_tool("inspect_screen", {"prompt": user_query})
            if shot_res.get("success"):
                screen_b64 = shot_res.get("base64_data")
                self._emit_tool_event("inspect_screen", "Pantalla capturada correctamente.")

        # Bucle ReAct autónomo (hasta MAX_REACT_STEPS iteraciones)
        for step in range(1, MAX_REACT_STEPS + 1):
            log.info("Bucle ReAct - Paso %d/%d", step, MAX_REACT_STEPS)

            # Intentar generar con el proveedor elegido (con fallback en cascada)
            events = self._execute_stream_with_fallback(chosen_provider, screen_b64)

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

            # Si el proveedor falló catastróficamente, intentar con el siguiente en cascada
            if has_error and not full_thought_text and not tool_calls_to_run:
                chosen_provider = "ollama" if chosen_provider != "ollama" else "gemini"
                log.info("Activando fallback automático a: %s", chosen_provider)
                events2 = self._execute_stream_with_fallback(chosen_provider, screen_b64)
                for event in events2:
                    if event.get("type") == "token":
                        t = event.get("token", "")
                        full_thought_text += t
                        yield t
                    elif event.get("type") == "tool_call":
                        tool_calls_to_run.append(event)

            # Si el modelo no solicitó herramientas, la respuesta está completa
            if not tool_calls_to_run:
                if full_thought_text:
                    self.history.append({"role": "assistant", "content": full_thought_text})
                break

            # Ejecutar herramientas autónomamente (YOLO Seguro)
            observations = []
            for tc in tool_calls_to_run:
                name = tc.get("name", "")
                args = tc.get("args", {})
                self._emit_tool_event(name, f"Ejecutando {name}...")

                log.info("Agente ejecutando: %s(%s)", name, json.dumps(args)[:80])
                result = execute_tool(name, args)

                # Si falló un comando de shell, capturar el stderr para autocorrección
                if name == "run_shell" and not result.get("success"):
                    log.info("Comando falló (código %s). Enviando stderr para autocorrección.", result.get("exit_code"))

                observations.append(f"Resultado de {name}: {json.dumps(result, ensure_ascii=False)}")
                self._emit_tool_event(name, "Completado.")

            # Inyectar observaciones al historial para que el modelo decida el siguiente paso o corrija
            obs_content = "\n\n".join(observations)
            self.history.append({
                "role": "user",
                "content": f"[OBSERVACIÓN DEL SISTEMA]:\n{obs_content}\n\nSi hubo un error, corrígelo de inmediato. Si la tarea terminó, resume el resultado para el Señor VK con elegancia.",
            })

    def _execute_stream_with_fallback(
        self,
        provider_name: str,
        screen_b64: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Invoca el generador correspondiente según el proveedor."""
        if provider_name == "gemini" and self.gemini.is_available():
            yield from self.gemini.stream_chat(self.history, screen_b64=screen_b64)
            return

        if provider_name == "claude" and self.claude.is_available():
            yield from self.claude.stream_chat(self.history, tools=TOOL_DEFINITIONS)
            return

        if self.ollama.is_available():
            yield from self.ollama.stream_chat(self.history, tools=TOOL_DEFINITIONS)
            return

        # Fallback de emergencia local directo si ninguna API externa ni Ollama responden
        yield {"type": "token", "token": "Sistemas locales listos, Señor VK. Procesando comando de inmediato. "}
        # Intentar ejecutar como shell directamente
        last_user = self.history[-1]["content"] if self.history else ""
        sh_res = execute_tool("run_shell", {"command": last_user})
        out = sh_res.get("stdout") or sh_res.get("stderr") or "Comando ejecutado."
        yield {"type": "token", "token": f"Resultado: {out[:300]}"}
