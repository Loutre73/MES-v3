"""Indicateurs Prometheus de l'API : route en gabarit, /metrics servi hors de /api."""
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from mes_api import metrics


def make_app() -> FastAPI:
    app = FastAPI()
    metrics.install(app)
    router = APIRouter()   # comme l'application : routes déclarées dans des routeurs inclus

    @router.get("/api/lignes/{line_id}")
    async def ligne(line_id: int):
        return {"id": line_id}

    app.include_router(router)
    return app


def test_route_template_and_exposition():
    client = TestClient(make_app())
    for i in (1, 2, 3):
        assert client.get(f"/api/lignes/{i}").status_code == 200
    assert client.get("/api/inexistante").status_code == 404
    body = client.get("/metrics").text
    assert 'mes_api_requests_total{method="GET",route="/api/lignes/{line_id}",status="200"} 3.0' in body
    assert 'route="inconnue",status="404"' in body
    assert "/api/lignes/1" not in body       # pas une série par identifiant
    assert 'route="/metrics"' not in body     # la collecte ne se mesure pas elle-même
