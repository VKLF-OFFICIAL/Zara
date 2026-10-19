#!/usr/bin/env python3
"""Script de Inicialización, Diagnóstico y Despliegue de ZARA.

Automatiza la comprobación de entorno, dependencias de audio/IA, configuración
de plataforma (Windows 10/11 y Linux Kali/Fedora) y el lanzamiento del asistente.
"""

from __future__ import annotations
import argparse
import hashlib
import re
import importlib
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import urllib.request


PROJECT_DIR = Path(__file__).resolve().parent
ENV_EXAMPLE = PROJECT_DIR / ".env.example"
ENV_FILE = PROJECT_DIR / ".env"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"

# [FIX MUL-9] En Linux, el torch de PyPI declara como dependencias los paquetes nvidia-*
# de CUDA (varios GB) aunque no haya GPU. Sin GPU NVIDIA se instala antes el de solo CPU
# desde este índice; pip ya no vuelve a bajar el de CUDA porque el requisito queda
# satisfecho. Whisper usa ctranslate2, no torch, así que el de CUDA no aporta nada.
# ZARA_TORCH=cuda fuerza el de PyPI y ZARA_TORCH=cpu fuerza el de CPU.
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# [FIX MUL-8] Marcador que se escribe en el venv solo cuando `pip install -r requirements.txt`
# termina bien. Guarda la huella de requirements.txt: si una instalación se cortó (red,
# disco lleno) o el archivo cambió tras un git pull, los lanzadores vuelven a instalar.
DEPS_MARKER_NAME = ".zara-deps-ok"

# (módulo que se importa, paquete de pip que lo instala)
CRITICAL_MODULES: List[Tuple[str, str]] = [
    ("sounddevice", "sounddevice"),
    ("numpy", "numpy"),
    ("torch", "torch"),
    ("faster_whisper", "faster-whisper"),
    ("edge_tts", "edge-tts"),
    ("miniaudio", "miniaudio"),
    ("PySide6", "pyside6"),
    ("psutil", "psutil"),
    ("mss", "mss"),
    ("PIL", "pillow"),
]

# [FIX V1_-3] Paquetes del sistema que ZARA usa en Linux y que pip no instala.
# clave -> (para qué sirve, paquete apt (Debian/Ubuntu/Kali), dnf (Fedora), pacman (Arch))
LINUX_SYSTEM_PACKAGES: Dict[str, Tuple[str, str, str, str]] = {
    "portaudio": ("micrófono y altavoces (sounddevice)", "libportaudio2", "portaudio", "portaudio"),
    "xcb-cursor": ("ventanas de Qt en X11", "libxcb-cursor0", "xcb-util-cursor", "xcb-util-cursor"),
    "wl-clipboard": ("portapapeles en Wayland", "wl-clipboard", "wl-clipboard", "wl-clipboard"),
    "xclip": ("portapapeles en X11", "xclip", "xclip", "xclip"),
    "playerctl": ("control de música y vídeo", "playerctl", "playerctl", "playerctl"),
}
_PKG_MANAGERS = {
    "apt": (1, "sudo apt install -y"),
    "dnf": (2, "sudo dnf install -y"),
    "pacman": (3, "sudo pacman -S --needed"),
}


if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def read_version() -> str:
    """Lee VERSION/RELEASE_STAGE de config.py sin importarlo (puede correr antes de instalar deps)."""
    try:
        text = (PROJECT_DIR / "config.py").read_text(encoding="utf-8")
        version = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M).group(1)
        stage = re.search(r'^RELEASE_STAGE\s*=\s*"([^"]+)"', text, re.M).group(1)
        return f"{version}-{stage}"
    except Exception:
        return "desconocida"


def print_banner() -> None:
    banner = f"""
 ============================================================
    Z A R A  --  T A C T I C A L   A I   C O R E
    Versión {read_version()} (pre-lanzamiento)
    Protocol J.A.R.V.I.S. | Autonomous Assistant for Señor VK
 ============================================================
"""
    print(banner)



