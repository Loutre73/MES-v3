"""Exploitation : tableau de bord, arrêts, shifts, alarmes, moteurs, énergie, paramètres, utilisateurs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import db, workers
from .auth import ROLES, User, current_user, hash_password, require

router = APIRouter(prefix="/api", tags=["exploitation"])


# ----------------------------------------------------------------------------- tableau de bord
@router.get("/tableau-de-bord")
async def dashboard(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        lines = db.rows(await conn.fetch(
            """SELECT m.id, m.name,
                      (SELECT v.value FROM historian.value v JOIN catalog.tag g ON g.id = v.tag_id
                       WHERE g.machine_id = m.id AND g.role = 'etat_marche' ORDER BY v.time DESC LIMIT 1) AS en_marche,
                      (SELECT v.value FROM historian.value v JOIN catalog.tag g ON g.id = v.tag_id
                       WHERE g.machine_id = m.id AND g.role = 'debit' ORDER BY v.time DESC LIMIT 1) AS debit,
                      (m.attributes->>'cadence_nominale_th')::float AS cadence_nominale_th,
                      s.id AS shift_id, s.starts_at, s.ends_at, s.team, t.label AS shift_label, s.kind, r.code AS recipe_code, r.label AS recipe_label,
                      k.tonnage_t, coalesce(k.target_t, s.target_t) AS target_t, k.availability, k.performance, k.oee, k.energy_kwh, k.kwh_per_t,
                      k.stop_count, k.unqualified_count, k.unplanned_stop_s, k.run_s
               FROM catalog.machine m JOIN catalog.machine_type ty ON ty.id = m.type_id
               LEFT JOIN mes.shift s ON s.line_id = m.id AND s.starts_at <= now() AND s.ends_at > now()
               LEFT JOIN mes.shift_template t ON t.id = s.template_id
               LEFT JOIN mes.shift_kpi k ON k.shift_id = s.id LEFT JOIN mes.recipe r ON r.id = s.recipe_id
               WHERE m.enabled AND (ty.definition->>'track_stops')::boolean ORDER BY m.name"""))
        counts = dict(await conn.fetchrow(
            """SELECT (SELECT count(*) FROM mes.alarm_event WHERE cleared_at IS NULL) AS alarmes_actives,
                      (SELECT count(*) FROM mes.alarm_event WHERE ack_at IS NULL) AS alarmes_non_acquittees,
                      (SELECT count(*) FROM mes.stop_event WHERE cause_id IS NULL AND NOT planned AND ends_at IS NOT NULL) AS arrets_a_qualifier,
                      (SELECT count(*) FROM (SELECT DISTINCT ON (machine_id) status FROM mes.drift_snapshot
                         WHERE time > now() - interval '1 hour' ORDER BY machine_id, time DESC) d WHERE status = 'alerte') AS moteurs_en_alerte,
                      (SELECT extract(epoch FROM now() - max(time)) FROM historian.value WHERE time > now() - interval '1 hour') AS retard_collecte_s"""))
    return {"lignes": lines, **counts}


# ----------------------------------------------------------------------------- arrêts
@router.get("/arrets/causes")
async def causes(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch("SELECT * FROM mes.stop_cause WHERE active ORDER BY category, label"))


class CauseBody(BaseModel):
    code: str
    label: str
    category: str
    parent_id: int | None = None
    active: bool = True


@router.post("/arrets/causes")
async def add_cause(body: CauseBody, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn:
        cid = await conn.fetchval(
            """INSERT INTO mes.stop_cause (code, label, category, parent_id, active) VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (code) DO UPDATE SET label = $2, category = $3, parent_id = $4, active = $5 RETURNING id""",
            body.code, body.label, body.category, body.parent_id, body.active)
        await db.audit(conn, user.username, "cause_arret", "stop_cause", cid, body.model_dump())
    return {"id": cid}


@router.get("/arrets")
async def list_stops(line_id: int | None = None, a_qualifier: bool = False, jours: int = 7, _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT e.id, e.machine_id, m.name AS machine, e.starts_at, e.ends_at,
                      extract(epoch FROM coalesce(e.ends_at, now()) - e.starts_at) AS duree_s,
                      e.plc_code, e.planned, e.cause_id, c.label AS cause, c.category, e.comment, e.qualified_by, e.qualified_at
               FROM mes.stop_event e JOIN catalog.machine m ON m.id = e.machine_id LEFT JOIN mes.stop_cause c ON c.id = e.cause_id
               WHERE e.starts_at > now() - make_interval(days => $1) AND ($2::int IS NULL OR e.machine_id = $2)
                 AND (NOT $3 OR (e.cause_id IS NULL AND NOT e.planned))
               ORDER BY e.starts_at DESC LIMIT 500""", jours, line_id, a_qualifier))


