"""Agent d'exploitation du MES v3 : le serveur de la console /systeme.

- état des services (conteneurs Docker du projet), de l'hôte et des flux de données ;
- démarrer, arrêter, redémarrer un service ou l'application, lire ses journaux ;
- relais vers le contrôle du collecteur (historisation) et du simulateur (règles, scénarios) ;
- journal des opérations (qui a fait quoi).

Il tourne à part de l'API : la console reste disponible quand l'application est arrêtée.
Bibliothèque standard Python seulement. Sécurité :
- session MES vérifiée ici (même cookie signé que l'API, clé SECRET_KEY) ;
- lecture pour toute session, actions réservées au rôle admin ;
- liste fermée d'actions Docker (start, stop, restart, logs) sur les seuls services du projet ;
  aucune suppression de volume ; seul conteneur créé : « mes-maj », pour les opérations de version
  (mise à jour, retour arrière, sauvegarde), depuis l'image mes-ops d'un numéro de version vérifié ;
- base de données : uniquement les fonctions historian.stockage_etat() et historian.purger(), appelées
  par psql dans le conteneur de la base, avec des paramètres vérifiés ici (aucun SQL libre).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
PORT = int(os.environ.get("PORT", "9200"))
PROJECT = os.environ.get("COMPOSE_PROJECT", "mes")
SECRET = os.environ.get("SECRET_KEY", "")
DATA = Path(os.environ.get("OPS_DATA", "/data"))
COLLECTOR_URL = os.environ.get("COLLECTOR_CONTROL_URL", "http://collector:9109")
SIMULATOR_URL = os.environ.get("SIMULATOR_CONTROL_URL", "http://simulator:8090")
API_METRICS_URL = os.environ.get("API_METRICS_URL", "http://api:8000/metrics")
COOKIE = "mes_session"
# Accès sans identifiant (poste de développement ou démonstration) : chaque visiteur est administrateur.
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "false").lower() in ("1", "true", "oui", "yes")

# Services connus : libellé, couche, et ce que la console a le droit d'en faire.
# web et l'agent servent la console elle-même : ni arrêt ni redémarrage depuis la console.
SERVICES = {
    "simulator": ("Simulateur S7-1500", "Acquisition", "Automate simulé, serveur OPC UA"),
    "collector": ("Collecteur OPC UA", "Acquisition", "Abonnements OPC UA, écriture dans l'historian"),
    "db": ("Base TimescaleDB", "Stockage", "Historian et données MES"),
    "migrate": ("Migrations", "Stockage", "Mise à jour du schéma au démarrage"),
    "api": ("API MES", "Application", "Événements, KPI, recettes, planning"),
    "web": ("Web (nginx)", "Application", "Point d'entrée, interface"),
    "grafana": ("Grafana", "Application", "Tableaux de bord"),
    "ops": ("Agent d'exploitation", "Application", "Console /systeme"),
    "prometheus": ("Prometheus", "Observabilité", "Métriques techniques"),
    "node-exporter": ("node-exporter", "Observabilité", "Métriques de l'hôte"),
    "docker-exporter": ("docker-exporter", "Observabilité", "Métriques des conteneurs"),
    "process-exporter": ("process-exporter", "Observabilité", "Métriques des processus"),
    "postgres-exporter": ("postgres-exporter", "Observabilité", "Métriques de la base"),
}
FIXED = {"web", "ops", "migrate"}           # pas d'action depuis la console
APPLICATION = ["collector", "api", "grafana"]  # « l'applicatif » : la base, le web et la console restent en marche
ACTIONS = {"start", "stop", "restart"}


# ----------------------------------------------------------------------------- session MES
def read_session(cookie_header: str | None) -> dict | None:
    """Vérifie le cookie de session signé par l'API (même format que mes_api.auth.make_token)."""
    if AUTH_DISABLED:
        return {"username": "admin", "role": "admin", "display_name": "Administrateur (accès libre)", "auth": False}
    if not cookie_header or not SECRET:
        return None
    token = None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            token = v.strip('"')
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return {"username": data.get("u"), "role": data.get("r"), "display_name": data.get("n")}


# ----------------------------------------------------------------------------- API Docker
class UnixConnection(http.client.HTTPConnection):
    def __init__(self, timeout: float = 10):
        super().__init__("localhost", timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET)


