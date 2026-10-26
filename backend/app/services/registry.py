"""Component 1: ownership, asset and component registry."""
from datetime import timedelta

from sqlalchemy import select

from ..clock import clock
from ..config import TIMINGS
from ..models import Asset, Component, ComponentVersion, DiscoveredAsset, Claim, Task, Finding, MailingListMember, Notification, Person
from ..rules import versions
from . import audit
from .common import (create_claim, raise_finding, first_with_role, bu_head, owner_validity, notify)


def import_sbom(db, asset: Asset, doc: dict, *, method: str = "sbom", scope: list[str] | None = None,
                actor: str = "system") -> int:
    """Accepts CycloneDX ({'components': [...]}) or SPDX ({'packages': [...]})."""
    if "components" in doc:
        items = [(c["name"], c.get("version", ""), _prop(c, "path")) for c in doc["components"]]
    elif "packages" in doc:
        items = [(p["name"], p.get("versionInfo", ""), None) for p in doc["packages"]]
    else:
        raise ValueError("unrecognised SBOM: expected CycloneDX 'components' or SPDX 'packages'")
    before = {versions.norm(c.name): c for c in asset.components}
    asset.components.clear()
    for name, version, path in items:
        asset.components.append(Component(name=name, version=version, install_path=path))
        old = before.pop(versions.norm(name), None)
        if not old or old.version != version:
            record_version(db, asset, name, old.version if old else None, version, source=method, actor=actor)
    for old in before.values():
        record_version(db, asset, old.name, old.version, None, source=method, actor=actor)
    asset.inventory_verified_at = clock.now()
    asset.inventory_method = method
    asset.inventory_scope = scope if scope is not None else list(asset.app_roots and [r + "/**" for r in asset.app_roots])
    audit.log(db, actor, "asset", asset.id, "component_inventory_imported", count=len(items), method=method)
    return len(items)


# ---------- version audit log ----------
def record_version(db, asset: Asset, component: str, old: str | None, new: str | None, *, source: str,
                   actor: str = "system", note: str | None = None) -> ComponentVersion:
    """Every change to what version a system runs goes through here, so the history is complete."""
    row = ComponentVersion(asset_id=asset.id, component=component, from_version=old, to_version=new,
                           source=source, actor=actor or "system", note=note, ts=clock.now())
    db.add(row)
    audit.log(db, actor, "asset", asset.id, "component_version_changed", component=component,
              from_version=old, to_version=new, source=source)
    return row


def set_component_version(db, asset: Asset, name: str, version: str, *, install_path: str | None = None,
                          source: str = "manual", actor: str = "system", note: str | None = None) -> Component:
    """Record an upgrade (or a newly found component) without re-importing the whole inventory."""
    comp = next((c for c in asset.components if versions.norm(c.name) == versions.norm(name)), None)
    if comp and comp.version == version:
        raise ValueError(f"{comp.name} is already recorded at {version}")
    old = comp.version if comp else None
    if comp:
        comp.version = version
        comp.install_path = install_path or comp.install_path
    else:
        comp = Component(name=name, version=version, install_path=install_path)
        asset.components.append(comp)
    record_version(db, asset, comp.name, old, version, source=source, actor=actor, note=note)
    return comp


def required_fix(version: str, fixed: str | None) -> str | None:
    """Advisories list one fixed release per branch ("2.3.32, 2.5.10.1"): the lowest one above the current version."""
    options = [f.strip() for f in (fixed or "").split(",") if f.strip()]
    above = [f for f in options if versions._cmp(versions.parse(f), versions.parse(version)) > 0]
    if above:
        return min(above, key=versions.parse)
    return max(options, key=versions.parse) if options else None


def updates_needed(component: Component, advisories) -> list[dict]:
    """Known advisories that put this component's recorded version in an affected range."""
    out = []
    for adv in advisories:
        if versions.norm(adv.product) == versions.norm(component.name) and \
                versions.affected(component.version, adv.affected_versions or []):
            out.append({"advisory_id": adv.id, "cve": adv.cve, "kev": adv.kev, "cvss": adv.cvss,
                        "fixed_version": required_fix(component.version, adv.fixed_version)})
    return out


def _prop(component: dict, key: str):
    for p in component.get("properties", []):
        if p.get("name") == key:
            return p.get("value")
    return None


def attest_ownership(db, asset: Asset, person: Person):
    """Quarterly attestation; creates the ownership claim that decays (F1)."""
    if asset.owner_id != person.id:
        raise PermissionError("only the named owner can attest ownership")
    now = clock.now()
    for old in db.scalars(select(Claim).where(Claim.claim_type == "ownership", Claim.asset_id == asset.id,
                                              Claim.status == "active")):
        old.status = "superseded"
    asset.ownership_attested_at = now
    c = create_claim(db, actor=person.directory_id, claim_type="ownership", asset_id=asset.id,
                     statement=f"{person.name} owns {asset.name}", value="attested", owner_id=person.id,
                     submitter_id=person.id, evidence_ts=now, verification_method="attestation",
                     expiry=now + timedelta(days=TIMINGS["attestation_days"]))
    return c


