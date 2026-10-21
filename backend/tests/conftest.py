from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clock import clock
from app.db import Base
from app import models
from app.seed import data
from app.services.common import person_by_directory_id

T0 = datetime(2017, 3, 8, 9, 0)


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """Tests must never call a real model, whatever is in the developer's environment."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture
def db():
    clock.set(T0)
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    data.load(s)
    yield s
    s.close()
    clock.unfreeze()


@pytest.fixture
def who(db):
    return lambda did: person_by_directory_id(db, did)


@pytest.fixture
def asset(db):
    return lambda name: db.scalar(select(models.Asset).where(models.Asset.name == name))


STRUTS = dict(cve="CVE-2017-5638", product="Apache Struts", affected_versions=["2.3.5-2.3.31", "2.5-2.5.10"],
              cvss=10.0, kev=True, fixed_version="2.3.32")
