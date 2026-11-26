"""Planning des postes : créer, modifier, supprimer, dupliquer, copier ou vider une semaine, copie automatique."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, planning
from .auth import User, current_user, require
from .planning import PlanningError, ShiftData, Stop

router = APIRouter(prefix="/api", tags=["planning"])


class StopIn(BaseModel):
    type: str = "arret"
    starts_at: datetime
    ends_at: datetime
    reason: str | None = None


class ShiftIn(BaseModel):
    starts_at: datetime
    ends_at: datetime
    team: str | None = None
    target_t: float | None = None
    recipe_id: int | None = None
    comment: str | None = None
    stops: list[StopIn] = []


class ShiftCreate(ShiftIn):
    line_ids: list[int] = Field(min_length=1)


class ShiftUpdate(ShiftIn):
    line_id: int


def _data(body: ShiftIn, line_id: int) -> ShiftData:
    return ShiftData(line_id, body.starts_at, body.ends_at, body.team or None, body.target_t, body.recipe_id, body.comment,
                     None, [Stop(x.type, x.starts_at, x.ends_at, (x.reason or "").strip() or None) for x in body.stops])


def _check(s: ShiftData) -> None:
    try:
        planning.check_shift(s)
    except PlanningError as e:
        raise HTTPException(400, str(e)) from e


async def _no_overlap(conn, s: ShiftData, exclude: int | None = None) -> None:
    other = await planning.overlapping(conn, s.line_id, s.starts_at, s.ends_at, exclude)
    if other:
        name = await conn.fetchval("SELECT name FROM catalog.machine WHERE id = $1", s.line_id)
        raise HTTPException(409, f"{name} : ce créneau chevauche un poste existant")


@router.post("/shifts")
async def create_shifts(body: ShiftCreate, user: User = Depends(require("chef_equipe"))):
    """Crée le même poste sur une ou plusieurs lignes."""
    now = planning.now_utc()
    if body.starts_at <= now:
        raise HTTPException(400, "Ce créneau a déjà commencé : choisissez un horaire à venir")
    async with db.pool().acquire() as conn, conn.transaction():
        ids = []
        for lid in dict.fromkeys(body.line_ids):
            s = _data(body, lid)
            _check(s)
            await _no_overlap(conn, s)
            ids.append(await planning.insert_shift(conn, s, user.username))
        await db.audit(conn, user.username, "postes_crees", "shift", ids[0], {"ids": ids, **body.model_dump(mode="json")})
    return {"ids": ids}


@router.put("/shifts/{sid}")
async def update_shift(sid: int, body: ShiftUpdate, user: User = Depends(require("chef_equipe"))):
    """Remplace le contenu d'un poste. Un poste commencé ne change plus que d'équipe, d'objectif et de commentaire."""
    now = planning.now_utc()
    async with db.pool().acquire() as conn, conn.transaction():
        cur = await planning.load_shift(conn, sid)
        if cur is None:
            raise HTTPException(404, "Poste introuvable")
        new = _data(body, body.line_id)
        new.template_id = cur.template_id
        if cur.starts_at <= now:
            fixed = lambda s: (s.line_id, s.starts_at, s.ends_at, s.recipe_id,  # noqa: E731
                               sorted((x.type, x.starts_at, x.ends_at, x.reason) for x in s.stops))
            if fixed(new) != fixed(cur):
                raise HTTPException(409, "Poste commencé : seuls l'équipe, l'objectif et le commentaire peuvent changer")
            await conn.execute("UPDATE mes.shift SET team = $2, target_t = $3, comment = $4 WHERE id = $1",
                               sid, new.team, new.target_t, new.comment)
            s = await conn.fetchrow("SELECT * FROM mes.shift WHERE id = $1", sid)
            from .workers import compute_shift
            await compute_shift(conn, s)
        else:
            if new.starts_at <= now:
                raise HTTPException(400, "Ce créneau a déjà commencé : choisissez un horaire à venir")
            _check(new)
            await _no_overlap(conn, new, exclude=sid)
            await conn.execute(
                """UPDATE mes.shift SET line_id = $2, starts_at = $3, ends_at = $4, team = $5, target_t = $6, recipe_id = $7,
                          comment = $8 WHERE id = $1""",
                sid, new.line_id, new.starts_at, new.ends_at, new.team, new.target_t, new.recipe_id, new.comment)
            await planning.replace_stops(conn, sid, new.line_id, new.stops, user.username)
        await db.audit(conn, user.username, "poste_modifie", "shift", sid, body.model_dump(mode="json"))
    return {"ok": True}


