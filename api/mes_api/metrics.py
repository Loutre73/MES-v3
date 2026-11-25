"""Indicateurs Prometheus de l'API (tableau de bord « Système »).

Exposés sur http://api:8000/metrics, joignable seulement depuis le réseau Docker :
nginx ne relaie que /api/ et /grafana/.
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUESTS = Counter("mes_api_requests_total", "Requêtes HTTP traitées", ["method", "route", "status"])
LATENCY = Histogram("mes_api_request_duration_seconds", "Durée de traitement des requêtes HTTP",
                    ["method", "route"],
                    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30))
IN_PROGRESS = Gauge("mes_api_requests_in_progress", "Requêtes HTTP en cours")
EXCEPTIONS = Counter("mes_api_exceptions_total", "Exceptions non gérées", ["route"])
POOL_SIZE = Gauge("mes_api_db_pool_size", "Connexions ouvertes du pool de la base")
POOL_IDLE = Gauge("mes_api_db_pool_idle", "Connexions inactives du pool de la base")
POOL_MAX = Gauge("mes_api_db_pool_max", "Taille maximale du pool de la base")
TASK_SECONDS = Histogram("mes_api_task_duration_seconds", "Durée d'un cycle des tâches de fond", ["task"],
                         buckets=(.01, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120))
TASK_ERRORS = Counter("mes_api_task_errors_total", "Cycles de tâches de fond en erreur", ["task"])
TASK_LAST = Gauge("mes_api_task_last_success_timestamp", "Dernier cycle réussi d'une tâche de fond", ["task"])

for _task in ("evenements", "shifts", "derives", "purge"):   # séries à 0 dès le démarrage
    TASK_ERRORS.labels(_task)

SKIP = {"/metrics", "/api/sante"}


def route_of(request: Request) -> str:
    """Gabarit de la route (/api/lignes/{line_id}) plutôt que l'URL : cardinalité bornée.

    Le routage FastAPI renseigne scope["route"] ; à lire après le traitement de la requête.
    """
    route = request.scope.get("route")
    return getattr(route, "path_format", None) or getattr(route, "path", None) or "inconnue"


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def measure(request: Request, call_next):
        path = request.url.path
        if path in SKIP:
            return await call_next(request)
        method = request.method
        IN_PROGRESS.inc()
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            EXCEPTIONS.labels(route_of(request)).inc()
            raise
        finally:
            IN_PROGRESS.dec()
            route = route_of(request)
            LATENCY.labels(method, route).observe(time.perf_counter() - t0)
            REQUESTS.labels(method, route, str(status)).inc()

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint():
        from . import db
        pool = db._pool
        if pool is not None:
            POOL_SIZE.set(pool.get_size())
            POOL_IDLE.set(pool.get_idle_size())
            POOL_MAX.set(pool.get_max_size())
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
