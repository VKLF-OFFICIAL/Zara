from zara.audio.echo import is_echo, normalize


def test_normalize():
    assert normalize("¿Sí, Señor VK?") == "si senor vk"


def test_detects_exact_echo():
    assert is_echo("Son las 23:40, Señor VK.", ["Son las 23:40, Señor VK."])


def test_detects_partial_echo():
    spoken = ["Reproduciendo música en Spotify, Señor VK."]
    assert is_echo("reproduciendo música en Spotify señor", spoken)


def test_detects_ack_echo():
    assert is_echo("Sí, Señor VK", ["¿Sí, Señor VK?"])


def test_real_command_is_not_echo():
    spoken = ["Son las 23:40, Señor VK.", "Reproduciendo música en Spotify, Señor VK."]
    assert not is_echo("sube el volumen", spoken)
    assert not is_echo("qué hora es", spoken)
    assert not is_echo("abre firefox", spoken)


def test_empty_history_never_echo():
    assert not is_echo("hola", [])


def test_command_mentioning_senor_is_not_echo():
    # «Señor» aparece en casi todo lo que dice Zara; una orden que lo incluya,
    # pero con contenido propio, no debe confundirse con eco.
    spoken = ["Son las diez, Señor VK.", "Hecho, Señor VK."]
    assert not is_echo("pon el volumen al cincuenta, Señor", spoken)


def test_pure_filler_is_discarded():
    # «Señor VK» a secas no es una orden: se descarta.
    assert is_echo("Señor VK", ["cualquier cosa"])


def test_confirmation_responses_are_not_echo():
    # Respuestas de confirmación no se descartan a menos que Zara las acabara de decir
    spoken = ["Acción delicada: ¿Confirma, Señor VK?"]
    assert not is_echo("sí", spoken)
    assert not is_echo("no", spoken)
    assert not is_echo("confirmo", spoken)
    assert not is_echo("cancelo", spoken)

