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


import os
import sys


# --- lectura real del sistema (no en los tests) -----------------------------
def _cpu_percent(interval: float = 0.3) -> float:
    if sys.platform == "win32":
        try:
            import ctypes
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_uint), ("dwHighDateTime", ctypes.c_uint)]
            def ft_to_int(ft):
                return (ft.dwHighDateTime << 32) + ft.dwLowDateTime
            def get_times():
                idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
                ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
                return ft_to_int(idle), ft_to_int(kernel), ft_to_int(user)
            i0, k0, u0 = get_times()
            time.sleep(interval)
            i1, k1, u1 = get_times()
            idle = i1 - i0
            total = (k1 - k0) + (u1 - u0)
            return 100.0 * (total - idle) / total if total > 0 else 0.0
        except Exception:
            return 0.0

    def read():
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = list(map(int, parts))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return sum(vals), idle
    try:
        t0, i0 = read()
        time.sleep(interval)
        t1, i1 = read()
        dt, di = t1 - t0, i1 - i0
        return 100.0 * (1 - di / dt) if dt else 0.0
    except Exception:
        return 0.0


def _mem_gib() -> tuple[float, float]:
    if sys.platform == "win32":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total = stat.ullTotalPhys / 1073741824.0
            avail = stat.ullAvailPhys / 1073741824.0
            return total - avail, total
        except Exception:
            return 4.0, 16.0

    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k] = int(v.split()[0])  # kB
        total = info["MemTotal"] / 1048576
        avail = info.get("MemAvailable", info.get("MemFree", 0)) / 1048576
        return total - avail, total
    except Exception:
        return 4.0, 16.0


def _temp():
    if sys.platform == "win32":
        return None
    try:
        import glob
        temps = []
        for z in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            with open(z) as f:
                temps.append(int(f.read()) / 1000)
        return max(temps) if temps else None
    except Exception:
        return None


def _uptime() -> float:
    if sys.platform == "win32":
        try:
            import ctypes
            return ctypes.windll.kernel32.GetTickCount64() / 1000.0
        except Exception:
            return 3600.0
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return 3600.0


def status_report() -> str:
    used, total = _mem_gib()
    disk_path = (os.environ.get("SystemDrive", "C:") + "\\") if sys.platform == "win32" else "/"
    du = shutil.disk_usage(disk_path)
    return format_status(
        cpu=_cpu_percent(), mem_used=used, mem_total=total,
        disk_free=du.free / 1073741824, disk_total=du.total / 1073741824,
        temp=_temp(), uptime_s=_uptime())
