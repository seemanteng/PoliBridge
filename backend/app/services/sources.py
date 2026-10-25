"""Approved-source registry (Appendix E). A source is only trusted while it is approved, and "our register reflects
the current version of this source" is itself a claim: it is renewed each time someone checks the source and lapses
on the source's review cycle (F1), like any other evidence."""
import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import select

from ..clock import clock
from ..config import TIMINGS
from ..models import Source, SourceVersion, Claim, Obligation, Person, FeedItem, Finding, Dependency
from . import audit
from .common import create_claim, add_dependency, first_with_role

KINDS = ("advisory", "vendor", "regulator", "policy", "contract")
FEED_KINDS = ("advisory", "vendor")
JURISDICTIONS = ("US federal", "US state", "UK", "EU", "Internal", "Global")


def require_approved(source: Source):
    if source.status != "approved":
        raise ValueError(f"'{source.name}' is {source.status} in the approved-source registry; it cannot feed obligations")


def current_check(db, source: Source) -> Claim | None:
    return db.scalar(select(Claim).where(Claim.claim_type == "source_currency", Claim.source_id == source.id)
                     .order_by(Claim.id.desc()))


def review_state(db, source: Source) -> dict:
    """current | due_soon | overdue | never (manual sources); feeds are judged by feed health instead."""
    if source.intake == "feed":
        return {"status": "feed", "last_checked": None, "next_due": None, "claim_id": None}
    c = current_check(db, source)
    if c is None:
        return {"status": "never", "last_checked": None, "next_due": None, "claim_id": None}
    now = clock.now()
    soon = timedelta(days=TIMINGS["source_review_due_soon_days"])
    status = ("overdue" if c.status == "expired" or (c.expiry and c.expiry <= now) else
              "due_soon" if c.expiry and c.expiry - now <= soon else "current")
    return {"status": status, "last_checked": c.evidence_ts.isoformat() if c.evidence_ts else None,
            "next_due": c.expiry.isoformat() if c.expiry else None, "claim_id": c.id, "how": c.verification_method}


def record_check(db, source: Source, person: Person | None, *, how: str = "checked_no_change",
                 version: SourceVersion | None = None, note: str | None = None) -> Claim | None:
    """Renew the source-currency claim. `how` is checked_no_change | new_version | identical_upload."""
    if source.intake == "feed":
        return None
    now = clock.now()
    days = source.review_days or 90
    for old in db.scalars(select(Claim).where(Claim.claim_type == "source_currency", Claim.source_id == source.id,
                                              Claim.status == "active")):
        old.status = "superseded"
    latest = version or db.scalar(select(SourceVersion).where(SourceVersion.source_id == source.id)
                                  .order_by(SourceVersion.id.desc()))
    who = person.directory_id if person else "system"
    c = create_claim(db, actor=who, claim_type="source_currency", source_id=source.id, value=how,
                     statement=f"The register reflects the current {source.name}"
                               + (f" (version {latest.version})" if latest else ""),
                     evidence_scope=[source.url] if source.url else [], verification_method=how,
                     evidence_ts=now, submitter_id=person.id if person else None,
                     owner_id=source.owner_id or (person.id if person else None),
                     expiry=now + timedelta(days=days), reason=note)
    for ob in db.scalars(select(Obligation).where(Obligation.source_id == source.id,
                                                  Obligation.status.in_(("open", "closed")))):
        add_dependency(db, c, "obligation", ob.id)
    for f in db.scalars(select(Finding).where(Finding.kind == "source_review_overdue", Finding.status == "open",
                                              Finding.subject == overdue_subject(source))):
        f.status = "resolved"
        audit.log(db, who, "finding", f.id, "resolved", because="source re-checked")
    audit.log(db, who, "source", source.id, "checked", how=how, version=latest.version if latest else None,
              next_due=str(c.expiry))
    return c


def overdue_subject(source: Source) -> str:
    return f"{source.name} has not been re-checked"


