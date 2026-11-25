"""Accès à la base : un pool asyncpg partagé et quelques aides."""
from __future__ import annotations

import json
from typing import Any

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def _init(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def open_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10, init=_init)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    assert _pool is not None, "pool non initialisé"
    return _pool


def rows(records) -> list[dict[str, Any]]:
    return [dict(r) for r in records]


async def audit(conn, actor: str | None, action: str, entity: str | None = None,
                entity_id: Any = None, details: dict | None = None) -> None:
    await conn.execute(
        "INSERT INTO mes.audit_log (actor, action, entity, entity_id, details) VALUES ($1, $2, $3, $4, $5)",
        actor, action, entity, None if entity_id is None else str(entity_id), details,
    )


async def get_setting(conn, key: str, default=None):
    v = await conn.fetchval("SELECT value FROM mes.setting WHERE key = $1", key)
    return default if v is None else v
