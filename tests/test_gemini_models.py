from core.gemini_models import choose_gemini_model

AVAILABLE = {
    "models/gemini-2.5-flash": ["generateContent"],
    "models/gemini-2.5-flash-lite": ["generateContent"],
    "models/gemini-3.5-flash": ["generateContent"],
    "models/gemini-3.6-flash-preview-09-2026": ["generateContent"],
    "models/gemini-2.5-flash-native-audio-preview-12-2025": ["bidiGenerateContent"],
    "models/gemini-3.1-flash-live-preview": ["bidiGenerateContent"],
    "models/gemini-2.5-pro": ["generateContent"],
}


def test_auto_prefers_latest_stable_flash_without_lite_or_live():
    assert choose_gemini_model(AVAILABLE, "auto") == "gemini-3.5-flash"


def test_preferred_is_respected_when_it_exists():
    assert choose_gemini_model(AVAILABLE, "gemini-2.5-flash") == "gemini-2.5-flash"


def test_retired_preferred_falls_back_to_best():
    assert choose_gemini_model(AVAILABLE, "gemini-2.0-flash-live-001", live=True) == "gemini-3.1-flash-live-preview"


def test_live_picks_live_capable_models_only():
    assert "live" in choose_gemini_model(AVAILABLE, "auto", live=True) or "audio" in choose_gemini_model(AVAILABLE, "auto", live=True)


def test_empty_listing_uses_fallback():
    assert choose_gemini_model({}, "auto", fallback="gemini-2.5-flash") == "gemini-2.5-flash"
