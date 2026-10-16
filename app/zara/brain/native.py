"""Cerebros NATIVOS en memoria (sin subprocesos CLI).

Reemplazan las llamadas por subproceso a los binarios `claude`/`gemini` (que
tardaban ~2,5 s en arrancar y dependían del login del CLI) por adaptadores
persistentes sobre los SDK oficiales:

  • NativeGemini   → google-genai  (gemini-2.5-flash / gemini-2.5-pro)
  • NativeAnthropic→ AsyncAnthropic (claude-3-5-haiku / claude-3-7-sonnet)
  • NativeOllama   → HTTP /api/chat con herramientas nativas

Interfaz común (compatible con BrainRegistry y con el bucle ReAct de hands.py):
  • name: str
  • available() -> bool
  • ask(prompt, context="") -> str              (charla rápida, no streaming)
  • stream_tools(messages, tools=None)           (generador de eventos)
        eventos: {"type": "token", "token": str}
                 {"type": "tool_call", "id": str, "name": str, "args": dict}
                 {"type": "error", "error": str}

El cliente se crea UNA vez y se reutiliza (conexión persistente en memoria),
con importación perezosa del SDK para que el paquete cargue aunque no estén
instalados.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Generator, Iterable

from ..errors import BrainError
from .framing import frame_user
from . import tools as toolmod

log = logging.getLogger("zara.brain.native")

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


class _AsyncRunner:
    """Bucle asyncio persistente en un hilo propio: ejecuta corrutinas del SDK
    asíncrono sin crear/abrir un event loop nuevo en cada llamada."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coro, timeout: float = 120.0):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)


def _messages(system: str, prompt: str, context: str = "",
              history: Iterable[dict] | None = None) -> list[dict]:
    msgs: list[dict] = []
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": frame_user(prompt, context)})
    return msgs


# =============================================================================
# GEMINI (google-genai) — respuesta instantánea y enrutamiento
# =============================================================================

class NativeGemini:
    name = "gemini"

    def __init__(self, system: str = "", api_key: str | None = None,
                 model: str = "gemini-2.5-flash",
                 deep_model: str = "gemini-2.5-pro"):
        self.system = system
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = os.environ.get("GEMINI_MODEL", model)
        self.deep_model = os.environ.get("GEMINI_DEEP_MODEL", deep_model)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import google.genai  # noqa: F401
            return True
        except Exception:
            return False

    def _config(self, tools=None):
        from google.genai import types
        return types.GenerateContentConfig(
            system_instruction=self.system or None,
            temperature=0.3,
            tools=tools or None,
        )

    def _contents(self, messages: list[dict]):
        from google.genai import types
        contents = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            contents.append(types.Content(
                role=role, parts=[types.Part.from_text(text=str(m.get("content", "")))]))
        return contents

    def ask(self, prompt: str, context: str = "") -> str:
        if not self.available():
            raise BrainError("Gemini no disponible (falta GEMINI_API_KEY o google-genai)")
        try:
            client = self._get_client()
            resp = client.models.generate_content(
                model=self.model,
                contents=self._contents(_messages(self.system, prompt, context)),
                config=self._config())
            return (resp.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise BrainError(f"Gemini falló: {exc}") from exc

    def stream_tools(self, messages: list[dict],
                     tools: list[dict] | None = None) -> Generator[dict, None, None]:
        if not self.available():
            yield {"type": "error", "error": "Gemini no disponible"}
            return
        try:
            client = self._get_client()
            gem_tools = None
            if tools:
                from google.genai import types
                gem_tools = [types.Tool(function_declarations=t["function_declarations"])
                             for t in toolmod.to_gemini_tools(tools)]
            stream = client.models.generate_content_stream(
                model=self.model, contents=self._contents(messages),
                config=self._config(gem_tools))
            for chunk in stream:
                if getattr(chunk, "text", None):
                    yield {"type": "token", "token": chunk.text}
                for cand in (getattr(chunk, "candidates", None) or []):
                    for part in (getattr(cand.content, "parts", None) or []):
                        fc = getattr(part, "function_call", None)
                        if fc:
                            yield {"type": "tool_call", "id": fc.name,
                                   "name": fc.name, "args": dict(fc.args or {})}
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "error": f"Gemini: {exc}"}


# =============================================================================
# CLAUDE (AsyncAnthropic)
# =============================================================================

