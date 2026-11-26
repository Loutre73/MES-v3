"""Agent Mac : sécurité (hôte, clé), actions bornées, réglages, enchaînement des opérations.
Colima et Docker sont remplacés par de faux programmes (aucun Mac nécessaire)."""
import http.client
import importlib
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GB = 1024 ** 3

FAUX_COLIMA = r'''#!/usr/bin/env python3
import json, os, sys
f = os.environ["FAUX_ETAT"]
st = json.load(open(f)) if os.path.exists(f) else {"status": "Running", "disk": 20, "cpus": 2, "memory": 4}
a = sys.argv[1:]
open(os.environ["FAUX_LOG"], "a").write("colima " + " ".join(a) + "\n")
if a[0] == "list":
    print(json.dumps({"name": "default", "status": st["status"], "arch": "aarch64", "cpus": st["cpus"],
                      "memory": st["memory"] * 1024**3, "disk": st["disk"] * 1024**3, "runtime": "docker"}))
elif a[0] == "start":
    st["status"] = "Running"
    if "--disk" in a: st["disk"] = int(a[a.index("--disk") + 1])
elif a[0] == "stop":
    st["status"] = "Stopped"
elif a[0] == "ssh":
    cmd = a[a.index("--") + 1:]
    if cmd[0] == "df":
        print("Filesystem 1-blocks Used Available Capacity Mounted on")
        print(f"/dev/vdb1 {20*1024**3} {17*1024**3} {3*1024**3} 85% /var/lib/docker")
    elif cmd[0] == "date":
        import time; print(int(time.time()) + 42)
json.dump(st, open(f, "w"))
'''
FAUX_DOCKER = r'''#!/usr/bin/env python3
import os, sys, json
open(os.environ["FAUX_LOG"], "a").write("docker " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1:3] == ["system", "df"]:
    print(json.dumps({"Type": "Build Cache", "Size": "6.1GB", "Reclaimable": "6.1GB"}))
elif "ps" in sys.argv:
    print(json.dumps({"Service": "db", "State": "running", "Status": "Up 2 hours"}))
'''


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for nom, code in (("colima", FAUX_COLIMA), ("docker", FAUX_DOCKER), ("osascript", "#!/bin/sh\n")):
        (bin_ / nom).write_text(code)
        (bin_ / nom).chmod(0o755)
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "site.env").write_text("MES_VERSION=dev\nHISTORIAN_RETENTION=1 year\nPROMETHEUS_RETENTION=15d\nCOMPOSE_PROFILES=sim\n")
    (deploy / "mesctl").write_text("#!/bin/sh\necho mesctl $@ >> \"$FAUX_LOG\"\n")
    (deploy / "mesctl").chmod(0o755)
    monkeypatch.setenv("FAUX_ETAT", str(tmp_path / "etat.json"))
    monkeypatch.setenv("FAUX_LOG", str(tmp_path / "log.txt"))
    monkeypatch.setenv("MES_DEPLOY", str(deploy))
    monkeypatch.setenv("MES_AGENT_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("MES_AGENT_PORT", "0")
    sys.path.insert(0, str(HERE.parent))
    import agent_mac
    m = importlib.reload(agent_mac)
    os.environ["PATH"] = f"{bin_}:{os.environ['PATH']}"
    m.STATE.mkdir(parents=True)
    m.mac_disque = lambda: {"total": 500 * GB, "libre": 15 * GB, "pct": 97}
    m.mac_memoire = lambda: 16 * GB
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
    port = srv.server_address[1]
    m.HOTES = {f"localhost:{port}", f"127.0.0.1:{port}"}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    m._port, m._log, m._deploy = port, tmp_path / "log.txt", deploy
    yield m
    srv.shutdown()


def req(m, method, path, body=None, host=None, cle=True, origin=None):
    c = http.client.HTTPConnection("127.0.0.1", m._port, timeout=10)
    h = {"Host": host or f"localhost:{m._port}", "Content-Type": "application/json"}
    if cle:
        h["X-Agent-Cle"] = m.cle()
    if origin:
        h["Origin"] = origin
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    data = r.read()
    try:
        return r.status, json.loads(data), dict(r.getheaders())
    except ValueError:
        return r.status, data.decode(), dict(r.getheaders())


def attendre(m):
    for _ in range(100):
        if not m.JOB.busy():
            return m.JOB.snapshot()
        time.sleep(0.1)
    raise AssertionError("opération trop longue")


def test_page_contient_la_cle_et_etat(agent):
    s, page, _ = req(agent, "GET", "/")
    assert s == 200 and agent.cle() in page
    s, d, _ = req(agent, "GET", "/api/etat")
    assert d["colima"]["etat"] == "en_marche" and d["vm_disque"]["pct"] == 85
    assert d["vm_decalage_heure"] >= 40
    assert any(a["niveau"] == "orange" and "85" in a["texte"] for a in d["alertes"])
    assert d["disque_max_go"] == 30          # 20 Go + (15 - 5) Go libres sur le Mac
    assert d["services"][0]["service"] == "db"


def test_hote_etranger_refuse(agent):
    s, _, _ = req(agent, "GET", "/api/etat", host="evil.example:80")
    assert s == 403
    s, _, _ = req(agent, "POST", "/api/action", {"nom": "arreter"}, host="evil.example:80")
    assert s == 403


def test_cle_et_origine_obligatoires(agent):
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "arreter"}, cle=False)
    assert s == 403
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "arreter"}, origin="http://localhost:80")
    assert s == 403
    assert not agent._log.exists() or "stop" not in agent._log.read_text()


