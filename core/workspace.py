"""Carpetas del usuario y resolución de rutas para las herramientas de archivos.

Zara trabaja sobre las carpetas reales del usuario como lo haría Claude Code: entiende
«Documentos», «Escritorio» o «Descargas» aunque Windows las tenga redirigidas a OneDrive
o Linux las llame según el idioma del sistema (XDG), y resuelve las rutas relativas
contra una carpeta de trabajo en vez de contra el directorio donde se lanzó Zara.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("zara.workspace")

# Nombre de carpeta (sin acentos, minúsculas) -> clave canónica
_ALIASES = {
    "documentos": "documentos", "documents": "documentos", "mis documentos": "documentos", "my documents": "documentos",
    "escritorio": "escritorio", "desktop": "escritorio",
    "descargas": "descargas", "downloads": "descargas",
    "imagenes": "imagenes", "pictures": "imagenes", "fotos": "imagenes",
    "musica": "musica", "music": "musica",
    "videos": "videos", "video": "videos",
    "inicio": "home", "home": "home", "carpeta personal": "home", "usuario": "home",
}

# Windows: valores de "User Shell Folders" (respetan la redirección a OneDrive)
_WIN_SHELL_KEYS = {
    "documentos": "Personal",
    "escritorio": "Desktop",
    "descargas": "{374DE290-123F-4565-9164-39C4925E467B}",
    "imagenes": "My Pictures",
    "musica": "My Music",
    "videos": "My Video",
}
# Linux: claves de ~/.config/user-dirs.dirs
_XDG_KEYS = {
    "documentos": "XDG_DOCUMENTS_DIR",
    "escritorio": "XDG_DESKTOP_DIR",
    "descargas": "XDG_DOWNLOAD_DIR",
    "imagenes": "XDG_PICTURES_DIR",
    "musica": "XDG_MUSIC_DIR",
    "videos": "XDG_VIDEOS_DIR",
}
# Nombres por defecto si no hay configuración (se prueba el español y luego el inglés)
_FALLBACK_NAMES = {
    "documentos": ("Documentos", "Documents"),
    "escritorio": ("Escritorio", "Desktop"),
    "descargas": ("Descargas", "Downloads"),
    "imagenes": ("Imágenes", "Imagenes", "Pictures"),
    "musica": ("Música", "Musica", "Music"),
    "videos": ("Vídeos", "Videos"),
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.strip().lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _windows_shell_folder(value_name: str) -> Optional[Path]:
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            raw, _kind = winreg.QueryValueEx(key, value_name)
        return Path(os.path.expandvars(raw))
    except Exception:
        return None


def _xdg_user_dirs(home: Path) -> Dict[str, Path]:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    found: Dict[str, Path] = {}
    try:
        text = (config_home / "user-dirs.dirs").read_text(encoding="utf-8")
    except Exception:
        return found
    for line in text.splitlines():
        m = re.match(r'\s*(XDG_[A-Z]+_DIR)\s*=\s*"(.*)"\s*$', line)
        if m:
            found[m.group(1)] = Path(m.group(2).replace("$HOME", str(home)))
    return found


@lru_cache(maxsize=1)
def user_folders() -> Dict[str, Path]:
    """Carpetas personales reales del usuario actual (claves en español)."""
    home = Path.home()
    folders: Dict[str, Path] = {"home": home}
    xdg = _xdg_user_dirs(home) if sys.platform != "win32" else {}
    for key, names in _FALLBACK_NAMES.items():
        path: Optional[Path] = None
        if sys.platform == "win32":
            path = _windows_shell_folder(_WIN_SHELL_KEYS[key])
        else:
            path = xdg.get(_XDG_KEYS[key])
            if path is not None and path == home:
                path = None  # XDG apunta al HOME cuando la carpeta no existe
        if path is None:
            candidates = [home / n for n in names]
            existing = next((c for c in candidates if c.is_dir()), None)
            if existing is None:
                # Ninguna existe todavía: Windows usa nombres en inglés en disco; en Linux,
                # el idioma del sistema decide (XDG crearía «Documentos» en español).
                spanish = os.environ.get("LANG", "").lower().startswith("es")
                existing = candidates[0] if (sys.platform != "win32" and spanish) else candidates[-1]
            path = existing
        folders[key] = path
    return folders


def workspace_dir() -> Path:
    """Carpeta de trabajo por defecto para rutas relativas: ZARA_WORKSPACE o Documentos."""
    configured = os.environ.get("ZARA_WORKSPACE", "").strip()
    if configured:
        return resolve_user_path(configured, base=Path.home())
    documents = user_folders()["documentos"]
    # En instalaciones mínimas (p. ej. Kali como root) puede no existir: usar la carpeta personal.
    return documents if documents.is_dir() else Path.home()


def resolve_user_path(path: str, base: Optional[Path] = None) -> Path:
    """Convierte lo que dice un modelo («Documentos/mi-web», «~/x», «%USERPROFILE%\\x»,
    una ruta relativa…) en una ruta absoluta real del sistema.
    """
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        return (base or workspace_dir()).resolve()
    expanded = os.path.expanduser(os.path.expandvars(raw))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        parts = Path(expanded).parts
        if parts:
            alias = _ALIASES.get(_fold(parts[0]))
            if alias:
                return user_folders()[alias].joinpath(*parts[1:]).resolve()
        candidate = (base or workspace_dir()) / candidate
    return candidate.resolve()


def describe_user_folders() -> str:
    """Resumen para el prompt del sistema: dónde está cada carpeta en ESTE equipo."""
    f = user_folders()
    sistema = "Windows" if sys.platform == "win32" else ("macOS" if sys.platform == "darwin" else "Linux")
    return (
        f"SISTEMA: {sistema}. Carpeta personal: {f['home']}. Documentos: {f['documentos']}. "
        f"Escritorio: {f['escritorio']}. Descargas: {f['descargas']}. "
        f"Carpeta de trabajo para rutas relativas: {workspace_dir()}."
    )
