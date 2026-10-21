"""Component 5: evidence verification and closure."""
from datetime import timedelta

from sqlalchemy import select

from ..clock import clock
from ..config import TIMINGS
from ..models import Task, Evidence, Obligation, Asset, Advisory, Claim, Person, RiskAcceptance
from ..rules import evidence as E, versions
from . import audit
from .common import create_claim, add_dependency, notify, first_with_role, bu_head
from .assignment import _manager_of

TERMINAL = ("closed", "risk_accepted")


def mark_fixed(db, task: Task, person: Person, when=None):
    if task.owner_id != person.id:
        raise PermissionError("only the assigned owner can mark a task fixed")
    task.status, task.fixed_at, task.fixed_by_id = "fixed_pending_review", when or clock.now(), person.id
    audit.log(db, person.directory_id, "task", task.id, "marked_fixed")


def submit_evidence(db, task: Task, person: Person, *, purpose: str, asset_name, component, scope, method,
                    observed_version=None, timestamp=None) -> Evidence:
    """Structured evidence goes through automated checks (E1, E2, E4) first."""
    asset = db.get(Asset, task.asset_id)
    ob = db.get(Obligation, task.obligation_id)
    adv = db.get(Advisory, ob.advisory_id) if ob.advisory_id else None
    comp = next((c for c in asset.components if adv and versions.norm(c.name) == versions.norm(adv.product)), None)
    needed = E.required_paths(comp.install_path if comp and purpose == "remediation" else None, asset.app_roots)
    reasons = E.check_evidence(
        purpose=purpose, task_asset=asset.name, task_component=task.component, ev_asset=asset_name,
        ev_component=component, scope=scope or [], method=method, observed_version=observed_version,
        timestamp=timestamp, submitter_id=person.id, reported_fixed_at=task.fixed_at, needed_paths=needed,
        strong_method_required=(task.impact == "High"),
        affected_specs=adv.affected_versions if adv else [], now=clock.now())
    if purpose == "absence" and comp and adv and versions.affected(comp.version, adv.affected_versions):
        reasons.append("E1: inventory records show the component present at an affected version")
    ev = Evidence(task_id=task.id, purpose=purpose, asset_name=asset_name, component=component,
                  scope=scope or [], method=method, observed_version=observed_version, timestamp=timestamp,
                  submitter_id=person.id, reasons=reasons, status="rejected" if reasons else "pending",
                  created_at=clock.now())
    db.add(ev)
    db.flush()
    audit.log(db, person.directory_id, "evidence", ev.id, "submitted", task=task.id, purpose=purpose,
              status=ev.status, reasons=reasons)
    if reasons:
        _on_rejected(db, task, ev)
    return ev


def _on_rejected(db, task: Task, ev: Evidence):
    was_fixed = task.status == "fixed_pending_review"
    if task.status not in TERMINAL:
        task.status = "open"  # stays open; closure blocked
    if ev.purpose == "absence":  # asset stays/reverts to Cannot Determine
        audit.log(db, "system", "task", task.id, "absence_claim_rejected", reasons=ev.reasons)
    if was_fixed or ev.purpose == "remediation":
        notify(db, task=task, person=_manager_of(db, task), kind="evidence_rejected",
               message=f"Task {task.id} marked fixed but evidence rejected: {'; '.join(ev.reasons)}")
        audit.log(db, "system", "task", task.id, "escalated", to="manager", reason="evidence rejected")


def review_evidence(db, ev: Evidence, reviewer: Person, approve: bool, comment: str = ""):
    task = db.get(Task, ev.task_id)
    if ev.status != "pending":
        raise ValueError(f"evidence already {ev.status}")
    ev.reviewer_id = reviewer.id
    block = E.check_reviewer(reviewer.id, ev.submitter_id, task.fixed_by_id)  # E3
    if block:
        audit.log(db, reviewer.directory_id, "evidence", ev.id, "review_blocked", reasons=block)
        raise PermissionError("; ".join(block))
    if not approve:
        ev.status, ev.reasons = "rejected", [f"reviewer rejected: {comment or 'no reason given'}"]
        audit.log(db, reviewer.directory_id, "evidence", ev.id, "rejected", comment=comment)
        _on_rejected(db, task, ev)
        return ev
    ev.status = "accepted"
    audit.log(db, reviewer.directory_id, "evidence", ev.id, "accepted")
    asset = db.get(Asset, task.asset_id)
    ob = db.get(Obligation, task.obligation_id)
    adv = db.get(Advisory, ob.advisory_id)
    if ev.purpose == "remediation":
        days = TIMINGS["scan_evidence_validity_days"] if (asset.critical or asset.internet_facing) else 90
        claim = create_claim(
            db, actor=reviewer.directory_id, claim_type="remediation", asset_id=asset.id, obligation_id=ob.id,
            component=adv.product, value="remediated", statement=f"{asset.name} is patched for {adv.cve}",
            evidence_scope=ev.scope, verification_method=ev.method, evidence_ts=ev.timestamp,
            submitter_id=ev.submitter_id, reviewer_id=reviewer.id, owner_id=task.owner_id,
            expiry=clock.now() + timedelta(days=days))
        add_dependency(db, claim, "task", task.id)
        ev.claim_id = claim.id
        task.status, task.disposition, task.closed_at = "closed", "remediated", clock.now()
    else:  # absence: accepted evidence updates the inventory it rests on, then reassess
        asset.inventory_verified_at, asset.inventory_method, asset.inventory_scope = ev.timestamp, ev.method, ev.scope
        from .assessment import override_applicability
        task_owner = db.get(Person, task.owner_id)
        override_applicability(db, ob, asset, "does_not_apply", "component-scoped absence evidence accepted",
                               actor=task_owner or reviewer, reviewer=reviewer, evidence_id=ev.id)
    try_close_obligation(db, ob)
    return ev


