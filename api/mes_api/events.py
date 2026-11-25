"""Détection d'événements à partir de l'historian : arrêts des lignes et alarmes.

Le moteur suit les nouvelles lignes de historian.value pour les tags surveillés, dans l'ordre
chronologique, ce qui donne des horodatages exacts et ne manque aucun changement d'état,
même entre deux cycles de lecture.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger("events")
POLL_S = 2.0
OVERLAP = timedelta(seconds=10)   # relit un peu en arrière pour les valeurs arrivées en retard


def alarm_active(cond: str, threshold: float | None, v: float | None) -> bool | None:
    if v is None:
        return None
    if cond == "vrai":
        return v >= 0.5
    if cond == "faux":
        return v < 0.5
    if cond == "superieur":
        return threshold is not None and v > threshold
    if cond == "inferieur":
        return threshold is not None and v < threshold
    return None


class EventEngine:
    def __init__(self):
        self.cursor: datetime | None = None
        self.seen: dict[int, datetime] = {}               # tag -> horodatage du dernier point traité
        self.run_tags: dict[int, int] = {}                # tag etat_marche -> machine
        self.code_tags: dict[int, int] = {}               # tag code_arret -> machine
        self.alarm_defs: dict[int, list[dict]] = {}       # tag -> définitions
        self.running: dict[int, bool] = {}                # machine -> en marche
        self.codes: dict[int, int] = {}                   # machine -> dernier code d'arrêt automate
        self.alarm_state: dict[int, bool] = {}            # alarm_def -> active
        self.last_reload = 0.0

    async def reload(self, conn) -> None:
        rows = await conn.fetch(
            """SELECT g.id AS tag_id, g.role, m.id AS machine_id FROM catalog.tag g
               JOIN catalog.machine m ON m.id = g.machine_id JOIN catalog.machine_type t ON t.id = m.type_id
               WHERE m.enabled AND g.enabled AND (t.definition->>'track_stops')::boolean
                 AND g.role IN ('etat_marche', 'code_arret')""")
        self.run_tags = {r["tag_id"]: r["machine_id"] for r in rows if r["role"] == "etat_marche"}
        self.code_tags = {r["tag_id"]: r["machine_id"] for r in rows if r["role"] == "code_arret"}
        defs: dict[int, list[dict]] = {}
        for r in await conn.fetch("SELECT id, tag_id, condition, threshold FROM mes.alarm_def WHERE active"):
            defs.setdefault(r["tag_id"], []).append(dict(r))
        self.alarm_defs = defs
        open_alarms = {r["def_id"] for r in await conn.fetch("SELECT DISTINCT def_id FROM mes.alarm_event WHERE cleared_at IS NULL")}
        for d in (d for ds in defs.values() for d in ds):
            self.alarm_state.setdefault(d["id"], d["id"] in open_alarms)
        # état initial des lignes : dernière valeur connue
        for tag, mid in self.run_tags.items():
            if mid not in self.running:
                r = await conn.fetchrow(
                    "SELECT time, value FROM historian.value WHERE tag_id = $1 ORDER BY time DESC LIMIT 1", tag)
                if r is not None and r["value"] is not None:
                    self.running[mid] = r["value"] >= 0.5
                    if not self.running[mid]:
                        await self._open_stop(conn, mid, r["time"])
        self.last_reload = asyncio.get_running_loop().time()

    async def _open_stop(self, conn, mid: int, at: datetime) -> None:
        # idempotent : après un redémarrage, le moteur relit les dernières minutes de l'historian
        exists = await conn.fetchval(
            "SELECT 1 FROM mes.stop_event WHERE machine_id = $1 AND (ends_at IS NULL OR starts_at = $2)", mid, at)
        if exists:
            return
        planned = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM mes.planned_stop WHERE line_id = $1 AND $2 >= starts_at AND $2 < ends_at)", mid, at)
        code = self.codes.get(mid) or None
        cause = await conn.fetchval("SELECT cause_id FROM mes.plc_stop_code WHERE code = $1", code) if code else None
        await conn.execute(
            "INSERT INTO mes.stop_event (machine_id, starts_at, plc_code, planned, cause_id, qualified_by) VALUES ($1, $2, $3, $4, $5, $6)",
            mid, at, code, planned, cause, "automate" if cause else None)

    async def _close_stop(self, conn, mid: int, at: datetime) -> None:
        r = await conn.fetchrow(
            "UPDATE mes.stop_event SET ends_at = $2 WHERE machine_id = $1 AND ends_at IS NULL AND starts_at <= $2 RETURNING id, starts_at",
            mid, at)
        if r is None:
            return
        min_s = float(await db.get_setting(conn, "arret_duree_min_s", 60))
        if (at - r["starts_at"]).total_seconds() < min_s:
            # micro-arrêt : non qualifié, il pèse sur la performance et non sur la disponibilité
            await conn.execute("DELETE FROM mes.stop_event WHERE id = $1 AND cause_id IS NULL", r["id"])

    async def _code_changed(self, conn, mid: int, code: int) -> None:
        self.codes[mid] = code
        if code and not self.running.get(mid, True):
            cause = await conn.fetchval("SELECT cause_id FROM mes.plc_stop_code WHERE code = $1", code)
            await conn.execute(
                """UPDATE mes.stop_event SET plc_code = $2,
                          cause_id = coalesce(cause_id, $3), qualified_by = coalesce(qualified_by, CASE WHEN $3::int IS NULL THEN NULL ELSE 'automate' END)
                   WHERE machine_id = $1 AND ends_at IS NULL""", mid, code, cause)

    async def step(self, conn) -> int:
        loop = asyncio.get_running_loop()
        if loop.time() - self.last_reload > 30:
            await self.reload(conn)
        tags = list(self.run_tags) + list(self.code_tags) + list(self.alarm_defs)
        if not tags:
            return 0
        since = (self.cursor - OVERLAP) if self.cursor else datetime.now(timezone.utc) - timedelta(minutes=5)
        rows = await conn.fetch(
            "SELECT time, tag_id, value FROM historian.value WHERE tag_id = ANY($1) AND time > $2 ORDER BY time, tag_id",
            tags, since)
        n = 0
        for r in rows:
            tag, t, v = r["tag_id"], r["time"], r["value"]
            if tag in self.seen and t <= self.seen[tag]:
                continue
            self.seen[tag] = t
            n += 1
            if v is None:
                continue
            if tag in self.code_tags:
                await self._code_changed(conn, self.code_tags[tag], int(v))
            if tag in self.run_tags:
                mid = self.run_tags[tag]
                on = v >= 0.5
                prev = self.running.get(mid)
                self.running[mid] = on
                if prev is None or prev != on:
                    if on:
                        await self._close_stop(conn, mid, t)
                    else:
                        await self._open_stop(conn, mid, t)
            for d in self.alarm_defs.get(tag, []):
                act = alarm_active(d["condition"], d["threshold"], v)
                if act is None or act == self.alarm_state.get(d["id"], False):
                    continue
                self.alarm_state[d["id"]] = act
                if act:
                    await conn.execute(
                        """INSERT INTO mes.alarm_event (def_id, raised_at, value) SELECT $1, $2, $3
                           WHERE NOT EXISTS (SELECT 1 FROM mes.alarm_event WHERE def_id = $1 AND raised_at = $2)""", d["id"], t, v)
                else:
                    await conn.execute(
                        "UPDATE mes.alarm_event SET cleared_at = $2 WHERE def_id = $1 AND cleared_at IS NULL", d["id"], t)
        if rows:
            self.cursor = max(self.cursor or rows[-1]["time"], rows[-1]["time"])
        elif self.cursor is None:
            self.cursor = since
        return n

    async def run(self) -> None:
        while True:
            try:
                async with db.pool().acquire() as conn:
                    await self.step(conn)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("erreur du moteur d'événements")
            await asyncio.sleep(POLL_S)
