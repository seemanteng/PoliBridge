"""RAG guidance. Retrieval is over the source clause, internal policies and asset
context. Grounding is verified deterministically *after* generation, so a model
that invents a citation is withheld (A4). RAG never confirms applicability,
accepts risk or authorises closure; it only produces draft text."""
import json
import os
import re

from sqlalchemy import select

from ..models import Obligation, Guidance, SourceVersion, Source, Asset, Advisory
from . import audit

MIN_OVERLAP = 0.35


def _passages(db, ob: Obligation) -> list[dict]:
    out = []
    if ob.quotation:
        out.append({"id": f"{ob.source_ref}", "text": ob.quotation})
    if ob.advisory_id:
        a = db.get(Advisory, ob.advisory_id)
        out.append({"id": a.cve, "text": f"{a.cve}: {a.product} versions {', '.join(a.affected_versions)} are "
                                         f"affected. Fixed version: {a.fixed_version or 'n/a'}."})
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


def _stub(ob: Obligation, role: str, passages: list[dict]) -> dict:
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


def generate_guidance(db, ob: Obligation, role: str) -> Guidance:
    passages = _passages(db, ob)
    if not passages:
        g = Guidance(obligation_id=ob.id, role=role, status="withheld", grounded=False,
                     reason="no source passages retrieved")
    else:
        conflict = _conflict(passages)
        engine = "stub"
        try:
            if os.getenv("OPENAI_API_KEY"):
                draft, engine = _llm(ob, role, passages), "openai"
            else:
                draft = _stub(ob, role, passages)
        except Exception as e:  # network/model failure -> deterministic fallback, noted
            draft, engine = _stub(ob, role, passages), f"stub (llm failed: {type(e).__name__})"
        ok, why = verify_grounding(draft.get("text", ""), draft.get("citations", []), passages)
        if conflict:
            ok, why = False, conflict
        g = Guidance(obligation_id=ob.id, role=role, text=draft.get("text", "") if ok else "",
                     citations=draft.get("citations", []), grounded=ok,
                     status="draft" if ok else "withheld", reason=None if ok else f"{why} [{engine}]")
    db.add(g)
    db.flush()
    audit.log(db, "system", "guidance", g.id, g.status, obligation=ob.id, reason=g.reason)
    return g
