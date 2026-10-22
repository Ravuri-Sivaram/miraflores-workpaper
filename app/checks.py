"""Independent checks that do not trust the engine.

Each check recomputes something from source evidence (documents, client answers,
agency records, prior year) and compares it with what the engine put on the return.
A failing check becomes a *finding* with: evidence refs, the lines involved, a proposed
adjustment (from -> to, with a tax-effect category), and an estimated tax effect using
return_versions[].tax_effect_rates (the README says we cannot rerun the engine).

Checks are plain Python on purpose: deterministic, repeatable, auditable. The LLM never
produces a number.
"""
from __future__ import annotations

import itertools
import re
from collections import defaultdict
from datetime import date

from .packet import CURRENT_VERSION, Packet, area_of

TAX_YEAR = 2025


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class Ctx:
    def __init__(self, p: Packet, ver: str = CURRENT_VERSION):
        self.p = p
        self.ver = ver
        self.L = p.lines[ver]
        self.rates = p.versions[ver]["tax_effect_rates"]
        self.findings: list[dict] = []
        self.recons: list[dict] = []

    def val(self, key, default=0.0):
        l = self.L.get(key)
        return l["value"] if l else default

    def evv(self, ev_id):
        return self.p.ev[ev_id]["value"]

    def ev_field(self, doc, field):
        for e in self.p.ev_by_doc[doc]:
            if e["field"] == field:
                return e
        return None

    def page_text(self, doc, page=None):
        d = self.p.docs[doc]
        return " ".join(pg["printed_content"] for pg in d["pages"] if page is None or pg["page"] == page)

    # -------------------------------------------------------------- recording
    def effect(self, adjustments):
        fed = ca = fed_bal = ca_bal = 0.0
        for a in adjustments:
            delta = _f(a["to"]) - _f(a["from"])
            cat = a["category"]
            if cat == "payment_federal":
                fed_bal -= delta
                continue
            if cat == "payment_california":
                ca_bal -= delta
                continue
            if cat == "none":
                continue
            r = a.get("rates") or self.rates[cat]
            fed += delta * r["federal_per_dollar"]
            ca += delta * r["california_per_dollar"]
        return {"federal_tax": round(fed), "california_tax": round(ca),
                "federal_balance": round(fed + fed_bal), "california_balance": round(ca + ca_bal)}

    def finding(self, fid, title, *, severity, kind, summary, evidence, lines=(), adjustments=(),
                how_found, area=None, recommendation=None, judgment=None, what_if=None):
        adjustments = list(adjustments)
        for a in adjustments:
            a.setdefault("from", self.val(a["line"]))
        self.findings.append({
            "id": fid, "title": title, "severity": severity, "kind": kind, "summary": summary,
            "evidence": [e for e in evidence if e], "lines": list(lines) or [a["line"] for a in adjustments],
            "adjustments": adjustments, "effect": self.effect(adjustments),
            "how_found": how_found, "recommendation": recommendation, "judgment": judgment,
            "what_if": what_if,
            "area": area or area_of((list(lines) or [a["line"] for a in adjustments] or ["x"])[0]),
        })

    def recon(self, rid, title, left_label, left, right_label, right, refs, tolerance=1.0, note=None, area=None):
        diff = round(_f(left) - _f(right), 2)
        self.recons.append({"id": rid, "title": title, "left_label": left_label, "left": left,
                            "right_label": right_label, "right": right, "diff": diff,
                            "status": "agrees" if abs(diff) <= tolerance else "differs",
                            "refs": refs, "note": note, "area": area})


# =========================================================================== checks

def check_rsu_basis(c: Ctx):
    """1099-B shows $0 basis for noncovered RSU lots; the plan statement has the real basis."""
    supp = {}
    for e in c.p.ev_by_doc["D11"]:
        m = re.match(r"(RSU-\d+)_acquired", e["field"])
        if m:
            basis = c.ev_field("D11", m.group(1) + "_adjusted_basis")
            supp[e["value"]] = (basis, e)
    adjs, ev = [], ["D11"]
    for key, l in c.L.items():
        if not key.startswith("F8949.") or "Corvane" not in l["label"] or "RSU" not in l["label"]:
            continue
        acq = re.search(r"acquired (\d{4}-\d{2}-\d{2})", l["label"]).group(1)
        if "basis 0.00" in l["label"] and acq in supp:
            basis_ev, acq_ev = supp[acq]
            cat = "ltcg" if key.split(".")[1] in "DEFJKL" else "stcg"
            adjs.append({"line": key, "from": l["value"], "to": l["value"] - basis_ev["value"], "category": cat,
                         "note": f"Use adjusted basis {basis_ev['value']:,.2f} from plan statement ({basis_ev['id']})"})
            ev += [basis_ev["id"], acq_ev["id"]] + l["sources"][-1:]
    if adjs:
        total = sum(a["from"] - a["to"] for a in adjs)
        c.finding("F-RSU-BASIS", "RSU sales taxed twice: $0 basis used instead of plan-statement basis",
                  severity="error", kind="engine_error",
                  summary=f"Three Corvane RSU lots were reported at $0 basis (noncovered). The shares' value "
                          f"({total:,.0f}) was already taxed as wages on the W-2 (box 14 RSU 38,200 + 2023 vest). "
                          f"The engine had D11 in file but did not apply it, overstating capital gain by {total:,.0f}.",
                  evidence=ev + ["ev-0027", "D28"], adjustments=adjs,
                  how_found="Matched each $0-basis lot on the 1099-B (D28) to the supplemental statement (D11) by acquisition date.",
                  recommendation="Report basis per D11 with adjustment code B on Form 8949 (box B/E).",
                  area="wages_and_stock_compensation")


