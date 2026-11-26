import pytest
from fastapi import HTTPException

from mes_api import auth
from mes_api.routes_recipes import _validate, compare_values

TDEF = {"params": [
    {"name": "Vitesse", "label": "Vitesse", "datatype": "Float", "min": 0, "max": 100, "default": 50, "unit": "%"},
    {"name": "Temps", "label": "Temps", "datatype": "Int32", "min": 10, "max": 600, "default": 120, "unit": "s"},
]}


def test_validate_defaults_and_rounding():
    assert _validate(TDEF, {"Temps": "119.6"}) == {"Vitesse": 50.0, "Temps": 120}


def test_validate_bounds():
    with pytest.raises(HTTPException) as e:
        _validate(TDEF, {"Vitesse": 120})
    assert "hors bornes" in e.value.detail


def test_compare_tolerance():
    lines = compare_values(TDEF, {"Vitesse": 50, "Temps": 120}, {"Vitesse": 50.3, "Temps": 130}, 0.5)
    assert [l["ecart"] for l in lines] == [False, True]


def test_password_and_token():
    h = auth.hash_password("secret123")
    assert auth.check_password("secret123", h) and not auth.check_password("autre", h)
    u = auth.User("jdupont", "chef_equipe", "J. Dupont")
    assert auth.read_token(auth.make_token(u)) == u
    t = auth.make_token(u)
    assert auth.read_token(t[:-1] + ("0" if t[-1] != "0" else "1")) is None   # signature altérée


def test_require_roles():
    op, chef, admin = (auth.User("a", r, None) for r in ("operateur", "chef_equipe", "admin"))
    dep = auth.require("chef_equipe")
    assert dep(chef) is chef and dep(admin) is admin
    with pytest.raises(HTTPException):
        dep(op)
    with pytest.raises(HTTPException):   # sans rôle : administrateur seulement
        auth.require()(chef)


def test_acces_sans_identifiant(monkeypatch):
    from dataclasses import replace

    from mes_api import auth

    monkeypatch.setattr(auth, "settings", replace(auth.settings, auth_disabled=True))

    class Req:
        cookies = {}

    u = auth.current_user(Req())
    assert u.role == "admin"
