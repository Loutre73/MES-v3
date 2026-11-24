from datetime import datetime, timezone

from mes_collector.writer import Spool


def test_spool_roundtrip(tmp_path):
    sp = Spool(str(tmp_path))
    rows = [(datetime(2026, 1, 1, tzinfo=timezone.utc), 1, 2.5, 0), (datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc), 2, None, 2)]
    sp.save(rows)
    sp.save(rows[:1])
    files = sp.files()
    assert len(files) == 2
    assert Spool.load(files[0]) == rows
