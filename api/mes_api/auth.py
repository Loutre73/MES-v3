"""Authentification : comptes locaux et Active Directory (LDAP), sessions signées par cookie.

Rôles : operateur, chef_equipe, maintenance, admin. L'administrateur a tous les droits.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from . import db
from .config import settings

log = logging.getLogger("auth")
COOKIE = "mes_session"
ROLES = ("operateur", "chef_equipe", "maintenance", "admin")
ROLE_RANK = {"operateur": 0, "maintenance": 1, "chef_equipe": 2, "admin": 3}


@dataclass(frozen=True)
class User:
    username: str
    role: str
    display_name: str | None = None


# ----------------------------------------------------------------------------- mots de passe
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, h = stored.split("$")
    calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1)
    return hmac.compare_digest(calc, base64.b64decode(h))


# ----------------------------------------------------------------------------- jetons de session
def make_token(user: User) -> str:
    payload = json.dumps({"u": user.username, "r": user.role, "n": user.display_name,
                          "exp": int(time.time()) + settings.session_hours * 3600}).encode()
    body = base64.urlsafe_b64encode(payload).decode()
    sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_token(token: str) -> User | None:
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    data = json.loads(base64.urlsafe_b64decode(body))
    if data["exp"] < time.time():
        return None
    return User(data["u"], data["r"], data.get("n"))


def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE)
    user = read_token(token) if token else None
    if user is None:
        raise HTTPException(401, "Connexion requise")
    return user


def require(*roles: str):
    """Dépendance FastAPI : l'utilisateur doit avoir l'un des rôles ; sans rôle, admin seulement."""
    def dep(user: User = Depends(current_user)) -> User:
        if user.role != "admin" and (not roles or user.role not in roles):
            raise HTTPException(403, "Droits insuffisants pour cette action")
        return user
    return dep


# ----------------------------------------------------------------------------- annuaire AD
def _ldap_group_roles() -> dict[str, str]:
    out = {}
    for part in settings.ldap_group_roles.split(";"):
        if "=" in part:
            g, r = part.split("=", 1)
            if r.strip() in ROLES:
                out[g.strip().lower()] = r.strip()
    return out


def _ldap_login(username: str, password: str) -> tuple[str, str | None] | None:
    """Vérifie le mot de passe auprès de l'AD et renvoie (rôle, nom affiché) selon les groupes."""
    import ldap3  # import local : inutile si l'AD n'est pas configuré

    server = ldap3.Server(settings.ldap_url, get_info=ldap3.NONE, connect_timeout=5)
    bind_user = f"{settings.ldap_domain}\\{username}" if settings.ldap_domain else username
    try:
        conn = ldap3.Connection(server, user=bind_user, password=password, auto_bind=True, receive_timeout=10)
    except ldap3.core.exceptions.LDAPException as e:
        log.info("échec de connexion AD pour %s : %s", username, e)
        return None
    try:
        conn.search(settings.ldap_base_dn, f"(sAMAccountName={ldap3.utils.conv.escape_filter_chars(username)})",
                    attributes=["memberOf", "displayName"])
        if not conn.entries:
            return None
        entry = conn.entries[0]
        groups = [str(g).split(",")[0].removeprefix("CN=").lower() for g in entry.memberOf] if "memberOf" in entry else []
        mapping = _ldap_group_roles()
        roles = [mapping[g] for g in groups if g in mapping]
        if not roles:
            return None  # authentifié mais dans aucun groupe MES
        role = max(roles, key=lambda r: ROLE_RANK[r])
        name = str(entry.displayName) if "displayName" in entry else None
        return role, name
    finally:
        conn.unbind()


async def authenticate(username: str, password: str) -> User | None:
    username = username.strip()
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM mes.app_user WHERE lower(username) = lower($1)", username)
        if row and row["source"] == "local":
            if row["active"] and check_password(password, row["password_hash"]):
                await conn.execute("UPDATE mes.app_user SET last_login = now() WHERE username = $1", row["username"])
                return User(row["username"], row["role"], row["display_name"])
            return None
        if settings.ldap_url:
            res = await asyncio.to_thread(_ldap_login, username, password)
            if res is None:
                return None
            role, name = res
            if row and not row["active"]:
                return None
            await conn.execute(
                """INSERT INTO mes.app_user (username, display_name, role, source, last_login)
                   VALUES ($1, $2, $3, 'ldap', now())
                   ON CONFLICT (username) DO UPDATE SET display_name = $2, role = $3, last_login = now()""",
                username, name, role)
            return User(username, role, name)
    return None


async def ensure_admin() -> None:
    """Crée le compte admin local au premier démarrage (mot de passe ADMIN_PASSWORD)."""
    if not settings.admin_password:
        return
    async with db.pool().acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM mes.app_user WHERE username = 'admin'")
        if not exists:
            await conn.execute(
                "INSERT INTO mes.app_user (username, display_name, role, password_hash) VALUES ('admin', 'Administrateur', 'admin', $1)",
                hash_password(settings.admin_password))
            log.info("compte admin créé")