class Qualify(BaseModel):
    cause_id: int
    comment: str | None = None


@router.post("/arrets/{sid}/qualifier")
async def qualify(sid: int, body: Qualify, user: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        r = await conn.execute(
            "UPDATE mes.stop_event SET cause_id = $2, comment = $3, qualified_by = $4, qualified_at = now() WHERE id = $1",
            sid, body.cause_id, body.comment, user.username)
        if r.endswith(" 0"):
            raise HTTPException(404, "Arrêt introuvable")
        await db.audit(conn, user.username, "arret_qualifie", "stop_event", sid, body.model_dump())
    return {"ok": True}


class Split(BaseModel):
    at: datetime


@router.post("/arrets/{sid}/decouper")
async def split_stop(sid: int, body: Split, user: User = Depends(require("chef_equipe", "operateur"))):
    """Découpe un arrêt en deux (deux causes successives)."""
    async with db.pool().acquire() as conn, conn.transaction():
        e = await conn.fetchrow("SELECT * FROM mes.stop_event WHERE id = $1 FOR UPDATE", sid)
        if e is None:
            raise HTTPException(404, "Arrêt introuvable")
        end = e["ends_at"] or datetime.now(timezone.utc)
        if not (e["starts_at"] < body.at < end):
            raise HTTPException(400, "L'instant de découpe doit être pendant l'arrêt")
        await conn.execute("UPDATE mes.stop_event SET ends_at = $2 WHERE id = $1", sid, body.at)
        new_id = await conn.fetchval(
            """INSERT INTO mes.stop_event (machine_id, starts_at, ends_at, plc_code, planned, split_from)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            e["machine_id"], body.at, e["ends_at"], e["plc_code"], e["planned"], sid)
        await db.audit(conn, user.username, "arret_decoupe", "stop_event", sid, {"nouveau": new_id, "a": body.at.isoformat()})
    return {"id": new_id}


@router.get("/arrets/pareto")
async def pareto(jours: int = 7, line_id: int | None = None, depuis: datetime | None = None, jusqua: datetime | None = None,
                 _: User = Depends(current_user)):
    """Durée cumulée des arrêts non planifiés par cause : sur les `jours` derniers jours, ou de `depuis` à `jusqua`."""
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT coalesce(c.label, 'Non qualifié') AS cause, coalesce(c.category, 'non_qualifie') AS category,
                      count(*) AS nombre, sum(extract(epoch FROM coalesce(e.ends_at, now()) - e.starts_at)) / 60 AS minutes
               FROM mes.stop_event e LEFT JOIN mes.stop_cause c ON c.id = e.cause_id
               WHERE NOT e.planned AND e.starts_at > coalesce($3::timestamptz, now() - make_interval(days => $1))
                 AND ($4::timestamptz IS NULL OR e.starts_at < $4) AND ($2::int IS NULL OR e.machine_id = $2)
               GROUP BY 1, 2 ORDER BY minutes DESC""", jours, line_id, depuis, jusqua))


