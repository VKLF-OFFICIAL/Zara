import pytest
from zara.config import load_toml
from zara.errors import ConfigError

DEFAULTS = {"umbral_voz": 0.65, "esquina": "arriba-derecha"}


def test_missing_file_returns_defaults(tmp_path):
    out = load_toml(tmp_path / "no.toml", DEFAULTS)
    assert out == DEFAULTS
    out["umbral_voz"] = 0.9
    assert DEFAULTS["umbral_voz"] == 0.65  # no muta los defaults


def test_file_overrides_defaults(tmp_path):
    f = tmp_path / "z.toml"
    f.write_text('umbral_voz = 0.8\n')
    out = load_toml(f, DEFAULTS)
    assert out["umbral_voz"] == 0.8
    assert out["esquina"] == "arriba-derecha"


def test_invalid_toml_raises(tmp_path):
    f = tmp_path / "bad.toml"
    f.write_text('umbral_voz = = =\n')
    with pytest.raises(ConfigError):
        load_toml(f, DEFAULTS)