def check_python_version() -> bool:
    print(f"[*] Comprobando versión de Python: {sys.version.split()[0]}... ", end="")
    if sys.version_info < (3, 10):
        print("ERROR: ZARA requiere Python 3.10 o superior.")
        return False
    print("OK")
    if sys.version_info >= (3, 14):
        # [FIX V1_-1] requirements.txt omite kokoro-onnx en 3.14+ (no publica versiones).
        print("    -> Aviso: Kokoro (voz local opcional) solo existe hasta Python 3.13; ZARA usará edge-tts.")
    return True


def check_os_environment() -> None:
    system_name = platform.system()
    release = platform.release()
    print(f"[*] Sistema operativo detectado: {system_name} ({release})")
    if system_name == "Windows":
        print("    -> Configurando subsistema de audio WASAPI y PowerShell 7/5.1.")
    elif system_name == "Linux":
        # Comprobar servidor de sonido en Linux
        pw_check = shutil.which("pipewire") or shutil.which("pw-cli")
        pa_check = shutil.which("pulseaudio") or shutil.which("pactl")
        sound_backend = "PipeWire" if pw_check else ("PulseAudio" if pa_check else "ALSA/Generic")
        print(f"    -> Servidor de audio en Linux: {sound_backend}")
    else:
        print(f"    -> Advertencia: Plataforma no probada formalmente: {system_name}")


def _bundled_xcb_cursor() -> bool:
    """Algunas ruedas de PySide6 traen libxcb-cursor en PySide6/Qt/lib (main.py mira lo mismo)."""
    try:
        spec = importlib.util.find_spec("PySide6")
    except (ImportError, ValueError):
        return False
    if not spec or not spec.origin:
        return False
    return any((Path(spec.origin).parent / "Qt" / "lib").glob("libxcb-cursor.so*"))


def find_missing_linux_packages(
    env: Optional[Mapping[str, str]] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    find_library: Optional[Callable[[str], Optional[str]]] = None,
) -> List[str]:
    """[FIX V1_-3] Claves de LINUX_SYSTEM_PACKAGES que faltan en esta sesión.

    PortAudio siempre (sin él no hay micrófono ni voz). El resto según la sesión gráfica:
    libxcb-cursor solo en X11 puro (en Wayland main.py cae a Wayland nativo), wl-clipboard
    en Wayland, xclip/xsel en X11 y playerctl en cualquier escritorio.
    """
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    if find_library is None:
        import ctypes.util
        find_library = ctypes.util.find_library
    missing: List[str] = []
    if not find_library("portaudio"):
        missing.append("portaudio")
    wayland = bool(env.get("WAYLAND_DISPLAY")) or env.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    x11 = bool(env.get("DISPLAY")) and not wayland
    if x11 and not find_library("xcb-cursor") and not _bundled_xcb_cursor():
        missing.append("xcb-cursor")
    if wayland and not (which("wl-copy") and which("wl-paste")):
        missing.append("wl-clipboard")
    if x11 and not (which("xclip") or which("xsel")):
        missing.append("xclip")
    if (wayland or x11) and not which("playerctl"):
        missing.append("playerctl")
    return missing


def linux_package_manager(os_release: Optional[str] = None) -> str:
    """'apt', 'dnf', 'pacman' o '' según ID e ID_LIKE de /etc/os-release."""
    if os_release is None:
        try:
            os_release = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
        except OSError:
            os_release = ""
    ids = set()
    for line in os_release.splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("ID", "ID_LIKE"):
            ids.update(value.strip().strip("\"'").lower().split())
    if ids & {"debian", "ubuntu", "kali", "linuxmint", "pop", "raspbian"}:
        return "apt"
    if ids & {"fedora", "rhel", "centos", "rocky", "almalinux"}:
        return "dnf"
    if ids & {"arch", "manjaro", "endeavouros"}:
        return "pacman"
    return ""


