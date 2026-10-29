"""Component 5: evidence verification and closure.

A remediation or "Does Not Apply" claim closes only when:
1. structured evidence passes the automated checks (E1, E2, E4),
2. an independent reviewer accepts it (E3), and
3. two functions sign it off (report §6.5): the system owner (IT) confirms the component record, and
   Information Security confirms the verification covered that component. The impact rules (C2-C4) can add
   Compliance or Legal on top.
If the two sign-offs disagree, or either side has no evidence, the claim is Cannot Determine: the evidence is
set aside, both sides get a task, the owner's manager is told at once, and an unresolved disagreement goes to the
risk owner, senior management and the board report."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import object_session

from ..clock import clock
from ..config import TIMINGS
from ..models import Task, Evidence, Obligation, Asset, Advisory, Claim, Person, RiskAcceptance, Finding
from ..rules import evidence as E, versions
from . import audit
from .common import create_claim, add_dependency, notify, first_with_role, people_with_role, raise_finding
from .assignment import _manager_of, _sent

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
    missing = missing_signoffs(task)
    if missing:  # the evidence is good, but the claim waits for its sign-offs (§6.5, C2-C4)
        if ev.purpose == "remediation":
            task.status, task.fixed_at = "fixed_pending_review", task.fixed_at or clock.now()
        audit.log(db, "system", "task", task.id, "awaiting_required_signoffs", missing=missing)
        for key in missing:
            notify(db, task=task, person=required_signer(db, task, key), kind="signoff_requested",
                   message=f"{SIGNOFF_LABEL[key]} sign-off needed to close {asset.name}: {ob.title}")
        return ev
    _finish(db, task, ev, reviewer)
    try_close_obligation(db, ob)
    return ev


def _finish(db, task: Task, ev: Evidence, reviewer: Person):
    """Evidence accepted and every sign-off in: record the claim."""
    if ev.purpose == "remediation":
        _complete_remediation(db, task, ev, reviewer)
    else:
        _complete_absence(db, task, ev, reviewer)
    _resolve_dispute(db, task, "both sign-offs agree on new evidence")


def _complete_absence(db, task: Task, ev: Evidence, reviewer: Person):
    """Accepted absence evidence updates the inventory it rests on, then the asset becomes Does Not Apply."""
    asset = db.get(Asset, task.asset_id)
    ob = db.get(Obligation, task.obligation_id)
    asset.inventory_verified_at, asset.inventory_method, asset.inventory_scope = ev.timestamp, ev.method, ev.scope
    from .assessment import override_applicability
    task_owner = db.get(Person, task.owner_id) if task.owner_id else None
    claim = override_applicability(db, ob, asset, "does_not_apply", "component-scoped absence evidence accepted",
                                   actor=task_owner or reviewer, reviewer=reviewer, evidence_id=ev.id)
    ev.claim_id = claim.id


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


# ---------- sign-offs: the two-function sign-off (§6.5) plus whatever C2-C4 add ----------
DUAL = ("system_owner", "security")  # IT confirms the component record; Information Security confirms coverage
SIGNOFF_ROLE = {"security": "ciso", "compliance": "compliance", "legal": "legal"}
SIGNOFF_LABEL = {"system_owner": "System owner (IT)", "security": "Information Security",
                 "compliance": "Compliance (C3/C4)", "legal": "Legal (C4)"}
SIGNOFF_CONFIRMS = {"system_owner": "The system's component record matches what the evidence says",
                    "security": "The verification examined the affected component",
                    "compliance": "Closure meets the compliance obligation", "legal": "Closure meets the legal obligation"}
RECORD_SOURCES = {"deployment_record": "Deployment / change record", "sbom": "SBOM", "cmdb": "CMDB entry",
                  "package_manifest": "Package manifest on the host", "none": "No record available"}


def needed_signoffs(task: Task) -> list[str]:
    """Every remediation and Does Not Apply claim needs both functions. A monitoring renewal (component 6) keeps
    only the sign-offs its impact rules name."""
    extra = list(task.required_reviewers or [])
    db = object_session(task)
    from .assurance import renewal_source
    if db is not None and renewal_source(db, task):
        return extra
    return list(DUAL) + [k for k in extra if k not in DUAL]


def current_evidence(db, task: Task) -> Evidence | None:
    """The evidence a sign-off is given against: the latest submission that has not been rejected."""
    return db.scalar(select(Evidence).where(Evidence.task_id == task.id, Evidence.status != "rejected")
                     .order_by(Evidence.id.desc()))


def _valid_signoffs(task: Task) -> dict:
    """A sign-off counts only for the evidence it was given on. New evidence needs fresh sign-offs."""
    db = object_session(task)
    cur = current_evidence(db, task) if db is not None else None
    return {k: s for k, s in (task.reviewer_signoffs or {}).items()
            if s.get("evidence_id") is None or (cur is not None and s["evidence_id"] == cur.id)}


def missing_signoffs(task: Task) -> list[str]:
    signed = _valid_signoffs(task)
    return [k for k in needed_signoffs(task) if k not in signed]


def required_signer(db, task: Task, key: str) -> Person | None:
    if key == "system_owner":
        asset = db.get(Asset, task.asset_id)
        owner = db.get(Person, asset.owner_id) if asset.owner_id else None
        return owner if owner and owner.active else (db.get(Person, task.owner_id) if task.owner_id else None)
    return first_with_role(db, SIGNOFF_ROLE[key])


def _check_signer(db, task: Task, key: str, person: Person):
    if key not in needed_signoffs(task):
        raise ValueError(f"this task does not need a {SIGNOFF_LABEL.get(key, key)} sign-off")
    if task.status in TERMINAL:
        raise ValueError("this task is already closed")
    signer = required_signer(db, task, key)
    if signer is None or signer.id != person.id:
        raise PermissionError(f"the {SIGNOFF_LABEL[key]} sign-off belongs to {signer.name if signer else 'nobody'}")


def sign_required_review(db, task: Task, key: str, person: Person, *, record_version: str | None = None,
                         record_source: str | None = None, note: str = ""):
    """One function signs off the claim. The system owner states what their own component record shows; if that
    contradicts the evidence, or there is no record, the sign-offs disagree and the claim goes to Cannot Determine.
    Everyone but the system owner must be independent of the fix, like the evidence reviewer (E3)."""
    _check_signer(db, task, key, person)
    if key in _valid_signoffs(task):
        raise ValueError(f"{SIGNOFF_LABEL[key]} has already signed off")
    ev = current_evidence(db, task)
    if key in DUAL and ev is None:
        raise ValueError(f"{SIGNOFF_LABEL[key]} signs off against submitted evidence, and there is none yet")
    if key != "system_owner":
        submitters = {e.submitter_id for e in db.scalars(select(Evidence).where(Evidence.task_id == task.id))}
        if person.id == task.fixed_by_id or person.id in submitters:
            raise PermissionError("E3: whoever fixed the system or submitted its evidence cannot also sign it off")
    entry = {"by": person.name, "at": clock.now().isoformat(), "evidence_id": ev.id if ev else None}
    if note:
        entry["note"] = note
    if key == "system_owner":
        conflict = _record_conflict(task, ev, record_version, record_source)
        if conflict:
            return dispute_signoff(db, task, key, person, conflict, auto=True)
        entry.update(record_source=record_source, record_version=record_version or None)
    task.reviewer_signoffs = {**_valid_signoffs(task), key: entry}
    audit.log(db, person.directory_id, "task", task.id, "required_signoff", role=key,
              record=f"{record_version or 'not present'} ({RECORD_SOURCES.get(record_source, record_source)})"
              if key == "system_owner" else None)
    if not missing_signoffs(task):
        done = db.scalar(select(Evidence).where(Evidence.task_id == task.id, Evidence.status == "accepted",
                                                Evidence.claim_id.is_(None)).order_by(Evidence.id.desc()))
        if done is not None:
            _finish(db, task, done, db.get(Person, done.reviewer_id) or person)
            try_close_obligation(db, db.get(Obligation, task.obligation_id))
    return None


def _record_conflict(task: Task, ev: Evidence, record_version: str | None, record_source: str | None) -> str | None:
    """Compare the system owner's component record with the evidence. Returns why they disagree, if they do."""
    if not record_source:
        raise ValueError("say where your component record comes from")
    if record_source not in RECORD_SOURCES:
        raise ValueError(f"record source must be one of {', '.join(RECORD_SOURCES)}")
    comp = task.component or "the component"
    if record_source == "none":
        return (f"the system owner has no component record for {comp}; an empty record next to a clean scan is "
                "uncertainty, not agreement")
    if ev.purpose == "absence":
        if record_version:
            return (f"the system owner's record shows {comp} {record_version} installed, but the evidence claims "
                    f"{comp} is not present")
        return None
    if not record_version:
        raise ValueError(f"state the {comp} version your component record shows")
    if ev.observed_version and versions.norm(record_version) != versions.norm(ev.observed_version):
        return (f"the system owner's record shows {comp} {record_version}, but the verified evidence observed "
                f"{ev.observed_version}")
    return None