def docker(method: str, path: str, timeout: float = 10, raw: bool = False, body: dict | None = None):
    conn = UnixConnection(timeout)
    try:
        if body is None:
            conn.request(method, path)
        else:
            conn.request(method, path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        r = conn.getresponse()
        body = r.read()
        if r.status >= 400:
            try:
                msg = json.loads(body).get("message", "")
            except (ValueError, AttributeError):
                msg = body[:200].decode(errors="replace")
            raise RuntimeError(f"Docker {r.status} : {msg}")
        if raw:
            return body
        return json.loads(body) if body else None
    finally:
        conn.close()


def demux(raw: bytes) -> list[str]:
    """Journaux Docker sans TTY : trames de 8 octets d'en-tête (flux, taille) puis le texte."""
    out, i = [], 0
    if raw[:1] in (b"\x00", b"\x01", b"\x02") and len(raw) >= 8:
        chunks = []
        while i + 8 <= len(raw):
            size = int.from_bytes(raw[i + 4:i + 8], "big")
            chunks.append(raw[i + 8:i + 8 + size])
            i += 8 + size
        raw = b"".join(chunks)
    for line in raw.decode(errors="replace").splitlines():
        out.append(line)
    return out


def split_streams(raw: bytes) -> tuple[str, str]:
    """Sortie d'un exec Docker sans TTY : (stdout, stderr)."""
    out, err, i = [], [], 0
    while i + 8 <= len(raw):
        size = int.from_bytes(raw[i + 4:i + 8], "big")
        (err if raw[i] == 2 else out).append(raw[i + 8:i + 8 + size])
        i += 8 + size
    return b"".join(out).decode(errors="replace"), b"".join(err).decode(errors="replace")


def parse_time(s: str) -> float:
    if not s or s.startswith("0001"):
        return 0.0
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(s + "+00:00").timestamp()
    except ValueError:
        return 0.0


# ----------------------------------------------------------------------------- relevés en tâche de fond
class Sampler:
    """Relève toutes les 5 s l'état des conteneurs, l'hôte et les débits ; la console lit ce cache."""

    def __init__(self):
        self.lock = threading.Lock()
        self.containers: dict[str, dict] = {}
        self.host: dict = {}
        self.flux: dict = {}
        self.collector: dict | None = None
        self.simulator: dict | None = None
        self.docker_ok = False
        self.volumes: dict = {"at": 0, "items": []}
        self._prev_cpu: dict[str, tuple[float, float]] = {}
        self._prev_host: tuple[float, float] | None = None
        self._prev_api: tuple[float, float] | None = None
        self.history: dict[str, list] = {}   # séries courtes pour les mini-courbes (15 min)

    # -- conteneurs
    def sample_containers(self) -> None:
        try:
            items = docker("GET", "/containers/json?all=true&filters=" +
                           urllib.request.quote(json.dumps({"label": [f"com.docker.compose.project={PROJECT}"]})))
            self.docker_ok = True
        except Exception as e:  # noqa: BLE001
            self.docker_ok = False
            print(f"API Docker injoignable : {e}", flush=True)
            return
        out = {}
        for c in items:
            labels = c.get("Labels") or {}
            svc = labels.get("com.docker.compose.service", (c.get("Names") or ["?"])[0].lstrip("/"))
            try:
                info = docker("GET", f"/containers/{c['Id']}/json")
            except Exception:  # noqa: BLE001
                continue
            st = info.get("State", {})
            d = {"service": svc, "id": c["Id"][:12], "container": (c.get("Names") or [""])[0].lstrip("/"),
                 "image": c.get("Image", ""), "state": st.get("Status", ""), "running": bool(st.get("Running")),
                 "health": (st.get("Health") or {}).get("Status"), "exit_code": st.get("ExitCode"),
                 "started_at": parse_time(st.get("StartedAt", "")), "finished_at": parse_time(st.get("FinishedAt", "")),
                 "restarts": info.get("RestartCount", 0), "cpu_pct": None, "mem_bytes": None}
            if d["running"]:
                try:
                    s = docker("GET", f"/containers/{c['Id']}/stats?stream=false&one-shot=true", timeout=8)
                    cpu = s.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0) / 1e9
                    now = time.monotonic()
                    prev = self._prev_cpu.get(svc)
                    if prev and now > prev[1]:
                        d["cpu_pct"] = round(max(0.0, (cpu - prev[0]) / (now - prev[1]) * 100), 1)
                    self._prev_cpu[svc] = (cpu, now)
                    mem = s.get("memory_stats", {})
                    if "usage" in mem:
                        stats = mem.get("stats", {})
                        d["mem_bytes"] = max(mem["usage"] - stats.get("inactive_file", stats.get("total_inactive_file", 0)), 0)
                except Exception:  # noqa: BLE001
                    pass
            else:
                self._prev_cpu.pop(svc, None)
            out[svc] = d
            self.push(f"cpu:{svc}", d["cpu_pct"] or 0)
        with self.lock:
            self.containers = out

    # -- hôte (vu depuis le conteneur : /proc et le disque de Docker)
    def sample_host(self) -> None:
        h: dict = {}
        try:
            with open("/proc/stat") as f:
                parts = [float(x) for x in f.readline().split()[1:]]
            idle, total = parts[3] + parts[4], sum(parts)
            if self._prev_host and total > self._prev_host[1]:
                h["cpu_pct"] = round(100 * (1 - (idle - self._prev_host[0]) / (total - self._prev_host[1])), 1)
            self._prev_host = (idle, total)
            h["cpus"] = os.cpu_count()
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    mem[k] = int(v.split()[0]) * 1024
            h["mem_total"] = mem.get("MemTotal")
            h["mem_used"] = mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)
            with open("/proc/loadavg") as f:
                h["load1"] = float(f.read().split()[0])
            sv = os.statvfs("/data")
            h["disk_total"] = sv.f_blocks * sv.f_frsize
            h["disk_used"] = (sv.f_blocks - sv.f_bfree) * sv.f_frsize
            h["disk_pct"] = round(100 * h["disk_used"] / h["disk_total"], 1) if h["disk_total"] else None
        except OSError as e:
            print(f"relevé de l'hôte incomplet : {e}", flush=True)
        self.push("host_cpu", h.get("cpu_pct") or 0)
        with self.lock:
            self.host = h

    # -- collecteur, simulateur, API
    def sample_flux(self) -> None:
        col = http_json("GET", COLLECTOR_URL + "/etat", timeout=2)
        sim = http_json("GET", SIMULATOR_URL + "/etat", timeout=2)
        req_s = None
        try:
            with urllib.request.urlopen(API_METRICS_URL, timeout=2) as r:
                total = sum(float(line.rsplit(" ", 1)[1]) for line in r.read().decode().splitlines()
                            if line.startswith("mes_api_requests_total{"))
            now = time.monotonic()
            if self._prev_api and now > self._prev_api[1]:
                req_s = round(max(0.0, (total - self._prev_api[0]) / (now - self._prev_api[1])), 1)
            self._prev_api = (total, now)
        except (OSError, ValueError, IndexError):
            self._prev_api = None
        flux = {"opc_vals_s": col.get("notifications_s") if col else None,
                "pts_s": col.get("points_s") if col else None,
                "sim_changes_s": sim.get("changements_s") if sim else None,
                "req_s": req_s}
        for k in ("pts_s", "opc_vals_s", "req_s"):
            self.push(k, flux[k] or 0)
        with self.lock:
            self.collector, self.simulator, self.flux = col, sim, flux

    def sample_volumes(self) -> None:
        """Taille des volumes Docker (lent : toutes les 5 minutes)."""
        try:
            df = docker("GET", "/system/df?type=volume", timeout=60)
        except Exception as e:  # noqa: BLE001
            print(f"taille des volumes indisponible : {e}", flush=True)
            return
        items = []
        for v in df.get("Volumes") or []:
            lab = v.get("Labels") or {}
            if lab.get("com.docker.compose.project") != PROJECT:
                continue
            items.append({"name": lab.get("com.docker.compose.volume", v.get("Name")),
                          "bytes": (v.get("UsageData") or {}).get("Size", -1)})
        items.sort(key=lambda x: -x["bytes"])
        with self.lock:
            self.volumes = {"at": time.time(), "items": items}

    def push(self, key: str, value: float) -> None:
        h = self.history.setdefault(key, [])
        h.append(round(float(value), 2))
        del h[:-180]   # 180 relevés de 5 s = 15 min

    def loop(self) -> None:
        last_df = 0.0
        while True:
            t0 = time.monotonic()
            for fn in (self.sample_containers, self.sample_host, self.sample_flux):
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    print(f"relevé en erreur ({fn.__name__}) : {e}", flush=True)
            if time.monotonic() - last_df > 300:
                last_df = time.monotonic()
                threading.Thread(target=self.sample_volumes, daemon=True).start()
            time.sleep(max(0.5, 5 - (time.monotonic() - t0)))


