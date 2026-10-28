from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import DB_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(eng=None):
    from . import models  # noqa: F401
    eng = eng or engine
    Base.metadata.create_all(eng)
    # create_all never alters existing tables: add columns introduced after a database was first seeded
    added = {"advisory": {"description": "TEXT", "received_at": "DATETIME", "ingested_at": "DATETIME",
                          "sources": "JSON", "conflict": "TEXT"},
             "obligation": {"previous_quotation": "TEXT", "departments": "JSON", "supersedes_id": "INTEGER",
                            "superseded_reason": "TEXT", "source_version_id": "INTEGER"},
             "person": {"email": "TEXT"},
             "source": {"authority": "TEXT", "jurisdiction": "TEXT", "intake": "TEXT DEFAULT 'manual'",
                        "owner_id": "INTEGER", "review_days": "INTEGER", "status": "TEXT DEFAULT 'approved'",
                        "approved_at": "DATETIME", "proposed_by": "TEXT", "status_reason": "TEXT",
                        "pending_file": "TEXT", "pending_version": "TEXT",
                        "last_polled_at": "DATETIME", "last_success_at": "DATETIME",
                        "consecutive_failures": "INTEGER DEFAULT 0", "simulate_outage": "BOOLEAN DEFAULT 0",
                        "created_at": "DATETIME"},
             "source_version": {"sha256": "TEXT", "file_name": "TEXT", "retrieved_from": "TEXT",
                                "effective_date": "TEXT", "uploaded_by": "TEXT", "summary": "JSON"},
             "claim": {"source_id": "INTEGER"},
             "task": {"reviewer_signoffs": "JSON DEFAULT '{}'", "signoff_dispute": "JSON"},
             "guidance": {"engine": "TEXT", "department": "TEXT", "asset_id": "INTEGER", "recipient_id": "INTEGER",
                          "topic": "TEXT", "approver_id": "INTEGER", "approved_at": "DATETIME", "consult_id": "INTEGER",
                          "consulted_at": "DATETIME", "human_authored": "BOOLEAN DEFAULT 0"},
             "canary_run": {"feed": "TEXT", "stage": "TEXT"}}
    with eng.begin() as c:
        for table, cols_to_add in added.items():
            have = {r[1] for r in c.exec_driver_sql(f"PRAGMA table_info({table})")}
            for col, typ in cols_to_add.items():
                if have and col not in have:
                    c.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        # everyone has an email: fill the gap for people recorded before the field existed
        if {r[1] for r in c.exec_driver_sql("PRAGMA table_info(person)")} >= {"email"}:
            from .identity import email_for
            taken = {r[0] for r in c.exec_driver_sql("SELECT email FROM person WHERE email IS NOT NULL")}
            for pid, name in c.exec_driver_sql("SELECT id, name FROM person WHERE email IS NULL OR email = ''").fetchall():
                addr, n = email_for(name), 1
                while addr in taken:
                    n += 1
                    addr = email_for(name).replace("@", f"{n}@")
                taken.add(addr)
                c.exec_driver_sql("UPDATE person SET email = ? WHERE id = ?", (addr, pid))
