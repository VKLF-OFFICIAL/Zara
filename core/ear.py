"""Subsistema de Audio y Oído (ZARA v2.0).

Captura continua a 16 kHz mono con sounddevice, detección neuronal de voz
con Silero VAD v5 (cero cortes en pausas de hasta 700 ms), barge-in a los 250 ms
y transcripción con faster-whisper (GPU/CPU fallback e inyección fonética).
"""

from __future__ import annotations
import logging
import queue
import threading
import time
from typing import Callable, Generator, Optional
import numpy as np
import torch

from config import (
    FRAME_SIZE,
    MODELS_DIR,
    SAMPLE_RATE,
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


def normalize_audio(audio: np.ndarray, target_peak: float = 0.85, max_gain: float = 20.0) -> np.ndarray:
    """Control automático de ganancia (AGC) para evitar pérdida fonética."""
    arr = np.asarray(audio, dtype=np.float32)
    if arr.size == 0:
        return arr
    peak = float(np.max(np.abs(arr)))
    if peak > 0.002:
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

        self._stop_event = threading.Event()
        self._raw_queue: queue.Queue[np.ndarray] = queue.Queue()
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
            log.info("Silero VAD v5 cargado (silencio=%d ms, pad=%d ms)",
                     VAD_MIN_SILENCE_MS, VAD_SPEECH_PAD_MS)
        except Exception as exc:
            log.exception("Error al cargar Silero VAD: %s", exc)
            raise

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
        """Procesa frames con Silero VAD, gestiona Barge-in y segmenta locuciones."""
        barge_in_consecutive_frames = 0
        barge_in_required_frames = max(1, int(VAD_BARGE_IN_TRIGGER_MS / (FRAME_SIZE / SAMPLE_RATE * 1000)))

        buffer: list[np.ndarray] = []
        is_speech_active = False

        while not self._stop_event.is_set():
            try:
                frame = self._raw_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Nivel RMS para la animación visual del HUD
            rms = float(np.sqrt(np.mean(frame**2))) if frame.size else 0.0
            if self.rms_cb:
                try:
                    self.rms_cb(rms)
                except Exception:
                    pass

            tensor_chunk = torch.from_numpy(frame)
            if tensor_chunk.shape[0] != FRAME_SIZE:
                continue

            # 1. Detección de Barge-in mientras Zara habla
            if self.is_speaking_fn():
                with torch.no_grad():
                    speech_prob = self._vad_model(tensor_chunk, SAMPLE_RATE).item()
                if speech_prob >= VAD_THRESHOLD:
                    barge_in_consecutive_frames += 1
                    if barge_in_consecutive_frames >= barge_in_required_frames:
                        log.info("¡Barge-in detectado! El Señor VK está hablando. Interrumpiendo voz.")
                        if self.barge_in_cb:
                            self.barge_in_cb()
                        barge_in_consecutive_frames = 0
                else:
                    barge_in_consecutive_frames = max(0, barge_in_consecutive_frames - 1)
                # Durante habla propia se resetea el acumulador de orden para no oírse a sí misma
                self._vad_iterator.reset_states()
                buffer.clear()
                is_speech_active = False
                continue
            else:
                barge_in_consecutive_frames = 0

            # 2. Segmentación de locución del usuario mediante VADIterator
            vad_res = self._vad_iterator(tensor_chunk)
            buffer.append(frame)

            if vad_res is not None:
                if "start" in vad_res:
                    is_speech_active = True
                elif "end" in vad_res and is_speech_active:
                    # Fin de la frase con padding
                    utterance = np.concatenate(buffer)
                    buffer.clear()
                    is_speech_active = False
                    self._vad_iterator.reset_states()
                    if len(utterance) >= int(SAMPLE_RATE * 0.3):  # Mínimo 300 ms de audio
                        self._utterance_queue.put(utterance)

            # Límite de seguridad: locución no mayor a 25 segundos continuos
            if len(buffer) > int(25 * (SAMPLE_RATE / FRAME_SIZE)):
                if is_speech_active:
                    utterance = np.concatenate(buffer)
                    self._utterance_queue.put(utterance)
                buffer.clear()
                is_speech_active = False
                self._vad_iterator.reset_states()

    def transcribe_array(self, audio: np.ndarray) -> str:
        """Transcribe un array float32 16 kHz con faster-whisper optimizado."""
        if audio.size == 0:
            return ""

        norm_audio = normalize_audio(audio)
        segments, _info = self._whisper_model.transcribe(
            norm_audio,
            language=WHISPER_LANGUAGE,
            beam_size=WHISPER_BEAM_SIZE,
            best_of=WHISPER_BEAM_SIZE,
            vad_filter=True,
            initial_prompt=WHISPER_INITIAL_PROMPT,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text

    def listen_utterances(self) -> Generator[str, None, None]:
        """Generador que produce frases transcritas en tiempo real."""
        while not self._stop_event.is_set():
            try:
                utt = self._utterance_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            text = self.transcribe_array(utt)
            if text:
                yield text