SAMPLER = Sampler()


def http_json(method: str, url: str, body: dict | None = None, timeout: float = 5):
    """Appel JSON vers le collecteur ou le simulateur ; None s'il est injoignable."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return {"erreur": json.loads(e.read()).get("erreur", str(e)), "status": e.code}
        except (ValueError, AttributeError):
            return {"erreur": str(e), "status": e.code}
    except (OSError, ValueError):
        return None


SIM_ID = r"([A-Za-z0-9_-]{1,40})"


def sim_route(method: str, path: str, body: dict):
    """Actions de scénario relayées au simulateur : (chemin côté simulateur, libellé du journal) ou None."""
    sub = path.removeprefix("/api/ops/simulation")
    if sub == path:
        return None
    nom = (body or {}).get("nom") or ""
    if method == "POST" and sub == "/apercu":
        return sub, None
    if method == "POST" and sub == "/scenarios-arreter":
        return sub, lambda r: "Simulation : tous les scénarios arrêtés"
    m = re.fullmatch(r"/groupes/" + SIM_ID, sub)
    if m and method == "PUT":
        return sub, lambda r: f"Simulation : groupe « {r.get('nom', nom)} » enregistré"
    if m and method == "DELETE":
        return sub, lambda r: f"Simulation : groupe {m.group(1)} supprimé"
    m = re.fullmatch(r"/scenarios/" + SIM_ID, sub)
    if m and method == "PUT":
        return sub, lambda r: f"Simulation : scénario « {r.get('nom', nom)} » enregistré"
    if m and method == "DELETE":
        return sub, lambda r: f"Simulation : scénario {m.group(1)} supprimé"
    m = re.fullmatch(r"/scenarios/" + SIM_ID + r"/(lancer|arreter)", sub)
    if m and method == "POST":
        verb = "lancé" if m.group(2) == "lancer" else "arrêté"
        return sub, lambda r: f"Scénario « {r.get('nom', m.group(1))} » {verb}"
    return None


# ----------------------------------------------------------------------------- journal des opérations
JOURNAL_LOCK = threading.Lock()


def journal_add(who: str, action: str, ok: bool = True, detail: str = "") -> dict:
    e = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "who": who, "action": action,
         "ok": ok, "detail": detail}
    with JOURNAL_LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        path = DATA / "journal.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        if path.stat().st_size > 2_000_000:   # garde les 2 000 dernières lignes
            lines = path.read_text(encoding="utf-8").splitlines()[-2000:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"journal : {who} {action} {'ok' if ok else 'ÉCHEC'} {detail}", flush=True)
    return e


def journal_read(n: int = 100) -> list[dict]:
    path = DATA / "journal.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


# ----------------------------------------------------------------------------- actions
def container_id(svc: str) -> str:
    with SAMPLER.lock:
        c = SAMPLER.containers.get(svc)
    if c is None:
        SAMPLER.sample_containers()
        with SAMPLER.lock:
            c = SAMPLER.containers.get(svc)
    if c is None:
        raise LookupError(f"service {svc} absent (profil non activé ?)")
    return c["id"]


def service_action(svc: str, action: str) -> None:
    if svc not in SERVICES or svc in FIXED:
        raise PermissionError(f"action interdite sur {svc}")
    if action not in ACTIONS:
        raise PermissionError(f"action inconnue : {action}")
    cid = container_id(svc)
    docker("POST", f"/containers/{cid}/{action}?t=15", timeout=60)


# ----------------------------------------------------------------------------- stockage de l'historian
JEUX = {"brut": "Données brutes", "1min": "Agrégats 1 min", "1h": "Agrégats 1 h"}


def db_json(sql: str, timeout: float = 600):
    """Exécute une requête renvoyant une valeur JSON, par psql dans le conteneur de la base."""
    cid = container_id("db")
    ex = docker("POST", f"/containers/{cid}/exec", body={
        "Cmd": ["psql", "-U", "mes", "-d", "mes", "-XAtq", "-v", "ON_ERROR_STOP=1", "-c", sql],
        "AttachStdout": True, "AttachStderr": True})
    raw = docker("POST", f"/exec/{ex['Id']}/start", timeout=timeout, raw=True, body={"Detach": False, "Tty": False})
    out, err = split_streams(raw)
    code = (docker("GET", f"/exec/{ex['Id']}/json") or {}).get("ExitCode")
    if code != 0:
        msg = next((ln.split("ERROR:", 1)[1].strip() for ln in err.splitlines() if "ERROR:" in ln), err.strip() or out.strip())
        raise RuntimeError(msg or f"psql : code {code}")
    return json.loads(out.strip() or "null")


def purge_sql(jeu: str, garder_h) -> str:
    """Requête de purge, construite uniquement à partir de valeurs vérifiées."""
    if jeu not in JEUX:
        raise ValueError(f"jeu de données inconnu : {jeu}")
    if isinstance(garder_h, bool) or not isinstance(garder_h, int) or not 1 <= garder_h <= 24 * 365 * 10:
        raise ValueError("durée à conserver invalide (heures entières, de 1 à 87 600)")
    return f"SELECT historian.purger('{jeu}', make_interval(hours => {garder_h}))::text"


def describe_purge(jeu: str, garder_h: int, r: dict) -> str:
    garde = f"{garder_h // 24} j" if garder_h % 24 == 0 else f"{garder_h} h"
    return f"Purge : {JEUX[jeu].lower()} de plus de {garde} ({r.get('chunks', 0)} blocs, {r.get('liberes_octets', 0) / 1e6:.0f} Mo libérés)"


def overview(user: dict) -> dict:
    with SAMPLER.lock:
        containers = dict(SAMPLER.containers)
        host, flux = dict(SAMPLER.host), dict(SAMPLER.flux)
        col, sim, vols = SAMPLER.collector, SAMPLER.simulator, SAMPLER.volumes
    services = []
    for svc, (label, layer, role) in SERVICES.items():
        c = containers.get(svc)
        if c is None:
            continue
        services.append({**c, "label": label, "couche": layer, "role": role,
                         "pilotable": svc not in FIXED, "application": svc in APPLICATION,
                         "cpu_hist": SAMPLER.history.get(f"cpu:{svc}", [])[-60:]})
    for svc, c in containers.items():   # services inconnus du projet : affichés sans action
        if svc not in SERVICES:
            services.append({**c, "label": svc, "couche": "Autres", "role": "", "pilotable": False, "application": False,
                             "cpu_hist": []})
    if not (containers.get("collector") or {}).get("running"):
        col = None
    if not (containers.get("simulator") or {}).get("running"):
        sim = None
    app_running = [containers[s]["running"] for s in APPLICATION if s in containers]
    app = "en_service" if app_running and all(app_running) else "arretee" if app_running and not any(app_running) else "partielle"
    return {
        "user": {**user, "admin": user.get("role") == "admin"},
        "version": os.environ.get("MES_VERSION", "dev"),
        "docker": SAMPLER.docker_ok, "application": app, "services": services, "hote": host, "flux": flux,
        "collecteur": col, "simulateur": sim, "volumes": vols,
        "conservation": {"brut": os.environ.get("HISTORIAN_RETENTION", "1 year"),
                         "agregats_1min": os.environ.get("AGGREGATE_1MIN_RETENTION", "1 year"),
                         "agregats_1h": os.environ.get("AGGREGATE_1H_RETENTION", "3 years")},
        "historique": {k: SAMPLER.history.get(k, [])[-60:] for k in ("pts_s", "opc_vals_s", "req_s", "host_cpu")},
        "at": time.time(),
    }


# ----------------------------------------------------------------------------- versions et mises à jour
# Les opérations de version (mise à jour, retour arrière, sauvegarde) tournent dans un conteneur à part,
# « mes-maj », créé depuis l'image mes-ops de la version visée : il lance maj.sh puis mesctl, survit au
# remplacement de cette console pendant la mise à jour, et ses journaux donnent la progression.
REGISTRY = os.environ.get("MES_REGISTRY", "ghcr.io/loutre73")
RELEASES_URL = os.environ.get("MES_RELEASES_URL", "")
SITE = Path(os.environ.get("MES_SITE", "/mes"))           # dossier d'installation, monté en lecture
JOB_NAME = "mes-maj"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9][a-z0-9.]{0,20})?$")
ETAPES = {
    "update": ["Préparation", "Fichiers d'installation", "Sauvegarde de la base", "Récupération des images",
               "Migration de la base", "Redémarrage des services", "Contrôle de santé"],
    "rollback": ["Préparation", "Fichiers d'installation", "Restauration de la sauvegarde",
                 "Redémarrage des services", "Contrôle de santé"],
    "backup": ["Préparation", "Sauvegarde de la base"],
}
ACTION_LABEL = {"update": "Mise à jour", "rollback": "Retour à la version précédente", "backup": "Sauvegarde"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def version_key(v: str):
    """Tri des versions : 0.10.0 après 0.9.0, une pré-version (0.2.0-essai) avant la version finale."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$", v or "")
    if not m:
        return (-1, -1, -1, 0, v)
    return (int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1, m[4] or "")


