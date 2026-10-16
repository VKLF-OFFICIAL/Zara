"""Subsistema de Herramientas y Agente Autónomo (ZARA v2.0).

Política de ejecución silenciosa (YOLO Seguro), guardarraíl contra comandos
destructivos catastróficos, soporte nativo multiplataforma (Windows y Linux)
y bucle ReAct con autocorrección automática de hasta 8 iteraciones.
"""

from __future__ import annotations
import base64
import io
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import psutil

from config import (
    FORBIDDEN_COMMANDS_REGEX,
    MAX_REACT_STEPS,
    SHELL_TIMEOUT,
)

log = logging.getLogger("zara.tools")


def is_command_forbidden(cmd: str) -> Optional[str]:
    """Comprueba si el comando viola el guardarraíl de seguridad estricto."""
    for pattern in FORBIDDEN_COMMANDS_REGEX:
        if re.search(pattern, cmd, re.IGNORECASE):
            return pattern
    return None


# =============================================================================
# 1. HERRAMIENTA: EJECUCIÓN EN SHELL (run_shell)
# =============================================================================

def run_shell(command: str, timeout: int = SHELL_TIMEOUT, background: bool = False) -> Dict[str, Any]:
    """Ejecuta un comando en PowerShell (Windows) o Bash (Linux)."""
    blocked_pattern = is_command_forbidden(command)
    if blocked_pattern:
        log.warning("Comando bloqueado por guardarraíl catastrófico: %s (patrón: %s)", command, blocked_pattern)
        return {
            "success": False,
            "blocked": True,
            "stdout": "",
            "stderr": f"ACCESO DENEGADO POR GUARDARRAÍL: El comando coincide con el patrón destructivo '{blocked_pattern}'.",
            "exit_code": 126,
        }

    log.info("Ejecutando shell [%s]: %s", "Windows/PowerShell" if sys.platform == "win32" else "Linux/Bash", command)

    if sys.platform == "win32":
        shell_cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    else:
        shell_cmd = ["/bin/bash", "-c", command]

    if background:
        try:
            proc = subprocess.Popen(
                shell_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "success": True,
                "stdout": f"Proceso iniciado en segundo plano con PID {proc.pid}.",
                "stderr": "",
                "exit_code": 0,
            }
        except Exception as exc:
            return {"success": False, "stdout": "", "stderr": str(exc), "exit_code": 1}

    try:
        res = subprocess.run(
            shell_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            errors="replace",
        )
        stdout = res.stdout.strip()
        stderr = res.stderr.strip()
        exit_code = res.returncode
        return {
            "success": exit_code == 0,
            "blocked": False,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Tiempo de espera agotado ({timeout} s).",
            "exit_code": 124,
        }
    except Exception as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Error ejecutando subproceso: {exc}",
            "exit_code": 1,
        }


# =============================================================================
# 2. HERRAMIENTA: GESTOR DE ARCHIVOS QUIRÚRGICO (file_manager)
# =============================================================================

