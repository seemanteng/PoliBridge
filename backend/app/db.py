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
    added = {"advisory": {"description": "TEXT"},
             "obligation": {"previous_quotation": "TEXT", "departments": "JSON"},
             "person": {"email": "TEXT"}}
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
