"""Read models shaped for the UI screens. No state changes here."""
from datetime import timedelta

from sqlalchemy import select, or_

from ..clock import clock
from ..config import TIMINGS
from .. import models as M
from . import assurance, closure
from .assignment import OPEN_STATES, countdowns, is_overdue
from .assessment import _classify
from .common import bu_head, first_with_role


def hm(delta: timedelta) -> str:
    s = int(abs(delta).total_seconds())
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}d {h:02d}h" if d else f"{h}h {m:02d}m"


def _name(db, pid):
    p = db.get(M.Person, pid) if pid else None
    return p.name if p else None


def escalated_to(db, t: M.Task) -> str:
    kinds = {n.kind for n in db.scalars(select(M.Notification).where(M.Notification.task_id == t.id))}
    if "deadline_missed" in kinds:
        return "Risk owner, board report"
    if "escalate_ciso" in kinds:
        return "CISO"
    if "escalate_manager" in kinds:
        return "Owner's manager"
    return "—"


def task_label(t: M.Task) -> str:
    return f"TSK-{t.id:04d}"


def oversight(db) -> dict:
    now = clock.now()
    d = assurance.dashboard(db)
    live = [t for t in db.scalars(select(M.Task).where(M.Task.is_test == False))  # noqa: E712
            if t.status in OPEN_STATES]
    overdue = sorted([t for t in live if is_overdue(t, now)], key=lambda t: t.remediate_due)
    rows = []
    for t in overdue:
        a = db.get(M.Asset, t.asset_id)
        o = db.get(M.Person, t.owner_id) if t.owner_id else None
        rows.append({"id": t.id, "ref": task_label(t), "title": (db.get(M.Obligation, t.obligation_id).title if db.get(M.Obligation, t.obligation_id).kind == "policy"
                               else f"{'Investigate' if t.kind == 'investigation' else 'Remediate'} "
                                    f"{t.component or ''} on {a.name}"), "impact": t.impact, "owner": o.name if o else None,
                     "owner_note": ("rerouted · BU head" if t.owner_id != t.original_owner_id and o and o.role == "bu_head"
                                    else None), "late_by": hm(now - t.remediate_due),
                     "escalated_to": escalated_to(db, t)})
    lapsed = []
    cutoff = now - timedelta(days=45)
    for c in db.scalars(select(M.Claim).where(M.Claim.status == "expired")):
        dep = db.scalars(select(M.Dependency).where(M.Dependency.claim_id == c.id)).all()
        lapsed.append({"ts": c.expiry.isoformat() if c.expiry else None, "claim_id": c.id, "statement": c.statement,
                       "detail": c.reason or f"Evidence expired · {len(dep)} dependent decision(s) reopened"})
    for e in db.scalars(select(M.AuditEvent).where(M.AuditEvent.action == "absence_claim_rejected")):
        lapsed.append({"ts": e.ts.isoformat(), "claim_id": None, "statement": "“Does Not Apply” claim",
                       "detail": "Evidence rejected — " + "; ".join(e.detail.get("reasons", []))})
    lapsed = [x for x in lapsed if x["ts"] and x["ts"] >= cutoff.isoformat()]
    lapsed.sort(key=lambda x: x["ts"], reverse=True)

    obs = [o for o in db.scalars(select(M.Obligation).where(M.Obligation.is_test == False,  # noqa: E712
                                                          M.Obligation.kind == "advisory"))
           if o.status != "closed" and o.impact == "High"]
    banner = None
    if obs:
        o = obs[0]
        cd = db.scalars(select(M.Claim).where(M.Claim.obligation_id == o.id, M.Claim.claim_type == "applicability",
                                              M.Claim.status == "active", M.Claim.value == "cannot_determine")).all()
        od = [t for t in overdue if t.obligation_id == o.id]
        banner = {"obligation_id": o.id, "title": o.title,
                  "text": f"obligation cannot close: {len(cd)} assets remain Cannot Determine, "
                          f"{len(od)} remediation is past its deadline."}
    runs = db.scalars(select(M.CanaryRun).order_by(M.CanaryRun.id.desc())).all()
    failed = next((r for r in runs if r.status == "failed"), None)
    ras = db.scalars(select(M.RiskAcceptance).where(M.RiskAcceptance.status == "approved")).all()
    soon = [r for r in ras if r.expiry and r.expiry - now <= timedelta(days=7)]
    confirmations = []
    for a in db.scalars(select(M.Asset)):
        state = assurance.confirmation_state(db, a)
        if state in ("awaiting", "overdue", "unscheduled"):
            due = assurance.confirmation_due(db, a)
            confirmations.append({"asset": a.name, "asset_id": a.id, "state": state,
                                  "due": due.isoformat() if due else None,
                                  "owner": _name(db, a.owner_id)})
    overdue_confirmations = [a for a in confirmations if a["state"] == "overdue"]
    pending_confirmations = [a for a in confirmations if a["state"] in ("awaiting", "unscheduled")]
    failures = []
    for n in db.scalars(select(M.Notification).where(
            M.Notification.delivered.is_(False),
            or_(M.Notification.task_id.is_(None),
                ~M.Notification.task_id.in_(select(M.Task.id).where(M.Task.is_test.is_(True)))))
            .order_by(M.Notification.id.desc())):
        failures.append({"id": n.id, "ts": n.created_at.isoformat(), "kind": n.kind,
                         "recipient": _name(db, n.person_id), "task_id": n.task_id, "message": n.message})
    mailing_changes = []
    for e in db.scalars(select(M.AuditEvent).where(M.AuditEvent.action.in_(
            ("mailing_list_added", "mailing_list_updated", "mailing_list_removed"))).order_by(
                M.AuditEvent.id.desc()).limit(20)):
        asset = db.get(M.Asset, e.entity_id) if e.entity_id else None
        mailing_changes.append({"id": e.id, "ts": e.ts.isoformat(), "asset_id": e.entity_id,
                                "asset": asset.name if asset else None,
                                "actor": e.actor, "action": e.action, "detail": e.detail})
    uncertain = list(db.scalars(select(M.Claim).where(
        M.Claim.claim_type == "applicability", M.Claim.status == "active",
        M.Claim.value == "cannot_determine").order_by(M.Claim.id.desc())))
    expired = list(db.scalars(select(M.Claim).where(
        M.Claim.status == "expired").order_by(M.Claim.id.desc())))
    completed_runs = [r for r in runs if r.status != "running"]
    issues = lambda label, detail, href: {"label": label, "detail": detail, "href": href}  # noqa: E731
    drilldowns = {
        "overdue_tasks": [issues(t["title"], f"{t['owner'] or 'No owner'} · {t['late_by']} late",
                                 f"/remediations/{t['id']}") for t in rows],
        "cannot_determine": [issues(db.get(M.Asset, c.asset_id).name if c.asset_id else c.statement,
                                     c.statement, f"/claims/{c.id}") for c in uncertain],
        "accepted_risks": [issues(db.get(M.Asset, db.get(M.Task, r.task_id).asset_id).name,
                                  f"Risk acceptance RA-{r.id} · expires {r.expiry:%d %b %Y}" if r.expiry else f"RA-{r.id}",
                                  f"/remediations/{r.task_id}") for r in ras],
        "expired_evidence": [issues(c.statement, f"{c.claim_type.replace('_', ' ')} · expired {c.expiry:%d %b %Y}"
                                     if c.expiry else c.claim_type, f"/claims/{c.id}") for c in expired],
        "failed_deliveries": [issues(f"{n['kind'].replace('_', ' ')} to {n['recipient'] or 'no recipient'}",
                                      n["message"], f"/oversight?tab=notifications&notification={n['id']}")
                              for n in failures],
        "pending_confirmations": [issues(a["asset"], f"{a['owner'] or 'No owner'} · " +
                                         (f"Confirm by {a['due'][:10]}" if a["due"] else "Confirmation date missing"),
                                         f"/registry?tab=Systems&asset={a['asset_id']}") for a in pending_confirmations],
        "overdue_confirmations": [issues(a["asset"], f"{a['owner'] or 'No owner'} · Overdue since {a['due'][:10]}",
                                         f"/registry?tab=Systems&asset={a['asset_id']}") for a in overdue_confirmations],
        "lapsed_monitoring": [issues(c.statement, "Monitoring evidence expired", f"/claims/{c.id}")
                              for c in expired if c.claim_type == "monitoring"],
        "canary": [issues(db.get(M.Asset, r.asset_id).name, f"CNY-{r.id:04d} · {r.status}",
                          f"/alerts?tab=selftests&run={r.id}") for r in completed_runs],
    }
    return {
        "now": now.isoformat(), "banner": banner, "kpis": d,
        "high_overdue": sum(r["impact"] == "High" for r in rows),
        "risk_expiring_soon": len(soon),
        "risk_expiring_days": min(((r.expiry - now).days for r in soon), default=None),
        "overdue": rows, "lapsed": lapsed[:6],
        "confirmations": confirmations, "pending_confirmations": pending_confirmations,
        "overdue_confirmations": overdue_confirmations, "failed_deliveries": failures,
        "mailing_list_changes": mailing_changes,
        "drilldowns": drilldowns,
        "canary_failure": ({"id": failed.id, "ref": f"CNY-{failed.id:04d}",
                            "asset": db.get(M.Asset, failed.asset_id).name, "failures": failed.failures}
                           if failed else None),
    }


