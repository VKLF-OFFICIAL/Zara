"""Pruebas del grupo app_hud: main.py (controlador y arranque), ui/hud.py y core/confirm.py."""

import io
import logging
import os
import queue
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

import main
import ui.hud as hud_mod
from core.confirm import parse_confirmation
from ui.hud import HUDState


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    yield app


# --------------------------------------------------------------------------- dobles de prueba
class FakeEar:
    """Imita a ContinuousEar: listen_utterances() sale en cuanto se detiene el oído."""

    def __init__(self, **_kwargs):
        self._stop = threading.Event()
        self._stop.set()
        self.q: queue.Queue = queue.Queue()
        self.start_calls = 0
        self.stop_calls = 0
        self._muted = False

    def start(self):
        # Como el ContinuousEar real: cada arranque usa un evento de parada nuevo.
        self.start_calls += 1
        self._stop = threading.Event()

    def stop(self):
        self.stop_calls += 1
        self._stop.set()

    def is_running(self):
        return not self._stop.is_set()

    def listen_utterances(self):
        stop = self._stop  # cada generador se queda con el evento de su arranque
        while not stop.is_set():
            try:
                item = self.q.get(timeout=0.02)
            except queue.Empty:
                continue
            yield item

    def is_muted(self):
        return self._muted

    def set_muted(self, muted):
        self._muted = muted


class FakeLive:
    def __init__(self, **_kwargs):
        self.running = False
        self.available = True
        self.model_speaking = False
        self.start_calls = 0
        self.stop_calls = 0

    def is_available(self):
        return self.available

    def is_running(self):
        return self.running

    def is_model_speaking(self):
        return self.model_speaking

    def start(self):
        self.start_calls += 1
        self.running = True

    def stop(self):
        self.stop_calls += 1
        self.running = False

    def interrupt(self):
        pass

    def set_muted(self, _muted):
        pass

    def is_muted(self):
        return False

    def set_model(self, _model):
        pass


class FakeVoice:
    def __init__(self, on_state_change=None, **_kwargs):
        self.said: list[str] = []
        self.speaking = False
        self.shutdown_calls = 0

    def say(self, text):
        self.said.append(text)

    def stop(self):
        self.speaking = False

    def is_speaking(self):
        return self.speaking

    def feed_token_stream(self, gen):
        for _ in gen:
            pass

    def shutdown(self):
        self.shutdown_calls += 1


class _Provider:
    def __init__(self, available=True):
        self._available = available
        self.model = ""
        self.cli_model_override = ""
        self.host = "http://localhost:11434"

    def is_available(self):
        return self._available


class FakeBrain:
    def __init__(self, on_tool_event=None, **_kwargs):
        self._cancel_event = threading.Event()
        self.calls: list[str] = []
        self.tokens: list[str] = ["Hecho, Señor VK."]
        self.ollama = _Provider()
        self.claude = _Provider()
        self.gemini = _Provider()
        self.antigravity = _Provider()

    def cancel_current_turn(self):
        self._cancel_event.set()

    def run_react_agent(self, text, preferred_provider="auto"):
        self._cancel_event.clear()
        self.calls.append(text)
        yield from self.tokens


@pytest.fixture
def make_controller(monkeypatch):
    monkeypatch.setattr(main, "VoiceSynthesizer", FakeVoice)
    monkeypatch.setattr(main, "ZaraBrain", FakeBrain)
    monkeypatch.setattr(main, "ContinuousEar", FakeEar)
    monkeypatch.setattr(main, "GeminiLiveSession", FakeLive)
    monkeypatch.setattr(main, "set_confirmation_handler", lambda _h: None)
    monkeypatch.setattr(main.config, "persist_env_var", lambda *_a, **_k: None)
    monkeypatch.setattr(main.config, "DEFAULT_BRAIN", "ollama")
    created = []

    def _make(cli_mode=True, brain="ollama"):
        monkeypatch.setattr(main.config, "DEFAULT_BRAIN", brain)
        ctrl = main.ZaraController(cli_mode=cli_mode)
        created.append(ctrl)
        return ctrl

    yield _make
    for ctrl in created:
        ctrl._stop_event.set()
        ctrl.ear.stop()