def file_manager(
    action: str,
    path: str,
    content: str = "",
    old_str: str = "",
    new_str: str = "",
) -> Dict[str, Any]:
    """Lectura, escritura, edición quirúrgica y listado de archivos multiplataforma."""
    target = Path(path).resolve()
    action = action.lower().strip()

    try:
        if action == "read":
            if not target.is_file():
                return {"success": False, "error": f"El archivo '{target}' no existe o es un directorio."}
            text = target.read_text(encoding="utf-8", errors="replace")
            return {"success": True, "content": text, "path": str(target), "lines": len(text.splitlines())}

        elif action == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"success": True, "message": f"Archivo '{target}' escrito correctamente ({len(content)} caracteres)."}

        elif action == "append":
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "message": f"Contenido añadido a '{target}'."}

        elif action == "edit":
            if not target.is_file():
                return {"success": False, "error": f"El archivo '{target}' no existe."}
            text = target.read_text(encoding="utf-8", errors="replace")
            if old_str not in text:
                return {"success": False, "error": f"El fragmento a reemplazar no fue encontrado en '{target}'."}
            count = text.count(old_str)
            updated = text.replace(old_str, new_str)
            target.write_text(updated, encoding="utf-8")
            return {"success": True, "message": f"Reemplazo quirúrgico aplicado en '{target}' ({count} ocurrencias)."}

        elif action == "list":
            if not target.is_dir():
                return {"success": False, "error": f"'{target}' no es un directorio válido."}
            items = []
            for item in sorted(target.iterdir()):
                items.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size_bytes": item.stat().st_size if item.is_file() else None,
                })
            return {"success": True, "directory": str(target), "items": items}

        elif action == "delete":
            if target.is_file():
                target.unlink()
                return {"success": True, "message": f"Archivo '{target}' eliminado."}
            elif target.is_dir():
                shutil.rmtree(target)
                return {"success": True, "message": f"Directorio '{target}' eliminado."}
            else:
                return {"success": False, "error": f"Ruta '{target}' no encontrada."}

        elif action == "exists":
            return {"success": True, "exists": target.exists(), "is_file": target.is_file(), "is_dir": target.is_dir()}

        else:
            return {"success": False, "error": f"Acción desconocida '{action}'. Válidas: read, write, append, edit, list, delete, exists."}

    except Exception as exc:
        log.exception("Error en file_manager (%s sobre %s): %s", action, path, exc)
        return {"success": False, "error": str(exc)}


# =============================================================================
# 3. HERRAMIENTA: CONTROL DEL SISTEMA OPERATIVO (os_control)
# =============================================================================

def _set_windows_volume(pct: float) -> str:
    """Ajusta volumen en Windows mediante pycaw o teclado multimedia."""
    try:
        from pycaw.pycaw import AudioUtilities
        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, "volume_percent"):
            speakers.volume_percent = max(0.0, min(100.0, pct))
            return f"Volumen ajustado al {int(pct)}%."
        elif hasattr(speakers, "EndpointVolume"):
            speakers.EndpointVolume.SetMasterVolumeLevelScalar(max(0.0, min(1.0, pct / 100.0)), None)
            return f"Volumen ajustado al {int(pct)}%."
    except Exception as exc:
        log.warning("Fallo en pycaw: %s. Usando fallback por teclas virtuales.", exc)

    # Fallback por teclas virtuales multimedia
    try:
        import ctypes
        VK_VOLUME_DOWN = 0xAE
        VK_VOLUME_UP = 0xAF
        for _ in range(50):
            ctypes.windll.user32.keybd_event(VK_VOLUME_DOWN, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_VOLUME_DOWN, 0, 2, 0)
        target = max(0, min(100, int(pct)))
        for _ in range(target // 2):
            ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 2, 0)
        return f"Volumen ajustado al {target}%."
    except Exception as exc2:
        return f"Error ajustando volumen: {exc2}"


def _set_linux_volume(pct: float) -> str:
    """Ajusta volumen en Linux mediante wpctl (PipeWire) o pactl (PulseAudio)."""
    target = max(0, min(100, int(pct)))
    if shutil.which("wpctl"):
        subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{target/100:.2f}"], capture_output=True)
        return f"Volumen ajustado al {target}% vía PipeWire."
    elif shutil.which("pactl"):
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{target}%"], capture_output=True)
        return f"Volumen ajustado al {target}% vía PulseAudio."
    return "No se encontró control de volumen (wpctl/pactl)."