def site_env() -> dict:
    out = {}
    try:
        for line in (SITE / "deploy" / "site.env").read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and re.fullmatch(r"[A-Z_0-9]+", k):
                out[k] = v
    except OSError:
        pass
    return out


def read_state(name: str) -> str | None:
    try:
        return (SITE / "deploy" / ".mesctl" / name).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def site_history(n: int = 50) -> list[dict]:
    try:
        lines = (SITE / "deploy" / ".mesctl" / "historique.tsv").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-n:]):
        p = line.split("\t")
        if len(p) >= 6:
            out.append({"t": p[0], "action": p[1], "de": p[2], "vers": p[3], "resultat": p[4], "qui": p[5]})
    return out


def backups(env: dict) -> dict:
    rel = env.get("BACKUP_DIR") or "./backups"
    if rel.startswith("/"):
        return {"dossier": rel, "visible": False, "fichiers": []}
    d = SITE / "deploy" / rel
    files = []
    try:
        for f in d.glob("mes-*.dump"):
            st = f.stat()
            m = re.match(r"mes-(\d{8}-\d{6})-(.+)\.dump$", f.name)
            files.append({"nom": f.name, "octets": st.st_size, "t": st.st_mtime, "version": m[2] if m else None})
    except OSError:
        pass
    files.sort(key=lambda x: -x["t"])
    return {"dossier": rel, "visible": True, "fichiers": files}


