from __future__ import annotations
import logging
import sys
import threading
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets, QtNetwork

from .orb import Orb, State, AVATAR_CANDIDATES
from .panel import Panel

log = logging.getLogger("zara.ui")

INSTANCE_NAME = "zara-instance"


class _Signals(QtCore.QObject):
    state = QtCore.Signal(str)
    status = QtCore.Signal(str)
    user = QtCore.Signal(str)
    zara = QtCore.Signal(str)
    confirm = QtCore.Signal(str)
    ui = QtCore.Signal(str)


class MicWorker(QtCore.QThread):
    """Hilo de escucha: micrófono → wake → verificación → cerebro → voz.

    Un fallo al procesar una frase se registra y la escucha continúa.
    """

    def __init__(self, backend, signals: _Signals):
        super().__init__()
        self._b = backend
        self._sig = signals
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _process(self, utt):
        self._sig.state.emit(State.THINKING.value)
        result = self._b.handle_utterance(utt)
        if result is None:
            return
        if result.get("denied"):
            self._sig.state.emit(State.DENIED.value)
            self._b.speak("Lo siento, solo atiendo al Señor VK.")
            return
        if result.get("ack"):
            self._sig.state.emit(State.SPEAKING.value)
            import random
            ack_msg = random.choice([
                "A su servicio, Señor VK.",
                "¿Dígame, Señor VK?",
                "A la escucha, Señor VK.",
                "¿En qué puedo ayudarle, Señor VK?",
            ])
            self._b.speak(ack_msg)
            self._b.open_listen_window()
            return
        self._sig.user.emit(result["order"])
        if result.get("ui"):
            self._sig.ui.emit(result["ui"])
        if result["reply"]:
            self._sig.zara.emit(result["reply"])
            self._sig.state.emit(State.SPEAKING.value)
            self._b.speak(result["reply"])
        self._b.open_listen_window(after_reply=True)

    def run(self):
        try:
            self._b.ensure_ready()
        except Exception as exc:  # noqa: BLE001
            log.exception("no se pudo iniciar la escucha")
            self._sig.status.emit(f"sin micrófono: {exc}")
            self._sig.state.emit(State.IDLE.value)
            return
        self._sig.state.emit(State.LISTENING.value)
        if self._b.cfg.get("saludar_al_arrancar", True):
            try:
                g = self._b.greeting_text()
                self._sig.zara.emit(g)
                self._sig.state.emit(State.SPEAKING.value)
                self._b.speak(g)
                self._sig.state.emit(State.LISTENING.value)
            except Exception:
                log.exception("fallo en el saludo inicial")
        while not self._stop.is_set():
            try:
                for utt in self._b.mic.utterances(self._stop):
                    try:
                        self._process(utt)
                    except Exception:  # noqa: BLE001
                        log.exception("error procesando una frase; sigo escuchando")
                    finally:
                        self._sig.state.emit(State.LISTENING.value)
            except Exception:  # noqa: BLE001
                # p. ej. el micro se desconectó: reintentar en unos segundos
                log.exception("la captura de audio falló; reintento en 5 s")
                self._sig.status.emit("micrófono no disponible; reintentando…")
                self._stop.wait(5)


