"""FastAPI app: read APIs over the packet + derived state, one write API for actions."""
from __future__ import annotations

import base64
import os
import re
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import llm
from .checks import run_all, variance_review
from .packet import AREA_LABELS, CURRENT_VERSION, Packet, area_of, mask_text, SENSITIVE_PATTERNS
from .store import Store
from .workflow import LEVELS, SIGNOFF_AREAS, State, users

ROOT = Path(__file__).resolve().parent.parent
PACKET_PATH = os.environ.get("PACKET_PATH", str(ROOT / "data" / "okafor_lindqvist_2025_packet.json"))
DEFAULT_AS_OF = "2026-07-01"

P = Packet(PACKET_PATH)
FINDINGS, RECONS = run_all(P)
STORE = Store()
app = FastAPI(title="Miraflores workpaper", version="1.0")

DIAGNOSTICS = [{"id": f"DIAG-{d['code']}", "title": d["message"], "severity": d["severity"], "kind": "engine_diagnostic",
                "summary": d["message"], "evidence": d["refs"], "lines": [r for r in d["refs"] if r in P.lines[CURRENT_VERSION]],
                "adjustments": [], "effect": {}, "how_found": "Engine diagnostic (v3)",
                "area": area_of(next((r for r in d["refs"] if r in P.lines[CURRENT_VERSION]), "x"))}
               for d in P.versions[CURRENT_VERSION]["diagnostics"]]
ALL_FINDINGS = FINDINGS + DIAGNOSTICS
FINDING_BY_ID = {f["id"]: f for f in ALL_FINDINGS}
LINE_FINDINGS = defaultdict(list)
REF_FINDINGS = defaultdict(list)
for f in ALL_FINDINGS:
    for k in f["lines"]:
        LINE_FINDINGS[k].append(f["id"])
    for r in f["evidence"]:
        REF_FINDINGS[r].append(f["id"])
        if r.startswith("ev-") and r in P.ev:
            REF_FINDINGS[P.ev[r]["document_id"]].append(f["id"])

SUGGESTED_DISPOSITION = {
    "engagement_letter": "Engagement and scope (administrative) - scope exclusions drive the outside-filings list",
    "consent_7216": "Section 7216 consent (administrative)",
    "identity_document": "Identity verification",
    "irs_ip_pin_notice": "IP PIN used for e-file signature (not a return amount)",
    "prior_workpaper": "Prior-year context: open items carried into this year's findings",
    "irs_transcript": "Third-party record used to agree estimated payments",
    "irs_notice": "Notice record (prior-year resolved / post-filing exam)",
    "3922": "Supports ESPP lot N2 basis (applied through D11)",
    "letter": "Supports scholarship on Form 8863 (agrees to 1098-T box 5)",
    "books_report": "Supports contract labor and the Forms 1099-NEC finding",
    "1099-S": "Agrees to Form 4797 gross sales price",
    "paystub": "Supports California-source wage split",
    "retirement_contribution": "Supports SEP deduction",
    "K-1-state-supplement": "California share of Ridgeline income",
}


# ------------------------------------------------------------------------ helpers
def ctx(user: str | None, as_of: str | None):
    s = State(P, ALL_FINDINGS, STORE.all(), as_of or DEFAULT_AS_OF)
    uid = user if user in s.users else "st-01"
    return s, uid


def can_see_sensitive(s: State, uid: str) -> bool:
    return s.users[uid]["clearance"] and s.as_of >= s.users[uid]["active_from"]


def masker(s: State, uid: str):
    return (lambda x: x) if can_see_sensitive(s, uid) else mask_text


def has_sensitive(text: str) -> bool:
    return any(p.search(text or "") for p in SENSITIVE_PATTERNS)


def finding_state(s: State, f: dict):
    st = s.finding_status.get(f["id"])
    if st:
        return st
    if f["adjustments"]:
        linked = [a for a in s.adjustments.values() if a.get("finding") == f["id"]]
        if linked and all(a["status"] == "approved" for a in linked) and \
                {a["line"] for a in linked} >= {x["line"] for x in f["adjustments"]}:
            return {"status": "resolved", "note": "All proposed adjustments approved", "by": linked[-1].get("approved_by"), "at": linked[-1]["at"]}
        if linked and any(a["status"] == "proposed" for a in linked):
            return {"status": "in_progress", "note": "Adjustment proposed", "by": linked[-1]["proposed_by"], "at": linked[-1]["at"]}
    return {"status": "open"}


def open_errors(s: State):
    return [f["id"] for f in FINDINGS if f["severity"] == "error" and finding_state(s, f)["status"] in ("open", "in_progress")]


def name(s: State, uid):
    return s.users.get(uid, {}).get("name", uid)


def line_row(s: State, key: str):
    l = P.line(key)
    eff = s.effective(key)
    vers = {v: (P.line(key, v) or {}).get("value") for v in ("v1", "v2", "v3")}
    return {"key": key, "label": l["label"], "form": l["form"], "line": l["line"], "jurisdiction": l["jurisdiction"],
            "engine": l["value"], "value": eff["value"], "adjusted": eff["adjusted"], "pending": len(eff["pending"]),
            "rolled_up": eff["rolled_up"], "needs_engine": eff["needs_engine"],
            "area": area_of(key), "rule": l["rule"], "prior": P.py_value(key), "versions": vers,
            "findings": [fid for fid in LINE_FINDINGS.get(key, []) if finding_state(s, FINDING_BY_ID[fid])["status"] not in ("resolved", "accepted", "wont_fix")],
            "verified": [v["method"] for v in s.verifications.get(key, [])], "notes": sum(1 for n in s.notes.values() if n["target"] == key and n["status"] != "cleared")}


def resolved_ref(s: State, uid: str, ref: str):
    r = P.resolve(ref)
    m = masker(s, uid)
    if r["kind"] == "value":
        a = P.anchors.get(ref, {})
        doc = P.docs[r["doc"]]
        text = next(pg["printed_content"] for pg in doc["pages"] if pg["page"] == r["page"])
        span = a.get("span")
        if span:
            lo, hi = max(0, span[0] - 90), min(len(text), span[1] + 90)
            r["snippet"] = {"before": m(text[lo:span[0]]), "hit": m(text[span[0]:span[1]]), "after": m(text[span[1]:hi]),
                            "clip_start": lo > 0, "clip_end": hi < len(text)}
        else:
            r["snippet"] = {"before": m(text[:200]), "hit": "", "after": "", "not_located": True}
        r["position"] = P.ev[ref]["position"]
        r["value"] = m(r["value"]) if isinstance(r["value"], str) else r["value"]
        r["verified"] = s.verifications.get(ref, [])
    elif isinstance(r.get("value"), str):
        r["value"] = m(r["value"])
    elif isinstance(r.get("value"), dict) and r["kind"] == "household":
        r["value"] = {k: (m(v) if isinstance(v, str) else v) for k, v in r["value"].items()}
    r["findings"] = REF_FINDINGS.get(ref, [])
    return r


