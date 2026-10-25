"""RAG guidance. Retrieval is over the source clause, internal policies and asset context. Grounding is verified
deterministically *after* generation, so a model that invents a citation is withheld (A4). RAG never confirms
applicability, accepts risk or authorises closure; it only produces draft text, which approvals.yaml routes."""
import json
import os
import re

from sqlalchemy import select

from ..clock import clock
from ..config import APPROVALS
from ..models import Obligation, Guidance, SourceVersion, Source, Asset, Advisory, Person, Task
from ..rules import versions
from . import audit, approvals
from .common import raise_finding, notify

MIN_OVERLAP = 0.35


def _asset_passages(db, ob: Obligation, asset: Asset) -> list[dict]:
    """What the register knows about this one system: its component record and the evidence it will need."""
    adv = db.get(Advisory, ob.advisory_id) if ob.advisory_id else None
    if adv is None:
        return []
    comp = next((c for c in asset.components if versions.norm(c.name) == versions.norm(adv.product)), None)
    pid = f"{asset.name} (asset register)"
    if comp:
        when = f" of {asset.inventory_verified_at:%d %b %Y}" if asset.inventory_verified_at else ""
        line = (f"{asset.name} runs {comp.name} {comp.version} at {comp.install_path or 'an unrecorded path'}, "
                f"according to its {(asset.inventory_method or 'unverified').replace('_', ' ')} inventory{when}.")
    else:
        line = (f"{asset.name} has no current component record for {adv.product}, so whether it runs an affected "
                f"version cannot be determined.")
    roots = ", ".join(r.rstrip("/") + "/**" for r in asset.app_roots or []) or "every application folder"
    return [{"id": pid, "text": line},
            {"id": pid, "text": f"Evidence for {asset.name} must cover {roots} and use an authenticated scan or a "
                                f"direct version check."}]


def _passages(db, ob: Obligation, asset: Asset | None = None) -> list[dict]:
    out = []
    if ob.quotation:
        out.append({"id": f"{ob.source_ref}", "text": ob.quotation})
    if ob.advisory_id:
        a = db.get(Advisory, ob.advisory_id)
        if a.description:
            out.append({"id": f"{a.cve} (NVD)", "text": a.description})
        out.append({"id": a.cve, "text": f"{a.cve}: {a.product} versions {', '.join(a.affected_versions)} are "
                                         f"affected. Fixed version: {a.fixed_version or 'n/a'}."})
    if asset is not None:
        out += _asset_passages(db, ob, asset)
    for s in db.scalars(select(Source).where(Source.kind == "policy")):
        v = db.scalar(select(SourceVersion).where(SourceVersion.source_id == s.id).order_by(SourceVersion.id.desc()))
        if v:
            for line in v.text.splitlines():
                if line.strip():
                    out.append({"id": f"{s.name}", "text": line.strip()})
    return out


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _conflict(passages: list[dict]) -> str | None:
    """Different 'within N hours/days' requirements across retrieved passages."""
    seen = {}
    for p in passages:
        for n, unit in re.findall(r"within\s+(\d+)\s*(hours?|days?)", p["text"].lower()):
            seen.setdefault(unit.rstrip("s"), set()).add(int(n))
    for unit, nums in seen.items():
        if len(nums) > 1:
            return f"sources give conflicting {unit} limits: {sorted(nums)}"
    return None


def verify_grounding(text: str, citations: list[dict], passages: list[dict]) -> tuple[bool, str | None]:
    if not citations:
        return False, "no citations"
    for c in citations:
        quote = c.get("quote", "")
        if not any(quote and quote.lower() in p["text"].lower() for p in passages):
            return False, f"citation not found verbatim in sources: '{quote[:60]}'"
    cited = set().union(*[_tokens(c["quote"]) for c in citations])
    overlap = len(_tokens(text) & cited) / max(1, len(_tokens(text)))
    if overlap < MIN_OVERLAP:
        return False, f"guidance weakly grounded (overlap {overlap:.2f})"
    return True, None


