"""Interfaz Holográfica HUD Táctica (ZARA v2.0).

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

# Rutas candidatas para el retrato de Zara (relativas a la raíz del proyecto).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
AVATAR_CANDIDATES = [
    str(_PROJECT_ROOT / "config" / "avatar.jpg"),
    str(_PROJECT_ROOT / "config" / "zara-icon.png"),
]
APP_ICON_CANDIDATES = [
    str(_PROJECT_ROOT / "config" / "zara-icon.ico"),
    str(_PROJECT_ROOT / "config" / "zara-icon.png"),
]

# [DISEÑO] Pilas de fuentes con fallback seguro: la fuente preferida va
# primero (Orbitron para títulos "HUD", JetBrains Mono para la consola/datos
# técnicos, Inter para el resto de la interfaz), pero como no se garantiza
# que estén instaladas en el Windows del Señor VK, Qt recorre la lista y usa
# la primera disponible — nunca se queda sin fuente legible.
_FONT_TITLE = "'Orbitron', 'Segoe UI Semibold', 'Segoe UI', sans-serif"
_FONT_MONO = "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace"
_FONT_UI = "'Inter', 'Segoe UI', sans-serif"

_PANEL_STYLE = f"""
QFrame#bg {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(8, 11, 18, 245), stop:1 rgba(16, 23, 37, 250));
    border: 1px solid rgba(46, 242, 230, 60);
    border-radius: 14px;
}}
QLabel {{ color: #94A3B8; font-family: {_FONT_UI}; font-size: 12px; border: none; }}
QLabel#title {{ color: #E5EDF7; font-family: {_FONT_TITLE}; font-size: 20px; font-weight: 600; }}
QLabel#status {{ color: #E5EDF7; font-family: {_FONT_UI}; font-size: 12px; }}
QPushButton {{
    background-color: #131D2C;
    color: #DCE8F6;
    border: 1px solid #30445F;
    border-radius: 8px;
    padding: 9px 12px;
    font-family: {_FONT_UI};
    font-size: 12px;
}}
QPushButton:hover {{ background-color: #192A3D; border: 1px solid #2EF2E6; }}
QPushButton:pressed {{ background-color: #102B3A; padding-top: 10px; padding-bottom: 8px; }}
QPushButton#iconBtn {{
    background-color: #131D2C;
    border: 1px solid #30445F;
    border-radius: 20px;
    padding: 0px;
}}
QPushButton#iconBtn:hover {{ background-color: #192A3D; border: 1px solid #2EF2E6; }}
QPushButton#iconBtn:pressed {{ background-color: #102B3A; }}
QPushButton#closeBtn {{
    color: #F87171;
    border: none;
    background: transparent;
    font-size: 15px;
    font-weight: bold;
    padding: 0px 4px;
}}
QPushButton#closeBtn:hover {{ color: #FCA5A5; }}
QPlainTextEdit#console {{
    background-color: #0B111C;
    color: #C3D2E5;
    border: 1px solid #1E3045;
    border-radius: 10px;
    padding: 10px;
    font-family: {_FONT_MONO};
    font-size: 11px;
    selection-background-color: #17465A;
}}
QLineEdit {{
    background-color: #101725;
    color: #E5EDF7;
    border: 1px solid #30445F;
    border-radius: 8px;
    padding: 9px 10px;
    font-family: {_FONT_UI};
    font-size: 12px;
}}
QLineEdit:focus {{ border: 1px solid #2EF2E6; }}
QComboBox {{
    background-color: #101725;
    color: #E5EDF7;
    border: 1px solid #30445F;
    border-radius: 8px;
    padding: 7px 8px;
    font-family: {_FONT_UI};
    font-size: 12px;
    min-width: 140px;
}}
QComboBox:focus {{ border: 1px solid #2EF2E6; }}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #2EF2E6;
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: #101725;
    color: #E5EDF7;
    border: 1px solid #30445F;
    border-radius: 8px;
    selection-background-color: rgba(46, 242, 230, 35);
    selection-color: #FFFFFF;
    font-family: {_FONT_UI};
    font-size: 12px;
    outline: none;
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    min-height: 26px;
    padding: 4px 8px;
    border-radius: 6px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: rgba(46, 242, 230, 25);
    color: #FFFFFF;
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: rgba(46, 242, 230, 50);
    color: #FFFFFF;
}}
QScrollBar:vertical {{
    background: #0B111C;
    width: 8px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(46, 242, 230, 110);
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(46, 242, 230, 180);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
"""


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


def build_icon(draw_fn, color: QtGui.QColor, px: int = 40) -> QtGui.QIcon:
    """Renderiza un glifo vectorial en un QPixmap transparente de `px`x`px`."""
    pm = QtGui.QPixmap(px, px)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    draw_fn(p, QtCore.QRectF(0, 0, px, px), color)
    p.end()
    return QtGui.QIcon(pm)


class ZaraPanel(QtWidgets.QWidget):
    """Panel táctico desplegable: consola de eventos, entrada de texto y controles rápidos."""

    mic_toggle_requested = QtCore.Signal()
    interrupt_requested = QtCore.Signal()
    brain_changed = QtCore.Signal(str)
    model_changed = QtCore.Signal(str, str)  # (provider, model)
    quit_requested = QtCore.Signal()
    text_submitted = QtCore.Signal(str)
    close_requested = QtCore.Signal()

    _BRAIN_OPTIONS = [
        ("Auto", "auto"),
        ("Gemini Live (Streaming)", "gemini_live"),
        ("Ollama (local)", "ollama"),
        ("Claude", "claude"),
        ("Gemini", "gemini"),
    ]
    _GEMINI_LIVE_MODEL_OPTIONS = [
        ("Gemini 2.5 Flash Live", "gemini-2.5-flash-preview-native-audio-dialog"),
        ("Gemini 2.0 Flash Live", "gemini-2.0-flash-live-001"),
        ("Gemini 2.0 Flash Exp", "gemini-2.0-flash-exp"),
    ]
    _CLAUDE_MODEL_OPTIONS = [
        ("Por defecto", ""),
        ("Sonnet", "sonnet"),
        ("Opus", "opus"),
        ("Haiku", "haiku"),
        ("Fable", "fable"),
    ]
    _GEMINI_MODEL_OPTIONS = [
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
    ]

    def __init__(self):
        super().__init__()
        self._ollama_models: list[str] = []
        self._desired_model: dict[str, str] = {}
        self.setFixedSize(380, 520)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        bg = QtWidgets.QFrame(self)
        bg.setObjectName("bg")
        bg.setStyleSheet(_PANEL_STYLE)
        # [DISEÑO] Sombra suave para dar profundidad ("glassmorphism" ligero)
        # sin abusar de efectos: un único halo moderado, no uno por widget.
        shadow = QtWidgets.QGraphicsDropShadowEffect(bg)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 6)
        shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        bg.setGraphicsEffect(shadow)
        outer.addWidget(bg)

        layout = QtWidgets.QVBoxLayout(bg)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Cabecera
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("ZARA // PANEL TÁCTICO")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        close_btn = QtWidgets.QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close_requested.emit)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Estado
        self.status_lbl = QtWidgets.QLabel("ESTADO: EN ESPERA // MIC ACTIVO")
        self.status_lbl.setObjectName("status")
        layout.addWidget(self.status_lbl)

        # Consola de eventos
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(300)
        layout.addWidget(self.console, stretch=1)

        # Entrada de texto manual
        input_row = QtWidgets.QHBoxLayout()
        self.input_box = QtWidgets.QLineEdit()
        self.input_box.setPlaceholderText("Escribe una orden para Zara...")
        self.input_box.returnPressed.connect(self._submit_text)
        input_row.addWidget(self.input_box, stretch=1)
        send_btn = QtWidgets.QPushButton("Enviar")
        send_btn.setCursor(QtCore.Qt.PointingHandCursor)
        send_btn.clicked.connect(self._submit_text)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

        # Selector de cerebro (proveedor)
        brain_row = QtWidgets.QHBoxLayout()
        brain_row.addWidget(QtWidgets.QLabel("Cerebro:"))
        self.brain_combo = QtWidgets.QComboBox()
        for label, value in self._BRAIN_OPTIONS:
            self.brain_combo.addItem(label, value)
        self.brain_combo.currentIndexChanged.connect(self._on_provider_changed)
        brain_row.addWidget(self.brain_combo, stretch=1)
        layout.addLayout(brain_row)

        # Selector de modelo (depende del proveedor elegido arriba)
        model_row = QtWidgets.QHBoxLayout()
        self.model_lbl = QtWidgets.QLabel("Modelo:")
        model_row.addWidget(self.model_lbl)
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setFocusPolicy(QtCore.Qt.WheelFocus)
        self.model_combo.view().setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.model_combo.view().setAutoScroll(True)
        self.model_combo.view().setMinimumWidth(380)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.model_combo.activated.connect(self._on_model_changed)
        model_row.addWidget(self.model_combo, stretch=1)
        layout.addLayout(model_row)

        # Buscador / filtro rápido de modelos locales (aparece para Ollama)
        self.model_search_input = QtWidgets.QLineEdit()
        self.model_search_input.setPlaceholderText("🔍 Filtrar modelos locales (ej: qwen, deepseek)...")
        self.model_search_input.textChanged.connect(self._on_model_filter_changed)
        self.model_search_input.returnPressed.connect(self._on_model_filter_return_pressed)
        self.model_search_input.hide()
        layout.addWidget(self.model_search_input)

        self._refresh_model_options()

        # [DISEÑO] Botones rápidos como iconos circulares vectoriales (nada de
        # emojis): el texto descriptivo pasa a tooltip, por accesibilidad.
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self.mic_btn = QtWidgets.QPushButton()
        self.mic_btn.setObjectName("iconBtn")
        self.mic_btn.setFixedSize(40, 40)
        self.mic_btn.setIcon(build_icon(_draw_mic_icon, CYAN))
        self.mic_btn.setIconSize(QtCore.QSize(22, 22))
        self.mic_btn.setToolTip("Micrófono: activar / silenciar")
        self.mic_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.mic_btn.clicked.connect(self.mic_toggle_requested.emit)
        btn_row.addWidget(self.mic_btn)

        interrupt_btn = QtWidgets.QPushButton()
        interrupt_btn.setObjectName("iconBtn")
        interrupt_btn.setFixedSize(40, 40)
        interrupt_btn.setIcon(build_icon(_draw_stop_icon, QtGui.QColor("#F87171")))
        interrupt_btn.setIconSize(QtCore.QSize(22, 22))
        interrupt_btn.setToolTip("Parar: interrumpir la respuesta de Zara")
        interrupt_btn.setCursor(QtCore.Qt.PointingHandCursor)
        interrupt_btn.clicked.connect(self.interrupt_requested.emit)
        btn_row.addWidget(interrupt_btn)

        quit_btn = QtWidgets.QPushButton()
        quit_btn.setObjectName("iconBtn")
        quit_btn.setFixedSize(40, 40)
        quit_btn.setIcon(build_icon(_draw_power_icon, VIOLET))
        quit_btn.setIconSize(QtCore.QSize(22, 22))
        quit_btn.setToolTip("Salir: cerrar ZARA")
        quit_btn.setCursor(QtCore.Qt.PointingHandCursor)
        quit_btn.clicked.connect(self.quit_requested.emit)
        btn_row.addWidget(quit_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _submit_text(self) -> None:
        text = self.input_box.text().strip()
        if text:
            self.text_submitted.emit(text)
            self.input_box.clear()

    def _current_provider(self) -> str:
        return self.brain_combo.currentData() or "auto"

    def _on_provider_changed(self, _idx: int) -> None:
        self.brain_changed.emit(self._current_provider())
        # Limpiar filtro al cambiar proveedor
        self.model_search_input.blockSignals(True)
        self.model_search_input.clear()
        self.model_search_input.blockSignals(False)
        self._refresh_model_options()

    def _on_model_filter_changed(self, _text: str) -> None:
        self._refresh_model_options()

    def _on_model_filter_return_pressed(self) -> None:
        """Al pulsar Enter en el buscador de modelos, activa de inmediato el primer modelo filtrado."""
        if self.model_combo.count() > 0 and self.model_combo.isEnabled():
            self.model_combo.setCurrentIndex(0)
            self._on_model_changed(0)
            display_name = self.model_combo.currentText()
            self.log_line("SISTEMA", f"Modelo activado: {display_name}")
            self.input_box.setFocus()

    def _on_model_changed(self, idx: int) -> None:
        if idx < 0:
            return
        model = self.model_combo.itemData(idx)
        if model is None or model == "":
            return
        provider = self._current_provider()
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
                count_str = f"Modelo ({len(candidates)}/{len(self._ollama_models)}):" if filter_text else f"Modelo ({len(candidates)}):"
                self.model_lbl.setText(count_str)
                for name in candidates:
                    display = format_ollama_model_display(name, max_len=36)
                    self.model_combo.addItem(display, name)
                    idx = self.model_combo.count() - 1
                    self.model_combo.setItemData(idx, f"Modelo completo: {name}", QtCore.Qt.ToolTipRole)
                self.model_combo.setEnabled(True)
            else:
                self.model_lbl.setText("Modelo (0):")
                msg = "(sin coincidencias)" if filter_text else "(sin modelos detectados)"
                self.model_combo.addItem(msg, "")
                self.model_combo.setEnabled(False)
        else:
            self.model_search_input.hide()
            if provider == "claude":
                self.model_lbl.setText("Modelo:")
                for label, value in self._CLAUDE_MODEL_OPTIONS:
                    self.model_combo.addItem(label, value)
                self.model_combo.setEnabled(True)
            elif provider == "gemini_live":
                self.model_lbl.setText("Modelo:")
                for label, value in self._GEMINI_LIVE_MODEL_OPTIONS:
                    self.model_combo.addItem(label, value)
                self.model_combo.setEnabled(True)
            elif provider == "gemini":
                self.model_lbl.setText("Modelo:")
                for label, value in self._GEMINI_MODEL_OPTIONS:
                    self.model_combo.addItem(label, value)
                self.model_combo.setEnabled(True)
            else:  # auto
                self.model_lbl.setText("Modelo: (auto)")
                self.model_combo.addItem("El sistema elige", "")
                self.model_combo.setEnabled(False)

        desired = self._desired_model.get(provider)
        if desired is not None:
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == desired:
                    self.model_combo.setCurrentIndex(i)
                    break

        self.model_combo.blockSignals(False)

    # --- API pública ---
    def set_status(self, state_text: str, muted: bool) -> None:
        mic_text = "MIC PAUSADO" if muted else "MIC ACTIVO"
        self.status_lbl.setText(f"ESTADO: {state_text} // {mic_text}")

    def set_muted_visual(self, muted: bool) -> None:
        icon_fn = _draw_mic_off_icon if muted else _draw_mic_icon
        self.mic_btn.setIcon(build_icon(icon_fn, MUTED_GRAY if muted else CYAN))
        self.mic_btn.setToolTip("Micrófono silenciado" if muted else "Micrófono: activar / silenciar")

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

    def set_model_selection(self, provider: str, model: str) -> None:
        """Recuerda y aplica la selección de modelo activa para un proveedor dado."""
        self._desired_model[provider] = model
        if self._current_provider() == provider:
            self.model_combo.blockSignals(True)
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == model:
                    self.model_combo.setCurrentIndex(i)
                    break
            self.model_combo.blockSignals(False)

    def log_line(self, prefix: str, text: str) -> None:
        self.console.appendPlainText(f"[{prefix}] {text}")
        bar = self.console.verticalScrollBar()
        bar.setValue(bar.maximum())


class ZaraHUD(QtWidgets.QWidget):
    """Bola flotante de Zara: translúcida, siempre encima, arrastrable y con avatar."""

    toggle_listen_requested = QtCore.Signal()
    barge_in_requested = QtCore.Signal()
    mic_toggle_requested = QtCore.Signal()
    brain_changed = QtCore.Signal(str)
    model_changed = QtCore.Signal(str, str)  # (provider, model)
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
        self._press_pos = QtCore.QPoint()
        self._drag_pos = QtCore.QPoint()

        self._init_window()
        self._init_panel()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(28)  # ~36 fps

    def _init_window(self) -> None:
        self.setWindowTitle("ZARA")
        for candidate in APP_ICON_CANDIDATES:
            if Path(candidate).is_file():
                self.setWindowIcon(QtGui.QIcon(candidate))
                break
        self.setFixedSize(self._d, self._d)
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
            self.move(geom.right() - self._d - 24, geom.top() + 24)

    def _init_panel(self) -> None:
        self.panel = ZaraPanel()
        self.panel.hide()
        self.panel.close_requested.connect(self.panel.hide)
        self.panel.interrupt_requested.connect(self.barge_in_requested.emit)
        self.panel.mic_toggle_requested.connect(self._on_mic_toggle_clicked)
        self.panel.brain_changed.connect(self.brain_changed.emit)
        self.panel.model_changed.connect(self.model_changed.emit)
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
        r_core = w * 0.30

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
            p.drawEllipse(QtCore.QPointF(cx, w - 6), 3.0, 3.0)

    # --- arrastre, clic y panel ---
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            self._maybe_click = True
            self._moving = False
            self._press_pos = event.globalPosition().toPoint()
            self._drag_pos = self._press_pos - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.buttons() == QtCore.Qt.LeftButton:
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if moved > 6:
                self._moving = True
                self._maybe_click = False
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

    def _toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.panel.set_status(_STATE_TEXT.get(self._state, self._state.value), self._muted)
            self._position_panel()
            self.panel.show()
            self.panel.raise_()
        self.update()
