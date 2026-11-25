"""Écriture par lots dans historian.value, avec tampon disque si la base est indisponible."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

from . import metrics

log = logging.getLogger("writer")

Row = tuple[datetime, int, float | None, int]  # time, tag_id, value, quality
COLUMNS = ["time", "tag_id", "value", "quality"]


class Spool:
    """Fichiers JSON Lines horodatés : un fichier par lot non écrit, rejoués dans l'ordre."""

    def __init__(self, directory: str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, rows: list[Row]) -> None:
        path = self.dir / f"{time.time_ns()}.jsonl"
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for t, tag, v, q in rows:
                f.write(json.dumps([t.isoformat(), tag, v, q]) + "\n")
        os.replace(tmp, path)
        metrics.SPOOL_FILES.set(self.count())

    def files(self) -> list[Path]:
        return sorted(self.dir.glob("*.jsonl"))

    def count(self) -> int:
        return len(self.files())

    @staticmethod
    def load(path: Path) -> list[Row]:
        rows: list[Row] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                t, tag, v, q = json.loads(line)
                rows.append((datetime.fromisoformat(t), tag, v, q))
        return rows


class Writer:
    def __init__(self, dsn: str, spool_dir: str, max_rows: int, max_s: float):
        self.dsn = dsn
        self.spool = Spool(spool_dir)
        self.max_rows = max_rows
        self.max_s = max_s
        self.queue: asyncio.Queue[Row] = asyncio.Queue()
        self.pool: asyncpg.Pool | None = None

    def put(self, row: Row) -> None:
        self.queue.put_nowait(row)
        metrics.QUEUE.set(self.queue.qsize())

    async def _copy(self, rows: list[Row]) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=2)
        async with self.pool.acquire() as conn:
            with metrics.WRITE_SECONDS.time():
                await conn.copy_records_to_table("value", schema_name="historian", columns=COLUMNS, records=rows)

    async def _write(self, rows: list[Row]) -> None:
        try:
            await self._copy(rows)
            metrics.BATCH_ROWS.observe(len(rows))
            metrics.ROWS_WRITTEN.inc(len(rows))
            metrics.LAST_WRITE.set(time.time())
            metrics.DB_UP.set(1)
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
            log.warning("écriture impossible (%s) : %d lignes mises en tampon disque", e, len(rows))
            metrics.DB_UP.set(0)
            metrics.WRITE_ERRORS.inc()
            metrics.ROWS_SPOOLED.inc(len(rows))
            self.spool.save(rows)
            await self._reset_pool()

    async def _reset_pool(self) -> None:
        if self.pool is not None:
            try:
                self.pool.terminate()
            except Exception:  # noqa: BLE001
                pass
            self.pool = None

    async def run(self) -> None:
        asyncio.create_task(self._replay_loop())
        while True:
            first = await self.queue.get()
            rows = [first]
            deadline = time.monotonic() + self.max_s
            while len(rows) < self.max_rows:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                try:
                    rows.append(await asyncio.wait_for(self.queue.get(), timeout))
                except asyncio.TimeoutError:
                    break
            metrics.QUEUE.set(self.queue.qsize())
            await self._write(rows)

    async def _replay_loop(self) -> None:
        """Rejoue le tampon disque dès que la base répond à nouveau."""
        while True:
            await asyncio.sleep(5)
            files = self.spool.files()
            metrics.SPOOL_FILES.set(len(files))
            for path in files:
                try:
                    rows = Spool.load(path)
                    await self._copy(rows)
                    metrics.ROWS_REPLAYED.inc(len(rows))
                    metrics.ROWS_WRITTEN.inc(len(rows))
                except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
                    await self._reset_pool()
                    break
                path.unlink()
                log.info("tampon rejoué : %s", path.name)
            metrics.SPOOL_FILES.set(self.spool.count())


def now() -> datetime:
    return datetime.now(timezone.utc)
