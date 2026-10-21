from datetime import datetime, timedelta

from app.rules import applicability as A, evidence as E, impact as I, versions as V

NOW = datetime(2017, 3, 9, 12)
SPECS = ["2.3.5-2.3.31", "2.5-2.5.10"]


def test_version_ranges():
    assert V.affected("2.3.31", SPECS) and V.affected("2.5.10", SPECS)
    assert not V.affected("2.3.32", SPECS) and not V.affected("2.5.10.1", ["2.5-2.5.10"])


def assess(components, verified=NOW - timedelta(days=1), scope=("**",), roots=("/opt/acis",)):
    return A.assess_asset(components=components, inventory_verified_at=verified,
                          inventory_scope=list(scope), app_roots=list(roots), product="Apache Struts",
                          affected_specs=SPECS, now=NOW, stale_days=30)[0]


def test_a1_applies():
    assert assess([{"name": "apache struts", "version": "2.3.31"}]) == A.APPLIES


def test_a2_absent_verified():
    assert assess([{"name": "nginx", "version": "1.0"}]) == A.DNA


def test_a3_missing_stale_conflict_never_dna():
    assert assess([], verified=None) == A.CD
    assert assess([{"name": "nginx", "version": "1"}], verified=NOW - timedelta(days=60)) == A.CD
    assert assess([{"name": "apache struts", "version": "2.3.31", "conflict": True}]) == A.CD


def test_absence_needs_scope_covering_roots():
    assert assess([{"name": "nginx", "version": "1"}], scope=("/",)) == A.CD  # root only, not recursive


def test_a5_direction():
    assert A.override_requires_evidence(A.CD, A.DNA)
    assert A.override_requires_evidence(A.APPLIES, A.DNA)
    assert not A.override_requires_evidence(A.DNA, A.CD)


def test_scope_cover():
    assert not E.covers(["/"], "/opt/acis/struts")
    assert E.covers(["/opt/acis/**"], "/opt/acis/struts")
    assert E.covers(["**"], "/anything")


def ev(**kw):
    base = dict(purpose="remediation", task_asset="ACIS", task_component="Apache Struts",
                ev_asset="ACIS", ev_component="apache struts", scope=["/opt/acis/**"],
                method="authenticated_scan", observed_version="2.3.32", timestamp=NOW,
                submitter_id=1, reported_fixed_at=NOW - timedelta(hours=1),
                needed_paths=["/opt/acis/struts"], strong_method_required=True,
                affected_specs=SPECS, now=NOW)
    base.update(kw)
    return E.check_evidence(**base)


def test_evidence_ok_and_rejections():
    assert ev() == []
    assert any("E1" in r for r in ev(ev_asset="OTHER"))
    assert any("scope" in r for r in ev(scope=["/"]))
    assert any("E2" in r for r in ev(timestamp=NOW - timedelta(hours=5)))
    assert any("E4" in r for r in ev(method=None))
    assert any("still affected" in r for r in ev(observed_version="2.3.31"))
    assert any("authenticated" in r for r in ev(method="sbom"))


def test_reviewer_independence():
    assert E.check_reviewer(2, 1, 3) == []
    assert E.check_reviewer(1, 1, 3) and E.check_reviewer(3, 1, 3)


def test_impact_never_lowered_by_unknowns():
    known_low = I.classify(cvss=2.0, kev=False, internet_facing=False, critical=False,
                           data_sensitivity="internal", customer_facing=False)
    assert known_low["impact"] == "Low"
    unknown = I.classify(cvss=None, kev=False, internet_facing=None, critical=None,
                         data_sensitivity=None, customer_facing=None)
    assert unknown["impact"] == "High" and unknown["human_review"]
    acis = I.classify(cvss=10, kev=True, internet_facing=True, critical=True,
                      data_sensitivity="restricted", customer_facing=True)
    assert acis["impact"] == "High" and len(acis["triggers"]) >= 3


def test_non_recursive_scope_fails_tree_coverage():
    assert not E.covers_tree(["/opt/acis"], "/opt/acis/struts")
    assert not E.covers_tree(["/opt/acis/"], "/opt/acis")
    assert E.covers_tree(["/opt/acis/**"], "/opt/acis")
    assert E.scope_gaps(["/opt/acis"], ["/opt/acis"]) == ["/opt/acis"]
