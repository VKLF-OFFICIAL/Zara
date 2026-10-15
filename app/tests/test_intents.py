import pytest
from zara.intents import parse_intent


@pytest.mark.parametrize("text,kind,arg", [
    ("abre el panel", "show_panel", None),
    ("muestra el chat", "show_panel", None),
    ("sube el volumen", "volume_up", None),
    ("baja el volumen", "volume_down", None),
    ("pon el volumen al 40", "set_volume", "40"),
    ("silencia el sonido", "mute", None),
    ("quita el silencio", "unmute", None),
    ("pausa la música", "media_pause", None),
    ("reanuda la música", "media_play", None),
    ("pon música", "media_play", None),
    ("siguiente canción", "media_next", None),
    ("canción anterior", "media_prev", None),
    ("¿qué hora es?", "time", None),
    ("qué día es hoy", "date", None),
    ("abre firefox", "open_app", "firefox"),
    ("abre el navegador", "open_app", "navegador"),
    ("ejecuta ls -la /tmp", "run", "ls -la /tmp"),
    ("para", "stop_speaking", None),
    ("cállate", "stop_speaking", None),
])
def test_parse_intent(text, kind, arg):
    it = parse_intent(text)
    assert it is not None and it.kind == kind
    if arg is not None:
        assert it.arg == arg


def test_screen_request_is_a_screen_look_intent():
    # La visión de pantalla está activa (bajo orden explícita; la captura se borra).
    it = parse_intent("mira mi pantalla y dime qué error sale")
    assert it is not None and it.kind == "screen_look"


@pytest.mark.parametrize("text", [
    "¿para qué sirve Python?",
    "explícame la fotosíntesis",
    "hola qué tal",
    "¿qué opinas de la seguridad de mi web?",
])
def test_normal_questions_are_not_intents(text):
    assert parse_intent(text) is None


def test_run_keeps_original_case():
    assert parse_intent("Ejecuta echo HolaMundo").arg == "echo HolaMundo"


@pytest.mark.parametrize("text,kind,arg", [
    ("reproduce música en Spotify", "play_in_app", "spotify"),
    ("le produce música en Spotify", "play_in_app", "spotify"),   # transcripción real
    ("pon Spotify", "play_in_app", "spotify"),
    ("pon música en spotify", "play_in_app", "spotify"),
    ("cierra spotify", "close_app", "spotify"),
    ("cierra el navegador", "close_app", "navegador"),
    ("abre la carpeta descargas", "open_folder", "descargas"),
    ("abre mis documentos", "open_folder", "documentos"),
])
def test_control_intents(text, kind, arg):
    it = parse_intent(text)
    assert it is not None and it.kind == kind, it
    assert it.arg == arg


def test_web_search_google_and_youtube():
    it = parse_intent("busca recetas de paella en google")
    assert it.kind == "web_search" and it.arg == ("google", "recetas de paella")
    it = parse_intent("busca en youtube música lofi")
    assert it.kind == "web_search" and it.arg == ("youtube", "música lofi")
    it = parse_intent("busca el clima de Madrid")
    assert it.kind == "web_search" and it.arg == ("google", "el clima de Madrid")


@pytest.mark.parametrize("text,kind", [
    ("estado del sistema", "status"),
    ("cómo está el equipo", "status"),
    ("cómo andamos", "status"),
    ("qué puedes hacer", "help"),
    ("ayuda", "help"),
    ("bloquea el equipo", "lock"),
    ("bloquea la pantalla", "lock"),
    ("haz una captura", "screenshot"),
    ("captura de pantalla", "screenshot"),
])
def test_jarvis_intents(text, kind):
    it = parse_intent(text)
    assert it is not None and it.kind == kind, (text, it)


@pytest.mark.parametrize("text,arg", [
    ("apaga el equipo", "poweroff"),
    ("reinicia el equipo", "reboot"),
    ("suspende el equipo", "suspend"),
    ("apágate", "poweroff"),
])
def test_power_intents(text, arg):
    it = parse_intent(text)
    assert it.kind == "power" and it.arg == arg


@pytest.mark.parametrize("text,place", [
    ("qué tiempo hace", ""),
    ("qué tiempo hace en Madrid", "Madrid"),
    ("el tiempo en San Sebastián", "San Sebastián"),
    ("dime el clima", ""),
])
def test_weather_intent(text, place):
    it = parse_intent(text)
    assert it.kind == "weather" and it.arg == place


@pytest.mark.parametrize("text", [
    "recuérdame en 10 minutos que llame a Ana",
    "pon una alarma a las 8:30",
    "avísame en media hora",
])
def test_reminder_intent(text):
    it = parse_intent(text)
    assert it.kind == "reminder" and it.arg == text


def test_weather_not_triggered_by_question_about_feelings():
    assert parse_intent("qué opinas del tiempo libre") is None or \
        parse_intent("qué opinas del tiempo libre").kind != "weather"


@pytest.mark.parametrize("text,arg", [
    ("mira la pantalla", ""),
    ("qué ves en la pantalla", ""),
    ("qué hay en mi pantalla", ""),
    ("lee la pantalla", ""),
    ("describe la pantalla", ""),
    ("mira la pantalla y dime qué error hay", "dime que error hay"),
    ("analiza la pantalla y busca errores", "busca errores"),
])
def test_screen_look_intent(text, arg):
    it = parse_intent(text)
    assert it is not None and it.kind == "screen_look" and it.arg == arg


def test_screen_look_not_triggered_without_screen_word():
    for t in ["mira esto", "lee el documento", "describe la foto"]:
        it = parse_intent(t)
        assert it is None or it.kind != "screen_look", t
