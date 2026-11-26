import random
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simulator import DEFAULT_RULES, Instance, Plant, Var  # noqa: E402
from asyncua import ua  # noqa: E402


def plant_with_line():
    p = Plant({"time_acceleration": 60}, random.Random(1))
    inst = Instance("L3", "ligne", "DB_Lignes")
    for m, vt in (("Marche", ua.VariantType.Boolean), ("Tonnage_t", ua.VariantType.Double),
                  ("Debit_th", ua.VariantType.Float), ("Code_arret", ua.VariantType.Int16)):
        inst.vars[m] = Var(ua.NodeId(m, 3), vt, False if vt == ua.VariantType.Boolean else 0)
    p.init_state(inst, {})
    p.instances.append(inst)
    p.by_name["L3"] = inst
    p.rules["DB_Lignes"] = dict(DEFAULT_RULES["ligne"])
    p.structs["DB_Lignes"], p.counts["DB_Lignes"] = "ligne", 1
    return p, inst


def test_regles_validees_tout_ou_rien():
    p, _ = plant_with_line()
    with pytest.raises(ValueError):
        p.set_rules({"dbs": {"DB_Lignes": {"arrets_h": 5, "cadence_pct": 999}}})
    assert p.rules["DB_Lignes"]["arrets_h"] == DEFAULT_RULES["ligne"]["arrets_h"]
    p.set_rules({"global": {"time_acceleration": 120}, "dbs": {"DB_Lignes": {"arrets_h": 5}}})
    assert p.rules["DB_Lignes"]["arrets_h"] == 5 and p.cfg["time_acceleration"] == 120


def test_scenario_panne_puis_redemarrage():
    p, inst = plant_with_line()
    p.set_rules({"dbs": {"DB_Lignes": {"arrets_h": 0}}})
    p.start_scenario("panne_ligne", {"cible": "L3", "duree_min": 1})
    p.step(1.0)
    assert inst.vars["Marche"].value is False and inst.vars["Code_arret"].value == 1
    p.scenarios["panne_ligne"]["fin"] = time.time() - 1      # fin du scénario
    p.step(1.0)
    assert inst.vars["Marche"].value is True and inst.vars["Code_arret"].value == 0


def test_scenario_cible_invalide():
    p, _ = plant_with_line()
    with pytest.raises(ValueError):
        p.start_scenario("panne_ligne", {"cible": "M101"})
    with pytest.raises(LookupError):
        p.start_scenario("inconnu", {})
