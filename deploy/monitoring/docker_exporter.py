"""Exporter Prometheus des conteneurs Docker (CPU, mémoire, réseau, disque, redémarrages, santé).

Bibliothèque standard Python seulement : lit l'API Docker par le socket Unix à chaque collecte.
Remplace cAdvisor, qui ne reconnaît pas les conteneurs quand Docker utilise le magasin d'images
containerd (par défaut depuis Docker 29), et fonctionne aussi sous Colima / Docker Desktop.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
PORT = int(os.environ.get("PORT", "9417"))
POOL = ThreadPoolExecutor(max_workers=16)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=8)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(8)
        self.sock.connect(SOCKET)


def api(path: str):
    conn = UnixConnection()
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        if r.status != 200:
            raise RuntimeError(f"{path} : HTTP {r.status}")
        return json.loads(body)
    finally:
        conn.close()


def parse_time(s: str) -> float:
    # 2026-11-25T21:58:27.123456789Z -> secondes ; on tronque les nanosecondes
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(s + "+00:00").timestamp()
    except ValueError:
        return 0.0


def esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def one(c: dict) -> list[tuple[str, dict, float]]:
    cid = c["Id"]
    labels = c.get("Labels") or {}
    name = (c.get("Names") or ["/" + cid[:12]])[0].lstrip("/")
    lab = {"service": labels.get("com.docker.compose.service", name),
           "project": labels.get("com.docker.compose.project", ""), "container": name}
    out: list[tuple[str, dict, float]] = []
    info = api(f"/containers/{cid}/json")
    state = info.get("State", {})
    health = (state.get("Health") or {}).get("Status")
    out.append(("info", {**lab, "image": c.get("Image", ""), "state": state.get("Status", "")}, 1))
    out.append(("running", lab, 1 if state.get("Running") else 0))
    out.append(("restarts_total", lab, info.get("RestartCount", 0)))
    out.append(("health", lab, {"healthy": 1, "unhealthy": 0, "starting": 0.5}.get(health, -1)))
    started = parse_time(state.get("StartedAt", ""))
    if started > 0:
        out.append(("start_time_seconds", lab, started))
    if not state.get("Running"):
        return out
    st = api(f"/containers/{cid}/stats?stream=false&one-shot=true")
    cpu = st.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage")
    if cpu is not None:
        out.append(("cpu_seconds_total", lab, cpu / 1e9))
    mem = st.get("memory_stats", {})
    if "usage" in mem:
        stats = mem.get("stats", {})
        cache = stats.get("inactive_file", stats.get("total_inactive_file", 0))
        out.append(("memory_bytes", lab, max(mem["usage"] - cache, 0)))   # même calcul que « docker stats »
        out.append(("memory_limit_bytes", lab, mem.get("limit", 0)))
    rx = tx = 0
    for n in (st.get("networks") or {}).values():
        rx += n.get("rx_bytes", 0)
        tx += n.get("tx_bytes", 0)
    out.append(("network_receive_bytes_total", lab, rx))
    out.append(("network_transmit_bytes_total", lab, tx))
    rd = wr = 0
    for e in (st.get("blkio_stats", {}).get("io_service_bytes_recursive") or []):
        op = e.get("op", "").lower()
        if op == "read":
            rd += e.get("value", 0)
        elif op == "write":
            wr += e.get("value", 0)
    out.append(("disk_read_bytes_total", lab, rd))
    out.append(("disk_write_bytes_total", lab, wr))
    out.append(("pids", lab, st.get("pids_stats", {}).get("current", 0)))
    return out


HELP = {
    "info": "Conteneur présent (étiquettes : image, état)",
    "running": "1 si le conteneur tourne",
    "restarts_total": "Redémarrages du conteneur par Docker",
    "health": "Santé Docker : 1 sain, 0.5 démarrage, 0 en échec, -1 sans healthcheck",
    "start_time_seconds": "Heure de démarrage (epoch)",
    "cpu_seconds_total": "Temps CPU consommé",
    "memory_bytes": "Mémoire utilisée hors cache récupérable (comme docker stats)",
    "memory_limit_bytes": "Limite mémoire (mémoire de l'hôte si aucune limite)",
    "network_receive_bytes_total": "Octets reçus",
    "network_transmit_bytes_total": "Octets émis",
    "disk_read_bytes_total": "Octets lus sur disque",
    "disk_write_bytes_total": "Octets écrits sur disque",
    "pids": "Processus et threads dans le conteneur",
}
COUNTERS = {k for k in HELP if k.endswith("_total")}


def collect() -> str:
    t0 = time.perf_counter()
    lines: dict[str, list[str]] = {k: [] for k in HELP}
    up = 1
    try:
        containers = api("/containers/json?all=true")
        for res in POOL.map(lambda c: _safe(one, c), containers):
            for metric, lab, value in res:
                labels = ",".join(f'{k}="{esc(v)}"' for k, v in lab.items())
                lines[metric].append(f"mes_container_{metric}{{{labels}}} {value}")
    except Exception as e:  # noqa: BLE001
        up = 0
        print(f"API Docker injoignable : {e}", flush=True)
    out = []
    for k, rows in lines.items():
        out.append(f"# HELP mes_container_{k} {HELP[k]}")
        out.append(f"# TYPE mes_container_{k} {'counter' if k in COUNTERS else 'gauge'}")
        out.extend(rows)
    out += ["# HELP mes_docker_up 1 si l'API Docker répond", "# TYPE mes_docker_up gauge", f"mes_docker_up {up}",
            "# HELP mes_docker_scrape_seconds Durée de la collecte", "# TYPE mes_docker_scrape_seconds gauge",
            f"mes_docker_scrape_seconds {time.perf_counter() - t0:.4f}"]
    return "\n".join(out) + "\n"


def _safe(fn, c):
    try:
        return fn(c)
    except Exception as e:  # noqa: BLE001  (conteneur supprimé pendant la collecte, etc.)
        print(f"conteneur {c.get('Id', '')[:12]} ignoré : {e}", flush=True)
        return []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        body = collect().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"exporter Docker sur :{PORT}, socket {SOCKET}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