def _stub(ob: Obligation, role: str, passages: list[dict], asset: Asset | None = None) -> dict:
    if asset is not None:  # the asset's own record, the fixed version, and the evidence it needs
        own = [p for p in passages if p["id"].endswith("(asset register)")]
        fix = next((p for p in passages if "Fixed version" in p["text"]), None)
        use = own[:1] + ([fix] if fix and "no current component record" not in own[0]["text"] else []) + own[1:2]
        return {"text": f"For the {role} of {asset.name}: " + " ".join(p["text"] for p in use),
                "citations": [{"source": p["id"], "quote": p["text"]} for p in use]}
    p = passages[0]
    return {"text": f"For {role}: {ob.required_action or 'review this requirement'}. Source says: \"{p['text']}\"",
            "citations": [{"source": p["id"], "quote": p["text"]}]}


def _llm(ob: Obligation, role: str, passages: list[dict]) -> dict:
    from openai import OpenAI
    client = OpenAI()
    prompt = (
        "Draft short guidance for the given role using ONLY the numbered passages. Return JSON "
        '{"text": str, "citations": [{"source": str, "quote": <exact substring of a passage>}]}. '
        "Do not decide applicability, accept risk or declare anything closed.\n"
        f"Role: {role}\nObligation: {ob.title}\nPassages:\n" +
        "\n".join(f"[{i}] ({p['id']}) {p['text']}" for i, p in enumerate(passages, 1)))
    r = client.chat.completions.create(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                                       messages=[{"role": "user", "content": prompt}],
                                       response_format={"type": "json_object"}, temperature=0)
    return json.loads(r.choices[0].message.content)


def _draft(db, ob: Obligation, role: str, asset: Asset | None = None) -> dict:
    """{text, citations, grounded, reason, engine}"""
    passages = _passages(db, ob, asset)
    if not passages:
        return {"text": "", "citations": [], "grounded": False, "reason": "no source passages retrieved", "engine": None}
    conflict = _conflict(passages)
    engine = "stub"
    try:
        if os.getenv("OPENAI_API_KEY"):
            draft, engine = _llm(ob, role, passages), "openai"
        else:
            draft = _stub(ob, role, passages, asset)
    except Exception as e:  # network/model failure -> deterministic fallback, noted
        draft, engine = _stub(ob, role, passages, asset), f"stub (llm failed: {type(e).__name__})"
    ok, why = verify_grounding(draft.get("text", ""), draft.get("citations", []), passages)
    if conflict:
        ok, why = False, conflict
    return {"text": draft.get("text", "") if ok else "", "citations": draft.get("citations", []), "grounded": ok,
            "reason": None if ok else f"{why} [{engine}]", "engine": engine}


def _withhold(db, ob: Obligation, g: Guidance):
    to = approvals.withheld_to(db, ob)
    raise_finding(db, "guidance_withheld", "Medium", f"Guidance withheld: {ob.title}"
                  + (f" · {g.department}" if g.department else ""),
                  f"{g.reason}. Write or approve the guidance by hand (A4).", assigned_to=to)


def generate_guidance(db, ob: Obligation, role: str) -> Guidance:
    """Obligation-level draft, for the fix in general. It names no recipient, so it is shown, not released."""
    d = _draft(db, ob, role)
    g = Guidance(obligation_id=ob.id, role=role, text=d["text"], citations=d["citations"], grounded=d["grounded"],
                 status="draft" if d["grounded"] else "withheld", reason=d["reason"], engine=d["engine"],
                 created_at=clock.now())
    db.add(g)
    db.flush()
    audit.log(db, "system", "guidance", g.id, g.status, obligation=ob.id, reason=g.reason)
    return g


