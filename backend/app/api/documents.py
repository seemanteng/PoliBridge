"""Documents: upload and preview PDFs/text in docs/ and extract their text. A document becomes evidence of a source's
wording when it is ingested as a version of an approved source (api/sources.py), which fingerprints it.
Only files directly inside docs/ are served. GETs need no X-User header (an <iframe> cannot send one)."""
import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
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


def _sha(f: Path) -> str:
    return hashlib.sha256(f.read_bytes()).hexdigest()


@router.get("/documents")
def list_documents(db=Depends(get_db)):
    """Current documents, then every earlier version that an upload replaced. Each says which source version, if
    any, it was ingested as."""
    linked = {}
    for v in db.scalars(select(M.SourceVersion).where(M.SourceVersion.file_name.is_not(None))):
        linked.setdefault(v.file_name, []).append(f"{db.get(M.Source, v.source_id).name} · v{v.version}")
    cur = [{"id": f"docs/{f.name}", "name": f.stem, "ext": f.suffix.lower().lstrip("."), "group": "Documents",
            "size": f.stat().st_size, "sha256": _sha(f), "linked": linked.get(f.name, [])} for f in _files()]
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
    """Raw request body is the file. Uploading a file with the same title replaces it (the old one is archived)."""
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
    digest = hashlib.sha256(body).hexdigest()
    audit.log(db, me.directory_id, "document", 0, "uploaded", file=clean, replaced=replaced, sha256=digest)
    db.commit()
    return {"id": f"docs/{clean}", "name": stem, "file": clean, "replaced": replaced, "sha256": digest}
