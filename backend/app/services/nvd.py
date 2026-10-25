"""Look up a CVE in the public NVD (API 2.0) and CISA KEV feeds to pre-fill the advisory form.
Network access is only used here; ingestion itself still goes through intake.ingest_advisory."""
import re

import httpx

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")


def _spec(m: dict, cpe_version: str) -> str | None:
    """One NVD cpeMatch entry as a version spec understood by rules.versions.in_range."""
    lo = m.get("versionStartIncluding") or m.get("versionStartExcluding")
    lo_op = ">=" if m.get("versionStartIncluding") else ">"
    hi = m.get("versionEndIncluding") or m.get("versionEndExcluding")
    hi_op = "<=" if m.get("versionEndIncluding") else "<"
    if lo and hi and lo_op == ">=" and hi_op == "<=":
        return f"{lo}-{hi}"
    parts = ([f"{lo_op}{lo}"] if lo else []) + ([f"{hi_op}{hi}"] if hi else [])
    if parts:
        return " ".join(parts)
    return cpe_version if cpe_version not in ("*", "-", "") else None


def _candidates(cve: dict) -> list[dict]:
    by_product: dict[str, list[str]] = {}
    fixed: dict[str, list[str]] = {}
    for conf in cve.get("configurations", []):
        for node in conf.get("nodes", []):
            for m in node.get("cpeMatch", []):
                if not m.get("vulnerable"):
                    continue
                f = m["criteria"].split(":")
                if len(f) < 6 or f[2] != "a":
                    continue
                spec = _spec(m, f[5])
                specs = by_product.setdefault(f[4], [])
                if m.get("versionEndExcluding"):  # "before X" means X is the fix
                    fixed.setdefault(f[4], []).append(m["versionEndExcluding"])
                if spec and spec not in specs:
                    specs.append(spec)
    return [{"product": p, "versions": v, "fixed_version": ", ".join(fixed.get(p, [])) or None}
            for p, v in by_product.items() if v]


def _cvss(cve: dict) -> float | None:
    metrics = cve.get("metrics", {})
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        if metrics.get(key):
            return metrics[key][0]["cvssData"]["baseScore"]
    return None


def lookup(cve_id: str) -> dict:
    cve_id = cve_id.strip().upper()
    if not CVE_RE.match(cve_id):
        raise ValueError(f"'{cve_id}' is not a CVE id (expected CVE-YYYY-NNNN)")
    try:
        r = httpx.get(NVD_URL, params={"cveId": cve_id}, timeout=15)
        r.raise_for_status()
        vulns = r.json().get("vulnerabilities", [])
    except httpx.HTTPError as e:
        raise ConnectionError(f"NVD lookup failed: {e}") from e
    if not vulns:
        raise LookupError(f"{cve_id} not found in NVD")
    cve = vulns[0]["cve"]
    try:
        kev_feed = httpx.get(KEV_URL, timeout=15, follow_redirects=True).json().get("vulnerabilities", [])
        kev = any(v.get("cveID") == cve_id for v in kev_feed)
    except (httpx.HTTPError, ValueError):
        kev = None  # unknown; the form leaves the box for the user
    desc = next((d["value"] for d in cve.get("descriptions", []) if d["lang"] == "en"), "")
    return {"cve": cve_id, "description": desc, "cvss": _cvss(cve), "kev": kev, "candidates": _candidates(cve),
            "published": cve.get("published")}
