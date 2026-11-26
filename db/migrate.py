"""Applique les migrations SQL dans l'ordre, puis les paramètres du site.

Idempotent : peut être relancé à chaque démarrage ou mise à jour.
Variables : DATABASE_URL, HISTORIAN_RETENTION, HISTORIAN_CHUNK_INTERVAL, AGGREGATE_1MIN_RETENTION,
AGGREGATE_1H_RETENTION, MES_READER_PASSWORD.
"""
import asyncio
import logging
import os
import pathlib
import sys

import asyncpg

log = logging.getLogger("migrate")
MIGRATIONS = pathlib.Path(os.environ.get("MIGRATIONS_DIR", pathlib.Path(__file__).parent / "migrations"))


async def connect(dsn: str, attempts: int = 60) -> asyncpg.Connection:
    for i in range(attempts):
        try:
            return await asyncpg.connect(dsn)
        except asyncpg.InvalidPasswordError:
            # inutile d'attendre : site.env ne correspond pas à la base existante
            log.error("mot de passe refusé par la base : DB_PASSWORD de site.env ne correspond pas au volume de données")
            raise
        except (OSError, asyncpg.CannotConnectNowError, asyncpg.PostgresError) as e:
            log.info("base indisponible (%s), nouvel essai dans 2 s", e)
            await asyncio.sleep(2)
    raise SystemExit("base de données injoignable")


async def main() -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    conn = await connect(os.environ["DATABASE_URL"])
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r["version"] for r in await conn.fetch("SELECT version FROM public.schema_migrations")}
    for f in sorted(MIGRATIONS.glob("*.sql")):
        if f.stem in done:
            continue
        log.info("application de %s", f.name)
        async with conn.transaction():
            await conn.execute(f.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO public.schema_migrations (version) VALUES ($1)", f.stem)

    policies = {
        "historian.value": os.environ.get("HISTORIAN_RETENTION", "1 year"),
        "historian.value_1min": os.environ.get("AGGREGATE_1MIN_RETENTION", "1 year"),
        "historian.value_1h": os.environ.get("AGGREGATE_1H_RETENTION", "3 years"),
    }
    # Purge vérifiée toutes les heures : une conservation courte (quelques heures) est respectée
    # à la taille d'un chunk près.
    for rel, keep in policies.items():
        await conn.execute("SELECT remove_retention_policy($1::regclass, if_exists => true)", rel)
        await conn.execute(
            "SELECT add_retention_policy($1::regclass, $2::text::interval, schedule_interval => interval '1 hour')", rel, keep)
        log.info("conservation de %s : %s", rel, keep)

    # Taille des chunks bruts : la purge supprime des chunks entiers, donc les données brutes sont
    # gardées entre HISTORIAN_RETENTION et HISTORIAN_RETENTION + HISTORIAN_CHUNK_INTERVAL.
    # S'applique aux nouveaux chunks seulement.
    chunk = os.environ.get("HISTORIAN_CHUNK_INTERVAL") or "6 hours"
    await conn.execute("SELECT set_chunk_time_interval('historian.value', $1::text::interval)", chunk)
    log.info("chunks de historian.value : %s", chunk)

    # Le rafraîchissement des agrégats ne doit jamais recalculer une période déjà purgée des
    # données brutes, sinon les agrégats de cette période seraient effacés.
    raw_keep = policies["historian.value"]
    for view, offset, end, every in (("historian.value_1min", "2 hours", "1 minute", "1 minute"),
                                     ("historian.value_1h", "1 day", "1 hour", "15 minutes")):
        start = await conn.fetchval(
            "SELECT least($1::text::interval, $2::text::interval - interval '1 hour')::text", offset, raw_keep)
        await conn.execute("SELECT remove_continuous_aggregate_policy($1::regclass, if_not_exists => true)", view)
        await conn.execute(
            "SELECT add_continuous_aggregate_policy($1::regclass, start_offset => $2::text::interval,"
            " end_offset => $3::text::interval, schedule_interval => $4::text::interval)", view, start, end, every)
        log.info("rafraîchissement de %s : fenêtre de %s", view, start)

    pwd = os.environ.get("MES_READER_PASSWORD")
    if pwd:
        await conn.execute(f"ALTER ROLE mes_reader PASSWORD {quote(pwd)}")
    await conn.close()
    log.info("base à jour")
    return 0


def quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
