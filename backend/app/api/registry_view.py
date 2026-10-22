"""Read model for the Registry page: people, org structure, business units and systems, with the links between them
(owner -> manager -> BU head, open tasks, claims, findings)."""
from datetime import timedelta

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..clock import clock
from ..db import get_db
from .. import models as M
from ..config import TIMINGS
from ..services import assessment, assignment, audit, common, registry as reg, views
from .routes import actor, require_editor as _require_editor

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
            "id": a.id, "name": a.name, "business_unit_id": a.business_unit_id, "owner_id": a.owner_id,
            "app_roots": a.app_roots or [], "internet_facing": a.internet_facing, "critical": a.critical,
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
            "business_units": [{"id": b.id, "name": b.name, "head": name(b.head_id), "head_id": b.head_id} for b in bus],
            "assets": out_assets, "findings": findings, "discovered": discovered}


# ---------- editing: people and system ownership ----------
ROLES = ["owner", "manager", "bu_head", "ciso", "risk_owner", "senior_mgmt", "executive", "compliance", "legal", "cco",
         "reviewer", "internal_audit"]
class PersonIn(BaseModel):
    name: str
    directory_id: str | None = None
    title: str | None = None
    role: str = "owner"
    manager_id: int | None = None
    business_unit_id: int | None = None
    deliverable: bool = True


class PersonPatch(BaseModel):
    name: str | None = None
    title: str | None = None
    role: str | None = None
    manager_id: int | None = None
    business_unit_id: int | None = None
    deliverable: bool | None = None
    clear_manager: bool = False


def _check_links(db, role, manager_id, bu_id, self_id=None):
    if role is not None and role not in ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(ROLES)}")
    if manager_id is not None:
        m = db.get(M.Person, manager_id)
        if not m or not m.active:
            raise HTTPException(400, "the manager must be an active person")
        # a person cannot report to themselves or to someone below them
        seen, cur = set(), m
        while cur and cur.id not in seen:
            if cur.id == self_id:
                raise HTTPException(400, "that would create a reporting loop")
            seen.add(cur.id)
            cur = db.get(M.Person, cur.manager_id) if cur.manager_id else None
    if bu_id is not None and not db.get(M.BusinessUnit, bu_id):
        raise HTTPException(400, "unknown business unit")


@router.post("/people")
def add_person(body: PersonIn, db=Depends(get_db), me=Depends(actor)):
    _require_editor(me)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    did = (body.directory_id or re.sub(r"[^a-z0-9]", "", (name.split()[0][:1] + name.split()[-1]).lower())).strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,24}", did):
        raise HTTPException(400, "directory id must be 3-24 letters, numbers, dots, dashes or underscores")
    if common.person_by_directory_id(db, did):
        raise HTTPException(400, f"directory id '{did}' is already in use")
    _check_links(db, body.role, body.manager_id, body.business_unit_id)
    p = M.Person(directory_id=did, name=name, title=(body.title or "").strip() or None, role=body.role,
                 manager_id=body.manager_id, business_unit_id=body.business_unit_id, deliverable=body.deliverable)
    db.add(p)
    db.flush()
    audit.log(db, me.directory_id, "person", p.id, "jml_joiner", directory_id=did)
    db.commit()
    return {"id": p.id, "directory_id": p.directory_id}


@router.patch("/people/{pid}")
def edit_person(pid: int, body: PersonPatch, db=Depends(get_db), me=Depends(actor)):
    _require_editor(me)
    p = db.get(M.Person, pid)
    if not p:
        raise HTTPException(404, "person not found")
    if not p.active:
        raise HTTPException(400, f"{p.name} has left; leavers cannot be edited")
    ch = body.model_dump(exclude_none=True, exclude={"clear_manager"})
    if body.clear_manager:
        ch["manager_id"] = None
    if "name" in ch and not ch["name"].strip():
        raise HTTPException(400, "name is required")
    _check_links(db, ch.get("role"), ch.get("manager_id"), ch.get("business_unit_id"), self_id=p.id)
    moved = ("business_unit_id" in ch and ch["business_unit_id"] != p.business_unit_id) or \
            ("manager_id" in ch and ch["manager_id"] != p.manager_id)
    for k, v in ch.items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    audit.log(db, me.directory_id, "person", p.id, "updated", **{k: str(v) for k, v in ch.items()})
    if moved:  # a mover invalidates the ownership claims of anything they own (B3)
        reg.joiner_mover_leaver(db, p, "mover", actor=me.directory_id)
    db.commit()
    return {"id": p.id, "moved": moved}


