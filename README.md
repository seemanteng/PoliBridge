# PoliBridge

Human-in-the-loop GRC demonstrator for the 2017 Equifax case (IS4234, Group 1). Every status is a
**claim**: it needs evidence that covers its scope, it expires, and the system tests its own routing.
All data is synthetic; CMDB, directory, scanner and ticketing integrations are represented by seed data.

## Run

```bash
# backend (Python 3.11+)
cd backend && python3 -m venv .venv && .venv/bin/pip install fastapi "uvicorn[standard]" sqlalchemy pyyaml pytest httpx openai python-dotenv
.venv/bin/uvicorn app.main:app --port 8000

# frontend (separate terminal)
cd frontend && npm install && npm run dev      # http://localhost:5180
```

On first start the database is seeded with the **March 2017 replay** (the storyline in `mockups/PoliBridge.pdf`).
The sidebar has a simulated clock (+1h/+1d/+7d/+30d: escalation and expiry are time-driven), a **Replay** /
**Baseline** reset (Baseline = 8 Mar, before the advisory, so you can ingest it yourself), and a user switcher
(the `X-User` header stands in for SSO). Try: switch to *Ama Mensah* (reviewer), or ingest the advisory on Baseline
and advance the clock past 24h/48h.

Optional LLM guidance: copy `backend/.env.example` to `backend/.env` and add `OPENAI_API_KEY`.
Without it a deterministic stub is used. Grounding is verified in code after generation, so a model that
invents a citation is withheld (rule A4) either way.

## Tests

```bash
cd backend && .venv/bin/pytest -q
```
Covers the seven scenarios of report §7.1, each rule A1–F3, the API, and the replay. Tests never call a real LLM.

## Layout

| Path | What |
|---|---|
| `backend/timings.yaml` | Escalation/deadline policy; the rules read it, nothing hard-codes it |
| `backend/app/rules/` | Pure deterministic rules: applicability (A1–A3, A5), impact (C), evidence (E1–E4), versions |
| `backend/app/services/` | `registry` (C1) · `intake`, `rag` (C2) · `assessment` (C3) · `assignment` (C4) · `closure` (C5) · `assurance` (C6) · `views` (UI read models) |
| `backend/app/seed/replay.py` | The demo storyline, played through the real services |
| `backend/app/seed/data.py` | Small fixture organisation used by the tests |
| `frontend/src/pages/` | The six mockup screens |

## Deliberate decisions to know about

- **Timings follow the Build Plan, not the report.** High: acknowledge 24h (manager 24h, CISO 48h), interim control
  48h, remediate 7d; Medium 48h / 30d; Low 5 working days / 90d. Report §6.8, Appendix A, Appendix C (D1–D3) and
  test scenario 2 still say 4h/8h/48h and need updating.
- **Evidence scope must be recursive.** `/opt/acis` (depth 1) does not cover `/opt/acis/webapps/…/struts.jar`;
  `/opt/acis/**` does. This is what rejects the Equifax-style "we scanned it" claim.
- A task the owner fixed on time and that is only waiting for review is not counted overdue.
- Canary tasks and findings are excluded from operational metrics and reported separately.
- Stale component inventory (30 days) turns Applies/Does Not Apply back into Cannot Determine; `Applies` claims
  therefore lapse too, which reopens dependants (F2).

## Not built / limits

No real authentication or connectors; email delivery is simulated by a per-person `deliverable` flag;
regulatory-source diffing is line/clause based (no PDF parsing); report appendices E–I (schema write-ups,
screenshots, AI-use statement) are documentation, not code.
