from datetime import date, datetime, timedelta, timezone

import pytest

from mes_api import planning
from mes_api.planning import PlanningError, ShiftData, Stop, TZ


def loc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def test_journee_de_planning_6h_6h():
    assert planning.day_start(loc(2026, 11, 26, 17)) == loc(2026, 11, 26, 6)
    assert planning.day_start(loc(2026, 11, 27, 5, 59)) == loc(2026, 11, 26, 6)
    assert planning.week_monday(loc(2026, 11, 26, 17)) == date(2026, 11, 23)
    # lundi 5 h appartient encore à la semaine précédente
    assert planning.week_monday(loc(2026, 11, 30, 5)) == date(2026, 11, 23)
    assert planning.week_monday(loc(2026, 11, 30, 6)) == date(2026, 11, 30)


def test_semaine_commence_un_lundi():
    with pytest.raises(PlanningError):
        planning.week_bounds(date(2026, 11, 24))


def test_copie_garde_l_heure_locale_au_changement_d_heure():
    # passage à l'heure d'hiver le 25 octobre 2026 (Europe/Paris)
    t = loc(2026, 10, 19, 22).astimezone(timezone.utc)
    assert planning.shift_local(t, 7).astimezone(TZ).hour == 22


def poste(**kw):
    base = dict(line_id=1, starts_at=loc(2026, 12, 1, 6), ends_at=loc(2026, 12, 1, 14))
    base.update(kw)
    return ShiftData(**base)


def test_regles_d_un_poste():
    planning.check_shift(poste())
    planning.check_shift(poste(starts_at=loc(2026, 12, 1, 22), ends_at=loc(2026, 12, 2, 6)))
    with pytest.raises(PlanningError, match="6 h"):
        planning.check_shift(poste(starts_at=loc(2026, 12, 1, 22), ends_at=loc(2026, 12, 2, 7)))
    with pytest.raises(PlanningError, match="15 minutes"):
        planning.check_shift(poste(ends_at=loc(2026, 12, 1, 6, 10)))


def test_arrets_a_l_interieur_du_poste():
    ok = [Stop("maintenance", loc(2026, 12, 1, 8), loc(2026, 12, 1, 9)), Stop("arret", loc(2026, 12, 1, 9), loc(2026, 12, 1, 10))]
    planning.check_shift(poste(stops=ok))
    with pytest.raises(PlanningError, match="intérieur"):
        planning.check_shift(poste(stops=[Stop("arret", loc(2026, 12, 1, 13), loc(2026, 12, 1, 15))]))
    with pytest.raises(PlanningError, match="chevauchent"):
        planning.check_shift(poste(stops=[Stop("arret", loc(2026, 12, 1, 8), loc(2026, 12, 1, 10)),
                                          Stop("arret", loc(2026, 12, 1, 9), loc(2026, 12, 1, 11))]))
    with pytest.raises(PlanningError, match="Type"):
        planning.check_shift(poste(stops=[Stop("pause", loc(2026, 12, 1, 8), loc(2026, 12, 1, 9))]))


def test_deplacer_un_poste_deplace_ses_arrets():
    s = poste(stops=[Stop("arret", loc(2026, 12, 1, 8), loc(2026, 12, 1, 9), "Nettoyage")], team="A", target_t=300)
    c = planning.moved(s, 3, loc(2026, 12, 3, 14))
    assert (c.line_id, c.ends_at, c.team, c.target_t) == (3, loc(2026, 12, 3, 22), "A", 300)
    assert (c.stops[0].starts_at, c.stops[0].reason) == (loc(2026, 12, 3, 16), "Nettoyage")


def test_declenchement_copie_auto():
    assert planning.auto_trigger(date(2026, 11, 23), 4, 6) == loc(2026, 11, 27, 6)
    # 2 h du « vendredi » de planning = samedi 2 h
    assert planning.auto_trigger(date(2026, 11, 23), 4, 2) == loc(2026, 11, 28, 2)
    assert planning.auto_trigger(date(2026, 11, 23), 0, 3) == loc(2026, 11, 24, 3)