# ----------------------------------------------------------------------------- shifts
@router.get("/shifts")
async def list_shifts(line_id: int | None = None, depuis: datetime | None = None, jours: int = 3, _: User = Depends(current_user)):
    start = depuis or (datetime.now(timezone.utc) - timedelta(days=1))
    jours = max(1, min(jours, 62))
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT s.id, s.line_id, m.name AS line, s.starts_at, s.ends_at, s.team, s.target_t, s.comment, t.label, t.code AS template,
                      s.kind, s.recipe_id, r.code AS recipe_code, r.label AS recipe_label, k.tonnage_t, k.availability, k.performance, k.oee, k.energy_kwh, k.kwh_per_t, k.final,
                      k.stop_count, k.unqualified_count, k.planned_stop_s, k.unplanned_stop_s, k.run_s,
                      coalesce((SELECT json_agg(json_build_object('id', p.id, 'type', p.type, 'starts_at', p.starts_at, 'ends_at', p.ends_at,
                                                'reason', p.reason) ORDER BY p.starts_at)
                                FROM mes.planned_stop p WHERE p.shift_id = s.id), '[]') AS stops
               FROM mes.shift s JOIN catalog.machine m ON m.id = s.line_id LEFT JOIN mes.shift_template t ON t.id = s.template_id
               LEFT JOIN mes.shift_kpi k ON k.shift_id = s.id LEFT JOIN mes.recipe r ON r.id = s.recipe_id
               WHERE s.starts_at >= $1 AND s.starts_at < $1 + make_interval(days => $2) AND ($3::int IS NULL OR s.line_id = $3)
               ORDER BY s.starts_at, m.name""", start, jours, line_id))


PLAN_FIELDS = ("team", "target_t", "comment", "recipe_id")


class ShiftPatch(BaseModel):
    """Champs à modifier ; un champ envoyé à null est effacé, un champ absent est conservé."""
    team: str | None = None
    target_t: float | None = None
    comment: str | None = None
    recipe_id: int | None = None


async def _recompute(conn, sid: int) -> None:
    s = await conn.fetchrow("SELECT * FROM mes.shift WHERE id = $1", sid)
    if s and s["starts_at"] <= datetime.now(timezone.utc):
        await workers.compute_shift(conn, s)


@router.patch("/shifts/{sid}")
async def patch_shift(sid: int, body: ShiftPatch, user: User = Depends(require("chef_equipe"))):
    changes = body.model_dump(exclude_unset=True)
    async with db.pool().acquire() as conn:
        s = await conn.fetchrow("SELECT starts_at, ends_at FROM mes.shift WHERE id = $1", sid)
        if s is None:
            raise HTTPException(404, "Poste introuvable")
        # la recette d'un poste terminé n'est plus modifiable ; l'équipe, l'objectif
        # et le commentaire restent corrigeables pour le calcul des KPI
        if s["ends_at"] <= datetime.now(timezone.utc) and "recipe_id" in changes:
            raise HTTPException(409, "Poste terminé : sa recette n'est plus modifiable")
        if changes.get("recipe_id") and not await conn.fetchval(
                "SELECT 1 FROM mes.recipe WHERE id = $1 AND deleted_at IS NULL", changes["recipe_id"]):
            raise HTTPException(400, "Recette introuvable")
        if changes:
            cols = [c for c in PLAN_FIELDS if c in changes]
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            await conn.execute(f"UPDATE mes.shift SET {sets} WHERE id = $1", sid, *[changes[c] for c in cols])
            await _recompute(conn, sid)
        await db.audit(conn, user.username, "shift_modifie", "shift", sid, changes)
    return {"ok": True}


@router.get("/arrets-planifies")
async def list_planned(line_id: int | None = None, _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT p.*, m.name AS line FROM mes.planned_stop p JOIN catalog.machine m ON m.id = p.line_id
               WHERE p.ends_at > now() - interval '2 days' AND ($1::int IS NULL OR p.line_id = $1) ORDER BY p.starts_at""", line_id))


class PlannedBody(BaseModel):
    line_id: int
    starts_at: datetime
    ends_at: datetime
    reason: str