def _wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# --------------------------------------------------------------------------- AUD-2 / V1_-6
def test_volver_de_live_relanza_el_consumidor_de_locuciones(make_controller):
    ctrl = make_controller()
    ctrl.start()
    ctrl.ear.q.put("Zara, primera orden")
    assert _wait_for(lambda: ctrl.brain.calls == ["Zara, primera orden"])

    ctrl._on_brain_changed("gemini_live")
    assert not ctrl.ear.is_running()
    assert _wait_for(lambda: not ctrl._worker_thread.is_alive())  # el consumidor murió

    ctrl._on_brain_changed("ollama")
    assert ctrl.ear.is_running()
    ctrl.ear.q.put("Zara, segunda orden")
    assert _wait_for(lambda: "Zara, segunda orden" in ctrl.brain.calls)


def test_arrancar_en_live_y_pasar_a_clasico_atiende_la_voz(make_controller):
    ctrl = make_controller(brain="gemini_live")
    ctrl.start()
    assert ctrl.live_session.running and not ctrl.ear.is_running()

    ctrl._on_brain_changed("ollama")
    ctrl.ear.q.put("Zara, hola")
    assert _wait_for(lambda: ctrl.brain.calls == ["Zara, hola"])


def test_live_muerta_sola_tambien_reabre_el_microfono(make_controller):
    ctrl = make_controller(brain="gemini_live")
    ctrl.start()
    ctrl.live_session.running = False  # la sesión Live se cayó por su cuenta

    ctrl._on_brain_changed("claude")
    assert ctrl.ear.is_running()
    ctrl.ear.q.put("Zara, qué hora es")
    assert _wait_for(lambda: ctrl.brain.calls == ["Zara, qué hora es"])


def test_ida_y_vuelta_rapida_a_live_no_deja_sorda_a_zara(make_controller):
    ctrl = make_controller()
    ctrl.start()
    for _ in range(3):  # el consumidor aún no ha salido cuando se vuelve al modo clásico
        ctrl._on_brain_changed("gemini_live")
        ctrl._on_brain_changed("ollama")
    ctrl.ear.q.put("Zara, sigues ahí")
    assert _wait_for(lambda: ctrl.brain.calls == ["Zara, sigues ahí"])
    time.sleep(0.5)
    consumers = [t for t in threading.enumerate()
                 if t.is_alive() and getattr(t, "_target", None) == ctrl._voice_loop_worker]
    assert len(consumers) == 1  # un único consumidor de locuciones


def test_cambio_entre_cerebros_clasicos_no_duplica_oido_ni_consumidor(make_controller):
    ctrl = make_controller()
    ctrl.start()
    worker = ctrl._worker_thread
    ctrl._on_brain_changed("claude")
    ctrl._on_brain_changed("ollama")
    assert ctrl.ear.start_calls == 1
    assert ctrl._worker_thread is worker and worker.is_alive()


# --------------------------------------------------------------------------- AUD-4
def test_avisos_desde_hilos_llegan_al_panel_en_el_hilo_gui(qapp, make_controller, monkeypatch):
    calls = []

    def fake_log_line(self, prefix, text):
        calls.append((prefix, text, threading.current_thread() is threading.main_thread()))

    monkeypatch.setattr(hud_mod.ZaraPanel, "log_line", fake_log_line)
    monkeypatch.setattr(main, "CONFIRM_TIMEOUT_S", 0.05)
    ctrl = make_controller(cli_mode=False)

    def _from_worker():
        ctrl._ask_confirmation("borrar notas.txt")
        ctrl._on_live_state_change("reconnecting")

    t = threading.Thread(target=_from_worker)
    t.start()
    t.join(2)
    # Nada se ha tocado desde el hilo secundario: las líneas esperan en la cola de Qt.
    assert calls == []
    QtCore.QCoreApplication.sendPostedEvents()
    QtCore.QCoreApplication.processEvents()
    prefixes = [c[0] for c in calls]
    assert "CONFIRMAR" in prefixes and "SISTEMA" in prefixes
    assert all(in_gui for _p, _t, in_gui in calls)


