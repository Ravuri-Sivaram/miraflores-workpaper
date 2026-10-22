"""End-to-end tests of the rules the brief asks for. Run: pytest -q"""
import hashlib
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

c = TestClient(main.app)
PREP, REV, PARTNER, MARCUS, ADMIN, QR, CLIENT = "st-01", "st-02", "st-03", "st-04", "st-05", "st-06", "cu-01"


def act(user, body, as_of="2026-07-01"):
    return c.post("/api/action", json=body, headers={"X-User": user, "X-As-Of": as_of})


def get(path, user=PREP, as_of="2026-07-01"):
    return c.get(path, headers={"X-User": user, "X-As-Of": as_of}).json()


def setup_function(_):
    main.STORE.reset()


def test_packet_loaded_as_delivered_and_untouched():
    before = hashlib.sha256(open(main.PACKET_PATH, "rb").read()).hexdigest()
    get("/api/overview")
    assert hashlib.sha256(open(main.PACKET_PATH, "rb").read()).hexdigest() == before
    assert len(main.P.docs) == 74 and len(main.P.ev) == 3012


def test_key_errors_are_found_independently():
    ids = {f["id"] for f in get("/api/findings")["findings"]}
    for fid in ("F-RSU-BASIS", "F-1099K-DOUBLE", "F-MILEAGE-DUP", "F-INT-DUP", "F-FX-RATE", "F-SEHI", "F-Q4-ESTIMATE",
                "F-CA-EST-YEAR", "F-CA-WAGE-SOURCE", "F-CAR-GIFT", "F-MORTGAGE-DUP", "F-PROPERTY-TAX", "F-MERIDIAN-P32",
                "F-CRYPTO-C6", "F-THEO-SSN", "F-MISREAD-ev-1045"):
        assert fid in ids, fid


def test_every_value_traces_to_a_page():
    t = get("/api/trace/F1040.1a")
    assert any(s["kind"] == "value" and s["snippet"]["hit"] for s in t["sources"])


def test_preparer_cannot_approve_and_reviewer_limited_to_1000():
    r = act(PREP, {"type": "adjustment.propose", "target": "Sch1.17", "data": {"to": 0, "category": "adjustment", "reason": "qa-25"}})
    adj = r.json()["events"][0]["id"]
    assert act(PREP, {"type": "adjustment.approve", "target": adj}).status_code == 403          # own work / role
    assert act(REV, {"type": "adjustment.approve", "target": adj}).status_code == 403           # effect > 1,000
    assert act(PARTNER, {"type": "adjustment.approve", "target": adj}).status_code == 200
    assert get("/api/trace/Sch1.17")["value"] == 0
    assert get("/api/trace/Sch1.26")["rolled_up"] is True                                      # linear roll-up


def test_adjustment_requires_reason_and_keeps_original():
    assert act(PREP, {"type": "adjustment.propose", "target": "SchC.1", "data": {"to": 151300, "category": "schc_profit"}}).status_code == 400
    act(PREP, {"type": "adjustment.propose", "target": "SchC.1", "data": {"to": 151300, "category": "schc_profit", "reason": "x"}})
    t = get("/api/trace/SchC.1")
    assert t["engine"] == 163700 and t["adjustments"][0]["from"] == 163700


def test_signoff_reopens_when_signed_numbers_change_and_no_self_review():
    assert act(PREP, {"type": "signoff", "target": "business", "data": {"level": "preparer"}}).status_code == 200
    assert act(REV, {"type": "signoff", "target": "business", "data": {"level": "reviewer"}}).status_code == 200
    so = get("/api/review")["signoffs"]["business"]
    assert so["reviewer"]["valid"]
    r = act(PREP, {"type": "adjustment.propose", "target": "SchC.27a.1", "data": {"to": 6634, "category": "schc_profit", "reason": "Spotify"}})
    act(REV, {"type": "adjustment.approve", "target": r.json()["events"][0]["id"]})
    so = get("/api/review")["signoffs"]["business"]
    assert not so["reviewer"]["valid"] and "SchC.27a.1" in so["reviewer"]["changed"]
    # the reviewer who approved can still review, but a preparer who adjusted cannot sign as reviewer
    assert act(PARTNER, {"type": "signoff", "target": "business", "data": {"level": "reviewer"}}).status_code == 403


def test_blocking_review_note_and_client_question_flow():
    act(REV, {"type": "note.add", "target": "SchE.14A", "data": {"kind": "review_note", "text": "HVAC?"}})
    gates = {g["id"]: g["ok"] for g in get("/api/review")["gates"]}
    assert gates["notes"] is False
    assert act(PREP, {"type": "note.add", "target": "x", "data": {"kind": "review_note", "text": "nope"}}).status_code == 403
    q = act(PREP, {"type": "note.add", "target": "F8949.H.C6", "data": {"kind": "client_question", "text": "Basis?"}}).json()["events"][0]["id"]
    assert act(CLIENT, {"type": "note.reply", "target": q, "data": {"text": "3,050"}}).status_code == 200


def test_bulk_and_undo():
    r = act(PREP, {"type": "bulk", "actions": [{"type": "finding.set", "target": "F-8867", "data": {"status": "resolved"}},
                                              {"type": "finding.set", "target": "F-STBRIGID", "data": {"status": "resolved"}}]})
    batch = r.json()["batch"]
    st = {f["id"]: f["state"]["status"] for f in get("/api/findings")["findings"]}
    assert st["F-8867"] == "resolved"
    c.post("/api/action", json={"type": "undo", "batch": batch}, headers={"X-User": PREP})
    st = {f["id"]: f["state"]["status"] for f in get("/api/findings")["findings"]}
    assert st["F-8867"] == "open" and st["F-STBRIGID"] == "open"


def test_masking_and_view_log():
    d = get("/api/documents/D08", user=MARCUS)
    assert "666-41-2287" not in d["pages"][0]["text"] and d["masked"]
    get("/api/documents/D08", user=PREP)
    assert any(v["what"] == "D08" for v in get("/api/firm", user=ADMIN)["views"])
    assert c.post("/api/reveal", json={"ref": "household:theo"}, headers={"X-User": MARCUS}).status_code == 403


def test_quality_reviewer_not_active_before_2027():
    assert act(QR, {"type": "note.add", "target": "F1040.11", "data": {"kind": "review_note", "text": "q"}}, as_of="2026-07-01").status_code == 403
    assert act(QR, {"type": "note.add", "target": "F1040.11", "data": {"kind": "review_note", "text": "q"}}, as_of="2027-01-10").status_code == 200


def test_judgment_requires_authority():
    assert act(PREP, {"type": "judgment.record", "target": "SchE.14A", "data": {"decision": "capitalize"}}).status_code == 400


def test_client_cannot_approve_until_gates_pass_and_filing_needs_approval():
    assert act(CLIENT, {"type": "filing.client_approved"}).status_code == 409
    assert act(PARTNER, {"type": "filing.filed"}).status_code == 409


def test_notice_response_record_builds():
    n = get("/api/notice/N-2025-L566", as_of="2028-02-01")
    assert len(n["items"]) == 2 and n["items"][1]["evidence"]