def linux_install_commands(missing: Sequence[str], manager: str = "") -> List[str]:
    """Órdenes para instalar los paquetes que faltan (apt y dnf si no se reconoce la distro)."""
    managers = [manager] if manager in _PKG_MANAGERS else ["apt", "dnf"]
    commands = []
    for name in managers:
        column, prefix = _PKG_MANAGERS[name]
        packages = " ".join(LINUX_SYSTEM_PACKAGES[key][column] for key in missing)
        commands.append(f"{prefix} {packages}")
    return commands


def check_linux_system_packages(
    platform_name: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    find_library: Optional[Callable[[str], Optional[str]]] = None,
    os_release: Optional[str] = None,
) -> List[str]:
    """[FIX V1_-3] Avisa (sin detener el arranque) de los paquetes del sistema que faltan en Linux."""
    platform_name = sys.platform if platform_name is None else platform_name
    if not platform_name.startswith("linux"):
        return []
    missing = find_missing_linux_packages(env=env, which=which, find_library=find_library)
    if not missing:
        print("[*] Paquetes del sistema (audio, ventanas, portapapeles, multimedia): OK")
        return []
    print("[!] Faltan paquetes del sistema que ZARA usa en Linux (pip no los instala):")
    for key in missing:
        purpose, apt_pkg, dnf_pkg, _ = LINUX_SYSTEM_PACKAGES[key]
        name = apt_pkg if apt_pkg == dnf_pkg else f"{apt_pkg} / {dnf_pkg}"
        print(f"    - {name}: {purpose}")
    print("    Instálelos con:")
    for command in linux_install_commands(missing, linux_package_manager(os_release)):
        print(f"      {command}")
    return missing


def ensure_env_file() -> None:
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
            print(f"[+] Archivo de configuración creado: {ENV_FILE.name} (copiado desde {ENV_EXAMPLE.name})")
            print("    -> Puede editar .env para añadir claves de Gemini o Anthropic si lo desea.")
        else:
            print("[-] .env.example no encontrado; omitiendo copia de entorno.")
    else:
        print(f"[*] Archivo de configuración {ENV_FILE.name} presente.")


def _in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def deps_marker_path() -> Optional[Path]:
    """Marcador de instalación completa dentro del venv activo (None fuera de un venv)."""
    return Path(sys.prefix) / DEPS_MARKER_NAME if _in_virtualenv() else None


def requirements_fingerprint() -> str:
    try:
        return hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()
    except OSError:
        return ""


def install_pending() -> bool:
    """True si este venv no completó `pip install -r requirements.txt` con el archivo actual."""
    marker = deps_marker_path()
    if marker is None:
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() != requirements_fingerprint()
    except OSError:
        return True


def _write_deps_marker() -> None:
    marker = deps_marker_path()
    if marker is not None:
        try:
            marker.write_text(requirements_fingerprint(), encoding="utf-8")
        except OSError:
            pass


def _clear_deps_marker() -> None:
    marker = deps_marker_path()
    if marker is not None:
        try:
            marker.unlink()
        except OSError:
            pass


def has_nvidia_gpu() -> bool:
    return bool(shutil.which("nvidia-smi")) or Path("/proc/driver/nvidia/version").exists()


def wants_cpu_torch(
    platform_name: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    gpu: Optional[bool] = None,
) -> bool:
    """[FIX MUL-9] True si conviene instalar torch desde el índice de solo CPU de PyTorch.

    Solo en Linux sin GPU NVIDIA: en Windows (y macOS) el torch de PyPI ya es el de CPU, y
    con GPU se respeta el de PyPI (CUDA) para no quitarle la aceleración a nadie.
    """
    platform_name = sys.platform if platform_name is None else platform_name
    env = os.environ if env is None else env
    choice = env.get("ZARA_TORCH", "").strip().lower()
    if choice == "cpu":
        return True
    if choice in ("cuda", "gpu", "pypi"):
        return False
    if not platform_name.startswith("linux"):
        return False
    return not (has_nvidia_gpu() if gpu is None else gpu)


