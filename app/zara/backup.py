from __future__ import annotations
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable
from .errors import BackupError


def backup_path(src: Path, copias_dir: Path,
                clock: Callable[[], datetime] = datetime.now) -> Path:
    if not src.exists():
        raise BackupError(f"No existe la ruta a copiar: {src}")
    stamp = f"{clock():%Y-%m-%d_%H%M%S}"
    dest = copias_dir / stamp / src.name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    except OSError as exc:
        raise BackupError(f"No se pudo copiar {src}: {exc}") from exc
    return dest