def os_control(action: str, target: str = "", value: str = "") -> Dict[str, Any]:
    """Control de aplicaciones, volumen, procesos y portapapeles."""
    action = action.lower().strip()

    try:
        if action == "open_app":
            tgt = target.strip()
            if not tgt:
                return {"success": False, "error": "Debes especificar la aplicación o URL a abrir."}

            if tgt.startswith("http://") or tgt.startswith("https://"):
                if sys.platform == "win32":
                    os.startfile(tgt)
                else:
                    subprocess.Popen(["xdg-open", tgt], stdin=subprocess.DEVNULL)
                return {"success": True, "message": f"URL '{tgt}' abierta en el navegador."}

            if sys.platform == "win32":
                # Intentos de ejecución en Windows
                if shutil.which(tgt) or shutil.which(f"{tgt}.exe"):
                    subprocess.Popen([tgt], shell=True)
                    return {"success": True, "message": f"Aplicación '{tgt}' iniciada."}
                # Start Menu búsqueda rápida
                appdata = Path(os.environ.get("APPDATA", ""))
                progdata = Path(os.environ.get("ProgramData", ""))
                for base in [appdata / "Microsoft/Windows/Start Menu/Programs", progdata / "Microsoft/Windows/Start Menu/Programs"]:
                    if base.is_dir():
                        for lnk in base.rglob("*.lnk"):
                            if tgt.lower() in lnk.stem.lower():
                                os.startfile(str(lnk))
                                return {"success": True, "message": f"Aplicación '{lnk.stem}' iniciada desde el menú inicio."}
                # Fallback PowerShell Start-Process
                subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{tgt}' -ErrorAction SilentlyContinue"])
                return {"success": True, "message": f"Comando Start-Process lanzado para '{tgt}'."}
            else:
                # Linux (KDE, GNOME, XFCE con xdg-open o gtk-launch)
                if shutil.which("gtk-launch"):
                    subprocess.Popen(["gtk-launch", tgt], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    return {"success": True, "message": f"Lanzado '{tgt}' vía gtk-launch."}
                elif shutil.which(tgt):
                    subprocess.Popen([tgt], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    return {"success": True, "message": f"Lanzado binario '{tgt}'."}
                else:
                    subprocess.Popen(["xdg-open", tgt], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    return {"success": True, "message": f"Lanzado '{tgt}' vía xdg-open."}

        elif action == "close_app":
            tgt = target.lower().strip()
            closed = []
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    p_name = p.info["name"].lower()
                    if tgt in p_name:
                        p.terminate()
                        closed.append(f"{p_name} (PID {p.info['pid']})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if closed:
                return {"success": True, "message": f"Procesos terminados: {', '.join(closed)}"}
            return {"success": False, "message": f"No se encontró ningún proceso activo llamado '{target}'."}

        elif action == "list_processes":
            procs = []
            for p in sorted(psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
                            key=lambda x: x.info["memory_percent"] or 0, reverse=True)[:15]:
                procs.append({
                    "pid": p.info["pid"],
                    "name": p.info["name"],
                    "cpu_pct": p.info["cpu_percent"],
                    "mem_pct": round(p.info["memory_percent"] or 0, 1),
                })
            return {"success": True, "top_processes": procs}

        elif action == "set_volume":
            try:
                pct = float(value or target)
            except ValueError:
                return {"success": False, "error": f"Valor de volumen inválido: '{value or target}'"}
            if sys.platform == "win32":
                msg = _set_windows_volume(pct)
            else:
                msg = _set_linux_volume(pct)
            return {"success": True, "message": msg}

        elif action == "mute":
            if sys.platform == "win32":
                try:
                    from pycaw.pycaw import AudioUtilities
                    speakers = AudioUtilities.GetSpeakers()
                    if hasattr(speakers, "EndpointVolume"):
                        speakers.EndpointVolume.SetMute(1, None)
                        return {"success": True, "message": "Sonido silenciado."}
                except Exception:
                    pass
                import ctypes
                ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0xAD, 0, 2, 0)
                return {"success": True, "message": "Silencio activado."}
            else:
                if shutil.which("wpctl"):
                    subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"], capture_output=True)
                elif shutil.which("pactl"):
                    subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], capture_output=True)
                return {"success": True, "message": "Sonido silenciado."}

        elif action == "unmute":
            if sys.platform == "win32":
                try:
                    from pycaw.pycaw import AudioUtilities
                    speakers = AudioUtilities.GetSpeakers()
                    if hasattr(speakers, "EndpointVolume"):
                        speakers.EndpointVolume.SetMute(0, None)
                        return {"success": True, "message": "Sonido reactivado."}
                except Exception:
                    pass
                import ctypes
                ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0xAD, 0, 2, 0)
                return {"success": True, "message": "Silencio desactivado."}
            else:
                if shutil.which("wpctl"):
                    subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], capture_output=True)
                elif shutil.which("pactl"):
                    subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], capture_output=True)
                return {"success": True, "message": "Sonido reactivado."}

        elif action == "read_clipboard":
            if sys.platform == "win32":
                res = subprocess.run(["powershell.exe", "-NoProfile", "Get-Clipboard"], capture_output=True, text=True)
                return {"success": True, "clipboard": res.stdout.strip()}
            elif shutil.which("xclip"):
                res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True)
                return {"success": True, "clipboard": res.stdout.strip()}
            elif shutil.which("wl-paste"):
                res = subprocess.run(["wl-paste"], capture_output=True, text=True)
                return {"success": True, "clipboard": res.stdout.strip()}
            return {"success": False, "error": "No hay herramienta de portapapeles disponible."}

        elif action == "write_clipboard":
            text_to_copy = value or target
            if sys.platform == "win32":
                subprocess.run(["powershell.exe", "-NoProfile", f"Set-Clipboard -Value '{text_to_copy}'"])
                return {"success": True, "message": "Texto copiado al portapapeles."}
            elif shutil.which("xclip"):
                p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE, text=True)
                p.communicate(input=text_to_copy)
                return {"success": True, "message": "Texto copiado al portapapeles."}
            elif shutil.which("wl-copy"):
                p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, text=True)
                p.communicate(input=text_to_copy)
                return {"success": True, "message": "Texto copiado al portapapeles."}
            return {"success": False, "error": "No hay herramienta de portapapeles disponible."}

        elif action in ("system_stats", "system_telemetry", "telemetry"):
            import platform as _platform
            import time as _time

            boot = psutil.boot_time()
            uptime_hrs = round((_time.time() - boot) / 3600, 1)
            cpu_pct = psutil.cpu_percent(interval=0.05)
            cpu_count = psutil.cpu_count(logical=True)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("C:" if sys.platform == "win32" else "/")
            batt = psutil.sensors_battery()

            battery_info = None
            if batt:
                battery_info = {
                    "percent": batt.percent,
                    "plugged": batt.power_plugged,
                }

            free_gb = round(disk.free / (1024 ** 3), 1)
            free_ram_mb = round(ram.available / (1024 ** 2))
            batt_msg = f", Batería al {batt.percent}%" if batt else ""

            return {
                "success": True,
                "telemetry": {
                    "os": _platform.platform(),
                    "cpu_percent": cpu_pct,
                    "cpu_cores": cpu_count,
                    "ram_percent": ram.percent,
                    "ram_used_mb": round((ram.total - ram.available) / (1024 ** 2)),
                    "ram_free_mb": free_ram_mb,
                    "disk_free_gb": free_gb,
                    "disk_percent": disk.percent,
                    "uptime_hours": uptime_hrs,
                    "battery": battery_info,
                },
                "message": (
                    f"Telemetría táctica: CPU al {cpu_pct}%, RAM al {ram.percent}% ({free_ram_mb} MB libres), "
                    f"Disco al {disk.percent}% ({free_gb} GB disponibles), equipo encendido hace {uptime_hrs} horas{batt_msg}."
                ),
            }

        elif action in ("network_info", "network_check", "ip"):
            import socket as _socket
            import time as _time

            hostname = _socket.gethostname()
            try:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
                s.connect(("1.1.1.1", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                local_ip = _socket.gethostbyname(hostname)

            t0 = _time.time()
            ping_ms = None
            try:
                s = _socket.create_connection(("1.1.1.1", 53), timeout=1.5)
                s.close()
                ping_ms = round((_time.time() - t0) * 1000, 1)
            except Exception:
                pass

            lat_text = f"{ping_ms} milisegundos" if ping_ms is not None else "no disponible"
            return {
                "success": True,
                "network": {
                    "hostname": hostname,
                    "local_ip": local_ip,
                    "latency_ms": ping_ms,
                    "internet_ok": ping_ms is not None,
                },
                "message": (
                    f"Diagnóstico de red: Host '{hostname}', IP local {local_ip}, "
                    f"latencia a internet {lat_text}."
                ),
            }

        elif action in ("media_play_pause", "play_pause", "play", "pause"):
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.keybd_event(0xB3, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0xB3, 0, 2, 0)
                return {"success": True, "message": "Comando Play/Pause multimedia enviado."}
            else:
                if shutil.which("playerctl"):
                    subprocess.run(["playerctl", "play-pause"], capture_output=True)
                elif shutil.which("xdotool"):
                    subprocess.run(["xdotool", "key", "XF86AudioPlay"], capture_output=True)
                return {"success": True, "message": "Play/Pause multimedia activado."}

        elif action in ("media_next", "next_track"):
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.keybd_event(0xB0, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0xB0, 0, 2, 0)
                return {"success": True, "message": "Pista siguiente multimedia enviada."}
            else:
                if shutil.which("playerctl"):
                    subprocess.run(["playerctl", "next"], capture_output=True)
                elif shutil.which("xdotool"):
                    subprocess.run(["xdotool", "key", "XF86AudioNext"], capture_output=True)
                return {"success": True, "message": "Pista siguiente enviada."}

        elif action in ("media_prev", "prev_track"):
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.keybd_event(0xB1, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0xB1, 0, 2, 0)
                return {"success": True, "message": "Pista anterior multimedia enviada."}
            else:
                if shutil.which("playerctl"):
                    subprocess.run(["playerctl", "previous"], capture_output=True)
                elif shutil.which("xdotool"):
                    subprocess.run(["xdotool", "key", "XF86AudioPrev"], capture_output=True)
                return {"success": True, "message": "Pista anterior enviada."}

        elif action in ("show_desktop", "minimize_all"):
            if sys.platform == "win32":
                import ctypes
                # Win+D
                ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0x44, 0, 2, 0)
                ctypes.windll.user32.keybd_event(0x5B, 0, 2, 0)
                return {"success": True, "message": "Escritorio mostrado y ventanas minimizadas."}
            else:
                if shutil.which("xdotool"):
                    subprocess.run(["xdotool", "key", "super+d"], capture_output=True)
                elif shutil.which("wmctrl"):
                    subprocess.run(["wmctrl", "-k", "on"], capture_output=True)
                return {"success": True, "message": "Escritorio mostrado."}

        elif action in ("lock_screen", "lock"):
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.LockWorkStation()
                return {"success": True, "message": "Estación de trabajo bloqueada por seguridad."}
            else:
                if shutil.which("loginctl"):
                    subprocess.run(["loginctl", "lock-session"], capture_output=True)
                elif shutil.which("xdg-screensaver"):
                    subprocess.run(["xdg-screensaver", "lock"], capture_output=True)
                return {"success": True, "message": "Sesión bloqueada."}

        elif action == "notify":
            msg_text = value or target or "Notificación táctica de Zara"
            if sys.platform == "win32":
                ps_script = (
                    f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
                    f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText01);"
                    f"$template.GetElementsByTagName('text')[0].AppendChild($template.CreateTextNode('{msg_text}')) > $null;"
                    f"$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
                    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('ZARA Tactical AI').Show($toast);"
                )
                subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-Command", ps_script],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                return {"success": True, "message": f"Notificación en pantalla enviada: '{msg_text}'."}
            else:
                if shutil.which("notify-send"):
                    subprocess.Popen(["notify-send", "ZARA", msg_text], stdin=subprocess.DEVNULL)
                return {"success": True, "message": f"Notificación en pantalla enviada: '{msg_text}'."}

        else:
            return {"success": False, "error": f"Acción de SO desconocida '{action}'."}

    except Exception as exc:
        log.exception("Error en os_control: %s", exc)
        return {"success": False, "error": str(exc)}


