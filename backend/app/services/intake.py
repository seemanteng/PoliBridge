"""Component 2: source intake, change detection, guidance gate."""
import hashlib
import re

from sqlalchemy import select

from ..clock import clock, add_working_days
from ..config import APPROVALS
from ..models import Source, SourceVersion, Advisory, Obligation, Person, BusinessUnit, Guidance
from ..rules import versions
from . import audit, sources as S
from .assessment import assess_obligation
from .common import notify, raise_finding, first_with_role


def _approved_advisory_source(db, name: str) -> Source:
    src = db.scalar(select(Source).where(Source.name == name, Source.kind.in_(S.FEED_KINDS)))
    if src is None:
        raise ValueError(f"'{name}' is not in the approved-source registry")
    S.require_approved(src)
    return src


def ingest_advisory(db, *, cve: str, product: str, affected_versions: list[str], cvss: float | None,
                    kev: bool = False, fixed_version: str | None = None, source_name: str = "CISA/NVD",
                    description: str | None = None, is_canary: bool = False, is_test: bool = False,
                    published_at=None, received_at=None) -> Obligation:
    """Fast path (C1): advisories bypass the Compliance/Legal gate and are matched at once. A CVE already ingested
    from another approved source is merged into the same obligation, so owners never get two tasks for one flaw."""
    src = _approved_advisory_source(db, source_name)
    now = clock.now()
    entry = {"source": src.name, "received_at": (received_at or now).isoformat(),
             "published_at": (published_at or now).isoformat(), "affected_versions": list(affected_versions),
             "cvss": cvss, "kev": kev}
    if not is_canary:
        existing = db.scalar(select(Advisory).where(Advisory.cve == cve, Advisory.is_canary == False))  # noqa: E712
        if existing is not None:
            return _merge(db, existing, src, entry, fixed_version=fixed_version, description=description)
    adv = Advisory(cve=cve, source_id=src.id, product=product, affected_versions=affected_versions, cvss=cvss,
                   kev=kev, fixed_version=fixed_version, description=description, is_canary=is_canary,
                   published_at=published_at or now, received_at=received_at or now, ingested_at=now, sources=[entry])
    db.add(adv)
    db.flush()
    ob = Obligation(kind="advisory", title=f"{cve}: {product}", advisory_id=adv.id, source_id=src.id,
                    source_ref=cve, status="open", is_test=is_test or is_canary, created_at=now,
                    required_action=f"Patch {product}" + (f" to {fixed_version}" if fixed_version else ""))
    db.add(ob)
    db.flush()
    audit.log(db, "system", "obligation", ob.id, "created_from_advisory", cve=cve, source=src.name)
    if is_canary:  # the canary is matched against its one target asset by the self-test, not the whole estate
        return ob
    assess_obligation(db, ob)  # each task created here gets guidance for its own system
    from . import rag
    rag.generate_guidance(db, ob, "system owner")  # and the fix in general, on screen straight away
    return ob


def _merge(db, adv: Advisory, src: Source, entry: dict, *, fixed_version, description) -> Obligation:
    ob = db.scalar(select(Obligation).where(Obligation.advisory_id == adv.id))
    if any(e["source"] == src.name for e in adv.sources or []):
        audit.log(db, "system", "advisory", adv.id, "duplicate_ignored", source=src.name)
        return ob
    adv.sources = [*(adv.sources or []), entry]
    reassess = False
    if entry["cvss"] is not None and (adv.cvss is None or entry["cvss"] > adv.cvss):
        adv.cvss = entry["cvss"]  # the more severe score stands; unknown never lowers it
    adv.kev = adv.kev or entry["kev"]
    adv.fixed_version = adv.fixed_version or fixed_version
    adv.description = adv.description or description
    pub = entry["published_at"]
    if pub and pub < adv.published_at.isoformat():
        adv.published_at = adv.published_at.fromisoformat(pub)
    if not versions.same_ranges(adv.affected_versions, entry["affected_versions"]):
        first = adv.sources[0]
        adv.conflict = (f"{first['source']} lists {', '.join(first['affected_versions'])}; {src.name} lists "
                        f"{', '.join(entry['affected_versions'])}. Both ranges are matched until someone decides.")
        adv.affected_versions = [*adv.affected_versions, *[v for v in entry["affected_versions"]
                                                          if v not in adv.affected_versions]]
        to = first_with_role(db, APPROVALS["advisory"]["withheld_to"])
        raise_finding(db, "advisory_conflict", "High", f"{adv.cve}: sources disagree on affected versions",
                      adv.conflict, assigned_to=to)
        reassess = True
    audit.log(db, "system", "advisory", adv.id, "corroborated", source=src.name, conflict=bool(adv.conflict))
    if reassess and ob.status != "closed":
        assess_obligation(db, ob)
    return ob


