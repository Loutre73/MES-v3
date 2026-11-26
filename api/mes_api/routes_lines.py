"""Vues de l'interface : détail d'une ligne (frise du poste, production par heure, moteurs) et séries horaires usine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from . import db
from .auth import User, current_user

router = APIRouter(prefix="/api", tags=["interface"])

SLOW_RATIO = 0.7  # débit sous 70 % de la cadence nominale, ligne en marche : ralenti


def slow_intervals(buckets: list[tuple[datetime, float | None]], step: timedelta, nominal: float | None,
                   stops: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Plages de ralenti : tranches où le débit moyen est positif mais sous SLOW_RATIO x cadence, hors arrêts."""
    if not nominal:
        return []
    out: list[list[datetime]] = []
    for t, v in buckets:
        if v is None or v <= 0 or v >= SLOW_RATIO * nominal:
            continue
        end = t + step
        if any(s <= t and end <= e for s, e in stops):
            continue
        if out and out[-1][1] >= t:
            out[-1][1] = end
        else:
            out.append([t, end])
    return [(a, b) for a, b in out]


async def _tag(conn, machine_id: int, role: str) -> int | None:
    return await conn.fetchval("SELECT id FROM catalog.tag WHERE machine_id = $1 AND role = $2 AND enabled", machine_id, role)


async def _last(conn, tag_id: int | None):
    if tag_id is None:
        return None
    return await conn.fetchval("SELECT value FROM historian.value WHERE tag_id = $1 ORDER BY time DESC LIMIT 1", tag_id)


@router.get("/lignes/{lid}")
async def line_detail(lid: int, shift_id: int | None = None, _: User = Depends(current_user)):
    now = datetime.now(timezone.utc)
    async with db.pool().acquire() as conn:
        line = await conn.fetchrow(
            """SELECT m.id, m.name, m.area_id, m.attributes FROM catalog.machine m JOIN catalog.machine_type t ON t.id = m.type_id
               WHERE m.id = $1 AND (t.definition->>'track_stops')::boolean""", lid)
        if line is None:
            raise HTTPException(404, "Ligne introuvable")
        nominal = (line["attributes"] or {}).get("cadence_nominale_th")
        nominal = float(nominal) if nominal else None
        shift_sql = """SELECT s.id, s.starts_at, s.ends_at, s.team, s.target_t, s.comment, s.kind, s.recipe_id,
                              t.label, t.code AS template, r.code AS recipe_code, r.label AS recipe_label,
                              k.tonnage_t, k.availability, k.performance, k.quality, k.oee, k.energy_kwh, k.kwh_per_t,
                              k.stop_count, k.unqualified_count, k.unplanned_stop_s, k.planned_stop_s, k.run_s
                       FROM mes.shift s LEFT JOIN mes.shift_template t ON t.id = s.template_id
                       LEFT JOIN mes.shift_kpi k ON k.shift_id = s.id LEFT JOIN mes.recipe r ON r.id = s.recipe_id
                       WHERE s.line_id = $1 AND """
        if shift_id:
            shift = await conn.fetchrow(shift_sql + "s.id = $2", lid, shift_id)
        else:
            shift = await conn.fetchrow(shift_sql + "s.starts_at <= $2 AND s.ends_at > $2", lid, now)
        if shift is None:
            shift = await conn.fetchrow(shift_sql + "s.starts_at <= $2 ORDER BY s.starts_at DESC LIMIT 1", lid, now)
        nav = {}
        if shift is not None:
            nav["precedent"] = await conn.fetchval(
                "SELECT id FROM mes.shift WHERE line_id = $1 AND starts_at < $2 ORDER BY starts_at DESC LIMIT 1", lid, shift["starts_at"])
            nav["suivant"] = await conn.fetchval(
                "SELECT id FROM mes.shift WHERE line_id = $1 AND starts_at > $2 AND starts_at <= $3 ORDER BY starts_at LIMIT 1",
                lid, shift["starts_at"], now)
        run_tag, rate_tag = await _tag(conn, lid, "etat_marche"), await _tag(conn, lid, "debit")
        result = {
            "ligne": {"id": line["id"], "name": line["name"], "cadence_nominale_th": nominal,
                      "en_marche": await _last(conn, run_tag), "debit": await _last(conn, rate_tag)},
            "shift": dict(shift) if shift else None, "navigation": nav,
            "arrets": [], "ralentis": [], "debit": [], "production_h": [], "moteurs": [], "alarmes": [],
        }
        if shift is not None:
            start, end = shift["starts_at"], shift["ends_at"]
            stops = db.rows(await conn.fetch(
                """SELECT e.id, e.starts_at, e.ends_at, extract(epoch FROM coalesce(e.ends_at, now()) - e.starts_at) AS duree_s,
                          e.plc_code, e.planned, e.cause_id, c.label AS cause, c.category, e.comment, e.qualified_by
                   FROM mes.stop_event e LEFT JOIN mes.stop_cause c ON c.id = e.cause_id
                   WHERE e.machine_id = $1 AND coalesce(e.ends_at, now()) > $2 AND e.starts_at < $3 ORDER BY e.starts_at""",
                lid, start, end))
            result["arrets"] = stops
            if rate_tag is not None:
                five = await conn.fetch(
                    """SELECT time_bucket('5 minutes', bucket) AS t, avg(avg) AS v FROM historian.value_1min
                       WHERE tag_id = $1 AND bucket >= $2 AND bucket < $3 GROUP BY 1 ORDER BY 1""", rate_tag, start, min(end, now))
                result["debit"] = [{"t": r["t"], "v": r["v"]} for r in five]
                spans = [(s["starts_at"], s["ends_at"] or now) for s in stops]
                result["ralentis"] = [{"starts_at": a, "ends_at": b} for a, b in
                                      slow_intervals([(r["t"], r["v"]) for r in five], timedelta(minutes=5), nominal, spans)]
                hours = await conn.fetch(
                    """SELECT time_bucket('1 hour', bucket, $2::timestamptz) AS h, avg(avg) AS t FROM historian.value_1min
                       WHERE tag_id = $1 AND bucket >= $2 AND bucket < $3 GROUP BY 1 ORDER BY 1""", rate_tag, start, min(end, now))
                got = {r["h"]: r["t"] for r in hours}
                n_hours = max(1, round((end - start).total_seconds() / 3600))
                per_hour = (shift["target_t"] / n_hours) if shift["target_t"] else nominal
                h = start
                while h < end:
                    result["production_h"].append({"h": h, "t": got.get(h) if h < now else None, "objectif": per_hour})
                    h += timedelta(hours=1)
        # moteurs rattachés à la ligne (attribut « ligne »), à défaut ceux de la même zone
        result["moteurs"] = db.rows(await conn.fetch(
            """SELECT m.id, m.name, d.mean_current, d.ref_current, d.deviation_pct, d.status,
                      (SELECT g.id FROM catalog.tag g WHERE g.machine_id = m.id AND g.role = 'courant_moteur') AS tag_courant
               FROM catalog.machine m JOIN catalog.machine_type t ON t.id = m.type_id
               LEFT JOIN LATERAL (SELECT * FROM mes.drift_snapshot s WHERE s.machine_id = m.id ORDER BY s.time DESC LIMIT 1) d ON true
               WHERE m.enabled AND t.definition->'members' @> '[{"role": "courant_moteur"}]'
                 AND (m.attributes->>'ligne' = $1 OR (m.attributes->>'ligne' IS NULL AND $2::int IS NOT NULL AND m.area_id = $2))
               ORDER BY abs(coalesce(d.deviation_pct, 0)) DESC, m.name LIMIT 12""", line["name"], line["area_id"]))
        result["alarmes"] = db.rows(await conn.fetch(
            """SELECT e.id, e.raised_at, e.cleared_at, e.ack_at, d.priority, d.message, m.name AS machine
               FROM mes.alarm_event e JOIN mes.alarm_def d ON d.id = e.def_id JOIN catalog.tag g ON g.id = d.tag_id
               JOIN catalog.machine m ON m.id = g.machine_id
               WHERE (e.cleared_at IS NULL OR e.ack_at IS NULL)
                 AND (m.id = $1 OR m.attributes->>'ligne' = $2) ORDER BY e.raised_at DESC LIMIT 20""", lid, line["name"]))
    return result


