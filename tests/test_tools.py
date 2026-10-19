"""Pruebas unitarias para core/tools.py (ZARA v2.0)."""

import os
import sys
import tempfile
from pathlib import Path
import pytest

from core.tools import (
    execute_tool,
    file_manager,
    format_tools_for_local_prompt,
    is_command_forbidden,
    os_control,
    run_shell,
    web_search,
    _normalize_windows_command,
)


def test_is_command_forbidden():
    assert is_command_forbidden("rm -rf /") is not None
    assert is_command_forbidden("mkfs.ext4 /dev/nvme0n1") is not None
    assert is_command_forbidden("dd if=/dev/urandom of=/dev/sda") is not None
    assert is_command_forbidden("Format-Volume -DriveLetter D") is not None
    assert is_command_forbidden("echo 'hola mundo'") is None
    assert is_command_forbidden("Get-Service") is None


def test_run_shell_echo():
    if sys.platform == "win32":
        cmd = "Write-Output 'ZARA_TEST_OK'"
    else:
        cmd = "echo 'ZARA_TEST_OK'"
    res = run_shell(cmd)
    assert res["success"] is True
    assert "ZARA_TEST_OK" in res["stdout"]
    assert res["exit_code"] == 0
    assert not res.get("blocked")


def test_run_shell_forbidden_blocks():
    res = run_shell("rm -rf / --no-preserve-root")
    assert res["success"] is False
    assert res["blocked"] is True
    assert "ACCESO DENEGADO POR GUARDARRAÍL" in res["stderr"]


def test_file_manager_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "zara_test.txt"

        # 1. Write
        w_res = file_manager("write", str(test_file), content="Línea 1: Zara operativa\nLínea 2: Modelo activo")
        assert w_res["success"] is True
        assert test_file.is_file()

        # 2. Exists
        e_res = file_manager("exists", str(test_file))
        assert e_res["success"] is True
        assert e_res["exists"] is True

        # 3. Read
        r_res = file_manager("read", str(test_file))
        assert r_res["success"] is True
        assert "Zara operativa" in r_res["content"]
        assert r_res["lines"] == 2

        # 4. Append
        a_res = file_manager("append", str(test_file), content="\nLínea 3: Test completado")
        assert a_res["success"] is True

        # 5. Edit (surgical replacement)
        ed_res = file_manager("edit", str(test_file), old_str="Modelo activo", new_str="Modelo Qwen 2.5")
        assert ed_res["success"] is True

        # Verify replacement
        r2 = file_manager("read", str(test_file))
        assert "Modelo Qwen 2.5" in r2["content"]
        assert "Modelo activo" not in r2["content"]

        # 6. List
        l_res = file_manager("list", tmpdir)
        assert l_res["success"] is True
        assert any(item["name"] == "zara_test.txt" for item in l_res["items"])

        # 7. Delete
        d_res = file_manager("delete", str(test_file))
        assert d_res["success"] is True
        assert not test_file.exists()


def test_web_search_live_query():
    # Búsqueda real de prueba
    res = web_search("python programming", max_results=3)
    assert res["success"] is True
    assert res["results_count"] > 0
    assert len(res["results"]) <= 3
    assert "title" in res["results"][0]
    assert "snippet" in res["results"][0]


def test_web_search_empty_query():
    res = web_search("   ")
    assert res["success"] is False
    assert "vacía" in res["error"]


def test_os_control_clipboard():
    test_val = "ZARA_TACTICAL_CLIPBOARD_TEST_2026"
    w_clip = os_control("write_clipboard", value=test_val)
    if w_clip["success"]:
        r_clip = os_control("read_clipboard")
        assert r_clip["success"] is True
        assert test_val in r_clip["clipboard"]


def test_os_control_system_telemetry():
    res = os_control("system_stats")
    assert res["success"] is True
    assert "telemetry" in res
    tel = res["telemetry"]
    assert "cpu_percent" in tel
    assert "ram_percent" in tel
    assert "disk_free_gb" in tel
    assert "uptime_hours" in tel
    assert tel["ram_free_mb"] > 0
    assert "Telemetría táctica" in res["message"]


def test_os_control_network_info():
    res = os_control("network_info")
    assert res["success"] is True
    assert "network" in res
    net = res["network"]
    assert "hostname" in net
    assert "local_ip" in net
    assert len(net["local_ip"].split(".")) == 4  # IPv4 válido


def test_os_control_media_and_desktop_commands():
    # Play/Pause
    res_media = os_control("media_play_pause")
    assert res_media["success"] is True

    # Next track
    res_next = os_control("media_next")
    assert res_next["success"] is True

    # Show desktop
    res_desk = os_control("show_desktop")
    assert res_desk["success"] is True


def test_action_alias_map_resolves_known_enum_values():
    """[Regresión, hallazgo en vivo contra Ollama real] El modelo a veces
    llama directamente a un VALOR del enum "action" de una herramienta
    genérica (p. ej. "open_app", parte de os_control) como si fuera el
    nombre de la propia herramienta. El mapa de alias debe reconocerlos."""
    from core.tools import _get_action_alias_map

    alias_map = _get_action_alias_map()
    assert alias_map["open_app"] == ("os_control", "action")
    assert alias_map["list"] == ("file_manager", "action")


def test_execute_tool_remaps_hallucinated_action_as_name(monkeypatch):
    """[Regresión, hallazgo en vivo] {"name": "open_app", "arguments": {...}}
    debe remapearse a os_control(action="open_app", ...) en vez de
    rechazarse como 'herramienta desconocida' (lo que antes hacía que Zara
    leyera el JSON crudo en voz alta sin ejecutar nada)."""
    calls = []

    def fake_os_control(action, target="", value=""):
        calls.append((action, target, value))
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("core.tools.os_control", fake_os_control)

    res = execute_tool("open_app", {"target": "Bloc de notas"})

    assert res["success"] is True
    assert calls == [("open_app", "Bloc de notas", "")]


