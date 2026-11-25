"""Découverte automatique : reconnaissance des instances de machines par signature de structure."""
from __future__ import annotations

import logging
from collections import Counter

from . import db, opc
from .config import settings

log = logging.getLogger("discovery")
MAX_DEPTH = 3


def members_of(nodes: dict[str, opc.BNode], nid: str) -> dict[str, tuple[str, str]]:
    """Feuilles numériques sous un nœud, jusqu'à MAX_DEPTH niveaux : chemin relatif -> (NodeId, type)."""
    out: dict[str, tuple[str, str]] = {}
    stack = [(c, nodes[c].name, 1) for c in nodes[nid].children]
    while stack:
        cid, rel, depth = stack.pop()
        n = nodes[cid]
        if n.is_var and not n.children:
            if n.datatype:
                out[rel] = (n.node_id, n.datatype)
        elif depth < MAX_DEPTH:
            stack.extend((c, f"{rel}.{nodes[c].name}", depth + 1) for c in n.children)
    return out


def match_type(members: dict[str, tuple[str, str]], types: list[dict]) -> tuple[dict | None, float]:
    """Renvoie le type dont toute la signature est présente, et la part de ses membres trouvés."""
    best, best_cov = None, 0.0
    for t in types:
        if all(s["path"] in members and members[s["path"]][1] == s["datatype"] for s in t["signature"]):
            cov = sum(1 for m in t["members"] if m["path"] in members) / max(1, len(t["members"]))
            if best is None or cov > best_cov or (cov == best_cov and len(t["signature"]) > len(best["signature"])):
                best, best_cov = t, cov
    return best, best_cov


def analyse(root_id: str, nodes: dict[str, opc.BNode], types: list[dict]) -> dict:
    """Parcourt l'arbre de haut en bas ; un nœud reconnu n'est pas exploré plus loin."""
    sig_names = {s["path"].split(".")[0] for t in types for s in t["signature"]}
    instances, unknown = [], Counter()
    unknown_examples: dict[tuple, list[str]] = {}
    raw_leaves = 0
    stack = list(nodes[root_id].children)
    while stack:
        nid = stack.pop()
        n = nodes[nid]
        if not n.children:
            continue
        child_names = {nodes[c].name for c in n.children}
        matched = None
        if child_names & sig_names:
            members = members_of(nodes, nid)
            t, cov = match_type(members, types)
            if t is not None:
                db_name = n.path.split(".")[0]
                instances.append({
                    "base_node": nid, "path": n.path, "db": db_name, "name": n.name,
                    "type": t["code"], "coverage": round(cov, 2),
                    "members": {p: {"node_id": v[0], "datatype": v[1]} for p, v in sorted(members.items())},
                    "missing": [m["path"] for m in t["members"] if m["path"] not in members and not m["optional"]],
                })
                matched = t
        if matched is None:
            leaf_children = [nodes[c] for c in n.children if nodes[c].is_var and not nodes[c].children]
            if leaf_children and len(leaf_children) == len(n.children):
                key = tuple(sorted(f"{c.name}:{c.datatype}" for c in leaf_children))
                unknown[key] += 1
                unknown_examples.setdefault(key, [])
                if len(unknown_examples[key]) < 3:
                    unknown_examples[key].append(n.path)
                raw_leaves += sum(1 for c in leaf_children if c.datatype)
            else:
                stack.extend(n.children)
    instances.sort(key=lambda i: i["path"])
    return {
        "instances": instances,
        "unknown": [{"members": list(k), "count": c, "examples": unknown_examples[k]}
                    for k, c in unknown.most_common()],
        "unknown_tags": raw_leaves,
        "total_nodes": len(nodes),
    }


async def run(actor: str | None) -> dict:
    async with db.pool().acquire() as conn:
        run_id = await conn.fetchval("INSERT INTO catalog.discovery_run (started_by) VALUES ($1) RETURNING id", actor)
        types = [r["definition"] for r in await conn.fetch("SELECT definition FROM catalog.machine_type ORDER BY code")]
    try:
        async with opc.connect() as client:
            root_id, nodes = await opc.browse_tree(client, settings.browse_root)
        result = analyse(root_id, nodes, types)
        async with db.pool().acquire() as conn:
            existing = {r["base_node"]: dict(r) for r in await conn.fetch(
                """SELECT m.id, m.name, m.base_node, t.code AS type FROM catalog.machine m
                   JOIN catalog.machine_type t ON t.id = m.type_id WHERE m.enabled""")}
            found = set()
            for inst in result["instances"]:
                ex = existing.get(inst["base_node"])
                found.add(inst["base_node"])
                inst["status"] = "nouveau" if ex is None else ("existant" if ex["type"] == inst["type"] else "type_change")
                if ex:
                    inst["machine_id"] = ex["id"]
                    inst["name"] = ex["name"]
            result["disappeared"] = [{"id": e["id"], "name": e["name"], "base_node": b}
                                     for b, e in existing.items() if b not in found]
            await conn.execute("UPDATE catalog.discovery_run SET finished_at = now(), status = 'termine', result = $2 WHERE id = $1",
                               run_id, result)
        log.info("découverte %d : %d instances, %d structures inconnues", run_id, len(result["instances"]), len(result["unknown"]))
        return {"id": run_id, "status": "termine", **result}
    except Exception as e:
        async with db.pool().acquire() as conn:
            await conn.execute("UPDATE catalog.discovery_run SET finished_at = now(), status = 'erreur', error = $2 WHERE id = $1",
                               run_id, str(e))
        raise


