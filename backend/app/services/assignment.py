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
OVERRIDE_ROLES = ("ciso", "risk_owner", "compliance", "cco", "legal")


def profile(task: Task) -> str:
    """Which timing block in timings.yaml applies: Emergency for C1 tasks, otherwise the impact level."""
    return "Emergency" if task.emergency else task.impact


def policy(task: Task) -> dict:
    return TIMINGS[profile(task)]


def _trigger(db, obligation: Obligation, is_test: bool, now) -> tuple[str, object]:
    """What the fix deadline counts from: the vendor's release, or a later known exploit. Tests, canaries and
    tasks with no advisory count from discovery."""
    from ..models import Advisory
    adv = db.get(Advisory, obligation.advisory_id) if obligation.advisory_id and not is_test else None
    if adv is None or adv.published_at is None:
        return "discovered", now
    if adv.exploited_at and adv.exploited_at > adv.published_at:
        return "exploit_known", min(adv.exploited_at, now)
    return "vendor_release", min(adv.published_at, now)


def _deadlines(cfg: dict, created, trigger_at):
    ack_due = (add_working_days(created, cfg["ack_working_days"]) if "ack_working_days" in cfg
               else created + timedelta(hours=cfg["ack_hours"]))
    interim = trigger_at + timedelta(hours=cfg["interim_hours"]) if cfg.get("interim_hours") else None
    return ack_due, interim, trigger_at + timedelta(hours=cfg["remediate_hours"])


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
                owner: Person | None = None, emergency: bool = False) -> Task:
    now = clock.now()
    owner, invalid_reason = (owner, None) if owner is not None else route(db, asset)
    emergency = bool(emergency and impact == "High")
    cfg = TIMINGS["Emergency" if emergency else impact]
    trigger, trigger_at = _trigger(db, obligation, is_test, now)
    ack_due, interim_due, rem_due = _deadlines(cfg, now, trigger_at)
    basis = {"trigger": trigger, "trigger_at": trigger_at.isoformat(), "overdue_at_discovery": rem_due <= now,
             "interim_reset": False, "override": None}
    if rem_due <= now or (interim_due and interim_due <= now):
        # found already late: keep the true fix deadline, but give a short, fresh window to reduce exposure
        interim_due = now + timedelta(hours=TIMINGS["late_discovery_interim_hours"])
        basis["interim_reset"] = True
    t = Task(obligation_id=obligation.id, asset_id=asset.id, kind=kind, impact=impact, component=component,
             owner_id=owner.id if owner else None, original_owner_id=asset.owner_id, created_at=now,
             ack_due=ack_due, interim_due=interim_due, remediate_due=rem_due, emergency=emergency,
             deadline_basis=basis, required_reviewers=reviewers or [], is_test=is_test)
    db.add(t)
    db.flush()
    audit.log(db, "system", "task", t.id, "created", kind=kind, impact=impact, asset=asset.name,
              owner=owner.directory_id if owner else None, ack_due=str(ack_due), remediate_due=str(rem_due),
              emergency=emergency, trigger=trigger, trigger_at=str(trigger_at))
    if claim is not None:
        add_dependency(db, claim, "task", t.id)
    if invalid_reason:
        raise_finding(db, "ownership_correction", "High" if impact == "High" else "Medium",
                      f"{asset.name}: invalid owner record", f"{invalid_reason}. Task routed to fallback "
                      f"{owner.name if owner else 'nobody'}.", assigned_to=owner, is_test=is_test)
        audit.log(db, "system", "task", t.id, "fallback_routed", reason=invalid_reason)
    notify(db, task=t, person=owner, kind="assign",
           message=f"{'Emergency' if emergency else impact} {kind} task for {asset.name}: {obligation.title}")
    if basis["overdue_at_discovery"]:
        audit.log(db, "system", "task", t.id, "overdue_at_discovery", remediate_due=str(rem_due),
                  trigger=trigger, trigger_at=str(trigger_at))
        _escalate_missed(db, t, asset, "fix deadline had already passed when the issue was found")
    return t


def _escalate_missed(db, task: Task, asset: Asset, reason: str):
    """D3: risk owner and senior management are told, and the item joins the board risk report."""
    if _sent(db, task, "deadline_missed"):
        return
    for p in people_with_role(db, "risk_owner") + people_with_role(db, "senior_mgmt"):
        notify(db, task=task, person=p, kind="deadline_missed",
               message=f"Remediation deadline missed: task {task.id} on {asset.name} ({reason})")
    task.board_flag = True
    audit.log(db, "system", "task", task.id, "escalated", to="risk_owner+senior_mgmt", reason=reason,
              board_report=True)