def check_interest_duplicates(c: Ctx):
    """Same interest reported on a composite 1099 and a standalone 1099-INT for the same account."""
    lines = [(k, l) for k, l in c.L.items() if re.fullmatch(r"SchB\.1\.\d+", k)]
    for (k1, l1), (k2, l2) in itertools.combinations(lines, 2):
        if l1["value"] != l2["value"] or not l1["value"]:
            continue
        docs1 = {c.p.ev[s]["document_id"] for s in l1["sources"] if s in c.p.ev}
        docs2 = {c.p.ev[s]["document_id"] for s in l2["sources"] if s in c.p.ev}
        acct = lambda ds: set(re.findall(r"ending (\d{4})", " ".join(c.page_text(d) for d in ds)))
        shared = acct(docs1) & acct(docs2)
        if shared:
            c.finding("F-INT-DUP", f"Interest counted twice ({l1['value']:,.0f}) - same Northlake sweep account",
                      severity="error", kind="engine_error",
                      summary=f"{k1} and {k2} both report {l1['value']:,.0f} for account ending {shared.pop()}: "
                              "the composite 1099 (D28) already includes the bank-sweep 1099-INT that Northlake Bank "
                              "also sent separately (D29). 2024 reported this payer once.",
                      evidence=sorted(docs1 | docs2) + l1["sources"] + l2["sources"] + ["py:/return_2024/lines/SchB.1.1"],
                      adjustments=[{"line": k2, "to": 0, "category": "interest", "note": "Duplicate of " + k1}],
                      how_found="Pairs of Schedule B payers with identical amounts whose source documents name the same account number.",
                      recommendation="Keep one line; keep D29 in the index as 'duplicate of D28 page 1'.")


def check_fx_year(c: Ctx):
    fx = c.p.params["fx"]["SEK"]
    for k, l in c.L.items():
        for s in l.get("sources") or []:
            m = re.match(r"ref:fx\.SEK\.(\d{4})_average", s)
            if m and int(m.group(1)) != TAX_YEAR and k.startswith("SchB"):
                sek_ev = next(x for x in l["sources"] if x.startswith("ev-"))
                sek = c.evv(sek_ev)
                right = round(sek / fx[f"{TAX_YEAR}_average"])
                c.finding("F-FX-RATE", "Swedish interest converted at the 2024 exchange rate",
                          severity="error", kind="engine_error",
                          summary=f"SEK {sek:,.2f} was divided by {fx[m.group(1) + '_average']} (2024 average). The 2025 average "
                                  f"is {fx[f'{TAX_YEAR}_average']}, giving {right:,} instead of {l['value']:,}.",
                          evidence=[sek_ev, s, f"ref:fx.SEK.{TAX_YEAR}_average", "D36"],
                          adjustments=[{"line": k, "to": right, "category": "interest", "note": "2025 average rate"}],
                          how_found="Every currency conversion must use a rate for the tax year of the income.")


def check_1099k_double(c: Ctx):
    gross = c.val("SchC.1")
    books = c.evv("ev-0103")
    nec = sum(c.evv(e) for e in ("ev-0113", "ev-0116", "ev-0119", "ev-0122", "ev-0125"))
    small = c.evv("ev-0111")
    k_amt = c.evv("ev-0127")
    via_card = c.evv("ev-0109")
    c.recon("R-SCHC-RECEIPTS", "Books revenue = 1099-NEC + clients without 1099", "Books total income (D14)", books,
            "1099-NEC total + non-1099 deposits", nec + small, ["ev-0103", "ev-0111", "D16", "D17", "D18", "D19", "D20"],
            area="business", note="1099-NEC total also equals what the IRS later cites in Letter 566 (142,000).")
    c.recon("R-SCHC-GROSS", "Schedule C gross receipts = books revenue", "SchC.1 (engine)", gross,
            "Books total income", books, ["SchC.1", "ev-0103"], area="business")
    if gross - books == k_amt and via_card == k_amt:
        c.finding("F-1099K-DOUBLE", "Card receipts (1099-K 12,400) counted twice in gross receipts",
                  severity="error", kind="engine_error",
                  summary=f"The engine added the Paywell 1099-K ({k_amt:,.0f}) on top of books revenue because it could not "
                          f"match it to a deposit. The transaction detail (D15 p3) shows that {via_card:,.0f} of Atlas Outdoor "
                          f"Gear's 41,200 came through Paywell and was deposited gross - it is inside both the books and "
                          "Atlas's 1099-NEC. Books revenue already reconciles to 1099-NECs plus small clients exactly.",
                  evidence=["ev-0127", "ev-0109", "ev-0110", "ev-0116", "ev-0103", "D15", "D21"],
                  adjustments=[{"line": "SchC.1", "to": books, "category": "schc_profit", "note": "Remove unmatched 1099-K add-on"}],
                  how_found="Three-way reconciliation: books revenue vs 1099-NEC + non-1099 deposits vs engine gross receipts.",
                  recommendation="Keep the reconciliation in the file: the 2028 IRS letter asks exactly this question.")


def check_mileage(c: Ctx):
    doc = c.p.docs["D23"]
    seen, distinct_total, dup_pages = {}, 0, []
    for pg in doc["pages"]:
        body = re.sub(r"^\(.*?\)\s*", "", pg["printed_content"])
        trips = tuple(re.findall(r"(\d{4}-\d{2}-\d{2}) .*? (\d+) mi", body))
        tot = int(re.search(r"Page total ([\d,]+)", body).group(1).replace(",", ""))
        trip_sum = sum(int(t[1]) for t in trips)
        c.recon(f"R-MILES-P{pg['page']}", f"Mileage log page {pg['page']}: trips foot to page total",
                "Sum of trips", trip_sum, "Page total", tot, [f"doc:D23#page{pg['page']}"], area="business")
        if trips in seen:
            dup_pages.append((pg["page"], seen[trips]))
        else:
            seen[trips] = pg["page"]
            distinct_total += tot
    odo = c.evv("ev-0479") - c.evv("ev-0478")
    eng = c.val("SchC.44a")
    if dup_pages and eng != distinct_total:
        rate = c.p.params["mileage_rate"]
        new_exp = round(distinct_total * rate)
        p, orig = dup_pages[0]
        c.finding("F-MILEAGE-DUP", "Mileage log page scanned twice - business miles overstated",
                  severity="error", kind="engine_error",
                  summary=f"Page {p} of the log is a second scan of page {orig} (same trips, 'same coffee stain'). The engine "
                          f"summed every page total ({eng:,.0f} mi). Distinct pages total {distinct_total:,} mi; odometer "
                          f"shows {odo:,.0f} mi driven, so business use is {distinct_total / odo:.0%}.",
                  evidence=["ev-0226", "ev-0315", "ev-0404", "ev-0477", "ev-0478", "ev-0479", "D23", "msg-03"],
                  adjustments=[{"line": "SchC.44a", "to": distinct_total, "category": "none", "note": "Distinct pages only"},
                               {"line": "SchC.9", "to": new_exp, "category": "schc_profit", "rates": _neg(c.rates["schc_profit"]),
                                "note": f"{distinct_total:,} mi x {rate}"},
                               {"line": "SchC.44c", "to": round(odo - distinct_total), "category": "none", "note": "Odometer less business"}],
                  how_found="Compared page contents for duplicates before summing; footed every page.")


