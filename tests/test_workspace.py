"""Carpetas del usuario, resolución de rutas y herramientas de archivos tipo editor."""

import sys
from pathlib import Path

import pytest

import core.workspace as ws
from core.tools import file_manager, run_shell


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """HOME aislado con user-dirs.dirs en español, como un Linux configurado en es_ES."""
    home = tmp_path / "vk"
    (home / ".config").mkdir(parents=True)
    (home / "Documentos").mkdir()
    (home / "Escritorio").mkdir()
    (home / ".config" / "user-dirs.dirs").write_text(
        'XDG_DOCUMENTS_DIR="$HOME/Documentos"\nXDG_DESKTOP_DIR="$HOME/Escritorio"\nXDG_DOWNLOAD_DIR="$HOME"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("ZARA_WORKSPACE", raising=False)
    monkeypatch.setattr(ws.Path, "home", classmethod(lambda cls: home))
    ws.user_folders.cache_clear()
    yield home
    ws.user_folders.cache_clear()


@pytest.mark.skipif(sys.platform == "win32", reason="XDG es de Linux")
def test_spanish_aliases_follow_xdg(fake_home):
    folders = ws.user_folders()
    assert folders["documentos"] == fake_home / "Documentos"
    assert folders["escritorio"] == fake_home / "Escritorio"
    # XDG apunta al HOME cuando la carpeta no existe: se usa el nombre por defecto
    assert folders["descargas"] != fake_home
    assert ws.resolve_user_path("Documentos/mi-web") == (fake_home / "Documentos" / "mi-web").resolve()
    assert ws.resolve_user_path("documents/x") == (fake_home / "Documentos" / "x").resolve()
    assert ws.resolve_user_path("Escritorio") == (fake_home / "Escritorio").resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="rutas POSIX")
def test_relative_paths_use_workspace_not_cwd(fake_home, monkeypatch):
    assert ws.workspace_dir() == fake_home / "Documentos"
    assert ws.resolve_user_path("proyecto/a.txt") == (fake_home / "Documentos" / "proyecto" / "a.txt").resolve()
    assert ws.resolve_user_path("~/notas.txt") == (fake_home / "notas.txt").resolve()
    monkeypatch.setenv("ZARA_WORKSPACE", "Escritorio")
    assert ws.workspace_dir() == (fake_home / "Escritorio").resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="rutas POSIX")
def test_workspace_falls_back_to_home_when_documents_missing(fake_home):
    (fake_home / "Documentos").rmdir()
    assert ws.workspace_dir() == fake_home


@pytest.mark.skipif(sys.platform == "win32", reason="rutas POSIX")
def test_build_a_small_project_file_by_file(fake_home):
    """Lo que haría Zara al «crear una web en Documentos»: carpeta, archivos, árbol, edición."""
    assert file_manager("mkdir", "Documentos/mi-web/css")["success"]
    assert file_manager("write", "Documentos/mi-web/index.html", content="<h1>Hola</h1>\n<p>Hola</p>\n")["success"]
    assert file_manager("write", "Documentos/mi-web/css/app.css", content="body { color: red; }\n")["success"]

    tree = file_manager("tree", "Documentos/mi-web")
    assert "css/" in tree["tree"] and "index.html" in tree["tree"] and "app.css" in tree["tree"]

    # Fragmento repetido: no se edita a ciegas
    ambiguous = file_manager("edit", "Documentos/mi-web/index.html", old_str="Hola", new_str="Adiós")
    assert ambiguous["success"] is False and "2 veces" in ambiguous["error"]
    unique = file_manager("edit", "Documentos/mi-web/index.html", old_str="<h1>Hola</h1>", new_str="<h1>Bienvenido</h1>")
    assert unique["success"] is True
    all_of_them = file_manager("edit", "Documentos/mi-web/css/app.css", old_str="red", new_str="blue", replace_all=True)
    assert all_of_them["success"] is True

    found = file_manager("search", "Documentos/mi-web", pattern="Bienvenido")
    assert found["count"] == 1 and "index.html:1" in found["matches"][0]

    page = file_manager("read", "Documentos/mi-web/index.html", start_line=2, max_lines=1)
    assert page["total_lines"] == 2 and "<p>Hola</p>" in page["content"] and "<h1>" not in page["content"]


def test_tree_skips_heavy_dependency_folders(tmp_path):
    (tmp_path / "vendor" / "laravel").mkdir(parents=True)
    (tmp_path / "vendor" / "laravel" / "x.php").write_text("x", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "User.php").write_text("x", encoding="utf-8")
    tree = file_manager("tree", str(tmp_path))["tree"]
    assert "vendor/ (omitida)" in tree and "x.php" not in tree and "User.php" in tree


def test_read_accepts_numbers_as_text_from_llm(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("\n".join(f"línea {i}" for i in range(1, 11)), encoding="utf-8")
    res = file_manager("read", str(f), start_line="3", max_lines="2")
    assert res["success"] and "línea 3" in res["content"] and "línea 5" not in res["content"]
    assert res["next_start_line"] == 5


@pytest.mark.skipif(sys.platform == "win32", reason="usa bash")
def test_run_shell_runs_in_requested_folder(tmp_path):
    (tmp_path / "proyecto").mkdir()
    res = run_shell("pwd", cwd=str(tmp_path / "proyecto"))
    assert res["success"] and res["stdout"].endswith("proyecto")
    missing = run_shell("pwd", cwd=str(tmp_path / "no-existe"))
    assert missing["success"] is False and "no existe" in missing["stderr"]
    # timeout como texto (lo mandan así algunos modelos) no rompe la llamada
    assert run_shell("echo ok", timeout="5", cwd=str(tmp_path))["success"]


def test_mkdir_is_blocked_in_protected_paths():
    res = file_manager("mkdir", "/etc/zara_prueba")
    assert res["success"] is False and res.get("blocked") is True
