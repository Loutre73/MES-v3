"""Contrôle du collecteur pour la console d'exploitation (port interne 9109, non publié).

GET /etat           état de la session OPC UA, débits, historisation
GET /historisation  état de l'historisation (globale et par DB)
PUT /historisation  {"actif": bool, "duree_min": int|null} et/ou {"dbs": {"DB_Moteurs": false}}

Historisation suspendue : le collecteur reste abonné et suit les valeurs, mais n'écrit plus
dans l'historian. À la reprise, la dernière valeur de chaque tag est réécrite (point d'ancrage).
L'état est conservé dans le volume du tampon disque : il survit à un redémarrage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("control")

_DB_RE = re.compile(r'"([^"]+)"')


def perimeter(node_id: str) -> str:
    """Périmètre d'un tag : son DB, sans le suffixe de découpage (DB_Process_03 -> DB_Process)."""
    m = _DB_RE.search(node_id or "")
    name = m.group(1) if m else "Autres"
    return re.sub(r"_\d+$", "", name)


class Historisation:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.active = True
        self.until: float | None = None        # reprise automatique (epoch) si suspendue pour une durée
        self.off_dbs: set[str] = set()
        self.changed_at: float | None = None
        self.load()

    def load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.active = bool(d.get("actif", True))
            self.until = d.get("jusqua")
            self.off_dbs = set(d.get("dbs_suspendus", []))
            self.changed_at = d.get("modifie")
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"actif": self.active, "jusqua": self.until, "dbs_suspendus": sorted(self.off_dbs),
                                       "modifie": self.changed_at}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("état de l'historisation non enregistré : %s", e)

    def tick(self) -> bool:
        """Reprise automatique à l'échéance ; renvoie vrai si l'historisation vient de reprendre."""
        if not self.active and self.until is not None and time.time() >= self.until:
            self.active, self.until = True, None
            self.changed_at = time.time()
            self.save()
            log.info("historisation reprise automatiquement")
            return True
        return False

    def allows(self, db: str) -> bool:
        return self.active and db not in self.off_dbs

    def update(self, body: dict) -> list[str] | None:
        """Applique une demande ; renvoie les périmètres qui reprennent (None = tous)."""
        resumed: list[str] | None = []
        if "actif" in body:
            on = bool(body["actif"])
            if on and not self.active:
                resumed = None
            self.active = on
            d = body.get("duree_min")
            if not on and d is not None:
                d = int(d)
                if d <= 0 or d > 7 * 24 * 60:
                    raise ValueError("durée de suspension invalide (1 min à 7 jours)")
            self.until = None if on or not d else time.time() + 60 * d
        for db, on in (body.get("dbs") or {}).items():
            if on and db in self.off_dbs:
                self.off_dbs.discard(db)
                if resumed is not None:
                    resumed.append(db)
            elif not on:
                self.off_dbs.add(db)
        self.changed_at = time.time()
        self.save()
        return resumed

    def state(self) -> dict:
        iso = (lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds") if t else None)
        return {"actif": self.active, "jusqua": iso(self.until), "dbs_suspendus": sorted(self.off_dbs),
                "modifie": iso(self.changed_at)}


class Rates:
    """Débits par seconde à partir de compteurs, sur les dernières secondes."""

    def __init__(self):
        self.prev: dict[str, tuple[float, float]] = {}
        self.values: dict[str, float] = {}

    def update(self, counters: dict[str, float]) -> None:
        now = time.monotonic()
        for k, v in counters.items():
            p = self.prev.get(k)
            if p and now > p[1]:
                self.values[k] = round(max(0.0, (v - p[0]) / (now - p[1])), 1)
            self.prev[k] = (v, now)


async def serve(port: int, handler) -> asyncio.base_events.Server:
    """Serveur HTTP minimal : une requête par connexion, corps JSON."""

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:] if h)}
            n = int(headers.get("content-length") or 0)
            if n > 100_000:
                raise ValueError("corps trop grand")
            raw = await asyncio.wait_for(reader.readexactly(n), 10) if n else b""
            body = json.loads(raw) if raw else {}
            status, payload = await handler(method, target, body)
        except (ValueError, json.JSONDecodeError) as e:
            status, payload = 400, {"erreur": str(e)}
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        except Exception as e:  # noqa: BLE001
            log.exception("requête de contrôle en erreur")
            status, payload = 500, {"erreur": str(e)}
        data = json.dumps(payload, ensure_ascii=False).encode()
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Error"}.get(status, "OK")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\n"
                     f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
        try:
            await writer.drain()
        finally:
            writer.close()

    return await asyncio.start_server(client, "0.0.0.0", port)
