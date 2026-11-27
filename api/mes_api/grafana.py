"""Préférences Grafana de chaque utilisateur : langue de l'interface Grafana et tableau d'accueil
dans la langue choisie dans le MES (tableaux anglais = uid suffixé « -en »).

Appelé au contrôle d'accès de nginx (/api/session/proxy) : la langue est lue en base au plus toutes
les CACHE_S secondes par utilisateur, et Grafana n'est appelé que lorsqu'elle a changé."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request

from . import db

log = logging.getLogger("api.grafana")
URL = os.environ.get("GRAFANA_URL", "http://grafana:3000")
CACHE_S = 30
# langue MES → (langue de l'interface Grafana, tableau d'accueil)
PREFS = {"fr": ("fr-FR", "mes-performance"), "en": ("en-US", "mes-performance-en")}

_lu: dict[str, tuple[float, str]] = {}      # utilisateur → (instant de lecture, langue)
_applique: dict[str, str] = {}              # utilisateur → langue déjà envoyée à Grafana


def oublier(username: str) -> None:
    """La langue de l'utilisateur vient de changer : la relire au prochain accès."""
    _lu.pop(username, None)


async def _langue(username: str) -> str:
    t, lang = _lu.get(username, (0.0, ""))
    if time.monotonic() - t > CACHE_S:
        async with db.pool().acquire() as conn:
            lang = await conn.fetchval("SELECT langue FROM mes.app_user WHERE username = $1", username) or "fr"
        lang = lang if lang in PREFS else "fr"
        _lu[username] = (time.monotonic(), lang)
    return lang


def _patch(username: str, role: str, lang: str) -> None:
    langue, accueil = PREFS[lang]
    req = urllib.request.Request(
        URL + "/api/user/preferences", method="PATCH",
        data=json.dumps({"language": langue, "homeDashboardUID": accueil}).encode(),
        headers={"Content-Type": "application/json", "X-WEBAUTH-USER": username, "X-WEBAUTH-ROLE": role})
    with urllib.request.urlopen(req, timeout=3) as r:
        r.read()


async def synchroniser(username: str, role: str) -> None:
    """Aligne les préférences Grafana sur la langue MES ; un échec est réessayé au prochain accès."""
    try:
        lang = await _langue(username)
        if _applique.get(username) == lang:
            return
        await asyncio.to_thread(_patch, username, role, lang)
        _applique[username] = lang
        log.info("préférences Grafana de %s : %s", username, lang)
    except Exception as e:  # Grafana pas encore prêt, base indisponible… : l'accès n'est pas bloqué
        log.warning("préférences Grafana de %s non appliquées : %s", username, e)
