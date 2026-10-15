"""Acciones directas del sistema que Zara ejecuta sin consultar al cerebro."""
from __future__ import annotations
import logging
import os
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path

log = logging.getLogger("zara.actions")

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def _run(cmd: list[str], timeout: int = 5) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _detach(cmd: list[str]) -> None:
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


# --- hora y fecha ---------------------------------------------------------
def say_time(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"Son las {now:%H:%M}, Señor VK."


def say_date(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return (f"Hoy es {_DIAS[now.weekday()]}, {now.day} de "
            f"{_MESES[now.month - 1]} de {now.year}, Señor VK.")


# --- volumen (PipeWire vía pactl) ------------------------------------------
_DEFAULT_SINK = "@DEFAULT_SINK@"


def _volume_pct(sink: str) -> int | None:
    try:
        out = _run(["pactl", "get-sink-volume", sink]).stdout
        m = re.search(r"(\d+)%", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def volume(kind: str, value: str | None = None, sink: str | None = None) -> str:
    """Controla el volumen de la salida indicada (o la predeterminada)."""
    if not shutil.which("pactl"):
        return "No encuentro el control de audio (pactl), Señor VK."
    sink = sink or _DEFAULT_SINK
    cur = _volume_pct(sink)
    if kind in ("volume_up", "volume_down", "set_volume"):
        if kind == "set_volume":
            target = int(value or 50)
        else:
            step = 10 if kind == "volume_up" else -10
            target = (cur if cur is not None else 50) + step
        target = max(0, min(100, target))
        _run(["pactl", "set-sink-mute", sink, "0"])
        _run(["pactl", "set-sink-volume", sink, f"{target}%"])
    elif kind == "mute":
        _run(["pactl", "set-sink-mute", sink, "1"])
        return "Sonido silenciado, Señor VK."
    elif kind == "unmute":
        _run(["pactl", "set-sink-mute", sink, "0"])
    pct = _volume_pct(sink)
    return f"Volumen al {pct} %, Señor VK." if pct is not None else "Hecho, Señor VK."


# --- música (MPRIS por D-Bus) ---------------------------------------------
def _mpris_players() -> list[str]:
    out = _run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.DBus",
                "/org/freedesktop/DBus", "org.freedesktop.DBus.ListNames"]).stdout
    return re.findall(r'"(org\.mpris\.MediaPlayer2\.[^"]+)"', out)


def _mpris_status(name: str) -> str:
    out = _run(["dbus-send", "--session", "--print-reply", f"--dest={name}",
                "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties.Get",
                "string:org.mpris.MediaPlayer2.Player", "string:PlaybackStatus"]).stdout
    m = re.search(r'string "(\w+)"', out)
    return m.group(1) if m else ""


def _mpris_call(name: str, method: str) -> None:
    _run(["dbus-send", "--session", "--type=method_call", f"--dest={name}",
          "/org/mpris/MediaPlayer2", f"org.mpris.MediaPlayer2.Player.{method}"])


def media(kind: str) -> str:
    try:
        players = _mpris_players()
    except Exception:
        players = []
    if not players:
        return "No hay ningún reproductor abierto, Señor VK."
    playing = [p for p in players if _mpris_status(p) == "Playing"]
    # Preferimos el que está sonando; si no, Spotify; si no, el primero.
    target = (playing or [p for p in players if "spotify" in p.lower()] or players)[0]
    if kind == "media_pause":
        for p in playing or [target]:
            _mpris_call(p, "Pause")
        return "Música en pausa, Señor VK."
    if kind == "media_play":
        _mpris_call(target, "Play")
        return "Reanudo la música, Señor VK."
    if kind == "media_next":
        _mpris_call(target, "Next")
        return "Siguiente canción, Señor VK."
    if kind == "media_prev":
        _mpris_call(target, "Previous")
        return "Canción anterior, Señor VK."
    return "Hecho, Señor VK."


# --- abrir aplicaciones ---------------------------------------------------
_ALIASES = {
    "navegador": ["xdg-open", "https://www.google.com"],
    "internet": ["xdg-open", "https://www.google.com"],
    "terminal": ["konsole"], "consola": ["konsole"],
    "archivos": ["dolphin"], "explorador de archivos": ["dolphin"],
    "carpeta personal": ["dolphin"], "gestor de archivos": ["dolphin"],
    "calculadora": ["kcalc"], "ajustes": ["systemsettings"],
    "configuracion": ["systemsettings"], "configuracion del sistema": ["systemsettings"],
    "editor de texto": ["kate"], "notas": ["kate"],
}

_DESKTOP_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/snapd/desktop/applications"),
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def find_desktop_entry(target: str, dirs: list[Path] | None = None) -> Path | None:
    """Busca un .desktop cuyo nombre contenga el objetivo (sin acentos)."""
    t = _norm(target)
    best = None
    for d in dirs or _DESKTOP_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.desktop")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            entry = text.split("\n[", 1)[0]  # solo la sección [Desktop Entry]
            if re.search(r"^NoDisplay\s*=\s*true", entry, re.M | re.I):
                continue
            names = [_norm(n) for n in re.findall(r"^Name(?:\[[^\]]+\])?\s*=\s*(.+)$", entry, re.M)]
            if any(n == t for n in names):
                return f  # coincidencia exacta: la mejor
            if best is None and (any(t in n for n in names) or t in _norm(f.stem)):
                best = f
    return best


def open_app(target: str) -> str:
    t = _norm(target).rstrip(".!? ")
    if not t:
        return "¿Qué desea que abra, Señor VK?"
    try:
        if t in _ALIASES and shutil.which(_ALIASES[t][0]):
            _detach(_ALIASES[t])
            return f"Abriendo {target}, Señor VK."
        if shutil.which(t):
            _detach([t])
            return f"Abriendo {target}, Señor VK."
        entry = find_desktop_entry(t)
        if entry is not None and shutil.which("gio"):
            _detach(["gio", "launch", str(entry)])
            return f"Abriendo {target}, Señor VK."
        if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?", t):
            _detach(["xdg-open", f"https://{t}"])
            return f"Abriendo {t} en el navegador, Señor VK."
    except Exception as exc:  # noqa: BLE001
        log.exception("no se pudo abrir %s", target)
        return f"No pude abrir {target}: {exc}"
    return f"No encuentro ninguna aplicación llamada «{target}», Señor VK."


# --- reproducir en una app concreta ------------------------------------------
def _find_player(app: str) -> str | None:
    try:
        return next((p for p in _mpris_players() if app.lower() in p.lower()), None)
    except Exception:
        return None


def play_in_app(app: str, wait_s: float = 10.0) -> str:
    """Abre la app de música si hace falta y le da al play (MPRIS)."""
    import time
    player = _find_player(app)
    if player is None:
        msg = open_app(app)
        if msg.startswith("No "):
            return msg
        deadline = time.time() + wait_s
        while player is None and time.time() < deadline:
            time.sleep(0.5)
            player = _find_player(app)
    if player is None:
        return (f"He abierto {app.capitalize()}, pero aún no acepta órdenes de "
                "reproducción. Dele al play una vez y después ya podré controlarlo.")
    _mpris_call(player, "Play")
    return f"Reproduciendo música en {app.capitalize()}, Señor VK."


# --- carpetas y búsquedas -----------------------------------------------------
_FOLDERS = {
    "descargas": ["Descargas", "Downloads"],
    "documentos": ["Documentos", "Documents"],
    "imagenes": ["Imágenes", "Pictures"], "fotos": ["Imágenes", "Pictures"],
    "musica": ["Música", "Music"],
    "videos": ["Vídeos", "Videos"],
    "escritorio": ["Escritorio", "Desktop"],
}


def folder_path(name: str, home: Path | None = None) -> Path | None:
    home = home or Path.home()
    key = _norm(name)
    if key == "personal":
        return home
    if key == "zara":
        p = Path("/mnt/almacen/ZARA")
        return p if p.is_dir() else None
    for cand in _FOLDERS.get(key, []):
        if (home / cand).is_dir():
            return home / cand
    return None


def open_folder(name: str) -> str:
    p = folder_path(name)
    if p is None:
        return f"No encuentro la carpeta «{name}», Señor VK."
    _detach(["xdg-open", str(p)])
    return f"Abriendo {p.name or 'su carpeta personal'}, Señor VK."


def search_url(engine: str, query: str) -> str:
    from urllib.parse import quote_plus
    if engine == "youtube":
        return f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    return f"https://www.google.com/search?q={quote_plus(query)}"


def web_search(engine: str, query: str) -> str:
    _detach(["xdg-open", search_url(engine, query)])
    donde = "YouTube" if engine == "youtube" else "Google"
    return f"Buscando «{query}» en {donde}, Señor VK."


# --- cerrar aplicaciones (siempre con confirmación, desde el backend) ---------
_CLOSE_ALIASES = {
    "navegador": ["firefox", "opera", "chrome", "chromium", "brave", "vivaldi"],
    "terminal": ["konsole"], "archivos": ["dolphin"], "musica": ["spotify"],
}


def find_app_processes(name: str) -> list[tuple[int, str]]:
    """Procesos cuyo nombre (comm) coincide con la app pedida."""
    wanted = _CLOSE_ALIASES.get(_norm(name), [_norm(name)])
    found = []
    out = _run(["ps", "-u", str(os.getuid()), "-o", "pid=,comm="]).stdout
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid, comm = int(parts[0]), parts[1].strip()
        if pid == os.getpid():
            continue
        if any(comm.lower() == w or comm.lower().startswith(w) for w in wanted):
            found.append((pid, comm))
    return found


def terminate(procs: list[tuple[int, str]]) -> int:
    import signal
    n = 0
    for pid, _ in procs:
        try:
            os.kill(pid, signal.SIGTERM)
            n += 1
        except OSError:
            pass
    return n