def propose(db, *, name: str, kind: str, authority: str | None, jurisdiction: str | None, url: str | None,
            intake: str, owner: Person | None, review_days: int | None, proposer: Person,
            file_name: str | None = None, version: str | None = None) -> Source:
    name = (name or "").strip()
    if not name:
        raise ValueError("name the source")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if intake not in ("feed", "manual"):
        raise ValueError("intake must be feed or manual")
    if intake == "feed" and kind not in FEED_KINDS:
        raise ValueError("only advisory and vendor sources can be polled as feeds")
    if intake == "manual" and not (review_days and 1 <= review_days <= 730):
        raise ValueError("a manually checked source needs a review cycle between 1 and 730 days")
    if db.scalar(select(Source).where(Source.name == name)):
        raise ValueError(f"'{name}' is already in the registry")
    if file_name and intake == "feed":
        raise ValueError("a feed's advisories arrive through the feed, so it takes no file")
    s = Source(name=name, kind=kind, authority=authority, jurisdiction=jurisdiction, url=url, intake=intake,
               owner_id=owner.id if owner else None, review_days=review_days if intake == "manual" else None,
               status="pending", proposed_by=proposer.name, created_at=clock.now(),
               pending_file=file_name, pending_version=(version or "").strip() or ("1" if file_name else None))
    db.add(s)
    db.flush()
    audit.log(db, proposer.directory_id, "source", s.id, "proposed", name=name, kind=kind, file=file_name)
    return s


def approve(db, source: Source, approver: Person):
    """RACI row 1: the CCO is accountable for the approved-source registry."""
    if approver.role != "cco":
        raise PermissionError("only the Chief Compliance Officer approves sources")
    if source.status == "approved":
        raise ValueError("already approved")
    if source.proposed_by == approver.name:
        raise PermissionError("the person who proposed a source cannot also approve it")
    source.status, source.approved_by, source.approved_at, source.status_reason = "approved", approver.name, clock.now(), None
    audit.log(db, approver.directory_id, "source", source.id, "approved")


def suspend(db, source: Source, person: Person, reason: str):
    if not (reason or "").strip():
        raise ValueError("record why the source is suspended")
    if person.role not in ("cco", "compliance"):
        raise PermissionError("only Compliance or the CCO can suspend a source")
    source.status, source.status_reason = "suspended", reason.strip()
    audit.log(db, person.directory_id, "source", source.id, "suspended", reason=reason)


def source_view(db, s: Source) -> dict:
    from . import feeds  # local import: feeds uses intake, which uses this module
    owner = db.get(Person, s.owner_id) if s.owner_id else None
    versions = list(db.scalars(select(SourceVersion).where(SourceVersion.source_id == s.id).order_by(SourceVersion.id)))
    obs = list(db.scalars(select(Obligation).where(Obligation.source_id == s.id, Obligation.is_test == False)))  # noqa: E712
    return {"id": s.id, "name": s.name, "kind": s.kind, "authority": s.authority, "jurisdiction": s.jurisdiction,
            "url": s.url, "intake": s.intake, "status": s.status, "status_reason": s.status_reason,
            "owner_id": s.owner_id, "owner": owner.name if owner else None, "review_days": s.review_days,
            "approved_by": s.approved_by, "approved_at": s.approved_at.isoformat() if s.approved_at else None,
            "proposed_by": s.proposed_by, "versions": len(versions),
            "latest_version": versions[-1].version if versions else None,
            "latest_version_id": versions[-1].id if versions else None,
            "obligations": {"candidate": sum(o.status == "candidate" for o in obs),
                            "approved": sum(o.status in ("open", "closed") for o in obs),
                            "superseded": sum(o.status == "superseded" for o in obs)},
            "review": review_state(db, s), "feed": feeds.health(db, s) if s.intake == "feed" else None,
            "alerts": feeds.summary(db, s) if s.kind in FEED_KINDS else None,
            "simulate_outage": s.simulate_outage, "pending_file": s.pending_file, "pending_version": s.pending_version}