@router.post("/arrets-planifies")
async def add_planned(body: PlannedBody, user: User = Depends(require("chef_equipe"))):
    if body.ends_at <= body.starts_at:
        raise HTTPException(400, "La fin doit être après le début")
    async with db.pool().acquire() as conn:
        pid = await conn.fetchval(
            "INSERT INTO mes.planned_stop (line_id, starts_at, ends_at, reason, created_by) VALUES ($1, $2, $3, $4, $5) RETURNING id",
            body.line_id, body.starts_at, body.ends_at, body.reason, user.username)
        # les arrêts déjà détectés pendant cette plage deviennent planifiés
        await conn.execute(
            "UPDATE mes.stop_event SET planned = true WHERE machine_id = $1 AND starts_at >= $2 AND starts_at < $3",
            body.line_id, body.starts_at, body.ends_at)
        await db.audit(conn, user.username, "arret_planifie", "planned_stop", pid, body.model_dump(mode="json"))
    return {"id": pid}


@router.delete("/arrets-planifies/{pid}")
async def delete_planned(pid: int, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn:
        await conn.execute("DELETE FROM mes.planned_stop WHERE id = $1", pid)
        await db.audit(conn, user.username, "arret_planifie_supprime", "planned_stop", pid)
    return {"ok": True}


# ----------------------------------------------------------------------------- alarmes
@router.get("/alarmes")
async def list_alarms(actives: bool = False, jours: int = 7, q: str = "", _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT e.id, e.raised_at, e.cleared_at, e.ack_at, e.ack_by, e.value, d.priority, d.message, m.name AS machine
               FROM mes.alarm_event e JOIN mes.alarm_def d ON d.id = e.def_id JOIN catalog.tag g ON g.id = d.tag_id
               LEFT JOIN catalog.machine m ON m.id = g.machine_id
               WHERE (NOT $1 OR e.cleared_at IS NULL OR e.ack_at IS NULL) AND e.raised_at > now() - make_interval(days => $2)
                 AND ($3 = '' OR d.message ILIKE '%' || $3 || '%')
               ORDER BY (e.cleared_at IS NULL) DESC, e.raised_at DESC LIMIT 1000""", actives, jours, q))


@router.post("/alarmes/{aid}/acquitter")
async def ack_alarm(aid: int, user: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        await conn.execute("UPDATE mes.alarm_event SET ack_at = now(), ack_by = $2 WHERE id = $1 AND ack_at IS NULL", aid, user.username)
    return {"ok": True}


@router.post("/alarmes/acquitter-tout")
async def ack_all(user: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        r = await conn.execute("UPDATE mes.alarm_event SET ack_at = now(), ack_by = $1 WHERE ack_at IS NULL", user.username)
        await db.audit(conn, user.username, "alarmes_acquittees", details={"nombre": int(r.split()[-1])})
    return {"ok": True}


@router.get("/alarmes/definitions")
async def alarm_defs(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT d.*, g.name AS tag FROM mes.alarm_def d JOIN catalog.tag g ON g.id = d.tag_id ORDER BY d.message"""))


class AlarmDefBody(BaseModel):
    tag_id: int
    condition: str
    threshold: float | None = None
    priority: str = "moyenne"
    message: str
    active: bool = True


@router.post("/alarmes/definitions")
async def add_alarm_def(body: AlarmDefBody, user: User = Depends(require("maintenance"))):
    async with db.pool().acquire() as conn:
        did = await conn.fetchval(
            """INSERT INTO mes.alarm_def (tag_id, condition, threshold, priority, message, active) VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (tag_id, condition, threshold) DO UPDATE SET priority = $4, message = $5, active = $6 RETURNING id""",
            body.tag_id, body.condition, body.threshold, body.priority, body.message, body.active)
        await db.audit(conn, user.username, "alarme_definie", "alarm_def", did, body.model_dump())
    return {"id": did}


