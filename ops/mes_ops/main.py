"""Console d'exploitation du MES v3 : services Docker, historisation, architecture, simulation.

Service séparé de l'application (API, web, Grafana) pour pouvoir l'arrêter et la redémarrer :
il est joignable par l'application sous /exploitation/ et directement sur OPS_PORT.

Accès réservé au rôle admin. La session est le même jeton signé que l'application MES
(cookie mes_session, clé SECRET_KEY) ; un compte local admin peut aussi se connecter ici
quand l'application est arrêtée. Le pilotage Docker se limite aux conteneurs du projet
Compose (COMPOSE_PROJECT) et aux actions start, stop, restart et lecture des journaux :
jamais de suppression de conteneur, d'image ni de volume. Chaque action est journalisée
dans mes.audit_log.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger("ops")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

DATABASE_URL = os.environ["DATABASE_URL"]
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))
COOKIE = "mes_session"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false") == "true"
PROJECT = os.environ.get("COMPOSE_PROJECT", "mes")
DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
SIMULATOR_URL = os.environ.get("SIMULATOR_URL", "http://simulator:8081")
COLLECTOR_METRICS = os.environ.get("COLLECTOR_METRICS_URL", "http://collector:9108/metrics")
OPCUA_ENDPOINT = os.environ.get("OPCUA_ENDPOINT", "")
MES_VERSION = os.environ.get("MES_VERSION", "dev")
OPS_PORT = os.environ.get("OPS_PORT", "8090")
STATIC = Path(__file__).resolve().parent.parent / "static"
SAMPLE_S = 10
HISTORY = 360                      # 1 h d'échantillons CPU / mémoire

# Rôle et couche de chaque service. Couches : app (application, arrêtable d'un bloc),
# core (acquisition et stockage), mon (surveillance), task (tâche ponctuelle).
SERVICES = {
    "web": ("Interface web (nginx)", "app"),
    "api": ("API MES : écrans, événements, calculs", "app"),
    "grafana": ("Tableaux de bord", "app"),
    "collector": ("Collecteur OPC UA → historian", "core"),
    "db": ("Base PostgreSQL + TimescaleDB", "core"),
    "simulator": ("Automate S7-1500 simulé", "core"),
    "ops": ("Console d'exploitation", "core"),
    "migrate": ("Migrations de la base (au démarrage)", "task"),
    "prometheus": ("Métriques techniques", "mon"),
    "node-exporter": ("Métriques de l'hôte", "mon"),
    "process-exporter": ("Métriques des processus", "mon"),
    "postgres-exporter": ("Métriques de la base", "mon"),
    "docker-exporter": ("Métriques des conteneurs", "mon"),
}
APP_ORDER_START = ["api", "grafana", "web"]
APP_ORDER_STOP = ["web", "grafana", "api"]
NO_STOP = {"ops", "db"}            # arrêter la base ou la console se fait en ligne de commande (mesctl)
NO_ACTION = {"ops", "migrate"}

app = FastAPI(title="MES v3 · exploitation", docs_url=None, redoc_url=None)
pool: asyncpg.Pool | None = None
docker = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET), base_url="http://docker", timeout=30)
http = httpx.AsyncClient(timeout=5)
snapshot: dict = {"services": [], "at": 0}
history: dict[str, list[tuple[float, float | None, float | None]]] = {}
cache: dict[str, tuple[float, object]] = {}


# ----------------------------------------------------------------------------- session
def _sign(body: str) -> str:
    return hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()


def make_token(username: str, role: str, name: str | None) -> str:
    payload = json.dumps({"u": username, "r": role, "n": name, "exp": int(time.time()) + SESSION_HOURS * 3600}).encode()
    body = base64.urlsafe_b64encode(payload).decode()
    return f"{body}.{_sign(body)}"


def read_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body))
    except ValueError:
        return None
    return data if data.get("exp", 0) > time.time() else None


def check_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, h = stored.split("$")
    calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1)
    return hmac.compare_digest(calc, base64.b64decode(h))


def admin(request: Request) -> dict:
    user = read_token(request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Connexion requise")
    if user.get("r") != "admin":
        raise HTTPException(403, "Console réservée aux administrateurs")
    return user


async def audit(user: dict, action: str, entity_id: str | None, details: dict | None = None) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO mes.audit_log (actor, action, entity, entity_id, details) VALUES ($1, $2, 'exploitation', $3, $4)",
                user["u"], action, entity_id, json.dumps(details or {}))
    except (OSError, asyncpg.PostgresError) as e:
        log.warning("journal impossible : %s", e)


class Login(BaseModel):
    username: str
    password: str


@app.get("/api/session")
async def session(user: dict = Depends(admin)):
    return {"username": user["u"], "role": user["r"], "display_name": user.get("n")}


@app.post("/api/session")
async def login(body: Login, response: Response):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT username, role, display_name, password_hash FROM mes.app_user WHERE username = $1 AND active",
            body.username.strip())
    await asyncio.sleep(0.3)   # freine les essais en rafale
    if row is None or not check_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")
    if row["role"] != "admin":
        raise HTTPException(403, "Console réservée aux administrateurs")
    response.set_cookie(COOKIE, make_token(row["username"], row["role"], row["display_name"]), httponly=True,
                        samesite="lax", max_age=SESSION_HOURS * 3600, secure=COOKIE_SECURE, path="/")
    return {"username": row["username"], "role": row["role"], "display_name": row["display_name"]}


@app.delete("/api/session")
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# ----------------------------------------------------------------------------- Docker
def _since(started: str) -> float | None:
    if not started or started.startswith("0001"):
        return None
    s = started.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(s + "+00:00").timestamp()
    except ValueError:
        return None


def _cpu_mem(st: dict) -> tuple[float | None, float | None]:
    try:
        cpu, pre = st["cpu_stats"], st["precpu_stats"]
        dc = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        ds = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        n = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
        c = dc / ds * n * 100 if ds > 0 else 0.0
    except (KeyError, TypeError):
        c = None
    try:
        mem = st["memory_stats"]
        inactive = (mem.get("stats") or {}).get("inactive_file", 0)
        m = max(0, mem["usage"] - inactive)
    except (KeyError, TypeError):
        m = None
    return c, m


async def containers() -> list[dict]:
    flt = json.dumps({"label": [f"com.docker.compose.project={PROJECT}"]})
    r = await docker.get("/containers/json", params={"all": "1", "filters": flt})
    r.raise_for_status()
    return r.json()


async def _one(c: dict) -> dict:
    labels = c.get("Labels") or {}
    svc = labels.get("com.docker.compose.service", (c.get("Names") or ["?"])[0].lstrip("/"))
    info = (await docker.get(f"/containers/{c['Id']}/json")).json()
    st = info.get("State", {})
    cpu = mem = None
    if st.get("Running"):
        try:
            stats = (await docker.get(f"/containers/{c['Id']}/stats", params={"stream": "false"})).json()
            cpu, mem = _cpu_mem(stats)
        except (httpx.HTTPError, ValueError):
            pass
    role, layer = SERVICES.get(svc, ("", "mon"))
    image = c.get("Image", "")
    return {
        "service": svc, "id": c["Id"][:12], "container": (c.get("Names") or [""])[0].lstrip("/"),
        "role": role, "couche": layer, "image": image, "version": image.rsplit(":", 1)[-1] if ":" in image else "",
        "etat": st.get("Status", ""), "en_marche": bool(st.get("Running")),
        "sante": (st.get("Health") or {}).get("Status"), "code_sortie": st.get("ExitCode"),
        "depuis": _since(st.get("StartedAt", "")) if st.get("Running") else _since(st.get("FinishedAt", "")),
        "redemarrages": info.get("RestartCount", 0), "cpu": cpu, "memoire": mem,
        "arret_possible": svc not in NO_STOP and svc not in NO_ACTION,
        "action_possible": svc not in NO_ACTION,
    }


async def sample() -> None:
    try:
        items = await asyncio.gather(*(_one(c) for c in await containers()), return_exceptions=True)
    except httpx.HTTPError as e:
        log.warning("API Docker injoignable : %s", e)
        snapshot["erreur"] = f"API Docker injoignable : {e}"
        return
    services = [i for i in items if isinstance(i, dict)]
    now = time.time()
    for s in services:
        h = history.setdefault(s["service"], [])
        h.append((now, s["cpu"], s["memoire"]))
        del h[:-HISTORY]
        s["historique_cpu"] = [round(x[1], 2) if x[1] is not None else None for x in h[-60:]]
    order = {k: i for i, k in enumerate(SERVICES)}
    services.sort(key=lambda s: (order.get(s["service"], 99), s["service"]))
    snapshot.update(services=services, at=now, erreur=None)


async def sampler() -> None:
    while True:
        await sample()
        await asyncio.sleep(SAMPLE_S)


def find(service: str) -> dict:
    for s in snapshot["services"]:
        if s["service"] == service:
            return s
    raise HTTPException(404, f"service « {service} » introuvable dans le projet {PROJECT}")


async def act(service: str, action: str) -> None:
    s = find(service)
    if service in NO_ACTION or (action == "stop" and service in NO_STOP):
        raise HTTPException(403, f"action « {action} » interdite sur {service} depuis la console")
    r = await docker.post(f"/containers/{s['id']}/{action}", params={"t": "20"} if action != "start" else None, timeout=60)
    if r.status_code not in (204, 304):
        raise HTTPException(502, f"Docker a refusé {action} {service} : {r.text[:200]}")


@app.get("/api/services")
async def get_services(user: dict = Depends(admin)):
    if not snapshot["at"]:
        await sample()
    return snapshot


@app.post("/api/services/{service}/{action}")
async def service_action(service: str, action: str, user: dict = Depends(admin)):
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "action inconnue")
    await act(service, action)
    await audit(user, f"service_{action}", service)
    await sample()
    return find(service)


@app.post("/api/application/{action}")
async def application(action: str, user: dict = Depends(admin)):
    """Application = API, Grafana et interface web ; la base, le collecteur et la console restent en service."""
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "action inconnue")
    names = {s["service"] for s in snapshot["services"]}
    order = APP_ORDER_STOP if action == "stop" else APP_ORDER_START
    done = []
    for svc in [s for s in order if s in names]:
        if action == "restart":
            await act(svc, "restart")
        elif action == "stop" and find(svc)["en_marche"]:
            await act(svc, "stop")
        elif action == "start" and not find(svc)["en_marche"]:
            await act(svc, "start")
        done.append(svc)
    await audit(user, f"application_{action}", ",".join(done))
    await sample()
    return {"services": done}


def _demux(raw: bytes) -> str:
    """Flux de journaux Docker multiplexé (en-têtes de 8 octets) quand le conteneur n'a pas de TTY."""
    out, i = [], 0
    if raw[:1] in (b"\x00", b"\x01", b"\x02") and len(raw) >= 8:
        while i + 8 <= len(raw):
            n = int.from_bytes(raw[i + 4:i + 8], "big")
            out.append(raw[i + 8:i + 8 + n])
            i += 8 + n
        raw = b"".join(out)
    return raw.decode("utf-8", "replace")


