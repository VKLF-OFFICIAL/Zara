from datetime import datetime
from zara.memory.store import MemoryStore


def _store(tmp_path, clock=None):
    base = tmp_path / "memoria"
    base.mkdir()
    (base / "perfil.md").write_text("# Perfil\nEl Señor VK usa Fedora KDE.\n", encoding="utf-8")
    (base / "preferencias.md").write_text("# Preferencias\n", encoding="utf-8")
    (base / "proyectos").mkdir()
    (base / "proyectos" / "bugbounty.md").write_text(
        "# Bug Bounty\nTarget actual: acme.com\n", encoding="utf-8")
    (base / "diario").mkdir()
    return MemoryStore(base, clock=clock or (lambda: datetime(2026, 10, 14)))


def test_load_relevant_always_includes_profile_and_prefs(tmp_path):
    s = _store(tmp_path)
    ctx = s.load_relevant("hola")
    assert "El Señor VK usa Fedora KDE" in ctx


def test_load_relevant_includes_matching_project(tmp_path):
    s = _store(tmp_path)
    ctx = s.load_relevant("sigamos con el bugbounty de hoy")
    assert "acme.com" in ctx


def test_load_relevant_excludes_unrelated_project(tmp_path):
    s = _store(tmp_path)
    ctx = s.load_relevant("¿qué hora es?")
    assert "acme.com" not in ctx


def test_remember_appends_to_prefs(tmp_path):
    s = _store(tmp_path)
    s.remember("Prefiere respuestas cortas")
    prefs = (tmp_path / "memoria" / "preferencias.md").read_text(encoding="utf-8")
    assert "Prefiere respuestas cortas" in prefs


def test_append_diary_writes_dated_file(tmp_path):
    s = _store(tmp_path)
    s.append_diary("Arreglado el fondo de pantalla")
    f = tmp_path / "memoria" / "diario" / "2026-10-14.md"
    assert "Arreglado el fondo de pantalla" in f.read_text(encoding="utf-8")

