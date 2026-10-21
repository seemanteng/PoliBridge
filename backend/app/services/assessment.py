"""Component 3: applicability and impact assessment."""
from datetime import timedelta

from sqlalchemy import select

from ..clock import clock
from ..config import TIMINGS
from ..models import Asset, Advisory, Obligation, Claim, Task, Person, Dependency
from ..rules import applicability as A, impact as I
from . import audit
from .common import create_claim, add_dependency, raise_finding, first_with_role
from .assignment import create_task, OPEN_STATES

RANK = {"Low": 0, "Medium": 1, "High": 2}


def _active_claim(db, obligation_id, asset_id):
    return db.scalar(select(Claim).where(Claim.claim_type == "applicability", Claim.obligation_id == obligation_id,
                                         Claim.asset_id == asset_id, Claim.status == "active"))


def _classify(advisory: Advisory, asset: Asset, regulatory=False) -> dict:
    return I.classify(cvss=advisory.cvss, kev=advisory.kev, internet_facing=asset.internet_facing,
                      critical=asset.critical, data_sensitivity=asset.data_sensitivity,
                      customer_facing=asset.customer_facing, regulatory_trigger=regulatory,
                      threshold=TIMINGS["impact_modifier_threshold"])


def assess_obligation(db, obligation: Obligation, actor="system") -> list[Claim]:
    """Match the obligation's advisory against every registered asset."""
    adv = db.get(Advisory, obligation.advisory_id)
    claims = []
    for asset in db.scalars(select(Asset)):
        claims.append(assess_asset(db, obligation, adv, asset, actor))
    tasks = db.scalars(select(Task).where(Task.obligation_id == obligation.id))
    levels = [t.impact for t in tasks]
    obligation.impact = max(levels, key=RANK.get) if levels else None
    return claims


def assess_asset(db, obligation: Obligation, adv: Advisory, asset: Asset, actor="system", force_label=None,
                 reason=None) -> Claim:
    now = clock.now()
    if force_label:
        label, why = force_label, reason or "override"
    else:
        label, why = A.assess_asset(
            components=[{"name": c.name, "version": c.version, "conflict": c.conflict} for c in asset.components],
            inventory_verified_at=asset.inventory_verified_at, inventory_scope=asset.inventory_scope,
            app_roots=asset.app_roots, product=adv.product, affected_specs=adv.affected_versions,
            now=now, stale_days=TIMINGS["component_data_stale_days"])
    existing = _active_claim(db, obligation.id, asset.id)
    if existing and existing.value == label and not force_label:
        return existing
    if existing:
        existing.status = "superseded"
        audit.log(db, actor, "claim", existing.id, "superseded", by_value=label)
    expiry = (asset.inventory_verified_at + timedelta(days=TIMINGS["component_data_stale_days"])
              if asset.inventory_verified_at and label != A.CD else None)
    claim = create_claim(
        db, actor=actor, claim_type="applicability", asset_id=asset.id, obligation_id=obligation.id,
        component=adv.product, value=label, reason=why, owner_id=asset.owner_id,
        statement=f"{asset.name} {label.replace('_', ' ')} for {adv.cve} ({adv.product})",
        evidence_scope=list(asset.inventory_scope or []), verification_method=asset.inventory_method,
        evidence_ts=asset.inventory_verified_at, expiry=expiry)
    _sync_tasks(db, obligation, adv, asset, claim)
    return claim


def _sync_tasks(db, obligation, adv, asset, claim):
    """Create/close tasks so they reflect the current applicability label."""
    open_tasks = list(db.scalars(select(Task).where(Task.obligation_id == obligation.id, Task.asset_id == asset.id,
                                                    Task.status.in_(OPEN_STATES))))
    want = {A.APPLIES: "remediation", A.CD: "investigation"}.get(claim.value)
    for t in open_tasks:
        if t.kind != want:
            t.status, t.disposition, t.closed_at = "closed", "superseded", clock.now()
            audit.log(db, "system", "task", t.id, "closed_superseded", new_label=claim.value)
    if want and not any(t.kind == want and t.status in OPEN_STATES for t in open_tasks):
        info = _classify(adv, asset, regulatory=obligation.kind == "regulatory")
        if info["human_review"]:
            raise_finding(db, "impact_review", "Medium", f"{asset.name}: {adv.cve}",
                          f"Unknown criteria {info['unknown']}; classified conservatively",
                          assigned_to=first_with_role(db, "ciso"))
        create_task(db, obligation=obligation, asset=asset, kind=want, impact=info["impact"],
                    component=adv.product, claim=claim, reviewers=info["required_reviewers"],
                    is_test=obligation.is_test)


def override_applicability(db, obligation: Obligation, asset: Asset, new_label: str, reason: str,
                           actor: Person, reviewer: Person | None = None, evidence_id: int | None = None):
    """A5: more cautious always allowed with a reason; less cautious needs accepted,
    component-scoped evidence and an independent reviewer."""
    if not reason:
        raise ValueError("an override needs a recorded reason")
    adv = db.get(Advisory, obligation.advisory_id)
    current = _active_claim(db, obligation.id, asset.id)
    old = current.value if current else A.CD
    if A.override_requires_evidence(old, new_label):
        from ..models import Evidence
        ev = db.get(Evidence, evidence_id) if evidence_id else None
        if not ev or ev.status != "accepted" or ev.purpose != "absence":
            raise PermissionError("A5: moving to Does Not Apply needs accepted component-scoped evidence")
        if reviewer is None or reviewer.id in (actor.id, ev.submitter_id):
            raise PermissionError("A5: an independent reviewer must approve")
    claim = assess_asset(db, obligation, adv, asset, actor.directory_id, force_label=new_label,
                         reason=f"A5 override from {old}: {reason}")
    claim.submitter_id = actor.id
    claim.reviewer_id = reviewer.id if reviewer else None
    audit.log(db, actor.directory_id, "claim", claim.id, "override", old=old, new=new_label, reason=reason)
    return claim


def reassess_all(db):
    """Re-run matching after component data changes; used after SBOM import."""
    for ob in db.scalars(select(Obligation).where(Obligation.advisory_id.is_not(None),
                                                  Obligation.status == "open")):
        assess_obligation(db, ob, actor="system")
