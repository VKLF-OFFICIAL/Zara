"""Pruebas unitarias para core/live.py y el modo Gemini Live (ZARA v2.0)."""

import pytest
from unittest.mock import MagicMock, patch

from core.live import (
    GeminiLiveSession,
    LIVE_INPUT_SAMPLE_RATE,
    LIVE_OUTPUT_SAMPLE_RATE,
    LIVE_MODEL_CANDIDATES,
    LIVE_VOICE_NAME,
)
from core.tools import TOOL_DEFINITIONS
from ui.hud import ZaraPanel, HUDState


def test_gemini_live_session_initialization():
    session = GeminiLiveSession(api_key="test-key-123")
    assert session._api_key == "test-key-123"
    assert session.is_running() is False
    assert session.is_model_speaking() is False
    assert session.is_muted() is False


def test_gemini_live_session_mute_toggle():
    session = GeminiLiveSession(api_key="test-key-123")
    assert session.is_muted() is False

    session.set_muted(True)
    assert session.is_muted() is True

    session.set_muted(False)
    assert session.is_muted() is False


def test_gemini_live_availability():
    # Sin API key
    empty_session = GeminiLiveSession(api_key="")
    assert empty_session.is_available() is False

    # Con API key y SDK instalado
    valid_session = GeminiLiveSession(api_key="valid-key-xyz")
    assert valid_session.is_available() is True


def test_gemini_live_model_resolution():
    session = GeminiLiveSession(api_key="key", model="gemini-2.0-flash-live-001")
    assert session._resolve_live_model() == "gemini-2.0-flash-live-001"

    session.set_model("gemini-2.5-flash-preview-native-audio-dialog")
    assert session._resolve_live_model() == "gemini-2.5-flash-preview-native-audio-dialog"


def test_gemini_live_audio_parameters():
    assert LIVE_INPUT_SAMPLE_RATE == 16000
    assert LIVE_OUTPUT_SAMPLE_RATE == 24000
    assert len(LIVE_MODEL_CANDIDATES) >= 1
    assert "Aoede" in LIVE_VOICE_NAME or len(LIVE_VOICE_NAME) > 0


def test_gemini_live_build_live_tools():
    session = GeminiLiveSession(api_key="key")
    from google.genai import types

    tools = session._build_live_tools(types)
    assert len(tools) == 1
    assert hasattr(tools[0], "function_declarations")
    decls = tools[0].function_declarations
    assert len(decls) == len(TOOL_DEFINITIONS)
    names = [d.name for d in decls]
    assert "run_shell" in names
    assert "web_search" in names
    assert "os_control" in names


def test_gemini_live_interrupt_and_flush():
    session = GeminiLiveSession(api_key="key")
    # Simular audio en cola
    session._playback_queue.put(b"audio-bytes-chunk-1")
    session._playback_queue.put(b"audio-bytes-chunk-2")
    session._is_model_speaking = True

    session.interrupt()

    assert session.is_model_speaking() is False
    assert session._playback_queue.empty() is True


def test_hud_tactical_panel_has_gemini_live():
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = ZaraPanel()

    # Verificar que "gemini_live" está en las opciones de cerebro
    brain_values = [panel.brain_combo.itemData(i) for i in range(panel.brain_combo.count())]
    assert "gemini_live" in brain_values

    # Simular cambio a gemini_live
    idx = brain_values.index("gemini_live")
    panel.brain_combo.setCurrentIndex(idx)

    # Verificar que el combo de modelos contiene los modelos Live
    model_values = [panel.model_combo.itemData(i) for i in range(panel.model_combo.count())]
    assert "gemini-2.5-flash-preview-native-audio-dialog" in model_values
    assert "gemini-2.0-flash-live-001" in model_values

