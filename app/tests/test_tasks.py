import threading
from zara.tasks import BackgroundTasks


def _wait(ev, timeout=2.0):
    assert ev.wait(timeout), "la tarea no terminó a tiempo"


def test_runs_and_reports_result():
    done = threading.Event()
    got = {}

    def on_done(name, result):
        got["name"] = name
        got["result"] = result
        done.set()

    bg = BackgroundTasks()
    bg.submit("sumar", lambda: 2 + 2, on_done)
    _wait(done)
    assert got["name"] == "sumar"
    assert got["result"] == 4
    assert bg.count() == 0
    assert bg.active() == []


def test_exception_does_not_crash_and_clears():
    done = threading.Event()
    got = {}

    def boom():
        raise RuntimeError("falló")

    def on_done(name, result):
        got["result"] = result
        done.set()

    bg = BackgroundTasks()
    bg.submit("rota", boom, on_done)
    _wait(done)
    assert got["result"] is None   # fn lanzó → resultado None
    assert bg.count() == 0


def test_active_lists_running_task():
    release = threading.Event()
    started = threading.Event()
    done = threading.Event()

    def slow():
        started.set()
        release.wait(2.0)
        return "ok"

    bg = BackgroundTasks()
    bg.submit("lenta", slow, lambda n, r: done.set())
    _wait(started)
    assert "lenta" in bg.active()
    assert bg.count() == 1
    release.set()
    _wait(done)
    assert bg.count() == 0
