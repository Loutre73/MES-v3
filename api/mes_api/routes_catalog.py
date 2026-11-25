"""Catalogue : types, découverte, machines, tags, import et export CSV."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import db, discovery
from .auth import User, current_user, require
from .typedefs import siemens_node

router = APIRouter(prefix="/api/catalogue", tags=["catalogue"])


@router.get("/types")
async def list_types(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT t.id, t.code, t.label, t.version, t.definition,
                      (SELECT count(*) FROM catalog.machine m WHERE m.type_id = t.id AND m.enabled) AS machines
               FROM catalog.machine_type t ORDER BY t.code"""))


@router.post("/decouverte")
async def start_discovery(user: User = Depends(require())):
    try:
        return await discovery.run(user.username)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Découverte impossible : {e}") from e


@router.get("/decouverte/{run_id}")
async def get_discovery(run_id: int, _: User = Depends(require())):
    async with db.pool().acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM catalog.discovery_run WHERE id = $1", run_id)
    if r is None:
        raise HTTPException(404, "Découverte introuvable")
    return dict(r)


class Selection(BaseModel):
    base_node: str
    name: str | None = None
    area: str | None = None
    attributes: dict | None = None


class ApplyBody(BaseModel):
    selections: list[Selection]
    include_unknown: bool = False
    disable_missing: bool = False


@router.post("/decouverte/{run_id}/appliquer")
async def apply_discovery(run_id: int, body: ApplyBody, user: User = Depends(require())):
    try:
        return await discovery.apply(run_id, [s.model_dump() for s in body.selections],
                                     body.include_unknown, body.disable_missing, user.username)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/zones")
async def list_areas(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """WITH RECURSIVE z AS (
                 SELECT id, parent_id, name, name AS path FROM catalog.area WHERE parent_id IS NULL
                 UNION ALL SELECT a.id, a.parent_id, a.name, z.path || '/' || a.name FROM catalog.area a JOIN z ON a.parent_id = z.id)
               SELECT id, parent_id, name, path FROM z ORDER BY path"""))


MACHINES_SQL = """
WITH RECURSIVE z AS (
  SELECT id, name AS path FROM catalog.area WHERE parent_id IS NULL
  UNION ALL SELECT a.id, z.path || '/' || a.name FROM catalog.area a JOIN z ON a.parent_id = z.id)
SELECT m.id, m.name, m.base_node, m.attributes, m.enabled, t.code AS type, t.label AS type_label,
       z.path AS area, (SELECT count(*) FROM catalog.tag g WHERE g.machine_id = m.id AND g.enabled) AS tags
FROM catalog.machine m JOIN catalog.machine_type t ON t.id = m.type_id LEFT JOIN z ON z.id = m.area_id
"""


