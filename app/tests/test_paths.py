import pytest
from zara.paths import ZaraPaths
from zara.errors import ZaraNotMountedError


def test_resolve_creates_subdirs(fake_env):
    p = ZaraPaths.resolve(root=fake_env["root"], ssd=fake_env["ssd"],
                          sentinel=fake_env["sentinel"])
    for d in (p.config, p.memoria, p.espacio, p.tareas, p.registros,
              p.copias, p.voz):
        assert d.is_dir()
    assert p.ssd_voz.is_dir()


def test_resolve_raises_when_not_mounted(fake_env):
    fake_env["sentinel"].unlink()
    with pytest.raises(ZaraNotMountedError):
        ZaraPaths.resolve(root=fake_env["root"], ssd=fake_env["ssd"],
                          sentinel=fake_env["sentinel"])
    # Nunca debe crear el root si no hay montaje
    assert not fake_env["root"].exists()


def test_resolve_cross_platform_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ZARA_ROOT", str(tmp_path / "ZaraTest"))
    p = ZaraPaths.resolve()
    assert p.root == tmp_path / "ZaraTest"
    assert (p.root / "config").is_dir()

