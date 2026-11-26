#!/usr/bin/env python3
"""Agent Mac du MES v3 : piloter Colima et les réglages du MES sans ligne de commande.

La console /systeme tourne à l'intérieur de la machine virtuelle Colima : elle ne peut ni l'arrêter,
ni la démarrer, ni l'agrandir. Cet agent tourne sur le Mac lui-même (lancé par launchd à l'ouverture
de session) et sert la page « Poste Mac » sur http://localhost:9300, disponible même quand Colima est arrêté.

Sécurité :
- écoute 127.0.0.1 seulement, et refuse toute requête dont l'en-tête Host n'est pas localhost:PORT ;
- les actions exigent la clé de l'agent (en-tête X-Agent-Cle), présente seulement dans la page servie
  par l'agent : une autre page web ouverte dans le navigateur ne peut pas la lire ;
- liste fermée d'actions aux valeurs bornées, jamais de commande libre, jamais de suppression de données MES ;
- copie de site.env avant chaque modification ; journal de toutes les opérations.
Bibliothèque standard Python seulement (python3 de macOS).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("MES_AGENT_PORT", "9300"))
HERE = Path(__file__).resolve().parent
DEPLOY = Path(os.environ.get("MES_DEPLOY", HERE.parent)).resolve()
ENV_FILE = DEPLOY / "site.env"
STATE = Path(os.environ.get("MES_AGENT_STATE", Path.home() / "Library/Application Support/MES-agent"))
PROFILE = os.environ.get("COLIMA_PROFILE", "default")
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")

GB = 1024 ** 3
MAC_MARGE = 5 * GB            # place à laisser libre sur le Mac quand on agrandit le disque de Colima
SEUIL_ORANGE, SEUIL_ROUGE = 80, 90

# Réglages modifiables depuis la page : clé de site.env -> (libellé, expression autorisée)
REGLAGES = {
    "HISTORIAN_RETENTION": ("Conservation des données brutes", r"\d{1,4} (hour|day|week|month|year)s?"),
    "HISTORIAN_MAX_SIZE": ("Plafond de taille de la base", r"(|0|\d{1,4}(MB|GB))"),
    "PROMETHEUS_RETENTION": ("Conservation des métriques techniques", r"\d{1,4}[hdwy]"),
    "PROMETHEUS_RETENTION_SIZE": ("Taille maximale des métriques techniques", r"\d{1,4}(MB|GB)"),
}


# ----------------------------------------------------------------------------- outils
def run(cmd: list[str], timeout: float = 30, cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} introuvable"
    except subprocess.TimeoutExpired:
        return 124, f"délai dépassé ({int(timeout)} s)"


def compose(*args: str) -> list[str]:
    return ["docker", "compose", "--env-file", str(ENV_FILE), "-f", str(DEPLOY / "docker-compose.yml"), *args]


def env_read() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            m = re.match(r"^([A-Z_0-9]+)=(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def env_write(values: dict[str, str]) -> Path:
    """Modifie site.env (copie de sauvegarde d'abord) ; ajoute les clés absentes à la fin."""
    text = ENV_FILE.read_text()
    backup = ENV_FILE.with_name(f"site.env.avant-{datetime.now():%Y%m%d-%H%M%S}")
    backup.write_text(text)
    os.chmod(backup, 0o600)
    lines, seen = text.splitlines(), set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0]
        if k in values and "=" in line:
            lines[i] = f"{k}={values[k]}"
            seen.add(k)
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    return backup


def config() -> dict:
    try:
        return json.loads((STATE / "config.json").read_text())
    except (OSError, ValueError):
        return {"demarrage_auto": False}


def config_set(**kw) -> None:
    c = config()
    c.update(kw)
    (STATE / "config.json").write_text(json.dumps(c))


def cle() -> str:
    f = STATE / "cle"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(32))
        os.chmod(f, 0o600)
    return f.read_text().strip()


def journal_add(action: str, ok: bool, detail: str = "") -> None:
    e = {"t": datetime.now().isoformat(timespec="seconds"), "action": action, "ok": ok, "detail": detail[:300]}
    with open(STATE / "journal.jsonl", "a") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def journal_read(n: int = 30) -> list[dict]:
    try:
        lines = (STATE / "journal.jsonl").read_text().splitlines()[-n:]
    except OSError:
        return []
    return [json.loads(x) for x in reversed(lines) if x.strip()]