def test_ping_lisible_par_la_console(agent):
    s, d, h = req(agent, "GET", "/api/ping", origin="http://localhost")
    assert s == 200 and h.get("Access-Control-Allow-Origin") == "http://localhost"
    s, d, h = req(agent, "GET", "/api/ping", origin="https://evil.example")
    assert "Access-Control-Allow-Origin" not in h


def test_disque_borne(agent):
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "disque", "params": {"go": 18}})
    assert s == 409 and "agrandi" in d["erreur"]
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "disque", "params": {"go": 31}})
    assert s == 409 and "30" in d["erreur"]


def test_agrandir_disque_enchaine_arret_propre(agent):
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "disque", "params": {"go": 25}})
    assert s == 200
    op = attendre(agent)
    assert op["ok"], op["lignes"]
    log = agent._log.read_text().splitlines()
    i_stop_mes = next(i for i, x in enumerate(log) if x.startswith("docker compose") and " stop" in x)
    i_stop = log.index("colima stop -p default")
    i_start = log.index("colima start -p default --disk 25")
    i_up = next(i for i, x in enumerate(log) if x.startswith("docker compose") and " up -d" in x)
    assert i_stop_mes < i_stop < i_start < i_up
    assert agent.colima_info()["disque"] == 25 * GB
    assert agent.journal_read()[0]["ok"]


def test_une_seule_operation_a_la_fois(agent):
    req(agent, "POST", "/api/action", {"nom": "redemarrer"})
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "menage"})
    assert s == 409 and "déjà en cours" in d["erreur"]
    attendre(agent)


def test_reglages_valides_et_copie(agent):
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "reglages", "params": {"valeurs": {"HISTORIAN_RETENTION": "1 year; rm -rf /"}}})
    assert s == 409
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "reglages", "params": {"valeurs": {"SECRET_KEY": "x"}}})
    assert s == 409
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "reglages", "params": {"valeurs": {
        "HISTORIAN_RETENTION": "9 hours", "HISTORIAN_MAX_SIZE": "6GB", "PROMETHEUS_RETENTION": "3d"}}})
    assert s == 200
    assert attendre(agent)["ok"]
    env = (agent._deploy / "site.env").read_text()
    assert "HISTORIAN_RETENTION=9 hours\n" in env and "HISTORIAN_MAX_SIZE=6GB\n" in env and "PROMETHEUS_RETENTION=3d\n" in env
    assert "MES_VERSION=dev" in env
    copies = list(agent._deploy.glob("site.env.avant-*"))
    assert len(copies) == 1 and "HISTORIAN_RETENTION=1 year" in copies[0].read_text()


def test_actions_refusees_colima_arrete(agent):
    req(agent, "POST", "/api/action", {"nom": "arreter"})
    assert attendre(agent)["ok"]
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "menage"})
    assert s == 409 and "arrêté" in d["erreur"]
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "demarrer"})
    assert s == 200 and attendre(agent)["ok"]
    assert agent.colima_info()["etat"] == "en_marche"


def test_action_inconnue(agent):
    s, d, _ = req(agent, "POST", "/api/action", {"nom": "shell", "params": {"cmd": "ls"}})
    assert s == 409