def dispute_signoff(db, task: Task, key: str, person: Person, reason: str, *, auto: bool = False):
    """The sign-offs disagree (§6.5). The claim reverts to Cannot Determine: the evidence is set aside, every
    sign-off is cleared, both functions get a task, and the owner's manager is told at once."""
    _check_signer(db, task, key, person)
    if not (reason or "").strip():
        raise ValueError("say why you disagree")
    ev = current_evidence(db, task)
    if ev is None:
        raise ValueError("there is no evidence to disagree with")
    now = clock.now()
    asset = db.get(Asset, task.asset_id)
    label = f"TSK-{task.id:04d}"
    ev.status, ev.reasons = "rejected", [f"Sign-offs disagree ({SIGNOFF_LABEL[key]}): {reason}"]
    if task.status not in TERMINAL:
        task.status = "open"
    task.reviewer_signoffs = {}
    rounds = (task.signoff_dispute or {}).get("rounds", 0) + 1
    audit.log(db, person.directory_id, "task", task.id, "signoff_disputed", role=key, reason=reason, auto=auto,
              evidence=ev.id, rounds=rounds)
    # both sides receive a task: IT reconciles the record, Information Security re-verifies coverage
    sev = "High" if task.impact == "High" else "Medium"
    findings = []
    for side, what in (("system_owner", "reconcile the component record"),
                       ("security", "re-verify that the evidence covered the component")):
        who = required_signer(db, task, side)
        f = raise_finding(db, "signoff_disagreement", sev, f"{asset.name} ({label}): {what}",
                          f"Remediation sign-offs disagree. {SIGNOFF_LABEL[key]}: {reason}", assigned_to=who,
                          is_test=task.is_test)
        findings.append(f.id)
        notify(db, task=task, person=who, kind="signoff_disagreement",
               message=f"{asset.name}: sign-offs disagree, {what}. {reason}")
    mgr = _manager_of(db, task)
    notify(db, task=task, person=mgr, kind="signoff_disputed",
           message=f"{task.impact} task {task.id} on {asset.name}: remediation sign-offs disagree ({reason})")
    audit.log(db, "system", "task", task.id, "escalated", to="manager", reason="remediation sign-offs disagree")
    task.signoff_dispute = {"key": key, "label": SIGNOFF_LABEL[key], "by": person.name, "reason": reason,
                            "at": now.isoformat(), "auto": auto, "evidence_id": ev.id, "rounds": rounds,
                            "escalated_to": [mgr.name] if mgr else [], "escalated_at": None, "resolved_at": None,
                            "finding_ids": findings}
    return ev


