from unittest.mock import MagicMock, patch
from zara.backend import Backend
from zara.paths import ZaraPaths


def _make_backend(tmp_path):
    paths = ZaraPaths.resolve(root=tmp_path / "zara", ssd=tmp_path / "ssd")
    b = Backend(paths)
    b.confirm.enabled = False
    b._build_core_and_guardian()  # necesitamos el Guardián real
    return b


def test_execute_run_shell_through_guardian(tmp_path):
    b = _make_backend(tmp_path)
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(stdout="hola", stderr="", returncode=0)
        res = b._execute_tool("run_shell", {"command": "echo hola"})
    assert res["success"] is True
    assert res["stdout"] == "hola"


def test_execute_run_shell_blocks_catastrophic(tmp_path):
    b = _make_backend(tmp_path)
    res = b._execute_tool("run_shell", {"command": "mkfs.ext4 /dev/sda"})
    assert res["success"] is False
    assert res.get("blocked") is True
    assert res["exit_code"] == 126


def test_execute_file_manager_write_and_read(tmp_path):
    b = _make_backend(tmp_path)
    target = b.paths.espacio / "nota.txt"
    w = b._execute_tool("file_manager", {"action": "write", "path": str(target),
                                         "content": "datos"})
    assert w["success"]
    r = b._execute_tool("file_manager", {"action": "read", "path": str(target)})
    assert r["content"] == "datos"


def test_execute_unknown_tool(tmp_path):
    b = _make_backend(tmp_path)
    res = b._execute_tool("no_existe", {})
    assert res["success"] is False
    assert res["exit_code"] == 127