# =============================================================================
# 4. HERRAMIENTA: INSPECCIÓN DE PANTALLA (inspect_screen)
# =============================================================================

def inspect_screen(prompt: str = "") -> Dict[str, Any]:
    """Captura de pantalla en memoria (cero archivos en disco) y codificación base64."""
    try:
        from PIL import Image, ImageGrab

        # Estrategia 1: PIL ImageGrab (rápida y limpia en memoria)
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        jpeg_bytes = buf.getvalue()
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        return {
            "success": True,
            "width": img.width,
            "height": img.height,
            "format": "image/jpeg",
            "base64_data": b64,
            "prompt": prompt or "Describe qué hay en pantalla y detecta cualquier error visible.",
        }
    except Exception as exc:
        log.warning("Fallo en captura de pantalla con ImageGrab: %s", exc)

    # Estrategia 2: MSS
    try:
        import mss
        with mss.MSS() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(mon)
            from PIL import Image
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return {
                "success": True,
                "width": shot.width,
                "height": shot.height,
                "format": "image/jpeg",
                "base64_data": b64,
                "prompt": prompt,
            }
    except Exception as exc2:
        log.exception("Todas las estrategias de captura de pantalla fallaron: %s", exc2)
        return {"success": False, "error": f"No se pudo capturar la pantalla: {exc2}"}


