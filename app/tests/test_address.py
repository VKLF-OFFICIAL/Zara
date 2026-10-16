from zara.address import strip_vocatives, AddressModerator


def test_strip_removes_all_vocatives():
    assert strip_vocatives("Hecho, Señor VK.") == "Hecho."
    assert strip_vocatives("Señor VK, el servicio está activo.") == "El servicio está activo." or \
           strip_vocatives("Señor VK, el servicio está activo.") == "el servicio está activo."
    assert "Señor" not in strip_vocatives("Por supuesto, Señor. De inmediato, VK.")
    assert strip_vocatives("Todo en orden") == "Todo en orden"


def test_strip_keeps_non_vocative_text():
    # No debe tragarse palabras normales que contengan 'vk' u otras.
    assert strip_vocatives("Reinicio nginx.") == "Reinicio nginx."


def _mod(**kw):
    t = [0.0]
    m = AddressModerator(clock=lambda: t[0], **kw)
    return m, t


def test_first_reply_keeps_address():
    m, _ = _mod(every=6, cooldown=180)
    out = m.apply("Hecho, Señor VK.")
    assert "Señor VK" in out


def test_subsequent_replies_drop_address():
    m, _ = _mod(every=6, cooldown=9999)
    m.apply("Hecho, Señor VK.")          # turno 0: conserva
    outs = [m.apply("Listo, Señor VK.") for _ in range(4)]  # turnos 1-4: quitan
    assert all("Señor VK" not in o for o in outs)
    assert all(o for o in outs)          # pero siguen teniendo contenido


def test_address_returns_after_interval():
    m, _ = _mod(every=3, cooldown=9999)
    seen = [("Señor VK" in m.apply("Hecho, Señor VK.")) for _ in range(7)]
    # turno 0 y cada 3 (0,3,6) conservan; el resto no.
    assert seen[0] and seen[3] and seen[6]
    assert not seen[1] and not seen[2]


def test_cooldown_reintroduces_address():
    m, t = _mod(every=9999, cooldown=120)
    assert "Señor VK" in m.apply("Hola, Señor VK.")   # turno 0
    assert "Señor VK" not in m.apply("Listo, Señor VK.")  # turno 1, sin descanso
    t[0] = 200                                         # pasa el descanso
    assert "Señor VK" in m.apply("De nuevo, Señor VK.")


def test_pure_vocative_text_is_preserved():
    m, _ = _mod()
    # «¿Sí, Señor VK?» no debe quedar vacío.
    assert m.apply("¿Sí, Señor VK?").strip() != ""
