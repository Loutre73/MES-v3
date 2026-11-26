import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

os.environ["SECRET_KEY"] = "cle-de-test"
os.environ["OPS_DATA"] = "/tmp/mes-ops-test"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ops_agent as agent  # noqa: E402


def token(role="admin", exp=60, key="cle-de-test"):
    body = base64.urlsafe_b64encode(json.dumps({"u": "ben", "r": role, "n": None, "exp": int(time.time()) + exp}).encode()).decode()
    return body + "." + hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()


def test_session_signee_par_l_api():
    assert agent.read_session(f"autre=1; mes_session={token()}")["role"] == "admin"
    assert agent.read_session(f"mes_session={token(key='autre')}") is None      # mauvaise signature
    assert agent.read_session(f"mes_session={token(exp=-10)}") is None          # expirée
    assert agent.read_session(None) is None


def test_actions_limitees():
    for svc, action in (("web", "stop"), ("ops", "restart"), ("db", "rm"), ("inconnu", "start")):
        try:
            agent.service_action(svc, action)
        except PermissionError:
            continue
        raise AssertionError(f"{svc} {action} accepté")


def test_journaux_docker_multiplexes():
    raw = b"\x01\x00\x00\x00\x00\x00\x00\x06hello\n\x02\x00\x00\x00\x00\x00\x00\x04err\n"
    assert agent.demux(raw) == ["hello", "err"]
    assert agent.demux(b"texte brut\n") == ["texte brut"]


def test_libelles_du_journal():
    assert agent.describe_hist({"actif": False, "duree_min": 15}) == "Historisation suspendue pour 15 min"
    assert agent.describe_hist({"actif": True}) == "Historisation reprise"
    assert agent.describe_hist({"dbs": {"DB_Process": False}}) == "Historisation suspendue pour DB_Process"


def test_acces_sans_identifiant(monkeypatch):
    monkeypatch.setattr(agent, "AUTH_DISABLED", True)
    assert agent.read_session(None)["role"] == "admin"


def test_purge_parametres_verifies():
    assert agent.purge_sql("brut", 12) == "SELECT historian.purger('brut', make_interval(hours => 12))::text"
    for jeu, h in (("brut'; DROP TABLE x; --", 12), ("1min", "12"), ("1h", 0), ("1h", True), ("brut", 10**9)):
        try:
            agent.purge_sql(jeu, h)
        except ValueError:
            continue
        raise AssertionError(f"{jeu!r} {h!r} accepté")


def test_sortie_exec_docker():
    raw = b"\x01\x00\x00\x00\x00\x00\x00\x03{}\n\x02\x00\x00\x00\x00\x00\x00\x0bERROR:  x\n"
    assert agent.split_streams(raw) == ("{}\n", "ERROR:  x\n")


def test_libelle_de_purge():
    r = {"chunks": 3, "liberes_octets": 2_500_000_000}
    assert agent.describe_purge("1min", 720, r) == "Purge : agrégats 1 min de plus de 30 j (3 blocs, 2500 Mo libérés)"
    assert agent.describe_purge("brut", 6, r).startswith("Purge : données brutes de plus de 6 h")


def test_routes_des_scenarios_de_simulation():
    r = agent.sim_route("PUT", "/api/ops/simulation/scenarios/cascade", {"nom": "Cascade"})
    assert r[0] == "/scenarios/cascade" and "Cascade" in r[1]({})
    r = agent.sim_route("POST", "/api/ops/simulation/scenarios/cascade/lancer", {})
    assert r[0] == "/scenarios/cascade/lancer" and r[1]({"nom": "Cascade"}) == "Scénario « Cascade » lancé"
    assert agent.sim_route("DELETE", "/api/ops/simulation/groupes/g-1", {})[0] == "/groupes/g-1"
    assert agent.sim_route("POST", "/api/ops/simulation/apercu", {}) == ("/apercu", None)
    assert agent.sim_route("DELETE", "/api/ops/simulation/scenarios/../etc", {}) is None
    assert agent.sim_route("POST", "/api/ops/services/x/start", {}) is None
