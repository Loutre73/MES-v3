import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simulator import DEFAULT_RULES, STRUCTS, VARIANT, Instance, Plant, Var  # noqa: E402
from scenarios import Engine, select, normalize_selection  # noqa: E402
from asyncua import ua  # noqa: E402

LAYOUT = [("DB_Moteurs", "moteur", "M", 101, 30), ("DB_Lignes", "ligne", "L", 1, 3),
          ("DB_Energie", "compteur_energie", "C", 201, 4), ("DB_Process", "mesure", "P", 1, 40)]


def make_plant(tmp_path=None, **cfg):
    p = Plant({"time_acceleration": 60, "vitesse": 1, **cfg}, random.Random(1))
    for db, struct, prefix, start, n in LAYOUT:
        p.rules[db] = dict(DEFAULT_RULES[struct])
        p.structs[db], p.counts[db] = struct, n
        for k in p.rules[db]:          # comportement aléatoire coupé : tests déterministes
            if k in ("arrets_h", "bruit_pct", "change_ratio", "amplitude_pct", "derive_ratio"):
                p.rules[db][k] = 0
        for i in range(n):
            inst = Instance(f"{prefix}{start + i}", struct, db)
            for m, dt in STRUCTS[struct]:
                vt = VARIANT[dt]
                inst.vars[m] = Var(ua.NodeId(f"{inst.name}.{m}", 3), vt, False if vt == ua.VariantType.Boolean else 0)
                p.tag_index.append((f"{db}.{inst.name}.{m}", inst.vars[m]))
            p.init_state(inst, {})
            p.instances.append(inst)
            p.by_name[inst.name] = inst
    p.bind_engine(Engine(str(tmp_path) if tmp_path else None))
    return p


def run(p, sid, body, steps=1, dt=1.0):
    p.engine.put_seq(sid, body)
    p.engine.start(sid, p.clock)
    for _ in range(steps):
        p.step(dt)


def test_selection_par_type_plage_et_membres():
    p = make_plant()
    sel = normalize_selection({"type": "moteur", "instances": "M101-M110, M120", "membres": ["Courant"]}, p.engine.structs)
    hits = select(sel, p.instances)
    assert [i.name for i, _ in hits] == [f"M{n}" for n in range(101, 111)] + ["M120"]
    assert all(ms == ["Courant"] for _, ms in hits)
    assert p.engine.preview({"type": "mesure", "instances": "P1*"})["n_instances"] == 11   # P1, P10..P19
    assert p.engine.preview({"type": "tous", "contient": "DB_Lignes"})["tags"] == 12
    with pytest.raises(ValueError):
        normalize_selection({"type": "moteur", "membres": ["Tonnage_t"]}, p.engine.structs)


def test_defaut_sur_un_groupe_puis_redemarrage():
    p = make_plant()
    p.engine.put_group("l13", {"nom": "L1 et L3", "type": "ligne", "instances": "L1 L3"})
    run(p, "s", {"nom": "Panne", "duree_s": 30, "regles": [
        {"effet": "defaut", "cible": "l13", "params": {"code": 9}, "debut_s": 0, "duree_s": 30}]})
    L1, L2 = p.by_name["L1"].vars, p.by_name["L2"].vars
    assert L1["Marche"].value is False and L1["Code_arret"].value == 9 and L1["Debit_th"].value == 0
    assert L2["Marche"].value is True
    for _ in range(30):
        p.step(1.0)
    assert "s" not in p.engine.running and L1["Marche"].value is True and L1["Code_arret"].value == 0


def test_regles_empilees_sur_la_meme_variable():
    p = make_plant()
    cible = {"type": "moteur", "instances": "M101", "membres": ["Courant"]}
    p.step(1.0)
    base = p.by_name["M101"].vars["Courant"].raw
    run(p, "s", {"nom": "x", "duree_s": 100, "regles": [
        {"effet": "facteur", "cible": cible, "params": {"facteur": 2}, "debut_s": 0, "duree_s": 100},
        {"effet": "decalage", "cible": cible, "params": {"valeur": 5}, "debut_s": 0, "duree_s": 100}]})
    v = p.by_name["M101"].vars["Courant"]
    assert v.value == pytest.approx(base * 2 + 5, abs=0.01)
    assert v.raw == pytest.approx(base, abs=0.01)                 # la valeur brute n'est pas modifiée
    assert p.by_name["M102"].vars["Courant"].value != v.value
    p.engine.stop("s")
    p.step(1.0)
    assert v.value == pytest.approx(base, abs=0.01)                # retour à la normale


def test_rampe_repetition_et_figer():
    p = make_plant()
    cible = {"type": "moteur", "instances": "M101", "membres": ["Courant"]}
    p.step(1.0)
    base = p.by_name["M101"].vars["Courant"].raw
    run(p, "s", {"nom": "x", "duree_s": 1000, "regles": [
        {"effet": "rampe", "cible": cible, "params": {"pct": 100}, "debut_s": 0, "duree_s": 100}]}, steps=50)
    assert p.by_name["M101"].vars["Courant"].value == pytest.approx(base * 1.5, rel=0.03)
    occ = p.engine.occurrences
    p.engine.put_seq("r", {"nom": "r", "duree_s": 600, "regles": [
        {"effet": "arret", "cible": {"type": "ligne", "instances": "L1"}, "debut_s": 10, "duree_s": 20, "repeter_s": 120}]})
    p.engine.running["r"] = 0
    assert [bool(occ("r", t)) for t in (5, 15, 40, 135, 245, 250)] == [False, True, False, True, False, True]
    with pytest.raises(ValueError):
        p.engine.put_seq("bad", {"nom": "b", "duree_s": 600, "regles": [
            {"effet": "arret", "cible": {"type": "ligne"}, "debut_s": 0, "duree_s": 60, "repeter_s": 30}]})


