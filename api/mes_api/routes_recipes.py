"""Recettes : création, modification, duplication, versions, suppression, recherche, comparaison et chargement dans l'automate."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import db, opc
from .auth import User, current_user, require
from .typedefs import siemens_node

router = APIRouter(prefix="/api/recettes", tags=["recettes"])
log = logging.getLogger("recettes")


async def _type(conn, type_id: int) -> dict:
    t = await conn.fetchrow("SELECT * FROM mes.recipe_type WHERE id = $1", type_id)
    if t is None:
        raise HTTPException(404, "Type de recette introuvable")
    return dict(t)


async def _recipe(conn, rid: int, lock: bool = False) -> dict:
    r = await conn.fetchrow("SELECT * FROM mes.recipe WHERE id = $1 AND deleted_at IS NULL" + (" FOR UPDATE" if lock else ""), rid)
    if r is None:
        raise HTTPException(404, "Recette introuvable")
    return dict(r)


async def _code_free(conn, type_id: int, code: str, exclude: int | None = None) -> None:
    if not code.strip():
        raise HTTPException(400, "Le code est obligatoire")
    if await conn.fetchval("SELECT 1 FROM mes.recipe WHERE type_id = $1 AND code = $2 AND deleted_at IS NULL AND id IS DISTINCT FROM $3",
                           type_id, code.strip(), exclude):
        raise HTTPException(409, "Ce code de recette existe déjà")


async def usages(conn, rid: int) -> dict:
    """Où sert la recette : postes en cours ou à venir (une seule recette par poste), postes passés, chargements."""
    shifts = db.rows(await conn.fetch(
        """SELECT s.id, s.starts_at, s.ends_at, s.team, m.name AS line, s.starts_at <= now() AS en_cours
           FROM mes.shift s JOIN catalog.machine m ON m.id = s.line_id
           WHERE s.recipe_id = $1 AND s.ends_at > now() ORDER BY s.starts_at""", rid))
    past = await conn.fetchval("SELECT count(*) FROM mes.shift WHERE recipe_id = $1 AND ends_at <= now()", rid)
    loads = await conn.fetchval(
        "SELECT count(*) FROM mes.recipe_load l JOIN mes.recipe_version v ON v.id = l.version_id WHERE v.recipe_id = $1", rid)
    return {"postes": shifts, "postes_passes": past, "chargements": loads}


def _validate(tdef: dict, values: dict) -> dict:
    out, errors = {}, []
    for p in tdef["params"]:
        v = values.get(p["name"], p["default"])
        try:
            v = float(v)
        except (TypeError, ValueError):
            errors.append(f"{p['label']} : valeur numérique attendue")
            continue
        if p["datatype"] in ("Int16", "Int32", "UInt16", "UInt32", "Int64", "Byte"):
            v = int(round(v))
        if p.get("min") is not None and v < p["min"] or p.get("max") is not None and v > p["max"]:
            errors.append(f"{p['label']} : hors bornes [{p.get('min')} ; {p.get('max')}]")
        out[p["name"]] = v
    if errors:
        raise HTTPException(400, " ; ".join(errors))
    return out


@router.get("/types")
async def types(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch("SELECT id, code, label, definition FROM mes.recipe_type ORDER BY label"))


@router.get("")
async def search(q: str = "", type_id: int | None = None, statut: str | None = None, archivees: bool = False,
                 param: str | None = None, op: str = ">", valeur: float | None = None, _: User = Depends(current_user)):
    """Recherche par code, libellé, produit, statut, ou valeur d'un paramètre de la dernière version."""
    if op not in (">", "<", "=", ">=", "<="):
        raise HTTPException(400, "Opérateur invalide")
    cond = ""
    args: list[Any] = [q, type_id, statut, archivees]
    if param and valeur is not None:
        args += [param, valeur]
        cond = f" AND (v.\"values\"->>$5)::float8 {op} $6"
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            f"""SELECT r.id, r.code, r.label, r.product, r.archived, r.type_id, t.label AS type,
                       v.id AS version_id, v.version, v.status, v.created_at, v.created_by,
                       (SELECT max(l.requested_at) FROM mes.recipe_load l JOIN mes.recipe_version vv ON vv.id = l.version_id
                        WHERE vv.recipe_id = r.id AND l.result = 'acceptee') AS dernier_chargement,
                       (SELECT count(*) FROM mes.shift s WHERE s.recipe_id = r.id AND s.ends_at > now()) AS postes_a_venir
                FROM mes.recipe r JOIN mes.recipe_type t ON t.id = r.type_id
                JOIN LATERAL (SELECT * FROM mes.recipe_version v WHERE v.recipe_id = r.id ORDER BY v.version DESC LIMIT 1) v ON true
                WHERE r.deleted_at IS NULL AND ($1 = '' OR r.code ILIKE '%' || $1 || '%' OR r.label ILIKE '%' || $1 || '%' OR r.product ILIKE '%' || $1 || '%')
                  AND ($2::int IS NULL OR r.type_id = $2) AND ($3::text IS NULL OR v.status = $3) AND ($4 OR NOT r.archived){cond}
                ORDER BY r.code""", *args))


