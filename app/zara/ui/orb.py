from __future__ import annotations
import math
from enum import Enum
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets

# Paleta de Zara (de la imagen de referencia).
CYAN = QtGui.QColor("#2EF2E6")
VIOLET = QtGui.QColor("#5A4BFF")
PINK = QtGui.QColor("#E070FF")
AMBER = QtGui.QColor("#FFB300")
RED = QtGui.QColor("#FF4040")
BG = QtGui.QColor("#07080C")

AVATAR_CANDIDATES = [
    str(Path(__file__).resolve().parent.parent.parent.parent / "config" / "avatar.jpg"),
    str(Path.home() / "Zara" / "config" / "avatar.jpg"),
    "/mnt/almacen/ZARA/config/avatar.jpg",
    "/home/vklf/Imágenes/Eduhg.jpg",
]


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CONFIRM = "confirm"       # ámbar (🟡)
    CRITICAL = "critical"     # rojo (🔴)
    DENIED = "denied"


_STATE_COLOR = {
    State.IDLE: CYAN,
    State.LISTENING: CYAN,
    State.THINKING: VIOLET,
    State.SPEAKING: PINK,
    State.CONFIRM: AMBER,
    State.CRITICAL: RED,
    State.DENIED: RED,
}


def _lerp(a: QtGui.QColor, b: QtGui.QColor, t: float) -> QtGui.QColor:
    t = max(0.0, min(1.0, t))
    return QtGui.QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def _iridescent(phase: float) -> QtGui.QColor:
    """Color que recorre cian → violeta → rosa → cian (estética glitch)."""
    x = (math.sin(phase) + 1) / 2  # 0..1
    if x < 0.5:
        return _lerp(CYAN, VIOLET, x * 2)
    return _lerp(VIOLET, PINK, (x - 0.5) * 2)