def test_probabilite_tiree_une_fois_par_occurrence():
    p = make_plant()
    p.engine.put_seq("s", {"nom": "x", "duree_s": 100000, "regles": [
        {"effet": "arret", "cible": {"type": "ligne"}, "debut_s": 0, "duree_s": 10, "repeter_s": 100, "proba": 0.5}]})
    p.engine.running["s"] = 0
    hits = [bool(p.engine.occurrences("s", k * 100 + 1)) for k in range(400)]
    assert 120 < sum(hits) < 280
    assert hits == [bool(p.engine.occurrences("s", k * 100 + 5)) for k in range(400)]   # stable dans l'occurrence


def test_vitesse_et_pause():
    p = make_plant()
    L1 = p.by_name["L1"].vars
    p.step(1.0)
    p.step(1.0)
    t1 = L1["Tonnage_t"].value
    p.set_rules({"global": {"vitesse": 3600}})
    c0 = p.clock
    p.step(1.0)
    assert p.clock - c0 == pytest.approx(3600)
    assert L1["Tonnage_t"].value - t1 == pytest.approx(L1["Debit_th"].value, rel=0.05)   # 1 h de production
    p.set_rules({"global": {"vitesse": 0}})
    t2, c1 = L1["Tonnage_t"].value, p.clock
    p.step(1.0)
    assert L1["Tonnage_t"].value == t2 and p.clock == c1


def test_qualite_coupure_et_valeur_booleenne():
    p = make_plant()
    run(p, "s", {"nom": "x", "duree_s": 60, "regles": [
        {"effet": "qualite", "cible": {"type": "mesure", "instances": "P1-P5"}, "debut_s": 0, "duree_s": 60},
        {"effet": "valeur", "cible": {"type": "moteur", "instances": "M101", "membres": ["Defaut"]},
         "params": {"valeur": 1}, "debut_s": 0, "duree_s": 60},
        {"effet": "coupure", "debut_s": 0, "duree_s": 60}]})
    assert [p.by_name[f"P{i}"].vars["Valeur"].bad for i in (1, 5, 6)] == [True, True, False]
    assert p.by_name["M101"].vars["Defaut"].value is True and p.coupure
    assert p.search(names=["DB_Process.P1.Valeur"])[0]["qualite"] == "mauvaise"


def test_validation_et_suppression(tmp_path):
    p = make_plant(tmp_path)
    e = p.engine
    with pytest.raises(ValueError):
        e.put_seq("x", {"nom": "x", "duree_s": 60, "regles": [{"effet": "arret", "cible": "inconnu"}]})
    with pytest.raises(ValueError):
        e.put_seq("x", {"nom": "x", "duree_s": 60, "regles": [{"effet": "arret", "cible": {}, "debut_s": 90}]})
    with pytest.raises(ValueError):
        e.put_seq("x", {"nom": "x", "duree_s": 60, "regles": [{"effet": "explosion"}]})
    with pytest.raises(ValueError):
        e.put_group("a b", {"nom": "x"})
    e.put_group("g", {"nom": "G", "type": "ligne"})
    e.put_seq("s", {"nom": "S", "duree_s": 60, "regles": [{"effet": "arret", "cible": "g"}]})
    with pytest.raises(ValueError):
        e.delete_group("g")
    e.delete_seq("s")
    e.delete_group("g")
    with pytest.raises(LookupError):
        e.start("s", 0)


def test_exemples_valides_et_persistance(tmp_path):
    p = make_plant(tmp_path)
    p.engine.load()                                   # pas de fichier : exemples du site
    st = p.state()
    assert {s["id"] for s in st["scenarios"]} >= {"panne-ligne", "micro-arrets", "cascade", "coupure-opcua"}
    assert next(g for g in st["groupes"] if g["id"] == "courants")["tags"] == 30
    p.engine.put_group("perso", {"nom": "Perso", "type": "moteur", "instances": "M101-M105"})
    p2 = make_plant(tmp_path)
    p2.engine.load()
    assert p2.engine.groups["perso"]["instances"] == "M101-M105"
    for sid in p2.engine.seqs:                         # chaque exemple tourne sans erreur
        p2.engine.start(sid, p2.clock)
    for _ in range(5):
        p2.step(1.0)


def test_debit_des_lignes_et_tonnage():
    p = make_plant()
    p.engine.load()
    assert p.engine.count("debits") == {"n_instances": 3, "tags": 3}
    p.step(1.0)
    L1 = p.by_name["L1"].vars
    base = L1["Debit_th"].raw
    run(p, "s", {"nom": "x", "duree_s": 600, "regles": [
        {"effet": "facteur", "cible": "debits", "params": {"facteur": 0.5}, "debut_s": 0, "duree_s": 600}]}, steps=2)
    assert L1["Debit_th"].value == pytest.approx(base * 0.5, abs=0.1)
    t0 = L1["Tonnage_t"].value
    p.step(3600.0)          # le tonnage suit le débit réduit
    assert L1["Tonnage_t"].value - t0 == pytest.approx(base * 0.5, rel=0.05)
