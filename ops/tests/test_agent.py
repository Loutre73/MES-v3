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


# ----------------------------------------------------------------------------- versions et mises à jour
def test_tri_des_versions():
    vs = ["0.9.0", "0.10.0", "0.2.0-essai", "0.2.0", "1.0.0"]
    assert sorted(vs, key=agent.version_key) == ["0.2.0-essai", "0.2.0", "0.9.0", "0.10.0", "1.0.0"]
    assert agent.VERSION_RE.match("0.2.0") and agent.VERSION_RE.match("0.0.1-essai")
    for bad in ("dev", "0.2", "0.2.0;rm -rf /", "../0.2.0", "0.2.0-", "0.2.0-A"):
        assert not agent.VERSION_RE.match(bad), bad


def test_etapes_de_mise_a_jour():
    lignes = ["Mise à jour 0.1.0 -> 0.2.0", "==> Fichiers d'installation", "==> Sauvegarde de la base", "Sauvegarde vers x"]
    e = agent.job_steps("update", lignes, True, None)
    assert [x["etat"] for x in e] == ["fait", "fait", "en_cours", "a_faire", "a_faire", "a_faire", "a_faire"]
    fin = lignes + ["==> Récupération des images", "==> Migration de la base", "==> Redémarrage des services", "==> Contrôle de santé"]
    assert all(x["etat"] == "fait" for x in agent.job_steps("update", fin, False, 0))
    echec = agent.job_steps("update", lignes[:3], False, 1)
    assert [x["etat"] for x in echec][:4] == ["fait", "fait", "echec", "a_faire"]
    # retour arrière automatique après une migration ratée : étape ajoutée à la liste
    auto = agent.job_steps("update", fin[:6] + ["==> Restauration de la sauvegarde"], False, 1)
    assert auto[-1] == {"nom": "Restauration de la sauvegarde", "etat": "echec"}


def test_dossier_de_site(tmp_path, monkeypatch):
    d = tmp_path / "deploy"
    (d / ".mesctl").mkdir(parents=True)
    (d / "backups").mkdir()
    (d / "site.env").write_text("MES_VERSION=0.2.0\nBACKUP_DIR=./backups\n# commentaire\n")
    (d / ".mesctl" / "previous_version").write_text("0.1.0\n")
    (d / ".mesctl" / "historique.tsv").write_text("2026-11-28T10:00:00Z\tupdate\t0.1.0\t0.2.0\tok\tben\n")
    (d / "backups" / "mes-20261128-100000-0.1.0.dump").write_bytes(b"x" * 10)
    monkeypatch.setattr(agent, "SITE", tmp_path)
    assert agent.site_env()["BACKUP_DIR"] == "./backups"
    assert agent.read_state("previous_version") == "0.1.0"
    assert agent.site_history()[0] == {"t": "2026-11-28T10:00:00Z", "action": "update", "de": "0.1.0", "vers": "0.2.0", "resultat": "ok", "qui": "ben"}
    b = agent.backups(agent.site_env())
    assert b["fichiers"][0]["version"] == "0.1.0" and b["fichiers"][0]["octets"] == 10
    assert agent.backups({"BACKUP_DIR": "/srv/sauvegardes"})["visible"] is False


def test_version_d_essai_reservee_au_developpement(monkeypatch):
    monkeypatch.setenv("MES_VERSION", "0.2.0")
    try:
        agent.make_trial("0.2.1-essai")
    except PermissionError:
        return
    raise AssertionError("version d'essai acceptée hors développement")


def test_archive_chargee_sans_copie(monkeypatch):
    import io
    sent = []

    class FakeConn:
        def __init__(self, timeout=0): pass
        def putrequest(self, *a): sent.append(a)
        def putheader(self, *a): pass
        def endheaders(self): pass
        def send(self, b): sent.append(len(b))
        def close(self): pass
        def getresponse(self):
            class R:
                status = 200
                def read(self):
                    return b'{"stream":"Loaded image: ghcr.io/loutre73/mes-ops:0.2.0\\n"}\n{"stream":"Loaded image: grafana/grafana-oss:11.3.0\\n"}\n'
            return R()
    monkeypatch.setattr(agent, "UnixConnection", FakeConn)
    data = b"a" * (3 << 20)
    loaded = agent.load_archive(io.BytesIO(data), len(data))
    assert loaded == ["ghcr.io/loutre73/mes-ops:0.2.0", "grafana/grafana-oss:11.3.0"]
    assert sum(x for x in sent if isinstance(x, int)) == len(data)
