"""Impact inputs that come from the register: the third-party flag (C5) and affected scope (departments)."""
from sqlalchemy import select

from app.clock import clock
from app.models import Asset, BusinessUnit, Component, Advisory
from app.rules import impact as I
from app.services import assessment, intake
from .conftest import STRUTS

QUIET = dict(cvss=2.0, kev=False, internet_facing=False, critical=False, data_sensitivity="internal",
             customer_facing=False)


def test_modifier_count_covers_the_six_modifiers():
    assert I.classify(**QUIET)["modifiers"] == 0
    assert I.classify(**{**QUIET, "internet_facing": True})["modifiers"] == 1
    assert I.classify(**QUIET, affected_bus=2)["modifiers"] == 1
    assert I.classify(**QUIET, third_party_critical=True)["modifiers"] == 1
    assert I.classify(**{**QUIET, "cvss": 7.0})["modifiers"] == 1          # high severity, below critical
    assert I.classify(**{**QUIET, "data_sensitivity": "confidential"})["modifiers"] == 1
    assert I.classify(**{**QUIET, "critical": True})["modifiers"] == 1
    # regulatory significance is decisive only, never a modifier
    assert I.classify(**QUIET, regulatory_trigger=True)["modifiers"] == 0


def _add_asset(db, name, unit, third_party=False):
    a = Asset(name=name, business_unit_id=unit.id, app_roots=["/srv/x"], third_party_critical=third_party,
              inventory_verified_at=clock.now(), inventory_method="sbom", inventory_scope=["/srv/x/**"])
    a.components.append(Component(name="Apache Struts", version="2.3.31", install_path="/srv/x/struts"))
    db.add(a)
    db.flush()
    return a


def test_affected_scope_counts_departments_the_advisory_may_reach(db):
    adv = Advisory(cve="CVE-X", product="Apache Struts", affected_versions=["2.3.5-2.3.31"], cvss=5.0, kev=False)
    db.add(adv)
    db.flush()
    before = assessment.affected_business_units(db, adv)
    other = BusinessUnit(name="Retail Lending")
    db.add(other)
    db.flush()
    _add_asset(db, "Lending portal", other)
    assert assessment.affected_business_units(db, adv) == before + 1
    # a second affected system in the same department adds nothing
    _add_asset(db, "Lending API", other)
    assert assessment.affected_business_units(db, adv) == before + 1
    quiet_asset = db.scalar(select(Asset).where(Asset.name == "HR Intranet"))
    one = assessment._classify(adv, quiet_asset)
    many = assessment._classify(adv, quiet_asset, db=db)
    assert many["modifiers"] == one["modifiers"] + (1 if before + 1 > 1 else 0)


def test_third_party_flag_makes_a_quiet_system_high_impact(db):
    adv = Advisory(cve="CVE-Y", product="Apache Struts", affected_versions=["2.3.5-2.3.31"], cvss=2.0, kev=False)
    db.add(adv)
    db.flush()
    unit = db.scalar(select(BusinessUnit))
    vendor = _add_asset(db, "Vendor-run claims system", unit)
    assert assessment._classify(adv, vendor, db=db)["impact"] != "High"
    vendor.third_party_critical = True
    info = assessment._classify(adv, vendor, db=db)
    assert info["impact"] == "High" and any(t.startswith("C5") for t in info["triggers"])


def test_register_flag_reaches_the_task(db):
    unit = db.scalar(select(BusinessUnit))
    vendor = _add_asset(db, "Vendor-run claims system", unit, third_party=True)
    ob = intake.ingest_advisory(db, **{**STRUTS, "cvss": 3.0, "kev": False})
    from app.models import Task
    t = db.scalar(select(Task).where(Task.obligation_id == ob.id, Task.asset_id == vendor.id))
    assert t and t.impact == "High"