def guidance_for_task(db, ob: Obligation, task: Task) -> Guidance:
    """C1 fast path: the system owner gets advice about their own system at once; Information Security checks it
    alongside. A held-back draft goes to Compliance instead (A4)."""
    asset = db.get(Asset, task.asset_id)
    recipient = db.get(Person, task.owner_id) if task.owner_id else None
    d = _draft(db, ob, "system owner", asset)
    consult = approvals.holder(db, APPROVALS["advisory"]["consult"])
    if consult and recipient and consult.id == recipient.id:
        consult = None
    g = Guidance(obligation_id=ob.id, role="system owner", text=d["text"], citations=d["citations"],
                 grounded=d["grounded"], reason=d["reason"], engine=d["engine"], asset_id=asset.id,
                 recipient_id=recipient.id if recipient else None, consult_id=consult.id if consult and d["grounded"] else None,
                 status="released" if d["grounded"] else "withheld", created_at=clock.now())
    db.add(g)
    db.flush()
    audit.log(db, "system", "guidance", g.id, g.status, obligation=ob.id, asset=asset.name, reason=g.reason)
    if g.status == "withheld":
        _withhold(db, ob, g)
    return g


def guidance_for_department(db, ob: Obligation, department: str, head: Person | None) -> Guidance:
    """Clause guidance for one department head. It waits for the approver approvals.yaml names for the clause's
    topic; the approver is never the recipient."""
    topic, approver, consult = approvals.route_clause(db, ob, head)
    d = _draft(db, ob, f"{department}")
    g = Guidance(obligation_id=ob.id, role=department, department=department, text=d["text"], citations=d["citations"],
                 grounded=d["grounded"], reason=d["reason"], engine=d["engine"], topic=topic,
                 recipient_id=head.id if head else None, approver_id=approver.id if approver else None,
                 consult_id=consult.id if consult else None,
                 status="pending_approval" if d["grounded"] else "withheld", created_at=clock.now())
    db.add(g)
    db.flush()
    audit.log(db, "system", "guidance", g.id, g.status, obligation=ob.id, department=department,
              approver=approver.directory_id if approver else None, reason=g.reason)
    if g.status == "withheld":
        _withhold(db, ob, g)
    elif approver:
        notify(db, person=approver, kind="guidance_approval",
               message=f"Approve guidance for {department} on {ob.title} before it is released.")
    return g


def write_guidance(db, ob: Obligation, author: Person, *, text: str, citations: list[dict],
                   department: str | None = None, asset_id: int | None = None) -> Guidance:
    """A4: Compliance or Legal write the guidance themselves when a draft is held back or rejected. The same
    citation check applies, and the author can never be the recipient."""
    if author.role not in ("compliance", "legal", "cco"):
        raise PermissionError("only Compliance, Legal or the CCO write guidance by hand")
    if not (text or "").strip():
        raise ValueError("write the guidance")
    asset = db.get(Asset, asset_id) if asset_id else None
    passages = _passages(db, ob, asset)
    ok, why = verify_grounding(text, citations, passages)
    if not ok:
        raise ValueError(f"A4: {why}")
    recipient = None
    if asset is not None:
        t = db.scalar(select(Task).where(Task.obligation_id == ob.id, Task.asset_id == asset.id).order_by(Task.id.desc()))
        recipient = db.get(Person, t.owner_id) if t and t.owner_id else None
    elif department:
        row = next((d for d in ob.departments or [] if d["department"] == department), None)
        recipient = db.scalar(select(Person).where(Person.name == row.get("owner"))) if row and row.get("owner") else None
    if recipient and recipient.id == author.id:
        raise PermissionError("the recipient of guidance cannot write it for themselves")
    for old in db.scalars(select(Guidance).where(Guidance.obligation_id == ob.id, Guidance.department == department,
                                                 Guidance.asset_id == asset_id,
                                                 Guidance.status.in_(("withheld", "rejected", "pending_approval")))):
        old.status = "superseded"
    g = Guidance(obligation_id=ob.id, role=department or "system owner", department=department, asset_id=asset_id,
                 text=text.strip(), citations=citations, grounded=True, engine="human", human_authored=True,
                 recipient_id=recipient.id if recipient else None, approver_id=author.id, approved_at=clock.now(),
                 status="released", created_at=clock.now())
    db.add(g)
    db.flush()
    audit.log(db, author.directory_id, "guidance", g.id, "written", obligation=ob.id, department=department)
    approvals._release(db, g)
    return g


def passages_for(db, ob: Obligation, asset_id: int | None = None) -> list[dict]:
    return _passages(db, ob, db.get(Asset, asset_id) if asset_id else None)