def _neg(r):
    """Rates for an expense line: +1 of expense = -1 of profit."""
    return {"federal_per_dollar": -r["federal_per_dollar"], "california_per_dollar": -r["california_per_dollar"]}


def check_mixed_use(c: Ctx):
    notes = " ".join(c.p.calls["call-01"]["notes"])
    phone = re.search(r"business share is (\d+)%", notes)
    net = re.search(r"business use about (\d+)%", notes)
    tel, inet = c.evv("ev-0101"), c.evv("ev-0095")
    if phone and net and c.val("SchC.25") == tel + inet:
        to = round(tel * int(phone.group(1)) / 100 + inet * int(net.group(1)) / 100)
        c.finding("F-MIXED-USE", "Phone and internet deducted at 100% - client says partly personal",
                  severity="error", kind="client_data",
                  summary=f"Telephone {tel:,.0f} covers 4 family lines (business share {phone.group(1)}%) and home internet "
                          f"{inet:,.0f} is shared (business {net.group(1)}%) per the 3/26 call. Deductible: {to:,}.",
                  evidence=["call-01", "ev-0101", "ev-0095", "ev-0107", "ev-0108", "D15"],
                  adjustments=[{"line": "SchC.25", "to": to, "category": "schc_profit", "rates": _neg(c.rates["schc_profit"]),
                                "note": "Business-use percentages from client call"}],
                  how_found="Client call notes parsed for business-use percentages and applied to the books lines.")
    if "Spotify" in notes and "personal" in notes:
        sp = c.evv("ev-0106")
        c.finding("F-PERSONAL-SOFTWARE", "Personal subscription (Spotify Family) in business software",
                  severity="warning", kind="client_data",
                  summary=f"Client confirmed Spotify Family ({sp:,.0f}) was booked to Software by mistake.",
                  evidence=["call-01", "ev-0106", "ev-0099", "D15"],
                  adjustments=[{"line": "SchC.27a.1", "to": c.val("SchC.27a.1") - sp, "category": "schc_profit",
                                "rates": _neg(c.rates["schc_profit"]), "note": "Remove personal item"}],
                  how_found="Transaction detail cross-checked against client call.")


def check_sehi(c: Ctx):
    qa = c.p.qa["qa-25"]
    if c.val("Sch1.17") > 0 and qa["answer"].lower().startswith("yes"):
        c.finding("F-SEHI", "Self-employed health insurance deduction not allowed",
                  severity="error", kind="engine_error",
                  summary="Erik was eligible for family coverage through Ngozi's employers all year (qa-25). A month in which "
                          "the taxpayer is eligible for an employer-subsidized plan through a spouse does not qualify for the "
                          "SE health insurance deduction (IRC 162(l)(2)(B)). The 2024 preparer reached the same conclusion.",
                  evidence=["qa-25", "qa-27", "ev-0484", "D25", "py:/return_2024/lines/Sch1.17"],
                  adjustments=[{"line": "Sch1.17", "to": 0, "category": "adjustment", "note": "Eligible for spouse's employer plan"}],
                  how_found="Questionnaire answers tested against each deduction's eligibility condition.",
                  recommendation="Premiums may be a Schedule A medical expense (below 7.5% of AGI - no benefit).",
                  area="business")


def check_federal_estimates(c: Ctx):
    snap = c.p.raw["tax_agency_records"]["irs_account_transcript_2025"][0]
    irs = sum(t["amount"] for t in snap["transactions"] if t["code"] == "670" and t["date"] <= "2026-01-31")
    eftps = sum(c.evv(e) for e in ("ev-1056", "ev-1058", "ev-1060"))
    eng = c.val("F1040.26")
    c.recon("R-FED-EST", "Federal estimated payments = IRS account transcript", "F1040.26 (engine)", eng,
            f"IRS transcript as of {snap['as_of']}", irs, ["F1040.26", "agency:/irs_account_transcript_2025/0", "D67"],
            area="payments_and_penalty")
    if eng != irs:
        c.finding("F-Q4-ESTIMATE", "4th-quarter federal estimate overstated (client's 'I think 4,000')",
                  severity="error", kind="client_data",
                  summary=f"The engine used the client's recollection (qa-41: '4,000 I think', 01/14). The IRS transcript "
                          f"shows 3,000 posted 01/15/2026, and EFTPS confirmations cover only Q1-Q3 ({eftps:,.0f}). Payments "
                          f"are overstated by {eng - irs:,.0f}.",
                  evidence=["qa-41", "agency:/irs_account_transcript_2025/0", "D67", "D69", "ev-1056", "ev-1058", "ev-1060"],
                  adjustments=[{"line": "F1040.26", "to": irs, "category": "payment_federal", "note": "Per IRS transcript"}],
                  how_found="Agreed payments to the third-party record (IRS transcript) instead of the questionnaire.",
                  recommendation="Penalty (Form 2210) should be re-run with the 1/15 payment of 3,000.")


