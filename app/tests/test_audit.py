import json
from datetime import datetime
from zara.audit import AuditLog


def test_record_appends_jsonl(tmp_path):
    clock = lambda: datetime(2026, 10, 14, 12, 0, 0)
    log = AuditLog(tmp_path, clock=clock)
    log.record("classify", {"level": "CRITICAL", "cmd": "rm x"})
    log.record("prepare", {"backup": "/c/1"})
    f = tmp_path / "acciones-2026-10-14.jsonl"
    lines = f.read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "classify"
    assert first["level"] == "CRITICAL"
    assert first["ts"].startswith("2026-10-14T12:00:00")
