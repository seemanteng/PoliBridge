"""Read-only access to the project's PDFs (e.g. the report) so the UI can preview them.
Only files directly inside the allow-listed folders are served. No X-User header: an <iframe> cannot send one."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[3]
FOLDERS = {"docs": "Documents"}

router = APIRouter(prefix="/api/documents")


def _files():
    for folder, label in FOLDERS.items():
        for f in sorted((ROOT / folder).glob("*.pdf")):
            yield folder, label, f


@router.get("")
def list_documents():
    return [{"id": f"{folder}/{f.name}", "name": f.stem, "group": label, "size": f.stat().st_size}
            for folder, label, f in _files()]


@router.get("/{folder}/{name}")
def get_document(folder: str, name: str):
    match = next((f for fo, _, f in _files() if fo == folder and f.name == name), None)
    if not match:
        raise HTTPException(404, "document not found")
    return FileResponse(match, media_type="application/pdf", content_disposition_type="inline")
