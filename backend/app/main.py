import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from .api.routes import router  # noqa: E402
from .api.documents import router as documents_router  # noqa: E402
from .api.records import router as records_router  # noqa: E402
from .api.registry_view import router as registry_router  # noqa: E402
from .db import init_db, SessionLocal  # noqa: E402
from .models import Person  # noqa: E402
from .seed import replay  # noqa: E402
from .clock import clock  # noqa: E402

@asynccontextmanager
async def lifespan(_):
    init_db()
    clock.load()
    with SessionLocal() as db:
        if db.query(Person).count() == 0:
            replay.load_baseline(db)
            replay.run_storyline(db)
    yield


app = FastAPI(title="PoliBridge", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5180", "http://127.0.0.1:5180"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(documents_router)
app.include_router(records_router)
app.include_router(registry_router)


@app.get("/api/health")
def health():
    return {"ok": True, "rag_engine": "openai" if os.getenv("OPENAI_API_KEY") else "stub"}
