"""Read model for the Registry page: people, org structure, business units and systems, with the links between them
(owner -> manager -> BU head, open tasks, claims, findings)."""
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..clock import clock
from ..db import get_db
from .. import models as M
from ..config import TIMINGS
from ..services import common, views

router = APIRouter(prefix="/api/registry")

def _iso(d):
    return d.isoformat() if d else None


@router.get("")
def registry(db=Depends(get_db)):
    now = clock.now()
    people = {p.id: p for p in db.scalars(select(M.Person))}
    bus = list(db.scalars(select(M.BusinessUnit)))
    assets = list(db.scalars(select(M.Asset)))
    name = lambda i: people[i].name if i in people else None  # noqa: E731

    owns: dict[int, list[str]] = {}
    for a in assets:
        if a.owner_id:
            owns.setdefault(a.owner_id, []).append(a.name)

    out_people = [{
        "id": p.id, "directory_id": p.directory_id, "name": p.name, "title": p.title, "role": p.role,
        "manager_id": p.manager_id, "manager": name(p.manager_id),
        "business_unit": next((b.name for b in bus if b.id == p.business_unit_id), None),
        "active": p.active, "deliverable": p.deliverable, "owns": owns.get(p.id, []),
    } for p in people.values()]

    tasks_by_asset: dict[int, list] = {}
    for t in db.scalars(select(M.Task).where(M.Task.is_test.is_(False), M.Task.status.in_(
            ["open", "fixed_pending_review", "reopened"]))):
        tasks_by_asset.setdefault(t.asset_id, []).append(
            {"id": t.id, "ref": views.task_label(t), "kind": t.kind, "impact": t.impact, "status": t.status,
             "owner": name(t.owner_id)})
    claims_by_asset: dict[int, list] = {}
    for c in db.scalars(select(M.Claim).order_by(M.Claim.id)):
        if c.asset_id and c.claim_type in ("ownership", "monitoring", "applicability"):
            claims_by_asset.setdefault(c.asset_id, []).append(
                {"id": c.id, "type": c.claim_type, "value": c.value, "status": c.status, "statement": c.statement,
                 "expiry": _iso(c.expiry)})

    stale_days = TIMINGS["component_data_stale_days"]
    out_assets = []
    for a in assets:
        ok, why = common.owner_validity(db, a)
        owner = people.get(a.owner_id)
        bu = next((b for b in bus if b.id == a.business_unit_id), None)
        verified = a.inventory_verified_at
        out_assets.append({
            "id": a.id, "name": a.name, "internet_facing": a.internet_facing, "critical": a.critical,
            "customer_facing": a.customer_facing, "data_sensitivity": a.data_sensitivity,
            "business_unit": bu.name if bu else None,
            "owner": owner.name if owner else None, "owner_active": bool(owner and owner.active),
            "owner_valid": ok, "owner_issue": why or None, "legacy_contact": a.legacy_contact,
            "manager": name(owner.manager_id) if owner else None,
            "bu_head": name(bu.head_id) if bu else None,
            "ownership_attested_at": _iso(a.ownership_attested_at),
            "inventory": {"verified_at": _iso(verified), "method": a.inventory_method, "scope": a.inventory_scope,
                          "state": "none" if not verified else "stale" if now - verified > timedelta(days=stale_days) else "current"},
            "in_scanner_targets": a.in_scanner_targets,
            "components": [{"name": c.name, "version": c.version, "path": c.install_path} for c in a.components],
            "open_tasks": tasks_by_asset.get(a.id, []), "claims": claims_by_asset.get(a.id, []),
        })

    findings = [{"id": f.id, "kind": f.kind, "severity": f.severity, "subject": f.subject, "detail": f.detail,
                 "assigned_to": name(f.assigned_to_id), "created_at": _iso(f.created_at)}
                for f in db.scalars(select(M.Finding).where(M.Finding.status == "open", M.Finding.is_test.is_(False)))]
    discovered = [{"name": d.name, "source": d.source, "internet_facing": d.internet_facing,
                   "registered": any(a.name == d.name for a in assets)}
                  for d in db.scalars(select(M.DiscoveredAsset))]
    return {"now": _iso(now), "people": out_people,
            "business_units": [{"id": b.id, "name": b.name, "head": name(b.head_id)} for b in bus],
            "assets": out_assets, "findings": findings, "discovered": discovered}
