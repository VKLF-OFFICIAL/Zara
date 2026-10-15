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
                pct = float(value)
            except ValueError:
                return {"success": False, "error": f"Valor de volumen inválido: '{value}'"}
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
        "description": "Control del sistema operativo: abrir/cerrar aplicaciones, volumen, listar procesos y portapapeles.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open_app", "close_app", "list_processes", "set_volume", "mute", "unmute", "read_clipboard", "write_clipboard"],
                },
                "target": {"type": "string", "description": "Nombre de la app, proceso o URL a abrir/cerrar."},
                "value": {"type": "string", "description": "Valor auxiliar (ej: nivel de volumen del 0 al 100, texto para portapapeles)."},
            },
            "required": ["action"],
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
    elif name == "inspect_screen":
        return inspect_screen(prompt=args.get("prompt", ""))
    elif name == "invoke_claude_code":
        return invoke_claude_code(
            project_path=args.get("project_path", ""),
            instruction=args.get("instruction", ""),
        )
    else:
        return {"success": False, "error": f"Herramienta desconocida '{name}'."}
