"""Optional language layer (Claude via the Anthropic Messages API).

Rules this module enforces:
  * The LLM never produces or changes a number on the return. It explains and drafts.
  * Every claim must cite evidence ids in [[...]]; citations that don't resolve in the
    packet are stripped and reported, so a hallucinated reference can't reach a reviewer.
  * Sensitive values (SSNs, account numbers, PINs) are masked before anything is sent.
  * No API key -> the feature reports itself unavailable; nothing else in the app depends on it.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque

from .packet import mask_text

MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
API_URL = "https://api.anthropic.com/v1/messages"
_calls: dict[str, deque] = defaultdict(deque)
RATE_LIMIT = int(os.environ.get("LLM_RATE_PER_HOUR", "30"))

SYSTEM = (
    "You are the explanation assistant inside a CPA firm's tax workpaper. You receive structured, already-verified "
    "facts as JSON. Rules: (1) Never compute, estimate or invent a dollar amount - only repeat amounts that appear in "
    "the facts. (2) After every factual sentence cite the ids it relies on in double brackets, e.g. [[ev-0047]] "
    "[[F1040.11]] [[D73]]. Use only ids that appear in the facts. (3) Plain English a new staff member understands; "
    "short. (4) If the facts do not answer the question, say so."
)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _rate_ok(who: str) -> bool:
    q = _calls[who]
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return False
    q.append(now)
    return True


def mask_json(obj):
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, list):
        return [mask_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: mask_json(v) for k, v in obj.items() if k not in ("ssn", "ip_pin", "account", "routing")}
    return obj


def validate_citations(text: str, exists) -> tuple[str, list[str], list[str]]:
    good, bad = [], []

    def repl(m):
        ref = m.group(1).strip()
        if exists(ref):
            good.append(ref)
            return f"[[{ref}]]"
        bad.append(ref)
        return ""

    out = re.sub(r"\[\[([^\]]+)\]\]", repl, text)
    return out, sorted(set(good)), sorted(set(bad))


def complete(task: str, facts: dict, who: str, exists) -> dict:
    if not available():
        return {"mode": "unavailable", "text": None,
                "message": "AI explanations are off (no ANTHROPIC_API_KEY set). Everything else works without it."}
    if not _rate_ok(who):
        return {"mode": "rate_limited", "text": None, "message": "AI rate limit reached for this hour."}
    payload = {
        "model": MODEL, "max_tokens": 700, "system": SYSTEM,
        "messages": [{"role": "user", "content": f"Task: {task}\n\nFacts (JSON):\n{json.dumps(mask_json(facts), default=str)[:60000]}"}],
    }
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), method="POST", headers={
        "content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError) as ex:
        return {"mode": "error", "text": None, "message": f"AI call failed: {ex}"}
    text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
    text, cited, rejected = validate_citations(text, exists)
    return {"mode": "ai", "model": body.get("model", MODEL), "text": text, "cited": cited, "rejected_citations": rejected}