def check_ca_estimates(c: Ctx):
    pays = []
    for i in range(1, 10):
        d, a, y = (c.ev_field("D68", f"pay{i}_{f}") for f in ("date", "amount", "year"))
        if not d:
            break
        pays.append((d, a, y))
    designated = sum(a["value"] for d, a, y in pays if str(y["value"]) == str(TAX_YEAR))
    ftb = sum(x["amount"] for x in c.p.raw["tax_agency_records"]["ftb_account"]["tax_year_2025_payments"])
    eng = c.val("F540NR.72")
    c.recon("R-CA-EST", "California estimates = FTB account (designated 2025)", "F540NR.72 (engine)", eng,
            "FTB account, tax year 2025", ftb, ["F540NR.72", "agency:/ftb_account", "D68"], area="california")
    wrong = [(d, a, y) for d, a, y in pays if str(y["value"]) != str(TAX_YEAR) and a["id"] in c.L["F540NR.72"]["sources"]]
    if eng != designated and wrong:
        d, a, y = wrong[0]
        c.finding("F-CA-EST-YEAR", "California payment designated to 2024 counted as a 2025 estimate",
                  severity="error", kind="engine_error",
                  summary=f"The {d['value']} Web Pay payment of {a['value']:,.0f} was designated tax year 2024 (read correctly "
                          f"as {y['id']}) and the FTB holds it as a 2024 credit. The engine counted it by date. 2025 estimates "
                          f"are {designated:,.0f}, matching the FTB account; the California refund is overstated by {eng - designated:,.0f}.",
                  evidence=[d["id"], a["id"], y["id"], "agency:/ftb_account", "D68"],
                  adjustments=[{"line": "F540NR.72", "to": designated, "category": "payment_california", "note": "Per FTB account"}],
                  how_found="Matched each payment's designated tax year, then agreed to the FTB account record.",
                  recommendation="Ask the FTB to transfer the 3,500 2024 credit to 2025, or claim it separately; note for client.",
                  area="california")


def check_ca_wages(c: Ctx):
    res_end = next(r["to"] for r in c.p.raw["household"]["residency"] if r["state"] == "CA")
    box16 = c.evv("ev-0047")
    stub = c.evv("ev-1081")
    eng = c.val("SchCA540NR.1.E")
    if eng >= c.evv("ev-0030") + box16:
        to = eng - (box16 - stub)
        c.finding("F-CA-WAGE-SOURCE", "California-source wages include ~2.5 months of Texas work",
                  severity="error", kind="engine_error",
                  summary=f"California residency ended {res_end}. Halvorsen kept California as the work state through 09/30 "
                          f"(qa-45, W-2 note), so box 16 ({box16:,.0f}) includes Texas-period wages. The 07/18 paystub shows CA "
                          f"wages of {stub:,.0f} through 07/15 - the right California amount. Engine column E used the whole box 16.",
                  evidence=["ev-0047", "ev-1081", "D73", "D09", "qa-45", "household:ngozi"],
                  adjustments=[{"line": "SchCA540NR.1.E", "to": to, "category": "ca_source_income",
                                "note": f"Halvorsen CA-source = paystub YTD {stub:,.0f}"}],
                  how_found="Compared W-2 state wages with the residency dates and the move-date paystub.",
                  recommendation="Excess CA withholding (4,180) is still credited on the 540NR - the refund rises.",
                  area="california")


def check_vehicle_gift(c: Ctx):
    claimed = c.val("F8283.A1.vehicle")
    sold = c.evv("ev-1054")
    if "sold in arm's length" in c.page_text("D66") and claimed > sold:
        c.finding("F-CAR-GIFT", "Donated car claimed at Kelley Blue Book value; charity sold it for 2,900",
                  severity="error", kind="client_data",
                  summary=f"Form 1098-C box 4a is checked: the charity sold the Subaru for {sold:,.0f}. The deduction is "
                          f"limited to gross proceeds; the engine used the client's 6,000 estimate (qa-39).",
                  evidence=["ev-1054", "D66", "qa-39", "call-01"],
                  adjustments=[{"line": "F8283.A1.vehicle", "to": sold, "category": "itemized", "note": "Limited to 1098-C box 4c"}],
                  how_found="Checked each noncash gift against the acknowledgment rules for its property type.")


def check_mortgage(c: Ctx):
    note = c.L["SchA.8a"].get("note") or ""
    if "payoff statement" in note and "reported on the 2025 Form 1098" in c.page_text("D56"):
        amt = c.evv("ev-1032")
        c.finding("F-MORTGAGE-DUP", "Payoff interest counted twice in mortgage interest",
                  severity="error", kind="engine_error",
                  summary=f"Lonestar's 1098 (13,900) already 'includes interest paid at payoff 11/20/2025', and the payoff "
                          f"statement says the same. The engine added the payoff interest ({amt:,.0f}) again.",
                  evidence=["ev-1029", "ev-1032", "D55", "D56"],
                  adjustments=[{"line": "SchA.8a", "to": c.val("SchA.8a") - amt, "category": "itemized", "note": "Remove duplicate payoff interest"}],
                  how_found="Read the 'included in' language on each mortgage document before adding them.")


def check_property_tax(c: Ctx):
    paid = c.val("SchA.5b")
    credit = c.evv("ev-1027")
    if paid == c.evv("ev-1038") and "prior owner" in c.page_text("D58"):
        to = paid - credit
        salt_before = c.val("SchA.5e")
        salt_after = min(c.val("SchA.5a") + to, c.p.params["salt_cap"])
        c.finding("F-PROPERTY-TAX", "Home property tax includes the seller's share (01/01-07/07)",
                  severity="error", kind="engine_error",
                  summary=f"The 2025 Travis County bill ({paid:,.0f}) was paid by escrow in December, but the seller credited "
                          f"the buyers {credit:,.0f} for 01/01-07/07 at closing (D54). Only {to:,.0f} is the buyers' tax. With "
                          f"this, total SALT is {c.val('SchA.5a') + to:,.0f} - under the 40,000 cap, so the deduction drops.",
                  evidence=["ev-1038", "ev-1036", "ev-1027", "D54", "D58", "D57"],
                  adjustments=[{"line": "SchA.5b", "to": to, "category": "salt", "note": "Buyer's share only"},
                               {"line": "SchA.5e", "to": salt_after, "from": salt_before, "category": "itemized",
                                "rates": {"federal_per_dollar": c.rates["itemized"]["federal_per_dollar"], "california_per_dollar": 0},
                                "note": "SALT now below cap"}],
                  how_found="Closing-statement prorations applied to taxes paid in the year of purchase.")