# ---------- change detection ----------
REF = re.compile(r"^((?:§\s?[\w.\-]+|\d+(?:\.\d+)*|(?:principle|para|paragraph|clause|section|article)\s+\d+(?:\.\d+)*)"
                 r"(?:\([a-z0-9]+\))*)\s+(.*)$", re.I)
STOP = {"that", "this", "with", "your", "such", "shall", "from", "have", "which", "those", "these", "under", "other",
        "than", "their", "will", "into", "each", "must", "including", "also", "where", "when", "been", "being"}


def _norm(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) >= 4 and w not in STOP}


def _similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return 2 * len(wa & wb) / max(1, len(wa) + len(wb))


def parse_clauses(text: str) -> tuple[dict[str, str], dict[str, str], int]:
    """{ref: whole line}, {ref: body without the reference}, and how many lines had no clause number. Unnumbered
    lines are keyed by their first words, which stays stable while the line is unchanged."""
    clauses, bodies, unnumbered = {}, {}, 0
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        m = REF.match(line)
        if m:
            ref, body = re.sub(r"\s+", "", m.group(1)) if m.group(1).startswith("§") else m.group(1).strip(), m.group(2)
        else:
            unnumbered += 1
            ref, body = "¶ " + " ".join(line.split()[:6]), line
        base, n = ref, 1
        while ref in clauses:
            n += 1
            ref = f"{base} ({n})"
        clauses[ref], bodies[ref] = line, body
    return clauses, bodies, unnumbered


def diff_clauses(old_text: str | None, new_text: str) -> dict:
    """Clause-level comparison. Same number and wording: unchanged. Same number, new wording: changed. Wording found
    under a different number: renumbered (no new obligation). Reworded and renumbered: moved. Then new and removed."""
    old, old_b, _ = parse_clauses(old_text or "")
    new, new_b, unnumbered = parse_clauses(new_text)
    changes, unchanged = [], 0
    old_left, new_left = dict(old_b), {}
    for ref, body in new_b.items():
        if ref in old_left:
            if _norm(old_left[ref]) == _norm(body):
                old_left.pop(ref)
                unchanged += 1
            elif _similar(old_left[ref], body) >= 0.2:
                changes.append({"kind": "changed", "ref": ref, "old_ref": ref, "old": old[ref], "new": new[ref]})
                old_left.pop(ref)
            else:
                new_left[ref] = body  # the number was reused for different content
        else:
            new_left[ref] = body
    pairs = sorted(((1.0 if _norm(o) == _norm(n) else _similar(o, n), nr, orf)
                    for nr, n in new_left.items() for orf, o in old_left.items()), reverse=True)
    for score, nr, orf in pairs:
        if score < 0.45 or nr not in new_left or orf not in old_left:
            continue
        same = _norm(old_left[orf]) == _norm(new_left[nr])
        both_unnumbered = nr.startswith("¶") and orf.startswith("¶")
        kind = "changed" if both_unnumbered else "renumbered" if same else "moved"
        changes.append({"kind": kind, "ref": nr, "old_ref": orf, "old": old[orf], "new": new[nr]})
        new_left.pop(nr)
        old_left.pop(orf)
    changes += [{"kind": "new", "ref": r, "old_ref": None, "old": None, "new": new[r]} for r in new_left]
    changes += [{"kind": "removed", "ref": r, "old_ref": r, "old": old[r], "new": None} for r in old_left]
    order = {r: i for i, r in enumerate(list(new) + list(old))}
    changes.sort(key=lambda c: order.get(c["ref"], 0))
    warnings = []
    if unnumbered:
        warnings.append(f"{unnumbered} line{'s' if unnumbered > 1 else ''} had no clause number, so "
                        f"{'they were' if unnumbered > 1 else 'it was'} matched by wording instead.")
    count = lambda k: sum(c["kind"] == k for c in changes)  # noqa: E731
    return {"clauses": len(new), "unchanged": unchanged, "changed": count("changed") + count("moved"),
            "new": count("new"), "removed": count("removed"), "renumbered": count("renumbered"),
            "changes": changes, "warnings": warnings}


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _latest_obligation(db, source: Source, ref: str) -> Obligation | None:
    return db.scalar(select(Obligation).where(Obligation.source_id == source.id, Obligation.source_ref == ref,
                                              Obligation.kind != "advisory",
                                              Obligation.status.in_(("candidate", "open", "closed")))
                     .order_by(Obligation.id.desc()))


