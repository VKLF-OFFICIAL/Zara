"""Subsistema de Audio y Oído (ZARA v2.0).

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
"""

from __future__ import annotations
import collections
import logging
import queue
import re
import threading
import time
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
    WHISPER_INITIAL_PROMPT,
    WHISPER_LANGUAGE,
    WHISPER_MODEL_FALLBACK,
    WHISPER_MODEL_PRIMARY,
)

log = logging.getLogger("zara.ear")

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
    ):
        self.barge_in_cb = barge_in_callback
        self.is_speaking_fn = is_speaking_fn or (lambda: False)
        self.rms_cb = rms_callback

        self._muted = threading.Event()
        self._stop_event = threading.Event()
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

    def _init_whisper(self) -> None:
        """Inicializa faster-whisper en GPU CUDA o con fallback transparente a CPU."""
        from faster_whisper import WhisperModel
        import ctranslate2

        device = "cpu"
        compute_type = "int8"
        if torch.cuda.is_available() and "cuda" in ctranslate2.get_supported_devices():
            device = "cuda"
            compute_type = "float16"

        log.info("Inicializando STT faster-whisper en dispositivo: %s (%s)", device, compute_type)
        model_candidates = [WHISPER_MODEL_PRIMARY, WHISPER_MODEL_FALLBACK]

        for cand in model_candidates:
            try:
                log.info("Intentando cargar modelo Whisper: %s", cand)
                self._whisper_model = WhisperModel(
                    cand,
                    device=device,
                    compute_type=compute_type,
                    download_root=str(MODELS_DIR / "whisper"),
                )
                log.info("Modelo Whisper cargado exitosamente: %s", cand)
                return
            except Exception as exc:
                log.warning("No se pudo cargar %s en %s (%s): %s", cand, device, compute_type, exc)

        # Último fallback forzado en CPU int8
        log.warning("Activando fallback forzado en CPU con modelo 'small'")
        self._whisper_model = WhisperModel(
            "small",
            device="cpu",
            compute_type="int8",
            download_root=str(MODELS_DIR / "whisper"),
        )

    def start(self) -> None:
        """Inicia los hilos continuos de captura y detección de voz."""
        self._stop_event.clear()
        self._audio_thread = threading.Thread(target=self._capture_audio_worker, daemon=True)
        self._vad_thread = threading.Thread(target=self._vad_processing_worker, daemon=True)

        self._audio_thread.start()
        self._vad_thread.start()
        log.info("Oído de Zara activo y escuchando...")

    def stop(self) -> None:
        """Detiene de forma segura los hilos de escucha."""
        self._stop_event.set()
        if self._audio_thread and self._audio_thread.is_alive():
            self._audio_thread.join(timeout=1.0)
        if self._vad_thread and self._vad_thread.is_alive():
            self._vad_thread.join(timeout=1.0)
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

    def _capture_audio_worker(self) -> None:
        """Lee bloques continuos de 16 kHz desde sounddevice."""
        import sounddevice as sd

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("Estado de captura sounddevice: %s", status)
            if not self._stop_event.is_set():
                self._raw_queue.put(indata[:, 0].copy())

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=FRAME_SIZE,
                callback=callback,
            ):
                while not self._stop_event.is_set():
                    time.sleep(0.05)
        except Exception as exc:
            log.exception("Fallo en la captura de audio continua: %s", exc)

    def _vad_processing_worker(self) -> None:
        """Procesa frames con Silero VAD continuo, pre-roll ring buffer y Barge-in."""
        self._reset_segmentation_state()

        while not self._stop_event.is_set():
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

            self._process_frame(frame)

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
            else:
                self._speech_frames.clear()
                self._pre_roll_buffer.clear()
                self._is_speech_active = False
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

        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio ** 2)))
        # Si el audio es silencio electrónico o micrófono cerrado, descartar directamente
        if peak < 0.015 or rms < 0.003:
            return ""

        norm_audio = normalize_audio(audio)
        try:
            segments, _info = self._whisper_model.transcribe(
                norm_audio,
                language=WHISPER_LANGUAGE,
                beam_size=WHISPER_BEAM_SIZE,
                best_of=WHISPER_BEAM_SIZE,
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

        return clean_transcript(raw_text)

    def listen_utterances(self) -> Generator[str, None, None]:
        """Generador que produce frases transcritas en tiempo real.

        [BUG-4 FIX] La transcripción ocurre en este generador (llamado desde
        el hilo worker de main.py), no bloqueando el hilo de VAD.
        """
        while not self._stop_event.is_set():
            try:
                utt = self._utterance_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            text = self.transcribe_array(utt)
            if text:
                yield text