def notifier(titre: str, texte: str) -> None:
    """Notification macOS (centre de notifications)."""
    t = texte.replace('"', "'")
    run(["osascript", "-e", f'display notification "{t}" with title "{titre}"'], timeout=5)


# ----------------------------------------------------------------------------- état
def colima_info() -> dict:
    code, out = run(["colima", "list", "--json"], timeout=15)
    if code == 127:
        return {"installe": False, "etat": "absent"}
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("name") == PROFILE:
            return {"installe": True, "etat": "en_marche" if d.get("status") == "Running" else "arrete",
                    "cpus": d.get("cpus"), "memoire": d.get("memory"), "disque": d.get("disk"),
                    "arch": d.get("arch"), "runtime": d.get("runtime")}
    return {"installe": True, "etat": "arrete" if code == 0 else "inconnu", "detail": out[-200:]}


def vm_disque() -> dict | None:
    code, out = run(["colima", "ssh", "-p", PROFILE, "--", "df", "-P", "-B1", "/var/lib/docker"], timeout=15)
    if code != 0:
        return None
    parts = out.splitlines()[-1].split()
    try:
        total, used, free = int(parts[1]), int(parts[2]), int(parts[3])
    except (IndexError, ValueError):
        return None
    return {"total": total, "utilise": used, "libre": free, "pct": round(100 * used / total) if total else 0}


def vm_decalage_heure() -> int | None:
    code, out = run(["colima", "ssh", "-p", PROFILE, "--", "date", "+%s"], timeout=10)
    try:
        return int(out.strip().splitlines()[-1]) - int(time.time()) if code == 0 else None
    except (ValueError, IndexError):
        return None


def mac_disque() -> dict:
    root = "/System/Volumes/Data" if Path("/System/Volumes/Data").exists() else str(Path.home())
    u = shutil.disk_usage(root)
    return {"total": u.total, "libre": u.free, "pct": round(100 * (u.total - u.free) / u.total)}


def colima_fichier() -> int:
    """Place réellement occupée sur le Mac par les disques de la VM (fichiers creux : blocs alloués)."""
    total = 0
    base = Path.home() / ".colima" / "_lima"
    for p in list(base.glob("_disks/*/datadisk")) + list(base.glob("*/diffdisk")):
        try:
            total += p.stat().st_blocks * 512
        except OSError:
            pass
    return total


def docker_df() -> list[dict]:
    code, out = run(["docker", "system", "df", "--format", "{{json .}}"], timeout=20)
    if code != 0:
        return []
    noms = {"Images": "Images", "Containers": "Conteneurs", "Local Volumes": "Volumes (données)",
            "Build Cache": "Cache de construction"}
    rows = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        rows.append({"type": noms.get(d.get("Type"), d.get("Type")), "taille": d.get("Size"),
                     "recuperable": d.get("Reclaimable")})
    return rows


def services() -> list[dict]:
    code, out = run(compose("ps", "-a", "--format", "json"), timeout=20, cwd=DEPLOY)
    if code != 0:
        return []
    items = []
    txt = out.strip()
    try:
        data = json.loads(txt) if txt.startswith("[") else [json.loads(x) for x in txt.splitlines() if x.startswith("{")]
    except ValueError:
        return []
    for d in data:
        items.append({"service": d.get("Service"), "etat": d.get("State"), "statut": d.get("Status")})
    return sorted(items, key=lambda x: x["service"] or "")


def mac_memoire() -> int:
    code, out = run(["sysctl", "-n", "hw.memsize"], timeout=5)
    return int(out) if code == 0 and out.isdigit() else 0


def etat() -> dict:
    env = env_read()
    c = colima_info()
    marche = c.get("etat") == "en_marche"
    d = {
        "colima": c,
        "vm_disque": vm_disque() if marche else None,
        "vm_decalage_heure": vm_decalage_heure() if marche else None,
        "mac_disque": mac_disque(),
        "mac_memoire": mac_memoire(),
        "mac_cpus": os.cpu_count(),
        "colima_fichier": colima_fichier(),
        "docker": docker_df() if marche else [],
        "services": services() if marche else [],
        "reglages": {k: {"libelle": v[0], "valeur": env.get(k, "")} for k, v in REGLAGES.items()},
        "surveillance": "monitoring" in env.get("COMPOSE_PROFILES", "").split(","),
        "web_port": env.get("WEB_PORT", "80") or "80",
        "deploy": str(DEPLOY),
        "site_env": ENV_FILE.exists(),
        "config": config(),
        "operation": JOB.snapshot(),
        "journal": journal_read(),
        "seuils": {"orange": SEUIL_ORANGE, "rouge": SEUIL_ROUGE, "mac_marge": MAC_MARGE},
    }
    d["disque_max_go"] = disque_max_go(c, d["mac_disque"])
    d["alertes"] = alertes(d)
    return d


