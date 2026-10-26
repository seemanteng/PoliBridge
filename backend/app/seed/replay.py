"""The March 2017 replay, matching the frontend mockups. Everything is synthetic.

load_baseline() builds the organisation as it stood on 28 Feb 2017.
run_storyline() then plays 8-15 Mar through the real services (no direct row
edits for decisions), leaving the clock at 15 Mar 2017 14:20."""
from datetime import datetime, timedelta

from ..clock import clock
from ..identity import email_for
from ..models import (Person, BusinessUnit, Asset, Component, DiscoveredAsset, Source, SourceVersion, AuditEvent,
                      Dependency, BoardItem, Claim, Task, Evidence, Finding, ComponentVersion,
                      MailingListMember)
from ..services import registry, intake, assignment, closure, assurance
from ..services.common import create_claim, person_by_directory_id as P, raise_finding
from sqlalchemy import select

D = datetime

# CVE-2017-5638 description as published by NVD.
STRUTS_DESCRIPTION = (
    "The Jakarta Multipart parser in Apache Struts 2 2.3.x before 2.3.32 and 2.5.x before 2.5.10.1 has incorrect "
    "exception handling and error-message generation during file-upload attempts, which allows remote attackers to "
    "execute arbitrary commands via a crafted Content-Type, Content-Disposition, or Content-Length HTTP header, as "
    "exploited in the wild in March 2017 with a Content-Type header containing a #cmd= string.")

# Fictional internal policy. The guidance feature (services/rag.py) treats two different "within N hours/days"
# limits across policy lines as a conflict and withholds its output, so only the patch clause carries a time limit.
INFOSEC_POLICY_V1 = """\
Patch critical vulnerabilities on internet-facing systems within 48 hours of an advisory.
Owners must acknowledge security advisories assigned to them.
Every system must have a named owner, recorded by directory identity and not by email address or distribution list.
Owners must confirm their systems and software components at least every six months, and when they change role or leave.
Each security advisory must be matched against every registered system, and a system with missing or stale component data must be treated as unresolved, never as unaffected.
Where a patch cannot be applied, the owner must record a compensating control or a documented risk acceptance approved by the accountable risk owner.
Risk acceptances are time-limited and must be reopened for review when they expire.
A fix is complete only when evidence examined the affected component on the affected system, was generated after the change, and was approved by a reviewer independent of the person who did the work.
A claim that a system does not use an affected component must be supported by evidence of the same standard as a fix.
Monitoring controls relied on as compensating controls must have a named owner, and their claims lapse when the underlying certificate or configuration expires."""

# 16 CFR 314.4 "Elements" as in force in 2016 (the rule Equifax was subject to). One clause per line:
# the change detector in services/intake.py keys on the leading reference.
FTC_SAFEGUARDS_2016 = """\
§314.4(a) Designate an employee or employees to coordinate your information security program.
§314.4(b) Identify reasonably foreseeable internal and external risks to the security, confidentiality, and integrity of customer information that could result in the unauthorized disclosure, misuse, alteration, destruction or other compromise of such information, and assess the sufficiency of any safeguards in place to control these risks. At a minimum, such a risk assessment should include consideration of risks in each relevant area of your operations, including:
§314.4(b)(1) Employee training and management;
§314.4(b)(2) Information systems, including network and software design, as well as information processing, storage, transmission and disposal; and
§314.4(b)(3) Detecting, preventing and responding to attacks, intrusions, or other systems failures.
§314.4(c) Design and implement information safeguards to control the risks you identify through risk assessment, and regularly test or otherwise monitor the effectiveness of the safeguards' key controls, systems, and procedures.
§314.4(d) Oversee service providers, by:
§314.4(d)(1) Taking reasonable steps to select and retain service providers that are capable of maintaining appropriate safeguards for the customer information at issue; and
§314.4(d)(2) Requiring your service providers by contract to implement and maintain such safeguards.
§314.4(e) Evaluate and adjust your information security program in light of the results of the testing and monitoring required by paragraph (c) of this section; any material changes to your operations or business arrangements; or any other circumstances that you know or have reason to know may have a material impact on your information security program."""


