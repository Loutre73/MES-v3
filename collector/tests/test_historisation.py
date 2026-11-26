import time

from mes_collector.control import Historisation, Rates, perimeter


def test_perimetre_du_tag():
    assert perimeter('ns=3;s="DB_Moteurs"."M101"."Courant"') == "DB_Moteurs"
    assert perimeter('ns=3;s="DB_Process_03"."P1201"."Valeur"') == "DB_Process"
    assert perimeter("ns=2;i=1234") == "Autres"


def test_suspension_globale_puis_reprise(tmp_path):
    h = Historisation(tmp_path / "h.json")
    assert h.allows("DB_Moteurs")
    assert h.update({"actif": False}) == []
    assert not h.allows("DB_Moteurs") and h.until is None
    assert h.update({"actif": True}) is None          # reprise de tout
    assert h.allows("DB_Moteurs")


def test_suspension_par_db_et_persistance(tmp_path):
    h = Historisation(tmp_path / "h.json")
    h.update({"dbs": {"DB_Process": False}})
    assert not h.allows("DB_Process") and h.allows("DB_Moteurs")
    h2 = Historisation(tmp_path / "h.json")            # redémarrage du collecteur
    assert not h2.allows("DB_Process")
    assert h2.update({"dbs": {"DB_Process": True}}) == ["DB_Process"]


def test_suspension_pour_une_duree(tmp_path):
    h = Historisation(tmp_path / "h.json")
    h.update({"actif": False, "duree_min": 15})
    assert not h.tick()
    h.until = time.time() - 1
    assert h.tick() and h.allows("DB_Lignes")


def test_duree_invalide(tmp_path):
    h = Historisation(tmp_path / "h.json")
    try:
        h.update({"actif": False, "duree_min": 0})
    except ValueError:
        return
    raise AssertionError("durée nulle acceptée")


def test_debits():
    r = Rates()
    r.update({"x": 0})
    r.prev["x"] = (0, r.prev["x"][1] - 2)
    r.update({"x": 100})
    assert 45 <= r.values["x"] <= 55
