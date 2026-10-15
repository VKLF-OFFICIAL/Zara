from __future__ import annotations
from collections import deque
import numpy as np


class EnergyVad:
    """Detector de voz por energía (RMS). Agrupa frames en locuciones.

    push(frame) devuelve None mientras acumula, o el array de la locución
    completa cuando detecta suficiente silencio tras suficiente habla, o
    cuando la locución alcanza la duración máxima (p. ej. música continua).
    Conserva un búfer circular de pre-roll para no cortar la consonante inicial.
    """

    def __init__(self, threshold: float = 0.015, min_speech_frames: int = 8,
                 min_silence_frames: int = 25, max_speech_frames: int = 750,
                 preroll_frames: int = 15):
        # Con frames de 20 ms: 8 = 160 ms de habla mínima, 25 = 0,5 s de pausa
        # para cerrar la frase, 750 = 15 s de locución máxima, 15 = 300 ms de pre-roll.
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

