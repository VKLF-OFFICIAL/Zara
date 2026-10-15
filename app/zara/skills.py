"""Skills declarativas.

Permiten añadir capacidades a Zara sin tocar el código: cada skill es un
archivo TOML en `config/skills/*.toml` con unas frases de activación y qué
hacer. Es el método de kalliope/JARVIS-OS (capacidades declarativas) adaptado
a Zara, pasando siempre las acciones de shell por el Guardián.

Formato de un archivo de skill:

    nombre = "mi ip"
    frases = ["cual es mi ip", "mi ip publica"]
    tipo   = "shell"          # "shell" | "decir" | "prompt"
    comando = "curl -s ifconfig.me"     # para tipo shell
    # respuesta = "..."       # para tipo decir
    # prompt = "Resume: {arg}" # para tipo prompt
    # confirmar = false        # opcional (shell): forzar confirmación

En las frases, el primer grupo de captura (paréntesis) queda disponible como
{arg} en comando/respuesta/prompt; {0} es toda la coincidencia.
"""
from __future__ import annotations
import logging
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("zara.skills")

_VALID = {"shell", "decir", "prompt"}


def _norm(text: str) -> str:
    # Minúsculas, sin acentos y espacios colapsados, pero SIN tocar la
    # puntuación: así un argumento capturado (una URL, p. ej.) queda intacto.
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Skill:
    nombre: str
    patrones: list[re.Pattern]
    tipo: str
    comando: str = ""
    respuesta: str = ""
    prompt: str = ""
    confirmar: bool = False

    def match(self, text: str) -> dict | None:
        n = _norm(text)
        for p in self.patrones:
            m = p.search(n)
            if m:
                arg = (m.group(1) if m.groups() else "").strip()
                return {"arg": arg, "0": m.group(0).strip()}
        return None

    def render(self, campo: str, g: dict) -> str:
        val = getattr(self, campo, "") or ""
        return val.replace("{arg}", g.get("arg", "")).replace("{0}", g.get("0", ""))


@dataclass
class SkillSet:
    skills: list[Skill] = field(default_factory=list)

    def match(self, text: str):
        """Devuelve (skill, grupos) de la primera skill que case, o (None, None)."""
        if not (text or "").strip():
            return (None, None)
        for sk in self.skills:
            g = sk.match(text)
            if g is not None:
                return (sk, g)
        return (None, None)

    def names(self) -> list[str]:
        return [s.nombre for s in self.skills]


def _load_one(path: Path) -> Skill | None:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("skill %s ilegible: %s", path.name, exc)
        return None
    tipo = str(data.get("tipo", "")).lower()
    frases = data.get("frases") or []
    if tipo not in _VALID or not frases:
        log.warning("skill %s inválida (tipo=%r, frases=%d)", path.name, tipo, len(frases))
        return None
    patrones = []
    for fr in frases:
        try:
            patrones.append(re.compile(_norm(fr) if not _looks_regex(fr) else fr, re.I))
        except re.error as exc:
            log.warning("skill %s: frase inválida %r (%s)", path.name, fr, exc)
    if not patrones:
        return None
    return Skill(
        nombre=str(data.get("nombre", path.stem)),
        patrones=patrones, tipo=tipo,
        comando=str(data.get("comando", "")),
        respuesta=str(data.get("respuesta", "")),
        prompt=str(data.get("prompt", "")),
        confirmar=bool(data.get("confirmar", False)),
    )


def _looks_regex(s: str) -> bool:
    return bool(re.search(r"[\^\$\(\)\[\]\+\*\?\\]", s))


def load_skills(directory: Path) -> SkillSet:
    directory = Path(directory)
    if not directory.is_dir():
        return SkillSet([])
    out = []
    for f in sorted(directory.glob("*.toml")):
        sk = _load_one(f)
        if sk is not None:
            out.append(sk)
    if out:
        log.info("skills cargadas: %s", ", ".join(s.nombre for s in out))
    return SkillSet(out)
