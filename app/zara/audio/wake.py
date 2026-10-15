from __future__ import annotations
import re
import unicodedata

# «Zara» y sus confusiones fonéticas habituales en Whisper: Sara, Vara, Sada,
# Zahra, Zahara, Tsara, Zhara, Sarah… (inicial z/s/ts/x/v/zh/th + a + r/d + a).
# No casa con palabras comunes como «para», «cara», «clara», «rara», «nada».
_WAKE_TOKEN = re.compile(r"^(?:z|s|ts|x|v|zh|th)a(?:h?a)?h?[rd]ah?$")
# «Jarvis» (y sus confusiones): el detector neural usa el modelo «hey jarvis»,
# así que también se admite por voz. j/y/ch/sh + ar + v + i + s/z.
_JARVIS_TOKEN = re.compile(r"^(?:j|y|ch|sh)ar?vi[sz]?$")
# Mishears «débiles» de «Zara»: suenan parecido pero son palabras comunes
# (para, cara, clara, mara…). Solo cuentan como activación si van de VOCATIVO:
# seguidas de coma/pausa y una orden. Así «Para, ¿cuál es mi IP?» (Whisper oye
# «Zara»→«Para») activa, pero «para mañana quiero…» no.
_WEAK_TOKEN = re.compile(r"^(?:p|b|c|m|l|j|t|d|g|r|n|f|h)ara$|^clara$|^bada$|^pada$")
# Rellenos admitidos antes de la wake word.
_FILLERS = ("oye", "hey", "ok", "vale")

_WAKE_RE = re.compile(
    r"^\W*(?:(?:" + "|".join(_FILLERS) + r")\W+)?"
    r"(\w+)[\s,:;.!¡]*",
    re.IGNORECASE,
)

# Descompone el inicio: relleno opcional, 1ª palabra, separadores tras ella
# (coma/pausa) y el resto. Sirve para saber si un mishear débil es un vocativo.
_LEAD = re.compile(
    r"^\W*(?:(?:" + "|".join(_FILLERS) + r")\W+)?"
    r"([^\W\d_]+)([^\w]*)(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def _strip_accents(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _is_wake_token(word: str) -> bool:
    w = _strip_accents(word)
    return bool(_WAKE_TOKEN.match(w) or _JARVIS_TOKEN.match(w))


def has_wake(text: str) -> bool:
    """True si la frase va dirigida a Zara.

    - Mishears «fuertes» de «Zara» (z/s/v/ts/x/zh/th…) o «Jarvis»: siempre.
    - Mishears «débiles» que son palabras comunes (para, cara, clara…): solo
      como vocativo —seguidas de coma/pausa y una orden—.
    """
    m = _LEAD.match((text or "").strip())
    if not m:
        return False
    word = _strip_accents(m.group(1).lower())
    sep, rest = m.group(2), m.group(3).strip()
    if _WAKE_TOKEN.match(word) or _JARVIS_TOKEN.match(word):
        return True
    if _WEAK_TOKEN.match(word):
        return ("," in sep) and bool(rest)
    return False


def extract_after_wake(text: str) -> str:
    """Quita el prefijo de activación y devuelve la orden; si no hay wake, intacto."""
    if not has_wake(text):
        return text.strip()
    m = _WAKE_RE.match(text)
    return text[m.end():].strip() if m else text.strip()


def _clean_order(text: str) -> str:
    t = text.strip().strip(" .,;:!¡")
    return t if re.search(r"\w", t) else ""


class WakeGate:
    """Decide si una frase va dirigida a Zara.

    - «Zara, haz X» → orden.
    - «Zara» sola → acuse («¿Sí, Señor VK?») y abre una ventana de unos segundos
      en la que la siguiente frase cuenta como orden sin repetir «Zara».
    - En modo conversación, cada orden renueva esa ventana.
    """

    def __init__(self, window: float = 8.0, clock=None, conversation: bool = False):
        import time
        self.window = window
        self._clock = clock or time.monotonic
        self.conversation = conversation
        self._open_until = float("-inf")

    def open(self, seconds: float | None = None):
        self._open_until = self._clock() + (seconds or self.window)

    def route(self, text: str, neural: bool = False):
        """neural=True: el detector neural de wake ha disparado en esta locución,
        así que cuenta como dirigida a Zara aunque el texto no case «Zara»."""
        if not text or not text.strip():
            # El detector neural oyó la activación pero Whisper no sacó texto:
            # trátalo como «Zara» a secas (acuse + ventana de escucha).
            if neural:
                self.open()
                return ("ack", None)
            return (None, None)
        now = self._clock()
        if has_wake(text) or neural:
            order = _clean_order(extract_after_wake(text))
            if not order:
                self.open()
                return ("ack", None)
        elif now < self._open_until:
            order = _clean_order(text)
            if not order:
                return (None, None)
        else:
            return (None, None)
        if self.conversation:
            self.open()
        else:
            self._open_until = float("-inf")
        return ("order", order)
