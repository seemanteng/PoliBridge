"""Everyone in the registry has a contact email, whichever way the data was seeded."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clock import clock
from app.db import Base
from app.identity import email_for, valid_email
from app.models import Person
from app.seed import replay


def _everyone_has_one(db):
    people = list(db.scalars(select(Person)))
    assert people
    assert all(valid_email(p.email) for p in people), [p.name for p in people if not valid_email(p.email)]
    assert len({p.email for p in people}) == len(people), "two people share an address"


def test_fixture_seed_has_emails(db):
    _everyone_has_one(db)


def test_live_demo_seed_has_emails():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        replay.load_baseline(s, anchor=clock.now())
        _everyone_has_one(s)
    finally:
        s.close()
        clock.unfreeze()


def test_replay_seed_has_emails():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        replay.load_baseline(s)
        _everyone_has_one(s)
    finally:
        s.close()
        clock.unfreeze()


@pytest.mark.parametrize("name,expected", [
    ("Mei Lin", "mei.lin@polibridge.example"),
    ("Sofia Ramírez", "sofia.ramirez@polibridge.example"),   # accents are dropped
    ("Ama Mensah-Boateng", "ama.mensahboateng@polibridge.example"),
    ("Cher", "cher@polibridge.example"),
])
def test_generated_addresses(name, expected):
    assert email_for(name) == expected and valid_email(expected)
