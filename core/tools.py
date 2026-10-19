"""Subsistema de Herramientas y Agente Autónomo (ZARA).

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
from typing import Any, Callable, Dict, List, Optional, Tuple
import psutil

from core.workspace import resolve_user_path, workspace_dir
from config import (
    SHELL_TIMEOUT_MAX,
    CONFIRM_COMMANDS_REGEX,
    FORBIDDEN_COMMANDS_REGEX,
    PROTECTED_EXACT,
    PROTECTED_TREES,
    SHELL_TIMEOUT,
)

log = logging.getLogger("zara.tools")


def is_command_forbidden(cmd: str) -> Optional[str]:
    """Comprueba si el comando viola el guardarraíl de seguridad estricto."""
    for pattern in FORBIDDEN_COMMANDS_REGEX:
        if re.search(pattern, cmd, re.IGNORECASE):
            return pattern
    return None


# --- Confirmación de acciones sensibles ---------------------------------------
# El micrófono está siempre abierto: cualquier voz que diga «Zara» (TV, otra persona)
# podría disparar herramientas. Las acciones destructivas o de alto impacto piden un
# «sí» explícito al Señor VK. El manejador lo registra la capa de UI/voz (main.py);
# sin manejador (tests, uso como librería) no se pide confirmación.
_confirm_handler: Optional[Callable[[str], bool]] = None


def set_confirmation_handler(handler: Optional[Callable[[str], bool]]) -> None:
    """Registra la función que pregunta al usuario (descripción -> ¿confirmado?)."""
    global _confirm_handler
    _confirm_handler = handler


def needs_confirmation(name: str, args: Dict[str, Any]) -> Optional[str]:
    """Devuelve una descripción hablable si la llamada requiere confirmación, o None."""
    if name == "run_shell":
        cmd = str(args.get("command", ""))
        for pattern in CONFIRM_COMMANDS_REGEX:
            if re.search(pattern, cmd, re.IGNORECASE):
                return f"ejecutar el comando {cmd[:80]}"
    elif name == "file_manager" and str(args.get("action", "")).lower() == "delete":
        return f"eliminar {args.get('path', '')}"
    elif name == "os_control" and str(args.get("action", "")).lower() == "close_app":
        return f"cerrar la aplicación {args.get('target', '')}"
    elif name == "invoke_claude_code":
        return "delegar una tarea de programación en Claude Code con permisos completos"
    return None


# Metacaracteres que cmd.exe interpreta aunque vayan entre comillas ("BatBadBut").
_CMD_METACHARS = re.compile(r'[&|<>^%!"]')


def cli_safe_arg(executable: str, text: str) -> str:
    """Prepara texto libre (del usuario o de un modelo) para pasarlo como argumento a un CLI.

    En Windows, los CLI instalados con npm (claude, agy...) son shims `.cmd`/`.bat` que se
    ejecutan a través de cmd.exe: un `&`, `|` o `>` dentro del argumento rompería las comillas
    y ejecutaría lo que viniera detrás. Para esos ejecutables se neutralizan los
    metacaracteres y los saltos de línea (cmd.exe corta el argumento en el primer salto).
    """
    if sys.platform == "win32" and Path(executable).suffix.lower() in (".cmd", ".bat"):
        flat = " / ".join(line.strip() for line in text.splitlines() if line.strip())
        return _CMD_METACHARS.sub(" ", flat)
    return text


def is_path_protected(path: Path) -> Optional[str]:
    """Devuelve la ruta protegida que contiene (o es) `path`, o None si es seguro tocarla."""
    resolved = Path(os.path.realpath(path))
    for prot in PROTECTED_EXACT:
        if resolved == Path(os.path.realpath(prot)):
            return prot
    for prot in PROTECTED_TREES:
        prot_path = Path(os.path.realpath(prot))
        if resolved == prot_path or prot_path in resolved.parents:
            return prot
    return None


def _normalize_windows_command(cmd: str) -> str:
    """Traduce comandos Unix habituales emitidos por modelos locales a sintaxis PowerShell."""
    c = cmd.strip()
    if re.match(r"^ls\s+-[a-zA-Z]+(\s+.*)?$", c):
        c = re.sub(r"^ls\s+-[a-zA-Z]+", "Get-ChildItem -Force", c)
    elif c.startswith("which "):
        target = c[6:].strip()
        c = f"where.exe {target}"
    elif c == "ifconfig" or c.startswith("ifconfig "):
        c = "ipconfig"
    elif "grep " in c and not shutil.which("grep"):
        c = re.sub(r"\|\s*grep\s+(-[a-zA-Z]+\s+)?", "| Select-String ", c)
    elif c == "clear":
        c = "Clear-Host"
    elif c.startswith("uname"):
        c = "(Get-CimInstance Win32_OperatingSystem).Caption"
    return c


# =============================================================================
# 1. HERRAMIENTA: EJECUCIÓN EN SHELL (run_shell)
# =============================================================================

def _as_int(value: Any, default: int, low: int, high: int) -> int:
    """Los LLM mandan números como texto, null o decimales: normalizar sin lanzar."""
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "sí", "si", "yes")
    return bool(value)


def run_shell(command: str, timeout: Any = SHELL_TIMEOUT, background: Any = False, cwd: str = "") -> Dict[str, Any]:
    """Ejecuta un comando en PowerShell (Windows) o Bash (Linux) dentro de `cwd`.

    Sin `cwd`, se usa la carpeta de trabajo (Documentos o ZARA_WORKSPACE), nunca la
    carpeta desde la que se lanzó Zara.
    """
    command = str(command or "")
    timeout = _as_int(timeout, SHELL_TIMEOUT, 1, SHELL_TIMEOUT_MAX)
    background = _as_bool(background)
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

    workdir = resolve_user_path(cwd) if cwd else workspace_dir()
    if not workdir.is_dir():
        return {
            "success": False,
            "stdout": "",
            "stderr": f"La carpeta de trabajo '{workdir}' no existe. Créala antes con file_manager (mkdir).",
            "exit_code": 2,
        }

    if sys.platform == "win32":
        command = _normalize_windows_command(command)

    log.info("Ejecutando shell [%s] en %s: %s", "Windows/PowerShell" if sys.platform == "win32" else "Linux/Bash", workdir, command)

    if sys.platform == "win32":
        shell_cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    else:
        shell_cmd = ["/bin/bash", "-c", command]

    if background:
        try:
            proc = subprocess.Popen(
                shell_cmd,
                cwd=str(workdir),
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
                "cwd": str(workdir),
            }
        except Exception as exc:
            return {"success": False, "stdout": "", "stderr": str(exc), "exit_code": 1}

    try:
        res = subprocess.run(
            shell_cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "success": res.returncode == 0,
            "blocked": False,
            "stdout": _clip(res.stdout.strip(), MAX_TOOL_OUTPUT_CHARS),
            "stderr": _clip(res.stderr.strip(), MAX_TOOL_OUTPUT_CHARS // 2),
            "exit_code": res.returncode,
            "cwd": str(workdir),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Tiempo de espera agotado ({timeout} s). Para tareas largas usa background=true o un timeout mayor (máx. {SHELL_TIMEOUT_MAX}).",
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

MAX_TOOL_OUTPUT_CHARS = 20000   # lo que se devuelve al modelo por llamada
MAX_READ_LINES = 400            # líneas por lectura (se puede paginar con start_line)
_SKIP_DIRS = {".git", "node_modules", "vendor", "__pycache__", ".venv", "venv", ".idea", ".vscode", "storage", "dist", "build"}
_MUTATING_ACTIONS = ("write", "append", "edit", "delete", "mkdir")
_FILE_ACTIONS = ("read", "write", "append", "edit", "mkdir", "list", "tree", "search", "delete", "exists")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... recortado: {len(text) - limit} caracteres más]"


def file_manager(
    action: str,
    path: str,
    content: str = "",
    old_str: str = "",
    new_str: str = "",
    replace_all: Any = False,
    start_line: Any = 1,
    max_lines: Any = MAX_READ_LINES,
    pattern: str = "",
) -> Dict[str, Any]:
    """Archivos y carpetas como un editor de código: leer por tramos, escribir, editar con
    precisión, crear carpetas, ver el árbol de un proyecto y buscar texto.

    Las rutas pueden ser absolutas, con ~ o variables de entorno, empezar por una carpeta
    personal («Documentos/mi-web») o ser relativas a la carpeta de trabajo.
    """
    action = str(action or "").lower().strip()
    target = resolve_user_path(str(path or ""))

    if action in _MUTATING_ACTIONS:
        protected = is_path_protected(target)
        if protected:
            log.warning("file_manager '%s' bloqueado sobre ruta protegida: %s", action, target)
            return {
                "success": False,
                "blocked": True,
                "error": f"ACCESO DENEGADO POR GUARDARRAÍL: '{target}' está dentro de la ruta protegida '{protected}'.",
            }

    try:
        if action == "read":
            if not target.is_file():
                return {"success": False, "error": f"El archivo '{target}' no existe o es un directorio."}
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            first = _as_int(start_line, 1, 1, max(1, len(lines)))
            count = _as_int(max_lines, MAX_READ_LINES, 1, 2000)
            chunk = lines[first - 1:first - 1 + count]
            numbered = "\n".join(f"{first + i:>5}  {line}" for i, line in enumerate(chunk))
            result = {
                "success": True,
                "path": str(target),
                "total_lines": len(lines),
                "lines": len(lines),
                "start_line": first,
                "content": _clip(numbered, MAX_TOOL_OUTPUT_CHARS),
            }
            if first - 1 + count < len(lines):
                result["next_start_line"] = first + count
            return result

        elif action == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            target.write_text(str(content), encoding="utf-8", newline="\n")
            verb = "sobrescrito" if existed else "creado"
            return {"success": True, "message": f"Archivo '{target}' {verb} ({len(str(content))} caracteres)."}

        elif action == "append":
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8", newline="\n") as f:
                f.write(str(content))
            return {"success": True, "message": f"Contenido añadido a '{target}'."}

        elif action == "edit":
            if not target.is_file():
                return {"success": False, "error": f"El archivo '{target}' no existe."}
            if not old_str:
                return {"success": False, "error": "Para editar indica old_str (texto exacto a sustituir)."}
            text = target.read_text(encoding="utf-8", errors="replace")
            count = text.count(old_str)
            if count == 0:
                return {"success": False, "error": f"El fragmento a reemplazar no fue encontrado en '{target}'. Léelo de nuevo y copia el texto exacto."}
            if count > 1 and not _as_bool(replace_all):
                return {
                    "success": False,
                    "error": f"El fragmento aparece {count} veces en '{target}'. Añade más contexto para que sea único o usa replace_all=true.",
                }
            updated = text.replace(old_str, str(new_str))
            target.write_text(updated, encoding="utf-8", newline="\n")
            return {"success": True, "message": f"Reemplazo quirúrgico aplicado en '{target}' ({count} ocurrencias)."}

        elif action == "mkdir":
            target.mkdir(parents=True, exist_ok=True)
            return {"success": True, "message": f"Carpeta '{target}' lista.", "path": str(target)}

        elif action == "list":
            if not target.is_dir():
                return {"success": False, "error": f"'{target}' no es un directorio válido."}
            items = []
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]:
                items.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size_bytes": item.stat().st_size if item.is_file() else None,
                })
            return {"success": True, "directory": str(target), "items": items}

        elif action == "tree":
            if not target.is_dir():
                return {"success": False, "error": f"'{target}' no es un directorio válido."}
            lines_out: List[str] = []
            limit = 400

            def walk(folder: Path, depth: int) -> None:
                if depth > 4 or len(lines_out) >= limit:
                    return
                try:
                    entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                except OSError:
                    return
                for entry in entries:
                    if len(lines_out) >= limit:
                        return
                    indent = "  " * depth
                    if entry.is_dir():
                        skipped = entry.name in _SKIP_DIRS
                        lines_out.append(f"{indent}{entry.name}/" + (" (omitida)" if skipped else ""))
                        if not skipped:
                            walk(entry, depth + 1)
                    else:
                        lines_out.append(f"{indent}{entry.name}")

            walk(target, 0)
            return {"success": True, "directory": str(target), "tree": "\n".join(lines_out), "truncated": len(lines_out) >= limit}

        elif action == "search":
            if not pattern:
                return {"success": False, "error": "Para buscar indica pattern (texto o expresión regular)."}
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                regex = re.compile(re.escape(pattern), re.IGNORECASE)
            files = [target] if target.is_file() else []
            if target.is_dir():
                for root, dirs, names in os.walk(target):
                    dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                    files.extend(Path(root) / n for n in names)
            matches = []
            for file in files:
                if len(matches) >= 100:
                    break
                try:
                    if file.stat().st_size > 1_000_000:
                        continue
                    for number, line in enumerate(file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if regex.search(line):
                            matches.append(f"{file}:{number}: {line.strip()[:200]}")
                            if len(matches) >= 100:
                                break
                except OSError:
                    continue
            return {"success": True, "matches": matches, "count": len(matches), "truncated": len(matches) >= 100}

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
            return {"success": True, "path": str(target), "exists": target.exists(), "is_file": target.is_file(), "is_dir": target.is_dir()}

        else:
            return {"success": False, "error": f"Acción desconocida '{action}'. Válidas: {', '.join(_FILE_ACTIONS)}."}

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


# [FIX] Hallazgo de auditoría en vivo: el Señor VK (y el modelo que lo
# transcribe/razona) usa el nombre en ESPAÑOL de la aplicación ("Bloc de
# notas", "Calculadora"), pero `shutil.which()` solo resuelve nombres de
# ejecutable en inglés. Sin este alias, open_app caía siempre al último
# fallback (ver más abajo), que antes reportaba éxito sin comprobar nada.
_WIN_APP_ALIASES: Dict[str, str] = {
    "bloc de notas": "notepad",
    "notas": "notepad",
    "calculadora": "calc",
    "explorador de archivos": "explorer",
    "explorador": "explorer",
    "panel de control": "control",
    "simbolo del sistema": "cmd",
    "símbolo del sistema": "cmd",
    "terminal": "wt",
    "administrador de tareas": "taskmgr",
    "pintor": "mspaint",
    "paint": "mspaint",
    "wordpad": "write",
    "editor del registro": "regedit",
    "configuracion": "ms-settings:",
    "configuración": "ms-settings:",
    "ajustes": "ms-settings:",
}


def _os_open_app(target: str, value: str) -> Dict[str, Any]:
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
        # [FIX] Resolver alias en español ANTES de shutil.which, que
        # solo conoce nombres de ejecutable en inglés.
        candidate = _WIN_APP_ALIASES.get(tgt.lower(), tgt)

        # Intentos de ejecución en Windows
        resolved = shutil.which(candidate) or shutil.which(f"{candidate}.exe")
        if resolved:
            subprocess.Popen([resolved])
            return {"success": True, "message": f"Aplicación '{tgt}' iniciada."}

        # Start Menu búsqueda rápida (por nombre original Y por alias)
        appdata = Path(os.environ.get("APPDATA", ""))
        progdata = Path(os.environ.get("ProgramData", ""))
        needles = {tgt.lower(), candidate.lower()}
        for base in [appdata / "Microsoft/Windows/Start Menu/Programs", progdata / "Microsoft/Windows/Start Menu/Programs"]:
            if base.is_dir():
                for lnk in base.rglob("*.lnk"):
                    stem_low = lnk.stem.lower()
                    if any(n in stem_low for n in needles):
                        os.startfile(str(lnk))
                        return {"success": True, "message": f"Aplicación '{lnk.stem}' iniciada desde el menú inicio."}

        # [FIX] Último recurso VERIFICABLE: os.startfile() usa el
        # mismo mecanismo que el menú de Inicio de Windows (ShellExecute,
        # entiende más alias que PATH) y lanza una excepción SÍNCRONA
        # si no encuentra nada que ejecutar. El fallback anterior
        # (Start-Process de PowerShell en un subprocess.Popen separado)
        # reportaba éxito SIEMPRE sin comprobar si el proceso hijo
        # realmente consiguió arrancar algo.
        try:
            os.startfile(candidate)
            return {"success": True, "message": f"Aplicación '{tgt}' iniciada."}
        except OSError as exc:
            return {
                "success": False,
                "error": f"No se encontró ninguna aplicación llamada '{tgt}': {exc}",
            }
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


def _os_close_app(target: str, value: str) -> Dict[str, Any]:
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


def _os_list_processes(target: str, value: str) -> Dict[str, Any]:
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


def _os_set_volume(target: str, value: str) -> Dict[str, Any]:
    try:
        pct = float(value or target)
    except ValueError:
        return {"success": False, "error": f"Valor de volumen inválido: '{value or target}'"}
    if sys.platform == "win32":
        msg = _set_windows_volume(pct)
    else:
        msg = _set_linux_volume(pct)
    return {"success": True, "message": msg}


def _os_mute(target: str, value: str) -> Dict[str, Any]:
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


def _os_unmute(target: str, value: str) -> Dict[str, Any]:
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


def _os_read_clipboard(target: str, value: str) -> Dict[str, Any]:
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


def _os_write_clipboard(target: str, value: str) -> Dict[str, Any]:
    text_to_copy = value or target
    if sys.platform == "win32":
        # Literal entre comillas simples de PowerShell: la comilla simple se duplica.
        ps_text = text_to_copy.replace("'", "''")
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", f"Set-Clipboard -Value '{ps_text}'"])
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


def _os_system_stats(target: str, value: str) -> Dict[str, Any]:
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


def _os_network_info(target: str, value: str) -> Dict[str, Any]:
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


def _os_media_play_pause(target: str, value: str) -> Dict[str, Any]:
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


def _os_media_next(target: str, value: str) -> Dict[str, Any]:
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


def _os_media_prev(target: str, value: str) -> Dict[str, Any]:
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


def _os_show_desktop(target: str, value: str) -> Dict[str, Any]:
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


def _os_lock_screen(target: str, value: str) -> Dict[str, Any]:
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


def _os_notify(target: str, value: str) -> Dict[str, Any]:
    msg_text = value or target or "Notificación táctica de Zara"
    if sys.platform == "win32":
        msg_text = msg_text.replace("'", "''")  # evita inyección en el literal de PowerShell
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


_OS_ACTIONS: Dict[str, Callable[[str, str], Dict[str, Any]]] = {
    "open_app": _os_open_app,
    "close_app": _os_close_app,
    "list_processes": _os_list_processes,
    "set_volume": _os_set_volume,
    "mute": _os_mute,
    "unmute": _os_unmute,
    "read_clipboard": _os_read_clipboard,
    "write_clipboard": _os_write_clipboard,
    "system_stats": _os_system_stats,
    "system_telemetry": _os_system_stats,
    "telemetry": _os_system_stats,
    "network_info": _os_network_info,
    "network_check": _os_network_info,
    "ip": _os_network_info,
    "media_play_pause": _os_media_play_pause,
    "play_pause": _os_media_play_pause,
    "play": _os_media_play_pause,
    "pause": _os_media_play_pause,
    "media_next": _os_media_next,
    "next_track": _os_media_next,
    "media_prev": _os_media_prev,
    "prev_track": _os_media_prev,
    "show_desktop": _os_show_desktop,
    "minimize_all": _os_show_desktop,
    "lock_screen": _os_lock_screen,
    "lock": _os_lock_screen,
    "notify": _os_notify,
}


def os_control(action: str, target: str = "", value: str = "") -> Dict[str, Any]:
    """Control de aplicaciones, volumen, procesos y portapapeles."""
    action = action.lower().strip()
    handler = _OS_ACTIONS.get(action)
    if handler is None:
        return {"success": False, "error": f"Acción de SO desconocida '{action}'."}
    try:
        return handler(target, value)
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
            "-p", cli_safe_arg(claude_bin, instruction),
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
    """Busca en la web en tiempo real para obtener información actualizada (paquete `ddgs`)."""
    q = query.strip()
    if not q:
        return {"success": False, "error": "Consulta de búsqueda vacía."}
    try:
        max_results = max(1, min(int(max_results), 10))
    except (TypeError, ValueError):
        max_results = 5

    log.info("Buscando en la web: '%s'", q)
    try:
        from ddgs import DDGS

        raw = DDGS(timeout=8).text(q, region="es-es", max_results=max_results) or []
        results = [
            {
                "title": r.get("title", "") or f"Resultado {i + 1}",
                "snippet": r.get("body", ""),
                "url": r.get("href", ""),
            }
            for i, r in enumerate(raw)
        ]
        return {"success": True, "query": q, "results_count": len(results), "results": results}
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
                "cwd": {"type": "string", "description": "Carpeta donde ejecutarlo (por defecto, Documentos). Admite «Documentos/mi-web»."},
                "timeout": {"type": "integer", "description": "Tiempo límite en segundos (por defecto 60, máximo 900)."},
                "background": {"type": "boolean", "description": "True para lanzar procesos de segundo plano (servidores)."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "file_manager",
        "description": (
            "Archivos y carpetas como un editor de código. Rutas absolutas, con ~ o empezando por "
            "Documentos/Escritorio/Descargas. Acciones: read (por tramos de líneas), write (crea o "
            "sobrescribe), append, edit (sustituye old_str EXACTO y único por new_str), mkdir, list, "
            "tree (árbol del proyecto), search (busca pattern en archivos), delete, exists."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write", "append", "edit", "mkdir", "list", "tree", "search", "delete", "exists"]},
                "path": {"type": "string", "description": "Ruta del archivo o carpeta (p. ej. Documentos/mi-web/index.html)."},
                "content": {"type": "string", "description": "Contenido completo para write o append."},
                "old_str": {"type": "string", "description": "Texto exacto a reemplazar (edit). Debe ser único salvo replace_all."},
                "new_str": {"type": "string", "description": "Texto nuevo (edit)."},
                "replace_all": {"type": "boolean", "description": "edit: reemplazar todas las apariciones."},
                "start_line": {"type": "integer", "description": "read: primera línea (1 por defecto)."},
                "max_lines": {"type": "integer", "description": "read: número de líneas (400 por defecto)."},
                "pattern": {"type": "string", "description": "search: texto o expresión regular a buscar."},
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


def format_tools_for_local_prompt(tools: Optional[List[Dict[str, Any]]] = None) -> str:
    """Genera una especificación compacta y rigurosa de las herramientas para modelos locales."""
    t_list = tools or TOOL_DEFINITIONS
    lines = ["HERRAMIENTAS DEL SISTEMA DISPONIBLES:"]
    for t in t_list:
        name = t.get("name", "")
        desc = t.get("description", "")
        props = (t.get("parameters", {}) or {}).get("properties", {}) or {}
        param_list = []
        for p_name, p_info in props.items():
            enum_vals = p_info.get("enum")
            p_type = p_info.get("type", "str")
            if enum_vals:
                param_list.append(f"{p_name} [{'|'.join(enum_vals[:12])}]")
            else:
                param_list.append(f"{p_name}: {p_type}")
        params_str = ", ".join(param_list)
        lines.append(f"* {name}({params_str}): {desc}")
    return "\n".join(lines)


_ACTION_ALIAS_CACHE: Optional[Dict[str, Tuple[str, str]]] = None


def _get_action_alias_map() -> Dict[str, Tuple[str, str]]:
    """[FIX] Hallazgo de auditoría en vivo contra Ollama real: con modelos
    locales pequeños, el modelo a veces confunde un VALOR del enum "action"
    de una herramienta genérica (p. ej. "open_app", parte de `os_control`)
    con el NOMBRE de la propia herramienta, llamando a
    {"name": "open_app", "arguments": {...}} en vez de
    {"name": "os_control", "arguments": {"action": "open_app", ...}}.

    Construye un mapa inverso {valor_de_enum -> (herramienta_real, parametro)}
    a partir de TOOL_DEFINITIONS para corregirlo en tiempo de ejecución, en
    vez de rechazar la llamada como "herramienta desconocida" (lo que antes
    hacía que Zara leyera el JSON crudo en voz alta sin ejecutar nada)."""
    global _ACTION_ALIAS_CACHE
    if _ACTION_ALIAS_CACHE is None:
        alias_map: Dict[str, Tuple[str, str]] = {}
        for t in TOOL_DEFINITIONS:
            tool_name = t.get("name", "")
            props = (t.get("parameters", {}) or {}).get("properties", {}) or {}
            for param_name, schema in props.items():
                for enum_value in (schema.get("enum") or []):
                    alias_map.setdefault(enum_value, (tool_name, param_name))
        _ACTION_ALIAS_CACHE = alias_map
    return _ACTION_ALIAS_CACHE


def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Enrutador de ejecución de herramientas."""
    if _confirm_handler is not None:
        description = needs_confirmation(name, args)
        if description:
            try:
                confirmed = bool(_confirm_handler(description))
            except Exception as exc:  # ante la duda, no ejecutar
                log.warning("Fallo pidiendo confirmación: %s", exc)
                confirmed = False
            if not confirmed:
                log.info("Acción no confirmada, cancelada: %s %s", name, description)
                return {
                    "success": False,
                    "blocked": True,
                    "error": f"Acción cancelada: el Señor VK no confirmó {description}.",
                }
    name = name.strip()
    # [FIX] Hallazgo de Gemini: si un proveedor nativo (Gemini/Claude) alucina
    # una llamada sin parámetros, `args`/`input` puede llegar como None en vez
    # de {}. Los `args.get(...)` de más abajo lanzarían AttributeError y
    # romperían el generador de run_react_agent sin control en ese punto.
    args = args or {}

    known_names = {t.get("name", "") for t in TOOL_DEFINITIONS}
    if name not in known_names:
        alias = _get_action_alias_map().get(name)
        if alias:
            real_name, param_name = alias
            log.info(
                "Herramienta '%s' no reconocida; remapeada a %s(%s='%s').",
                name, real_name, param_name, name,
            )
            args = {param_name: name, **(args or {})}
            name = real_name

    if name == "run_shell":
        return run_shell(
            command=args.get("command", ""),
            timeout=args.get("timeout", SHELL_TIMEOUT),
            background=args.get("background", False),
            cwd=args.get("cwd") or "",
        )
    elif name == "file_manager":
        return file_manager(
            action=args.get("action", "read"),
            path=args.get("path", ""),
            content=args.get("content") or "",
            old_str=args.get("old_str") or "",
            new_str=args.get("new_str") or "",
            replace_all=args.get("replace_all", False),
            start_line=args.get("start_line", 1),
            max_lines=args.get("max_lines", MAX_READ_LINES),
            pattern=args.get("pattern") or "",
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
