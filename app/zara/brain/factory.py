from __future__ import annotations
import json
import os
import urllib.request
from .registry import BrainRegistry
from .native import NativeGemini, NativeAnthropic, NativeOllama
from .cli import ClaudeBrain, GeminiBrain

_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


def _ollama_models(host: str = _HOST) -> list[str]:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def build_registry(persona: str = "", host: str = _HOST,
                   claude_model: str | None = None,
                   claude_mode: str = "agente",
                   claude_workdir: str | None = None,
                   mcp_config: str | None = None) -> BrainRegistry:
    """Crea el registro de cerebros, NATIVOS primero (SDK en memoria, Tool Calling)
    y los CLI como último fallback.

    Orden (define el activo por defecto = primer disponible):
      1. Gemini nativo (google-genai)  — respuesta instantánea y enrutamiento.
      2. Claude nativo (AsyncAnthropic).
      3. Agente Claude CLI (control total del equipo) — fallback del legado.
      4. Gemini CLI — fallback solo si no hay nativo utilizable.
      5. Ollama nativo (/api/chat con herramientas) por cada modelo local.
    """
    brains: list = []

    gemini_native = NativeGemini(system=persona)
    brains.append(gemini_native)

    claude_native = NativeAnthropic(system=persona)
    brains.append(claude_native)

    # Agente Claude CLI (nombre «agente»): control nativo del equipo como respaldo.
    agente = ClaudeBrain(system=persona, model=claude_model, mode="agente",
                         workdir=claude_workdir, mcp_config=mcp_config)
    agente.name = "agente"
    brains.append(agente)

    # Gemini CLI solo si su nativo no es utilizable pero el CLI sí.
    if not gemini_native.available():
        gemini_cli = GeminiBrain(system=persona)
        if gemini_cli.available():
            gemini_cli.name = "gemini-cli"
            brains.append(gemini_cli)

    for name in _ollama_models(host):
        brains.append(NativeOllama(name, system=persona, host=host))

    return BrainRegistry(brains)
