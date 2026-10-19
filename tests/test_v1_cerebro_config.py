"""Regresiones del grupo «cerebro_config» (core/brain.py y config.py).

Cada test falla con el código anterior a la corrección: CER-6, CER-7, CER-8, CER-12,
CER-13, CER-14, MUL-7, V1_-4 y SEG-13. Segunda ronda (al final): SEG-2, O_BINARY del .env
en Windows, CER-6, CER-14, CER-13, CER-12 y CER-7.
"""

import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

import config
import core.brain as brain_mod
from core.brain import (
    AntigravityProvider,
    ClaudeProvider,
    OllamaProvider,
    ZaraBrain,
    ZaraRouter,
    _extract_embedded_tool_calls,
)
from core.tools import TOOL_DEFINITIONS

KNOWN = {t["name"] for t in TOOL_DEFINITIONS}
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="script con shebang / permisos POSIX")


@pytest.fixture
def brain(monkeypatch):
    # Sin red: OllamaProvider() consulta /api/tags al crearse.
    monkeypatch.setattr(OllamaProvider, "list_models", lambda self: [])
    return ZaraBrain()


# =============================================================================
# CER-6: «Zara, ...» no convierte una orden en charla
# =============================================================================

@pytest.mark.parametrize("query", [
    "Zara, haz una web completa con Laravel en Documentos",
    "Zara, ¿qué tiempo hace en Madrid?",
    "Zara, ¿cuánta memoria libre tengo?",
    "Oye Zara, investiga quién ganó el partido",
    "Zara, escribe un archivo index.php en Documentos",
    "Hola Zara, dime qué tiempo hace en Sevilla",
])
def test_wake_word_orders_are_not_chat(query):
    assert ZaraRouter.is_conversational(query) is False


@pytest.mark.parametrize("query", [
    "Zara", "Hola Zara", "Oye Zara", "Zara, ¿qué hora es?", "Gracias, Zara",
    "hola", "Hola, ¿cómo estás?", "buenos días",
])
def test_greetings_with_wake_word_stay_chat(query):
    assert ZaraRouter.is_conversational(query) is True


def test_wake_word_order_gets_tools_and_several_steps(brain, monkeypatch):
    seen = []

    def fake_stream(provider, screen_b64=None, is_chat_only=False, system_prompt=None, failed_provider=None):
        seen.append(is_chat_only)
        yield {"type": "token", "token": "Hecho, Señor VK."}

    monkeypatch.setattr(brain, "_execute_stream_with_fallback", fake_stream)
    list(brain.run_react_agent("Zara, haz una web con Laravel en Documentos", preferred_provider="ollama"))
    assert seen == [False]  # con herramientas (antes: modo charla, tools=None y 1 paso)


# =============================================================================
# CER-8: ClaudeProvider por API con el SDK anthropic real
# =============================================================================