# --------------------------------------------------------------------------- SEG-10
@pytest.mark.parametrize("texto", [
    "si quieres te lo explico luego",
    "vale, claro",
    "ok google pon música",
    "vale",
    "claro que sí, si lo hubieras dicho antes te lo habría hecho",
])
def test_confirmacion_ignora_muletillas_y_frases_ambiente(texto):
    assert parse_confirmation(texto) is None


@pytest.mark.parametrize("texto", ["Sí", "sí, adelante", "vale, hazlo", "Sí, por favor", "Zara, confirmo"])
def test_confirmacion_acepta_respuestas_claras(texto):
    assert parse_confirmation(texto) is True


def test_confirmacion_por_voz_mientras_zara_habla_se_descarta(make_controller):
    ctrl = make_controller()
    answers = queue.Queue(maxsize=1)
    ctrl._confirm_queue = answers
    ctrl.voice.speaking = True
    assert ctrl._route_confirmation("sí", from_voice=True) is True  # consumida, no es orden
    assert answers.empty()
    ctrl._handle_utterance("Sí, adelante")
    assert answers.empty() and ctrl.brain.calls == []
    # Con Zara en silencio, la misma respuesta sí cuenta.
    ctrl.voice.speaking = False
    ctrl._handle_utterance("Sí, adelante")
    assert answers.get_nowait() is True


def test_confirmacion_escrita_no_depende_de_la_voz(make_controller):
    ctrl = make_controller()
    answers = queue.Queue(maxsize=1)
    ctrl._confirm_queue = answers
    ctrl.voice.speaking = True
    ctrl._on_text_command("sí")
    assert answers.get_nowait() is True


# --------------------------------------------------------------------------- AUD-10
def test_solo_se_ejecuta_la_ultima_orden_en_espera(make_controller):
    ctrl = make_controller()
    with ctrl._busy_lock:  # un turno lento ocupa el cerebro
        ctrl._handle_utterance("Zara, sube el volumen")
        ctrl._handle_utterance("Zara, sube el volumen")
        ctrl._handle_utterance("Zara, no, bájalo")
        time.sleep(0.1)
    assert _wait_for(lambda: len(ctrl.brain.calls) >= 1)
    time.sleep(0.2)
    assert ctrl.brain.calls == ["Zara, no, bájalo"]


def test_parar_descarta_el_turno_que_esperaba(make_controller):
    ctrl = make_controller()
    turn_id = ctrl._next_turn_id()
    ctrl._on_barge_in()
    ctrl._process_query("Zara, borra la caché", turn_id)
    assert ctrl.brain.calls == []


# --------------------------------------------------------------------------- V1_-13
def test_turno_sin_texto_avisa_y_devuelve_la_bola_a_reposo(make_controller):
    ctrl = make_controller()
    ctrl.brain.tokens = []
    states, replies = [], []
    ctrl.sig_hud_state.connect(states.append)
    ctrl.sig_assistant_reply.connect(replies.append)
    ctrl._process_query("Zara, hola", ctrl._next_turn_id())
    assert main.NO_REPLY_MESSAGE in replies
    assert main.NO_REPLY_MESSAGE in ctrl.voice.said
    assert states[0] == HUDState.THINKING and states[-1] == HUDState.IDLE


def test_turno_cancelado_sin_texto_no_avisa(make_controller):
    ctrl = make_controller()
    ctrl.brain.tokens = []
    original = ctrl.brain.run_react_agent

    def _cancelled(text, preferred_provider="auto"):
        yield from original(text, preferred_provider)
        ctrl.brain.cancel_current_turn()  # barge-in a mitad del turno

    ctrl.brain.run_react_agent = _cancelled
    ctrl._process_query("Zara, hola", ctrl._next_turn_id())
    assert main.NO_REPLY_MESSAGE not in ctrl.voice.said


# --------------------------------------------------------------------------- V1_-8
def test_bloqueo_de_instancia_unica(tmp_path):
    lock_path = tmp_path / "zara.lock"
    first = main.acquire_single_instance_lock(lock_path)
    assert first is not None
    assert main.acquire_single_instance_lock(lock_path) is None  # segunda Zara
    first.unlock()
    again = main.acquire_single_instance_lock(lock_path)
    assert again is not None
    again.unlock()


