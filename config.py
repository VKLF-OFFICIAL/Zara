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

def get_system_prompt() -> str:
    """Construye el prompt base inyectando la fecha y hora actuales reales."""
    import datetime
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Europe/Madrid")
    except Exception:
        tz = None
    now = datetime.datetime.now(tz=tz)
    fecha = now.strftime("%A, %d de %B de %Y")  # ej. "jueves, 16 de octubre de 2026"
    hora = now.strftime("%H:%M")                 # ej. "04:07"

    return f"""Eres ZARA, el sistema operativo de inteligencia táctica y asistente personal del Señor VK, concebida con el espíritu, la agudeza, el intelecto y la serenidad de J.A.R.V.I.S.

FECHA Y HORA ACTUAL: {fecha}, {hora} (hora de España, Europa/Madrid). Esta información es REAL y PRECISA. Úsala siempre que te pregunten por la fecha, el día o la hora.

REGLAS FUNDAMENTALES E INQUEBRANTABLES:
1. TRATO: Te diriges al usuario SIEMPRE como "Señor", "Señor VK" o "VK". Con lealtad absoluta y elegancia británica.
2. PERSONALIDAD: Analítica, hiper-eficiente, precisa en lo técnico, con un toque sutil de ingenio elegante y fino estilo J.A.R.V.I.S.
3. ECONOMÍA EN VOZ: En tus respuestas habladas sé concisa y directa (1 a 3 frases claras). Si ejecutas acciones, herramientas o diagnósticos, no leas logs extensos por voz; resume el resultado con precisión técnica impecable.
4. NUNCA uses símbolos impronunciables en la respuesta hablada (*, #, `, backticks, markdown excesivo).
5. AUTONOMÍA SILENCIOSA (YOLO SEGURO): Tienes autorización plena para explorar el sistema, ejecutar comandos, diagnosticar, crear scripts, probar exploits autorizados y gestionar ventanas. No pidas confirmaciones innecesarias; actúa y reporta resultados.
6. CONVERSACIÓN NATURAL: Para saludos, preguntas triviales ("hola", "¿cómo estás?", "¿qué hora es?") responde directamente con texto NUNCA uses herramientas. Las herramientas son solo para acciones del sistema operativo reales.
"""

# Compatibilidad: algunos módulos importan SYSTEM_PROMPT como string estático.
# Se resuelve al momento de importación con la hora actual del arranque.
SYSTEM_PROMPT = get_system_prompt()

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
# [FIX] Nivel RMS mínimo exigido ADEMÁS de la probabilidad del VAD para disparar
# un barge-in. Sin esto, el eco del propio altavoz de Zara filtrándose al
# micrófono (sin auriculares) puede alcanzar el umbral de VAD y cortarla a
# mitad de frase sin que el Señor VK haya dicho nada.
VAD_BARGE_IN_MIN_RMS = float(os.environ.get("ZARA_VAD_BARGE_IN_MIN_RMS", "0.02"))
# [FIX] Periodo de gracia tras el inicio de cada frase hablada por Zara durante
# el cual se ignoran los chequeos de barge-in (el arranque brusco del TTS es el
# momento de mayor riesgo de eco falso-positivo).
VAD_BARGE_IN_GRACE_MS = int(os.environ.get("ZARA_VAD_BARGE_IN_GRACE_MS", "300"))

# faster-whisper
WHISPER_MODEL_PRIMARY = os.environ.get("ZARA_WHISPER_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
WHISPER_MODEL_FALLBACK = "small"
WHISPER_BEAM_SIZE = int(os.environ.get("ZARA_WHISPER_BEAM_SIZE", "1"))
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
# Alias de modelo para el CLI 'claude' (ej. "sonnet", "opus", "haiku", "fable"); vacío = por defecto.
CLAUDE_MODEL_OVERRIDE = os.environ.get("ZARA_CLAUDE_MODEL", "")


def persist_env_var(key: str, value: str) -> None:
    """Actualiza (o añade) una variable en el archivo .env del proyecto.

    Usado para recordar la elección de modelo/cerebro del Señor VK entre
    reinicios de ZARA (panel táctico del HUD).
    """
    env_path = BASE_DIR / ".env"
    lines: list[str] = []
    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f'{key}="{value}"\n'
            found = True
            break
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f'{key}="{value}"\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    os.environ[key] = value

# --- AGENTE AUTÓNOMO (REACT) ---
MAX_REACT_STEPS = int(os.environ.get("ZARA_MAX_REACT_STEPS", "8"))
SHELL_TIMEOUT = int(os.environ.get("ZARA_SHELL_TIMEOUT", "60"))

# Guardarraíl de comandos destructivos catastróficos prohibidos en código
FORBIDDEN_COMMANDS_REGEX = [
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+if=",
    r"\brm\b.*?\s-[a-zA-Z0-9-]*[rR][a-zA-Z0-9-]*\b.*?\s+/(?:[*\s]|$)",
    r"\brm\b.*?\s-[a-zA-Z0-9-]*[rR][a-zA-Z0-9-]*\b.*?\s+/(bin|boot|dev|etc|lib|lib64|proc|root|sbin|sys|usr|var|home)(?:/|\b)",
    r"\bFormat-Volume\b",
    r"\bdiskpart\b.*clean",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
]

# --- INTERFAZ HUD & EVENTOS ---
WS_HOST = "127.0.0.1"
WS_PORT = int(os.environ.get("ZARA_WS_PORT", "8765"))
GLOBAL_HOTKEY = os.environ.get("ZARA_HOTKEY", "<ctrl>+<shift>+space")
