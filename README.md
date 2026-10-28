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

On first start the database is seeded with the organisation as of today (staff, departments, systems, the
approved-source registry) and nothing pre-played: the clock is real time. Three mock advisory feeds (CISA/NVD, Apache,
Atlassian) then deliver a queue of five advisories, starting three minutes after a reset; **Documents → Security feeds →
Send next demo alert** delivers the next one at once. CVE-2017-5638 arrives from Apache first and from NVD second
and is merged into one obligation. You can still ingest an advisory by hand on **Acknowledgements** (**Fetch from NVD**
pulls a CVE live). `npm run reset` returns the data to that starting point; a database seeded before this version
needs a reset to get the sources and feeds. Feeds, escalation and source reviews are checked every minute; use
**Escalate** on the Alerts page to show escalation without waiting 24 hours.

To show change detection, open **Documents → Files**, pick `FTC_Safeguards_Rule_2022_revised_excerpt.txt` and
**Use as a new version** of the FTC Safeguards Rule: the 2016 → 2022 comparison finds changed, renumbered, moved and
removed clauses, and supersedes the two seeded obligations built on the 2016 wording.

There is no sign-in. Instead, the "Viewing as" menu at the bottom of the sidebar switches between demo roles: Super admin (every feature),
Risk & Compliance, System owner, Independent reviewer, Internal audit and Executive. Each role sees only its pages, and the backend refuses
changes the role may not make; the roles are defined in `backend/app/api/access.py`. Actions that belong to
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
| `backend/timings.yaml` | Escalation/deadline policy and feed thresholds; the rules read it, nothing hard-codes it |
| `backend/approvals.yaml` | Who approves guidance per clause topic, who checks alongside, sign-off window and escalation |
| `backend/app/rules/` | Pure deterministic rules: applicability (A1–A3, A5), impact (C), evidence (E1–E4), versions |
| `backend/app/services/` | `registry` (C1) · `intake`, `sources`, `feeds`, `rag`, `approvals` (C2) · `assessment` (C3) · `assignment` (C4) · `closure` (C5) · `assurance` (C6) · `views` (UI read models) |
| `backend/app/seed/sources.py` | Approved-source registry, paraphrased regulation excerpts and the mock feed queue |
| `backend/app/seed/replay.py` | The demo storyline, played through the real services |
| `backend/app/seed/data.py` | Small fixture organisation used by the tests |
| `frontend/src/pages/` | The screens; `pages/documents/` holds the Documents tabs (sources, versions, feeds, guidance, approval map, quarterly summary, files) |

## Deliberate decisions to know about

- **Timings follow the Build Plan, not the report.** High: acknowledge 24h (manager 24h, CISO 48h), interim control
  48h, remediate 7d; Medium 48h / 30d; Low 5 working days / 90d. Report §6.8, Appendix A, Appendix C (D1–D3) and
  test scenario 2 still say 4h/8h/48h and need updating.
- **Evidence scope must be recursive.** `/opt/acis` (depth 1) does not cover `/opt/acis/webapps/…/struts.jar`;
  `/opt/acis/**` does. This is what rejects the Equifax-style "we scanned it" claim.
- A task the owner fixed on time and that is only waiting for review is not counted overdue.
- Canary tasks and findings are excluded from operational metrics and reported separately. Canaries travel through the
  CISA/NVD feed and the real matching rule, so a feed outage fails them at their first step.
- **Feed ingest rule.** An advisory is ingested when its product is in some system's component record, or it is
  exploited, scores CVSS 7.0 or more, or has no score. Severe advisories are always ingested because a system with no
  component record could be running anything. The rest are logged with the reason, not ingested.
- **Sources decay.** Re-checking a manual source renews a `source_currency` claim that expires on the source's review
  cycle; a lapse raises a finding and flags the obligations quoting it. A feed with 3 failed polls in a row, or none
  successful for 24 hours, raises a finding.
- **Approval routing.** Advisory guidance reaches the system owner at once and the CISO checks it alongside (C1); clause
  guidance waits for the topic's approver in `approvals.yaml`, who can never be the recipient (the CCO steps in).
  Remediation closure also waits for the C2–C4 sign-offs a task's impact requires.
- Stale component inventory (30 days) turns Applies/Does Not Apply back into Cannot Determine; `Applies` claims
  therefore lapse too, which reopens dependants (F2).

## Not built / limits

No real authentication or connectors: the advisory feeds are mock queues, and email delivery is simulated by a
per-person `deliverable` flag. Regulatory texts in the seed are short paraphrased excerpts. Change detection is
clause based (one clause per line, matched by number, then by wording); PDFs are read with pypdf, scanned PDFs are not.
Report appendices F–I (prompt write-up, screenshots, AI-use statement) are documentation, not code.
