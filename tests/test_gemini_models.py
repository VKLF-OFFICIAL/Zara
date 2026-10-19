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


def test_retired_model_is_treated_as_auto(monkeypatch):
    import core.gemini_models as gm

    monkeypatch.setattr(gm, "list_gemini_models", lambda key: AVAILABLE)
    got = gm.resolve_gemini_model("k", "gemini-2.5-flash-preview-native-audio-dialog", live=True, fallback="x")
    assert got == "gemini-3.1-flash-live-preview"
    # un ID vigente fijado a mano no se toca ni consulta la red
    monkeypatch.setattr(gm, "list_gemini_models", lambda key: (_ for _ in ()).throw(AssertionError("sin red")))
    assert gm.resolve_gemini_model("k", "gemini-2.5-flash", live=False, fallback="x") == "gemini-2.5-flash"
