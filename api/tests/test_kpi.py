from datetime import datetime, timedelta, timezone

from mes_api.events import alarm_active
from mes_api.routes_recipes import compare_values
from mes_api.workers import compute_kpi, drift_status, merge, subtract

T0 = datetime(2026, 11, 25, 6, tzinfo=timezone.utc)


def h(x):
    return T0 + timedelta(hours=x)


def test_merge_and_subtract():
    assert merge([(h(1), h(2)), (h(1.5), h(3)), (h(4), h(5))]) == [(h(1), h(3)), (h(4), h(5))]
    assert subtract([(h(0), h(4))], [(h(1), h(2))]) == [(h(0), h(1)), (h(2), h(4))]


def test_oee_with_planned_and_unplanned_stops():
    # shift de 8 h, 1 h d'arrêt planifié, 1 h d'arrêt non planifié dont 30 min pendant le planifié
    k = compute_kpi(h(0), h(8), planned=[(h(2), h(3))], stops=[(h(2.5), h(3.5))], tonnage=650, nominal_rate=100)
    assert k["planned_stop_s"] == 3600
    assert k["unplanned_stop_s"] == 1800
    assert k["run_s"] == 6.5 * 3600
    assert round(k["availability"], 4) == round(6.5 / 7, 4)
    assert k["performance"] == 1.0
    assert round(k["oee"], 4) == round(6.5 / 7, 4)


def test_performance_and_missing_counter():
    k = compute_kpi(h(0), h(8), [], [], tonnage=400, nominal_rate=100)
    assert k["performance"] == 0.5
    assert compute_kpi(h(0), h(8), [], [], None, 100)["oee"] is None


def test_alarm_conditions_and_drift():
    assert alarm_active("vrai", None, 1.0) and not alarm_active("vrai", None, 0.0)
    assert alarm_active("superieur", 80, 81) and not alarm_active("superieur", 80, 79)
    assert alarm_active("vrai", None, None) is None
    assert drift_status(5, 10, 20) == "normal" and drift_status(-12, 10, 20) == "attention" and drift_status(25, 10, 20) == "alerte"


def test_recipe_comparison_tolerance():
    tdef = {"params": [{"name": "V", "label": "V", "min": 0, "max": 100}, {"name": "T", "label": "T"}]}
    lines = compare_values(tdef, {"V": 50, "T": 80}, {"V": 50.4, "T": 90}, 0.5)
    assert [l["ecart"] for l in lines] == [False, True]