# ------------------------------------------------------------------------ read APIs
@app.get("/api/bootstrap")
def bootstrap(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    hh = P.raw["household"]
    return {
        "client": {"id": P.raw["packet"]["client_id"], "name": "Okafor-Lindqvist household", "tax_year": hh["tax_year"],
                   "filing_status": hh["filing_status"], "people": [{"id": p["id"], "name": p["name"], "role": p["role"]} for p in hh["people"]],
                   "residency": hh["residency"], "related_returns": hh["related_returns"],
                   "prior_preparer": P.py["return_2024"]["prepared_by"]},
        "users": list(s.users.values()), "me": s.users[uid], "areas": {a: AREA_LABELS.get(a, a) for a in SIGNOFF_AREAS + ["partner_review"]},
        "as_of": s.as_of, "default_as_of": DEFAULT_AS_OF, "ai": llm.available(), "model": llm.MODEL if llm.available() else None,
        "timeline_dates": [t["at"][:10] for t in P.timeline], "deadlines": P.raw["firm"]["deadlines"],
        "verification_types": P.raw["firm"]["starter_standards"]["verification_types"],
        "forms": sorted({l["form"] for l in P.versions[CURRENT_VERSION]["lines"]}),
    }


@app.get("/api/overview")
def overview(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    tot = s.totals()
    key_lines = ["F1040.1z", "F1040.2b", "F1040.3b", "F1040.7", "F1040.8", "F1040.11", "F1040.12", "F1040.15", "F1040.16",
                 "F1040.24", "F1040.33", "F1040.37", "F540NR.17", "F540NR.32", "F540NR.64", "F540NR.97"]
    py_map = {"F1040.1z": "F1040.1a", "F540NR.17": "CA540.17", "F540NR.64": "CA540.64", "F540NR.32": "CA540.17"}
    rows = []
    for k in key_lines:
        r = line_row(s, k)
        r["prior"] = P.py_value(py_map.get(k, k))
        rows.append(r)
    fs = [dict(f, state=finding_state(s, f)) for f in ALL_FINDINGS]
    sev = Counter(f["severity"] for f in fs if f["state"]["status"] in ("open", "in_progress"))
    so = s.signoff_status()
    areas = []
    for a in SIGNOFF_AREAS:
        af = [f for f in fs if f["area"] == a and f["state"]["status"] in ("open", "in_progress")]
        areas.append({"id": a, "label": AREA_LABELS[a], "open_findings": len(af),
                      "errors": sum(1 for f in af if f["severity"] == "error"),
                      "signoff": so[a], "hours": sum(t["hours"] for t in s.time if t["area"] == a),
                      "budget": P.raw["firm"]["budget_hours"].get(a)})
    gates = s.gates(open_errors(s), [])
    stage = "Filed" if s.filing["filed"] else "Client approved" if s.filing["client_approved"] else \
        "Ready for client" if all(g["ok"] for g in gates) else "In review" if any(x["preparer"] for x in so.values()) else "In preparation"
    since = s.seen.get(uid)
    new_events = [e for e in s.events if since and e["at"] > since and e["actor"] != uid and e["type"] not in ("seen", "view.unmasked")]
    return {"summary": tot, "lines": rows, "severity": dict(sev), "areas": areas, "gates": gates, "stage": stage,
            "since_last_look": {"since": since, "count": len(new_events)},
            "my_items": my_items(s, uid)}


def my_items(s: State, uid: str):
    role = s.users[uid]["role"]
    out = []
    for n in s.notes.values():
        if n["status"] == "cleared":
            continue
        if n.get("to") == uid or (role == "client" and n["kind"] == "client_question") or (n["by"] == uid and n["status"] == "answered"):
            out.append({"type": "note", "id": n["id"], "text": n["text"], "target": n["target"], "kind": n["kind"], "status": n["status"]})
    if role in ("reviewer", "partner"):
        for a in s.adjustments.values():
            if a["status"] == "proposed" and a["proposed_by"] != uid and (role == "partner" or not a["needs_partner"]):
                out.append({"type": "approve", "id": a["id"], "text": f"Approve adjustment to {a['line']}: {a['from']} -> {a['to']}", "target": a["line"]})
    for area, asg in s.assignments.items():
        if asg.get("user") == uid:
            out.append({"type": "assignment", "id": area, "text": f"Assigned: {AREA_LABELS.get(area, area)} due {asg.get('due', '-')}", "target": area})
    return out


@app.get("/api/lines")
def lines(form: str | None = None, area: str | None = None, x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    keys = P.line_order[CURRENT_VERSION]
    out = []
    for k in keys:
        l = P.line(k)
        if form and l["form"] != form:
            continue
        if area and area_of(k) != area:
            continue
        if not form and k.startswith("F8949.") and not k.endswith(".total") and k.split(".")[2].startswith("X"):
            continue  # 260 direct-indexing lots: shown in the sales schedule, not the line list
        out.append(line_row(s, k))
    return out


@app.get("/api/trace/{key}")
def trace(key: str, x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    l = P.line(key)
    if not l:
        raise HTTPException(404, "No such line")
    row = line_row(s, key)
    hist = []
    for v in ("v1", "v2", "v3"):
        vv = P.versions[v]
        ch = next((c for c in vv["changes_from_previous"] if c["key"] == key), None)
        hist.append({"version": v, "at": vv["computed_at"], "trigger": vv["trigger"], "value": (P.line(key, v) or {}).get("value"), "change": ch})
    inputs = [line_row(s, i) for i in l["inputs"] if P.line(i)]
    parents = [line_row(s, k) for k in sorted(P.parents[CURRENT_VERSION].get(key, []))]
    py_key = key if P.py_value(key) is not None else None
    return {**row, "note": l.get("note"), "rule_text": P.rules.get(l["rule"], ""), "inputs": inputs, "parents": parents,
            "sources": [resolved_ref(s, uid, r) for r in l["sources"]],
            "prior_label": P.py["return_2024"]["line_labels"].get(py_key) if py_key else None,
            "prior_source": P.py["return_2024"]["line_sources"].get(py_key) if py_key else None,
            "history": hist, "findings_full": [dict(FINDING_BY_ID[f], state=finding_state(s, FINDING_BY_ID[f])) for f in LINE_FINDINGS.get(key, [])],
            "adjustments": [a for a in s.adjustments.values() if a["line"] == key],
            "verifications": s.verifications.get(key, []), "notes": [n for n in s.notes.values() if n["target"] == key],
            "attachments": s.attachments.get(key, []),
            "judgments": [j for j in s.judgments if j["target"] == key],
            "rates": P.versions[CURRENT_VERSION]["tax_effect_rates"]}


def doc_disposition(s: State, d: dict):
    used = sorted(P.cited_by[CURRENT_VERSION].get(d["id"], []))
    sup = d["id"] in P.versions[CURRENT_VERSION]["documents_superseded"]
    mentioned = [k for k, l in P.lines[CURRENT_VERSION].items() if d["id"] in (l.get("note") or "")]
    user = s.dispositions.get(d["id"])
    if user:
        status, reason = "explained", user["reason"]
    elif used:
        status, reason = "used", f"Lands on {len(used)} return line(s)"
    elif sup:
        status, reason = "superseded", "Superseded by the CORRECTED Meridian 1099 (D31)"
    elif mentioned:
        status, reason = "explained", P.lines[CURRENT_VERSION][mentioned[0]]["note"]
    elif d["extraction"]["status"] == "not_extracted":
        status, reason = "explained", d["extraction"]["reason"]
    else:
        status, reason = "needs_reason", SUGGESTED_DISPOSITION.get(d["document_type"])
    future = d["received_at"][:10] > s.as_of
    return {"status": "not_yet_received" if future else status, "reason": reason, "used_by": used, "mentioned_by": mentioned,
            "suggested": status == "needs_reason", "by": user and user["by"]}


@app.get("/api/documents")
def documents(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    out = []
    for d in P.raw["documents"]:
        disp = doc_disposition(s, d)
        evs = P.ev_by_doc[d["id"]]
        out.append({"id": d["id"], "title": mask_text(d["title"]), "type": d["document_type"], "issuer": d["issuer"],
                    "belongs_to": d["belongs_to"], "received_at": d["received_at"], "pages": d["page_count"],
                    "corrected": d.get("marked_corrected"), "extraction": d["extraction"], "values": len(evs),
                    "low_conf": sum(1 for e in evs if e["confidence"] < 0.8), "disposition": disp,
                    "findings": sorted(set(REF_FINDINGS.get(d["id"], []))),
                    "verified": bool(s.verifications.get(d["id"])), "attachments": len(s.attachments.get(d["id"], []))})
    return out


@app.get("/api/documents/{doc_id}")
def document(doc_id: str, x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    d = P.docs.get(doc_id)
    if not d:
        raise HTTPException(404)
    m = masker(s, uid)
    pages = []
    sensitive = False
    for pg in d["pages"]:
        anchors = []
        for e in P.ev_by_doc[doc_id]:
            if e["page"] != pg["page"]:
                continue
            a = P.anchors[e["id"]]
            anchors.append({"ev": e["id"], "span": a["span"], "label": e["label"], "field": e["field"],
                            "value": m(e["value"]) if isinstance(e["value"], str) else e["value"], "type": e["value_type"],
                            "confidence": e["confidence"], "position": e["position"],
                            "lines": sorted(P.cited_by[CURRENT_VERSION].get(e["id"], [])),
                            "findings": REF_FINDINGS.get(e["id"], []), "verified": [v["method"] for v in s.verifications.get(e["id"], [])]})
        sensitive = sensitive or has_sensitive(pg["printed_content"])
        pages.append({"page": pg["page"], "text": m(pg["printed_content"]), "anchors": anchors,
                      "extracted": any(True for _ in anchors) or d["extraction"]["status"] == "extracted"})
    if sensitive and can_see_sensitive(s, uid):
        STORE.append(uid, "view.unmasked", doc_id, {"reason": "document opened"})
    return {"id": doc_id, "title": m(d["title"]), "meta": {k: d.get(k) for k in ("document_type", "issuer", "belongs_to", "tax_year",
            "received_at", "uploaded_by", "file_name", "page_count", "marked_corrected")}, "extraction": d["extraction"],
            "source": {"kind": "simulated", "note": "Rendered from printed_content; a real PDF/image source plugs into the same anchors."},
            "pages": pages, "disposition": doc_disposition(s, d),
            "findings": [dict(FINDING_BY_ID[f], state=finding_state(s, FINDING_BY_ID[f])) for f in sorted(set(REF_FINDINGS.get(doc_id, [])))],
            "verifications": s.verifications.get(doc_id, []), "notes": [n for n in s.notes.values() if n["target"] == doc_id],
            "attachments": s.attachments.get(doc_id, []), "masked": not can_see_sensitive(s, uid)}


@app.get("/api/findings")
def findings(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    fs = [dict(f, state=finding_state(s, f)) for f in ALL_FINDINGS]
    return {"findings": fs, "reconciliations": RECONS, "variances": variance_review(P, s.standards),
            "variance_explained": {k: True for k in {n["target"] for n in s.notes.values()} | set(s.verifications)}}


@app.get("/api/resolve")
def resolve(ref: str, x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    return resolved_ref(s, uid, ref)


@app.get("/api/search")
def search(q: str, x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    ql = q.lower().strip()
    num = re.sub(r"[,$\s]", "", ql)
    isnum = bool(re.fullmatch(r"-?\d+(\.\d+)?", num))
    hits = []

    def match_val(v):
        if not isnum or not isinstance(v, (int, float)):
            return False
        return abs(abs(float(v)) - abs(float(num))) < 0.5

    for k, l in P.lines[CURRENT_VERSION].items():
        if ql in k.lower() or ql in l["label"].lower() or match_val(l["value"]):
            hits.append({"kind": "line", "id": k, "label": l["label"], "value": l["value"]})
    for d in P.raw["documents"]:
        if ql in d["id"].lower() or ql in d["title"].lower() or ql in d["issuer"].lower():
            hits.append({"kind": "document", "id": d["id"], "label": d["title"]})
    for e in P.raw["extracted_values"]:
        if match_val(e["value"]) or (not isnum and len(ql) > 3 and ql in e["label"].lower()):
            hits.append({"kind": "value", "id": e["id"], "label": f"{e['document_id']} · {e['label']}", "value": e["value"]})
    for f in ALL_FINDINGS:
        if ql in f["title"].lower() or ql in f["id"].lower():
            hits.append({"kind": "finding", "id": f["id"], "label": f["title"]})
    for qa in P.raw["client"]["questionnaire"]:
        if not isnum and len(ql) > 3 and (ql in qa["question"].lower() or ql in qa["answer"].lower()):
            hits.append({"kind": "client_answer", "id": qa["id"], "label": qa["question"], "value": qa["answer"]})
    return hits[:80]


# ------------------------------------------------------------------------ schedules
LOT_RE = re.compile(r"^(?P<desc>.+?) \((?:acquired (?P<acq>[^,]+), )?sold (?P<sold>[^;]+); proceeds (?P<proc>[\d,.\-]+), basis (?P<basis>[\d,.\-]+)(?:, adjustment (?P<code>\w) (?P<adj>[\d,.\-]+))?\)")


@app.get("/api/schedules/{name}")
def schedule(name: str, box: str | None = None, x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    L = P.lines[CURRENT_VERSION]
    if name == "sales":
        boxes = defaultdict(lambda: {"count": 0, "proceeds": 0.0, "basis": 0.0, "adjustments": 0.0, "gain": 0.0, "exceptions": 0})
        lots = []
        for k, l in L.items():
            if not k.startswith("F8949.") or k.endswith(".total"):
                continue
            bx, lot = k.split(".")[1], k.split(".")[2]
            m = LOT_RE.match(l["label"])
            proc = float(m.group("proc").replace(",", "")) if m else 0
            basis = float(m.group("basis").replace(",", "")) if m else 0
            adj = float((m.group("adj") or "0").replace(",", "")) if m else 0
            src_doc = next((P.ev[x]["document_id"] for x in l["sources"] if x in P.ev), "derived")
            exc = []
            if basis == 0:
                exc.append("zero basis")
            if m and m.group("code") == "W":
                exc.append("wash sale")
            if LINE_FINDINGS.get(k):
                exc.append("finding")
            row = {"key": k, "box": bx, "lot": lot, "desc": m.group("desc") if m else l["label"], "acquired": m and m.group("acq"),
                   "sold": m and m.group("sold"), "proceeds": proc, "basis": basis, "adj": adj, "gain": l["value"],
                   "effective": s.effective(k)["value"], "doc": src_doc, "exceptions": exc}
            b = boxes[(bx, src_doc)]
            b["count"] += 1; b["proceeds"] += proc; b["basis"] += basis; b["adjustments"] += adj; b["gain"] += l["value"]
            b["exceptions"] += 1 if exc else 0
            if box is None or box == bx:
                if box is not None or exc:
                    lots.append(row)
        summary = [{"box": k[0], "doc": k[1], "doc_title": P.docs[k[1]]["title"] if k[1] in P.docs else "Derived (Form 7203)",
                    **{x: round(y, 2) for x, y in v.items()}} for k, v in sorted(boxes.items())]
        return {"summary": summary, "lots": lots, "box_totals": {k: L[k]["value"] for k in L if k.startswith("F8949.") and k.endswith(".total")},
                "reconciliations": [r for r in RECONS if r["id"] in ("R-BRIGHTPATH", "R-MERIDIAN", "R-1099DA")]}
    if name == "assets":
        return {"assets": P.versions[CURRENT_VERSION]["assets"], "prior": P.py["asset_records"],
                "ca_differences": P.versions[CURRENT_VERSION]["carryovers_out"]["california"]["depreciation_basis_differences"],
                "reconciliations": [r for r in RECONS if r["id"].startswith("R-DEPR")],
                "lines": [line_row(s, k) for k in L if k.startswith(("F4562", "FTB3885A", "F4797"))]}
    if name == "books":
        mp = [("Sales - client projects + print and licensing", ["ev-0086", "ev-0087"], "SchC.1", "Engine added 1099-K 12,400 (see finding)"),
              ("Advertising", ["ev-0088"], "SchC.8", ""), ("Car and truck - fuel", ["ev-0090"], "SchC.9", "Actual fuel replaced by standard mileage"),
              ("Contract labor", ["ev-0091"], "SchC.11", ""), ("Depreciation (book SL 5 yr)", ["ev-0092"], "SchC.13", "Tax: 100% special depreciation"),
              ("Insurance - business liability", ["ev-0093"], "SchC.15", ""), ("Insurance - health (Erik)", ["ev-0094"], "Sch1.17", "Not a Schedule C expense; moved to Schedule 1"),
              ("Telephone + Internet", ["ev-0101", "ev-0095"], "SchC.25", "Business-use % (see finding)"), ("Legal and professional", ["ev-0096"], "SchC.17", ""),
              ("Meals", ["ev-0097"], "SchC.24b", "50% limit"), ("Office expenses", ["ev-0098"], "SchC.18", ""),
              ("Software and subscriptions", ["ev-0099"], "SchC.27a.1", ""), ("Supplies", ["ev-0100"], "SchC.22", ""), ("Travel", ["ev-0102"], "SchC.24a", ""),
              ("Bank and merchant fees", ["ev-0089"], "SchC.27a.2", ""), ("(no book entry)", [], "SchC.30", "Home office - simplified method")]
        rows = []
        for label, evs, key, why in mp:
            book = sum(P.ev[e]["value"] for e in evs)
            eff = s.effective(key)
            rows.append({"book_label": label, "evidence": evs, "book": book, "line": key, "line_label": L[key]["label"], "engine": eff["engine"],
                         "value": eff["value"], "difference": (eff["value"] or 0) - book, "reason": why, "findings": LINE_FINDINGS.get(key, [])})
        return {"rows": rows, "book_net": P.ev["ev-0105"]["value"], "engine_net": L["SchC.31"]["value"], "net_value": s.effective("SchC.31")["value"],
                "reconciliations": [r for r in RECONS if r["id"].startswith(("R-SCHC", "R-MILES"))]}
    if name == "state":
        rows = defaultdict(dict)
        labels = {}
        for k, l in L.items():
            if k.startswith("SchCA540NR.") and k.count(".") == 2:
                _, ln, col = k.split(".")
                rows[ln][col] = s.effective(k)["value"]
                rows[ln][col + "_key"] = k
                labels[ln] = re.sub(r" - (federal|CA additions|CA subtractions|total using CA law|California amount)$", "", l["label"])
        table = [{"line": ln, "label": labels[ln], **v} for ln, v in sorted(rows.items(), key=lambda x: int(re.sub(r"\D", "", x[0]) or 0))]
        return {"residency": P.raw["household"]["residency"], "table": table,
                "lines": [line_row(s, k) for k in L if k.startswith(("F540NR.", "ScheduleS.", "FTB5805"))],
                "ridgeline": {e["field"]: e["value"] for e in P.ev_by_doc["D38"]},
                "ratio_days": [P.params["ca_resident_days"], P.params["year_days"]]}
    if name == "carryovers":
        return {"into_2025": P.py["carryovers_into_2025"], "out_2025": P.versions[CURRENT_VERSION]["carryovers_out"],
                "reconciliations": [r for r in RECONS if r["id"].startswith("R-CARRY")]}
    if name == "basis":
        return {"records": P.py["basis_records"], "f7203": [line_row(s, k) for k in L if k.startswith("F7203")],
                "f8606": [line_row(s, k) for k in L if k.startswith("F8606")],
                "ridgeline": {e["field"]: e["value"] for e in P.ev_by_doc["D37"]},
                "reconciliations": [r for r in RECONS if r["id"] in ("R-SCORP-BASIS", "R-RIDGELINE-CAP", "R-8606")]}
    if name == "k1":
        return {"lines": [line_row(s, k) for k, l in L.items() if l["rule"] == "R-K1" or any(str(x) in ("ev-0965", "ev-0976", "ev-0968", "ev-0982") for x in l["sources"])],
                "docs": {d: [{"id": e["id"], "label": e["label"], "value": e["value"], "lines": sorted(P.cited_by[CURRENT_VERSION].get(e["id"], []))}
                             for e in P.ev_by_doc[d]] for d in ("D37", "D38", "D39", "D40")}}
    if name == "prior":
        ppw = P.py["prior_preparer_workpaper"]
        resolution = {0: ("D40 received 06/05; basis rolled forward independently", ["R-SCORP-BASIS", "D40"]),
                      1: ("2024 Form 5498 (D42) confirms the 7,000 basis", ["D42", "R-8606"]),
                      2: ("Measured 220 / 260 sq ft (D24); 2024 overstated", ["F-PY-HOMEOFFICE", "D24"]),
                      3: ("Still unresolved - listed in outside filings for the client", ["F-OUTSIDE-FILINGS"])}
        return {"memo": ppw["summary_memo"], "notes": ppw["notes"],
                "open_items": [{"item": t, "resolution": resolution.get(i, ("", []))[0], "refs": resolution.get(i, ("", []))[1]}
                               for i, t in enumerate(ppw["open_items_left_by_prior_preparer"])],
                "variances": variance_review(P, s.standards), "prior_findings": [f for f in FINDINGS if f["kind"] == "prior_year"],
                "return_2024": [{"key": k, "label": P.py["return_2024"]["line_labels"].get(k), "value": v,
                                 "current": (P.line(k) or {}).get("value")} for k, v in P.py["return_2024"]["lines"].items()]}
    if name == "whatif":
        return P.raw["what_if"]
    if name == "payments":
        return {"federal": [line_row(s, k) for k in ("F1040.25a", "F1040.26", "Sch3.10", "Sch3.11", "F1040.33", "F1040.37", "F1040.38")],
                "california": [line_row(s, k) for k in ("F540NR.71", "F540NR.72", "F540NR.73", "F540NR.78", "F540NR.97")],
                "agency": P.raw["tax_agency_records"], "extension": P.raw["what_if"]["extension"],
                "reconciliations": [r for r in RECONS if r["id"] in ("R-FED-EST", "R-CA-EST", "R-W2-BOX2", "R-EXCESS-SS")],
                "planning": P.raw["planning_reference_2026"]}
    raise HTTPException(404)


# ------------------------------------------------------------------------ review / activity / filing / firm
REQUIRED_STEPS = P.raw["firm"]["starter_standards"]["required_steps"]


def required_steps(s: State):
    fs = {f["id"]: finding_state(s, f)["status"] for f in ALL_FINDINGS}
    docs_ok = [d for d in P.raw["documents"] if doc_disposition(s, d)["status"] == "needs_reason"]
    so = s.signoff_status()
    var = variance_review(P, s.standards)
    explained = {n["target"] for n in s.notes.values()} | set(s.verifications)
    unexplained = [v["key"] for v in var if v["key"] not in explained]
    diag_open = [d["id"] for d in DIAGNOSTICS if fs[d["id"]] == "open"]
    est_ok = all(r["status"] == "agrees" or fs.get({"R-FED-EST": "F-Q4-ESTIMATE", "R-CA-EST": "F-CA-EST-YEAR"}[r["id"]]) in ("resolved", "accepted")
                 for r in RECONS if r["id"] in ("R-FED-EST", "R-CA-EST"))
    status = [
        (any(t["event"] == "engagement_signed" and t["at"][:10] <= s.as_of for t in P.timeline), "D01, D02 signed 01/12/2026"),
        (bool(s.verifications.get("D04")) and bool(s.verifications.get("D05")), "Verify D04 and D05"),
        (bool(s.verifications.get("D06")), "Record a verification on D06"),
        (not docs_ok, f"{len(docs_ok)} documents need a disposition"),
        (not unexplained, f"{len(unexplained)} of {len(var)} variances unexplained"),
        (all(r["status"] == "agrees" for r in RECONS if r["id"].startswith("R-CARRY")), "Carryover reconciliations"),
        (est_ok, "Federal / California estimate reconciliations"),
        (fs.get("F-8867") in ("resolved", "accepted"), "Resolve F-8867"),
        (not diag_open, f"{len(diag_open)} engine diagnostics without a disposition"),
        (all(so[a]["reviewer"] and so[a]["reviewer"]["valid"] for a in SIGNOFF_AREAS), "Reviewer sign-off on all areas"),
        (bool(so["partner_review"]["partner"] and so["partner_review"]["partner"]["valid"]), "Partner sign-off"),
        (bool(s.filing["client_approved"]), "Client signs Form 8879"),
    ]
    return [{"step": st, "ok": ok, "detail": det} for st, (ok, det) in zip(REQUIRED_STEPS, status)]


@app.get("/api/review")
def review(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    return {"signoffs": s.signoff_status(), "gates": s.gates(open_errors(s), []), "steps": required_steps(s),
            "notes": sorted(s.notes.values(), key=lambda n: n["at"], reverse=True),
            "adjustments": sorted(s.adjustments.values(), key=lambda a: a["at"], reverse=True),
            "judgments": s.judgments, "assignments": s.assignments, "levels": LEVELS,
            "areas": [{"id": a, "label": AREA_LABELS[a], "lines": len(s.area_lines(a)), "preparers": sorted(s.preparers_of(a))}
                      for a in SIGNOFF_AREAS] + [{"id": "partner_review", "label": "Whole return (partner)", "lines": 0, "preparers": []}],
            "totals": s.totals()}


@app.get("/api/activity")
def activity(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    evs = [dict(e, actor_name=name(s, e["actor"]), voided=e["id"] in s.voided) for e in reversed(s.all_events) if e["type"] != "seen"]
    tl = [dict(t, actor_name=name(s, t["actor"]) if t["actor"] else "System / client", future=t["at"][:10] > s.as_of) for t in P.timeline]
    versions = [{"version": v["version"], "at": v["computed_at"], "trigger": v["trigger"], "summary": v["summary"],
                 "changes": len(v["changes_from_previous"]), "used": len(v["documents_used"])} for v in P.raw["return_versions"]]
    return {"events": evs[:500], "timeline": tl, "versions": versions, "since": s.seen.get(uid)}


@app.get("/api/versions/compare")
def compare(a: str = "v2", b: str = "v3"):
    la, lb = P.lines[a], P.lines[b]
    out = []
    for k in sorted(set(la) | set(lb)):
        va, vb = (la.get(k) or {}).get("value"), (lb.get(k) or {}).get("value")
        if va != vb and not (k.startswith("F8949.") and k.split(".")[2].startswith("X")):
            out.append({"key": k, "label": (lb.get(k) or la.get(k))["label"], "a": va, "b": vb})
    return {"a": a, "b": b, "changes": out, "trigger": P.versions[b]["trigger"]}


@app.get("/api/filing")
def filing(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    ag = P.raw["tax_agency_records"]
    tl = [t for t in P.timeline if t["at"][:10] <= s.as_of]
    efile = [e for e in ag["efile"] if e["date"][:10] <= s.as_of]
    notices = [n for n in ag["notices"] if n["dated"] <= s.as_of]
    tot = s.totals()
    return {"gates": s.gates(open_errors(s), []), "steps": required_steps(s), "filing": s.filing, "efile": efile,
            "scripted_timeline": tl, "extension": P.raw["what_if"]["extension"], "notices": notices, "totals": tot,
            "deadlines": P.raw["firm"]["deadlines"], "post_filing_rates": P.raw["post_filing_rates"],
            "planning": P.raw["planning_reference_2026"],
            "estimates_2026": {"federal_safe_harbor_total": round(1.10 * tot["workpaper"]["federal_total_tax"]),
                               "federal_quarterly": round(1.10 * tot["workpaper"]["federal_total_tax"] / 4)},
            "carryovers_out": P.versions[CURRENT_VERSION]["carryovers_out"]}


NOTICE_MAP = {
    "Noncash charitable": ["F8283.A1.stock", "F8283.A1.vehicle", "D65", "D66", "F-CAR-GIFT"],
    "Schedule C gross receipts": ["SchC.1", "R-SCHC-RECEIPTS", "R-SCHC-GROSS", "F-1099K-DOUBLE", "D15", "D16", "D17", "D18", "D19", "D20", "D21"],
}


@app.get("/api/notice/{nid}")
def notice(nid: str, x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    n = next((x for x in P.raw["tax_agency_records"]["notices"] if x["id"] == nid), None)
    if not n:
        raise HTTPException(404)
    items = []
    for it in n.get("items_under_examination", []):
        refs = next((v for k, v in NOTICE_MAP.items() if it.startswith(k)), [])
        ev = []
        for r in refs:
            if r in FINDING_BY_ID:
                f = FINDING_BY_ID[r]
                ev.append({"kind": "finding", "id": r, "label": f["title"], "value": f["summary"], "state": finding_state(s, f)})
            elif r.startswith("R-"):
                rc = next(x for x in RECONS if x["id"] == r)
                ev.append({"kind": "reconciliation", "id": r, "label": rc["title"], "value": f"{rc['left_label']} {rc['left']:,} vs {rc['right_label']} {rc['right']:,}"})
            elif r in P.lines[CURRENT_VERSION]:
                rr = line_row(s, r)
                ev.append({"kind": "line", "id": r, "label": rr["label"], "value": rr["value"],
                           "verifications": s.verifications.get(r, []), "adjustments": [a for a in s.adjustments.values() if a["line"] == r]})
            else:
                ev.append(resolved_ref(s, uid, r))
        items.append({"item": it, "evidence": ev})
    return {"notice": n, "items": items, "filed": s.filing["filed"], "signoffs": s.signoff_status()["partner_review"],
            "judgments": s.judgments}


@app.get("/api/client")
def client(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    qs = [n for n in s.notes.values() if n["kind"] == "client_question"]
    tot = s.totals()
    gates = s.gates(open_errors(s), [])
    return {"questions": qs, "totals": tot, "ready": all(g["ok"] for g in gates), "gates": gates, "filing": s.filing,
            "summary_lines": [line_row(s, k) for k in ("F1040.11", "F1040.15", "F1040.24", "F1040.33", "F1040.37", "F540NR.64", "F540NR.97")],
            "outside": FINDING_BY_ID.get("F-OUTSIDE-FILINGS")}


@app.get("/api/firm")
def firm(x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    hours = defaultdict(float)
    for t in s.time:
        hours[t["area"]] += t["hours"]
    budget = [{"area": a, "label": AREA_LABELS.get(a, a), "budget": b, "actual": hours.get(a, 0.0)} for a, b in P.raw["firm"]["budget_hours"].items()]
    return {"staff": list(s.users.values()), "standards": {**P.raw["firm"]["starter_standards"], "variance_review": s.standards},
            "views": list(reversed(s.views))[:200], "budget": budget, "time": s.time, "identity": {k: v for k, v in P.raw["firm"]["firm_identity"].items() if k != "ero_pin"},
            "assignment_at_start": P.raw["firm"]["assignment_at_start"], "assignments": s.assignments, "deadlines": P.raw["firm"]["deadlines"]}


@app.get("/api/files/{fid}")
def get_file(fid: str):
    f = STORE.get_file(fid)
    if not f:
        raise HTTPException(404)
    return Response(f["data"], media_type=f["mime"] or "application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{f["name"]}"'})


# ------------------------------------------------------------------------ write API
def _apply(s: State, uid: str, act: dict, batch: str | None):
    t, tgt, d = act["type"], act.get("target"), act.get("data") or {}
    deny = lambda ok_reason: (_ for _ in ()).throw(HTTPException(403, ok_reason[1])) if not ok_reason[0] else None
    if t == "adjustment.propose":
        deny(s.can(uid, "propose_adjustment"))
        l = P.line(tgt)
        if not l:
            raise HTTPException(400, "Unknown line")
        if not d.get("reason"):
            raise HTTPException(400, "A reason is required for every adjustment")
        frm = s.effective(tgt)["value"] if d.get("from") is None else d["from"]
        to = float(d["to"])
        cat = d.get("category", "none")
        from .checks import Ctx
        c = Ctx(P)
        eff = c.effect([{"from": frm, "to": to, "category": cat, "rates": d.get("rates")}]) if cat != "none" else \
            {"federal_tax": 0, "california_tax": 0, "federal_balance": 0, "california_balance": 0}
        return STORE.append(uid, t, tgt, {"from": frm, "to": to, "category": cat, "rates": d.get("rates"), "reason": d["reason"],
                                          "finding": d.get("finding"), "effect": eff}, batch)
    if t in ("adjustment.approve", "adjustment.reject", "adjustment.revert"):
        a = s.adjustments.get(tgt)
        if not a:
            raise HTTPException(404)
        if t == "adjustment.revert":
            deny(s.can(uid, "propose_adjustment"))
        else:
            deny(s.can(uid, "approve_adjustment", adj=a))
        return STORE.append(uid, t, tgt, {"note": d.get("note", "")}, batch)
    if t == "verify":
        deny(s.can(uid, "verify"))
        if d.get("method") not in P.raw["firm"]["starter_standards"]["verification_types"]:
            raise HTTPException(400, "Unknown verification method")
        return STORE.append(uid, t, tgt, {"method": d["method"], "note": d.get("note", ""), "evidence": d.get("evidence", [])}, batch)
    if t == "note.add":
        kind = d.get("kind", "comment")
        deny(s.can(uid, {"review_note": "review_note", "client_question": "client_question"}.get(kind, "comment"), client_ok=False))
        return STORE.append(uid, t, tgt, {"text": d["text"], "kind": kind, "blocking": kind == "review_note" and d.get("blocking", True),
                                          "to": d.get("to"), "due": d.get("due")}, batch)
    if t == "note.reply":
        n = s.notes.get(tgt)
        if not n:
            raise HTTPException(404)
        deny(s.can(uid, "reply", note=n))
        return STORE.append(uid, t, tgt, {"text": d["text"]}, batch)
    if t == "note.clear":
        n = s.notes.get(tgt)
        if not n:
            raise HTTPException(404)
        deny(s.can(uid, "clear_note", note=n))
        return STORE.append(uid, t, tgt, {}, batch)
    if t == "finding.set":
        st = d.get("status")
        deny(s.can(uid, "finding_accept" if st == "accepted" else "finding_resolve"))
        if st in ("accepted", "wont_fix") and not d.get("note"):
            raise HTTPException(400, "Say why the finding is accepted without change")
        return STORE.append(uid, t, tgt, {"status": st, "note": d.get("note", "")}, batch)
    if t == "judgment.record":
        deny(s.can(uid, "judgment"))
        if not d.get("authority"):
            raise HTTPException(400, "Firm rule: every judgment call records the authority relied on")
        return STORE.append(uid, t, tgt, {"topic": d.get("topic", ""), "decision": d["decision"], "authority": d["authority"],
                                          "kind": d.get("kind", "judgment")}, batch)
    if t == "signoff":
        lvl = d["level"]
        deny(s.can(uid, "signoff", level=lvl, area=tgt))
        h, snap = (s.overall_hash(), {}) if tgt == "partner_review" else s.area_hash(tgt)
        return STORE.append(uid, t, tgt, {"level": lvl, "hash": h, "snapshot": snap}, batch)
    if t == "attach":
        info = d.get("file") and STORE.put_file(d["file"]["name"], d["file"].get("mime", "application/octet-stream"), d["file"]["b64"], uid)
        return STORE.append(uid, t, tgt, {"note": d.get("note", ""), "file": info, "kind": d.get("kind", "note")}, batch)
    if t == "doc.disposition":
        deny(s.can(uid, "disposition"))
        return STORE.append(uid, t, tgt, {"reason": d["reason"]}, batch)
    if t == "time.log":
        deny(s.can(uid, "time"))
        return STORE.append(uid, t, tgt, {"hours": float(d["hours"]), "note": d.get("note", "")}, batch)
    if t == "assign":
        deny(s.can(uid, "assign"))
        return STORE.append(uid, t, tgt, {"user": d["user"], "due": d.get("due")}, batch)
    if t == "standards.set":
        deny(s.can(uid, "admin"))
        return STORE.append(uid, t, None, {k: d[k] for k in ("flag_if_change_over_percent", "and_over_amount") if k in d}, batch)
    if t == "seen":
        return STORE.append(uid, t, None, {}, batch)
    if t == "filing.client_approved":
        deny(s.can(uid, "client_approve"))
        gates = s.gates(open_errors(s), [])
        if not all(g["ok"] for g in gates):
            raise HTTPException(409, "Not ready: " + "; ".join(g["label"] for g in gates if not g["ok"]))
        return STORE.append(uid, t, None, {"form": "8879 / FTB 8879", "totals": s.totals()["workpaper"], "hash": s.overall_hash()}, batch)
    if t == "filing.filed":
        deny(s.can(uid, "file"))
        if not s.filing["client_approved"]:
            raise HTTPException(409, "Client has not approved and signed Form 8879")
        if s.filing["client_approved"]["hash"] != s.overall_hash():
            raise HTTPException(409, "The return changed after the client approved it - get approval again")
        return STORE.append(uid, t, None, {"hash": s.overall_hash(), "totals": s.totals()["workpaper"]}, batch)
    if t == "filing.amend":
        deny(s.can(uid, "amend"))
        if not s.filing["filed"]:
            raise HTTPException(409, "Nothing filed yet")
        return STORE.append(uid, t, None, {"reason": d.get("reason", "")}, batch)
    raise HTTPException(400, f"Unknown action {t}")


@app.post("/api/action")
def action(body: dict = Body(...), x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    if body.get("type") == "undo":
        batch = body.get("batch")
        evs = [e["id"] for e in s.all_events if e["batch"] == batch and e["actor"] == uid]
        if not evs:
            raise HTTPException(404, "Nothing of yours to undo in that batch")
        return {"ok": True, "event": STORE.append(uid, "undo", batch, {"events": evs})}
    acts = body["actions"] if body.get("type") == "bulk" else [body]
    batch = "b-" + uuid.uuid4().hex[:10]
    done = []
    for a in acts:
        done.append(_apply(s, uid, a, batch))
        s = State(P, ALL_FINDINGS, STORE.all(), s.as_of)  # later actions in a batch see earlier ones
    return {"ok": True, "batch": batch, "events": done}


@app.post("/api/reveal")
def reveal(body: dict = Body(...), x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    s, uid = ctx(x_user, x_as_of)
    ok, why = s.can(uid, "reveal")
    if not ok:
        raise HTTPException(403, why)
    ref = body["ref"]
    if ref.startswith("household:"):
        val = P.people[ref[10:]]
    elif ref == "bank":
        val = P.raw["household"]["bank_account_for_refund_or_payment"]
    else:
        raise HTTPException(400)
    STORE.append(uid, "view.unmasked", ref, {"reason": body.get("reason", "")})
    return val


# ------------------------------------------------------------------------ AI
def _facts_for_line(key):
    s = State(P, ALL_FINDINGS, STORE.all(), DEFAULT_AS_OF)
    l = P.line(key)
    return {"line": {k: l[k] for k in ("key", "label", "value", "rule", "inputs", "sources", "note")},
            "rule_text": P.rules.get(l["rule"]), "inputs": [{"key": i, "label": P.line(i)["label"], "value": P.line(i)["value"]} for i in l["inputs"] if P.line(i)],
            "sources": [P.resolve(r) for r in l["sources"]], "prior_year": P.py_value(key),
            "history": [{"version": v, "value": (P.line(key, v) or {}).get("value")} for v in ("v1", "v2", "v3")],
            "findings": [FINDING_BY_ID[f] for f in LINE_FINDINGS.get(key, [])],
            "adjustments": [a for a in s.adjustments.values() if a["line"] == key]}


def _exists(ref):
    return P.exists(ref) or ref in FINDING_BY_ID or any(r["id"] == ref for r in RECONS)


def _who(request: Request, x_user):
    return (x_user or "anon") + ":" + (request.client.host if request.client else "")


@app.post("/api/ai/explain")
def ai_explain(request: Request, body: dict = Body(...), x_user: str | None = Header(None)):
    tgt = body["target"]
    if tgt in FINDING_BY_ID:
        facts = {"finding": FINDING_BY_ID[tgt], "evidence": [P.resolve(r) for r in FINDING_BY_ID[tgt]["evidence"]]}
        task = "Explain this finding to a reviewer in 4-6 sentences: what is wrong, how we know, what changes."
    else:
        facts = _facts_for_line(tgt)
        task = "Explain how this return line was built, where its numbers come from, and anything a reviewer should look at."
    return llm.complete(task, facts, _who(request, x_user), _exists)


@app.post("/api/ai/draft")
def ai_draft(request: Request, body: dict = Body(...), x_user: str | None = Header(None)):
    f = FINDING_BY_ID[body["finding"]]
    kind = body.get("kind", "client_question")
    task = ("Draft a short, friendly question to the client (no jargon, no ids in the text except a final 'Ref:' line with "
            "the [[ids]]) that would resolve this finding." if kind == "client_question" else
            "Draft a concise review note to the preparer describing what to fix and the evidence to attach.")
    return llm.complete(task, {"finding": f, "evidence": [P.resolve(r) for r in f["evidence"]]}, _who(request, x_user), _exists)


@app.post("/api/ai/ask")
def ai_ask(request: Request, body: dict = Body(...), x_user: str | None = Header(None), x_as_of: str | None = Header(None)):
    q = body["q"]
    words = [w for w in re.findall(r"[a-z0-9]{3,}", q.lower()) if w not in {"the", "and", "where", "what", "did", "how", "why", "this", "that", "with"}]
    hits = []
    for w in words[:6]:
        hits += search(w, x_user, x_as_of)[:12]
    uniq = {(h["kind"], h["id"]): h for h in hits}
    facts = {"question": q, "matches": list(uniq.values())[:40],
             "related_findings": [FINDING_BY_ID[h["id"]] for h in uniq.values() if h["kind"] == "finding"][:8]}
    res = llm.complete("Answer the question using only these facts. Cite ids.", facts, _who(request, x_user), _exists)
    res["matches"] = facts["matches"]
    return res


# ------------------------------------------------------------------------ demo helpers
@app.post("/api/admin/reset")
def reset(x_user: str | None = Header(None)):
    s, uid = ctx(x_user, None)
    if s.users[uid]["role"] not in ("firm_administrator", "partner"):
        raise HTTPException(403, "Administrator or partner only")
    STORE.reset()
    return {"ok": True}


@app.post("/api/admin/seed")
def seed(x_user: str | None = Header(None)):
    from .demo import seed_demo
    s, uid = ctx(x_user, None)
    if s.users[uid]["role"] not in ("firm_administrator", "partner"):
        raise HTTPException(403, "Administrator or partner only")
    n = seed_demo(STORE, P, ALL_FINDINGS)
    return {"ok": True, "events": n}


@app.get("/api/packet")
def packet_raw():
    """The packet exactly as delivered (for auditors / re-loading)."""
    return FileResponse(PACKET_PATH, media_type="application/json")


@app.get("/api/pulse")
def pulse():
    work = [e for e in STORE.all() if e["type"] not in ("view.unmasked", "seen")]
    return {"count": len(work), "last_actor": work[-1]["actor"] if work else None}


@app.get("/healthz")
def health():
    return {"ok": True, "findings": len(FINDINGS), "events": len(STORE.all())}


app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True), name="static")
