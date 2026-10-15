from __future__ import annotations
import json
import urllib.request
from .registry import BrainRegistry
from .ollama import OllamaBrain
from .cli import ClaudeBrain, GeminiBrain

_HOST = "http://localhost:11434"


def _ollama_models(host: str = _HOST) -> list[str]:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def build_registry(persona: str = "", host: str = _HOST,
                   claude_model: str | None = None,
                   claude_mode: str = "consulta",
                   claude_workdir: str | None = None,
                   mcp_config: str | None = None) -> BrainRegistry:
    """Crea el registro de cerebros: Claude y Gemini + locales Ollama.

    El orden determina el activo por defecto (primer disponible): Claude primero.
    Gemini solo aparece si es utilizable (ver GeminiBrain.available).
    """
    # Dos Claude: «claude» rápido (consulta con haiku, por defecto) y «agente»
    # (control total, modelo potente). Zara pasa al agente automáticamente
    # cuando la petición es una tarea.
    fast = ClaudeBrain(system=persona, model=claude_model or "haiku", mode="consulta")
    brains = [fast]
    agente = ClaudeBrain(system=persona, model=claude_model, mode="agente",
                         workdir=claude_workdir, mcp_config=mcp_config)
    agente.name = "agente"
    brains.append(agente)
    gemini = GeminiBrain(system=persona)
    if gemini.available():
        brains.append(gemini)
    for name in _ollama_models(host):
        # Inyectamos SIEMPRE la persona de Zara como system (sobrescribe la del
        # modelfile) para que la identidad sea coherente en todos los cerebros.
        brains.append(OllamaBrain(name, system=persona, host=host))
    return BrainRegistry(brains)