@app.get("/api/services/{service}/journaux")
async def logs(service: str, lignes: int = 200, user: dict = Depends(admin)):
    s = find(service)
    r = await docker.get(f"/containers/{s['id']}/logs",
                         params={"stdout": "1", "stderr": "1", "tail": str(max(10, min(lignes, 2000))), "timestamps": "0"})
    text = _demux(r.content)
    return {"service": service, "lignes": [ln for ln in text.splitlines() if ln.strip()]}


# ----------------------------------------------------------------------------- base : historisation, stockage
KEY = "historisation"
# Périmètre d'un tag : son DB (premier segment du NodeId Siemens ns=3;s="DB"."Instance"."Membre"),
# sans le suffixe de découpage (DB_Process_03 -> DB_Process). Même règle que le collecteur.
SCOPE_SQL = r"""regexp_replace(coalesce(substring(t.node_id from 's="([^"]+)"'), split_part(t.name, '.', 1)), '_\d+$', '')"""


async def cached(key: str, ttl: float, fn):
    hit = cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = await fn()
    cache[key] = (time.time(), val)
    return val


async def _hist_setting(conn) -> dict:
    raw = await conn.fetchval("SELECT value FROM mes.setting WHERE key = $1", KEY)
    data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return {"global": data.get("global"), "db": data.get("db") or {}}


