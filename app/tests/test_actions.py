from datetime import datetime
from zara.actions import say_time, say_date, find_desktop_entry


def test_say_time():
    assert say_time(datetime(2026, 10, 14, 9, 5)) == "Son las 09:05, Señor VK."


def test_say_date_spanish():
    assert say_date(datetime(2026, 10, 14)) == "Hoy es miércoles, 14 de octubre de 2026, Señor VK."


def _desk(d, name, body):
    (d / name).write_text("[Desktop Entry]\nType=Application\n" + body)


def test_find_desktop_entry_by_name(tmp_path):
    _desk(tmp_path, "org.mozilla.firefox.desktop", "Name=Firefox\nName[es]=Firefox\n")
    _desk(tmp_path, "spotify.desktop", "Name=Spotify\n")
    assert find_desktop_entry("spotify", [tmp_path]).name == "spotify.desktop"


def test_find_desktop_entry_accents_and_exact_preferred(tmp_path):
    _desk(tmp_path, "a.desktop", "Name=Música Pro Studio\n")
    _desk(tmp_path, "b.desktop", "Name=Música\n")
    assert find_desktop_entry("musica", [tmp_path]).name == "b.desktop"


def test_find_desktop_entry_skips_hidden(tmp_path):
    _desk(tmp_path, "h.desktop", "Name=Discord\nNoDisplay=true\n")
    assert find_desktop_entry("discord", [tmp_path]) is None


from zara.actions import folder_path, search_url


def test_folder_path_spanish_names(tmp_path):
    (tmp_path / "Descargas").mkdir()
    (tmp_path / "Imágenes").mkdir()
    assert folder_path("descargas", home=tmp_path) == tmp_path / "Descargas"
    assert folder_path("imagenes", home=tmp_path) == tmp_path / "Imágenes"
    assert folder_path("personal", home=tmp_path) == tmp_path
    assert folder_path("inexistente", home=tmp_path) is None


def test_search_url_encodes_query():
    assert search_url("google", "paella valenciana") == \
        "https://www.google.com/search?q=paella+valenciana"
    assert search_url("youtube", "lofi & chill") == \
        "https://www.youtube.com/results?search_query=lofi+%26+chill"
