"""Configuración central de ZARA.

Carga variables de entorno, rutas multiplataforma y parámetros del sistema
para Windows (10/11) y Linux (Kali/Fedora).
"""

from __future__ import annotations
import math
import os
import shutil
import stat
import sys
from pathlib import Path

# Versión (única fuente de verdad; SemVer 0.y.z = en desarrollo, sin API estable).
# 0.3.0-alpha: la tubería de voz → cerebro → herramientas → HUD funciona de extremo a
# extremo, pero la latencia, la verificación de hablante, el empaquetado y los tests de
# audio/UI en CI aún no están resueltos. Pasará a 1.0.0 cuando haya una versión estable.
VERSION = "0.3.0"
RELEASE_STAGE = "alpha"
VERSION_FULL = f"{VERSION}-{RELEASE_STAGE}"
APP_TITLE = f"ZARA v{VERSION_FULL}"

# Raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent

def _config_warning(msg: str) -> None:
    """Avisos de configuración: se imprimen por stderr porque el logging aún no existe."""
    try:
        print(f"[config] {msg}", file=sys.stderr)
    except Exception:
        pass


def _read_env_text(path: Path) -> str:
    """Lee un .env en UTF-8 (con o sin BOM) o UTF-16 (PowerShell 5.1 con `>` escribe UTF-16 LE)."""
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig")
    return text.replace("\r\n", "\n")  # como el modo texto: al reescribir no quedan \r\r\n