# =============================================================================
# 5. HERRAMIENTA: DELEGAR EN CLAUDE CODE CLI (invoke_claude_code)
# =============================================================================

def invoke_claude_code(project_path: str, instruction: str) -> Dict[str, Any]:
    """Si el binario 'claude' está en el PATH, ejecuta refactorizaciones o tareas de ingeniería pesada."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return {
            "success": False,
            "error": "El binario 'claude' CLI no está instalado en el PATH del sistema.",
        }

    target_dir = Path(project_path).resolve()
    if not target_dir.is_dir():
        target_dir = Path.cwd()

    log.info("Invocando Claude Code CLI en '%s' con instrucción: %s", target_dir, instruction[:60])
    try:
        cmd = [
            claude_bin,
            "-p", instruction,
            "--dangerously-skip-permissions",
            "--setting-sources", "project",
        ]
        res = subprocess.run(
            cmd,
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
            errors="replace",
        )
        return {
            "success": res.returncode == 0,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
            "exit_code": res.returncode,
        }
    except Exception as exc:
        return {"success": False, "error": f"Fallo al invocar Claude Code: {exc}"}


# =============================================================================
# 6. HERRAMIENTA: BÚSQUEDA WEB EN TIEMPO REAL (web_search)
# =============================================================================

def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Busca en la web en tiempo real para obtener información actualizada."""
    import html
    import json as _json
    import urllib.parse
    import urllib.request

    q = query.strip()
    if not q:
        return {"success": False, "error": "Consulta de búsqueda vacía."}

    log.info("Buscando en la web: '%s'", q)
    try:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(q)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        raw = html.unescape(raw)
        results = []
        snippets = re.findall(r'class="result__snippet[^"]*"[^>]*>(.*?)</a>', raw, re.DOTALL)
        titles = re.findall(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', raw, re.DOTALL)

        clean_tag = re.compile(r"<[^>]+>")
        for idx, snip in enumerate(snippets[:max_results]):
            snippet_text = clean_tag.sub("", snip).strip()
            title_text = ""
            link = ""
            if idx < len(titles):
                link = titles[idx][0]
                title_text = clean_tag.sub("", titles[idx][1]).strip()
            results.append({
                "title": title_text or f"Resultado {idx+1}",
                "snippet": snippet_text,
                "url": link,
            })

        if not results:
            api_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote_plus(q)}&format=json&no_html=1&skip_disambig=1"
            req_api = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req_api, timeout=5.0) as resp:
                data = _json.loads(resp.read().decode("utf-8", errors="replace"))
                answer = data.get("AbstractText") or data.get("Answer")
                if answer:
                    results.append({
                        "title": data.get("Heading", "Respuesta"),
                        "snippet": answer,
                        "url": data.get("AbstractURL", ""),
                    })

        return {
            "success": True,
            "query": q,
            "results_count": len(results),
            "results": results,
        }
    except Exception as exc:
        log.warning("Fallo en web_search ('%s'): %s", q, exc)
        return {
            "success": False,
            "query": q,
            "error": f"Error buscando en la web: {exc}",
            "results": [],
        }