# --------------------------------------------------------------------------- V1_-10
def test_cli_lee_ordenes_y_confirmaciones_del_teclado(qapp, make_controller, capsys):
    ctrl = make_controller(cli_mode=True)
    answers = queue.Queue(maxsize=1)
    ctrl._confirm_queue = answers
    main.read_stdin_commands(ctrl, io.StringIO("sí\n"))
    assert answers.get_nowait() is True

    ctrl._confirm_queue = None
    main.read_stdin_commands(ctrl, io.StringIO("\nqué hora es\n"))
    assert _wait_for(lambda: ctrl.brain.calls == ["qué hora es"])
    out = []

    def _printed():
        QtCore.QCoreApplication.processEvents()  # la respuesta llega por señal en cola
        out.append(capsys.readouterr().out)
        return "ZARA: Hecho, Señor VK." in "".join(out)

    assert _wait_for(_printed)


def test_cli_muestra_avisos_del_panel_en_la_terminal(make_controller, capsys):
    ctrl = make_controller(cli_mode=True)
    ctrl._panel_line("CONFIRMAR", "¿borrar notas.txt?")
    assert "[CONFIRMAR] ¿borrar notas.txt?" in capsys.readouterr().out


def test_read_stdin_sin_consola_no_falla(make_controller, monkeypatch):
    ctrl = make_controller(cli_mode=True)
    monkeypatch.setattr(sys, "stdin", None)
    main.read_stdin_commands(ctrl)  # pythonw.exe en Windows: sys.stdin es None


# --------------------------------------------------------------------------- V1_-12
def test_setup_logging_archivo_nivel_y_excepthook(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    root = logging.getLogger()
    old_level = root.level
    try:
        log_file = main.setup_logging(tmp_path / "logs", "debug")
        assert root.level == logging.DEBUG
        main.setup_logging(tmp_path / "logs", "debug")  # idempotente: un solo handler
        handlers = [h for h in root.handlers if getattr(h, "_zara_file_log", False)]
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.handlers.RotatingFileHandler)
        try:
            raise RuntimeError("fallo de prueba")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
        handlers[0].flush()
        text = log_file.read_text(encoding="utf-8")
        assert "Excepción no capturada" in text and "fallo de prueba" in text
    finally:
        for h in list(root.handlers):
            if getattr(h, "_zara_file_log", False):
                root.removeHandler(h)
                h.close()
        root.setLevel(old_level)


@pytest.mark.parametrize("value, expected", [
    ("debug", logging.DEBUG), ("WARNING", logging.WARNING), ("10", 10),
    ("", logging.INFO), (None, logging.INFO), ("loquesea", logging.INFO),
])
def test_resolve_log_level(value, expected):
    assert main._resolve_log_level(value) == expected


# --------------------------------------------------------------------------- MUL-5 / V1_-7 / MUL-13
def test_wayland_usa_xwayland_si_no_se_fijo_plataforma():
    env = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert main.prepare_qt_platform(env, "linux", xcb_cursor_ok=True) is None
    assert env["QT_QPA_PLATFORM"] == "xcb;wayland"


def test_wayland_respeta_la_plataforma_elegida_por_el_usuario():
    env = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0",
           "QT_QPA_PLATFORM": "wayland"}
    assert main.prepare_qt_platform(env, "linux", xcb_cursor_ok=True) is None
    assert env["QT_QPA_PLATFORM"] == "wayland"


def test_wayland_sin_libxcb_cursor_sigue_en_wayland_nativo():
    env = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert main.prepare_qt_platform(env, "linux", xcb_cursor_ok=False) is None
    assert "QT_QPA_PLATFORM" not in env


def test_x11_sin_libxcb_cursor_da_mensaje_claro():
    env = {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}
    msg = main.prepare_qt_platform(env, "linux", xcb_cursor_ok=False)
    assert msg and "libxcb-cursor0" in msg and "xcb-util-cursor" in msg
    assert main.prepare_qt_platform({"DISPLAY": ":0"}, "linux", xcb_cursor_ok=True) is None
    forced = {"DISPLAY": ":0", "QT_QPA_PLATFORM": "xcb"}
    assert main.prepare_qt_platform(forced, "linux", xcb_cursor_ok=False)


