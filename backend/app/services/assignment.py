"""Component 4: assignment, acknowledgement, escalation, risk acceptance."""
from datetime import timedelta

from sqlalchemy import select

from ..clock import clock, add_working_days
from ..config import TIMINGS
from ..models import Task, Asset, Person, Notification, RiskAcceptance, Obligation
from . import audit
from .common import (notify, raise_finding, first_with_role, people_with_role, bu_head,
                     owner_validity, create_claim, add_dependency)

OPEN_STATES = ("open", "reopened", "fixed_pending_review")


def _deadlines(impact: str, start):
    t = TIMINGS[impact]
    ack_due = (add_working_days(start, t["ack_working_days"]) if "ack_working_days" in t
               else start + timedelta(hours=t["ack_hours"]))
    interim = start + timedelta(hours=t["interim_hours"]) if t.get("interim_hours") else None
    return ack_due, interim, start + timedelta(hours=t["remediate_hours"])


def is_overdue(task: Task, now=None) -> bool:
    """Past the remediation deadline and not yet dealt with. A task the owner fixed on
    time and that is only waiting for independent review is not overdue."""
    now = now or clock.now()
    if task.status not in OPEN_STATES or now < task.remediate_due:
        return False
    return not (task.status == "fixed_pending_review" and task.fixed_at and task.fixed_at <= task.remediate_due)


def route(db, asset: Asset) -> tuple[Person | None, str | None]:
    """Named owner if valid; otherwise business-unit head, then CISO (B3)."""
    ok, why = owner_validity(db, asset)
    if ok:
        return db.get(Person, asset.owner_id), None
    return (bu_head(db, asset) or first_with_role(db, "ciso")), why


def create_task(db, *, obligation: Obligation, asset: Asset, kind: str, impact: str,
                component: str | None, claim=None, reviewers=None, is_test=False,
                owner: Person | None = None) -> Task:
    now = clock.now()
    owner, invalid_reason = (owner, None) if owner is not None else route(db, asset)
    ack_due, interim_due, rem_due = _deadlines(impact, now)
    t = Task(obligation_id=obligation.id, asset_id=asset.id, kind=kind, impact=impact, component=component,
             owner_id=owner.id if owner else None, original_owner_id=asset.owner_id, created_at=now,
             ack_due=ack_due, interim_due=interim_due, remediate_due=rem_due,
             required_reviewers=reviewers or [], is_test=is_test)
    db.add(t)
    db.flush()
    audit.log(db, "system", "task", t.id, "created", kind=kind, impact=impact, asset=asset.name,
              owner=owner.directory_id if owner else None, ack_due=str(ack_due), remediate_due=str(rem_due))
    if claim is not None:
        add_dependency(db, claim, "task", t.id)
    if invalid_reason:
        raise_finding(db, "ownership_correction", "High" if impact == "High" else "Medium",
                      f"{asset.name}: invalid owner record", f"{invalid_reason}. Task routed to fallback "
                      f"{owner.name if owner else 'nobody'}.", assigned_to=owner, is_test=is_test)
        audit.log(db, "system", "task", t.id, "fallback_routed", reason=invalid_reason)
    notify(db, task=t, person=owner, kind="assign",
           message=f"{impact} {kind} task for {asset.name}: {obligation.title}")
    return t


def acknowledge(db, task: Task, person: Person):
    if task.owner_id != person.id:
        raise PermissionError("only the assigned owner can acknowledge")
    task.acknowledged_at = clock.now()
    task.acknowledged_by_id = person.id
    audit.log(db, person.directory_id, "task", task.id, "acknowledged")


def escalate_now(db, task: Task, by: Person) -> str:
    """Take the next escalation step immediately instead of waiting for the timer. Returns who was told."""
    if task.status not in OPEN_STATES:
        raise ValueError("this item is no longer open")
    asset = db.get(Asset, task.asset_id)
    msg = f"{task.impact} task {task.id} on {asset.name}: escalated by {by.name}"
    if task.acknowledged_at is None:
        for kind, who in (("escalate_manager", "manager"), ("escalate_ciso", "ciso")):
            if not _sent(db, task, kind):
                target = _manager_of(db, task) if who == "manager" else first_with_role(db, "ciso")
                if target is None:
                    raise ValueError(f"there is no active {who} to escalate to")
                notify(db, task=task, person=target, kind=kind, message=msg + " (unacknowledged)")
                audit.log(db, by.directory_id, "task", task.id, "escalated", to=who, reason="manual escalation: unacknowledged")
                return target.name
    if not _sent(db, task, "deadline_missed"):
        targets = people_with_role(db, "risk_owner") + people_with_role(db, "senior_mgmt")
        if not targets:
            raise ValueError("there is no risk owner or senior manager to escalate to")
        for p in targets:
            notify(db, task=task, person=p, kind="deadline_missed", message=msg + " (to risk owner and senior management)")
        task.board_flag = True
        audit.log(db, by.directory_id, "task", task.id, "escalated", to="risk_owner+senior_mgmt",
                  reason="manual escalation", board_report=True)
        return " and ".join(p.name for p in targets)
    raise ValueError("already escalated to the top of the ladder: risk owner and senior management")


