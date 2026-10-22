"""Read models and actions for the Acknowledgements and Alerts pages."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..clock import clock
from ..db import get_db
from .. import models as M
from ..services import assignment, intake, views
from ..services.assignment import OPEN_STATES, is_overdue
from .routes import actor, guarded, on_behalf, one

router = APIRouter(prefix="/api")


def _iso(d):
    return d.isoformat() if d else None


@router.get("/acknowledgements")
def acknowledgements(db=Depends(get_db)):
    """One row per sign-off owed: every system an advisory touches, and every department a clause involves."""
    now = clock.now()
    rows = []
    for o in db.scalars(select(M.Obligation).where(M.Obligation.is_test == False).order_by(M.Obligation.id.desc())):  # noqa: E712
        if o.advisory_id:
            for r in views.estate(db, o)["rows"]:
                a = db.get(M.Asset, r["asset_id"])
                t = db.get(M.Task, r["task_id"]) if r.get("task_id") else None
                owner = db.get(M.Person, t.owner_id) if t and t.owner_id else a.owner
                entered = None
                if t is None:
                    status, who, when = "not_required", None, None
                elif t.acknowledged_at:
                    status, who, when = "signed", db.get(M.Person, t.acknowledged_by_id).name, t.acknowledged_at
                    e = db.scalar(select(M.AuditEvent).where(M.AuditEvent.entity == "task", M.AuditEvent.entity_id == t.id,
                                                             M.AuditEvent.action == "entered_on_behalf")
                                  .order_by(M.AuditEvent.id.desc()))
                    if e and (e.detail or {}).get("did") == "acknowledge":
                        entered = db.scalar(select(M.Person.name).where(M.Person.directory_id == e.actor))
                else:
                    status, who, when = ("overdue" if now > t.ack_due else "pending"), None, None
                bu = a.business_unit
                rows.append({"key": f"t{r['asset_id']}-{o.id}", "obligation_id": o.id, "title": o.title, "kind": "Advisory",
                             "scope": a.name, "department": bu.name if bu else None, "owner": owner.name if owner else None,
                             "owner_active": bool(owner and owner.active), "label": r["state"], "impact": t.impact if t else None, "status": status,
                             "signed_by": who, "signed_at": _iso(when), "entered_by": entered, "due": _iso(t.ack_due) if t else None,
                             "task_id": t.id if t else None, "department_key": None})
        elif o.status in ("open", "closed") and o.departments:
            for d in o.departments:
                rows.append({"key": f"o{o.id}-{d['department']}", "obligation_id": o.id, "title": o.title,
                             "kind": "Policy clause" if o.kind == "policy" else "Regulatory clause", "scope": d["department"],
                             "department": d["department"], "owner": d.get("owner"), "owner_active": True,
                             "label": d["label"], "status": "signed" if d.get("signed_at") else "pending",
                             "signed_by": d.get("signed_by"), "signed_at": d.get("signed_at"), "entered_by": d.get("entered_by"), "due": None,
                             "task_id": None, "department_key": d["department"]})
    return rows


class Signoff(BaseModel):
    department: str


@router.post("/obligations/{oid}/signoff")
def signoff(oid: int, body: Signoff, db=Depends(get_db), me=Depends(actor)):
    ob = one(db, M.Obligation, oid)
    row = next((d for d in ob.departments or [] if d["department"] == body.department), None)
    owner = db.scalar(select(M.Person).where(M.Person.name == (row or {}).get("owner"))) if row else None
    if owner is None:
        raise HTTPException(400, f"{body.department} has no owner to sign off")
    on_behalf(db, me, owner, "obligation", oid, f"sign_off {body.department}")
    guarded(lambda: intake.signoff_department(db, ob, owner, body.department))(db)
    if me.id != owner.id:
        ob.departments = [{**d, "entered_by": me.name} if d["department"] == body.department else d for d in ob.departments]
        db.commit()
    return {"ok": True}


# ---------- alerts ----------
def _ladder(db, t: M.Task) -> dict:
    kinds = {n.kind for n in db.scalars(select(M.Notification).where(M.Notification.task_id == t.id))}
    if t.acknowledged_at is None:
        order = ["escalate_manager", "escalate_ciso", "deadline_missed"]
        names = ["owner's manager", "CISO", "risk owner and senior management"]
    else:
        order, names = ["deadline_missed"], ["risk owner and senior management"]
    level = next((names[i - 1] for i in range(len(order), 0, -1) if order[i - 1] in kinds), None)
    nxt = next((names[i] for i, k in enumerate(order) if k not in kinds), None)
    return {"escalated_to": level, "next": nxt}


@router.get("/alerts")
def alerts(db=Depends(get_db)):
    """Everything that is waiting on a sign-off or an update and has not been dealt with."""
    now = clock.now()
    items = []
    for t in db.scalars(select(M.Task).where(M.Task.is_test == False, M.Task.status.in_(OPEN_STATES))):  # noqa: E712
        a = db.get(M.Asset, t.asset_id)
        owner = db.get(M.Person, t.owner_id) if t.owner_id else None
        base = {"task_id": t.id, "ref": views.task_label(t), "impact": t.impact, "asset": a.name,
                "owner": owner.name if owner else None, "title": f"{'Investigate' if t.kind == 'investigation' else 'Remediate'} {t.component or ''} on {a.name}".replace("  ", " "),
                **_ladder(db, t)}
        if t.acknowledged_at is None and now > t.ack_due:
            items.append({**base, "type": "unsigned", "what": "Acknowledgement not signed",
                          "late_by_h": round((now - t.ack_due).total_seconds() / 3600, 1), "due": _iso(t.ack_due)})
        if is_overdue(t, now):
            items.append({**base, "type": "overdue", "what": "Remediation past its deadline",
                          "late_by_h": round((now - t.remediate_due).total_seconds() / 3600, 1), "due": _iso(t.remediate_due)})
        elif (t.impact == "High" and t.status in ("open", "reopened") and t.interim_due and now > t.interim_due and not t.interim_control):
            items.append({**base, "type": "interim", "what": "No interim control or risk acceptance",
                          "late_by_h": round((now - t.interim_due).total_seconds() / 3600, 1), "due": _iso(t.interim_due)})
    items.sort(key=lambda i: (i["impact"] != "High", -i["late_by_h"]))
    findings = [{"id": f.id, "kind": f.kind, "severity": f.severity, "subject": f.subject, "detail": f.detail,
                 "assigned_to": db.get(M.Person, f.assigned_to_id).name if f.assigned_to_id else None,
                 "created_at": _iso(f.created_at)}
                for f in db.scalars(select(M.Finding).where(M.Finding.status == "open", M.Finding.is_test == False)  # noqa: E712
                                    .order_by(M.Finding.id.desc()))]
    log = [{"ts": _iso(n.created_at), "kind": n.kind, "to": db.get(M.Person, n.person_id).name if n.person_id else None,
            "message": n.message, "delivered": n.delivered, "task_id": n.task_id}
           for n in db.scalars(select(M.Notification).where(M.Notification.kind.in_(
               ["escalate_manager", "escalate_ciso", "deadline_missed", "interim_missing", "reminder"]))
               .order_by(M.Notification.id.desc()).limit(40))]
    return {"now": _iso(now), "items": items, "findings": findings, "log": log}


@router.post("/tasks/{tid}/escalate")
def escalate(tid: int, db=Depends(get_db), me=Depends(actor)):
    t = one(db, M.Task, tid)
    return {"told": guarded(lambda: assignment.escalate_now(db, t, me))(db)}


@router.post("/tasks/{tid}/remind")
def remind(tid: int, db=Depends(get_db), me=Depends(actor)):
    t = one(db, M.Task, tid)
    return {"told": guarded(lambda: assignment.send_reminder(db, t, me))(db)}
