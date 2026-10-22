# How AI was used

> **Edit this so it accurately describes what you did.** The brief asks candidates to say how they used AI, and being specific helps.

## While building

- I used **Claude (Anthropic)** as a pair programmer and analyst, working through the packet with it before writing code.
- **Analysis.** Claude parsed the packet, compared every extracted value with the printed page, re-footed the engine's sums and reconciled brokerage lots, payments and K-1s. I reviewed each candidate error against the source documents and decided which ones are real errors, which are judgment calls and which are the engine being right.
- **Design.** I chose the approach (event-sourced workpaper on an immutable packet, deterministic checks, LLM limited to explanation) after discussing alternatives. The tradeoffs are in `DECISIONS.md`.
- **Code.** Claude wrote most of the code to my specification. I reviewed it, ran it, and checked it in a browser and with the automated tests in `tests/`.

## In the product

- An optional **"Explain with AI"** and **"Draft client question / review note"** feature uses Claude Sonnet 5.5 through the Anthropic API.
- Guardrails:
  - It receives only structured, already-verified facts.
  - It must cite evidence ids; ids that don't exist in the packet are removed and reported.
  - SSNs, account numbers and PINs are masked before sending.
  - It never produces or changes a number.
- Every check, calculation and reconciliation is deterministic Python. The workpaper runs fully without AI.
