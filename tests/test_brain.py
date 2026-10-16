"""Pruebas unitarias para core/brain.py (ZARA v2.0)."""

import pytest

from core.brain import (
    ZaraBrain,
    ZaraRouter,
    _extract_embedded_tool_call,
    _summarize_observations_locally,
)


def test_router_is_conversational():
    # Saludos y preguntas de cortesía deben ser True
    assert ZaraRouter.is_conversational("hola") is True
    assert ZaraRouter.is_conversational("Hola Zara") is True
    assert ZaraRouter.is_conversational("buenos días") is True
    assert ZaraRouter.is_conversational("¿cómo estás?") is True
    assert ZaraRouter.is_conversational("qué hora es") is True
    assert ZaraRouter.is_conversational("gracias") is True
    assert ZaraRouter.is_conversational("adiós") is True

    # Órdenes operativas del sistema deben ser False
    assert ZaraRouter.is_conversational("abre el bloc de notas") is False
    assert ZaraRouter.is_conversational("ejecuta un escaneo nmap a 192.168.1.1") is False
    assert ZaraRouter.is_conversational("busca en la web las noticias de hoy") is False
    assert ZaraRouter.is_conversational("lista los archivos del directorio actual") is False
    assert ZaraRouter.is_conversational("crea un script en python") is False
    assert ZaraRouter.is_conversational("cierra la ventana") is False


def test_router_classify_keywords():
    assert ZaraRouter.classify("analiza este código para encontrar un bug", preferred="auto") == "claude"
    assert ZaraRouter.classify("mira la pantalla y dime qué error hay", preferred="auto") in ("gemini", "claude", "ollama")


def test_extract_embedded_tool_call():
    known_tools = {"run_shell", "file_manager", "os_control", "web_search"}

    # Bloque JSON dentro de markdown
    fenced_text = '```json\n{"name": "os_control", "arguments": {"action": "list_processes"}}\n```'
    call = _extract_embedded_tool_call(fenced_text, known_tools)
    assert call is not None
    assert call["name"] == "os_control"
    assert call["arguments"]["action"] == "list_processes"

    # JSON plano sin markdown
    bare_text = '{"name": "web_search", "arguments": {"query": "clima madrid"}}'
    call2 = _extract_embedded_tool_call(bare_text, known_tools)
    assert call2 is not None
    assert call2["name"] == "web_search"

    # Texto conversacional sin herramientas
    chat_text = "Hola Señor VK, estoy a su completa disposición."
    assert _extract_embedded_tool_call(chat_text, known_tools) is None


def test_summarize_observations_locally():
    obs = [
        'Resultado de os_control: {"success": true, "message": "Volumen ajustado al 75%."}',
    ]
    summary = _summarize_observations_locally(obs)
    assert "Volumen ajustado al 75%" in summary

    obs_web = [
        'Resultado de web_search: {"success": true, "results": [{"title": "Noticia 2026", "snippet": "Avances en IA cuántica."}]}',
    ]
    summary_web = _summarize_observations_locally(obs_web)
    assert "Avances en IA cuántica" in summary_web


def test_history_trimming():
    brain = ZaraBrain()
    # Llenar historial con más de 40 mensajes
    for i in range(50):
        brain.history.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"})
    brain._trim_history()
    assert len(brain.history) <= 40
    assert brain.history[-1]["content"] == "msg 49"