def supersede(db, old: Obligation, reason: str, by: Obligation | None = None):
    """The wording an obligation rests on has changed or gone. Departments that signed it are told to sign again
    once the new wording is approved, and its guidance is withdrawn (F2 applied to obligations)."""
    was = old.status
    old.status, old.superseded_reason = "superseded", reason
    for g in db.scalars(select(Guidance).where(Guidance.obligation_id == old.id,
                                               Guidance.status.in_(("draft", "pending_approval", "released")))):
        g.status = "superseded"
    audit.log(db, "system", "obligation", old.id, "superseded", reason=reason, by=by.id if by else None, was=was)
    for d in old.departments or []:
        if d.get("signed_at"):
            p = db.scalar(select(Person).where(Person.name == d["signed_by"]))
            notify(db, person=p, kind="signoff_reopened",
                   message=f"{old.title}: the wording {d['department']} signed off has {reason.lower()}. "
                           "Sign off again once the new wording is approved.")
    if was in ("open", "closed") and by is None:
        raise_finding(db, "clause_removed", "Medium", f"{old.title} removed from the source",
                      f"OBL-{old.id:04d} was approved against wording that is no longer in the source. Confirm it can be retired.",
                      assigned_to=first_with_role(db, "compliance"))


def ingest_source_version(db, source: Source, version: str, text: str, *, file_name: str | None = None,
                          retrieved_from: str | None = None, effective_date: str | None = None,
                          uploaded_by: Person | None = None) -> dict:
    """Compare with the previous version; changed clauses become *candidate* obligations with exact quotations and
    supersede the obligations built on the old wording. An identical text is recorded as a check, not a version."""
    S.require_approved(source)
    if source.kind in S.FEED_KINDS:
        raise ValueError(f"'{source.name}' is an advisory feed; its advisories arrive through the feed")
    prev = db.scalar(select(SourceVersion).where(SourceVersion.source_id == source.id)
                     .order_by(SourceVersion.id.desc()))
    digest = fingerprint(text)
    if prev and _norm(prev.text) == _norm(text):
        S.record_check(db, source, uploaded_by, how="identical_upload", version=prev,
                       note=f"Uploaded text identical to version {prev.version}")
        clauses, _, _ = parse_clauses(text)
        return {"source": source.name, "version": prev.version, "identical": True, "changed": 0,
                "unchanged": len(clauses), "new": 0, "removed": [], "renumbered": 0, "candidates": [], "changes": [],
                "warnings": [], "sha256": digest}
    d = diff_clauses(prev.text if prev else None, text)
    sv = SourceVersion(source_id=source.id, version=version, text=text, ingested_at=clock.now(), sha256=digest,
                       file_name=file_name, retrieved_from=retrieved_from, effective_date=effective_date,
                       uploaded_by=uploaded_by.name if uploaded_by else None)
    db.add(sv)
    db.flush()
    candidates, rows = [], []
    # look up the obligations on the old wording before creating any, so a new candidate is never mistaken for one
    olds = {c["old_ref"]: _latest_obligation(db, source, c["old_ref"]) for c in d["changes"] if c["old_ref"]}
    for c in d["changes"]:
        old_ob = olds.get(c["old_ref"]) if c["old_ref"] else None
        if c["kind"] == "renumbered":
            if old_ob:
                old_ob.source_ref, old_ob.title = c["ref"], f"{source.name} {c['ref']}"
                audit.log(db, "system", "obligation", old_ob.id, "renumbered", old=c["old_ref"], new=c["ref"])
            rows.append({**c, "obligation_id": old_ob.id if old_ob else None, "departments": []})
            continue
        if c["kind"] == "removed":
            if old_ob:
                supersede(db, old_ob, f"Removed from {source.name} in version {version}")
            rows.append({**c, "obligation_id": old_ob.id if old_ob else None, "departments": []})
            continue
        ob = Obligation(kind="regulatory" if source.kind != "policy" else "policy",
                        title=f"{source.name} {c['ref']}", source_id=source.id, source_ref=c["ref"],
                        quotation=c["new"], previous_quotation=c["old"],
                        departments=suggest_departments(db, c["new"]), status="candidate", created_at=clock.now(),
                        source_version_id=sv.id, required_action="To be confirmed by Compliance/Legal")
        db.add(ob)
        db.flush()
        if old_ob:
            ob.supersedes_id = old_ob.id
            what = "Changed" if c["kind"] == "changed" else f"Renumbered to {c['ref']} and changed"
            supersede(db, old_ob, f"{what} in version {version}", by=ob)
        audit.log(db, "system", "obligation", ob.id, "candidate_created", ref=ob.source_ref, kind=c["kind"])
        candidates.append(ob)
        rows.append({**c, "obligation_id": ob.id, "supersedes": old_ob.id if old_ob else None,
                     "departments": [x["department"] for x in ob.departments or []]})
    sv.summary = {k: d[k] for k in ("clauses", "changed", "new", "removed", "renumbered", "unchanged", "warnings")}
    sv.summary["changes"] = [{k: r.get(k) for k in ("kind", "ref", "old_ref", "old", "new", "obligation_id", "supersedes")}
                             for r in rows]
    S.record_check(db, source, uploaded_by, how="new_version", version=sv)
    audit.log(db, uploaded_by.directory_id if uploaded_by else "system", "source", source.id, "version_ingested",
              version=version, sha256=digest, changed=d["changed"], new=d["new"], removed=d["removed"])
    return {"source": source.name, "version": version, "version_id": sv.id, "identical": False,
            "summary": {k: v for k, v in sv.summary.items() if k != "changes"},
            "changed": len(candidates), "unchanged": d["unchanged"], "new": d["new"], "renumbered": d["renumbered"],
            "removed": [c["ref"] for c in d["changes"] if c["kind"] == "removed"], "candidates": [o.id for o in candidates],
            "changes": [{"obligation_id": r.get("obligation_id"), "ref": r["ref"], "old_ref": r["old_ref"],
                         "kind": r["kind"], "old": r["old"], "new": r["new"], "supersedes": r.get("supersedes"),
                         "departments": r["departments"]} for r in rows],
            "warnings": d["warnings"], "sha256": digest}


