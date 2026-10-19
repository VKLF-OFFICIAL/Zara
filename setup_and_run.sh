#!/usr/bin/env bash
# =====================================================================
# ZARA - Lanzador Táctico para Linux (Kali Linux, Fedora, Debian)
# Protocol J.A.R.V.I.S. | Asistente para el Señor VK
# =====================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo "    Z A R A   v 2 . 0  --  L I N U X   L A U N C H E R     "
echo "    Protocol J.A.R.V.I.S. | Asistente para el Señor VK     "
echo "============================================================"

# Detección de distribución
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO_NAME=$NAME
    echo "[*] Distribución detectada: $DISTRO_NAME"
fi

# Detección de servidor de audio
if command -v wpctl &> /dev/null || pgrep -x "pipewire" &> /dev/null; then
    echo "[*] Servidor de audio: PipeWire (Control activo por wpctl)"
elif command -v pactl &> /dev/null || pgrep -x "pulseaudio" &> /dev/null; then
    echo "[*] Servidor de audio: PulseAudio (Control activo por pactl)"
else
    echo "[!] Advertencia: Ni PipeWire ni PulseAudio detectados activamente. Usando ALSA por defecto."
fi

# True si el intérprete arranca y es Python >= 3.10
python_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' > /dev/null 2>&1
}

# True si el venv tiene un intérprete que arranca y con pip
venv_ok() {
    [ -x "$1/bin/python" ] && "$1/bin/python" -m pip --version > /dev/null 2>&1
}

print_python_help() {
    echo "    En Kali/Debian/Ubuntu: sudo apt update && sudo apt install -y python3 python3-venv python3-pip libportaudio2"
    echo "    En Fedora: sudo dnf install -y python3 python3-pip portaudio"
}

# Localizar entorno virtual de Python
PYTHON_BIN=""
VENV_DIR="$SCRIPT_DIR/.venv"
INSTALL_ARGS=()

if [ -x "$SCRIPT_DIR/app/.venv/bin/python" ]; then
    # Entorno que gestiona el usuario: se usa tal cual
    PYTHON_BIN="$SCRIPT_DIR/app/.venv/bin/python"
else
    # [FIX MUL-8] Un .venv roto se quedaba para siempre: en Debian/Kali sin python3-venv,
    # `python3 -m venv` deja bin/python sin pip, y tras actualizar el python3 del sistema
    # el venv apunta a un intérprete que ya no existe. Se detecta y se recrea.
    if [ -e "$VENV_DIR" ] && ! venv_ok "$VENV_DIR"; then
        echo "[!] El entorno virtual .venv está incompleto o roto (sin intérprete o sin pip). Recreándolo..."
        rm -rf "$VENV_DIR"
    fi

    if [ ! -d "$VENV_DIR" ]; then
        BASE_PYTHON=""
        for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.14; do
            if command -v "$candidate" &> /dev/null && python_ok "$candidate"; then
                BASE_PYTHON="$(command -v "$candidate")"
                break
            fi
        done
        if [ -z "$BASE_PYTHON" ]; then
            echo "[!] Error: No se encontró intérprete de Python 3.10+ en el sistema."
            print_python_help
            exit 1
        fi

        echo "[*] Creando entorno virtual .venv con $("$BASE_PYTHON" --version)..."
        if ! "$BASE_PYTHON" -m venv "$VENV_DIR" || ! venv_ok "$VENV_DIR"; then
            rm -rf "$VENV_DIR"
            echo "[!] Error: No se pudo crear el entorno virtual (falta el módulo venv/ensurepip de Python)."
            echo "    En Kali/Debian/Ubuntu: sudo apt install -y python3-venv"
            echo "    (si sigue fallando, el paquete con la versión exacta: python3.X-venv)"
            echo "    En Fedora: sudo dnf install -y python3 python3-pip"
            exit 1
        fi
        "$VENV_DIR/bin/python" -m pip install --upgrade pip \
            || echo "[!] No se pudo actualizar pip; se continúa con el del entorno virtual."
    fi

    PYTHON_BIN="$VENV_DIR/bin/python"
    # setup_and_run.py instala las dependencias (con torch de solo CPU si no hay GPU NVIDIA)
    # o completa una instalación que se cortó; si ya está todo, no descarga nada.
    INSTALL_ARGS=(--install)
fi

echo "[*] Utilizando Python: $("$PYTHON_BIN" --version) en $PYTHON_BIN"

# Ejecutar el orquestador principal
exec "$PYTHON_BIN" "$SCRIPT_DIR/setup_and_run.py" "${INSTALL_ARGS[@]}" "$@"
