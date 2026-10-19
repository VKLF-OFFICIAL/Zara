"""Subsistema de Herramientas y Agente Autónomo (ZARA).

Política de ejecución silenciosa (YOLO Seguro), guardarraíl contra comandos
destructivos catastróficos, soporte nativo multiplataforma (Windows y Linux)
y bucle ReAct con autocorrección automática de hasta 8 iteraciones.
"""

from __future__ import annotations
import base64
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import psutil

from core.workspace import resolve_user_path, user_folders, workspace_dir
from config import (
    BASE_DIR,
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


# Por encima de esta longitud (o si encadena varias órdenes) la descripción avisa de que
# conviene leerla entera en el panel: la voz puede resumir, pero nunca se recorta el texto.
_LONG_DESCRIPTION = 100
_COMPOUND_RE = re.compile(r"[;&|`\n]|\$\(")


def _describe_long(prefix: str, text: str, kind: str) -> str:
    if len(text) > _LONG_DESCRIPTION or _COMPOUND_RE.search(text):
        return f"{prefix} {kind} de {len(text)} caracteres (texto completo en el panel): {text}"
    return f"{prefix} {kind} {text}"


def needs_confirmation(name: str, args: Dict[str, Any]) -> Optional[str]:
    """Devuelve una descripción hablable si la llamada requiere confirmación, o None.

    Normaliza nombre, alias y acción igual que `execute_tool`, de modo que lo que se
    pregunta es exactamente lo que se va a ejecutar. La descripción va COMPLETA (sin
    recortar): la parte peligrosa de un comando suele ir al final.
    """
    name, args = _normalize_call(name, args)
    action = str(args.get("action") or "").strip().lower()
    if name == "run_shell":
        cmd = str(args.get("command") or "")
        for pattern in CONFIRM_COMMANDS_REGEX:
            if re.search(pattern, cmd, re.IGNORECASE):
                return _describe_long("ejecutar", cmd, "el comando")
    elif name == "file_manager":
        raw_path = str(args.get("path") or "")
        if not raw_path.strip():
            return None  # file_manager rechaza las rutas vacías sin tocar nada
        if action == "delete":
            return _describe_delete(raw_path)
        target = resolve_user_path(raw_path)
        if action in ("read", "search") and _is_secret_path(target):
            return f"leer {target}, que puede contener contraseñas o claves privadas"
        if action in _MUTATING_ACTIONS and _is_startup_path(target):
            return f"modificar {target}, que se ejecuta automáticamente al iniciar sesión"
    elif name == "os_control" and action == "close_app":
        target = str(args.get("target") or "").strip()
        if len(_process_key(target)) < _MIN_PROCESS_NAME:
            return None  # _os_close_app lo rechaza sin cerrar nada
        return f"cerrar la aplicación {target}"
    elif name == "invoke_claude_code":
        target_dir, _reason = _claude_code_dir(args.get("project_path"))
        if target_dir is None:
            return None  # invoke_claude_code rechaza la carpeta sin ejecutar nada
        instruction = str(args.get("instruction") or "").strip()
        return _describe_long(
            f"delegar en Claude Code, con permisos completos, en la carpeta {target_dir},", instruction, "la tarea"
        )
    return None


def _describe_delete(raw_path: str) -> Optional[str]:
    """Qué se va a borrar de verdad: el enlace (no su destino) o la ruta real resuelta."""
    link = _link_path(raw_path)
    if link is not None:
        if _protected_match(_lexical(link), ancestors=True):
            return None  # el guardarraíl lo bloquea sin preguntar
        return f"eliminar el enlace {link} (su destino no se toca)"
    target = resolve_user_path(raw_path)
    if is_path_protected(target, ancestors=True):
        return None
    if target.is_dir():
        return f"eliminar la carpeta {target} y todo su contenido"
    return f"eliminar {target}"


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


def _extra_protected_trees() -> List[str]:
    """Árboles protegidos que se calculan aquí (además de config.PROTECTED_TREES).

    - La propia instalación de Zara: su código, su .env con las claves y su .git. Una
      inyección de prompt que reescriba el .env (OLLAMA_HOST de un atacante) o los
      guardarraíles tomaría el control del asistente en el siguiente arranque.
    - Windows: Program Files y ProgramData según el entorno (no siempre están en C:).
    """
    trees = [str(BASE_DIR)]
    if sys.platform == "win32":
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
            value = os.environ.get(var, "").strip()
            if value:
                trees.append(value)
    else:
        trees.append("/var/lib")  # dpkg, rpm, bases de datos del sistema (Kali como root)
    return trees


def _extra_protected_exact() -> List[str]:
    """Carpetas que no se pueden borrar ni sobrescribir enteras, aunque sí trabajar dentro:
    las carpetas personales (Documentos, Escritorio...), la de trabajo y, en Linux, los
    puntos de montaje habituales."""
    exact = [str(p) for p in user_folders().values()]
    exact.append(str(workspace_dir()))
    if sys.platform != "win32":
        exact += ["/opt", "/mnt", "/media", "/srv", "/run/media"]
    return exact


def _mount_points() -> List[Path]:
    """Puntos de montaje de discos reales (unidades D:, E:... o /media/vk/USB)."""
    try:
        return [Path(os.path.realpath(p.mountpoint)) for p in psutil.disk_partitions(all=False)]
    except Exception:
        return []


def _protected_match(resolved: Path, ancestors: bool = False, exact: bool = True) -> Optional[str]:
    """Compara una ruta YA resuelta con las rutas protegidas.

    Con `ancestors=True` (borrados y Claude Code) también se bloquea una ruta que CONTIENE
    una protegida: borrar C:\\Users o /home se llevaría el HOME, y borrar la carpeta de un
    proyecto que tiene dentro Zara se llevaría su .git. También las raíces de unidad y los
    puntos de montaje: shutil.rmtree no se para en la frontera de un disco montado.
    Con `exact=False` (mkdir) se ignoran las rutas exactas: crear una carpeta que ya
    existe no destruye nada.
    """
    exact_paths = (list(PROTECTED_EXACT) + _extra_protected_exact()) if exact else []
    trees = list(PROTECTED_TREES) + _extra_protected_trees()
    for prot in exact_paths:
        prot_path = Path(os.path.realpath(prot))
        if resolved == prot_path or (ancestors and resolved in prot_path.parents):
            return prot
    for prot in trees:
        prot_path = Path(os.path.realpath(prot))
        if resolved == prot_path or prot_path in resolved.parents:
            return prot
        if ancestors and resolved in prot_path.parents:
            return prot
    if ancestors:
        if resolved.parent == resolved or os.path.ismount(resolved):
            return str(resolved)
        for mount in _mount_points():
            if resolved in mount.parents:
                return str(mount)
    return None


def is_path_protected(path: Path, ancestors: bool = False) -> Optional[str]:
    """Devuelve la ruta protegida que contiene (o es) `path`, o None si es seguro tocarla.

    `ancestors=True` bloquea además las rutas que contienen una protegida (ver
    `_protected_match`); se usa para borrar y para dar permisos completos a Claude Code.
    """
    return _protected_match(Path(os.path.realpath(path)), ancestors)


def _lexical(link: Path) -> Path:
    """Ruta real de la carpeta que contiene `link`, sin seguir el propio enlace."""
    return Path(os.path.realpath(link.parent)) / link.name


def _link_path(path: str) -> Optional[Path]:
    """Si `path` (tal como lo dio el modelo) es un enlace simbólico o una junction de
    Windows, devuelve la ruta del PROPIO enlace sin seguirlo; si no, None.

    resolve_user_path() resuelve los enlaces, así que «borra ~/web/public/storage» (el
    enlace de Laravel) acababa haciendo rmtree de storage/app/public, el destino real.
    """
    raw = os.path.expanduser(os.path.expandvars(str(path or "").strip().strip('"').strip("'")))
    if not raw:
        return None
    p = Path(raw)
    if not p.name:
        return None  # raíz de unidad
    link = resolve_user_path(str(p.parent)) / p.name
    is_junction = getattr(os.path, "isjunction", None)
    try:
        if link.is_symlink() or (is_junction is not None and is_junction(link)):
            return link
    except OSError:
        return None
    return None


# --- Secretos y archivos de arranque -------------------------------------------
# Leer estos archivos pide confirmación: una inyección de prompt (web, README, pantalla)
# podría hacer que el modelo lea una clave y la saque por una URL o una búsqueda.
_SECRET_NAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".pgpass", ".git-credentials",
    "logins.json", "key4.db", "login data", "shadow", "gshadow",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore", ".jks")
_ENV_NOT_SECRET = (".example", ".sample", ".dist", ".template")


def _secret_dirs() -> List[Path]:
    home = Path.home()
    dirs = [
        home / ".ssh", home / ".gnupg", home / ".aws", home / ".azure", home / ".kube",
        home / ".config" / "gcloud", home / ".password-store", home / ".local" / "share" / "keyrings",
        home / ".mozilla" / "firefox", home / ".config" / "google-chrome", home / ".config" / "chromium",
        home / ".config" / "BraveSoftware", home / ".docker",
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            dirs += [Path(appdata) / "Microsoft" / "Credentials", Path(appdata) / "Microsoft" / "Protect",
                     Path(appdata) / "Mozilla" / "Firefox" / "Profiles"]
        if local:
            dirs += [Path(local) / "Microsoft" / "Credentials", Path(local) / "Google" / "Chrome" / "User Data",
                     Path(local) / "Microsoft" / "Edge" / "User Data", Path(local) / "BraveSoftware"]
    return [Path(os.path.realpath(d)) for d in dirs]


def _is_secret_name(name: str) -> bool:
    """Claves privadas, almacenes de contraseñas y archivos .env (no sus plantillas)."""
    name = name.lower()
    if name in _SECRET_NAMES or name.endswith(_SECRET_SUFFIXES):
        return True
    if name.startswith(("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")) and not name.endswith(".pub"):
        return True
    return (name == ".env" or name.startswith(".env.") or name.endswith(".env")) and not name.endswith(_ENV_NOT_SECRET)


def _in_dirs(resolved: Path, dirs: List[Path]) -> bool:
    return any(resolved == d or d in resolved.parents for d in dirs)


def _is_secret_path(path: Path) -> bool:
    """¿Es (o está dentro de) un almacén de claves, credenciales o un archivo .env?"""
    resolved = Path(os.path.realpath(path))
    return _is_secret_name(resolved.name) or _in_dirs(resolved, _secret_dirs())


def _is_startup_path(path: Path) -> bool:
    """¿Es un archivo que se ejecuta solo al iniciar sesión (persistencia)?"""
    home = Path.home()
    resolved = Path(os.path.realpath(path))
    files = [home / n for n in (".bashrc", ".bash_profile", ".bash_login", ".profile", ".zshrc",
                                ".zprofile", ".xprofile", ".xinitrc", ".xsessionrc")]
    files.append(home / ".config" / "fish" / "config.fish")
    dirs = [home / ".config" / "autostart", home / ".config" / "systemd", home / ".config" / "environment.d",
            home / ".local" / "share" / "applications"]
    if sys.platform == "win32":
        docs = user_folders().get("documentos", home / "Documents")
        dirs += [docs / "WindowsPowerShell", docs / "PowerShell"]
        for var in ("APPDATA", "ProgramData"):
            base = os.environ.get(var, "")
            if base:
                dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")
    if any(resolved == Path(os.path.realpath(f)) for f in files):
        return True
    for d in dirs:
        d = Path(os.path.realpath(d))
        if resolved == d or d in resolved.parents:
            return True
    return False


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


def _as_text(value: Any, default: str = "") -> str:
    """Los LLM mandan null, números u objetos JSON donde se espera texto: normalizar.

    Un objeto (p. ej. el contenido de un package.json enviado como dict) se serializa
    como JSON legible en vez de como repr de Python.
    """
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


# --- Subprocesos con muerte del árbol completo -----------------------------------
# subprocess.run(timeout=...) solo mata al shell: los nietos (php artisan serve, npm run
# dev, ping -t, el node.exe de un shim .cmd) siguen vivos con el pipe de salida abierto.
# En Windows, run() vuelve a llamar a communicate() SIN timeout y el turno se cuelga para
# siempre; en Linux quedan huérfanos ocupando puertos o editando archivos.
_WIN_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_WIN_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

# PowerShell 5.1 escribe la salida redirigida en la página OEM (cp850): 'Diseño' llegaba
# como 'Dise¤o'. Se fuerza UTF-8 sin BOM (el try cubre el caso sin consola asociada).
_PS_UTF8_PREFIX = (
    "try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}; "
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)


def kill_process_tree(proc: "subprocess.Popen") -> None:
    """Mata `proc` y todos sus descendientes (Linux: grupo de procesos; Windows: taskkill /T)."""
    try:
        descendants = psutil.Process(proc.pid).children(recursive=True)
    except psutil.Error:
        descendants = []
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, creationflags=_WIN_NO_WINDOW,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    else:
        try:
            pgid = os.getpgid(proc.pid)
            if pgid != os.getpgrp():  # nunca el grupo de la propia Zara
                os.killpg(pgid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except OSError:
            pass
    # Respaldo: descendientes que se hayan escapado del grupo (setsid, daemons)
    for child in descendants:
        try:
            child.kill()
        except psutil.Error:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_capture(cmd: List[str], timeout: float, cwd: Optional[str] = None) -> Tuple[int, str, str, bool]:
    """Ejecuta `cmd` capturando la salida en UTF-8 y, si vence `timeout`, mata el árbol
    entero. Devuelve (código, stdout, stderr, agotado)."""
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if cwd:
        kwargs["cwd"] = cwd
    if sys.platform == "win32":
        kwargs["creationflags"] = _WIN_NEW_GROUP
    else:
        kwargs["start_new_session"] = True  # grupo propio: killpg alcanza a todos los nietos
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, (out or "").lstrip("\ufeff"), err or "", False
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            out, err = "", ""
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe:
                        pipe.close()
                except OSError:
                    pass
        return 124, out or "", err or "", True
    except BaseException:
        kill_process_tree(proc)
        raise


def run_shell(command: str, timeout: Any = SHELL_TIMEOUT, background: Any = False, cwd: str = "") -> Dict[str, Any]:
    """Ejecuta un comando en PowerShell (Windows) o Bash (Linux) dentro de `cwd`.

    Sin `cwd`, se usa la carpeta de trabajo (Documentos o ZARA_WORKSPACE), nunca la
    carpeta desde la que se lanzó Zara.
    """
    command = _as_text(command)
    cwd = _as_text(cwd)
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
        # El prefijo UTF-8 se añade DESPUÉS del guardarraíl, que mira el comando original.
        shell_cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-Command", _PS_UTF8_PREFIX + command]
    else:
        shell_cmd = ["/bin/bash", "-c", command]

    if background:
        try:
            if sys.platform == "win32":
                # Grupo propio y sin consola: el servidor no comparte la ventana de Zara ni
                # recibe su Ctrl+C (start_new_session no tiene efecto en Windows).
                detach: Dict[str, Any] = {"creationflags": _WIN_NEW_GROUP | _WIN_NO_WINDOW}
            else:
                detach = {"start_new_session": True}
            proc = subprocess.Popen(
                shell_cmd,
                cwd=str(workdir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                **detach,
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
        code, out, err, timed_out = _run_capture(shell_cmd, timeout, cwd=str(workdir))
        if timed_out:
            return {
                "success": False,
                "stdout": _clip(out.strip(), MAX_TOOL_OUTPUT_CHARS),
                "stderr": (
                    f"Tiempo de espera agotado ({timeout} s); el comando y sus subprocesos se han detenido. "
                    f"Para servidores o tareas largas usa background=true o un timeout mayor (máx. {SHELL_TIMEOUT_MAX})."
                    + (f"\n{_clip(err.strip(), MAX_TOOL_OUTPUT_CHARS // 2)}" if err.strip() else "")
                ),
                "exit_code": 124,
                "cwd": str(workdir),
            }
        return {
            "success": code == 0,
            "blocked": False,
            "stdout": _clip(out.strip(), MAX_TOOL_OUTPUT_CHARS),
            "stderr": _clip(err.strip(), MAX_TOOL_OUTPUT_CHARS // 2),
            "exit_code": code,
            "cwd": str(workdir),
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
    raw_path = _as_text(path)
    content, old_str, new_str, pattern = (_as_text(v) for v in (content, old_str, new_str, pattern))

    # Una ruta vacía resuelve a la carpeta de trabajo (Documentos): «delete» con path
    # vacío haría rmtree de Documentos entero.
    if action in _MUTATING_ACTIONS and not raw_path.strip():
        return {"success": False, "error": f"Para '{action}' indica la ruta (path) del archivo o carpeta."}

    if action == "delete":
        link = _link_path(raw_path)
        if link is not None:
            return _delete_link(link)

    target = resolve_user_path(raw_path)

    if action in _MUTATING_ACTIONS:
        if action == "delete":
            protected = is_path_protected(target, ancestors=True)
        else:
            protected = _protected_match(Path(os.path.realpath(target)), exact=(action != "mkdir"))
        if protected:
            log.warning("file_manager '%s' bloqueado sobre ruta protegida: %s", action, target)
            return {
                "success": False,
                "blocked": True,
                "error": f"ACCESO DENEGADO POR GUARDARRAÍL: '{target}' está dentro de (o contiene) la ruta protegida '{protected}'.",
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
            # newline="" conserva los CRLF de Windows; sin errors="replace", un archivo que
            # no es UTF-8 (cp1252 con ñ/á) se rechaza en vez de reescribirse con U+FFFD.
            try:
                with open(target, encoding="utf-8", newline="") as f:
                    text = f.read()
            except UnicodeDecodeError:
                return {
                    "success": False,
                    "error": f"'{target}' no está en UTF-8: no se edita para no corromperlo. Conviértelo antes o usa write.",
                }
            new_str = str(new_str)
            # El modelo escribe con \n: en un archivo con CRLF se adapta el fragmento.
            crlf = text.count("\r\n") * 2 > text.count("\n")
            if crlf and "\r\n" not in old_str:
                old_str = old_str.replace("\n", "\r\n")
            if crlf:
                new_str = new_str.replace("\r\n", "\n").replace("\n", "\r\n")
            count = text.count(old_str)
            if count == 0:
                return {"success": False, "error": f"El fragmento a reemplazar no fue encontrado en '{target}'. Léelo de nuevo y copia el texto exacto."}
            if count > 1 and not _as_bool(replace_all):
                return {
                    "success": False,
                    "error": f"El fragmento aparece {count} veces en '{target}'. Añade más contexto para que sea único o usa replace_all=true.",
                }
            updated = text.replace(old_str, new_str)
            with open(target, "w", encoding="utf-8", newline="") as f:
                f.write(updated)
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
                # Al recorrer carpetas no se leen .env ni claves: solo con una lectura
                # explícita (que pide confirmación) se ven sus contenidos.
                inside_secret = _is_secret_path(target)
                secret_dirs = _secret_dirs()
                for root, dirs, names in os.walk(target):
                    dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                    if not inside_secret and _in_dirs(Path(os.path.realpath(root)), secret_dirs):
                        dirs[:] = []
                        continue
                    files.extend(Path(root) / n for n in names if inside_secret or not _is_secret_name(n))
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
            # Los enlaces ya se han tratado arriba (_delete_link): aquí target es real.
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


def _delete_link(link: Path) -> Dict[str, Any]:
    """Borra un enlace simbólico o una junction SIN tocar el contenido al que apunta."""
    protected = _protected_match(_lexical(link), ancestors=True)
    if protected:
        log.warning("file_manager 'delete' bloqueado sobre enlace protegido: %s", link)
        return {
            "success": False,
            "blocked": True,
            "error": f"ACCESO DENEGADO POR GUARDARRAÍL: '{link}' es la ruta protegida '{protected}'.",
        }
    try:
        try:
            os.unlink(link)  # en Windows también quita enlaces de carpeta y junctions
        except (IsADirectoryError, PermissionError):
            os.rmdir(link)  # junction que unlink no acepta: rmdir no entra en el destino
        return {"success": True, "message": f"Enlace '{link}' eliminado (su destino no se ha tocado)."}
    except OSError as exc:
        return {"success": False, "error": f"No se pudo eliminar el enlace '{link}': {exc}"}


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


# --- Linux: decir la verdad sobre lo que se ha hecho -----------------------------
# Antes, las ramas de Linux devolvían éxito aunque faltara la herramienta del sistema o
# esta fallara (Zara decía «Sesión bloqueada» con el equipo abierto). Ahora se prueba
# cada alternativa, se mira el código de salida y, si nada funciona, se dice qué instalar.

def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _first_ok(commands: List[List[str]], timeout: float = 5) -> Optional[str]:
    """Ejecuta, por orden, los comandos cuya herramienta exista hasta que uno termine con
    código 0. Devuelve el nombre de la herramienta que funcionó, o None.

    La salida va a DEVNULL: así un proceso que se queda en segundo plano (xdg-screensaver,
    un salvapantallas) no retiene el pipe y no bloquea el turno.
    """
    for cmd in commands:
        if not shutil.which(cmd[0]):
            continue
        try:
            res = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            return cmd[0]
    return None


def _set_linux_volume(pct: float) -> Tuple[bool, str]:
    """Ajusta volumen en Linux con wpctl (PipeWire), pactl (PulseAudio) o amixer (ALSA)."""
    target = max(0, min(100, int(pct)))
    tool = _first_ok([
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{target / 100:.2f}"],
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{target}%"],
        ["amixer", "-q", "sset", "Master", f"{target}%"],
    ])
    if tool:
        return True, f"Volumen ajustado al {target}% ({tool})."
    return False, "No se pudo ajustar el volumen: instale wireplumber (wpctl), pulseaudio-utils (pactl) o alsa-utils (amixer)."


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


# Linux: nombres en español -> ejecutables o ids .desktop habituales (GNOME, KDE, Xfce...).
_LINUX_EDITORS = ["gnome-text-editor", "gedit", "kate", "kwrite", "mousepad", "xed", "pluma", "org.gnome.TextEditor"]
_LINUX_FILES = ["nautilus", "dolphin", "thunar", "nemo", "pcmanfm", "caja", "org.gnome.Nautilus"]
_LINUX_SETTINGS = ["gnome-control-center", "systemsettings", "xfce4-settings-manager"]
_LINUX_APP_ALIASES: Dict[str, List[str]] = {
    "calculadora": ["gnome-calculator", "kcalc", "galculator", "mate-calc", "xcalc", "org.gnome.Calculator"],
    "bloc de notas": _LINUX_EDITORS,
    "notas": _LINUX_EDITORS,
    "editor de texto": _LINUX_EDITORS,
    "editor": _LINUX_EDITORS,
    "terminal": ["x-terminal-emulator", "gnome-terminal", "kgx", "konsole", "xfce4-terminal", "qterminal",
                 "mate-terminal", "xterm", "org.gnome.Console", "org.gnome.Terminal"],
    "navegador": ["x-www-browser", "firefox", "firefox-esr", "chromium", "chromium-browser", "google-chrome"],
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "explorador de archivos": _LINUX_FILES,
    "explorador": _LINUX_FILES,
    "archivos": _LINUX_FILES,
    "gestor de archivos": _LINUX_FILES,
    "administrador de tareas": ["gnome-system-monitor", "plasma-systemmonitor", "ksysguard", "xfce4-taskmanager"],
    "monitor del sistema": ["gnome-system-monitor", "plasma-systemmonitor", "ksysguard", "xfce4-taskmanager"],
    "configuracion": _LINUX_SETTINGS,
    "configuración": _LINUX_SETTINGS,
    "ajustes": _LINUX_SETTINGS,
    "visual studio code": ["code", "codium"],
    "vscode": ["code", "codium"],
}

# open_app abre aplicaciones, no ejecuta scripts ni instaladores: eso pasa por run_shell,
# que tiene guardarraíl y confirmación (un .bat escrito con file_manager y lanzado aquí
# se los saltaba).
_BLOCKED_OPEN_SUFFIXES = {
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".scr",
    ".pif", ".com", ".reg", ".msi", ".desktop", ".sh", ".run", ".appimage", ".py", ".pyw", ".jar",
}
_APP_ID_RE = re.compile(r"[\w.+-]+")


def _open_linux_app(tgt: str) -> Dict[str, Any]:
    """Linux: alias en español, .desktop (gtk-launch) y PATH, comprobando el resultado."""
    candidates = [tgt] + [c for c in _LINUX_APP_ALIASES.get(tgt.lower(), []) if c != tgt]
    app_ids = [c for c in candidates if _APP_ID_RE.fullmatch(c) and not is_command_forbidden(c)]

    # 1) Lanzador .desktop: abre también apps de terminal (htop) en una terminal.
    if shutil.which("gtk-launch"):
        for app_id in app_ids:
            try:
                res = subprocess.run(
                    ["gtk-launch", app_id],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
            except subprocess.TimeoutExpired:
                return {"success": True, "message": f"Lanzado '{app_id}' vía gtk-launch."}
            except OSError:
                break
            if res.returncode == 0:
                return {"success": True, "message": f"Lanzado '{app_id}' vía gtk-launch."}

    # 2) Ejecutable del PATH (sin argumentos; nunca rutas ni apagados)
    for app_id in app_ids:
        exe = shutil.which(app_id)
        if not exe:
            continue
        proc = subprocess.Popen(
            [exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            code = proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            code = None  # sigue abierta: ha arrancado
        if code not in (None, 0):
            return {"success": False, "error": f"'{app_id}' se cerró nada más arrancar (código {code})."}
        return {"success": True, "message": f"Lanzado '{app_id}'."}

    # 3) Un archivo o carpeta que existe: con su programa por defecto
    path = Path(os.path.expanduser(tgt))
    if path.exists() and shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return {"success": True, "message": f"'{path}' abierto con su aplicación predeterminada."}

    return {
        "success": False,
        "error": f"No se encontró ninguna aplicación llamada '{tgt}' (ni en el menú de aplicaciones ni en el PATH).",
    }


def _os_open_app(target: str, value: str) -> Dict[str, Any]:
    tgt = target.strip()
    if not tgt:
        return {"success": False, "error": "Debes especificar la aplicación o URL a abrir."}

    if tgt.startswith("http://") or tgt.startswith("https://"):
        if sys.platform == "win32":
            os.startfile(tgt)
        else:
            subprocess.Popen(["xdg-open", tgt], stdin=subprocess.DEVNULL, start_new_session=True)
        return {"success": True, "message": f"URL '{tgt}' abierta en el navegador."}

    # Windows ignora los puntos y espacios finales («a.bat.» es a.bat)
    if Path(tgt.rstrip(". ")).suffix.lower() in _BLOCKED_OPEN_SUFFIXES:
        return {
            "success": False,
            "blocked": True,
            "error": "Por seguridad, open_app no ejecuta scripts ni instaladores; usa run_shell, que pide confirmación cuando hace falta.",
        }

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
        # Linux (KDE, GNOME, XFCE...): solo se informa de éxito si algo arrancó de verdad.
        return _open_linux_app(tgt)


# close_app compara NOMBRES de proceso, no subcadenas: con target vacío, '' in nombre era
# True para todos y se terminaban el escritorio, explorer.exe, PipeWire y la propia Zara.
_MIN_PROCESS_NAME = 3
_CRITICAL_PROCESSES = {
    "explorer", "csrss", "winlogon", "lsass", "dwm", "svchost", "services", "smss", "wininit",
    "systemd", "init", "gnome-shell", "plasmashell", "kwin_wayland", "kwin_x11", "xorg", "xwayland",
    "mutter", "gdm", "sddm", "lightdm", "pipewire", "pipewire-pulse", "wireplumber", "pulseaudio",
    "dbus-daemon", "dbus-broker", "xfwm4", "xfce4-session", "sshd", "login",
}


def _process_key(name: str) -> str:
    key = str(name or "").strip().lower()
    return key[:-4] if key.endswith(".exe") else key


def _os_close_app(target: str, value: str) -> Dict[str, Any]:
    tgt = _process_key(target)
    if len(tgt) < _MIN_PROCESS_NAME:
        return {"success": False, "error": "Indique el nombre exacto del proceso o la aplicación a cerrar."}
    if tgt in _CRITICAL_PROCESSES:
        return {"success": False, "blocked": True, "error": f"'{tgt}' es un proceso del sistema: no se cierra."}
    # Nombres en español («calculadora») -> nombres de proceso reales
    if sys.platform == "win32":
        targets = {tgt, _process_key(_WIN_APP_ALIASES.get(tgt, tgt))}
    else:
        targets = {tgt} | {_process_key(c) for c in _LINUX_APP_ALIASES.get(tgt, []) if "." not in c}
    targets = {t for t in targets if len(t) >= _MIN_PROCESS_NAME and t not in _CRITICAL_PROCESSES}

    protect = {os.getpid()}
    try:
        protect |= {p.pid for p in psutil.Process().parents()}  # Zara y quien la lanzó
    except psutil.Error:
        pass

    exact, prefix = [], []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if p.info["pid"] in protect:
                continue
            names = {_process_key(p.info.get("name") or "")}
            # En Linux el nombre se trunca a 15 caracteres (gnome-text-edit): mirar también el ejecutable
            names.add(_process_key(os.path.basename(p.info.get("exe") or "")))
            names.discard("")
            if names & _CRITICAL_PROCESSES:
                continue
            if names & targets:
                exact.append(p)
            elif any(len(t) >= 4 and n.startswith(t) for t in targets for n in names):
                prefix.append(p)  # firefox -> firefox-bin / firefox-esr
        except (psutil.Error, AttributeError, TypeError):
            continue

    closed = []
    for p in exact or prefix:
        try:
            p.terminate()
            closed.append(f"{p.info.get('name')} (PID {p.info['pid']})")
        except psutil.Error:
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
    raw = value if value not in (None, "") else target  # 0 es un volumen válido
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return {"success": False, "error": f"Valor de volumen inválido: '{raw}'"}
    if sys.platform == "win32":
        msg = _set_windows_volume(pct)
        return {"success": not msg.startswith("Error"), "message": msg}
    ok, msg = _set_linux_volume(pct)
    return {"success": ok, "message": msg} if ok else {"success": False, "error": msg}


def _linux_set_mute(muted: bool) -> Dict[str, Any]:
    flag = "1" if muted else "0"
    tool = _first_ok([
        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag],
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag],
        ["amixer", "-q", "sset", "Master", "mute" if muted else "unmute"],
    ])
    if tool:
        return {"success": True, "message": f"Sonido {'silenciado' if muted else 'reactivado'} ({tool})."}
    return {"success": False, "error": "No se pudo cambiar el silencio: instale wireplumber (wpctl), pulseaudio-utils (pactl) o alsa-utils (amixer)."}


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
        return _linux_set_mute(True)


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
        return _linux_set_mute(False)


def _linux_clipboard_tools(paste: bool) -> List[List[str]]:
    """wl-clipboard primero en Wayland; xclip/xsel primero en X11."""
    if paste:
        wayland = [["wl-paste", "--no-newline"]]
        x11 = [["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]]
    else:
        wayland = [["wl-copy"]]
        x11 = [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    return wayland + x11 if _is_wayland() else x11 + wayland


_NO_CLIPBOARD_TOOL = "No hay herramienta de portapapeles: instale wl-clipboard (Wayland) o xclip (X11)."


def _os_read_clipboard(target: str, value: str) -> Dict[str, Any]:
    if sys.platform == "win32":
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_UTF8_PREFIX + "Get-Clipboard"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=15,
        )
        if res.returncode != 0:
            return {"success": False, "error": f"No se pudo leer el portapapeles: {res.stderr.strip()}"}
        return {"success": True, "clipboard": res.stdout.lstrip("\ufeff").strip()}
    found = False
    for cmd in _linux_clipboard_tools(paste=True):
        if not shutil.which(cmd[0]):
            continue
        found = True
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 stdin=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            return {"success": True, "clipboard": res.stdout.strip()}
    if found:
        return {"success": False, "error": "No se pudo leer el portapapeles (vacío o sin acceso a la sesión gráfica)."}
    return {"success": False, "error": _NO_CLIPBOARD_TOOL}


def _os_write_clipboard(target: str, value: str) -> Dict[str, Any]:
    text_to_copy = value or target
    if not text_to_copy:
        return {"success": False, "error": "No hay texto que copiar al portapapeles."}
    if sys.platform == "win32":
        # El texto viaja en una variable de entorno, nunca dentro del script: PowerShell
        # también cierra cadenas con comillas tipográficas (’ ‘ ‚ ‛) y duplicar solo la
        # comilla ASCII permitía inyectar órdenes («hola’; Start-Process calc; ’»).
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard -Value $env:ZARA_TEXT"],
            env={**os.environ, "ZARA_TEXT": text_to_copy},
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=15,
        )
        if res.returncode != 0:
            return {"success": False, "error": f"No se pudo copiar al portapapeles: {res.stderr.strip()}"}
        return {"success": True, "message": "Texto copiado al portapapeles."}
    found = False
    for cmd in _linux_clipboard_tools(paste=False):
        if not shutil.which(cmd[0]):
            continue
        found = True
        try:
            # Sin capturar la salida: wl-copy/xclip se quedan en segundo plano sirviendo
            # el portapapeles y retendrían el pipe.
            res = subprocess.run(cmd, input=text_to_copy, text=True, encoding="utf-8",
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            return {"success": True, "message": f"Texto copiado al portapapeles ({cmd[0]})."}
    if found:
        return {"success": False, "error": "No se pudo copiar al portapapeles (sin acceso a la sesión gráfica)."}
    return {"success": False, "error": _NO_CLIPBOARD_TOOL}


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


def _linux_media(playerctl_cmd: str, x11_key: str, done: str) -> Dict[str, Any]:
    commands = [["playerctl", playerctl_cmd]]
    if not _is_wayland():
        commands.append(["xdotool", "key", x11_key])  # xdotool no llega al compositor Wayland
    tool = _first_ok(commands)
    if tool:
        return {"success": True, "message": f"{done} ({tool})."}
    return {
        "success": False,
        "error": "No se pudo controlar la reproducción: instale playerctl y compruebe que hay un reproductor abierto.",
    }


def _os_media_play_pause(target: str, value: str) -> Dict[str, Any]:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.keybd_event(0xB3, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0xB3, 0, 2, 0)
        return {"success": True, "message": "Comando Play/Pause multimedia enviado."}
    return _linux_media("play-pause", "XF86AudioPlay", "Play/Pause multimedia activado")


def _os_media_next(target: str, value: str) -> Dict[str, Any]:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.keybd_event(0xB0, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0xB0, 0, 2, 0)
        return {"success": True, "message": "Pista siguiente multimedia enviada."}
    return _linux_media("next", "XF86AudioNext", "Pista siguiente enviada")


def _os_media_prev(target: str, value: str) -> Dict[str, Any]:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.keybd_event(0xB1, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0xB1, 0, 2, 0)
        return {"success": True, "message": "Pista anterior multimedia enviada."}
    return _linux_media("previous", "XF86AudioPrev", "Pista anterior enviada")


def _os_show_desktop(target: str, value: str) -> Dict[str, Any]:
    if sys.platform == "win32":
        import ctypes
        # Win+D
        ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x44, 0, 2, 0)
        ctypes.windll.user32.keybd_event(0x5B, 0, 2, 0)
        return {"success": True, "message": "Escritorio mostrado y ventanas minimizadas."}
    if _is_wayland():
        # wmctrl/xdotool solo ven XWayland; KDE expone el atajo por D-Bus.
        kde = ["org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Show Desktop"]
        commands = [["qdbus", *kde], ["qdbus6", *kde]]
    else:
        # EWMH (_NET_SHOWING_DESKTOP) antes que Super+D, que no todos los escritorios asignan
        commands = [["wmctrl", "-k", "on"], ["xdotool", "key", "super+d"]]
    tool = _first_ok(commands)
    if tool:
        return {"success": True, "message": f"Escritorio mostrado ({tool})."}
    if _is_wayland():
        return {"success": False, "error": "En esta sesión Wayland no hay forma de mostrar el escritorio desde fuera; use Super+D."}
    return {"success": False, "error": "No se pudo mostrar el escritorio: instale wmctrl o xdotool."}


def _os_lock_screen(target: str, value: str) -> Dict[str, Any]:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return {"success": True, "message": "Estación de trabajo bloqueada por seguridad."}
    tool = _first_ok([
        ["loginctl", "lock-session"],
        ["xdg-screensaver", "lock"],
        ["xflock4"],
        ["dm-tool", "lock"],
    ])
    if tool:
        return {"success": True, "message": f"Bloqueo de sesión solicitado ({tool})."}
    # Nunca decir «bloqueada» si no lo está: es una afirmación de seguridad.
    return {
        "success": False,
        "error": "No se pudo bloquear la sesión: ni loginctl, ni xdg-screensaver, ni xflock4, ni dm-tool funcionaron. El equipo sigue desbloqueado.",
    }


def _os_notify(target: str, value: str) -> Dict[str, Any]:
    msg_text = value or target or "Notificación táctica de Zara"
    if sys.platform == "win32":
        # El texto va en una variable de entorno (ver _os_write_clipboard): nada del
        # mensaje se interpreta como código PowerShell.
        ps_script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
            "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText01);"
            "$template.GetElementsByTagName('text')[0].AppendChild($template.CreateTextNode($env:ZARA_TEXT)) > $null;"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('ZARA Tactical AI').Show($toast);"
        )
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            env={**os.environ, "ZARA_TEXT": msg_text},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return {"success": True, "message": f"Notificación en pantalla enviada: '{msg_text}'."}
    if _first_ok([["notify-send", "ZARA", msg_text]]):
        return {"success": True, "message": f"Notificación en pantalla enviada: '{msg_text}'."}
    return {"success": False, "error": "No se pudo mostrar la notificación: instale libnotify (notify-send) y un servicio de notificaciones."}


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
    action = _as_text(action).lower().strip()
    handler = _OS_ACTIONS.get(action)
    if handler is None:
        return {"success": False, "error": f"Acción de SO desconocida '{action}'."}
    try:
        return handler(_as_text(target), _as_text(value))
    except Exception as exc:
        log.exception("Error en os_control: %s", exc)
        return {"success": False, "error": str(exc)}



# =============================================================================
# 4. HERRAMIENTA: INSPECCIÓN DE PANTALLA (inspect_screen)
# =============================================================================

_DEFAULT_SCREEN_PROMPT = "Describe qué hay en pantalla y detecta cualquier error visible."


def _screenshot_result(img: Any, prompt: str) -> Dict[str, Any]:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    return {
        "success": True,
        "width": img.width,
        "height": img.height,
        "format": "image/jpeg",
        "base64_data": base64.b64encode(buf.getvalue()).decode("ascii"),
        "prompt": prompt or _DEFAULT_SCREEN_PROMPT,
    }


def _is_black(img: Any) -> bool:
    """Una captura completamente negra es lo que devuelve X11 sobre XWayland: no es éxito."""
    try:
        return img.convert("L").getextrema()[1] == 0
    except Exception:
        return False


def _wayland_screenshot() -> Optional[Any]:
    """Wayland: ImageGrab/mss solo ven XWayland (negro). Se usa la herramienta nativa del
    compositor: grim (wlroots/Sway), spectacle (KDE) o gnome-screenshot. El archivo
    temporal se borra en cuanto se carga en memoria."""
    from PIL import Image

    fd, tmp = tempfile.mkstemp(prefix="zara-captura-", suffix=".png")
    os.close(fd)
    try:
        for cmd in (["grim", tmp], ["spectacle", "-b", "-n", "-f", "-o", tmp], ["gnome-screenshot", "-f", tmp]):
            if not shutil.which(cmd[0]):
                continue
            try:
                res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=15)
            except (OSError, subprocess.SubprocessError):
                continue
            if res.returncode != 0 or os.path.getsize(tmp) == 0:
                continue
            try:
                with Image.open(tmp) as shot:
                    img = shot.copy()
            except Exception:
                continue
            if not _is_black(img):
                return img
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return None


def inspect_screen(prompt: str = "") -> Dict[str, Any]:
    """Captura de pantalla en memoria y codificación base64 (en Wayland, vía archivo temporal)."""
    prompt = _as_text(prompt)
    linux = sys.platform.startswith("linux")
    if linux and _is_wayland():
        try:
            img = _wayland_screenshot()
            if img is not None:
                return _screenshot_result(img, prompt)
        except Exception as exc:
            log.warning("Fallo en captura nativa de Wayland: %s", exc)

    # Estrategia 1: PIL ImageGrab (rápida y limpia en memoria)
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab()
        if linux and _is_black(img):
            raise OSError("la captura salió negra (sesión Wayland sin acceso de captura X11)")
        return _screenshot_result(img, prompt)
    except Exception as exc:
        log.warning("Fallo en captura de pantalla con ImageGrab: %s", exc)

    # Estrategia 2: MSS (mss>=10.2 expone MSS; las 10.0/10.1, la fábrica mss.mss)
    try:
        import mss
        from PIL import Image

        factory = getattr(mss, "MSS", None) or getattr(mss, "mss")
        with factory() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if linux and _is_black(img):
            raise OSError("la captura salió negra (sesión Wayland sin acceso de captura X11)")
        return _screenshot_result(img, prompt)
    except Exception as exc2:
        log.exception("Todas las estrategias de captura de pantalla fallaron: %s", exc2)
        error = f"No se pudo capturar la pantalla: {exc2}"
        if linux and _is_wayland():
            error += ". En Wayland instale grim (Sway/wlroots), spectacle (KDE) o gnome-screenshot."
        return {"success": False, "error": error}


# =============================================================================
# 5. HERRAMIENTA: DELEGAR EN CLAUDE CODE CLI (invoke_claude_code)
# =============================================================================

CLAUDE_CODE_TIMEOUT = 600  # s; al vencer se mata el árbol entero (node de un shim .cmd incluido)


def _claude_code_dir(project_path: Any) -> Tuple[Optional[Path], str]:
    """Valida la carpeta donde Claude Code trabajará con permisos completos.

    Devuelve (carpeta, "") o (None, motivo). Nunca se vuelve al cwd: los lanzadores dejan
    como cwd el repositorio de Zara, y una ruta mal transcrita acababa ejecutando Claude
    Code con --dangerously-skip-permissions sobre el propio asistente.
    """
    raw = _as_text(project_path).strip()
    if not raw:
        return None, "Indica la carpeta del proyecto (project_path)."
    target = resolve_user_path(raw)
    if not target.is_dir():
        return None, f"La carpeta '{target}' no existe. Créala antes con file_manager (mkdir) y vuelve a intentarlo."
    zara_dir = Path(os.path.realpath(BASE_DIR))
    if target == zara_dir or zara_dir in target.parents or target in zara_dir.parents:
        return None, f"Por seguridad, Claude Code no puede trabajar en la carpeta de Zara ni en una que la contenga ('{target}')."
    protected = is_path_protected(target, ancestors=True)
    if protected:
        return None, (
            f"ACCESO DENEGADO POR GUARDARRAÍL: '{target}' es o contiene la ruta protegida '{protected}'. "
            "Usa la carpeta concreta del proyecto (p. ej. Documentos/mi-web)."
        )
    return target, ""


def invoke_claude_code(project_path: str, instruction: str) -> Dict[str, Any]:
    """Delega una tarea de programación en el CLI 'claude' dentro de una carpeta de proyecto
    existente, validada y ajena a Zara (ver _claude_code_dir)."""
    instruction = _as_text(instruction).strip()
    if not instruction:
        return {"success": False, "error": "Indica la instrucción para Claude Code."}
    target_dir, reason = _claude_code_dir(project_path)
    if target_dir is None:
        return {"success": False, "blocked": "GUARDARRAÍL" in reason, "error": reason}

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return {
            "success": False,
            "error": "El binario 'claude' CLI no está instalado en el PATH del sistema.",
        }

    log.info("Invocando Claude Code CLI en '%s' con instrucción: %s", target_dir, instruction[:60])
    try:
        cmd = [
            claude_bin,
            "-p", cli_safe_arg(claude_bin, instruction),
            "--dangerously-skip-permissions",
            "--setting-sources", "project",
        ]
        code, out, err, timed_out = _run_capture(cmd, CLAUDE_CODE_TIMEOUT, cwd=str(target_dir))
        if timed_out:
            return {
                "success": False,
                "stdout": _clip(out.strip(), MAX_TOOL_OUTPUT_CHARS),
                "error": f"Claude Code superó el tiempo límite ({CLAUDE_CODE_TIMEOUT} s) y se ha detenido junto con sus subprocesos.",
                "exit_code": 124,
                "cwd": str(target_dir),
            }
        return {
            "success": code == 0,
            "stdout": _clip(out.strip(), MAX_TOOL_OUTPUT_CHARS),
            "stderr": _clip(err.strip(), MAX_TOOL_OUTPUT_CHARS // 2),
            "exit_code": code,
            "cwd": str(target_dir),
        }
    except Exception as exc:
        return {"success": False, "error": f"Fallo al invocar Claude Code: {exc}"}


# =============================================================================
# 6. HERRAMIENTA: BÚSQUEDA WEB EN TIEMPO REAL (web_search)
# =============================================================================

def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Busca en la web en tiempo real para obtener información actualizada (paquete `ddgs`)."""
    q = _as_text(query).strip()
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
        "description": (
            "Delega una refactorización compleja o tarea de ingeniería en el CLI local de Claude Code, que "
            "trabaja con permisos completos SOLO dentro de project_path. La carpeta debe existir (créala antes "
            "con file_manager mkdir) y ser la del proyecto concreto: no se admite la carpeta de Zara, la carpeta "
            "personal, Documentos entera ni carpetas del sistema. Pide confirmación al Señor VK."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "Carpeta EXISTENTE del proyecto (p. ej. Documentos/mi-web)."},
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


def _normalize_call(name: Any, args: Any) -> Tuple[str, Dict[str, Any]]:
    """Deja la llamada en su forma canónica: nombre limpio, alias resueltos y acción en
    minúsculas sin espacios.

    Se hace ANTES de decidir si hay que pedir confirmación: antes, {"name": "delete"},
    " run_shell" o {"action": "delete "} no coincidían con needs_confirmation, pero luego
    se remapeaban/limpiaban y se ejecutaban (rmtree incluido) sin el «sí» del Señor VK.
    """
    name = _as_text(name).strip()
    # [FIX] Hallazgo de Gemini: si un proveedor nativo (Gemini/Claude) alucina
    # una llamada sin parámetros, `args`/`input` puede llegar como None en vez de {}.
    args = dict(args) if isinstance(args, dict) else {}
    known_names = {t.get("name", "") for t in TOOL_DEFINITIONS}
    lowered = name.lower()
    if lowered in known_names:
        name = lowered
    else:
        alias = _get_action_alias_map().get(lowered)
        if alias:
            real_name, param_name = alias
            args = {param_name: lowered, **args}
            name = real_name
    if isinstance(args.get("action"), str):
        args["action"] = args["action"].strip().lower()
    return name, args


def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Enrutador de ejecución de herramientas."""
    original = name
    name, args = _normalize_call(name, args)
    if str(original or "").strip() != name:
        log.info("Herramienta '%s' normalizada/remapeada a %s(%s).", original, name, args.get("action", ""))

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

    # Ninguna excepción por argumentos raros del LLM debe tumbar el turno entero.
    try:
        return _dispatch(name, args)
    except Exception as exc:
        log.exception("Error ejecutando %s con %s: %s", name, args, exc)
        return {"success": False, "error": f"Argumentos inválidos para {name}: {exc}"}


def _dispatch(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "run_shell":
        return run_shell(
            command=_as_text(args.get("command")),
            timeout=args.get("timeout", SHELL_TIMEOUT),
            background=args.get("background", False),
            cwd=_as_text(args.get("cwd")),
        )
    elif name == "file_manager":
        return file_manager(
            action=_as_text(args.get("action"), "read"),
            path=_as_text(args.get("path")),
            content=_as_text(args.get("content")),
            old_str=_as_text(args.get("old_str")),
            new_str=_as_text(args.get("new_str")),
            replace_all=args.get("replace_all", False),
            start_line=args.get("start_line", 1),
            max_lines=args.get("max_lines", MAX_READ_LINES),
            pattern=_as_text(args.get("pattern")),
        )
    elif name == "os_control":
        return os_control(
            action=_as_text(args.get("action")),
            target=_as_text(args.get("target")),
            value=_as_text(args.get("value")),
        )
    elif name == "web_search":
        return web_search(
            query=_as_text(args.get("query")),
            max_results=args.get("max_results", 5),
        )
    elif name == "inspect_screen":
        return inspect_screen(prompt=_as_text(args.get("prompt")))
    elif name == "invoke_claude_code":
        return invoke_claude_code(
            project_path=_as_text(args.get("project_path")),
            instruction=_as_text(args.get("instruction")),
        )
    else:
        return {"success": False, "error": f"Herramienta desconocida '{name}'."}