def try_close_obligation(db, ob: Obligation) -> tuple[bool, list[str]]:
    """E5: all affected assets have an approved disposition and none is Cannot Determine."""
    blockers = []
    for t in db.scalars(select(Task).where(Task.obligation_id == ob.id, Task.is_test == False)):  # noqa: E712
        if t.status not in TERMINAL:
            blockers.append(f"task {t.id} ({t.kind}) is {t.status}")
    for c in db.scalars(select(Claim).where(Claim.obligation_id == ob.id, Claim.claim_type == "applicability",
                                            Claim.status == "active", Claim.value == "cannot_determine")):
        blockers.append(f"asset {c.asset_id} remains Cannot Determine")
    if ob.kind == "regulatory" and not ob.approved_by_id:
        blockers.append("regulatory obligation not approved by Compliance/Legal")
    if blockers:
        return False, blockers
    if ob.status != "closed":
        ob.status, ob.closed_at = "closed", clock.now()
        audit.log(db, "system", "obligation", ob.id, "closed")
    return True, []


def disposition_summary(db, ob: Obligation) -> list[dict]:
    """Distinguishes remediation from accepted risk per asset."""
    out = []
    for t in db.scalars(select(Task).where(Task.obligation_id == ob.id)):
        out.append({"task_id": t.id, "asset": db.get(Asset, t.asset_id).name, "status": t.status,
                    "disposition": t.disposition})
    return out


def evidence_checks(db, ev: Evidence) -> dict:
    """The claim-versus-evidence table a reviewer sees (rules E1-E3)."""
    task = db.get(Task, ev.task_id)
    asset = db.get(Asset, task.asset_id)
    ob = db.get(Obligation, task.obligation_id)
    adv = db.get(Advisory, ob.advisory_id) if ob.advisory_id else None
    comp = next((c for c in asset.components if adv and versions.norm(c.name) == versions.norm(adv.product)), None)
    needed = E.required_paths(comp.install_path if comp and ev.purpose == "remediation" else None, asset.app_roots)
    gaps = E.scope_gaps(ev.scope or [], needed)
    strong = task.impact == "High"
    validity = TIMINGS["scan_evidence_validity_days"]
    age_ok = ev.timestamp is not None and (clock.now() - ev.timestamp) <= timedelta(days=validity)
    indep = E.check_reviewer(ev.reviewer_id or -1, ev.submitter_id, task.fixed_by_id) if ev.reviewer_id else []
    P = lambda i: db.get(Person, i).name if i else None  # noqa: E731
    rows = [
        {"check": "Asset", "requires": asset.name, "recorded": ev.asset_name or "—",
         "pass": ev.asset_name == asset.name},
        {"check": "Component scope",
         "requires": f"{task.component or 'component'}, any path under " + (", ".join(needed) or "application roots"),
         "recorded": ", ".join(ev.scope or []) or "—", "pass": bool(ev.scope) and not gaps},
        {"check": "Method",
         "requires": "Authenticated scan or direct version check" + (" (critical item)" if strong else ""),
         "recorded": (ev.method or "—").replace("_", " "),
         "pass": bool(ev.method) and (not strong or ev.method in E.STRONG_METHODS)},
        {"check": "Timestamp", "requires": f"Within scan validity · {validity} days",
         "recorded": ev.timestamp.strftime("%d %b %H:%M") if ev.timestamp else "—", "pass": age_ok},
        {"check": "Independence", "requires": "Reviewer differs from submitter and from whoever did the work",
         "recorded": f"{P(ev.submitter_id)} → {P(ev.reviewer_id) if ev.reviewer_id else 'unassigned'}",
         "pass": not indep},
    ]
    return {"checks": rows, "all_pass": all(r["pass"] for r in rows), "reasons": ev.reasons,
            "examined": ev.scope or [], "needed_paths": needed, "gaps": gaps,
            "component": task.component, "asset": asset.name,
            "claim": (f"“{asset.name} does not use {task.component}”" if ev.purpose == "absence"
                      else f"“{asset.name} is patched for {adv.cve if adv else 'the advisory'}”")}


def decide_evidence(db, ev: Evidence, reviewer: Person, action: str, comment: str = "") -> Evidence:
    """approve | reject | request. Rejecting already-rejected evidence confirms it."""
    task = db.get(Task, ev.task_id)
    if action == "approve":
        return review_evidence(db, ev, reviewer, True, comment)
    if action == "reject":
        if ev.status == "pending":
            return review_evidence(db, ev, reviewer, False, comment)
        ev.reviewer_id = reviewer.id
        audit.log(db, reviewer.directory_id, "evidence", ev.id, "rejection_confirmed", comment=comment)
        return ev
    if action == "request":
        audit.log(db, reviewer.directory_id, "evidence", ev.id, "specific_evidence_requested", comment=comment)
        notify(db, task=task, person=db.get(Person, task.owner_id) if task.owner_id else None,
               kind="evidence_requested", message=comment or "Specific evidence requested")
        return ev
    raise ValueError("action must be approve, reject or request")
