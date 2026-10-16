"""Pruebas unitarias para core/tools.py (ZARA v2.0)."""

import os
import sys
import tempfile
from pathlib import Path
import pytest

from core.tools import (
    execute_tool,
    file_manager,
    is_command_forbidden,
    os_control,
    run_shell,
    web_search,
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

