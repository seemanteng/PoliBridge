"""Response deadlines (rules C1, D1-D3): Emergency vs High timings, what each clock counts from, late discovery,
exploits that emerge later, and task-specific overrides."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.models import Advisory, Notification
from app.services import assignment, intake, views
from .conftest import STRUTS
from .test_api import client
from .test_scenarios import kinds, task_for

NGINX = dict(cve="CVE-2017-7529", product="nginx", affected_versions=["1.10.0-1.10.3"], cvss=9.8, kev=False,
             fixed_version="1.10.4")
STRUTS_HIGH = {**STRUTS, "cvss": 8.0, "kev": False}  # serious but not critical, no known exploit yet


def test_critical_internet_facing_is_emergency_with_48h_fix(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    assert t.impact == "High" and t.emergency and assignment.profile(t) == "Emergency"
    assert t.ack_due - t.created_at == timedelta(hours=4)
    assert t.remediate_due - t.created_at == timedelta(hours=48) and t.interim_due is None


def test_internal_high_keeps_7_days_with_48h_interim(db, who):
    ob = intake.ingest_advisory(db, **NGINX)
    t = task_for(db, ob, "HR Intranet")
    assert t.impact == "High" and not t.emergency                       # critical, but not internet-facing
    assert t.ack_due - t.created_at == timedelta(hours=4)
    assert t.interim_due - t.created_at == timedelta(hours=48)
    assert t.remediate_due - t.created_at == timedelta(days=7)
    rows = {r["key"]: r for r in views.response_rules(db, t)["rows"]}
    assert set(rows) == {"acknowledge", "progress", "interim", "remediate"} and rows["remediate"]["window"] == "7 days"


def test_fix_deadline_counts_from_vendor_release_not_discovery(db, who):
    released = clock.now() - timedelta(hours=20)
    ob = intake.ingest_advisory(db, **STRUTS, published_at=released)
    t = task_for(db, ob, "Credit Report Portal")
    assert t.remediate_due == released + timedelta(hours=48)          # 28h left, not 48h
    assert t.ack_due == t.created_at + timedelta(hours=4)               # acknowledgement counts from assignment
    assert t.deadline_basis["trigger"] == "vendor_release" and not t.deadline_basis["overdue_at_discovery"]
    rr = views.response_rules(db, t)
    assert rr["trigger_label"] == "the vendor released the fix" and rr["trigger_at"] == released.isoformat()


def test_found_after_the_deadline_escalates_at_once(db, who):
    released = clock.now() - timedelta(days=3)
    ob = intake.ingest_advisory(db, **STRUTS, published_at=released)
    t = task_for(db, ob, "Credit Report Portal")
    assert t.remediate_due == released + timedelta(hours=48) and t.remediate_due < clock.now()  # true deadline kept
    assert t.deadline_basis["overdue_at_discovery"] and t.board_flag
    assert "deadline_missed" in kinds(db, t)                                                    # D3, no waiting
    assert t.interim_due == clock.now() + timedelta(hours=4) and t.deadline_basis["interim_reset"]
    rows = {r["key"]: r for r in views.response_rules(db, t)["rows"]}
    assert rows["interim"]["window"] == "4h (found late)" and "Already past" in rows["remediate"]["why"]
    c = client(db)
    items = c.get("/api/alerts").json()["items"]
    assert any(i["type"] == "found_late" and i["task_id"] == t.id for i in items)
    from app.main import app
    app.dependency_overrides.clear()


def test_reassignment_never_resets_either_clock(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    ack, due = t.ack_due, t.remediate_due
    clock.advance(hours=2)
    assignment.reject_task(db, t, who("atan"), "not my system")
    assert t.owner_id == who("dortiz").id and (t.ack_due, t.remediate_due) == (ack, due)
    assignment.acknowledge(db, t, who("dortiz"))
    assert t.remediate_due == due


def test_progress_check_at_24h_is_a_request_not_a_deadline(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    assignment.acknowledge(db, t, who("atan"))
    clock.advance(hours=23)
    assignment.tick_all(db)
    assert "progress_check" not in kinds(db, t)
    clock.advance(hours=2)
    assignment.tick_all(db)
    assignment.tick_all(db)
    assert kinds(db, t).count("progress_check") == 1 and "deadline_missed" not in kinds(db, t)
    note = db.scalar(select(Notification).where(Notification.kind == "progress_check", Notification.task_id == t.id))
    assert note.person_id == who("atan").id and "workaround" in note.message


def test_exploit_emerging_later_makes_it_emergency_but_never_later(db, who):
    ob = intake.ingest_advisory(db, **STRUTS_HIGH)
    t = task_for(db, ob, "Credit Report Portal")
    assert t.impact == "High" and not t.emergency and t.remediate_due - t.created_at == timedelta(days=7)
    adv = db.get(Advisory, ob.advisory_id)
    adv.kev = True
    exploit = clock.now() + timedelta(days=1)
    clock.advance(days=1)
    assignment.retime_for_exploit(db, adv, exploit)
    assert t.emergency and t.remediate_due == exploit + timedelta(hours=48)
    assert t.deadline_basis["trigger"] == "exploit_known" and "exploit_known" in kinds(db, t)
    due = t.remediate_due
    assignment.retime_for_exploit(db, adv, exploit + timedelta(days=5))  # a later date would lengthen it: ignored
    assert t.remediate_due == due


def test_override_can_only_shorten_and_needs_authority(db, who):
    ob = intake.ingest_advisory(db, **NGINX)
    t = task_for(db, ob, "HR Intranet")
    sooner, later = clock.now() + timedelta(days=2), t.remediate_due + timedelta(days=1)
    with pytest.raises(PermissionError):
        assignment.override_deadline(db, t, sooner, "regulator asked", who("ofoo"))
    with pytest.raises(ValueError, match="risk acceptance"):
        assignment.override_deadline(db, t, later, "more time", who("crhodes"))
    with pytest.raises(ValueError, match="reason"):
        assignment.override_deadline(db, t, sooner, " ", who("crhodes"))
    assignment.override_deadline(db, t, sooner, "Emergency directive from the regulator", who("crhodes"))
    assert t.remediate_due == sooner and t.interim_due <= sooner
    rr = views.response_rules(db, t)
    rem = next(r for r in rr["rows"] if r["key"] == "remediate")
    assert rr["override"]["by"] == "Casey Rhodes" and rem["window"] == "Task-specific deadline"
    assert "regulator" in rem["why"] and "deadline_overridden" in kinds(db, t)


def test_override_api(db, who):
    ob = intake.ingest_advisory(db, **NGINX)
    t = task_for(db, ob, "HR Intranet")
    c = client(db)
    due = (clock.now() + timedelta(days=3)).isoformat()
    r = c.post(f"/api/tasks/{t.id}/deadline-override", json={"due": due, "reason": "Board directive"})
    assert r.status_code == 200, r.text
    detail = c.get(f"/api/tasks/{t.id}").json()
    assert detail["response_rules"]["override"]["reason"] == "Board directive"
    r = c.post(f"/api/tasks/{t.id}/deadline-override",
               json={"due": (clock.now() + timedelta(days=30)).isoformat(), "reason": "more time"})
    assert r.status_code == 400
    from app.main import app
    app.dependency_overrides.clear()
