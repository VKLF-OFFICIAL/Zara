import pytest
from zara.brain.registry import BrainRegistry
from zara.errors import BrainError


class FakeBrain:
    def __init__(self, name, ok=True, answer="ok"):
        self.name = name
        self._ok = ok
        self._answer = answer
        self.asked = []

    def available(self):
        return self._ok

    def ask(self, prompt, context=""):
        self.asked.append((prompt, context))
        return self._answer


def test_default_active_is_first_available():
    r = BrainRegistry([FakeBrain("claude"), FakeBrain("ollama")])
    assert r.active_name == "claude"


def test_switch_changes_active():
    r = BrainRegistry([FakeBrain("claude"), FakeBrain("ollama")])
    r.switch("ollama")
    assert r.active_name == "ollama"


def test_switch_unknown_raises():
    r = BrainRegistry([FakeBrain("claude")])
    with pytest.raises(BrainError):
        r.switch("inexistente")


def test_ask_routes_to_active():
    claude = FakeBrain("claude", answer="desde claude")
    ollama = FakeBrain("ollama", answer="desde ollama")
    r = BrainRegistry([claude, ollama])
    r.switch("ollama")
    assert r.ask("hola", context="ctx") == "desde ollama"
    assert ollama.asked == [("hola", "ctx")]


def test_suggest_bug_bounty_uses_local_uncensored():
    r = BrainRegistry([FakeBrain("claude"), FakeBrain("qwen-obliterated")])
    assert r.suggest("necesito ayuda con bug bounty en este target") == "qwen-obliterated"


def test_suggest_general_keeps_default():
    r = BrainRegistry([FakeBrain("claude"), FakeBrain("qwen-obliterated")])
    assert r.suggest("¿qué tiempo hace hoy?") == "claude"


def test_match_name_partial():
    # switch acepta coincidencia parcial amigable por voz
    r = BrainRegistry([FakeBrain("qwen3.8-27b-obliterated")])
    r.switch("qwen")
    assert r.active_name == "qwen3.8-27b-obliterated"
