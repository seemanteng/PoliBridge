"""Shared lookups, claim creation and notification. Kept small on purpose."""
from datetime import timedelta

from sqlalchemy import select

from ..clock import clock
from ..config import TIMINGS
from ..models import Person, Asset, Claim, Dependency, Notification, Finding
from . import audit


def person_by_directory_id(db, did: str) -> Person | None:
    return db.scalar(select(Person).where(Person.directory_id == did))


def first_with_role(db, role: str) -> Person | None:
    return db.scalar(select(Person).where(Person.role == role, Person.active == True))  # noqa: E712


def people_with_role(db, role: str) -> list[Person]:
    return list(db.scalars(select(Person).where(Person.role == role, Person.active == True)))  # noqa: E712


def bu_head(db, asset: Asset) -> Person | None:
    bu = asset.business_unit
    head = db.get(Person, bu.head_id) if bu and bu.head_id else None
    return head if head and head.active else None


def owner_validity(db, asset: Asset) -> tuple[bool, str]:
    """B3: owner must be a named, active directory identity with a current attestation."""
    now = clock.now()
    if asset.owner_id is None:
        why = "no named owner" + (f" (legacy contact '{asset.legacy_contact}' is not an identity)"
                                  if asset.legacy_contact else "")
        return False, why
    owner = db.get(Person, asset.owner_id)
    if not owner or not owner.active:
        return False, "owner has left or is inactive in the directory"
    att = asset.ownership_attested_at
    if att is None or now - att > timedelta(days=TIMINGS["attestation_days"]):
        return False, "ownership attestation missing or expired"
    return True, ""


def notify(db, *, task=None, person: Person | None, kind: str, message: str = "") -> Notification:
    delivered = bool(person and person.active and person.deliverable)
    n = Notification(task_id=task.id if task else None, person_id=person.id if person else None,
                     kind=kind, message=message, delivered=delivered, created_at=clock.now())
    db.add(n)
    if not delivered:
        raise_finding(db, "delivery_failure", "High" if task and task.impact == "High" else "Medium",
                      f"{kind} to {person.name if person else 'nobody'} not delivered",
                      "Recipient inactive or mailbox undeliverable; message did not reach a human.",
                      assigned_to=first_with_role(db, "ciso"), is_test=bool(task and task.is_test))
    return n


def raise_finding(db, kind, severity, subject, detail="", assigned_to=None, is_test=False, dedupe=True):
    if dedupe:
        existing = db.scalar(select(Finding).where(Finding.kind == kind, Finding.subject == subject,
                                                   Finding.status == "open"))
        if existing:
            return existing
    f = Finding(kind=kind, severity=severity, subject=subject, detail=detail,
                assigned_to_id=assigned_to.id if assigned_to else None, is_test=is_test,
                created_at=clock.now())
    db.add(f)
    db.flush()
    audit.log(db, "system", "finding", f.id, "raised", kind=kind, subject=subject)
    return f


def create_claim(db, *, actor="system", **fields) -> Claim:
    c = Claim(created_at=clock.now(), **fields)
    db.add(c)
    db.flush()
    audit.log(db, actor, "claim", c.id, "created", type=c.claim_type, value=c.value,
              statement=c.statement, expiry=str(c.expiry) if c.expiry else None)
    return c


def add_dependency(db, claim: Claim, dep_type: str, dep_id: int):
    db.add(Dependency(claim_id=claim.id, dependent_type=dep_type, dependent_id=dep_id))
