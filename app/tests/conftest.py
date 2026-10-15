from pathlib import Path
import pytest


@pytest.fixture
def fake_env(tmp_path):
    """Crea un root y un ssd falsos con el centinela de montaje presente."""
    root = tmp_path / "almacen" / "ZARA"
    ssd = tmp_path / "ssd"
    sentinel = tmp_path / "almacen" / ".zara_mounted"
    root.parent.mkdir(parents=True)
    sentinel.write_text("ok")
    ssd.mkdir()
    return {"root": root, "ssd": ssd, "sentinel": sentinel}
