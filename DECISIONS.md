# Decisions and tradeoffs

| # | Decision | Why | Tradeoff / what I'd do next |
|---|---|---|---|
| 1 | **Python FastAPI backend + plain HTML/JS front end, no build step** | Checks, tracing and rules live in testable Python. The UI is one static folder that anyone can read. One process serves both, so there's no CORS and one URL | No component framework. A larger product would move to React/TypeScript with typed API clients |
| 2 | **The packet is loaded unchanged; all interpretation is in code** | The brief says "loading the packet as delivered". The packet becomes immutable evidence and could be swapped for another client's | Indexes are rebuilt at start-up (≈0.05 s here); a big firm would cache them |
| 3 | **Event sourcing: every human action is an append-only event; state is replayed** | History, who/why, undo, "since I last looked", sign-off reopening and replay to a date come from one mechanism instead of five features | State is recomputed per request. That's fine for hundreds of events; production would add snapshots |
| 4 | **Sign-offs store a fingerprint (hash) of the numbers they covered** | "Sign-offs that reopen when what was signed changes" becomes automatic and exact. The UI can list which lines changed | Granularity is per area. Per-line sign-off is possible with the same mechanism |
| 5 | **We don't re-run the engine.** Linear lines (sums, carries) roll up exactly; non-linear lines are flagged "engine re-run needed"; totals use the packet's `tax_effect_rates` | The README forbids recomputing tax. Pretending to would be the least trustworthy thing a workpaper could do | Estimates can drift when a change crosses a threshold (e.g. SALT cap); the UI labels them as estimates |
| 6 | **Signs for engine sums are recovered by search** (which ±1/0 pattern reproduces the engine's own value) | The packet lists inputs but not signs. Recovering them lets an adjusted lot flow up to AGI. The same search re-foots all 85 engine sums independently | Lines with more than 8 inputs are only accepted if they are plain sums |
| 7 | **Checks are deterministic Python, not an LLM** | A check that "doesn't trust the engine" must not be another thing that can be confidently wrong. Each check states how it found the problem and cites evidence ids | Some checks encode domain knowledge (e.g. 1098-C box 4a limits the gift). They're explicit and unit-tested rather than learned |
| 8 | **LLM (Claude Sonnet 5.5) only explains and drafts.** It never writes numbers, every claim must cite ids, unknown citations are stripped, sensitive data is masked first, and it's rate-limited | Language is where an LLM helps a new hire. Numbers are where it hurts. The citation validator makes hallucinated references visible and keeps them out of the record | Needs `ANTHROPIC_API_KEY`. Without it the app is fully functional and the trace is the explanation |
| 9 | **Simulated documents rendered from `printed_content`, with values located by character span ("anchors")** | The UI only consumes anchors (page + span/bbox), so real PDFs can replace the simulated pages by supplying OCR boxes | Span matching is heuristic (sequential search). A value that isn't found is itself a signal; that's how the TIN misread surfaced |
| 10 | **Roles enforced on the server from the firm's own standards** | The brief asks for "enforced roles". Hiding a button isn't enforcement | The demo has no real login; the role switcher stands in for authentication |
| 11 | **Masking by default for staff without clearance; every unmasked view logged** | This is the firm rule in the packet. Masking happens server-side, so masked data never reaches the browser | Cleared staff opening a document containing an SSN is logged as a view, which is coarse but auditable |
| 12 | **"As of" date replays the scripted season** (agency records, e-file, notices) | Lets a reviewer step from intake → rejection → filing → 2028 exam without editing data | The workpaper's own events use real time, not scenario time |
| 13 | **SQLite locally, Postgres (Neon) in production through one `DATABASE_URL`** | Free hosting wipes local disks; saved work must survive restarts | Two free services to set up instead of one |
| 14 | **Large detail by exception:** 295 sales summarized by box and broker; only zero-basis, wash-sale and flagged lots listed by default | "Hundreds of sales without swamping the review" | Every lot is still one click away (filter by box) |
| 15 | **Live updates by polling** (every 8 s, ignoring your own changes and view-log events) | Simple and robust for "several people at once" on free hosting | WebSockets would be instant; polling is good enough for a prototype |

## Known limitations

- No real authentication. The role switcher stands in for login.
- California AMT and the HVAC capitalization are flagged as judgments, not computed. That needs the engine.
- The notice response is a printable record (browser "Save as PDF"), not a generated letter.
