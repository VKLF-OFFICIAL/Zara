from zara.skills import load_skills, Skill, SkillSet


def _write(d, name, body):
    (d / name).write_text(body, encoding="utf-8")


def test_loads_and_matches_decir(tmp_path):
    _write(tmp_path, "saluda.toml", """
nombre = "saluda"
frases = ["buenos dias", "buenas"]
tipo = "decir"
respuesta = "Buenos días, Señor VK."
""")
    ss = load_skills(tmp_path)
    assert ss.names() == ["saluda"]
    sk, g = ss.match("Buenos días")          # case/acentos indiferentes
    assert sk is not None and sk.tipo == "decir"
    assert sk.render("respuesta", g) == "Buenos días, Señor VK."


def test_capture_group_available_as_arg(tmp_path):
    _write(tmp_path, "resume.toml", """
nombre = "resume web"
frases = ["^resume (.+)$"]
tipo = "prompt"
prompt = "Resume en 3 frases: {arg}"
""")
    ss = load_skills(tmp_path)
    sk, g = ss.match("resume https://vklf.es")
    assert g["arg"] == "https://vklf.es"
    assert sk.render("prompt", g) == "Resume en 3 frases: https://vklf.es"


def test_shell_skill_loaded(tmp_path):
    _write(tmp_path, "ip.toml", """
nombre = "mi ip"
frases = ["cual es mi ip", "mi ip publica"]
tipo = "shell"
comando = "curl -s ifconfig.me"
confirmar = false
""")
    ss = load_skills(tmp_path)
    sk, g = ss.match("¿cuál es mi IP?")
    assert sk.tipo == "shell"
    assert sk.render("comando", g) == "curl -s ifconfig.me"
    assert sk.confirmar is False


def test_invalid_skill_ignored(tmp_path):
    _write(tmp_path, "mala.toml", 'nombre = "x"\ntipo = "inexistente"\nfrases = ["hola"]\n')
    _write(tmp_path, "sinfrases.toml", 'nombre = "y"\ntipo = "decir"\n')
    _write(tmp_path, "rota.toml", 'esto no es toml válido = = =')
    assert load_skills(tmp_path).skills == []


def test_no_match_returns_none(tmp_path):
    _write(tmp_path, "x.toml", 'nombre="x"\ntipo="decir"\nfrases=["patata"]\nrespuesta="ok"\n')
    ss = load_skills(tmp_path)
    assert ss.match("otra cosa") == (None, None)
    assert ss.match("") == (None, None)


def test_missing_dir_is_empty():
    ss = load_skills("/ruta/que/no/existe/skills")
    assert isinstance(ss, SkillSet) and ss.skills == []
