from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from .errors import ZaraNotMountedError

import os
import sys

if sys.platform == "win32":
    ZARA_ROOT = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Zara"
    SSD = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "zara"
    MOUNT_SENTINEL = None
else:
    ZARA_ROOT = Path("/mnt/almacen/ZARA")
    SSD = Path.home() / ".local/share/zara"
    MOUNT_SENTINEL = Path("/mnt/almacen/.zara_mounted")

_SUBDIRS = ("config", "memoria", "espacio", "tareas", "registros", "copias", "voz")


@dataclass
class ZaraPaths:
    root: Path
    ssd: Path

    @classmethod
    def resolve(cls, root: Path | None = None, ssd: Path | None = None,
                sentinel: Path | None = None) -> "ZaraPaths":
        env_root = os.environ.get("ZARA_ROOT") or os.environ.get("ZARA_DIR")
        target_root = Path(env_root) if (env_root and root is None) else (root or ZARA_ROOT)
        target_ssd = ssd or SSD
        target_sentinel = sentinel if sentinel is not None else (None if (env_root or sys.platform == "win32") else MOUNT_SENTINEL)
        if target_sentinel is not None and not target_sentinel.exists():
            raise ZaraNotMountedError(
                f"El almacén no está montado (falta {target_sentinel}). "
                "Monta /mnt/almacen antes de iniciar Zara."
            )
        self = cls(root=target_root, ssd=target_ssd)
        for d in _SUBDIRS:
            (target_root / d).mkdir(parents=True, exist_ok=True)
        self.ssd_voz.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def memoria(self) -> Path:
        return self.root / "memoria"

    @property
    def espacio(self) -> Path:
        return self.root / "espacio"

    @property
    def tareas(self) -> Path:
        return self.root / "tareas"

    @property
    def registros(self) -> Path:
        return self.root / "registros"

    @property
    def copias(self) -> Path:
        return self.root / "copias"

    @property
    def voz(self) -> Path:
        return self.root / "voz"

    @property
    def ssd_voz(self) -> Path:
        return self.ssd / "voz"
