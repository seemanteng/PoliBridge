from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import get_db
from .conftest import STRUTS


def client(db):
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def h(u):
    return {"X-User": u}


def test_api_flow(db):
    c = client(db)
    assert c.get("/api/tasks").status_code == 200
    r = c.post("/api/advisories", json=STRUTS, headers=h("pnair"))
    assert r.status_code == 200, r.text
    ob = r.json()
    assert ob["closable"] is False and len(ob["tasks"]) == 3
    portal = next(t for t in ob["tasks"] if t["asset"] == "Credit Report Portal")
    # anyone can act, but the sign-off is recorded for the owner and the audit trail says who entered it
    assert c.post(f"/api/tasks/{portal['id']}/acknowledge", headers=h("ofoo")).status_code == 200
    log = c.get(f"/api/timeline?entity=task&entity_id={portal['id']}").json()
    assert any(e["action"] == "entered_on_behalf" and e["actor"] == "ofoo" for e in log)
    assert c.post(f"/api/tasks/{portal['id']}/acknowledge", headers=h("atan")).status_code == 200
    assert c.get("/api/dashboard").json()["cannot_determine"] == 2
    assert c.post("/api/tasks/1/acknowledge").status_code != 401  # no header: the Risk & Compliance operator acts
    assert c.post("/api/clock/advance", json={"hours": 30}).status_code == 200
    assert c.get("/api/health").json()["ok"]
    app.dependency_overrides.clear()


def test_sbom_upload_reassesses(db):
    c = client(db)
    c.post("/api/advisories", json=STRUTS, headers=h("pnair"))
    acis = next(a for a in c.get("/api/assets").json() if a["name"] == "ACIS Dispute Portal")
    sbom = {"bomFormat": "CycloneDX", "components": [
        {"name": "Apache Struts", "version": "2.3.31", "properties": [{"name": "path", "value": "/opt/acis/struts"}]}]}
    r = c.post(f"/api/assets/{acis['id']}/sbom", json={"document": sbom, "scope": ["/opt/acis/**"]}, headers=h("atan"))
    assert r.status_code == 200, r.text
    claims = c.get("/api/claims", params={"claim_type": "applicability", "status": "active"}).json()
    assert next(x for x in claims if x["asset"] == "ACIS Dispute Portal")["value"] == "applies"
    assert c.post(f"/api/assets/{acis['id']}/sbom", json={"document": {"x": 1}}, headers=h("atan")).status_code == 400
    app.dependency_overrides.clear()


def test_ui_views(db):
    from sqlalchemy import select
    from app.models import Task
    c = client(db)
    c.post("/api/advisories", json=STRUTS, headers=h("pnair"))
    c.post("/api/clock/advance", json={"hours": 24 * 8})
    o = c.get("/api/oversight").json()
    assert o["banner"] and o["overdue"] and o["overdue"][0]["escalated_to"] == "Risk owner, board report"
    t = c.get(f"/api/tasks/{o['overdue'][0]['id']}").json()
    assert t["overdue"] and t["timeline"] and t["people"]["owner"]
    ob = c.get("/api/obligations").json()[0]
    est = c.get(f"/api/obligations/{ob['id']}").json()["estate"]
    assert est["counts"]["cannot_determine"] == 2 and est["why_high"]
    acis = next(r for r in est["rows"] if r["asset"].startswith("ACIS"))
    tid = acis["task_id"]
    ev = c.post(f"/api/tasks/{tid}/evidence", headers=h("dortiz"), json={
        "purpose": "absence", "asset_name": "ACIS Dispute Portal", "component": "Apache Struts", "scope": ["/opt/acis"],
        "method": "authenticated_scan", "timestamp": c.get("/api/clock").json()["now"]}).json()
    d = c.get(f"/api/evidence/{ev['id']}").json()
    assert not d["all_pass"] and any(r["check"] == "Component scope" and not r["pass"] for r in d["checks"])
    assert c.post(f"/api/evidence/{ev['id']}/decision", headers=h("rverma"),
                  json={"action": "reject", "comment": "scope too narrow"}).status_code == 200
    assert c.get("/api/canary").json()["runs"] == []
    cl = c.get("/api/claims", params={"claim_type": "monitoring"}).json()[0]
    cd = c.get(f"/api/claims/{cl['id']}").json()
    assert cd["depends_on"] and cd["status"] == "active"
    app.dependency_overrides.clear()


def test_version_log_and_update_flags(db):
    c = client(db)
    c.post("/api/advisories", json=STRUTS, headers=h("pnair"))
    reg = lambda: {a["name"]: a for a in c.get("/api/registry").json()["assets"]}  # noqa: E731
    portal = reg()["Credit Report Portal"]
    struts = next(x for x in portal["components"] if x["name"] == "Apache Struts")
    # 2.3.31 is in the affected range, so PoliBridge flags the update and names the release that fixes it
    assert struts["update_to"] == "2.3.32" and struts["advisories"][0]["cve"] == STRUTS["cve"]
    assert portal["updates_needed"] == 1
    # versions on record before the log existed show as its starting point
    assert {x["component"] for x in portal["version_history"]} == {"Apache Struts", "OpenJDK"}
    # a typed-in version cannot clear an advisory: that needs reviewed remediation evidence (A5)
    r = c.post(f"/api/registry/assets/{portal['id']}/components", json={"name": "Apache Struts", "version": "2.3.32"},
               headers=h("atan"))
    assert r.status_code == 400 and "remediation evidence" in r.json()["detail"]
    # other upgrades are logged with who made them and why
    r = c.post(f"/api/registry/assets/{portal['id']}/components",
               json={"name": "OpenJDK", "version": "8u131", "note": "quarterly patching"}, headers=h("atan"))
    assert r.status_code == 200, r.text
    top = reg()["Credit Report Portal"]["version_history"][0]
    assert (top["component"], top["from"], top["to"], top["actor"], top["note"]) == \
        ("OpenJDK", "8u121", "8u131", "atan", "quarterly patching")
    assert c.post(f"/api/registry/assets/{portal['id']}/components", json={"name": "OpenJDK", "version": "8u131"},
                  headers=h("atan")).status_code == 400  # unchanged
    # an SBOM import logs each change against what was on record
    sbom = {"components": [{"name": "Apache Struts", "version": "2.3.31"}, {"name": "log4j", "version": "2.17.1"}]}
    assert c.post(f"/api/assets/{portal['id']}/sbom", json={"document": sbom}, headers=h("atan")).status_code == 200
    hist = reg()["Credit Report Portal"]["version_history"]
    assert {(x["component"], x["from"], x["to"]) for x in hist if x["source"] == "sbom"} == \
        {("log4j", None, "2.17.1"), ("OpenJDK", "8u131", None)}
    app.dependency_overrides.clear()


def test_required_fix_picks_the_branch():
    from app.services.registry import required_fix
    assert required_fix("2.5.8", "2.3.32, 2.5.10.1") == "2.5.10.1"
    assert required_fix("2.3.20", "2.3.32, 2.5.10.1") == "2.3.32"
    assert required_fix("1.0", None) is None
