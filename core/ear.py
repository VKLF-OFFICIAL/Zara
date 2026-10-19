"""Subsistema de Audio y Oído (ZARA).

Captura continua a 16 kHz mono con sounddevice, detección neuronal de voz
con Silero VAD v5 (cero cortes en pausas de hasta 700 ms), barge-in a los 250 ms
y transcripción con faster-whisper (GPU/CPU fallback e inyección fonética).

BUGS CORREGIDOS:
  - [BUG-1] VADIterator: reset_states() llamado durante barge-in mientras Zara
    habla provocaba pérdida del estado interno y silencio post-barge-in.
    Ahora sólo se resetea tras detectar el final real de una locución.
  - [BUG-2] Conversión tensor_chunk: numpy float32 -> torch sin .unsqueeze(0)
    → Silero v5 espera [1, T] en el nuevo API 'model(tensor, sr)'. Añadido.
  - [BUG-3] FRAME_SIZE=512 → 32 ms a 16 kHz; el VADIterator de silero-vad >= 6
    acepta exactamente 512 muestras. Verificación de longitud ahora permite
    bloques menores y los rellena con ceros (padding) en vez de descartarlos.
  - [BUG-4] listen_utterances() consumía el hilo principal bloqueando el VAD si
    la transcripción era lenta. Ahora se transcribe en el propio hilo del worker.
  - [MUL-1] La GPU se detecta con ctranslate2.get_cuda_device_count() (la API
    anterior no existía y tumbaba el arranque en Linux con NVIDIA) y, si CUDA no
    sirve de verdad (faltan cuBLAS/cuDNN), Whisper pasa a CPU en vez de quedar sordo.
  - [V1_-5] Sin red y sin el modelo descargado ya no se cae la app: se usa un
    modelo en caché si lo hay o el oído queda sin STT con un aviso claro.
  - [AUD-7/V1_-9] El micrófono se reintenta con espera si falta, se desconecta o
    deja de dar audio, y se captura a su frecuencia nativa si no admite 16 kHz.
"""

from __future__ import annotations
import collections
import logging
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Generator, Optional
import numpy as np
import torch

from config import (
    FRAME_SIZE,
    MODELS_DIR,
    SAMPLE_RATE,
    VAD_BARGE_IN_GRACE_MS,
    VAD_BARGE_IN_MIN_RMS,
    VAD_BARGE_IN_TRIGGER_MS,
    VAD_MIN_SILENCE_MS,
    VAD_SPEECH_PAD_MS,
    VAD_THRESHOLD,
    WHISPER_BEAM_SIZE,
    WHISPER_CPU_THREADS,
    WHISPER_INITIAL_PROMPT,
    WHISPER_LANGUAGE,
    WHISPER_MODEL_FALLBACK,
    WHISPER_MODEL_PRIMARY,
    resolve_beam_size,
    resolve_whisper_model,
)

log = logging.getLogger("zara.ear")

# [FIX AUD-7/V1_-9] Reintentos del micrófono: espera creciente entre intentos y
# tiempo máximo sin recibir audio antes de dar el dispositivo por perdido.
_MIC_RETRY_MIN_S = 1.0
_MIC_RETRY_MAX_S = 10.0
_MIC_STALL_S = 4.0
# [FIX AUD-7] Mínimo entre reinicializaciones de PortAudio buscando un micrófono nuevo.
_PA_REFRESH_INTERVAL_S = 60.0


class _AudioBackendError(RuntimeError):
    """sounddevice/PortAudio no disponibles (el mensaje ya explica cómo resolverlo)."""


def _import_sounddevice():
    """[FIX MUL-2] Importa sounddevice con un error claro en vez de matar el hilo.

    En Linux sin la librería de sistema PortAudio (libportaudio2), `import sounddevice`
    lanza OSError, no ImportError. Se reintenta en cada intento de abrir el micrófono,
    así que si el usuario instala PortAudio con Zara abierta, el oído se recupera solo.
    """
    try:
        import sounddevice as sd
    except OSError as exc:
        raise _AudioBackendError(
            f"PortAudio no está instalado ({exc}): Zara no puede usar el micrófono. En Linux: "
            "sudo apt install libportaudio2 (Debian/Kali/Ubuntu) o sudo dnf install portaudio (Fedora)."
        ) from exc
    except ImportError as exc:
        raise _AudioBackendError(
            f"Falta el paquete sounddevice ({exc}): ejecute pip install -r requirements.txt."
        ) from exc
    return sd


def _describe_mic_error(exc: BaseException) -> str:
    """Mensaje para el log y el panel cuando el micrófono no se puede usar."""
    if isinstance(exc, _AudioBackendError):
        return str(exc)
    if sys.platform == "win32":
        hint = ("Compruebe que hay un micrófono predeterminado y que en Configuración > Privacidad > "
                "Micrófono las aplicaciones de escritorio pueden usarlo.")
    else:
        hint = ("Compruebe que el micrófono está conectado y sin silenciar, y que PipeWire/PulseAudio "
                "está activo (pavucontrol).")
    return f"Micrófono no disponible ({exc}). {hint}"


