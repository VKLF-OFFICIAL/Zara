from __future__ import annotations
from pathlib import Path

from .paths import ZaraPaths
from .config import load_toml, load_env
from .brain.factory import build_registry
from .memory.store import MemoryStore
from .voice.speaker import Voice
from .voice.synth import EdgeSynth, EspeakSynth, PiperSynth
from .core import ZaraCore

_CONFIG_DEFAULTS = {
    "voz": "es-MX-DaliaNeural",
    "cerebro_por_defecto": "",      # "" = primer disponible (Claude)
    "modelo_claude": "",            # "" = el de tu suscripción por defecto
    "cerebro_claude_modo": "agente",   # "agente" = control total | "consulta" = solo web+lectura
    "claude_directorio": "",           # "" = su carpeta personal; dir de trabajo del agente
    "hablar": True,
    "motor_voz": "edge",               # "edge" (natural, red) | "piper" (local, instantánea) | "espeak"
    "voz_piper": "/mnt/almacen/IA/piper-voices/es_ES-sharvard-medium.onnx",
    "cli_fallback": False,             # red de seguridad CLI (off: solo cerebros nativos)
}


def load_persona(paths: ZaraPaths) -> str:
    f = paths.config / "persona.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def build_core(paths: ZaraPaths | None = None, speak: bool | None = None) -> ZaraCore:
    load_env()  # claves de API de los cerebros nativos (.env del repo)
    paths = paths or ZaraPaths.resolve()
    cfg = load_toml(paths.config / "zara.toml", _CONFIG_DEFAULTS)
    persona = load_persona(paths)

    mcp = paths.config / "mcp.json"
    brains = build_registry(
        persona, claude_model=cfg.get("modelo_claude") or None,
        claude_mode=cfg.get("cerebro_claude_modo", "agente"),
        claude_workdir=cfg.get("claude_directorio") or None,
        mcp_config=str(mcp) if mcp.exists() else None,
        cli_fallback=bool(cfg.get("cli_fallback", False)))
    if cfg.get("cerebro_por_defecto"):
        try:
            brains.switch(cfg["cerebro_por_defecto"])
        except Exception:
            pass

    # Cadena de voz según preferencia; siempre con respaldos.
    edge = EdgeSynth(cfg["voz"])
    piper = PiperSynth(cfg["voz_piper"])
    espeak = EspeakSynth()
    if cfg.get("motor_voz") == "piper":
        chain = [piper, edge, espeak]        # local primero: instantánea y offline
    elif cfg.get("motor_voz") == "espeak":
        chain = [espeak]
    else:
        chain = [edge, piper, espeak]        # Edge (natural), con Piper offline de respaldo
    voice = Voice(chain, out_dir=paths.espacio / "voz")
    memory = MemoryStore(paths.memoria)
    do_speak = cfg["hablar"] if speak is None else speak
    return ZaraCore(brains, voice, memory, speak=do_speak)
