"""Rules E1-E4 plus scope coverage. Pure functions over plain values so they
can be unit-tested without a database."""
from datetime import datetime, timedelta

from . import versions

STRONG_METHODS = {"authenticated_scan", "direct_version_check"}


def covers(scope: list[str], path: str) -> bool:
    """A scope entry 'p' covers exactly p; 'p/**' covers everything under p.
    A scan of '/' alone examined only the root directory (Equifax, March 2017)."""
    path = path.rstrip("/") or "/"
    for entry in scope:
        if entry == "**":
            return True
        if entry.endswith("/**"):
            base = entry[:-3].rstrip("/") or "/"
            if base == "/" or path == base or path.startswith(base + "/"):
                return True
        elif (entry.rstrip("/") or "/") == path:
            return True
    return False


def covers_tree(scope: list[str], path: str) -> bool:
    """Evidence must have examined everything *under* the path, so a non-recursive
    entry (a directory listed at depth 1) never counts: the component sits deeper."""
    path = path.rstrip("/") or "/"
    for entry in scope:
        if entry == "**":
            return True
        if entry.endswith("/**"):
            base = entry[:-3].rstrip("/") or "/"
            if base == "/" or path == base or path.startswith(base + "/"):
                return True
    return False


def required_paths(component_path: str | None, app_roots: list[str]) -> list[str]:
    """Where evidence must have looked: the component's directory if known,
    otherwise every application root of the asset (needed to prove absence)."""
    if component_path:
        return [component_path]
    return list(app_roots)


def scope_gaps(scope: list[str], needed: list[str]) -> list[str]:
    if not needed:
        return ["no application roots recorded, so scope cannot be confirmed"]
    return [p for p in needed if not covers_tree(scope, p)]


def check_evidence(*, purpose: str, task_asset: str, task_component: str | None,
                   ev_asset: str | None, ev_component: str | None, scope: list[str],
                   method: str | None, observed_version: str | None,
                   timestamp: datetime | None, submitter_id: int | None,
                   reported_fixed_at: datetime | None, needed_paths: list[str],
                   strong_method_required: bool, affected_specs: list[str],
                   now: datetime) -> list[str]:
    """Returns rejection reasons; empty list means evidence is acceptable."""
    reasons: list[str] = []
    # E4 incomplete
    missing = [n for n, v in (("asset", ev_asset), ("component", ev_component), ("scope", scope),
                              ("method", method), ("timestamp", timestamp),
                              ("submitter", submitter_id)) if not v]
    if missing:
        return [f"E4: evidence incomplete, missing {', '.join(missing)}"]
    # E1 right asset and component
    if ev_asset != task_asset:
        reasons.append(f"E1: evidence is for '{ev_asset}', task is for '{task_asset}'")
    if task_component and versions.norm(ev_component) != versions.norm(task_component):
        reasons.append(f"E1: evidence covers '{ev_component}', not '{task_component}'")
    gaps = scope_gaps(scope, needed_paths)
    if gaps:
        reasons.append("E1: evidence scope does not cover " + ", ".join(gaps))
    if strong_method_required and method not in STRONG_METHODS:
        reasons.append(f"E1: method '{method}' is not an authenticated scan or direct version check")
    if timestamp > now + timedelta(minutes=5):
        reasons.append("E2: evidence timestamp is in the future")
    # E2 evidence must be generated after remediation (remediation purpose only)
    if purpose == "remediation":
        if reported_fixed_at and timestamp < reported_fixed_at:
            reasons.append("E2: evidence predates the reported remediation")
        if observed_version and affected_specs and versions.affected(observed_version, affected_specs):
            reasons.append(f"E1: observed version {observed_version} is still affected")
        if not observed_version:
            reasons.append("E4: remediation evidence must state the observed version")
    return reasons


def check_reviewer(reviewer_id: int | None, submitter_id: int | None, fixed_by_id: int | None) -> list[str]:
    """E3: reviewer independent of whoever did the work."""
    if reviewer_id is None:
        return ["E3: no reviewer"]
    reasons = []
    if reviewer_id == submitter_id:
        reasons.append("E3: reviewer is the evidence submitter")
    if reviewer_id == fixed_by_id:
        reasons.append("E3: reviewer performed the remediation")
    return reasons
