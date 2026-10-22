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
    with eng.begin() as c:
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(advisory)")}
        if cols and "description" not in cols:
            c.exec_driver_sql("ALTER TABLE advisory ADD COLUMN description TEXT")
