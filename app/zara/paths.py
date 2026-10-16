from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from .errors import ZaraNotMountedError

import os
import sys

# Dos sistemas de archivos, dos velocidades (punto 6 del plan de optimización I/O):
#   • root  → almacenamiento FRÍO/persistente. En Linux, la partición NTFS
#             /mnt/almacen/ZARA (copias de seguridad, memoria personal, config).
#   • ssd   → sistema de archivos NATIVO y rápido (~/.local/share/zara). Aquí
#             viven las rutas CALIENTES: logs JSONL append-only, caché, el
#             espacio de trabajo y las tareas. Nunca bloquean el hilo de voz
#             con latencia de I/O de NTFS.
if sys.platform == "win32":
    ZARA_ROOT = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Zara"
    SSD = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "zara"
    MOUNT_SENTINEL = None
else:
    ZARA_ROOT = Path("/mnt/almacen/ZARA")
    SSD = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "zara"
    MOUNT_SENTINEL = Path("/mnt/almacen/.zara_mounted")

# Subcarpetas frías (en root) y calientes (en ssd).
_ROOT_SUBDIRS = ("config", "memoria", "copias", "voz")
_SSD_SUBDIRS = ("registros", "cache", "espacio", "tareas", "voz")


@dataclass
class ZaraPaths:
    root: Path
    ssd: Path

    @classmethod
    def resolve(cls, root: Path | None = None, ssd: Path | None = None,
                sentinel: Path | None = None) -> "ZaraPaths":
        env_root = os.environ.get("ZARA_ROOT") or os.environ.get("ZARA_DIR")
        explicit_root = root is not None or bool(env_root)
        target_root = Path(env_root) if (env_root and root is None) else Path(root or ZARA_ROOT)
        target_ssd = Path(ssd or SSD)
        # El centinela de montaje solo se exige si se pide EXPLÍCITAMENTE (tests y
        # modo estricto). Por defecto, si el almacén frío no está montado, Zara NO
        # se niega a arrancar: degrada a trabajar íntegramente sobre el SSD rápido,
        # de modo que la latencia de NTFS jamás bloquea la voz.
        if sentinel is not None:
            if not sentinel.exists():
                raise ZaraNotMountedError(
                    f"El almacén no está montado (falta {sentinel}). "
                    "Monta /mnt/almacen antes de iniciar Zara en modo estricto."
                )
        elif not explicit_root and sys.platform != "win32" and MOUNT_SENTINEL is not None:
            if not MOUNT_SENTINEL.exists():
                # Almacén frío ausente → todo al SSD nativo (sin dependencia NTFS).
                target_root = target_ssd / "frio"

        self = cls(root=target_root, ssd=target_ssd)
        for d in _ROOT_SUBDIRS:
            (target_root / d).mkdir(parents=True, exist_ok=True)
        for d in _SSD_SUBDIRS:
            (target_ssd / d).mkdir(parents=True, exist_ok=True)
        return self

    # --- Rutas frías (persistentes, en el almacén) -------------------------
    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def memoria(self) -> Path:
        return self.root / "memoria"

    @property
    def copias(self) -> Path:
        """Copias de seguridad: almacenamiento frío, nunca en el hilo de voz."""
        return self.root / "copias"

    @property
    def voz(self) -> Path:
        return self.root / "voz"

    # --- Rutas calientes (SSD nativo, baja latencia) -----------------------
    @property
    def registros(self) -> Path:
        """Logs JSONL append-only y zara.log: SSD nativo (no NTFS)."""
        return self.ssd / "registros"

    @property
    def cache(self) -> Path:
        """Caché efímera de alta frecuencia en el SSD."""
        return self.ssd / "cache"

    @property
    def espacio(self) -> Path:
        """Espacio de trabajo del agente y audio temporal de voz: SSD."""
        return self.ssd / "espacio"

    @property
    def tareas(self) -> Path:
        return self.ssd / "tareas"

    @property
    def ssd_voz(self) -> Path:
        return self.ssd / "voz"
