from zara.audio.wake import extract_after_wake, has_wake


def test_detects_wake_at_start():
    assert has_wake("Zara, abre el navegador")
    assert has_wake("zara que hora es")
    assert has_wake("Oye Zara, ayúdame")  # dentro de las primeras palabras


def test_no_wake():
    assert not has_wake("abre el navegador")
    assert not has_wake("mañana sara vendrá")  # 'sara' no es 'zara'


def test_extract_after_wake_strips_prefix():
    assert extract_after_wake("Zara, ¿qué hora es?") == "¿qué hora es?"
    assert extract_after_wake("zara abre firefox") == "abre firefox"


def test_extract_without_wake_returns_original():
    assert extract_after_wake("hola qué tal") == "hola qué tal"


def test_tolerates_common_mishears():
    # Whisper a veces oye 'sara'/'zahra' — los aceptamos como wake.
    assert has_wake("Sara enciende la música")
    assert has_wake("Zhara, apaga la luz")


def test_accepts_phonetic_mishears_seen_in_real_use():
    # Transcripciones reales/sintéticas de «Zara» en el micro del Señor VK.
    assert has_wake("¡Vara! ¿Qué tiempo se ve hoy?")
    assert has_wake("Sada.")
    assert has_wake("Zahara, abre firefox")
    assert has_wake("Tsara qué hora es")


def test_rejects_common_spanish_words():
    for t in ["para mañana quiero", "cara bonita", "claro que sí", "nada más",
              "rara vez", "Clara vendrá"]:
        assert not has_wake(t), t


def test_extract_after_phonetic_wake():
    assert extract_after_wake("¡Vara! ¿Qué tiempo hace hoy?") == "¿Qué tiempo hace hoy?"
    assert extract_after_wake("Sada, sube el volumen") == "sube el volumen"


def test_detects_jarvis_wake():
    # El detector neural usa «hey jarvis»; también se admite por texto.
    assert has_wake("Jarvis, pon música")
    assert has_wake("hey jarvis qué hora es")
    assert has_wake("Oye Jarvis, ayúdame")


def test_jarvis_not_confused_with_common_words():
    for t in ["para mañana", "cara bonita", "jardín bonito", "jamás volveré"]:
        assert not has_wake(t), t


def test_extract_after_jarvis_wake():
    assert extract_after_wake("Jarvis, pon música") == "pon música"
    assert extract_after_wake("hey jarvis abre firefox") == "abre firefox"


def test_weak_mishear_as_vocative_is_wake():
    # Whisper oye «Zara»→«Para» pero con coma (vocativo) y orden detrás: es Zara.
    assert has_wake("Para, ¿cuál es mi IP pública?")
    assert has_wake("Clara, abre el navegador")
    assert has_wake("Cara, sube el volumen")
    assert extract_after_wake("Para, ¿cuál es mi IP pública?") == "¿cuál es mi IP pública?"


def test_weak_mishear_without_vocative_is_not_wake():
    # Sin coma, son palabras normales: no deben activar.
    assert not has_wake("para mañana quiero un café")
    assert not has_wake("Clara vendrá mañana")
    assert not has_wake("cara bonita la tuya")
    assert not has_wake("Para,")   # coma pero sin orden detrás
    assert not has_wake("Para")    # suelta
