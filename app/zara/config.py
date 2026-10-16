from __future__ import annotations
import os
import tomllib
from copy import deepcopy
from pathlib import Path
from .errors import ConfigError

_ENV_LOADED = False


def load_env(start: Path | None = None) -> None:
    """Carga un `.env` (KEY=VALUE) en os.environ sin pisar lo ya definido.

    Reemplaza al antiguo cargador de `config.py` de la raíz (ya unificado): así
    GEMINI_API_KEY / ANTHROPIC_API_KEY / OLLAMA_HOST llegan a los cerebros nativos.
    Busca `.env` desde el directorio actual y desde el paquete hacia arriba.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    candidates = []
    here = Path(start) if start else Path.cwd()
    for base in (here, Path(__file__).resolve().parent.parent.parent):
        for d in (base, *base.parents):
            candidates.append(d / ".env")
    seen = set()
    for env_path in candidates:
        if env_path in seen or not env_path.is_file():
            seen.add(env_path)
            continue
        seen.add(env_path)
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass
        break
    _ENV_LOADED = True


def load_toml(path: Path, defaults: dict) -> dict:
    result = deepcopy(defaults)
    if not path.exists():
        return result
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML inválido en {path}: {exc}") from exc
    result.update(data)
    return result