class ZaraApp:
    def __init__(self, backend):
        self.backend = backend
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("Zara")
        QtGui.QGuiApplication.setDesktopFileName("zara")
        self._icon = self._make_icon()
        if not self._icon.isNull():
            self.app.setWindowIcon(self._icon)

        self.orb = Orb()
        self.panel = Panel()
        self.sig = _Signals()
        self._busy = threading.Lock()

        self.orb.clicked.connect(self.toggle_panel)
        self.panel.submitted.connect(self.on_text)
        self.panel.brain_changed.connect(self.on_brain)
        self.panel.stop_voice.connect(self.backend.stop_speaking)

        self.sig.state.connect(self._on_state)
        self.sig.status.connect(self.panel.set_status)
        self.sig.user.connect(lambda t: self.panel.add_line("VK", t, "#2EF2E6"))
        self.sig.zara.connect(lambda t: self.panel.add_line("Zara", t, "#E070FF"))
        self.sig.confirm.connect(self._on_confirm_request)
        self.sig.ui.connect(self._on_ui)

        # Puente de confirmación del Guardián ↔ panel
        self.backend.confirm.request_cb = lambda msg: self.sig.confirm.emit(msg)
        self.panel.confirm_result.connect(self._on_confirm_result)
        # Recordatorios que saltan en segundo plano → al panel
        self.backend.on_reminder = lambda t: self.sig.zara.emit(t)
        # Tareas del agente que terminan en segundo plano → avisa y lo dice
        self.backend.on_task_done = self._on_task_done

        self._init_tray()
        self.worker = MicWorker(backend, self.sig)
        self._state = State.IDLE

    # --- instancia única -----------------------------------------------------
    @staticmethod
    def notify_running_instance() -> bool:
        """Si ya hay una Zara abierta, le pide mostrar el chat y devuelve True."""
        sock = QtNetwork.QLocalSocket()
        sock.connectToServer(INSTANCE_NAME)
        if sock.waitForConnected(300):
            sock.write(b"show\n")
            sock.flush()
            sock.waitForBytesWritten(300)
            sock.disconnectFromServer()
            return True
        return False

    def _start_instance_server(self):
        QtNetwork.QLocalServer.removeServer(INSTANCE_NAME)  # restos de un cierre brusco
        self._server = QtNetwork.QLocalServer()
        self._server.newConnection.connect(self._on_instance_msg)
        if not self._server.listen(INSTANCE_NAME):
            log.warning("no pude abrir el canal de instancia única")

    def _on_instance_msg(self):
        conn = self._server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
        self.show_panel()

    # --- iconos y bandeja --------------------------------------------------
    def _make_icon(self) -> QtGui.QIcon:
        """Icono circular con el avatar de Zara para bandeja y ventana."""
        src = next((c for c in AVATAR_CANDIDATES if Path(c).exists()), None)
        if not src:
            return QtGui.QIcon()
        pm = QtGui.QPixmap(src)
        if pm.isNull():
            return QtGui.QIcon()
        size = 128
        pm = pm.scaled(size, size, QtCore.Qt.KeepAspectRatioByExpanding,
                       QtCore.Qt.SmoothTransformation)
        out = QtGui.QPixmap(size, size)
        out.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(out)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        path = QtGui.QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, pm)
        painter.setClipping(False)
        painter.setPen(QtGui.QPen(QtGui.QColor("#2EF2E6"), 6))
        painter.drawEllipse(3, 3, size - 6, size - 6)
        painter.end()
        return QtGui.QIcon(out)

    def _init_tray(self):
        icon = self._icon if not self._icon.isNull() else \
            self.app.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
        self.tray = QtWidgets.QSystemTrayIcon(icon)
        menu = QtWidgets.QMenu()
        menu.addAction("Mostrar chat", self.show_panel)
        menu.addAction("Detener voz", self.backend.stop_speaking)
        menu.addSeparator()
        menu.addAction("Salir", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Zara")
        self.tray.activated.connect(
            lambda reason: self.toggle_panel()
            if reason == QtWidgets.QSystemTrayIcon.Trigger else None)
        self.tray.show()

    # --- estado ----------------------------------------------------------------
    _STATUS = {
        "idle": "en reposo · Señor VK",
        "listening": "escuchando · diga «Zara»",
        "thinking": "pensando…",
        "speaking": "hablando…",
        "confirm": "esperando su confirmación",
        "denied": "voz no reconocida",
    }

    def _on_state(self, s: str):
        self._state = State(s)
        self.orb.set_state(self._state)
        text = self._STATUS.get(s, s)
        self.panel.set_status(text)
        self.orb.setToolTip(f"Zara — {text}\nClic: chat · Arrastrar: mover")

    # --- panel ---------------------------------------------------------------
    def show_panel(self):
        if self.panel.isVisible():
            self.panel.raise_()
            self.panel.activateWindow()
            return
        try:
            self.panel.set_brains(self.backend.brain_names(), self.backend.active_brain())
        except Exception:
            log.exception("no pude listar los cerebros")
        # En X11 lo colocamos nosotros; en Wayland lo hace el script de KWin.
        g = self.orb.frameGeometry()
        self.panel.move(max(0, g.left() - self.panel.width() - 12), g.top())
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        self.panel.input.setFocus()

    def toggle_panel(self):
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.show_panel()

    def _on_ui(self, action: str):
        if action == "show_panel":
            self.show_panel()

    def on_text(self, text: str):
        def work():
            if not self._busy.acquire(blocking=False):
                self.sig.zara.emit("Un momento, Señor VK: aún estoy con lo anterior.")
                return
            try:
                self.sig.state.emit(State.THINKING.value)
                self.sig.user.emit(text)
                res = self.backend.respond(text, verified=True)
                if res.get("ui"):
                    self.sig.ui.emit(res["ui"])
                if res["reply"]:
                    self.sig.zara.emit(res["reply"])
                    self.sig.state.emit(State.SPEAKING.value)
                    self.backend.speak(res["reply"])
            except Exception:  # noqa: BLE001
                log.exception("error en la petición escrita")
            finally:
                self._busy.release()
                self.sig.state.emit(
                    State.LISTENING.value if self.worker.isRunning() else State.IDLE.value)
        threading.Thread(target=work, daemon=True).start()

    def on_brain(self, name: str):
        nombre = self.backend.switch_brain(name)
        self.panel.add_line("Zara", f"Cerebro activo: {nombre}.", "#8aa")

    def _on_confirm_request(self, message: str):
        self.show_panel()
        self.orb.set_state(State.CONFIRM)
        self.panel.ask_confirm(message)

    def _on_confirm_result(self, ok: bool):
        self.backend.confirm.resolve(ok)

    def _on_task_done(self, text: str):
        """Una tarea de segundo plano ha terminado (se llama desde su hilo)."""
        self.sig.zara.emit(text)
        self.sig.state.emit(State.SPEAKING.value)
        self.backend.speak(text)
        self.sig.state.emit(
            State.LISTENING.value if self.worker.isRunning() else State.IDLE.value)

    # --- ciclo de vida -------------------------------------------------------
    def run(self):
        self._start_instance_server()
        self.orb.place_top_right()
        self.orb.show()
        self.orb.setToolTip("Zara — iniciando…\nClic: chat · Arrastrar: mover")
        self.worker.start()
        return self.app.exec()

    def quit(self):
        try:
            self.backend.stop_speaking()
            self.backend._reminders.stop()
            self.worker.stop()
            self.worker.wait(3000)
        except Exception:
            pass
        self.app.quit()
