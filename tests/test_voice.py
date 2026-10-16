"""Pruebas unitarias para core/voice.py (ZARA v2.0)."""

import pytest
from core.voice import SentenceChunker, clean_text_for_speech


def test_clean_text_for_speech_markdown_removal():
    raw = "Hola **Señor VK**, he ejecutado `ls -la` y encontré *3* archivos."
    cleaned = clean_text_for_speech(raw)
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "*" not in cleaned
    assert "Señor VK" in cleaned
    assert "ls -la" in cleaned


def test_clean_text_code_block_replacement():
    raw = "Mire este código:\n```python\nprint('hello')\n```\n¿Desea ejecutarlo?"
    cleaned = clean_text_for_speech(raw)
    assert "print('hello')" not in cleaned
    assert "código omitido en voz" in cleaned


def test_sentence_chunker_basic():
    chunker = SentenceChunker(min_length=20)
    tokens = ["Sistemas ", "tácticos ", "operativos, ", "Señor VK. ", "Todo en orden."]
    chunks = []
    for tok in tokens:
        res = chunker.push(tok)
        chunks.extend(res)
    # Debe haber emitido al menos una frase completa antes del final
    assert len(chunks) >= 1
    assert "Sistemas tácticos operativos, Señor VK." in chunks[0]

    # Vaciar buffer remanente
    rem = chunker.flush()
    if rem:
        chunks.append(rem)
    assert len(chunks) >= 2


def test_sentence_chunker_short_abbreviation_not_cut():
    chunker = SentenceChunker(min_length=30)
    # "Sr. " es muy corto, no debe emitirse como frase aislada
    res1 = chunker.push("Buenas tardes Sr. ")
    assert len(res1) == 0

    res2 = chunker.push("VK, los escaneos de red han finalizado correctamente.")
    assert len(res2) == 1
    assert "Buenas tardes Sr. VK" in res2[0]


def test_sentence_chunker_flush_empty():
    chunker = SentenceChunker(min_length=20)
    assert chunker.flush() == []
