"""Load the pipeline packet exactly as delivered and build read-only indexes over it.

Nothing in here changes the packet. Every index is derived, so the original JSON stays
the single source of truth and can be re-loaded or replaced by a new packet at any time.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

CURRENT_VERSION = "v3"

# Which review area a return line belongs to. Areas mirror firm.budget_hours so time,
# sign-offs and findings all roll up the same way.
AREA_RULES = [
    ("california", ("SchCA540NR.", "F540NR.", "ScheduleS.", "FTB")),
    ("pass_throughs_and_basis", ("SchE.28", "SchE.32", "F7203.", "SchD.12", "F8995.2.2", "F8995.15.2", "F8949.F.")),
    ("rentals_and_sale", ("SchE.", "F4562-E.", "F4797.", "F8582.")),
    ("business", ("SchC.", "SchSE.", "F4562-C.", "Sch1.15", "Sch1.16", "Sch1.17", "F8995.2.1", "F8995.15.1", "Sch1.3")),
    ("wages_and_stock_compensation", ("F1040.1", "F8959.", "Sch3.11", "F6251.2i", "F8949.B.N", "F8949.E.N", "F8949.A.N")),
    ("payments_and_penalty", ("Sch3.10", "Sch3.15")),
    ("investments_and_digital_assets", ("SchB.", "F8949.", "SchD.", "F8938.", "Sch3.1", "F8960.")),
    ("deductions_and_credits", ("SchA.", "F8283.", "F8812.", "F8863.", "F2441.", "F8606.", "F8889.", "F8867.", "Sch1.13", "F8995.", "Sch3.8")),
    ("payments_and_penalty", ("Sch3.10", "Sch3.15", "F2210.")),
]
AREA_LABELS = {
    "intake_and_engagement": "Intake & engagement",
    "wages_and_stock_compensation": "Wages & stock compensation",
    "investments_and_digital_assets": "Investments & digital assets",
    "business": "Business (Schedule C)",
    "rentals_and_sale": "Rentals & sale",
    "pass_throughs_and_basis": "K-1s & basis",
    "deductions_and_credits": "Deductions & credits",
    "california": "California",
    "payments_and_penalty": "Payments & penalty",
    "return_summary": "Return summary & tax",
    "partner_review": "Partner review",
}

SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),             # SSN / ITIN
    re.compile(r"\bSE\d{2}(?: ?\d{4}){5}\b"),         # Swedish IBAN
    re.compile(r"\b4483019276\b"),                     # refund bank account
    re.compile(r"\b990000017\b"),                      # routing number
    re.compile(r"\b482915\b"),                         # IP PIN
    re.compile(r"\b(?:41207|88310)\b"),                # e-file signature PINs
]


def mask_text(s: str) -> str:
    if not isinstance(s, str):
        return s
    out = s
    for p in SENSITIVE_PATTERNS:
        out = p.sub(lambda m: "•" * max(0, len(m.group()) - 4) + m.group()[-4:], out)
    return out


F1040_AREAS = {
    "1a": "wages_and_stock_compensation", "1z": "wages_and_stock_compensation",
    "2b": "investments_and_digital_assets", "3a": "investments_and_digital_assets",
    "3b": "investments_and_digital_assets", "7": "investments_and_digital_assets",
    "4a": "deductions_and_credits", "4b": "deductions_and_credits", "12": "deductions_and_credits",
    "13": "deductions_and_credits", "19": "deductions_and_credits",
    "25a": "payments_and_penalty", "25d": "payments_and_penalty", "26": "payments_and_penalty",
    "31": "payments_and_penalty", "33": "payments_and_penalty", "37": "payments_and_penalty",
    "38": "payments_and_penalty",
}


def area_of(key: str) -> str:
    if key.startswith("F1040."):
        return F1040_AREAS.get(key[6:], "return_summary")
    for area, prefixes in AREA_RULES:
        if key.startswith(prefixes):
            return area
    return "return_summary"


def json_pointer(obj, pointer: str):
    """RFC 6901 pointer lookup ('' or '/a/b/0')."""
    if pointer in ("", "/"):
        return obj
    cur = obj
    for raw in pointer.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(pointer)
    return cur


def _num_variants(v) -> list[str]:
    out = []
    try:
        f = float(v)
    except (TypeError, ValueError):
        return [str(v)] if v not in (None, "") else []
    out += ["{:,.2f}".format(f), "{:,.2f}".format(abs(f))]
    if f == int(f):
        out += ["{:,}".format(int(f)), str(int(f))]
    if abs(f) < 1:
        out += ["{}%".format(int(round(f * 100)))]
    out.append(str(v))
    return out


def _date_variants(v: str) -> list[str]:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", str(v or ""))
    if not m:
        return [str(v)] if v else []
    y, mo, d = m.groups()
    return [f"{mo}/{d}/{y}", v, f"{mo}/{y}"]


class Packet:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.raw = json.loads(self.path.read_text())
        r = self.raw
        self.docs = {d["id"]: d for d in r["documents"]}
        self.ev = {e["id"]: e for e in r["extracted_values"]}
        self.ev_by_doc = defaultdict(list)
        for e in r["extracted_values"]:
            self.ev_by_doc[e["document_id"]].append(e)
        self.versions = {v["version"]: v for v in r["return_versions"]}
        self.lines = {ver: {l["key"]: l for l in v["lines"]} for ver, v in self.versions.items()}
        self.line_order = {ver: [l["key"] for l in v["lines"]] for ver, v in self.versions.items()}
        self.params = r["engine_reference"]["parameters"]
        self.rules = r["engine_reference"]["rules"]
        self.py = r["prior_years"]
        self.qa = {q["id"]: q for q in r["client"]["questionnaire"]}
        self.msgs = {m["id"]: m for m in r["client"]["messages"]}
        self.calls = {c["id"]: c for c in r["client"]["call_notes"]}
        self.people = {p["id"]: p for p in r["household"]["people"]}
        self.timeline = r["timeline"]
        self.staff = {s["id"]: s for s in r["firm"]["staff"]}
        self.client_users = {c["id"]: c for c in r["firm"]["client_users"]}
        self.assets = {ver: {a["asset_id"]: a for a in v.get("assets", [])} for ver, v in self.versions.items()}
        self.py_assets = {a["asset_id"]: a for a in self.py["asset_records"]}
        self._build_reverse_index()
        self._build_anchors()
        self.coef = self._linear_coefficients(CURRENT_VERSION)

    LINEAR_RULES = {"R-SUM", "R-CARRY"}

    def _linear_coefficients(self, ver):
        """For sum/carry lines, recover how inputs combine (+1 / -1 / not used).

        The engine lists inputs but not signs. We search for the sign pattern that reproduces
        the engine's own value; that lets the workpaper roll an approved adjustment up through
        every linear total (e.g. a lot -> Form 8949 total -> Schedule D -> AGI) without
        pretending to re-run non-linear computations such as tax or limitations.
        """
        import itertools
        L = self.lines[ver]
        out = {}
        for k, l in L.items():
            if l["rule"] not in self.LINEAR_RULES or not l["inputs"] or not isinstance(l["value"], (int, float)):
                continue
            if k.startswith("SchCA540NR.") and k.endswith(".D") and l["inputs"] == [k[:-2] + ".A"]:
                base = k[:-2]
                out[k] = {base + ".A": 1, base + ".B": -1, base + ".C": 1}
                continue
            ins = [i for i in l["inputs"] if i in L and isinstance(L[i]["value"], (int, float))]
            vals = [L[i]["value"] for i in ins]
            if len(ins) > 8:
                if abs(sum(vals) - l["value"]) <= 1:
                    out[k] = {i: 1 for i in ins}
                continue
            best = None
            for combo in itertools.product((1, -1, 0), repeat=len(ins)):
                if abs(sum(c * v for c, v in zip(combo, vals)) - l["value"]) <= 1:
                    score = (combo.count(0), combo.count(-1))
                    if best is None or score < best[0]:
                        best = (score, combo)
            if best:
                out[k] = {i: c for i, c in zip(ins, best[1]) if c}
        return out

    # ------------------------------------------------------------------ indexes
    def _build_reverse_index(self):
        """ev / doc / ref -> lines that cite them, and line -> parent lines (who uses me)."""
        self.cited_by = {}
        self.parents = {}
        for ver, lines in self.lines.items():
            cited = defaultdict(set)
            parents = defaultdict(set)
            for key, l in lines.items():
                for s in l.get("sources") or []:
                    cited[s].add(key)
                    if s.startswith("ev-") and s in self.ev:
                        cited[self.ev[s]["document_id"]].add(key)
                for i in l.get("inputs") or []:
                    parents[i].add(key)
            self.cited_by[ver] = cited
            self.parents[ver] = parents

    def _build_anchors(self):
        """Locate each extracted value inside its page's printed text.

        Real documents would provide pixel boxes from OCR; simulated pages get character
        spans. The document view only consumes `anchors`, so swapping in real PDFs means
        changing this function, not the UI.
        """
        self.anchors = {}  # ev id -> {page, span:[a,b] | None, found_text}
        for doc_id, evs in self.ev_by_doc.items():
            doc = self.docs[doc_id]
            pages = {p["page"]: p["printed_content"] for p in doc["pages"]}
            cursor = defaultdict(int)
            for e in evs:
                text = pages.get(e["page"], "")
                cands = []
                if e.get("text_as_read"):
                    cands.append(e["text_as_read"])
                if e["value_type"] == "date":
                    cands += _date_variants(e["value"])
                elif e["value_type"] in ("money", "quantity", "integer", "percent"):
                    cands += _num_variants(e["value"])
                elif e["value"] not in (None, "", True, False):
                    cands.append(str(e["value"]))
                span = None
                for start in (cursor[e["page"]], 0):
                    for c in cands:
                        if not c:
                            continue
                        idx = self._find_token(text, c, start)
                        if idx >= 0:
                            span = [idx, idx + len(c)]
                            break
                    if span:
                        break
                if not span:
                    span = self._fallback_span(text, e)
                if span and span[0] >= cursor[e["page"]]:
                    cursor[e["page"]] = span[1]
                self.anchors[e["id"]] = {"page": e["page"], "span": span,
                                         "found_text": text[span[0]:span[1]] if span else None}

    @staticmethod
    def _fallback_span(text: str, e: dict):
        """Booleans print as 'checked', blanks as '(not reported)', smudged digits partially."""
        hints = []
        if e["value_type"] == "boolean":
            m = re.match(r"(Box \w+)", e["label"])
            hints = [m.group(1)] if m else []
            hints += ["checked", "IRA/SEP/SIMPLE"]
        elif not e.get("text_as_read"):
            hints = ["(not reported)", "cost basis unknown", "cost  sold"]
        elif e.get("text_as_read"):
            hints = [e["text_as_read"][-6:]]
        for h in hints:
            i = text.find(h)
            if i >= 0:
                return [i, i + len(h)]
        return None

    @staticmethod
    def _find_token(text: str, token: str, start: int) -> int:
        i = text.find(token, start)
        while i >= 0:
            before = text[i - 1] if i > 0 else " "
            after = text[i + len(token)] if i + len(token) < len(text) else " "
            if not (before.isdigit() or before in ",.") and not (after.isdigit() or (after == "," and i + len(token) + 1 < len(text) and text[i + len(token) + 1].isdigit())):
                return i
            i = text.find(token, i + 1)
        return -1

    # ------------------------------------------------------------------ lookups
    def line(self, key: str, ver: str = CURRENT_VERSION):
        return self.lines.get(ver, {}).get(key)

    def py_value(self, key: str):
        return self.py["return_2024"]["lines"].get(key)

    def resolve(self, ref: str, ver: str = CURRENT_VERSION) -> dict:
        """Turn any reference format from the README into a displayable node."""
        try:
            if ref.startswith("ev-"):
                e = self.ev[ref]
                d = self.docs[e["document_id"]]
                return {"kind": "value", "id": ref, "label": e["label"], "value": e["value"],
                        "doc": e["document_id"], "doc_title": d["title"], "page": e["page"],
                        "field": e["field"], "confidence": e["confidence"], "text_as_read": e["text_as_read"],
                        "value_type": e["value_type"]}
            if re.fullmatch(r"D\d\d", ref):
                d = self.docs[ref]
                return {"kind": "document", "id": ref, "label": d["title"], "doc": ref}
            m = re.fullmatch(r"doc:(D\d\d)#page(\d+)", ref)
            if m:
                d = self.docs[m.group(1)]
                return {"kind": "document", "id": ref, "doc": m.group(1), "page": int(m.group(2)),
                        "label": f"{d['title']} - page {m.group(2)} (not extracted)"}
            if ref.startswith("qa-"):
                q = self.qa[ref]
                return {"kind": "client_answer", "id": ref, "label": q["question"], "value": q["answer"],
                        "by": q["answered_by"], "at": q["answered_at"]}
            if ref.startswith("msg-"):
                m_ = self.msgs[ref]
                return {"kind": "client_message", "id": ref, "label": f"Message from {m_['from']}",
                        "value": m_["text"], "at": m_["sent_at"]}
            if ref.startswith("call-"):
                c = self.calls[ref]
                return {"kind": "call_note", "id": ref, "label": "Client call " + c["date"][:10],
                        "value": " | ".join(c["notes"])}
            if ref.startswith("py:"):
                v = json_pointer(self.py, ref[3:])
                label = "Prior year: " + ref[3:].strip("/").replace("/", " › ")
                if ref.startswith("py:/return_2024/lines/"):
                    k = ref.split("/")[-1]
                    label = "2024 return: " + self.py["return_2024"]["line_labels"].get(k, k)
                return {"kind": "prior_year", "id": ref, "label": label, "value": v,
                        "doc": "D06" if "return_2024" in ref else "D07"}
            if ref.startswith("agency:"):
                v = json_pointer(self.raw["tax_agency_records"], ref[7:])
                return {"kind": "agency", "id": ref, "label": "Agency record: " + ref[7:].strip("/"), "value": v}
            if ref.startswith("asset:"):
                aid = ref[6:]
                a = self.assets.get(ver, {}).get(aid) or self.py_assets.get(aid)
                return {"kind": "asset", "id": ref, "label": a["description"], "value": a}
            if ref.startswith("household:"):
                p = self.people[ref[10:]]
                return {"kind": "household", "id": ref, "label": f"{p['name']} ({p['role']})", "value": p}
            if ref.startswith("ref:param."):
                n = ref[10:]
                return {"kind": "parameter", "id": ref, "label": "Engine parameter " + n, "value": self.params.get(n)}
            if ref.startswith("ref:fx."):
                _, _, cur, rate = ref.split(".")
                return {"kind": "parameter", "id": ref, "label": f"{cur} per USD ({rate.replace('_', ' ')})",
                        "value": self.params["fx"][cur][rate]}
            if ref.startswith("timeline:"):
                t = next(t for t in self.timeline if t["id"] == ref[9:])
                return {"kind": "timeline", "id": ref, "label": t["event"].replace("_", " "), "value": t.get("detail"), "at": t["at"]}
            if ref in self.versions:
                v = self.versions[ref]
                return {"kind": "version", "id": ref, "label": f"Return {ref} ({v['computed_at'][:10]})", "value": v["trigger"]}
            if ref in self.lines.get(ver, {}):
                l = self.lines[ver][ref]
                return {"kind": "line", "id": ref, "label": l["label"], "value": l["value"]}
        except (KeyError, IndexError, ValueError, StopIteration):
            pass
        return {"kind": "unknown", "id": ref, "label": ref, "missing": True}

    def exists(self, ref: str) -> bool:
        return self.resolve(ref).get("kind") != "unknown"
