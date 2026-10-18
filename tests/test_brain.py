"""Pruebas unitarias para core/brain.py (ZARA v2.0)."""

import pytest

from core.brain import (
    OllamaProvider,
    ZaraBrain,
    ZaraRouter,
    _extract_embedded_tool_call,
    _pick_default_ollama_model,
    _summarize_observations_locally,
)


def test_pick_default_ollama_model_prefers_coder_variant():
    """[Autodetección] Entre varios modelos instalados, se prefiere uno con
    'coder' en el nombre (suele seguir mejor las instrucciones de
    tool-calling); si no hay ninguno, se usa el primero de la lista."""
    assert _pick_default_ollama_model([]) == ""
    assert _pick_default_ollama_model(["llama3.1:8b", "mistral:7b"]) == "llama3.1:8b"
    assert _pick_default_ollama_model(
        ["llama3.1:8b", "huihui_ai/qwen2.5-coder-abliterate:7b-instruct", "mistral:7b"]
    ) == "huihui_ai/qwen2.5-coder-abliterate:7b-instruct"


def test_ollama_provider_autodetects_model_when_unconfigured(monkeypatch):
    """[Regresión] Sin OLLAMA_MODEL en .env (model=""), OllamaProvider debe
    autodetectar consultando los modelos instalados, en vez de quedarse con
    un nombre fijo que puede no existir en esta máquina."""
    monkeypatch.setattr(
        OllamaProvider, "list_models", lambda self: ["mistral:7b", "qwen2.5-coder:7b"]
    )

    provider = OllamaProvider(model="")
    assert provider.model == "qwen2.5-coder:7b"


def test_ollama_provider_keeps_explicit_model(monkeypatch):
    """Si OLLAMA_MODEL SÍ está configurado, no se toca ni se consulta nada."""
    calls = []
    monkeypatch.setattr(
        OllamaProvider, "list_models", lambda self: calls.append(1) or ["otro:modelo"]
    )

    provider = OllamaProvider(model="mi-modelo-fijo:latest")
    assert provider.model == "mi-modelo-fijo:latest"
    assert calls == []


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
    assert ZaraRouter.is_conversational("mi ip") is False
    assert ZaraRouter.is_conversational("telemetría") is False
    assert ZaraRouter.is_conversational("pausa música") is False
    assert ZaraRouter.is_conversational("minimiza todo") is False
    assert ZaraRouter.is_conversational("cuánta ram") is False


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


def test_extract_embedded_tool_call_accepts_hallucinated_action_as_name():
    """[Regresión, hallazgo en vivo contra Ollama real] El modelo a veces
    llama a {"name": "open_app", ...} confundiendo el VALOR del enum
    "action" de os_control con el nombre de la herramienta. Si se pasan las
    definiciones de herramientas, debe aceptarse (sin remapear aquí; eso lo
    hace execute_tool) en vez de descartarse como nombre desconocido."""
    from core.tools import TOOL_DEFINITIONS

    known_tools = {"run_shell", "file_manager", "os_control", "web_search"}
    text = '{"name": "open_app", "arguments": {"target": "Bloc de notas"}}'

    # Sin las definiciones, se descarta (comportamiento anterior preservado).
    assert _extract_embedded_tool_call(text, known_tools) is None

    # Con las definiciones, se acepta para que llegue a execute_tool().
    call = _extract_embedded_tool_call(text, known_tools, TOOL_DEFINITIONS)
    assert call is not None
    assert call["name"] == "open_app"
    assert call["arguments"]["target"] == "Bloc de notas"


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


