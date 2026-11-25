"""Tâches périodiques : génération des shifts, calcul des KPI et de l'énergie, dérives moteurs, purges."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings

log = logging.getLogger("workers")
TZ = ZoneInfo(settings.timezone)


# ----------------------------------------------------------------------------- intervalles
def merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[list[datetime]] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def total(intervals) -> float:
    return sum((e - s).total_seconds() for s, e in merge(intervals))


def clip(intervals, start: datetime, end: datetime):
    return [(max(s, start), min(e, end)) for s, e in intervals if e > start and s < end]


def subtract(a, b):
    """Intervalles de a privés de ceux de b."""
    out = []
    b = merge(b)
    for s, e in merge(a):
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
        if cur < e:
            out.append((cur, e))
    return out


def compute_kpi(start: datetime, end: datetime, planned, stops, tonnage: float | None,
                nominal_rate: float | None) -> dict:
    """KPI d'un shift (TRS = disponibilité x performance x qualité)."""
    duration = (end - start).total_seconds()
    planned_c = merge(clip(planned, start, end))
    stops_c = clip(stops, start, end)
    planned_s = total(planned_c)
    unplanned_s = total(subtract(stops_c, planned_c))
    opening = max(0.0, duration - planned_s)
    run_s = max(0.0, opening - unplanned_s)
    availability = run_s / opening if opening > 0 else None
    performance = None
    if tonnage is not None and nominal_rate and run_s > 0:
        performance = min(1.0, max(0.0, tonnage / (nominal_rate * run_s / 3600)))
    quality = 1.0  # pas de mesure de non-qualité dans l'automate à ce stade
    oee = availability * performance * quality if availability is not None and performance is not None else None
    return {"duration_s": duration, "planned_stop_s": planned_s, "run_s": run_s, "unplanned_stop_s": unplanned_s,
            "availability": availability, "performance": performance, "quality": quality, "oee": oee}


# ----------------------------------------------------------------------------- shifts
async def lines(conn):
    return await conn.fetch(
        """SELECT m.id, m.name, m.attributes, m.area_id FROM catalog.machine m JOIN catalog.machine_type t ON t.id = m.type_id
           WHERE m.enabled AND (t.definition->>'track_stops')::boolean ORDER BY m.name""")


async def generate_shifts(conn) -> int:
    days = int(await db.get_setting(conn, "shifts_jours_a_l_avance", 7))
    templates = await conn.fetch("SELECT * FROM mes.shift_template WHERE active")
    today = datetime.now(TZ).date()
    n = 0
    for line in await lines(conn):
        rate = (line["attributes"] or {}).get("cadence_nominale_th")
        for d in range(-1, days + 1):
            day = today + timedelta(days=d)
            for t in templates:
                start = datetime.combine(day, t["start_time"], TZ)
                end = start + t["duration"]
                target = round(float(rate) * t["duration"].total_seconds() / 3600, 1) if rate else None
                r = await conn.execute(
                    """INSERT INTO mes.shift (line_id, template_id, starts_at, ends_at, target_t)
                       VALUES ($1, $2, $3, $4, $5) ON CONFLICT (line_id, starts_at)
                       DO UPDATE SET target_t = EXCLUDED.target_t WHERE mes.shift.target_t IS NULL AND EXCLUDED.target_t IS NOT NULL""",
                    line["id"], t["id"], start, end, target)
                n += r.endswith("1")
    return n


async def counter_delta(conn, tag_id: int, start: datetime, end: datetime) -> float | None:
    """Somme des incréments d'un compteur sur [start, end].

    Part de la dernière valeur connue avant start (ou de la première valeur de la période si la
    collecte a démarré en cours de poste) ; une remise à zéro ne donne pas d'incrément négatif.
    """
    r = await conn.fetchrow(
        """SELECT sum(greatest(d, 0)) AS total, count(*) AS n FROM (
             SELECT value - lag(value) OVER (ORDER BY time) AS d FROM (
               SELECT $2::timestamptz AS time, historian.value_at($1, $2) AS value
               UNION ALL
               SELECT time, value FROM historian.value WHERE tag_id = $1 AND time > $2 AND time <= $3 AND value IS NOT NULL
             ) x WHERE value IS NOT NULL) y""", tag_id, start, end)
    if r["n"] == 0:
        return None
    return float(r["total"] or 0.0)