def host_dir() -> str:
    """Chemin, sur l'hôte, du dossier d'installation (parent du dossier deploy de docker-compose.yml)."""
    if os.environ.get("MES_DIR_HOTE"):
        return os.environ["MES_DIR_HOTE"]
    me = docker("GET", f"/containers/{socket.gethostname()}/json")
    wd = (me.get("Config", {}).get("Labels") or {}).get("com.docker.compose.project.working_dir")
    if not wd:
        raise RuntimeError("dossier d'installation inconnu (lancez la pile avec mesctl)")
    return str(Path(wd).parent)


def local_versions() -> dict[str, dict]:
    """Versions dont l'image mes-ops est sur ce serveur (téléchargée, archive chargée ou version d'essai)."""
    out = {}
    try:
        imgs = docker("GET", "/images/json?filters=" + urllib.parse.quote(json.dumps({"reference": [f"{REGISTRY}/mes-ops"]})))
    except Exception:  # noqa: BLE001
        return out
    for im in imgs or []:
        for ref in im.get("RepoTags") or []:
            tag = ref.rsplit(":", 1)[-1]
            if VERSION_RE.match(tag):
                out[tag] = {"version": tag, "t": im.get("Created"), "octets": im.get("Size")}
    return out


RELEASES_CACHE: dict = {"at": 0, "data": None, "erreur": None}


def online_versions() -> tuple[list[dict], str | None]:
    """Versions publiées (API des versions GitHub), gardées 10 minutes ; aucune recherche si l'adresse est vide."""
    if not RELEASES_URL:
        return [], None
    if time.time() - RELEASES_CACHE["at"] < 600:
        return RELEASES_CACHE["data"] or [], RELEASES_CACHE["erreur"]
    data, err = [], None
    try:
        req = urllib.request.Request(RELEASES_URL + "?per_page=20", headers={"Accept": "application/vnd.github+json", "User-Agent": "mes-ops"})
        with urllib.request.urlopen(req, timeout=6) as r:
            for rel in json.loads(r.read()):
                tag = (rel.get("tag_name") or "").removeprefix("v")
                if rel.get("draft") or not VERSION_RE.match(tag):
                    continue
                archive = next((a.get("browser_download_url") for a in rel.get("assets") or []
                                if a.get("name", "").startswith("mes-images-")), None)
                data.append({"version": tag, "t": rel.get("published_at"), "titre": rel.get("name") or "",
                             "notes": (rel.get("body") or "")[:2000], "page": rel.get("html_url"), "archive": archive,
                             "pre": bool(rel.get("prerelease"))})
    except Exception as e:  # noqa: BLE001
        err = f"liste des versions en ligne indisponible ({e})"
    RELEASES_CACHE.update(at=time.time(), data=data, erreur=err)
    return data, err


def job_steps(action: str, lines: list[str], running: bool, code: int | None) -> list[dict]:
    """Étapes prévues de l'opération, avec leur état d'après les lignes « ==> » de mesctl et maj.sh."""
    seen = [ln[4:].strip() for ln in lines if ln.startswith("==> ")]
    names = list(ETAPES.get(action, []))
    for s in seen:   # étapes imprévues (retour arrière automatique…)
        if s not in names:
            names.append(s)
    last = max((names.index(s) for s in seen), default=0)
    out = []
    for i, n in enumerate(names):
        if i < last or (i == last and not running and code == 0):
            st = "fait"
        elif i == last:
            st = "en_cours" if running else "echec"
        else:
            st = "a_faire" if running or code != 0 else "fait"
        out.append({"nom": n, "etat": st})
    return out


