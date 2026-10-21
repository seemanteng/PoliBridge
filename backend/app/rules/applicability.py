"""Rules A1-A3 and A5. A5 ranks labels by caution: moving down needs evidence."""
from datetime import datetime, timedelta

from . import versions
from .evidence import scope_gaps

APPLIES, DNA, CD = "applies", "does_not_apply", "cannot_determine"
CAUTION = {DNA: 0, APPLIES: 1, CD: 1}


def assess_asset(*, components: list[dict], inventory_verified_at: datetime | None,
                 inventory_scope: list[str], app_roots: list[str], product: str,
                 affected_specs: list[str], now: datetime, stale_days: int) -> tuple[str, str]:
    """Returns (label, reason). components: dicts with name, version, conflict."""
    # A3: no data, unverified or stale -> Cannot Determine, never Does Not Apply
    if inventory_verified_at is None:
        return CD, "A3: no verified component inventory for this asset"
    if now - inventory_verified_at > timedelta(days=stale_days):
        return CD, f"A3: component inventory is stale (verified {inventory_verified_at:%Y-%m-%d})"
    matches = [c for c in components if versions.norm(c["name"]) == versions.norm(product)]
    if any(c.get("conflict") for c in matches) or len({c["version"] for c in matches}) > 1:
        return CD, "A3: conflicting component records"
    # A1: confirmed present at an affected version
    for c in matches:
        if versions.affected(c["version"], affected_specs):
            return APPLIES, f"A1: {c['name']} {c['version']} is in the affected range"
    if matches:
        return DNA, f"A2: {matches[0]['name']} {matches[0]['version']} is outside the affected range"
    # A2 absent: only if the inventory's recorded scope covers the asset
    gaps = scope_gaps(inventory_scope, app_roots)
    if gaps:
        return CD, "A3: inventory scope does not cover " + ", ".join(gaps) + "; absence not established"
    return DNA, f"A2: {product} absent from verified inventory covering all application roots"


def override_requires_evidence(old: str, new: str) -> bool:
    """A5: a less cautious label needs scoped evidence and an independent reviewer."""
    return CAUTION[new] < CAUTION[old]
