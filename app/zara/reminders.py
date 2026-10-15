"""Recordatorios y alarmas: interpreta cuándo y programa el aviso."""
from __future__ import annotations
import json
import logging
import re
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("zara.reminders")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


_REL = re.compile(r"\ben\s+(media\s+hora|un?\s+\w+|\d+)\s*"
                  r"(segundos?|minutos?|min|horas?|hora|h)?\b")
_ABS = re.compile(r"\ba\s+las?\s+(\d{1,2})(?:[:.](\d{2}))?\b")
_CLEAN = re.compile(
    r"\b(recuerdame|recuerdeme|avisame|despiertame|pon(?:me)?\s+una\s+alarma|"
    r"pon(?:me)?\s+un\s+recordatorio|alarma|recordatorio)\b", re.I)


def _message(text: str) -> str:
    # Trabajamos token a token: decidimos qué quitar sobre la versión sin
    # acentos, pero conservamos las palabras originales (nombres propios, etc.).
    ow = text.split()
    nw = _norm(text).split()
    joined = " ".join(nw)
    rel = _REL.search(joined)
    abs_ = None if rel else _ABS.search(joined)
    # índices de tokens que caen dentro de la expresión temporal
    time_idx = set()
    span = rel.span() if rel else (abs_.span() if abs_ else None)
    if span:
        pos = 0
        for i, w in enumerate(nw):
            start, end = pos, pos + len(w)
            if start < span[1] and end > span[0]:
                time_idx.add(i)
            pos = end + 1
    triggers = {"recuerdame", "recuerdeme", "avisame", "despiertame", "alarma",
                "recordatorio", "pon", "ponme", "una", "un"}
    out = []
    started = False
    for i, (o, nm) in enumerate(zip(ow, nw)):
        norm_tok = re.sub(r"[^\w]", "", nm)
        if i in time_idx:
            continue
        if not started and norm_tok in triggers:
            continue
        if not started and norm_tok in ("que", "de", "para", "a", "el", "la"):
            continue
        started = True
        out.append(o)
    return re.sub(r"\s+", " ", " ".join(out)).strip(" ,.;:")


def parse_when(text: str, now: datetime | None = None):
    """Devuelve (datetime_del_aviso | None, mensaje)."""
    now = now or datetime.now()
    n = _norm(text)
    when = None

    m = _REL.search(n)
    if m:
        qty_raw, unit = m.group(1), (m.group(2) or "minutos")
        if "media hora" in qty_raw:
            delta = timedelta(minutes=30)
        else:
            num = {"un": 1, "una": 1, "uno": 1}.get(qty_raw.strip(),
                   None)
            if num is None:
                num = int(re.search(r"\d+", qty_raw).group()) if re.search(r"\d+", qty_raw) else 1
            if unit.startswith("seg"):
                delta = timedelta(seconds=num)
            elif unit.startswith("h"):
                delta = timedelta(hours=num)
            else:
                delta = timedelta(minutes=num)
        when = now + delta
    else:
        m = _ABS.search(n)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2) or 0)
            cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand <= now:
                cand += timedelta(days=1)
            when = cand

    return when, _message(text)


class ReminderScheduler:
    """Programa avisos en un hilo; los persiste para sobrevivir a reinicios."""

    def __init__(self, store_path: Path, fire_cb, clock=datetime.now):
        self.path = Path(store_path)
        self._fire = fire_cb
        self._clock = clock
        self._items: list[dict] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._load()

    def _load(self):
        try:
            self._items = json.loads(self.path.read_text())
        except Exception:
            self._items = []

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2))
        except OSError:
            log.exception("no pude guardar los recordatorios")

    def add(self, when: datetime, message: str):
        with self._lock:
            self._items.append({"at": when.isoformat(), "msg": message})
            self._items.sort(key=lambda x: x["at"])
            self._save()
        self._arm()

    def start(self):
        self._arm()

    def stop(self):
        if self._timer:
            self._timer.cancel()

    def pending(self) -> list[dict]:
        return list(self._items)

    def _arm(self):
        if self._timer:
            self._timer.cancel()
        with self._lock:
            if not self._items:
                return
            nxt = datetime.fromisoformat(self._items[0]["at"])
        delay = max(0.0, (nxt - self._clock()).total_seconds())
        self._timer = threading.Timer(min(delay, 3600), self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self):
        now = self._clock()
        due = []
        with self._lock:
            keep = []
            for it in self._items:
                (due if datetime.fromisoformat(it["at"]) <= now else keep).append(it)
            self._items = keep
            if due:
                self._save()
        for it in due:
            try:
                self._fire(it["msg"])
            except Exception:
                log.exception("fallo al disparar recordatorio")
        self._arm()
