import json
from zara.brain.native import NativeGemini, NativeAnthropic, NativeOllama


def test_cloud_brains_unavailable_without_keys():
    assert NativeGemini(api_key="").available() is False
    assert NativeAnthropic(api_key="").available() is False


def test_ollama_names_and_system():
    b = NativeOllama("qwen2.5-coder:7b", system="Eres Zara.")
    assert b.name == "qwen2.5-coder:7b"
    assert b.system == "Eres Zara."


def test_ollama_ask_builds_chat_payload(monkeypatch):
    captured = {}

    def fake_post(self, payload):
        captured.update(payload)
        return {"message": {"content": "Hola, Señor VK."}}

    monkeypatch.setattr(NativeOllama, "_post", fake_post)
    b = NativeOllama("qwen", system="Eres Zara.")
    out = b.ask("hola", context="ctx")
    assert out == "Hola, Señor VK."
    assert captured["messages"][0]["role"] == "system"
    assert captured["stream"] is False


def test_ollama_stream_tools_parses_tool_calls(monkeypatch):
    def fake_post(self, payload):
        return {"message": {"tool_calls": [
            {"function": {"name": "run_shell", "arguments": {"command": "ls"}}}]}}

    monkeypatch.setattr(NativeOllama, "_post", fake_post)
    events = list(NativeOllama("qwen").stream_tools([{"role": "user", "content": "lista"}],
                                                    tools=None))
    assert events[0]["type"] == "tool_call"
    assert events[0]["name"] == "run_shell"
    assert events[0]["args"] == {"command": "ls"}


def test_ollama_stream_tools_parses_string_arguments(monkeypatch):
    def fake_post(self, payload):
        return {"message": {"tool_calls": [
            {"function": {"name": "run_shell",
                          "arguments": json.dumps({"command": "whoami"})}}]}}

    monkeypatch.setattr(NativeOllama, "_post", fake_post)
    ev = list(NativeOllama("qwen").stream_tools([{"role": "user", "content": "x"}]))[0]
    assert ev["args"] == {"command": "whoami"}
