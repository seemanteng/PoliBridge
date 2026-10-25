"""Component 6: ongoing assurance. Expiry (F1), dependency reopen (F2),
risk-acceptance reopen (D5), canaries (F3), dashboard and oversight summaries."""
from datetime import timedelta

from sqlalchemy import select, func

from ..clock import clock
from ..config import TIMINGS
from ..models import (Claim, Dependency, Task, Obligation, RiskAcceptance, Asset, Advisory, Finding, Person,
                      Notification, CanaryRun, AuditEvent, BoardItem, Source, FeedItem)
from ..rules import applicability as A
from . import audit
from .common import notify, raise_finding, owner_validity, bu_head, first_with_role, people_with_role
from .assignment import tick_all, reroute_task, create_task, OPEN_STATES, countdowns, route, is_overdue
from .assessment import assess_asset
from .closure import TERMINAL


def _reopen_obligation(db, ob_id):
    ob = db.get(Obligation, ob_id) if ob_id else None
    if ob and ob.status == "closed":
        ob.status, ob.closed_at = "open", None
        audit.log(db, "system", "obligation", ob.id, "reopened")


def expire_claims(db) -> list[Claim]:
    now = clock.now()
    expired = list(db.scalars(select(Claim).where(Claim.status == "active", Claim.expiry.is_not(None),
                                                  Claim.expiry <= now)))
    for c in expired:
        c.status = "expired"
        audit.log(db, "system", "claim", c.id, "expired", type=c.claim_type, expiry=str(c.expiry))   # F1
        owner = db.get(Person, c.owner_id) if c.owner_id else None
        notify(db, person=owner, kind="expiry",
               message=(f"Re-check due: {c.statement} is no longer supported; its review cycle has lapsed."
                        if c.claim_type == "source_currency" else
                        f"Claim lapsed: {c.statement}. Reverted to Cannot Determine."))
        for dep in db.scalars(select(Dependency).where(Dependency.claim_id == c.id)):   # F2
            _reopen_dependent(db, c, dep)
        _type_specific(db, c)
    return expired


def _reopen_dependent(db, claim: Claim, dep: Dependency):
    if dep.dependent_type == "task":
        t = db.get(Task, dep.dependent_id)
        if t and t.status in TERMINAL:
            t.status, t.disposition, t.closed_at = "reopened", None, None
            audit.log(db, "system", "task", t.id, "reopened", because=f"{claim.statement} lapsed")
            notify(db, task=t, person=db.get(Person, t.owner_id) if t.owner_id else None, kind="reopened",
                   message=f"Task {t.id} reopened: {claim.statement} no longer supported by current evidence")
            _reopen_obligation(db, t.obligation_id)
        if t:
            t.board_flag = t.board_flag or t.impact == "High"
    elif dep.dependent_type == "risk_acceptance":
        ra = db.get(RiskAcceptance, dep.dependent_id)
        if ra and ra.status == "approved":
            ra.status = "expired"
            t = db.get(Task, ra.task_id)
            if t and t.status == "risk_accepted":      # D5
                t.status, t.disposition, t.closed_at = "reopened", None, None
                audit.log(db, "system", "task", t.id, "reopened", because="risk acceptance expired")
                _reopen_obligation(db, t.obligation_id)
            for p in people_with_role(db, "risk_owner"):
                notify(db, task=t, person=p, kind="risk_acceptance_expired",
                       message=f"Risk acceptance {ra.id} expired; task reopened")
    elif dep.dependent_type == "interim_control":
        t = db.get(Task, dep.dependent_id)
        if t and t.interim_control:
            t.interim_control, t.interim_reopened = None, True
            audit.log(db, "system", "task", t.id, "interim_control_reopened", because=f"{claim.statement} lapsed")
            notify(db, task=t, person=db.get(Person, t.owner_id) if t.owner_id else None, kind="reopened",
                   message=f"Interim control on task {t.id} reopened: {claim.statement} lapsed")
    elif dep.dependent_type == "board_item":
        b = db.get(BoardItem, dep.dependent_id)
        if b:
            b.status = "reopened"
        audit.log(db, "system", "board_item", dep.dependent_id, "reopened", because=f"{claim.statement} lapsed")
    elif dep.dependent_type == "obligation":  # the source it quotes has not been re-checked: flag, don't reopen
        audit.log(db, "system", "obligation", dep.dependent_id, "source_review_lapsed", because=f"{claim.statement} lapsed")


