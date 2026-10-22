# Core idea and data model

## The idea: the return as a chain of evidence

A spreadsheet workpaper asks you to trust a grid of numbers. This one starts from what the workpaper has to **prove**: where each number came from, what was done to it, who checked it and what is still open. Every number is clickable and opens its own proof.

```
source field  ──read as──▶  extracted value  ──rule──▶  return line  ──feeds──▶  total  ──▶  tax
(doc, page,                 (ev-id, confidence)          (key, rule,                (linear roll-up
 span/bbox)                                               inputs, sources)            or "needs engine")
        ▲                           ▲                           ▲
   verification              finding (our check)          adjustment / sign-off / note / judgment
   (method, who, when)        evidence + est. effect        (who, why, effect, status)
```

Three design rules follow from this:

1. **Never edit the packet.** The pipeline's output (74 documents, 3,012 values, 3 engine versions) is loaded byte-for-byte and treated as immutable evidence. Everything people do is stored on top of it.
2. **Every human action is an event.** Adjusting, verifying, noting, asking the client, recording a judgment, signing off, approving, filing, viewing sensitive data and undoing are all appended to one event log with who/when/why. State is *derived* by replaying the log, which gives several things almost for free:
   - Full history, and the original value is never lost.
   - Undo, which is just another event.
   - "What changed since I last looked".
   - Replay to any date.
3. **Don't trust the engine, but don't pretend to be it.** Independent checks recompute from source evidence. When the workpaper changes a number, sums and carries roll up exactly (linear roll-up). Non-linear lines (tax, limits) are flagged "engine re-run needed", and totals show a clearly labelled estimate using the packet's `tax_effect_rates`.

## Data model

| Entity | Source | Key fields |
|---|---|---|
| **Document** | packet (immutable) | id, type, pages[printed text], received, extraction status → *disposition* (used / superseded / explained / needs reason) |
| **Value** | packet | ev-id, doc, page, field, value, confidence → **anchor** (char span on the simulated page; bbox for a real PDF) |
| **Line** | packet (v1/v2/v3) | key, rule, inputs[], sources[], note → *effective value*, *rolled_up*, *needs_engine* |
| **Reference** | packet formats | `ev-` `D` `qa-` `msg-` `call-` `py:/ptr` `agency:/ptr` `asset:` `household:` `ref:param.` `timeline:` → one resolver returns a displayable node |
| **Finding** | our checks | id, severity, kind (engine / extraction / client data / judgment / completeness), evidence[], lines[], proposed adjustments[{line, from, to, category}], estimated effect, how found |
| **Reconciliation** | our checks | left vs right, difference, evidence (e.g. books revenue = 1099-NEC + non-1099 deposits) |
| **Event** | people (append-only) | id, at, actor, type, target, data, batch |

Derived from the events:
- **Adjustment** (proposed → approved / rejected / reverted; partner needed if effect > 1,000)
- **Verification** (one of the firm's five methods)
- **Note** (comment, blocking review note, or client question that becomes evidence)
- **Judgment** (topic, decision, authority)
- **Sign-off** (area, level, **fingerprint** of the numbers it covered)
- **Filing state** (client approved on fingerprint X → filed and frozen → amendment)

**Sign-offs reopen themselves.** A sign-off stores a hash of its area's effective values, adjustments and finding statuses. If anything in that area changes later, the hash no longer matches, so the sign-off shows as *reopened* and lists which lines changed. Filing works the same way. The client approves fingerprint X; if the return changes after that, transmission is refused.

**Roles are enforced on the server**, using the firm standards in the packet:
- Nobody reviews work they prepared.
- Adjustments with an effect over 1,000 need a partner.
- Every judgment records its authority.
- Open review notes block filing.
- Staff without clearance see masked SSNs and accounts, and every unmasked view is logged.
- A staff member can't act before their start date.

**Documents are swappable.** The document view only consumes *anchors* (page + span/bbox per value). Simulated pages are rendered from `printed_content` today. A real PDF would supply bounding boxes from OCR, and nothing else in the UI changes.
