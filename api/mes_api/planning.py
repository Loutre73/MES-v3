"""Planning des postes : semaines, création et copie de postes, copie automatique d'une semaine vide.

Une semaine de planning commence le lundi à 6 h (heure du site) ; une journée de planning va de 6 h à 6 h.
Un poste appartient à la journée où il commence et ne la dépasse pas. Les arrêts planifiés d'un poste
(arrêt ou maintenance) sont des plages de mes.planned_stop rattachées au poste.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings

TZ = ZoneInfo(settings.timezone)
DAY_START = time(6)
STOP_TYPES = ("arret", "maintenance")
MIN_SHIFT = timedelta(minutes=15)


class PlanningError(ValueError):
    """Règle de planning non respectée (message affichable)."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def day_start(t: datetime) -> datetime:
    """Début (6 h) de la journée de planning qui contient t."""
    loc = t.astimezone(TZ)
    d = loc.date() if loc.time() >= DAY_START else loc.date() - timedelta(days=1)
    return datetime.combine(d, DAY_START, TZ)


def week_monday(t: datetime) -> date:
    d = day_start(t).date()
    return d - timedelta(days=d.weekday())


def week_bounds(monday: date) -> tuple[datetime, datetime]:
    if monday.weekday() != 0:
        raise PlanningError("Une semaine de planning commence un lundi")
    return datetime.combine(monday, DAY_START, TZ), datetime.combine(monday + timedelta(days=7), DAY_START, TZ)


def shift_local(t: datetime, days: int) -> datetime:
    """Même heure locale, `days` jours plus tard (les changements d'heure gardent l'heure affichée)."""
    loc = t.astimezone(TZ)
    return datetime.combine(loc.date() + timedelta(days=days), loc.timetz().replace(tzinfo=None), TZ)


@dataclass
class Stop:
    type: str
    starts_at: datetime
    ends_at: datetime
    reason: str | None = None


@dataclass
class ShiftData:
    line_id: int
    starts_at: datetime
    ends_at: datetime
    team: str | None = None
    target_t: float | None = None
    recipe_id: int | None = None
    comment: str | None = None
    template_id: int | None = None
    stops: list[Stop] = field(default_factory=list)


def check_shift(s: ShiftData) -> None:
    """Règles d'un poste et de ses arrêts planifiés, indépendantes de la base."""
    if s.ends_at - s.starts_at < MIN_SHIFT:
        raise PlanningError("Un poste dure au moins 15 minutes")
    if s.ends_at > day_start(s.starts_at) + timedelta(days=1):
        raise PlanningError("Un poste se termine au plus tard à 6 h le lendemain de son début")
    if s.target_t is not None and s.target_t < 0:
        raise PlanningError("L'objectif ne peut pas être négatif")
    prev = None
    for st in sorted(s.stops, key=lambda x: x.starts_at):
        if st.type not in STOP_TYPES:
            raise PlanningError("Type d'arrêt planifié : arret ou maintenance")
        if st.ends_at <= st.starts_at:
            raise PlanningError("La fin d'un arrêt planifié doit suivre son début")
        if st.starts_at < s.starts_at or st.ends_at > s.ends_at:
            raise PlanningError("Un arrêt planifié doit rester à l'intérieur du poste")
        if prev and st.starts_at < prev.ends_at:
            raise PlanningError("Deux arrêts planifiés du poste se chevauchent")
        prev = st


async def overlapping(conn, line_id: int, start: datetime, end: datetime, exclude: int | None = None):
    return await conn.fetch(
        """SELECT id, starts_at, ends_at FROM mes.shift
           WHERE line_id = $1 AND starts_at < $3 AND ends_at > $2 AND id <> coalesce($4, 0) ORDER BY starts_at""",
        line_id, start, end, exclude)


async def stops_of(conn, shift_id: int) -> list[Stop]:
    return [Stop(r["type"], r["starts_at"], r["ends_at"], r["reason"]) for r in await conn.fetch(
        "SELECT type, starts_at, ends_at, reason FROM mes.planned_stop WHERE shift_id = $1 ORDER BY starts_at", shift_id)]


