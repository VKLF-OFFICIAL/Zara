from zara.audio.wake import WakeGate


def _gate(conversation=False):
    t = [0.0]
    return WakeGate(window=8.0, clock=lambda: t[0], conversation=conversation), t


def test_wake_only_acks_and_opens_window():
    g, t = _gate()
    assert g.route("Zara.") == ("ack", None)
    t[0] = 3
    assert g.route("qué hora es") == ("order", "qué hora es")


def test_window_expires():
    g, t = _gate()
    g.route("Zara")
    t[0] = 9
    assert g.route("qué hora es") == (None, None)


def test_window_consumed_after_order():
    g, t = _gate()
    g.route("Zara")
    t[0] = 2
    g.route("qué hora es")
    t[0] = 3
    assert g.route("otra cosa") == (None, None)


def test_wake_with_order():
    g, _ = _gate()
    assert g.route("Zara, abre firefox") == ("order", "abre firefox")


def test_not_addressed():
    g, _ = _gate()
    assert g.route("hola qué tal") == (None, None)
    assert g.route("   ") == (None, None)


def test_conversation_mode_renews_window():
    g, t = _gate(conversation=True)
    assert g.route("Zara, hola") == ("order", "hola")
    t[0] = 5
    assert g.route("y mañana qué") == ("order", "y mañana qué")


def test_neural_wake_counts_as_addressed():
    # El detector neural disparó aunque el texto no casara «Zara».
    g, _ = _gate()
    assert g.route("pon música", neural=True) == ("order", "pon música")


def test_neural_wake_without_text_is_ack():
    g, _ = _gate()
    assert g.route("", neural=True) == ("ack", None)


def test_neural_false_still_requires_wake():
    g, _ = _gate()
    assert g.route("pon música", neural=False) == (None, None)
