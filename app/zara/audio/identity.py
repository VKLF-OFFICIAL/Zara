from __future__ import annotations
import os
from pathlib import Path
import numpy as np

# Umbral por defecto de similitud para aceptar al Señor VK.
DEFAULT_THRESHOLD = 0.75


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def decide(score: float, threshold: float = DEFAULT_THRESHOLD) -> bool:
    return score >= threshold


class SpeakerVerifier:
    """Verifica que quien habla es el Señor VK comparando con su huella de voz.

    La huella (embedding medio) se guarda en el SSD con permisos 600.
    """

    def __init__(self, ref_path: Path, threshold: float = DEFAULT_THRESHOLD):
        self.ref_path = Path(ref_path)
        self.threshold = threshold
        self._encoder = None
        self._ref = None

    def _enc(self):
        if self._encoder is None:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder(verbose=False)
        return self._encoder

    def _embed(self, audio16k: np.ndarray) -> np.ndarray:
        from resemblyzer import preprocess_wav
        wav = preprocess_wav(np.asarray(audio16k, dtype=np.float32), source_sr=16000)
        return self._enc().embed_utterance(wav)

    def is_enrolled(self) -> bool:
        return self.ref_path.exists()

    def enroll(self, samples: list[np.ndarray]) -> np.ndarray:
        embs = [self._embed(s) for s in samples]
        ref = np.mean(embs, axis=0)
        self.ref_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.ref_path, ref)
        try:
            os.chmod(self.ref_path, 0o600)
        except OSError:
            pass  # el SSD es ext4; en otros FS puede no aplicar
        self._ref = ref
        return ref

    def _load_ref(self) -> np.ndarray:
        if self._ref is None:
            self._ref = np.load(self.ref_path)
        return self._ref

    def verify(self, audio16k: np.ndarray, threshold: float | None = None) -> tuple[bool, float]:
        if not self.is_enrolled():
            raise FileNotFoundError("No hay huella de voz; haga la inscripción primero")
        dur = len(audio16k) / 16000
        score = cosine(self._embed(audio16k), self._load_ref())
        target_thr = threshold if threshold is not None else self.threshold
        # En locuciones breves (< 1.2 s), el embedding tiene varianza más alta;
        # adaptamos el umbral gradualmente para no rechazar falsamente al usuario.
        if dur < 1.2:
            target_thr = max(0.55, target_thr - (1.2 - dur) * 0.15)
        return decide(score, target_thr), score

