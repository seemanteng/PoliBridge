"""The March 2017 replay, matching the frontend mockups. Everything is synthetic.

load_baseline() builds the organisation as it stood on 28 Feb 2017.
run_storyline() then plays 8-15 Mar through the real services (no direct row
edits for decisions), leaving the clock at 15 Mar 2017 14:20."""
from datetime import datetime, timedelta

from ..clock import clock
from ..models import (Person, BusinessUnit, Asset, Component, DiscoveredAsset, Source, SourceVersion, AuditEvent,
                      Dependency, BoardItem, Claim, Task, Evidence, Finding)
from ..services import registry, intake, assignment, closure, assurance
from ..services.common import create_claim, person_by_directory_id as P, raise_finding
from sqlalchemy import select

D = datetime


def _person(db, did, name, role, title=None, manager=None, bu=None, active=True):
    p = Person(directory_id=did, name=name, role=role, title=title, manager_id=manager.id if manager else None,
               business_unit_id=bu.id if bu else None, active=active)
    db.add(p)
    db.flush()
    return p


def load_baseline(db):
    clock.set(D(2017, 2, 28, 8, 0))
    bu = BusinessUnit(name="Consumer Services")
    db.add(bu)
    db.flush()
    ciso = _person(db, "crhodes", "Casey Rhodes", "ciso", "CISO")
    _person(db, "sharlow", "Sam Harlow", "senior_mgmt", "Chief Operating Officer")
    _person(db, "phargreave", "Paul Hargreave", "risk_owner", "Accountable risk owner")
    _person(db, "mlin", "Mei Lin", "compliance", "Compliance Technology")
    _person(db, "lmarsh", "Leo Marsh", "legal", "Legal counsel")
    _person(db, "cmendes", "Carla Mendes", "cco", "Chief Compliance Officer")
    _person(db, "ichen", "Ivy Chen", "internal_audit", "Internal Audit")
    _person(db, "amensah", "Ama Mensah", "reviewer", "Independent reviewer")
    _person(db, "tbrandt", "Tess Brandt", "reviewer", "Independent reviewer")
    varga = _person(db, "lvarga", "Lena Varga", "manager", "VP Consumer Technology", bu=bu)
    whit = _person(db, "dwhitfield", "Dana Whitfield", "bu_head", "Head, Consumer Services", manager=varga, bu=bu)
    bu.head_id = whit.id
    okafor = _person(db, "rokafor", "Remi Okafor", "owner", "Former ACIS owner", manager=varga, bu=bu, active=False)
    adeyemi = _person(db, "madeyemi", "Mo Adeyemi", "owner", "Credit File API owner", manager=varga, bu=bu)
    kowalski = _person(db, "hkowalski", "Hana Kowalski", "owner", "Member portal owner", manager=varga, bu=bu)
    ramirez = _person(db, "sramirez", "Sofia Ramírez", "owner", "Reporting platform owner", manager=varga, bu=bu)
    _person(db, "jpark", "Jin Park", "owner", "Security analyst", manager=varga, bu=bu)
    osei = _person(db, "kosei", "Kofi Osei", "owner", "Network security engineer", manager=varga, bu=bu)
    db.flush()

    def asset(name, owner, inv=None, method=None, scope=None, comps=(), **kw):
        a = Asset(name=name, owner_id=owner.id, business_unit_id=bu.id, **kw)
        for n, v, path in comps:
            a.components.append(Component(name=n, version=v, install_path=path))
        a.inventory_verified_at, a.inventory_method, a.inventory_scope = inv, method, scope or []
        db.add(a)
        db.flush()
        return a

    acis = asset("ACIS · consumer dispute portal", okafor, internet_facing=True, critical=True, customer_facing=True,
                 data_sensitivity="confidential", app_roots=["/opt/acis"],
                 legacy_contact="acis-ops (distribution list)")
    cf = asset("Credit File API", adeyemi, D(2017, 3, 2), "sbom", ["/opt/cfapi/**"],
               [("struts2-core", "2.3.30", "/opt/cfapi/webapps/struts")], internet_facing=True, critical=True,
               data_sensitivity="confidential", app_roots=["/opt/cfapi"])
    mp = asset("Member portal", kowalski, D(2017, 3, 6), "sca", ["/opt/member/**"],
               [("struts2-core", "2.5.8", "/opt/member/webapps/struts")], internet_facing=True, critical=True,
               customer_facing=True, data_sensitivity="confidential", app_roots=["/opt/member"])
    batch = asset("Batch reporting server", ramirez, D(2016, 1, 15), "sbom", ["/srv/batch/**"],
                  [("jasperreports", "6.1.0", "/srv/batch/jasper")], app_roots=["/srv/batch"])
    hr = asset("HR payroll · vendor SaaS", ramirez, D(2017, 3, 1), "vendor_sbom", ["/vendor/hr/**"],
               [("rails", "4.2.7", "/vendor/hr/app")], internet_facing=True, data_sensitivity="confidential",
               app_roots=["/vendor/hr"])
    wiki = asset("Internal wiki", ramirez, D(2017, 3, 7), "authenticated_scan", ["/srv/wiki/**"],
                 [("confluence", "5.9", "/srv/wiki/app")], app_roots=["/srv/wiki"])
    db.add_all([DiscoveredAsset(name=a.name, source="network", internet_facing=a.internet_facing)
                for a in (acis, cf, mp, batch, hr, wiki)])
    for a in (cf, mp, batch, hr, wiki):
        registry.attest_ownership(db, a, db.get(Person, a.owner_id))

    # Ownership of ACIS lapsed when its owner left (2 Feb)
    old = create_claim(db, actor="directory", claim_type="ownership", asset_id=acis.id, value="attested",
                       statement="Remi Okafor owns ACIS", owner_id=okafor.id, submitter_id=okafor.id,
                       verification_method="attestation", evidence_ts=D(2016, 9, 1), expiry=D(2017, 3, 1))
    old.status, old.reason = "expired", "Leaver event invalidated attestation"
    old.expiry = D(2017, 2, 2)
    db.add(AuditEvent(ts=D(2017, 2, 2, 8), actor="directory", entity="person", entity_id=okafor.id, action="jml_leaver",
                      detail={}))
    raise_finding(db, "ownership_reassignment", "High", "Reassign owner of ACIS · consumer dispute portal",
                  "Remi Okafor flagged as leaver on 2 Feb; no replacement named.", assigned_to=whit)

    cert = create_claim(db, actor="kosei", claim_type="monitoring", asset_id=acis.id, value="in_effect",
                        statement="ACIS outbound traffic is inspected", owner_id=osei.id, submitter_id=osei.id,
                        reviewer_id=P(db, "tbrandt").id, verification_method="certificate_check",
                        evidence_ts=D(2016, 9, 16, 10, 20), expiry=D(2017, 3, 14, 23, 59),
                        evidence_scope=["cert:SSLV-02", "Decryption policy for ACIS egress"], component="SSLV-02")
    board = BoardItem(ref="Q1-07", title="monitoring coverage", owner_id=ciso.id)
    db.add(board)
    db.flush()
    db.add(Dependency(claim_id=cert.id, dependent_type="board_item", dependent_id=board.id))

    cisa = Source(name="CISA/NVD", kind="advisory", url="https://www.cisa.gov", approved_by="Compliance")
    ftc = Source(name="FTC Safeguards Rule", kind="regulator", approved_by="Legal")
    pol = Source(name="Equifax InfoSec Policy", kind="policy", approved_by="CISO")
    db.add_all([cisa, ftc, pol])
    db.flush()
    db.add(SourceVersion(source_id=pol.id, version="1", ingested_at=clock.now(),
                         text="Patch critical vulnerabilities on internet-facing systems within 48 hours of an advisory.\n"
                              "Owners must acknowledge security advisories assigned to them."))
    db.add(SourceVersion(source_id=ftc.id, version="2016", ingested_at=clock.now(),
                         text="§314.4(a) Designate an employee to coordinate the information security program.\n"
                              "§314.4(c)(2) Review access controls periodically."))
    db.commit()


