"""Suspension de l'historisation, pilotée depuis la console d'exploitation.

Le réglage est lu dans mes.setting (clé « historisation ») :
    {"global": null | {"jusqu_a": "<ISO 8601>" | null, "par": "...", "depuis": "..."},
     "db": {"DB_Process": {...même forme...}, ...}}
Une entrée présente suspend l'écriture de son périmètre jusqu'à « jusqu_a » (null : jusqu'à
réactivation). Le collecteur garde sa session OPC UA et ses abonnements : seules les écritures
dans l'historian sont retenues. À la reprise, la dernière valeur de chaque tag est réécrite pour
ancrer les lectures.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger("suspension")
KEY = "historisation"
_SUFFIX = re.compile(r"_\d+$")
_DB = re.compile(r's="([^"]+)"')


def scope_of(node_id: str, name: str = "") -> str:
    """Périmètre d'un tag : son DB, premier segment du NodeId Siemens (ns=3;s="DB"."Instance"."Membre"),
    sans le suffixe de découpage (DB_Process_03 -> DB_Process). À défaut, premier segment du nom."""
    m = _DB.search(node_id or "")
    return _SUFFIX.sub("", m.group(1) if m else (name or node_id).split(".", 1)[0])


def _active(entry, now: datetime) -> bool:
    if not isinstance(entry, dict):
        return False
    until = entry.get("jusqu_a")
    if not until:
        return True
    try:
        return datetime.fromisoformat(until) > now
    except ValueError:
        return True


class Suspension:
    def __init__(self) -> None:
        self.global_on = False
        self.scopes: set[str] = set()

    def update(self, raw) -> tuple[bool, set[str]]:
        """Applique un nouveau réglage ; renvoie (reprise globale, périmètres repris)."""
        if isinstance(raw, str):
            raw = json.loads(raw)
        raw = raw or {}
        now = datetime.now(timezone.utc)
        g = _active(raw.get("global"), now)
        scopes = {k for k, v in (raw.get("db") or {}).items() if _active(v, now)}
        resumed_global = self.global_on and not g
        resumed = self.scopes - scopes
        if g != self.global_on or scopes != self.scopes:
            log.info("historisation : %s ; périmètres suspendus : %s",
                     "suspendue" if g else "active", ", ".join(sorted(scopes)) or "aucun")
        self.global_on, self.scopes = g, scopes
        return resumed_global, resumed

    def blocks(self, scope: str) -> bool:
        return self.global_on or scope in self.scopes