def disque_max_go(c: dict, mac: dict) -> int:
    actuel = int((c.get("disque") or 0) / GB)
    return max(actuel, actuel + int((mac["libre"] - MAC_MARGE) / GB))


def alertes(d: dict) -> list[dict]:
    out = []
    v = d.get("vm_disque")
    if v and v["pct"] >= SEUIL_ROUGE:
        out.append({"niveau": "rouge", "texte": f"Disque de Colima plein à {v['pct']} % : le MES risque de s'arrêter. Faites le ménage ou purgez l'historian."})
    elif v and v["pct"] >= SEUIL_ORANGE:
        out.append({"niveau": "orange", "texte": f"Disque de Colima rempli à {v['pct']} %."})
    m = d["mac_disque"]
    if m["libre"] < MAC_MARGE:
        out.append({"niveau": "rouge", "texte": f"Plus que {m['libre'] / GB:.1f} Go libres sur le Mac."})
    elif m["libre"] < 2 * MAC_MARGE:
        out.append({"niveau": "orange", "texte": f"{m['libre'] / GB:.1f} Go libres sur le Mac."})
    o = d.get("vm_decalage_heure")
    if o is not None and abs(o) > 5:
        out.append({"niveau": "orange", "texte": f"L'horloge de Colima a {abs(o)} s d'écart avec le Mac (après une veille) : bouton « Remettre à l'heure »."})
    return out


