"""Pruebas unitarias para ui/hud.py (ZARA v2.0)."""

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
    assert "Modelo (6):" in panel.model_lbl.text()
    assert not panel.model_search_input.isHidden()

    # 2. Filtrar por "deepseek"
    panel.model_search_input.setText("deepseek")
    assert panel.model_combo.count() == 1
    assert "deepseek-coder-v2:16b" in panel.model_combo.itemData(0)
    assert "Modelo (1/6):" in panel.model_lbl.text()

    # 3. Filtrar por "qwen"
    panel.model_search_input.setText("qwen")
    assert panel.model_combo.count() == 3  # qwen2.5-coder, qwen2.5, huihui_ai/qwen2.5-coder
    assert "Modelo (3/6):" in panel.model_lbl.text()

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
    assert "MIC PAUSADO" in hud.panel.status_lbl.text()

    hud.set_muted(False)
    assert hud._muted is False
    assert "MIC ACTIVO" in hud.panel.status_lbl.text()