async def compute_shift(conn, shift) -> None:
    now = datetime.now(timezone.utc)
    start, end = shift["starts_at"], min(shift["ends_at"], now)
    if end <= start:
        return
    line_id = shift["line_id"]
    # Période couverte par la collecte : un poste commencé avant la mise en service n'est compté
    # qu'à partir de la première donnée, et un poste sans aucune donnée n'a pas de KPI.
    run_tag = await conn.fetchval("SELECT id FROM catalog.tag WHERE machine_id = $1 AND role = 'etat_marche'", line_id)
    if run_tag is not None and await conn.fetchval("SELECT historian.value_at($1, $2)", run_tag, start) is None:
        first = await conn.fetchval(
            "SELECT time FROM historian.value WHERE tag_id = $1 AND time >= $2 AND time < $3 ORDER BY time LIMIT 1", run_tag, start, end)
        if first is None:
            return
        start = first
    planned = [(r["starts_at"], r["ends_at"]) for r in await conn.fetch(
        "SELECT starts_at, ends_at FROM mes.planned_stop WHERE line_id = $1 AND ends_at > $2 AND starts_at < $3", line_id, start, end)]
    stop_rows = await conn.fetch(
        """SELECT starts_at, coalesce(ends_at, $4) AS ends_at, cause_id FROM mes.stop_event
           WHERE machine_id = $1 AND coalesce(ends_at, $4) > $2 AND starts_at < $3""", line_id, start, end, now)
    stops = [(r["starts_at"], r["ends_at"]) for r in stop_rows]
    line = await conn.fetchrow("SELECT name, attributes, area_id FROM catalog.machine WHERE id = $1", line_id)
    rate = (line["attributes"] or {}).get("cadence_nominale_th")
    ton_tag = await conn.fetchval("SELECT id FROM catalog.tag WHERE machine_id = $1 AND role = 'compteur_tonnage'", line_id)
    tonnage = await counter_delta(conn, ton_tag, start, end) if ton_tag else None
    k = compute_kpi(start, end, planned, stops, tonnage, float(rate) if rate else None)

    # Énergie : compteurs rattachés à la ligne (attribut « ligne ») ou, à défaut, à la même zone
    meters = await conn.fetch(
        """SELECT m.id, g.id AS tag_id FROM catalog.machine m JOIN catalog.tag g ON g.machine_id = m.id AND g.role = 'compteur_energie'
           WHERE m.enabled AND (m.attributes->>'ligne' = $1 OR (m.attributes->>'ligne' IS NULL AND m.area_id = $2 AND $2 IS NOT NULL))""",
        line["name"], line["area_id"])
    energy = None
    for m in meters:
        kwh = await counter_delta(conn, m["tag_id"], start, end)
        if kwh is None:
            continue
        energy = (energy or 0) + kwh
        await conn.execute(
            """INSERT INTO mes.energy_shift (shift_id, meter_id, kwh) VALUES ($1, $2, $3)
               ON CONFLICT (shift_id, meter_id) DO UPDATE SET kwh = $3""", shift["id"], m["id"], kwh)
    final = shift["ends_at"] <= now - timedelta(minutes=5)
    unqualified = sum(1 for r in stop_rows if r["cause_id"] is None)
    await conn.execute(
        """INSERT INTO mes.shift_kpi (shift_id, computed_at, final, duration_s, planned_stop_s, run_s, unplanned_stop_s,
                stop_count, unqualified_count, tonnage_t, target_t, nominal_rate_th, availability, performance, quality, oee,
                energy_kwh, kwh_per_t)
           VALUES ($1, now(), $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
           ON CONFLICT (shift_id) DO UPDATE SET computed_at = now(), final = $2, duration_s = $3, planned_stop_s = $4,
                run_s = $5, unplanned_stop_s = $6, stop_count = $7, unqualified_count = $8, tonnage_t = $9, target_t = $10,
                nominal_rate_th = $11, availability = $12, performance = $13, quality = $14, oee = $15, energy_kwh = $16, kwh_per_t = $17""",
        shift["id"], final, k["duration_s"], k["planned_stop_s"], k["run_s"], k["unplanned_stop_s"], len(stop_rows), unqualified,
        tonnage, shift["target_t"], float(rate) if rate else None, k["availability"], k["performance"], k["quality"], k["oee"],
        energy, (energy / tonnage) if energy is not None and tonnage else None)