class NativeAnthropic:
    name = "claude"

    def __init__(self, system: str = "", api_key: str | None = None,
                 model: str = "claude-3-5-haiku-latest",
                 deep_model: str = "claude-3-7-sonnet-latest",
                 max_tokens: int = 2048):
        self.system = system
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = os.environ.get("ANTHROPIC_MODEL", model)
        self.deep_model = os.environ.get("ANTHROPIC_DEEP_MODEL", deep_model)
        self.max_tokens = max_tokens
        self._client = None
        self._runner: _AsyncRunner | None = None

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=self.api_key)
            self._runner = _AsyncRunner()
        return self._client

    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except Exception:
            return False

    def ask(self, prompt: str, context: str = "") -> str:
        if not self.available():
            raise BrainError("Claude no disponible (falta ANTHROPIC_API_KEY o anthropic)")
        client = self._get_client()
        msgs = _messages(self.system, prompt, context)

        async def _run():
            resp = await client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                system=self.system or None, messages=msgs, temperature=0.2)
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

        try:
            return self._runner.run(_run())
        except Exception as exc:  # noqa: BLE001
            raise BrainError(f"Claude falló: {exc}") from exc

    def stream_tools(self, messages: list[dict],
                     tools: list[dict] | None = None) -> Generator[dict, None, None]:
        if not self.available():
            yield {"type": "error", "error": "Claude no disponible"}
            return
        client = self._get_client()
        runner = self._runner
        anth_tools = toolmod.to_anthropic_tools(tools) if tools else None
        queue: list[dict] = []
        done = threading.Event()

        async def _run():
            try:
                async with client.messages.stream(
                    model=self.model, max_tokens=self.max_tokens,
                    system=self.system or None, messages=messages,
                    tools=anth_tools, temperature=0.2) as stream:
                    async for event in stream:
                        if getattr(event, "type", "") == "text":
                            queue.append({"type": "token", "token": event.text})
                    final = await stream.get_final_message()
                    for block in final.content:
                        if getattr(block, "type", "") == "tool_use":
                            queue.append({"type": "tool_call", "id": block.id,
                                          "name": block.name, "args": dict(block.input or {})})
            except Exception as exc:  # noqa: BLE001
                queue.append({"type": "error", "error": f"Claude: {exc}"})
            finally:
                done.set()

        fut = asyncio.run_coroutine_threadsafe(_run(), runner._loop)
        idx = 0
        import time as _t
        while not done.is_set() or idx < len(queue):
            while idx < len(queue):
                yield queue[idx]
                idx += 1
            if not done.is_set():
                _t.sleep(0.01)
        try:
            fut.result(timeout=1)
        except Exception:
            pass


# =============================================================================
# OLLAMA (/api/chat con herramientas nativas)
# =============================================================================

class NativeOllama:
    def __init__(self, model: str, system: str = "", host: str = _OLLAMA_HOST,
                 timeout: int = 300):
        self.name = model
        self.model = model
        self.system = system
        self.host = host.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=3) as r:
                data = json.loads(r.read())
            tags = {m["name"] for m in data.get("models", [])}
            return any(t == self.model or t.split(":")[0] == self.model for t in tags)
        except Exception:
            return False

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.URLError as exc:
            raise BrainError(f"Ollama no respondió ({self.model}): {exc}") from exc

    def ask(self, prompt: str, context: str = "") -> str:
        msgs = []
        if self.system:
            msgs.append({"role": "system", "content": self.system})
        msgs.append({"role": "user", "content": frame_user(prompt, context)})
        data = self._post({"model": self.model, "messages": msgs, "stream": False})
        return data.get("message", {}).get("content", "").strip()

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Una vuelta de /api/chat con herramientas; devuelve el mensaje completo."""
        msgs = messages
        if self.system and not (msgs and msgs[0].get("role") == "system"):
            msgs = [{"role": "system", "content": self.system}] + list(msgs)
        payload: dict[str, Any] = {"model": self.model, "messages": msgs, "stream": False,
                                   "options": {"temperature": 0.2}}
        if tools:
            payload["tools"] = toolmod.to_ollama_tools(tools)
        data = self._post(payload)
        return data.get("message", {})

    def stream_tools(self, messages: list[dict],
                     tools: list[dict] | None = None) -> Generator[dict, None, None]:
        try:
            msg = self.chat(messages, tools)
        except BrainError as exc:
            yield {"type": "error", "error": str(exc)}
            return
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            yield {"type": "tool_call", "id": fn.get("name", ""),
                   "name": fn.get("name", ""), "args": args}
        content = (msg.get("content") or "").strip()
        if content:
            yield {"type": "token", "token": content}