def _pick_input_samplerate(sd) -> int:  # noqa: ANN001
    """[FIX V1_-9] 16 kHz si el micrófono lo admite; si no, su frecuencia nativa.

    Un dispositivo ALSA «hw» directo (sin PipeWire/Pulse) o algunos USB/Bluetooth no
    admiten 16 kHz y antes el hilo de captura moría. Si no hay micrófono por defecto,
    `query_devices` lanza la excepción y la gestiona el bucle de reintentos.
    """
    try:
        sd.check_input_settings(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        return SAMPLE_RATE
    except Exception as exc:
        info = sd.query_devices(kind="input")
        native = int(round(float(info.get("default_samplerate") or 0)))
        if native <= 0 or native == SAMPLE_RATE:
            raise
        log.info("El micrófono no admite %d Hz (%s): se captura a %d Hz y se remuestrea.",
                 SAMPLE_RATE, exc, native)
        return native


class _StreamResampler:
    """[FIX V1_-9] Remuestreo en streaming de la frecuencia nativa del micrófono a 16 kHz.

    Media móvil como paso bajo sencillo (contra el aliasing al diezmar) e interpolación
    lineal, conservando el estado entre bloques. Entrega bloques exactos de FRAME_SIZE
    muestras, que es lo que espera Silero VAD.
    """

    def __init__(self, src_rate: int, dst_rate: int = SAMPLE_RATE, frame_size: int = FRAME_SIZE):
        self._step = float(src_rate) / float(dst_rate)  # muestras de entrada por muestra de salida
        taps = int(self._step) if self._step >= 2.0 else 1
        self._kernel = np.full(taps, 1.0 / taps, dtype=np.float32)
        self._fir_hist = np.zeros(taps - 1, dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)
        self._pos = 0.0  # posición de la siguiente muestra de salida, relativa a _tail[0]
        self._pending = np.zeros(0, dtype=np.float32)
        self._frame_size = frame_size

    def process(self, block: np.ndarray) -> list[np.ndarray]:
        block = np.asarray(block, dtype=np.float32)
        if self._kernel.size > 1:
            ext = np.concatenate([self._fir_hist, block])
            filtered = np.convolve(ext, self._kernel, mode="valid").astype(np.float32)
            self._fir_hist = ext[-(self._kernel.size - 1):]
        else:
            filtered = block
        x = np.concatenate([self._tail, filtered])
        n = x.size
        next_pos = self._pos
        if n >= 2 and self._pos <= n - 1:
            count = int((n - 1 - self._pos) // self._step) + 1
            positions = self._pos + self._step * np.arange(count)
            out = np.interp(positions, np.arange(n), x).astype(np.float32)
            self._pending = np.concatenate([self._pending, out])
            next_pos = self._pos + self._step * count
        # Conservar desde la última muestra necesaria para interpolar el siguiente bloque.
        drop = min(int(next_pos), max(n - 1, 0))
        self._tail = x[drop:]
        self._pos = next_pos - drop
        frames = []
        while self._pending.size >= self._frame_size:
            frames.append(self._pending[: self._frame_size].copy())
            self._pending = self._pending[self._frame_size:]
        return frames


def _drain_queue(q: queue.Queue) -> None:
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


def _cuda_runtime_libraries() -> list[str]:
    """Librerías de CUDA que ctranslate2 carga al transcribir en GPU, según su versión.

    ctranslate2 4.x usa CUDA 12; desde la 4.5, cuDNN 9 (antes, cuDNN 8).
    [FIX MUL-1] Desde la 4.6.3 la Conv1d de Whisper es CUDA pura y cuDNN es opcional
    (CHANGELOG de ctranslate2 v4.6.3): exigirlo dejaba en CPU, sin motivo, a quien solo
    tiene cuBLAS (p. ej. pip install nvidia-cublas-cu12 sin nvidia-cudnn-cu12).
    """
    try:
        import ctranslate2
        parts = [int(re.match(r"\d*", p).group() or 0) for p in ctranslate2.__version__.split(".")[:3]]
        major, minor, patch = (parts + [0, 0, 0])[:3]
    except Exception:
        major, minor, patch = 4, 5, 0
    cuda = "12" if major >= 4 else "11"
    if (major, minor, patch) >= (4, 6, 3):
        return [f"cublas64_{cuda}.dll"] if sys.platform == "win32" else [f"libcublas.so.{cuda}"]
    if (major, minor) >= (4, 5):
        if sys.platform == "win32":
            return [f"cublas64_{cuda}.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll"]
        return [f"libcublas.so.{cuda}", "libcudnn.so.9", "libcudnn_ops.so.9", "libcudnn_cnn.so.9"]
    if sys.platform == "win32":
        return [f"cublas64_{cuda}.dll", "cudnn64_8.dll", "cudnn_ops_infer64_8.dll", "cudnn_cnn_infer64_8.dll"]
    return [f"libcublas.so.{cuda}", "libcudnn.so.8", "libcudnn_ops_infer.so.8", "libcudnn_cnn_infer.so.8"]


def _can_load_library(name: str) -> bool:
    """Indica si el sistema puede cargar la librería dinámica `name`."""
    import ctypes
    import ctypes.util

    try:
        ctypes.CDLL(name)
        return True
    except OSError:
        pass
    if sys.platform == "win32":
        # Desde Python 3.8, ctypes no busca en el PATH de Windows, pero ctranslate2.dll sí
        # (ahí suelen estar CUDA y cuDNN): se localiza a mano y se carga por ruta completa.
        path = ctypes.util.find_library(name)
        if path:
            try:
                ctypes.CDLL(path)
                return True
            except OSError:
                pass
    return False


def _missing_cuda_libs() -> list[str]:
    """Librerías de CUDA necesarias para Whisper que no se pueden cargar."""
    return [name for name in _cuda_runtime_libraries() if not _can_load_library(name)]


def _detect_whisper_device() -> tuple[str, str]:
    """[FIX MUL-1] Elige dispositivo y tipo de cómputo para faster-whisper.

    Antes se llamaba a `ctranslate2.get_supported_devices()`, que no existe: en Linux con
    NVIDIA (el torch de PyPI trae CUDA) lanzaba AttributeError y Zara no arrancaba; en
    Windows (torch de PyPI solo CPU) Whisper quedaba siempre en CPU aunque hubiera una RTX.
    Ahora se pregunta a ctranslate2, que es quien ejecuta Whisper, sin depender de torch.
    Además se comprueba que cuBLAS/cuDNN se pueden cargar: si faltan, faster-whisper puede
    abortar el proceso entero al transcribir (no es una excepción capturable).
    """
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() <= 0:
            return "cpu", "int8"
    except Exception as exc:
        log.debug("Sin GPU CUDA para Whisper: %s", exc)
        return "cpu", "int8"
    missing = _missing_cuda_libs()
    if missing:
        log.warning(
            "Hay GPU NVIDIA, pero faltan librerías de CUDA para Whisper (%s): se usa la CPU. "
            "Instale cuBLAS y cuDNN para CUDA 12 para usar la GPU.", ", ".join(missing),
        )
        return "cpu", "int8"
    return "cuda", "float16"

# [FIX] Palabra de activación obligatoria: sin escucha continua filtrada por
# una palabra clave, cualquier ruido ambiente (TV, conversaciones, música)
# que contenga términos como "pausa", "play" o "minimiza" podía disparar
# acciones reales sobre el sistema (minimizar ventanas, pausar/reproducir
# multimedia...) sin que el Señor VK se dirigiera a Zara en absoluto.
_WAKE_WORD_RE = re.compile(r"(?i)\bzara\b")


def contains_wake_word(text: str) -> bool:
    """Indica si la locución transcrita menciona a Zara en algún punto."""
    return bool(text) and bool(_WAKE_WORD_RE.search(text))


HALLUCINATION_PATTERNS = [
    r"(?i)\bsubt[ií]tulos\s+(?:por|realizados|creados)?\s*(?:la\s+comunidad|amara)?\b",
    r"(?i)\bamara\.org\b",
    r"(?i)\bgracias\s+por\s+ver\b",
    r"(?i)\bsuscr[ií]bete\b",
    r"(?i)\bdale\s+(?:a\s+)?like\b",
    r"(?i)\bhasta\s+la\s+pr[oó]xima\b",
    r"(?i)\btodos\s+los\s+derechos\s+reservados\b",
    r"(?i)\btransmisi[oó]n\s+en\s+vivo\b",
    r"(?i)\bthank\s+you\s+for\s+watching\b",
    r"(?i)\bplease\s+subscribe\b",
]


def clean_transcript(text: str) -> str:
    """Elimina alucinaciones comunes de Whisper, repeticiones y artefactos de ruido."""
    if not text:
        return ""
    t = text.strip()
    # Limpieza de artefactos como '...', '---', '***' pero preservando '?' y '!'
    t = re.sub(r"^[.\-_~*#\s]+", "", t)
    t = re.sub(r"[.\-_~*#\s]+$", "", t)
    t = t.strip()
    if len(t) < 2:
        return ""

    for pat in HALLUCINATION_PATTERNS:
        if re.search(pat, t):
            log.info("Alucinación de Whisper suprimida: '%s'", text)
            return ""

    words = t.lower().split()
    if len(words) >= 4 and len(set(words)) == 1:
        log.info("Repetición patológica de Whisper suprimida: '%s'", text)
        return ""

    return t


def normalize_audio(audio: np.ndarray, target_peak: float = 0.90, max_gain: float = 6.0) -> np.ndarray:
    """Control automático de ganancia (AGC) inteligente para voz.
    
    Eleva la voz a un nivel óptimo preservando transitorios sin amplificar
    el piso de ruido de fondo o estática electrónica del micrófono.
    """
    arr = np.asarray(audio, dtype=np.float32)
    if arr.size == 0:
        return arr
    peak = float(np.max(np.abs(arr)))
    if peak > 0.01:
        gain = min(target_peak / peak, max_gain)
        return arr * gain
    return arr


class ContinuousEar:
    """Oído táctico de Zara: captura continua, VAD neuronal y transcripción Whisper."""

    def __init__(
        self,
        barge_in_callback: Optional[Callable[[], None]] = None,
        is_speaking_fn: Optional[Callable[[], bool]] = None,
        rms_callback: Optional[Callable[[float], None]] = None,
        status_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self.barge_in_cb = barge_in_callback
        self.is_speaking_fn = is_speaking_fn or (lambda: False)
        self.rms_cb = rms_callback
        # [FIX AUD-7/V1_-9] Avisos para el panel: status_callback(nivel, mensaje) con nivel
        # "ERROR" (micrófono o STT no disponibles) o "SISTEMA" (micrófono recuperado).
        self.status_cb = status_callback
        self.mic_error = ""  # último problema del micrófono ("" si funciona)
        self.stt_error = ""  # motivo por el que no hay reconocimiento de voz ("" si lo hay)
        self._mic_opened = False
        self._last_pa_refresh: Optional[float] = None  # [FIX AUD-7] ver _refresh_audio_devices
        self._device = "cpu"

        self._muted = threading.Event()
        self._stop_event = threading.Event()
        self._run_lock = threading.Lock()
        self._raw_queue: queue.Queue[np.ndarray] = queue.Queue()
        # La utterance queue almacena arrays de audio raw (float32) sin transcribir
        self._utterance_queue: queue.Queue[np.ndarray] = queue.Queue()

        self._audio_thread: Optional[threading.Thread] = None
        self._vad_thread: Optional[threading.Thread] = None

        # Componentes neuronales (carga perezosa/segura)
        self._vad_model = None
        self._vad_iterator = None
        self._whisper_model = None

        self._init_vad()
        self._init_whisper()

    def _init_vad(self) -> None:
        """Inicializa Silero VAD v5."""
        try:
            from silero_vad import VADIterator, load_silero_vad
            self._vad_model = load_silero_vad()
            self._vad_iterator = VADIterator(
                self._vad_model,
                threshold=VAD_THRESHOLD,
                sampling_rate=SAMPLE_RATE,
                min_silence_duration_ms=VAD_MIN_SILENCE_MS,
                speech_pad_ms=VAD_SPEECH_PAD_MS,
            )
            # [FIX] Instancia de modelo INDEPENDIENTE y dedicada exclusivamente a
            # los chequeos de barge-in mientras Zara habla. Antes se llamaba
            # directamente a `self._vad_model(...)` (el mismo objeto que envuelve
            # `self._vad_iterator`) saltándose el iterador. Silero VAD mantiene
            # estado recurrente (LSTM) entre llamadas: esas invocaciones directas
            # corrompían el estado que el iterador necesita para segmentar
            # correctamente la SIGUIENTE locución del Señor VK, degradando el
            # reconocimiento justo después de cada respuesta de Zara.
            self._vad_model_bargein = load_silero_vad()

            # [FIX] Tamaños de buffer derivados de la configuración, calculados
            # una sola vez. Extraídos a atributos de instancia (en vez de
            # variables locales del bucle) para que la máquina de estados de
            # segmentación/barge-in se pueda testear llamando directamente a
            # `_process_frame()` sin necesidad de hilos, cola ni hardware real.
            self._barge_in_required_frames = max(
                1, int(VAD_BARGE_IN_TRIGGER_MS / (FRAME_SIZE / SAMPLE_RATE * 1000))
            )
            self._pre_roll_capacity = max(
                4, int(VAD_SPEECH_PAD_MS / (FRAME_SIZE / SAMPLE_RATE * 1000))
            )
            self._reset_segmentation_state()

            log.info("Silero VAD v5 cargado (silencio=%d ms, pad=%d ms)",
                     VAD_MIN_SILENCE_MS, VAD_SPEECH_PAD_MS)
        except Exception as exc:
            log.exception("Error al cargar Silero VAD: %s", exc)
            raise

    def _reset_segmentation_state(self) -> None:
        """Reinicia la máquina de estados de segmentación/barge-in a cero."""
        self._barge_in_consecutive_frames = 0
        # Pre-roll ring buffer para capturar consonantes iniciales (ej. s, p, t, k)
        # sin acumular silencio leading antes de que el usuario empiece a hablar.
        self._pre_roll_buffer: collections.deque[np.ndarray] = collections.deque(
            maxlen=self._pre_roll_capacity
        )
        # [FIX] Pre-roll INDEPENDIENTE que se va llenando mientras Zara habla,
        # para no perder el arranque real de una interrupción (ver _process_frame).
        self._bargein_preroll_buffer: collections.deque[np.ndarray] = collections.deque(
            maxlen=self._pre_roll_capacity
        )
        self._speech_frames: list[np.ndarray] = []
        self._is_speech_active = False
        # Seguimiento del flanco de inicio de habla de Zara para aplicar el
        # periodo de gracia anti-eco.
        self._was_speaking = False
        self._speaking_started_at = 0.0

    def _notify_status(self, level: str, message: str) -> None:
        if self.status_cb:
            try:
                self.status_cb(level, message)
            except Exception:
                pass

    def stt_available(self) -> bool:
        """Indica si hay modelo Whisper cargado (si no, ver `stt_error`)."""
        return self._whisper_model is not None

    def _init_whisper(self) -> None:
        """Inicializa faster-whisper en GPU CUDA o con fallback transparente a CPU.

        [FIX MUL-1/V1_-5] Nunca lanza: si CUDA no transcribe de verdad se pasa a CPU; si no
        se puede descargar el modelo (sin red o con un proxy que bloquea Hugging Face) se usa
        cualquier modelo ya descargado; y si no hay ninguno, el oído queda sin STT
        (`stt_available()` es False) en vez de tumbar la app, que sigue sirviendo para el chat
        escrito o Gemini Live.
        """
        self._whisper_model = None
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            self._set_stt_unavailable(
                f"Reconocimiento de voz no disponible: no se pudo cargar faster-whisper ({exc}). "
                "Ejecute pip install -r requirements.txt; mientras tanto puede usar el chat escrito."
            )
            return

        device, compute_type = _detect_whisper_device()
        log.info("Inicializando STT faster-whisper en dispositivo: %s (%s)", device, compute_type)
        self._device = device
        if device == "cuda":
            if self._load_whisper(WhisperModel, "cuda", compute_type):
                return
            log.warning("CUDA no utilizable para Whisper (faltan cuBLAS/cuDNN de CUDA 12 o no hay memoria); usando CPU.")
            self._device = "cpu"
        if self._load_whisper(WhisperModel, "cpu", "int8"):
            return
        if self._load_cached_whisper(WhisperModel):
            return
        self._set_stt_unavailable(
            "Reconocimiento de voz no disponible: no hay ningún modelo Whisper descargado y no se pudo "
            "descargar (¿sin conexión o con un proxy que bloquea huggingface.co?). Conéctese a internet "
            "una vez y reinicie Zara para descargarlo; mientras tanto puede usar el chat escrito."
        )

    def _set_stt_unavailable(self, message: str) -> None:
        self._whisper_model = None
        self.stt_error = message
        log.error(message)
        self._notify_status("ERROR", message)

    def _whisper_candidates(self, device: str) -> list[str]:
        """Modelos a probar en `device`, de mejor a más ligero."""
        chosen = resolve_whisper_model(WHISPER_MODEL_PRIMARY, device)
        if WHISPER_MODEL_PRIMARY not in ("auto", chosen):
            log.warning(
                "ZARA_WHISPER_MODEL=%s es demasiado pesado para %s: usando '%s'. "
                "Para forzarlo, define ZARA_WHISPER_FORCE=1.", WHISPER_MODEL_PRIMARY, device.upper(), chosen,
            )
        names = [chosen, WHISPER_MODEL_FALLBACK]
        if device == "cpu":
            names.append("small")  # el antiguo «último fallback forzado»
        return list(dict.fromkeys(names))

    def _load_whisper(self, whisper_cls, device: str, compute_type: str) -> bool:  # noqa: ANN001
        """Carga el primer candidato que funcione en `device`. Devuelve True si lo consigue."""
        # Hilos de CPU: faster-whisper usa 4 por defecto; aprovechar todos los núcleos
        # reduce la transcripción de forma casi lineal.
        extra = {"cpu_threads": WHISPER_CPU_THREADS} if device == "cpu" else {}
        for cand in self._whisper_candidates(device):
            try:
                log.info("Intentando cargar modelo Whisper: %s", cand)
                model = whisper_cls(
                    cand,
                    device=device,
                    compute_type=compute_type,
                    download_root=str(MODELS_DIR / "whisper"),
                    **extra,
                )
            except Exception as exc:
                log.warning("No se pudo cargar %s en %s (%s): %s", cand, device, compute_type, exc)
                continue
            log.info("Modelo Whisper cargado exitosamente: %s", cand)
            self._whisper_model = model
            if self._warm_up_whisper() or device != "cuda":
                return True
            # [FIX MUL-1] En CUDA el modelo puede cargarse y fallar al transcribir (faltan
            # cuBLAS/cuDNN de CUDA 12, driver antiguo...): así Zara quedaba sorda. Se
            # descarta y se pasa a CPU; otro candidato en CUDA fallaría igual.
            self._whisper_model = None
            return False
        return False

    def _cached_whisper_dirs(self) -> list[Path]:
        """Modelos Whisper completos ya descargados en la caché local, por preferencia."""
        root = MODELS_DIR / "whisper"
        found: list[Path] = []
        try:
            for model_bin in root.glob("models--*/snapshots/*/model.bin"):
                snap = model_bin.parent
                # Sin tokenizer.json, faster-whisper lo pediría a internet.
                if model_bin.is_file() and (snap / "tokenizer.json").is_file():
                    found.append(snap)
        except OSError:
            return []
        prefs = [resolve_whisper_model(WHISPER_MODEL_PRIMARY, "cpu"), WHISPER_MODEL_FALLBACK, "small"]

        def _rank(snap: Path) -> tuple[int, str]:
            repo = snap.parent.parent.name.lower()  # p. ej. models--systran--faster-whisper-small
            for i, name in enumerate(prefs):
                name = name.lower()
                key = name.replace("/", "--") if "/" in name else f"-{name}"
                if repo.endswith(key):
                    return i, repo
            return len(prefs), repo

        return sorted(found, key=_rank)

    def _load_cached_whisper(self, whisper_cls) -> bool:  # noqa: ANN001
        """[FIX V1_-5] Sin red: carga en CPU cualquier modelo que ya esté descargado."""
        for snap in self._cached_whisper_dirs():
            try:
                log.warning("Sin poder descargar el modelo elegido: usando el modelo en caché %s", snap.parent.parent.name)
                self._whisper_model = whisper_cls(
                    str(snap), device="cpu", compute_type="int8", cpu_threads=WHISPER_CPU_THREADS,
                )
            except Exception as exc:
                log.warning("No se pudo cargar el modelo en caché %s: %s", snap, exc)
                continue
            self._device = "cpu"
            self._warm_up_whisper()
            return True
        return False

    def _warm_up_whisper(self) -> bool:
        """Una transcripción de prueba al arrancar: la primera llamada real carga kernels
        y reserva memoria, y sin esto se pagaba en la primera orden del Señor VK.

        [FIX MUL-1] Devuelve False si falla: en CUDA eso indica que la GPU no sirve.
        """
        try:
            t0 = time.monotonic()
            noise = (np.random.default_rng(0).standard_normal(SAMPLE_RATE) * 0.01).astype(np.float32)
            list(self._whisper_model.transcribe(
                noise, language=WHISPER_LANGUAGE, beam_size=1, without_timestamps=True, vad_filter=False,
            )[0])
            log.info("Whisper precalentado en %.2f s.", time.monotonic() - t0)
            return True
        except Exception as exc:
            log.warning("Fallo en la transcripción de prueba de Whisper: %s", exc)
            return False

    def start(self) -> None:
        """Inicia los hilos continuos de captura y detección de voz.

        [FIX] Se puede repetir: main.py vuelve a llamarlo tras stop() al salir de Gemini
        Live. Cada arranque usa un evento de parada NUEVO (un hilo rezagado de la tanda
        anterior sigue viendo el suyo activado y termina, en vez de revivir), vacía las
        colas para no procesar audio ni locuciones viejas y crea hilos nuevos.
        """
        with self._run_lock:
            if self.is_running():
                log.debug("El oído ya estaba activo.")
                return
            for t in (self._audio_thread, self._vad_thread):
                if t is not None and t.is_alive() and t is not threading.current_thread():
                    t.join(timeout=2.0)
            self._stop_event = threading.Event()
            _drain_queue(self._raw_queue)
            _drain_queue(self._utterance_queue)
            stop_event = self._stop_event
            self._audio_thread = threading.Thread(
                target=self._capture_audio_worker, args=(stop_event,), name="zara-ear-capture", daemon=True,
            )
            self._vad_thread = threading.Thread(
                target=self._vad_processing_worker, args=(stop_event,), name="zara-ear-vad", daemon=True,
            )
            self._audio_thread.start()
            self._vad_thread.start()
        log.info("Oído de Zara activo y escuchando...")

    def is_running(self) -> bool:
        """True si la captura está activa (entre start() y stop())."""
        thread = self._audio_thread
        return bool(thread is not None and thread.is_alive() and not self._stop_event.is_set())

    def stop(self) -> None:
        """Detiene de forma segura los hilos de escucha (se puede volver a llamar a start())."""
        with self._run_lock:
            self._stop_event.set()
            for t in (self._audio_thread, self._vad_thread):
                if t is not None and t.is_alive() and t is not threading.current_thread():
                    t.join(timeout=1.0)
        log.info("Oído de Zara detenido.")

    def set_muted(self, muted: bool) -> None:
        """Pausa o reanuda la escucha (el micrófono sigue capturando, pero se ignora)."""
        if muted:
            self._muted.set()
        else:
            self._muted.clear()
        log.info("Escucha de Zara %s.", "pausada" if muted else "reanudada")

    def is_muted(self) -> bool:
        return self._muted.is_set()

    def _capture_audio_worker(self, stop_event: Optional[threading.Event] = None) -> None:
        """Lee bloques continuos de 16 kHz desde sounddevice.

        [FIX AUD-7/V1_-9] Antes, cualquier fallo al abrir el micrófono (no hay, está
        desactivado, PipeWire aún no arrancó, VM sin entrada, no admite 16 kHz) se
        registraba y el hilo moría: Zara quedaba sorda en silencio hasta reiniciar, aunque
        luego se conectara un micrófono. Ahora se reintenta con espera creciente (1 s a
        10 s), se avisa una vez por log y al panel (status_callback) y se reabre también si
        el dispositivo se desconecta en marcha o deja de enviar audio.
        """
        stop_event = stop_event or self._stop_event
        backoff = _MIC_RETRY_MIN_S
        while not stop_event.is_set():
            self._mic_opened = False
            try:
                self._run_input_stream(stop_event)
            except Exception as exc:
                if stop_event.is_set():
                    break
                if self._mic_opened:
                    backoff = _MIC_RETRY_MIN_S  # funcionaba y se ha caído: reintentar enseguida
                self._report_mic_error(exc)
                stop_event.wait(backoff)
                backoff = min(backoff * 2.0, _MIC_RETRY_MAX_S)

    def _report_mic_error(self, exc: BaseException) -> None:
        message = _describe_mic_error(exc)
        if message != self.mic_error:
            log.warning("%s Se reintentará automáticamente.", message)
            self._notify_status("ERROR", message)
        else:
            log.debug("Micrófono sigue sin estar disponible: %s", exc)
        self.mic_error = message

    def _refresh_audio_devices(self, sd) -> None:  # noqa: ANN001
        """PortAudio solo enumera los dispositivos al inicializarse: si Zara arrancó sin
        micrófono, uno conectado después no aparece (sobre todo en Windows) hasta
        reinicializarlo. Solo se hace si no hay ningún micrófono visible y Zara no está
        hablando, porque reinicializar PortAudio corta cualquier reproducción en curso.

        [FIX AUD-7] Como mucho una vez cada _PA_REFRESH_INTERVAL_S: antes se hacía en cada
        reintento (cada 10 s, indefinidamente, en un PC sin dispositivo de grabación), y
        cada reinicialización vuelve a enumerar todos los dispositivos (lento en Windows) y
        puede cortar una voz que empiece justo entonces."""
        try:
            sd.query_devices(kind="input")
            return  # hay micrófono por defecto: no hace falta
        except Exception:
            pass
        now = time.monotonic()
        last = self._last_pa_refresh
        if last is not None and now - last < _PA_REFRESH_INTERVAL_S:
            return
        try:
            if self.is_speaking_fn():
                return
            self._last_pa_refresh = now
            sd.stop()  # cierra el stream de sd.play() antes de terminar PortAudio
            try:
                sd._terminate()
            finally:
                sd._initialize()
            log.debug("PortAudio reinicializado para buscar micrófonos nuevos.")
        except Exception as exc:
            log.debug("No se pudo reinicializar PortAudio: %s", exc)

    def _run_input_stream(self, stop_event: threading.Event) -> None:
        """Abre el micrófono y lo mantiene hasta stop(). Lanza una excepción si no se puede
        abrir, si el stream se detiene o si deja de llegar audio (dispositivo desconectado)."""
        sd = _import_sounddevice()
        if self.mic_error:
            self._refresh_audio_devices(sd)
        rate = _pick_input_samplerate(sd)
        resampler = _StreamResampler(rate) if rate != SAMPLE_RATE else None
        blocksize = FRAME_SIZE if resampler is None else max(1, round(FRAME_SIZE * rate / SAMPLE_RATE))
        last_audio = [time.monotonic()]

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("Estado de captura sounddevice: %s", status)
            if stop_event.is_set():
                return
            last_audio[0] = time.monotonic()
            mono = indata[:, 0].copy()
            if resampler is None:
                self._raw_queue.put(mono)
            else:
                for frame in resampler.process(mono):
                    self._raw_queue.put(frame)

        with sd.InputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            blocksize=blocksize,
            callback=callback,
        ) as stream:
            self._mic_opened = True
            last_audio[0] = time.monotonic()
            if self.mic_error:
                log.info("Micrófono disponible de nuevo (%d Hz).", rate)
                self._notify_status("SISTEMA", "Micrófono disponible de nuevo.")
                self.mic_error = ""
            while not stop_event.is_set():
                stop_event.wait(0.1)
                if stop_event.is_set():
                    break
                if not stream.active:
                    raise RuntimeError("el stream del micrófono se ha detenido")
                if time.monotonic() - last_audio[0] > _MIC_STALL_S:
                    raise RuntimeError("el micrófono dejó de enviar audio (¿desconectado?)")

    def _vad_processing_worker(self, stop_event: Optional[threading.Event] = None) -> None:
        """Procesa frames con Silero VAD continuo, pre-roll ring buffer y Barge-in."""
        stop_event = stop_event or self._stop_event
        self._reset_segmentation_state()
        if self._vad_iterator is not None:
            self._vad_iterator.reset_states()  # tras un stop()/start() se empieza de cero
        last_error = ""

        while not stop_event.is_set():
            try:
                frame = self._raw_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Escucha pausada: DRENAR toda la cola acumulada y aplanar el HUD
            if self._muted.is_set():
                try:
                    while True:
                        self._raw_queue.get_nowait()
                except queue.Empty:
                    pass
                if self.rms_cb:
                    try:
                        self.rms_cb(0.0)
                    except Exception:
                        pass
                self._reset_segmentation_state()
                self._vad_iterator.reset_states()
                continue

            # [FIX V1_-9] Un fallo inesperado en un frame no debe matar el hilo VAD para
            # siempre (Zara quedaría sorda sin aviso): se registra y se sigue.
            try:
                self._process_frame(frame)
            except Exception as exc:
                if str(exc) != last_error:
                    log.exception("Fallo procesando un frame de audio: %s", exc)
                last_error = str(exc)

    def _process_frame(self, frame: np.ndarray) -> None:
        """[FIX, extraído para testabilidad] Procesa UN frame de audio: RMS
        para el HUD, barge-in (modelo dedicado + gate de RMS + periodo de
        gracia anti-eco) y segmentación de la locución del Señor VK mediante
        Silero VAD continuo. Factorizado fuera del bucle de `_vad_processing_worker`
        para poder testear la máquina de estados directamente, sin hilos,
        colas ni hardware de audio real.
        """
        # Nivel RMS para la animación visual del HUD
        rms = float(np.sqrt(np.mean(frame ** 2))) if frame.size else 0.0
        if self.rms_cb:
            try:
                self.rms_cb(rms)
            except Exception:
                pass

        # Rellenar con ceros si el bloque es menor que FRAME_SIZE
        if frame.shape[0] < FRAME_SIZE:
            frame = np.pad(frame, (0, FRAME_SIZE - frame.shape[0]))
        tensor_chunk = torch.from_numpy(frame).unsqueeze(0)  # [1, 512]

        # 1. Detección de Barge-in mientras Zara habla
        if self.is_speaking_fn():
            if not self._was_speaking:
                self._speaking_started_at = time.monotonic()
                # [FIX AUDITORÍA] Reiniciar el estado recurrente del modelo
                # dedicado al barge-in al EMPEZAR cada sesión de habla de
                # Zara (no entre frames de la misma sesión, donde el estado
                # acumulado es intencional). Evita que un sesgo residual de
                # la sesión anterior adelante o retrase la detección de la
                # siguiente interrupción.
                self._vad_model_bargein.reset_states()
            self._was_speaking = True

            # [FIX] Mantener un pre-roll de los frames más recientes MIENTRAS
            # Zara habla, para no perder el arranque real de una interrupción
            # genuina (antes se descartaba cada frame con speech_frames.clear()
            # mientras hablaba, así que al confirmarse el barge-in se perdían
            # las primeras palabras de lo que el Señor VK empezó a decir).
            self._bargein_preroll_buffer.append(frame.copy())

            # [FIX] Usar el modelo VAD dedicado al barge-in (no el que usa el
            # iterador de segmentación) para no corromper su estado interno.
            with torch.no_grad():
                speech_prob = self._vad_model_bargein(tensor_chunk, SAMPLE_RATE).item()

            in_grace_period = (
                time.monotonic() - self._speaking_started_at
            ) * 1000.0 < VAD_BARGE_IN_GRACE_MS
            bargein_confirmed = False

            # [FIX] Exigir también un nivel RMS mínimo y respetar el periodo
            # de gracia tras iniciar la frase. Sin esto, el eco del propio
            # altavoz de Zara filtrándose al micrófono (sin auriculares)
            # puede alcanzar el umbral de VAD y cortarla a mitad de frase
            # sin que el Señor VK haya dicho nada.
            if (
                speech_prob >= VAD_THRESHOLD
                and rms >= VAD_BARGE_IN_MIN_RMS
                and not in_grace_period
            ):
                self._barge_in_consecutive_frames += 1
                if self._barge_in_consecutive_frames >= self._barge_in_required_frames:
                    log.info("¡Barge-in detectado! El Señor VK está hablando. Interrumpiendo voz.")
                    if self.barge_in_cb:
                        self.barge_in_cb()
                    self._barge_in_consecutive_frames = 0
                    bargein_confirmed = True
            else:
                self._barge_in_consecutive_frames = max(0, self._barge_in_consecutive_frames - 1)

            if bargein_confirmed:
                # [FIX] Sembrar la locución real con el pre-roll acumulado
                # durante el habla de Zara, para no perder el arranque de la
                # interrupción. A partir de aquí la segmentación normal
                # (bloque 2, en la siguiente llamada) sigue acumulando sobre
                # esta base hasta detectar el "end" real.
                self._speech_frames = list(self._bargein_preroll_buffer)
                self._is_speech_active = True
                self._pre_roll_buffer.clear()
                self._bargein_preroll_buffer.clear()
                # [FIX AUD-14] El VADIterator no recibió los frames de la interrupción
                # (mientras Zara habla se retorna antes del bloque 2), así que seguía con
                # triggered=False y solo emite "end" si estaba triggered: tras un «¡basta!»
                # corto, la locución acumulaba silencio hasta el límite de 25 s. Se marca
                # como dentro de una locución para que cierre tras VAD_MIN_SILENCE_MS.
                self._vad_iterator.reset_states()
                self._vad_iterator.triggered = True
            else:
                self._speech_frames.clear()
                self._pre_roll_buffer.clear()
                self._is_speech_active = False
                # [FIX AUD-14] Si el VADIterator quedó dentro de una locución (barge-in
                # confirmado y Zara vuelve a hablar antes del "end"), hay que reiniciarlo:
                # con triggered=True no emite "start" y la siguiente orden se perdería.
                if getattr(self._vad_iterator, "triggered", False):
                    self._vad_iterator.reset_states()
            return
        else:
            self._was_speaking = False
            self._barge_in_consecutive_frames = 0
            self._bargein_preroll_buffer.clear()

        # 2. Segmentación de locución mediante Silero VAD continuo
        tensor_1d = tensor_chunk.squeeze(0)
        vad_res = self._vad_iterator(tensor_1d)

        if not self._is_speech_active:
            self._pre_roll_buffer.append(frame.copy())
            if vad_res is not None and "start" in vad_res:
                self._is_speech_active = True
                # [FIX AUDITORÍA] `frame` ya quedó como último elemento de
                # `_pre_roll_buffer` en el append de arriba; volver a
                # añadirlo aquí duplicaba los primeros 32 ms de cada locución.
                self._speech_frames = list(self._pre_roll_buffer)
        else:
            self._speech_frames.append(frame.copy())
            if vad_res is not None and "end" in vad_res:
                utterance = np.concatenate(self._speech_frames)
                self._speech_frames = []
                self._pre_roll_buffer.clear()
                self._is_speech_active = False
                self._vad_iterator.reset_states()

                # Validación de locución: duración mínima y nivel de energía real
                if len(utterance) >= int(SAMPLE_RATE * 0.35):
                    u_peak = float(np.max(np.abs(utterance)))
                    u_rms = float(np.sqrt(np.mean(utterance ** 2)))
                    # Filtro anti-micrófono cerrado: evita encolar silencio electrónico
                    if u_peak >= 0.015 and u_rms >= 0.003:
                        self._utterance_queue.put(utterance)
                    else:
                        log.debug("Locución ignorada por energía nula (peak=%.4f, rms=%.4f)", u_peak, u_rms)

        # Límite de seguridad: locución no mayor a 25 segundos continuos
        if len(self._speech_frames) > int(25 * (SAMPLE_RATE / FRAME_SIZE)):
            if self._is_speech_active:
                utterance = np.concatenate(self._speech_frames)
                u_peak = float(np.max(np.abs(utterance)))
                if u_peak >= 0.015:
                    self._utterance_queue.put(utterance)
            self._speech_frames = []
            self._pre_roll_buffer.clear()
            self._is_speech_active = False
            self._vad_iterator.reset_states()

    def transcribe_array(self, audio: np.ndarray) -> str:
        """Transcribe un array float32 16 kHz con faster-whisper optimizado."""
        if audio.size == 0:
            return ""
        if self._whisper_model is None:
            # [FIX V1_-5] Sin modelo Whisper (sin red en el primer arranque): sin texto.
            log.debug("Locución descartada: reconocimiento de voz no disponible.")
            return ""

        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio ** 2)))
        # Si el audio es silencio electrónico o micrófono cerrado, descartar directamente
        if peak < 0.015 or rms < 0.003:
            return ""

        norm_audio = normalize_audio(audio)
        t0 = time.monotonic()
        beam = resolve_beam_size(WHISPER_BEAM_SIZE, getattr(self, "_device", "cpu"))
        try:
            segments, _info = self._whisper_model.transcribe(
                norm_audio,
                language=WHISPER_LANGUAGE,
                beam_size=beam,
                best_of=beam,
                temperature=0.0,
                vad_filter=False,  # Silero VAD ya segmentó con precisión milimétrica
                no_speech_threshold=0.6,
                initial_prompt=WHISPER_INITIAL_PROMPT,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            raw_text = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as exc:
            log.warning("Fallo en la transcripción Whisper: %s", exc)
            return ""
        elapsed = time.monotonic() - t0
        audio_s = audio.size / SAMPLE_RATE
        # Diagnóstico de latencia: si x_tiempo_real > 1, el modelo es demasiado grande para este equipo.
        log.info("[LATENCIA] STT %.2f s para %.1f s de audio (x%.2f tiempo real)", elapsed, audio_s, elapsed / max(audio_s, 0.01))

        return clean_transcript(raw_text)

    def listen_utterances(self) -> Generator[str, None, None]:
        """Generador que produce frases transcritas en tiempo real.

        [BUG-4 FIX] La transcripción ocurre en este generador (llamado desde
        el hilo worker de main.py), no bloqueando el hilo de VAD.

        Termina al llamar a stop(); tras un nuevo start() se puede volver a iterar
        (cada generador se queda con el evento de parada del arranque en curso).
        """
        stop_event = self._stop_event
        while not stop_event.is_set():
            try:
                utt = self._utterance_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            text = self.transcribe_array(utt)
            if text:
                yield text
