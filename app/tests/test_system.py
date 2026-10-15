from zara.system import format_status, greeting
from datetime import datetime


def test_format_status_mentions_key_metrics():
    s = format_status(cpu=12.0, mem_used=16.0, mem_total=46.0,
                      disk_free=67.0, disk_total=221.0, temp=50.0, uptime_s=9000)
    assert "12" in s and "%" in s
    assert "16" in s and "46" in s          # RAM usada / total
    assert "67" in s                         # disco libre
    assert "50" in s and "º" in s            # temperatura
    assert "Señor VK" in s


def test_format_status_without_temp():
    s = format_status(cpu=5.0, mem_used=8.0, mem_total=46.0,
                      disk_free=67.0, disk_total=221.0, temp=None, uptime_s=60)
    assert "temperatura" not in s.lower() or "º" not in s


def test_greeting_by_time():
    assert "Buenos días" in greeting(datetime(2026, 10, 14, 9, 0))
    assert "Buenas tardes" in greeting(datetime(2026, 10, 14, 16, 0))
    assert "Buenas noches" in greeting(datetime(2026, 10, 14, 23, 0))
    assert "Señor VK" in greeting(datetime(2026, 10, 14, 9, 0))