def _active(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    until = entry.get("jusqu_a")
    return not until or datetime.fromisoformat(until) > datetime.now(timezone.utc)


async def _rates() -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT {SCOPE_SQL} AS scope, count(*) AS n FROM historian.value v JOIN catalog.tag t ON t.id = v.tag_id
                WHERE v.time > now() - interval '1 minute' GROUP BY 1""")
        tags = await conn.fetch(
            f"SELECT {SCOPE_SQL} AS scope, count(*) AS n FROM catalog.tag t WHERE enabled AND historize GROUP BY 1 ORDER BY 1")
        lag = await conn.fetchval(
            "SELECT extract(epoch FROM now() - max(time)) FROM historian.value WHERE time > now() - interval '1 hour'")
    rate = {r["scope"]: r["n"] / 60 for r in rows}
    return {"scopes": [{"nom": r["scope"], "tags": r["n"], "points_s": round(rate.get(r["scope"], 0), 1)} for r in tags],
            "points_s": round(sum(rate.values()), 1), "tags": sum(r["n"] for r in tags),
            "retard_s": float(lag) if lag is not None else None}


@app.get("/api/historisation")
async def get_historisation(user: dict = Depends(admin)):
    async with pool.acquire() as conn:
        setting = await _hist_setting(conn)
    rates = await cached("rates", 10, _rates)
    g = setting["global"] if _active(setting["global"]) else None
    scopes = [{**s, "suspension": setting["db"].get(s["nom"]) if _active(setting["db"].get(s["nom"])) else None}
              for s in rates["scopes"]]
    return {"global": g, "scopes": scopes, "points_s": rates["points_s"], "tags": rates["tags"], "retard_s": rates["retard_s"]}


class HistBody(BaseModel):
    portee: str = "global"          # "global" ou un nom de DB
    actif: bool
    minutes: int | None = None      # durée de la suspension ; None = jusqu'à réactivation


@app.put("/api/historisation")
async def put_historisation(body: HistBody, user: dict = Depends(admin)):
    if body.portee != "global" and not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", body.portee):
        raise HTTPException(400, "périmètre invalide")
    now = datetime.now(timezone.utc)
    entry = None if body.actif else {
        "depuis": now.isoformat(), "par": user["u"],
        "jusqu_a": (now + timedelta(minutes=body.minutes)).isoformat() if body.minutes else None}
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO mes.setting (key, value) VALUES ($1, '{}') ON CONFLICT DO NOTHING", KEY)
            await conn.execute("SELECT 1 FROM mes.setting WHERE key = $1 FOR UPDATE", KEY)
            setting = await _hist_setting(conn)
            if body.portee == "global":
                setting["global"] = entry
            elif entry is None:
                setting["db"].pop(body.portee, None)
            else:
                setting["db"][body.portee] = entry
            setting["db"] = {k: v for k, v in setting["db"].items() if _active(v)}
            await conn.execute("UPDATE mes.setting SET value = $2::jsonb WHERE key = $1", KEY, json.dumps(setting))
    await audit(user, "historisation_reprise" if body.actif else "historisation_suspendue", body.portee,
                {"minutes": body.minutes})
    return await get_historisation(user)


async def _storage() -> dict:
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT hypertable_size('historian.value')")
        dbsize = await conn.fetchval("SELECT pg_database_size(current_database())")
        job = await conn.fetchrow(
            """SELECT next_start, config->>'drop_after' AS drop_after FROM timescaledb_information.jobs
               WHERE proc_name = 'policy_retention' AND hypertable_name = 'value' AND hypertable_schema = 'historian'""")
        purge = None
        if job:
            freed = await conn.fetchval(
                """SELECT coalesce(sum(d.total_bytes), 0) FROM timescaledb_information.chunks c
                   JOIN chunks_detailed_size('historian.value') d ON d.chunk_schema = c.chunk_schema AND d.chunk_name = c.chunk_name
                   WHERE c.range_end <= $1::timestamptz - $2::text::interval""", job["next_start"], job["drop_after"])
            purge = {"prochaine": job["next_start"].isoformat() if job["next_start"] else None,
                     "conservation": job["drop_after"], "octets": int(freed or 0)}
    st = os.statvfs("/")
    return {"historian_brut": int(raw or 0), "base": int(dbsize or 0), "purge": purge,
            "disque": {"total": st.f_blocks * st.f_frsize, "libre": st.f_bavail * st.f_frsize}}


@app.get("/api/stockage")
async def storage(user: dict = Depends(admin)):
    return await cached("storage", 30, _storage)


# ----------------------------------------------------------------------------- architecture
async def _collector() -> dict | None:
    try:
        text = (await http.get(COLLECTOR_METRICS, timeout=3)).text
    except httpx.HTTPError:
        return None
    vals: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("mes_collector_") and " " in line and "{" not in line:
            k, v = line.rsplit(" ", 1)
            try:
                vals[k] = float(v)
            except ValueError:
                pass
    g = vals.get
    return {"opcua": g("mes_collector_opcua_up") == 1, "base": g("mes_collector_db_up") == 1,
            "tags": g("mes_collector_tags"), "abonnements": g("mes_collector_subscriptions"),
            "file": g("mes_collector_queue"), "tampon": g("mes_collector_spool_files"),
            "recus": g("mes_collector_notifications_total"), "ecrits": g("mes_collector_rows_written_total"),
            "suspendus": g("mes_collector_rows_suspended_total"),
            "historisation_suspendue": g("mes_collector_historisation_suspended") == 1}


@app.get("/api/etat")
async def etat(user: dict = Depends(admin)):
    if not snapshot["at"]:
        await sample()
    try:
        rates = await cached("rates", 10, _rates)
    except (OSError, asyncpg.PostgresError):
        rates = None
    return {"version": MES_VERSION, "projet": PROJECT, "opcua": OPCUA_ENDPOINT, "port_console": OPS_PORT,
            "services": snapshot["services"], "collecte": rates, "collecteur": await _collector(),
            "docker_erreur": snapshot.get("erreur")}


@app.get("/api/journal")
async def journal(limite: int = 40, user: dict = Depends(admin)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT at, actor, action, entity, entity_id, details FROM mes.audit_log ORDER BY at DESC LIMIT $1",
            max(1, min(limite, 200)))
    return [{"at": r["at"].isoformat(), "qui": r["actor"], "action": r["action"], "objet": r["entity"],
             "id": r["entity_id"], "details": json.loads(r["details"]) if isinstance(r["details"], str) else r["details"]}
            for r in rows]


# ----------------------------------------------------------------------------- simulation
async def sim(method: str, path: str, body: dict | None = None):
    try:
        r = await http.request(method, SIMULATOR_URL + path, json=body, timeout=5)
    except httpx.HTTPError:
        raise HTTPException(503, "simulateur injoignable (profil sim arrêté ou absent)") from None
    data = r.json()
    if r.status_code >= 400:
        raise HTTPException(r.status_code, data.get("detail", "erreur du simulateur"))
    return data


@app.get("/api/simulation")
async def sim_state(user: dict = Depends(admin)):
    return await sim("GET", "/etat")


@app.put("/api/simulation/regles")
async def sim_rules(request: Request, user: dict = Depends(admin)):
    body = await request.json()
    out = await sim("PUT", "/regles", body)
    await audit(user, "simulation_regles", None, body)
    return out


@app.post("/api/simulation/evenements")
async def sim_event(request: Request, user: dict = Depends(admin)):
    body = await request.json()
    out = await sim("POST", "/evenements", body)
    await audit(user, "simulation_evenement", body.get("type"), body.get("params"))
    return out


@app.delete("/api/simulation/evenements/{eid}")
async def sim_event_stop(eid: int, user: dict = Depends(admin)):
    out = await sim("DELETE", f"/evenements/{eid}")
    await audit(user, "simulation_evenement_arrete", str(eid))
    return out


@app.get("/api/tags")
async def tags(q: str = "", limite: int = 30, user: dict = Depends(admin)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT t.id, t.name, t.unit, lv.value, lv.time, lv.quality FROM catalog.tag t
               LEFT JOIN LATERAL (SELECT value, time, quality FROM historian.value WHERE tag_id = t.id
                                  ORDER BY time DESC LIMIT 1) lv ON true
               WHERE t.enabled AND (t.name ILIKE $1 OR t.node_id ILIKE $1) ORDER BY t.name LIMIT $2""",
            "%" + q.replace("%", r"\%").replace("_", r"\_") + "%", max(1, min(limite, 100)))
    return [{"id": r["id"], "nom": r["name"], "unite": r["unit"], "valeur": r["value"],
             "heure": r["time"].isoformat() if r["time"] else None, "qualite": r["quality"]} for r in rows]


