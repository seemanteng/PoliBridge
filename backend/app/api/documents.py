"""Documents: upload and preview PDFs/text in docs/, extract text for ingestion, and per-document update reminders.
Only files directly inside docs/ are served. GETs need no X-User header (an <iframe> cannot send one)."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from ..clock import clock
from ..db import get_db
from .. import models as M
from ..services import audit
from .routes import actor

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs"
ALLOWED = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8"}
MAX_BYTES = 20 * 1024 * 1024
SOON_DAYS = 7

router = APIRouter(prefix="/api")


def _files(folder: str = "docs"):
    d = DOCS if folder == "docs" else DOCS / "archive"
    return sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in ALLOWED) if d.exists() else []


def _find(folder: str, name: str):
    return next((f for f in _files(folder) if folder in ("docs", "archive") and f.name == name), None)


@router.get("/documents")
def list_documents():
    """Current documents, then every earlier version that an upload replaced."""
    cur = [{"id": f"docs/{f.name}", "name": f.stem, "ext": f.suffix.lower().lstrip("."), "group": "Documents",
            "size": f.stat().st_size} for f in _files()]
    past = []
    for f in sorted(_files("archive"), key=lambda f: f.stat().st_mtime, reverse=True):
        m = re.match(r"^(.*)-(\d{8})-(\d{6})$", f.stem)
        title, when = (m.group(1), f"{m.group(2)[6:]}/{m.group(2)[4:6]}/{m.group(2)[:4]} {m.group(3)[:2]}:{m.group(3)[2:4]}") if m else (f.stem, "")
        past.append({"id": f"archive/{f.name}", "name": f"{title} · replaced {when}".strip(" ·"), "title": title,
                     "ext": f.suffix.lower().lstrip("."), "group": "Past versions", "size": f.stat().st_size})
    return cur + past


@router.get("/documents/{folder}/{name}")
def get_document(folder: str, name: str):
    match = _find(folder, name)
    if not match:
        raise HTTPException(404, "document not found")
    return FileResponse(match, media_type=ALLOWED[match.suffix.lower()], content_disposition_type="inline")


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="replace")


@router.get("/documents/{folder}/{name}/text")
def document_text(folder: str, name: str):
    match = _find(folder, name)
    if not match:
        raise HTTPException(404, "document not found")
    text = extract_text(match).strip()
    if not text:
        raise HTTPException(422, "no text could be extracted (is this a scanned PDF?)")
    return {"id": f"docs/{match.name}", "name": match.stem, "text": text}


@router.post("/documents/upload")
async def upload(request: Request, filename: str, db=Depends(get_db), me=Depends(actor)):
    """Raw request body is the file. Uploading a file with the same title replaces it (the old one is archived)
    and marks any reminder for that title as satisfied."""
    clean = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(filename).name).strip(" .") or "document"
    ext = Path(clean).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"unsupported file type '{ext}'; upload {', '.join(ALLOWED)}")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty file")
    if len(body) > MAX_BYTES:
        raise HTTPException(413, "file is larger than 20 MB")
    DOCS.mkdir(exist_ok=True)
    stem = Path(clean).stem
    replaced = False
    for old in _files():
        if old.stem == stem:
            (DOCS / "archive").mkdir(exist_ok=True)
            old.rename(DOCS / "archive" / f"{old.stem}-{clock.now():%Y%m%d-%H%M%S}{old.suffix}")
            replaced = True
    (DOCS / clean).write_bytes(body)
    satisfied = 0
    for r in db.scalars(select(M.DocumentReminder).where(M.DocumentReminder.document == stem)):
        r.last_updated_at, r.last_notified_at = clock.now(), None
        satisfied += 1
    audit.log(db, me.directory_id, "document", 0, "uploaded", file=clean, replaced=replaced, reminders_reset=satisfied)
    db.commit()
    return {"id": f"docs/{clean}", "name": stem, "replaced": replaced, "reminders_reset": satisfied}


# ---------- reminders ----------
def reminder_view(db, r: M.DocumentReminder) -> dict:
    now = clock.now()
    due = r.last_updated_at + timedelta(days=r.every_days)
    left = (due - now).total_seconds() / 86400
    owner = db.get(M.Person, r.owner_id)
    return {"id": r.id, "document": r.document, "every_days": r.every_days, "owner": owner.name if owner else None,
            "owner_id": r.owner_id, "last_updated_at": r.last_updated_at.isoformat(), "due_at": due.isoformat(),
            "days_left": round(left, 1), "has_document": any(f.stem == r.document for f in _files()),
            "status": "overdue" if left < 0 else "due_soon" if left <= SOON_DAYS else "ok"}


@router.get("/reminders")
def list_reminders(db=Depends(get_db)):
    return [reminder_view(db, r) for r in db.scalars(select(M.DocumentReminder).order_by(M.DocumentReminder.id))]


class ReminderIn(BaseModel):
    document: str
    owner_id: int
    every_days: int
    first_due: datetime | None = None  # when the first update is due; defaults to one cycle from now


@router.post("/reminders")
def add_reminder(body: ReminderIn, db=Depends(get_db), me=Depends(actor)):
    title = Path(body.document).stem if Path(body.document).suffix.lower() in ALLOWED else body.document.strip()
    if not title:
        raise HTTPException(400, "choose a document")
    if not 1 <= body.every_days <= 3650:
        raise HTTPException(400, "reminder cycle must be between 1 and 3650 days")
    owner = db.get(M.Person, body.owner_id)
    if not owner or not owner.active:
        raise HTTPException(400, "the reminder owner must be an active person")
    # the cycle is counted from the last update, so the first due date fixes where the cycle starts
    due = body.first_due
    if due is not None and due.tzinfo is not None:  # the clock is naive UTC
        due = due.astimezone(timezone.utc).replace(tzinfo=None)
    start = due - timedelta(days=body.every_days) if due else clock.now()
    r = M.DocumentReminder(document=title, owner_id=owner.id, every_days=body.every_days,
                           last_updated_at=start, created_at=clock.now())
    db.add(r)
    db.flush()
    audit.log(db, me.directory_id, "document", r.id, "reminder_set", document=title, every_days=body.every_days,
              owner=owner.directory_id)
    db.commit()
    return reminder_view(db, r)


@router.delete("/reminders/{rid}")
def delete_reminder(rid: int, db=Depends(get_db), me=Depends(actor)):
    r = db.get(M.DocumentReminder, rid)
    if not r:
        raise HTTPException(404, "reminder not found")
    audit.log(db, me.directory_id, "document", r.id, "reminder_removed", document=r.document)
    db.delete(r)
    db.commit()
    return {"ok": True}