def send_reminder(db, task: Task, by: Person) -> str:
    owner = db.get(Person, task.owner_id) if task.owner_id else None
    if owner is None:
        raise ValueError("this item has no owner to remind")
    asset = db.get(Asset, task.asset_id)
    notify(db, task=task, person=owner, kind="reminder",
           message=f"Reminder from {by.name}: {task.impact} task {task.id} on {asset.name} needs your "
                   f"{'acknowledgement' if task.acknowledged_at is None else 'remediation update'}")
    audit.log(db, by.directory_id, "task", task.id, "reminder_sent", to=owner.directory_id)
    return owner.name


def reroute_task(db, task: Task, reason: str, actor="system"):
    """Immediate reroute to the BU head. Deadlines are untouched, so a rejection
    can never become an informal extension (B4)."""
    asset = db.get(Asset, task.asset_id)
    new_owner = bu_head(db, asset) or first_with_role(db, "ciso")
    if new_owner is None or new_owner.id == task.owner_id:
        return
    old = task.owner_id
    task.owner_id = new_owner.id
    task.acknowledged_at = None
    audit.log(db, actor, "task", task.id, "rerouted", reason=reason, from_owner=old, to=new_owner.directory_id,
              ack_due=str(task.ack_due), remediate_due=str(task.remediate_due))
    raise_finding(db, "ownership_correction", "High" if task.impact == "High" else "Medium",
                  f"{asset.name}: ownership correction", f"Task rerouted: {reason}", assigned_to=new_owner,
                  is_test=task.is_test)
    notify(db, task=task, person=new_owner, kind="assign", message=f"Rerouted task for {asset.name}: {reason}")


def reject_task(db, task: Task, person: Person, reason: str):
    if task.owner_id != person.id:
        raise PermissionError("only the assigned owner can reject")
    audit.log(db, person.directory_id, "task", task.id, "rejected_by_recipient", reason=reason)
    reroute_task(db, task, f"rejected by {person.name}: {reason}", actor=person.directory_id)


def transfer_task(db, task: Task, new_owner: Person, approver: Person):
    if approver.role not in ("bu_head", "manager", "ciso", "risk_owner"):
        raise PermissionError("transfer must be approved by a manager or business-unit head")
    old = task.owner_id
    task.owner_id = new_owner.id
    audit.log(db, approver.directory_id, "task", task.id, "transferred", from_owner=old,
              to=new_owner.directory_id, deadlines_reset=False)
    notify(db, task=task, person=new_owner, kind="assign", message="Task transferred to you; deadline unchanged")


def record_interim_control(db, task: Task, person: Person, description: str, relies_on_claim=None):
    """An interim control may rely on a monitoring claim; if that claim lapses it reopens (F2)."""
    task.interim_control = description
    task.interim_reopened = False
    if relies_on_claim is not None:
        add_dependency(db, relies_on_claim, "interim_control", task.id)
    audit.log(db, person.directory_id, "task", task.id, "interim_control_recorded", control=description)


# ---- risk acceptance (D4, D5) ----
def request_risk_acceptance(db, task: Task, person: Person, *, scenario, controls, residual_risk,
                            days: int) -> RiskAcceptance:
    if not (scenario and controls and residual_risk):
        raise ValueError("risk acceptance needs scenario, controls and residual risk")
    ra = RiskAcceptance(task_id=task.id, scenario=scenario, controls=controls, residual_risk=residual_risk,
                        requested_by_id=person.id, created_at=clock.now(),
                        expiry=clock.now() + timedelta(days=min(days, TIMINGS["risk_acceptance_max_days"])))
    db.add(ra)
    db.flush()
    audit.log(db, person.directory_id, "risk_acceptance", ra.id, "requested", task=task.id)
    return ra


def approve_risk_acceptance(db, ra: RiskAcceptance, approver: Person):
    task = db.get(Task, ra.task_id)
    if approver.role not in ("risk_owner", "senior_mgmt", "ciso"):
        raise PermissionError("approver is not an authorised risk owner")
    if approver.id in (task.owner_id, ra.requested_by_id):
        raise PermissionError("approver cannot be the requester or task owner")
    if task.impact == "High" and approver.role == "ciso" and not ra.controls.strip():
        raise PermissionError("High impact risk acceptance requires compensating controls")
    ra.approver_id = approver.id
    ra.status = "approved"
    asset = db.get(Asset, task.asset_id)
    claim = create_claim(db, actor=approver.directory_id, claim_type="risk_acceptance", asset_id=asset.id,
                         obligation_id=task.obligation_id, value="accepted", owner_id=ra.requested_by_id,
                         statement=f"Risk of task {task.id} on {asset.name} accepted: {ra.scenario}",
                         submitter_id=ra.requested_by_id, reviewer_id=approver.id, expiry=ra.expiry,
                         verification_method="approval", evidence_ts=clock.now())
    ra.claim_id = claim.id
    add_dependency(db, claim, "risk_acceptance", ra.id)
    task.status, task.disposition, task.closed_at = "risk_accepted", "risk_accepted", clock.now()
    audit.log(db, approver.directory_id, "risk_acceptance", ra.id, "approved", expiry=str(ra.expiry))
    from .closure import try_close_obligation
    try_close_obligation(db, db.get(Obligation, task.obligation_id))


