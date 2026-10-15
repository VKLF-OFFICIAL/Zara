from datetime import datetime
from pathlib import Path
import pytest
from zara.guardian.action import Action, Level
from zara.guardian.rules import RuleSet
from zara.guardian.engine import Guardian
from zara.audit import AuditLog
from zara.errors import BackupError


@pytest.fixture
def guardian(tmp_path):
    espacio = tmp_path / "ZARA" / "espacio"
    espacio.mkdir(parents=True)
    rules = RuleSet.from_toml(tmp_path / "no.toml",
                              zara_root=tmp_path / "ZARA", espacio=espacio)
    audit = AuditLog(tmp_path / "reg", clock=lambda: datetime(2026, 10, 14))
    g = Guardian(rules, audit, copias_dir=tmp_path / "copias",
                 clock=lambda: datetime(2026, 10, 14, 1, 2, 3))
    return g, espacio, tmp_path


def test_classify_critical_sets_backup(guardian):
    g, _, _ = guardian
    d = g.classify(Action(kind="shell", description="x", command="sudo rm -rf /tmp/z"))
    assert d.level is Level.CRITICAL
    assert d.needs_backup is True
    assert d.prepared is False


def test_prepare_blocked_raises(guardian):
    g, _, _ = guardian
    d = g.classify(Action(kind="shell", description="x",
                          command="dd if=/dev/zero of=/dev/sda"))
    assert d.is_blocked
    with pytest.raises(PermissionError):
        g.prepare(d)


def test_prepare_makes_backup_for_critical(guardian):
    g, _, tmp = guardian
    victim = tmp / "Documentos"
    victim.mkdir()
    (victim / "importante.txt").write_text("datos")
    d = g.classify(Action(kind="file_delete", description="borrar docs",
                          command="sudo rm -rf " + str(victim),
                          paths=(victim,)))
    assert d.level is Level.CRITICAL and d.needs_backup
    d2 = g.prepare(d)
    assert d2.prepared is True
    assert d2.backup_at is not None
    assert (d2.backup_at / "importante.txt").read_text() == "datos"


def test_prepare_backup_failure_leaves_unprepared(guardian):
    g, _, tmp = guardian
    missing = tmp / "no-existe"
    d = g.classify(Action(kind="file_delete", description="x",
                          command="sudo rm -rf " + str(missing),
                          paths=(missing,)))
    assert d.needs_backup
    with pytest.raises(BackupError):
        g.prepare(d)
    assert d.prepared is False


def test_classify_is_audited(guardian):
    g, _, tmp = guardian
    g.classify(Action(kind="shell", description="x", command="ls"))
    reg = (tmp / "reg" / "acciones-2026-10-14.jsonl").read_text()
    assert '"event": "classify"' in reg
