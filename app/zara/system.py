"""Informe del estado del equipo (al estilo del parte de JARVIS)."""
from __future__ import annotations
import shutil
import time
from datetime import datetime


def greeting(now: datetime | None = None) -> str:
    now = now or datetime.now()
    h = now.hour
    if 6 <= h < 13:
        saludo = "Buenos días"
    elif 13 <= h < 20:
        saludo = "Buenas tardes"
    else:
        saludo = "Buenas noches"
    return f"{saludo}, Señor VK."


def _uptime_str(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d} día{'s' if d != 1 else ''}")
    if h:
        parts.append(f"{h} hora{'s' if h != 1 else ''}")
    if m and not d:
        parts.append(f"{m} minuto{'s' if m != 1 else ''}")
    return " y ".join(parts) if parts else "pocos segundos"


def format_status(cpu, mem_used, mem_total, disk_free, disk_total, temp, uptime_s) -> str:
    frases = [
        f"CPU al {cpu:.0f}%.",
        f"Memoria: {mem_used:.0f} de {mem_total:.0f} gigas en uso.",
        f"Disco: {disk_free:.0f} gigas libres de {disk_total:.0f}.",
    ]
    if temp is not None:
        frases.append(f"Temperatura: {temp:.0f}º.")
    frases.append(f"Encendido desde hace {_uptime_str(uptime_s)}.")
    return "Señor VK, estado del equipo. " + " ".join(frases)


# --- lectura real del sistema (no en los tests) -----------------------------
def _cpu_percent(interval: float = 0.3) -> float:
    def read():
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = list(map(int, parts))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return sum(vals), idle
    t0, i0 = read()
    time.sleep(interval)
    t1, i1 = read()
    dt, di = t1 - t0, i1 - i0
    return 100.0 * (1 - di / dt) if dt else 0.0


def _mem_gib():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k] = int(v.split()[0])  # kB
    total = info["MemTotal"] / 1048576
    avail = info.get("MemAvailable", info["MemFree"]) / 1048576
    return total - avail, total


def _temp():
    try:
        import glob
        temps = []
        for z in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            with open(z) as f:
                temps.append(int(f.read()) / 1000)
        return max(temps) if temps else None
    except Exception:
        return None


def _uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def status_report() -> str:
    used, total = _mem_gib()
    du = shutil.disk_usage("/")
    return format_status(
        cpu=_cpu_percent(), mem_used=used, mem_total=total,
        disk_free=du.free / 1073741824, disk_total=du.total / 1073741824,
        temp=_temp(), uptime_s=_uptime())