def _resolve_dispute(db, task: Task, how: str):
    d = task.signoff_dispute
    if not d or d.get("resolved_at"):
        return
    for fid in d.get("finding_ids") or []:
        f = db.get(Finding, fid)
        if f and f.status == "open":
            f.status = "resolved"
    task.signoff_dispute = {**d, "resolved_at": clock.now().isoformat(), "resolved_how": how}
    audit.log(db, "system", "task", task.id, "signoff_dispute_resolved", how=how)


def check_disputes(db) -> list[int]:
    """Sweep: a disagreement nobody has resolved in time goes up to the risk owner and senior management, and into
    the board report. Escalated once. A task closed another way (e.g. risk acceptance) ends its dispute."""
    escalated, now = [], clock.now()
    limit = timedelta(hours=TIMINGS["signoff_dispute_escalate_hours"])
    for t in db.scalars(select(Task).where(Task.signoff_dispute.is_not(None))):
        d = t.signoff_dispute
        if not d or d.get("resolved_at"):
            continue
        if t.status in TERMINAL:
            _resolve_dispute(db, t, f"task {t.status.replace('_', ' ')}")
            continue
        if d.get("escalated_at") or now - _parse(d["at"]) < limit:
            continue
        asset = db.get(Asset, t.asset_id)
        targets = people_with_role(db, "risk_owner") + people_with_role(db, "senior_mgmt")
        if not _sent(db, t, "deadline_missed"):  # someone may already have escalated it by hand (Alerts page)
            for p in targets:
                notify(db, task=t, person=p, kind="dispute_unresolved",
                       message=f"Remediation sign-offs on {asset.name} (task {t.id}) still disagree after "
                               f"{TIMINGS['signoff_dispute_escalate_hours']}h: {d['reason']}")
        t.board_flag = True
        t.signoff_dispute = {**d, "escalated_at": now.isoformat(),
                             "escalated_to": (d.get("escalated_to") or []) + [p.name for p in targets]}
        audit.log(db, "system", "task", t.id, "escalated", to="risk_owner+senior_mgmt",
                  reason="remediation sign-off disagreement unresolved", board_report=True)
        escalated.append(t.id)
    return escalated