def task_timeline(db, t: M.Task) -> list[dict]:
    ev_ids = [e.id for e in db.scalars(select(M.Evidence).where(M.Evidence.task_id == t.id))]
    q = select(M.AuditEvent).where(((M.AuditEvent.entity == "task") & (M.AuditEvent.entity_id == t.id)) |
                                   ((M.AuditEvent.entity == "evidence") & (M.AuditEvent.entity_id.in_(ev_ids or [-1]))))
    out = []
    for e in db.scalars(q.order_by(M.AuditEvent.id)):
        d, a = e.detail or {}, e.action
        title = detail = None
        tone = "normal"
        if a == "created":
            title, detail = "Task created", f"{t.impact} {t.kind} task · acknowledgement due {t.ack_due:%d %b %H:%M}"
        elif a == "fallback_routed":
            head = bu_head(db, db.get(M.Asset, t.asset_id))
            title = f"Rerouted to {head.name if head else 'fallback'}, business-unit head"
            detail = f"Owner record invalid: {d.get('reason')}. Original deadline kept. Ownership-correction finding raised."
        elif a == "rerouted":
            title, detail = "Rerouted", f"{d.get('reason')}. Deadline kept: {t.remediate_due:%d %b %H:%M}."
        elif a == "acknowledged":
            title = f"Acknowledged by {_name(db, t.acknowledged_by_id) or e.actor}"
            detail = f"after {hm(e.ts - t.created_at)}"
        elif a == "rejected_by_recipient":
            title, detail = "Rejected by recipient — not my system", d.get("reason")
        elif a == "transferred":
            title, detail = "Ownership transferred", "Approved transfer; deadlines not reset."
        elif a == "interim_control_recorded":
            title, detail = "Interim compensating control approved", d.get("control")
        elif a == "interim_control_reopened":
            title, tone, detail = "Interim control reopened", "lapse", d.get("because")
        elif a == "escalated":
            to, why = d.get("to"), d.get("reason")
            if why == "remediation deadline missed":
                title, tone = "Deadline missed — escalated", "bad"
                detail = "To the accountable risk owner and senior management. Added to the next board risk report."
            else:
                title, detail = f"Escalated to {to}", why
        elif a == "marked_fixed":
            title, detail = "Marked fixed by owner", "Awaiting evidence and independent review."
        elif a == "submitted" and d.get("purpose") == "absence" and d.get("status") == "rejected":
            continue  # shown once, as the rejected claim, by absence_claim_rejected
        elif a == "submitted":
            title = "Evidence submitted" + (" — rejected" if d.get("status") == "rejected" else "")
            detail = "; ".join(d.get("reasons") or []) or "Passed automated checks; awaiting independent review."
            tone = "bad" if d.get("status") == "rejected" else "normal"
        elif a == "absence_claim_rejected":
            title, tone, detail = "“Does Not Apply” evidence rejected", "normal", "; ".join(d.get("reasons", []))
        elif a in ("accepted", "rejected", "rejection_confirmed", "review_blocked"):
            title = {"accepted": "Evidence accepted", "rejected": "Evidence rejected by reviewer",
                     "rejection_confirmed": "Rejection confirmed", "review_blocked": "Review blocked"}[a]
            detail = d.get("comment") or "; ".join(d.get("reasons", []))
        elif a == "reopened":
            title, tone, detail = "Reopened", "lapse", d.get("because")
        elif a == "closed_superseded":
            title, detail = "Closed — applicability changed", f"Now {d.get('new_label')}"
        if title:
            out.append({"ts": e.ts.isoformat(), "title": title, "detail": detail, "tone": tone})
    return out


