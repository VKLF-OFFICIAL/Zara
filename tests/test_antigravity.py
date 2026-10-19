"""Cerebro Gemini vía Antigravity CLI (suscripción Google AI Pro), con un `agy` simulado."""

import os
import stat
import sys
import textwrap

import pytest

import core.brain as brain_mod
from core.brain import AntigravityProvider, ZaraRouter
from core.tools import TOOL_DEFINITIONS, cli_safe_arg

FAKE_AGY = textwrap.dedent(
    """\
    #!{python}
    import os, sys, json
    mode = os.environ.get("FAKE_AGY_MODE", "text")
    with open(os.environ["FAKE_AGY_ARGS"], "w", encoding="utf-8") as f:
        json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd()}}, f)
    if mode == "text":
        print("Buenas tardes, Señor VK.")
        print("Son las cinco y cuarto.")
    elif mode == "tool":
        print('{{"name": "os_control", "arguments": {{"action": "system_stats"}}}}')
    elif mode == "auth":
        sys.stderr.write("Error: authentication required. Run agy to sign in.\\n")
        sys.exit(41)
    """
)


@pytest.fixture
def fake_agy(tmp_path, monkeypatch):
    script = tmp_path / "agy"
    script.write_text(FAKE_AGY.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    args_file = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_AGY_ARGS", str(args_file))
    monkeypatch.setattr(brain_mod, "AGY_BIN", str(script))
    return args_file


def _read_args(args_file):
    import json

    return json.loads(args_file.read_text(encoding="utf-8"))


@pytest.mark.skipif(sys.platform == "win32", reason="script con shebang")
def test_text_response_is_streamed(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "text")
    provider = AntigravityProvider()
    events = list(provider.stream_chat([{"role": "user", "content": "¿qué hora es?"}], tools=None, system_prompt="SYS"))
    text = "".join(e["token"] for e in events if e["type"] == "token")
    assert "Buenas tardes" in text and "cinco y cuarto" in text
    assert not any(e["type"] == "error" for e in events)
    info = _read_args(fake_agy)
    assert info["argv"][0] == "-p"
    assert "¿qué hora es?" in info["argv"][1]
    # se ejecuta en su carpeta aislada, no en el directorio del proyecto
    assert info["cwd"].endswith("agy-workspace")


@pytest.mark.skipif(sys.platform == "win32", reason="script con shebang")
def test_tool_call_json_becomes_tool_event(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "tool")
    provider = AntigravityProvider()
    events = list(provider.stream_chat([{"role": "user", "content": "estado del sistema"}], tools=TOOL_DEFINITIONS, system_prompt="SYS"))
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls and calls[0]["name"] == "os_control"
    assert calls[0]["args"].get("action") == "system_stats"
    # el JSON de la herramienta no se lee en voz alta
    assert not any(e["type"] == "token" for e in events)


@pytest.mark.skipif(sys.platform == "win32", reason="script con shebang")
def test_auth_failure_explains_how_to_log_in(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "auth")
    provider = AntigravityProvider()
    events = list(provider.stream_chat([{"role": "user", "content": "hola"}], system_prompt="SYS"))
    errors = [e["error"] for e in events if e["type"] == "error"]
    assert errors and "iniciar sesión" in errors[0]


@pytest.mark.skipif(sys.platform == "win32", reason="script con shebang")
def test_model_flag_is_passed(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "text")
    provider = AntigravityProvider(model="gemini-3.5-flash")
    list(provider.stream_chat([{"role": "user", "content": "hola"}], system_prompt="SYS"))
    argv = _read_args(fake_agy)["argv"]
    assert argv[-2:] == ["--model", "gemini-3.5-flash"]


def test_unavailable_without_binary(monkeypatch):
    monkeypatch.setattr(brain_mod, "AGY_BIN", "agy-que-no-existe-xyz")
    provider = AntigravityProvider()
    assert provider.is_available() is False
    events = list(provider.stream_chat([{"role": "user", "content": "hola"}]))
    assert events[0]["type"] == "error"


def test_prompt_keeps_latest_message_when_truncated():
    history = [{"role": "user", "content": "x" * 5000} for _ in range(20)]
    history.append({"role": "user", "content": "PREGUNTA FINAL"})
    prompt = AntigravityProvider.build_prompt(history, TOOL_DEFINITIONS, "SISTEMA")
    assert len(prompt) <= AntigravityProvider.MAX_PROMPT_CHARS + 50
    assert "PREGUNTA FINAL" in prompt
    assert prompt.startswith("SISTEMA")


def test_router_uses_subscription_when_no_api_keys(monkeypatch):
    monkeypatch.setattr(brain_mod, "GEMINI_API_KEY", "")
    monkeypatch.setattr(brain_mod, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(brain_mod.shutil, "which", lambda name: "/usr/bin/agy" if name == brain_mod.AGY_BIN else None)
    assert ZaraRouter.classify("hola, ¿cómo va el día?", preferred="auto") == "antigravity"
    # una API key de Gemini, si existe, sigue teniendo prioridad (es más rápida)
    monkeypatch.setattr(brain_mod, "GEMINI_API_KEY", "clave")
    assert ZaraRouter.classify("hola", preferred="auto") == "gemini"


def test_cli_safe_arg_neutralises_cmd_metacharacters(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    evil = 'hola" & calc.exe & echo "\nsegunda línea | dir > x'
    safe = cli_safe_arg(r"C:\Users\vk\AppData\Roaming\npm\agy.cmd", evil)
    for ch in '&|<>"^%!':
        assert ch not in safe
    assert "\n" not in safe
    assert "calc.exe" in safe  # el texto se conserva, solo pierde el poder de ejecutar
    # un .exe nativo no pasa por cmd.exe: el texto va intacto
    assert cli_safe_arg(r"C:\Tools\agy.exe", evil) == evil
    monkeypatch.setattr(sys, "platform", "linux")
    assert cli_safe_arg("/usr/bin/agy", evil) == evil
