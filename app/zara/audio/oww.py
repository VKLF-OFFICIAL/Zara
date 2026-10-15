"""Detector de wake word neural (openWakeWord).

A diferencia del reconocimiento por texto (transcribir toda la locución y
casar «Zara» fonéticamente), este es un modelo pequeño entrenado para la
frase de activación, robusto frente a música y ruido de fondo —el mayor
fallo del método por texto—. Usa el modelo preentrenado «hey jarvis».

Trabaja sobre el array de la locución ya segmentada por el VAD (no necesita
flujo continuo): recorre la locución en ventanas de 80 ms y toma la máxima
puntuación.
"""
from __future__ import annotations
import os
import warnings
import numpy as np

# openWakeWord crea sesiones onnxruntime que piden CUDA; aquí solo hay CPU.
warnings.filterwarnings("ignore", message=".*CUDAExecutionProvider.*")

_FRAME = 1280  # 80 ms a 16 kHz, el tamaño de ventana que espera openWakeWord


class WakeWordDetector:
    name = "openwakeword"

    def __init__(self, model: str = "hey_jarvis_v0.1", threshold: float = 0.5):
        self.model_name = model
        self.threshold = threshold
        self._model = None

    def _model_path(self) -> str | None:
        try:
            import openwakeword
        except Exception:
            return None
        name = self.model_name
        if not name.endswith(".onnx"):
            name += ".onnx"
        if os.path.isabs(name):
            return name if os.path.exists(name) else None
        base = os.path.join(os.path.dirname(openwakeword.__file__),
                            "resources", "models")
        p = os.path.join(base, name)
        return p if os.path.exists(p) else None

    def available(self) -> bool:
        return self._model_path() is not None

    def _ensure(self):
        if self._model is None:
            from openwakeword.model import Model
            path = self._model_path()
            if path is None:
                raise RuntimeError("modelo de wake word no encontrado")
            self._model = Model(wakeword_model_paths=[path])
        return self._model

    def score(self, audio) -> float:
        """Máxima puntuación de activación (0..1) en toda la locución."""
        try:
            model = self._ensure()
        except Exception:
            return 0.0
        pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
        if len(pcm) < _FRAME:
            return 0.0
        model.reset()
        best = 0.0
        for i in range(0, len(pcm) - _FRAME + 1, _FRAME):
            r = model.predict(pcm[i:i + _FRAME])
            if r:
                best = max(best, float(max(r.values())))
        return best

    def detect(self, audio) -> bool:
        return self.score(audio) >= self.threshold
