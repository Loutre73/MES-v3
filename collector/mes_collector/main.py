"""Point d'entrée du collecteur."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict

import asyncpg
from asyncua import Client, ua
from prometheus_client import start_http_server

from . import control, metrics
from .catalog import TagDef, bootstrap, fingerprint, load_tags
from .config import Config
from .control import Historisation, Rates, perimeter
from .writer import Writer, now

log = logging.getLogger("collector")

QUALITY_GOOD, QUALITY_UNCERTAIN, QUALITY_BAD = 0, 1, 2


def quality_of(status: ua.StatusCode | None) -> int:
    if status is None:
        return QUALITY_GOOD
    sev = (status.value >> 30) & 0x3
    return {0: QUALITY_GOOD, 1: QUALITY_UNCERTAIN}.get(sev, QUALITY_BAD)


def to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Collector:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.writer = Writer(cfg.database_url, cfg.spool_dir, cfg.batch_max_rows, cfg.batch_max_s)
        self.tags: dict[int, TagDef] = {}
        self.by_node: dict[ua.NodeId, int] = {}
        self.last: dict[int, tuple[float | None, int, float]] = {}   # tag -> (valeur, qualité, instant d'écriture)
        self.handles: dict[int, tuple[object, int]] = {}            # tag -> (abonnement, handle serveur)
        self.subs: list = []
        self.hist = Historisation(os.path.join(cfg.spool_dir, "historisation.json"))
        self.db_of: dict[int, str] = {}                              # tag -> périmètre (DB)
        self.counts = {"notifications": 0, "points": 0, "supprimes": 0}
        self.points_db: dict[str, int] = defaultdict(int)
        self.rates = Rates()
        self.opc_up = False
        self.connected_at: float | None = None
        metrics.HIST_ACTIVE.set(1 if self.hist.active else 0)

    # --- notifications OPC UA -------------------------------------------------------------
    def datachange_notification(self, node, val, data) -> None:
        metrics.NOTIFICATIONS.inc()
        self.counts["notifications"] += 1
        metrics.LAST_NOTIFICATION.set(time.time())
        tag_id = self.by_node.get(node.nodeid)
        if tag_id is None:
            return
        dv = data.monitored_item.Value
        ts = dv.SourceTimestamp or dv.ServerTimestamp or now()
        if ts.tzinfo is None:
            from datetime import timezone
            ts = ts.replace(tzinfo=timezone.utc)
        self.accept(tag_id, ts, to_float(val), quality_of(getattr(dv, "StatusCode", None)))

    def status_change_notification(self, status) -> None:
        log.warning("changement d'état de l'abonnement : %s", status)

    def accept(self, tag_id: int, ts, value: float | None, quality: int) -> None:
        prev = self.last.get(tag_id)
        tag = self.tags[tag_id]
        if prev is not None and prev[1] == quality and not tag.deadband.passes(prev[0], value):
            return
        db = self.perimeter_of(tag_id)
        if not self.hist.allows(db):
            # historisation suspendue : on suit la valeur (référence de la deadband) sans l'écrire ;
            # instant d'écriture à 0 pour que la reprise réécrive cette valeur
            self.last[tag_id] = (value, quality, 0.0)
            self.counts["supprimes"] += 1
            metrics.ROWS_SUPPRESSED.inc()
            return
        self.last[tag_id] = (value, quality, time.monotonic())
        metrics.ROWS_STORED.inc()
        self.counts["points"] += 1
        self.points_db[db] += 1
        self.writer.put((ts, tag_id, value, quality))

    def perimeter_of(self, tag_id: int) -> str:
        db = self.db_of.get(tag_id)
        if db is None:
            tag = self.tags.get(tag_id)
            db = self.db_of[tag_id] = perimeter(tag.node_id if tag else "")
        return db

    def resume(self, dbs: list[str] | None) -> int:
        """Reprise de l'historisation : réécrit la dernière valeur connue des périmètres repris."""
        ts = now()
        n = 0
        for tag_id, (value, quality, _) in list(self.last.items()):
            db = self.perimeter_of(tag_id)
            if (dbs is None or db in dbs) and self.hist.allows(db):
                self.last[tag_id] = (value, quality, time.monotonic())
                self.writer.put((ts, tag_id, value, quality))
                n += 1
        log.info("historisation reprise (%s) : %d valeurs d'ancrage écrites", "tout" if dbs is None else ", ".join(dbs), n)
        return n

    # --- abonnements ---------------------------------------------------------------------
    async def subscribe(self, client: Client, tag_ids: list[int]) -> None:
        cfg = self.cfg
        # regroupement par deadband absolue : un filtre serveur par groupe
        groups: dict[float, list[int]] = defaultdict(list)
        # Compteurs (énergie, tonnage) : filtrage côté collecteur seulement. Ils avancent par petits pas ;
        # certains serveurs comparent la deadband à l'échantillon précédent et non à la dernière valeur
        # transmise, et ne remonteraient alors jamais un compteur lent.
        for t in tag_ids:
            tag = self.tags[t]
            server = cfg.server_deadband and tag.deadband.kind == "absolute" and not (tag.role or "").startswith("compteur")
            groups[tag.deadband.value if server else 0.0].append(t)
        for deadband, ids in groups.items():
            for i in range(0, len(ids), cfg.items_per_subscription):
                chunk = ids[i:i + cfg.items_per_subscription]
                sub = await client.create_subscription(cfg.publishing_ms, self)
                self.subs.append(sub)
                nodes = [client.get_node(self.tags[t].node_id) for t in chunk]
                for t, n in zip(chunk, nodes):
                    self.by_node[n.nodeid] = t
                mfilter = None
                if deadband > 0:
                    mfilter = ua.DataChangeFilter(
                        Trigger=ua.DataChangeTrigger.StatusValue,
                        DeadbandType=ua.DeadbandType.Absolute,
                        DeadbandValue=deadband,
                    )
                handles = await sub._subscribe(nodes, ua.AttributeIds.Value, mfilter=mfilter,
                                               queuesize=1, sampling_interval=cfg.sampling_ms)
                handles = handles if isinstance(handles, list) else [handles]
                retry = [(t, n) for t, n, h in zip(chunk, nodes, handles) if not isinstance(h, int)]
                for t, h in zip(chunk, handles):
                    if isinstance(h, int):
                        self.handles[t] = (sub, h)
                if retry and mfilter is not None:
                    log.info("filtre de deadband refusé par le serveur pour %d tags : filtrage côté collecteur seul", len(retry))
                    h2 = await sub._subscribe([n for _, n in retry], ua.AttributeIds.Value, mfilter=None,
                                              queuesize=1, sampling_interval=cfg.sampling_ms)
                    h2 = h2 if isinstance(h2, list) else [h2]
                    for (t, _), h in zip(retry, h2):
                        if isinstance(h, int):
                            self.handles[t] = (sub, h)
                        else:
                            log.warning("abonnement refusé pour %s : %s", self.tags[t].name, h)
                elif retry:
                    for t, h in [(t, h) for t, h in zip(chunk, handles) if not isinstance(h, int)]:
                        log.warning("abonnement refusé pour %s : %s", self.tags[t].name, h)
        metrics.TAGS.set(len(self.handles))
        metrics.SUBSCRIPTIONS.set(len(self.subs))
        log.info("%d tags surveillés dans %d abonnements", len(self.handles), len(self.subs))

    async def unsubscribe(self, tag_ids: list[int]) -> None:
        for t in tag_ids:
            sub, h = self.handles.pop(t, (None, None))
            if sub is not None:
                try:
                    await sub.unsubscribe(h)
                except Exception as e:  # noqa: BLE001
                    log.warning("désabonnement de %s impossible : %s", t, e)
            self.last.pop(t, None)
        self.by_node = {n: t for n, t in self.by_node.items() if t in self.handles}
        metrics.TAGS.set(len(self.handles))

    # --- boucles de fond -------------------------------------------------------------------
    async def keepalive_loop(self) -> None:
        """Réécrit la dernière valeur des tags qui n'ont pas changé depuis longtemps."""
        while True:
            await asyncio.sleep(60)
            limit = time.monotonic() - self.cfg.keepalive_rewrite_s
            ts = now()
            for tag_id, (value, quality, written) in list(self.last.items()):
                if written < limit and self.hist.allows(self.perimeter_of(tag_id)):
                    self.last[tag_id] = (value, quality, time.monotonic())
                    self.writer.put((ts, tag_id, value, quality))

    async def catalog_loop(self, client: Client, fp) -> None:
        """Recharge le catalogue à chaud quand il change (ajout, retrait, deadband)."""
        while True:
            await asyncio.sleep(self.cfg.catalog_poll_s)
            try:
                conn = await asyncpg.connect(self.cfg.database_url)
                try:
                    new_fp = await fingerprint(conn)
                    if new_fp == fp:
                        continue
                    tags = await load_tags(conn)
                finally:
                    await conn.close()
            except (OSError, asyncpg.PostgresError) as e:
                log.warning("lecture du catalogue impossible : %s", e)
                continue
            fp = new_fp
            removed = [t for t in self.tags if t not in tags]
            added = [t for t in tags if t not in self.tags]
            self.tags.update(tags)
            self.db_of.clear()
            await self.unsubscribe(removed)
            for t in removed:
                self.tags.pop(t, None)
            if added:
                await self.subscribe(client, added)
            log.info("catalogue rechargé : %d ajoutés, %d retirés", len(added), len(removed))

    def mark_bad(self) -> None:
        ts = now()
        for tag_id, (value, quality, _) in list(self.last.items()):
            if quality != QUALITY_BAD and self.hist.allows(self.perimeter_of(tag_id)):
                self.last[tag_id] = (value, QUALITY_BAD, time.monotonic())
                self.writer.put((ts, tag_id, value, QUALITY_BAD))

    # --- cycle de vie ------------------------------------------------------------------------
    async def session(self) -> None:
        cfg = self.cfg
        client = Client(cfg.endpoint, timeout=30)
        if cfg.security:
            await client.set_security_string(cfg.security)
        if cfg.username:
            client.set_user(cfg.username)
            client.set_password(cfg.password or "")
        try:
            async with client:
                metrics.OPC_UP.set(1)
                self.opc_up, self.connected_at = True, time.time()
                metrics.SESSIONS.inc()
                log.info("connecté à %s", cfg.endpoint)
                conn = await asyncpg.connect(cfg.database_url)
                try:
                    if cfg.bootstrap_browse and (await fingerprint(conn))[0] == 0:
                        await bootstrap(client, conn, cfg.browse_root)
                    fp = await fingerprint(conn)
                    self.tags = await load_tags(conn)
                finally:
                    await conn.close()
                self.by_node.clear()
                self.handles.clear()
                self.subs.clear()
                t0 = time.monotonic()
                await self.subscribe(client, list(self.tags))
                metrics.SUBSCRIBE_SECONDS.set(time.monotonic() - t0)
                log.info("abonnements créés en %.1f s", time.monotonic() - t0)
                tasks = [asyncio.create_task(self.catalog_loop(client, fp)),
                         asyncio.create_task(self.keepalive_loop())]
                try:
                    while True:
                        await asyncio.sleep(5)
                        await client.check_connection()
                finally:
                    for t in tasks:
                        t.cancel()
        finally:
            metrics.OPC_UP.set(0)
            metrics.SUBSCRIPTIONS.set(0)
            self.opc_up = False

    # --- contrôle (console d'exploitation) ------------------------------------------------
    async def control_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            if self.hist.tick():
                self.resume(None)
            metrics.HIST_ACTIVE.set(1 if self.hist.active else 0)
            self.rates.update({**self.counts, **{f"db:{k}": v for k, v in self.points_db.items()}})

    def perimeters(self) -> list[dict]:
        tags: dict[str, int] = defaultdict(int)
        for t in self.handles or self.tags:
            tags[self.perimeter_of(t)] += 1
        return [{"nom": db, "tags": n, "historise": db not in self.hist.off_dbs,
                 "points_s": self.rates.values.get(f"db:{db}", 0.0)} for db, n in sorted(tags.items())]

    def status(self) -> dict:
        r = self.rates.values
        return {"opcua": self.opc_up, "endpoint": self.cfg.endpoint, "connecte_depuis": self.connected_at,
                "tags": len(self.handles), "abonnements": len(self.subs),
                "notifications_s": r.get("notifications", 0.0), "points_s": r.get("points", 0.0),
                "supprimes_s": r.get("supprimes", 0.0), "file": self.writer.queue.qsize(),
                "tampon_disque": self.writer.spool.count(), "base_ok": bool(metrics.DB_UP._value.get()),
                "derniere_ecriture": metrics.LAST_WRITE._value.get() or None,
                "historisation": {**self.hist.state(), "perimetres": self.perimeters()}}

    async def handle(self, method: str, target: str, body: dict):
        path = target.split("?")[0]
        if method == "GET" and path == "/etat":
            return 200, self.status()
        if method == "GET" and path == "/historisation":
            return 200, {**self.hist.state(), "perimetres": self.perimeters()}
        if method == "PUT" and path == "/historisation":
            try:
                resumed = self.hist.update(body)
            except ValueError as e:
                return 400, {"erreur": str(e)}
            metrics.HIST_ACTIVE.set(1 if self.hist.active else 0)
            if resumed is None or resumed:
                self.resume(resumed)
            log.info("historisation : %s", self.hist.state())
            return 200, {**self.hist.state(), "perimetres": self.perimeters()}
        return 404, {"erreur": "introuvable"}

    async def run(self) -> None:
        start_http_server(self.cfg.metrics_port)
        await control.serve(self.cfg.control_port, self.handle)
        asyncio.create_task(self.control_loop())
        asyncio.create_task(self.writer.run())
        delay = 2
        while True:
            try:
                await self.session()
                delay = 2
            except Exception as e:  # noqa: BLE001
                metrics.SESSION_ERRORS.inc()
                log.error("session interrompue : %s ; reconnexion dans %d s", e, delay)
                self.mark_bad()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    asyncio.run(Collector(Config.from_env()).run())


if __name__ == "__main__":
    main()
