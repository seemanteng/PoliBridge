"""Component 2: source intake, change detection, guidance gate."""
import re
from datetime import datetime

from sqlalchemy import select

from ..clock import clock
from ..models import Source, SourceVersion, Advisory, Obligation, Person, BusinessUnit
from . import audit
from .assessment import assess_obligation


def ingest_advisory(db, *, cve: str, product: str, affected_versions: list[str], cvss: float | None,
                    kev: bool = False, fixed_version: str | None = None, source_name: str = "CISA/NVD",
                    description: str | None = None, is_canary: bool = False, is_test: bool = False) -> Obligation:
    """Fast path (C1): advisories bypass the Compliance/Legal gate and are matched at once."""
    src = db.scalar(select(Source).where(Source.name == source_name, Source.kind == "advisory"))
    if src is None:
        raise ValueError(f"'{source_name}' is not in the approved-source registry")
    adv = Advisory(cve=cve, source_id=src.id, product=product, affected_versions=affected_versions, cvss=cvss,
                   kev=kev, fixed_version=fixed_version, description=description, published_at=clock.now(), is_canary=is_canary)
    db.add(adv)
    db.flush()
    ob = Obligation(kind="advisory", title=f"{cve}: {product}", advisory_id=adv.id, source_id=src.id,
                    source_ref=cve, status="open", is_test=is_test or is_canary, created_at=clock.now(),
                    required_action=f"Patch {product}" + (f" to {fixed_version}" if fixed_version else ""))
    db.add(ob)
    db.flush()
    audit.log(db, "system", "obligation", ob.id, "created_from_advisory", cve=cve, source=src.name)
    assess_obligation(db, ob)
    if not ob.is_test:  # the required fix is on screen straight away, not drafted on request
        from . import rag
        rag.generate_guidance(db, ob, "system owner")
    return ob


def _clauses(text: str) -> dict[str, str]:
    """Split into referenced clauses: lines like '§314.4(c) text' or 'Para N' fallbacks."""
    out = {}
    for i, line in enumerate([l.strip() for l in text.splitlines() if l.strip()], 1):
        m = re.match(r"^(§?\s?[\d.]+(?:\([a-z0-9]+\))*)\s+(.*)$", line)
        out[m.group(1).strip() if m else f"line {i}"] = line
    return out


# Which departments a clause is likely to involve, by the words it uses. This only *suggests* scope with the
# reason shown; Compliance/Legal decide the label for each department (applies / does not apply / cannot determine).
DEPT_KEYWORDS = {
    "Information Security": ["information security", "encrypt", "multi-factor", "authenticat", "access control", "penetration",
                             "vulnerab", "incident", "security event", "intrusion", "safeguard", "secure development", "systems"],
    "Risk & Compliance": ["risk assessment", "risk", "compliance", "periodically", "written", "evaluate", "program"],
    "Legal": ["contract", "service provider", "affiliate", "notify", "notification", "law", "regulation", "breach"],
    "Internal Audit": ["regularly test", "monitor", "audit", "effectiveness", "review"],
    "Executive Office": ["board of directors", "governing body", "qualified individual", "senior member", "designate"],
    "Consumer Services": ["customer information", "disposal", "dispose", "retention", "personnel", "training", "applications"],
}


