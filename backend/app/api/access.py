"""Role-based access for the demonstrator. There is no sign-in: the portal sends the chosen role in the X-Role header.
A role may only change what its pages allow. Roles with security clearance can read the whole API (pages share lookups
such as the people list); roles without it read only the ownership directory, with vulnerability, exposure and
remediation detail left out, so it never reaches their browser.
Requests without a role act as the super admin, so scripts and the background sweep keep working."""
import re

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

PAGES = {"registry": "Registry", "documents": "Documents", "acknowledgements": "Acknowledgements",
         "remediations": "Remediations", "alerts": "Alerts", "claims": "Claims", "oversight": "Oversight",
         "admin": "demo settings"}
ALL = [p for p in PAGES if p != "admin"]

ROLES = {
    "super_admin": {"label": "Super admin", "description": "Every feature in PoliBridge, including demo settings",
                    "view": ALL, "edit": ALL + ["admin"]},
    "compliance": {"label": "Risk & Compliance", "description": "Sees everything; runs the programme but not the registry",
                   "view": ALL, "edit": ["documents", "acknowledgements", "remediations", "alerts", "claims"]},
    "owner": {"label": "System owner", "description": "Acknowledges advisories and fixes their systems",
              "view": ["registry", "acknowledgements", "remediations", "claims"], "edit": ["acknowledgements", "remediations"]},
    "reviewer": {"label": "Independent reviewer", "description": "Reviews remediation evidence",
                 "view": ["registry", "acknowledgements", "remediations", "claims"], "edit": ["remediations"]},
    "internal_audit": {"label": "Internal audit", "description": "Sees everything, changes nothing",
                       "view": ALL, "edit": []},
    "executive": {"label": "Executive", "description": "Programme oversight and alerts, read only",
                  "view": ["registry", "oversight", "alerts"], "edit": []},
    "marketing": {"label": "Marketing Executive", "description": "Finds who owns each system and who to contact",
                  "view": ["registry"], "edit": [], "security": False},
}
for _r in ROLES.values():
    _r.setdefault("security", True)  # cleared to see vulnerabilities, exposure, discovery and remediation work
DEFAULT = "super_admin"
# the only reads open to a role without security clearance: the ownership directory and what the shell needs
_UNCLEARED_READS = {"/api/registry", "/api/roles", "/api/health", "/api/clock", "/api/people"}


def role_of(rid: str | None):
    return ROLES.get(rid or DEFAULT)


def cleared(x_role: str | None = Header(default=None)) -> bool:
    """Dependency: whether the calling role may see security detail."""
    role = role_of(x_role)
    return bool(role and role["security"])

# which page a change belongs to; the first match wins, and anything unlisted is a demo setting
_RULES = [(re.compile(p), area) for p, area in [
    (r"^/tasks/\d+/acknowledge$", "acknowledgements"),
    (r"^/tasks/\d+/(escalate|remind)$", "alerts"),
    (r"^/(registry|people|assets|sweep)(/|$)", "registry"),
    (r"^/(documents|sources|source-versions|feeds)(/|$)", "documents"),
    (r"^/(advisories|obligations|guidance)(/|$)", "acknowledgements"),
    (r"^/(tasks|evidence|risk-acceptances)(/|$)", "remediations"),
    (r"^/canary(/|$)", "alerts"),
    (r"^/(claims|monitoring)(/|$)", "claims"),
]]


def area_of(path: str) -> str:
    path = path.removeprefix("/api")
    return next((area for rx, area in _RULES if rx.search(path)), "admin")


async def enforce(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/"):
        return await call_next(request)
    rid = request.headers.get("x-role") or DEFAULT
    role = ROLES.get(rid)
    if role is None:
        return JSONResponse({"detail": f"Unknown role {rid!r}."}, status_code=403)
    if request.method in ("GET", "HEAD"):
        if role["security"] or path.rstrip("/") in _UNCLEARED_READS:
            return await call_next(request)
        return JSONResponse({"detail": f"{role['label']} is not cleared to see this information."}, status_code=403)
    area = area_of(path)
    if area not in role["edit"]:
        return JSONResponse({"detail": f"{role['label']} cannot change {PAGES[area]}. Switch role to do this."}, status_code=403)
    return await call_next(request)


router = APIRouter(prefix="/api")


@router.get("/roles")
def roles():
    return [{"id": k, **v} for k, v in ROLES.items()]
