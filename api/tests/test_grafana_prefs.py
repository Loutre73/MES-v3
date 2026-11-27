import asyncio
import json
import subprocess
import sys
from pathlib import Path

from mes_api import grafana
from mes_api.i18n import LANGUES

ROOT = Path(__file__).resolve().parents[2]


def test_tableaux_traduits_a_jour():
    # les copies anglaises sont régénérées, complètes et cohérentes (surcharges, liens, uid)
    r = subprocess.run([sys.executable, str(ROOT / "grafana/generate/traduire.py"), "--verifier"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_accueil_de_chaque_langue_existe():
    uids = {json.loads(p.read_text())["uid"] for p in ROOT.glob("grafana/dashboards*/*.json")}
    for code in LANGUES:
        assert grafana.PREFS[code][1] in uids


def test_synchronisation_une_fois_par_langue(monkeypatch):
    appels = []
    langue = {"v": "en"}

    async def lire(username):
        return langue["v"]

    monkeypatch.setattr(grafana, "_langue", lire)
    monkeypatch.setattr(grafana, "_patch", lambda u, r, l: appels.append((u, r, l)))
    grafana._applique.clear()

    async def scenario():
        await grafana.synchroniser("alice", "Viewer")
        await grafana.synchroniser("alice", "Viewer")      # déjà appliqué : pas de nouvel appel
        langue["v"] = "fr"
        await grafana.synchroniser("alice", "Viewer")

    asyncio.run(scenario())
    assert appels == [("alice", "Viewer", "en"), ("alice", "Viewer", "fr")]


def test_echec_reessaye(monkeypatch):
    n = {"v": 0}

    async def lire(username):
        return "en"

    def patch(u, r, l):
        n["v"] += 1
        if n["v"] == 1:
            raise OSError("Grafana indisponible")

    monkeypatch.setattr(grafana, "_langue", lire)
    monkeypatch.setattr(grafana, "_patch", patch)
    grafana._applique.clear()
    asyncio.run(grafana.synchroniser("bob", "Admin"))     # échec : l'accès n'est pas bloqué
    asyncio.run(grafana.synchroniser("bob", "Admin"))     # réessayé
    assert n["v"] == 2 and grafana._applique["bob"] == "en"
