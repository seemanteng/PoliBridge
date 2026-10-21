from sqlalchemy import select

from app.models import Source, Obligation
from app.services import intake, rag, closure
from .conftest import STRUTS


def src(db, name):
    return db.scalar(select(Source).where(Source.name == name))


def test_unapproved_source_rejected(db):
    try:
        intake.ingest_advisory(db, **{**STRUTS, "source_name": "random-blog"})
        assert False
    except ValueError as e:
        assert "approved-source" in str(e)


def test_regulatory_diff_creates_candidates_with_exact_quotes(db, who):
    s = src(db, "FTC Safeguards Rule")
    v2 = ("§314.4(a) Designate an employee to coordinate the information security program.\n"
          "§314.4(c)(2) Review access controls and encrypt customer information within 30 days of change.\n"
          "§314.4(h) Notify the FTC within 30 days of a breach affecting 500 consumers.")
    r = intake.ingest_source_version(db, s, "2023", v2)
    assert r["changed"] == 2 and r["unchanged"] == 1
    cands = [db.get(Obligation, i) for i in r["candidates"]]
    assert all(c.status == "candidate" and c.quotation in v2 for c in cands)
    assert {c.source_ref for c in cands} == {"§314.4(c)(2)", "§314.4(h)"}
    assert "FTC Safeguards Rule" not in intake.quarterly_summary(db)["unchanged_sources"]


def test_only_compliance_or_legal_can_approve(db, who):
    s = src(db, "FTC Safeguards Rule")
    r = intake.ingest_source_version(db, s, "2", "§1.1 New clause")
    ob = db.get(Obligation, r["candidates"][0])
    try:
        intake.decide_candidate(db, ob, who("atan"), True)
        assert False
    except PermissionError:
        pass
    intake.decide_candidate(db, ob, who("pnair"), True, applicability="applies")
    assert ob.status == "open" and ob.applicability_decision == "applies"


def test_regulatory_obligation_cannot_close_unapproved(db):
    s = src(db, "FTC Safeguards Rule")
    r = intake.ingest_source_version(db, s, "2", "§1.1 New clause")
    ok, blockers = closure.try_close_obligation(db, db.get(Obligation, r["candidates"][0]))
    assert not ok


def test_rag_grounded_guidance(db):
    ob = intake.ingest_advisory(db, **STRUTS)
    g = rag.generate_guidance(db, ob, "system owner")
    assert g.status == "draft" and g.grounded and g.citations


def test_rag_withholds_ungrounded_and_conflicting(db):
    ob = intake.ingest_advisory(db, **STRUTS)
    passages = rag._passages(db, ob)
    ok, why = rag.verify_grounding("Patch now", [{"source": "x", "quote": "invented sentence"}], passages)
    assert not ok and "not found" in why
    ok, _ = rag.verify_grounding("anything", [], passages)
    assert not ok
    # conflict: policy says 48 hours, regulatory clause says 30 days -> different units are fine,
    # but two 'within N hours' limits conflict
    s = src(db, "FTC Safeguards Rule")
    r = intake.ingest_source_version(db, s, "3", "§9.9 Patch within 72 hours of an advisory.")
    reg = db.get(Obligation, r["candidates"][0])
    g = rag.generate_guidance(db, reg, "system owner")
    assert g.status == "withheld" and "conflicting" in g.reason