class Jobs:
    """Une seule opération de version à la fois ; « Préparation » (téléchargement de l'outil) tourne ici."""

    def __init__(self):
        self.lock = threading.Lock()
        self.prep: dict | None = None   # {action, version, qui, debut, erreur}

    def inspect(self) -> dict | None:
        try:
            return docker("GET", f"/containers/{JOB_NAME}/json")
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def state(self) -> dict | None:
        with self.lock:
            prep = dict(self.prep) if self.prep else None
        if prep and not prep.get("fini"):
            return {"action": prep["action"], "version": prep.get("version"), "qui": prep["qui"], "debut": prep["debut"],
                    "en_cours": True, "code": None, "fin": None, "lignes": [prep.get("message", "")],
                    "etapes": [{"nom": n, "etat": "en_cours" if i == 0 else "a_faire"} for i, n in enumerate(ETAPES[prep["action"]])]}
        if prep and prep.get("erreur"):
            return {"action": prep["action"], "version": prep.get("version"), "qui": prep["qui"], "debut": prep["debut"],
                    "en_cours": False, "code": 1, "fin": prep.get("fin"), "lignes": [prep["erreur"]],
                    "etapes": [{"nom": "Préparation", "etat": "echec"}]}
        info = self.inspect()
        if info is None:
            return None
        lab = info.get("Config", {}).get("Labels") or {}
        st = info.get("State", {})
        running = bool(st.get("Running"))
        code = None if running else st.get("ExitCode")
        try:
            raw = docker("GET", f"/containers/{JOB_NAME}/logs?stdout=1&stderr=1&tail=600", raw=True)
            lines = [ANSI.sub("", ln) for ln in demux(raw)]
        except Exception:  # noqa: BLE001
            lines = []
        action = lab.get("mes.maj.action", "")
        self.journalise(info, action, lab, code)
        return {"action": action, "version": lab.get("mes.maj.version") or None, "de": lab.get("mes.maj.de"),
                "qui": lab.get("mes.maj.qui"), "debut": parse_time(st.get("StartedAt", "")),
                "fin": None if running else parse_time(st.get("FinishedAt", "")), "en_cours": running, "code": code,
                "lignes": lines[-300:], "etapes": job_steps(action, lines, running, code)}

    def journalise(self, info: dict, action: str, lab: dict, code) -> None:
        """Fin d'opération inscrite une fois au journal (même si cette console a été remplacée entre-temps)."""
        if code is None:
            return
        mark = DATA / "maj-journalisee"
        try:
            if mark.exists() and mark.read_text() == info["Id"]:
                return
            DATA.mkdir(parents=True, exist_ok=True)
            mark.write_text(info["Id"])
        except OSError:
            return
        v = lab.get("mes.maj.version")
        label = ACTION_LABEL.get(action, action) + (f" vers {v}" if v and action == "update" else "")
        journal_add(lab.get("mes.maj.qui", "?"), label, code == 0,
                    "" if code == 0 else f"code {code}")

    def start(self, action: str, who: str, version: str | None = None) -> None:
        if action not in ETAPES:
            raise ValueError("opération inconnue")
        with self.lock:
            if self.prep and not self.prep.get("fini"):
                raise RuntimeError("une opération est déjà en cours")
            info = self.inspect()
            if info and (info.get("State") or {}).get("Running"):
                raise RuntimeError("une opération est déjà en cours")
            self.prep = {"action": action, "version": version, "qui": who, "debut": time.time(), "message": "Préparation…"}
        threading.Thread(target=self._run, args=(action, who, version), daemon=True).start()

    def _run(self, action: str, who: str, version: str | None) -> None:
        try:
            current = os.environ.get("MES_VERSION", "dev")
            if action == "update":
                tool = version
            elif action == "rollback":
                tool = read_state("previous_version")
                if not tool:
                    raise RuntimeError("aucune version précédente connue")
            else:
                tool = current
            image = f"{REGISTRY}/mes-ops:{tool}"
            try:
                docker("GET", f"/images/{urllib.parse.quote(image, safe='/:')}/json")
            except RuntimeError:
                self.prep["message"] = f"Téléchargement de {image}"
                pull_image(image)
            hdir = host_dir()
            if self.inspect() is not None:
                docker("DELETE", f"/containers/{JOB_NAME}?force=true")
            cmd = [action] + ([version] if action == "update" else [])
            user = job_user()
            c = docker("POST", f"/containers/create?name={JOB_NAME}", body={
                "Image": image, "Entrypoint": ["/app/maj.sh"], "Cmd": cmd, **({"User": user["user"]} if user else {}),
                "Env": [f"MES_DIR={hdir}", f"MES_QUI={who}", "DOCKER_CONFIG=/tmp/.docker"],
                "Labels": {"mes.maj.action": action, "mes.maj.version": version or (tool if action == "rollback" else ""),
                           "mes.maj.de": current, "mes.maj.qui": who},
                "HostConfig": {"Binds": [f"{SOCKET}:/var/run/docker.sock", f"{hdir}:{hdir}"], "GroupAdd": user.get("groupes", []),
                               "LogConfig": {"Type": "json-file", "Config": {"max-size": "5m"}}}})
            docker("POST", f"/containers/{c['Id']}/start", timeout=30)
            with self.lock:
                self.prep = {**self.prep, "fini": True}
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.prep = {**(self.prep or {}), "fini": True, "erreur": str(e), "fin": time.time()}
            label = ACTION_LABEL[action] + (f" vers {version}" if version else "")
            journal_add(who, label, False, str(e))


JOBS = Jobs()


def job_user() -> dict:
    """La tâche écrit dans le dossier d'installation sous l'identité de son propriétaire (fichiers modifiables
    ensuite par mesctl en ligne de commande), avec le groupe du socket Docker."""
    try:
        st = (SITE / "deploy").stat()
        sock = os.stat(SOCKET)
    except OSError:
        return {}
    if st.st_uid == 0:
        return {}
    return {"user": f"{st.st_uid}:{st.st_gid}", "groupes": [str(sock.st_gid)]}


def pull_image(image: str) -> None:
    repo, _, tag = image.rpartition(":")
    conn = UnixConnection(timeout=1800)
    try:
        conn.request("POST", f"/images/create?fromImage={urllib.parse.quote(repo, safe='')}&tag={urllib.parse.quote(tag)}")
        r = conn.getresponse()
        body = r.read().decode(errors="replace")
        if r.status >= 400:
            raise RuntimeError(f"image {image} introuvable : {body[:200]}")
        for line in body.splitlines():
            try:
                if "error" in json.loads(line):
                    raise RuntimeError(f"image {image} introuvable : {json.loads(line)['error']}")
            except ValueError:
                pass
    finally:
        conn.close()


def load_archive(stream, length: int) -> list[str]:
    """Charge une archive d'images (docker save, .tar ou .tar.gz) en la passant directement à Docker, sans copie."""
    conn = UnixConnection(timeout=3600)
    try:
        conn.putrequest("POST", "/images/load?quiet=1")
        conn.putheader("Content-Type", "application/x-tar")
        conn.putheader("Content-Length", str(length))
        conn.endheaders()
        left = length
        while left > 0:
            chunk = stream.read(min(1 << 20, left))
            if not chunk:
                raise RuntimeError("envoi interrompu")
            conn.send(chunk)
            left -= len(chunk)
        r = conn.getresponse()
        body = r.read().decode(errors="replace")
        if r.status >= 400:
            raise RuntimeError(f"archive refusée par Docker : {body[:200]}")
    finally:
        conn.close()
    loaded, errors = [], []
    for line in body.splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if j.get("error"):
            errors.append(j["error"])
        m = re.search(r"Loaded image: (\S+)", j.get("stream", ""))
        if m:
            loaded.append(m[1])
    if errors:
        raise RuntimeError("archive invalide : " + errors[0])
    return loaded


def make_trial(version: str) -> list[str]:
    """Poste de développement : donne aux images :dev un numéro de version, pour essayer mise à jour et retour."""
    if os.environ.get("MES_VERSION", "dev") != "dev":
        raise PermissionError("version d'essai : seulement sur une installation de développement (version dev)")
    if not VERSION_RE.match(version):
        raise ValueError("numéro de version invalide (exemple : 0.0.1-essai)")
    done = []
    imgs = docker("GET", "/images/json")
    for im in imgs or []:
        for ref in im.get("RepoTags") or []:
            if ref.startswith(REGISTRY + "/mes-") and ref.endswith(":dev"):
                repo = ref.rsplit(":", 1)[0]
                docker("POST", f"/images/{urllib.parse.quote(ref, safe='/:')}/tag?repo={urllib.parse.quote(repo, safe='')}&tag={version}")
                done.append(repo.rsplit("/", 1)[-1])
    if "mes-ops" not in done:
        raise RuntimeError("images :dev introuvables sur ce serveur")
    return sorted(done)


