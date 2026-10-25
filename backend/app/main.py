import asyncio
import logging
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
from .api.workflow import router as workflow_router  # noqa: E402
from .api.sources import router as sources_router  # noqa: E402
from .db import init_db, SessionLocal  # noqa: E402
from .models import Person  # noqa: E402
from .seed import replay  # noqa: E402
from .clock import clock  # noqa: E402
from .services import assurance  # noqa: E402

log = logging.getLogger("polibridge")
SWEEP_EVERY_S = 60


def _sweep_once():
    with SessionLocal() as db:
        assurance.sweep(db)


async def _sweeper():
    """Feeds, escalation, expiry and source reviews are driven by time, so something has to look at the clock."""
    while True:
        await asyncio.sleep(SWEEP_EVERY_S)
        try:
            await asyncio.to_thread(_sweep_once)
        except Exception:  # keep the loop alive; the next pass retries
            log.exception("sweep failed")


@asynccontextmanager
async def lifespan(_):
    init_db()
    clock.load()
    with SessionLocal() as db:
        empty = db.query(Person).count() == 0
    if empty or clock.simulated:
        # a database left over from the frozen-clock 2017 replay cannot run on real time, so it is re-seeded
        if not empty:
            log.warning("Database was seeded on a simulated 2017 clock; re-seeding it for live time.")
        replay.reset_demo("baseline")
    else:
        with SessionLocal() as db:  # a database seeded before the source registry: fill in what it is missing
            from .seed.sources import upgrade
            done = upgrade(db)
            db.commit()
        if done:
            log.warning("Upgraded the demo data: %s", "; ".join(done))
    await asyncio.to_thread(_sweep_once)
    task = asyncio.create_task(_sweeper())
    yield
    task.cancel()


app = FastAPI(title="PoliBridge", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5180", "http://127.0.0.1:5180"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(documents_router)
app.include_router(records_router)
app.include_router(registry_router)
app.include_router(workflow_router)
app.include_router(sources_router)


@app.get("/api/health")
def health():
    return {"ok": True, "rag_engine": "openai" if os.getenv("OPENAI_API_KEY") else "stub"}