def _at(db, when: datetime):
    clock.set(when)
    assurance.sweep(db)


def run_storyline(db):
    me = lambda d: P(db, d)  # noqa: E731
    asset = lambda name: db.scalar(select(Asset).where(Asset.name.like(name + "%")))  # noqa: E731

    # 28 Feb - 1 Mar: scheduled canary on the batch server is never acknowledged; escalation fires on time
    assurance.run_canary(db, asset("Batch reporting"))
    _at(db, D(2017, 3, 1, 10, 0))
    # 6 Mar: routing-rule change triggers a canary on the member portal; acknowledged after 5h42m
    clock.set(D(2017, 3, 6, 7, 0))
    run = assurance.run_canary(db, asset("Member portal"))
    clock.set(D(2017, 3, 6, 12, 42))
    assignment.acknowledge(db, db.get(Task, run.task_id), me("hkowalski"))
    assurance.sweep(db)

    # 8 Mar 09:12 advisory ingested and matched against every asset
    clock.set(D(2017, 3, 8, 9, 12))
    ob = intake.ingest_advisory(db, cve="CVE-2017-5638", product="struts2-core",
                                affected_versions=["2.3.5-2.3.31", "2.5-2.5.10"], cvss=10.0, kev=True,
                                fixed_version="2.3.32, 2.5.10.1")
    tasks = {db.get(Asset, t.asset_id).name.split(" ·")[0]: t
             for t in db.scalars(select(Task).where(Task.obligation_id == ob.id))}
    db.commit()
    _at(db, D(2017, 3, 8, 16, 5))
    assignment.acknowledge(db, tasks["ACIS"], me("dwhitfield"))
    assignment.acknowledge(db, tasks["Credit File API"], me("madeyemi"))
    assignment.acknowledge(db, tasks["Member portal"], me("hkowalski"))
    assignment.acknowledge(db, tasks["Batch reporting server"], me("sramirez"))

    # 9 Mar: interim compensating control on ACIS, relying on traffic inspection
    clock.set(D(2017, 3, 9, 17, 40))
    cert = db.scalar(select(Claim).where(Claim.claim_type == "monitoring"))
    assignment.record_interim_control(
        db, tasks["ACIS"], me("dwhitfield"),
        "Web-application firewall rule on ACIS, with detection relying on traffic inspection (MC-019).", cert)
    db.commit()

    # 10-11 Mar: Credit File API patched and verified by an independent reviewer
    clock.set(D(2017, 3, 10, 14, 0))
    closure.mark_fixed(db, tasks["Credit File API"], me("madeyemi"))
    clock.set(D(2017, 3, 11, 9, 30))
    ev = closure.submit_evidence(db, tasks["Credit File API"], me("madeyemi"), purpose="remediation",
                                 asset_name="Credit File API", component="struts2-core",
                                 scope=["/opt/cfapi/**"], method="authenticated_scan", observed_version="2.3.32",
                                 timestamp=clock.now())
    closure.review_evidence(db, ev, me("amensah"), True, "Authenticated scan covers the component path.")
    db.commit()

    # 11 Mar 03:00: canary after an ownership change on ACIS fails at routing
    clock.set(D(2017, 3, 11, 3, 0))
    assurance.run_canary(db, asset("ACIS"))
    db.commit()

    # 12 Mar: member portal fixed, evidence awaiting independent review
    clock.set(D(2017, 3, 12, 10, 0))
    closure.mark_fixed(db, tasks["Member portal"], me("hkowalski"))
    clock.set(D(2017, 3, 12, 15, 30))
    closure.submit_evidence(db, tasks["Member portal"], me("hkowalski"), purpose="remediation",
                            asset_name="Member portal", component="struts2-core", scope=["/opt/member/**"],
                            method="direct_version_check", observed_version="2.5.10.1", timestamp=clock.now())
    db.commit()

    # 15 Mar 00:00: the inspection certificate has lapsed, taking the monitoring claim with it
    _at(db, D(2017, 3, 15, 0, 0))
    # 15 Mar 09:12: ACIS remediation deadline passes -> risk owner, senior management, board report
    _at(db, D(2017, 3, 15, 9, 13))
    # 15 Mar 10:48: a "does not apply" claim for ACIS rests on an unauthenticated, shallow scan
    clock.set(D(2017, 3, 15, 11, 2))
    closure.submit_evidence(db, tasks["ACIS"], me("jpark"), purpose="absence", asset_name="ACIS · consumer dispute portal",
                            component="struts2-core", scope=["/opt/acis"], method="unauthenticated_external_scan",
                            timestamp=D(2017, 3, 15, 10, 48))
    _at(db, D(2017, 3, 15, 14, 20))
    db.commit()
    clock.save()
    return {"obligation_id": ob.id, "now": clock.now().isoformat()}
