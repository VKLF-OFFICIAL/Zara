from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass


@dataclass
class Intent:
    kind: str
    arg: str | None = None


def _norm(text: str) -> str:
    """minúsculas, sin acentos ni signos de apertura/cierre."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[¿?¡!.,;:]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_STOP = {"para", "para ya", "calla", "callate", "silencio", "basta", "deten la voz",
         "deja de hablar", "stop", "shh", "shhh"}

_POLITE_LEAD = re.compile(
    r"^(?:por favor\s+)?"
    r"(?:(?:me\s+)?(?:puedes|podrias|podrías|quisiera|quiero|te importaria|te importaría)\s+"
    r"(?:hacer\s+el\s+favor\s+de\s+|decirme\s+|decir\s+|ayudarme\s+a\s+|ponerme\s+|abrirme\s+)?|"
    r"hacer\s+el\s+favor\s+de\s+)?"
    r"(?:por favor\s+)?",
    re.IGNORECASE,
)
_POLITE_TAIL = re.compile(r"\s+(?:por favor|muchas gracias|gracias)$", re.IGNORECASE)

_RULES: list[tuple[str, str]] = [
    ("show_panel", r"^(abre|abrir|muestra|mostrar|ensena|ensenar|despliega|desplegar) (el |la )?(panel|chat|conversacion)$"),
    ("volume_up", r"^(sube|subir|aumenta|aumentar) (el |un poco el )?(volumen|sonido)|^mas volumen"),
    ("volume_down", r"^(baja|bajar|reduce|reducir|disminuye|disminuir) (el |un poco el )?(volumen|sonido)|^menos volumen"),
    ("set_volume", r"volumen (al|a|en) (\d{1,3})"),
    ("unmute", r"^(quita|quitar|desactiva|desactivar) el silencio|^(activa|activar) (el )?(sonido|audio)|^desmutea|^desmutear"),
    ("mute", r"^(silencia|silenciar|mutea|mutear)( el sonido| el audio| todo)?$|^(desactiva|desactivar|quita|quitar) (el )?(sonido|audio)$"),
    ("media_pause", r"^(pausa|pausar|para|parar|deten|detener|detén|quita|quitar|apaga|apagar)( la)? (musica|cancion|reproduccion)|^pausa$"),
    ("media_play", r"^(reanuda|reanudar|continua|continuar|reproduce|reproducir|pon|poner|inicia|iniciar|toca|tocar|escucha|escuchar)( la| algo de| un poco de)? (musica|cancion|tema)|^(reanuda|play)$"),
    ("media_next", r"^(siguiente|proxima) (cancion|tema|pista)|^pasa (de )?(cancion|tema)"),
    ("media_prev", r"(cancion|tema|pista) anterior|^vuelve a la (cancion )?anterior"),
    ("time", r"^(que hora es|dime la hora|que hora tenemos|dime que hora tenemos|la hora)"),
    ("date", r"^(que dia es( hoy)?|que fecha es( hoy)?|dime la fecha|en que dia estamos|que dia tenemos)"),
    ("status", r"^(estado|status|informe|diagnostico|telemetria)( del sistema| del equipo)?$|"
               r"^como (esta|estas|va|andas|andamos|vas|te encuentras)"
               r"( el equipo| el sistema| todo| por ahi)?$"),
    ("help", r"^(que puedes hacer|que sabes hacer|ayuda|que ordenes hay|"
             r"comandos|en que me puedes ayudar|que mas puedes hacer|que haces)$"),
    ("lock", r"^(bloquea|bloquear)( el equipo| la pantalla| la sesion| el ordenador)?$"),
    ("screenshot", r"^(haz(me)?|hacer(me)?|saca(me)?|sacar(me)?|toma|tomar) (una? )?(captura|pantallazo)( de pantalla)?$|"
                   r"^(captura|pantallazo)( de pantalla)?$"),
]

_POWER = [
    ("poweroff", r"^(apaga(te)?|apague)( el (equipo|ordenador|pc|sistema))?$"),
    ("reboot", r"^(reinicia(r)?|reinicie)( el (equipo|ordenador|pc|sistema))?$"),
    ("suspend", r"^(suspende(r)?|suspenda|duerme(te)?|hiberna(r)?)"
                r"( el (equipo|ordenador|pc|sistema))?$"),
]

_REMINDER_RE = re.compile(
    r"\b(recuerdame|recuerdeme|avisame|despiertame|alarma|recordatorio)\b")
_WEATHER_RE = re.compile(
    r"^(?:qué|que)?\s*(?:qué tiempo (?:hace|va a hacer)|tiempo que hace|"
    r"el tiempo|el clima|dime el (?:tiempo|clima)|qué clima)"
    r"(?:\s+(?:en|de)\s+(.+?))?\s*[?.!]*$", re.I)

_SCREEN_RE = re.compile(
    r"^(?:mira(?:me)?|lee(?:me)?|revisa|observa|describe(?:me)?|analiza|"
    r"echa(?:le)? un vistazo a|que (?:ves|hay|pone|aparece|esta) en)\s+"
    r"(?:la |mi |esta )?(?:pantalla|monitor)\b[\s,]*(?:y\s+)?(.*)$")

_MUSIC_APPS = "spotify|vlc|rhythmbox|elisa|audacious|amberol"
_PLAY_APP_RE = re.compile(
    r"^(?:le |la )?(?:reproduce|reproduzca|reproducir|produce|pon|pone|poner|ponme|con|toca|tocar|escucha|escuchar)\s+"
    r"(?:(?:la |algo de |un poco de )?musica\s+)?(?:en |con )?(" + _MUSIC_APPS + r")$")
_FOLDER_RE = re.compile(
    r"^(?:abre|muestra|ensena)\s+(?:la |mi |mis |el )?(?:carpeta\s+(?:de\s+)?(?:mis\s+)?)?"
    r"(descargas|documentos|imagenes|fotos|musica|videos|escritorio|personal|zara)$")
_CLOSE_RE = re.compile(r"^(?:cierra|cerrar|cierre)\s+(?:el |la |los |las )?(.+?)[.!?]*$", re.I)
_SEARCH_IN_RE = re.compile(r"^busca(?:r)?\s+en\s+(google|youtube)\s+(.+?)[.!?]*$", re.I)
_SEARCH_RE = re.compile(r"^busca(?:r)?\s+(.+?)(?:\s+en\s+(google|youtube))?[.!?]*$", re.I)
_OPEN_RE = re.compile(r"^(?:abre|abrir|lanza|inicia|arranca)\s+(?:el\s+|la\s+|los\s+|las\s+)?(.+?)[.!?]*$", re.I)
_RUN_RE = re.compile(r"^(?:ejecuta|corre)(?:\s+el\s+comando)?\s+(.+?)\s*$", re.I)


def _match_direct(n: str, raw: str) -> Intent | None:
    if not n:
        return None
    if n in _STOP:
        return Intent("stop_speaking")

    # Recordatorios/alarmas antes que «pon» (música) o «busca».
    if _REMINDER_RE.search(n):
        return Intent("reminder", raw)

    # El tiempo (preservando el lugar con acentos del texto original).
    m = _WEATHER_RE.match(raw.strip())
    if m:
        return Intent("weather", (m.group(1) or "").strip())

    m = _SCREEN_RE.match(n)
    if m:
        return Intent("screen_look", m.group(1).strip())

    for kind, pattern in _POWER:
        if re.match(pattern, n):
            return Intent("power", kind)

    m = _PLAY_APP_RE.match(n)
    if m:
        return Intent("play_in_app", m.group(1))

    for kind, pattern in _RULES:
        m = re.search(pattern, n)
        if m:
            if kind == "set_volume":
                return Intent(kind, m.group(2))
            return Intent(kind)

    body = raw.lstrip("¿¡")
    m = _SEARCH_IN_RE.match(body)
    if m:
        return Intent("web_search", (m.group(1).lower(), m.group(2).strip()))
    m = _SEARCH_RE.match(body)
    if m:
        return Intent("web_search", ((m.group(2) or "google").lower(), m.group(1).strip()))

    m = _FOLDER_RE.match(n)
    if m:
        return Intent("open_folder", m.group(1))

    m = _CLOSE_RE.match(body)
    if m:
        return Intent("close_app", m.group(1).strip().lower())

    m = _OPEN_RE.match(raw.lstrip("¿¡"))
    if m:
        return Intent("open_app", m.group(1).strip().lower())

    m = _RUN_RE.match(raw)
    if m:
        return Intent("run", m.group(1).strip())
    return None


def parse_intent(text: str) -> Intent | None:
    """Reconoce órdenes directas que Zara resuelve sin consultar al cerebro."""
    raw = (text or "").strip()
    n = _norm(raw)
    if not n:
        return None

    # 1. Probar coincidencia directa
    it = _match_direct(n, raw)
    if it is not None:
        return it

    # 2. Desenvolver cortesías coloquiales («¿me puedes abrir...?», «podrías subir...», etc.)
    stripped_n = _POLITE_LEAD.sub("", n).strip()
    stripped_n = _POLITE_TAIL.sub("", stripped_n).strip()
    if stripped_n and stripped_n != n:
        stripped_raw = re.sub(r"^[¿¡\s]+", "", raw)
        stripped_raw = _POLITE_LEAD.sub("", stripped_raw).strip()
        stripped_raw = _POLITE_TAIL.sub("", stripped_raw).strip()
        it = _match_direct(stripped_n, stripped_raw)
        if it is not None:
            return it

    return None
