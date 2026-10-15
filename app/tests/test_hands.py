from zara.hands import extract_actions, strip_actions, PROTOCOL


def test_extract_single_action():
    txt = "Voy a mirarlo.\n<ejecutar>ls -la /tmp</ejecutar>"
    assert extract_actions(txt) == ["ls -la /tmp"]


def test_extract_multiple_actions_in_order():
    txt = ("<ejecutar>whoami</ejecutar> y luego "
           "<EJECUTAR>uptime</EJECUTAR>")
    assert extract_actions(txt) == ["whoami", "uptime"]


def test_multiline_command():
    txt = "<ejecutar>\necho hola &&\necho adios\n</ejecutar>"
    assert extract_actions(txt) == ["echo hola &&\necho adios"]


def test_no_actions():
    assert extract_actions("Hola, Señor VK, aquí tiene la respuesta.") == []
    assert extract_actions("") == []


def test_strip_actions_leaves_natural_text():
    txt = "Hecho.\n<ejecutar>rm x</ejecutar>\nListo."
    assert strip_actions(txt) == "Hecho.\n\nListo."


def test_protocol_mentions_guardian_and_tag():
    assert "<ejecutar>" in PROTOCOL
    assert "Guardián" in PROTOCOL


# --- el bucle de manos del backend (con dobles de prueba) -------------------
from zara.backend import Backend


class FakeBrain:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def ask(self, prompt, context=""):
        self.prompts.append((prompt, context))
        return self.replies.pop(0)


class FakeSelf:
    def __init__(self, max_steps=4):
        self._hands_max_steps = max_steps
        self.ran = []

    def _run_guarded_raw(self, cmd, force_confirm=False):
        self.ran.append(cmd)
        return ("ok", f"salida-de-{cmd}")

    def active_brain(self):
        return "qwen"


def test_hands_loop_executes_and_feeds_back():
    brain = FakeBrain(["<ejecutar>uptime</ejecutar>",
                       "Lleva 3 horas encendido, Señor VK."])
    fake = FakeSelf()
    out = Backend._hands_runner(fake, brain, "cuánto lleva encendido", "CTX")
    assert fake.ran == ["uptime"]
    assert out == "Lleva 3 horas encendido, Señor VK."
    assert brain.prompts[0][1] == "CTX"            # memoria en el 1er turno
    assert "salida-de-uptime" in brain.prompts[1][0]  # salida realimentada
    assert brain.prompts[1][1] == ""               # memoria no se repite


def test_hands_loop_no_action_returns_text():
    brain = FakeBrain(["Claro, la capital de Francia es París."])
    fake = FakeSelf()
    out = Backend._hands_runner(fake, brain, "capital de Francia", "")
    assert fake.ran == []
    assert "París" in out


def test_hands_loop_is_bounded():
    brain = FakeBrain(["<ejecutar>x</ejecutar>"] * 10)
    fake = FakeSelf(max_steps=3)
    Backend._hands_runner(fake, brain, "bucle infinito", "")
    assert len(fake.ran) == 3  # cortó, no se quedó en bucle
