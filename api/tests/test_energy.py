from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mes_api.energy import deltas, split, stopped_seconds

T0 = datetime(2026, 11, 25, 6, tzinfo=timezone.utc)
TZ = ZoneInfo("Europe/Paris")


def m(x):
    return T0 + timedelta(minutes=x)


def test_stopped_seconds_spreads_over_steps():
    s = stopped_seconds([(m(0.5), m(2.5)), (m(2), m(3))], T0, 60)
    assert s == {m(0): 30, m(1): 60, m(2): 60}


def test_deltas_ignore_counter_reset():
    assert deltas(10, [(m(1), 12), (m(2), 1), (m(3), 4)]) == [(m(1), 2), (m(2), 0), (m(3), 3)]
    assert deltas(None, [(m(1), 12), (m(2), 13)]) == [(m(2), 1)]


def test_split_by_line_state():
    meters = [{"id": 1, "tag_id": 10, "compteur": "C1", "line": "L1", "usage": "broyage"},
              {"id": 2, "tag_id": 20, "compteur": "C2", "line": None, "usage": "utilités"}]
    samples = {10: [(m(i), 100 + 10 * (i + 1)) for i in range(4)], 20: [(m(i), 5 * (i + 1)) for i in range(4)]}
    seeds = {10: 100, 20: 0}
    # L1 arrêtée la 2e minute, L2 toujours en marche : le compteur commun reste en production
    stops = {"L1": [(m(1), m(2))], "L2": []}
    serie, rows, total = split(meters, samples, seeds, stops, T0, m(4), 60, TZ, False)
    c1 = next(r for r in rows if r["compteur"] == "C1")
    assert c1["kwh"] == 40 and c1["prod_kwh"] == 30 and c1["hors_kwh"] == 10
    c2 = next(r for r in rows if r["compteur"] == "C2")
    assert c2["hors_kwh"] == 0 and c2["prod_kwh"] == 20
    assert total == {"kwh": 60, "prod_kwh": 50, "hors_kwh": 10}
    assert len(serie) == 1 and serie[0]["prod_kwh"] == 50
    assert rows[0]["compteur"] == "C1"   # du plus gros au plus petit
