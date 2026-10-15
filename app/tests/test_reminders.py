from datetime import datetime
from zara.reminders import parse_when

NOW = datetime(2026, 10, 14, 23, 30, 0)


def test_relative_minutes():
    when, msg = parse_when("recuérdame en 10 minutos que llame a Ana", NOW)
    assert when == datetime(2026, 10, 14, 23, 40, 0)
    assert msg == "llame a Ana"


def test_relative_hours():
    when, msg = parse_when("avísame en 2 horas", NOW)
    assert when == datetime(2026, 10, 15, 1, 30, 0)
    assert msg == ""


def test_relative_seconds_singular():
    when, _ = parse_when("recuérdame en 1 minuto descansar", NOW)
    assert when == datetime(2026, 10, 14, 23, 31, 0)


def test_absolute_time_today():
    when, msg = parse_when("pon una alarma a las 23:45", NOW)
    assert when == datetime(2026, 10, 14, 23, 45, 0)


def test_absolute_time_rolls_to_tomorrow_if_past():
    when, _ = parse_when("despiértame a las 8:30", NOW)
    assert when == datetime(2026, 10, 15, 8, 30, 0)


def test_absolute_hour_only():
    when, _ = parse_when("recuérdame a las 7 sacar la basura", NOW)
    assert when == datetime(2026, 10, 15, 7, 0, 0)


def test_media_hora():
    when, _ = parse_when("recuérdame en media hora", NOW)
    assert when == datetime(2026, 10, 15, 0, 0, 0)


def test_no_time_returns_none():
    assert parse_when("recuérdame llamar a Ana", NOW)[0] is None


def test_scheduler_fires_and_persists(tmp_path):
    from datetime import timedelta
    from zara.reminders import ReminderScheduler
    fired = []
    t = [datetime(2026, 10, 14, 23, 30, 0)]
    sch = ReminderScheduler(tmp_path / "r.json", fired.append, clock=lambda: t[0])
    sch.add(t[0] + timedelta(seconds=1), "comprobar el horno")
    assert (tmp_path / "r.json").exists()
    assert len(sch.pending()) == 1
    t[0] = datetime(2026, 10, 14, 23, 31, 0)   # avanza el reloj
    sch._tick()
    assert fired == ["comprobar el horno"]
    assert sch.pending() == []                  # ya no queda
    sch.stop()


def test_scheduler_reloads_from_disk(tmp_path):
    from zara.reminders import ReminderScheduler
    (tmp_path / "r.json").write_text('[{"at": "2026-12-25T09:00:00", "msg": "felicitar"}]')
    sch = ReminderScheduler(tmp_path / "r.json", lambda m: None)
    assert sch.pending()[0]["msg"] == "felicitar"
    sch.stop()
