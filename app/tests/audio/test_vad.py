import numpy as np
from zara.audio.vad import EnergyVad


def _silence(n=160):
    return np.zeros(n, dtype=np.float32)


def _speech(n=160, amp=0.3):
    return (np.random.randn(n).astype(np.float32)) * amp


def test_emits_utterance_after_speech_then_silence():
    vad = EnergyVad(threshold=0.02, min_speech_frames=3, min_silence_frames=5)
    out = None
    # ruido de fondo
    for _ in range(3):
        assert vad.push(_silence()) is None
    # habla
    for _ in range(6):
        assert vad.push(_speech()) is None
    # silencio hasta cerrar
    results = [vad.push(_silence()) for _ in range(6)]
    emitted = [r for r in results if r is not None]
    assert len(emitted) == 1
    assert emitted[0].shape[0] >= 6 * 160  # contiene el habla


def test_ignores_too_short_speech():
    vad = EnergyVad(threshold=0.02, min_speech_frames=5, min_silence_frames=4)
    vad.push(_speech())  # 1 frame, por debajo de min_speech
    results = [vad.push(_silence()) for _ in range(6)]
    assert all(r is None for r in results)


def test_resets_after_emit():
    vad = EnergyVad(threshold=0.02, min_speech_frames=2, min_silence_frames=3)
    for _ in range(4):
        vad.push(_speech())
    for _ in range(4):
        vad.push(_silence())
    # segunda locución
    for _ in range(4):
        vad.push(_speech())
    out = None
    for _ in range(4):
        r = vad.push(_silence())
        if r is not None:
            out = r
    assert out is not None


def test_caps_very_long_speech():
    # Con música continua el VAD no puede acumular para siempre.
    vad = EnergyVad(threshold=0.02, min_speech_frames=2, min_silence_frames=5,
                    max_speech_frames=10)
    outs = [vad.push(_speech()) for _ in range(12)]
    emitted = [o for o in outs if o is not None]
    assert len(emitted) == 1
    assert emitted[0].shape[0] == 10 * 160