@app.get("/api/tags/{tag_id}/tendance")
async def trend(tag_id: int, minutes: int = 10, user: dict = Depends(admin)):
    async with pool.acquire() as conn:
        name = await conn.fetchval("SELECT name FROM catalog.tag WHERE id = $1", tag_id)
        if name is None:
            raise HTTPException(404, "tag inconnu")
        rows = await conn.fetch(
            """SELECT time, value, quality FROM historian.value WHERE tag_id = $1
               AND time > now() - make_interval(mins => $2) ORDER BY time""", tag_id, max(1, min(minutes, 540)))
        before = await conn.fetchval("SELECT historian.value_at($1, now() - make_interval(mins => $2))",
                                     tag_id, max(1, min(minutes, 540)))
    return {"id": tag_id, "nom": name, "avant": before,
            "points": [[r["time"].isoformat(), r["value"], r["quality"]] for r in rows]}


# ----------------------------------------------------------------------------- cycle de vie, page
@app.on_event("startup")
async def startup():
    global pool
    for attempt in range(30):
        try:
            pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
            break
        except (OSError, asyncpg.PostgresError) as e:
            log.info("base pas encore prête (%s), nouvel essai", e)
            await asyncio.sleep(2)
    asyncio.create_task(sampler())


@app.get("/api/sante")
async def health():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")