def requirement_spec(name: str, path: Optional[Path] = None) -> str:
    """Requisito de `name` en requirements.txt sin comentario ni marcador (p. ej. 'torch>=2.2.0,<3')."""
    try:
        lines = (path or REQUIREMENTS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return name
    for line in lines:
        req = line.split("#", 1)[0].split(";", 1)[0].strip()
        if re.match(rf"{re.escape(name)}\s*(?:[<>=!~]|$)", req, re.IGNORECASE):
            return req.replace(" ", "")
    return name


def build_install_commands(python: str, cpu_torch: bool) -> List[List[str]]:
    """Órdenes de pip para instalar requirements.txt (con torch de CPU antes si procede)."""
    commands = []
    if cpu_torch:
        commands.append([python, "-m", "pip", "install", requirement_spec("torch"), "--index-url", TORCH_CPU_INDEX])
    commands.append([python, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)])
    return commands


def _torch_installed() -> bool:
    try:
        return importlib.util.find_spec("torch") is not None
    except (ImportError, ValueError):
        return False


def install_requirements(run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run) -> bool:
    """Instala requirements.txt en el Python actual y marca el venv si todo fue bien."""
    cpu_torch = wants_cpu_torch() and not _torch_installed()
    for index, cmd in enumerate(build_install_commands(sys.executable, cpu_torch)):
        torch_step = cpu_torch and index == 0
        if torch_step:
            print("[*] Linux sin GPU NVIDIA: instalando torch solo para CPU (evita varios GB de CUDA; "
                  "ZARA_TORCH=cuda lo desactiva)...")
        try:
            ok = run(cmd).returncode == 0
        except Exception as exc:
            print(f"ERROR ejecutando pip: {exc}")
            ok = False
        if ok:
            continue
        if torch_step:
            # Sin acceso a download.pytorch.org (proxy, red corporativa): se sigue con PyPI.
            print("    [!] No se pudo usar el índice de CPU de PyTorch; torch se instalará desde PyPI.")
            continue
        print("ERROR instalando dependencias (revise los mensajes de pip de arriba).")
        if not _in_virtualenv():
            print("    -> Use un entorno virtual: los lanzadores setup_and_run.sh / zara.cmd crean .venv solos.")
        return False
    _write_deps_marker()
    return True


def probe_dependencies(
    import_module: Callable[[str], object] = importlib.import_module,
) -> Tuple[List[str], Dict[str, str]]:
    """Devuelve (paquetes de pip que faltan, {módulo: error} de los instalados que no cargan)."""
    missing: List[str] = []
    broken: Dict[str, str] = {}
    for mod_name, pkg_name in CRITICAL_MODULES:
        try:
            import_module(mod_name)
        except ImportError:
            missing.append(pkg_name)
        except Exception as exc:
            # [FIX V1_-3] Instalado pero sin su librería del sistema: sounddevice lanza OSError
            # sin PortAudio en Linux, y torch en Windows sin el runtime de Visual C++. pip no
            # lo arregla: antes salía un traceback sin decir qué instalar.
            broken[mod_name] = f"{type(exc).__name__}: {exc}"
    return missing, broken


def _broken_hint(mod_name: str, platform_name: str) -> str:
    if mod_name == "sounddevice" and platform_name.startswith("linux"):
        return ("instale PortAudio: sudo apt install -y libportaudio2 (Debian/Kali/Ubuntu) "
                "o sudo dnf install -y portaudio (Fedora)")
    if platform_name == "win32":
        return ("instale Microsoft Visual C++ Redistributable x64 "
                "(https://aka.ms/vs/17/release/vc_redist.x64.exe) y vuelva a abrir ZARA")
    pkg_name = dict(CRITICAL_MODULES).get(mod_name, mod_name)
    return f"reinstálelo con: {sys.executable} -m pip install --force-reinstall {pkg_name}"


def report_broken(broken: Mapping[str, str], platform_name: Optional[str] = None) -> bool:
    """Explica qué hacer con los módulos que no cargan. True si alguno impide arrancar.

    Sin sounddevice ZARA arranca igualmente (chat escrito y panel, sin voz), así que no bloquea.
    """
    platform_name = sys.platform if platform_name is None else platform_name
    blocking = False
    for mod_name, error in broken.items():
        print(f"[!] {mod_name} está instalado pero no carga ({error}).")
        print(f"    -> {_broken_hint(mod_name, platform_name)}")
        if mod_name == "sounddevice":
            print("    -> ZARA arrancará sin micrófono ni voz hasta que lo resuelva.")
        else:
            blocking = True
    return blocking


