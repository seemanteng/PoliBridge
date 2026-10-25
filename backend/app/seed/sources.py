"""The approved-source registry and the mock advisory feeds. Everything here is synthetic: regulation texts are short
paraphrased excerpts for the demonstrator, and the feeds are queues of FeedItem rows, not live connections."""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..clock import clock
from ..models import Source, SourceVersion, FeedItem, Obligation, Dependency, Claim, Advisory, Task, Guidance
from ..services.common import create_claim, person_by_directory_id as P

# Fictional contract with a federal agency customer. Its 1-hour reporting window deliberately differs from the
# InfoSec policy's 48 hours, so guidance drafted for it is withheld as conflicting (A4) and routed to Legal.
AGENCY_CONTRACT_V1 = """\
Clause 7.1 Contractor shall report any security incident affecting agency data within 1 hour of discovery.
Clause 7.2 Contractor shall maintain an inventory of every system that processes agency data, including its software components.
Clause 7.3 Contractor shall apply security patches to systems that process agency data in accordance with its documented patch policy."""

# Paraphrased excerpt of the UK Data Protection Act 1998, Schedule 1 (the law Equifax Ltd was fined under).
UK_DPA_1998 = """\
Principle 5 Personal data processed for any purpose shall not be kept for longer than is necessary for that purpose.
Principle 7 Appropriate technical and organisational measures shall be taken against unauthorised or unlawful processing of personal data and against accidental loss or destruction of, or damage to, personal data."""

# Paraphrased excerpt of Georgia's breach-notification statute (Equifax's home state).
GEORGIA_BREACH_LAW = """\
§10-1-912(a) A data collector that maintains computerized personal information shall notify any resident of this state whose unencrypted personal information was, or is reasonably believed to have been, acquired by an unauthorized person, in the most expedient time possible and without unreasonable delay.
§10-1-912(d) Where more than 10,000 residents must be notified at one time, the data collector shall also notify, without unreasonable delay, every consumer reporting agency that compiles files on consumers on a nationwide basis."""

STRUTS_NVD = (
    "The Jakarta Multipart parser in Apache Struts 2 2.3.x before 2.3.32 and 2.5.x before 2.5.10.1 has incorrect "
    "exception handling and error-message generation during file-upload attempts, which allows remote attackers to "
    "execute arbitrary commands via a crafted Content-Type, Content-Disposition, or Content-Length HTTP header, as "
    "exploited in the wild in March 2017 with a Content-Type header containing a #cmd= string.")

# (feed, minutes after reset when the feed exposes it, minutes the issuer published it before that, advisory)
MOCK_FEED = [
    ("Apache Software Foundation security bulletins", 3, 90, dict(
        cve="CVE-2017-5638", product="struts2-core", affected_versions=["2.3.5-2.3.31", "2.5-2.5.10"], cvss=None,
        kev=False, fixed_version="2.3.32, 2.5.10.1",
        description="S2-045: possible remote code execution when performing file upload based on the Jakarta "
                    "Multipart parser. Maximum security rating: Critical.")),
    ("CISA/NVD", 5, 30, dict(
        cve="CVE-2017-5638", product="struts2-core", affected_versions=[">=2.3.5 <2.3.32", ">=2.5.0 <2.5.10.1"],
        cvss=10.0, kev=True, fixed_version="2.3.32, 2.5.10.1", description=STRUTS_NVD)),
    ("Atlassian security advisories", 7, 45, dict(
        cve="CVE-2019-3396", product="confluence",
        affected_versions=["<6.6.12", ">=6.7.0 <6.12.3", ">=6.13.0 <6.13.3", ">=6.14.0 <6.14.2"], cvss=9.8, kev=True,
        fixed_version="6.6.12, 6.12.3, 6.13.3, 6.14.2",
        description="The Widget Connector macro in Confluence Server before 6.6.12, 6.7.0 before 6.12.3, 6.13.0 before "
                    "6.13.3 and 6.14.0 before 6.14.2 allows remote attackers to achieve path traversal and remote "
                    "code execution via server-side template injection.")),
    ("CISA/NVD", 9, 90, dict(
        cve="CVE-2016-2098", product="rails", affected_versions=["<3.2.22.2", ">=4.0.0 <4.1.14.2", ">=4.2.0 <4.2.5.2"],
        cvss=7.3, kev=False, fixed_version="3.2.22.2, 4.1.14.2, 4.2.5.2",
        description="Action Pack in Ruby on Rails before 3.2.22.2, 4.x before 4.1.14.2 and 4.2.x before 4.2.5.2 allows "
                    "remote attackers to execute arbitrary Ruby code through an application's unrestricted use of render.")),
    ("CISA/NVD", 11, 120, dict(
        cve="CVE-2020-11022", product="jquery", affected_versions=[">=1.2 <3.5.0"], cvss=6.1, kev=False,
        fixed_version="3.5.0",
        description="In jQuery 1.2 and later before 3.5.0, passing HTML from untrusted sources to DOM manipulation "
                    "methods may execute untrusted code.")),
]