def _sent(db, task: Task, kind: str) -> bool:
    return db.scalar(select(Notification.id).where(Notification.task_id == task.id,
                                                   Notification.kind == kind)) is not None


def _manager_of(db, task: Task) -> Person | None:
    owner = db.get(Person, task.owner_id) if task.owner_id else None
    if owner and owner.manager_id:
        m = db.get(Person, owner.manager_id)
        if m and m.active:
            return m
    head = bu_head(db, db.get(Asset, task.asset_id))
    if head and (owner is None or head.id != owner.id):
        return head
    return first_with_role(db, "ciso")  # owner is the BU head: nobody between them and the CISO


def tick_task(db, task: Task):
    """Evaluate escalation timers for one task against the current clock."""
    if task.status not in OPEN_STATES:
        return
    now, cfg = clock.now(), TIMINGS[task.impact]
    asset = db.get(Asset, task.asset_id)

    ok, why = owner_validity(db, asset)
    owner = db.get(Person, task.owner_id) if task.owner_id else None
    if owner and owner.role == "owner" and (not owner.active or not ok) and asset.owner_id == owner.id:
        reroute_task(db, task, why or "owner invalid", actor="system")

    if task.acknowledged_at is None:
        for rule in cfg["ack_escalations"]:
            due = (add_working_days(task.created_at, rule["after_working_days"]) if "after_working_days" in rule
                   else task.created_at + timedelta(hours=rule["after_hours"]))
            kind = f"escalate_{rule['to']}"
            if now >= due and not _sent(db, task, kind):
                target = _manager_of(db, task) if rule["to"] == "manager" else first_with_role(db, "ciso")
                notify(db, task=task, person=target, kind=kind,
                       message=f"{task.impact} task {task.id} on {asset.name} unacknowledged")
                audit.log(db, "system", "task", task.id, "escalated", to=rule["to"], reason="unacknowledged")

    if (task.impact == "High" and task.interim_due and now >= task.interim_due
            and not task.interim_control and not _has_accepted_risk(db, task)
            and not _sent(db, task, "interim_missing")):
        notify(db, task=task, person=first_with_role(db, "ciso"), kind="interim_missing",
               message=f"No compensating control or risk acceptance for task {task.id} on {asset.name}")
        raise_finding(db, "interim_control_missing", "High", f"Task {task.id}: {asset.name}",
                      "Unpatched High Impact task has neither a compensating control nor a risk acceptance",
                      assigned_to=first_with_role(db, "ciso"), is_test=task.is_test)
        audit.log(db, "system", "task", task.id, "escalated", to="ciso", reason="interim control missing")

    if is_overdue(task, now) and not _sent(db, task, "deadline_missed"):
        for p in people_with_role(db, "risk_owner") + people_with_role(db, "senior_mgmt"):
            notify(db, task=task, person=p, kind="deadline_missed",
                   message=f"Remediation deadline missed: task {task.id} on {asset.name}")
        task.board_flag = True
        audit.log(db, "system", "task", task.id, "escalated", to="risk_owner+senior_mgmt",
                  reason="remediation deadline missed", board_report=True)


def _has_accepted_risk(db, task: Task) -> bool:
    return db.scalar(select(RiskAcceptance.id).where(RiskAcceptance.task_id == task.id,
                                                     RiskAcceptance.status == "approved")) is not None


def tick_all(db):
    for t in db.scalars(select(Task).where(Task.status.in_(OPEN_STATES))):
        tick_task(db, t)


def countdowns(task: Task) -> dict:
    """Separate acknowledgement and remediation countdowns, in hours."""
    now = clock.now()
    return {
        "ack_remaining_h": None if task.acknowledged_at else round((task.ack_due - now).total_seconds() / 3600, 1),
        "remediate_remaining_h": round((task.remediate_due - now).total_seconds() / 3600, 1),
        "interim_remaining_h": (round((task.interim_due - now).total_seconds() / 3600, 1)
                                if task.interim_due and not task.interim_control else None),
        "interim_state": ("not_required" if not task.interim_due else "reopened" if task.interim_reopened
                          else "recorded" if task.interim_control else "pending"),
    }
