from zara.voice.text import speech_text


def test_strips_markdown_symbols():
    out = speech_text("**No**, Señor. Use `ls` y # nada _más_")
    for ch in "*`#_":
        assert ch not in out
    assert "No, Señor" in out


def test_links_keep_label():
    assert "documentación" in speech_text("Vea la [documentación](https://x.com/doc).")
    assert "https" not in speech_text("Vea la [documentación](https://x.com/doc).")


def test_code_block_replaced():
    out = speech_text("Hola.\n```bash\nrm -rf x\n```\nFin.")
    assert "rm -rf" not in out
    assert "panel" in out
    assert out.startswith("Hola")


def test_bullets_flattened():
    out = speech_text("Opciones:\n- uno\n- dos")
    assert "-" not in out
    assert "uno" in out and "dos" in out


def test_long_text_truncated_at_sentence():
    long = " ".join(f"Frase número {i}." for i in range(60))
    out = speech_text(long, max_chars=120)
    assert len(out) < 200
    assert "panel" in out


def test_unit_and_abbreviation_normalization():
    out = speech_text("CPU al 40%, temperatura 25°C, disco con 15GB libres y viento a 20km/h.")
    assert "por ciento" in out
    assert "grados" in out
    assert "gigas" in out
    assert "kilómetros por hora" in out
    assert "C P U" in out


def test_xml_and_ssml_characters_escaped():
    out = speech_text("Valor < 10 & > 5")
    assert "<" not in out
    assert ">" not in out
    assert "&" not in out
    assert "menor que" in out
    assert "mayor que" in out

