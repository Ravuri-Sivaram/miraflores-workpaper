"""People, roles and the derived workpaper state.

State is never stored directly: it is a pure function of (packet, findings, events, as-of
date). That gives undo, history, "what changed since I last looked" and sign-offs that
reopen automatically when what they covered changes.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from .packet import AREA_LABELS, CURRENT_VERSION, Packet, area_of

SIGNOFF_AREAS = ["intake_and_engagement", "wages_and_stock_compensation", "investments_and_digital_assets", "business",
                 "rentals_and_sale", "pass_throughs_and_basis", "deductions_and_credits", "california",
                 "payments_and_penalty", "return_summary"]
LEVELS = ["preparer", "reviewer", "partner"]
PARTNER_THRESHOLD = 1000


def users(p: Packet):
    out = {}
    for s in p.raw["firm"]["staff"]:
        out[s["id"]] = {"id": s["id"], "name": s["name"], "role": s["role"], "credential": s.get("credential"),
                        "clearance": s["sensitive_data_clearance"], "active_from": s["active_from"], "note": s.get("note")}
    for c in p.raw["firm"]["client_users"]:
        out[c["id"]] = {"id": c["id"], "name": c["name"], "role": "client", "clearance": True,
                        "active_from": "2026-01-01", "credential": None}
    return out


class State:
    def __init__(self, p: Packet, findings: list[dict], events: list[dict], as_of: str):
        self.p = p
        self.as_of = as_of
        self.users = users(p)
        self.findings = {f["id"]: f for f in findings}
        voided = set()
        for e in events:
            if e["type"] == "undo":
                voided.update(e["data"].get("events", []))
        self.voided = voided
        self.events = [e for e in events if e["id"] not in voided and e["type"] != "undo"]
        self.all_events = events
        self.adjustments = {}
        self.verifications = defaultdict(list)
        self.notes = {}
        self.finding_status = {}
        self.judgments = []
        self.signoffs = []
        self.attachments = defaultdict(list)
        self.dispositions = {}
        self.time = []
        self.views = []
        self.seen = {}
        self.standards = dict(p.raw["firm"]["starter_standards"]["variance_review"])
        self.assignments = {}
        self.filing = {"client_approved": None, "filed": None, "amendment": None}
        self._replay()

    # -------------------- replay ------------------------
    def _replay(self):
        for e in self.events:
            t, d, tgt = e["type"], e["data"], e["target"]
            if t == "adjustment.propose":
                eff = d.get("effect") or {}
                self.adjustments[e["id"]] = {
                    "id": e["id"], "line": tgt, "from": d["from"], "to": d["to"], "category": d["category"],
                    "rates": d.get("rates"), "reason": d.get("reason", ""), "finding": d.get("finding"),
                    "proposed_by": e["actor"], "at": e["at"], "status": "proposed", "effect": eff,
                    "needs_partner": max(abs(eff.get("federal_tax", 0)), abs(eff.get("california_tax", 0)),
                                         abs(eff.get("federal_balance", 0)), abs(eff.get("california_balance", 0))) > PARTNER_THRESHOLD,
                    "amendment": bool(self.filing["filed"]), "history": [{"what": "proposed", "by": e["actor"], "at": e["at"]}]}
            elif t in ("adjustment.approve", "adjustment.reject", "adjustment.revert"):
                a = self.adjustments.get(tgt)
                if a:
                    a["status"] = {"adjustment.approve": "approved", "adjustment.reject": "rejected",
                                   "adjustment.revert": "reverted"}[t]
                    a["history"].append({"what": a["status"], "by": e["actor"], "at": e["at"], "note": d.get("note")})
                    if t == "adjustment.approve":
                        a["approved_by"] = e["actor"]
            elif t == "verify":
                self.verifications[tgt].append({"id": e["id"], "method": d["method"], "note": d.get("note", ""),
                                                "evidence": d.get("evidence", []), "by": e["actor"], "at": e["at"]})
            elif t == "note.add":
                self.notes[e["id"]] = {"id": e["id"], "target": tgt, "kind": d.get("kind", "comment"), "text": d["text"],
                                       "blocking": bool(d.get("blocking")), "by": e["actor"], "at": e["at"],
                                       "to": d.get("to"), "status": "open", "thread": [], "due": d.get("due")}
            elif t == "note.reply":
                n = self.notes.get(tgt)
                if n:
                    n["thread"].append({"by": e["actor"], "at": e["at"], "text": d["text"], "id": e["id"]})
                    if n["status"] == "open":
                        n["status"] = "answered"
            elif t == "note.clear":
                n = self.notes.get(tgt)
                if n:
                    n["status"] = "cleared"
                    n["cleared_by"] = e["actor"]
                    n["cleared_at"] = e["at"]
            elif t == "finding.set":
                self.finding_status[tgt] = {"status": d["status"], "note": d.get("note", ""), "by": e["actor"], "at": e["at"]}
            elif t == "judgment.record":
                self.judgments.append({"id": e["id"], "target": tgt, **d, "by": e["actor"], "at": e["at"]})
            elif t == "signoff":
                self.signoffs.append({"id": e["id"], "area": tgt, "level": d["level"], "hash": d["hash"],
                                      "snapshot": d.get("snapshot", {}), "by": e["actor"], "at": e["at"]})
            elif t == "attach":
                self.attachments[tgt].append({"id": e["id"], **d, "by": e["actor"], "at": e["at"]})
            elif t == "doc.disposition":
                self.dispositions[tgt] = {"reason": d["reason"], "by": e["actor"], "at": e["at"]}
            elif t == "time.log":
                self.time.append({"area": tgt, "hours": float(d["hours"]), "note": d.get("note", ""), "by": e["actor"], "at": e["at"]})
            elif t == "view.unmasked":
                self.views.append({"what": tgt, "by": e["actor"], "at": e["at"]})
            elif t == "seen":
                self.seen[e["actor"]] = e["at"]
            elif t == "standards.set":
                self.standards.update(d)
            elif t == "assign":
                self.assignments[tgt] = {**d, "by": e["actor"], "at": e["at"]}
            elif t == "filing.client_approved":
                self.filing["client_approved"] = {"by": e["actor"], "at": e["at"], **d}
            elif t == "filing.filed":
                self.filing["filed"] = {"by": e["actor"], "at": e["at"], **d}
            elif t == "filing.amend":
                self.filing["amendment"] = {"by": e["actor"], "at": e["at"], **d}

    # ------------------- values -------------------------
    def _rolled(self, key: str):
        """Workpaper value: own approved adjustment, else linear roll-up of adjusted inputs."""
        if not hasattr(self, "_memo"):
            self._memo = {}
            self._by_line = defaultdict(list)
            for a in self.adjustments.values():
                self._by_line[a["line"]].append(a)
        if key in self._memo:
            return self._memo[key]
        self._memo[key] = (None, False, False)  # cycle guard
        base = self.p.line(key)
        val = base["value"] if base else None
        appr = [a for a in self._by_line.get(key, []) if a["status"] == "approved"]
        if appr:
            res = (appr[-1]["to"], False, False)
        elif base and key in self.p.coef:
            d = 0.0
            for i, c in self.p.coef[key].items():
                iv = self._rolled(i)[0]
                ev = (self.p.line(i) or {}).get("value")
                if isinstance(iv, (int, float)) and isinstance(ev, (int, float)):
                    d += c * (iv - ev)
            res = (val + d if d else val, bool(d), False)
        elif base:
            stale = any(isinstance(self._rolled(i)[0], (int, float)) and self._rolled(i)[0] != (self.p.line(i) or {}).get("value")
                        for i in base["inputs"] if self.p.line(i))
            res = (val, False, stale)
        else:
            res = (val, False, False)
        self._memo[key] = res
        return res

    def effective(self, key: str):
        base = self.p.line(key)
        val = base["value"] if base else None
        appr = [a for a in self.adjustments.values() if a["line"] == key and a["status"] == "approved"]
        pend = [a for a in self.adjustments.values() if a["line"] == key and a["status"] == "proposed"]
        value, rolled, stale = self._rolled(key)
        if isinstance(value, float) and value == int(value):
            value = int(value) if isinstance(val, int) else value
        return {"engine": val, "value": value, "adjusted": bool(appr) or rolled, "rolled_up": rolled,
                "needs_engine": stale, "approved": appr, "pending": pend}

    def totals(self):
        v = self.p.versions[CURRENT_VERSION]["summary"]
        out = {"engine": v, "approved": defaultdict(float), "pending": defaultdict(float)}
        for a in self.adjustments.values():
            bucket = "approved" if a["status"] == "approved" else "pending" if a["status"] == "proposed" else None
            if bucket:
                for k, x in (a["effect"] or {}).items():
                    out[bucket][k] += x
        fed, ca = v["federal"], v["california"]
        ap = out["approved"]
        out["workpaper"] = {
            "federal_total_tax": fed["total_tax"] + ap["federal_tax"],
            "federal_balance_due": fed["balance_due"] + ap["federal_balance"],
            "california_total_tax": ca["total_tax"] + ap["california_tax"],
            "california_balance_due": ca["balance_due"] + ap["california_balance"],
        }
        out["approved"] = dict(out["approved"])
        out["pending"] = dict(out["pending"])
        return out

    # ------------------ sign-off -----------------------
    def area_lines(self, area: str):
        return [k for k in self.p.line_order[CURRENT_VERSION] if area_of(k) == area]

    def area_hash(self, area: str):
        snap = {k: self.effective(k)["value"] for k in self.area_lines(area)}
        adj = sorted((a["id"], a["status"]) for a in self.adjustments.values() if area_of(a["line"]) == area)
        fnd = sorted((fid, self.finding_status.get(fid, {}).get("status", "open"))
                     for fid, f in self.findings.items() if f.get("area") == area)
        blob = json.dumps({"lines": snap, "adj": adj, "findings": fnd}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:12], snap

    def signoff_status(self):
        out = {}
        for area in SIGNOFF_AREAS + ["partner_review"]:
            h, snap = self.area_hash(area) if area != "partner_review" else (self.overall_hash(), {})
            row = {}
            for lvl in LEVELS:
                so = [s for s in self.signoffs if s["area"] == area and s["level"] == lvl]
                if not so:
                    row[lvl] = None
                    continue
                s = so[-1]
                valid = s["hash"] == h
                changed = []
                if not valid and area != "partner_review":
                    changed = [k for k, v in snap.items() if s["snapshot"].get(k) != v]
                row[lvl] = {"by": s["by"], "at": s["at"], "valid": valid, "changed": changed[:20]}
            out[area] = row
        return out

    def overall_hash(self):
        parts = [self.area_hash(a)[0] for a in SIGNOFF_AREAS]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]

    def preparers_of(self, area: str):
        who = {a["proposed_by"] for a in self.adjustments.values() if area_of(a["line"]) == area}
        who |= {s["by"] for s in self.signoffs if s["area"] == area and s["level"] == "preparer"}
        return who

    # ------------------------ gates ------------------------
    def gates(self, findings_open_errors: list[str], recon_differs: list[str]):
        so = self.signoff_status()
        open_blocking = [n for n in self.notes.values() if n["blocking"] and n["status"] != "cleared"]
        need_partner = [a for a in self.adjustments.values()
                        if a["status"] == "approved" and a["needs_partner"]
                        and self.users.get(a.get("approved_by"), {}).get("role") != "partner"]
        pending = [a for a in self.adjustments.values() if a["status"] == "proposed"]
        missing_reviewer = [a for a in SIGNOFF_AREAS if not (so[a]["reviewer"] and so[a]["reviewer"]["valid"])]
        missing_preparer = [a for a in SIGNOFF_AREAS if not (so[a]["preparer"] and so[a]["preparer"]["valid"])]
        partner_ok = bool(so["partner_review"]["partner"] and so["partner_review"]["partner"]["valid"])
        g = [
            {"id": "notes", "label": "No open blocking review notes", "ok": not open_blocking,
             "detail": f"{len(open_blocking)} open"},
            {"id": "findings", "label": "Every error finding resolved or accepted with a reason", "ok": not findings_open_errors,
             "detail": f"{len(findings_open_errors)} open: " + ", ".join(findings_open_errors[:6])},
            {"id": "pending", "label": "No adjustments awaiting approval", "ok": not pending, "detail": f"{len(pending)} pending"},
            {"id": "partner_adj", "label": "Adjustments over 1,000 approved by a partner", "ok": not need_partner,
             "detail": f"{len(need_partner)} need partner"},
            {"id": "preparer", "label": "Preparer sign-off on every area (still valid)", "ok": not missing_preparer,
             "detail": ", ".join(AREA_LABELS.get(a, a) for a in missing_preparer)},
            {"id": "reviewer", "label": "Reviewer sign-off on every area (still valid)", "ok": not missing_reviewer,
             "detail": ", ".join(AREA_LABELS.get(a, a) for a in missing_reviewer)},
            {"id": "partner", "label": "Partner sign-off on the whole return (still valid)", "ok": partner_ok, "detail": ""},
        ]
        return g

    def can(self, uid: str, action: str, **kw) -> tuple[bool, str]:
        u = self.users.get(uid)
        if not u:
            return False, "Unknown user"
        if self.as_of < u["active_from"]:
            return False, f"{u['name']} is not on the engagement until {u['active_from']}"
        role = u["role"]
        staff_tax = role in ("preparer", "reviewer", "partner")
        filed = bool(self.filing["filed"]) and not self.filing["amendment"]
        if action == "propose_adjustment":
            if not staff_tax:
                return False, "Only preparers, reviewers and partners change the return"
            if filed:
                return False, "Return is frozen at filing - start an amendment first"
            return True, ""
        if action == "approve_adjustment":
            a = kw["adj"]
            if role not in ("reviewer", "partner"):
                return False, "Only a reviewer or partner approves adjustments"
            if a["proposed_by"] == uid:
                return False, "You cannot approve your own adjustment"
            if a["needs_partner"] and role != "partner":
                return False, "Tax effect over 1,000 - partner approval required (firm rule)"
            return True, ""
        if action in ("verify", "judgment", "finding_resolve", "client_question", "disposition", "assign"):
            return (staff_tax, "" if staff_tax else "Preparer, reviewer or partner only")
        if action == "finding_accept":
            return (role in ("reviewer", "partner"), "Accepting a finding without change needs a reviewer or partner")
        if action == "review_note":
            ok = role in ("reviewer", "partner", "quality_reviewer")
            return ok, "" if ok else "Review notes are raised by reviewers, partners or quality review"
        if action == "comment":
            return (role != "client" or kw.get("client_ok", False), "")
        if action == "reply":
            n = kw["note"]
            if role == "client":
                return (n["kind"] == "client_question", "Clients answer client questions only")
            return True, ""
        if action == "clear_note":
            n = kw["note"]
            ok = n["by"] == uid or role == "partner"
            return ok, "" if ok else "Only the person who raised it (or a partner) clears a note"
        if action == "signoff":
            lvl, area = kw["level"], kw["area"]
            if role != lvl:
                return False, f"{lvl.title()} sign-off needs a {lvl}"
            if lvl in ("reviewer", "partner") and uid in self.preparers_of(area):
                return False, "You prepared or adjusted this area - you cannot review it (firm rule)"
            return True, ""
        if action == "client_approve":
            return (role == "client", "Only the client approves and signs Form 8879")
        if action == "file":
            ok = uid == self.p.raw["firm"]["firm_identity"]["paid_preparer_signing_returns"]
            return ok, "" if ok else "Only the signing partner (ERO) transmits the return"
        if action == "amend":
            return (role == "partner", "Only a partner opens an amendment")
        if action == "admin":
            return (role == "firm_administrator", "Firm administrator only")
        if action == "reveal":
            if role == "client":
                return True, ""
            return (u["clearance"], "No sensitive-data clearance - values stay masked")
        if action == "time":
            return (role != "client", "")
        return False, "Unknown action"
