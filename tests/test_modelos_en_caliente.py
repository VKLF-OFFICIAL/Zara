"""Un modelo nuevo o cambiado se aplica con Zara abierta, sin reiniciarla."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets

import main
from tests.test_v1_app_hud import make_controller, _wait_for  # noqa: F401 (fixture)


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    yield app


def _items(panel):
    return [panel.model_combo.itemData(i) for i in range(panel.model_combo.count())]


# --------------------------------------------------------------------------- panel
def test_abrir_el_panel_pide_recargar_los_modelos(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    calls = []
    panel.models_refresh_requested.connect(lambda: calls.append(1))
    panel.show()
    assert calls
    panel.hide()
    calls.clear()
    panel.brain_combo.setCurrentIndex(panel.brain_combo.findData("ollama"))
    assert calls


def test_modelo_ollama_recien_instalado_aparece_y_se_puede_elegir(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    panel.set_brain("ollama")
    panel.set_ollama_models(["llama3.2:3b"], "llama3.2:3b")
    assert _items(panel) == ["llama3.2:3b"]

    changed = []
    panel.model_changed.connect(lambda p, m: changed.append((p, m)))
    panel.set_ollama_models(["llama3.2:3b", "qwen3:8b"])  # tras un `ollama pull`
    assert _items(panel) == ["llama3.2:3b", "qwen3:8b"]
    assert panel.model_combo.currentData() == "llama3.2:3b"  # se conserva la elección
    panel.model_combo.setCurrentIndex(1)
    assert changed == [("ollama", "qwen3:8b")]


def test_modelos_gemini_descubiertos_se_anaden_al_combo(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    panel.set_brain("gemini")
    panel.set_gemini_models(["gemini-3.5-flash", "gemini-2.5-flash"], ["gemini-3.1-flash-live-preview"])
    items = _items(panel)
    assert items[0] == "auto" and "gemini-3.5-flash" in items
    assert items.count("gemini-2.5-flash") == 1  # sin duplicar los fijos

    panel.set_brain("gemini_live")
    assert "gemini-3.1-flash-live-preview" in _items(panel)


def test_modelo_fuera_de_la_lista_se_muestra_igualmente(qapp):
    from ui.hud import ZaraPanel

    panel = ZaraPanel()
    panel.set_brain("gemini")
    panel.set_model_selection("gemini", "gemini-9-flash")  # escrito a mano en .env
    assert panel.model_combo.currentData() == "gemini-9-flash"

    panel.set_brain("ollama")
    panel.set_ollama_models(["llama3.2:3b"])
    panel.set_model_selection("ollama", "borrado:7b")  # no se inventan modelos de Ollama
    assert "borrado:7b" not in _items(panel)


# --------------------------------------------------------------------------- controlador
def test_cambiar_modelo_live_reconecta_la_sesion_en_marcha(make_controller):
    ctrl = make_controller(brain="gemini_live")
    ctrl.start()
    assert ctrl.live_session.running
    starts, stops = ctrl.live_session.start_calls, ctrl.live_session.stop_calls

    ctrl._on_model_changed("gemini_live", "gemini-3.1-flash-live-preview")
    assert ctrl.live_session.model == "gemini-3.1-flash-live-preview"
    assert ctrl.live_session.running
    assert ctrl.live_session.stop_calls == stops + 1
    assert ctrl.live_session.start_calls == starts + 1

    # El mismo modelo otra vez no corta la conversación.
    ctrl._on_model_changed("gemini_live", "gemini-3.1-flash-live-preview")
    assert ctrl.live_session.start_calls == starts + 1


def test_cambiar_modelo_live_sin_sesion_no_la_arranca(make_controller):
    ctrl = make_controller()
    ctrl.start()
    ctrl._on_model_changed("gemini_live", "gemini-3.1-flash-live-preview")
    assert not ctrl.live_session.running
    assert ctrl.live_session.model == "gemini-3.1-flash-live-preview"


def test_recargar_modelos_vuelve_a_consultar_ollama_y_gemini(make_controller, monkeypatch):
    installed = [["llama3.2:3b"]]
    gemini_calls = []
    monkeypatch.setattr(main, "list_ollama_models", lambda host: list(installed[0]))
    monkeypatch.setattr(main.config, "GEMINI_API_KEY", "clave")

    def fake_list(key):
        gemini_calls.append(key)
        return {"models/gemini-3.5-flash": ["generateContent"]}

    monkeypatch.setattr(main, "list_gemini_models", fake_list)
    ctrl = make_controller()
    ctrl.brain.ollama.model = "llama3.2:3b"
    ollama_lists, gemini_lists = [], []
    direct = QtCore.Qt.DirectConnection  # las señales salen del hilo de consulta
    ctrl.sig_ollama_models.connect(lambda models, cur: ollama_lists.append(models), direct)
    ctrl.sig_gemini_models.connect(lambda chat, live: gemini_lists.append(chat), direct)

    ctrl.request_models_refresh()
    assert _wait_for(lambda: gemini_lists and not ctrl._models_refresh_lock.locked())
    assert ollama_lists[-1] == ["llama3.2:3b"]
    assert gemini_lists[-1] == ["gemini-3.5-flash"]

    installed[0] = ["llama3.2:3b", "qwen3:8b"]  # `ollama pull qwen3:8b` con Zara abierta
    ctrl.request_models_refresh()
    assert _wait_for(lambda: len(ollama_lists) == 2 and not ctrl._models_refresh_lock.locked())
    assert ollama_lists[-1] == ["llama3.2:3b", "qwen3:8b"]
    assert len(gemini_calls) == 1  # Gemini no se consulta en cada apertura del panel
