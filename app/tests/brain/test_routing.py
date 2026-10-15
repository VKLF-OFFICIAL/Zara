from zara.brain.routing import needs_agent


def test_tasks_need_agent():
    for t in ["créame un script de backup", "crea una carpeta en el escritorio",
              "programa un juego de snake", "arregla el bug del login",
              "edita el archivo config.py", "instala docker",
              "automatiza la copia de mis documentos", "haz un script que ordene fotos",
              "refactoriza este código", "compila el proyecto",
              "descarga el repo y analízalo", "configura nginx",
              "genera un proyecto de react", "modifica el archivo hosts"]:
        assert needs_agent(t), t


def test_chat_does_not_need_agent():
    for t in ["qué es python", "explícame la fotosíntesis", "quién fue Napoleón",
              "cómo estás", "qué opinas de la IA", "cuéntame un chiste",
              "cuánto es 2 por 3", "qué hora es", "hola", "traduce hola al inglés",
              "¿qué tiempo hace?", "dame ideas para una cena"]:
        assert not needs_agent(t), t


def test_explicit_agent_keyword():
    assert needs_agent("modo agente, revisa mi sistema")
    assert needs_agent("ponte en modo agente")