def _definitions(policy_text: str, ftc_text: str) -> list[tuple]:
    """(name, kind, live demo only, fields, first version (label, text, effective date) or None, days since last
    review). The seed builds the registry from these, and upgrade() fills in a database seeded before they existed."""
    return [
        ("CISA/NVD", "advisory", False, dict(url="https://nvd.nist.gov", authority="CISA and NIST (US government)",
                                             jurisdiction="US federal", intake="feed", owner="mlin"), None, None),
        ("Apache Software Foundation security bulletins", "vendor", True,
         dict(url="https://cwiki.apache.org/confluence/display/WW/Security+Bulletins", authority="Apache Software Foundation",
              jurisdiction="Global", intake="feed", owner="mlin"), None, None),
        ("Atlassian security advisories", "vendor", True,
         dict(url="https://www.atlassian.com/trust/security/advisories", authority="Atlassian", jurisdiction="Global",
              intake="feed", owner="mlin"), None, None),
        # the FTC rule is due for review soon and the UK act is overdue (it was in fact replaced in 2018)
        ("FTC Safeguards Rule", "regulator", False,
         dict(url="https://www.ecfr.gov/current/title-16/chapter-I/subchapter-C/part-314",
              authority="U.S. Federal Trade Commission", jurisdiction="US federal", owner="lmarsh", review_days=90),
         ("2016", ftc_text, "2003-05-23"), 80),
        ("Equifax InfoSec Policy", "policy", False,
         dict(authority="Equifax CISO (internal)", jurisdiction="Internal", owner="crhodes", review_days=365),
         ("1", policy_text, None), 30),
        ("UK Data Protection Act 1998", "regulator", True,
         dict(url="https://www.legislation.gov.uk/ukpga/1998/29", authority="UK Parliament (enforced by the ICO)",
              jurisdiction="UK", owner="lmarsh", review_days=180), ("1998", UK_DPA_1998, None), 200),
        ("Georgia breach-notification law", "regulator", True,
         dict(url="https://law.justia.com/codes/georgia/title-10/chapter-1/article-34/", authority="State of Georgia",
              jurisdiction="US state", owner="lmarsh", review_days=180), ("2017", GEORGIA_BREACH_LAW, None), 20),
        ("Federal agency data services contract", "contract", True,
         dict(authority="U.S. federal agency customer", jurisdiction="US federal", owner="lmarsh", review_days=365),
         ("1", AGENCY_CONTRACT_V1, None), 10),
    ]


def _first_version(db, s: Source, version: tuple, at: datetime):
    label, text, effective = version
    db.add(SourceVersion(source_id=s.id, version=label, ingested_at=at, text=text, summary={"baseline": True},
                         uploaded_by="seed", retrieved_from=s.url, effective_date=effective))
    db.flush()


def _review(db, s: Source, when: datetime):
    return create_claim(db, actor="seed", claim_type="source_currency", source_id=s.id, value="checked_no_change",
                        statement=f"The register reflects the current {s.name}", evidence_scope=[s.url] if s.url else [],
                        verification_method="checked_no_change", evidence_ts=when, submitter_id=s.owner_id,
                        owner_id=s.owner_id, expiry=when + timedelta(days=s.review_days))


def _queue_feed(db, by_name: dict, anchor: datetime):
    for name, after, published_before, fields in MOCK_FEED:
        s = by_name.get(name)
        if s is not None:
            at = anchor + timedelta(minutes=after)
            db.add(FeedItem(source_id=s.id, available_at=at, published_at=at - timedelta(minutes=published_before), **fields))
    db.flush()


def load_sources(db, *, live: bool, anchor: datetime, seeded_at: datetime, policy_text: str, ftc_text: str):
    """Registry entries for both modes. The live demo also gets mock feeds, review history and two approved FTC
    obligations (so a new FTC version has something to supersede)."""
    made, reviews = {}, {}
    for name, kind, live_only, fields, version, checked in _definitions(policy_text, ftc_text):
        if live_only and not live:
            continue
        f = dict(fields)
        owner, intake = P(db, f.pop("owner")), f.pop("intake", "manual")
        s = Source(name=name, kind=kind, status="approved", approved_at=seeded_at, created_at=seeded_at,
                   approved_by="Carla Mendes", owner_id=owner.id, intake=intake if live else "manual", **f)
        db.add(s)
        db.flush()
        made[name] = s
        if version:
            _first_version(db, s, version, seeded_at)
        if live and checked:
            reviews[name] = _review(db, s, anchor - timedelta(days=checked))
    if not live:  # the 2017 replay ingests by hand, as its storyline expects
        return
    _approved_ftc_obligations(db, made["FTC Safeguards Rule"], reviews["FTC Safeguards Rule"], anchor)
    _queue_feed(db, made, anchor)