def check_dependencies(auto_install: bool = False) -> bool:
    """True si están los paquetes críticos y cargan; con auto_install instala lo que falte."""
    print("[*] Verificando módulos y paquetes críticos...")
    missing, broken = probe_dependencies()

    if missing:
        print(f"[-] Faltan los siguientes paquetes requeridos: {', '.join(missing)}")
        _clear_deps_marker()  # el próximo arranque desde los lanzadores volverá a instalar
        if not auto_install:
            print("    -> Ejecute con --install (o: python -m pip install -r requirements.txt)")
            return False
        print("[*] Procediendo a instalar dependencias automáticamente...")
        if not install_requirements():
            return False
        importlib.invalidate_caches()
        missing, broken = probe_dependencies()
        if missing:
            print(f"[-] Siguen faltando tras la instalación: {', '.join(missing)}")
            return False
    elif auto_install and install_pending():
        # [FIX MUL-8] Los módulos críticos están, pero la última instalación no terminó (o
        # requirements.txt cambió): se completa. Sin red no se bloquea el arranque.
        print("[*] Completando la instalación de dependencias del entorno virtual...")
        if not install_requirements():
            print("    [!] No se pudo completar; ZARA arrancará con lo que ya está instalado.")

    if not broken:
        print("    -> Todas las dependencias críticas de IA y Audio están instaladas.")
    return not report_broken(broken)


def check_audio_devices() -> None:
    print("[*] Comprobando dispositivos de entrada y salida de audio...")
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_in = sd.default.device[0]
        default_out = sd.default.device[1]

        if default_in is None or default_in < 0:
            print("    [!] ADVERTENCIA: No se detectó micrófono por defecto.")
        else:
            dev_name = devices[default_in]["name"] if default_in < len(devices) else "Desconocido"
            print(f"    -> Micrófono (Entrada) predeterminado: [{default_in}] {dev_name}")

        if default_out is None or default_out < 0:
            print("    [!] ADVERTENCIA: No se detectó altavoz por defecto.")
        else:
            dev_name = devices[default_out]["name"] if default_out < len(devices) else "Desconocido"
            print(f"    -> Altavoces (Salida) predeterminados: [{default_out}] {dev_name}")
    except Exception as exc:
        print(f"    [!] Error al consultar dispositivos de audio: {exc}")


def check_ollama_service() -> None:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    url = f"{host.rstrip('/')}/api/tags"
    print(f"[*] Comprobando disponibilidad del servicio local Ollama ({host})... ", end="")
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                print("EN LÍNEA")
                return
    except Exception:
        pass
    print("NO DETECTADO (Offline o puerto cerrado)")
    print("    -> Si usará Ollama para comandos sin internet, ejecute 'ollama serve' en otra terminal.")