def test_run_react_agent_stops_on_cancel(monkeypatch):
    """[AUDITORÍA DE VOZ, regresión] Un barge-in debe cancelar de verdad la
    generación en curso, no solo el audio. Antes cancel_current_turn() no
    existía: el bucle ReAct seguía produciendo tokens/herramientas de un
    turno ya interrumpido, que acababan reproduciéndose igualmente poco
    después."""
    brain = ZaraBrain()

    def fake_stream(*args, **kwargs):
        for i in range(10):
            if i == 3:
                # Simula el barge-in llegando a mitad de la respuesta.
                brain.cancel_current_turn()
            yield {"type": "token", "token": f"tok{i} "}

    monkeypatch.setattr(brain, "_execute_stream_with_fallback", lambda *a, **k: fake_stream())

    tokens = list(brain.run_react_agent("hola, cuenta hasta diez", preferred_provider="ollama"))

    assert len(tokens) < 10, "el bucle ReAct no se detuvo tras la cancelación"
    assert not any(m.get("role") == "assistant" for m in brain.history), (
        "un turno cancelado no debe dejar una respuesta a medias en el historial"
    )


def test_react_loop_exhaustion_yields_summary_and_keeps_alternation(monkeypatch):
    """[AUDITORÍA, hallazgo de Gemini] Si se agotan los pasos del bucle ReAct
    mientras el modelo sigue pidiendo herramientas (nunca llega a una
    respuesta final), el agente debe responder algo en vez de terminar en
    silencio, y el historial no debe quedar con dos mensajes 'user'
    consecutivos (la observación huérfana + la siguiente pregunta real)."""
    import core.brain as brain_module

    brain = ZaraBrain()
    monkeypatch.setattr(brain_module, "MAX_REACT_STEPS", 2)

    call_counter = {"n": 0}

    def always_calls_tool(*args, **kwargs):
        # El modelo nunca da una respuesta final: siempre vuelve a pedir la
        # misma herramienta con argumentos DISTINTOS (contador explícito, no
        # id() que CPython podría reciclar) para no activar el guardarraíl
        # de "herramienta repetida sin progreso" y forzar el agotamiento real
        # de MAX_REACT_STEPS.
        call_counter["n"] += 1
        yield {"type": "tool_call", "name": "web_search", "args": {"query": f"consulta-{call_counter['n']}"}}

    monkeypatch.setattr(brain, "_execute_stream_with_fallback", always_calls_tool)
    monkeypatch.setattr(
        "core.brain.execute_tool",
        lambda name, args: {"success": True, "results": []},
    )

    outputs = list(brain.run_react_agent("busca en la web lo último de ia", preferred_provider="ollama"))

    assert outputs, "el agente no debe quedarse en silencio al agotar los pasos ReAct"
    assert brain.history[-1]["role"] == "assistant", (
        "debe cerrarse con un mensaje assistant, no dejar la observación huérfana como último mensaje"
    )

    # Simular el siguiente turno: no debe haber dos 'user' consecutivos.
    brain.history.append({"role": "user", "content": "siguiente pregunta"})
    roles = [m["role"] for m in brain.history]
    for prev, nxt in zip(roles, roles[1:]):
        assert not (prev == "user" and nxt == "user"), "dos mensajes 'user' consecutivos en el historial"


def test_cancel_current_turn_is_per_turn(monkeypatch):
    """Una cancelación de un turno anterior no debe afectar al turno
    SIGUIENTE: run_react_agent limpia el flag al empezar."""
    brain = ZaraBrain()
    brain.cancel_current_turn()  # cancelación "residual" de un turno previo

    def fake_stream(*args, **kwargs):
        yield {"type": "token", "token": "hola Señor VK"}

    monkeypatch.setattr(brain, "_execute_stream_with_fallback", lambda *a, **k: fake_stream())

    tokens = list(brain.run_react_agent("hola", preferred_provider="ollama"))
    assert tokens == ["hola Señor VK"], "el nuevo turno no debería heredar la cancelación del anterior"


def test_history_trimming():
    brain = ZaraBrain()
    # Llenar historial con más de 40 mensajes
    for i in range(50):
        brain.history.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"})
    brain._trim_history()
    assert len(brain.history) <= 40
    assert brain.history[-1]["content"] == "msg 49"
