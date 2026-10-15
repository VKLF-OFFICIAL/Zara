from zara.voice import synth


def _which(available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


def test_prefers_pw_play_with_target(monkeypatch):
    monkeypatch.setattr(synth.shutil, "which", _which({"pw-play", "paplay", "ffplay"}))
    assert synth.player_cmd("/x/a.wav", "cascos") == ["pw-play", "--target", "cascos", "/x/a.wav"]


def test_pw_play_without_sink_uses_default(monkeypatch):
    monkeypatch.setattr(synth.shutil, "which", _which({"pw-play"}))
    assert synth.player_cmd("/x/a.wav") == ["pw-play", "/x/a.wav"]


def test_falls_back_to_paplay(monkeypatch):
    monkeypatch.setattr(synth.shutil, "which", _which({"paplay", "ffplay"}))
    assert synth.player_cmd("/x/a.wav", "cascos") == ["paplay", "--device=cascos", "/x/a.wav"]


def test_never_uses_ffplay_when_pipewire_tools_exist(monkeypatch):
    # ffplay (SDL) entrega silencio con PipeWire en este equipo.
    monkeypatch.setattr(synth.shutil, "which", _which({"pw-play", "ffplay"}))
    assert synth.player_cmd("/x/a.wav")[0] != "ffplay"