def _person(db, did, name, role, title=None, manager=None, bu=None, active=True):
    p = Person(directory_id=did, name=name, email=email_for(name), role=role, title=title,
               manager_id=manager.id if manager else None, business_unit_id=bu.id if bu else None, active=active)
    db.add(p)
    db.flush()
    return p


# The moment the baseline organisation is "as of" in the March 2017 storyline (8 Mar, before the advisory).
BASE = D(2017, 3, 8, 9, 0)


def load_baseline(db, anchor: datetime | None = None):
    """Build the organisation. Dates are written as March 2017 and shifted so that BASE lands on `anchor`.
    The default reproduces 2017 exactly (the replay and the tests); pass the current time for a live demo."""
    A = anchor or BASE
    T = lambda d: A + (d - BASE)  # noqa: E731
    live = anchor is not None  # the live demo is deliberately smaller than the 2017 replay
    clock.set(T(D(2017, 2, 28, 8, 0)))
    bu = BusinessUnit(name="Consumer Services")
    units = {n: BusinessUnit(name=n) for n in ("Executive Office", "Information Security", "Risk & Compliance",
                                               "Legal", "Internal Audit")}
    db.add_all([bu, *units.values()])
    db.flush()
    # Synthetic org chart: everyone reports to someone (the CEO to the board) and belongs to a department.
    ceo = _person(db, "evoss", "Elena Voss", "executive", "Chief Executive Officer", bu=units["Executive Office"])
    coo = _person(db, "sharlow", "Sam Harlow", "senior_mgmt", "Chief Operating Officer", ceo, units["Executive Office"])
    ciso = _person(db, "crhodes", "Casey Rhodes", "ciso", "CISO", coo, units["Information Security"])
    _person(db, "phargreave", "Paul Hargreave", "risk_owner", "Accountable risk owner", coo, units["Risk & Compliance"])
    cco = _person(db, "cmendes", "Carla Mendes", "cco", "Chief Compliance Officer", ceo, units["Risk & Compliance"])
    _person(db, "mlin", "Mei Lin", "compliance", "Compliance Technology", cco, units["Risk & Compliance"])
    legal = _person(db, "lmarsh", "Leo Marsh", "legal", "Legal counsel", ceo, units["Legal"])
    audit = _person(db, "ichen", "Ivy Chen", "internal_audit", "Internal Audit", ceo, units["Internal Audit"])
    _person(db, "amensah", "Ama Mensah", "reviewer", "Independent reviewer", audit, units["Internal Audit"])
    _person(db, "tbrandt", "Tess Brandt", "reviewer", "Independent reviewer", audit, units["Internal Audit"])
    for name, head in (("Executive Office", ceo), ("Information Security", ciso), ("Risk & Compliance", cco),
                       ("Legal", legal), ("Internal Audit", audit)):
        units[name].head_id = head.id
    varga = _person(db, "lvarga", "Lena Varga", "manager", "VP Consumer Technology", coo, bu)
    whit = _person(db, "dwhitfield", "Dana Whitfield", "bu_head", "Head, Consumer Services", manager=varga, bu=bu)
    bu.head_id = whit.id
    okafor = _person(db, "rokafor", "Remi Okafor", "owner", "Former ACIS owner", manager=varga, bu=bu, active=False)
    adeyemi = kowalski = None
    if not live:  # their systems are only in the replay
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

    # ACIS's inventory was last refreshed by its former owner and has been stale since: still Cannot Determine (A3)
    acis = asset("ACIS · consumer dispute portal", okafor, T(D(2015, 11, 20)), "sbom", ["/opt/acis/**"],
                 [("Apache Tomcat", "7.0.59", "/opt/acis/tomcat"), ("OpenJDK", "1.7.0_79", "/opt/acis/jdk"),
                  ("Apache HTTP Server", "2.2.15", "/opt/acis/httpd"), ("OpenSSL", "1.0.1e", "/opt/acis/httpd/ssl"),
                  ("jquery", "1.8.3", "/opt/acis/web/static/js"), ("Oracle JDBC driver", "11.2.0.4", "/opt/acis/lib")],
                 internet_facing=True, critical=True, customer_facing=True,
                 data_sensitivity="confidential", app_roots=["/opt/acis"],
                 legacy_contact="acis-ops (distribution list)")
    # its version history, as recorded while Remi Okafor still owned it
    for when, comp, frm, to, src, note in (
            (D(2014, 6, 2), "Apache Tomcat", None, "7.0.54", "sbom", "initial build"),
            (D(2014, 6, 2), "OpenJDK", None, "1.7.0_60", "sbom", "initial build"),
            (D(2014, 6, 2), "Apache HTTP Server", None, "2.2.15", "sbom", "initial build"),
            (D(2014, 6, 2), "OpenSSL", None, "1.0.1e", "sbom", "initial build"),
            (D(2014, 6, 2), "jquery", None, "1.8.3", "sbom", "initial build"),
            (D(2014, 6, 2), "Oracle JDBC driver", None, "11.2.0.3", "sbom", "initial build"),
            (D(2014, 10, 14), "OpenJDK", "1.7.0_60", "1.7.0_71", "manual", "Oracle October critical patch update"),
            (D(2015, 3, 9), "Apache Tomcat", "7.0.54", "7.0.59", "manual", "quarterly patching"),
            (D(2015, 5, 18), "OpenJDK", "1.7.0_71", "1.7.0_79", "manual", "April critical patch update"),
            (D(2015, 11, 20), "Oracle JDBC driver", "11.2.0.3", "11.2.0.4", "sbom", "database client upgrade")):
        db.add(ComponentVersion(asset_id=acis.id, component=comp, from_version=frm, to_version=to, source=src,
                                actor="rokafor", note=note, ts=T(when)))
    cf = mp = None
    if not live:
        cf = asset("Credit File API", adeyemi, T(D(2017, 3, 2)), "sbom", ["/opt/cfapi/**"],
                   [("struts2-core", "2.3.30", "/opt/cfapi/webapps/struts")], internet_facing=True, critical=True,
                   data_sensitivity="confidential", app_roots=["/opt/cfapi"])
        mp = asset("Member portal", kowalski, T(D(2017, 3, 6)), "sca", ["/opt/member/**"],
                   [("struts2-core", "2.5.8", "/opt/member/webapps/struts")], internet_facing=True, critical=True,
                   customer_facing=True, data_sensitivity="confidential", app_roots=["/opt/member"])
    batch = asset("Batch reporting server", ramirez, T(D(2016, 1, 15)), "sbom", ["/srv/batch/**"],
                  [("jasperreports", "6.1.0", "/srv/batch/jasper")], app_roots=["/srv/batch"])
    hr = asset("HR payroll · vendor SaaS", ramirez, T(D(2017, 3, 1)), "vendor_sbom", ["/vendor/hr/**"],
               [("rails", "4.2.7", "/vendor/hr/app")], internet_facing=True, data_sensitivity="confidential",
               app_roots=["/vendor/hr"], in_scanner_targets=False)  # vendor-hosted, left off the scanner list (B2)
    wiki = asset("Internal wiki", ramirez, T(D(2017, 3, 7)), "authenticated_scan", ["/srv/wiki/**"],
                 [("confluence", "5.9", "/srv/wiki/app")], app_roots=["/srv/wiki"])
    db.add_all([DiscoveredAsset(name=a.name, source="network", internet_facing=a.internet_facing)
                for a in (acis, cf, mp, batch, hr, wiki) if a])
    # seen by discovery but never registered (B1): a public endpoint someone stood up, and an internal database
    db.add_all([DiscoveredAsset(name="dispute-api.shadow.example", source="dns", internet_facing=True),
                DiscoveredAsset(name="acis-reporting-db02", source="network", internet_facing=False)])
    for a in (cf, mp, batch, hr, wiki if not live else None):
        if a:
            registry.attest_ownership(db, a, db.get(Person, a.owner_id))
    if live:  # the wiki was last attested in mid-December, so its quarterly re-attestation is due soon (B3)
        seeded = clock.now()
        clock.set(T(D(2016, 12, 15, 10, 0)))
        registry.attest_ownership(db, wiki, ramirez)
        clock.set(seeded)

    # Ownership of ACIS lapsed when its owner left (2 Feb)
    old = create_claim(db, actor="directory", claim_type="ownership", asset_id=acis.id, value="attested",
                       statement="Remi Okafor owns ACIS", owner_id=okafor.id, submitter_id=okafor.id,
                       verification_method="attestation", evidence_ts=T(D(2016, 9, 1)), expiry=T(D(2017, 3, 1)))
    old.status, old.reason = "expired", "Leaver event invalidated attestation"
    old.expiry = T(D(2017, 2, 2))
    db.add(AuditEvent(ts=T(D(2017, 2, 2, 8)), actor="directory", entity="person", entity_id=okafor.id, action="jml_leaver",
                      detail={}))
    raise_finding(db, "ownership_reassignment", "High", "Reassign owner of ACIS · consumer dispute portal",
                  "Remi Okafor flagged as leaver on 2 Feb; no replacement named.", assigned_to=whit)

    cert = create_claim(db, actor="kosei", claim_type="monitoring", asset_id=acis.id, value="in_effect",
                        statement="ACIS outbound traffic is inspected", owner_id=osei.id, submitter_id=osei.id,
                        reviewer_id=P(db, "tbrandt").id, verification_method="certificate_check",
                        evidence_ts=T(D(2016, 9, 16, 10, 20)), expiry=T(D(2017, 3, 14, 23, 59)),
                        evidence_scope=["cert:SSLV-02", "Decryption policy for ACIS egress"], component="SSLV-02")
    create_claim(db, actor="kosei", claim_type="monitoring", asset_id=acis.id, value="in_effect",
                 statement="ACIS inbound web traffic passes the web-application firewall", owner_id=osei.id,
                 submitter_id=osei.id, reviewer_id=P(db, "amensah").id, verification_method="configuration_review",
                 evidence_ts=T(D(2016, 11, 3, 14, 0)), expiry=T(D(2017, 11, 3, 23, 59)),
                 evidence_scope=["policy:WAF-ACIS-01"], component="WAF-ACIS-01")
    board = BoardItem(ref="Q1-07", title="monitoring coverage", owner_id=ciso.id)
    db.add(board)
    db.flush()
    db.add(Dependency(claim_id=cert.id, dependent_type="board_item", dependent_id=board.id))

    # stakeholder mailing lists: summaries only. Remi Okafor joined ACIS's before leaving, so sends skip him.
    lists = {acis: [("dwhitfield", "Department head"), ("kosei", "Network security"), ("jpark", "Security analyst"),
                    ("mlin", "Compliance"), ("rokafor", "Former owner")],
             hr: [("sramirez", "System owner"), ("mlin", "Compliance"), ("lmarsh", "Legal: payroll data")],
             wiki: [("sramirez", "System owner"), ("jpark", "Security analyst")],
             batch: [("sramirez", "System owner"), ("dwhitfield", "Department head")]}
    for a, members in lists.items():
        for did, stake in members:
            db.add(MailingListMember(asset_id=a.id, person_id=P(db, did).id, stake=stake, added_by="dwhitfield",
                                     added_at=T(D(2016, 6, 1))))

    from .sources import load_sources
    load_sources(db, live=live, anchor=A, seeded_at=clock.now(), policy_text=INFOSEC_POLICY_V1,
                 ftc_text=FTC_SAFEGUARDS_2016)
    db.commit()
    if anchor is not None:
        clock.unfreeze()
        clock.save()


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
                                affected_versions=[">=2.2.3 <2.3.32", ">=2.5.0 <2.5.10.1"], cvss=9.8, kev=True,
                                fixed_version="2.3.32, 2.5.10.1",  # as published by NVD / CISA KEV
                                description=STRUTS_DESCRIPTION)
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
    # a critical system holding personal data: Security, the system owner and Compliance sign off too (C2, C3)
    for key, did in (("security", "crhodes"), ("system_owner", "madeyemi"), ("compliance", "mlin")):
        if key in tasks["Credit File API"].required_reviewers:
            closure.sign_required_review(db, tasks["Credit File API"], key, me(did))
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


def reset_demo(mode: str = "baseline") -> dict:
    """baseline: the organisation as of today on real time, nothing pre-played. replay: the March 2017 storyline on
    a frozen clock (kept for tests and development)."""
    from ..db import Base, engine, SessionLocal
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if mode == "replay":
            load_baseline(s)
            out = run_storyline(s)
        else:
            clock.unfreeze()
            load_baseline(s, anchor=clock.now())
            out = {"now": clock.now().isoformat()}
    return {"mode": mode, **out}
