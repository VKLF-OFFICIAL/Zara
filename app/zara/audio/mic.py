from __future__ import annotations
import queue
import numpy as np
from .vad import EnergyVad

SAMPLE_RATE = 16000
FRAME = 320  # 20 ms a 16 kHz (EnergyVad); Silero usa 512


class Microphone:
    """Captura del micrófono a 16 kHz mono y segmentación por voz (VAD).

    Si el VAD soporta barge-in (Silero), se le pasa un `on_barge_in` que se
    dispara cuando el Señor VK habla mientras Zara responde: así se aborta la
    voz al instante (~250 ms) en lugar de ignorar el audio mientras suena.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, frame: int = FRAME,
                 device=None, vad=None, on_barge_in=None):
        import threading
        self.sample_rate = sample_rate
        self.vad = vad or EnergyVad()
        # El tamaño de frame lo fija el VAD (Silero exige 512).
        self.frame = getattr(self.vad, "frame_size", frame)
        self.device = device
        self.on_barge_in = on_barge_in
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
                    # Zara está hablando: no transcribimos, pero vigilamos barge-in.
                    if self.on_barge_in is not None and hasattr(self.vad, "is_barge_in"):
                        try:
                            if self.vad.is_barge_in(frame):
                                self.on_barge_in()
                        except Exception:
                            pass
                    else:
                        self.vad.reset()
                    continue
                utt = self.vad.push(frame)
                if utt is not None:
                    yield utt