@router.get("/machines")
async def list_machines(type: str | None = None, _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        if type:
            return db.rows(await conn.fetch(MACHINES_SQL + " WHERE t.code = $1 ORDER BY m.name", type))
        return db.rows(await conn.fetch(MACHINES_SQL + " ORDER BY t.code, m.name"))


class MachinePatch(BaseModel):
    name: str | None = None
    area: str | None = None
    attributes: dict | None = None
    enabled: bool | None = None


@router.patch("/machines/{mid}")
async def patch_machine(mid: int, body: MachinePatch, user: User = Depends(require())):
    async with db.pool().acquire() as conn, conn.transaction():
        m = await conn.fetchrow("SELECT * FROM catalog.machine WHERE id = $1", mid)
        if m is None:
            raise HTTPException(404, "Machine introuvable")
        if body.name and body.name != m["name"]:
            await conn.execute("UPDATE catalog.machine SET name = $2 WHERE id = $1", mid, body.name)
            await conn.execute("UPDATE catalog.tag SET name = $2 || '.' || member WHERE machine_id = $1 AND member IS NOT NULL",
                               mid, body.name)
        if body.area is not None:
            await conn.execute("UPDATE catalog.machine SET area_id = $2 WHERE id = $1", mid,
                               await discovery.ensure_area(conn, body.area))
        if body.attributes is not None:
            await conn.execute("UPDATE catalog.machine SET attributes = attributes || $2 WHERE id = $1", mid, body.attributes)
            if "courant_nominal" in body.attributes and body.attributes["courant_nominal"] not in (None, ""):
                await conn.execute(
                    """INSERT INTO mes.motor_reference (machine_id, ref_current, method, set_by) VALUES ($1, $2, 'nominal', $3)
                       ON CONFLICT (machine_id) DO UPDATE SET ref_current = $2, method = 'nominal', set_at = now(), set_by = $3
                       WHERE mes.motor_reference.method = 'nominal'""",
                    mid, float(body.attributes["courant_nominal"]), user.username)
        if body.enabled is not None:
            await conn.execute("UPDATE catalog.machine SET enabled = $2 WHERE id = $1", mid, body.enabled)
            await conn.execute("UPDATE catalog.tag SET enabled = $2 WHERE machine_id = $1", mid, body.enabled)
        await db.audit(conn, user.username, "machine_modifiee", "machine", mid, body.model_dump(exclude_none=True))
    return {"ok": True}


@router.get("/tags")
async def list_tags(q: str = "", machine_id: int | None = None, limit: int = 200, _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT id, node_id, name, data_type, unit, machine_id, member, role, deadband_kind, deadband_value,
                      eu_min, eu_max, historize, enabled
               FROM catalog.tag WHERE ($1 = '' OR name ILIKE '%' || $1 || '%')
                 AND ($2::int IS NULL OR machine_id = $2) ORDER BY name LIMIT $3""", q, machine_id, min(limit, 2000)))


# ----------------------------------------------------------------------------- CSV
def _csv_response(header: list[str], data: list[list], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    w.writerows(data)
    # BOM pour une ouverture directe dans Excel
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _read_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,")
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


@router.get("/export/machines.csv")
async def export_machines(_: User = Depends(require())):
    async with db.pool().acquire() as conn:
        ms = await conn.fetch(MACHINES_SQL + " ORDER BY t.code, m.name")
    attrs = sorted({k for m in ms for k in (m["attributes"] or {})})
    data = [[m["name"], m["type"], m["area"] or "", m["base_node"], "oui" if m["enabled"] else "non",
             *[(m["attributes"] or {}).get(a, "") for a in attrs]] for m in ms]
    return _csv_response(["machine", "type", "zone", "noeud_opcua", "actif", *[f"attr:{a}" for a in attrs]], data, "machines.csv")


@router.post("/import/machines")
async def import_machines(file: UploadFile = File(...), user: User = Depends(require())):
    """Crée ou met à jour des machines. Les tags sont déduits du type (NodeId au format S7-1500)."""
    lines = _read_csv(await file.read())
    errors, done = [], 0
    async with db.pool().acquire() as conn, conn.transaction():
        types = {r["code"]: (r["id"], r["definition"]) for r in await conn.fetch("SELECT id, code, definition FROM catalog.machine_type")}
        for i, row in enumerate(lines, start=2):
            t = types.get((row.get("type") or "").strip())
            base = (row.get("noeud_opcua") or "").strip()
            name = (row.get("machine") or "").strip()
            if not t or not base or not name:
                errors.append(f"ligne {i} : machine, type connu et noeud_opcua obligatoires")
                continue
            type_id, tdef = t
            if not (base.startswith("ns=") and base.endswith('"')):
                errors.append(f"ligne {i} : noeud_opcua attendu au format ns=3;s=\"DB\".\"Instance\"")
                continue
            members = {m["path"]: {"node_id": base + "." + ".".join(f'"{p}"' for p in m["path"].split(".")),
                                   "datatype": next((s["datatype"] for s in tdef["signature"] if s["path"] == m["path"]), "Float")}
                       for m in tdef["members"]}
            attributes = {k[5:]: _num(v) for k, v in row.items() if k and k.startswith("attr:") and v not in (None, "")}
            mid = await discovery.upsert_machine(conn, tdef, type_id, name, base, members, row.get("zone") or None, attributes)
            if (row.get("actif") or "oui").strip().lower() in ("non", "0", "false"):
                await conn.execute("UPDATE catalog.machine SET enabled = false WHERE id = $1", mid)
                await conn.execute("UPDATE catalog.tag SET enabled = false WHERE machine_id = $1", mid)
            done += 1
        await db.audit(conn, user.username, "import_machines", details={"lignes": done, "erreurs": len(errors)})
    return {"importees": done, "erreurs": errors}


TAG_COLS = ["node_id", "name", "data_type", "unit", "role", "deadband_kind", "deadband_value", "eu_min", "eu_max", "historize", "enabled"]


@router.get("/export/tags.csv")
async def export_tags(_: User = Depends(require())):
    async with db.pool().acquire() as conn:
        rs = await conn.fetch(f"SELECT {', '.join(TAG_COLS)} FROM catalog.tag ORDER BY name")
    data = [[("oui" if v else "non") if isinstance(v, bool) else ("" if v is None else v) for v in r.values()] for r in rs]
    return _csv_response(TAG_COLS, data, "tags.csv")


@router.post("/import/tags")
async def import_tags(file: UploadFile = File(...), user: User = Depends(require())):
    """Met à jour unités, rôles, deadbands et activation des tags existants (clé : node_id)."""
    lines = _read_csv(await file.read())
    errors, done = [], 0
    async with db.pool().acquire() as conn, conn.transaction():
        for i, row in enumerate(lines, start=2):
            kind = (row.get("deadband_kind") or "none").strip()
            if kind not in ("none", "absolute", "percent"):
                errors.append(f"ligne {i} : deadband_kind doit valoir none, absolute ou percent")
                continue
            r = await conn.execute(
                """UPDATE catalog.tag SET unit = nullif($2, ''), role = nullif($3, ''), deadband_kind = $4,
                          deadband_value = $5, eu_min = $6, eu_max = $7, historize = $8, enabled = $9
                   WHERE node_id = $1""",
                row["node_id"].strip(), row.get("unit") or "", row.get("role") or "", kind,
                float(_num(row.get("deadband_value")) or 0), _num(row.get("eu_min")), _num(row.get("eu_max")),
                _yes(row.get("historize")), _yes(row.get("enabled")))
            if r.endswith(" 0"):
                errors.append(f"ligne {i} : tag inconnu {row['node_id']}")
            else:
                done += 1
        await db.audit(conn, user.username, "import_tags", details={"lignes": done, "erreurs": len(errors)})
    return {"mis_a_jour": done, "erreurs": errors}


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return v


def _yes(v) -> bool:
    return (v or "oui").strip().lower() in ("oui", "1", "true", "vrai", "yes")


__all__ = ["router", "siemens_node"]
