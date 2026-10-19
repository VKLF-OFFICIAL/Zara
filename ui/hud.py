"""Interfaz Holográfica HUD Táctica (ZARA).

Esfera flotante sin bordes (FramelessWindowHint), translúcida, siempre encima,
con el avatar de Zara recortado en círculo y reactividad por estado (IDLE,
LISTENING, THINKING/TOOL, SPEAKING) y nivel RMS del micrófono/voz.

Al hacer clic sobre la bola (sin arrastrar) se despliega un pequeño panel
táctico lateral con consola de eventos, entrada de texto y controles rápidos
(micrófono, interrumpir, cerebro LLM activo, salir).
"""

from __future__ import annotations
import math
from enum import Enum
from pathlib import Path
from typing import Callable, Optional
from PySide6 import QtCore, QtGui, QtWidgets

from config import VERSION_FULL
from ui.geometry import orb_layout

SEGMENT_DOT_GAP = 12  # separación del punto "panel abierto" respecto al anillo segmentado


class HUDState(Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"


# [DISEÑO] Paleta táctica definitiva de Zara (revisión visual con ChatGPT como
# diseñador: mismo espíritu cian/violeta/rosa, pero fondos más sobrios y
# acentos reservados para lo importante, en vez de neón por todas partes).
CYAN = QtGui.QColor("#2EF2E6")       # Acento / IDLE
VIOLET = QtGui.QColor("#8878FF")     # Procesando (THINKING)
PINK = QtGui.QColor("#FF5CB8")       # Voz de Zara (SPEAKING)
GREEN = QtGui.QColor("#34D399")      # Escuchando (LISTENING)
BG = QtGui.QColor("#080B12")         # Fondo principal
PANEL_BG = QtGui.QColor("#101725")   # Paneles y tarjetas
SURFACE = QtGui.QColor("#172438")    # Superficie elevada
TEXT_MAIN = QtGui.QColor("#E5EDF7")  # Texto principal
MUTED_GRAY = QtGui.QColor("#64748B")

_STATE_COLOR = {
    HUDState.IDLE: CYAN,
    HUDState.LISTENING: GREEN,
    HUDState.THINKING: VIOLET,
    HUDState.SPEAKING: PINK,
}

_STATE_TEXT = {
    HUDState.IDLE: "EN ESPERA",
    HUDState.LISTENING: "ESCUCHANDO",
    HUDState.THINKING: "PROCESANDO",
    HUDState.SPEAKING: "RESPONDIENDO",
}
_STATE_BY_TEXT = {text: state for state, text in _STATE_TEXT.items()}

# Rutas candidatas para el retrato de Zara (relativas a la raíz del proyecto).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
AVATAR_CANDIDATES = [
    str(_PROJECT_ROOT / "config" / "avatar.png"),
    str(_PROJECT_ROOT / "config" / "zara-icon.png"),
]
APP_ICON_CANDIDATES = [
    str(_PROJECT_ROOT / "config" / "zara-icon.ico"),
    str(_PROJECT_ROOT / "config" / "zara-icon.png"),
]

# [DISEÑO] Pilas de fuentes con fallback seguro: Qt recorre la lista y usa la primera
# instalada. Segoe UI Variable es la fuente nativa de Windows 11 (aspecto profesional);
# Inter/Segoe UI cubren Windows 10 y Linux. La monoespaciada solo se usa en chips técnicos.
_FONT_UI = "'Segoe UI Variable Text', 'Segoe UI', 'Inter', 'Noto Sans', sans-serif"
_FONT_MONO = "'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace"

PANEL_W, PANEL_H = 400, 620

_PANEL_STYLE = f"""
* {{ font-family: {_FONT_UI}; }}
QFrame#bg {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0E1422, stop:1 #0A0F1A);
    border: 1px solid rgba(148, 163, 184, 38);
    border-radius: 18px;
}}
QLabel {{ color: #94A3B8; font-size: 12px; border: none; background: transparent; }}
QLabel#title {{ color: #F1F5F9; font-size: 15px; font-weight: 600; }}
QLabel#status {{ color: #94A3B8; font-size: 11px; }}
QLabel#caption {{ color: #8391A7; font-size: 11px; font-weight: 600; }}
QLabel#version {{ color: #475569; font-size: 10px; }}
QFrame#divider {{ background: rgba(148, 163, 184, 26); border: none; max-height: 1px; min-height: 1px; }}
QFrame#card {{
    background: rgba(23, 34, 52, 150);
    border: 1px solid rgba(148, 163, 184, 26);
    border-radius: 12px;
}}
QPushButton#iconBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 15px;
    padding: 0px;
}}
QPushButton#iconBtn:hover {{ background: rgba(148, 163, 184, 30); border: 1px solid rgba(148, 163, 184, 45); }}
QPushButton#iconBtn:pressed {{ background: rgba(46, 242, 230, 40); }}
QPushButton#sendBtn {{
    border: none;
    border-radius: 17px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #22D3EE, stop:1 #8878FF);
}}
QPushButton#sendBtn:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #67E8F9, stop:1 #A397FF); }}
QPushButton#sendBtn:disabled {{ background: #1E293B; }}
QFrame#composer {{
    background: #121A2A;
    border: 1px solid rgba(148, 163, 184, 45);
    border-radius: 19px;
}}
QLineEdit {{
    background: #121A2A;
    color: #E2E8F0;
    border: 1px solid rgba(148, 163, 184, 45);
    border-radius: 10px;
    padding: 7px 10px;
    font-size: 12px;
    selection-background-color: rgba(46, 242, 230, 70);
}}
QLineEdit:focus {{ border: 1px solid rgba(46, 242, 230, 150); }}
QLineEdit#composerInput {{ background: transparent; border: none; padding: 8px 4px 8px 14px; font-size: 13px; }}
QComboBox {{
    background: #121A2A;
    color: #E2E8F0;
    border: 1px solid rgba(148, 163, 184, 45);
    border-radius: 10px;
    padding: 6px 26px 6px 10px;
    font-size: 12px;
}}
QComboBox:hover {{ border: 1px solid rgba(148, 163, 184, 90); }}
QComboBox:focus {{ border: 1px solid rgba(46, 242, 230, 150); }}
QComboBox:disabled {{ color: #64748B; background: #0F1624; border: 1px solid rgba(148, 163, 184, 25); }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 22px; border: none; }}
QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; border: none; }}
QComboBox QAbstractItemView {{
    background: #121A2A;
    color: #E2E8F0;
    border: 1px solid rgba(148, 163, 184, 60);
    border-radius: 8px;
    selection-background-color: rgba(46, 242, 230, 45);
    selection-color: #FFFFFF;
    font-size: 12px;
    outline: none;
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 4px 8px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:selected {{ background: rgba(46, 242, 230, 45); color: #FFFFFF; }}
QScrollArea#chat, QWidget#chatInner {{ background: transparent; border: none; }}
QFrame#bubbleUser {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0E7490, stop:1 #4C3FA8);
    border-radius: 14px;
    border-bottom-right-radius: 4px;
}}
QFrame#bubbleZara {{
    background: #172234;
    border: 1px solid rgba(148, 163, 184, 30);
    border-radius: 14px;
    border-bottom-left-radius: 4px;
}}
QFrame#bubbleConfirm {{
    background: rgba(245, 158, 11, 28);
    border: 1px solid rgba(245, 158, 11, 120);
    border-radius: 12px;
}}
QLabel#bubbleText {{ color: #E8EEF6; font-size: 13px; }}
QLabel#bubbleTextUser {{ color: #FFFFFF; font-size: 13px; }}
QLabel#bubbleTime {{ color: rgba(226, 232, 240, 120); font-size: 10px; }}
QLabel#confirmTitle {{ color: #FBBF24; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
QLabel#chipTool {{
    color: #A5B4FC;
    background: rgba(136, 120, 255, 26);
    border: 1px solid rgba(136, 120, 255, 70);
    border-radius: 10px;
    padding: 3px 10px;
    font-family: {_FONT_MONO};
    font-size: 10px;
}}
QLabel#chipToolDone {{
    color: #6EE7B7;
    background: rgba(52, 211, 153, 20);
    border: 1px solid rgba(52, 211, 153, 60);
    border-radius: 10px;
    padding: 3px 10px;
    font-family: {_FONT_MONO};
    font-size: 10px;
}}
QLabel#chipError {{
    color: #FCA5A5;
    background: rgba(248, 113, 113, 24);
    border: 1px solid rgba(248, 113, 113, 80);
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
}}
QLabel#systemNote {{ color: #64748B; font-size: 10.5px; }}
QLabel#emptyState {{ color: #475569; font-size: 12px; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(148, 163, 184, 70); border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(148, 163, 184, 130); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QToolTip {{ color: #E2E8F0; background: #121A2A; border: 1px solid rgba(148, 163, 184, 60); padding: 4px 6px; }}
"""


def uses_native_wayland() -> bool:
    """True si Qt corre con el plugin wayland nativo (no XWayland).

    Ahí un cliente no puede colocar su ventana: move() se ignora y el arrastre lo hace
    el compositor con startSystemMove(). En Windows, X11 y XWayland es False.
    """
    return QtGui.QGuiApplication.platformName().lower().startswith("wayland")


def _lerp(a: QtGui.QColor, b: QtGui.QColor, t: float) -> QtGui.QColor:
    t = max(0.0, min(1.0, t))
    return QtGui.QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def _iridescent(phase: float) -> QtGui.QColor:
    """Color que recorre cian → violeta → rosa → cian (estética glitch) para IDLE."""
    x = (math.sin(phase) + 1) / 2
    if x < 0.5:
        return _lerp(CYAN, VIOLET, x * 2)
    return _lerp(VIOLET, PINK, (x - 0.5) * 2)


def format_ollama_model_display(full_name: str, max_len: int = 36) -> str:
    """Formatea inteligentemente el nombre de un modelo de Ollama para el HUD.

    Estructura Ollama: [host/][namespace/]model_base[:tag]
    Principios de diseño:
      1. El tag (:7b, :14b, :Q3_K_M, :latest) es el discriminador esencial:
         NUNCA se trunca para que modelos hermanos sean siempre identificables.
      2. Los prefijos de host (ej: 'hf.co/', 'huggingface.co/') y namespace se
         omiten de la etiqueta principal para aprovechar el ancho visual.
      3. Si el nombre base + tag cabe en max_len, se muestra completo.
      4. Si excede max_len, se recorta la parte derecha del nombre base con '…',
         manteniendo el tag íntegro al final (ej: 'qwen2.5-coder-abli…:14b-instruct').
    """
    name = (full_name or "").strip()
    if not name:
        return ""

    tag = ""
    if ":" in name:
        base, tag = name.rsplit(":", 1)
    else:
        base = name

    parts = [p for p in base.split("/") if p]
    model_base = parts[-1] if parts else base

    tag_suffix = f":{tag}" if tag else ""
    full_display = f"{model_base}{tag_suffix}"

    if len(full_display) <= max_len:
        return full_display

    tag_len = len(tag_suffix)
    avail = max_len - tag_len - 1  # 1 para '…'
    if avail >= 6:
        return f"{model_base[:avail]}…{tag_suffix}"

    return full_display[:max_len - 1] + "…"


# =============================================================================
# [DISEÑO] Iconos vectoriales dibujados a mano (QPainter), sin emojis ni
# dependencias externas de SVG/fuentes de iconos. Cada función pinta un
# glifo simple y reconocible sobre un QPixmap transparente que luego se usa
# como QIcon en los botones del panel.
# =============================================================================

def _draw_mic_icon(p: QtGui.QPainter, rect: QtCore.QRectF, color: QtGui.QColor) -> None:
    cx, cy = rect.center().x(), rect.center().y()
    w, h = rect.width(), rect.height()
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(color)
    capsule = QtCore.QRectF(cx - w * 0.14, cy - h * 0.32, w * 0.28, h * 0.42)
    p.drawRoundedRect(capsule, capsule.width() / 2, capsule.width() / 2)
    pen = QtGui.QPen(color, w * 0.07, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    stand = QtCore.QRectF(cx - w * 0.22, cy - h * 0.12, w * 0.44, h * 0.4)
    p.drawArc(stand, 0, -180 * 16)
    p.drawLine(QtCore.QPointF(cx, cy + h * 0.28), QtCore.QPointF(cx, cy + h * 0.38))
    p.drawLine(QtCore.QPointF(cx - w * 0.16, cy + h * 0.38), QtCore.QPointF(cx + w * 0.16, cy + h * 0.38))


def _draw_mic_off_icon(p: QtGui.QPainter, rect: QtCore.QRectF, color: QtGui.QColor) -> None:
    _draw_mic_icon(p, rect, color)
    pen = QtGui.QPen(QtGui.QColor("#F87171"), rect.width() * 0.09, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
    p.setPen(pen)
    d = rect.width() * 0.32
    cx, cy = rect.center().x(), rect.center().y()
    p.drawLine(QtCore.QPointF(cx - d, cy - d), QtCore.QPointF(cx + d, cy + d))


def _draw_stop_icon(p: QtGui.QPainter, rect: QtCore.QRectF, color: QtGui.QColor) -> None:
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(color)
    w, h = rect.width(), rect.height()
    sq = QtCore.QRectF(rect.center().x() - w * 0.2, rect.center().y() - h * 0.2, w * 0.4, h * 0.4)
    p.drawRoundedRect(sq, w * 0.06, w * 0.06)


def _draw_power_icon(p: QtGui.QPainter, rect: QtCore.QRectF, color: QtGui.QColor) -> None:
    w, h = rect.width(), rect.height()
    cx, cy = rect.center().x(), rect.center().y()
    pen = QtGui.QPen(color, w * 0.09, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    arc_rect = QtCore.QRectF(cx - w * 0.26, cy - h * 0.26, w * 0.52, h * 0.52)
    p.drawArc(arc_rect, (90 + 35) * 16, (360 - 70) * 16)
    p.drawLine(QtCore.QPointF(cx, cy - h * 0.34), QtCore.QPointF(cx, cy))


def _draw_close_icon(p: QtGui.QPainter, rect: QtCore.QRectF, color: QtGui.QColor) -> None:
    w = rect.width()
    cx, cy = rect.center().x(), rect.center().y()
    p.setPen(QtGui.QPen(color, w * 0.08, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    d = w * 0.2
    p.drawLine(QtCore.QPointF(cx - d, cy - d), QtCore.QPointF(cx + d, cy + d))
    p.drawLine(QtCore.QPointF(cx - d, cy + d), QtCore.QPointF(cx + d, cy - d))


def _draw_send_icon(p: QtGui.QPainter, rect: QtCore.QRectF, color: QtGui.QColor) -> None:
    """Flecha hacia arriba (enviar), estilo de los chats modernos."""
    w = rect.width()
    cx, cy = rect.center().x(), rect.center().y()
    p.setPen(QtGui.QPen(color, w * 0.1, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    p.drawLine(QtCore.QPointF(cx, cy + w * 0.24), QtCore.QPointF(cx, cy - w * 0.22))
    p.drawLine(QtCore.QPointF(cx - w * 0.18, cy - w * 0.04), QtCore.QPointF(cx, cy - w * 0.22))
    p.drawLine(QtCore.QPointF(cx + w * 0.18, cy - w * 0.04), QtCore.QPointF(cx, cy - w * 0.22))


def circular_pixmap(path: str, size: int) -> Optional[QtGui.QPixmap]:
    """Carga una imagen y la devuelve recortada en círculo (para el avatar de la cabecera)."""
    src = QtGui.QPixmap(path)
    if src.isNull():
        return None
    scaled = src.scaled(size * 2, size * 2, QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)
    out = QtGui.QPixmap(size * 2, size * 2)
    out.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(out)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    path_c = QtGui.QPainterPath()
    path_c.addEllipse(0, 0, size * 2, size * 2)
    p.setClipPath(path_c)
    p.drawPixmap(0, 0, scaled)
    p.end()
    out.setDevicePixelRatio(2.0)  # nítido en pantallas HiDPI
    return out


def build_icon(draw_fn, color: QtGui.QColor, px: int = 40) -> QtGui.QIcon:
    """Renderiza un glifo vectorial en un QPixmap transparente de `px`x`px`."""
    pm = QtGui.QPixmap(px, px)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    draw_fn(p, QtCore.QRectF(0, 0, px, px), color)
    p.end()
    return QtGui.QIcon(pm)


class TacticalComboBox(QtWidgets.QComboBox):
    """ComboBox táctico adaptado a paneles holográficos flotantes.

    1. Ignora la rueda de ratón (wheelEvent) cuando está cerrado para evitar cambios
       accidentales de modelo/cerebro al hacer scroll sobre el chat o la consola.
    2. Fuerza elidado central (ElideMiddle) y desactiva el scroll horizontal en el popup.
    3. Asegura altura y espaciado táctil/clickeable adecuado.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMaxVisibleItems(10)
        view = self.view()
        view.setTextElideMode(QtCore.Qt.ElideMiddle)
        view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        # Texto abreviado POR EL MEDIO: así un modelo largo conserva su etiqueta final
        # (p. ej. "qwen2.5-co…der:7b") en vez de cortarse justo donde están las diferencias.
        painter = QtWidgets.QStylePainter(self)
        opt = QtWidgets.QStyleOptionComboBox()
        self.initStyleOption(opt)
        painter.drawComplexControl(QtWidgets.QStyle.CC_ComboBox, opt)
        field = self.style().subControlRect(
            QtWidgets.QStyle.CC_ComboBox, opt, QtWidgets.QStyle.SC_ComboBoxEditField, self
        )
        opt.currentText = self.fontMetrics().elidedText(opt.currentText, QtCore.Qt.ElideMiddle, max(10, field.width() - 4))
        painter.drawControl(QtWidgets.QStyle.CE_ComboBoxLabel, opt)
        painter.end()
        # Chevron vectorial: la flecha por CSS se ve como un cuadrado en varios estilos de Qt.
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        color = QtGui.QColor("#94A3B8" if self.isEnabled() else "#475569")
        p.setPen(QtGui.QPen(color, 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        cx, cy = self.width() - 14, self.height() / 2 + 1
        p.drawPolyline(QtGui.QPolygonF([
            QtCore.QPointF(cx - 4, cy - 2), QtCore.QPointF(cx, cy + 2), QtCore.QPointF(cx + 4, cy - 2),
        ]))
        p.end()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802
        if not self.view().isVisible():
            event.ignore()
        else:
            super().wheelEvent(event)


class ChatView(QtWidgets.QScrollArea):
    """Conversación en burbujas: Señor VK a la derecha, Zara a la izquierda, y los eventos
    del sistema (herramientas, avisos, errores) como chips discretos centrados.

    Todo el texto se pinta como TEXTO PLANO: la salida de un modelo nunca se interpreta
    como HTML (evita etiquetas inyectadas y textos que "desaparecen").
    """

    MAX_ITEMS = 200
    BUBBLE_MAX_W = 268

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("chat")
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        self._inner = QtWidgets.QWidget()
        self._inner.setObjectName("chatInner")
        self._layout = QtWidgets.QVBoxLayout(self._inner)
        self._layout.setContentsMargins(2, 6, 6, 6)
        self._layout.setSpacing(8)
        # Sin esto, el QScrollArea encogía el contenido al alto visible (Qt solo impone el
        # mínimo del layout en ventanas) y aplastaba las burbujas en vez de hacer scroll.
        self._layout.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
        self._layout.addStretch(1)  # empuja los mensajes hacia abajo, como un chat real
        self.setWidget(self._inner)

        self._empty = QtWidgets.QLabel("Di «Zara» o escribe abajo para empezar.")
        self._empty.setObjectName("emptyState")
        self._empty.setAlignment(QtCore.Qt.AlignCenter)
        self._layout.addWidget(self._empty)

        self._rows: list[QtWidgets.QWidget] = []
        self._to_fit: list[QtWidgets.QLabel] = []
        self._pending_tools: dict[str, QtWidgets.QLabel] = {}
        self._avatar_small: Optional[QtGui.QPixmap] = None
        for candidate in AVATAR_CANDIDATES:
            if Path(candidate).is_file():
                self._avatar_small = circular_pixmap(candidate, 22)
                if self._avatar_small is not None:
                    break

        # Al crecer el contenido, bajar al último mensaje.
        self.verticalScrollBar().rangeChanged.connect(
            lambda _min, _max: self.verticalScrollBar().setValue(_max)
        )

    # --- construcción de filas ---
    def _text_label(self, text: str, object_name: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName(object_name)
        lbl.setTextFormat(QtCore.Qt.PlainText)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lbl.setMaximumWidth(self.BUBBLE_MAX_W - 24)
        self._to_fit.append(lbl)  # se dimensiona al insertarlo (ver _fit_label)
        return lbl

    def _fit_label(self, lbl: QtWidgets.QLabel, max_w: int) -> None:
        """Ancho natural del texto (hasta `max_w`) y altura exacta para ese ancho.

        Debe llamarse con la etiqueta ya dentro del panel, para medir con la fuente real de
        la hoja de estilos. Sin esto, Qt partía las líneas demasiado pronto y, dentro de un
        QScrollArea, aplastaba las burbujas (texto cortado) en vez de mostrar el scroll.
        """
        lbl.ensurePolished()
        fm = lbl.fontMetrics()
        natural = max((fm.horizontalAdvance(line) for line in lbl.text().splitlines() or [""]), default=0)
        width = max(1, min(max_w, natural + 2))
        lbl.setFixedWidth(width)
        lbl.setMinimumHeight(lbl.heightForWidth(width))

    def _bubble(self, text: str, user: bool) -> QtWidgets.QFrame:
        bubble = QtWidgets.QFrame()
        bubble.setObjectName("bubbleUser" if user else "bubbleZara")
        bubble.setMaximumWidth(self.BUBBLE_MAX_W)
        lay = QtWidgets.QVBoxLayout(bubble)
        lay.setContentsMargins(12, 8, 12, 6)
        lay.setSpacing(2)
        lay.addWidget(self._text_label(text, "bubbleTextUser" if user else "bubbleText"))
        stamp = QtWidgets.QLabel(QtCore.QTime.currentTime().toString("HH:mm"))
        stamp.setObjectName("bubbleTime")
        stamp.setAlignment(QtCore.Qt.AlignRight)
        lay.addWidget(stamp)
        return bubble

    def _append_row(self, row: QtWidgets.QWidget) -> None:
        if self._empty is not None:
            self._empty.hide()
            self._layout.removeWidget(self._empty)
            self._empty.deleteLater()
            self._empty = None
        self._layout.addWidget(row)
        self._rows.append(row)
        for lbl in self._to_fit:
            self._fit_label(lbl, lbl.maximumWidth())
        self._to_fit.clear()
        while len(self._rows) > self.MAX_ITEMS:
            old = self._rows.pop(0)
            self._layout.removeWidget(old)
            old.deleteLater()

    def _centered(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        lay.addWidget(widget)
        lay.addStretch(1)
        return row

    # --- API ---
    def add_user(self, text: str) -> None:
        row = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(row)
        lay.setContentsMargins(40, 0, 0, 0)
        lay.addStretch(1)
        lay.addWidget(self._bubble(text, user=True))
        self._append_row(row)

    def add_assistant(self, text: str) -> None:
        row = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 40, 0)
        lay.setSpacing(6)
        if self._avatar_small is not None:
            av = QtWidgets.QLabel()
            av.setPixmap(self._avatar_small)
            av.setFixedSize(22, 22)
            lay.addWidget(av, 0, QtCore.Qt.AlignBottom)
        lay.addWidget(self._bubble(text, user=False))
        lay.addStretch(1)
        self._append_row(row)

    def add_tool(self, tool_name: str, desc: str) -> None:
        """Un chip por herramienta: «ejecutando…» se transforma en «✓» al completarse."""
        done = desc.strip().lower().startswith("completad")
        pending = self._pending_tools.pop(tool_name, None)
        if done and pending is not None:
            pending.setText(f"✓  {tool_name}")
            pending.setObjectName("chipToolDone")
            pending.style().unpolish(pending)
            pending.style().polish(pending)
            return
        chip = QtWidgets.QLabel(f"✓  {tool_name}" if done else f"⋯  {tool_name}")
        chip.setObjectName("chipToolDone" if done else "chipTool")
        chip.setTextFormat(QtCore.Qt.PlainText)
        chip.setToolTip(desc)
        if not done:
            self._pending_tools[tool_name] = chip
        self._append_row(self._centered(chip))

    def add_confirm(self, text: str) -> None:
        card = QtWidgets.QFrame()
        card.setObjectName("bubbleConfirm")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(3)
        title = QtWidgets.QLabel("CONFIRMACIÓN NECESARIA")
        title.setObjectName("confirmTitle")
        lay.addWidget(title)
        lay.addWidget(self._text_label(text, "bubbleText"))
        hint = QtWidgets.QLabel("Responda «sí» o «no» por voz, o escríbalo abajo.")
        hint.setObjectName("bubbleTime")
        lay.addWidget(hint)
        self._append_row(card)

    def add_error(self, text: str) -> None:
        chip = QtWidgets.QLabel(text)
        chip.setObjectName("chipError")
        chip.setTextFormat(QtCore.Qt.PlainText)
        chip.setWordWrap(True)
        chip.setMaximumWidth(self.BUBBLE_MAX_W + 40)
        self._to_fit.append(chip)
        self._append_row(self._centered(chip))

    def add_system(self, text: str) -> None:
        note = QtWidgets.QLabel(text)
        note.setObjectName("systemNote")
        note.setTextFormat(QtCore.Qt.PlainText)
        note.setWordWrap(True)
        note.setAlignment(QtCore.Qt.AlignCenter)
        note.setMaximumWidth(self.BUBBLE_MAX_W + 40)
        self._to_fit.append(note)
        self._append_row(self._centered(note))

    def count(self) -> int:
        return len(self._rows)


class ZaraPanel(QtWidgets.QWidget):
    """Panel táctico desplegable: consola de eventos, entrada de texto y controles rápidos."""

    mic_toggle_requested = QtCore.Signal()
    interrupt_requested = QtCore.Signal()
    brain_changed = QtCore.Signal(str)
    model_changed = QtCore.Signal(str, str)  # (provider, model)
    # Pide volver a consultar los modelos instalados/publicados (al abrir el panel o cambiar de
    # cerebro): un modelo nuevo aparece en la lista sin reiniciar Zara.
    models_refresh_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()
    text_submitted = QtCore.Signal(str)
    close_requested = QtCore.Signal()

    _BRAIN_OPTIONS = [
        ("Auto", "auto"),
        ("Gemini Live", "gemini_live"),
        ("Ollama (local)", "ollama"),
        ("Claude", "claude"),
        ("Gemini", "gemini"),
        ("Gemini · AI Pro", "antigravity"),
    ]
    _ANTIGRAVITY_MODEL_OPTIONS = [
        ("Por defecto", ""),
    ]
    _GEMINI_LIVE_MODEL_OPTIONS = [
        ("Auto (más reciente)", "auto"),
        ("Gemini 2.5 Flash Live", "gemini-2.5-flash-native-audio-preview-12-2025"),
    ]
    _CLAUDE_MODEL_OPTIONS = [
        ("Por defecto", ""),
        ("Sonnet", "sonnet"),
        ("Opus", "opus"),
        ("Haiku", "haiku"),
        ("Fable", "fable"),
    ]
    _GEMINI_MODEL_OPTIONS = [
        ("Auto (más reciente)", "auto"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
    ]

    def __init__(self):
        super().__init__()
        self._ollama_models: list[str] = []
        self._gemini_models: list[str] = []
        self._gemini_live_models: list[str] = []
        self._desired_model: dict[str, str] = {}
        self.setFixedSize(PANEL_W, PANEL_H)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        bg = QtWidgets.QFrame(self)
        bg.setObjectName("bg")
        self.setStyleSheet(_PANEL_STYLE)

        # Sombra suave para dar profundidad, un único efecto para todo el panel.
        shadow = QtWidgets.QGraphicsDropShadowEffect(bg)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 170))
        bg.setGraphicsEffect(shadow)
        outer.addWidget(bg)

        layout = QtWidgets.QVBoxLayout(bg)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        # 1. Cabecera: avatar + nombre + estado | acciones rápidas
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        self.avatar_lbl = QtWidgets.QLabel()
        self.avatar_lbl.setFixedSize(36, 36)
        for candidate in AVATAR_CANDIDATES:
            if Path(candidate).is_file():
                pm = circular_pixmap(candidate, 36)
                if pm is not None:
                    self.avatar_lbl.setPixmap(pm)
                    break
        header.addWidget(self.avatar_lbl)

        name_box = QtWidgets.QVBoxLayout()
        name_box.setSpacing(1)
        title = QtWidgets.QLabel("Zara")
        title.setObjectName("title")
        name_box.addWidget(title)
        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(6)
        self.status_dot = QtWidgets.QLabel()
        self.status_dot.setFixedSize(8, 8)
        status_row.addWidget(self.status_dot, 0, QtCore.Qt.AlignVCenter)
        self.status_lbl = QtWidgets.QLabel()
        self.status_lbl.setObjectName("status")
        status_row.addWidget(self.status_lbl)
        status_row.addStretch(1)
        name_box.addLayout(status_row)
        header.addLayout(name_box, stretch=1)

        def _icon_button(draw_fn, color, tooltip) -> QtWidgets.QPushButton:
            btn = QtWidgets.QPushButton()
            btn.setObjectName("iconBtn")
            btn.setFixedSize(30, 30)
            btn.setIcon(build_icon(draw_fn, color, px=36))
            btn.setIconSize(QtCore.QSize(20, 20))
            btn.setToolTip(tooltip)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            return btn

        self.mic_btn = _icon_button(_draw_mic_icon, CYAN, "Micrófono: activar / silenciar")
        self.mic_btn.clicked.connect(self.mic_toggle_requested.emit)
        interrupt_btn = _icon_button(_draw_stop_icon, QtGui.QColor("#F87171"), "Parar la respuesta de Zara")
        interrupt_btn.clicked.connect(self.interrupt_requested.emit)
        quit_btn = _icon_button(_draw_power_icon, QtGui.QColor("#A5B4FC"), "Cerrar Zara por completo")
        quit_btn.clicked.connect(self.quit_requested.emit)
        close_btn = _icon_button(_draw_close_icon, QtGui.QColor("#94A3B8"), "Ocultar panel (Zara sigue activa)")
        close_btn.clicked.connect(self.close_requested.emit)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(2)
        for btn in (self.mic_btn, interrupt_btn, quit_btn, close_btn):
            actions.addWidget(btn)
        header.addLayout(actions)
        layout.addLayout(header)

        # 2. Selectores de IA dentro de una tarjeta compacta
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card_lay = QtWidgets.QGridLayout(card)
        card_lay.setContentsMargins(12, 10, 12, 10)
        card_lay.setHorizontalSpacing(10)
        card_lay.setVerticalSpacing(4)
        brain_lbl = QtWidgets.QLabel("CEREBRO")
        brain_lbl.setObjectName("caption")
        self.model_lbl = QtWidgets.QLabel("MODELO")
        self.model_lbl.setObjectName("caption")
        card_lay.addWidget(brain_lbl, 0, 0)
        card_lay.addWidget(self.model_lbl, 0, 1)

        self.brain_combo = TacticalComboBox()
        for label, value in self._BRAIN_OPTIONS:
            self.brain_combo.addItem(label, value)
        self.brain_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.brain_combo.activated.connect(self._on_provider_changed)
        self.model_combo = TacticalComboBox()
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.model_combo.activated.connect(self._on_model_changed)
        card_lay.addWidget(self.brain_combo, 1, 0)
        card_lay.addWidget(self.model_combo, 1, 1)
        card_lay.setColumnStretch(0, 1)
        card_lay.setColumnStretch(1, 1)

        # Buscador de modelos locales (solo con Ollama)
        self.model_search_input = QtWidgets.QLineEdit()
        self.model_search_input.setPlaceholderText("Filtrar modelos locales (qwen, llama…)")
        self.model_search_input.setClearButtonEnabled(True)
        self.model_search_input.textChanged.connect(self._on_model_filter_changed)
        self.model_search_input.returnPressed.connect(self._on_model_filter_return_pressed)
        self.model_search_input.hide()
        card_lay.addWidget(self.model_search_input, 2, 0, 1, 2)
        layout.addWidget(card)

        self._refresh_model_options()

        # 3. Conversación
        self.chat = ChatView()
        layout.addWidget(self.chat, stretch=1)

        # 4. Caja de escritura con botón de enviar integrado
        composer = QtWidgets.QFrame()
        composer.setObjectName("composer")
        comp_lay = QtWidgets.QHBoxLayout(composer)
        comp_lay.setContentsMargins(0, 3, 4, 3)
        comp_lay.setSpacing(4)
        self.input_box = QtWidgets.QLineEdit()
        self.input_box.setObjectName("composerInput")
        self.input_box.setPlaceholderText("Escribe a Zara…")
        self.input_box.returnPressed.connect(self._submit_text)
        comp_lay.addWidget(self.input_box, stretch=1)
        self.send_btn = QtWidgets.QPushButton()
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(34, 34)
        self.send_btn.setIcon(build_icon(_draw_send_icon, QtGui.QColor("#FFFFFF"), px=36))
        self.send_btn.setIconSize(QtCore.QSize(20, 20))
        self.send_btn.setToolTip("Enviar (Intro)")
        self.send_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self._submit_text)
        self.send_btn.setEnabled(False)
        self.input_box.textChanged.connect(lambda t: self.send_btn.setEnabled(bool(t.strip())))
        comp_lay.addWidget(self.send_btn)
        layout.addWidget(composer)

        version = QtWidgets.QLabel(f"Zara v{VERSION_FULL} · Esc para ocultar")
        version.setObjectName("version")
        version.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(version)

        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self, activated=self.close_requested.emit)
        self.set_status(_STATE_TEXT[HUDState.IDLE], False)

    def _submit_text(self) -> None:
        text = self.input_box.text().strip()
        if text:
            self.text_submitted.emit(text)
            self.input_box.clear()

    def _current_provider(self) -> str:
        return self.brain_combo.currentData() or "auto"

    def showEvent(self, event) -> None:  # noqa: N802 (API de Qt)
        super().showEvent(event)
        self.models_refresh_requested.emit()

    def _on_provider_changed(self, _idx: int) -> None:
        provider = self._current_provider()
        self.brain_changed.emit(provider)
        self.models_refresh_requested.emit()
        # Limpiar filtro al cambiar proveedor
        self.model_search_input.blockSignals(True)
        self.model_search_input.clear()
        self.model_search_input.blockSignals(False)
        self._refresh_model_options()

        # Sincronizar de inmediato el modelo activo del nuevo proveedor con el backend
        current_model = self.model_combo.currentData()
        if current_model is not None and self.model_combo.isEnabled():
            if self._desired_model.get(provider) != current_model:
                self._desired_model[provider] = current_model
                self.model_changed.emit(provider, current_model)

    def _on_model_filter_changed(self, _text: str) -> None:
        self._refresh_model_options()

    def _on_model_filter_return_pressed(self) -> None:
        """Al pulsar Enter en el buscador de modelos, activa de inmediato el primer modelo filtrado."""
        if self.model_combo.count() > 0 and self.model_combo.isEnabled():
            model = self.model_combo.itemData(0)
            if model is not None:
                provider = self._current_provider()
                self.model_combo.setCurrentIndex(0)
                if self._desired_model.get(provider) != model:
                    self._desired_model[provider] = model
                    self.model_changed.emit(provider, model)
                display_name = self.model_combo.currentText()
                self.log_line("SISTEMA", f"Modelo activado: {display_name}")
                self.input_box.setFocus()

    def _on_model_changed(self, idx: int) -> None:
        if idx < 0:
            return
        model = self.model_combo.itemData(idx)
        if model is None:
            return
        provider = self._current_provider()
        # Evitar re-emisiones idénticas duplicadas (disparadas a la vez por currentIndexChanged y activated)
        if self._desired_model.get(provider) == model:
            return
        self._desired_model[provider] = model
        self.model_changed.emit(provider, model)

    def _refresh_model_options(self) -> None:
        """Repuebla el combo de modelo según el proveedor seleccionado arriba con soporte de filtrado."""
        provider = self._current_provider()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.setMaxVisibleItems(12)

        if provider == "ollama":
            self.model_search_input.show()
            filter_text = self.model_search_input.text().strip().lower()

            # Filtrar lista de modelos locales
            candidates = self._ollama_models
            if filter_text:
                candidates = [m for m in candidates if filter_text in m.lower()]

            if candidates:
                count_str = f"MODELO ({len(candidates)}/{len(self._ollama_models)})" if filter_text else f"MODELO ({len(candidates)})"
                self.model_lbl.setText(count_str)
                for name in candidates:
                    display = format_ollama_model_display(name, max_len=36)
                    self.model_combo.addItem(display, name)
                    idx = self.model_combo.count() - 1
                    self.model_combo.setItemData(idx, f"Modelo completo: {name}", QtCore.Qt.ToolTipRole)
                self.model_combo.setEnabled(True)
            else:
                self.model_lbl.setText("MODELO (0)")
                msg = "(sin coincidencias)" if filter_text else "(sin modelos detectados)"
                self.model_combo.addItem(msg, "")
                self.model_combo.setEnabled(False)
        else:
            self.model_search_input.hide()
            if provider == "antigravity":
                self.model_lbl.setText("MODELO")
                for label, value in self._ANTIGRAVITY_MODEL_OPTIONS:
                    self.model_combo.addItem(label, value)
                self.model_combo.setEnabled(True)
            elif provider == "claude":
                self.model_lbl.setText("MODELO")
                for label, value in self._CLAUDE_MODEL_OPTIONS:
                    self.model_combo.addItem(label, value)
                self.model_combo.setEnabled(True)
            elif provider == "gemini_live":
                self.model_lbl.setText("MODELO")
                self._add_options(self._GEMINI_LIVE_MODEL_OPTIONS, self._gemini_live_models)
                self.model_combo.setEnabled(True)
            elif provider == "gemini":
                self.model_lbl.setText("MODELO")
                self._add_options(self._GEMINI_MODEL_OPTIONS, self._gemini_models)
                self.model_combo.setEnabled(True)
            else:  # auto
                self.model_lbl.setText("MODELO")
                self.model_combo.addItem("El sistema elige", "")
                self.model_combo.setEnabled(False)

        desired = self._desired_model.get(provider)
        if desired is not None:
            self._select_model_item(provider, desired)

        self.model_combo.blockSignals(False)

    def _add_options(self, fixed: list[tuple[str, str]], discovered: list[str]) -> None:
        """Opciones fijas y, detrás, los modelos descubiertos en la API que no estén ya."""
        for label, value in fixed:
            self.model_combo.addItem(label, value)
        known = {value for _label, value in fixed}
        for name in discovered:
            if name not in known:
                known.add(name)
                self.model_combo.addItem(name, name)

    def _select_model_item(self, provider: str, model: str) -> None:
        """Selecciona `model` en el combo; si no está en la lista (p. ej. un modelo escrito en
        .env o publicado después), se añade para que el panel muestre el que de verdad se usa."""
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == model:
                self.model_combo.setCurrentIndex(i)
                return
        # En Ollama solo se listan los modelos instalados (o los filtrados): no se inventan.
        if model and provider not in ("ollama", "auto") and self.model_combo.isEnabled():
            self.model_combo.addItem(model, model)
            self.model_combo.setCurrentIndex(self.model_combo.count() - 1)

    # --- API pública ---
    def set_status(self, state_text: str, muted: bool) -> None:
        mic_text = "Mic pausado" if muted else "Mic activo"
        self.status_lbl.setText(f"{state_text.capitalize()} · {mic_text}")
        color = MUTED_GRAY if muted else _STATE_COLOR.get(_STATE_BY_TEXT.get(state_text, HUDState.IDLE), CYAN)
        self.status_dot.setStyleSheet(f"background: {color.name()}; border-radius: 4px;")

    def set_muted_visual(self, muted: bool) -> None:
        icon_fn = _draw_mic_off_icon if muted else _draw_mic_icon
        self.mic_btn.setIcon(build_icon(icon_fn, MUTED_GRAY if muted else CYAN, px=36))
        self.mic_btn.setToolTip("Micrófono silenciado: pulse para reactivar" if muted else "Micrófono: activar / silenciar")

    def set_brain(self, value: str) -> None:
        for i in range(self.brain_combo.count()):
            if self.brain_combo.itemData(i) == value:
                self.brain_combo.blockSignals(True)
                self.brain_combo.setCurrentIndex(i)
                self.brain_combo.blockSignals(False)
                break
        self._refresh_model_options()

    def set_ollama_models(self, models: list[str], current: str = "") -> None:
        """Rellena el combo de modelos de Ollama con los modelos realmente instalados."""
        self._ollama_models = models
        if current:
            self._desired_model["ollama"] = current
        if self._current_provider() == "ollama":
            self._refresh_model_options()

    def set_gemini_models(self, chat_models: list[str], live_models: list[str]) -> None:
        """Añade al panel los modelos Gemini que la API ofrece ahora mismo."""
        self._gemini_models = list(chat_models)
        self._gemini_live_models = list(live_models)
        if self._current_provider() in ("gemini", "gemini_live"):
            self._refresh_model_options()

    def set_model_selection(self, provider: str, model: str) -> None:
        """Recuerda y aplica la selección de modelo activa para un proveedor dado."""
        self._desired_model[provider] = model
        if self._current_provider() == provider:
            self.model_combo.blockSignals(True)
            self._select_model_item(provider, model)
            self.model_combo.blockSignals(False)

    def log_line(self, prefix: str, text: str) -> None:
        """Añade un mensaje a la conversación según su origen (API usada por main.py)."""
        kind = prefix.upper()
        if kind == "VK":
            self.chat.add_user(text)
        elif kind == "ZARA":
            self.chat.add_assistant(text)
        elif kind == "TOOL":
            name, _, desc = text.partition(":")
            self.chat.add_tool(name.strip(), desc.strip())
        elif kind == "CONFIRMAR":
            self.chat.add_confirm(text)
        elif kind == "ERROR":
            self.chat.add_error(text)
        else:
            self.chat.add_system(text)


class ZaraHUD(QtWidgets.QWidget):
    """Bola flotante de Zara: translúcida, siempre encima, arrastrable y con avatar."""

    toggle_listen_requested = QtCore.Signal()
    barge_in_requested = QtCore.Signal()
    mic_toggle_requested = QtCore.Signal()
    brain_changed = QtCore.Signal(str)
    model_changed = QtCore.Signal(str, str)  # (provider, model)
    models_refresh_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()
    text_command_submitted = QtCore.Signal(str)

    def __init__(self, on_toggle_listen: Optional[Callable[[], None]] = None, diameter: int = 118):
        super().__init__()
        self.on_toggle_listen = on_toggle_listen

        self._d = diameter
        self._state = HUDState.IDLE
        self._phase = 0.0
        # [DISEÑO] Fase de color IDLE independiente de la de rotación/onda:
        # un ciclo cian→violeta→rosa de ~10s se percibe premium y calmado;
        # si compartiera la fase con el giro del anillo, iría a ~3.5s (muy
        # "nervioso" para un HUD de alta gama).
        self._color_phase = 0.0
        self._level = 0.2
        self._target_level = 0.2
        self._muted = False
        self._avatar = self._load_avatar(diameter)

        self._maybe_click = False
        self._moving = False
        self._system_move = False  # arrastre delegado al compositor (Wayland nativo)
        self._quit_emitted = False
        self._press_pos = QtCore.QPoint()
        self._drag_pos = QtCore.QPoint()

        self._init_window()
        self._init_panel()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(28)  # ~36 fps

    def _init_window(self) -> None:
        self.setWindowTitle(f"ZARA v{VERSION_FULL}")
        for candidate in APP_ICON_CANDIDATES:
            if Path(candidate).is_file():
                self.setWindowIcon(QtGui.QIcon(candidate))
                break
        # La ventana es MAYOR que el núcleo: el halo y el anillo segmentado se pintan
        # fuera de él y, con ventana = diámetro, se recortaban por los cuatro lados.
        self._side, self._r_core, self._r_segments = orb_layout(self._d)
        self.setFixedSize(self._side, self._side)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setCursor(QtCore.Qt.OpenHandCursor)

        screen = QtGui.QGuiApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.move(geom.right() - self._side - 24, geom.top() + 24)

    def _init_panel(self) -> None:
        self.panel = ZaraPanel()
        self.panel.hide()
        self.panel.close_requested.connect(self.panel.hide)
        self.panel.interrupt_requested.connect(self.barge_in_requested.emit)
        self.panel.mic_toggle_requested.connect(self._on_mic_toggle_clicked)
        self.panel.brain_changed.connect(self.brain_changed.emit)
        self.panel.model_changed.connect(self.model_changed.emit)
        self.panel.models_refresh_requested.connect(self.models_refresh_requested.emit)
        self.panel.quit_requested.connect(self.quit_requested.emit)
        self.panel.text_submitted.connect(self.text_command_submitted.emit)

    def _on_mic_toggle_clicked(self) -> None:
        self.mic_toggle_requested.emit()
        if self.on_toggle_listen:
            self.on_toggle_listen()
        self.toggle_listen_requested.emit()

    def _load_avatar(self, d: int) -> Optional[QtGui.QPixmap]:
        for candidate in AVATAR_CANDIDATES:
            if Path(candidate).is_file():
                pm = QtGui.QPixmap(candidate)
                if not pm.isNull():
                    return pm.scaled(
                        d * 2, d * 2,
                        QtCore.Qt.KeepAspectRatioByExpanding,
                        QtCore.Qt.SmoothTransformation,
                    )
        return None

    def _position_panel(self) -> None:
        """Coloca el panel pegado a la bola, a su izquierda (o a la derecha si no cabe)."""
        screen = QtGui.QGuiApplication.screenAt(self.geometry().center()) or QtGui.QGuiApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else None

        gap = 10
        orb_pos = self.pos()
        panel_x = orb_pos.x() - self.panel.width() - gap
        panel_y = orb_pos.y() + (self.height() // 2) - (self.panel.height() // 2)

        if geom is not None:
            if panel_x < geom.left():
                panel_x = orb_pos.x() + self.width() + gap
            panel_y = max(geom.top(), min(panel_y, geom.bottom() - self.panel.height()))

        self.panel.move(panel_x, panel_y)

    # --- API pública (usada por main.py / ZaraController) ---
    def set_hud_state(self, state: HUDState) -> None:
        self._state = state
        tooltips = {
            HUDState.IDLE: "ZARA // En espera, Señor VK",
            HUDState.LISTENING: "ZARA // Escuchando...",
            HUDState.THINKING: "ZARA // Procesando / agente táctico",
            HUDState.SPEAKING: "ZARA // Respondiendo",
        }
        self.setToolTip(tooltips.get(state, state.value))
        if self.panel.isVisible():
            self.panel.set_status(_STATE_TEXT.get(state, state.value), self._muted)
        self.update()

    def set_rms_level(self, rms: float) -> None:
        self._target_level = min(1.0, max(0.0, rms * 12.0))

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self.panel.set_muted_visual(muted)
        self.panel.set_status(_STATE_TEXT.get(self._state, self._state.value), muted)
        self.panel.log_line("SISTEMA", "Escucha pausada." if muted else "Escucha reanudada.")
        self.update()

    def set_brain(self, value: str) -> None:
        self.panel.set_brain(value)

    def set_ollama_models(self, models: list, current: str = "") -> None:
        self.panel.set_ollama_models(models, current)

    def set_gemini_models(self, chat_models: list, live_models: list) -> None:
        self.panel.set_gemini_models(chat_models, live_models)

    def set_model_selection(self, provider: str, model: str) -> None:
        self.panel.set_model_selection(provider, model)

    def update_tool_status(self, tool_name: str, desc: str) -> None:
        """Actualiza el tooltip y la consola del panel con la herramienta en ejecución."""
        self.setToolTip(f"ZARA // [{tool_name.upper()}] {desc}")
        self.panel.log_line("TOOL", f"{tool_name}: {desc}")

    def update_user_speech(self, text: str) -> None:
        """Muestra en el tooltip y la consola del panel lo que Zara ha oído del Señor VK."""
        self.setToolTip(f"ZARA // VK: {text}")
        self.panel.log_line("VK", text)

    def update_assistant_reply(self, text: str) -> None:
        """Registra en la consola del panel la última respuesta hablada de Zara."""
        self.panel.log_line("ZARA", text)

    # --- animación ---
    def _tick(self) -> None:
        self._phase += 0.05
        if self._phase > 2 * math.pi:
            self._phase -= 2 * math.pi
        # ~36 fps * 0.018 rad/tick ≈ 2π cada 10s (vs. ~3.5s de self._phase).
        self._color_phase += 0.018
        if self._color_phase > 2 * math.pi:
            self._color_phase -= 2 * math.pi

        if self._state in (HUDState.LISTENING, HUDState.SPEAKING):
            self._target_level = max(self._target_level, 0.45 + 0.4 * abs(math.sin(self._phase * 2.3)))
        elif self._state == HUDState.THINKING:
            self._target_level = 0.6

        self._level += (self._target_level - self._level) * 0.2
        self._target_level *= 0.92  # decaimiento natural del nivel objetivo
        self.update()

    def _ring_color(self) -> QtGui.QColor:
        if self._muted:
            return MUTED_GRAY
        if self._state == HUDState.IDLE:
            return _iridescent(self._color_phase)
        return _STATE_COLOR[self._state]

    # --- pintura ---
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)

        w = self.width()
        cx, cy = w / 2.0, w / 2.0
        c = self._ring_color()
        r_core = self._r_core

        # 1) Halo exterior difuso (varias capas)
        pulse = 1.0 + 0.16 * self._level
        for width_px, alpha in ((22, 26), (14, 55), (7, 110)):
            glow = QtGui.QColor(c)
            glow.setAlpha(alpha)
            p.setPen(QtGui.QPen(glow, width_px))
            p.setBrush(QtCore.Qt.NoBrush)
            rr = r_core * pulse + width_px * 0.9
            p.drawEllipse(QtCore.QPointF(cx, cy), rr, rr)

        # 2) Anillo exterior segmentado (estética tech), girando lento
        p.save()
        p.translate(cx, cy)
        p.rotate((self._phase * 18) % 360)
        seg_r = self._r_segments
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

        # 3) Núcleo de vidrio con el avatar recortado en círculo
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QPointF(cx, cy), r_core, r_core)
        p.setClipPath(path)
        if self._avatar is not None:
            p.drawPixmap(
                int(cx - r_core), int(cy - r_core),
                int(r_core * 2), int(r_core * 2),
                self._avatar, 0, 0, self._avatar.width(), self._avatar.height(),
            )
        else:
            grad = QtGui.QRadialGradient(cx, cy, r_core)
            grad.setColorAt(0, VIOLET)
            grad.setColorAt(1, BG)
            p.fillRect(self.rect(), grad)
        # Viñeta interior para dar volumen
        vig = QtGui.QRadialGradient(cx, cy * 0.8, r_core * 1.3)
        vig.setColorAt(0.55, QtGui.QColor(0, 0, 0, 0))
        vig.setColorAt(1.0, QtGui.QColor(0, 0, 0, 160))
        p.fillRect(self.rect(), vig)
        if self._muted:
            p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 90))
        p.setClipping(False)

        # 4) Anillo principal y arco/onda reactiva según estado
        p.setBrush(QtCore.Qt.NoBrush)
        rim = QtGui.QColor(c)
        rim.setAlpha(230)
        p.setPen(QtGui.QPen(rim, 3.2))
        p.drawEllipse(QtCore.QPointF(cx, cy), r_core, r_core)

        if self._muted:
            # Icono de micrófono silenciado (barra diagonal) sobre el núcleo
            p.setPen(QtGui.QPen(QtGui.QColor("#F87171"), 3.0, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            d = r_core * 0.8
            p.drawLine(QtCore.QPointF(cx - d, cy - d), QtCore.QPointF(cx + d, cy + d))
        elif self._state == HUDState.THINKING:
            p.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 3.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            start = int((self._phase * 170 / math.pi) % 360) * 16
            p.drawArc(QtCore.QRectF(cx - r_core, cy - r_core, r_core * 2, r_core * 2), start, 100 * 16)
        elif self._state in (HUDState.SPEAKING, HUDState.LISTENING):
            p.setPen(QtGui.QPen(c, 2))
            pts = 48
            amp = 5 + 9 * self._level
            poly = QtGui.QPolygonF()
            for k in range(pts + 1):
                a = 2 * math.pi * k / pts
                rr = r_core + 7 + amp * math.sin(a * 6 + self._phase * 4)
                poly.append(QtCore.QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a)))
            p.drawPolyline(poly)

        # 5) Indicador de panel abierto (puntito bajo la bola)
        if self.panel.isVisible():
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(c))
            p.drawEllipse(QtCore.QPointF(cx, cy + self._r_segments + SEGMENT_DOT_GAP), 3.0, 3.0)

    # --- arrastre, clic y panel ---
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            self._maybe_click = True
            self._moving = False
            self._system_move = False
            self._press_pos = event.globalPosition().toPoint()
            self._drag_pos = self._press_pos - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.buttons() == QtCore.Qt.LeftButton:
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if moved > 6:
                self._moving = True
                self._maybe_click = False
            if uses_native_wayland():
                # [FIX MUL-5/V1_-7] En Wayland nativo move() se ignora: al superar el umbral
                # de arrastre se cede el movimiento al compositor (el clic sigue funcionando).
                if self._moving and not self._system_move:
                    self._system_move = self._start_system_move()
                event.accept()
                return
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            if self.panel.isVisible():
                self._position_panel()
            event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if event.button() == QtCore.Qt.LeftButton and self._maybe_click and not self._moving:
            if self._state == HUDState.SPEAKING:
                # Prioridad: un clic rápido mientras Zara habla la interrumpe de inmediato.
                self.barge_in_requested.emit()
            else:
                self._toggle_panel()
        self._maybe_click = False
        self._moving = False
        self._system_move = False

    def _start_system_move(self) -> bool:
        """Pide al compositor que arrastre la bola (Wayland nativo). True si lo acepta."""
        handle = self.windowHandle()
        return bool(handle is not None and handle.startSystemMove())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        """[FIX V1_-15] Alt+F4 (o el gestor de ventanas) sobre la bola cierra Zara de verdad.

        La bola es una ventana Qt.Tool, que Qt no cuenta para salir al cerrar la última
        ventana: sin esto se ocultaba y quedaba un proceso invisible con el micrófono abierto
        y sin forma de salir. Se acepta el cierre (si se ignorase, app.quit() no podría
        terminar en Qt 6) y se pide el apagado una sola vez.
        """
        event.accept()
        self.panel.hide()
        if not self._quit_emitted:
            self._quit_emitted = True
            self.quit_requested.emit()

    def _toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.panel.set_status(_STATE_TEXT.get(self._state, self._state.value), self._muted)
            self._position_panel()
            self.panel.show()
            self.panel.raise_()
        self.update()
