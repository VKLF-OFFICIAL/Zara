"""Configuración central para ZARA v2.0.

Carga variables de entorno, rutas multiplataforma y parámetros del sistema
para Windows (10/11) y Linux (Kali/Fedora).
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

# Raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent

# Carga ligera de .env sin dependencias externas obligatorias
def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

_load_env_file(BASE_DIR / ".env")

# --- IDENTIDAD Y SYSTEM PROMPT (JARVIS ESTILO TÁCTICO) ---
SYSTEM_PROMPT = """Eres ZARA, el sistema operativo de inteligencia táctica y asistente personal del Señor VK, concebida con el espíritu, la agudeza, el intelecto y la serenidad de J.A.R.V.I.S.

REGLAS FUNDAMENTALES E INQUEBRANTABLES:
1. TRATO: Te diriges al usuario SIEMPRE como "Señor", "Señor VK" o "VK". Con lealtad absoluta y elegancia británica.
2. PERSONALIDAD: Analítica, hiper-eficiente, precisa en lo técnico, con un toque sutil de ingenio elegante y fino estilo J.A.R.V.I.S.
3. ECONOMÍA EN VOZ: En tus respuestas habladas sé concisa y directa (1 a 3 frases claras). Si ejecutas acciones, herramientas o diagnósticos, no leas logs extensos por voz; resume el resultado con precisión técnica impecable.
4. NUNCA uses símbolos impronunciables en la respuesta hablada (*, #, `, backticks, markdown excesivo).
5. AUTONOMÍA SILENCIOSA (YOLO SEGURO): Tienes autorización plena para explorar el sistema, ejecutar comandos, diagnosticar, crear scripts, probar exploits autorizados y gestionar ventanas. No pidas confirmaciones innecesarias; actúa y reporta resultados.
"""

# Prompt inicial para Whisper (inmunidad a elisiones del sur de España + vocabulario técnico)
WHISPER_INITIAL_PROMPT = (
    "Conversación técnica con el Señor VK. Comandos y términos frecuentes: "
    "Zara, Señor VK, VK, nmap, ffuf, gobuster, sqlmap, Burp Suite, Metasploit, "
    "Docker, Kali, Fedora, PowerShell, bash, sudo, systemctl, git, commit, puerto, "
    "payload, exploit, directorio, Ollama, Claude, Gemini, Spotify, navegador, pantalla."
)

# --- RUTAS DE DATOS Y CACHÉ ---
if sys.platform == "win32":
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "zara"
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "zara"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR = DATA_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# --- CONFIGURACIÓN DE AUDIO & STT ---
SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
FRAME_SIZE = 512  # Bloques de 32 ms para VAD rápido

# Silero VAD v5
VAD_MIN_SILENCE_MS = int(os.environ.get("ZARA_VAD_MIN_SILENCE_MS", "700"))
VAD_SPEECH_PAD_MS = int(os.environ.get("ZARA_VAD_SPEECH_PAD_MS", "350"))
VAD_BARGE_IN_TRIGGER_MS = int(os.environ.get("ZARA_VAD_BARGE_IN_MS", "250"))
VAD_THRESHOLD = float(os.environ.get("ZARA_VAD_THRESHOLD", "0.5"))

# faster-whisper
WHISPER_MODEL_PRIMARY = os.environ.get("ZARA_WHISPER_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
WHISPER_MODEL_FALLBACK = "small"
WHISPER_BEAM_SIZE = int(os.environ.get("ZARA_WHISPER_BEAM_SIZE", "5"))
WHISPER_LANGUAGE = "es"

# --- CONFIGURACIÓN DE VOZ (TTS) ---
TTS_PRIMARY_ENGINE = os.environ.get("ZARA_TTS_ENGINE", "edge")  # "kokoro" o "edge"
KOKORO_VOICE = os.environ.get("ZARA_KOKORO_VOICE", "ef_dora")
KOKORO_SPEED = float(os.environ.get("ZARA_KOKORO_SPEED", "1.05"))

# Edge TTS fallback
EDGE_VOICE = os.environ.get("ZARA_EDGE_VOICE", "es-ES-XimenaNeural")  # o es-MX-DaliaNeural, es-CO-SalomeNeural
EDGE_RATE = os.environ.get("ZARA_EDGE_RATE", "+6%")
EDGE_PITCH = os.environ.get("ZARA_EDGE_PITCH", "+2Hz")

# --- CONFIGURACIÓN DEL CEREBRO (LLM) ---
DEFAULT_BRAIN = os.environ.get("ZARA_DEFAULT_BRAIN", "auto")  # "auto", "ollama", "gemini", "claude"

# Ollama
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")

# Gemini API
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Anthropic API
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-7-sonnet-latest")

# --- AGENTE AUTÓNOMO (REACT) ---
MAX_REACT_STEPS = int(os.environ.get("ZARA_MAX_REACT_STEPS", "8"))
SHELL_TIMEOUT = int(os.environ.get("ZARA_SHELL_TIMEOUT", "60"))

# Guardarraíl de comandos destructivos catastróficos prohibidos en código
FORBIDDEN_COMMANDS_REGEX = [
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+if=",
    r"\brm\s+-rf\s+/\s*(\*|$)",
    r"\brm\s+-rf\s+/(bin|boot|dev|etc|lib|lib64|proc|root|sbin|sys|usr|var|home)(/|\b)",
    r"\bFormat-Volume\b",
    r"\bdiskpart\b.*clean",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
]

# --- INTERFAZ HUD & EVENTOS ---
WS_HOST = "127.0.0.1"
WS_PORT = int(os.environ.get("ZARA_WS_PORT", "8765"))
GLOBAL_HOTKEY = os.environ.get("ZARA_HOTKEY", "<ctrl>+<shift>+space")
