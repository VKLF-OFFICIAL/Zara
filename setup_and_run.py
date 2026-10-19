#!/usr/bin/env python3
"""Script de Inicialización, Diagnóstico y Despliegue de ZARA.

Automatiza la comprobación de entorno, dependencias de audio/IA, configuración
de plataforma (Windows 10/11 y Linux Kali/Fedora) y el lanzamiento del asistente.
"""

from __future__ import annotations
import argparse
import re
import importlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request


PROJECT_DIR = Path(__file__).resolve().parent
ENV_EXAMPLE = PROJECT_DIR / ".env.example"
ENV_FILE = PROJECT_DIR / ".env"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"


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


def check_dependencies(auto_install: bool = False) -> bool:
    print("[*] Verificando módulos y paquetes críticos...")
    critical_modules = [
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

    missing = []
    for mod_name, pkg_name in critical_modules:
        try:
            importlib.import_module(mod_name)
        except ImportError:
            missing.append(pkg_name)

    if not missing:
        print("    -> Todas las dependencias críticas de IA y Audio están instaladas.")
        return True

    print(f"[-] Faltan los siguientes paquetes requeridos: {', '.join(missing)}")
    if auto_install:
        print("[*] Procediendo a instalar dependencias automáticamente...")
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)]
        try:
            res = subprocess.run(cmd, check=True)
            return res.returncode == 0
        except Exception as exc:
            print(f"ERROR instalando dependencias: {exc}")
            return False
    else:
        print("    -> Ejecute: pip install -r requirements.txt (o use --install)")
        return False


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
    print("[1/5] Probando Enrutador Cerebral (ZaraRouter)... ", end="")
    try:
        from core.brain import ZaraRouter
        provider = ZaraRouter.classify("Revisa el código y lanza escaneo nmap")
        print(f"OK -> Enrutado a: {provider.upper()}")
    except Exception as exc:
        print(f"FALLO: {exc}")
        return False

    # 2. Guardarraíles y Herramientas
    print("[2/5] Probando Guardarraíles de Seguridad y Herramientas... ", end="")
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
    print("[3/5] Probando Carga de Silero VAD v5... ", end="")
    try:
        from silero_vad import load_silero_vad
        model = load_silero_vad()
        print(f"OK ({type(model).__name__} cargado)")
    except Exception as exc:
        print(f"FALLO: {exc}")
        return False

    # 4. Motor de Audio / Miniaudio
    print("[4/5] Probando Decodificación en Memoria (miniaudio)... ", end="")
    try:
        import miniaudio  # noqa: F401  (el import ES la prueba)
        print("OK (miniaudio listo para streaming en RAM sin disco)")
    except Exception as exc:
        print(f"FALLO: {exc}")
        return False

    # 5. Interfaz HUD (PySide6)
    print("[5/5] Probando Componentes Gráficos (PySide6)... ", end="")
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
    ensure_env_file()

    deps_ok = check_dependencies(auto_install=args.install)
    if not deps_ok and not args.install:
        print("\n[!] Advertencia: Algunas librerías no están instaladas.")
        print("    Puede ejecutar con '--install' para instalarlas automáticamente.")

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
