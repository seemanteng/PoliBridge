"""Source intake API: approved-source registry, source versions and change detection, advisory feeds, guidance
approval, approval map and the quarterly change summary."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from ..config import APPROVALS
from ..db import get_db
from .. import models as M
from ..services import approvals, closure, feeds, intake, rag, sources as S
from .routes import actor, approver_for, guarded, on_behalf, one, ser

router = APIRouter(prefix="/api")


def _role_holder(db, role: str):
    return approver_for(db, (role,))


# ---------- registry ----------
@router.get("/sources")
def list_sources(db=Depends(get_db)):
    return [S.source_view(db, s) for s in db.scalars(select(M.Source).order_by(M.Source.id))]


class SourceIn(BaseModel):
    name: str
    kind: str
    authority: str | None = None
    jurisdiction: str | None = None
    url: str | None = None
    intake: str = "manual"
    owner_id: int | None = None
    review_days: int | None = None
    document: str | None = None  # "docs/<file name>": the source's current text, ingested once it is approved
    version: str | None = None


@router.post("/sources")
def propose_source(body: SourceIn, db=Depends(get_db), me=Depends(actor)):
    owner = db.get(M.Person, body.owner_id) if body.owner_id else S.default_owner(db, body.kind)
    file_name = _file_text(body.document)[1] if body.document else None  # unreadable files are refused now, not at approval
    s = guarded(lambda: S.propose(db, name=body.name, kind=body.kind, authority=body.authority,
                                  jurisdiction=body.jurisdiction, url=body.url, intake=body.intake, owner=owner,
                                  review_days=body.review_days, proposer=me, file_name=file_name,
                                  version=body.version))(db)
    return S.source_view(db, s)


class SourcePatch(BaseModel):
    owner_id: int | None = None
    review_days: int | None = None
    url: str | None = None
    authority: str | None = None
    jurisdiction: str | None = None
    simulate_outage: bool | None = None


@router.patch("/sources/{sid}")
def patch_source(sid: int, body: SourcePatch, db=Depends(get_db), me=Depends(actor)):
    s = one(db, M.Source, sid)
    changes = body.model_dump(exclude_none=True)
    if "review_days" in changes and not 1 <= changes["review_days"] <= 730:
        raise HTTPException(400, "review cycle must be between 1 and 730 days")
    if "owner_id" in changes and not (db.get(M.Person, changes["owner_id"]) and db.get(M.Person, changes["owner_id"]).active):
        raise HTTPException(400, "the owner must be an active person")
    if "simulate_outage" in changes and s.intake != "feed":
        raise HTTPException(400, "only feeds can be switched into a simulated outage")
    for k, v in changes.items():
        setattr(s, k, v)
    from ..services import audit
    audit.log(db, me.directory_id, "source", s.id, "updated", **changes)
    db.commit()
    return S.source_view(db, s)


@router.post("/sources/{sid}/approve")
def approve_source(sid: int, db=Depends(get_db), me=Depends(actor)):
    s = one(db, M.Source, sid)
    cco = on_behalf(db, me, _role_holder(db, "cco"), "source", sid, "approve")
    text = _file_text(f"docs/{s.pending_file}")[0] if s.pending_file else None
    guarded(lambda: S.approve(db, s, cco))(db)
    if text is not None:  # the suggested file becomes version one; each clause is a candidate for Compliance/Legal
        proposer = db.scalar(select(M.Person).where(M.Person.name == s.proposed_by)) or cco
        r = guarded(lambda: intake.ingest_source_version(db, s, s.pending_version or "1", text, file_name=s.pending_file,
                                                         retrieved_from=s.url, uploaded_by=proposer))(db)
        s.pending_file = s.pending_version = None
        db.commit()
        return {**S.source_view(db, s), "ingested": {"version": r["version"], "candidates": len(r["candidates"])}}
    if s.intake == "manual":
        S.record_check(db, s, cco, how="checked_no_change", note="Approved into the registry")
        db.commit()
    return S.source_view(db, s)


class Reason(BaseModel):
    reason: str


@router.post("/sources/{sid}/suspend")
def suspend_source(sid: int, body: Reason, db=Depends(get_db), me=Depends(actor)):
    s = one(db, M.Source, sid)
    guarded(lambda: S.suspend(db, s, me, body.reason))(db)
    return S.source_view(db, s)


class CheckIn(BaseModel):
    note: str | None = None


@router.post("/sources/{sid}/check")
def check_source(sid: int, body: CheckIn, db=Depends(get_db), me=Depends(actor)):
    """The source owner confirms the register still reflects the current version: renews its currency claim."""
    s = one(db, M.Source, sid)
    if s.intake == "feed":
        raise HTTPException(400, "feeds are checked by polling; use Check feeds now")
    owner = db.get(M.Person, s.owner_id) if s.owner_id else me
    owner = on_behalf(db, me, owner, "source", sid, "check")
    guarded(lambda: (S.require_approved(s), S.record_check(db, s, owner, how="checked_no_change", note=body.note)))(db)
    return S.source_view(db, s)


# ---------- versions ----------
def _version_view(v: M.SourceVersion, full=False) -> dict:
    d = {"id": v.id, "version": v.version, "ingested_at": v.ingested_at.isoformat(), "sha256": v.sha256,
         "file_name": v.file_name, "retrieved_from": v.retrieved_from, "effective_date": v.effective_date,
         "uploaded_by": v.uploaded_by, "summary": {k: x for k, x in (v.summary or {}).items() if k != "changes"},
         "baseline": bool((v.summary or {}).get("baseline")) or v.summary is None}
    if full:
        d["text"] = v.text
        d["changes"] = (v.summary or {}).get("changes", [])
    return d


@router.get("/sources/{sid}/versions")
def source_versions(sid: int, db=Depends(get_db)):
    s = one(db, M.Source, sid)
    vs = list(db.scalars(select(M.SourceVersion).where(M.SourceVersion.source_id == sid).order_by(M.SourceVersion.id.desc())))
    return {"source": S.source_view(db, s), "versions": [_version_view(v) for v in vs]}


@router.get("/source-versions/{vid}")
def source_version(vid: int, db=Depends(get_db)):
    v = one(db, M.SourceVersion, vid)
    d = _version_view(v, full=True)
    for c in d["changes"]:
        ob = db.get(M.Obligation, c["obligation_id"]) if c.get("obligation_id") else None
        c["obligation_status"] = ob.status if ob else None
    d["source"] = db.get(M.Source, v.source_id).name
    return d


class VersionIn(BaseModel):
    version: str = ""
    text: str | None = None
    document: str | None = None  # "docs/<file name>" from the Documents page
    retrieved_from: str | None = None
    effective_date: str | None = None


def _file_text(document: str) -> tuple[str, str]:
    from .documents import _find, extract_text
    folder, _, name = document.partition("/")
    f = _find(folder, name)
    if not f:
        raise HTTPException(404, f"document '{name}' not found")
    text = extract_text(f).strip()
    if not text:
        raise HTTPException(422, "no text could be extracted (is this a scanned PDF?)")
    return text, f.name


def _text_of(body: VersionIn) -> tuple[str, str | None]:
    if body.document:
        return _file_text(body.document)
    if not (body.text or "").strip():
        raise HTTPException(400, "give the text or pick an uploaded document")
    return body.text, None


@router.get("/sources/{sid}/compare")
def compare_versions(sid: int, to: int | None = None, from_: int | None = Query(None, alias="from"), db=Depends(get_db)):
    """Two versions of a source text side by side: each changed clause with its reference and exact wording."""
    s = one(db, M.Source, sid)
    vs = list(db.scalars(select(M.SourceVersion).where(M.SourceVersion.source_id == sid).order_by(M.SourceVersion.id)))
    out = {"source": S.source_view(db, s), "versions": [_version_view(v) for v in reversed(vs)]}
    if not vs:
        return {**out, "from": None, "to": None, "changes": [], "unchanged": [], "clauses": []}
    new = next((v for v in vs if v.id == to), vs[-1])
    older = [v for v in vs if v.id < new.id]
    old = next((v for v in older if v.id == from_), older[-1] if older else None)
    if from_ is not None and (old is None or old.id != from_):
        raise HTTPException(400, "compare against an earlier version of the same source")
    clauses, _, _ = intake.parse_clauses(new.text)
    if old is None:  # only one version: show the clauses on record
        return {**out, "from": None, "to": _version_view(new), "changes": [], "unchanged": [],
                "clauses": [{"ref": r, "text": t} for r, t in clauses.items()]}
    d = intake.diff_clauses(old.text, new.text)
    consecutive = old.id == older[-1].id
    stored = {(c["kind"], c["ref"], c["old_ref"]): c for c in (new.summary or {}).get("changes", [])} if consecutive else {}
    for c in d["changes"]:
        hit = stored.get((c["kind"], c["ref"], c["old_ref"]))
        ob = db.get(M.Obligation, hit["obligation_id"]) if hit and hit.get("obligation_id") else (
            db.scalar(select(M.Obligation).where(M.Obligation.source_version_id == new.id, M.Obligation.source_ref == c["ref"]))
            if c["kind"] not in ("removed", "renumbered") else None)
        c["obligation_id"], c["obligation_status"] = (ob.id, ob.status) if ob else (None, None)
        c["supersedes"] = hit.get("supersedes") if hit else None
    changed_refs = {c["ref"] for c in d["changes"]}
    return {**out, "from": _version_view(old), "to": _version_view(new), "warnings": d["warnings"],
            "summary": {k: d[k] for k in ("clauses", "changed", "new", "removed", "renumbered", "unchanged")},
            "changes": d["changes"], "unchanged": [{"ref": r, "text": t} for r, t in clauses.items() if r not in changed_refs],
            "clauses": []}


@router.post("/sources/{sid}/preview")
def preview_version(sid: int, body: VersionIn, db=Depends(get_db)):
    s = one(db, M.Source, sid)
    text, _ = _text_of(body)
    return intake.preview_source_version(db, s, text)


@router.post("/sources/{sid}/versions")
def new_source_version(sid: int, body: VersionIn, db=Depends(get_db), me=Depends(actor)):
    s = one(db, M.Source, sid)
    text, file_name = _text_of(body)
    label = body.version.strip() or (file_name.rsplit(".", 1)[0] if file_name else "")
    if not label:
        raise HTTPException(400, "give the version a label, for example 2022")
    if db.scalar(select(M.SourceVersion).where(M.SourceVersion.source_id == sid, M.SourceVersion.version == label)):
        raise HTTPException(400, f"{s.name} already has a version labelled '{label}'")
    return guarded(lambda: intake.ingest_source_version(
        db, s, label, text, file_name=file_name, retrieved_from=body.retrieved_from,
        effective_date=body.effective_date, uploaded_by=me))(db)


# ---------- feeds ----------
@router.get("/feeds")
def feeds_view(db=Depends(get_db)):
    return feeds.feeds_view(db)


@router.get("/feeds/{sid}/dashboard")
def feed_dashboard(sid: int, db=Depends(get_db)):
    s = one(db, M.Source, sid)
    if s.kind not in S.FEED_KINDS:
        raise HTTPException(400, f"{s.name} is a source text, not a security feed")
    return feeds.dashboard(db, s)


@router.post("/feeds/{sid}/release-next")
def release_next_from(sid: int, db=Depends(get_db)):
    s = one(db, M.Source, sid)
    r = guarded(lambda: feeds.release_next_from(db, s))(db)
    feeds.check_health(db)
    db.commit()
    return r


@router.post("/feeds/poll")
def poll_feeds(db=Depends(get_db)):
    r = feeds.poll_all(db)
    feeds.check_health(db)
    db.commit()
    return r


@router.post("/feeds/release-next")
def release_next(db=Depends(get_db)):
    r = guarded(lambda: feeds.release_next(db))(db)
    feeds.check_health(db)
    db.commit()
    return r


# ---------- guidance and approval ----------
def guidance_view(db, g: M.Guidance) -> dict:
    ob = db.get(M.Obligation, g.obligation_id)
    name = lambda i: db.get(M.Person, i).name if i and db.get(M.Person, i) else None  # noqa: E731
    asset = db.get(M.Asset, g.asset_id) if g.asset_id else None
    return {**ser(g), "obligation": ob.title, "obligation_kind": ob.kind, "obligation_status": ob.status,
            "asset": asset.name if asset else None, "recipient": name(g.recipient_id), "approver": name(g.approver_id),
            "consult": name(g.consult_id),
            "topic_label": APPROVALS["clause_topics"].get(g.topic, {}).get("label") if g.topic else None}


@router.get("/guidance")
def list_guidance(db=Depends(get_db)):
    q = select(M.Guidance).join(M.Obligation, M.Obligation.id == M.Guidance.obligation_id).where(
        M.Obligation.is_test == False, M.Guidance.recipient_id.is_not(None) | M.Guidance.department.is_not(None)  # noqa: E712
        | (M.Guidance.status == "withheld")).order_by(M.Guidance.id.desc())
    return [guidance_view(db, g) for g in db.scalars(q)]


@router.post("/guidance/{gid}/approve")
def approve_guidance(gid: int, db=Depends(get_db), me=Depends(actor)):
    g = one(db, M.Guidance, gid)
    person = on_behalf(db, me, db.get(M.Person, g.approver_id) if g.approver_id else me, "guidance", gid, "approve")
    guarded(lambda: approvals.approve_guidance(db, g, person))(db)
    return guidance_view(db, g)


@router.post("/guidance/{gid}/reject")
def reject_guidance(gid: int, body: Reason, db=Depends(get_db), me=Depends(actor)):
    g = one(db, M.Guidance, gid)
    person = on_behalf(db, me, db.get(M.Person, g.approver_id) if g.approver_id else me, "guidance", gid, "reject")
    guarded(lambda: approvals.reject_guidance(db, g, person, body.reason))(db)
    return guidance_view(db, g)


@router.post("/guidance/{gid}/consult")
def consult_guidance(gid: int, db=Depends(get_db), me=Depends(actor)):
    g = one(db, M.Guidance, gid)
    person = on_behalf(db, me, db.get(M.Person, g.consult_id) if g.consult_id else me, "guidance", gid, "consult")
    guarded(lambda: approvals.consult_guidance(db, g, person))(db)
    return guidance_view(db, g)


@router.get("/obligations/{oid}/passages")
def passages(oid: int, asset_id: int | None = None, db=Depends(get_db)):
    """What a hand-written answer may quote: the same passages RAG retrieves."""
    return rag.passages_for(db, one(db, M.Obligation, oid), asset_id)


class WrittenGuidance(BaseModel):
    text: str
    citations: list[dict]
    department: str | None = None
    asset_id: int | None = None
    author_role: str = "compliance"  # compliance | legal | cco


@router.post("/obligations/{oid}/guidance/write")
def write_guidance(oid: int, body: WrittenGuidance, db=Depends(get_db), me=Depends(actor)):
    ob = one(db, M.Obligation, oid)
    if body.author_role not in ("compliance", "legal", "cco"):
        raise HTTPException(400, "the author must be Compliance, Legal or the CCO")
    author = on_behalf(db, me, _role_holder(db, body.author_role), "obligation", oid, "write_guidance")
    g = guarded(lambda: rag.write_guidance(db, ob, author, text=body.text, citations=body.citations,
                                           department=body.department, asset_id=body.asset_id))(db)
    return guidance_view(db, g)


@router.get("/approval-map")
def approval_map(person_id: int, db=Depends(get_db)):
    return approvals.approval_map(db, one(db, M.Person, person_id))


@router.get("/approval-rules")
def approval_rules(db=Depends(get_db)):
    r = approvals.rules_view()
    who = lambda role: (lambda p: p.name if p else None)(approvals.holder(db, role))  # noqa: E731
    for t in r["topics"]:
        t["approver_name"], t["consult_name"] = who(t["approver"]), who(t["consult"])
    r["advisory"] = {**r["advisory"], "consult_name": who(r["advisory"]["consult"]),
                     "withheld_name": who(r["advisory"]["withheld_to"])}
    r["fallback_name"] = who(r["segregation_fallback"])
    r["signoff"] = {**r["signoff"], "escalate_name": who(r["signoff"]["escalate_to"]),
                    "escalate_if_cco_name": who(r["signoff"]["escalate_to_if_cco"])}
    return r


class SignoffIn(BaseModel):
    role: str  # security | system_owner | compliance | legal
    record_version: str | None = None  # system_owner: the version their own component record shows
    record_source: str | None = None  # system_owner: where that record comes from (closure.RECORD_SOURCES)
    note: str = ""


@router.post("/tasks/{tid}/review-signoff")
def review_signoff(tid: int, body: SignoffIn, db=Depends(get_db), me=Depends(actor)):
    t = one(db, M.Task, tid)
    signer = closure.required_signer(db, t, body.role) if body.role in closure.needed_signoffs(t) else None
    if signer is None:
        raise HTTPException(400, "nobody can sign this off: check the task's required reviewers")
    person = on_behalf(db, me, signer, "task", tid, f"sign_off {body.role}")
    guarded(lambda: closure.sign_required_review(db, t, body.role, person, record_version=body.record_version,
                                                 record_source=body.record_source, note=body.note))(db)
    return {"signoffs": closure.signoff_view(db, t), "status": t.status, "dispute": t.signoff_dispute}


# ---------- quarterly summary ----------
@router.get("/quarterly-summary")
def quarterly(quarter: str | None = None, db=Depends(get_db)):
    try:
        return S.quarterly_summary(db, quarter)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/quarterly-summary.csv")
def quarterly_csv(quarter: str | None = None, db=Depends(get_db)):
    try:
        label, body = S.quarterly_csv(db, quarter)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="polibridge-source-changes-{label}.csv"'})
