from datetime import datetime
from zara.core import ZaraCore


class FakeRegistry:
    def __init__(self):
        self.active_name = "claude"
        self._asked = []

    def names(self):
        return ["claude", "qwen-obliterated"]

    def switch(self, name):
        self.active_name = "qwen-obliterated" if "qwen" in name else name
        return self.active_name

    def suggest(self, task):
        return self.active_name

    def ask(self, prompt, context=""):
        self._asked.append((prompt, context))
        return f"[{self.active_name}] respuesta a: {prompt}"


class FakeVoice:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)
        return None


class FakeMemory:
    def __init__(self):
        self.remembered = []
        self.diary = []
        self.ctx = "CONTEXTO"

    def load_relevant(self, q):
        return self.ctx

    def remember(self, text):
        self.remembered.append(text)

    def append_diary(self, entry):
        self.diary.append(entry)


def _core(speak=False):
    return ZaraCore(FakeRegistry(), FakeVoice(), FakeMemory(), speak=speak)


def test_switch_brain_command():
    c = _core()
    res = c.handle_text("Zara, usa qwen")
    assert c.brains.active_name == "qwen-obliterated"
    assert "qwen" in res["reply"].lower()
    assert res["brain"] == "qwen-obliterated"


def test_remember_command():
    c = _core()
    c.handle_text("Zara, recuerda que prefiero respuestas cortas")
    assert c.memory.remembered == ["prefiero respuestas cortas"]


def test_normal_question_routes_to_brain_with_context():
    c = _core()
    res = c.handle_text("¿cuál es la capital de Francia?")
    assert "respuesta a" in res["reply"]
    assert c.brains._asked[0][1] == "CONTEXTO"  # usó el contexto de memoria
    assert c.memory.diary  # registró en el diario


def test_speaks_when_enabled():
    c = _core(speak=True)
    c.handle_text("hola")
    assert c.voice.said  # llamó a la voz


def test_usa_inside_question_goes_to_brain_not_switch():
    c = _core()
    res = c.handle_text("¿Para qué se usa Python?")
    assert c.brains.active_name == "claude"
    assert "respuesta a" in res["reply"]


def test_pon_unknown_target_goes_to_brain():
    c = _core()
    res = c.handle_text("pon música relajante")
    assert c.brains.active_name == "claude"
    assert "respuesta a" in res["reply"]


def test_remember_only_at_start():
    c = _core()
    c.handle_text("mi abuela no recuerda nada")
    assert c.memory.remembered == []


def test_hands_executor_used_when_given():
    c = _core()
    seen = {}

    def hands(brain, prompt, context):
        seen["prompt"] = prompt
        seen["context"] = context
        return "resultado por manos"

    res = c.handle_text("crea un archivo vacío", hands=hands)
    assert res["reply"] == "resultado por manos"
    assert seen["prompt"] == "crea un archivo vacío"
    assert seen["context"] == "CONTEXTO"      # recibe el contexto de memoria
    assert c.brains._asked == []              # NO se usó brain.ask directo
    assert c.memory.diary                      # se registró igual


def test_no_hands_uses_brain_ask():
    c = _core()
    c.handle_text("hola", hands=None)
    assert c.brains._asked  # vía normal
