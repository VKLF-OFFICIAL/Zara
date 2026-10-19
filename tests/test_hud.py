"""Pruebas unitarias para ui/hud.py."""

import os
import sys
import pytest
from PySide6 import QtCore, QtWidgets

# Evitar abrir ventanas físicas durante los tests
os.environ["QT_QPA_PLATFORM"] = "offscreen"

@pytest.fixture(scope="session")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    yield app


def test_panel_creation_and_model_filtering(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()

    # Simular lista con múltiples modelos de Ollama
    models = [
        "qwen2.5-coder:7b",
        "qwen2.5:14b",
        "deepseek-coder-v2:16b",
        "llama3.2:3b",
        "mistral:7b",
        "huihui_ai/qwen2.5-coder-abliterated:latest",
    ]
    panel.set_ollama_models(models, current="qwen2.5-coder:7b")
    panel.set_brain("ollama")

    # 1. Comprobar que todos los modelos están presentes en el combo
    assert panel.model_combo.count() == len(models)
    assert "MODELO (6)" in panel.model_lbl.text()
    assert not panel.model_search_input.isHidden()

    # 2. Filtrar por "deepseek"
    panel.model_search_input.setText("deepseek")
    assert panel.model_combo.count() == 1
    assert "deepseek-coder-v2:16b" in panel.model_combo.itemData(0)
    assert "MODELO (1/6)" in panel.model_lbl.text()

    # 3. Filtrar por "qwen"
    panel.model_search_input.setText("qwen")
    assert panel.model_combo.count() == 3  # qwen2.5-coder, qwen2.5, huihui_ai/qwen2.5-coder
    assert "MODELO (3/6)" in panel.model_lbl.text()

    # 4. Filtro sin coincidencias
    panel.model_search_input.setText("modelo_inexistente_xyz")
    assert panel.model_combo.count() == 1
    assert "(sin coincidencias)" in panel.model_combo.itemText(0)
    assert not panel.model_combo.isEnabled()

    # 5. Limpiar filtro restaura todos
    panel.model_search_input.clear()
    assert panel.model_combo.count() == len(models)
    assert panel.model_combo.isEnabled()

    # 6. Cambiar a Claude oculta el buscador de modelos locales
    panel.set_brain("claude")
    assert panel.model_search_input.isHidden()
    assert panel.model_combo.count() > 0


def test_hud_state_and_colors(qapp):
    from ui.hud import HUDState, ZaraHUD

    hud = ZaraHUD()
    hud.set_hud_state(HUDState.IDLE)
    assert hud._state == HUDState.IDLE

    hud.set_hud_state(HUDState.LISTENING)
    assert hud._state == HUDState.LISTENING

    hud.set_muted(True)
    assert hud._muted is True
    assert "mic pausado" in hud.panel.status_lbl.text().lower()

    hud.set_muted(False)
    assert hud._muted is False
    assert "mic activo" in hud.panel.status_lbl.text().lower()


def test_format_ollama_model_display():
    from ui.hud import format_ollama_model_display

    # Modelos del Señor VK mostrados en las capturas de Ollama
    m1 = "huihui_ai/qwen2.5-coder-abliterate:7b-instruct"
    m2 = "huihui_ai/qwen2.5-coder-abliterate:14b-instruct"
    m3 = "hf.co/OBLITERATUS/Qwen3.8-27B-OBLITERATED:Q3_K_M"
    m4 = "hf.co/OBLITERATUS/Qwen3.8-27B-OBLITERATED:Q4_K_M"

    d1 = format_ollama_model_display(m1, max_len=36)
    d2 = format_ollama_model_display(m2, max_len=36)
    d3 = format_ollama_model_display(m3, max_len=36)
    d4 = format_ollama_model_display(m4, max_len=36)

    # 1. Ambos modelos de qwen deben diferenciarse claramente por su tag
    assert d1 != d2
    assert d1.endswith(":7b-instruct")
    assert d2.endswith(":14b-instruct")

    # 2. Ambos modelos de OBLITERATUS deben diferenciarse por su cuantización
    assert d3 != d4
    assert d3.endswith(":Q3_K_M")
    assert d4.endswith(":Q4_K_M")

    # 3. No deben exceder el max_len
    assert len(d1) <= 36
    assert len(d2) <= 36
    assert len(d3) <= 36
    assert len(d4) <= 36


def test_model_selection_activation_and_return_pressed(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    models = [
        "huihui_ai/qwen2.5-coder-abliterate:7b-instruct",
        "huihui_ai/qwen2.5-coder-abliterate:14b-instruct",
        "hf.co/OBLITERATUS/Qwen3.8-27B-OBLITERATED:Q3_K_M",
    ]
    panel.set_ollama_models(models, current=models[0])
    panel.set_brain("ollama")

    emitted = []
    panel.model_changed.connect(lambda p, m: emitted.append((p, m)))

    # Filtrar por "14b" y pulsar Enter
    panel.model_search_input.setText("14b")
    assert panel.model_combo.count() == 1
    panel.model_search_input.returnPressed.emit()

    assert len(emitted) == 1
    assert emitted[0] == ("ollama", "huihui_ai/qwen2.5-coder-abliterate:14b-instruct")
    assert panel._desired_model.get("ollama") == "huihui_ai/qwen2.5-coder-abliterate:14b-instruct"


def test_tactical_combobox_wheel_protection(qapp):
    from ui.hud import TacticalComboBox
    from PySide6 import QtGui

    combo = TacticalComboBox()
    combo.addItems(["Opción 1", "Opción 2", "Opción 3"])
    combo.setCurrentIndex(0)

    # Evento de rueda de ratón con popup cerrado: debe ignorarse
    wheel_ev = QtGui.QWheelEvent(
        QtCore.QPointF(10, 10),
        QtCore.QPointF(10, 10),
        QtCore.QPoint(0, 120),
        QtCore.QPoint(0, 120),
        QtCore.Qt.NoButton,
        QtCore.Qt.NoModifier,
        QtCore.Qt.ScrollUpdate,
        False,
    )
    combo.wheelEvent(wheel_ev)
    assert not wheel_ev.isAccepted()
    assert combo.currentIndex() == 0


def test_dropdown_sync_and_duplicate_suppression(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    panel.set_ollama_models(["m1", "m2"], current="m1")

    brain_emitted = []
    model_emitted = []
    panel.brain_changed.connect(lambda p: brain_emitted.append(p))
    panel.model_changed.connect(lambda p, m: model_emitted.append((p, m)))

    # 1. Cambiar a Claude debe emitir brain_changed y sincronizar el modelo de Claude
    panel.brain_combo.setCurrentIndex(3)  # Claude
    panel.brain_combo.activated.emit(3)

    assert "claude" in brain_emitted
    assert any(p == "claude" for p, _ in model_emitted)

    # 2. Seleccionar "sonnet" y luego "Por defecto" ("") en Claude debe funcionar
    model_emitted.clear()
    panel.model_combo.setCurrentIndex(1)  # Sonnet
    panel.model_combo.activated.emit(1)
    assert ("claude", "sonnet") in model_emitted

    model_emitted.clear()
    panel.model_combo.setCurrentIndex(0)  # Por defecto ("")
    panel.model_combo.activated.emit(0)
    assert ("claude", "") in model_emitted

    # 3. Disparos repetidos con el mismo modelo no deben duplicar eventos
    model_emitted.clear()
    panel.model_combo.activated.emit(0)
    panel._on_model_changed(0)
    assert len(model_emitted) == 0




def test_chat_routes_messages_and_renders_plain_text(qapp):
    from ui.hud import ZaraPanel
    from PySide6 import QtWidgets

    panel = ZaraPanel()
    chat = panel.chat
    panel.log_line("VK", "<b>hola</b>")
    panel.log_line("ZARA", "Respuesta")
    panel.log_line("SISTEMA", "Cerebro cambiado")
    panel.log_line("ERROR", "Algo falló")
    panel.log_line("CONFIRMAR", "¿borrar x?")
    assert chat.count() == 5
    # La salida del modelo nunca se interpreta como HTML
    texts = [l for l in chat.findChildren(QtWidgets.QLabel) if l.text() == "<b>hola</b>"]
    assert texts and texts[0].textFormat() == QtCore.Qt.PlainText


def test_tool_chip_collapses_when_completed(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    panel.log_line("TOOL", "web_search: Ejecutando web_search...")
    panel.log_line("TOOL", "web_search: Completado.")
    # «ejecutando» y «completado» comparten un único chip
    assert panel.chat.count() == 1


def test_chat_keeps_a_bounded_history(qapp):
    from ui.hud import ChatView, ZaraPanel

    panel = ZaraPanel()
    for i in range(ChatView.MAX_ITEMS + 15):
        panel.log_line("SISTEMA", f"evento {i}")
    assert panel.chat.count() == ChatView.MAX_ITEMS


def test_long_messages_scroll_instead_of_being_squashed(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    panel.show()
    for _ in range(6):
        panel.log_line("ZARA", "Una respuesta larga de varias líneas para forzar el scroll del chat. " * 3)
    qapp.processEvents()
    chat = panel.chat
    # el contenido nunca es más bajo que su mínimo: si no cabe, aparece el scroll
    assert chat._inner.height() >= chat._inner.minimumSizeHint().height()
    assert chat.verticalScrollBar().maximum() > 0


def test_send_button_only_enabled_with_text(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    sent = []
    panel.text_submitted.connect(sent.append)
    assert not panel.send_btn.isEnabled()
    panel.input_box.setText("  hola zara ")
    assert panel.send_btn.isEnabled()
    panel.send_btn.click()
    assert sent == ["hola zara"]
    assert panel.input_box.text() == ""
