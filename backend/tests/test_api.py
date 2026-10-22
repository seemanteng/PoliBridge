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
