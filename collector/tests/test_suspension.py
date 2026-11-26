from datetime import datetime, timedelta, timezone

from mes_collector.suspension import Suspension, scope_of


def iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_scope_of_strips_split_suffix():
    assert scope_of('ns=3;s="DB_Process_03"."P1204"."Valeur"', "P1204.Valeur") == "DB_Process"
    assert scope_of('ns=3;s="DB_Moteurs"."M101"."Courant"', "M101.Courant") == "DB_Moteurs"
    assert scope_of("ns=2;i=1234", "Ligne1.Debit") == "Ligne1"


def test_global_and_scopes():
    s = Suspension()
    assert not s.blocks("DB_Moteurs")
    s.update({"global": None, "db": {"DB_Process": {"jusqu_a": None}}})
    assert s.blocks("DB_Process") and not s.blocks("DB_Moteurs")
    s.update('{"global": {"jusqu_a": null}, "db": {}}')
    assert s.blocks("DB_Moteurs")


def test_expiry_and_resume_report():
    s = Suspension()
    s.update({"global": {"jusqu_a": iso(10)}, "db": {"DB_Lignes": {"jusqu_a": iso(10)}}})
    assert s.blocks("X")
    resumed_global, resumed = s.update({"global": {"jusqu_a": iso(-1)}, "db": {}})
    assert resumed_global and resumed == {"DB_Lignes"}
    assert not s.blocks("DB_Lignes")


def test_empty_setting():
    s = Suspension()
    assert s.update(None) == (False, set())
