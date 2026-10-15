import numpy as np
import pytest
from zara.audio.oww import WakeWordDetector


def test_unknown_model_not_available(tmp_path):
    det = WakeWordDetector(model=str(tmp_path / "no-existe.onnx"))
    assert det.available() is False
    # score nunca lanza, aunque no haya modelo
    assert det.score(np.zeros(16000, dtype=np.float32)) == 0.0
    assert det.detect(np.zeros(16000, dtype=np.float32)) is False


def test_short_audio_scores_zero():
    det = WakeWordDetector()
    assert det.score(np.zeros(100, dtype=np.float32)) == 0.0


def test_silence_scores_low_when_model_present():
    det = WakeWordDetector()
    if not det.available():
        pytest.skip("openWakeWord no instalado")
    assert det.score(np.zeros(16000, dtype=np.float32)) < det.threshold