def attestation_due(asset: Asset):
    return asset.ownership_attested_at + timedelta(days=TIMINGS["attestation_days"]) if asset.ownership_attested_at else None


def attestation_state(asset: Asset) -> str:
    """never | current | due_soon | lapsed"""
    due = attestation_due(asset)
    if due is None:
        return "never"
    now = clock.now()
    if due <= now:
        return "lapsed"
    return "due_soon" if due - now <= timedelta(days=TIMINGS["attestation_due_soon_days"]) else "current"


def attestation_reminders(db) -> list[Asset]:
    """B3: the quarterly run. Owners are asked to re-attest before the attestation lapses, once per cycle."""
    out = []
    for a in db.scalars(select(Asset).where(Asset.ownership_attested_at.is_not(None))):
        owner = db.get(Person, a.owner_id) if a.owner_id else None
        if attestation_state(a) != "due_soon" or not owner or not owner.active:
            continue
        sent = db.scalar(select(Notification).where(
            Notification.kind == "attestation_due", Notification.person_id == owner.id,
            Notification.message.contains(a.name), Notification.created_at >= a.ownership_attested_at))
        if sent:
            continue
        notify(db, person=owner, kind="attestation_due",
               message=f"Re-attest ownership of {a.name} by {attestation_due(a):%d %b %Y}. If it lapses, ownership "
                       "becomes invalid and its open work is rerouted to the department head.")
        audit.log(db, "system", "asset", a.id, "attestation_reminder_sent", to=owner.directory_id)
        out.append(a)
    return out


def joiner_mover_leaver(db, person: Person, event: str, actor="directory"):
    """B3: JML event invalidates ownership claims early and reroutes open tasks."""
    from .assignment import reroute_task  # local import avoids a cycle
    if event == "leaver":
        person.active = False
    audit.log(db, actor, "person", person.id, f"jml_{event}")
    affected = list(db.scalars(select(Asset).where(Asset.owner_id == person.id)))
    for asset in affected:
        for c in db.scalars(select(Claim).where(Claim.claim_type == "ownership", Claim.asset_id == asset.id,
                                                Claim.status == "active")):
            c.status = "expired"
            c.reason = f"invalidated by {event} event"
        asset.ownership_attested_at = None
        raise_finding(db, "ownership_reassignment", "High" if asset.critical else "Medium",
                      f"Reassign owner of {asset.name}", f"{person.name} flagged as {event}.",
                      assigned_to=bu_head(db, asset))
        for t in db.scalars(select(Task).where(Task.asset_id == asset.id,
                                               Task.status.in_(("open", "reopened", "fixed_pending_review")))):
            reroute_task(db, t, f"owner {event}", actor=actor)
    return affected


def reconcile_discovery(db) -> list[Finding]:
    """B1 and B2."""
    out = []
    registered = {a.name for a in db.scalars(select(Asset))}
    ciso = first_with_role(db, "ciso")
    for d in db.scalars(select(DiscoveredAsset)):
        if d.name not in registered:
            out.append(raise_finding(
                db, "unregistered_asset", "High" if d.internet_facing else "Medium", d.name,
                f"Seen by {d.source} but absent from the asset register"
                + (". Internet-facing with no named owner, so advisories cannot be matched or routed to it: "
                   "register it and name an owner" if d.internet_facing else ""),
                assigned_to=ciso))
    # registering the asset is what resolves the finding
    for f in db.scalars(select(Finding).where(Finding.kind == "unregistered_asset", Finding.status == "open")):
        if f.subject in registered:
            f.status = "resolved"
            audit.log(db, "system", "finding", f.id, "resolved", reason="asset registered")
    gaps = set()
    for a in db.scalars(select(Asset)):
        if not a.in_scanner_targets:
            gaps.add(a.name)
            out.append(raise_finding(db, "coverage_gap", "High" if a.internet_facing else "Medium", a.name,
                                     "Registered asset missing from vulnerability scanner target lists",
                                     assigned_to=ciso))
    # adding the asset to the scanner targets is what resolves the finding
    for f in db.scalars(select(Finding).where(Finding.kind == "coverage_gap", Finding.status == "open")):
        if f.subject not in gaps:
            f.status = "resolved"
            audit.log(db, "system", "finding", f.id, "resolved", reason="asset in scanner targets")
    return out


def data_quality_scan(db) -> list[Finding]:
    now = clock.now()
    out = []
    for a in db.scalars(select(Asset)):
        ok, why = owner_validity(db, a)
        if not ok:
            out.append(raise_finding(db, "data_quality", "High" if a.critical else "Medium",
                                     f"{a.name}: ownership", why, assigned_to=bu_head(db, a)))
        if a.inventory_verified_at is None or not a.components:
            out.append(raise_finding(db, "data_quality", "High" if a.internet_facing else "Medium",
                                     f"{a.name}: components", "No component inventory recorded",
                                     assigned_to=bu_head(db, a)))
        elif now - a.inventory_verified_at > timedelta(days=TIMINGS["component_data_stale_days"]):
            out.append(raise_finding(db, "data_quality", "Medium", f"{a.name}: components",
                                     "Component inventory is stale", assigned_to=bu_head(db, a)))
    for p in db.scalars(select(Person).where(Person.active == True)):  # noqa: E712
        if not (p.email or "").strip():
            out.append(raise_finding(db, "data_quality", "Medium", f"{p.name}: contact email",
                                     "No email recorded, so notifications cannot reach this person",
                                     assigned_to=first_with_role(db, "compliance")))
    return out


