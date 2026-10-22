"""REST API. The caller's identity comes from the X-User header (a directory id),
standing in for SSO in the demonstrator."""
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, inspect

from ..clock import clock
from ..rules.versions import norm
from ..db import get_db
from .. import models as M
from ..services import (registry, intake, nvd, rag, assessment, assignment, closure, assurance, common, views)

router = APIRouter(prefix="/api")


def ser(o):
    if o is None:
        return None
    out = {}
    for c in inspect(o).mapper.column_attrs:
        v = getattr(o, c.key)
        out[c.key] = v.isoformat() if isinstance(v, datetime) else v
    return out


def sers(items):
    return [ser(i) for i in items]


def actor(db=Depends(get_db), x_user: str | None = Header(default=None)):
    if not x_user:
        raise HTTPException(401, "X-User header (directory id) required")
    p = common.person_by_directory_id(db, x_user)
    if not p:
        raise HTTPException(401, f"unknown user {x_user}")
    return p


def one(db, model, id_):
    o = db.get(model, id_)
    if not o:
        raise HTTPException(404, f"{model.__name__} {id_} not found")
    return o


def guarded(fn):
    """Domain errors become HTTP errors; successful calls commit."""
    def run(db, *a, **kw):
        try:
            r = fn(*a, **kw)
            db.commit()
            return r
        except PermissionError as e:
            db.rollback()
            raise HTTPException(403, str(e))
        except ValueError as e:
            db.rollback()
            raise HTTPException(400, str(e))
    return run


# ---------- demo / time ----------
@router.get("/people")
def people(db=Depends(get_db)):
    return sers(db.scalars(select(M.Person)))


@router.get("/clock")
def get_clock():
    return {"now": clock.now().isoformat()}


class Advance(BaseModel):
    hours: float = 0
    days: float = 0


@router.post("/clock/advance")
def advance(body: Advance, db=Depends(get_db)):
    clock.advance(hours=body.hours, days=body.days)
    clock.save()
    r = assurance.sweep(db)
    return {"now": clock.now().isoformat(), **r}


@router.post("/clock/reset")
def reset_clock():
    clock.unfreeze()
    clock.save()
    return {"now": clock.now().isoformat()}


@router.post("/sweep")
def sweep(db=Depends(get_db)):
    return assurance.sweep(db)


@router.post("/demo/reset")
def demo_reset(mode: str = "replay"):
    """replay: the full March 2017 storyline. baseline: the organisation on 8 Mar, before the advisory.
    Both are synthetic."""
    from ..db import Base, engine, SessionLocal
    from ..seed import replay
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        replay.load_baseline(s)
        if mode == "replay":
            out = replay.run_storyline(s)
        else:
            clock.set(datetime(2017, 3, 8, 9, 0))
            clock.save()
            out = {"now": clock.now().isoformat()}
    return {"mode": mode, **out}


# ---------- registry (C1) ----------
@router.get("/assets")
def assets(db=Depends(get_db)):
    out = []
    for a in db.scalars(select(M.Asset)):
        ok, why = common.owner_validity(db, a)
        d = ser(a)
        d.update(owner_name=a.owner.name if a.owner else None, owner_valid=ok, owner_issue=why or None,
                 business_unit=a.business_unit.name if a.business_unit else None,
                 components=[ser(c) for c in a.components])
        out.append(d)
    return out


class AssetPatch(BaseModel):
    owner_id: int | None = None
    technical_owner_id: int | None = None
    internet_facing: bool | None = None
    critical: bool | None = None
    in_scanner_targets: bool | None = None
    data_sensitivity: str | None = None


@router.patch("/assets/{asset_id}")
def patch_asset(asset_id: int, body: AssetPatch, db=Depends(get_db), me=Depends(actor)):
    a = one(db, M.Asset, asset_id)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(a, k, v)
    registry.audit.log(db, me.directory_id, "asset", a.id, "updated", **changes)
    db.commit()
    return ser(a)


