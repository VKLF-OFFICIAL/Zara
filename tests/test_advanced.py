"""Pruebas avanzadas y de estrés para ZARA v2.0."""

import json
import pytest
from core.brain import list_ollama_models, _extract_embedded_tool_call
from core.tools import file_manager, run_shell, web_search, is_command_forbidden
from core.voice import clean_text_for_speech, SentenceChunker


def test_clean_text_special_characters_and_emojis():
    raw = "¡Alerta táctica! 🚀 Se detectó un puerto abierto: 8080/tcp. ¿Proceder? 🤔"
    cleaned = clean_text_for_speech(raw)
    assert "Alerta táctica" in cleaned
    assert "8080/tcp" in cleaned
    assert "Proceder" in cleaned


def test_sentence_chunker_long_monologue():
    chunker = SentenceChunker(min_length=35)
    long_text = (
        "Señor VK, he analizado la configuración del sistema operativo. "
        "Todos los procesos en segundo plano presentan un consumo normal de memoria y CPU. "
        "No se registran anomalías en los registros del firewall. "
        "Procedo a mantener la escucha pasiva en los canales de audio."
    )
    tokens = long_text.split(" ")
    emitted = []
    for tok in tokens:
        res = chunker.push(tok + " ")
        emitted.extend(res)
    rem = chunker.flush()
    emitted.extend(rem)

    # Debe haber emitido al menos 3 frases
    assert len(emitted) >= 3
    for chunk in emitted:
        assert len(chunk) > 10


def test_file_manager_edge_cases():
    # Intento de leer archivo inexistente
    res_no = file_manager("read", "non_existent_path_xyz_123.bin")
    assert res_no["success"] is False
    assert "no existe" in res_no["error"]

    # Intento de editar sin encontrar el texto previo (debe fallar limpiamente sin excepción)
    import config
    res_edit_bad = file_manager("edit", str(config.BASE_DIR / "config.py"), old_str="TEXTO_IMPOSIBLE_DE_ENCONTRAR_XYZ", new_str="NADA")
    assert res_edit_bad["success"] is False
    assert "no fue encontrado" in res_edit_bad["error"]


def test_web_search_resilience():
    # Caracteres especiales y comillas en la búsqueda
    res = web_search("python \"machine learning\" 2026", max_results=2)
    assert res["success"] is True
    assert isinstance(res["results"], list)


def test_tool_extraction_with_malformed_json():
    known = {"run_shell", "file_manager"}
    # JSON roto no debe romper el sistema ni lanzar excepción
    assert _extract_embedded_tool_call("{name: 'run_shell', args:", known) is None
    assert _extract_embedded_tool_call("Texto plano sin nada", known) is None
    assert _extract_embedded_tool_call("", known) is None


def test_forbidden_guardrail_edge_variations():
    variations = [
        "sudo rm -rf /",
        "rm -rf / --no-preserve-root",
        "rm -r -f /",
        "rm -Rf /",
        "rm -r -f /etc/shadow",
        "mkfs.vfat /dev/sdb1",
        "mkfs.ext4 /dev/nvme0n1p2",
        "Format-Volume -DriveLetter E",
    ]
    for v in variations:
        assert is_command_forbidden(v) is not None, f"Falló guardarraíl en variación: {v}"