# ---------- quarterly change summary ----------
def quarter_bounds(label: str | None) -> tuple[str, datetime, datetime]:
    now = clock.now()
    if label:
        try:
            y, q = label.upper().split("-Q")
            y, q = int(y), int(q)
            assert 1 <= q <= 4
        except (ValueError, AssertionError):
            raise ValueError("quarter must look like 2026-Q4")
    else:
        y, q = now.year, (now.month - 1) // 3 + 1
    start = datetime(y, 3 * (q - 1) + 1, 1)
    end = datetime(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
    return f"{y}-Q{q}", start, end


def quarterly_summary(db, quarter: str | None = None) -> dict:
    """Per source, what changed in the quarter, what was decided, and which sources nobody checked."""
    label, start, end = quarter_bounds(quarter)
    within = lambda d: d is not None and start <= d < end  # noqa: E731
    rows = []
    for s in db.scalars(select(Source).order_by(Source.id)):
        versions = [v for v in db.scalars(select(SourceVersion).where(SourceVersion.source_id == s.id))
                    if within(v.ingested_at) and v.summary and not v.summary.get("baseline")]
        counts = {k: sum((v.summary or {}).get(k, 0) for v in versions)
                  for k in ("changed", "new", "removed", "renumbered", "unchanged")}
        obs = [o for o in db.scalars(select(Obligation).where(Obligation.source_id == s.id, Obligation.is_test == False,  # noqa: E712
                                                              Obligation.kind != "advisory"))
               if within(o.created_at)]
        checks = [c for c in db.scalars(select(Claim).where(Claim.claim_type == "source_currency", Claim.source_id == s.id))
                  if within(c.evidence_ts)]
        items = [i for i in db.scalars(select(FeedItem).where(FeedItem.source_id == s.id, FeedItem.is_canary == False))  # noqa: E712
                 if within(i.received_at)]
        rs = review_state(db, s)
        changed = bool(versions) or any(i.outcome in ("ingested", "merged", "conflict") for i in items)
        rows.append({
            "source": s.name, "kind": s.kind, "jurisdiction": s.jurisdiction, "intake": s.intake, "status": s.status,
            "versions_seen": len(versions), "versions": [v.version for v in versions], **counts,
            "candidate_obligations": sum(o.status == "candidate" for o in obs),
            "approved_obligations": sum(o.status in ("open", "closed") for o in obs),
            "rejected_obligations": sum(o.status == "rejected" for o in obs),
            "superseded_obligations": sum(o.status == "superseded" for o in obs),
            "checks": len(checks), "checked_no_change": sum(c.value == "checked_no_change" for c in checks),
            "advisories_ingested": sum(i.outcome == "ingested" for i in items),
            "advisories_merged": sum(i.outcome in ("merged", "conflict") for i in items),
            "advisories_skipped": sum(i.outcome == "skipped" for i in items),
            "review_status": rs["status"], "last_checked": rs["last_checked"], "has_changes": changed,
        })
    approved = [r for r in rows if r["status"] == "approved"]
    return {"quarter": label, "start": start.isoformat(), "end": end.isoformat(), "generated_at": clock.now().isoformat(),
            "sources": rows,
            "unchanged_sources": [r["source"] for r in approved if not r["has_changes"]],
            "checked_no_change": [r["source"] for r in approved if not r["has_changes"] and r["checks"]],
            "not_checked": [r["source"] for r in approved if r["intake"] == "manual" and not r["checks"]
                            and not r["versions_seen"]],
            "overdue_reviews": [r["source"] for r in approved if r["review_status"] == "overdue"]}


CSV_COLUMNS = ["source", "kind", "jurisdiction", "intake", "versions_seen", "changed", "new", "removed", "renumbered",
               "unchanged", "candidate_obligations", "approved_obligations", "rejected_obligations",
               "superseded_obligations", "checks", "advisories_ingested", "advisories_merged", "advisories_skipped",
               "review_status", "last_checked"]


def quarterly_csv(db, quarter: str | None = None) -> tuple[str, str]:
    s = quarterly_summary(db, quarter)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in s["sources"]:
        w.writerow(r)
    return s["quarter"], buf.getvalue()


def stale_sources(db) -> set[int]:
    """Sources whose currency claim has lapsed: obligations from them may rest on outdated wording."""
    return {s.id for s in db.scalars(select(Source)) if review_state(db, s)["status"] == "overdue"}


def obligations_depending(db, claim: Claim) -> list[int]:
    return [d.dependent_id for d in db.scalars(select(Dependency).where(Dependency.claim_id == claim.id,
                                                                         Dependency.dependent_type == "obligation"))]


def default_owner(db, kind: str) -> Person | None:
    return first_with_role(db, {"regulator": "legal", "contract": "legal", "policy": "ciso"}.get(kind, "compliance"))