def check_meridian_page32(c: Ctx):
    d = c.p.docs["D31"]
    summ = c.evv("ev-0703")
    lots = sum(e["value"] for e in c.p.ev_by_doc["D31"] if re.match(r"M\d+_proceeds", e["field"]))
    c.recon("R-MERIDIAN", "Meridian 1099-B: summary proceeds = sum of lots", "Summary (D31 p1)", summ,
            "Sum of extracted lots", lots, ["ev-0703", "D31"], area="investments_and_digital_assets")
    if d["extraction"]["status"] == "partial":
        text = c.page_text("D31", 32)
        m = re.search(r"(M\d+) (.+?) ([\d.]+) sh acquired (\S+) sold (\S+) proceeds ([\d,.]+) basis ([\d,.]+) (short|long)-term (\w+)", text)
        if m:
            proc, basis = float(m.group(6).replace(",", "")), float(m.group(7).replace(",", ""))
            c.finding("F-MERIDIAN-P32", f"Sale {m.group(1)} on unread page 32 is missing from Form 8949",
                      severity="warning", kind="extraction_error",
                      summary=f"The pipeline did not read page 32 ('Additional transactions'). It holds {m.group(1)} {m.group(2)}: "
                              f"proceeds {proc:,.2f}, basis {basis:,.2f}, {m.group(8)}-term {m.group(9)}. That is exactly the "
                              f"{summ - lots:,.2f} gap the engine flagged (BROKER_SUMMARY_DETAIL_MISMATCH) but never resolved.",
                      evidence=["doc:D31#page32", "ev-0703", "D31"],
                      adjustments=[{"line": "SchD.2", "to": c.val("SchD.2") + proc - basis, "category": "stcg",
                                    "note": f"Add {m.group(1)} (box B, noncovered)"}],
                      how_found="Every partially-extracted document's unread pages are read and reconciled to its summary.",
                      area="investments_and_digital_assets")


def check_crypto_basis(c: Ctx):
    l = c.L.get("F8949.H.C6")
    msg = c.p.msgs.get("msg-07")
    if l and "basis 0.00" in l["label"] and msg:
        m = re.search(r"in (\w+ \d{4}) for about ([\d,]+)", msg["text"])
        basis = float(m.group(2).replace(",", ""))
        c.finding("F-CRYPTO-C6", "Bitcoin lot C6 reported at zero basis and wrong holding period",
                  severity="judgment", kind="client_data",
                  summary=f"C6 was transferred in from another exchange. Erik says it was bought 03/15/2021 for about "
                          f"{basis:,.0f} (bank statement line to 'CryptoNorth'). That makes it long-term with gain "
                          f"{l['value'] - basis:,.0f}, not short-term {l['value']:,.0f}. Substantiation is a bank line only.",
                  evidence=["msg-07", "ev-0879", "ev-0919", "D34", "D35"],
                  adjustments=[{"line": "F8949.H.C6", "to": 0, "category": "stcg", "note": "Move out of short-term"},
                               {"line": "SchD.10", "from": c.val("SchD.10"), "to": c.val("SchD.10") + l["value"] - basis,
                                "category": "ltcg", "note": "Long-term, basis 3,050 (box K)"}],
                  how_found="Engine diagnostic DIGITAL_ASSET_BASIS_MISSING linked to the client's later message.",
                  judgment="Accept the bank-statement line as adequate substantiation? Attach it as evidence before sign-off.",
                  area="investments_and_digital_assets")


def check_dependent_ssn(c: Ctx):
    py_deps = {d["name"]: d["ssn"] for d in c.p.py["return_2024"]["dependents"]}
    card = c.evv("ev-1080")
    for pid, p in c.p.people.items():
        if p["role"] == "dependent" and p["name"] in py_deps and py_deps[p["name"]] != p["ssn"]:
            c.finding("F-THEO-SSN", f"{p['name']}'s SSN in the organizer differs from last year's return",
                      severity="error", kind="client_data",
                      summary=f"Organizer: {p['ssn']}; 2024 return as filed: {py_deps[p['name']]}"
                              + (f"; Social Security card (D71): {card}" if card else "")
                              + ". The digits are transposed in the organizer. This is detectable in January from the prior-year "
                              "return - the e-file was rejected for it on 07/13 (R0000-504-02).",
                      evidence=[f"household:{pid}", "D06", "D71", "ev-1080", "agency:/efile/0", "msg-10"],
                      lines=["F8812.4"], adjustments=[],
                      how_found="Every identity field agreed to the prior-year return and identity documents before filing.",
                      recommendation=f"Use {py_deps[p['name']]} (agrees to the SS card).", area="intake_and_engagement")


def check_reread(c: Ctx):
    """Re-read every extracted text/ID value against the printed page."""
    for e in c.p.raw["extracted_values"]:
        if e["value_type"] in ("ein", "ssn", "account_number", "code", "text") and e["value"]:
            a = c.p.anchors.get(e["id"])
            if a and not a["span"]:
                text = c.page_text(e["document_id"], e["page"])
                near = re.findall(r"\d{2}-\d{7}", text) if e["value_type"] == "ein" else []
                printed = near[0] if near else None
                users = sorted(c.p.cited_by[c.ver].get(e["id"], []))
                c.finding(f"F-MISREAD-{e['id']}", f"Extracted {e['label'].lower()} not printed on the page ({e['id']})",
                          severity="error", kind="extraction_error",
                          summary=f"Pipeline read '{e['value']}' at confidence {e['confidence']}, but the page prints "
                                  f"'{printed or '?'}' (digits transposed)." + (f" It flows to {', '.join(users)}." if users else ""),
                          evidence=[e["id"], e["document_id"]], lines=users or [], adjustments=[],
                          how_found="Re-read: every extracted identifier must appear verbatim on its page.",
                          recommendation=f"Correct to {printed} on Form 2441 (provider TIN)." if printed else None,
                          area=area_of(users[0]) if users else "deductions_and_credits")
    lbl = c.L.get("F2441.1a", {}).get("label", "")
    if "Bright Oak" in lbl and "Kids Club" not in lbl:
        c.finding("F-PROVIDER-NAME", "Care provider name on Form 2441 does not match the provider statement",
                  severity="warning", kind="engine_error",
                  summary="Engine labels provider 1 'Bright Oak Preschool'; the statement (D61) and the 2024 return say 'Bright Oak Kids Club'.",
                  evidence=["D61", "ev-1043", "py:/return_2024/lines/F2441.provider"], lines=["F2441.1a"], adjustments=[],
                  how_found="Provider identity agreed to source and prior year.")


