"""Decide si una petición necesita el agente (tarea real) o basta charla rápida."""
from __future__ import annotations
import re
import unicodedata


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# Verbos/expresiones que implican ACTUAR sobre el equipo (editar, crear, ejecutar
# tareas de varios pasos). Lo demás es conversación y va por la vía rápida.
_AGENT = re.compile(
    r"\b("
    r"creame|crea(r)?|programa(r|me)?|codifica|desarrolla|monta(r)?|genera(r|me)?|"
    r"arregla(r)?|corrige|corrige(r)?|repara|refactoriza|depura|"
    r"edita(r)?|modifica(r)?|cambia(?:le)? el (archivo|fichero|codigo)|"
    r"instala(r)?|desinstala|actualiza(r)? el|configura(r)?|despliega|compila(r)?|"
    r"automatiza(r)?|descarga(r)? (el|los|la) (repo|repositorio|proyecto|archivo)|"
    r"clona(r)? el repo|analiza(r)? (el|mi|este) (codigo|proyecto|archivo|carpeta|repo)|"
    r"revisa(r)? (el|mi|este) (codigo|proyecto|archivo|repo)|"
    r"haz(me)? (un|una) (script|programa|proyecto|aplicacion|app|web|pagina|bot)|"
    r"escribe(me)? (un|una) (script|programa|codigo|funcion|clase|archivo|proyecto)|"
    r"modo agente|ponte en modo agente|como agente"
    r")\b")


def needs_agent(text: str) -> bool:
    return bool(_AGENT.search(_norm(text)))
