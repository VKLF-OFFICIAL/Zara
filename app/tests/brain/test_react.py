from zara.hands import ReActAgent, _summarize_result


class FakeNativeBrain:
    """Cerebro con Tool Calling nativo simulado: devuelve guiones de eventos."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.seen = []

    def stream_tools(self, messages, tools=None):
        self.seen.append(messages[-1]["content"])
        for ev in self.scripts.pop(0):
            yield ev


def test_react_runs_tool_then_answers():
    brain = FakeNativeBrain([
        [{"type": "tool_call", "name": "run_shell", "args": {"command": "uptime -p"}}],
        [{"type": "token", "token": "Lleva tres horas encendido, Señor VK."}],
    ])
    ran = []

    def execute(name, args):
        ran.append((name, args))
        return {"success": True, "stdout": "up 3 hours", "exit_code": 0}

    out = ReActAgent(brain, execute, max_steps=10).run("cuánto lleva encendido")
    assert ran == [("run_shell", {"command": "uptime -p"})]
    assert "tres horas" in out


def test_react_autocorrects_on_error():
    brain = FakeNativeBrain([
        [{"type": "tool_call", "name": "run_shell", "args": {"command": "systemctl staus"}}],
        [{"type": "tool_call", "name": "run_shell", "args": {"command": "systemctl status"}}],
        [{"type": "token", "token": "Servicio activo, Señor VK."}],
    ])
    results = iter([
        {"success": False, "stderr": "Unknown command 'staus'", "exit_code": 1},
        {"success": True, "stdout": "active (running)", "exit_code": 0},
    ])

    def execute(name, args):
        return next(results)

    out = ReActAgent(brain, execute, max_steps=10).run("estado del servicio")
    assert "activo" in out.lower()
    # El error del primer intento se realimentó para que corrija.
    assert any("ERROR" in m for m in brain.seen[1:])


def test_react_is_bounded():
    # El cerebro pide herramienta indefinidamente: el bucle debe cortar.
    scripts = [[{"type": "tool_call", "name": "run_shell", "args": {"command": "x"}}]
               for _ in range(20)]
    brain = FakeNativeBrain(scripts)
    calls = []

    def execute(name, args):
        calls.append(1)
        return {"success": True, "stdout": "ok", "exit_code": 0}

    ReActAgent(brain, execute, max_steps=5).run("bucle")
    assert len(calls) == 5


def test_summarize_marks_errors_for_correction():
    s = _summarize_result("run_shell", {}, {"success": False, "stderr": "boom", "exit_code": 2})
    assert "ERROR" in s and "REINTENTA" in s
    ok = _summarize_result("run_shell", {}, {"success": True, "stdout": "listo", "exit_code": 0})
    assert "OK" in ok and "listo" in ok