def suggest_departments(db, text: str) -> list[dict]:
    low = text.lower()
    units = {b.name: b for b in db.scalars(select(BusinessUnit))}
    found = []
    for dept, words in DEPT_KEYWORDS.items():
        hits = [w for w in words if w in low]
        if hits and dept in units:
            found.append((len(hits), dept, hits))
    found.sort(key=lambda x: -x[0])
    if not found and "Risk & Compliance" in units:
        found = [(0, "Risk & Compliance", [])]
    out = []
    for _, dept, hits in found[:4]:
        head = db.get(Person, units[dept].head_id) if units[dept].head_id else None
        out.append({"department": dept, "owner": head.name if head else None,
                    "reason": ("mentions: " + ", ".join(hits[:4])) if hits else "no specific keyword; Compliance triages",
                    "label": "cannot_determine", "decided_by": None, "note": None})
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
    removed = [ref for ref in old if ref not in new]
    candidates = []
    for ref, quote in changed.items():
        ob = Obligation(kind="regulatory" if source.kind != "policy" else "policy",
                        title=f"{source.name} {ref}", source_id=source.id, source_ref=ref, quotation=quote,
                        previous_quotation=old.get(ref), departments=suggest_departments(db, quote),
                        status="candidate", created_at=clock.now(),
                        required_action="To be confirmed by Compliance/Legal")
        db.add(ob)
        candidates.append(ob)
    db.flush()
    for ob in candidates:
        audit.log(db, "system", "obligation", ob.id, "candidate_created", ref=ob.source_ref)
    return {"source": source.name, "version": version, "changed": len(changed), "unchanged": len(new) - len(changed),
            "removed": removed, "candidates": [o.id for o in candidates],
            "changes": [{"obligation_id": o.id, "ref": o.source_ref, "kind": "changed" if o.previous_quotation else "new",
                         "old": o.previous_quotation, "new": o.quotation,
                         "departments": [d["department"] for d in o.departments or []]} for o in candidates]}


LABELS = ("applies", "does_not_apply", "cannot_determine")


def decide_candidate(db, ob: Obligation, approver: Person, approve: bool, applicability: str | None = None,
                     required_action: str | None = None, departments: list[dict] | None = None):
    """Materiality and applicability of regulatory obligations is a human decision."""
    if approver.role not in ("compliance", "legal"):
        raise PermissionError("only Compliance or Legal can decide regulatory candidates")
    if ob.status != "candidate":
        raise ValueError("not a candidate")
    scope = [dict(d) for d in (ob.departments or [])]
    for dec in departments or []:
        row = next((d for d in scope if d["department"] == dec.get("department")), None)
        if row is None:
            raise ValueError(f"{dec.get('department')} is not a department involved in this clause")
        if dec.get("label") not in LABELS:
            raise ValueError(f"label for {row['department']} must be one of {', '.join(LABELS)}")
        if dec["label"] == "does_not_apply" and not (dec.get("note") or "").strip():
            raise ValueError(f"A5: Does Not Apply for {row['department']} needs a recorded reason")
        row.update(label=dec["label"], note=(dec.get("note") or None), decided_by=approver.name)
    if departments:
        ob.departments = scope
        labels = {d["label"] for d in scope}
        applicability = applicability or ("applies" if "applies" in labels else
                                          "cannot_determine" if "cannot_determine" in labels else "does_not_apply")
    ob.approved_by_id = approver.id
    ob.status = "open" if approve else "rejected"
    ob.applicability_decision = applicability
    if required_action:
        ob.required_action = required_action
    audit.log(db, approver.directory_id, "obligation", ob.id, "approved" if approve else "rejected",
              applicability=applicability)


def signoff_department(db, ob: Obligation, person: Person, department: str):
    """The department's owner signs off that they have seen and accepted the label on an approved clause."""
    if ob.status not in ("open", "closed"):
        raise ValueError("a clause can be signed off only after Compliance or Legal approve it")
    scope = [dict(d) for d in (ob.departments or [])]
    row = next((d for d in scope if d["department"] == department), None)
    if row is None:
        raise ValueError(f"{department} is not involved in this clause")
    if person.name != row.get("owner"):
        raise PermissionError(f"only {row.get('owner')}, who owns {department}, can sign off for it")
    if row.get("signed_at"):
        raise ValueError(f"{department} has already signed off")
    row.update(signed_by=person.name, signed_at=clock.now().isoformat())
    ob.departments = scope
    audit.log(db, person.directory_id, "obligation", ob.id, "signed_off", department=department, label=row.get("label"))


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