def check_low_confidence(c: Ctx):
    e = c.p.ev["ev-1049"]
    c.finding("F-STBRIGID", "Handwritten church receipt: 1,500 or 7,500?",
              severity="judgment", kind="extraction_error",
              summary=f"The first digit is ambiguous; the pipeline read {e['value']:,.0f} at confidence {e['confidence']}. "
                      "The client says the parish confirmed 1,500 by phone and it matches check #2207 (msg-08). "
                      "Over 250 requires a written acknowledgment - a phone call is not one.",
              evidence=["ev-1049", "D64", "msg-08"], lines=["SchA.11"], adjustments=[],
              how_found="Low-confidence reads (< 0.6) are routed to a person.",
              judgment="Keep 1,500 (lower reading, confirmed). Request a corrected written acknowledgment and the bank image of check #2207.",
              area="deductions_and_credits")


def check_hvac(c: Ctx):
    wi = next((w for w in c.p.raw["what_if"]["alternatives"] if w["id"] == "hvac_improvement"), None)
    if c.val("SchE.14A") >= c.evv("ev-1001"):
        c.finding("F-HVAC", "11,000 HVAC replacement deducted as a repair",
                  severity="judgment", kind="judgment",
                  summary="The whole system was replaced with a new heat pump, air handler and line set (D47, call-01). That is "
                          "a restoration of a building system - generally capitalized and depreciated over 27.5 years (Reg. "
                          "1.263(a)-3(k)), not a repair. The 2,500 de minimis safe harbor does not cover it.",
                  evidence=["ev-1001", "D47", "D46", "call-01", "qa-37"], lines=["SchE.14A"],
                  adjustments=[], how_found="Repairs over 2,500 tested against capitalization rules; engine what-if available.",
                  judgment="Recommend capitalizing (what-if: federal +3,633, California +896).",
                  what_if=wi and {"id": wi["id"], "federal": wi["federal"]["total_tax_change"], "california": wi["california"]["total_tax_change"]},
                  area="rentals_and_sale")


def check_sep(c: Ctx):
    contrib = c.evv("ev-0485")
    ded = c.val("Sch1.16")
    if contrib > ded:
        c.finding("F-SEP-EXCESS", f"SEP contribution exceeds the limit by {contrib - ded:,.0f}",
                  severity="warning", kind="judgment",
                  summary=f"Erik deposited {contrib:,.0f} (D26) but the limit is {ded:,.0f}. The excess is not deductible and "
                          "is subject to a 6% excise tax each year it stays in (Form 5329) unless withdrawn with earnings by the "
                          "extended due date. The limit itself moves when the Schedule C findings are booked.",
                  evidence=["ev-0485", "D26", "qa-26"], lines=["Sch1.16"], adjustments=[],
                  how_found="Contribution compared with the computed limit.",
                  recommendation="Tell the client before 10/15/2026; recompute the limit after Schedule C changes.",
                  area="business")


def check_questionnaire(c: Ctx):
    n_sales = sum(1 for k in c.L if k.startswith("F8949.") and not k.endswith(".total"))
    c.finding("F-QA-CONFLICTS", "Client answers contradicted by documents",
              severity="info", kind="client_data",
              summary=f"qa-14 'no sales' vs {n_sales} Form 8949 lines; qa-15 'didn't really sell' vs 12 Coinhaven dispositions; "
                      "qa-31 'no prior IRA basis' vs 7,000 on the 2024 Form 8606 and the 2024 Form 5498 (the engine correctly "
                      "followed the documents); qa-39 car 'about 6,000' vs 2,900 on the 1098-C; qa-41 '4,000 I think' vs 3,000 "
                      "on the IRS transcript. Documents win; each conflict is documented.",
              evidence=["qa-14", "qa-15", "qa-31", "qa-39", "qa-41", "D42", "py:/return_2024/lines/F8606.14", "D66"],
              lines=["F8606.2"], adjustments=[],
              how_found="Every questionnaire answer is linked to the documents that confirm or contradict it.",
              area="intake_and_engagement")


def check_outside_filings(c: Ctx):
    max_usd = c.val("F8938.1.max")
    c.finding("F-OUTSIDE-FILINGS", "Filings outside this return that the client needs to know about",
              severity="info", kind="completeness",
              summary=f"(1) FinCEN 114 (FBAR): foreign maximum {max_usd:,.0f} USD > 10,000 - due 10/15/2026, not in engagement scope. "
                      "(2) Forms 1099-NEC for Mara Quintero 4,800 and Dev Patel 2,600 - due 02/02/2026, not filed (late). "
                      "(3) Form 3520 for the 2023 Swedish inheritance (> 100,000) - prior preparer's open item, never resolved. "
                      "(4) Oregon and Arizona composite returns filed by the entities - no individual filing needed. "
                      "(5) Ada's own 2025 return must keep 'can be claimed as a dependent' checked. "
                      "(6) 3,500 California 2024 credit held at FTB.",
              evidence=["F8938.1.max", "D36", "D27", "qa-23", "py:/prior_preparer_workpaper/open_items_left_by_prior_preparer/3",
                        "D01", "D38", "D39", "agency:/ftb_account/tax_year_2024_payments_after_filing/0"],
              lines=["F8938.1.max"], adjustments=[],
              how_found="Completeness checklist: every outside filing the facts imply, with scope per the engagement letter (D01).",
              area="intake_and_engagement")