# =============================================================================
# REGISTRO Y ESQUEMAS DE HERRAMIENTAS (TOOL DEFINITIONS)
# =============================================================================

TOOL_DEFINITIONS = [
    {
        "name": "run_shell",
        "description": "Ejecuta comandos del sistema en PowerShell (Windows) o Bash (Linux). NUNCA pide confirmación previa.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando exacto a ejecutar."},
                "timeout": {"type": "integer", "description": "Tiempo límite en segundos (por defecto 60)."},
                "background": {"type": "boolean", "description": "True para lanzar procesos de segundo plano."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "file_manager",
        "description": "Lectura, escritura, reemplazo quirúrgico y listado de archivos y directorios.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write", "append", "edit", "list", "delete", "exists"]},
                "path": {"type": "string", "description": "Ruta absoluta o relativa del archivo o directorio."},
                "content": {"type": "string", "description": "Contenido para acciones write o append."},
                "old_str": {"type": "string", "description": "Texto exacto a reemplazar para la acción edit."},
                "new_str": {"type": "string", "description": "Nuevo texto de reemplazo para la acción edit."},
            },
            "required": ["action", "path"],
        },
    },
    {
        "name": "os_control",
        "description": "Control táctico del sistema operativo: abrir/cerrar aplicaciones, telemetría de CPU/RAM/disco (system_stats), diagnóstico de red e IP (network_info), reproducción multimedia (media_play_pause, media_next, media_prev), control de volumen (set_volume, mute, unmute), minimizar ventanas (show_desktop), bloquear pantalla (lock_screen), notificaciones nativas (notify) y portapapeles.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "open_app", "close_app", "list_processes", "system_stats",
                        "network_info", "media_play_pause", "media_next", "media_prev",
                        "show_desktop", "lock_screen", "notify", "set_volume",
                        "mute", "unmute", "read_clipboard", "write_clipboard"
                    ],
                    "description": "Acción interactiva del sistema operativo a ejecutar.",
                },
                "target": {"type": "string", "description": "Nombre de la app, proceso o URL a abrir/cerrar."},
                "value": {"type": "string", "description": "Valor auxiliar (ej: nivel de volumen del 0 al 100, texto para portapapeles o mensaje de notificación)."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "web_search",
        "description": "Busca en la web en tiempo real (noticias, eventos de 2026, información reciente, documentación). Úsala cuando el Señor VK pida información actualizada o del mundo exterior.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Términos de búsqueda."},
                "max_results": {"type": "integer", "description": "Número máximo de resultados a devolver (por defecto 5)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "inspect_screen",
        "description": "Captura en memoria la pantalla actual del Señor VK para analizar ventanas, código o errores visuales.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Pregunta o indicación específica de análisis de pantalla."},
            },
        },
    },
    {
        "name": "invoke_claude_code",
        "description": "Delega una refactorización compleja o tarea de ingeniería en el CLI local de Claude Code.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "Ruta de la carpeta del proyecto a editar."},
                "instruction": {"type": "string", "description": "Instrucción detallada de programación."},
            },
            "required": ["project_path", "instruction"],
        },
    },
]


def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Enrutador de ejecución de herramientas."""
    name = name.strip()
    if name == "run_shell":
        return run_shell(
            command=args.get("command", ""),
            timeout=args.get("timeout", SHELL_TIMEOUT),
            background=args.get("background", False),
        )
    elif name == "file_manager":
        return file_manager(
            action=args.get("action", "read"),
            path=args.get("path", ""),
            content=args.get("content", ""),
            old_str=args.get("old_str", ""),
            new_str=args.get("new_str", ""),
        )
    elif name == "os_control":
        return os_control(
            action=args.get("action", ""),
            target=args.get("target", ""),
            value=args.get("value", ""),
        )
    elif name == "web_search":
        return web_search(
            query=args.get("query", ""),
            max_results=args.get("max_results", 5),
        )
    elif name == "inspect_screen":
        return inspect_screen(prompt=args.get("prompt", ""))
    elif name == "invoke_claude_code":
        return invoke_claude_code(
            project_path=args.get("project_path", ""),
            instruction=args.get("instruction", ""),
        )
    else:
        return {"success": False, "error": f"Herramienta desconocida '{name}'."}
