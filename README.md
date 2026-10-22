# Miraflores tax workpaper: Okafor-Lindqvist 2025

A web-native workpaper that proves a return is right. It shows where every number came from, what was done to it, who checked it and what is still open. It is not built as a spreadsheet.


## Run locally (2 minutes)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://localhost:8000
```

The packet loads **as delivered** from `data/okafor_lindqvist_2025_packet.json`. To use another packet, set `PACKET_PATH=/path/to/packet.json`. Saved work goes to `workpaper.db` (SQLite) unless `DATABASE_URL` is set.

The data starts clean. To see a workpaper in the middle of review, go to **Firm → Load demo activity** while acting as Helen Strand (partner) or Jo Kimura (administrator).

Tests: `pytest -q`. There are 13 end-to-end tests covering the rules: roles, the partner threshold, reopened sign-offs, bulk actions with undo, masking, the filing freeze, and checks that don't trust the engine.

## Deploy (free): Render + Neon

1. Push this folder to a GitHub repo.
2. **Neon** (neon.tech): create a free project and copy the connection string (`postgresql://…?sslmode=require`). Render's free disk is wiped on every restart, so saved work needs a real database.
3. **Render** (render.com): *New → Blueprint*, then pick the repo (it reads `render.yaml`). When asked, paste the Neon URL into `DATABASE_URL`.
4. Optional: add `ANTHROPIC_API_KEY` to turn on "Explain with AI" and the drafting buttons.
5. Open `https://<your-service>.onrender.com`. Free instances sleep, so the first load can take about 30-60 s.

## Structure

```
app/packet.py    load the packet unchanged; indexes (ev→lines, doc→lines, parents), reference resolver, value anchors
app/checks.py    25 independent checks + 27 reconciliations → findings with evidence, proposed adjustments, est. tax effect
app/store.py     append-only event store (SQLite / Postgres)
app/workflow.py  roles, permissions, derived state: adjustments, sign-off fingerprints, gates, linear roll-up
app/llm.py       optional Claude layer: explains & drafts, citation-validated, masked, never produces numbers
app/main.py      API (read endpoints + one /api/action write endpoint)
app/demo.py      optional demo activity
static/          single-page front end (vanilla JS, no build step)
tests/           rule tests
```

## Using it (90 seconds)

- **Acting as** (top right) switches person and role. Rules are enforced on the server, not just hidden in the UI. Try approving your own adjustment, or opening D08 as Marcus Bell (no clearance).
- **As of** replays the season. Set it to 2026-07-14 for the e-file rejection, or 2028-02-01 for the IRS examination letter.
- **Click any number** to open its trace: source document and field, rule, inputs, where it goes, history across v1/v2/v3, findings, adjustments, verifications and notes.
- **Findings & checks** lists what the engine and the extraction got wrong, each with its evidence and estimated tax effect.
