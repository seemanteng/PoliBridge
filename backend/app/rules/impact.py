"""Impact classification (Appendix B, rules C1-C6). Missing information never
lowers the classification: an unknown criterion counts at its worst value."""

DECISIVE_REVIEW = {
    "critical_system": ["security", "system_owner"],
    "sensitive_data": ["security", "compliance"],
    "regulatory": ["legal", "compliance"],
}


def classify(*, cvss: float | None, kev: bool, internet_facing: bool | None,
             critical: bool | None, data_sensitivity: str | None, customer_facing: bool | None,
             regulatory_trigger: bool = False, third_party_critical: bool = False,
             affected_bus: int = 1, threshold: int = 4) -> dict:
    unknown = []
    if cvss is None and not kev:
        unknown.append("cvss")
    if internet_facing is None:
        unknown.append("internet_exposure")
    if critical is None:
        unknown.append("system_criticality")
    if data_sensitivity is None:
        unknown.append("data_sensitivity")
    # Unknowns are assumed worst-case so they can only raise the result.
    sev_critical = kev or cvss is None or cvss >= 9.0
    sev_high = kev or cvss is None or cvss >= 7.0
    exposed = internet_facing is None or internet_facing
    is_critical_sys = critical is None or critical or bool(customer_facing)
    sensitive = data_sensitivity is None or data_sensitivity in ("confidential", "restricted")

    triggers, reviewers = [], set()
    if sev_critical and exposed:                          # C1
        triggers.append("C1: critical/exploited vulnerability on internet-facing asset")
    if is_critical_sys and (critical or customer_facing):  # C2
        triggers.append("C2: critical system affected")
        reviewers.update(DECISIVE_REVIEW["critical_system"])
    if sensitive and sev_high:                            # C3
        triggers.append("C3: sensitive data with material security impact")
        reviewers.update(DECISIVE_REVIEW["sensitive_data"])
    if regulatory_trigger:                                # C4
        triggers.append("C4: regulatory reporting or legal obligation triggered")
        reviewers.update(DECISIVE_REVIEW["regulatory"])
    if third_party_critical:                              # C5
        triggers.append("C5: critical third-party dependency affected")

    modifiers = sum([sev_high, exposed, bool(is_critical_sys), sensitive, affected_bus > 1,
                     third_party_critical])
    if triggers or modifiers >= threshold:
        level = "High"
    elif sev_high or modifiers >= 2:
        level = "Medium"
    else:
        level = "Low"
    if not triggers and modifiers >= threshold:
        triggers.append(f"modifier combination ({modifiers}) meets threshold {threshold}")
    return {"impact": level, "triggers": triggers, "unknown": unknown,
            "human_review": bool(unknown),  # uncertainty -> human review, never a downgrade
            "required_reviewers": sorted(reviewers),
            "borderline": level == "Medium" and modifiers == threshold - 1}  # C6