def _type_specific(db, c: Claim):
    asset = db.get(Asset, c.asset_id) if c.asset_id else None
    if c.claim_type == "source_currency" and c.source_id:
        from .sources import overdue_subject
        src = db.get(Source, c.source_id)
        owner = db.get(Person, c.owner_id) if c.owner_id else None
        n = db.scalar(select(func.count(Dependency.id)).where(Dependency.claim_id == c.id,
                                                              Dependency.dependent_type == "obligation"))
        raise_finding(db, "source_review_overdue", "High" if src.kind in ("regulator", "contract") else "Medium",
                      overdue_subject(src),
                      f"Last checked {c.evidence_ts:%d %b %Y} on a {src.review_days}-day cycle. {n} approved "
                      f"obligation{'s' if n != 1 else ''} may rest on outdated wording until someone checks it.",
                      assigned_to=owner or first_with_role(db, "compliance"))
    elif c.claim_type == "applicability" and asset and c.obligation_id:
        ob = db.get(Obligation, c.obligation_id)
        adv = db.get(Advisory, ob.advisory_id)
        assess_asset(db, ob, adv, asset, actor="system")   # stale inventory -> Cannot Determine + investigation task
        _reopen_obligation(db, ob.id)
    elif c.claim_type == "monitoring" and asset:
        owner = db.get(Person, c.owner_id) if c.owner_id else None
        raise_finding(db, "lapsed_monitoring", "High", f"{asset.name}: {c.statement}",
                      "Monitoring control evidence (certificate/configuration) has expired",
                      assigned_to=owner or first_with_role(db, "ciso"))
        ob = Obligation(kind="policy", title=f"Renew {c.component or 'monitoring control'} for {asset.name}",
                        status="open", impact="High", created_at=clock.now(),
                        required_action="Renew the certificate/configuration and submit evidence")
        db.add(ob)
        db.flush()
        create_task(db, obligation=ob, asset=asset, kind="remediation", impact="High", component=c.component,
                    owner=owner if owner and owner.active else None)
    elif c.claim_type == "ownership" and asset:
        raise_finding(db, "attestation_overdue", "High" if asset.critical else "Medium",
                      f"{asset.name}: ownership attestation lapsed", c.statement, assigned_to=bu_head(db, asset))
        for t in db.scalars(select(Task).where(Task.asset_id == asset.id, Task.status.in_(OPEN_STATES))):
            reroute_task(db, t, "ownership attestation expired")


def sweep(db) -> dict:
    """One pass of everything time-driven. Call after advancing the clock."""
    from . import feeds, approvals
    polled = feeds.poll_all(db)
    tick_all(db)
    expired = expire_claims(db)
    tick_all(db)
    evaluate_canaries(db)
    silent = feeds.check_health(db)
    late_signoffs = approvals.check_clause_signoffs(db)
    db.commit()
    return {"expired_claims": [c.id for c in expired],
            "sources_due": [db.get(Source, c.source_id).name for c in expired if c.claim_type == "source_currency"],
            "feeds": [p for p in polled if p["received"] or not p["ok"]], "silent_feeds": silent,
            "late_signoffs": late_signoffs}


# ---- canaries (F3) ----
CANARY_PRODUCT = "pb-canary-component"


def run_canary(db, asset: Asset) -> CanaryRun:
    """Send a test advisory, clearly marked, through the primary advisory feed and the real matching rule, then
    check routing and escalation would work for a real one. A dead feed fails the canary at its first step."""
    from . import feeds
    now = clock.now()
    src = db.scalar(select(Source).where(Source.kind == "advisory", Source.status == "approved").order_by(Source.id))
    run = CanaryRun(asset_id=asset.id, created_at=now, feed=src.name if src else None)
    db.add(run)
    db.flush()
    if src is None:
        return _fail_early(db, run, "feed", "no approved advisory feed to send the test advisory through")
    item = FeedItem(source_id=src.id, cve=f"PB-CANARY-{run.id:04d}", product=CANARY_PRODUCT, affected_versions=["1.0"],
                    cvss=10.0, kev=False, description="TEST advisory injected by the PoliBridge canary self-test.",
                    published_at=now, available_at=now, is_canary=True)
    db.add(item)
    db.flush()
    res = feeds.poll(db, src, only=item)
    if not res["ok"] or item.outcome != "ingested":
        return _fail_early(db, run, "feed", f"test advisory never arrived through {src.name}: "
                                            f"{res['error'] or item.reason}")
    ob = db.scalar(select(Obligation).where(Obligation.advisory_id == item.advisory_id))
    adv = db.get(Advisory, item.advisory_id)
    comps = [{"name": c.name, "version": c.version, "conflict": c.conflict} for c in asset.components]
    label, why = A.assess_asset(components=comps + [{"name": CANARY_PRODUCT, "version": "1.0", "conflict": False}],
                                inventory_verified_at=now, inventory_scope=[r.rstrip("/") + "/**" for r in asset.app_roots or []],
                                app_roots=asset.app_roots or [], product=adv.product, affected_specs=adv.affected_versions,
                                now=now, stale_days=TIMINGS["component_data_stale_days"])
    if label != A.APPLIES:
        return _fail_early(db, run, "match", f"the matching rule labelled the test component {label}: {why}")
    ob.impact = "High"
    task = create_task(db, obligation=ob, asset=asset, kind="remediation", impact="High", component=CANARY_PRODUCT,
                       is_test=True)
    run.task_id = task.id
    failures = []
    ok, why = owner_validity(db, asset)
    if not ok:
        failures.append(f"not routed to a valid named owner: {why}")
    owner = db.get(Person, task.owner_id) if task.owner_id else None
    if owner is None or not (owner.active and owner.deliverable):
        failures.append("recipient cannot receive notifications")
    mgr = db.get(Person, owner.manager_id) if owner and owner.manager_id else None
    if mgr is None or not mgr.active:
        failures.append("no active manager for escalation")
    if first_with_role(db, "ciso") is None:
        failures.append("no CISO for escalation")
    if failures:
        run.stage = "route"
    _finish_if_decided(db, run, task, failures)
    audit.log(db, "canary", "canary_run", run.id, run.status, asset=asset.name, failures=failures)
    return run


