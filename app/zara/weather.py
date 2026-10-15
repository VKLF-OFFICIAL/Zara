"""El tiempo, vía wttr.in (gratis, sin clave)."""
from __future__ import annotations
import json
import urllib.parse
import urllib.request


def weather_url(place: str = "") -> str:
    if place.strip():
        return f"https://wttr.in/{urllib.parse.quote(place.strip())}?format=j1&lang=es"
    return "https://wttr.in/?format=j1&lang=es"


def parse_wttr(data: dict) -> str:
    cur = data["current_condition"][0]
    temp = cur["temp_C"]
    sens = cur.get("FeelsLikeC", temp)
    desc = cur.get("lang_es", [{}])[0].get("value") or cur.get("weatherDesc", [{}])[0].get("value", "")
    hum = cur.get("humidity", "")
    viento = cur.get("windspeedKmph", "")
    try:
        lugar = data["nearest_area"][0]["areaName"][0]["value"]
    except Exception:
        lugar = ""
    donde = f" en {lugar}" if lugar else ""
    s = f"Señor VK, el tiempo{donde}: {desc}, {temp} grados"
    if sens and sens != temp:
        s += f" (sensación {sens})"
    if hum:
        s += f", humedad {hum}%"
    if viento:
        s += f", viento {viento} kilómetros por hora"
    return s + "."


def weather(place: str = "", timeout: int = 8) -> str:
    try:
        req = urllib.request.Request(weather_url(place),
                                     headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return parse_wttr(data)
    except Exception:
        return ("No he podido consultar el tiempo ahora mismo, Señor VK. "
                "Puede que no haya conexión.")