def upgrade(db) -> list[str]:
    """Bring a demo database seeded before the source registry existed up to date, without a reset. It only fills in
    what is missing: sources, details, reviews and obligations someone already recorded are left as they are."""
    from .replay import INFOSEC_POLICY_V1, FTC_SAFEGUARDS_2016
    if P(db, "mlin") is None:  # not the demo organisation
        return []
    now, done, made = clock.now(), [], {}
    for name, kind, _, fields, version, checked in _definitions(INFOSEC_POLICY_V1, FTC_SAFEGUARDS_2016):
        f = dict(fields)
        owner, intake = P(db, f.pop("owner")), f.pop("intake", "manual")
        s = db.scalar(select(Source).where(Source.name == name))
        if s is None:
            s = Source(name=name, kind=kind, status="approved", approved_at=now, created_at=now, approved_by="Carla Mendes",
                       owner_id=owner.id if owner else None, intake=intake, **f)
            db.add(s)
            db.flush()
            done.append(f"added {name}")
        else:
            filled = []
            for k, v in {**f, "owner_id": owner.id if owner else None, "approved_at": now, "created_at": now}.items():
                if getattr(s, k) is None and v is not None:
                    setattr(s, k, v)
                    filled.append(k)
            if intake == "feed" and s.intake != "feed" and s.last_polled_at is None:
                s.intake = "feed"
                filled.append("intake")
            if filled:
                done.append(f"filled in {name}: {', '.join(filled)}")
        made[name] = s
        if version and not db.scalar(select(SourceVersion.id).where(SourceVersion.source_id == s.id)):
            _first_version(db, s, version, now)
        if checked and s.intake == "manual" and not db.scalar(select(Claim.id).where(
                Claim.claim_type == "source_currency", Claim.source_id == s.id)):
            _review(db, s, now - timedelta(days=checked))
    ftc = made["FTC Safeguards Rule"]
    if (db.scalar(select(func.count(SourceVersion.id)).where(SourceVersion.source_id == ftc.id)) == 1
            and not db.scalar(select(Obligation.id).where(Obligation.source_id == ftc.id))):
        review = db.scalar(select(Claim).where(Claim.claim_type == "source_currency", Claim.source_id == ftc.id,
                                               Claim.status == "active").order_by(Claim.id.desc()))
        if review is not None:
            _approved_ftc_obligations(db, ftc, review, now)
            done.append("added the two approved 2016 FTC obligations")
    for adv in db.scalars(select(Advisory).where(Advisory.is_canary == False)):  # noqa: E712
        if not adv.sources:  # recorded before each advisory kept the sources that reported it
            src = db.get(Source, adv.source_id) if adv.source_id else None
            adv.received_at = adv.received_at or adv.published_at
            adv.ingested_at = adv.ingested_at or adv.published_at
            adv.sources = [{"source": src.name if src else "CISA/NVD", "received_at": adv.received_at.isoformat(),
                            "published_at": adv.published_at.isoformat(), "affected_versions": list(adv.affected_versions),
                            "cvss": adv.cvss, "kev": adv.kev}]
            done.append(f"recorded the source of {adv.cve}")
    if not db.scalar(select(FeedItem.id).limit(1)):
        _queue_feed(db, made, now)
        done.append("queued the demo feed alerts")
    from ..services import rag  # per-system guidance for advisory tasks created before it was drafted
    for t in db.scalars(select(Task).where(Task.is_test == False, Task.status.in_(("open", "reopened", "fixed_pending_review")))):  # noqa: E712
        ob = db.get(Obligation, t.obligation_id)
        if ob.advisory_id and not db.scalar(select(Guidance.id).where(Guidance.obligation_id == ob.id,
                                                                      Guidance.asset_id == t.asset_id)):
            rag.guidance_for_task(db, ob, t)
            done.append(f"drafted guidance for task {t.id}")
    db.flush()
    return done


def _approved_ftc_obligations(db, ftc: Source, claim, anchor: datetime):
    """Two 2016 clauses already approved and signed off, so ingesting the 2022 text shows them being superseded."""
    v = db.scalar(select(SourceVersion).where(SourceVersion.source_id == ftc.id).order_by(SourceVersion.id))
    lines = {l.split(" ", 1)[0]: l for l in v.text.splitlines()}
    signed = (anchor - timedelta(days=70)).isoformat()
    rows = {
        "§314.4(a)": [("Executive Office", "Elena Voss"), ("Information Security", "Casey Rhodes")],
        "§314.4(c)": [("Information Security", "Casey Rhodes"), ("Internal Audit", "Ivy Chen")],
    }
    approver = P(db, "lmarsh")
    for ref, depts in rows.items():
        ob = Obligation(kind="regulatory", title=f"FTC Safeguards Rule {ref}", source_id=ftc.id, source_ref=ref,
                        quotation=lines[ref], status="open", approved_by_id=approver.id, applicability_decision="applies",
                        required_action="Maintain the safeguard the clause describes and evidence it each year",
                        created_at=anchor - timedelta(days=75), source_version_id=v.id,
                        departments=[{"department": d, "owner": o, "reason": "approved against the 2016 wording", "label": "applies",
                                      "decided_by": approver.name, "note": None, "signed_by": o, "signed_at": signed,
                                      "due": signed} for d, o in depts])
        db.add(ob)
        db.flush()
        db.add(Dependency(claim_id=claim.id, dependent_type="obligation", dependent_id=ob.id))