# ----------------------------------------------------------------------------- opérations
class Job:
    """Une seule opération à la fois, exécutée en arrière-plan ; la page suit son avancement."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cur: dict | None = None

    def snapshot(self) -> dict | None:
        with self.lock:
            return json.loads(json.dumps(self.cur)) if self.cur else None

    def busy(self) -> bool:
        with self.lock:
            return bool(self.cur and not self.cur["fini"])

    def start(self, nom: str, etapes: list[tuple], after=None) -> None:
        with self.lock:
            if self.cur and not self.cur["fini"]:
                raise RuntimeError(f"une opération est déjà en cours : {self.cur['nom']}")
            self.cur = {"nom": nom, "debut": time.time(), "etape": "", "lignes": [], "fini": False, "ok": None}
        threading.Thread(target=self._run, args=(nom, etapes, after), daemon=True).start()

    def log(self, s: str) -> None:
        with self.lock:
            self.cur["lignes"] = (self.cur["lignes"] + [s])[-200:]

    def _run(self, nom, etapes, after) -> None:
        ok, detail = True, ""
        for libelle, cmd, timeout, tolere in etapes:
            with self.lock:
                self.cur["etape"] = libelle
            self.log(f"▸ {libelle}")
            if callable(cmd):
                try:
                    msg = cmd()
                    if msg:
                        self.log(msg)
                    code = 0
                except Exception as e:  # noqa: BLE001
                    code, detail = 1, str(e)
                    self.log(detail)
            else:
                try:
                    p = subprocess.Popen(cmd, cwd=DEPLOY, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    t0 = time.time()
                    for line in p.stdout:
                        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line.rstrip())
                        if line:
                            self.log(line)
                        if time.time() - t0 > timeout:
                            p.kill()
                            self.log(f"délai dépassé ({timeout} s)")
                    code = p.wait()
                except FileNotFoundError:
                    code = 127
                    self.log(f"{cmd[0]} introuvable")
                if code != 0:
                    detail = f"{libelle} : code {code}"
            if code != 0 and not tolere:
                ok = False
                break
        if ok and after:
            try:
                after()
            except Exception as e:  # noqa: BLE001
                ok, detail = False, str(e)
        with self.lock:
            self.cur.update(fini=True, ok=ok, etape="Terminé" if ok else "Échec", fin=time.time())
        journal_add(nom, ok, detail)
        notifier("MES v3", f"{nom} : {'terminé' if ok else 'échec'}")


JOB = Job()

# Étapes : (libellé, commande ou fonction, délai en s, échec toléré)
def e_arret_mes():
    return ("Arrêt propre du MES (collecteur, puis base)", compose("stop", "-t", "30"), 300, True)


def e_demarrage_mes():
    return ("Démarrage du MES", compose("up", "-d"), 600, False)


def e_colima_stop():
    return ("Arrêt de la machine virtuelle Colima", ["colima", "stop", "-p", PROFILE], 300, False)


def e_colima_start(*args):
    return ("Démarrage de la machine virtuelle Colima", ["colima", "start", "-p", PROFILE, *args], 600, False)


def action(nom: str, p: dict) -> str:
    c = colima_info()
    marche = c.get("etat") == "en_marche"
    if nom == "demarrer":
        etapes = ([] if marche else [e_colima_start()]) + ([e_demarrage_mes()] if ENV_FILE.exists() else [])
        JOB.start("Démarrer Colima et le MES", etapes)
        return "Démarrage lancé"
    if nom == "arreter":
        if not marche:
            raise ValueError("Colima est déjà arrêté")
        JOB.start("Arrêter le MES et Colima", [e_arret_mes(), e_colima_stop()])
        return "Arrêt lancé"
    if nom == "redemarrer":
        JOB.start("Redémarrer Colima et le MES",
                  ([e_arret_mes(), e_colima_stop()] if marche else []) + [e_colima_start(), e_demarrage_mes()])
        return "Redémarrage lancé"
    if nom == "disque":
        go = int(p.get("go", 0))
        actuel = int((c.get("disque") or 0) / GB)
        maxi = disque_max_go(c, mac_disque())
        if go <= actuel:
            raise ValueError(f"le disque fait déjà {actuel} Go : il peut seulement être agrandi")
        if go > maxi:
            raise ValueError(f"au plus {maxi} Go, pour garder {MAC_MARGE // GB} Go libres sur le Mac")
        JOB.start(f"Agrandir le disque de Colima à {go} Go",
                  ([e_arret_mes(), e_colima_stop()] if marche else []) + [e_colima_start("--disk", str(go)), e_demarrage_mes()])
        return "Agrandissement lancé"
    if nom == "ressources":
        cpu, mem = int(p.get("cpus", 0)), int(p.get("memoire", 0))
        ram = int(mac_memoire() / GB)
        if not 1 <= cpu <= (os.cpu_count() or 1):
            raise ValueError("nombre de processeurs hors limites")
        if not 2 <= mem <= max(2, ram - 4):
            raise ValueError(f"mémoire entre 2 et {max(2, ram - 4)} Go (4 Go laissés au Mac)")
        JOB.start(f"Colima : {cpu} processeurs, {mem} Go de mémoire",
                  ([e_arret_mes(), e_colima_stop()] if marche else [])
                  + [e_colima_start("--cpu", str(cpu), "--memory", str(mem)), e_demarrage_mes()])
        return "Changement lancé"
    if not marche:
        raise ValueError("Colima est arrêté : démarrez-le d'abord")
    if nom == "menage":
        JOB.start("Ménage Docker (cache de construction, images inutilisées)", [
            ("Suppression du cache de construction", ["docker", "builder", "prune", "-af"], 600, False),
            ("Suppression des images sans nom", ["docker", "image", "prune", "-f"], 300, False),
            ("Rendre la place au Mac", ["colima", "ssh", "-p", PROFILE, "--", "sudo", "fstrim", "-av"], 300, True),
        ])
        return "Ménage lancé"
    if nom == "rendre_place":
        JOB.start("Rendre au Mac la place libérée dans Colima",
                  [("Rendre la place au Mac", ["colima", "ssh", "-p", PROFILE, "--", "sudo", "fstrim", "-av"], 300, False)])
        return "Lancé"
    if nom == "heure":
        JOB.start("Remettre Colima à l'heure", [
            ("Remise à l'heure", lambda: run(["colima", "ssh", "-p", PROFILE, "--", "sudo", "date", "-s",
                                              f"@{int(time.time())}"], timeout=15)[1], 30, False)])
        return "Lancé"
    if nom == "reglages":
        vals = {}
        for k, v in (p.get("valeurs") or {}).items():
            if k not in REGLAGES:
                raise ValueError(f"réglage inconnu : {k}")
            v = str(v).strip()
            if not re.fullmatch(REGLAGES[k][1], v):
                raise ValueError(f"valeur refusée pour « {REGLAGES[k][0]} » : {v}")
            vals[k] = v
        if not vals:
            raise ValueError("aucun réglage")
        if not ENV_FILE.exists():
            raise ValueError(f"site.env introuvable dans {DEPLOY}")

        def ecrire():
            b = env_write(vals)
            return f"site.env modifié (copie : {b.name}) : " + ", ".join(f"{k}={v}" for k, v in vals.items())
        JOB.start("Appliquer les réglages de conservation",
                  [("Écriture de site.env", ecrire, 10, False), ("Relance des services concernés", compose("up", "-d"), 600, False)])
        return "Réglages en cours d'application"
    if nom == "surveillance":
        on = bool(p.get("active"))
        JOB.start(f"Surveillance technique {'activée' if on else 'coupée'}",
                  [("Surveillance technique", [str(DEPLOY / "mesctl"), "monitoring", "on" if on else "off"], 600, False)])
        return "Lancé"
    raise ValueError(f"action inconnue : {nom}")


# ----------------------------------------------------------------------------- surveillance de fond
def veille() -> None:
    """Toutes les 5 minutes : notification macOS si le disque se remplit (au plus une par heure et par alerte)."""
    deja: dict[str, float] = {}
    while True:
        time.sleep(300)
        try:
            if JOB.busy():
                continue
            c = colima_info()
            d = {"vm_disque": vm_disque() if c.get("etat") == "en_marche" else None, "mac_disque": mac_disque(),
                 "vm_decalage_heure": None}
            for a in alertes(d):
                if a["niveau"] == "rouge" and time.time() - deja.get(a["texte"][:30], 0) > 3600:
                    notifier("MES v3 : place disque", a["texte"])
                    deja[a["texte"][:30]] = time.time()
        except Exception as e:  # noqa: BLE001
            print("veille :", e, flush=True)


def demarrage_auto() -> None:
    if config().get("demarrage_auto") and colima_info().get("etat") == "arrete":
        try:
            action("demarrer", {})
        except Exception as e:  # noqa: BLE001
            print("démarrage automatique :", e, flush=True)


# ----------------------------------------------------------------------------- HTTP
HOTES = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
ORIGINES_CONSOLE = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status: int, payload, ctype="application/json; charset=utf-8", headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def hote_ok(self) -> bool:
        return self.headers.get("Host", "") in HOTES

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/ping":   # seul point lisible par la console /systeme (autre origine) : aucune donnée
            o = self.headers.get("Origin", "")
            h = {"Access-Control-Allow-Origin": o} if ORIGINES_CONSOLE.match(o) else {}
            return self.send(200, {"ok": True, "url": f"http://localhost:{PORT}/"}, headers=h)
        if not self.hote_ok():
            return self.send(403, {"erreur": "hôte refusé"})
        if path in ("/", "/index.html"):
            page = (HERE / "page.html").read_text().replace("__CLE__", cle())
            return self.send(200, page.encode(), "text/html; charset=utf-8")
        if path == "/api/etat":
            return self.send(200, etat())
        return self.send(404, {"erreur": "introuvable"})

    def do_POST(self):  # noqa: N802
        if not self.hote_ok():
            return self.send(403, {"erreur": "hôte refusé"})
        o = self.headers.get("Origin")
        if o and o not in {f"http://{h}" for h in HOTES}:
            return self.send(403, {"erreur": "origine refusée"})
        if not secrets.compare_digest(self.headers.get("X-Agent-Cle", ""), cle()):
            return self.send(403, {"erreur": "clé de l'agent absente ou fausse : rechargez la page"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return self.send(400, {"erreur": "requête illisible"})
        path = self.path.split("?")[0]
        try:
            if path == "/api/action":
                return self.send(200, {"ok": action(str(body.get("nom", "")), body.get("params") or {})})
            if path == "/api/config":
                config_set(demarrage_auto=bool(body.get("demarrage_auto")))
                journal_add("Démarrage automatique " + ("activé" if body.get("demarrage_auto") else "désactivé"), True)
                return self.send(200, {"ok": "Enregistré"})
        except (ValueError, RuntimeError) as e:
            return self.send(409, {"erreur": str(e)})
        return self.send(404, {"erreur": "introuvable"})


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    cle()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Agent Mac du MES v3 : http://localhost:{PORT}/  (dossier {DEPLOY})", flush=True)
    threading.Thread(target=veille, daemon=True).start()
    threading.Thread(target=demarrage_auto, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--etat":
        STATE.mkdir(parents=True, exist_ok=True)
        print(json.dumps(etat(), indent=2, ensure_ascii=False))
    else:
        main()
