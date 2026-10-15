from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from .errors import ZaraNotMountedError

ZARA_ROOT = Path("/mnt/almacen/ZARA")
SSD = Path.home() / ".local/share/zara"
MOUNT_SENTINEL = Path("/mnt/almacen/.zara_mounted")

_SUBDIRS = ("config", "memoria", "espacio", "tareas", "registros", "copias", "voz")


@dataclass
class ZaraPaths:
    root: Path
    ssd: Path

    @classmethod
    def resolve(cls, root: Path = ZARA_ROOT, ssd: Path = SSD,
                sentinel: Path = MOUNT_SENTINEL) -> "ZaraPaths":
        if not sentinel.exists():
            raise ZaraNotMountedError(
                f"El almacén no está montado (falta {sentinel}). "
                "Monta /mnt/almacen antes de iniciar Zara."
            )
        self = cls(root=root, ssd=ssd)
        for d in _SUBDIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
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