MONITORING_METHODS = {"certificate_check": "cert", "configuration_review": "config"}


def register_monitoring_control(db, asset: Asset, name: str, owner: Person, expiry, actor="system",
                                method: str = "certificate_check"):
    """B3: the claim lapses when the certificate expires, or when the configuration is next due for review."""
    if method not in MONITORING_METHODS:
        raise ValueError(f"a monitoring control is verified by {' or '.join(MONITORING_METHODS)}")
    now = clock.now()
    return create_claim(db, actor=actor, claim_type="monitoring", asset_id=asset.id,
                        statement=f"{name} is in effect for {asset.name}", value="in_effect",
                        owner_id=owner.id, submitter_id=owner.id, evidence_ts=now,
                        verification_method=method, expiry=expiry, component=name,
                        evidence_scope=[f"{MONITORING_METHODS[method]}:{name}"])


def broadcast_recipients(db) -> list[Person]:
    """Mailing list is broadcast only and checked against active employees."""
    return list(db.scalars(select(Person).where(Person.active == True)))  # noqa: E712


# ---------- per-system mailing lists (broadcast only) ----------
def mailing_list_status(person: Person) -> str:
    """Checked against the directory at send time: only active employees receive summaries."""
    if not person.active:
        return "left"
    return "active" if person.deliverable else "undeliverable"


def add_to_mailing_list(db, asset: Asset, person: Person, stake: str | None = None, actor="system") -> MailingListMember:
    if not person.active:
        raise ValueError(f"{person.name} has left; only active employees can join a mailing list")
    if db.scalar(select(MailingListMember).where(MailingListMember.asset_id == asset.id,
                                                 MailingListMember.person_id == person.id)):
        raise ValueError(f"{person.name} is already on the {asset.name} mailing list")
    m = MailingListMember(asset_id=asset.id, person_id=person.id, stake=(stake or "").strip() or None,
                          added_by=actor, added_at=clock.now())
    db.add(m)
    db.flush()
    audit.log(db, actor, "asset", asset.id, "mailing_list_added", person=person.directory_id, stake=m.stake)
    return m


def system_summary(db, asset: Asset) -> str:
    """The status summary broadcast to a system's stakeholders. Informational: it assigns nothing to anyone."""
    from ..models import Advisory
    ok, why = owner_validity(db, asset)
    owner = db.get(Person, asset.owner_id) if asset.owner_id else None
    tasks = list(db.scalars(select(Task).where(Task.asset_id == asset.id, Task.is_test.is_(False),
                                               Task.status.in_(("open", "reopened", "fixed_pending_review")))))
    advisories = list(db.scalars(select(Advisory).where(Advisory.is_canary.is_(False))))
    outdated = [f"{c.name} {c.version}" for c in asset.components if updates_needed(c, advisories)]
    controls = list(db.scalars(select(Claim).where(Claim.claim_type == "monitoring", Claim.asset_id == asset.id,
                                                   Claim.status.in_(("active", "expired")))))
    lapsed = [c for c in controls if c.status == "expired"]
    lines = [
        f"Status summary for {asset.name}, {clock.now():%d %b %Y}",
        f"Owner: {owner.name if owner else 'none named'} ({'valid' if ok else why})",
        f"Open remediation and investigation tasks: {len(tasks)}"
        + (f" ({', '.join(sorted({t.impact for t in tasks}))} impact)" if tasks else ""),
        "Components needing an update: " + (", ".join(outdated) if outdated else "none known"),
        f"Monitoring controls: {len(controls) - len(lapsed)} in effect"
        + (f", {len(lapsed)} lapsed" if lapsed else ""),
    ]
    if not asset.in_scanner_targets:
        lines.append("Not in vulnerability scanner targets (coverage gap)")
    lines.append("This is a summary only; the owner receives any action needed directly.")
    return "\n".join(lines)


def broadcast_summary(db, asset: Asset, actor="system") -> dict:
    """Send the summary to the list. Leavers are skipped; actions are never routed through the list."""
    text = system_summary(db, asset)
    sent, skipped = [], []
    for m in db.scalars(select(MailingListMember).where(MailingListMember.asset_id == asset.id)):
        p = db.get(Person, m.person_id)
        status = mailing_list_status(p)
        if status == "left":
            skipped.append({"name": p.name, "reason": "no longer an active employee"})
            continue
        n = notify(db, person=p, kind="system_summary", message=text)
        (sent if n.delivered else skipped).append(
            {"name": p.name} if n.delivered else {"name": p.name, "reason": "mailbox undeliverable"})
    audit.log(db, actor, "asset", asset.id, "summary_broadcast", sent=len(sent), skipped=len(skipped))
    return {"summary": text, "sent": sent, "skipped": skipped}
