"""Component 5: evidence verification and closure. The two-function sign-off (report §6.5) and what happens when
the two sign-offs disagree."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.models import Claim, Finding, Notification, Task
from app.services import assurance, closure, intake, views
from .conftest import STRUTS, sign_required
from .test_api import client, h
from .test_scenarios import claim_for, kinds, task_for


def _fixed_portal(db, who, version="2.3.32"):
    """Credit Report Portal patched by its owner, evidence submitted and accepted by an independent reviewer."""
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal", "remediation")
    closure.mark_fixed(db, t, who("atan"))
    clock.advance(hours=1)
    ev = closure.submit_evidence(db, t, who("atan"), purpose="remediation", asset_name="Credit Report Portal",
                                 component="Apache Struts", scope=["/opt/portal/**"], method="authenticated_scan",
                                 observed_version=version, timestamp=clock.now())
    closure.review_evidence(db, ev, who("rverma"), True)
    return ob, t, ev


def _absence(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "ACIS Dispute Portal")
    ev = closure.submit_evidence(db, t, who("dortiz"), purpose="absence", asset_name="ACIS Dispute Portal",
                                 component="Apache Struts", scope=["/opt/acis/**"], method="authenticated_scan",
                                 timestamp=clock.now())
    closure.review_evidence(db, ev, who("rverma"), True)
    return ob, t, ev


# ---------- the two sign-offs ----------
def test_every_remediation_needs_the_asset_owner_and_information_security(db, who):
    ob, t, ev = _fixed_portal(db, who)
    assert closure.needed_signoffs(t)[:2] == ["system_owner", "security"]
    assert t.status == "fixed_pending_review" and {"system_owner", "security"} <= set(closure.missing_signoffs(t))
    sign_required(db, t)
    assert t.status == "closed" and t.disposition == "remediated"
    claim = db.get(Claim, ev.claim_id)
    so = views.claim_signoffs(db, claim)
    owner = next(s for s in so["signoffs"] if s["key"] == "system_owner")
    assert owner["signed_by"] == "Alex Tan" and owner["record_version"] == "2.3.32" and owner["record_source"] == "SBOM"
    assert next(s for s in so["signoffs"] if s["key"] == "security")["signed_by"] == "Casey Rhodes"


def test_low_impact_system_still_needs_both_functions(db, who):
    """Before, a system with no C2-C4 trigger closed on the reviewer alone. Now both functions always sign."""
    ob = intake.ingest_advisory(db, **STRUTS)
    t = next(x for x in db.scalars(select(Task).where(Task.obligation_id == ob.id)))
    t.required_reviewers = []
    assert closure.needed_signoffs(t) == ["system_owner", "security"]


def test_signoff_needs_evidence_and_the_right_person(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal", "remediation")
    with pytest.raises(ValueError, match="none yet"):
        closure.sign_required_review(db, t, "security", who("crhodes"))
    closure.mark_fixed(db, t, who("atan"))
    clock.advance(hours=1)
    closure.submit_evidence(db, t, who("atan"), purpose="remediation", asset_name="Credit Report Portal",
                            component="Apache Struts", scope=["/opt/portal/**"], method="authenticated_scan",
                            observed_version="2.3.32", timestamp=clock.now())
    with pytest.raises(PermissionError):
        closure.sign_required_review(db, t, "security", who("pnair"))  # belongs to the CISO
    with pytest.raises(ValueError, match="record comes from"):
        closure.sign_required_review(db, t, "system_owner", who("atan"), record_version="2.3.32")
    with pytest.raises(ValueError, match="version"):
        closure.sign_required_review(db, t, "system_owner", who("atan"), record_source="sbom")


def test_signoffs_belong_to_the_evidence_they_were_given_on(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal", "remediation")
    closure.mark_fixed(db, t, who("atan"))
    clock.advance(hours=1)
    sub = dict(purpose="remediation", asset_name="Credit Report Portal", component="Apache Struts",
               scope=["/opt/portal/**"], method="authenticated_scan", observed_version="2.3.32")
    ev = closure.submit_evidence(db, t, who("atan"), **sub, timestamp=clock.now())
    closure.sign_required_review(db, t, "security", who("crhodes"))  # signed while the evidence awaits review
    assert "security" not in closure.missing_signoffs(t)
    closure.review_evidence(db, ev, who("rverma"), False, "re-scan after the restart")
    ev2 = closure.submit_evidence(db, t, who("atan"), **sub, timestamp=clock.now())
    assert closure.current_evidence(db, t).id == ev2.id
    assert "security" in closure.missing_signoffs(t)  # that sign-off was on the rejected evidence


# ---------- disagreement ----------
def test_owner_record_contradicting_the_evidence_is_a_disagreement(db, who):
    ob, t, ev = _fixed_portal(db, who)
    closure.sign_required_review(db, t, "security", who("crhodes"))
    closure.sign_required_review(db, t, "system_owner", who("atan"), record_version="2.3.31", record_source="cmdb")
    # the claim is Cannot Determine: nothing closed, evidence set aside, every sign-off cleared
    assert t.status == "open" and ev.status == "rejected" and "Sign-offs disagree" in ev.reasons[0]
    assert ev.claim_id is None and t.reviewer_signoffs == {}
    d = t.signoff_dispute
    assert d["auto"] and d["key"] == "system_owner" and "2.3.31" in d["reason"] and not d["resolved_at"]
    # both functions get a task, and the owner's manager is told at once
    tasks = db.scalars(select(Finding).where(Finding.kind == "signoff_disagreement")).all()
    assert {f.assigned_to_id for f in tasks} == {who("atan").id, who("crhodes").id}
    told = {n.person_id for n in db.scalars(select(Notification).where(Notification.kind == "signoff_disputed"))}
    assert told == {who("mlee").id}
    assert claim_for(db, ob, "Credit Report Portal").value == "applies"  # still affected: the fix is unproven


def test_information_security_can_disagree_with_a_reason(db, who):
    ob, t, ev = _fixed_portal(db, who)
    with pytest.raises(ValueError, match="why"):
        closure.dispute_signoff(db, t, "security", who("crhodes"), "  ")
    with pytest.raises(PermissionError):
        closure.dispute_signoff(db, t, "security", who("atan"), "not mine to dispute")
    closure.dispute_signoff(db, t, "security", who("crhodes"), "scan credentials could not read the webapps folder")
    assert t.status == "open" and t.signoff_dispute["by"] == "Casey Rhodes" and not t.signoff_dispute["auto"]
    assert "signoff_disagreement" in kinds(db, t)
    tl = views.task_timeline(db, t)
    assert any(e["title"].startswith("Sign-offs disagree") and e["tone"] == "bad" for e in tl)


def test_unresolved_disagreement_escalates_once_then_resolves_on_agreement(db, who):
    ob, t, ev = _fixed_portal(db, who)
    closure.dispute_signoff(db, t, "security", who("crhodes"), "scan scope not credible")
    clock.advance(hours=47)
    assert assurance.sweep(db)["escalated_disputes"] == []
    clock.advance(hours=2)
    assert assurance.sweep(db)["escalated_disputes"] == [t.id]
    up = {n.person_id for n in db.scalars(select(Notification).where(Notification.kind == "dispute_unresolved"))}
    assert {who("rpark").id, who("swhitfield").id} <= up and t.board_flag
    assert assurance.sweep(db)["escalated_disputes"] == []  # escalated once
    # fresh evidence, both functions agree: the task closes and the disagreement is resolved
    ev2 = closure.submit_evidence(db, t, who("atan"), purpose="remediation", asset_name="Credit Report Portal",
                                  component="Apache Struts", scope=["/opt/portal/**"], method="direct_version_check",
                                  observed_version="2.3.32", timestamp=clock.now())
    closure.review_evidence(db, ev2, who("rkim"), True)
    sign_required(db, t)
    assert t.status == "closed" and t.signoff_dispute["resolved_at"]
    assert all(f.status == "resolved" for f in db.scalars(select(Finding).where(Finding.kind == "signoff_disagreement")))


def test_absence_claim_disagreement_keeps_cannot_determine(db, who):
    ob, t, ev = _absence(db, who)
    assert claim_for(db, ob, "ACIS Dispute Portal").value == "cannot_determine"
    owner = closure.required_signer(db, t, "system_owner")
    closure.sign_required_review(db, t, "system_owner", owner, record_version="2.3.20", record_source="package_manifest")
    assert t.signoff_dispute and "installed" in t.signoff_dispute["reason"]
    assert claim_for(db, ob, "ACIS Dispute Portal").value == "cannot_determine" and t.status == "open"


def test_no_component_record_is_uncertainty_not_agreement(db, who):
    ob, t, ev = _absence(db, who)
    owner = closure.required_signer(db, t, "system_owner")
    closure.sign_required_review(db, t, "system_owner", owner, record_source="none")
    assert t.signoff_dispute and "uncertainty" in t.signoff_dispute["reason"]
    assert claim_for(db, ob, "ACIS Dispute Portal").value == "cannot_determine"


def test_risk_acceptance_ends_an_open_disagreement(db, who):
    from app.services import assignment
    ob, t, ev = _fixed_portal(db, who)
    closure.dispute_signoff(db, t, "security", who("crhodes"), "scan scope not credible")
    ra = assignment.request_risk_acceptance(db, t, who("atan"), scenario="s", controls="WAF rule", residual_risk="Low", days=5)
    assignment.approve_risk_acceptance(db, ra, who("rpark"))
    assurance.sweep(db)
    assert t.signoff_dispute["resolved_at"] and "risk accepted" in t.signoff_dispute["resolved_how"]


# ---------- API and screens ----------
def test_api_signoff_and_dispute(db, who):
    ob, t, ev = _fixed_portal(db, who)
    c = client(db)
    r = c.post(f"/api/tasks/{t.id}/review-signoff", json={"role": "system_owner", "record_version": "2.3.32",
                                                          "record_source": "deployment_record"}, headers=h("pnair"))
    assert r.status_code == 200, r.text
    assert next(s for s in r.json()["signoffs"] if s["key"] == "system_owner")["signed_by"] == "Alex Tan"
    assert c.post(f"/api/tasks/{t.id}/signoff-dispute", json={"role": "security", "reason": ""}).status_code == 400
    r = c.post(f"/api/tasks/{t.id}/signoff-dispute", json={"role": "security", "reason": "scope not credible"})
    assert r.status_code == 200 and r.json()["dispute"]["by"] == "Casey Rhodes" and r.json()["status"] == "open"
    detail = c.get(f"/api/tasks/{t.id}").json()
    assert detail["dispute"]["reason"] == "scope not credible" and detail["record_sources"]
    alerts = c.get("/api/alerts").json()
    assert any(i["type"] == "disputed" and i["task_id"] == t.id for i in alerts["items"])
    from app.main import app
    app.dependency_overrides.clear()


def test_claim_detail_shows_who_signed_and_past_disagreements(db, who):
    ob, t, ev = _fixed_portal(db, who)
    closure.sign_required_review(db, t, "system_owner", who("atan"), record_version="2.3.30", record_source="cmdb")
    ev2 = closure.submit_evidence(db, t, who("atan"), purpose="remediation", asset_name="Credit Report Portal",
                                  component="Apache Struts", scope=["/opt/portal/**"], method="authenticated_scan",
                                  observed_version="2.3.32", timestamp=clock.now())
    closure.review_evidence(db, ev2, who("rverma"), True)
    sign_required(db, t)
    d = views.claim_detail(db, db.get(Claim, ev2.claim_id))
    assert d["closure"]["task_ref"] == f"TSK-{t.id:04d}" and len(d["closure"]["disputes"]) == 1
    assert all(s["signed_by"] for s in d["closure"]["signoffs"])