def run_diagnostics() -> bool:
    """Ejecuta una batería rápida de pruebas sobre los subsistemas."""
    print("\n" + "=" * 60)
    print("EJECUTANDO DIAGNÓSTICO INTEGRAL DE SUBSISTEMAS ZARA")
    print("=" * 60)

    # 1. Enrutador Cerebral
    print("[1/6] Probando Enrutador Cerebral (ZaraRouter)... ", end="")
    try:
        from core.brain import ZaraRouter
        provider = ZaraRouter.classify("Revisa el código y lanza escaneo nmap")
        print(f"OK -> Enrutado a: {provider.upper()}")
    except Exception as exc:
        print(f"FALLO: {exc}")
        return False

    # 2. Guardarraíles y Herramientas
    print("[2/6] Probando Guardarraíles de Seguridad y Herramientas... ", end="")
    try:
        from core.tools import execute_tool
        block_test = execute_tool("run_shell", {"command": "rm -rf /"})
        exec_test = execute_tool("run_shell", {"command": "echo ZARA_OPERATIVA"})
        if block_test["blocked"] and "ZARA_OPERATIVA" in exec_test.get("stdout", ""):
            print("OK (Guardarraíl bloqueó rm -rf / y ejecutó shell correctamente)")
        else:
            print(f"ANOMALÍA en resultados de prueba: {block_test}")
    except Exception as exc:
        print(f"FALLO: {exc}")
        return False

    # 3. VAD Neuronal Silero
    print("[3/6] Probando Carga de Silero VAD v5... ", end="")
    try:
        from silero_vad import load_silero_vad
        model = load_silero_vad()
        print(f"OK ({type(model).__name__} cargado)")
    except Exception as exc:
        print(f"FALLO: {exc}")
        return False

    # 4. Motor de Audio / Miniaudio
    print("[4/6] Probando Decodificación en Memoria (miniaudio)... ", end="")
    try:
        import miniaudio  # noqa: F401  (el import ES la prueba)
        print("OK (miniaudio listo para streaming en RAM sin disco)")
    except Exception as exc:
        print(f"FALLO: {exc}")
        return False

    # 5. Interfaz HUD (PySide6)
    print("[5/6] Probando Componentes Gráficos (PySide6)... ", end="")
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        from ui.hud import HUDState, ZaraHUD
        hud = ZaraHUD()
        hud.set_hud_state(HUDState.IDLE)
        print("OK (HUD Holográfico inicializado)")
    except Exception as exc:
        print(f"FALLO: {exc}")
        return False

    # 6. Cerebro Gemini con la suscripción (Antigravity CLI). No es obligatorio: solo informa.
    print("[6/6] Buscando Antigravity CLI (Gemini con suscripción AI Pro)... ", end="")
    try:
        from config import DEFAULT_BRAIN
        from core.brain import find_agy
        agy = find_agy()
        if agy:
            print(f"OK -> {agy}")
        elif DEFAULT_BRAIN == "antigravity":
            print("NO ENCONTRADO: es el cerebro por defecto; instale Antigravity CLI y ejecute 'agy' para iniciar sesión.")
        else:
            print("no instalado (opcional)")
    except Exception as exc:
        print(f"FALLO: {exc}")

    print("=" * 60)
    print("TODOS LOS SUBSISTEMAS ESTÁN OPERATIVOS Y CALIBRADOS")
    print("=" * 60 + "\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="ZARA Setup and Runner")
    parser.add_argument("--check", action="store_true", help="Ejecutar diagnóstico completo sin iniciar el bucle continuo.")
    parser.add_argument("--install", action="store_true", help="Instalar dependencias faltantes automáticamente vía pip.")
    parser.add_argument("--cli", action="store_true", help="Iniciar ZARA en modo terminal (sin HUD de PySide6).")
    args, unknown_args = parser.parse_known_args()

    print_banner()

    if not check_python_version():
        sys.exit(1)

    check_os_environment()
    check_linux_system_packages()
    ensure_env_file()

    deps_ok = check_dependencies(auto_install=args.install)
    if not deps_ok:
        # [FIX MUL-4/V1_-2] Antes se avisaba y se lanzaba main.py igualmente, que moría con
        # ModuleNotFoundError y la consola de zara.cmd se cerraba sin dejar ver nada.
        print("\n[!] ZARA no puede arrancar: faltan dependencias o no cargan (vea los mensajes anteriores).")
        if not args.install:
            print("    Ejecute con '--install' para instalarlas automáticamente.")
        sys.exit(1)

    check_audio_devices()
    check_ollama_service()

    if args.check:
        success = run_diagnostics()
        sys.exit(0 if success else 1)

    # Iniciar ZARA
    print("\n[*] Iniciando ZARA...")
    main_py = PROJECT_DIR / "main.py"
    cmd = [sys.executable, str(main_py)]
    if args.cli:
        cmd.append("--cli")
    cmd.extend(unknown_args)

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[*] ZARA detenida por el usuario. Buen día, Señor VK.")


if __name__ == "__main__":
    main()