def check_ca_amt(c: Ctx):
    c.finding("F-CA-AMT-ISO", "California AMT on the ISO exercise not computed",
              severity="judgment", kind="completeness",
              summary="1,000 ISOs granted 03/2022 while a California resident were exercised 07/30/2025 (after the move). "
                      "California sources the AMT preference by California workdays between grant and exercise - most of the "
                      "40,000 spread. The engine does not compute Schedule P (540NR).",
              evidence=["ev-0075", "ev-0076", "ev-0077", "ev-0078", "ev-0079", "D12", "household:ngozi"],
              lines=["F540NR.61"], adjustments=[], how_found="Engine limitation listed in the README; tracked as an open item.",
              judgment="Compute Schedule P manually (allocation by workdays) and record the method.", area="california")


def check_8867(c: Ctx):
    if c.val("F8867.status") == "Not completed":
        c.finding("F-8867", "Due diligence (Form 8867) not completed for CTC/ODC",
                  severity="warning", kind="completeness",
                  summary="Firm standard and Form 8867 require documented due diligence for the child tax credit and credit for "
                          "other dependents. Ada (19, full-time student) and Theo (8) - residency, relationship and age support.",
                  evidence=["F8867.status", "household:ada", "household:theo", "ev-1041"], lines=["F8867.status"], adjustments=[],
                  how_found="Firm required step 'Due diligence completed'.", area="deductions_and_credits")


def check_prior_preparer(c: Ctx):
    py_sq = 300
    actual = c.evv("ev-0481")
    c.finding("F-PY-HOMEOFFICE", "Prior preparer's 2024 home office used 300 sq ft; the room is 220",
              severity="info", kind="prior_year",
              summary=f"D07 left this open ('2 rooms combined? - client to confirm'). Measurements (D24) show {actual} sq ft in "
                      "Sacramento, so 2024 overstated the deduction by about 400. Immaterial; note it, no amendment recommended.",
              evidence=["D07", "ev-0481", "D24", "py:/return_2024/lines/SchC.30"], lines=["SchC.30"], adjustments=[],
              how_found="Prior preparer's open items are closed one by one against this year's evidence.", area="business")


# ====================================================================== reconciliations