def preview_source_version(db, source: Source, text: str) -> dict:
    """What ingesting this text would do, without saving anything."""
    prev = db.scalar(select(SourceVersion).where(SourceVersion.source_id == source.id)
                     .order_by(SourceVersion.id.desc()))
    clauses, _, _ = parse_clauses(text)
    if prev and _norm(prev.text) == _norm(text):
        return {"identical": True, "previous": prev.version, "clauses": len(clauses), "changes": [], "warnings": [],
                "sha256": fingerprint(text)}
    d = diff_clauses(prev.text if prev else None, text)
    for c in d["changes"]:
        old_ob = _latest_obligation(db, source, c["old_ref"]) if c["old_ref"] else None
        c["affects"] = (f"OBL-{old_ob.id:04d} ({old_ob.status})" if old_ob else None)
    return {**d, "identical": False, "previous": prev.version if prev else None, "sha256": fingerprint(text)}


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
        labels = {d["label"] for d in scope}
        applicability = applicability or ("applies" if "applies" in labels else
                                          "cannot_determine" if "cannot_determine" in labels else "does_not_apply")
    ob.approved_by_id = approver.id
    ob.status = "open" if approve else "rejected"
    ob.applicability_decision = applicability
    if required_action:
        ob.required_action = required_action
    if approve:  # each department head signs off within the window approvals.yaml sets
        due = add_working_days(clock.now(), APPROVALS["signoff"]["working_days"])
        for d in scope:
            d["due"] = due.isoformat()
    ob.departments = scope
    audit.log(db, approver.directory_id, "obligation", ob.id, "approved" if approve else "rejected",
              applicability=applicability)
    if approve:
        from . import rag
        src = db.get(Source, ob.source_id) if ob.source_id else None
        if src:
            c = S.current_check(db, src)
            if c and c.status == "active":
                from .common import add_dependency
                add_dependency(db, c, "obligation", ob.id)
        units = {b.name: b for b in db.scalars(select(BusinessUnit))}
        for d in scope:
            if d.get("label") in ("applies", "cannot_determine") and d["department"] in units:
                head = db.get(Person, units[d["department"]].head_id) if units[d["department"]].head_id else None
                rag.guidance_for_department(db, ob, d["department"], head)


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


def quarterly_summary(db, quarter: str | None = None) -> dict:
    return S.quarterly_summary(db, quarter)