class OwnerIn(BaseModel):
    owner_id: int


@router.post("/assets/{aid}/owner")
def reassign_owner(aid: int, body: OwnerIn, db=Depends(get_db), me=Depends(actor)):
    """Name a new owner. The old attestation is void until the new owner attests; open tasks move with no deadline reset."""
    if me.role not in ("bu_head", "manager", "ciso", "risk_owner"):
        raise HTTPException(403, "reassignment must be approved by a manager, business-unit head, CISO or risk owner")
    a = db.get(M.Asset, aid)
    new = db.get(M.Person, body.owner_id)
    if not a or not new:
        raise HTTPException(404, "system or person not found")
    if not new.active:
        raise HTTPException(400, f"{new.name} has left and cannot own a system")
    if a.owner_id == new.id:
        raise HTTPException(400, f"{new.name} already owns {a.name}")
    old = a.owner_id
    a.owner_id = new.id
    a.ownership_attested_at = None
    for c in db.scalars(select(M.Claim).where(M.Claim.claim_type == "ownership", M.Claim.asset_id == a.id,
                                                M.Claim.status == "active")):
        c.status, c.reason = "superseded", "owner reassigned"
    for t in db.scalars(select(M.Task).where(M.Task.asset_id == a.id, M.Task.status.in_(("open", "reopened", "fixed_pending_review")))):
        assignment.transfer_task(db, t, new, me)
    for f in db.scalars(select(M.Finding).where(M.Finding.kind == "ownership_reassignment", M.Finding.status == "open",
                                                  M.Finding.subject == f"Reassign owner of {a.name}")):
        f.status = "resolved"
    audit.log(db, me.directory_id, "asset", a.id, "owner_reassigned", from_owner=old, to=new.directory_id)
    db.commit()
    return {"owner": new.name, "needs_attestation": True}


# ---------- editing: departments and systems ----------
class DeptIn(BaseModel):
    name: str
    head_id: int | None = None


class DeptPatch(BaseModel):
    name: str | None = None
    head_id: int | None = None


def _check_head(db, head_id):
    if head_id is not None:
        h = db.get(M.Person, head_id)
        if not h or not h.active:
            raise HTTPException(400, "the department head must be an active person")


@router.post("/business-units")
def add_department(body: DeptIn, db=Depends(get_db), me=Depends(actor)):
    _require_editor(me)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    if db.scalar(select(M.BusinessUnit).where(M.BusinessUnit.name == name)):
        raise HTTPException(400, f"a department called '{name}' already exists")
    _check_head(db, body.head_id)
    b = M.BusinessUnit(name=name, head_id=body.head_id)
    db.add(b)
    db.flush()
    audit.log(db, me.directory_id, "business_unit", b.id, "created", name=name)
    db.commit()
    return {"id": b.id}


@router.patch("/business-units/{bid}")
def edit_department(bid: int, body: DeptPatch, db=Depends(get_db), me=Depends(actor)):
    _require_editor(me)
    b = db.get(M.BusinessUnit, bid)
    if not b:
        raise HTTPException(404, "department not found")
    old_name, old_head = b.name, b.head_id
    if body.name is not None:
        new = body.name.strip()
        if not new:
            raise HTTPException(400, "name is required")
        if new != b.name and db.scalar(select(M.BusinessUnit).where(M.BusinessUnit.name == new)):
            raise HTTPException(400, f"a department called '{new}' already exists")
        b.name = new
    if body.head_id is not None:
        _check_head(db, body.head_id)
        b.head_id = body.head_id
    # clause decisions refer to a department by name and show its owner: keep unsigned ones current
    head = db.get(M.Person, b.head_id) if b.head_id else None
    for ob in db.scalars(select(M.Obligation).where(M.Obligation.departments.is_not(None))):
        rows = [dict(d) for d in ob.departments or []]
        changed = False
        for d in rows:
            if d["department"] == old_name:
                d["department"] = b.name
                if not d.get("signed_at") and head:
                    d["owner"] = head.name
                changed = True
        if changed:
            ob.departments = rows
    audit.log(db, me.directory_id, "business_unit", b.id, "updated", name=b.name, head_id=b.head_id,
              was_name=old_name, was_head_id=old_head)
    db.commit()
    return {"id": b.id}


