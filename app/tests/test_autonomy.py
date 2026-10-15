from unittest.mock import MagicMock, patch
from zara.backend import Backend
from zara.paths import ZaraPaths


def _make_backend(tmp_path):
    zara_root = tmp_path / "zara"
    ssd = tmp_path / "ssd"
    paths = ZaraPaths.resolve(root=zara_root, ssd=ssd)
    b = Backend(paths)
    b._pedir_confirmacion = False
    b.confirm.enabled = False
    b.confirm.ask = MagicMock(return_value=False)
    return b


def test_autonomy_close_app_does_not_ask(tmp_path):
    b = _make_backend(tmp_path)
    with patch("zara.actions.find_app_processes", return_value=[(1234, "notepad.exe")]), \
         patch("zara.actions.terminate", return_value=1):
        res = b._close_app("notepad")
        assert "cerrado notepad.exe" in res["reply"]
        b.confirm.ask.assert_not_called()


def test_autonomy_power_does_not_ask(tmp_path):
    b = _make_backend(tmp_path)
    with patch("subprocess.Popen") as mock_popen:
        res = b._power("poweroff")
        assert "Voy a apagar el equipo" in res["reply"]
        b.confirm.ask.assert_not_called()
        mock_popen.assert_called_once()


def test_autonomy_guarded_run_does_not_ask(tmp_path):
    b = _make_backend(tmp_path)
    b._guardian = MagicMock()
    decision = MagicMock()
    decision.is_blocked = False
    decision.requires_confirmation = True
    decision.needs_backup = False
    b._guardian.classify.return_value = decision

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        status, res = b._run_guarded_raw("pip install cowsay")
        assert status == "ok"
        b.confirm.ask.assert_not_called()
