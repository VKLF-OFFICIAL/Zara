from zara.voice.stream import SentenceChunker
from zara.voice.text import clean_for_speech


def test_chunker_emits_on_punctuation_and_min_length():
    c = SentenceChunker(min_length=35)
    out = c.push("Por supuesto, Señor VK, inicio los protocolos de diagnóstico ahora.")
    assert len(out) == 1
    assert out[0].startswith("Por supuesto")


def test_chunker_holds_short_fragments():
    c = SentenceChunker(min_length=35)
    # Fragmento corto: no se emite hasta acumular longitud suficiente.
    assert c.push("Hola.") == []
    rest = c.push(" Ahora sí tenemos una frase bastante más larga que el umbral.")
    assert len(rest) == 1


def test_chunker_flush_returns_remainder():
    c = SentenceChunker(min_length=35)
    c.push("Fragmento sin puntuación final")
    out = c.flush()
    assert out and "Fragmento" in out[0]


def test_chunker_streams_sentence_by_sentence():
    c = SentenceChunker(min_length=10)
    sentences = []
    for tok in ["El servicio ", "está activo. ", "Todo en orden, Señor VK."]:
        sentences.extend(c.push(tok))
    sentences.extend(c.flush())
    assert len(sentences) == 2


def test_clean_strips_markdown_and_code():
    dirty = "Aquí tiene: ```python\nprint('x')\n``` y un `comando` con **negrita** y https://x.io"
    clean = clean_for_speech(dirty)
    assert "`" not in clean and "*" not in clean and "```" not in clean
    assert "print" not in clean
    assert "http" not in clean


def test_clean_normalizes_units():
    assert "por ciento" in clean_for_speech("uso al 80%")
