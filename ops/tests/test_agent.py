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
