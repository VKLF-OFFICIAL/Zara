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
                   mcp_config: str | None = None,
                   cli_fallback: bool = False) -> BrainRegistry:
    """Crea el registro de cerebros. Por defecto, SOLO adaptadores NATIVOS en
    memoria (SDK, Tool Calling): no hay ninguna dependencia de subproceso a los
    CLI de consola de Claude/Gemini.

    Orden (define el activo por defecto = primer disponible):
      1. Gemini nativo (google-genai)  — respuesta instantánea y enrutamiento.
      2. Claude nativo (AsyncAnthropic).
      3. Ollama nativo (/api/chat con herramientas) por cada modelo local.

    `cli_fallback=True` (opcional, desactivado por defecto) añade al final el
    agente Claude CLI y, si procede, el Gemini CLI, como RED DE SEGURIDAD para
    equipos sin claves de API. Nunca se usa si hay un nativo disponible.
    """
    brains: list = [NativeGemini(system=persona), NativeAnthropic(system=persona)]
    for name in _ollama_models(host):
        brains.append(NativeOllama(name, system=persona, host=host))

    if cli_fallback:
        agente = ClaudeBrain(system=persona, model=claude_model, mode="agente",
                             workdir=claude_workdir, mcp_config=mcp_config)
        agente.name = "agente"
        brains.append(agente)
        gemini_cli = GeminiBrain(system=persona)
        if gemini_cli.available():
            gemini_cli.name = "gemini-cli"
            brains.append(gemini_cli)

    return BrainRegistry(brains)