async def ensure_area(conn, path: str | None) -> int | None:
    """Crée au besoin l'arborescence « Atelier/Ligne » et renvoie l'id de la dernière zone."""
    if not path:
        return None
    parent = None
    for part in [p.strip() for p in path.split("/") if p.strip()]:
        row = await conn.fetchval(
            "SELECT id FROM catalog.area WHERE name = $1 AND parent_id IS NOT DISTINCT FROM $2", part, parent)
        if row is None:
            row = await conn.fetchval("INSERT INTO catalog.area (parent_id, name) VALUES ($1, $2) RETURNING id", parent, part)
        parent = row
    return parent


async def upsert_machine(conn, type_def: dict, type_id: int, name: str, base_node: str,
                         members: dict[str, dict], area: str | None, attributes: dict) -> int:
    area_id = await ensure_area(conn, area)
    mid = await conn.fetchval("SELECT id FROM catalog.machine WHERE base_node = $1", base_node)
    if mid is None:
        mid = await conn.fetchval(
            """INSERT INTO catalog.machine (type_id, area_id, name, base_node, attributes)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""", type_id, area_id, name, base_node, attributes)
    else:
        await conn.execute(
            """UPDATE catalog.machine SET type_id = $2, name = $3, enabled = true,
                      area_id = coalesce($4, area_id), attributes = attributes || $5 WHERE id = $1""",
            mid, type_id, name, area_id, attributes)
    defs = {m["path"]: m for m in type_def["members"]}
    for path, info in members.items():
        d = defs.get(path, {})
        tag_id = await conn.fetchval(
            """INSERT INTO catalog.tag (node_id, name, data_type, unit, machine_id, member, role,
                                        deadband_kind, deadband_value, eu_min, eu_max)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
               ON CONFLICT (node_id) DO UPDATE SET name = $2, data_type = $3, unit = coalesce($4, catalog.tag.unit),
                  machine_id = $5, member = $6, role = $7, enabled = true,
                  deadband_kind = CASE WHEN catalog.tag.machine_id IS NULL THEN $8 ELSE catalog.tag.deadband_kind END,
                  deadband_value = CASE WHEN catalog.tag.machine_id IS NULL THEN $9 ELSE catalog.tag.deadband_value END
               RETURNING id""",
            info["node_id"], f"{name}.{path}", info["datatype"], d.get("unit"), mid, path, d.get("role"),
            d.get("deadband_kind", "none"), d.get("deadband_value", 0.0), d.get("eu_min"), d.get("eu_max"))
        alarm = d.get("alarm")
        if alarm:
            await conn.execute(
                """INSERT INTO mes.alarm_def (tag_id, condition, threshold, priority, message)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (tag_id, condition, threshold) DO UPDATE SET priority = $4, message = $5""",
                tag_id, alarm.get("condition", "vrai"), alarm.get("seuil"), alarm.get("priorite", "moyenne"),
                f"{name} : {alarm.get('message', path)}")
    if any(m.get("role") == "courant_moteur" for m in type_def["members"]):
        nominal = (attributes or {}).get("courant_nominal")
        if nominal:
            await conn.execute(
                """INSERT INTO mes.motor_reference (machine_id, ref_current, method) VALUES ($1, $2, 'nominal')
                   ON CONFLICT (machine_id) DO NOTHING""", mid, float(nominal))
    return mid


async def apply(run_id: int, selections: list[dict], include_unknown: bool, disable_missing: bool,
                actor: str | None) -> dict:
    async with db.pool().acquire() as conn:
        run_row = await conn.fetchrow("SELECT * FROM catalog.discovery_run WHERE id = $1", run_id)
        if run_row is None or run_row["result"] is None:
            raise ValueError("découverte introuvable ou sans résultat")
        result = run_row["result"]
        by_node = {i["base_node"]: i for i in result["instances"]}
        types = {r["code"]: (r["id"], r["definition"]) for r in await conn.fetch("SELECT id, code, definition FROM catalog.machine_type")}
        created = updated = 0
        async with conn.transaction():
            for sel in selections:
                inst = by_node.get(sel["base_node"])
                if inst is None:
                    continue
                type_id, tdef = types[inst["type"]]
                existed = inst.get("machine_id") is not None
                await upsert_machine(conn, tdef, type_id, sel.get("name") or inst["name"], inst["base_node"],
                                     inst["members"], sel.get("area"), sel.get("attributes") or {})
                created += not existed
                updated += existed
            raw = 0
            if include_unknown:
                async with opc.connect() as client:
                    root_id, nodes = await opc.browse_tree(client, settings.browse_root)
                known = {m["node_id"] for i in result["instances"] for m in i["members"].values()}
                for n in nodes.values():
                    if n.is_var and not n.children and n.datatype and n.node_id not in known:
                        r = await conn.execute(
                            "INSERT INTO catalog.tag (node_id, name, data_type) VALUES ($1, $2, $3) ON CONFLICT (node_id) DO NOTHING",
                            n.node_id, n.path, n.datatype)
                        raw += r.endswith("1")
            disabled = 0
            if disable_missing:
                for d in result.get("disappeared", []):
                    await conn.execute("UPDATE catalog.machine SET enabled = false WHERE id = $1", d["id"])
                    await conn.execute("UPDATE catalog.tag SET enabled = false WHERE machine_id = $1", d["id"])
                    disabled += 1
            await conn.execute("UPDATE catalog.discovery_run SET status = 'applique' WHERE id = $1", run_id)
            summary = {"machines_creees": created, "machines_mises_a_jour": updated,
                       "tags_bruts_crees": raw, "machines_desactivees": disabled}
            await db.audit(conn, actor, "decouverte_appliquee", "discovery_run", run_id, summary)
    return summary
