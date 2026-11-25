"""Types de machines et types de recettes définis en YAML, livrés avec le produit."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from . import db
from .config import settings

log = logging.getLogger("types")

# Types TIA Portal -> types OPC UA tels que le serveur du S7-1500 les expose
TIA_TO_OPC = {
    "Bool": "Boolean", "Byte": "Byte", "Word": "UInt16", "DWord": "UInt32", "LWord": "UInt64",
    "SInt": "SByte", "USInt": "Byte", "Int": "Int16", "UInt": "UInt16", "DInt": "Int32",
    "UDInt": "UInt32", "LInt": "Int64", "ULInt": "UInt64", "Real": "Float", "LReal": "Double",
}
DEADBAND_KIND = {"absolu": "absolute", "pourcent": "percent", "aucune": "none"}


def normalize_type(raw: dict) -> dict:
    """Valide et normalise une définition de type de machine."""
    for key in ("type", "libelle", "signature", "membres"):
        if key not in raw:
            raise ValueError(f"type {raw.get('type', '?')} : clé {key} manquante")
    members = []
    for m in raw["membres"]:
        db_ = m.get("deadband") or {}
        members.append({
            "path": m["path"],
            "unit": m.get("unite"),
            "role": m.get("role"),
            "optional": bool(m.get("optionnel", False)),
            "deadband_kind": DEADBAND_KIND.get(db_.get("type", "aucune"), "none"),
            "deadband_value": float(db_.get("valeur", 0)),
            "eu_min": m.get("min"),
            "eu_max": m.get("max"),
            "alarm": m.get("alarme"),
        })
    signature = []
    for s in raw["signature"]:
        dt = s.get("datatype")
        if dt not in TIA_TO_OPC:
            raise ValueError(f"type {raw['type']} : type de donnée inconnu {dt}")
        signature.append({"path": s["path"], "datatype": TIA_TO_OPC[dt]})
    return {
        "code": raw["type"],
        "version": int(raw.get("version", 1)),
        "label": raw["libelle"],
        "signature": signature,
        "members": members,
        "attributes": [
            {"name": a["nom"], "label": a.get("libelle", a["nom"]), "unit": a.get("unite"),
             "required": bool(a.get("obligatoire", False))}
            for a in raw.get("attributs", [])
        ],
        "track_stops": bool(raw.get("suivi_arrets", False)),
    }


def load_dir(path: str) -> list[dict]:
    out = []
    p = Path(path)
    if not p.exists():
        return out
    for f in sorted(p.glob("*.yaml")):
        with f.open(encoding="utf-8") as fh:
            out.append(yaml.safe_load(fh))
    return out


async def sync_machine_types() -> int:
    types = [normalize_type(t) for t in load_dir(settings.types_dir)]
    async with db.pool().acquire() as conn:
        for t in types:
            await conn.execute(
                """INSERT INTO catalog.machine_type (code, version, label, definition, updated_at)
                   VALUES ($1, $2, $3, $4, now())
                   ON CONFLICT (code) DO UPDATE SET version = $2, label = $3, definition = $4, updated_at = now()""",
                t["code"], t["version"], t["label"], t)
    log.info("%d types de machines chargés", len(types))
    return len(types)


def normalize_recipe_type(raw: dict) -> dict:
    for key in ("type", "libelle", "db", "parametres"):
        if key not in raw:
            raise ValueError(f"type de recette {raw.get('type', '?')} : clé {key} manquante")
    return {
        "code": raw["type"],
        "label": raw["libelle"],
        "db": raw["db"],
        "buffer": raw.get("tampon", "Tampon"),
        "active": raw.get("actif", "Actif"),
        "handshake": {
            "request": raw.get("echange", {}).get("demande", "Demande"),
            "answer": raw.get("echange", {}).get("reponse", "Reponse"),
            "code": raw.get("echange", {}).get("code", "Code"),
            "timeout_s": float(raw.get("echange", {}).get("delai_s", 10)),
        },
        "params": [
            {"name": p["nom"], "label": p.get("libelle", p["nom"]), "unit": p.get("unite"),
             "datatype": TIA_TO_OPC.get(p.get("datatype", "Real"), "Float"),
             "min": p.get("min"), "max": p.get("max"), "default": p.get("defaut", 0)}
            for p in raw["parametres"]
        ],
    }


async def sync_recipe_types() -> int:
    types = [normalize_recipe_type(t) for t in load_dir(settings.recipes_dir)]
    async with db.pool().acquire() as conn:
        for t in types:
            await conn.execute(
                """INSERT INTO mes.recipe_type (code, label, definition, updated_at) VALUES ($1, $2, $3, now())
                   ON CONFLICT (code) DO UPDATE SET label = $2, definition = $3, updated_at = now()""",
                t["code"], t["label"], t)
    log.info("%d types de recettes chargés", len(types))
    return len(types)


def siemens_node(db_name: str, *parts: str) -> str:
    """NodeId chaîne au format du serveur OPC UA S7-1500 : ns=3;s="DB"."A"."B"."""
    return "ns=3;s=" + ".".join(f'"{p}"' for p in (db_name, *parts))
