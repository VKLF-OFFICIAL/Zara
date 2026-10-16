from __future__ import annotations
from collections import deque
import logging
import numpy as np

log = logging.getLogger("zara.vad")


class SileroVad:
    """Detector de voz NEURONAL (Silero VAD v5) — sustituye al VAD por energía.

    Mucho más robusto frente al ruido y al acento del sur de España (no corta en
    pausas naturales ni por aspirar las «s» finales). Segmenta locuciones a partir
    del flujo de frames de 16 kHz, con silencio de cierre configurable (~500-700 ms)
    y margen de relleno (~350 ms) para no amputar la consonante inicial ni final.

    Además detecta BARGE-IN: si el Señor VK empieza a hablar mientras Zara responde,
    `is_barge_in(frame)` dispara en cuanto acumula ~250 ms de voz para abortar la voz.

    Degrada con elegancia: si `silero-vad`/`torch` no están instalados, `available()`
    devuelve False y el backend recurre a `EnergyVad`.
    """

    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000,
                 min_silence_ms: int = 600, speech_pad_ms: int = 350,
                 barge_in_ms: int = 250, frame_size: int = 512,
                 max_speech_s: float = 25.0, min_speech_ms: int = 300):
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.min_silence_ms = min_silence_ms
        self.speech_pad_ms = speech_pad_ms
        self.barge_in_ms = barge_in_ms
        self.frame_size = frame_size
        self.max_speech_s = max_speech_s
        self.min_speech_ms = min_speech_ms
        self._model = None
        self._iterator = None
        self._buf: list[np.ndarray] = []
        self._active = False
        self._barge_frames = 0
        frame_ms = frame_size / sample_rate * 1000
        self._barge_needed = max(1, int(barge_in_ms / frame_ms))

    # --- carga perezosa del modelo ----------------------------------------
    def available(self) -> bool:
        try:
            import torch  # noqa: F401
            import silero_vad  # noqa: F401
            return True
        except Exception:
            return False

    def _ensure(self):
        if self._iterator is None:
            from silero_vad import VADIterator, load_silero_vad
            self._model = load_silero_vad()
            self._iterator = VADIterator(
                self._model, threshold=self.threshold,
                sampling_rate=self.sample_rate,
                min_silence_duration_ms=self.min_silence_ms,
                speech_pad_ms=self.speech_pad_ms)
            log.info("Silero VAD v5 activo (silencio=%d ms, pad=%d ms, barge-in=%d ms)",
                     self.min_silence_ms, self.speech_pad_ms, self.barge_in_ms)
        return self._iterator

    def reset(self) -> None:
        self._buf.clear()
        self._active = False
        self._barge_frames = 0
        if self._iterator is not None:
            try:
                self._iterator.reset_states()
            except Exception:
                pass

    def _speech_prob(self, frame: np.ndarray) -> float:
        import torch
        self._ensure()
        tensor = torch.from_numpy(np.asarray(frame, dtype=np.float32))
        if tensor.shape[0] != self.frame_size:
            return 0.0
        with torch.no_grad():
            return float(self._model(tensor, self.sample_rate).item())

    def is_barge_in(self, frame: np.ndarray) -> bool:
        """True cuando se acumulan ~`barge_in_ms` de voz continua (interrupción)."""
        try:
            prob = self._speech_prob(frame)
        except Exception:
            return False
        if prob >= self.threshold:
            self._barge_frames += 1
        else:
            self._barge_frames = max(0, self._barge_frames - 1)
        if self._barge_frames >= self._barge_needed:
            self._barge_frames = 0
            return True
        return False

    def push(self, frame: np.ndarray):
        """Alimenta un frame; devuelve el array de la locución al cerrarse, o None."""
        iterator = self._ensure()
        import torch
        frame = np.asarray(frame, dtype=np.float32)
        if frame.shape[0] != self.frame_size:
            return None
        self._buf.append(frame)
        res = iterator(torch.from_numpy(frame))
        if res is not None:
            if "start" in res:
                self._active = True
            elif "end" in res and self._active:
                utterance = np.concatenate(self._buf) if self._buf else None
                self.reset()
                if utterance is not None and len(utterance) >= int(
                        self.sample_rate * self.min_speech_ms / 1000):
                    return utterance
                return None
        # Tope de seguridad ante habla/música continua.
        if len(self._buf) > int(self.max_speech_s * self.sample_rate / self.frame_size):
            utterance = np.concatenate(self._buf) if self._active else None
            self.reset()
            return utterance
        return None


class EnergyVad:
    """Detector de voz por energía (RMS). Agrupa frames en locuciones.

    push(frame) devuelve None mientras acumula, o el array de la locución
    completa cuando detecta suficiente silencio tras suficiente habla, o
    cuando la locución alcanza la duración máxima (p. ej. música continua).
    Conserva un búfer circular de pre-roll para no cortar la consonante inicial.
    """

    def __init__(self, threshold: float = 0.015, min_speech_frames: int = 6,
                 min_silence_frames: int = 28, max_speech_frames: int = 750,
                 preroll_frames: int = 15):
        # Con frames de 20 ms: 6 = 120 ms de habla mínima, 28 = 0,56 s de pausa
        # para cerrar la frase sin cortar pausas naturales, 750 = 15 s máximo, 15 = 300 ms pre-roll.
        self.threshold = threshold
        self.min_speech = min_speech_frames
        self.min_silence = min_silence_frames
        self.max_frames = max_speech_frames
        self._preroll: deque[np.ndarray] = deque(maxlen=preroll_frames)
        self._buf: list[np.ndarray] = []
        self._speech_frames = 0
        self._silence_run = 0
        self._in_speech = False

    @staticmethod
    def _rms(frame: np.ndarray) -> float:
        if frame.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))

    def reset(self) -> None:
        self._buf.clear()
        self._preroll.clear()
        self._speech_frames = 0
        self._silence_run = 0
        self._in_speech = False

    def _emit(self):
        if len(self._buf) > self.max_frames:
            self._buf = self._buf[:self.max_frames]
        utterance = np.concatenate(self._buf) if self._buf else None
        enough = self._speech_frames >= self.min_speech
        self.reset()
        return utterance if enough else None

    def push(self, frame: np.ndarray):
        loud = self._rms(frame) >= self.threshold
        if loud:
            if not self._in_speech:
                self._in_speech = True
                self._buf.extend(self._preroll)
                self._preroll.clear()
            self._speech_frames += 1
            self._silence_run = 0
            self._buf.append(frame)
            if len(self._buf) >= self.max_frames:
                return self._emit()
            return None

        # frame silencioso
        if self._in_speech:
            self._silence_run += 1
            self._buf.append(frame)
            if self._silence_run >= self.min_silence or len(self._buf) >= self.max_frames:
                return self._emit()
            return None

        # Reposo: alimentar búfer circular de pre-roll
        self._preroll.append(frame)
        return None

