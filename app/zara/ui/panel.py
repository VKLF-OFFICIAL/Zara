from __future__ import annotations
import html
import re
from PySide6 import QtCore, QtWidgets, QtGui

PANEL_TITLE = "Zara · Chat"

_QSS = """
#panel { background: #0B0D14; border: 1px solid #2EF2E6; border-radius: 14px; }
QLabel#title { color: #2EF2E6; font-size: 15px; font-weight: 600; }
QLabel#status { color: #8aa; font-size: 11px; }
QTextBrowser { background: #07080C; color: #E6F7FF; border: 1px solid #1a2230;
               border-radius: 8px; font-size: 13px; }
QLineEdit { background: #07080C; color: #E6F7FF; border: 1px solid #2EF2E6;
            border-radius: 8px; padding: 6px; }
QComboBox { background: #07080C; color: #2EF2E6; border: 1px solid #5A4BFF;
            border-radius: 8px; padding: 4px; }
QPushButton { background: #12202a; color: #2EF2E6; border: 1px solid #2EF2E6;
              border-radius: 8px; padding: 6px 10px; }
QPushButton:hover { background: #163040; }
QPushButton#crit { border-color: #FF4040; color: #FF4040; }
QPushButton#icon { border: none; background: transparent; font-size: 15px; padding: 2px 6px; }
QPushButton#icon:hover { color: #E070FF; }
"""


def to_html(text: str) -> str:
    """Texto/markdown sencillo → HTML seguro para el chat."""
    parts = re.split(r"```[a-zA-Z0-9_-]*\n?(.*?)```", text or "", flags=re.S)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # bloque de código
            out.append('<pre style="background:#10141c;color:#9ff;padding:6px;'
                       'border-radius:6px;">' + html.escape(part.rstrip()) + "</pre>")
            continue
        s = html.escape(part)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`([^`]+)`", r'<code style="color:#9ff;">\1</code>', s)
        out.append(s.replace("\n", "<br>"))
    return "".join(out)


class Panel(QtWidgets.QWidget):
    submitted = QtCore.Signal(str)
    brain_changed = QtCore.Signal(str)
    confirm_result = QtCore.Signal(bool)   # True=confirmar, False=cancelar
    stop_voice = QtCore.Signal()
    closed = QtCore.Signal()

    def __init__(self):
        super().__init__(None)
        self.setObjectName("panel")
        self.setWindowTitle(PANEL_TITLE)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint
                            | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.setStyleSheet(_QSS)
        self.setFixedSize(420, 520)

        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Zara")
        title.setObjectName("title")
        self.status = QtWidgets.QLabel("en reposo · Señor VK")
        self.status.setObjectName("status")
        stop = QtWidgets.QPushButton("■")
        stop.setObjectName("icon")
        stop.setToolTip("Detener la voz")
        stop.clicked.connect(self.stop_voice.emit)
        close = QtWidgets.QPushButton("✕")
        close.setObjectName("icon")
        close.setToolTip("Cerrar el chat (Esc)")
        close.clicked.connect(self.hide)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.status)
        top.addWidget(stop)
        top.addWidget(close)
        v.addLayout(top)

        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel("Cerebro:")
        lbl.setStyleSheet("color:#8aa;")
        row.addWidget(lbl)
        self.brains = QtWidgets.QComboBox()
        self.brains.currentTextChanged.connect(self._on_brain)
        row.addWidget(self.brains, 1)
        v.addLayout(row)

        self.chat = QtWidgets.QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        v.addWidget(self.chat, 1)

        # Tarjeta de confirmación (oculta por defecto)
        self.confirm_box = QtWidgets.QFrame()
        cb = QtWidgets.QVBoxLayout(self.confirm_box)
        self.confirm_label = QtWidgets.QLabel()
        self.confirm_label.setWordWrap(True)
        self.confirm_label.setStyleSheet("color:#FFB300;")
        cb.addWidget(self.confirm_label)
        cbtns = QtWidgets.QHBoxLayout()
        ok = QtWidgets.QPushButton("Confirmar")
        ok.setObjectName("crit")
        no = QtWidgets.QPushButton("Cancelar")
        ok.clicked.connect(lambda: self._confirm(True))
        no.clicked.connect(lambda: self._confirm(False))
        cbtns.addWidget(ok)
        cbtns.addWidget(no)
        cb.addLayout(cbtns)
        self.confirm_box.hide()
        v.addWidget(self.confirm_box)

        bottom = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Escriba a Zara…")
        self.input.returnPressed.connect(self._send)
        send = QtWidgets.QPushButton("Enviar")
        send.clicked.connect(self._send)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(send)
        v.addLayout(bottom)

        QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self.hide)

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.closed.emit()

    def set_brains(self, names, active):
        self.brains.blockSignals(True)
        self.brains.clear()
        self.brains.addItems(names)
        if active in names:
            self.brains.setCurrentText(active)
        self.brains.blockSignals(False)

    def _on_brain(self, name):
        if name:
            self.brain_changed.emit(name)

    def _send(self):
        t = self.input.text().strip()
        if t:
            self.input.clear()
            self.submitted.emit(t)

    def add_line(self, who: str, text: str, color: str):
        self.chat.append(f'<b style="color:{color}">{html.escape(who)}:</b> {to_html(text)}')
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def set_status(self, text: str):
        self.status.setText(text)

    def ask_confirm(self, message: str):
        self.confirm_label.setText(message)
        self.confirm_box.show()

    def _confirm(self, ok: bool):
        self.confirm_box.hide()
        self.confirm_result.emit(ok)