_SSE = (
    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message",'
    '"role":"assistant","model":"m","content":[],"stop_reason":null,"stop_sequence":null,'
    '"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
    'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hola, Señor VK"}}\n\n'
    'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":3}}\n\n'
    'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


def _claude_with_mock_transport(bodies):
    anthropic = pytest.importorskip("anthropic")
    try:
        import httpx2 as hx  # el SDK 1.x usa httpx2
    except ImportError:  # versiones que aún usan httpx
        import httpx as hx

    def handler(request):
        bodies.append(json.loads(request.content))
        return hx.Response(200, headers={"content-type": "text/event-stream"}, content=_SSE.encode())

    provider = ClaudeProvider(api_key="")
    provider._client = anthropic.Anthropic(api_key="x", http_client=hx.Client(transport=hx.MockTransport(handler)))
    return provider


@pytest.mark.parametrize("tools", [None, TOOL_DEFINITIONS[:2]])
def test_claude_api_stream_accepts_kwargs(monkeypatch, tools):
    monkeypatch.setattr(brain_mod.shutil, "which", lambda name: None)  # sin CLI de respaldo
    bodies = []
    provider = _claude_with_mock_transport(bodies)
    events = list(provider.stream_chat([{"role": "user", "content": "hola"}], tools=tools, system_prompt="SYS"))
    assert not any(e["type"] == "error" for e in events), events
    assert "".join(e["token"] for e in events if e["type"] == "token") == "Hola, Señor VK"
    assert len(bodies) == 1
    body = bodies[0]
    assert "temperature" not in body
    if tools:
        assert [t["name"] for t in body["tools"]] == [t["name"] for t in tools]
    else:
        assert "tools" not in body  # nunca "tools": null


# =============================================================================
# CER-12: extracción de llamadas embebidas
# =============================================================================

def test_openai_style_tool_call_is_parsed():
    text = json.dumps({
        "type": "function",
        "function": {"name": "os_control", "arguments": json.dumps({"action": "system_stats"})},
    })
    calls = _extract_embedded_tool_calls(text, KNOWN, TOOL_DEFINITIONS)
    assert calls == [{"name": "os_control", "arguments": {"action": "system_stats"}}]


@pytest.mark.parametrize("text", [
    '{"name": {"first": "Ana"}}',
    '{"action": ["open_app"]}',
    '{"tool": 3, "arguments": {}}',
])
def test_non_string_names_do_not_crash(text):
    assert _extract_embedded_tool_calls(text, KNOWN, TOOL_DEFINITIONS) == []


def test_function_tag_is_not_executed_twice():
    text = '<function name="os_control" arguments=\'{"action": "open_app", "target": "notepad"}\'/>'
    calls = _extract_embedded_tool_calls(text, KNOWN, TOOL_DEFINITIONS)
    assert len(calls) == 1
    assert calls[0]["name"] == "os_control"


class _FakeOllamaResponse:
    def __init__(self, chunks):
        self.status_code = 200
        self._lines = [json.dumps({"message": {"content": c}}) for c in chunks]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        return iter(self._lines)

    def close(self):
        pass


def test_ollama_does_not_speak_embedded_tool_json(monkeypatch):
    chunks = ["Claro, Señor VK, ", "abro el bloc de notas. ", '{"na', 'me": "os_control", ',
              '"arguments": {"action": "open_app", "target": "notepad"}}']
    monkeypatch.setattr(brain_mod, "_http_post_stream", lambda url, payload: _FakeOllamaResponse(chunks))
    provider = OllamaProvider(model="m")
    provider._native_tools_supported["m"] = False
    events = list(provider.stream_chat([{"role": "user", "content": "abre el bloc"}], tools=TOOL_DEFINITIONS))
    spoken = "".join(e["token"] for e in events if e["type"] == "token")
    assert "abro el bloc de notas" in spoken
    assert "{" not in spoken and "os_control" not in spoken
    calls = [e for e in events if e["type"] == "tool_call"]
    assert len(calls) == 1 and calls[0]["name"] == "os_control"


def test_ollama_text_with_braces_is_still_spoken(monkeypatch):
    chunks = ["En PHP un array vacío ", "se escribe así: {} y ya está."]
    monkeypatch.setattr(brain_mod, "_http_post_stream", lambda url, payload: _FakeOllamaResponse(chunks))
    provider = OllamaProvider(model="m")
    provider._native_tools_supported["m"] = False
    events = list(provider.stream_chat([{"role": "user", "content": "x"}], tools=TOOL_DEFINITIONS))
    spoken = "".join(e["token"] for e in events if e["type"] == "token")
    assert spoken == "".join(chunks)


# =============================================================================
# CER-13: errores del respaldo y turnos cancelados
# =============================================================================

def test_fallback_error_is_reported_not_silent(brain, monkeypatch):
    def failing(provider, screen_b64=None, is_chat_only=False, system_prompt=None, failed_provider=None):
        if provider == "gemini":
            yield {"type": "error", "error": "429 RESOURCE_EXHAUSTED"}
        else:
            yield {"type": "error", "error": "Antigravity CLI falló (código 41): ejecute «agy» una vez para iniciar sesión"}

    monkeypatch.setattr(brain, "_execute_stream_with_fallback", failing)
    out = "".join(brain.run_react_agent("Zara, ¿qué tiempo hace en Madrid?", preferred_provider="gemini"))
    assert "No he podido obtener respuesta" in out
    assert "iniciar sesión" in out
    assert brain.history[-1]["role"] == "assistant"


def test_fallback_does_not_retry_the_ollama_that_just_failed(brain, monkeypatch):
    calls = []
    monkeypatch.setattr(brain.gemini, "is_available", lambda: False)
    monkeypatch.setattr(brain.ollama, "is_available", lambda: True)
    monkeypatch.setattr(brain.ollama, "stream_chat", lambda *a, **k: calls.append(1) or iter([]))
    events = list(brain._execute_stream_with_fallback("gemini", failed_provider="ollama"))
    assert calls == []
    assert events and events[0]["type"] == "error"


def test_cancel_before_tools_closes_the_turn(brain, monkeypatch):
    def stream(*a, **k):
        brain.cancel_current_turn()
        yield {"type": "tool_call", "name": "run_shell", "args": {"command": "ls"}}

    executed = []
    monkeypatch.setattr(brain, "_execute_stream_with_fallback", stream)
    monkeypatch.setattr(brain_mod, "execute_tool", lambda n, a: executed.append(n) or {"success": True})
    list(brain.run_react_agent("Zara, borra los logs", preferred_provider="ollama"))
    assert executed == []
    assert [m["role"] for m in brain.history] == ["user", "assistant"]
    assert "no la ejecutes" in brain.history[-1]["content"]


# =============================================================================
# CER-7: la captura de pantalla no entra en el historial como base64
# =============================================================================

def test_inspect_screen_base64_goes_to_next_step_not_history(brain, monkeypatch):
    big = "QUJD" * 50000  # ~200 KB de base64
    screens = []
    step = {"n": 0}

    def stream(provider, screen_b64=None, is_chat_only=False, system_prompt=None, failed_provider=None):
        screens.append(screen_b64)
        step["n"] += 1
        if step["n"] == 1:
            yield {"type": "tool_call", "name": "inspect_screen", "args": {"prompt": "qué ves"}}
        else:
            yield {"type": "token", "token": "Veo su escritorio, Señor VK."}

    monkeypatch.setattr(brain, "_execute_stream_with_fallback", stream)
    monkeypatch.setattr(brain_mod, "execute_tool", lambda n, a: {"success": True, "base64_data": big})
    list(brain.run_react_agent("Zara, ¿qué ves?", preferred_provider="gemini"))
    assert screens == [None, big]
    assert all(big[:1000] not in m["content"] for m in brain.history)


# =============================================================================
# CER-14: Claude CLI con historial, herramientas, watchdog y código de salida
# =============================================================================

FAKE_CLAUDE = textwrap.dedent(
    """\
    #!{python}
    import os, sys, time
    mode = os.environ.get("FAKE_CLAUDE_MODE", "text")
    with open(os.environ["FAKE_CLAUDE_ARGS"], "w", encoding="utf-8") as f:
        f.write(sys.argv[2])
    if mode == "text":
        print("Buenas tardes, Señor VK. Todo en orden.")
    elif mode == "tool":
        print('{{"name": "file_manager", "arguments": {{"action": "mkdir", "path": "Documentos/web"}}}}')
    elif mode == "fail":
        sys.stderr.write("Invalid API key. Please run /login\\n")
        sys.exit(1)
    elif mode == "hang":
        time.sleep(30)
    """
)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    args_file = tmp_path / "prompt.txt"
    monkeypatch.setenv("FAKE_CLAUDE_ARGS", str(args_file))
    monkeypatch.setattr(brain_mod.shutil, "which", lambda name: str(script) if name == "claude" else None)
    provider = ClaudeProvider(api_key="")
    provider._workdir = tmp_path / "claude-workspace"
    return provider, args_file


HISTORY = [
    {"role": "user", "content": "Zara, crea una carpeta web en Documentos"},
    {"role": "assistant", "content": "Ejecutando acción: file_manager(...)"},
    {"role": "user", "content": "[OBSERVACIÓN DEL SISTEMA]: ok"},
]


@POSIX_ONLY
def test_claude_cli_gets_history_and_tools(fake_claude, monkeypatch):
    provider, args_file = fake_claude
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "tool")
    events = list(provider.stream_chat(HISTORY, tools=TOOL_DEFINITIONS, system_prompt="SYS"))
    prompt = args_file.read_text(encoding="utf-8")
    assert "crea una carpeta web en Documentos" in prompt  # la petición original no se pierde
    assert "file_manager" in prompt                         # y conoce las herramientas
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls and calls[0]["name"] == "file_manager"
    assert not any(e["type"] == "token" for e in events)    # el JSON no se lee en voz alta