def override_deadline(db, task: Task, due, reason: str, by: Person):
    """A specific obligation, emergency or policy may set a shorter fix deadline. Only shorter: the more
    restrictive outcome applies (§6.8). A longer one is an exception and needs risk acceptance (D4)."""
    if by.role not in OVERRIDE_ROLES:
        raise PermissionError("a deadline override needs the CISO, risk owner, Compliance or Legal")
    if task.status not in OPEN_STATES:
        raise ValueError("this task is no longer open")
    if not (reason or "").strip():
        raise ValueError("an override needs a recorded reason")
    if due >= task.remediate_due:
        raise ValueError("an override can only shorten the deadline; a later date needs risk acceptance (D4)")
    if due <= clock.now():
        raise ValueError("the new deadline must be in the future")
    previous = task.remediate_due
    task.remediate_due = due
    if task.interim_due and task.interim_due > due:
        task.interim_due = due
    basis = dict(task.deadline_basis or {})
    basis["override"] = {"due": due.isoformat(), "previous": previous.isoformat(), "reason": reason.strip(),
                         "by": by.name, "at": clock.now().isoformat()}
    task.deadline_basis = basis
    audit.log(db, by.directory_id, "task", task.id, "deadline_overridden", previous=str(previous), due=str(due),
              reason=reason.strip())
    owner = db.get(Person, task.owner_id) if task.owner_id else None
    if owner:
        notify(db, task=task, person=owner, kind="deadline_overridden",
               message=f"Task {task.id}: fix now due {due:%d %b %H:%M} ({reason.strip()})")


def retime_for_exploit(db, advisory, exploited_at):
    """A working exploit has become known. Open tasks on internet-facing systems become Emergency, and their fix
    deadline moves to 48 hours from the exploit if that is sooner. A deadline is never moved later."""
    from .assessment import _classify
    advisory.exploited_at = exploited_at
    obs = db.scalars(select(Obligation).where(Obligation.advisory_id == advisory.id))
    for ob in obs:
        for t in db.scalars(select(Task).where(Task.obligation_id == ob.id, Task.status.in_(OPEN_STATES),
                                               Task.is_test == False)):  # noqa: E712
            asset = db.get(Asset, t.asset_id)
            info = _classify(advisory, asset, regulatory=ob.kind == "regulatory")
            if info["impact"] != "High" or not info["emergency"]:
                continue
            due = exploited_at + timedelta(hours=TIMINGS["Emergency"]["remediate_hours"])
            changed = not t.emergency or due < t.remediate_due
            t.impact, t.emergency = "High", True
            if due < t.remediate_due:
                t.remediate_due = due
                basis = dict(t.deadline_basis or {})
                basis.update(trigger="exploit_known", trigger_at=exploited_at.isoformat())
                t.deadline_basis = basis
            if changed:
                audit.log(db, "system", "task", t.id, "exploit_known", exploited_at=str(exploited_at),
                          remediate_due=str(t.remediate_due))
                owner = db.get(Person, t.owner_id) if t.owner_id else None
                if owner:
                    notify(db, task=t, person=owner, kind="exploit_known",
                           message=f"Working exploit known for task {t.id} on {asset.name}: now Emergency, "
                                   f"fix due {t.remediate_due:%d %b %H:%M}")


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
    now, cfg = clock.now(), policy(task)
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

    # progress check: a status request, not a deadline
    hours = cfg.get("progress_checkpoint_hours")
    owner = db.get(Person, task.owner_id) if task.owner_id else None  # may have just been rerouted
    if (hours and task.status in ("open", "reopened") and now >= task.created_at + timedelta(hours=hours)
            and owner and not _sent(db, task, "progress_check")):
        notify(db, task=task, person=owner, kind="progress_check",
               message=f"{hours}h progress check on task {task.id} ({asset.name}): is the fix in, a workaround in "
                       "place, or is the work blocked?")
        audit.log(db, "system", "task", task.id, "progress_check_requested", after_hours=hours)

    if (task.interim_due and now >= task.interim_due and task.status in ("open", "reopened")
            and not task.interim_control and not _has_accepted_risk(db, task)
            and not _sent(db, task, "interim_missing")):
        notify(db, task=task, person=first_with_role(db, "ciso"), kind="interim_missing",
               message=f"No compensating control or risk acceptance for task {task.id} on {asset.name}")
        raise_finding(db, "interim_control_missing", "High", f"Task {task.id}: {asset.name}",
                      f"Unpatched {profile(task)} task has neither a compensating control nor a risk acceptance",
                      assigned_to=first_with_role(db, "ciso"), is_test=task.is_test)
        audit.log(db, "system", "task", task.id, "escalated", to="ciso", reason="interim control missing")

    if is_overdue(task, now):
        _escalate_missed(db, task, asset, "remediation deadline missed")


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
        # a workaround can be recorded even when no interim deadline applies (e.g. Emergency, where the fix is due first)
        "interim_state": ("reopened" if task.interim_reopened else "recorded" if task.interim_control
                          else "not_required" if not task.interim_due else "pending"),
    }
