from conftest import ROOT

from mes_api.discovery import analyse
from mes_api.opc import BNode
from mes_api.typedefs import load_dir, normalize_type

TYPES = [normalize_type(t) for t in load_dir(str(ROOT / "catalog" / "types"))]


def tree(spec: dict) -> tuple[str, dict]:
    """spec : {chemin: type de donnée ou None pour un dossier}."""
    nodes = {"root": BNode("root", "DataBlocksGlobal", "", None, False, [])}
    for path, dt in spec.items():
        parts = path.split(".")
        parent = "root"
        for i, p in enumerate(parts):
            nid = ".".join(parts[: i + 1])
            if nid not in nodes:
                leaf = i == len(parts) - 1 and dt is not None
                nodes[nid] = BNode(nid, p, nid, parent, leaf, [], dt if leaf else None)
                nodes[parent].children.append(nid)
            parent = nid
    return "root", nodes


def test_recognises_motors_by_signature_whatever_their_names():
    spec = {}
    for name in ("M101", "Pompe_7"):
        spec |= {f"DB_Moteurs.{name}.Courant": "Float", f"DB_Moteurs.{name}.Marche": "Boolean",
                 f"DB_Moteurs.{name}.Defaut": "Boolean", f"DB_Moteurs.{name}.Vitesse": "Float"}
    res = analyse(*tree(spec), TYPES)
    assert [i["name"] for i in res["instances"]] == ["M101", "Pompe_7"]
    assert all(i["type"] == "moteur" for i in res["instances"])
    assert res["instances"][0]["coverage"] == 0.8  # Heures absent (optionnel)
    assert res["instances"][0]["missing"] == []


def test_wrong_datatype_is_not_recognised():
    spec = {"DB.X.Courant": "Int16", "DB.X.Marche": "Boolean", "DB.X.Defaut": "Boolean"}
    res = analyse(*tree(spec), TYPES)
    assert res["instances"] == []
    assert res["unknown"][0]["count"] == 1


def test_line_and_unknown_structures():
    spec = {"DB_L.L1.Marche": "Boolean", "DB_L.L1.Tonnage_t": "Double", "DB_L.L1.Debit_th": "Float",
            "DB_P.P1.Valeur": "Float", "DB_P.P2.Valeur": "Float"}
    res = analyse(*tree(spec), TYPES)
    assert [(i["name"], i["type"]) for i in res["instances"]] == [("L1", "ligne")]
    assert res["unknown"][0]["count"] == 2
    assert res["unknown_tags"] == 2


def test_nested_struct_members():
    spec = {"DB.M1.Courant": "Float", "DB.M1.Marche": "Boolean", "DB.M1.Defaut": "Boolean", "DB.M1.Diag.Temp": "Float"}
    res = analyse(*tree(spec), TYPES)
    assert "Diag.Temp" in res["instances"][0]["members"]
