from zara.weather import weather_url, parse_wttr


def test_url_default_autolocation():
    assert weather_url("") == "https://wttr.in/?format=j1&lang=es"
    assert weather_url("Madrid") == "https://wttr.in/Madrid?format=j1&lang=es"
    assert weather_url("San Sebastián") == "https://wttr.in/San%20Sebasti%C3%A1n?format=j1&lang=es"


def test_parse_wttr():
    data = {"current_condition": [{"temp_C": "14", "FeelsLikeC": "12",
            "lang_es": [{"value": "Parcialmente nublado"}],
            "humidity": "80", "windspeedKmph": "9"}],
            "nearest_area": [{"areaName": [{"value": "Madrid"}]}]}
    s = parse_wttr(data)
    assert "Madrid" in s and "14" in s and "Parcialmente nublado" in s and "Señor VK" in s
