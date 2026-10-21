from ..db import init_db, SessionLocal, engine, Base
from . import data

Base.metadata.drop_all(engine)
init_db()
with SessionLocal() as db:
    print(data.load(db))
