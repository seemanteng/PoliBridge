"""Source intake: approved-source registry, feeds, change detection, guidance approval and source decay."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.models import (Source, SourceVersion, FeedItem, Advisory, Obligation, Finding, Guidance, Claim, Task, Person,
                        Notification, BusinessUnit)
from app.services import approvals, assurance, closure, feeds, intake, rag, sources as S
from .conftest import STRUTS, sign_required


def src(db, name):
    return db.scalar(select(Source).where(Source.name == name))


def feed_item(db, source, **kw):
    fields = dict(cve="CVE-2017-5638", product="Apache Struts", affected_versions=["2.3.5-2.3.31"], cvss=10.0,
                  kev=True, published_at=clock.now() - timedelta(hours=1), available_at=clock.now())
    fields.update(kw)
    i = FeedItem(source_id=source.id, **fields)
    db.add(i)
    db.flush()
    return i


@pytest.fixture
def apache(db, who):
    s = Source(name="Apache bulletins", kind="vendor", intake="feed", status="approved", approved_at=clock.now(),
               created_at=clock.now(), owner_id=who("pnair").id)
    db.add(s)
    db.flush()
    return s


# ---------- registry ----------
def test_only_the_cco_approves_a_source_and_never_their_own_proposal(db, who):
    s = S.propose(db, name="State AG bulletins", kind="regulator", authority="State AG", jurisdiction="US state",
                  url=None, intake="manual", owner=who("lbrandt"), review_days=90, proposer=who("pnair"))
    assert s.status == "pending"
    with pytest.raises(ValueError):
        intake.ingest_source_version(db, s, "1", "§1 Text")
    with pytest.raises(PermissionError):
        S.approve(db, s, who("pnair"))
    S.approve(db, s, who("cmendes"))
    assert s.status == "approved"
    own = S.propose(db, name="CCO memo", kind="policy", authority=None, jurisdiction="Internal", url=None,
                    intake="manual", owner=None, review_days=30, proposer=who("cmendes"))
    with pytest.raises(PermissionError):
        S.approve(db, own, who("cmendes"))


def test_suspended_source_cannot_feed_obligations(db, who):
    S.suspend(db, src(db, "CISA/NVD"), who("pnair"), "feed compromised")
    with pytest.raises(ValueError, match="suspended"):
        intake.ingest_advisory(db, **STRUTS)


# ---------- feeds ----------
def test_feed_ingests_without_anyone_typing_it(db, apache):
    feed_item(db, apache)
    r = feeds.poll(db, apache)
    assert r["ok"] and r["received"] == ["CVE-2017-5638 · ingested"]
    adv = db.scalar(select(Advisory).where(Advisory.cve == "CVE-2017-5638"))
    assert adv.received_at == clock.now() and adv.published_at < adv.received_at
    assert db.scalar(select(Task.id).where(Task.obligation_id == db.scalar(
        select(Obligation.id).where(Obligation.advisory_id == adv.id))))


def test_low_severity_unwatched_advisory_is_logged_not_ingested(db, apache):
    i = feed_item(db, apache, cve="CVE-2020-11022", product="jquery", affected_versions=[">=1.2 <3.5.0"], cvss=6.1, kev=False)
    feeds.poll(db, apache)
    assert i.outcome == "skipped" and "below" in i.reason
    assert db.scalar(select(Advisory).where(Advisory.cve == "CVE-2020-11022")) is None
    # the same severity on watched software is ingested
    j = feed_item(db, apache, cve="CVE-2099-0001", product="nginx", affected_versions=["<1.11"], cvss=5.0, kev=False)
    feeds.poll(db, apache)
    assert j.outcome == "ingested" and "watchlist" in j.reason


def test_same_cve_from_two_sources_is_one_obligation(db, apache):
    feed_item(db, apache, cvss=None, kev=False)
    feeds.poll(db, apache)
    ob = intake.ingest_advisory(db, **{**STRUTS, "affected_versions": [">=2.3.5 <2.3.32"]})
    adv = db.get(Advisory, ob.advisory_id)
    assert [s["source"] for s in adv.sources] == ["Apache bulletins", "CISA/NVD"]
    assert adv.cvss == 10.0 and adv.kev and adv.conflict is None  # same versions, written differently
    assert db.scalar(select(Advisory.id).where(Advisory.cve == "CVE-2017-5638").offset(1)) is None
    assert len(db.scalars(select(Obligation).where(Obligation.kind == "advisory")).all()) == 1


def test_sources_disagreeing_on_versions_go_to_compliance(db, apache):
    feed_item(db, apache, affected_versions=["2.3.5-2.3.31"])
    feeds.poll(db, apache)
    ob = intake.ingest_advisory(db, **{**STRUTS, "affected_versions": ["2.3.5-2.3.31", "2.5-2.5.10"]})
    adv = db.get(Advisory, ob.advisory_id)
    assert adv.conflict and "2.5-2.5.10" in adv.affected_versions  # the wider range is matched meanwhile
    f = db.scalar(select(Finding).where(Finding.kind == "advisory_conflict"))
    assert f and db.get(Person, f.assigned_to_id).role == "compliance"


def test_silent_feed_raises_a_finding_and_recovers(db, apache):
    apache.simulate_outage = True
    for _ in range(3):
        assert not feeds.poll(db, apache)["ok"]
    assert feeds.check_health(db) == ["Apache bulletins"]
    f = db.scalar(select(Finding).where(Finding.kind == "feed_silent"))
    assert f.status == "open" and db.get(Person, f.assigned_to_id).directory_id == "pnair"
    apache.simulate_outage = False
    feeds.poll(db, apache)
    assert f.status == "resolved"


def test_canary_travels_through_the_feed_and_fails_when_it_is_down(db, asset):
    run = assurance.run_canary(db, asset("Credit Report Portal"))
    assert run.status == "running" and run.feed == "CISA/NVD"
    src(db, "CISA/NVD").simulate_outage = True
    run = assurance.run_canary(db, asset("Credit Report Portal"))
    assert run.status == "failed" and run.stage == "feed" and "never arrived" in run.failures[0]


# ---------- change detection ----------
FTC_2016 = """§314.4(a) Designate an employee or employees to coordinate your information security program.
§314.4(c) Design and implement information safeguards to control the risks you identify, and regularly test or otherwise monitor the effectiveness of the safeguards' key controls, systems, and procedures.
§314.4(d) Oversee service providers, by:
§314.4(d)(1) Taking reasonable steps to select and retain service providers that are capable of maintaining appropriate safeguards."""
FTC_2022 = """§314.4(a) Designate a qualified individual responsible for overseeing and implementing your information security program.
§314.4(d)(1) Regularly test or otherwise monitor the effectiveness of the safeguards' key controls, systems, and procedures, including those to detect attacks.
§314.4(f) Oversee service providers, by:
§314.4(h) Establish a written incident response plan."""


def test_diff_recognises_renumbered_moved_and_removed_clauses():
    d = intake.diff_clauses(FTC_2016, FTC_2022)
    kinds = {(c["kind"], c["old_ref"], c["ref"]) for c in d["changes"]}
    assert ("renumbered", "§314.4(d)", "§314.4(f)") in kinds
    assert ("moved", "§314.4(c)", "§314.4(d)(1)") in kinds
    assert ("changed", "§314.4(a)", "§314.4(a)") in kinds
    assert ("new", None, "§314.4(h)") in kinds
    assert ("removed", "§314.4(d)(1)", "§314.4(d)(1)") in kinds  # a reused number is not mistaken for a change


def test_unnumbered_lines_are_matched_by_wording():
    old = "Patch critical systems within 48 hours.\nOwners must acknowledge advisories."
    new = "Owners must acknowledge advisories.\nNew line about logging.\nPatch critical systems within 48 hours."
    d = intake.diff_clauses(old, new)
    assert d["unchanged"] == 2 and [c["kind"] for c in d["changes"]] == ["new"] and d["warnings"]


def test_new_version_supersedes_obligations_on_the_old_wording(db, who):
    s = src(db, "FTC Safeguards Rule")
    r1 = intake.ingest_source_version(db, s, "a", FTC_2016)
    for oid in r1["candidates"]:
        ob = db.get(Obligation, oid)
        intake.decide_candidate(db, ob, who("pnair"), True,
                                departments=[{"department": d["department"], "label": "applies"} for d in ob.departments])
    old_c = db.scalar(select(Obligation).where(Obligation.source_ref == "§314.4(c)"))
    old_d = db.scalar(select(Obligation).where(Obligation.source_ref == "§314.4(d)"))
    r2 = intake.ingest_source_version(db, s, "b", FTC_2022)
    assert old_c.status == "superseded" and "Renumbered" in old_c.superseded_reason
    new_d1 = db.scalar(select(Obligation).where(Obligation.source_ref == "§314.4(d)(1)", Obligation.status == "candidate"))
    assert new_d1.supersedes_id == old_c.id and new_d1.previous_quotation == old_c.quotation
    assert old_d.status == "open" and old_d.source_ref == "§314.4(f)"  # renumbered only: stays approved
    assert r2["summary"]["renumbered"] == 1
    v = db.get(SourceVersion, r2["version_id"])
    assert v.sha256 == intake.fingerprint(FTC_2022)


def test_identical_upload_is_a_check_not_a_version(db):
    s = src(db, "FTC Safeguards Rule")
    before = len(db.scalars(select(SourceVersion).where(SourceVersion.source_id == s.id)).all())
    current = db.scalar(select(SourceVersion).where(SourceVersion.source_id == s.id)).text
    r = intake.ingest_source_version(db, s, "again", current + "\n")
    assert r["identical"] and len(db.scalars(select(SourceVersion).where(SourceVersion.source_id == s.id)).all()) == before
    assert S.review_state(db, s)["status"] == "current"


# ---------- source decay ----------
def test_source_review_lapses_into_a_finding_until_rechecked(db, who):
    s = src(db, "FTC Safeguards Rule")
    s.owner_id, s.review_days = who("lbrandt").id, 30
    S.record_check(db, s, who("lbrandt"))
    assert S.review_state(db, s)["status"] == "current"
    clock.advance(days=31)
    assurance.sweep(db)
    f = db.scalar(select(Finding).where(Finding.kind == "source_review_overdue"))
    assert f and f.status == "open" and S.review_state(db, s)["status"] == "overdue"
    assert s.id in S.stale_sources(db)
    S.record_check(db, s, who("lbrandt"))
    assert f.status == "resolved" and S.review_state(db, s)["status"] == "current"


def test_quarterly_summary_is_bounded_to_the_quarter(db):
    s = src(db, "FTC Safeguards Rule")
    intake.ingest_source_version(db, s, "2", "§314.4(h) Notify the FTC within 30 days.")
    q = S.quarterly_summary(db)
    assert q["quarter"] == "2017-Q1" and "FTC Safeguards Rule" not in q["unchanged_sources"]
    assert "FTC Safeguards Rule" in S.quarterly_summary(db, "2017-Q2")["unchanged_sources"]
    with pytest.raises(ValueError):
        S.quarterly_summary(db, "Q5")
    assert S.quarterly_csv(db)[1].startswith("source,kind")


# ---------- guidance approval ----------
@pytest.fixture
def units(db, who):
    """The fixture organisation has one department; clause routing needs the rest, each with a head."""
    for name, head in (("Information Security", "crhodes"), ("Risk & Compliance", "cmendes"), ("Legal", "lbrandt"),
                       ("Executive Office", "swhitfield")):
        db.add(BusinessUnit(name=name, head_id=who(head).id))
    db.flush()


def _approve_clause(db, who, text, approver="pnair"):
    r = intake.ingest_source_version(db, src(db, "FTC Safeguards Rule"), "x", text)
    ob = db.get(Obligation, r["candidates"][-1])
    intake.decide_candidate(db, ob, who(approver), True, departments=[{"department": d["department"], "label": "applies"}
                                                                      for d in ob.departments])
    return ob


def test_clause_guidance_waits_for_the_topic_approver(db, who, units):
    ob = _approve_clause(db, who, "§314.4(c)(3) Protect by encryption all customer information held or transmitted.")
    gs = {g.department: g for g in db.scalars(select(Guidance).where(Guidance.obligation_id == ob.id,
                                                                     Guidance.department.is_not(None)))}
    g = next(x for d, x in gs.items() if d != "Information Security")
    assert g.status == "pending_approval" and g.topic == "security"
    assert db.get(Person, g.approver_id).directory_id == "pnair" and db.get(Person, g.consult_id).directory_id == "crhodes"
    assert gs["Information Security"].consult_id is None  # the CISO cannot check guidance addressed to them
    with pytest.raises(PermissionError):
        approvals.approve_guidance(db, g, who("lbrandt"))  # not the routed approver
    approvals.approve_guidance(db, g, who("pnair"))
    assert g.status == "released" and db.scalar(select(Notification).where(Notification.kind == "guidance_released"))


def test_approver_is_never_the_recipient(db, who, units):
    ob = _approve_clause(db, who, "§314.4(j) Notify the FTC of a breach affecting 500 consumers under the law.")
    rows = {d["department"]: d for d in ob.departments}
    assert "Legal" in rows
    g = db.scalar(select(Guidance).where(Guidance.obligation_id == ob.id, Guidance.department == "Legal"))
    assert g.topic == "legal" and db.get(Person, g.recipient_id).directory_id == "lbrandt"
    assert db.get(Person, g.approver_id).role == "cco"  # Legal would approve its own guidance, so the CCO steps in


def test_withheld_guidance_goes_to_compliance_and_can_be_written_by_hand(db, who, units):
    ob = _approve_clause(db, who, "§9.9 Patch within 72 hours of an advisory.")  # conflicts with the 48-hour policy
    g = db.scalar(select(Guidance).where(Guidance.obligation_id == ob.id, Guidance.department.is_not(None)))
    assert g.status == "withheld" and "conflicting" in g.reason
    assert db.scalar(select(Finding).where(Finding.kind == "guidance_withheld"))
    with pytest.raises(ValueError, match="A4"):
        rag.write_guidance(db, ob, who("pnair"), text="Do something", citations=[{"source": "x", "quote": "invented"}],
                           department=g.department)
    quote = "Patch within 72 hours of an advisory."
    w = rag.write_guidance(db, ob, who("pnair"), text=f"Policy and rule differ; follow the stricter: {quote}",
                           citations=[{"source": "§9.9", "quote": quote}], department=g.department)
    assert w.status == "released" and w.human_authored and g.status == "superseded"


def test_advisory_guidance_is_per_system_and_released_at_once(db):
    ob = intake.ingest_advisory(db, **STRUTS)
    gs = db.scalars(select(Guidance).where(Guidance.obligation_id == ob.id, Guidance.asset_id.is_not(None))).all()
    assert gs and all(g.status == "released" and g.consult_id for g in gs)
    portal = next(g for g in gs if "Credit Report Portal" in g.text)
    assert "2.3.31" in portal.text and "/opt/portal/**" in portal.text  # its own component record and evidence scope


# ---------- C2-C4 sign-offs ----------
def test_required_signoffs_hold_closure_and_respect_independence(db, who):
    ob = intake.ingest_advisory(db, **STRUTS)
    t = db.scalar(select(Task).where(Task.obligation_id == ob.id, Task.kind == "remediation"))
    assert set(t.required_reviewers) >= {"security", "system_owner", "compliance"}
    closure.mark_fixed(db, t, who("atan"))
    clock.advance(hours=1)
    ev = closure.submit_evidence(db, t, who("atan"), purpose="remediation", asset_name="Credit Report Portal",
                                 component="Apache Struts", scope=["/opt/portal/**"], method="authenticated_scan",
                                 observed_version="2.3.32", timestamp=clock.now())
    closure.review_evidence(db, ev, who("rverma"), True)
    assert t.status == "fixed_pending_review" and closure.missing_signoffs(t)
    with pytest.raises(PermissionError):
        closure.sign_required_review(db, t, "security", who("pnair"))  # the CISO's sign-off
    sign_required(db, t)
    assert t.status == "closed"


def test_late_clause_signoff_escalates_to_the_cco(db, who, units):
    ob = _approve_clause(db, who, "§314.4(i) Report in writing to your board of directors at least annually.")
    assert all(d.get("due") for d in ob.departments)
    clock.advance(days=8)
    late = assurance.sweep(db)["late_signoffs"]
    assert late and db.scalar(select(Notification).where(Notification.kind == "signoff_overdue")).person_id == who("cmendes").id
    assert assurance.sweep(db)["late_signoffs"] == []  # escalated once


def test_approval_map_follows_the_directory(db, who):
    m = approvals.approval_map(db, who("atan"))
    ack = next(s for s in m["sections"] if s["key"] == "ack")
    assert ack["steps"][1]["who"]["name"] == "Mark Lee"  # atan's manager
    head = approvals.approval_map(db, who("dortiz"))
    assert any(s["key"].startswith("clause-") for s in head["sections"])


def test_guidance_is_redrafted_when_the_component_record_changes(db, asset):
    from app.services import assessment, registry
    intake.ingest_advisory(db, **STRUTS)
    acis = asset("ACIS Dispute Portal")
    registry.import_sbom(db, acis, {"components": [{"name": "Apache Struts", "version": "2.3.20",
                                                    "properties": [{"name": "path", "value": "/opt/acis/struts"}]}]},
                         scope=["/opt/acis/**"])
    assessment.reassess_all(db)
    gs = db.scalars(select(Guidance).where(Guidance.asset_id == acis.id)).all()
    assert {g.status for g in gs} == {"superseded", "released"}
    assert "2.3.20" in next(g for g in gs if g.status == "released").text


def test_suggested_source_brings_its_file_as_version_one(db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_db
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app)
    try:
        assert c.post("/api/sources", json={"name": "Feed with a file", "kind": "vendor", "intake": "feed",
                                            "document": "docs/FTC_Safeguards_Rule_2022_revised_excerpt.txt"}).status_code == 400
        assert c.post("/api/sources", json={"name": "Missing file", "kind": "regulator", "review_days": 90,
                                            "document": "docs/nope.txt"}).status_code == 404
        r = c.post("/api/sources", json={"name": "FTC Safeguards Rule (amended)", "kind": "regulator", "review_days": 90,
                                         "document": "docs/FTC_Safeguards_Rule_2022_revised_excerpt.txt", "version": "2022"},
                   headers={"X-User": "pnair"}).json()
        assert r["status"] == "pending" and r["pending_version"] == "2022" and r["versions"] == 0
        a = c.post(f"/api/sources/{r['id']}/approve").json()
        assert a["status"] == "approved" and a["latest_version"] == "2022" and a["pending_file"] is None
        assert a["ingested"]["candidates"] == 11 and a["obligations"]["candidate"] == 11
        assert a["review"]["status"] == "current"
    finally:
        app.dependency_overrides.clear()


def test_feed_dashboard_shows_each_system_against_each_alert(db, apache, asset):
    feed_item(db, apache, product="Apache Struts")
    feeds.poll(db, apache)
    d = feeds.dashboard(db, apache)
    assert len(d["systems"]) == 5 and len(d["alerts"]) == 1
    a = d["alerts"][0]
    assert a["new"] and a["awaiting_ack"] >= 1
    portal = asset("Credit Report Portal")
    assert a["cells"][portal.id]["state"] == "applies" and a["cells"][portal.id]["task_ref"]
    s = feeds.summary(db, apache)
    assert s == {**s, "alerts": 1, "new": 1} and s["systems_affected"] >= 1
    clock.advance(hours=25)
    assert not feeds.dashboard(db, apache)["alerts"][0]["new"]


def test_compare_two_versions_with_exact_quotations(db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_db
    s = src(db, "FTC Safeguards Rule")
    first = db.scalar(select(SourceVersion).where(SourceVersion.source_id == s.id)).id
    intake.ingest_source_version(db, s, "a", FTC_2016)
    intake.ingest_source_version(db, s, "b", FTC_2022)
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app)
    try:
        r = c.get(f"/api/sources/{s.id}/compare").json()  # latest against the one before it
        assert r["to"]["version"] == "b" and r["from"]["version"] == "a"
        moved = next(x for x in r["changes"] if x["kind"] == "moved")
        assert moved["old"] in FTC_2016 and moved["new"] in FTC_2022 and moved["obligation_id"]
        assert any(x["kind"] == "renumbered" for x in r["changes"])
        older = c.get(f"/api/sources/{s.id}/compare", params={"from": first}).json()
        assert older["from"]["id"] == first and older["changes"]
        assert c.get(f"/api/sources/{s.id}/compare", params={"to": first}).json()["clauses"]  # nothing earlier
        assert c.get(f"/api/sources/{s.id}/compare", params={"to": first, "from": r["to"]["id"]}).status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_upgrade_fills_an_old_database_without_overwriting_it(db, who):
    from app.seed import replay
    from app.seed.sources import upgrade
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    replay.load_baseline(s)  # the 2017 organisation: three sources, no feeds
    old = s.scalar(select(Source).where(Source.name == "FTC Safeguards Rule"))
    old.owner_id, old.review_days, old.authority = None, None, "kept as typed"
    done = upgrade(s)
    assert done and old.owner_id and old.review_days == 90 and old.authority == "kept as typed"
    names = {x.name for x in s.scalars(select(Source))}
    assert {"UK Data Protection Act 1998", "Atlassian security advisories"} <= names
    assert s.scalar(select(Source).where(Source.name == "CISA/NVD")).intake == "feed"
    assert s.scalar(select(FeedItem.id)) and len(s.scalars(select(Obligation).where(Obligation.source_id == old.id)).all()) == 2
    assert upgrade(s) == []  # nothing left to do the second time
    s.close()
