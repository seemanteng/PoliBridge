"""Component 2: source intake, change detection, guidance gate."""
import re
from datetime import datetime

from sqlalchemy import select

from ..clock import clock
from ..models import Source, SourceVersion, Advisory, Obligation, Person
from . import audit
from .assessment import assess_obligation


def ingest_advisory(db, *, cve: str, product: str, affected_versions: list[str], cvss: float | None,
                    kev: bool = False, fixed_version: str | None = None, source_name: str = "CISA/NVD",
                    is_canary: bool = False, is_test: bool = False) -> Obligation:
    """Fast path (C1): advisories bypass the Compliance/Legal gate and are matched at once."""
    src = db.scalar(select(Source).where(Source.name == source_name, Source.kind == "advisory"))
    if src is None:
        raise ValueError(f"'{source_name}' is not in the approved-source registry")
    adv = Advisory(cve=cve, source_id=src.id, product=product, affected_versions=affected_versions, cvss=cvss,
                   kev=kev, fixed_version=fixed_version, published_at=clock.now(), is_canary=is_canary)
    db.add(adv)
    db.flush()
    ob = Obligation(kind="advisory", title=f"{cve}: {product}", advisory_id=adv.id, source_id=src.id,
                    source_ref=cve, status="open", is_test=is_test or is_canary, created_at=clock.now(),
                    required_action=f"Patch {product}" + (f" to {fixed_version}" if fixed_version else ""))
    db.add(ob)
    db.flush()
    audit.log(db, "system", "obligation", ob.id, "created_from_advisory", cve=cve, source=src.name)
    assess_obligation(db, ob)
    return ob


def _clauses(text: str) -> dict[str, str]:
    """Split into referenced clauses: lines like '§314.4(c) text' or 'Para N' fallbacks."""
    out = {}
    for i, line in enumerate([l.strip() for l in text.splitlines() if l.strip()], 1):
        m = re.match(r"^(§?\s?[\d.]+(?:\([a-z0-9]+\))*)\s+(.*)$", line)
        out[m.group(1).strip() if m else f"line {i}"] = line
    return out


def ingest_source_version(db, source: Source, version: str, text: str) -> dict:
    """Compare with the previous version; changed clauses become *candidate* obligations
    with exact quotations. Unchanged sources are recorded for the quarterly summary."""
    prev = db.scalar(select(SourceVersion).where(SourceVersion.source_id == source.id)
                     .order_by(SourceVersion.id.desc()))
    db.add(SourceVersion(source_id=source.id, version=version, text=text, ingested_at=clock.now()))
    old = _clauses(prev.text) if prev else {}
    new = _clauses(text)
    changed = {ref: q for ref, q in new.items() if old.get(ref) != q}
    candidates = []
    for ref, quote in changed.items():
        ob = Obligation(kind="regulatory" if source.kind != "policy" else "policy",
                        title=f"{source.name} {ref}", source_id=source.id, source_ref=ref, quotation=quote,
                        status="candidate", created_at=clock.now(),
                        required_action="To be confirmed by Compliance/Legal")
        db.add(ob)
        candidates.append(ob)
    db.flush()
    for ob in candidates:
        audit.log(db, "system", "obligation", ob.id, "candidate_created", ref=ob.source_ref)
    return {"source": source.name, "version": version, "changed": len(changed), "unchanged": len(new) - len(changed),
            "candidates": [o.id for o in candidates]}


def decide_candidate(db, ob: Obligation, approver: Person, approve: bool, applicability: str | None = None,
                     required_action: str | None = None):
    """Materiality and applicability of regulatory obligations is a human decision."""
    if approver.role not in ("compliance", "legal"):
        raise PermissionError("only Compliance or Legal can decide regulatory candidates")
    if ob.status != "candidate":
        raise ValueError("not a candidate")
    ob.approved_by_id = approver.id
    ob.status = "open" if approve else "rejected"
    ob.applicability_decision = applicability
    if required_action:
        ob.required_action = required_action
    audit.log(db, approver.directory_id, "obligation", ob.id, "approved" if approve else "rejected",
              applicability=applicability)


def quarterly_summary(db) -> dict:
    sources = list(db.scalars(select(Source)))
    rows = []
    for s in sources:
        versions = list(db.scalars(select(SourceVersion).where(SourceVersion.source_id == s.id)
                                   .order_by(SourceVersion.id.desc())))
        obs = list(db.scalars(select(Obligation).where(Obligation.source_id == s.id,
                                                        Obligation.kind != "advisory")))
        rows.append({"source": s.name, "kind": s.kind, "versions_seen": len(versions),
                     "candidate_obligations": sum(o.status == "candidate" for o in obs),
                     "approved_obligations": sum(o.status in ("open", "closed") for o in obs),
                     "changed": bool(obs)})
    return {"generated_at": str(clock.now()), "sources": rows,
            "unchanged_sources": [r["source"] for r in rows if not r["changed"]]}
