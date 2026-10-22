"""Synthetic organisation modelled on the Equifax case. All names are fictional."""
from datetime import timedelta

from ..clock import clock
from ..identity import email_for
from ..models import (Person, BusinessUnit, Asset, Component, DiscoveredAsset, Source, SourceVersion)
from ..services import registry
from ..services.common import create_claim

POLICY_V1 = """Patch critical vulnerabilities on internet-facing systems within 48 hours of an advisory.
Owners must acknowledge security advisories assigned to them."""

SAFEGUARDS_V1 = """§314.4(a) Designate an employee to coordinate the information security program.
§314.4(c)(2) Review access controls periodically."""


def _p(db, did, name, role, manager=None, bu=None):
    p = Person(directory_id=did, name=name, email=email_for(name), role=role, manager_id=manager.id if manager else None,
               business_unit_id=bu.id if bu else None)
    db.add(p)
    db.flush()
    return p


def load(db) -> dict:
    now = clock.now()
    bu = BusinessUnit(name="Consumer Services")
    db.add(bu)
    db.flush()
    ciso = _p(db, "crhodes", "Casey Rhodes", "ciso")
    senior = _p(db, "swhitfield", "Sam Whitfield", "senior_mgmt")
    risk = _p(db, "rpark", "Riley Park", "risk_owner")
    _p(db, "pnair", "Priya Nair", "compliance")
    _p(db, "lbrandt", "Leo Brandt", "legal")
    _p(db, "cmendes", "Carla Mendes", "cco")
    _p(db, "ichen", "Ivy Chen", "internal_audit")
    _p(db, "rverma", "Rob Verma", "reviewer")
    _p(db, "rkim", "Rae Kim", "reviewer")
    head = _p(db, "dortiz", "Dana Ortiz", "bu_head", bu=bu)
    bu.head_id = head.id
    mgr = _p(db, "mlee", "Mark Lee", "manager", manager=head, bu=bu)
    alex = _p(db, "atan", "Alex Tan", "owner", manager=mgr, bu=bu)
    gary = _p(db, "glim", "Gary Lim", "owner", manager=mgr, bu=bu)
    ops = _p(db, "ofoo", "Olivia Foo", "owner", manager=mgr, bu=bu)

    def asset(name, owner, **kw):
        a = Asset(name=name, owner_id=owner.id if owner else None, business_unit_id=bu.id, **kw)
        db.add(a)
        db.flush()
        return a

    acis = asset("ACIS Dispute Portal", gary, internet_facing=True, critical=True, customer_facing=True,
                 data_sensitivity="restricted", app_roots=["/opt/acis"], legacy_contact="acis-admins@corp.example")
    acis.ownership_attested_at = now - timedelta(days=240)   # lapsed attestation
    portal = asset("Credit Report Portal", alex, internet_facing=True, critical=True, customer_facing=True,
                   data_sensitivity="restricted", app_roots=["/opt/portal"])
    portal.components.append(Component(name="Apache Struts", version="2.3.31", install_path="/opt/portal/struts"))
    portal.components.append(Component(name="OpenJDK", version="8u121", install_path="/opt/portal/jdk"))
    portal.inventory_verified_at, portal.inventory_method = now - timedelta(days=2), "authenticated_scan"
    portal.inventory_scope = ["/opt/portal/**"]
    hr = asset("HR Intranet", ops, data_sensitivity="confidential", app_roots=["/srv/hr"])
    hr.components.append(Component(name="nginx", version="1.10.3", install_path="/srv/hr/nginx"))
    hr.inventory_verified_at, hr.inventory_method, hr.inventory_scope = now - timedelta(days=3), "sbom", ["/srv/hr/**"]
    legacy = asset("Mainframe Gateway", None, critical=True, app_roots=["/opt/gw"],
                   legacy_contact="mainframe-support@corp.example (distribution list)")
    mkt = asset("Marketing Site", ops, internet_facing=True, app_roots=["/var/www"], in_scanner_targets=False)
    mkt.components.append(Component(name="WordPress", version="4.7.3", install_path="/var/www/wp"))
    mkt.inventory_verified_at, mkt.inventory_method, mkt.inventory_scope = now - timedelta(days=1), "sca", ["/var/www/**"]
    db.flush()
    for a, o in ((portal, alex), (hr, ops), (mkt, ops)):
        registry.attest_ownership(db, a, o)

    db.add_all([DiscoveredAsset(name=a.name, source="network", internet_facing=a.internet_facing)
                for a in (acis, portal, hr, legacy, mkt)])
    db.add(DiscoveredAsset(name="dispute-api.shadow.example", source="dns", internet_facing=True))

    registry.register_monitoring_control(db, acis, "SSL inspection certificate", gary, now + timedelta(days=45))

    cisa = Source(name="CISA/NVD", kind="advisory", url="https://www.cisa.gov", approved_by="Compliance")
    ftc = Source(name="FTC Safeguards Rule", kind="regulator", url="https://www.ecfr.gov", approved_by="Legal")
    pol = Source(name="Equifax InfoSec Policy", kind="policy", approved_by="CISO")
    db.add_all([cisa, ftc, pol])
    db.flush()
    db.add(SourceVersion(source_id=pol.id, version="1", text=POLICY_V1, ingested_at=now))
    db.add(SourceVersion(source_id=ftc.id, version="2016", text=SAFEGUARDS_V1, ingested_at=now))
    db.commit()
    return {"people": 14, "assets": 5, "note": "ACIS has no component data and a lapsed owner attestation"}
