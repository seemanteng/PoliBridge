# PoliBridge

Human-in-the-loop GRC demonstrator for the 2017 Equifax case (IS4234, Group 1). Every status is a
**claim**: it needs evidence that covers its scope, it expires, and the system tests its own routing.
All data is synthetic; CMDB, directory, scanner and ticketing integrations are represented by seed data.

## Run

```bash
# backend (Python 3.11+)
cd backend && python3 -m venv .venv && .venv/bin/pip install fastapi "uvicorn[standard]" sqlalchemy pyyaml pytest httpx openai python-dotenv pypdf
.venv/bin/uvicorn app.main:app --port 8000

# frontend (separate terminal)
cd frontend && npm install && npm run dev      # http://localhost:5180
```

On first start the database is seeded with the organisation as of today (staff, departments, six systems, approved
sources) and nothing pre-played: the clock is real time. Ingest an advisory on **Acknowledgements** (**Fetch from NVD** pulls
CVE-2017-5638 live) to see each system get a label, then sign off for the owners.
`npm run reset` returns the data to that starting point. Escalation and document reminders are checked every minute; use
**Escalate** on the Alerts page to show escalation without waiting 24 hours.

There is no sign-in and no role-based view: the Risk & Compliance team runs PoliBridge and can see and edit everything. Actions that belong to
an owner or an approver (acknowledging, signing off, submitting evidence, approving a risk acceptance) are recorded for that named person, and the
audit log notes that Risk & Compliance entered them. The control rules that compare people still apply, for example the evidence reviewer must
be someone other than whoever submitted or fixed it.

Everything is saved in `backend/polibridge.db` and survives refreshes and restarts. Only `npm run reset` clears it. To try things
without touching the demo data, start a throwaway copy: `POLIBRIDGE_DATA_DIR=/tmp/pb-rehearsal npm run dev:backend`.

The March 2017 storyline is still in `backend/app/seed/replay.py` and runs on a frozen clock for tests and development
(`POST /api/demo/reset?mode=replay`); the UI does not offer it.

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
