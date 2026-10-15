from pathlib import Path
import pytest
from zara.voice.speaker import Voice
from zara.errors import VoiceError


class FakeSynth:
    def __init__(self, name, ok=True):
        self.name = name
        self.ok = ok
        self.calls = []

    def available(self):
        return True

    def synthesize(self, text, out_path):
        self.calls.append(text)
        if not self.ok:
            raise RuntimeError(f"{self.name} falló")
        Path(out_path).write_bytes(b"RIFFfake")
        return Path(out_path)


def test_uses_first_working_synth(tmp_path):
    a = FakeSynth("edge")
    b = FakeSynth("espeak")
    v = Voice([a, b], out_dir=tmp_path)
    out = v.say("Hola Señor VK")
    assert out.exists()
    assert a.calls == ["Hola Señor VK"]
    assert b.calls == []  # no se usa el respaldo si el primero funciona


def test_falls_back_when_first_fails(tmp_path):
    a = FakeSynth("edge", ok=False)
    b = FakeSynth("espeak", ok=True)
    v = Voice([a, b], out_dir=tmp_path)
    out = v.say("Buenas noches")
    assert out.exists()
    assert a.calls == ["Buenas noches"]
    assert b.calls == ["Buenas noches"]


def test_raises_when_all_fail(tmp_path):
    a = FakeSynth("edge", ok=False)
    b = FakeSynth("espeak", ok=False)
    v = Voice([a, b], out_dir=tmp_path)
    with pytest.raises(VoiceError):
        v.say("prueba")


def test_empty_text_raises(tmp_path):
    v = Voice([FakeSynth("edge")], out_dir=tmp_path)
    with pytest.raises(ValueError):
        v.say("   ")


def test_piper_synth_available_needs_model(tmp_path):
    from zara.voice.synth import PiperSynth
    assert PiperSynth(str(tmp_path / "no.onnx")).available() is False