def _write_private(path: Path, text: str) -> None:
    """Escritura atómica del .env; en Linux/macOS queda en 0600 (contiene claves de API).

    El temporal nace ya con 0600 (os.open) para que las claves nunca sean legibles por
    otros usuarios ni durante un instante. En Windows el modo apenas tiene efecto.
    """
    tmp_path = path.with_name(path.name + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if os.name != "nt":
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # por si el temporal ya existía con otro modo
    os.replace(tmp_path, path)  # nunca deja un .env a medias


def _parse_env_value(raw: str) -> str:
    """Valor de una línea KEY=valor: quita comillas y comentarios en línea (`1  # nota`)."""
    v = raw.strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        if end != -1:
            return v[1:end]
    elif " #" in v or "\t#" in v:
        v = v.split("#", 1)[0].rstrip()
    return v.strip("'\"")


# Carga ligera de .env sin dependencias externas obligatorias
def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = _read_env_text(path)
    except Exception as exc:
        # Antes se ignoraba en silencio y se perdían todas las claves de API sin aviso.
        _config_warning(f"No se pudo leer {path}: {exc}. Guárdelo como UTF-8.")
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = _parse_env_value(v)
        if k and k not in os.environ:
            os.environ[k] = v


def _env_num(name: str, default, cast=int, minimum=None):
    """Lee un número del entorno sin tumbar el arranque si está mal escrito.

    Acepta coma decimal (`0,5`) y comentarios en línea; si el valor no es válido avisa
    por stderr y usa el valor por defecto (antes un ValueError impedía `import config`).
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    text = raw.split("#", 1)[0].strip().replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is None or not math.isfinite(number) or (cast is int and not number.is_integer()):
        _config_warning(f"{name}={raw!r} no es un número válido; uso {default}.")
        return default
    value = int(number) if cast is int else float(number)
    if minimum is not None and value < minimum:
        _config_warning(f"{name}={raw!r} es menor que {minimum}; uso {default}.")
        return default
    return value


# Marca que indica que el .env ya está en el formato actual (ver _migrate_legacy_env).
ENV_SCHEMA_MARKER = "# zara-env-schema: 2"

# Valores que solo pudieron llegar a un .env copiando las plantillas .env.example antiguas
# (antes todos los ajustes vivían ahí). Al estar en .env tapaban config/ajustes.env.
_LEGACY_TEMPLATE_VALUES = {
    "ZARA_DEFAULT_BRAIN": {"auto"},
    "ZARA_GEMINI_LIVE_MODEL": {"gemini-2.5-flash-preview-native-audio-dialog"},
    "ZARA_WHISPER_MODEL": {"deepdml/faster-whisper-large-v3-turbo-ct2"},
    "ZARA_WHISPER_BEAM_SIZE": {"5"},
    "ZARA_VAD_MIN_SILENCE_MS": {"700"},
    "ZARA_WS_PORT": {"8765"},
}

# Claves quitadas del .env en esta ejecución (main.py lo registra en el log).
ENV_MIGRATED_KEYS: list[str] = []


def _migrate_legacy_env(path: Path) -> list[str]:
    """Una sola vez por .env: quita las líneas heredadas de plantillas antiguas.

    Solo elimina pares clave=valor idénticos a los de esas plantillas (nunca claves de
    API ni valores elegidos por el usuario), guarda una copia en .env.bak y añade
    ENV_SCHEMA_MARKER para no volver a tocar el archivo.
    """
    if not path.is_file():
        return []
    try:
        lines = _read_env_text(path).splitlines(keepends=True)
    except Exception:
        return []
    if any(line.strip() == ENV_SCHEMA_MARKER for line in lines):
        return []

    kept: list[str] = []
    removed: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            key, value = key.strip(), value.strip().strip("'\"")
            if value in _LEGACY_TEMPLATE_VALUES.get(key, ()):
                removed.append(key)
                continue
        kept.append(line)

    try:
        if removed:
            bak = path.with_name(path.name + ".bak")
            shutil.copy2(path, bak)
            if os.name != "nt":
                os.chmod(bak, stat.S_IRUSR | stat.S_IWUSR)  # la copia también lleva las claves
        if kept and not kept[-1].endswith("\n"):
            kept[-1] += "\n"
        kept.append(ENV_SCHEMA_MARKER + "\n")
        _write_private(path, "".join(kept))
    except Exception:
        return []
    return removed


# Orden de prioridad: entorno del sistema > .env (local, privado) > config/ajustes.env
# (preferencias del repositorio). _load_env_file nunca sobrescribe lo ya definido.
ENV_MIGRATED_KEYS.extend(_migrate_legacy_env(BASE_DIR / ".env"))
_load_env_file(BASE_DIR / ".env")
_load_env_file(BASE_DIR / "config" / "ajustes.env")

def get_system_prompt() -> str:
    """Construye el prompt base inyectando la fecha y hora actuales reales."""
    import datetime
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Europe/Madrid")
    except Exception:
        tz = None
    now = datetime.datetime.now(tz=tz)
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    # Manual: strftime("%A/%B") depende del locale del SO y salía en inglés en muchos equipos.
    fecha = f"{dias[now.weekday()]}, {now.day:02d} de {meses[now.month - 1]} de {now.year}"
    hora = now.strftime("%H:%M")                 # ej. "04:07"
    # El modelo debe saber en qué SO y con qué shell trabaja: sin esto emitía comandos de
    # PowerShell en Linux (y al revés) y rutas de otro sistema.
    import platform
    if sys.platform == "win32":
        sistema, shell = "Windows", "PowerShell 5.1 (usa sintaxis de PowerShell, no de Bash)"
    else:
        sistema, shell = ("macOS" if sys.platform == "darwin" else "Linux"), "bash"
    entorno = f"{sistema} {platform.release()}; la herramienta run_shell ejecuta {shell}"

    return f"""Eres ZARA, el sistema operativo de inteligencia táctica y asistente personal del Señor VK, concebida con el espíritu, la agudeza, el intelecto y la serenidad de J.A.R.V.I.S.

FECHA Y HORA ACTUAL: {fecha}, {hora} (hora de España, Europa/Madrid). Esta información es REAL y PRECISA. Úsala siempre que te pregunten por la fecha, el día o la hora.

SISTEMA OPERATIVO: {entorno}. Usa rutas absolutas de este sistema.

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
# Silencio que cierra una locución. Es tiempo muerto puro en CADA orden: 500 ms conserva
# las pausas naturales y recorta 200 ms de latencia frente a los 700 ms anteriores.
VAD_MIN_SILENCE_MS = _env_num("ZARA_VAD_MIN_SILENCE_MS", 500, minimum=0)
VAD_SPEECH_PAD_MS = _env_num("ZARA_VAD_SPEECH_PAD_MS", 350, minimum=0)
VAD_BARGE_IN_TRIGGER_MS = _env_num("ZARA_VAD_BARGE_IN_MS", 250, minimum=0)
VAD_THRESHOLD = _env_num("ZARA_VAD_THRESHOLD", 0.5, float, minimum=0.0)
# [FIX] Nivel RMS mínimo exigido ADEMÁS de la probabilidad del VAD para disparar
# un barge-in. Sin esto, el eco del propio altavoz de Zara filtrándose al
# micrófono (sin auriculares) puede alcanzar el umbral de VAD y cortarla a
# mitad de frase sin que el Señor VK haya dicho nada.
VAD_BARGE_IN_MIN_RMS = _env_num("ZARA_VAD_BARGE_IN_MIN_RMS", 0.02, float, minimum=0.0)
# [FIX] Periodo de gracia tras el inicio de cada frase hablada por Zara durante
# el cual se ignoran los chequeos de barge-in (el arranque brusco del TTS es el
# momento de mayor riesgo de eco falso-positivo).
VAD_BARGE_IN_GRACE_MS = _env_num("ZARA_VAD_BARGE_IN_GRACE_MS", 300, minimum=0)

# faster-whisper
# "auto" elige según el dispositivo: large-v3-turbo (~800 M parámetros) solo es rápido en
# GPU; en CPU tarda decenas de segundos por frase, así que ahí se usa "small".
# Se puede fijar a mano: tiny | base | small | medium | <repo HF>.
WHISPER_MODEL_PRIMARY = os.environ.get("ZARA_WHISPER_MODEL", "auto")
WHISPER_MODEL_GPU = "deepdml/faster-whisper-large-v3-turbo-ct2"
WHISPER_MODEL_CPU = "small"
WHISPER_MODEL_FALLBACK = "small"
WHISPER_CPU_THREADS = _env_num("ZARA_WHISPER_CPU_THREADS", max(4, os.cpu_count() or 4), minimum=1)


# La plantilla .env.example antigua fijaba large-v3-turbo + beam 5 y setup_and_run la copiaba
# a todos los .env: en CPU eso hace que cada orden tarde decenas de segundos. Salvo que se
# fuerce con ZARA_WHISPER_FORCE=1, en CPU se ignora ese valor heredado y se usa el modelo ligero.
WHISPER_FORCE = os.environ.get("ZARA_WHISPER_FORCE", "") == "1"


def resolve_whisper_model(setting: str, device: str) -> str:
    """Traduce ZARA_WHISPER_MODEL ("auto" o un nombre concreto) al modelo a cargar."""
    if not setting or setting == "auto":
        return WHISPER_MODEL_GPU if device == "cuda" else WHISPER_MODEL_CPU
    if setting == WHISPER_MODEL_GPU and device != "cuda" and not WHISPER_FORCE:
        return WHISPER_MODEL_CPU
    return setting


def resolve_beam_size(beam: int, device: str) -> int:
    """En CPU un haz > 1 multiplica el tiempo de decodificación; se limita salvo ZARA_WHISPER_FORCE=1."""
    return beam if (device == "cuda" or WHISPER_FORCE) else min(beam, 1)
WHISPER_BEAM_SIZE = _env_num("ZARA_WHISPER_BEAM_SIZE", 1, minimum=1)
WHISPER_LANGUAGE = "es"

# --- CONFIGURACIÓN DE VOZ (TTS) ---
TTS_PRIMARY_ENGINE = os.environ.get("ZARA_TTS_ENGINE", "edge")  # "kokoro" o "edge"
KOKORO_VOICE = os.environ.get("ZARA_KOKORO_VOICE", "ef_dora")
KOKORO_SPEED = _env_num("ZARA_KOKORO_SPEED", 1.05, float, minimum=0.1)

# Edge TTS fallback
EDGE_VOICE = os.environ.get("ZARA_EDGE_VOICE", "es-ES-XimenaNeural")  # o es-MX-DaliaNeural, es-CO-SalomeNeural
EDGE_RATE = os.environ.get("ZARA_EDGE_RATE", "+6%")
EDGE_PITCH = os.environ.get("ZARA_EDGE_PITCH", "+2Hz")

# --- CONFIGURACIÓN DEL CEREBRO (LLM) ---
DEFAULT_BRAIN = os.environ.get("ZARA_DEFAULT_BRAIN", "auto")  # "auto", "antigravity", "ollama", "gemini", "claude"

# Ollama
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
# [FIX] Vacío por defecto = autodetectar entre los modelos instalados
# (ver OllamaProvider.__init__ en core/brain.py) en vez de fallar contra un
# nombre fijo que puede no existir en esta máquina.
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")

# Gemini API & Live
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# "auto" = descubrir con models.list() el Flash estable más reciente (core/gemini_models.py).
# Los *_FALLBACK se usan solo si la API no responde al listar.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "auto")
GEMINI_MODEL_FALLBACK = "gemini-2.5-flash"
GEMINI_LIVE_MODEL = os.environ.get("ZARA_GEMINI_LIVE_MODEL", "auto")
GEMINI_LIVE_MODEL_FALLBACK = "gemini-2.5-flash-native-audio-preview-12-2025"
GEMINI_LIVE_VOICE = os.environ.get("ZARA_GEMINI_LIVE_VOICE", "Aoede")

# Anthropic API
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
# Antigravity CLI (agy): Gemini con la cuota de la suscripción Google AI Pro/Ultra, sin API key.
# Google retiró Gemini CLI para cuentas personales el 18-jun-2026 y lo sustituyó por agy.
AGY_BIN = os.environ.get("ZARA_AGY_BIN", "agy")
AGY_MODEL = os.environ.get("ZARA_AGY_MODEL", "")  # "" = el modelo por defecto de agy (ver `agy models`)
AGY_TIMEOUT = _env_num("ZARA_AGY_TIMEOUT", 120, minimum=5)

# Alias de modelo para el CLI 'claude' (ej. "sonnet", "opus", "haiku", "fable"); vacío = por defecto.
CLAUDE_MODEL_OVERRIDE = os.environ.get("ZARA_CLAUDE_MODEL", "")


def persist_env_var(key: str, value: str) -> None:
    """Actualiza (o añade) una variable en el archivo .env del proyecto.

    Usado para recordar la elección de modelo/cerebro del Señor VK entre
    reinicios de ZARA (panel táctico del HUD).
    """
    if not key.isidentifier() or any(c in value for c in "\r\n\"\0"):
        raise ValueError(f"Valor no válido para persistir en .env: {key!r}")
    env_path = BASE_DIR / ".env"
    lines: list[str] = []
    if env_path.is_file():
        lines = _read_env_text(env_path).splitlines(keepends=True)

    if not any(line.strip() == ENV_SCHEMA_MARKER for line in lines):
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(ENV_SCHEMA_MARKER + "\n")

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

    # Escritura atómica y privada: antes el temporal nacía 0644 y os.replace devolvía el
    # .env a «legible por todos» aunque el usuario lo hubiera dejado en 0600.
    _write_private(env_path, "".join(lines))

    os.environ[key] = value

# --- AGENTE AUTÓNOMO (REACT) ---
MAX_REACT_STEPS = _env_num("ZARA_MAX_REACT_STEPS", 8, minimum=1)
SHELL_TIMEOUT = _env_num("ZARA_SHELL_TIMEOUT", 60, minimum=1)
SHELL_TIMEOUT_MAX = 900  # composer/npm install pueden tardar varios minutos

# Guardarraíl de comandos destructivos catastróficos prohibidos en código
FORBIDDEN_COMMANDS_REGEX = [
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+if=",
    r"\brm\b.*?\s-[a-zA-Z0-9-]*[rR][a-zA-Z0-9-]*\b.*?\s+/(?:[*\s]|$)",
    r"\brm\b.*?\s-[a-zA-Z0-9-]*[rR][a-zA-Z0-9-]*\b.*?\s+/(bin|boot|dev|etc|lib|lib64|proc|root|sbin|sys|usr|var|home)(?:/|\b)",
    r"\bFormat-Volume\b",
    r"\bdiskpart\b.*clean",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\brm\b.*?\s-[a-zA-Z0-9-]*[rR][a-zA-Z0-9-]*\b.*?\s+(~|\$HOME|\"\$HOME\")/?(?:[*\s]|$)",
    r"\bfind\s+/(?:\s|$).*-delete\b",
    r"\b(shutdown|reboot|poweroff|halt)\b",
    r"\bStop-Computer\b|\bRestart-Computer\b",
    r"(>|\btee\b|\bmv\b|\bcp\b).*?/etc/(fstab|passwd|shadow|sudoers)\b",
    r"(>|\bof=)\s*/dev/(sd|nvme|vd|hd)\w+",
]

# Comandos que NO se bloquean pero piden confirmación hablada (si hay manejador registrado).
CONFIRM_COMMANDS_REGEX = [
    r"\bsudo\b", r"(^|[;&|]\s*)su\b", r"\brm\b", r"\brmdir\b", r"\bRemove-Item\b", r"\bdel\s",
    r"\bkill(all)?\b", r"\bpkill\b", r"\bStop-Process\b", r"\btaskkill\b",
    r"\b(pip3?|npm|apt|apt-get|dnf|yum|pacman|winget|choco)\s+(install|remove|uninstall|upgrade)\b",
    r"\bsystemctl\b", r"\bchmod\b\s+-R", r"\bchown\b", r"\bgit\s+(push|reset\s+--hard|clean)\b",
    r"\b(curl|wget)\b.*(\|\s*(ba)?sh|-o\b|-O\b)", r"\bInvoke-Expression\b|\biex\b",
    r"\bSet-ExecutionPolicy\b", r"\breg\s+(add|delete)\b", r"\bpasswd\b", r"\.ssh/|id_rsa|id_ed25519",
]

# Rutas que las herramientas de ficheros nunca pueden modificar ni borrar.
# PROTECTED_TREES: el directorio y todo lo que cuelga de él.
# PROTECTED_EXACT: solo el directorio en sí (no se puede borrar/sobrescribir, pero sí
# trabajar dentro: el agente debe poder escribir en el HOME del usuario).
if sys.platform == "win32":
    PROTECTED_TREES = [
        os.environ.get("SystemRoot", r"C:\Windows"),
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        "/etc",
        "/boot",
        "/bin",
        "/sbin",
        "/lib",
        "/usr",
        "/var",
        "/root",
    ]
    PROTECTED_EXACT = [str(Path.home()), "C:\\", "/"]
else:
    PROTECTED_TREES = ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys", "/usr"]
    PROTECTED_EXACT = ["/", "/home", "/root", "/var", str(Path.home())]
PROTECTED_TREES += [str(Path.home() / ".ssh"), str(Path.home() / ".gnupg"), str(BASE_DIR / ".git")]