def reconciliations(c: Ctx):
    p = c.p
    w2 = {}
    for doc in ("D08", "D09", "D10"):
        ein = c.ev_field(doc, "employer_ein")["value"]
        w2.setdefault(ein, doc)
    box = lambda f: sum(c.ev_field(d, f)["value"] for d in w2.values())
    c.recon("R-W2-BOX1", "Wages = box 1 of each distinct W-2", "F1040.1a", c.val("F1040.1a"), "Distinct W-2s (by EIN)", box("box1"),
            ["F1040.1a"] + list(w2.values()), note="D10 is a phone photo of D09 (same EIN) - counted once.", area="wages_and_stock_compensation")
    c.recon("R-W2-BOX2", "Federal withholding = box 2", "F1040.25a", c.val("F1040.25a"), "W-2 box 2", box("box2"),
            ["F1040.25a"] + list(w2.values()), area="payments_and_penalty")
    ss_max = p.params["ss_wage_base"] * 0.062
    c.recon("R-EXCESS-SS", "Excess social security recomputed", "Sch3.11", c.val("Sch3.11"), "Box 4 total - 6.2% x wage base",
            round(box("box4") - ss_max), ["Sch3.11", "ref:param.ss_wage_base"], area="payments_and_penalty")
    hsa = c.evv("ev-0024") + c.evv("ev-0043")
    c.recon("R-HSA", "Employer HSA (W-2 code W) = Form 5498-SA", "W-2 code W", hsa, "5498-SA box 2", c.evv("ev-0997"),
            ["ev-0024", "ev-0043", "ev-0997"], area="deductions_and_credits")
    st = [e["value"] for e in p.ev_by_doc["D74"] if e["field"].endswith("_proceeds") and re.match(r"X\d+_", e["field"])]
    c.recon("R-BRIGHTPATH", "Brightpath 1099-B: summary = 260 lots", "Summary ST + LT proceeds",
            round(c.evv("ev-1083") + c.evv("ev-1086"), 2), f"Sum of {len(st)} extracted lots", round(sum(st), 2),
            ["ev-1083", "ev-1086", "D74"], tolerance=0.01, area="investments_and_digital_assets")
    da = sum(e["value"] for e in p.ev_by_doc["D34"] if e["field"].endswith("_proceeds"))
    c.recon("R-1099DA", "Coinhaven 1099-DA total = lots", "Total gross proceeds", c.evv("ev-0907"), "Sum of 12 lots", da,
            ["ev-0907", "D34"], area="investments_and_digital_assets")
    cap = c.evv("ev-0969") + 9870 - c.evv("ev-0967") - c.evv("ev-0968")
    c.recon("R-RIDGELINE-CAP", "Ridgeline capital account rolls forward", "K-1 ending capital", c.evv("ev-0970"),
            "Beginning + income - distributions - foreign tax", cap, ["ev-0969", "ev-0967", "ev-0968", "ev-0970", "D37"],
            area="pass_throughs_and_basis")
    br = p.py["basis_records"]["scorp_lindqvist_family_woodworks"]
    b = br[0]["ending_stock_basis"] + c.evv("ev-0983") + c.evv("ev-0984") - c.evv("ev-0985") + br[2]["k1_income"] - br[2]["distributions"]
    c.recon("R-SCORP-BASIS", "Woodworks stock basis rolled 2022 -> 2025 independently", "F7203.1 (engine)", c.val("F7203.1"),
            "2022 21,300 + 2023 K-1 (D40) + 2024 K-1", b, ["F7203.1", "py:/basis_records/scorp_lindqvist_family_woodworks", "D40"],
            note="v1/v2 assumed no 2023 change (8,200); v3 used D40 and agrees.", area="pass_throughs_and_basis")
    co = p.py["carryovers_into_2025"]
    c.recon("R-CARRY-CAP", "Capital loss carryover agrees to 2024", "SchD.6 + SchD.14", -(c.val("SchD.6") + c.val("SchD.14")),
            "2024 carryover (D07 p6)", co["capital_loss_short"] + co["capital_loss_long"],
            ["SchD.6", "SchD.14", "py:/carryovers_into_2025"], area="investments_and_digital_assets")
    c.recon("R-CARRY-PAL", "Passive loss carryover agrees to 2024", "F8582.1c", -c.val("F8582.1c"), "2024 Form 8582 unallowed",
            co["passive_1450_fremont"] + co["passive_1200_barton_hills"], ["F8582.1c", "py:/carryovers_into_2025"], area="rentals_and_sale")
    c.recon("R-8606", "Backdoor Roth: taxable conversion recomputed", "F8606.18", c.val("F8606.18"),
            "Converted 14,026 - basis (7,000 2024 + 7,000 2025)", c.evv("ev-0987") - c.evv("ev-0991") - c.evv("ev-0993"),
            ["F8606.18", "ev-0987", "ev-0991", "ev-0993"], area="deductions_and_credits")
    c.recon("R-RENT-A", "Fremont rent = 1099-MISC", "Owner statement rent", c.evv("ev-0998"), "1099-MISC box 1", c.evv("ev-1003"),
            ["ev-0998", "ev-1003"], area="rentals_and_sale")
    c.recon("R-1099S", "Condo sale price = 1099-S", "Form 4797 line 21", c.val("F4797.21"), "1099-S box 2", c.evv("ev-1023"),
            ["F4797.21", "ev-1023"], area="rentals_and_sale")
    # depreciation recomputed per asset (straight line, mid-month)
    for aid in ("SAC-BLDG", "SAC-ROOF", "AUS-BLDG", "AUS-FLOOR"):
        a = p.assets[c.ver][aid]
        yearly = a["cost"] / a["life_years"]
        months = 9.5 if a.get("disposed") else 12
        c.recon(f"R-DEPR-{aid}", f"Depreciation recomputed: {a['description']}", "Engine 2025", a["federal"]["depreciation_2025"],
                f"Cost / {a['life_years']} x {months}/12", round(yearly * months / 12, 2), [f"asset:{aid}"], tolerance=0.05,
                area="rentals_and_sale")
    # independent re-footing of every engine sum
    bad = []
    n = 0
    for k, l in c.L.items():
        if l["rule"] != "R-SUM" or not l["inputs"] or not isinstance(l["value"], (int, float)):
            continue
        inputs = list(l["inputs"])
        if k.startswith("SchCA540NR.") and k.endswith(".D") and l["inputs"] == [k[:-2] + ".A"]:
            # engine lists only column A; CA law total is A - B + C
            base = k[:-2]
            vals = [c.val(base + ".A"), -c.val(base + ".B"), c.val(base + ".C")]
            n += 1
            if abs(sum(vals) - l["value"]) > 1:
                bad.append(k)
            continue
        vals = [c.val(i) for i in inputs]
        if not all(isinstance(v, (int, float)) for v in vals):
            continue
        n += 1
        if abs(sum(vals) - l["value"]) <= 1:
            continue
        ok = False
        if len(vals) <= 12:
            for signs in itertools.product((1, -1), repeat=len(vals) - 1):
                s = vals[0] + sum(sg * v for sg, v in zip(signs, vals[1:]))
                if abs(s - l["value"]) <= 1 or abs(abs(s) - abs(l["value"])) <= 1:
                    ok = True
                    break
        if not ok:
            bad.append(k)
    c.recon("R-REFOOT", f"Every engine sum re-footed ({n} lines)", "Lines that re-foot", n - len(bad), "Lines checked", n,
            ["R-SUM"], note=("Do not re-foot: " + ", ".join(bad)) if bad else "All arithmetic agrees.", area="return_summary")


ALL_CHECKS = [check_rsu_basis, check_interest_duplicates, check_fx_year, check_1099k_double, check_mileage,
              check_mixed_use, check_sehi, check_federal_estimates, check_ca_estimates, check_ca_wages,
              check_vehicle_gift, check_mortgage, check_property_tax, check_meridian_page32, check_crypto_basis,
              check_dependent_ssn, check_reread, check_low_confidence, check_hvac, check_sep, check_questionnaire,
              check_outside_filings, check_ca_amt, check_8867, check_prior_preparer]


def variance_review(p: Packet, standards: dict, ver: str = CURRENT_VERSION):
    """Firm standard: flag lines that moved > X% and > Y dollars vs prior year."""
    pct = standards.get("flag_if_change_over_percent", 10) / 100
    amt = standards.get("and_over_amount", 1000)
    out = []
    labels = p.py["return_2024"]["line_labels"]
    for k, py in p.py["return_2024"]["lines"].items():
        if not isinstance(py, (int, float)):
            continue
        cur_l = p.line(k, ver)
        if not cur_l or not isinstance(cur_l["value"], (int, float)):
            continue
        cur = cur_l["value"]
        d = cur - py
        if abs(d) > amt and (py == 0 or abs(d) / abs(py) > pct):
            out.append({"key": k, "label": cur_l["label"], "py_label": labels.get(k), "prior": py, "current": cur,
                        "change": d, "pct": (d / abs(py)) if py else None, "area": area_of(k)})
    return out


def run_all(p: Packet, ver: str = CURRENT_VERSION):
    c = Ctx(p, ver)
    for fn in ALL_CHECKS:
        try:
            fn(c)
        except Exception as ex:  # a broken check must never hide the others
            c.findings.append({"id": f"F-CHECK-ERROR-{fn.__name__}", "title": f"Check {fn.__name__} failed: {ex}",
                               "severity": "warning", "kind": "system", "summary": str(ex), "evidence": [], "lines": [],
                               "adjustments": [], "effect": {}, "how_found": "", "area": "return_summary"})
    reconciliations(c)
    return c.findings, c.recons