@pytest.mark.skipif(sys.platform != "win32", reason="Resolución de apps específica de Windows")
def test_open_app_spanish_alias_resolves_via_which(monkeypatch):
    """[Regresión, hallazgo en vivo] 'Bloc de notas' (nombre en español que
    usa el Señor VK) debe resolverse al ejecutable real (notepad) en vez de
    caer directamente al fallback sin verificar."""
    import core.tools as tools_module

    popen_calls = []
    monkeypatch.setattr(tools_module.subprocess, "Popen", lambda args, **kw: popen_calls.append(args))
    monkeypatch.setattr(
        tools_module.shutil, "which",
        lambda name: "C:\\Windows\\System32\\notepad.exe" if name.lower() == "notepad" else None,
    )

    res = os_control("open_app", target="Bloc de notas")

    assert res["success"] is True
    assert popen_calls == [["C:\\Windows\\System32\\notepad.exe"]]


@pytest.mark.skipif(sys.platform != "win32", reason="Resolución de apps específica de Windows")
def test_open_app_reports_real_failure_when_nothing_resolves(monkeypatch):
    """[Regresión, hallazgo en vivo] Antes el último fallback (Start-Process
    de PowerShell en un subprocess separado, fire-and-forget) reportaba
    éxito SIEMPRE sin comprobar si algo realmente se abrió. Ahora debe
    reportar un fallo real cuando no se encuentra nada que ejecutar."""
    import core.tools as tools_module

    monkeypatch.setattr(tools_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(tools_module.Path, "is_dir", lambda self: False)

    def fake_startfile(path):
        raise OSError("[WinError 2] no encontrado (doble de prueba)")

    monkeypatch.setattr(tools_module.os, "startfile", fake_startfile)

    res = os_control("open_app", target="aplicacion_inexistente_zzz")

    assert res["success"] is False
    assert "No se encontró ninguna aplicación" in res["error"]


@pytest.mark.skipif(sys.platform != "win32", reason="Resolución de apps específica de Windows")
def test_open_app_startfile_fallback_reports_honest_success(monkeypatch):
    """El último recurso (os.startfile) debe reportar éxito solo cuando
    realmente no lanza excepción, usando el alias en español resuelto."""
    import core.tools as tools_module

    monkeypatch.setattr(tools_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(tools_module.Path, "is_dir", lambda self: False)
    startfile_calls = []
    monkeypatch.setattr(tools_module.os, "startfile", lambda path: startfile_calls.append(path))

    res = os_control("open_app", target="configuracion")

    assert res["success"] is True
    assert startfile_calls == ["ms-settings:"]


def test_execute_tool_dispatch():
    # Comprobar que execute_tool enruta a run_shell, web_search y os_control telemetry
    if sys.platform == "win32":
        res_sh = execute_tool("run_shell", {"command": "Write-Output 42"})
    else:
        res_sh = execute_tool("run_shell", {"command": "echo 42"})
    assert res_sh["success"] is True
    assert "42" in res_sh["stdout"]

    # Telemetría vía execute_tool
    res_tel = execute_tool("os_control", {"action": "system_stats"})
    assert res_tel["success"] is True
    assert "cpu_percent" in res_tel["telemetry"]

    res_unknown = execute_tool("non_existent_tool", {})
    assert res_unknown["success"] is False
    assert "desconocida" in res_unknown["error"]


def test_normalize_windows_command(monkeypatch):
    import core.tools as _tools
    monkeypatch.setattr(_tools.shutil, "which", lambda name: None)
    assert _normalize_windows_command("ls -la") == "Get-ChildItem -Force"
    assert _normalize_windows_command("ls -lh /path") == "Get-ChildItem -Force /path"
    assert _normalize_windows_command("which python") == "where.exe python"
    assert _normalize_windows_command("ifconfig") == "ipconfig"
    assert _normalize_windows_command("clear") == "Clear-Host"
    assert "Win32_OperatingSystem" in _normalize_windows_command("uname -a")
    # Unix grep to PowerShell Select-String
    converted_grep = _normalize_windows_command("Get-Content log.txt | grep error")
    assert "Select-String error" in converted_grep


def test_format_tools_for_local_prompt():
    formatted = format_tools_for_local_prompt()
    assert "HERRAMIENTAS DEL SISTEMA DISPONIBLES:" in formatted
    assert "run_shell" in formatted
    assert "file_manager" in formatted
    assert "os_control" in formatted
    assert "web_search" in formatted
    assert "open_app" in formatted  # Enum values represented




@pytest.mark.parametrize("cmd", [
    "rm -rf ~", "rm -rf $HOME/", "shutdown -h now", "reboot",
    "echo x > /etc/fstab", "find / -name x -delete", "dd if=/dev/zero of=/dev/sda",
])
def test_guardrail_blocks_catastrophic(cmd):
    assert is_command_forbidden(cmd)


@pytest.mark.parametrize("cmd", ["ls -la", "rm -rf /tmp/x", "rm -rf ~/proyecto/build", "echo hola"])
def test_guardrail_allows_normal(cmd):
    assert not is_command_forbidden(cmd)


def test_file_manager_protected_paths():
    for action in ("write", "append", "delete"):
        res = file_manager(action, "/etc/zara_test_protegido", content="x")
        assert res["success"] is False and res.get("blocked") is True
    res = file_manager("write", "/root/.ssh/zara_test", content="x")
    assert res.get("blocked") is True
    assert file_manager("exists", "/etc")["success"] is True
