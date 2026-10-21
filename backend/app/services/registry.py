"""Component 1: ownership, asset and component registry."""
from datetime import timedelta

from sqlalchemy import select

from ..clock import clock
from ..config import TIMINGS
from ..models import Asset, Component, DiscoveredAsset, Claim, Task, Finding, Person
from . import audit
from .common import (create_claim, raise_finding, first_with_role, bu_head, owner_validity)


def import_sbom(db, asset: Asset, doc: dict, *, method: str = "sbom", scope: list[str] | None = None,
                actor: str = "system") -> int:
    """Accepts CycloneDX ({'components': [...]}) or SPDX ({'packages': [...]})."""
    if "components" in doc:
        items = [(c["name"], c.get("version", ""), _prop(c, "path")) for c in doc["components"]]
    elif "packages" in doc:
        items = [(p["name"], p.get("versionInfo", ""), None) for p in doc["packages"]]
    else:
        raise ValueError("unrecognised SBOM: expected CycloneDX 'components' or SPDX 'packages'")
    asset.components.clear()
    for name, version, path in items:
        asset.components.append(Component(name=name, version=version, install_path=path))
    asset.inventory_verified_at = clock.now()
    asset.inventory_method = method
    asset.inventory_scope = scope if scope is not None else list(asset.app_roots and [r + "/**" for r in asset.app_roots])
    audit.log(db, actor, "asset", asset.id, "component_inventory_imported", count=len(items), method=method)
    return len(items)


def _prop(component: dict, key: str):
    for p in component.get("properties", []):
        if p.get("name") == key:
            return p.get("value")
    return None


def attest_ownership(db, asset: Asset, person: Person):
    """Six-monthly attestation; creates the ownership claim that decays (F1)."""
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
                + (" (internet-facing: registration and ownership obligation raised)" if d.internet_facing else ""),
                assigned_to=ciso))
    for a in db.scalars(select(Asset)):
        if not a.in_scanner_targets:
            out.append(raise_finding(db, "coverage_gap", "High" if a.internet_facing else "Medium", a.name,
                                     "Registered asset missing from vulnerability scanner target lists",
                                     assigned_to=ciso))
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
    return out


def register_monitoring_control(db, asset: Asset, name: str, owner: Person, cert_expiry, actor="system"):
    now = clock.now()
    return create_claim(db, actor=actor, claim_type="monitoring", asset_id=asset.id,
                        statement=f"{name} is in effect for {asset.name}", value="in_effect",
                        owner_id=owner.id, submitter_id=owner.id, evidence_ts=now,
                        verification_method="certificate_check", expiry=cert_expiry,
                        evidence_scope=[f"cert:{name}"])


def broadcast_recipients(db) -> list[Person]:
    """Mailing list is broadcast only and checked against active employees."""
    return list(db.scalars(select(Person).where(Person.active == True)))  # noqa: E712
