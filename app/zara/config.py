from __future__ import annotations
import tomllib
from copy import deepcopy
from pathlib import Path
from .errors import ConfigError


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