class Sbom(BaseModel):
    document: dict
    method: str = "sbom"
    scope: list[str] | None = None


@router.post("/assets/{asset_id}/sbom")
def upload_sbom(asset_id: int, body: Sbom, db=Depends(get_db), me=Depends(actor)):
    a = one(db, M.Asset, asset_id)
    try:
        n = registry.import_sbom(db, a, body.document, method=body.method, scope=body.scope, actor=me.directory_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    assessment.reassess_all(db)
    db.commit()
    return {"components": n}


@router.post("/assets/{asset_id}/attest")
def attest(asset_id: int, db=Depends(get_db), me=Depends(actor)):
    return guarded(lambda: ser(registry.attest_ownership(db, one(db, M.Asset, asset_id), me)))(db)


class Jml(BaseModel):
    event: str  # leaver|mover|joiner


@router.post("/people/{person_id}/jml")
def jml(person_id: int, body: Jml, db=Depends(get_db), me=Depends(actor)):
    p = one(db, M.Person, person_id)
    registry.joiner_mover_leaver(db, p, body.event, actor=me.directory_id)
    db.commit()
    return ser(p)


@router.post("/registry/reconcile")
def reconcile(db=Depends(get_db)):
    f = registry.reconcile_discovery(db) + registry.data_quality_scan(db)
    db.commit()
    return sers(f)


@router.get("/findings")
def findings(include_tests: bool = False, db=Depends(get_db)):
    q = select(M.Finding).order_by(M.Finding.id.desc())
    return sers(db.scalars(q))


@router.post("/findings/{fid}/resolve")
def resolve_finding(fid: int, db=Depends(get_db), me=Depends(actor)):
    f = one(db, M.Finding, fid)
    f.status = "resolved"
    registry.audit.log(db, me.directory_id, "finding", f.id, "resolved")
    db.commit()
    return ser(f)


@router.get("/claims")
def claims(claim_type: str | None = None, status: str | None = None, db=Depends(get_db)):
    q = select(M.Claim).order_by(M.Claim.id.desc())
    if claim_type:
        q = q.where(M.Claim.claim_type == claim_type)
    if status:
        q = q.where(M.Claim.status == status)
    out = []
    for c in db.scalars(q):
        d = ser(c)
        d["asset"] = db.get(M.Asset, c.asset_id).name if c.asset_id else None
        d["dependents"] = sers(db.scalars(select(M.Dependency).where(M.Dependency.claim_id == c.id)))
        out.append(d)
    return out


class Monitoring(BaseModel):
    asset_id: int
    name: str
    owner_id: int
    cert_expiry: datetime


@router.post("/monitoring")
def add_monitoring(body: Monitoring, db=Depends(get_db), me=Depends(actor)):
    c = registry.register_monitoring_control(db, one(db, M.Asset, body.asset_id), body.name,
                                             one(db, M.Person, body.owner_id), body.cert_expiry, me.directory_id)
    db.commit()
    return ser(c)


@router.get("/broadcast-list")
def broadcast(db=Depends(get_db)):
    return [{"name": p.name, "directory_id": p.directory_id} for p in registry.broadcast_recipients(db)]


# ---------- intake (C2) ----------
@router.get("/sources")
def sources(db=Depends(get_db)):
    return sers(db.scalars(select(M.Source)))


class AdvisoryIn(BaseModel):
    cve: str
    product: str
    affected_versions: list[str]
    cvss: float | None = None
    kev: bool = False
    fixed_version: str | None = None
    source_name: str = "CISA/NVD"
    description: str | None = None


@router.get("/advisories/lookup")
def lookup_advisory(cve: str, db=Depends(get_db), me=Depends(actor)):
    """Pre-fill from NVD + CISA KEV. Suggests the registered component name that matches the CPE product."""
    try:
        res = nvd.lookup(cve)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ConnectionError as e:
        raise HTTPException(502, str(e))
    known = {c.name for c in db.scalars(select(M.Component))}
    for c in res["candidates"]:
        n = norm(c["product"])
        c["inventory_name"] = next((k for k in sorted(known) if n and n in norm(k)), None)
    return res


@router.post("/advisories")
def post_advisory(body: AdvisoryIn, db=Depends(get_db), me=Depends(actor)):
    ob = guarded(lambda: intake.ingest_advisory(db, **body.model_dump()))(db)
    return _obligation_detail(db, ob.id)


class SourceText(BaseModel):
    version: str
    text: str


@router.post("/sources/{sid}/versions")
def new_source_version(sid: int, body: SourceText, db=Depends(get_db)):
    r = intake.ingest_source_version(db, one(db, M.Source, sid), body.version, body.text)
    db.commit()
    return r


@router.get("/quarterly-summary")
def quarterly(db=Depends(get_db)):
    return intake.quarterly_summary(db)


@router.get("/obligations")
def obligations(status: str | None = None, db=Depends(get_db)):
    q = select(M.Obligation).where(M.Obligation.is_test == False).order_by(M.Obligation.id.desc())  # noqa: E712
    if status:
        q = q.where(M.Obligation.status == status)
    out = []
    for o in db.scalars(q):
        d = ser(o)
        ts = list(db.scalars(select(M.Task).where(M.Task.obligation_id == o.id)))
        d["task_counts"] = {"total": len(ts), "closed": sum(t.status in closure.TERMINAL for t in ts)}
        out.append(d)
    return out


def _obligation_detail(db, oid: int):
    o = one(db, M.Obligation, oid)
    d = ser(o)
    d["advisory"] = ser(db.get(M.Advisory, o.advisory_id)) if o.advisory_id else None
    d["claims"] = []
    for c in db.scalars(select(M.Claim).where(M.Claim.obligation_id == oid, M.Claim.claim_type == "applicability")
                        .order_by(M.Claim.id)):
        cd = ser(c)
        cd["asset"] = db.get(M.Asset, c.asset_id).name
        d["claims"].append(cd)
    d["tasks"] = [task_view(db, t) for t in db.scalars(select(M.Task).where(M.Task.obligation_id == oid))]
    ok, blockers = closure.try_close_obligation(db, o) if o.status != "closed" else (True, [])
    d["closable"], d["closure_blockers"] = ok, blockers
    d["guidance"] = sers(db.scalars(select(M.Guidance).where(M.Guidance.obligation_id == oid)))
    d["dispositions"] = closure.disposition_summary(db, o)
    if o.advisory_id:
        d["estate"] = views.estate(db, o)
    return d


@router.get("/obligations/{oid}")
def get_obligation(oid: int, db=Depends(get_db)):
    r = _obligation_detail(db, oid)
    db.commit()  # try_close_obligation may close an eligible obligation
    return r


class Decision(BaseModel):
    approve: bool
    applicability: str | None = None
    required_action: str | None = None


@router.post("/obligations/{oid}/decide")
def decide(oid: int, body: Decision, db=Depends(get_db), me=Depends(actor)):
    guarded(lambda: intake.decide_candidate(db, one(db, M.Obligation, oid), me, body.approve,
                                            body.applicability, body.required_action))(db)
    return ser(db.get(M.Obligation, oid))


class GuidanceIn(BaseModel):
    role: str = "system owner"


@router.post("/obligations/{oid}/guidance")
def guidance(oid: int, body: GuidanceIn, db=Depends(get_db)):
    g = rag.generate_guidance(db, one(db, M.Obligation, oid), body.role)
    db.commit()
    return ser(g)


class Override(BaseModel):
    asset_id: int
    new_label: str
    reason: str
    reviewer_id: int | None = None
    evidence_id: int | None = None


@router.post("/obligations/{oid}/override")
def override(oid: int, body: Override, db=Depends(get_db), me=Depends(actor)):
    reviewer = db.get(M.Person, body.reviewer_id) if body.reviewer_id else None
    c = guarded(lambda: assessment.override_applicability(
        db, one(db, M.Obligation, oid), one(db, M.Asset, body.asset_id), body.new_label, body.reason, me,
        reviewer, body.evidence_id))(db)
    return ser(c)


# ---------- tasks (C4/C5) ----------
def task_view(db, t: M.Task):
    d = ser(t)
    a = db.get(M.Asset, t.asset_id)
    d.update(asset=a.name, owner=db.get(M.Person, t.owner_id).name if t.owner_id else None,
             original_owner=db.get(M.Person, t.original_owner_id).name if t.original_owner_id else None,
             obligation=db.get(M.Obligation, t.obligation_id).title, countdown=assignment.countdowns(t),
             overdue=assignment.is_overdue(t))
    return d


@router.get("/tasks")
def tasks(status: str | None = None, owner: str | None = None, include_tests: bool = False, db=Depends(get_db)):
    q = select(M.Task).order_by(M.Task.id.desc())
    if not include_tests:
        q = q.where(M.Task.is_test == False)  # noqa: E712
    if status == "open":
        q = q.where(M.Task.status.in_(assignment.OPEN_STATES))
    elif status:
        q = q.where(M.Task.status == status)
    if owner:
        p = common.person_by_directory_id(db, owner)
        q = q.where(M.Task.owner_id == (p.id if p else -1))
    return [task_view(db, t) for t in db.scalars(q)]


@router.get("/tasks/{tid}")
def task_detail(tid: int, db=Depends(get_db)):
    return views.task_detail(db, one(db, M.Task, tid))


@router.post("/tasks/{tid}/acknowledge")
def ack(tid: int, db=Depends(get_db), me=Depends(actor)):
    guarded(lambda: assignment.acknowledge(db, one(db, M.Task, tid), me))(db)
    return task_view(db, db.get(M.Task, tid))


class Reason(BaseModel):
    reason: str


@router.post("/tasks/{tid}/reject")
def reject(tid: int, body: Reason, db=Depends(get_db), me=Depends(actor)):
    guarded(lambda: assignment.reject_task(db, one(db, M.Task, tid), me, body.reason))(db)
    return task_view(db, db.get(M.Task, tid))


class Transfer(BaseModel):
    new_owner_id: int


@router.post("/tasks/{tid}/transfer")
def transfer(tid: int, body: Transfer, db=Depends(get_db), me=Depends(actor)):
    guarded(lambda: assignment.transfer_task(db, one(db, M.Task, tid), one(db, M.Person, body.new_owner_id), me))(db)
    return task_view(db, db.get(M.Task, tid))


class Interim(BaseModel):
    description: str


@router.post("/tasks/{tid}/interim-control")
def interim(tid: int, body: Interim, db=Depends(get_db), me=Depends(actor)):
    guarded(lambda: assignment.record_interim_control(db, one(db, M.Task, tid), me, body.description))(db)
    return task_view(db, db.get(M.Task, tid))


@router.post("/tasks/{tid}/fixed")
def fixed(tid: int, db=Depends(get_db), me=Depends(actor)):
    guarded(lambda: closure.mark_fixed(db, one(db, M.Task, tid), me))(db)
    return task_view(db, db.get(M.Task, tid))


class EvidenceIn(BaseModel):
    purpose: str  # remediation|absence
    asset_name: str | None = None
    component: str | None = None
    scope: list[str] = []
    method: str | None = None
    observed_version: str | None = None
    timestamp: datetime | None = None


@router.post("/tasks/{tid}/evidence")
def evidence(tid: int, body: EvidenceIn, db=Depends(get_db), me=Depends(actor)):
    ev = guarded(lambda: closure.submit_evidence(db, one(db, M.Task, tid), me, **body.model_dump()))(db)
    return ser(ev)


@router.get("/evidence")
def evidence_list(status: str | None = None, db=Depends(get_db)):
    q = select(M.Evidence).order_by(M.Evidence.id.desc())
    if status:
        q = q.where(M.Evidence.status == status)
    out = []
    for e in db.scalars(q):
        d = ser(e)
        t = db.get(M.Task, e.task_id)
        d["asset_task"] = f"{db.get(M.Asset, t.asset_id).name}: {db.get(M.Obligation, t.obligation_id).title}"
        d["submitter"] = db.get(M.Person, e.submitter_id).name if e.submitter_id else None
        d["worked_by"] = db.get(M.Person, t.fixed_by_id).name if t.fixed_by_id else None
        out.append(d)
    return out


class Review(BaseModel):
    approve: bool
    comment: str = ""


@router.post("/evidence/{eid}/review")
def review(eid: int, body: Review, db=Depends(get_db), me=Depends(actor)):
    ev = guarded(lambda: closure.review_evidence(db, one(db, M.Evidence, eid), me, body.approve, body.comment))(db)
    return ser(ev)


class RiskIn(BaseModel):
    scenario: str
    controls: str
    residual_risk: str
    days: int = 30


@router.post("/tasks/{tid}/risk-acceptance")
def risk_request(tid: int, body: RiskIn, db=Depends(get_db), me=Depends(actor)):
    ra = guarded(lambda: assignment.request_risk_acceptance(db, one(db, M.Task, tid), me, **body.model_dump()))(db)
    return ser(ra)


@router.get("/risk-acceptances")
def risk_list(db=Depends(get_db)):
    return sers(db.scalars(select(M.RiskAcceptance).order_by(M.RiskAcceptance.id.desc())))


@router.post("/risk-acceptances/{rid}/approve")
def risk_approve(rid: int, db=Depends(get_db), me=Depends(actor)):
    guarded(lambda: assignment.approve_risk_acceptance(db, one(db, M.RiskAcceptance, rid), me))(db)
    return ser(db.get(M.RiskAcceptance, rid))


@router.get("/notifications")
def notifications(db=Depends(get_db)):
    return sers(db.scalars(select(M.Notification).order_by(M.Notification.id.desc()).limit(200)))


# ---------- assurance (C6) ----------
@router.get("/dashboard")
def dashboard(db=Depends(get_db)):
    return assurance.dashboard(db)


@router.get("/oversight")
def oversight(db=Depends(get_db)):
    return views.oversight(db)


@router.get("/board-summary")
def board_summary(db=Depends(get_db)):
    return assurance.oversight_summary(db)


@router.get("/timeline")
def timeline(entity: str | None = None, entity_id: int | None = None, db=Depends(get_db)):
    return sers(assurance.audit_timeline(db, entity, entity_id))


class CanaryIn(BaseModel):
    asset_id: int


@router.post("/canary/run")
def canary(body: CanaryIn, db=Depends(get_db)):
    run = assurance.run_canary(db, one(db, M.Asset, body.asset_id))
    db.commit()
    return ser(run)


@router.get("/canary")
def canary_list(db=Depends(get_db)):
    return views.canary_view(db)


@router.get("/claims/{cid}")
def claim_detail(cid: int, db=Depends(get_db)):
    return views.claim_detail(db, one(db, M.Claim, cid))


@router.get("/evidence/{eid}")
def evidence_detail(eid: int, db=Depends(get_db)):
    e = one(db, M.Evidence, eid)
    d = ser(e)
    t = db.get(M.Task, e.task_id)
    d.update(closure.evidence_checks(db, e))
    d["task_ref"] = views.task_label(t)
    d["submitter"] = db.get(M.Person, e.submitter_id).name if e.submitter_id else None
    d["submitted_at"] = e.created_at.isoformat()
    return d


class Decision2(BaseModel):
    action: str  # approve|reject|request
    comment: str = ""


@router.post("/evidence/{eid}/decision")
def evidence_decision(eid: int, body: Decision2, db=Depends(get_db), me=Depends(actor)):
    guarded(lambda: closure.decide_evidence(db, one(db, M.Evidence, eid), me, body.action, body.comment))(db)
    return evidence_detail(eid, db)
