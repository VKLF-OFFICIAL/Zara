#!/usr/bin/env bash
# =====================================================================
# ZARA v2.0 - Lanzador Táctico para Linux (Kali Linux, Fedora, Debian)
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

# Localizar entorno virtual de Python
PYTHON_BIN=""

if [ -f "$SCRIPT_DIR/app/.venv/bin/python" ]; then
    PYTHON_BIN="$SCRIPT_DIR/app/.venv/bin/python"
elif [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 &> /dev/null; then
    # Comprobar si existe venv o crearlo
    if [ ! -d "$SCRIPT_DIR/.venv" ]; then
        echo "[*] Creando entorno virtual .venv..."
        python3 -m venv "$SCRIPT_DIR/.venv"
        "$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip
        echo "[*] Instalando dependencias de ZARA v2.0..."
        "$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
    fi
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "[!] Error: No se encontró intérprete de Python 3.10+ en el sistema."
    echo "    En Kali/Debian: sudo apt update && sudo apt install -y python3 python3-venv python3-pip portaudio19-dev"
    echo "    En Fedora: sudo dnf install -y python3 python3-devel alsa-lib-devel portaudio-devel"
    exit 1
fi

echo "[*] Utilizando Python: $($PYTHON_BIN --version) en $PYTHON_BIN"

# Ejecutar el orquestador principal
exec "$PYTHON_BIN" "$SCRIPT_DIR/setup_and_run.py" "$@"