async def replace_stops(conn, shift_id: int, line_id: int, stops: list[Stop], actor: str | None) -> None:
    await conn.execute("DELETE FROM mes.planned_stop WHERE shift_id = $1", shift_id)
    for st in stops:
        await conn.execute(
            """INSERT INTO mes.planned_stop (line_id, starts_at, ends_at, reason, created_by, shift_id, type)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            line_id, st.starts_at, st.ends_at, st.reason or None, actor, shift_id, st.type)


async def insert_shift(conn, s: ShiftData, actor: str | None) -> int:
    sid = await conn.fetchval(
        """INSERT INTO mes.shift (line_id, template_id, starts_at, ends_at, team, target_t, comment, kind, recipe_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, 'production', $8) RETURNING id""",
        s.line_id, s.template_id, s.starts_at, s.ends_at, s.team, s.target_t, s.comment, s.recipe_id)
    await replace_stops(conn, sid, s.line_id, s.stops, actor)
    return sid


async def load_shift(conn, sid: int) -> ShiftData | None:
    r = await conn.fetchrow("SELECT * FROM mes.shift WHERE id = $1", sid)
    if r is None:
        return None
    return ShiftData(r["line_id"], r["starts_at"], r["ends_at"], r["team"], r["target_t"], r["recipe_id"],
                     r["comment"], r["template_id"], await stops_of(conn, sid))


def moved(s: ShiftData, line_id: int, start: datetime) -> ShiftData:
    """Copie d'un poste sur une autre ligne ou à un autre moment ; ses arrêts suivent."""
    d = start - s.starts_at
    return ShiftData(line_id, start, start + (s.ends_at - s.starts_at), s.team, s.target_t, s.recipe_id, None,
                     s.template_id, [Stop(x.type, x.starts_at + d, x.ends_at + d, x.reason) for x in s.stops])


async def place(conn, s: ShiftData, replace: bool, actor: str | None, now: datetime) -> str:
    """Pose un poste ; renvoie « cree », « passe » (créneau commencé), « occupe » ou « hors_journee »."""
    if s.starts_at <= now:
        return "passe"
    try:
        check_shift(s)
    except PlanningError:
        return "hors_journee"
    others = await overlapping(conn, s.line_id, s.starts_at, s.ends_at)
    if others:
        if not replace or any(o["starts_at"] <= now for o in others):
            return "occupe"
        await conn.execute("DELETE FROM mes.shift WHERE id = ANY($1::int[])", [o["id"] for o in others])
    await insert_shift(conn, s, actor)
    return "cree"


async def copy_week(conn, src: date, dst: date, *, line_ids: list[int] | None = None, teams: bool = True,
                    targets: bool = True, stops: bool = True, replace: bool = False, actor: str | None = None,
                    now: datetime | None = None) -> dict:
    """Copie les postes de la semaine src dans la semaine dst, jour pour jour et heure pour heure.

    Les créneaux déjà commencés ne sont jamais touchés. Sans `replace`, un poste existant de la semaine cible
    est conservé et la copie qui le chevaucherait est ignorée ; avec `replace`, les postes à venir de la
    semaine cible (sur les lignes concernées) sont d'abord supprimés.
    """
    now = now or now_utc()
    s0, s1 = week_bounds(src)
    d0, d1 = week_bounds(dst)
    if src == dst:
        raise PlanningError("Choisissez une autre semaine que la semaine modèle")
    out = {"cree": 0, "passe": 0, "occupe": 0, "hors_journee": 0, "supprimes": 0}
    if replace:
        r = await conn.execute(
            """DELETE FROM mes.shift WHERE starts_at >= $1 AND starts_at < $2 AND starts_at > $3
                 AND ($4::int[] IS NULL OR line_id = ANY($4::int[]))""", d0, d1, now, line_ids)
        out["supprimes"] = int(r.split()[-1])
    days = (dst - src).days
    for r in await conn.fetch(
            """SELECT id FROM mes.shift WHERE starts_at >= $1 AND starts_at < $2
                 AND ($3::int[] IS NULL OR line_id = ANY($3::int[])) ORDER BY starts_at""", s0, s1, line_ids):
        s = await load_shift(conn, r["id"])
        c = moved(s, s.line_id, shift_local(s.starts_at, days))
        c.ends_at = shift_local(s.ends_at, days)
        c.stops = [Stop(x.type, shift_local(x.starts_at, days), shift_local(x.ends_at, days), x.reason) for x in s.stops] if stops else []
        if not teams:
            c.team = None
        if not targets:
            c.target_t, c.recipe_id = None, None
        out[await place(conn, c, False, actor, now)] += 1
    return out


async def clear_week(conn, monday: date, line_ids: list[int] | None, now: datetime | None = None) -> int:
    """Supprime les postes à venir d'une semaine ; les postes commencés sont conservés."""
    w0, w1 = week_bounds(monday)
    r = await conn.execute(
        """DELETE FROM mes.shift WHERE starts_at >= $1 AND starts_at < $2 AND starts_at > $3
             AND ($4::int[] IS NULL OR line_id = ANY($4::int[]))""", w0, w1, now or now_utc(), line_ids)
    return int(r.split()[-1])


# ----------------------------------------------------------------------------- copie automatique
async def set_setting(conn, key: str, value) -> None:
    await conn.execute(
        "INSERT INTO mes.setting (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, value)


async def auto_settings(conn) -> dict:
    return {"active": bool(await db.get_setting(conn, "planning_copie_auto", True)),
            "jour": int(await db.get_setting(conn, "planning_copie_jour", 4)),
            "heure": int(await db.get_setting(conn, "planning_copie_heure", 6))}


def auto_trigger(monday: date, jour: int, heure: int) -> datetime:
    """Instant de déclenchement dans la semaine qui commence `monday` (jour 0 = lundi)."""
    d = monday + timedelta(days=jour + (1 if time(heure) < DAY_START else 0))  # avant 6 h : fin de la journée de planning
    return datetime.combine(d, time(heure), TZ)


async def auto_copy(conn, now: datetime | None = None) -> dict | None:
    """Copie la semaine en cours dans la suivante si celle-ci est encore vide à l'heure prévue.

    Une semaine n'est traitée qu'une fois : si on la vide ensuite à la main, elle n'est pas recopiée.
    """
    now = now or now_utc()
    cfg = await auto_settings(conn)
    if not cfg["active"]:
        return None
    monday = week_monday(now)
    if now < auto_trigger(monday, cfg["jour"], cfg["heure"]):
        return None
    nxt = monday + timedelta(days=7)
    if await db.get_setting(conn, "planning_copie_derniere") == nxt.isoformat():
        return None
    n0, n1 = week_bounds(nxt)
    out = None
    async with conn.transaction():
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM mes.shift WHERE starts_at >= $1 AND starts_at < $2)", n0, n1):
            out = await copy_week(conn, monday, nxt, actor="copie automatique", now=now)
            await db.audit(conn, "copie automatique", "semaine_copiee", "planning", nxt.isoformat(),
                           {"source": monday.isoformat(), **out})
        await set_setting(conn, "planning_copie_derniere", nxt.isoformat())
    return out


async def init_from_templates(conn) -> int:
    """Mise en service : remplit une fois le planning vide à partir des modèles de postes (veille à J+7)."""
    if await db.get_setting(conn, "planning_initialise", False):
        return 0
    if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM mes.shift)"):
        await set_setting(conn, "planning_initialise", True)
        return 0
    from .workers import lines
    templates = await conn.fetch("SELECT * FROM mes.shift_template WHERE active")
    lns = await lines(conn)
    if not templates or not lns:
        return 0  # catalogue pas encore découvert : on réessaiera
    today = datetime.now(TZ).date()
    n = 0
    for line in lns:
        rate = (line["attributes"] or {}).get("cadence_nominale_th")
        for d in range(-1, 8):
            for t in templates:
                start = datetime.combine(today + timedelta(days=d), t["start_time"], TZ)
                end = start + t["duration"]
                target = round(float(rate) * t["duration"].total_seconds() / 3600, 1) if rate else None
                r = await conn.execute(
                    """INSERT INTO mes.shift (line_id, template_id, starts_at, ends_at, target_t) VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (line_id, starts_at) DO NOTHING""", line["id"], t["id"], start, end, target)
                n += r.endswith("1")
    await set_setting(conn, "planning_initialise", True)
    return n
