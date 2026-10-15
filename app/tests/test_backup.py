import pytest
from datetime import datetime
from zara.backup import backup_path
from zara.errors import BackupError


def test_backup_file(tmp_path):
    src = tmp_path / "dato.txt"
    src.write_text("hola")
    copias = tmp_path / "copias"
    clock = lambda: datetime(2026, 10, 14, 9, 30, 15)
    dest = backup_path(src, copias, clock=clock)
    assert dest == copias / "2026-10-14_093015" / "dato.txt"
    assert dest.read_text() == "hola"


def test_backup_dir(tmp_path):
    src = tmp_path / "carpeta"
    (src).mkdir()
    (src / "a.txt").write_text("a")
    copias = tmp_path / "copias"
    dest = backup_path(src, copias, clock=lambda: datetime(2026, 1, 1, 0, 0, 0))
    assert (dest / "a.txt").read_text() == "a"


def test_backup_missing_source_raises(tmp_path):
    with pytest.raises(BackupError):
        backup_path(tmp_path / "noexiste", tmp_path / "copias")