class Orb(QtWidgets.QWidget):
    """Esfera flotante sin marco, translúcida, siempre encima, arrastrable."""

    clicked = QtCore.Signal()

    def __init__(self, diameter: int = 118):
        super().__init__(None)
        self._d = diameter
        self._state = State.IDLE
        self._phase = 0.0
        self._level = 0.2
        self._target_level = 0.2
        self._drag_off = None
        self._avatar = self._load_avatar(diameter)

        self.setFixedSize(diameter, diameter)
        self.setWindowTitle("Zara")
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setCursor(QtCore.Qt.OpenHandCursor)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(28)  # ~36 fps

    def _load_avatar(self, d: int) -> QtGui.QPixmap | None:
        for c in AVATAR_CANDIDATES:
            if Path(c).exists():
                pm = QtGui.QPixmap(c)
                if not pm.isNull():
                    return pm.scaled(d * 2, d * 2,
                                     QtCore.Qt.KeepAspectRatioByExpanding,
                                     QtCore.Qt.SmoothTransformation)
        return None

    # --- API pública ---
    def set_state(self, state: State):
        self._state = state
        self.update()

    def set_level(self, level: float):
        self._target_level = max(0.0, min(1.0, level))

    def place_top_right(self, margin: int = 24):
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self._d - margin, screen.top() + margin)

    # --- animación ---
    def _tick(self):
        self._phase += 0.05
        if self._state in (State.LISTENING, State.SPEAKING):
            self._target_level = 0.45 + 0.4 * abs(math.sin(self._phase * 2.3))
        elif self._state == State.THINKING:
            self._target_level = 0.6
        else:
            self._target_level = 0.22
        # suavizado del nivel
        self._level += (self._target_level - self._level) * 0.2
        self.update()

    def _ring_color(self) -> QtGui.QColor:
        if self._state in (State.IDLE, State.LISTENING):
            return _iridescent(self._phase)
        return _STATE_COLOR[self._state]

    # --- pintura ---
    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        w = self.width()
        cx, cy = w / 2.0, w / 2.0
        c = self._ring_color()
        r_core = w * 0.30

        # 1) halo exterior difuso (varias capas)
        pulse = 1.0 + 0.16 * self._level
        for i, alpha in ((22, 26), (14, 55), (7, 110)):
            glow = QtGui.QColor(c)
            glow.setAlpha(alpha)
            p.setPen(QtGui.QPen(glow, i))
            p.setBrush(QtCore.Qt.NoBrush)
            rr = r_core * pulse + i * 0.9
            p.drawEllipse(QtCore.QPointF(cx, cy), rr, rr)

        # 2) anillo exterior segmentado (estética tech), girando lento
        p.save()
        p.translate(cx, cy)
        p.rotate((self._phase * 18) % 360)
        seg_r = r_core * 1.55
        seg = QtGui.QColor(c)
        seg.setAlpha(150)
        p.setPen(QtGui.QPen(seg, 2))
        for k in range(24):
            if k % 2 == 0:
                ang = math.radians(k * 15)
                x1, y1 = seg_r * math.cos(ang), seg_r * math.sin(ang)
                x2, y2 = (seg_r + 5) * math.cos(ang), (seg_r + 5) * math.sin(ang)
                p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))
        p.restore()

        # 3) núcleo de vidrio con el avatar recortado en círculo
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QPointF(cx, cy), r_core, r_core)
        p.setClipPath(path)
        if self._avatar is not None:
            p.drawPixmap(int(cx - r_core), int(cy - r_core),
                         int(r_core * 2), int(r_core * 2),
                         self._avatar, 0, 0, self._avatar.width(), self._avatar.height())
        else:
            grad = QtGui.QRadialGradient(cx, cy, r_core)
            grad.setColorAt(0, VIOLET)
            grad.setColorAt(1, BG)
            p.fillRect(self.rect(), grad)
        # viñeta interior para dar volumen
        vig = QtGui.QRadialGradient(cx, cy * 0.8, r_core * 1.3)
        vig.setColorAt(0.55, QtGui.QColor(0, 0, 0, 0))
        vig.setColorAt(1.0, QtGui.QColor(0, 0, 0, 160))
        p.fillRect(self.rect(), vig)
        p.setClipping(False)

        # 4) anillo principal y arco de estado
        p.setBrush(QtCore.Qt.NoBrush)
        rim = QtGui.QColor(c)
        rim.setAlpha(230)
        p.setPen(QtGui.QPen(rim, 3.2))
        p.drawEllipse(QtCore.QPointF(cx, cy), r_core, r_core)

        if self._state == State.THINKING:
            p.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 3.5, QtCore.Qt.SolidLine,
                                QtCore.Qt.RoundCap))
            start = int((self._phase * 170 / math.pi) % 360) * 16
            p.drawArc(QtCore.QRectF(cx - r_core, cy - r_core, r_core * 2, r_core * 2),
                      start, 100 * 16)
        elif self._state in (State.SPEAKING, State.LISTENING):
            # onda reactiva alrededor del núcleo
            p.setPen(QtGui.QPen(c, 2))
            pts = 48
            amp = 5 + 9 * self._level
            poly = QtGui.QPolygonF()
            for k in range(pts + 1):
                a = 2 * math.pi * k / pts
                rr = r_core + 7 + amp * math.sin(a * 6 + self._phase * 4)
                poly.append(QtCore.QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a)))
            p.drawPolyline(poly)
        elif self._state in (State.CONFIRM, State.CRITICAL, State.DENIED):
            # anillo de alerta doble
            p.setPen(QtGui.QPen(c, 2))
            p.drawEllipse(QtCore.QPointF(cx, cy), r_core + 7, r_core + 7)

    # --- arrastre y clic (Wayland: lo mueve el compositor vía startSystemMove) ---
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            self._maybe_click = True
            self._moving = False
            self._press_pos = ev.globalPosition().toPoint()

    def mouseMoveEvent(self, ev):
        if getattr(self, "_maybe_click", False) and not getattr(self, "_moving", False):
            moved = (ev.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if moved > 6:
                self._moving = True
                self._maybe_click = False
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemMove()  # el compositor arrastra la ventana

    def mouseReleaseEvent(self, ev):
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if getattr(self, "_maybe_click", False):
            self._maybe_click = False
            self.clicked.emit()
