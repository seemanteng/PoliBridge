"""Approval routing (approvals.yaml). Decides who approves guidance before it reaches an owner, enforces that the
approver is never the recipient, escalates late clause sign-offs, and draws each person's approval map."""
from datetime import datetime

from sqlalchemy import select

from ..clock import clock
from ..config import APPROVALS, TIMINGS
from ..models import Person, Obligation, Guidance, BusinessUnit, Asset
from . import audit
from .common import first_with_role, people_with_role, notify, raise_finding


def holder(db, role: str | None) -> Person | None:
    return first_with_role(db, role) if role else None


def clause_topic(text: str) -> str:
    low = (text or "").lower()
    for key, cfg in APPROVALS["clause_topics"].items():
        if any(k in low for k in cfg["keywords"]):
            return key
    return APPROVALS["default_topic"]


def route_clause(db, ob: Obligation, recipient: Person | None) -> tuple[str, Person | None, Person | None]:
    """(topic, approver, consulted). The approver may never be the recipient (segregation of duties)."""
    topic = clause_topic(ob.quotation or ob.title)
    cfg = APPROVALS["clause_topics"][topic]
    approver = holder(db, cfg["approver"])
    if approver is None or (recipient and approver.id == recipient.id):
        approver = holder(db, APPROVALS["segregation_fallback"])
    consult = holder(db, cfg.get("consult"))
    if consult and recipient and consult.id == recipient.id:
        consult = None
    return topic, approver, consult


def withheld_to(db, ob: Obligation) -> Person | None:
    if ob.kind == "advisory":
        return holder(db, APPROVALS["advisory"]["withheld_to"])
    w = APPROVALS["withheld_to"]
    return holder(db, w.get(clause_topic(ob.quotation or ob.title), w["default"]))


def approve_guidance(db, g: Guidance, person: Person):
    if g.status != "pending_approval":
        raise ValueError(f"guidance is {g.status.replace('_', ' ')}, not waiting for approval")
    if g.recipient_id and person.id == g.recipient_id:
        raise PermissionError("the recipient of guidance cannot approve it")
    if g.approver_id and person.id != g.approver_id:
        approver = db.get(Person, g.approver_id)
        raise PermissionError(f"approvals.yaml routes this guidance to {approver.name if approver else 'another approver'}")
    g.status, g.approver_id, g.approved_at = "released", person.id, clock.now()
    audit.log(db, person.directory_id, "guidance", g.id, "approved", obligation=g.obligation_id)
    _release(db, g)


def _release(db, g: Guidance):
    ob = db.get(Obligation, g.obligation_id)
    recipient = db.get(Person, g.recipient_id) if g.recipient_id else None
    notify(db, person=recipient, kind="guidance_released",
           message=f"Guidance for {g.department or 'your system'} on {ob.title} is ready.")


def reject_guidance(db, g: Guidance, person: Person, reason: str):
    if g.status != "pending_approval":
        raise ValueError(f"guidance is {g.status.replace('_', ' ')}, not waiting for approval")
    if g.approver_id and person.id != g.approver_id:
        raise PermissionError("only the routed approver can reject this guidance")
    if not (reason or "").strip():
        raise ValueError("record why the guidance is rejected")
    g.status, g.reason = "rejected", f"Rejected by {person.name}: {reason.strip()}"
    audit.log(db, person.directory_id, "guidance", g.id, "rejected", reason=reason)


def consult_guidance(db, g: Guidance, person: Person, note: str = ""):
    """The parallel review: Information Security confirms the advice is technically right. Not a gate."""
    if g.consult_id is None:
        raise ValueError("this guidance has no parallel review")
    if person.id != g.consult_id:
        raise PermissionError("only the routed reviewer can record this check")
    if g.consulted_at:
        raise ValueError("already checked")
    g.consulted_at = clock.now()
    audit.log(db, person.directory_id, "guidance", g.id, "consulted", note=note or None)


def check_clause_signoffs(db) -> list[str]:
    """Department sign-offs not done by their due date are escalated once (CCO, or the CEO if the CCO is late)."""
    now, out = clock.now(), []
    cfg = APPROVALS["signoff"]
    for ob in db.scalars(select(Obligation).where(Obligation.status == "open", Obligation.departments.is_not(None))):
        rows, changed = [dict(d) for d in ob.departments or []], False
        for d in rows:
            if d.get("signed_at") or d.get("escalated_at") or not d.get("due"):
                continue
            if datetime.fromisoformat(d["due"]) > now:
                continue
            cco = holder(db, cfg["escalate_to"])
            target = holder(db, cfg["escalate_to_if_cco"]) if cco and cco.name == d.get("owner") else cco
            notify(db, person=target, kind="signoff_overdue",
                   message=f"{d['department']} has not signed off {ob.title} (due {d['due'][:10]}).")
            raise_finding(db, "clause_signoff_overdue", "Medium", f"{ob.title}: {d['department']} sign-off overdue",
                          f"Owner {d.get('owner') or 'none'}; escalated to {target.name if target else 'nobody'}.",
                          assigned_to=target)
            d["escalated_at"], d["escalated_to"], changed = now.isoformat(), target.name if target else None, True
            out.append(f"{ob.title} · {d['department']}")
        if changed:
            ob.departments = rows
            audit.log(db, "system", "obligation", ob.id, "signoff_escalated")
    return out