@router.get("/{rid}")
async def get_recipe(rid: int, _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        r = await _recipe(conn, rid)
        versions = db.rows(await conn.fetch(
            """SELECT v.*, EXISTS (SELECT 1 FROM mes.recipe_load l WHERE l.version_id = v.id) AS chargee
               FROM mes.recipe_version v WHERE v.recipe_id = $1 ORDER BY v.version DESC""", rid))
        loads = db.rows(await conn.fetch(
            """SELECT l.*, v.version FROM mes.recipe_load l JOIN mes.recipe_version v ON v.id = l.version_id
               WHERE v.recipe_id = $1 ORDER BY l.requested_at DESC LIMIT 50""", rid))
        t = await _type(conn, r["type_id"])
        u = await usages(conn, rid)
    return {**r, "type": t, "versions": versions, "chargements": loads, "usages": u}


@router.get("/{rid}/usages")
async def get_usages(rid: int, _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        await _recipe(conn, rid)
        return await usages(conn, rid)


class RecipeBody(BaseModel):
    type_id: int
    code: str
    label: str
    product: str | None = None
    values: dict | None = None
    comment: str | None = None


@router.post("")
async def create(body: RecipeBody, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn, conn.transaction():
        t = await _type(conn, body.type_id)
        values = _validate(t["definition"], body.values or {})
        await _code_free(conn, body.type_id, body.code)
        if not body.label.strip():
            raise HTTPException(400, "Le libellé est obligatoire")
        rid = await conn.fetchval(
            "INSERT INTO mes.recipe (type_id, code, label, product, created_by) VALUES ($1, $2, $3, $4, $5) RETURNING id",
            body.type_id, body.code.strip(), body.label.strip(), (body.product or "").strip() or None, user.username)
        await conn.execute(
            """INSERT INTO mes.recipe_version (recipe_id, version, "values", comment, created_by) VALUES ($1, 1, $2, $3, $4)""",
            rid, values, body.comment or "Création", user.username)
        await db.audit(conn, user.username, "recette_creee", "recipe", rid, {"code": body.code})
    return {"id": rid}


class DuplicateBody(BaseModel):
    code: str
    label: str | None = None
    product: str | None = None


@router.post("/{rid}/dupliquer")
async def duplicate(rid: int, body: DuplicateBody, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn, conn.transaction():
        r = await _recipe(conn, rid)
        await _code_free(conn, r["type_id"], body.code)
        last = await conn.fetchrow("SELECT * FROM mes.recipe_version WHERE recipe_id = $1 ORDER BY version DESC LIMIT 1", rid)
        nid = await conn.fetchval(
            "INSERT INTO mes.recipe (type_id, code, label, product, created_by) VALUES ($1, $2, $3, $4, $5) RETURNING id",
            r["type_id"], body.code.strip(), body.label or f"Copie de {r['label']}", body.product or r["product"], user.username)
        await conn.execute(
            """INSERT INTO mes.recipe_version (recipe_id, version, "values", comment, created_by) VALUES ($1, 1, $2, $3, $4)""",
            nid, last["values"], f"Copie de {r['code']} v{last['version']}", user.username)
        await db.audit(conn, user.username, "recette_dupliquee", "recipe", nid, {"source": rid})
    return {"id": nid}


class VersionBody(BaseModel):
    values: dict
    comment: str | None = None
    label: str | None = None
    product: str | None = None


@router.post("/{rid}/versions")
async def new_version(rid: int, body: VersionBody, user: User = Depends(require("chef_equipe"))):
    """Toute modification crée une nouvelle version, en brouillon."""
    async with db.pool().acquire() as conn, conn.transaction():
        r = await _recipe(conn, rid, lock=True)
        t = await _type(conn, r["type_id"])
        values = _validate(t["definition"], body.values)
        n = await conn.fetchval("SELECT coalesce(max(version), 0) + 1 FROM mes.recipe_version WHERE recipe_id = $1", rid)
        vid = await conn.fetchval(
            """INSERT INTO mes.recipe_version (recipe_id, version, "values", comment, created_by) VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            rid, n, values, body.comment, user.username)
        if body.label or body.product is not None:
            await conn.execute("UPDATE mes.recipe SET label = coalesce($2, label), product = coalesce($3, product) WHERE id = $1",
                               rid, body.label, body.product)
        await db.audit(conn, user.username, "recette_modifiee", "recipe", rid, {"version": n})
    return {"id": vid, "version": n}


class DraftBody(BaseModel):
    values: dict
    comment: str | None = None


@router.put("/versions/{vid}")
async def update_draft(vid: int, body: DraftBody, user: User = Depends(require("chef_equipe"))):
    """Un brouillon jamais chargé se corrige sur place ; une version validée, elle, donne une nouvelle version."""
    async with db.pool().acquire() as conn, conn.transaction():
        v = await conn.fetchrow(
            """SELECT v.*, r.type_id FROM mes.recipe_version v JOIN mes.recipe r ON r.id = v.recipe_id
               WHERE v.id = $1 AND r.deleted_at IS NULL FOR UPDATE OF v""", vid)
        if v is None:
            raise HTTPException(404, "Version introuvable")
        if v["status"] != "brouillon" or await conn.fetchval("SELECT 1 FROM mes.recipe_load WHERE version_id = $1", vid):
            raise HTTPException(409, "Version validée ou déjà chargée : créer une nouvelle version")
        values = _validate((await _type(conn, v["type_id"]))["definition"], body.values)
        await conn.execute("""UPDATE mes.recipe_version SET "values" = $2, comment = coalesce($3, comment) WHERE id = $1""",
                           vid, values, body.comment or None)
        await db.audit(conn, user.username, "recette_modifiee", "recipe", v["recipe_id"], {"version": v["version"], "brouillon": True})
    return {"id": vid, "version": v["version"]}


@router.post("/versions/{vid}/valider")
async def validate_version(vid: int, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn:
        r = await conn.execute(
            "UPDATE mes.recipe_version SET status = 'validee', validated_at = now(), validated_by = $2 WHERE id = $1 AND status = 'brouillon'",
            vid, user.username)
        if r.endswith(" 0"):
            raise HTTPException(400, "Seule une version en brouillon peut être validée")
        await db.audit(conn, user.username, "recette_validee", "recipe_version", vid)
    return {"ok": True}


@router.post("/{rid}/archiver")
async def archive(rid: int, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn:
        await _recipe(conn, rid)
        await conn.execute("UPDATE mes.recipe SET archived = NOT archived WHERE id = $1", rid)
        await db.audit(conn, user.username, "recette_archivee", "recipe", rid)
    return {"ok": True}


class RecipePatch(BaseModel):
    code: str | None = None
    label: str | None = None
    product: str | None = None


@router.patch("/{rid}")
async def update(rid: int, body: RecipePatch, user: User = Depends(require("chef_equipe"))):
    """Code, libellé et produit : sans nouvelle version (les valeurs, elles, sont versionnées)."""
    async with db.pool().acquire() as conn, conn.transaction():
        r = await _recipe(conn, rid, lock=True)
        code = body.code.strip() if body.code is not None else r["code"]
        label = body.label.strip() if body.label is not None else r["label"]
        product = (body.product.strip() or None) if body.product is not None else r["product"]
        if code != r["code"]:
            await _code_free(conn, r["type_id"], code, exclude=rid)
        if not label:
            raise HTTPException(400, "Le libellé est obligatoire")
        await conn.execute("UPDATE mes.recipe SET code = $2, label = $3, product = $4 WHERE id = $1", rid, code, label, product)
        await db.audit(conn, user.username, "recette_renommee", "recipe", rid,
                       {"avant": {k: r[k] for k in ("code", "label", "product")}, "apres": {"code": code, "label": label, "product": product}})
    return {"ok": True}


def delete_plan(u: dict, detach: bool) -> str:
    """Décide de la suppression : 'effacer' (jamais servie), 'masquer' (historique conservé) ; lève une erreur sinon."""
    running = [s for s in u["postes"] if s["en_cours"]]
    if running:
        raise HTTPException(409, f"En production sur {running[0]['line']} : attendre la fin du poste")
    if u["postes"] and not detach:
        raise HTTPException(409, f"Prévue sur {len(u['postes'])} poste(s) à venir")
    return "effacer" if not u["postes_passes"] and not u["chargements"] else "masquer"


@router.delete("/{rid}")
async def delete(rid: int, retirer_postes: bool = False, user: User = Depends(require("chef_equipe"))):
    """Supprime une recette. Les postes à venir qui l'utilisent la perdent (retirer_postes=true exigé) ;
    une recette déjà produite ou chargée reste lisible dans l'historique, mais disparaît des listes."""
    async with db.pool().acquire() as conn, conn.transaction():
        r = await _recipe(conn, rid, lock=True)
        u = await usages(conn, rid)
        mode = delete_plan(u, retirer_postes)
        detached = len(u["postes"])
        if detached:
            await conn.execute("UPDATE mes.shift SET recipe_id = NULL WHERE recipe_id = $1 AND starts_at > now()", rid)
        if mode == "effacer":
            await conn.execute("DELETE FROM mes.recipe_version WHERE recipe_id = $1", rid)
            await conn.execute("DELETE FROM mes.recipe WHERE id = $1", rid)
        else:
            await conn.execute("UPDATE mes.recipe SET deleted_at = now(), deleted_by = $2, archived = true WHERE id = $1", rid, user.username)
        await db.audit(conn, user.username, "recette_supprimee", "recipe", rid,
                       {"code": r["code"], "mode": mode, "postes_retires": detached})
    return {"mode": mode, "postes_retires": detached}


@router.delete("/versions/{vid}")
async def delete_version(vid: int, user: User = Depends(require("chef_equipe"))):
    """Abandon d'un brouillon jamais chargé (une recette garde au moins une version)."""
    async with db.pool().acquire() as conn, conn.transaction():
        v = await conn.fetchrow(
            """SELECT v.* FROM mes.recipe_version v JOIN mes.recipe r ON r.id = v.recipe_id
               WHERE v.id = $1 AND r.deleted_at IS NULL FOR UPDATE OF v""", vid)
        if v is None:
            raise HTTPException(404, "Version introuvable")
        if v["status"] != "brouillon":
            raise HTTPException(400, "Seul un brouillon peut être supprimé")
        if await conn.fetchval("SELECT 1 FROM mes.recipe_load WHERE version_id = $1", vid):
            raise HTTPException(400, "Version déjà chargée dans l'automate")
        if await conn.fetchval("SELECT count(*) FROM mes.recipe_version WHERE recipe_id = $1", v["recipe_id"]) <= 1:
            raise HTTPException(400, "Dernière version : supprimer la recette")
        await conn.execute("DELETE FROM mes.recipe_version WHERE id = $1", vid)
        await db.audit(conn, user.username, "recette_version_supprimee", "recipe", v["recipe_id"], {"version": v["version"]})
    return {"ok": True}


# ----------------------------------------------------------------------------- comparaison
async def _live_values(tdef: dict) -> dict:
    nodes = [siemens_node(tdef["db"], tdef["active"], p["name"]) for p in tdef["params"]]
    async with opc.connect() as client:
        vals = await opc.read_values(client, nodes)
    return {p["name"]: (float(v) if v is not None else None) for p, v in zip(tdef["params"], vals)}


def compare_values(tdef: dict, a: dict, b: dict, tolerance_pct: float) -> list[dict]:
    out = []
    for p in tdef["params"]:
        va, vb = a.get(p["name"]), b.get(p["name"])
        if va is None or vb is None:
            diff = va != vb
        else:
            span = (p["max"] - p["min"]) if p.get("max") is not None and p.get("min") is not None else max(abs(va), abs(vb), 1)
            diff = abs(va - vb) > span * tolerance_pct / 100
        out.append({"param": p["name"], "label": p["label"], "unit": p.get("unit"), "a": va, "b": vb, "ecart": diff})
    return out


@router.get("/versions/{vid}/comparer")
async def compare(vid: int, avec: str = "production", _: User = Depends(current_user)):
    """Compare une version à la production en cours (valeurs actives lues dans l'automate) ou à une autre version."""
    async with db.pool().acquire() as conn:
        v = await conn.fetchrow(
            "SELECT v.*, r.type_id FROM mes.recipe_version v JOIN mes.recipe r ON r.id = v.recipe_id WHERE v.id = $1", vid)
        if v is None:
            raise HTTPException(404, "Version introuvable")
        tdef = (await _type(conn, v["type_id"]))["definition"]
        tol = float(await db.get_setting(conn, "recette_tolerance_pct", 0.5))
        if avec == "production":
            try:
                other = await _live_values(tdef)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Lecture automate impossible : {e}") from e
            label = "Production en cours"
        else:
            o = await conn.fetchrow("SELECT * FROM mes.recipe_version WHERE id = $1", int(avec))
            if o is None:
                raise HTTPException(404, "Version de comparaison introuvable")
            other, label = o["values"], f"Version {o['version']}"
    lines = compare_values(tdef, v["values"], other, tol)
    return {"reference": f"Version {v['version']}", "comparee": label, "ecarts": sum(l["ecart"] for l in lines), "lignes": lines}


# ----------------------------------------------------------------------------- chargement automate
REFUS = {1: "Machine en marche", 2: "Valeur hors bornes automate", 3: "Défaut présent", 9: "Refus automate"}


async def load_to_plc(load_id: int, tdef: dict, values: dict) -> None:
    hs = tdef["handshake"]
    req = siemens_node(tdef["db"], hs["request"])
    ans = siemens_node(tdef["db"], hs["answer"])
    code = siemens_node(tdef["db"], hs["code"])
    result, plc_code, details = "erreur", None, {}
    try:
        async with opc.connect(write=True) as client:
            # 1. valeurs dans le tampon, 2. réponse remise à zéro, 3. demande ; l'automate répond 1 (acceptée) ou 2 (refusée)
            await opc.write_values(client, [(siemens_node(tdef["db"], tdef["buffer"], p["name"]), values[p["name"]], p["datatype"])
                                            for p in tdef["params"]])
            await opc.write_values(client, [(ans, 0, "Int16")])
            await opc.write_values(client, [(req, True, "Boolean")])
            deadline = time.monotonic() + hs["timeout_s"]
            result = "delai_depasse"
            while time.monotonic() < deadline:
                await asyncio.sleep(0.3)
                a, c = await opc.read_values(client, [ans, code])
                if a in (1, 2):
                    result = "acceptee" if a == 1 else "refusee"
                    plc_code = int(c) if c is not None else None
                    details = {"motif": REFUS.get(plc_code, "Refus automate")} if a == 2 else {}
                    break
            await opc.write_values(client, [(req, False, "Boolean")])
    except Exception as e:  # noqa: BLE001
        log.exception("chargement %s en erreur", load_id)
        details = {"erreur": str(e)}
    async with db.pool().acquire() as conn:
        await conn.execute("UPDATE mes.recipe_load SET finished_at = now(), result = $2, plc_code = $3, details = $4 WHERE id = $1",
                           load_id, result, plc_code, details)


@router.post("/versions/{vid}/charger")
async def load(vid: int, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn:
        v = await conn.fetchrow(
            "SELECT v.*, r.type_id, r.archived FROM mes.recipe_version v JOIN mes.recipe r ON r.id = v.recipe_id WHERE v.id = $1", vid)
        if v is None:
            raise HTTPException(404, "Version introuvable")
        if v["status"] != "validee" or v["archived"]:
            raise HTTPException(400, "Seule une version validée d'une recette active peut être chargée")
        if await conn.fetchval("SELECT 1 FROM mes.recipe_load WHERE result = 'en_cours' AND requested_at > now() - interval '1 minute'"):
            raise HTTPException(409, "Un chargement est déjà en cours")
        tdef = (await _type(conn, v["type_id"]))["definition"]
        lid = await conn.fetchval("INSERT INTO mes.recipe_load (version_id, requested_by) VALUES ($1, $2) RETURNING id", vid, user.username)
        await db.audit(conn, user.username, "recette_chargement", "recipe_version", vid, {"chargement": lid})
    asyncio.create_task(load_to_plc(lid, tdef, v["values"]))
    return {"id": lid}


@router.get("/chargements/{lid}")
async def load_status(lid: int, _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM mes.recipe_load WHERE id = $1", lid)
    if r is None:
        raise HTTPException(404, "Chargement introuvable")
    return dict(r)