def task_detail(db, t: M.Task) -> dict:
    a = db.get(M.Asset, t.asset_id)
    ob = db.get(M.Obligation, t.obligation_id)
    renewal = assurance.renewal_source(db, t)
    owner = db.get(M.Person, t.owner_id) if t.owner_id else None
    mgr = db.get(M.Person, owner.manager_id) if owner and owner.manager_id else None
    risk = first_with_role(db, "risk_owner")
    reviewer = first_with_role(db, "reviewer")
    cd = countdowns(t)
    now = clock.now()
    rem = t.remediate_due - now
    title = (f"Renew {t.component} monitoring on {a.name}" if renewal else
             f"Establish whether {a.name} runs an affected {t.component} version" if t.kind == "investigation"
             else f"Remediate {t.component} on {a.name}")
    return {
        "id": t.id, "ref": task_label(t), "obligation_id": ob.id, "obligation": ob.title, "asset": a.name,
        "kind": t.kind, "impact": t.impact, "status": t.status, "title": title, "component": t.component,
        "overdue": is_overdue(t, now),
        "summary": (f"The evidence for “{renewal.statement}” expired. Renew the certificate or configuration, "
                    f"then verify {t.component} on {a.name} and submit new evidence for independent review." if renewal else
                    f"{a.name} has no verified component data for {t.component}, so PoliBridge cannot rule it in or out. "
                    "Supply component-scoped evidence, or remediate, or request time-limited risk acceptance."
                    if t.kind == "investigation" else
                    f"Patch {t.component} on {a.name}, then submit evidence for independent review."),
        "ack": {"due_h": TIMINGS[t.impact].get("ack_hours"), "acknowledged": bool(t.acknowledged_at),
                "at": t.acknowledged_at.isoformat() if t.acknowledged_at else None,
                "note": f"after {hm(t.acknowledged_at - t.created_at)}" if t.acknowledged_at else
                        f"due {t.ack_due:%d %b %H:%M}", "remaining_h": cd["ack_remaining_h"]},
        "interim": {"state": cd["interim_state"], "due_h": TIMINGS[t.impact].get("interim_hours"),
                    "note": (t.interim_control if cd["interim_state"] == "recorded" else
                             "relied on a monitoring claim that has lapsed" if cd["interim_state"] == "reopened" else
                             f"due {t.interim_due:%d %b %H:%M}" if t.interim_due else "Not required")},
        "disposition": {"remaining": ("−" if rem.total_seconds() < 0 else "") + hm(rem), "due": f"{t.remediate_due:%d %b %H:%M}",
                        "days": TIMINGS[t.impact]["remediate_hours"] // 24, "overdue": is_overdue(t, now)},
        "people": {"owner": {"name": owner.name, "title": owner.title or owner.role} if owner else None,
                   "manager": mgr.name if mgr else None, "risk_owner": risk.name if risk else None,
                   "reviewer": reviewer.name if reviewer else None},
        "close_requirement": (f"Mark the control renewed, then submit a {renewal.verification_method.replace('_', ' ')} "
                              f"with scope {', '.join(renewal.evidence_scope or [])} and a timestamp after the renewal. "
                              "An independent reviewer must approve it; approved, time-limited risk acceptance is another disposition."
                              if renewal else f"A direct version check or authenticated scan whose recorded scope covers {t.component} on "
                              f"{a.name} — or approved, time-limited risk acceptance."),
        "renewal": ({"source_claim_id": renewal.id, "scope": renewal.evidence_scope or [],
                     "method": renewal.verification_method, "statement": renewal.statement,
                     "marked_fixed": bool(t.fixed_at)} if renewal else None),
        "timeline": task_timeline(db, t),
        "evidence": [{"id": e.id, "status": e.status, "purpose": e.purpose, "reasons": e.reasons}
                     for e in db.scalars(select(M.Evidence).where(M.Evidence.task_id == t.id))],
        "signoffs": closure.signoff_view(db, t),
        "guidance": [{"id": g.id, "status": g.status, "text": g.text, "citations": g.citations, "reason": g.reason,
                      "engine": g.engine, "recipient": _name(db, g.recipient_id), "consult": _name(db, g.consult_id),
                      "consulted_at": g.consulted_at.isoformat() if g.consulted_at else None}
                     for g in db.scalars(select(M.Guidance).where(M.Guidance.obligation_id == ob.id,
                                                                  M.Guidance.asset_id == a.id).order_by(M.Guidance.id.desc()))],
        "can_reject": t.status in OPEN_STATES,
    }


def estate(db, ob: M.Obligation) -> dict:
    adv = db.get(M.Advisory, ob.advisory_id)
    rows = []
    tasks = {(t.asset_id): t for t in db.scalars(select(M.Task).where(M.Task.obligation_id == ob.id))
             .all()[::-1]}  # latest task per asset wins
    for c in db.scalars(select(M.Claim).where(M.Claim.obligation_id == ob.id, M.Claim.claim_type == "applicability",
                                              M.Claim.status == "active")):
        a = db.get(M.Asset, c.asset_id)
        comp = next((x for x in a.components if x.name.lower().replace(" ", "") in adv.product.lower().replace(" ", "")
                     or adv.product.lower().replace(" ", "") in x.name.lower().replace(" ", "")), None)
        stale = (a.inventory_verified_at is not None and
                 clock.now() - a.inventory_verified_at > timedelta(days=TIMINGS["component_data_stale_days"]))
        t = tasks.get(a.id)
        rej = db.scalars(select(M.Evidence).where(M.Evidence.task_id == t.id, M.Evidence.status == "rejected")
                         .order_by(M.Evidence.id.desc())).first() if t else None
        basis = c.reason or ""
        if rej and c.value == "cannot_determine":
            basis = f"“Does Not Apply” claim rejected {rej.created_at:%d %b} — " + "; ".join(rej.reasons)
        if t and t.status == "closed" and t.disposition == "remediated":
            t_status = f"Remediated · verified {t.closed_at:%d %b}"
        elif t and t.status == "closed":
            t_status = "Closed"
        else:
            t_status = {"open": ("Investigation" if t and t.kind == "investigation" else "Remediation") +
                                (" · overdue" if t and is_overdue(t) else
                                 " · acknowledged" if t and t.acknowledged_at else ""),
                        "fixed_pending_review": "Fixed · awaiting independent review",
                        "risk_accepted": "Risk accepted", "reopened": "Reopened"}.get(t.status, t.status) if t else None
        rows.append({
            "asset_id": a.id, "asset": a.name,
            "tags": ", ".join(x for x in ["Internet-facing" if a.internet_facing else "Internal",
                                          "personal data" if a.data_sensitivity in ("confidential", "restricted") else ""] if x),
            "component_record": f"{comp.name} {comp.version}" if comp else "None on record",
            "provenance": (f"{(a.inventory_method or '').upper() if a.inventory_method in ('sbom', 'sca') else (a.inventory_method or '').replace('_', ' ')}"
                           f" · {a.inventory_verified_at:%d %b %Y}") if a.inventory_verified_at else None,
            "stale": stale, "state": c.value, "basis": basis, "claim_id": c.id,
            "task_id": t.id if t else None, "task_ref": task_label(t) if t else None, "task_status": t_status,
            "task_overdue": bool(t and is_overdue(t)),
        })
    counts = {"applies": sum(r["state"] == "applies" for r in rows),
              "cannot_determine": sum(r["state"] == "cannot_determine" for r in rows),
              "does_not_apply": sum(r["state"] == "does_not_apply" for r in rows)}
    approved = sum(1 for r in rows if r["state"] == "does_not_apply" or
                   (r["task_id"] and tasks[r["asset_id"]].status in closure.TERMINAL))
    review = sum(1 for r in rows if r["task_id"] and tasks[r["asset_id"]].status == "fixed_pending_review")
    # why-high-impact: classify against the most exposed asset with a task
    why = []
    for r in rows:
        t = tasks.get(r["asset_id"])
        if t:
            info = _classify(adv, db.get(M.Asset, r["asset_id"]), regulatory=ob.kind == "regulatory")
            if info["impact"] == "High":
                why = info["triggers"]
                break
    return {"rows": rows, "counts": counts, "gate": {"total": len(rows), "approved": approved, "in_review": review,
                                                    "cannot_determine": counts["cannot_determine"]}, "why_high": why,
            "timings": TIMINGS.get(ob.impact or "High", {})}


def claim_detail(db, c: M.Claim) -> dict:
    a = db.get(M.Asset, c.asset_id) if c.asset_id else None
    deps = []
    for dep in db.scalars(select(M.Dependency).where(M.Dependency.claim_id == c.id)):
        if dep.dependent_type in ("task", "interim_control"):
            t = db.get(M.Task, dep.dependent_id)
            owner = _name(db, t.owner_id) if t else None
            kind = "Interim control on" if dep.dependent_type == "interim_control" else "Task"
            deps.append({"kind": dep.dependent_type, "label": f"{kind} {task_label(t)}", "owner": owner,
                         "reopened": bool(t and (t.status == "reopened" or t.interim_reopened))})
        elif dep.dependent_type == "risk_acceptance":
            r = db.get(M.RiskAcceptance, dep.dependent_id)
            deps.append({"kind": "risk_acceptance", "label": f"Risk acceptance RA-{r.id:04d} · {r.scenario[:40]}",
                         "owner": _name(db, r.approver_id), "reopened": r.status == "expired"})
        elif dep.dependent_type == "board_item":
            b = db.get(M.BoardItem, dep.dependent_id)
            deps.append({"kind": "board_item", "label": f"Board report item {b.ref} · {b.title}",
                         "owner": _name(db, b.owner_id), "reopened": b.status == "reopened"})
        elif dep.dependent_type == "obligation":
            o = db.get(M.Obligation, dep.dependent_id)
            if o:
                deps.append({"kind": "obligation", "label": f"Obligation OBL-{o.id:04d} · {o.title}", "owner": None,
                             "reopened": c.status == "expired" and o.status in ("open", "closed")})
    cert = next((s[5:] for s in (c.evidence_scope or []) if str(s).startswith("cert:")), None)
    hist = [{"ts": e.ts.isoformat(), "text": f"{e.action.replace('_', ' ').capitalize()}"
             + (f" · {e.detail.get('statement')}" if e.action == "created" and e.detail.get("statement") else "")}
            for e in db.scalars(select(M.AuditEvent).where(M.AuditEvent.entity == "claim",
                                                           M.AuditEvent.entity_id == c.id).order_by(M.AuditEvent.id))]
    return {
        "id": c.id, "ref": f"{'MC' if c.claim_type == 'monitoring' else 'SRC' if c.claim_type == 'source_currency' else 'CLM'}-{c.id:03d}", "type": c.claim_type,
        "source": db.get(M.Source, c.source_id).name if c.source_id else None,
        "statement": c.statement, "value": c.value, "status": c.status, "asset": a.name if a else None,
        "component": c.component, "evidence_scope": c.evidence_scope, "method": c.verification_method,
        "evidence_ts": c.evidence_ts.isoformat() if c.evidence_ts else None, "expiry": c.expiry.isoformat() if c.expiry else None,
        "submitter": _name(db, c.submitter_id), "reviewer": _name(db, c.reviewer_id), "owner": _name(db, c.owner_id),
        "depends_on": ({"label": f"TLS certificate · {cert}", "expiry": c.expiry.isoformat() if c.expiry else None,
                        "expired": c.status == "expired"} if cert else None),
        "dependents": deps, "history": hist, "reason": c.reason,
        "life": {"start": (c.evidence_ts or c.created_at).isoformat(), "end": c.expiry.isoformat() if c.expiry else None,
                 "now": clock.now().isoformat()},
    }


def canary_view(db) -> dict:
    runs = db.scalars(select(M.CanaryRun).order_by(M.CanaryRun.id.desc())).all()
    done = [r for r in runs if r.status != "running"]
    out = []
    for r in runs:
        a = db.get(M.Asset, r.asset_id)
        t = db.get(M.Task, r.task_id) if r.task_id else None
        owner = db.get(M.Person, a.owner_id) if a.owner_id else None
        named = owner is not None and owner.active and not r.failures
        feed = r.feed or "the advisory feed"
        early = r.stage in ("feed", "match")
        steps = [
            {"n": 1, "title": f"Test advisory sent through {feed}", "result": "fail" if r.stage == "feed" else "pass",
             "detail": r.failures[0] if r.stage == "feed" else f"PB-CANARY-{r.id:04d}, labelled TEST, polled like a real advisory."},
            {"n": 2, "title": f"Matched to {a.name}", "result": "not_run" if r.stage == "feed" else "fail" if r.stage == "match" else "pass",
             "detail": "Blocked by step 1." if r.stage == "feed" else r.failures[0] if r.stage == "match"
             else "The real matching rule labelled the test component Applies."},
        ]
        if early:
            steps.append({"n": 3, "title": "Reached a named owner", "result": "not_run", "detail": "Blocked by an earlier step."})
            f = db.scalar(select(M.Finding).where(M.Finding.kind == "canary_failure", M.Finding.subject == f"Canary failed on {a.name}"))
            out.append({"id": r.id, "ref": f"CNY-{r.id:04d}", "asset": a.name, "ts": r.created_at.isoformat(),
                        "status": r.status, "failures": r.failures, "steps": steps, "stage": r.stage,
                        "named_owner": owner.name if owner and owner.active else "No owner",
                        "finding": ({"id": f.id, "ref": f"DQF-{f.id:04d}", "status": f.status,
                                     "assignee": _name(db, f.assigned_to_id)} if f else None)})
            continue
        route_fail = next((f for f in r.failures if "valid named owner" in f or "not delivered" in f), None)
        steps.append({"n": 3, "title": "Reached a named owner", "result": "fail" if route_fail else "pass",
                      "detail": (route_fail + (f" Legacy contact: {a.legacy_contact}." if a.legacy_contact else "")
                                 if route_fail else f"Routed to {owner.name}, a directory identity.")})
        if route_fail:
            steps.append({"n": 4, "title": "Acknowledged in time", "result": "not_run", "detail": f"Blocked by step 3."})
            head = bu_head(db, a)
            steps.append({"n": 5, "title": "Fallback routing", "result": "pass" if head else "fail",
                          "detail": f"Reached {head.name}, business-unit head." if head else "No business-unit head."})
        else:
            other = [f for f in r.failures]
            steps.append({"n": 4, "title": "Acknowledged in time / escalated on schedule",
                          "result": "fail" if other else "pass" if r.status == "passed" else "pending",
                          "detail": "; ".join(other) or ("Escalated on schedule." if r.status == "passed" else "Waiting for the acknowledgement window.")})
        f = db.scalar(select(M.Finding).where(M.Finding.kind == "canary_failure", M.Finding.subject == f"Canary failed on {a.name}"))
        out.append({"id": r.id, "ref": f"CNY-{r.id:04d}", "asset": a.name, "ts": r.created_at.isoformat(),
                    "status": r.status, "failures": r.failures, "steps": steps, "stage": r.stage or ("route" if r.failures else None),
                    "named_owner": (owner.name if named else "Distribution list" if a.legacy_contact else "No owner"),
                    "finding": ({"id": f.id, "ref": f"DQF-{f.id:04d}", "status": f.status,
                                 "assignee": _name(db, f.assigned_to_id)} if f else None)})
    return {"passed": sum(r.status == "passed" for r in done), "total": len(done), "runs": out}