# ----------------------------------------------------------------------------- moteurs
@router.get("/moteurs")
async def motors(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT m.id, m.name, m.attributes, d.time, d.mean_current, d.ref_current, d.deviation_pct, d.status, r.method, r.set_at,
                      (SELECT g.id FROM catalog.tag g WHERE g.machine_id = m.id AND g.role = 'courant_moteur') AS tag_courant
               FROM catalog.machine m JOIN catalog.machine_type t ON t.id = m.type_id
               LEFT JOIN LATERAL (SELECT * FROM mes.drift_snapshot s WHERE s.machine_id = m.id ORDER BY s.time DESC LIMIT 1) d ON true
               LEFT JOIN mes.motor_reference r ON r.machine_id = m.id
               WHERE m.enabled AND t.definition->'members' @> '[{"role": "courant_moteur"}]' ORDER BY m.name"""))


@router.get("/moteurs/{mid}/historique")
async def motor_history(mid: int, jours: int = 7, _: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        snaps = db.rows(await conn.fetch(
            "SELECT time, mean_current, ref_current, deviation_pct, status FROM mes.drift_snapshot WHERE machine_id = $1 AND time > now() - make_interval(days => $2) ORDER BY time",
            mid, jours))
    return snaps


class RefBody(BaseModel):
    method: str  # nominal | mesure
    value: float | None = None


@router.post("/moteurs/{mid}/reference")
async def set_reference(mid: int, body: RefBody, user: User = Depends(require("maintenance"))):
    """Recale la référence : valeur nominale saisie, ou courant moyen mesuré sur la fenêtre de calcul (après maintenance)."""
    async with db.pool().acquire() as conn:
        if body.method == "mesure":
            window = int(await db.get_setting(conn, "derive_fenetre_min", 15))
            value = await conn.fetchval(
                """SELECT avg(v.value) FROM historian.value v JOIN catalog.tag g ON g.id = v.tag_id
                   WHERE g.machine_id = $1 AND g.role = 'courant_moteur' AND v.time > now() - make_interval(mins => $2) AND v.value > 0""",
                mid, window)
            if value is None:
                raise HTTPException(400, "Aucune mesure récente en marche pour ce moteur")
        elif body.method == "nominal" and body.value:
            value = body.value
        else:
            raise HTTPException(400, "Méthode nominal (avec valeur) ou mesure")
        await conn.execute(
            """INSERT INTO mes.motor_reference (machine_id, ref_current, method, set_by) VALUES ($1, $2, $3, $4)
               ON CONFLICT (machine_id) DO UPDATE SET ref_current = $2, method = $3, set_at = now(), set_by = $4""",
            mid, value, body.method, user.username)
        await db.audit(conn, user.username, "reference_moteur", "machine", mid, {"methode": body.method, "valeur": value})
        await workers.compute_drifts(conn)
    return {"ref_current": value}


# ----------------------------------------------------------------------------- énergie et performance
@router.get("/energie")
async def energy(jours: int = 7, depuis: datetime | None = None, jusqua: datetime | None = None, _: User = Depends(current_user)):
    """Énergie des postes commencés sur les `jours` derniers jours, ou de `depuis` à `jusqua`."""
    async with db.pool().acquire() as conn:
        par_shift = db.rows(await conn.fetch(
            """SELECT s.starts_at, m.name AS line, t.label, k.energy_kwh, k.tonnage_t, k.kwh_per_t
               FROM mes.shift_kpi k JOIN mes.shift s ON s.id = k.shift_id JOIN catalog.machine m ON m.id = s.line_id
               LEFT JOIN mes.shift_template t ON t.id = s.template_id
               WHERE s.starts_at > coalesce($2::timestamptz, now() - make_interval(days => $1)) AND ($3::timestamptz IS NULL OR s.starts_at < $3)
               ORDER BY s.starts_at, m.name""", jours, depuis, jusqua))
        par_compteur = db.rows(await conn.fetch(
            """SELECT c.name AS compteur, c.attributes->>'ligne' AS line, c.attributes->>'usage' AS usage, sum(e.kwh) AS kwh
               FROM mes.energy_shift e JOIN mes.shift s ON s.id = e.shift_id JOIN catalog.machine c ON c.id = e.meter_id
               WHERE s.starts_at > coalesce($2::timestamptz, now() - make_interval(days => $1)) AND ($3::timestamptz IS NULL OR s.starts_at < $3)
               GROUP BY 1, 2, 3 ORDER BY kwh DESC""", jours, depuis, jusqua))
        puissance = await conn.fetchval(
            """SELECT sum(last) FROM (SELECT DISTINCT ON (g.id) v.value AS last FROM catalog.tag g JOIN historian.value v ON v.tag_id = g.id
               WHERE g.role = 'puissance' AND g.enabled AND v.time > now() - interval '15 minutes' ORDER BY g.id, v.time DESC) x""")
    return {"par_shift": par_shift, "par_compteur": par_compteur, "puissance_kw": puissance}


# ----------------------------------------------------------------------------- tendances (pour les courbes de l'application)
@router.get("/tendance/{tag_id}")
async def trend(tag_id: int, minutes: int = 60, _: User = Depends(current_user)):
    minutes = max(1, min(minutes, 60 * 24 * 31))
    async with db.pool().acquire() as conn:
        if minutes <= 360:
            data = await conn.fetch(
                "SELECT time, value FROM historian.value WHERE tag_id = $1 AND time > now() - make_interval(mins => $2) ORDER BY time",
                tag_id, minutes)
        else:
            data = await conn.fetch(
                "SELECT bucket AS time, avg AS value FROM historian.value_1min WHERE tag_id = $1 AND bucket > now() - make_interval(mins => $2) ORDER BY bucket",
                tag_id, minutes)
        before = await conn.fetchval("SELECT historian.value_at($1, now() - make_interval(mins => $2))", tag_id, minutes)
    return {"avant": before, "points": [[r["time"].isoformat(), r["value"]] for r in data]}


# ----------------------------------------------------------------------------- paramètres et utilisateurs
@router.get("/parametres")
async def get_settings(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return {r["key"]: r["value"] for r in await conn.fetch("SELECT key, value FROM mes.setting WHERE key NOT LIKE 'planning\\_%' ORDER BY key")}  # planning : réglé depuis la vue Planning


@router.put("/parametres/{key}")
async def put_setting(key: str, value: dict, user: User = Depends(require("maintenance"))):
    async with db.pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM mes.setting WHERE key = $1", key):
            raise HTTPException(404, "Paramètre inconnu")
        if user.role not in ("admin",) and not key.startswith("derive_"):
            raise HTTPException(403, "Droits insuffisants pour ce paramètre")
        await conn.execute("UPDATE mes.setting SET value = $2 WHERE key = $1", key, value["value"])
        await db.audit(conn, user.username, "parametre", "setting", key, value)
    return {"ok": True}


@router.get("/utilisateurs")
async def users(_: User = Depends(require())):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch("SELECT username, display_name, role, source, active, last_login FROM mes.app_user ORDER BY username"))


class UserBody(BaseModel):
    username: str
    display_name: str | None = None
    role: str
    password: str | None = None
    active: bool = True


@router.post("/utilisateurs")
async def upsert_user(body: UserBody, user: User = Depends(require())):
    if body.role not in ROLES:
        raise HTTPException(400, "Rôle inconnu")
    async with db.pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO mes.app_user (username, display_name, role, password_hash, active) VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (username) DO UPDATE SET display_name = coalesce($2, mes.app_user.display_name), role = $3,
                  password_hash = coalesce($4, mes.app_user.password_hash), active = $5""",
            body.username, body.display_name, body.role, hash_password(body.password) if body.password else None, body.active)
        await db.audit(conn, user.username, "utilisateur", "app_user", body.username, {"role": body.role, "actif": body.active})
    return {"ok": True}


@router.get("/journal")
async def journal(limit: int = 200, _: User = Depends(require())):
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch("SELECT * FROM mes.audit_log ORDER BY at DESC LIMIT $1", min(limit, 2000)))