@router.get("/usine/debit")
async def plant_rate(heures: int = 8, _: User = Depends(current_user)):
    """Débit (t/h) de chaque ligne, moyenne par 5 minutes, sur les dernières heures."""
    heures = max(1, min(heures, 48))
    async with db.pool().acquire() as conn:
        return db.rows(await conn.fetch(
            """SELECT time_bucket('5 minutes', v.bucket) AS t, m.id AS line_id, avg(v.avg) AS v
               FROM historian.value_1min v JOIN catalog.tag g ON g.id = v.tag_id AND g.role = 'debit'
               JOIN catalog.machine m ON m.id = g.machine_id JOIN catalog.machine_type ty ON ty.id = m.type_id
               WHERE (ty.definition->>'track_stops')::boolean AND m.enabled AND v.bucket > now() - make_interval(hours => $1)
               GROUP BY 1, 2 ORDER BY 2, 1""", heures))


@router.get("/usine/horaire")
async def plant_hourly(heures: int = 24, _: User = Depends(current_user)):
    """Production (t) par ligne et énergie (kWh) de l'usine, heure par heure, sur les dernières heures."""
    heures = max(1, min(heures, 24 * 7))
    async with db.pool().acquire() as conn:
        production = db.rows(await conn.fetch(
            """SELECT time_bucket('1 hour', v.bucket) AS h, m.id AS line_id, m.name AS line, avg(v.avg) AS t
               FROM historian.value_1min v JOIN catalog.tag g ON g.id = v.tag_id AND g.role = 'debit'
               JOIN catalog.machine m ON m.id = g.machine_id JOIN catalog.machine_type ty ON ty.id = m.type_id
               WHERE (ty.definition->>'track_stops')::boolean AND m.enabled
                 AND v.bucket > date_trunc('hour', now()) - make_interval(hours => $1 - 1)
               GROUP BY 1, 2, 3 ORDER BY 1, 3""", heures))
        energie = db.rows(await conn.fetch(
            """SELECT h, sum(kw) AS kwh FROM (
                 SELECT time_bucket('1 hour', v.bucket) AS h, v.tag_id, avg(v.avg) AS kw
                 FROM historian.value_1min v JOIN catalog.tag g ON g.id = v.tag_id AND g.role = 'puissance' AND g.enabled
                 WHERE v.bucket > date_trunc('hour', now()) - make_interval(hours => $1 - 1) GROUP BY 1, 2) x
               GROUP BY h ORDER BY h""", heures))
    return {"production": production, "energie": energie}
