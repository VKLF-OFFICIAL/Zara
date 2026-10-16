#!/usr/bin/env python3
"""Inicialización, diagnóstico y lanzamiento de ZARA.

Fuente de verdad ÚNICA: el paquete `app/zara`. Este script comprueba el entorno
(audio, IA, plataforma), prepara `.env` y arranca el asistente:

    python setup_and_run.py            # HUD holográfico (PySide6)
    python setup_and_run.py --cli      # modo terminal, sin HUD
    python setup_and_run.py --enroll   # inscripción de la huella de voz
    python setup_and_run.py --check    # diagnóstico de subsistemas
    python setup_and_run.py --install  # instala dependencias (pip -r requirements.txt)
"""

from __future__ import annotations
import argparse
import importlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request

PROJECT_DIR = Path(__file__).resolve().parent
APP_DIR = PROJECT_DIR / "app"
ENV_EXAMPLE = PROJECT_DIR / ".env.example"
ENV_FILE = PROJECT_DIR / ".env"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"

# El paquete vive en app/; lo ponemos en el path para importarlo y lanzarlo.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def print_banner() -> None:
    print("""
 ============================================================
    Z A R A  --  T A C T I C A L   A I   C O R E
    Protocolo J.A.R.V.I.S. | Asistente del Señor VK
 ============================================================
""")


def check_python_version() -> bool:
    print(f"[*] Python {sys.version.split()[0]}... ", end="")
    if sys.version_info < (3, 10):
        print("ERROR: se requiere Python 3.10 o superior.")
        return False
    print("OK")
    return True


def check_os_environment() -> None:
    print(f"[*] Sistema: {platform.system()} ({platform.release()})")
    if platform.system() == "Linux":
        backend = ("PipeWire" if shutil.which("pw-cli") or shutil.which("pipewire")
                   else "PulseAudio" if shutil.which("pactl") else "ALSA")
        print(f"    -> Servidor de audio: {backend}")


def ensure_env_file() -> None:
    if not ENV_FILE.exists() and ENV_EXAMPLE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        print(f"[+] Creado {ENV_FILE.name} desde {ENV_EXAMPLE.name}. "
              "Añada ahí GEMINI_API_KEY / ANTHROPIC_API_KEY si lo desea.")
    else:
        print(f"[*] {ENV_FILE.name} {'presente' if ENV_FILE.exists() else 'no creado'}.")


def check_dependencies(auto_install: bool = False) -> bool:
    print("[*] Verificando dependencias críticas...")
    modules = [
        ("sounddevice", "sounddevice"), ("numpy", "numpy"),
        ("faster_whisper", "faster-whisper"), ("edge_tts", "edge-tts"),
        ("miniaudio", "miniaudio"), ("silero_vad", "silero-vad"),
        ("torch", "torch"), ("PySide6", "pyside6"), ("psutil", "psutil"),
    ]
    missing = [pkg for mod, pkg in modules if not _has(mod)]
    if not missing:
        print("    -> Todas las dependencias críticas instaladas.")
        return True
    print(f"[-] Faltan: {', '.join(missing)}")
    if auto_install:
        print("[*] Instalando con pip...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                            str(REQUIREMENTS_FILE)], check=True)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR instalando: {exc}")
            return False
    print("    -> Ejecute: pip install -r requirements.txt (o use --install)")
    return False


def _has(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def check_ollama_service() -> None:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    print(f"[*] Ollama ({host})... ", end="")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=1.5) as r:
            if r.status == 200:
                print("EN LÍNEA")
                return
    except Exception:
        pass
    print("no detectado (offline u otro puerto)")


def run_diagnostics() -> bool:
    print("\n" + "=" * 60)
    print("DIAGNÓSTICO DE SUBSISTEMAS ZARA")
    print("=" * 60)
    ok = True

    print("[1/5] Guardián y herramientas... ", end="")
    try:
        from zara.brain.tools import is_command_forbidden, file_manager  # noqa: F401
        assert is_command_forbidden("rm -rf /") and not is_command_forbidden("ls")
        print("OK")
    except Exception as exc:  # noqa: BLE001
        print(f"FALLO: {exc}"); ok = False

    print("[2/5] Cerebros nativos (SDK)... ", end="")
    try:
        from zara.brain.native import NativeGemini, NativeAnthropic, NativeOllama  # noqa: F401
        print("OK")
    except Exception as exc:  # noqa: BLE001
        print(f"FALLO: {exc}"); ok = False

    print("[3/5] Silero VAD v5... ", end="")
    try:
        from zara.audio.vad import SileroVad
        print("OK" if SileroVad().available() else "no instalado (usará VAD por energía)")
    except Exception as exc:  # noqa: BLE001
        print(f"FALLO: {exc}"); ok = False

    print("[4/5] Voz en streaming (Edge+miniaudio)... ", end="")
    try:
        from zara.voice.stream import EdgeMemorySynth
        print("OK" if EdgeMemorySynth().available() else "parcial (faltan edge-tts/miniaudio)")
    except Exception as exc:  # noqa: BLE001
        print(f"FALLO: {exc}"); ok = False

    print("[5/5] HUD (PySide6)... ", end="")
    try:
        import PySide6  # noqa: F401
        print("OK")
    except Exception as exc:  # noqa: BLE001
        print(f"no disponible: {exc}")

    print("=" * 60)
    print("DIAGNÓSTICO COMPLETADO" + ("" if ok else " (con incidencias)"))
    print("=" * 60 + "\n")
    return ok


def launch(mode: str, extra: list[str]) -> int:
    """Arranca el paquete app/zara en el modo pedido (misma fuente de verdad)."""
    module = {"gui": "zara", "cli": "zara.chat", "enroll": "zara.enroll"}[mode]
    cmd = [sys.executable, "-m", module, *extra]
    try:
        return subprocess.run(cmd, cwd=str(APP_DIR)).returncode
    except KeyboardInterrupt:
        print("\n[*] ZARA detenida. Buen día, Señor VK.")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="ZARA — setup y lanzador")
    parser.add_argument("--check", action="store_true", help="Diagnóstico de subsistemas.")
    parser.add_argument("--install", action="store_true", help="Instalar dependencias (pip).")
    parser.add_argument("--cli", action="store_true", help="Modo terminal (sin HUD).")
    parser.add_argument("--enroll", action="store_true", help="Inscribir la huella de voz.")
    args, extra = parser.parse_known_args()

    print_banner()
    if not check_python_version():
        sys.exit(1)
    check_os_environment()
    ensure_env_file()
    deps_ok = check_dependencies(auto_install=args.install)
    check_ollama_service()

    if args.check:
        sys.exit(0 if run_diagnostics() else 1)
    if not deps_ok and not args.install:
        print("\n[!] Faltan librerías; puede continuar o ejecutar con --install.")

    mode = "enroll" if args.enroll else "cli" if args.cli else "gui"
    print(f"\n[*] Iniciando ZARA ({mode})...")
    sys.exit(launch(mode, extra))


if __name__ == "__main__":
    main()
