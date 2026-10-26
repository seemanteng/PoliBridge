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
    from .assurance import renewal_source, check_renewal_evidence
    renewal = renewal_source(db, task)
    comp = next((c for c in asset.components if adv and versions.norm(c.name) == versions.norm(adv.product)), None)
    needed = E.required_paths(comp.install_path if comp and purpose == "remediation" else None, asset.app_roots)
    reasons = check_renewal_evidence(
        task, asset, renewal, purpose=purpose, asset_name=asset_name, component=component,
        scope=scope or [], method=method, timestamp=timestamp, submitter_id=person.id) if renewal else E.check_evidence(
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
    if ev.purpose == "remediation":
        missing = missing_signoffs(task)
        if missing:  # C2-C4: the evidence is good, but the task waits for the extra sign-offs its impact needs
            task.status, task.fixed_at = "fixed_pending_review", task.fixed_at or clock.now()
            audit.log(db, "system", "task", task.id, "awaiting_required_signoffs", missing=missing)
            for key in missing:
                notify(db, task=task, person=required_signer(db, task, key), kind="signoff_requested",
                       message=f"{SIGNOFF_LABEL[key]} sign-off needed to close {asset.name}: {ob.title}")
            return ev
        _complete_remediation(db, task, ev, reviewer)
    else:  # absence: accepted evidence updates the inventory it rests on, then reassess
        asset.inventory_verified_at, asset.inventory_method, asset.inventory_scope = ev.timestamp, ev.method, ev.scope
        from .assessment import override_applicability
        task_owner = db.get(Person, task.owner_id)
        override_applicability(db, ob, asset, "does_not_apply", "component-scoped absence evidence accepted",
                               actor=task_owner or reviewer, reviewer=reviewer, evidence_id=ev.id)
    try_close_obligation(db, ob)
    return ev


def _complete_remediation(db, task: Task, ev: Evidence, reviewer: Person):
    from .assurance import renewal_source, complete_renewal
    renewal = renewal_source(db, task)
    if renewal:
        complete_renewal(db, task, ev, reviewer, renewal)
        return
    asset = db.get(Asset, task.asset_id)
    ob = db.get(Obligation, task.obligation_id)
    adv = db.get(Advisory, ob.advisory_id)
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
    # accepted evidence shows the version now running: keep the inventory and its version log current
    comp = next((c for c in asset.components if versions.norm(c.name) == versions.norm(adv.product)), None)
    if comp and ev.observed_version and comp.version != ev.observed_version:
        from .registry import set_component_version  # local import avoids a cycle
        set_component_version(db, asset, comp.name, ev.observed_version, source="remediation_evidence",
                              actor=reviewer.directory_id, note=f"{adv.cve} remediated, evidence {ev.id} accepted")


# ---------- C2-C4: sign-offs a task's impact requires before closure ----------
SIGNOFF_ROLE = {"security": "ciso", "compliance": "compliance", "legal": "legal"}
SIGNOFF_LABEL = {"security": "Security (C2/C3)", "system_owner": "System owner (C2)", "compliance": "Compliance (C3/C4)",
                 "legal": "Legal (C4)"}


def missing_signoffs(task: Task) -> list[str]:
    return [k for k in task.required_reviewers or [] if k not in (task.reviewer_signoffs or {})]


def required_signer(db, task: Task, key: str) -> Person | None:
    if key == "system_owner":
        asset = db.get(Asset, task.asset_id)
        owner = db.get(Person, asset.owner_id) if asset.owner_id else None
        return owner if owner and owner.active else (db.get(Person, task.owner_id) if task.owner_id else None)
    return first_with_role(db, SIGNOFF_ROLE[key])


def sign_required_review(db, task: Task, key: str, person: Person):
    """Security, Compliance or Legal sign off a task their impact rule names. Everyone but the system owner must be
    independent of the fix, like the evidence reviewer (E3)."""
    if key not in (task.required_reviewers or []):
        raise ValueError(f"this task does not need a {SIGNOFF_LABEL.get(key, key)} sign-off")
    if key in (task.reviewer_signoffs or {}):
        raise ValueError(f"{SIGNOFF_LABEL[key]} has already signed off")
    signer = required_signer(db, task, key)
    if signer is None or signer.id != person.id:
        raise PermissionError(f"the {SIGNOFF_LABEL[key]} sign-off belongs to {signer.name if signer else 'nobody'}")
    if key != "system_owner":
        submitters = {e.submitter_id for e in db.scalars(select(Evidence).where(Evidence.task_id == task.id))}
        if person.id == task.fixed_by_id or person.id in submitters:
            raise PermissionError("E3: whoever fixed the system or submitted its evidence cannot also sign it off")
    task.reviewer_signoffs = {**(task.reviewer_signoffs or {}), key: {"by": person.name, "at": clock.now().isoformat()}}
    audit.log(db, person.directory_id, "task", task.id, "required_signoff", role=key)
    if task.status == "fixed_pending_review" and not missing_signoffs(task):
        ev = db.scalar(select(Evidence).where(Evidence.task_id == task.id, Evidence.purpose == "remediation",
                                              Evidence.status == "accepted").order_by(Evidence.id.desc()))
        if ev is not None:
            _complete_remediation(db, task, ev, db.get(Person, ev.reviewer_id) or person)
            try_close_obligation(db, db.get(Obligation, task.obligation_id))


def signoff_view(db, task: Task) -> list[dict]:
    out = []
    for key in task.required_reviewers or []:
        s = (task.reviewer_signoffs or {}).get(key)
        who = required_signer(db, task, key)
        out.append({"key": key, "label": SIGNOFF_LABEL.get(key, key), "signer": who.name if who else None,
                    "signed_by": s["by"] if s else None, "signed_at": s["at"] if s else None})
    return out


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
    from .assurance import renewal_source
    renewal = renewal_source(db, task)
    comp = next((c for c in asset.components if adv and versions.norm(c.name) == versions.norm(adv.product)), None)
    needed = E.required_paths(comp.install_path if comp and ev.purpose == "remediation" else None, asset.app_roots)
    gaps = [entry for entry in renewal.evidence_scope or [] if entry not in (ev.scope or [])] if renewal else E.scope_gaps(ev.scope or [], needed)
    strong = task.impact == "High"
    validity = TIMINGS["scan_evidence_validity_days"]
    age_ok = ev.timestamp is not None and (clock.now() - ev.timestamp) <= timedelta(days=validity)
    indep = E.check_reviewer(ev.reviewer_id or -1, ev.submitter_id, task.fixed_by_id) if ev.reviewer_id else []
    P = lambda i: db.get(Person, i).name if i else None  # noqa: E731
    rows = [
        {"check": "Asset", "requires": asset.name, "recorded": ev.asset_name or "—",
         "pass": ev.asset_name == asset.name},
        {"check": "Component scope",
         "requires": (", ".join(renewal.evidence_scope or []) if renewal else
                      f"{task.component or 'component'}, any path under " + (", ".join(needed) or "application roots")),
         "recorded": ", ".join(ev.scope or []) or "—", "pass": bool(ev.scope) and not gaps},
        {"check": "Method",
         "requires": (renewal.verification_method.replace("_", " ") if renewal else
                      "Authenticated scan or direct version check" + (" (critical item)" if strong else "")),
         "recorded": (ev.method or "—").replace("_", " "),
         "pass": bool(ev.method) and (ev.method == renewal.verification_method if renewal else
                                      not strong or ev.method in E.STRONG_METHODS)},
        {"check": "Timestamp", "requires": f"Within scan validity · {validity} days",
         "recorded": ev.timestamp.strftime("%d %b %H:%M") if ev.timestamp else "—", "pass": age_ok},
        {"check": "Independence", "requires": "Reviewer differs from submitter and from whoever did the work",
         "recorded": f"{P(ev.submitter_id)} → {P(ev.reviewer_id) if ev.reviewer_id else 'unassigned'}",
         "pass": not indep},
    ]
    return {"checks": rows, "all_pass": all(r["pass"] for r in rows), "reasons": ev.reasons,
            "examined": ev.scope or [], "needed_paths": renewal.evidence_scope if renewal else needed, "gaps": gaps,
            "component": task.component, "asset": asset.name, "renewal": bool(renewal),
            "claim": (f"“{renewal.statement}”" if renewal else
                      f"“{asset.name} does not use {task.component}”" if ev.purpose == "absence"
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
