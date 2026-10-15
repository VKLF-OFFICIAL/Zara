"""Interfaz Holográfica HUD Táctica (ZARA v2.0).

Ventana translúcida sin bordes (FramelessWindowHint), núcleo de reactor reactivo
a estados (IDLE, LISTENING, THINKING/TOOL, SPEAKING), osciloscopio en tiempo real,
consola táctica de ejecución de herramientas y control por atajo global/clic.
"""

from __future__ import annotations
import math
import sys
from enum import Enum
from typing import Callable, Optional
from PySide6 import QtCore, QtGui, QtWidgets


class HUDState(Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"


class HolographicReactor(QtWidgets.QWidget):
    """Widget del reactor central con animación por ondas, gradientes y reactividad RMS."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(140, 140)

        self._state = HUDState.IDLE
        self._phase = 0.0
        self._rms = 0.0
        self._target_rms = 0.0
        self._glow_radius = 50.0

        # Temporizador a 60 FPS para suavidad visual
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(16)

    def set_state(self, state: HUDState) -> None:
        self._state = state
        self.update()

    def set_rms(self, val: float) -> None:
        # Suavizado de nivel RMS
        self._target_rms = min(1.0, max(0.0, val * 12.0))

    def _animate(self) -> None:
        self._phase += 0.05
        if self._phase > 2 * math.pi:
            self._phase -= 2 * math.pi

        # Interpolación exponencial de RMS
        self._rms += (self._target_rms - self._rms) * 0.2
        self._target_rms *= 0.90  # Decaimiento natural
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        # Paleta táctica según estado
        if self._state == HUDState.IDLE:
            base_col = QtGui.QColor(46, 242, 230)       # Cian JARVIS
            pulse = math.sin(self._phase * 0.8) * 4.0
            r_outer = 48.0 + pulse
        elif self._state == HUDState.LISTENING:
            base_col = QtGui.QColor(16, 185, 129)      # Verde esmeralda táctico
            r_outer = 48.0 + (self._rms * 22.0)
        elif self._state == HUDState.THINKING:
            base_col = QtGui.QColor(245, 158, 11)      # Ámbar/Naranja procesamiento
            pulse = math.sin(self._phase * 3.0) * 8.0
            r_outer = 50.0 + pulse
        else:  # SPEAKING
            base_col = QtGui.QColor(236, 72, 153)      # Magenta/Rosa onda de voz
            pulse = abs(math.sin(self._phase * 2.0)) * 14.0
            r_outer = 50.0 + pulse

        # 1. Resplandor exterior difuso (Glow)
        rad_grad = QtGui.QRadialGradient(cx, cy, r_outer + 15.0)
        col_fade = QtGui.QColor(base_col)
        col_fade.setAlpha(80)
        rad_grad.setColorAt(0.0, col_fade)
        rad_grad.setColorAt(0.7, QtGui.QColor(base_col.red(), base_col.green(), base_col.blue(), 25))
        rad_grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
        painter.setBrush(rad_grad)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(QtCore.QPointF(cx, cy), r_outer + 15.0, r_outer + 15.0)

        # 2. Anillo de rotación tecnológico
        pen_ring = QtGui.QPen(base_col, 2.0)
        pen_ring.setDashPattern([6, 8, 2, 8])
        pen_ring.setDashOffset(self._phase * 15.0)
        painter.setPen(pen_ring)
        painter.setBrush(QtCore.Qt.NoPen)
        painter.drawEllipse(QtCore.QPointF(cx, cy), r_outer + 4.0, r_outer + 4.0)

        # 3. Núcleo central sólido con gradiente interno
        core_grad = QtGui.QRadialGradient(cx, cy, r_outer)
        core_grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 230))
        core_grad.setColorAt(0.5, base_col)
        core_grad.setColorAt(1.0, QtGui.QColor(base_col.red() // 2, base_col.green() // 2, base_col.blue() // 2, 180))
        painter.setBrush(core_grad)
        painter.setPen(QtGui.QPen(base_col, 1.5))
        painter.drawEllipse(QtCore.QPointF(cx, cy), r_outer * 0.72, r_outer * 0.72)

        # 4. Cruz táctica central
        pen_cross = QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 1.5)
        painter.setPen(pen_cross)
        painter.drawLine(QtCore.QPointF(cx - 8, cy), QtCore.QPointF(cx + 8, cy))
        painter.drawLine(QtCore.QPointF(cx, cy - 8), QtCore.QPointF(cx, cy + 8))


class ZaraHUD(QtWidgets.QWidget):
    """Ventana HUD translúcida completa, con reactor, estado y miniconsola táctica."""

    toggle_listen_requested = QtCore.Signal()
    barge_in_requested = QtCore.Signal()

    def __init__(self, on_toggle_listen: Optional[Callable[[], None]] = None):
        super().__init__()
        self.on_toggle_listen = on_toggle_listen

        self._drag_pos = QtCore.QPoint()

        self._init_window()
        self._init_ui()

    def _init_window(self) -> None:
        self.setWindowTitle("ZARA HUD")
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setFixedSize(360, 220)

        # Colocar en esquina superior derecha
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.move(geom.right() - 380, geom.top() + 40)

    def _init_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(6)

        # Tarjeta de fondo estilo HUD militar / cian oscuro
        self.bg_frame = QtWidgets.QFrame(self)
        self.bg_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(8, 14, 26, 220), stop:1 rgba(15, 23, 42, 235));
                border: 1px solid rgba(46, 242, 230, 90);
                border-radius: 16px;
            }
        """)

        frame_layout = QtWidgets.QVBoxLayout(self.bg_frame)
        frame_layout.setContentsMargins(10, 8, 10, 10)
        frame_layout.setSpacing(6)

        # Fila superior: Reactor + Título / Estado
        top_row = QtWidgets.QHBoxLayout()

        self.reactor = HolographicReactor(self)
        self.reactor.mousePressEvent = lambda e: self._on_reactor_click(e)
        top_row.addWidget(self.reactor)

        info_layout = QtWidgets.QVBoxLayout()
        info_layout.setSpacing(2)

        title_lbl = QtWidgets.QLabel("ZARA // TACTICAL HUD")
        title_lbl.setStyleSheet("color: #2EF2E6; font-family: 'Consolas', monospace; font-size: 13px; font-weight: bold; border: none;")
        info_layout.addWidget(title_lbl)

        self.state_lbl = QtWidgets.QLabel("ESTADO: EN ESPERA // SEÑOR VK")
        self.state_lbl.setStyleSheet("color: #94A3B8; font-family: 'Consolas', monospace; font-size: 11px; border: none;")
        info_layout.addWidget(self.state_lbl)

        self.command_lbl = QtWidgets.QLabel("VOZ: LISTA")
        self.command_lbl.setStyleSheet("color: #10B981; font-family: 'Consolas', monospace; font-size: 10px; font-weight: 600; border: none;")
        info_layout.addWidget(self.command_lbl)

        info_layout.addStretch()
        top_row.addLayout(info_layout)
        top_row.addStretch()

        frame_layout.addLayout(top_row)

        # Miniconsola táctica inferior (muestra comandos y herramientas en ejecución)
        self.console_box = QtWidgets.QLineEdit(self)
        self.console_box.setReadOnly(True)
        self.console_box.setPlaceholderText(">> Esperando órdenes del Señor VK...")
        self.console_box.setStyleSheet("""
            QLineEdit {
                background-color: rgba(3, 7, 18, 180);
                color: #2EF2E6;
                border: 1px solid rgba(46, 242, 230, 60);
                border-radius: 6px;
                padding: 4px 8px;
                font-family: 'Consolas', monospace;
                font-size: 11px;
            }
        """)
        frame_layout.addWidget(self.console_box)

        main_layout.addWidget(self.bg_frame)

    def _on_reactor_click(self, event: QtGui.QMouseEvent) -> None:
        """Clic en el reactor: conmuta escucha o fuerza barge-in si está hablando."""
        if event.button() == QtCore.Qt.LeftButton:
            if self.reactor._state == HUDState.SPEAKING:
                self.barge_in_requested.emit()
            elif self.on_toggle_listen:
                self.on_toggle_listen()
            self.toggle_listen_requested.emit()

    def set_hud_state(self, state: HUDState) -> None:
        self.reactor.set_state(state)
        state_texts = {
            HUDState.IDLE: "ESTADO: EN ESPERA // SEÑOR VK",
            HUDState.LISTENING: "ESTADO: ESCUCHANDO (VOZ ACTIVA)",
            HUDState.THINKING: "ESTADO: PROCESANDO / AGENTE TÁCTICO",
            HUDState.SPEAKING: "ESTADO: ZARA RESPONDIENDO",
        }
        self.state_lbl.setText(state_texts.get(state, state.value))

    def set_rms_level(self, rms: float) -> None:
        self.reactor.set_rms(rms)

    def update_tool_status(self, tool_name: str, desc: str) -> None:
        """Actualiza la miniconsola del HUD cuando el agente ejecuta una herramienta."""
        self.console_box.setText(f">> [{tool_name.upper()}] {desc}")
        self.command_lbl.setText(f"TOOL: {tool_name}")

    def update_user_speech(self, text: str) -> None:
        """Muestra en la miniconsola lo que Zara ha oído del Señor VK."""
        self.console_box.setText(f">> VK: {text}")

    # Arrastrar ventana haciendo clic y manteniendo
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.buttons() == QtCore.Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