def versions_overview() -> dict:
    env = site_env()
    current = os.environ.get("MES_VERSION", "dev")
    local = local_versions()
    online, err = online_versions()
    by = {}
    for o in online:
        by[o["version"]] = {**o, "locale": o["version"] in local, "en_ligne": True}
    for v, l in local.items():
        by.setdefault(v, {"version": v, "t": l["t"], "titre": "", "notes": "", "page": None, "archive": None,
                          "pre": "-" in v, "en_ligne": False})["locale"] = True
    items = sorted(by.values(), key=lambda x: version_key(x["version"]), reverse=True)
    for it in items:
        it["actuelle"] = it["version"] == current
        it["plus_recente"] = current == "dev" or version_key(it["version"]) > version_key(current)
    return {"actuelle": current, "precedente": read_state("previous_version"), "dev": current == "dev",
            "depot_git": (SITE / ".git").exists(), "site_visible": (SITE / "deploy").exists(),
            "versions": items, "en_ligne": bool(RELEASES_URL), "erreur_en_ligne": err,
            "historique": site_history(), "sauvegardes": backups(env), "operation": JOBS.state()}


# ----------------------------------------------------------------------------- serveur HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "mes-ops"

    def log_message(self, *args):
        pass

    def send(self, status: int, payload=None, ctype="application/json; charset=utf-8", headers=None):
        body = b"" if payload is None else (payload if isinstance(payload, bytes) else
                                            json.dumps(payload, ensure_ascii=False).encode())
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def err(self, status: int, msg: str):
        self.send(status, {"detail": msg})

    def user(self) -> dict | None:
        return read_session(self.headers.get("Cookie"))

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise ValueError("requête trop grande")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    # -- lecture
    def do_GET(self):  # noqa: N802
        path, _, query = self.path.partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        if path == "/sante":
            return self.send(200, {"ok": True})
        u = self.user()
        if path == "/auth":   # contrôle d'accès de nginx pour la page /systeme
            return self.send(200 if u else 401, {"ok": bool(u)})
        if u is None:
            return self.err(401, "Connexion requise")
        if path == "/api/ops/etat":
            return self.send(200, overview(u))
        if path == "/api/ops/journal":
            return self.send(200, journal_read(int(q.get("n", "100"))))
        m = re.fullmatch(r"/api/ops/services/([a-z0-9-]+)/journaux", path)
        if m:
            try:
                cid = container_id(m.group(1))
                n = max(10, min(2000, int(q.get("lignes", "200"))))
                raw = docker("GET", f"/containers/{cid}/logs?stdout=1&stderr=1&timestamps=1&tail={n}", raw=True)
                return self.send(200, {"lignes": demux(raw)})
            except LookupError as e:
                return self.err(404, str(e))
            except Exception as e:  # noqa: BLE001
                return self.err(502, str(e))
        if path == "/api/ops/stockage":
            try:
                return self.send(200, db_json("SELECT historian.stockage_etat()::text", timeout=30))
            except LookupError as e:
                return self.err(404, str(e))
            except Exception as e:  # noqa: BLE001
                return self.err(503, f"Base indisponible : {e}")
        if path == "/api/ops/maj":
            try:
                return self.send(200, versions_overview())
            except Exception as e:  # noqa: BLE001
                return self.err(502, str(e))
        if path == "/api/ops/historisation":
            r = http_json("GET", COLLECTOR_URL + "/historisation")
            return self.send(200, r) if r is not None else self.err(503, "Collecteur injoignable")
        if path.startswith("/api/ops/simulation"):
            sub = path.removeprefix("/api/ops/simulation") or "/etat"
            if sub not in ("/etat", "/tags"):
                return self.err(404, "Introuvable")
            r = http_json("GET", SIMULATOR_URL + sub + ("?" + query if query else ""))
            return self.send(200, r) if r is not None else self.err(503, "Simulateur absent ou injoignable")
        return self.err(404, "Introuvable")

    # -- actions (admin)
    def do_POST(self):  # noqa: N802
        self.act("POST")

    def do_PUT(self):  # noqa: N802
        self.act("PUT")

    def do_DELETE(self):  # noqa: N802
        self.act("DELETE")

    def act(self, method: str):
        path = self.path.partition("?")[0]
        u = self.user()
        if u is None:
            return self.err(401, "Connexion requise")
        if u.get("role") != "admin":
            return self.err(403, "Action réservée aux administrateurs")
        if method == "POST" and path == "/api/ops/maj/archive":
            return self.archive(u.get("username") or "?")
        # contre la falsification de requête : JSON obligatoire (un formulaire d'un autre site ne peut pas l'envoyer)
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.err(415, "JSON attendu")
        try:
            body = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.err(400, "Corps de requête invalide")
        who = u.get("username") or "?"
        m = re.fullmatch(r"/api/ops/services/([a-z0-9-]+)/(start|stop|restart)", path)
        if method == "POST" and m:
            svc, action = m.groups()
            label = SERVICES.get(svc, (svc,))[0]
            verb = {"start": "démarré", "stop": "arrêté", "restart": "redémarré"}[action]
            try:
                service_action(svc, action)
            except PermissionError as e:
                return self.err(403, str(e))
            except LookupError as e:
                return self.err(404, str(e))
            except Exception as e:  # noqa: BLE001
                journal_add(who, f"{label} {verb}", False, str(e))
                return self.err(502, str(e))
            threading.Thread(target=SAMPLER.sample_containers, daemon=True).start()
            return self.send(200, journal_add(who, f"{label} {verb}"))
        m = re.fullmatch(r"/api/ops/application/(start|stop|restart)", path)
        if method == "POST" and m:
            action = m.group(1)
            order = APPLICATION if action != "stop" else list(reversed(APPLICATION))
            errors = []
            for svc in order:
                try:
                    service_action(svc, action)
                except LookupError:
                    pass
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{svc} : {e}")
            verb = {"start": "démarrée", "stop": "arrêtée", "restart": "redémarrée"}[action]
            e = journal_add(who, f"Application {verb} (collecteur, API, Grafana)", not errors, " ; ".join(errors))
            SAMPLER.sample_containers()
            return self.send(200 if not errors else 502, e if not errors else {"detail": " ; ".join(errors)})
        if method == "POST" and path == "/api/ops/stockage/purge":
            jeu, garder_h = body.get("jeu"), body.get("garder_h")
            try:
                sql = purge_sql(jeu, garder_h)
            except ValueError as e:
                return self.err(400, str(e))
            try:
                r = db_json(sql)
            except LookupError as e:
                return self.err(404, str(e))
            except Exception as e:  # noqa: BLE001
                journal_add(who, f"Purge : {JEUX[jeu].lower()}", False, str(e))
                return self.err(502, str(e))
            journal_add(who, describe_purge(jeu, garder_h, r))
            threading.Thread(target=SAMPLER.sample_volumes, daemon=True).start()
            return self.send(200, r)
        if method == "PUT" and path == "/api/ops/historisation":
            r = http_json("PUT", COLLECTOR_URL + "/historisation", body)
            if r is None:
                return self.err(503, "Collecteur injoignable")
            if "erreur" in r:
                return self.err(r.get("status", 400), r["erreur"])
            journal_add(who, describe_hist(body))
            return self.send(200, r)
        if method == "PUT" and path == "/api/ops/simulation/regles":
            r = http_json("PUT", SIMULATOR_URL + "/regles", body)
            if r is None:
                return self.err(503, "Simulateur absent ou injoignable")
            if "erreur" in r:
                return self.err(r.get("status", 400), r["erreur"])
            g = body.get("global") or {}
            if set(g) == {"vitesse"} and not body.get("dbs"):
                v = float(g["vitesse"])
                journal_add(who, "Simulation en pause" if v == 0 else f"Vitesse de simulation ×{v:g}")
            else:
                journal_add(who, "Règles de simulation modifiées", True, json.dumps(body, ensure_ascii=False)[:300])
            return self.send(200, r)
        m = re.fullmatch(r"/api/ops/maj/(lancer|retour|sauvegarde|essai)", path)
        if method == "POST" and m:
            return self.maj(m.group(1), body, who)
        sim = sim_route(method, path, body)
        if sim:
            sub, label = sim
            r = http_json(method, SIMULATOR_URL + sub, body)
            if r is None:
                return self.err(503, "Simulateur absent ou injoignable")
            if "erreur" in r:
                return self.err(r.get("status", 400), r["erreur"])
            if label:
                journal_add(who, label(r))
            return self.send(200, r)
        return self.err(404, "Introuvable")


    def maj(self, op: str, body: dict, who: str):
        current = os.environ.get("MES_VERSION", "dev")
        try:
            if op == "essai":
                v = str(body.get("version") or "")
                images = make_trial(v)
                journal_add(who, f"Version d'essai {v} créée", True, ", ".join(images))
                return self.send(200, {"version": v, "images": images})
            if op == "lancer":
                v = str(body.get("version") or "")
                if not VERSION_RE.match(v):
                    raise ValueError("numéro de version invalide")
                if v == current:
                    raise ValueError(f"la version {v} est déjà en service")
                if current != "dev" and version_key(v) < version_key(current):
                    raise ValueError("version plus ancienne : utilisez le retour à la version précédente")
                JOBS.start("update", who, v)
                journal_add(who, f"Mise à jour {current} → {v} lancée")
            elif op == "retour":
                prev = read_state("previous_version")
                if not prev:
                    raise ValueError("aucune version précédente connue")
                JOBS.start("rollback", who, prev)
                journal_add(who, f"Retour à la version {prev} lancé")
            else:
                JOBS.start("backup", who)
                journal_add(who, "Sauvegarde lancée")
        except PermissionError as e:
            return self.err(403, str(e))
        except ValueError as e:
            return self.err(400, str(e))
        except RuntimeError as e:
            return self.err(409, str(e))
        return self.send(202, {"ok": True})

    def archive(self, who: str):
        # pas de JSON ici (fichier binaire) : l'en-tête propre à la console bloque les envois d'un autre site
        if self.headers.get("X-MES-Console") != "1":
            return self.err(415, "Envoi refusé")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return self.err(411, "Taille de l'archive inconnue")
        try:
            loaded = load_archive(self.rfile, n)
        except Exception as e:  # noqa: BLE001
            journal_add(who, "Archive de version chargée", False, str(e))
            self.close_connection = True
            return self.err(400, str(e))
        versions = sorted({ref.rsplit(":", 1)[-1] for ref in loaded if ref.startswith(f"{REGISTRY}/mes-ops:")}, key=version_key)
        journal_add(who, "Archive chargée" + (f" : version {', '.join(versions)}" if versions else ""), True, f"{len(loaded)} images")
        return self.send(200, {"images": loaded, "versions": versions})


def describe_hist(body: dict) -> str:
    if "dbs" in body and "actif" not in body:
        on = [k for k, v in body["dbs"].items() if v]
        off = [k for k, v in body["dbs"].items() if not v]
        parts = []
        if off:
            parts.append("suspendue pour " + ", ".join(off))
        if on:
            parts.append("reprise pour " + ", ".join(on))
        return "Historisation " + " ; ".join(parts)
    if body.get("actif"):
        return "Historisation reprise"
    d = body.get("duree_min")
    return "Historisation suspendue " + (f"pour {d} min" if d else "jusqu'à réactivation")


def main() -> None:
    if AUTH_DISABLED:
        print("AUTH_DISABLED : accès sans identifiant, chaque visiteur est administrateur", flush=True)
    if not SECRET:
        print("SECRET_KEY absent : aucune session ne sera acceptée", flush=True)
    threading.Thread(target=SAMPLER.loop, daemon=True).start()
    print(f"agent d'exploitation sur :{PORT} (projet {PROJECT})", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