# ---------- approval map ----------
def _p(p: Person | None, fallback: str = "Nobody holds this role") -> dict:
    if p is None:
        return {"name": fallback, "title": None, "department": None, "missing": True}
    bu = p.business_unit_id
    return {"name": p.name, "title": p.title or p.role, "id": p.id, "department": None if bu is None else bu, "missing": False}


def _step(person, kind: str, what: str, rule: str, note: str | None = None) -> dict:
    return {"who": person, "type": kind, "what": what, "rule": rule, "note": note}


def approval_map(db, person: Person) -> dict:
    """For one person: who must approve each thing they do, who reviews alongside, and who is told if it is late.
    Built from the directory (manager, department head) and approvals.yaml, so it follows people as they move."""
    bus = {b.id: b for b in db.scalars(select(BusinessUnit))}
    dept = lambda x: bus[x].name if x in bus else None  # noqa: E731

    def P(p, fallback="Nobody holds this role"):
        d = _p(p, fallback)
        d["department"] = dept(p.business_unit_id) if p else None
        return d

    me = P(person)
    mgr = db.get(Person, person.manager_id) if person.manager_id else None
    mgr = mgr if mgr and mgr.active else None
    bu = bus.get(person.business_unit_id)
    head = db.get(Person, bu.head_id) if bu and bu.head_id else None
    ciso, risk, senior = holder(db, "ciso"), holder(db, "risk_owner"), holder(db, "senior_mgmt")
    compliance, legal, cco = holder(db, "compliance"), holder(db, "legal"), holder(db, "cco")
    reviewers = [p for p in people_with_role(db, "reviewer") if p.id != person.id]
    rev = {"name": " or ".join(p.name for p in reviewers) or "No independent reviewer", "title": "Independent reviewer",
           "department": dept(reviewers[0].business_unit_id) if reviewers else None, "missing": not reviewers}
    first_up = mgr or (head if head and head.id != person.id else None) or ciso
    hi = TIMINGS["High"]
    esc = hi["ack_escalations"]
    owns = [a.name for a in db.scalars(select(Asset).where(Asset.owner_id == person.id))]
    sections = []
    # every role can end up owning a task (fallback routing), so the system-owner view applies to everyone
    sections += [
        {"key": "guidance", "label": "Get fix guidance", "steps": [
            _step(P(None, "PoliBridge"), "sys", "Drafts fix advice from the advisory, the policy and the system's own component record", "RAG"),
            _step(P(None, "PoliBridge"), "sys", "Checks every quote is real; holds back weak or conflicting drafts", "A4"),
            _step(me, "owner", "Receives the advice straight away, without waiting for approval", "C1"),
            _step(P(holder(db, APPROVALS["advisory"]["consult"])), "parallel", "Checks the advice is technically right, alongside", "C1"),
            _step(P(holder(db, APPROVALS["advisory"]["withheld_to"])), "approve",
                  "If the draft was held back: writes or approves the guidance instead", "A4")]},
        {"key": "ack", "label": "Acknowledge a task", "steps": [
            _step(me, "owner", f"Signs the acknowledgement within {hi['ack_hours']} hours (High impact)", "D1"),
            _step(P(first_up), "late", f"Told if not signed after {esc[0]['after_hours']} hours", "D1"),
            _step(P(ciso), "late", f"Told if still not signed after {esc[1]['after_hours']} hours", "D2"),
            _step(P(risk), "late", f"Told with {senior.name if senior else 'senior management'} if the "
                                   f"{hi['remediate_hours'] // 24}-day fix deadline is missed; the item joins the board report", "D3")]},
        {"key": "fix", "label": "Close with fix evidence", "steps": [
            _step(me, "owner", "Submits evidence that examined the affected component", "E1–E4"),
            _step(P(None, "PoliBridge"), "sys", "Checks the evidence covers the right system, folder and time", "E1, E2, E4"),
            _step(rev, "approve", "Approves the evidence; cannot be whoever submitted it or did the fix", "E3"),
            _step(P(ciso), "approve", "Also signs off when a critical system is affected", "C2"),
            _step(P(compliance), "approve", "Also signs off when sensitive data is affected (with the CISO)", "C3"),
            _step(P(legal), "approve", "Also signs off when a legal or reporting duty is triggered", "C4")]},
        {"key": "dna", "label": "Say “does not apply”", "steps": [
            _step(me, "owner", "Submits evidence that the component is absent, covering the whole application folder", "A5"),
            _step(P(None, "PoliBridge"), "sys", "Rejects scans that never reached the component", "A5, E1"),
            _step(rev, "approve", "Approves before the label changes", "A5")]},
        {"key": "risk", "label": "Ask for more time", "steps": [
            _step(me, "owner", f"Records the risk, the stop-gap controls and an end date ({TIMINGS['risk_acceptance_max_days']} days at most)", "D4"),
            _step(P(risk if not risk or risk.id != person.id else senior), "approve",
                  "Approves; cannot be the owner or the person asking", "D4"),
            _step(P(senior), "approve", "Back-up approver, then the CISO; High impact needs stop-gap controls", "D4"),
            _step(P(None, "PoliBridge"), "sys", "Reopens the task and tells the risk owner when the end date passes", "D5")]},
        {"key": "hand", "label": "Pass the task on", "steps": [
            _step(me, "owner", "Rejects the task as not theirs, with a reason", "B4"),
            _step(P(head if head and head.id != person.id else ciso), "approve",
                  "Takes the task at once; the deadline keeps running", "B4"),
            _step(P(mgr or head), "approve", "Approves a transfer to a new named owner", "B4"),
            _step(P(None, "PoliBridge"), "sys", "Raises a finding to fix the ownership record", "B4")]},
    ]
    heads = [b for b in bus.values() if b.head_id == person.id]
    for b in heads:
        for key, cfg in APPROVALS["clause_topics"].items():
            fake = Obligation(kind="regulatory", title=cfg["label"], quotation=cfg["keywords"][0])
            _, approver, consult = route_clause(db, fake, person)
            swapped = holder(db, cfg["approver"]) and holder(db, cfg["approver"]).id == person.id
            cco_late = cco and cco.id == person.id
            late = holder(db, APPROVALS["signoff"]["escalate_to_if_cco"]) if cco_late else cco
            steps = [
                _step(P(None, "PoliBridge"), "sys", "Spots the changed clause and suggests the departments involved", "§6.4"),
                _step(P(compliance), "approve", f"Approves the clause as an obligation (or {legal.name if legal else 'Legal'}) and labels each department", "§6.4"),
                _step(P(None, "PoliBridge"), "sys", f"Drafts guidance written for {b.name}, quoting the clause", "RAG"),
                _step(P(approver), "approve", "Approves the guidance before it is released"
                      + (" (the usual approver is the recipient, so the CCO steps in)" if swapped else ""), "A4")]
            if consult:
                steps.append(_step(P(consult), "parallel", "Checks the security advice is technically right", "C1"))
            steps += [_step(me, "owner", f"Receives the guidance and signs off the label for {b.name}", "§6.2"),
                      _step(P(late), "late", f"Told if the sign-off is not done within {APPROVALS['signoff']['working_days']} working days", "approvals.yaml")]
            sections.append({"key": f"clause-{b.id}-{key}", "label": f"{cfg['label']} for {b.name}", "steps": steps})
    approves = []
    for key, cfg in APPROVALS["clause_topics"].items():
        if holder(db, cfg["approver"]) and holder(db, cfg["approver"]).id == person.id:
            approves.append(f"{cfg['label']} guidance (unless it is addressed to {person.name})")
        if holder(db, cfg.get("consult")) and holder(db, cfg.get("consult")).id == person.id:
            approves.append(f"{cfg['label']} guidance: technical check alongside")
    if person.role == "cco":
        approves += ["New sources for the approved-source registry",
                     "Any guidance whose usual approver is also its recipient"]
    if holder(db, APPROVALS["advisory"]["consult"]) and holder(db, APPROVALS["advisory"]["consult"]).id == person.id:
        approves.append("Advisory fix guidance: technical check alongside the owner")
    roles = {"ciso": ["Critical-system fix evidence (C2)", "Sensitive-data fix evidence, with Compliance (C3)"],
             "compliance": ["Sensitive-data and regulatory fix evidence (C3, C4)", "Regulatory clauses as obligations"],
             "legal": ["Fix evidence where a legal or reporting duty is triggered (C4)", "Regulatory clauses as obligations"],
             "risk_owner": ["Risk acceptances (D4)"], "senior_mgmt": ["Risk acceptances, as back-up (D4)"],
             "reviewer": ["Fix and absence evidence, independently (E3, A5)"],
             "bu_head": ["Task transfers in the department (B4)"], "manager": ["Task transfers for direct reports (B4)"]}
    approves += roles.get(person.role, [])
    return {"person": {**me, "role": person.role, "owns": owns,
                       "manager": mgr.name if mgr else None, "head": head.name if head else None},
            "sections": sections, "approves": approves}


def rules_view() -> dict:
    return {"advisory": APPROVALS["advisory"],
            "topics": [{"key": k, "label": v["label"], "keywords": v["keywords"], "approver": v["approver"],
                        "consult": v.get("consult")} for k, v in APPROVALS["clause_topics"].items()],
            "default_topic": APPROVALS["default_topic"], "segregation_fallback": APPROVALS["segregation_fallback"],
            "withheld_to": APPROVALS["withheld_to"], "signoff": APPROVALS["signoff"]}
