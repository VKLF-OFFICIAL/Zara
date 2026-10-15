from pathlib import Path
import pytest
from zara.guardian.action import Action, Level
from zara.guardian.rules import RuleSet


@pytest.fixture
def rs(tmp_path):
    espacio = tmp_path / "ZARA" / "espacio"
    espacio.mkdir(parents=True)
    # usa los defaults internos (sin TOML) para fijar el comportamiento fail-safe
    return RuleSet.from_toml(tmp_path / "noexiste.toml",
                             zara_root=tmp_path / "ZARA", espacio=espacio), espacio


def test_blocked_wins(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="sudo dd if=/dev/zero of=/dev/sda"))
    assert lvl is Level.BLOCKED


def test_sudo_is_critical(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="sudo dnf update"))
    assert lvl is Level.CRITICAL  # sudo (critical) gana a dnf (confirm)


def test_write_inside_espacio_is_safe(rs):
    r, espacio = rs
    lvl, _, _ = r.classify(Action(kind="file_write", description="x",
                                  paths=(espacio / "nota.txt",)))
    assert lvl is Level.SAFE


def test_write_outside_root_is_at_least_confirm(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="file_write", description="x",
                                  paths=(Path("/home/vklf/Documentos/x.txt"),)))
    assert lvl >= Level.CONFIRM


def test_symlink_escaping_espacio_not_safe(rs, tmp_path):
    r, espacio = rs
    outside = tmp_path / "fuera"
    outside.mkdir()
    link = espacio / "escape"
    link.symlink_to(outside)
    lvl, _, _ = r.classify(Action(kind="file_write", description="x",
                                  paths=(link / "x.txt",)))
    assert lvl >= Level.CONFIRM


def test_unknown_command_defaults_to_confirm(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="algo-raro --flag"))
    assert lvl is Level.CONFIRM


def test_pip_install_is_confirm(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="pip install requests"))
    assert lvl is Level.CONFIRM


def test_corrupt_toml_falls_back_to_safe_defaults(tmp_path):
    espacio = tmp_path / "ZARA" / "espacio"
    espacio.mkdir(parents=True)
    corrupt = tmp_path / "guardian.toml"
    corrupt.write_text("blocked = = =\n")  # TOML inválido
    r = RuleSet.from_toml(corrupt, zara_root=tmp_path / "ZARA", espacio=espacio)
    # Debe usar los defaults seguros, no abrirse ni romper: sudo sigue siendo crítico
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="sudo rm -rf /etc"))
    assert lvl is Level.BLOCKED
    lvl2, _, _ = r.classify(Action(kind="shell", description="x",
                                   command="algo-desconocido"))
    assert lvl2 is Level.CONFIRM