def test_linux_sin_sesion_grafica_sugiere_cli():
    msg = main.prepare_qt_platform({}, "linux", xcb_cursor_ok=True)
    assert msg and "--cli" in msg


def test_windows_y_offscreen_no_tocan_la_plataforma():
    env = {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}
    assert main.prepare_qt_platform(env, "win32", xcb_cursor_ok=False) is None
    assert "QT_QPA_PLATFORM" not in env
    assert main.prepare_qt_platform({"QT_QPA_PLATFORM": "offscreen"}, "linux", xcb_cursor_ok=False) is None


def test_has_xcb_cursor_devuelve_bool():
    assert isinstance(main._has_xcb_cursor(), bool)


def _mouse(kind, pos, buttons):
    p = QtCore.QPointF(pos)
    button = QtCore.Qt.LeftButton if kind != QtCore.QEvent.MouseMove else QtCore.Qt.NoButton
    return QtGui.QMouseEvent(kind, p, p, p, button, buttons, QtCore.Qt.NoModifier)


def test_arrastre_en_wayland_nativo_lo_hace_el_compositor(qapp, monkeypatch):
    monkeypatch.setattr(hud_mod, "uses_native_wayland", lambda: True)
    hud = hud_mod.ZaraHUD()
    calls = []
    hud._start_system_move = lambda: calls.append(1) or True
    start_pos = hud.pos()
    hud.mousePressEvent(_mouse(QtCore.QEvent.MouseButtonPress, QtCore.QPoint(10, 10), QtCore.Qt.LeftButton))
    for x in (30, 60, 90):
        hud.mouseMoveEvent(_mouse(QtCore.QEvent.MouseMove, QtCore.QPoint(x, 10), QtCore.Qt.LeftButton))
    assert calls == [1]  # una sola petición al compositor
    assert hud.pos() == start_pos  # move() no se usa en Wayland nativo
    hud.mouseReleaseEvent(_mouse(QtCore.QEvent.MouseButtonRelease, QtCore.QPoint(90, 10), QtCore.Qt.NoButton))
    assert not hud.panel.isVisible()  # fue un arrastre, no un clic


def test_arrastre_fuera_de_wayland_sigue_moviendo_la_bola(qapp, monkeypatch):
    monkeypatch.setattr(hud_mod, "uses_native_wayland", lambda: False)
    hud = hud_mod.ZaraHUD()
    hud._start_system_move = lambda: pytest.fail("no debe usarse fuera de Wayland")
    start_pos = hud.pos()
    hud.mousePressEvent(_mouse(QtCore.QEvent.MouseButtonPress, QtCore.QPoint(10, 10), QtCore.Qt.LeftButton))
    hud.mouseMoveEvent(_mouse(QtCore.QEvent.MouseMove, QtCore.QPoint(60, 10), QtCore.Qt.LeftButton))
    assert hud.pos() != start_pos


# --------------------------------------------------------------------------- V1_-15
def test_cerrar_la_bola_pide_salir_una_sola_vez(qapp):
    hud = hud_mod.ZaraHUD()
    hud.show()
    hud.panel.show()
    emitted = []
    hud.quit_requested.connect(lambda: emitted.append(1))
    hud.close()
    hud.close()
    assert emitted == [1]
    assert not hud.panel.isVisible()


def test_shutdown_es_idempotente_y_quit_no_se_repite(make_controller, monkeypatch):
    ctrl = make_controller()
    ctrl.start()
    ctrl.shutdown()
    ctrl.shutdown()
    assert ctrl.voice.shutdown_calls == 1
    quits = []

    class _App:
        def quit(self):
            quits.append(1)

    monkeypatch.setattr(main.QtCore.QCoreApplication, "instance", staticmethod(lambda: _App()))
    ctrl._on_quit_requested()  # ya se está cerrando: no relanza el cierre
    assert quits == []