async def compute_kpis(conn, recompute_days: int = 2) -> int:
    shifts = await conn.fetch(
        """SELECT s.* FROM mes.shift s LEFT JOIN mes.shift_kpi k ON k.shift_id = s.id
           WHERE s.starts_at <= now() AND s.ends_at > now() - make_interval(days => $1)
             AND (k.final IS NULL OR NOT k.final OR k.computed_at < s.ends_at)""", recompute_days)
    for s in shifts:
        await compute_shift(conn, s)
    return len(shifts)


# ----------------------------------------------------------------------------- dérives moteurs
def drift_status(dev: float | None, warn: float, alert: float) -> str:
    if dev is None:
        return "sans_reference"
    a = abs(dev)
    return "alerte" if a >= alert else "attention" if a >= warn else "normal"


async def compute_drifts(conn) -> int:
    window = int(await db.get_setting(conn, "derive_fenetre_min", 15))
    warn = float(await db.get_setting(conn, "derive_seuil_attention_pct", 10))
    alert = float(await db.get_setting(conn, "derive_seuil_alerte_pct", 20))
    rows = await conn.fetch(
        """WITH motors AS (
             SELECT m.id, max(g.id) FILTER (WHERE g.role = 'courant_moteur') AS cur, max(g.id) FILTER (WHERE g.role = 'etat_marche') AS run
             FROM catalog.machine m JOIN catalog.tag g ON g.machine_id = m.id
             WHERE m.enabled GROUP BY m.id HAVING count(*) FILTER (WHERE g.role = 'courant_moteur') > 0)
           SELECT mo.id,
                  (SELECT avg(v.value) FROM historian.value v WHERE v.tag_id = mo.cur AND v.time > now() - make_interval(mins => $1) AND v.value > 0) AS mean,
                  (SELECT v.value FROM historian.value v WHERE v.tag_id = mo.run ORDER BY v.time DESC LIMIT 1) AS running,
                  r.ref_current
           FROM motors mo LEFT JOIN mes.motor_reference r ON r.machine_id = mo.id""", window)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for r in rows:
        if r["running"] is not None and r["running"] < 0.5 or r["mean"] is None:
            status, dev = "arret", None
        else:
            dev = (r["mean"] - r["ref_current"]) / r["ref_current"] * 100 if r["ref_current"] else None
            status = drift_status(dev, warn, alert)
        await conn.execute(
            """INSERT INTO mes.drift_snapshot (time, machine_id, mean_current, ref_current, deviation_pct, status)
               VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING""",
            now, r["id"], r["mean"], r["ref_current"], dev, status)
    return len(rows)


# ----------------------------------------------------------------------------- purges
async def purge(conn) -> None:
    keep = await db.get_setting(conn, "kpi_conservation", "3 years")
    await conn.execute("DELETE FROM mes.shift WHERE ends_at < now() - $1::text::interval", keep)
    await conn.execute("DELETE FROM mes.stop_event WHERE starts_at < now() - $1::text::interval", keep)
    await conn.execute("DELETE FROM mes.alarm_event WHERE raised_at < now() - $1::text::interval AND cleared_at IS NOT NULL", keep)
    await conn.execute("DELETE FROM mes.drift_snapshot WHERE time < now() - interval '1 year'")
    await conn.execute("DELETE FROM mes.audit_log WHERE at < now() - $1::text::interval", keep)


async def loop_every(seconds: float, fn, name: str) -> None:
    while True:
        try:
            async with db.pool().acquire() as conn:
                await fn(conn)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("tâche %s en erreur", name)
        await asyncio.sleep(seconds)


async def shifts_and_kpis(conn) -> None:
    await generate_shifts(conn)
    await compute_kpis(conn)


def start_all() -> list[asyncio.Task]:
    from .events import EventEngine
    return [
        asyncio.create_task(EventEngine().run()),
        asyncio.create_task(loop_every(60, shifts_and_kpis, "shifts")),
        asyncio.create_task(loop_every(300, compute_drifts, "derives")),
        asyncio.create_task(loop_every(86400, purge, "purge")),
    ]


__all__ = ["compute_kpi", "merge", "subtract", "drift_status", "start_all", "date", "time"]
