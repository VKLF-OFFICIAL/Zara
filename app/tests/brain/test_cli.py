from pathlib import Path
from zara.brain.cli import ClaudeBrain


def test_consulta_mode_limits_tools():
    b = ClaudeBrain(mode="consulta")
    cmd = b._build_cmd("hola", "")
    assert "--tools" in cmd
    tools = cmd[cmd.index("--tools") + 1]
    assert set(tools.split(",")) == {"WebFetch", "WebSearch", "Read"}
    assert "--dangerously-skip-permissions" not in cmd
    assert b.cwd == ClaudeBrain.SANDBOX


def test_agente_mode_full_power_in_workdir(tmp_path):
    b = ClaudeBrain(mode="agente", workdir=str(tmp_path))
    cmd = b._build_cmd("arregla el bug", "")
    assert "--dangerously-skip-permissions" in cmd
    assert "--tools" not in cmd            # todas las herramientas
    assert b.cwd == str(tmp_path)


def test_agente_default_workdir_is_home():
    b = ClaudeBrain(mode="agente")
    assert b.cwd == str(Path.home())


def test_model_and_persona_flags():
    b = ClaudeBrain(mode="agente", model="haiku", system="Eres Zara.")
    cmd = b._build_cmd("hola", "ctx")
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert "Eres Zara." in cmd[cmd.index("--append-system-prompt") + 1]
    assert "ctx" in cmd[cmd.index("-p") + 1]


def test_prompt_uses_p_flag():
    cmd = ClaudeBrain(mode="agente")._build_cmd("hola", "")
    assert "-p" in cmd and "--setting-sources" in cmd


def test_mcp_config_passed_in_agent_mode(tmp_path):
    mcp = tmp_path / "mcp.json"
    mcp.write_text('{"mcpServers": {}}')
    b = ClaudeBrain(mode="agente", workdir=str(tmp_path), mcp_config=str(mcp))
    cmd = b._build_cmd("haz algo", "")
    assert cmd[cmd.index("--mcp-config") + 1] == str(mcp)
    assert "--strict-mcp-config" in cmd


def test_mcp_config_ignored_when_missing(tmp_path):
    b = ClaudeBrain(mode="agente", mcp_config=str(tmp_path / "no-existe.json"))
    assert b.mcp_config is None
    assert "--mcp-config" not in b._build_cmd("hola", "")
