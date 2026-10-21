from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clock import clock
from app.db import Base
from app.models import Task, Finding, BoardItem, Claim, Evidence, Obligation
from app.seed import replay
from app.services import views, assurance


@pytest.fixture
def rdb():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    replay.load_baseline(s)
    replay.run_storyline(s)
    yield s
    s.close()
    clock.unfreeze()


def test_replay_end_state(rdb):
    db = rdb
    assert clock.now() == datetime(2017, 3, 15, 14, 20)
    o = views.oversight(db)
    assert o["banner"]["text"].startswith("obligation cannot close: 2 assets remain Cannot Determine")
    assert [r["ref"] for r in o["overdue"]][0].startswith("TSK-")
    acis = o["overdue"][0]
    assert "Risk owner" in acis["escalated_to"] and acis["owner_note"] == "rerouted · BU head"
    assert o["canary_failure"]["asset"].startswith("ACIS") and o["kpis"]["lapsed_monitoring"] == 1
    assert o["kpis"]["canary"]["runs"] == 3 and o["kpis"]["canary"]["passed"] == 2


def test_replay_estate_matches_advisory_page(rdb):
    db = rdb
    ob = db.scalar(select(Obligation).where(Obligation.kind == "advisory", Obligation.is_test == False))  # noqa: E712
    est = views.estate(db, ob)
    assert est["counts"] == {"applies": 2, "cannot_determine": 2, "does_not_apply": 2}
    by = {r["asset"].split(" ·")[0]: r for r in est["rows"]}
    assert by["ACIS"]["state"] == "cannot_determine" and "rejected" in by["ACIS"]["basis"]
    assert by["Batch reporting server"]["stale"] and by["Batch reporting server"]["state"] == "cannot_determine"
    assert by["Credit File API"]["task_status"].startswith("Remediated")
    assert by["Member portal"]["task_status"].startswith("Fixed")
    assert est["gate"]["approved"] == 3 and est["gate"]["in_review"] == 1


def test_replay_lapse_reopened_dependents(rdb):
    db = rdb
    cert = db.scalar(select(Claim).where(Claim.claim_type == "monitoring"))
    d = views.claim_detail(db, cert)
    assert d["status"] == "expired" and all(x["reopened"] for x in d["dependents"]) and len(d["dependents"]) == 2
    assert db.scalar(select(BoardItem)).status == "reopened"
    acis = next(t for t in db.scalars(select(Task)) if t.component == "struts2-core" and "ACIS" in db.get(
        __import__("app.models", fromlist=["Asset"]).Asset, t.asset_id).name and not t.is_test)
    td = views.task_detail(db, acis)
    assert td["interim"]["state"] == "reopened" and td["disposition"]["overdue"]
    titles = [e["title"] for e in td["timeline"]]
    assert any("Rerouted to Dana Whitfield" in t for t in titles) and "Deadline missed — escalated" in titles
    ev = db.scalar(select(Evidence).where(Evidence.purpose == "absence"))
    checks = {c["check"]: c["pass"] for c in __import__("app.services.closure", fromlist=["x"]).evidence_checks(db, ev)["checks"]}
    assert checks == {"Asset": True, "Component scope": False, "Method": False, "Timestamp": True, "Independence": True}


def test_canary_view_explains_routing_failure(rdb):
    c = views.canary_view(rdb)
    failed = next(r for r in c["runs"] if r["status"] == "failed")
    assert [s["result"] for s in failed["steps"]] == ["pass", "pass", "fail", "not_run", "pass"]
    assert failed["finding"] and failed["finding"]["assignee"] == "Dana Whitfield"
