# Video walkthrough: script (≈7 minutes)

**Before recording:**
1. Act as **Helen Strand** and go to **Firm → Reset saved work → Load demo activity**.
2. Set **As of** to `2026-07-01`.
3. Act as **Priya Raman**.

Record your screen with Loom or OBS and upload the video to YouTube as *unlisted*. Talk naturally; the lines below are a guide, not a script to read.

---

### 1. The idea (0:00-0:45) · Overview page
> "The brief said: don't rebuild a spreadsheet, invent from the purpose. A workpaper's purpose is to *prove* the return: where each number came from, what was done to it, who checked it, what's open. So I built it as a chain of evidence. Every number on screen is clickable and opens its own proof."

Point at:
- The four totals, showing engine v3 vs workpaper with approved changes and pending ones.
- "What blocks filing".
- The area cards with sign-off status and time against budget.

### 2. Trace a number (0:45-2:00)
1. Click **Capital gain or (loss) F1040.7**. Show the rule, its input (SchD.16), where it goes, and the history v1 → v2 → v3 with what triggered each.
2. Go to **Return → SchC** and click **Gross receipts**.
   > "Here's a source: the engine added the 1099-K on top of the books. I can see the exact field highlighted on the page."
3. Click **open document at this field** to land on D21 with the value highlighted.
4. Go back and open **D23 mileage log**.
   > "Every extracted value is anchored to its location on the page. Blue means it's on the return, amber is low confidence, red has a finding. Today these pages are simulated from the packet text; the view only consumes anchors, so a real PDF with OCR boxes drops straight in."

### 3. Checks that don't trust the engine (2:00-3:30) · Findings & checks
> "These come from deterministic Python checks that recompute from source evidence. Each finding says how it surfaced, shows its evidence, proposes an adjustment, and estimates the tax effect with the packet's rate table, because we can't re-run the engine."

Walk through three:
- **RSU basis.** $0 basis on the 1099-B; the plan statement had the real basis. About 15.5k of federal tax.
- **1099-K double count.** Books 151,300 reconcile exactly to the 1099-NECs plus small clients. Mention that the 2028 IRS letter asks exactly this, and we already have the answer.
- **Provider TIN misread at 0.97 confidence.** Found by re-reading every identifier against the page.

Then open **Independent checks**:
> "27 reconciliations. Most agree, which matters too: it's evidence the engine was right."

### 4. Change and review (3:30-5:00)
1. Open **Review & sign-off** and show the adjustment queue. Approved, pending, and the "partner" tag on anything over 1,000.
2. Switch **Acting as → Daniel Ortiz (reviewer)**. Try to approve a pending adjustment over 1,000. The server refuses: "partner approval required".
   > "Roles are enforced on the server, not just hidden."
3. Approve a small one. Sign off **Business** as reviewer.
4. Switch to **Priya** and propose a change to a Business line (e.g. Advertising), with a reason. Switch to **Helen** and approve it.
5. Back on Review, Daniel's Business sign-off now shows **reopened** and lists the changed line.
   > "Sign-offs store a fingerprint of what they covered, so a change anywhere underneath reopens them automatically."
6. Show **bulk + undo**: on Documents, *Confirm all suggested reasons*, then click **Undo** in the toast.
7. Show **Activity → Engine versions → compare v2 and v3**.

### 5. People, client, filing and after (5:00-6:15)
1. Switch to **Marcus Bell** (no clearance) and open **D08**. The SSNs are masked. Switch to **Jo Kimura** and show the **Firm → view log**.
2. **Client portal:** a client question and its answer become evidence. Approval is locked until the gate is green.
3. **Filing & after:** set As of to `2026-07-14`. Show the e-file rejection for Theo's SSN, and point out that it was **already a finding in January** from the prior-year return.
4. Set As of to `2028-02-01` and open **Notice → Build response record**.
   > "It's the self-contained record for the examiner: the items under exam, the evidence, who verified it, and our reconciliation."

### 6. Technical decisions and tradeoffs (6:15-7:15)
- **FastAPI + plain JS.** Logic in testable Python; no build step; one URL.
- **Event sourcing.** Undo, history, "since I last looked" and reopening sign-offs all come from one append-only log. Recomputing state per request is fine at this size; production would add snapshots.
- **No fake recomputation.** Sums and carries roll up exactly (I recover each sum's signs by search, which also re-foots all of the engine's arithmetic). Tax and limits are flagged "engine re-run needed", and totals use the rate estimate.
- **AI.** Claude only explains and drafts. It must cite evidence ids, invalid citations are stripped, data is masked, and it never touches numbers. The checks are plain Python on purpose.
- **Storage.** SQLite locally and Postgres in production, so saved work survives free-hosting restarts.
- **What I'd do next.** Real authentication, engine re-runs on demand, per-line sign-offs, OCR anchors for real PDFs.
- **How I used AI to build it.** Say it plainly; see `AI_USAGE.md`.