def _fail_early(db, run: CanaryRun, stage: str, failure: str) -> CanaryRun:
    run.status, run.stage, run.failures = "failed", stage, [failure]
    asset = db.get(Asset, run.asset_id)
    to = first_with_role(db, "compliance") if stage == "feed" else bu_head(db, asset) or first_with_role(db, "ciso")
    raise_finding(db, "canary_failure", "High", f"Canary failed on {asset.name}", failure, assigned_to=to)
    audit.log(db, "canary", "canary_run", run.id, "failed", asset=asset.name, stage=stage, failures=[failure])
    return run


def _finish_if_decided(db, run, task, failures):
    if failures:
        run.status, run.failures = "failed", failures
        asset = db.get(Asset, run.asset_id)
        raise_finding(db, "canary_failure", "High", f"Canary failed on {asset.name}", "; ".join(failures),
                      assigned_to=bu_head(db, asset) or first_with_role(db, "ciso"))
        task.status, task.disposition = "closed", "canary"


def evaluate_canaries(db):
    """After the ack deadline, an unacknowledged canary must have escalated on schedule."""
    now = clock.now()
    for run in db.scalars(select(CanaryRun).where(CanaryRun.status == "running")):
        task = db.get(Task, run.task_id)
        if task.acknowledged_at is None and now < task.ack_due:
            continue
        failures = []
        if task.acknowledged_at is None:
            sent = db.scalar(select(Notification.id).where(Notification.task_id == task.id,
                                                           Notification.kind == "escalate_manager"))
            if not sent:
                failures.append("unacknowledged canary did not escalate to the manager on schedule")
        if failures:
            _finish_if_decided(db, run, task, failures)
        else:
            run.status = "passed"
            task.status, task.disposition = "closed", "canary"
        audit.log(db, "canary", "canary_run", run.id, run.status, failures=failures)


# ---- reporting ----
def dashboard(db) -> dict:
    now = clock.now()
    live = [t for t in db.scalars(select(Task).where(Task.is_test == False)) if t.status in OPEN_STATES]  # noqa: E712
    overdue = [t for t in live if is_overdue(t, now)]
    cd = db.scalars(select(Claim).where(Claim.claim_type == "applicability", Claim.status == "active",
                                        Claim.value == "cannot_determine")).all()
    accepted = db.scalars(select(RiskAcceptance).where(RiskAcceptance.status == "approved")).all()
    expired = db.scalars(select(Claim).where(Claim.status == "expired")).all()
    lapsed = [c for c in expired if c.claim_type == "monitoring"]
    runs = db.scalars(select(CanaryRun)).all()
    done = [r for r in runs if r.status != "running"]
    return {
        "overdue_tasks": len(overdue),
        "open_tasks": len(live),
        "unacknowledged_tasks": sum(t.acknowledged_at is None for t in live),
        "cannot_determine": len(cd),
        "accepted_risks": len(accepted),
        "expired_evidence": len(expired),
        "lapsed_monitoring": len(lapsed),
        "open_findings": db.scalar(select(func.count(Finding.id)).where(Finding.status == "open")),
        "board_items": sum(t.board_flag for t in db.scalars(select(Task).where(Task.is_test == False))),  # noqa: E712
        "canary": {"runs": len(done), "passed": sum(r.status == "passed" for r in done),
                   "pass_rate": round(sum(r.status == "passed" for r in done) / len(done), 2) if done else None},
        "now": str(now),
    }


def oversight_summary(db) -> dict:
    d = dashboard(db)
    flagged = [t for t in db.scalars(select(Task).where(Task.board_flag == True, Task.is_test == False))]  # noqa: E712
    return {"period_end": str(clock.now()), "headline": d,
            "board_risk_items": [{"task": t.id, "asset": db.get(Asset, t.asset_id).name, "impact": t.impact,
                                  "status": t.status, "remediate_due": str(t.remediate_due)} for t in flagged],
            "accepted_risks": [{"id": r.id, "task": r.task_id, "expiry": str(r.expiry)}
                               for r in db.scalars(select(RiskAcceptance).where(RiskAcceptance.status == "approved"))],
            "canary_failures": [f.subject for f in db.scalars(select(Finding).where(Finding.kind == "canary_failure"))]}


def audit_timeline(db, entity: str | None = None, entity_id: int | None = None, limit=2000):
    """Newest first."""
    q = select(AuditEvent).order_by(AuditEvent.id.desc())
    if entity:
        q = q.where(AuditEvent.entity == entity)
    if entity_id is not None:
        q = q.where(AuditEvent.entity_id == entity_id)
    return list(db.scalars(q.limit(limit)))