@POSIX_ONLY
def test_claude_cli_nonzero_exit_is_an_error(fake_claude, monkeypatch):
    provider, _ = fake_claude
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    events = list(provider.stream_chat(HISTORY, tools=None, system_prompt="SYS"))
    errors = [e["error"] for e in events if e["type"] == "error"]
    assert errors and "código 1" in errors[0] and "iniciar sesión" in errors[0]


@POSIX_ONLY
def test_claude_cli_hang_is_cut_by_watchdog(fake_claude, monkeypatch):
    provider, _ = fake_claude
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    monkeypatch.setattr(brain_mod, "AGY_TIMEOUT", 1)
    import time

    t0 = time.monotonic()
    events = list(provider.stream_chat(HISTORY, tools=None, system_prompt="SYS"))
    assert time.monotonic() - t0 < 15
    assert any(e["type"] == "error" and "no respondió" in e["error"] for e in events)


@POSIX_ONLY
def test_agy_does_not_speak_trailing_tool_json(tmp_path, monkeypatch):
    script = tmp_path / "agy"
    script.write_text(
        f"#!{sys.executable}\n"
        "print('Claro, Señor VK, consulto el sistema.')\n"
        "print('{\"name\": \"os_control\", \"arguments\": {\"action\": \"system_stats\"}}')\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(brain_mod, "AGY_BIN", str(script))
    provider = AntigravityProvider()
    provider._workdir = tmp_path / "agy-workspace"
    events = list(provider.stream_chat([{"role": "user", "content": "estado"}], tools=TOOL_DEFINITIONS, system_prompt="S"))
    spoken = "".join(e["token"] for e in events if e["type"] == "token")
    assert "consulto el sistema" in spoken and "{" not in spoken
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["os_control"]


def test_code_keyword_prefers_gemini_over_claude_cli(monkeypatch):
    monkeypatch.setattr(brain_mod, "GEMINI_API_KEY", "clave")
    monkeypatch.setattr(brain_mod, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(brain_mod, "find_agy", lambda: None)
    monkeypatch.setattr(brain_mod.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    assert ZaraRouter.classify("Zara, crea un script en Documentos") == "gemini"
    # Con API key de Anthropic, el código sigue yendo a Claude.
    monkeypatch.setattr(brain_mod, "ANTHROPIC_API_KEY", "k")
    assert ZaraRouter.classify("Zara, crea un script en Documentos") == "claude"


# =============================================================================
# MUL-7: el prompt dice qué SO y qué shell usa run_shell
# =============================================================================

def test_system_prompt_names_os_and_shell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert "run_shell ejecuta bash" in config.get_system_prompt()
    monkeypatch.setattr(sys, "platform", "win32")
    prompt = config.get_system_prompt()
    assert "Windows" in prompt and "PowerShell" in prompt


# =============================================================================
# V1_-4: lectura robusta del .env y de los números
# =============================================================================

@pytest.mark.parametrize("raw, cast, expected", [
    ("0,5", float, 0.5),
    ("1  # haz", int, 1),
    ("120s", int, 120),        # no válido -> valor por defecto
    ("abc", float, 0.5),       # no válido -> valor por defecto
    ("2.5", int, 120),         # un entero con decimales no es válido
    ("nan", float, 0.5),
])
def test_env_num_never_raises(monkeypatch, raw, cast, expected):
    default = 0.5 if cast is float else 120
    monkeypatch.setenv("ZARA_T_NUM", raw)
    assert config._env_num("ZARA_T_NUM", default, cast) == expected


def test_env_num_minimum_and_warning(monkeypatch, capsys):
    monkeypatch.setenv("ZARA_T_NUM", "0")
    assert config._env_num("ZARA_T_NUM", 8, minimum=1) == 8
    assert "ZARA_T_NUM" in capsys.readouterr().err


def test_bad_number_in_env_does_not_break_import():
    env = dict(os.environ, ZARA_AGY_TIMEOUT="120s", ZARA_VAD_THRESHOLD="0,6", ZARA_MAX_REACT_STEPS="ocho")
    res = subprocess.run(
        [sys.executable, "-c", "import config; print(config.AGY_TIMEOUT, config.VAD_THRESHOLD, config.MAX_REACT_STEPS)"],
        cwd=str(config.BASE_DIR), env=env, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == ["120", "0.6", "8"]
    assert "ZARA_AGY_TIMEOUT" in res.stderr


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig"])
def test_load_env_file_handles_bom_encodings(tmp_path, monkeypatch, encoding):
    env = tmp_path / ".env"
    env.write_text('ZARA_T_KEY="secreto"\r\nZARA_T_N=3  # comentario\r\n', encoding=encoding)
    for k in ("ZARA_T_KEY", "ZARA_T_N"):
        monkeypatch.delenv(k, raising=False)
    config._load_env_file(env)
    assert os.environ.get("ZARA_T_KEY") == "secreto"
    assert os.environ.get("ZARA_T_N") == "3"


def test_persist_env_var_reads_utf16_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setenv("ZARA_DEFAULT_BRAIN", "")
    (tmp_path / ".env").write_text('GEMINI_API_KEY="x"\r\n', encoding="utf-16")
    config.persist_env_var("ZARA_DEFAULT_BRAIN", "gemini")
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'GEMINI_API_KEY="x"' in text and 'ZARA_DEFAULT_BRAIN="gemini"' in text
    assert "\r\r" not in text


# =============================================================================
# SEG-13: el .env con claves queda en 0600 en Linux/macOS
# =============================================================================

@POSIX_ONLY
def test_persist_env_var_keeps_env_private(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setenv("ZARA_DEFAULT_BRAIN", "")
    env = tmp_path / ".env"
    env.write_text('GEMINI_API_KEY="x"\n', encoding="utf-8")
    env.chmod(0o644)
    config.persist_env_var("ZARA_DEFAULT_BRAIN", "auto")
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    env.unlink()
    config.persist_env_var("ZARA_DEFAULT_BRAIN", "auto")  # .env nuevo
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


@POSIX_ONLY
def test_legacy_migration_keeps_env_and_backup_private(tmp_path):
    env = tmp_path / ".env"
    env.write_text('GEMINI_API_KEY="x"\nZARA_WS_PORT=8765\n', encoding="utf-8")
    env.chmod(0o644)
    assert config._migrate_legacy_env(env) == ["ZARA_WS_PORT"]
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".env.bak").stat().st_mode) == 0o600


def test_cli_watchdog_kills_whole_tree_on_windows(monkeypatch):
    """En Windows el CLI es un shim .cmd: hay que matar también el node.exe hijo."""
    calls = []

    class _Proc:
        pid = 4321

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(brain_mod.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    brain_mod._kill_process_tree(_Proc())
    assert calls[0] == ["taskkill", "/F", "/T", "/PID", "4321"]
    assert calls[-1] == "kill"


# =============================================================================
# SEGUNDA RONDA
# =============================================================================

def _forbidden(cmd):
    import re

    return any(re.search(p, cmd, re.IGNORECASE) for p in config.FORBIDDEN_COMMANDS_REGEX)


def _confirms(cmd):
    import re

    return any(re.search(p, cmd, re.IGNORECASE) for p in config.CONFIRM_COMMANDS_REGEX)


# --- SEG-2: borrados masivos evadían el guardarraíl ---------------------------

@pytest.mark.parametrize("cmd", [
    # bash: HOME en todas sus formas, /home, la raíz con comodín, la marca al final
    "rm -rf ~/*", "rm -rf ~/.*", 'rm -rf "$HOME"', 'rm -rf "$HOME"/*', "rm -rf ${HOME}",
    "rm --recursive --force ~", "rm / -rf", "rm -rf -- /", "rm -rf '/'", "/bin/rm -rf /home",
    "rm -rf /home/*", "rm -rf /home/vk", "cd /tmp; rm -rf ~", "rm -fr ~/ && echo ok",
    "find ~ -type f -delete", "find $HOME -exec rm -rf {} +", "find /home -delete",
    "find ~ -name '*.txt' | xargs rm -f",
    # PowerShell y sus alias
    "Remove-Item -Recurse -Force C:\\", "Remove-Item C:\\Users\\vk -Recurse -Force",
    "Remove-Item -Recurse -Force $env:USERPROFILE", 'Remove-Item -Recurse "$env:USERPROFILE\\*"',
    "ri -r -fo C:\\*", "rm -r -fo $HOME", "Remove-Item -Recurse -Force C:\\Windows\\System32",
    'Remove-Item -Recurse -Force "C:\\Program Files"',
    "Get-ChildItem C:\\ -Recurse | Remove-Item -Force", "gci ~ | ri -r -fo",
    "Get-ChildItem -Path $env:USERPROFILE -Recurse | Remove-Item",
    # cmd
    "del /s /q C:\\*", "del /f /s /q %USERPROFILE%", "rd /s /q C:\\", "rmdir /s /q C:\\Users",
    "rd /s/q %USERPROFILE%",
    # discos y copias de seguridad
    "format C: /q", "Clear-Disk -Number 0 -RemoveData", "vssadmin delete shadows /all /quiet",
])
def test_mass_deletes_are_forbidden(cmd):
    assert _forbidden(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "ls -la", "rm -rf /tmp/x", "rm -rf ~/proyecto/build", "rm -rf ~/proyecto/build/*",
    "rm -rf /home/vk/proyecto/build", "Remove-Item -Recurse -Force C:\\Users\\vk\\Documents\\build",
    "rd /s /q build", "del /s *.tmp", "find . -name '*.pyc' -delete", "find ~ -name '*.txt'",
    "ls ~ | grep x", "Get-ChildItem C:\\ | Select-Object Name", "dir /s C:\\", "ls -R ~",
    "cp -r ~ /tmp/backup", "rm -rf node_modules",
])
def test_scoped_commands_are_not_forbidden(cmd):
    assert not _forbidden(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "rm -rf ~/proyecto/build", "find . -delete", "find . -exec rm {} +", "rd /s /q build",
    "erase x.txt", "ri x", "shred -u a", "unlink a", "rsync -a --delete a/ b/",
    "Clear-Content log.txt", "rm -rf $(echo /)",
])
def test_other_deletes_ask_for_confirmation(cmd):
    assert _confirms(cmd), cmd


def test_guardrail_regex_is_not_catastrophically_slow():
    import time

    t0 = time.monotonic()
    for cmd in ("rm -r" + " /etcx/a" * 3000, "rm -rf " + "a " * 5000, "find " + "~/a " * 3000):
        _forbidden(cmd)
    assert time.monotonic() - t0 < 5


# --- NUEVO: el .env se escribía en modo texto del CRT en Windows ---------------

def test_write_private_opens_in_binary_mode(tmp_path, monkeypatch):
    fake_binary = 0x40000000  # valor ficticio que no usa ningún flag de Linux
    seen = []
    real_open = os.open

    def spy(path, flags, mode=0o777, **kw):
        seen.append(flags)
        return real_open(path, flags & ~fake_binary, mode, **kw)

    monkeypatch.setattr(os, "O_BINARY", fake_binary, raising=False)
    monkeypatch.setattr(os, "open", spy)
    config._write_private(tmp_path / ".env", "A=1\n")
    assert seen and seen[0] & fake_binary
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "A=1\n"


# --- CER-6: saludos de varias palabras seguidos de una orden -------------------

@pytest.mark.parametrize("query", [
    "Buenas tardes Zara, investiga quién ganó el partido",
    "buenos días, investiga quién ganó el partido",
    "Zara, buenos días, investiga quién ganó el partido",
    "qué tal, ¿quién ganó la liga ayer?",
    "de nada, ahora investiga quién ganó el partido",
    "cuándo es el próximo partido del Betis",
])
def test_multiword_greeting_does_not_swallow_the_order(query):
    assert ZaraRouter.is_conversational(query) is False


@pytest.mark.parametrize("query", [
    "Buenos días, Zara", "buenas noches", "¿Qué tal, Zara?", "qué hora es en Tokio",
    "Hola Zara, ¿cómo estás?", "buenas, ¿qué hora es?",
])
def test_plain_greetings_still_chat(query):
    assert ZaraRouter.is_conversational(query) is True


# --- CER-14: claude.cmd pasa por cmd.exe (8191 caracteres) ---------------------

LONG_HISTORY = [{"role": "user", "content": "x" * 6000}, {"role": "assistant", "content": "y" * 6000},
                {"role": "user", "content": "Zara, crea una carpeta web en Documentos"}]


def test_claude_cmd_shim_gets_prompt_by_stdin(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"], captured["kw"] = cmd, kw
        return iter([])

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(brain_mod.shutil, "which",
                        lambda name: r"C:\Users\vk\AppData\Roaming\npm\claude.cmd" if name == "claude" else None)
    monkeypatch.setattr(brain_mod, "_run_cli_stream", fake_run)
    list(ClaudeProvider(api_key="").stream_chat(LONG_HISTORY, tools=TOOL_DEFINITIONS, system_prompt="SYS"))
    assert len(subprocess.list2cmdline(captured["cmd"])) < 8191
    assert "crea una carpeta web" in captured["kw"]["stdin_text"]
    assert "\n" in captured["kw"]["stdin_text"]  # entero, sin aplanar


def test_agy_cmd_shim_fits_cmd_exe_line(monkeypatch):
    captured = {}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(brain_mod, "find_agy", lambda: r"C:\Users\vk\AppData\Roaming\npm\agy.cmd")
    monkeypatch.setattr(brain_mod, "_run_cli_stream", lambda cmd, **kw: captured.update(cmd=cmd) or iter([]))
    list(AntigravityProvider().stream_chat(LONG_HISTORY, tools=TOOL_DEFINITIONS, system_prompt="SYS"))
    line = subprocess.list2cmdline(captured["cmd"])
    assert len(line) <= 8191 - 600
    assert "crea una carpeta web" in line  # se recorta lo viejo, no la última petición


def test_build_prompt_never_exceeds_tiny_limit():
    prompt = AntigravityProvider.build_prompt(LONG_HISTORY, TOOL_DEFINITIONS, "SYS", max_chars=500)
    assert len(prompt) <= 500


@POSIX_ONLY
def test_run_cli_stream_feeds_stdin(tmp_path):
    cmd = [sys.executable, "-c", "import sys; d = sys.stdin.read(); print('recibidos', len(d), d[-5:])"]
    events = list(brain_mod._run_cli_stream(cmd, cwd=tmp_path, tools=None, timeout=30, label="t",
                                            stdin_text="a" * 200000 + "FINAL"))
    text = "".join(e.get("token", "") for e in events)
    assert "recibidos 200005 FINAL" in text


# --- CER-13: la pista del cerebro principal no se pierde ------------------------

def test_agy_login_hint_survives_when_no_backup(brain, monkeypatch):
    hint = "Antigravity CLI falló (código 41): auth. Abra una terminal y ejecute «agy» una vez para iniciar sesión"
    monkeypatch.setattr(brain.gemini, "is_available", lambda: False)
    monkeypatch.setattr(brain.claude, "is_available", lambda: False)
    monkeypatch.setattr(brain.ollama, "is_available", lambda: False)
    monkeypatch.setattr(brain.antigravity, "is_available", lambda: True)
    monkeypatch.setattr(brain.antigravity, "stream_chat", lambda *a, **k: iter([{"type": "error", "error": hint}]))
    out = "".join(brain.run_react_agent("Zara, investiga quién ganó el partido", preferred_provider="antigravity"))
    assert "iniciar sesión" in out
    assert "Compruebe que Ollama" not in out


# --- CER-12: JSON de llamada que no empieza por "name" ----------------------------

@pytest.mark.parametrize("call", [
    '{"action": "open_app", "target": "notepad"}',
    '{"arguments": {"action": "open_app", "target": "notepad"}, "name": "os_control"}',
])
def test_gate_hides_any_json_tool_call(call):
    gate = brain_mod._ToolMarkupGate()
    text = "Claro, lo abro ahora mismo. " + call
    spoken = "".join(gate.feed(text[i:i + 7]) for i in range(0, len(text), 7))
    assert _extract_embedded_tool_calls(text, KNOWN, TOOL_DEFINITIONS)
    assert "Claro, lo abro" in spoken and "notepad" not in spoken


def test_gate_releases_plain_json_that_was_not_a_call():
    gate = brain_mod._ToolMarkupGate()
    text = 'La respuesta es {"temperatura": 20} grados.'
    spoken = gate.feed(text)
    events = list(brain_mod._final_text_events([], text, False, gate))
    assert spoken + "".join(e["token"] for e in events) == text


# --- CER-7: la nota de la captura no miente --------------------------------------

@pytest.mark.parametrize("gemini_ok, expected", [(False, "no puede ver"), (True, "adjunta")])
def test_screenshot_note_matches_provider(brain, monkeypatch, gemini_ok, expected):
    step = {"n": 0}

    def stream(provider, screen_b64=None, is_chat_only=False, system_prompt=None, failed_provider=None):
        step["n"] += 1
        if step["n"] == 1:
            yield {"type": "tool_call", "name": "inspect_screen", "args": {"prompt": "qué ves"}}
        else:
            yield {"type": "token", "token": "Listo."}

    monkeypatch.setattr(brain.gemini, "is_available", lambda: gemini_ok)
    monkeypatch.setattr(brain, "_execute_stream_with_fallback", stream)
    monkeypatch.setattr(brain_mod, "execute_tool", lambda n, a: {"success": True, "base64_data": "QUJD"})
    list(brain.run_react_agent("Zara, ¿qué ves?", preferred_provider="gemini" if gemini_ok else "antigravity"))
    obs = [m["content"] for m in brain.history if "Resultado de inspect_screen" in m["content"]]
    assert obs and expected in obs[0]
