from __future__ import annotations
import queue
import numpy as np
from .vad import EnergyVad

SAMPLE_RATE = 16000
FRAME = 320  # 20 ms a 16 kHz


class Microphone:
    """Captura del micrófono a 16 kHz mono y segmentación por voz (VAD)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, frame: int = FRAME,
                 device=None, vad: EnergyVad | None = None):
        import threading
        self.sample_rate = sample_rate
        self.frame = frame
        self.device = device
        self.vad = vad or EnergyVad()
        # Mientras está activo se descarta el audio (Zara hablando).
        self.muted = threading.Event()

    def record_seconds(self, seconds: float) -> np.ndarray:
        import sounddevice as sd
        n = int(self.sample_rate * seconds)
        audio = sd.rec(n, samplerate=self.sample_rate, channels=1,
                       dtype="float32", device=self.device)
        sd.wait()
        return audio.reshape(-1)

    def utterances(self, stop_event=None):
        """Generador infinito que produce arrays de locución según el VAD.

        stop_event: objeto con is_set() para terminar el bucle.
        """
        import sounddevice as sd
        q: queue.Queue = queue.Queue()

        def cb(indata, frames, time_info, status):  # noqa: ANN001
            q.put(indata[:, 0].copy())

        with sd.InputStream(samplerate=self.sample_rate, channels=1,
                            dtype="float32", blocksize=self.frame,
                            device=self.device, callback=cb):
            while stop_event is None or not stop_event.is_set():
                try:
                    frame = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if self.muted.is_set():
                    self.vad.reset()
                    continue
                utt = self.vad.push(frame)
                if utt is not None:
                    yield utt
