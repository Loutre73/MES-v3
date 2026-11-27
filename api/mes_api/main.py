"""Application FastAPI du MES v3."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel

from . import auth, db, grafana, metrics, typedefs, workers
from .i18n import langue_valide
from .config import settings
from .routes_catalog import router as catalog_router
from .routes_lines import router as lines_router
from .routes_mes import router as mes_router
from .routes_planning import router as planning_router
from .routes_recipes import router as recipes_router

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("asyncua").setLevel(logging.WARNING)
log = logging.getLogger("api")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(30):
        try:
            await db.open_pool()
            break
        except Exception as e:  # noqa: BLE001
            log.info("base indisponible (%s), nouvel essai", e)
            await asyncio.sleep(2)
    await typedefs.sync_machine_types()
    await typedefs.sync_recipe_types()
    await auth.ensure_admin()
    if settings.auth_disabled:
        log.warning("AUTH_DISABLED : accès sans identifiant, chaque visiteur est administrateur")
    tasks = workers.start_all() if settings.workers else []
    yield
    for t in tasks:
        t.cancel()
    await db.close_pool()


app = FastAPI(title="MES v3", version=os.environ.get("MES_VERSION", "dev"), lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
metrics.install(app)
app.include_router(catalog_router)
app.include_router(mes_router)
app.include_router(planning_router)
app.include_router(recipes_router)
app.include_router(lines_router)


class Login(BaseModel):
    username: str
    password: str


@app.post("/api/session")
async def login(body: Login, response: Response):
    user = await auth.authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")
    response.set_cookie(auth.COOKIE, auth.make_token(user), httponly=True, samesite="lax",
                        max_age=settings.session_hours * 3600, secure=os.environ.get("COOKIE_SECURE", "false") == "true")
    return {"username": user.username, "role": user.role, "display_name": user.display_name}


@app.delete("/api/session")
async def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/session")
async def me(user: auth.User = Depends(auth.current_user)):
    async with db.pool().acquire() as conn:
        langue = await conn.fetchval("SELECT langue FROM mes.app_user WHERE username = $1", user.username)
    return {"username": user.username, "role": user.role, "display_name": user.display_name, "langue": langue,
            "version": app.version, "ldap": bool(settings.ldap_url), "auth": not settings.auth_disabled}


class Langue(BaseModel):
    langue: str | None = None


@app.put("/api/session/langue")
async def set_langue(body: Langue, user: auth.User = Depends(auth.current_user)):
    """Langue de l'interface de l'utilisateur connecté, retrouvée sur tous les postes."""
    try:
        langue = langue_valide(body.langue)
    except ValueError:
        raise HTTPException(400, "Langue inconnue")
    async with db.pool().acquire() as conn:
        await conn.execute("UPDATE mes.app_user SET langue = $2 WHERE username = $1", user.username, langue)
    grafana.oublier(user.username)
    return {"langue": langue}


GRAFANA_ROLE = {"admin": "Admin", "maintenance": "Viewer", "chef_equipe": "Viewer", "operateur": "Viewer"}


@app.get("/api/session/proxy", include_in_schema=False)
async def proxy_auth(response: Response, user: auth.User = Depends(auth.current_user)):
    """Contrôle d'accès de nginx pour Grafana (auth_request) : Grafana reçoit l'utilisateur MES connecté."""
    role = GRAFANA_ROLE.get(user.role, "Viewer")
    await grafana.synchroniser(user.username, role)   # aussi en accès libre (utilisateur « admin »)
    response.headers["X-Mes-User"] = user.username
    response.headers["X-Mes-Role"] = role
    return {"ok": True}


@app.get("/api/sante")
async def health():
    async with db.pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"ok": True, "version": app.version}
