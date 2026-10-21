"""The seven scenarios from report Section 7.1, using the Build Plan timings."""
from datetime import timedelta

from sqlalchemy import select

from app.clock import clock
from app.models import Task, Claim, Finding, Notification, Evidence, AuditEvent, Obligation, CanaryRun, RiskAcceptance
from app.services import intake, assignment, closure, assurance, assessment, registry
from .conftest import STRUTS


def task_for(db, ob, asset_name, kind=None):
    from app.models import Asset
    a = db.scalar(select(Asset).where(Asset.name == asset_name))
    q = select(Task).where(Task.obligation_id == ob.id, Task.asset_id == a.id)
    if kind:
        q = q.where(Task.kind == kind)
    return db.scalars(q).first()


def claim_for(db, ob, asset_name):
    from app.models import Asset
    a = db.scalar(select(Asset).where(Asset.name == asset_name))
    return db.scalar(select(Claim).where(Claim.obligation_id == ob.id, Claim.asset_id == a.id,
                                         Claim.claim_type == "applicability", Claim.status == "active"))


def kinds(db, task):
    return [n.kind for n in db.scalars(select(Notification).where(Notification.task_id == task.id))]


# 1. March 2017 replay
def test_march_2017_replay(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    acis = task_for(db, ob, "ACIS Dispute Portal")
    assert claim_for(db, ob, "ACIS Dispute Portal").value == "cannot_determine"   # never silently excluded
    assert acis.kind == "investigation" and acis.impact == "High"
    assert acis.owner_id == who("dortiz").id                                       # fallback to BU head
    assert db.scalar(select(Finding).where(Finding.kind == "ownership_correction"))
    assert claim_for(db, ob, "Credit Report Portal").value == "applies"
    assert not task_for(db, ob, "HR Intranet")
    ok, blockers = closure.try_close_obligation(db, ob)
    assert not ok and any("Cannot Determine" in b for b in blockers)               # E5

    clock.advance(hours=25)
    assignment.tick_all(db)
    assert "escalate_manager" in kinds(db, acis) and "escalate_ciso" not in kinds(db, acis)
    clock.advance(hours=24)
    assignment.tick_all(db)
    assert "escalate_ciso" in kinds(db, acis)
    assert "interim_missing" in kinds(db, acis)       # High: 48h interim control missing
    clock.advance(days=6)
    assignment.tick_all(db)
    assert "deadline_missed" in kinds(db, acis) and acis.board_flag


# 2. unacknowledged or rejected task
def test_unacknowledged_escalates_at_24h_and_48h(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    clock.advance(hours=23)
    assignment.tick_all(db)
    assert "escalate_manager" not in kinds(db, t)
    clock.advance(hours=2)
    assignment.tick_all(db)
    assert "escalate_manager" in kinds(db, t)
    clock.advance(hours=24)
    assignment.tick_all(db)
    assert "escalate_ciso" in kinds(db, t)


def test_acknowledgement_stops_escalation(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    assignment.acknowledge(db, t, who("atan"))
    clock.advance(hours=60)
    assignment.tick_all(db)
    assert "escalate_manager" not in kinds(db, t)


def test_rejection_reroutes_and_deadline_keeps_running(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    due = t.remediate_due
    assignment.reject_task(db, t, who("atan"), "not my system")
    assert t.owner_id == who("dortiz").id and t.remediate_due == due
    assert db.scalar(select(Finding).where(Finding.kind == "ownership_correction",
                                           Finding.subject.like("%Credit Report%")))


def test_transfer_does_not_reset_deadlines(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    due = t.remediate_due
    assignment.transfer_task(db, t, who("ofoo"), who("mlee"))
    assert t.owner_id == who("ofoo").id and t.remediate_due == due


# 3. missed deadline / expired risk acceptance
def test_missed_deadline_and_risk_acceptance_reopen(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    ra = assignment.request_risk_acceptance(db, t, who("atan"), scenario="Patch window blocked",
                                            controls="WAF virtual patch", residual_risk="Medium", days=10)
    try:
        assignment.approve_risk_acceptance(db, ra, who("atan"))
        assert False, "requester must not approve"
    except PermissionError:
        pass
    assignment.approve_risk_acceptance(db, ra, who("rpark"))
    assert t.status == "risk_accepted"
    clock.advance(days=11)
    assurance.sweep(db)
    assert t.status != "risk_accepted" and ra.status == "expired"                  # D5
    assert db.scalar(select(Notification).where(Notification.kind == "risk_acceptance_expired"))


def test_deadline_miss_goes_to_risk_owner_and_board(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    clock.advance(days=8)
    assignment.tick_all(db)
    notified = {n.person_id for n in db.scalars(select(Notification).where(Notification.kind == "deadline_missed"))}
    assert {who("rpark").id, who("swhitfield").id} <= notified and t.board_flag
    assert assurance.oversight_summary(db)["board_risk_items"]


def test_risk_acceptance_requires_authorised_approver(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    ra = assignment.request_risk_acceptance(db, t, who("atan"), scenario="s", controls="c", residual_risk="Low", days=5)
    try:
        assignment.approve_risk_acceptance(db, ra, who("mlee"))
        assert False
    except PermissionError:
        pass


# 4. scan evidence that omits the affected component
def test_scan_omitting_component_is_rejected_and_asset_stays_cannot_determine(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "ACIS Dispute Portal")
    ev = closure.submit_evidence(db, t, who("dortiz"), purpose="absence", asset_name="ACIS Dispute Portal",
                                 component="Apache Struts", scope=["/"], method="authenticated_scan",
                                 timestamp=clock.now())
    assert ev.status == "rejected" and any("scope" in r for r in ev.reasons)
    assert claim_for(db, ob, "ACIS Dispute Portal").value == "cannot_determine"
    assert t.status == "open"


def test_scoped_absence_evidence_needs_independent_review(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "ACIS Dispute Portal")
    ev = closure.submit_evidence(db, t, who("dortiz"), purpose="absence", asset_name="ACIS Dispute Portal",
                                 component="Apache Struts", scope=["/opt/acis/**"], method="authenticated_scan",
                                 timestamp=clock.now())
    assert ev.status == "pending"
    try:
        closure.review_evidence(db, ev, who("dortiz"), True)
        assert False
    except PermissionError:
        pass
    closure.review_evidence(db, ev, who("rverma"), True)
    assert claim_for(db, ob, "ACIS Dispute Portal").value == "does_not_apply"
    assert t.status == "closed"


def test_a5_override_direction(db, who, asset):
    ob = intake.ingest_advisory(db, **STRUTS)
    acis = asset("ACIS Dispute Portal")
    # more cautious: allowed with a reason
    c = assessment.override_applicability(db, ob, asset("HR Intranet"), "cannot_determine", "suspect inventory",
                                          who("pnair"))
    assert c.value == "cannot_determine"
    # less cautious without evidence: refused
    try:
        assessment.override_applicability(db, ob, acis, "does_not_apply", "looks fine", who("pnair"), who("rverma"))
        assert False
    except PermissionError:
        pass


# 5. irrelevant technology with verified data
def test_does_not_apply_creates_no_task(db):
    ob = intake.ingest_advisory(db, **STRUTS)
    assert claim_for(db, ob, "HR Intranet").value == "does_not_apply"
    assert task_for(db, ob, "HR Intranet") is None
    assert claim_for(db, ob, "Marketing Site").value == "does_not_apply"


# 6. attestation / certificate expiry
def test_certificate_expiry_lapses_monitoring_claim(db, who):
    clock.advance(days=46)
    assurance.sweep(db)
    f = db.scalar(select(Finding).where(Finding.kind == "lapsed_monitoring"))
    assert f and assurance.dashboard(db)["lapsed_monitoring"] == 1


def test_attestation_expiry_invalidates_owner_and_reroutes(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    assert t.owner_id == who("atan").id
    clock.advance(days=185)
    assurance.sweep(db)
    assert t.owner_id == who("dortiz").id
    assert db.scalar(select(Finding).where(Finding.kind == "attestation_overdue"))


def test_leaver_event_invalidates_attestation_early(db, who, asset):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal")
    registry.joiner_mover_leaver(db, who("atan"), "leaver")
    assert t.owner_id == who("dortiz").id and asset("Credit Report Portal").ownership_attested_at is None


def test_stale_inventory_reverts_to_cannot_determine_and_reopens(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal", "remediation")
    assignment.acknowledge(db, t, who("atan"))
    closure.mark_fixed(db, t, who("atan"))
    clock.advance(hours=1)
    ev = closure.submit_evidence(db, t, who("atan"), purpose="remediation", asset_name="Credit Report Portal",
                                 component="Apache Struts", scope=["/opt/portal/**"], method="authenticated_scan",
                                 observed_version="2.3.32", timestamp=clock.now())
    assert ev.status == "pending"
    closure.review_evidence(db, ev, who("rverma"), True)
    assert t.status == "closed" and t.disposition == "remediated"
    clock.advance(days=15)                      # scan evidence validity is 14 days for critical assets
    assurance.sweep(db)
    assert t.status == "reopened"               # F2


def test_remediation_evidence_rejections(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal", "remediation")
    closure.mark_fixed(db, t, who("atan"))
    clock.advance(hours=1)
    bad = closure.submit_evidence(db, t, who("atan"), purpose="remediation", asset_name="Credit Report Portal",
                                  component="Apache Struts", scope=["/opt/portal/**"], method="authenticated_scan",
                                  observed_version="2.3.32", timestamp=clock.now() - timedelta(hours=3))
    assert bad.status == "rejected" and any("E2" in r for r in bad.reasons)
    assert t.status == "open"
    assert "evidence_rejected" in kinds(db, t)   # escalated to the owner's manager
    wrong = closure.submit_evidence(db, t, who("atan"), purpose="remediation", asset_name="HR Intranet",
                                    component="Apache Struts", scope=["/opt/portal/**"], method="authenticated_scan",
                                    observed_version="2.3.32", timestamp=clock.now())
    assert wrong.status == "rejected" and any("E1" in r for r in wrong.reasons)


def test_reviewer_who_did_the_work_is_blocked(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = task_for(db, ob, "Credit Report Portal", "remediation")
    closure.mark_fixed(db, t, who("atan"))
    ev = closure.submit_evidence(db, t, who("ofoo"), purpose="remediation", asset_name="Credit Report Portal",
                                 component="Apache Struts", scope=["/opt/portal/**"], method="authenticated_scan",
                                 observed_version="2.3.32", timestamp=clock.now() + timedelta(minutes=1))
    assert ev.status == "pending"
    try:
        closure.review_evidence(db, ev, who("atan"), True)   # atan performed the fix
        assert False
    except PermissionError as e:
        assert "E3" in str(e)


def test_full_closure_distinguishes_remediation(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    portal = task_for(db, ob, "Credit Report Portal", "remediation")
    closure.mark_fixed(db, portal, who("atan"))
    clock.advance(hours=1)
    ev = closure.submit_evidence(db, portal, who("atan"), purpose="remediation", asset_name="Credit Report Portal",
                                 component="Apache Struts", scope=["/opt/portal/**"], method="direct_version_check",
                                 observed_version="2.3.32", timestamp=clock.now())
    closure.review_evidence(db, ev, who("rverma"), True)
    for name, root in (("ACIS Dispute Portal", "/opt/acis/**"), ("Mainframe Gateway", "/opt/gw/**")):
        t = task_for(db, ob, name, "investigation")
        e = closure.submit_evidence(db, t, who("dortiz"), purpose="absence", asset_name=name,
                                    component="Apache Struts", scope=[root], method="authenticated_scan",
                                    timestamp=clock.now())
        closure.review_evidence(db, e, who("rkim"), True)
    assert ob.status == "closed"
    assert {d["disposition"] for d in closure.disposition_summary(db, ob)} == {"remediated", "superseded"}


# 7. canary against a distribution-list owner
def test_canary_detects_list_owner(db, asset):
    run = assurance.run_canary(db, asset("Mainframe Gateway"))
    assert run.status == "failed" and any("valid named owner" in f for f in run.failures)
    assert db.scalar(select(Finding).where(Finding.kind == "canary_failure"))
    assert assurance.dashboard(db)["canary"]["pass_rate"] == 0.0


def test_canary_passes_when_escalation_fires(db, asset):
    run = assurance.run_canary(db, asset("Credit Report Portal"))
    assert run.status == "running"
    clock.advance(hours=25)
    assurance.sweep(db)
    db.refresh(run)
    assert run.status == "passed"


def test_canary_excluded_from_operational_metrics(db, asset):
    assurance.run_canary(db, asset("Credit Report Portal"))
    assert assurance.dashboard(db)["open_tasks"] == 0


def test_audit_trail_complete(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    actions = {e.action for e in db.scalars(select(AuditEvent))}
    assert {"created", "created_from_advisory", "fallback_routed", "raised"} <= actions