class SystemIn(BaseModel):
    name: str
    business_unit_id: int
    owner_id: int | None = None
    internet_facing: bool = False
    critical: bool = False
    customer_facing: bool = False
    data_sensitivity: str = "internal"
    app_roots: list[str] = []
    in_scanner_targets: bool = True


class SystemPatch(BaseModel):
    name: str | None = None
    business_unit_id: int | None = None
    internet_facing: bool | None = None
    critical: bool | None = None
    customer_facing: bool | None = None
    data_sensitivity: str | None = None
    app_roots: list[str] | None = None
    in_scanner_targets: bool | None = None


SENSITIVITY = ("internal", "confidential", "restricted")


def _roots(roots):
    out = []
    for r in roots or []:
        r = r.strip().rstrip("/*")
        if r:
            if not r.startswith("/"):
                raise HTTPException(400, f"application folder '{r}' must be an absolute path such as /opt/app")
            out.append(r)
    return out


@router.post("/assets")
def add_system(body: SystemIn, db=Depends(get_db), me=Depends(actor)):
    _require_editor(me)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    if db.scalar(select(M.Asset).where(M.Asset.name == name)):
        raise HTTPException(400, f"a system called '{name}' already exists")
    if not db.get(M.BusinessUnit, body.business_unit_id):
        raise HTTPException(400, "choose a department")
    if body.data_sensitivity not in SENSITIVITY:
        raise HTTPException(400, f"data sensitivity must be one of {', '.join(SENSITIVITY)}")
    owner = db.get(M.Person, body.owner_id) if body.owner_id else None
    if body.owner_id and (not owner or not owner.active):
        raise HTTPException(400, "the owner must be an active person")
    a = M.Asset(name=name, business_unit_id=body.business_unit_id, owner_id=owner.id if owner else None,
                internet_facing=body.internet_facing, critical=body.critical, customer_facing=body.customer_facing,
                data_sensitivity=body.data_sensitivity, app_roots=_roots(body.app_roots),
                in_scanner_targets=body.in_scanner_targets)
    db.add(a)
    db.flush()
    audit.log(db, me.directory_id, "asset", a.id, "created", name=name, owner=owner.directory_id if owner else None)
    # open advisories now cover the new system; with no component data it correctly starts as Cannot Determine (A3)
    assessment.reassess_all(db)
    db.commit()
    return {"id": a.id, "needs_attestation": bool(owner)}


@router.patch("/assets/{aid}/details")
def edit_system(aid: int, body: SystemPatch, db=Depends(get_db), me=Depends(actor)):
    _require_editor(me)
    a = db.get(M.Asset, aid)
    if not a:
        raise HTTPException(404, "system not found")
    ch = body.model_dump(exclude_none=True)
    if "name" in ch:
        ch["name"] = ch["name"].strip()
        if not ch["name"]:
            raise HTTPException(400, "name is required")
        if ch["name"] != a.name and db.scalar(select(M.Asset).where(M.Asset.name == ch["name"])):
            raise HTTPException(400, f"a system called '{ch['name']}' already exists")
    if "business_unit_id" in ch and not db.get(M.BusinessUnit, ch["business_unit_id"]):
        raise HTTPException(400, "unknown department")
    if "data_sensitivity" in ch and ch["data_sensitivity"] not in SENSITIVITY:
        raise HTTPException(400, f"data sensitivity must be one of {', '.join(SENSITIVITY)}")
    if "app_roots" in ch:
        ch["app_roots"] = _roots(ch["app_roots"])
    old_name = a.name
    exposure_changed = any(k in ch and ch[k] != getattr(a, k) for k in ("internet_facing", "critical", "customer_facing"))
    for k, v in ch.items():
        setattr(a, k, v)
    if a.name != old_name:  # evidence and discovery records match systems by name
        for e in db.scalars(select(M.Evidence).where(M.Evidence.asset_name == old_name)):
            e.asset_name = a.name
        for d in db.scalars(select(M.DiscoveredAsset).where(M.DiscoveredAsset.name == old_name)):
            d.name = a.name
    audit.log(db, me.directory_id, "asset", a.id, "updated", **{k: str(v) for k, v in ch.items()})
    if exposure_changed:  # impact depends on exposure, so open advisories are re-matched
        assessment.reassess_all(db)
    db.commit()
    return {"id": a.id}