@router.delete("/shifts/{sid}")
async def delete_shift(sid: int, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn:
        s = await conn.fetchrow("SELECT starts_at FROM mes.shift WHERE id = $1", sid)
        if s is None:
            raise HTTPException(404, "Poste introuvable")
        if s["starts_at"] <= planning.now_utc():
            raise HTTPException(409, "Un poste commencé ou terminé ne peut pas être supprimé")
        await conn.execute("DELETE FROM mes.shift WHERE id = $1", sid)
        await db.audit(conn, user.username, "poste_supprime", "shift", sid)
    return {"ok": True}


class Target(BaseModel):
    line_id: int
    starts_at: datetime


class DuplicateBody(BaseModel):
    cibles: list[Target] = Field(min_length=1, max_length=200)
    remplacer: bool = False


@router.post("/shifts/{sid}/dupliquer")
async def duplicate_shift(sid: int, body: DuplicateBody, user: User = Depends(require("chef_equipe"))):
    """Duplique un poste (équipe, recette, objectif, arrêts) sur d'autres jours, lignes ou heures."""
    now = planning.now_utc()
    async with db.pool().acquire() as conn, conn.transaction():
        src = await planning.load_shift(conn, sid)
        if src is None:
            raise HTTPException(404, "Poste introuvable")
        out = {"cree": 0, "passe": 0, "occupe": 0, "hors_journee": 0}
        for t in body.cibles:
            out[await planning.place(conn, planning.moved(src, t.line_id, t.starts_at), body.remplacer, user.username, now)] += 1
        await db.audit(conn, user.username, "poste_duplique", "shift", sid, {**out, "remplacer": body.remplacer})
    return out


class CopyWeekBody(BaseModel):
    source: date
    destination: date
    line_ids: list[int] | None = None
    equipes: bool = True
    objectifs: bool = True
    arrets: bool = True
    remplacer: bool = False


@router.post("/planning/copier-semaine")
async def copy_week(body: CopyWeekBody, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn, conn.transaction():
        try:
            out = await planning.copy_week(conn, body.source, body.destination, line_ids=body.line_ids, teams=body.equipes,
                                           targets=body.objectifs, stops=body.arrets, replace=body.remplacer, actor=user.username)
        except PlanningError as e:
            raise HTTPException(400, str(e)) from e
        await db.audit(conn, user.username, "semaine_copiee", "planning", body.destination.isoformat(),
                       {**body.model_dump(mode="json"), **out})
    return out


class ClearWeekBody(BaseModel):
    semaine: date
    line_ids: list[int] | None = None


@router.post("/planning/vider-semaine")
async def clear_week(body: ClearWeekBody, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn, conn.transaction():
        try:
            n = await planning.clear_week(conn, body.semaine, body.line_ids)
        except PlanningError as e:
            raise HTTPException(400, str(e)) from e
        await db.audit(conn, user.username, "semaine_videe", "planning", body.semaine.isoformat(),
                       {"line_ids": body.line_ids, "supprimes": n})
    return {"supprimes": n}


class AutoBody(BaseModel):
    active: bool
    jour: int = Field(ge=0, le=6)
    heure: int = Field(ge=0, le=23)


@router.get("/planning/copie-auto")
async def get_auto(_: User = Depends(current_user)):
    async with db.pool().acquire() as conn:
        return await planning.auto_settings(conn)


@router.put("/planning/copie-auto")
async def put_auto(body: AutoBody, user: User = Depends(require("chef_equipe"))):
    async with db.pool().acquire() as conn:
        await planning.set_setting(conn, "planning_copie_auto", body.active)
        await planning.set_setting(conn, "planning_copie_jour", body.jour)
        await planning.set_setting(conn, "planning_copie_heure", body.heure)
        await db.audit(conn, user.username, "copie_auto_reglee", "planning", None, body.model_dump())
    return {"ok": True}