def _parse(iso: str):
    from datetime import datetime
    return datetime.fromisoformat(iso)


def signoff_view(db, task: Task) -> list[dict]:
    out, signed = [], _valid_signoffs(task)
    for key in needed_signoffs(task):
        s = signed.get(key)
        who = required_signer(db, task, key)
        out.append({"key": key, "label": SIGNOFF_LABEL.get(key, key), "dual": key in DUAL,
                    "confirms": SIGNOFF_CONFIRMS.get(key), "signer": who.name if who else None,
                    "signed_by": s["by"] if s else None, "signed_at": s["at"] if s else None,
                    "record_version": s.get("record_version") if s else None,
                    "record_source": RECORD_SOURCES.get(s.get("record_source")) if s and s.get("record_source") else None,
                    "note": s.get("note") if s else None})
    return out


def try_close_obligation(db, ob: Obligation) -> tuple[bool, list[str]]:
    """E5: all affected assets have an approved disposition and none is Cannot Determine."""
    blockers = []
    for t in db.scalars(select(Task).where(Task.obligation_id == ob.id, Task.is_test == False)):  # noqa: E712
        if t.status not in TERMINAL:
            name = db.get(Asset, t.asset_id).name
            blockers.append(f"{name}: TSK-{t.id:04d} ({t.kind}) is still {t.status.replace('_', ' ')}")
    for c in db.scalars(select(Claim).where(Claim.obligation_id == ob.id, Claim.claim_type == "applicability",
                                            Claim.status == "active", Claim.value == "cannot_determine")):
        blockers.append(f"{db.get(Asset, c.asset_id).name}: still Cannot Determine, so it is not yet known whether it is affected")
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
