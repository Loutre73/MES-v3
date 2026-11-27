import random
import sys
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


def test_compteurs_repris_apres_redemarrage(tmp_path):
    from simulator import Control
    p, inst = plant_with_line()
    inst.vars["Tonnage_t"].raw = 1234.5
    c = Control(p, str(tmp_path))
    c.save_counters()
    p2, inst2 = plant_with_line()
    assert inst2.vars["Tonnage_t"].raw != 1234.5
    Control(p2, str(tmp_path)).load_counters()
    assert inst2.vars["Tonnage_t"].raw == 1234.5
    p2.step(1.0)   # publié au premier cycle, puis le compteur reprend sa course
    assert inst2.vars["Tonnage_t"].value >= 1234.5 and inst2.vars["Tonnage_t"] in p2.changed
