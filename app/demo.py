"""Optional demo activity so a reviewer opening the link sees a workpaper mid-review.

It replays a plausible week: the preparer agrees documents and proposes fixes for the
findings, the reviewer approves the small ones and raises a blocking note, a client
question is answered. Several items are left open on purpose (partner approvals,
sign-offs) so the filing gate shows what still blocks.
"""
from .checks import Ctx


def seed_demo(store, p, findings):
    c = Ctx(p)
    F = {f["id"]: f for f in findings}
    n = 0

    def add(actor, type_, target=None, data=None, batch=None):
        nonlocal n
        n += 1
        return store.append(actor, type_, target, data or {}, batch)

    for doc, how in (("D04", "Agreed name, DOB to organizer"), ("D05", "Agreed name, DOB to organizer"),
                     ("D06", "2024 return reviewed; carryovers agreed to D07 p6")):
        add("st-01", "verify", doc, {"method": "agreed_to_source_document", "note": how})
    add("st-01", "verify", "F1040.1a", {"method": "recalculated", "note": "Box 1 of D08 + D09; D10 is a duplicate image"})
    add("st-01", "verify", "F7203.1", {"method": "recalculated", "note": "Rolled 2022 basis with D40 (2023) and 2024 K-1", "evidence": ["D40"]})
    add("st-01", "verify", "SchE.3A", {"method": "agreed_to_third_party_record", "note": "Owner statement = 1099-MISC"})

    adj_ids = {}
    for fid in ("F-RSU-BASIS", "F-1099K-DOUBLE", "F-MILEAGE-DUP", "F-MIXED-USE", "F-PERSONAL-SOFTWARE", "F-SEHI",
                "F-INT-DUP", "F-FX-RATE", "F-Q4-ESTIMATE", "F-CA-EST-YEAR", "F-CA-WAGE-SOURCE", "F-CAR-GIFT",
                "F-MORTGAGE-DUP", "F-PROPERTY-TAX"):
        f = F[fid]
        for a in f["adjustments"]:
            eff = c.effect([a]) if a["category"] != "none" else {"federal_tax": 0, "california_tax": 0, "federal_balance": 0, "california_balance": 0}
            e = add("st-01", "adjustment.propose", a["line"], {"from": a["from"], "to": a["to"], "category": a["category"],
                                                              "rates": a.get("rates"), "reason": f"{f['title']}: {a.get('note', '')}",
                                                              "finding": fid, "effect": eff})
            adj_ids.setdefault(fid, []).append((e["id"], eff))
    # reviewer approves what is within his authority
    for fid, lst in adj_ids.items():
        for aid, eff in lst:
            big = max(abs(v) for v in eff.values()) > 1000
            if not big and fid not in ("F-CA-WAGE-SOURCE",):
                add("st-02", "adjustment.approve", aid, {"note": "Agreed to evidence"})
    # partner approves two of the big ones; the rest wait for her
    for fid in ("F-RSU-BASIS", "F-SEHI"):
        for aid, eff in adj_ids[fid]:
            add("st-03", "adjustment.approve", aid, {"note": "Reviewed with D11 / qa-25"})

    add("st-01", "judgment.record", "SchA.11", {"topic": "St. Brigid gift amount", "decision": "Use 1,500 (lower reading; client confirmed; matches check #2207). Written acknowledgment requested.",
                                                 "authority": "IRC 170(f)(8) contemporaneous written acknowledgment; Pub. 526"})
    add("st-01", "finding.set", "F-STBRIGID", {"status": "resolved", "note": "Judgment recorded; acknowledgment requested from parish"})
    add("st-01", "judgment.record", "SchE.14A", {"topic": "HVAC replacement", "decision": "Capitalize as improvement (27.5-year), per what-if hvac_improvement",
                                                  "authority": "Treas. Reg. 1.263(a)-3(k)(1)(vi) restoration; building-system unit of property 1.263(a)-3(e)(2)(ii)"})
    q = add("st-01", "note.add", "F8949.H.C6", {"kind": "client_question", "text": "Could you upload the bank statement page showing the 3,050 payment to CryptoNorth on 03/15/2021? We will use it as proof of the Bitcoin's cost.", "to": "cu-02"})
    add("cu-02", "note.reply", q["id"], {"text": "Uploaded - it's the March 2021 statement, line 14."})
    rn = add("st-02", "note.add", "SchE.14A", {"kind": "review_note", "blocking": True,
                                               "text": "HVAC: judgment says capitalize, but SchE.14A still includes the 11,000. Propose the adjustment and add the depreciation line before I sign rentals."})
    add("st-02", "note.add", "F540NR.61", {"kind": "review_note", "blocking": True,
                                          "text": "Need the Schedule P (540NR) ISO allocation by California workdays before partner review."})
    for area, h in (("wages_and_stock_compensation", 2.5), ("investments_and_digital_assets", 4.5), ("business", 3.0),
                    ("rentals_and_sale", 2.0), ("california", 2.0), ("intake_and_engagement", 1.0)):
        add("st-01", "time.log", area, {"hours": h, "note": "Preparation"})
    add("st-02", "time.log", "business", {"hours": 1.0, "note": "Review"})
    add("st-02", "assign", "california", {"user": "st-04", "due": "2026-07-03"})
    return n
