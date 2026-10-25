"""In-app records rendered as previewable documents: source texts, advisories and submitted evidence.
Evidence is stored as structured fields (no uploaded files), so it is shown as a record with its checks."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ..db import get_db
from .. import models as M
from ..services import closure, views

router = APIRouter(prefix="/api/records")


def _ts(d, fmt="%d %b %Y %H:%M"):
    return d.strftime(fmt) if d else "—"


@router.get("")
def list_records(db=Depends(get_db)):
    out = []
    for sv in db.scalars(select(M.SourceVersion).order_by(M.SourceVersion.source_id, M.SourceVersion.id)):
        s = db.get(M.Source, sv.source_id)
        out.append({"id": f"source/{sv.id}", "group": "Source texts", "name": f"{s.name} · v{sv.version}"})
    for a in db.scalars(select(M.Advisory).where(M.Advisory.is_canary.is_(False)).order_by(M.Advisory.id)):
        out.append({"id": f"advisory/{a.id}", "group": "Advisories", "name": f"{a.cve} · {a.product}"})
    for e in db.scalars(select(M.Evidence).order_by(M.Evidence.id)):
        out.append({"id": f"evidence/{e.id}", "group": "Evidence", "name": f"EVD-{e.id:04d} · {e.asset_name or 'unknown asset'}"})
    return out


@router.get("/{kind}/{rid}")
def get_record(kind: str, rid: int, db=Depends(get_db)):
    if kind == "source":
        sv = db.get(M.SourceVersion, rid)
        s = db.get(M.Source, sv.source_id) if sv else None
        if not sv:
            raise HTTPException(404, "record not found")
        return {"title": s.name, "subtitle": f"Version {sv.version}", "text": sv.text,
                "fields": [["Kind", s.kind], ["Jurisdiction", s.jurisdiction or "—"], ["Approved by", s.approved_by or "—"],
                           ["Ingested", _ts(sv.ingested_at)], ["Uploaded by", sv.uploaded_by or "—"],
                           ["Retrieved from", sv.retrieved_from or s.url or "—"], ["Effective", sv.effective_date or "—"],
                           ["File", sv.file_name or "—"], ["SHA-256", sv.sha256 or "—"]]}
    if kind == "advisory":
        a = db.get(M.Advisory, rid)
        if not a:
            raise HTTPException(404, "record not found")
        s = db.get(M.Source, a.source_id) if a.source_id else None
        return {"title": a.cve, "subtitle": a.product, "text": a.description,
                "fields": [["Sources", ", ".join(x["source"] for x in a.sources or []) or (s.name if s else "—")],
                           ["Published", _ts(a.published_at)], ["Received", _ts(a.received_at)],
                           ["Ingested", _ts(a.ingested_at)], ["Affected product", a.product],
                           ["Affected versions", ", ".join(a.affected_versions)], ["Fixed version", a.fixed_version or "—"],
                           ["CVSS", a.cvss if a.cvss is not None else "unknown"],
                           ["Exploited in the wild (KEV)", "yes" if a.kev else "no"], ["Conflict", a.conflict or "—"]]}
    if kind == "evidence":
        e = db.get(M.Evidence, rid)
        if not e:
            raise HTTPException(404, "record not found")
        c = closure.evidence_checks(db, e)
        P = lambda i: db.get(M.Person, i).name if i else "—"  # noqa: E731
        return {"title": c["claim"], "subtitle": f"EVD-{e.id:04d} · {e.purpose}", "text": None, "checks": c["checks"],
                "fields": [["Task", views.task_label(db.get(M.Task, e.task_id))], ["Asset", e.asset_name or "—"],
                           ["Component", e.component or "—"], ["Scope examined", ", ".join(e.scope or []) or "—"],
                           ["Method", (e.method or "—").replace("_", " ")], ["Observed version", e.observed_version or "—"],
                           ["Evidence timestamp", _ts(e.timestamp)], ["Submitted by", P(e.submitter_id)],
                           ["Reviewer", P(e.reviewer_id)], ["Status", e.status],
                           ["Reasons", "; ".join(e.reasons) or "—"]]}
    raise HTTPException(404, "unknown record type")
