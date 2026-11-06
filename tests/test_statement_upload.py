"""Adding a statement through a person's own app. A fake chat server; no network and no model."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kitty import access
from kitty import claims as C
from kitty import statement_import as SI
from kitty import statements as S
from kitty.agent import PersonalAgent, RulesBrain
from kitty.paths import for_trip
from kitty.person_app import create_app
from kitty.trips import write_json

GOOD = """txn_date,txn_time,narration,ref,debit,credit,balance,account
2026-10-01,09:00,SALARY NEFT CANARY-SALARY,N1,,95000.00,142000.00,XXXXXX4411
2026-10-16,08:12,COORG HOMESTAY CANARY-STAY,C1,18000.00,,96000.00,XXXXXX4411
2026-10-17,14:10,UPI/DR/BILAL K/3001/GPAY,U1,3000.00,,93000.00,XXXXXX4411
2026-10-17,18:10,UPI/CR/BILAL K/3002/GPAY,U2,,500.00,93500.00,XXXXXX4411
2026-10-18,23:00,TITOS BAR,C2,850.00,,92650.00,XXXXXX4411
2026-10-30,10:30,RENT CANARY-RENT,N2,28000.00,,64650.00,XXXXXX4411
"""
TRIP = {"trip_id": "coorg-1a2b", "name": "Coorg", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR", "people": {"asha": {"name": "Asha", "statement_name": "ASHA M"}, "bilal": {"name": "Bilal", "statement_name": "BILAL K"}}}
HEADERS = {"content-type": "text/csv", "x-filename": "mine.csv", "x-kitty-synthetic": "yes"}
PORT = 7301


class FakeChat:
    user_id = "me"

    def __init__(self) -> None:
        self.posts: dict[str, list[dict]] = {"pc": [], "dc": [], "gc": []}
        self.http = self

    def get(self, url: str, params=None):
        class R:
            status_code = 200

            def raise_for_status(self):
                return None

        r = R()
        if "/channels/" in url:
            channel = url.split("/channels/")[1].split("/")[0]
            data = self.posts[channel]
            r.json = lambda: {"order": [p["id"] for p in reversed(data)], "posts": {p["id"]: p for p in data}}
        else:
            r.json = lambda: {"first_name": "Someone"}
        return r

    def post(self, channel: str, message: str) -> dict:
        self.posts[channel].append({"id": str(sum(map(len, self.posts.values()))), "message": message, "user_id": "me", "create_at": 1})
        return {}


@pytest.fixture
def world(tmp_path):
    where = for_trip(tmp_path)
    for sub in (where.state, where.people, where.logs):
        sub.mkdir(parents=True)
    where.trip_json.write_text(json.dumps(TRIP))
    (where.people / "asha").mkdir()
    chat = FakeChat()
    config = {"mattermost": {"channels": {"private": "pc", "desk": "dc", "group": "gc"}}}
    invite = access.new_invite(where.access("asha"))
    app = create_app("asha", PORT, where, TRIP, config, lambda: chat)
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    assert client.get(f"/join?t={invite}", follow_redirects=False).status_code == 303
    return client, where, chat


def upload(client, body=GOOD, **headers):
    return client.post("/api/statement", content=body.encode() if isinstance(body, str) else body, headers={**HEADERS, **headers})


# ---- validation -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["priya", "arjun", "meera"])
def test_the_demos_own_statements_pass_and_parse_the_same(name, tmp_path):
    path = Path("data/synthetic") / f"{name}.csv"
    accepted = SI.validate(path.read_bytes())
    saved = tmp_path / "s.csv"
    saved.write_text(accepted.text)
    assert S.parse(saved) == S.parse(path)


def row(**over) -> str:
    base = {"txn_date": "2026-10-16", "txn_time": "10:00", "narration": "SHOP", "ref": "C1", "debit": "10.00", "credit": "", "balance": "", "account": "XXXXXX1234"}
    base.update(over)
    return ",".join(SI.HEADER) + "\n" + ",".join(base[c] for c in SI.HEADER) + "\n"


@pytest.mark.parametrize("over,code,column", [
    ({"txn_date": "16/10/2026"}, "bad_date", "txn_date"),
    ({"txn_date": "2026-02-30"}, "bad_date", "txn_date"),
    ({"txn_date": "1899-01-01"}, "bad_date", "txn_date"),
    ({"txn_time": "25:00"}, "bad_time", "txn_time"),
    ({"narration": ""}, "bad_narration", "narration"),
    ({"narration": "=CMD()"}, "bad_narration", "narration"),
    ({"narration": "<script>"}, "bad_narration", "narration"),
    ({"narration": "@channel hello"}, "bad_narration", "narration"),
    ({"narration": "`release c1`"}, "bad_narration", "narration"),
    ({"narration": "A" * 121}, "bad_narration", "narration"),
    ({"narration": "CARD 4111111111111111"}, "long_number", "narration"),
    ({"ref": "bad ref"}, "bad_ref", "ref"),
    ({"ref": "123456789012"}, "long_number", "ref"),
    ({"debit": '"1,000"'}, "bad_amount", "debit"),
    ({"debit": "-5"}, "bad_amount", "debit"),
    ({"debit": "1.234"}, "bad_amount", "debit"),
    ({"debit": "NaN"}, "bad_amount", "debit"),
    ({"debit": "0"}, "bad_amount", "debit"),
    ({"debit": "999999999"}, "bad_amount", "debit"),
    ({"debit": "", "credit": ""}, "no_amount", "debit"),
    ({"debit": "5", "credit": "5"}, "debit_and_credit", "credit"),
    ({"balance": "lots"}, "bad_amount", "balance"),
    ({"account": "123456781234"}, "unmasked_account", "account"),
    ({"account": ""}, "unmasked_account", "account"),
])
def test_bad_values_are_caught_with_line_and_column_and_never_echoed(over, code, column):
    with pytest.raises(SI.Rejected) as err:
        SI.validate(row(**over).encode())
    p = err.value.problems[0]
    assert p.code == code and p.column == column and p.line == 2
    for value in over.values():
        if len(value) > 2:
            assert value not in json.dumps(p.view())


@pytest.mark.parametrize("data,code", [
    (b"", "empty"),
    (b"   \n", "empty"),
    (b"PK\x03\x04zipfile", "not_csv"),
    (b"%PDF-1.7", "not_csv"),
    (b"a,b\x00,c", "not_csv"),
    (b"\xff\xfe\xfa\xfb,not,utf8", "not_utf8"),
    (b"date,amount\n2026-10-16,5\n", "bad_header"),
    (",".join(SI.HEADER).encode() + b"\n", "no_rows"),
    (",".join(SI.HEADER).encode() + b"\n1,2,3\n", "bad_columns"),
    (",".join(SI.HEADER).encode() + b'\n"unterminated,2\n', "bad_csv"),
])
def test_bad_files(data, code):
    with pytest.raises(SI.Rejected) as err:
        SI.validate(data)
    assert err.value.problems[0].code == code


def test_a_byte_order_mark_and_windows_line_endings_are_the_same_file():
    plain = SI.validate(GOOD.encode())
    windows = SI.validate(b"\xef\xbb\xbf" + GOOD.replace("\n", "\r\n").encode())
    assert plain.fingerprint == windows.fingerprint


def test_too_large_too_long_and_repeated_lines():
    with pytest.raises(SI.Rejected) as err:
        SI.validate(b"x" * (SI.MAX_BYTES + 1))
    assert err.value.problems[0].code == "too_large"
    many = ",".join(SI.HEADER) + "\n" + "".join(f"2026-10-16,10:00,SHOP,R{i},1.00,,,XXXXXX1234\n" for i in range(SI.MAX_ROWS + 1))
    with pytest.raises(SI.Rejected) as err:
        SI.validate(many.encode())
    assert err.value.problems[0].code == "too_many_rows"
    twice = row() + row().split("\n", 1)[1]
    with pytest.raises(SI.Rejected) as err:
        SI.validate(twice.encode())
    assert err.value.problems[0].code == "duplicate_line"


def test_many_errors_are_capped_with_the_true_total():
    body = ",".join(SI.HEADER) + "\n" + "".join(f"bad,10:00,SHOP,R{i},1.00,,,XXXXXX1234\n" for i in range(30))
    with pytest.raises(SI.Rejected) as err:
        SI.validate(body.encode())
    assert len(err.value.problems) == SI.MAX_SHOWN_ERRORS and err.value.total == 30


def test_a_transaction_time_of_a_reversal_shares_a_ref_with_its_debit():
    body = ",".join(SI.HEADER) + "\n2026-10-16,10:00,UPI/DR/ARJUN K/1/GPAY,U1,1500.00,,,XXXXXX1234\n2026-10-16,10:05,UPI/REV/ARJUN K/1/GPAY,U1,,1500.00,,XXXXXX1234\n"
    assert len(SI.validate(body.encode()).lines) == 2


# ---- the route ------------------------------------------------------------------------------------


def test_accepting_stores_only_validated_fields_and_answers_with_counts_only(world):
    client, where, _ = world
    messy = GOOD.replace("\n", "\r\n")
    r = upload(client, messy)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert r.json()["summary"] == {"lines": 6, "in_trip": 4, "outside_trip": 2, "spending": 2, "payments_to_friends": 1, "incoming": 1, "first": "2026-10-01", "last": "2026-10-30"}
    assert "CANARY" not in r.text and "COORG" not in r.text and "XXXXXX" not in r.text
    assert "\r" not in where.statement("asha").read_text()
    assert oct(where.statement("asha").stat().st_mode)[-3:] == "600"
    assert json.loads(where.person_status("asha").read_text()) == {"joined": True, "statement": True}


def test_the_same_file_again_is_noticed(world):
    client, where, _ = world
    upload(client)
    before = where.statement_meta("asha").read_text()
    r = upload(client, GOOD.replace("\n", "\r\n"))
    assert r.json()["status"] == "duplicate" and "already" in r.json()["message"]
    assert where.statement_meta("asha").read_text() == before


def test_before_a_review_a_new_file_replaces_the_old_one(world):
    client, where, _ = world
    upload(client)
    r = upload(client, GOOD.replace("TITOS BAR", "TITOS CLUB"))
    assert r.json()["status"] == "replaced"
    assert "TITOS CLUB" in where.statement("asha").read_text()


def test_after_a_review_starts_a_new_file_can_only_add_lines_and_rows_keep_their_ids(world):
    client, where, chat = world
    upload(client)
    before = S.parse(where.statement("asha"))
    chat.post("pc", "start")
    fewer = GOOD.replace("2026-10-18,23:00,TITOS BAR,C2,850.00,,92650.00,XXXXXX4411\n", "")
    r = upload(client, fewer)
    assert r.status_code == 422 and r.json()["problems"][0]["code"] == "lines_removed"
    # A later export, with the new line in the middle, and the file's order changed.
    later = GOOD.replace("2026-10-18,23:00,", "2026-10-18,20:00,UPI/DR/BILAL K/77/GPAY,U9,10.00,,,XXXXXX4411\n2026-10-18,23:00,")
    r = upload(client, later)
    assert r.json()["status"] == "added_lines" and r.json()["added"] == 1
    after = S.parse(where.statement("asha"))
    assert after[: len(before)] == before and len(after) == len(before) + 1
    assert upload(client, later).json()["status"] == "duplicate"
    assert client.delete("/api/statement").status_code == 409


def test_every_way_of_sending_the_wrong_thing(world):
    client, where, _ = world
    assert upload(client, **{"x-filename": "statement.xlsx"}).json()["problems"][0]["code"] == "not_csv_name"
    assert upload(client, **{"x-filename": ""}).status_code == 415
    assert upload(client, **{"x-kitty-synthetic": "no"}).json()["problems"][0]["code"] == "synthetic_required"
    assert upload(client, **{"content-type": "application/json"}).status_code == 415
    assert upload(client, b"x" * (SI.MAX_BYTES + 5)).status_code == 413
    assert not where.statement("asha").exists()


def test_errors_never_contain_file_content_in_the_reply_or_the_logs(world, caplog):
    client, _, _ = world
    caplog.set_level(logging.DEBUG)
    bad = GOOD.replace("18000.00", "CANARY-AMOUNT").replace("XXXXXX4411", "CANARY-ACCOUNT")
    r = upload(client, bad)
    assert r.status_code == 422 and r.json()["total"] >= 2
    assert "CANARY" not in r.text and "COORG" not in r.text
    upload(client)
    assert "CANARY" not in caplog.text and "COORG" not in caplog.text and "mine.csv" not in caplog.text


def test_only_the_invited_person_gets_in_and_the_link_works_once(tmp_path):
    where = for_trip(tmp_path)
    where.state.mkdir(parents=True)
    (where.people / "asha").mkdir(parents=True)
    invite = access.new_invite(where.access("asha"))
    app = create_app("asha", PORT, where, TRIP, {"mattermost": {"channels": {"private": "pc", "desk": "dc", "group": "gc"}}}, FakeChat)
    stranger = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    assert stranger.get("/api/feed").status_code == 401
    assert upload(stranger).status_code == 401
    assert stranger.get("/").status_code == 401 and "invite link" in stranger.get("/").text
    assert stranger.get("/healthz").status_code == 200
    assert stranger.get("/join?t=wrong").status_code == 403
    me = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    r = me.get(f"/join?t={invite}", follow_redirects=False)
    assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert invite not in r.headers["set-cookie"]
    assert me.get("/api/feed").status_code == 200
    latecomer = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    assert latecomer.get(f"/join?t={invite}").status_code == 403
    assert latecomer.get("/api/feed").status_code == 401
    access.new_invite(where.access("asha"))  # A new link ends the old session.
    assert me.get("/api/feed").status_code == 401


def test_requests_from_another_site_are_refused(world):
    client, where, _ = world
    assert upload(client, **{"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/say", json={"room": "private", "text": "hi"}, headers={"origin": "http://127.0.0.1:7302"}).status_code == 403
    assert client.get("/api/feed", headers={"host": "evil.example"}).status_code == 421
    assert not where.statement("asha").exists()


def test_a_person_cannot_reach_a_room_that_is_not_theirs(world):
    client, _, _ = world
    assert client.post("/api/say", json={"room": "bilal-private", "text": "x"}).status_code == 403


def test_the_original_demo_has_no_upload_route_in_use_and_no_login(tmp_path):
    where = for_trip(tmp_path)
    where = where.__class__(where.state, where.people, where.logs, where.trip_json, False)
    where.state.mkdir(parents=True)
    app = create_app("asha", PORT, where, TRIP, {"mattermost": {"channels": {"private": "pc", "desk": "dc", "group": "gc"}}}, FakeChat)
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    assert client.get("/api/feed").status_code == 200
    assert client.get("/api/feed").json()["statement"] is None
    assert upload(client).status_code == 404


# ---- what reaches the model ---------------------------------------------------------------------------


class RecordingBrain(RulesBrain):
    def __init__(self) -> None:
        self.seen: list[dict] = []

    def classify(self, rows, owner_name, trip_name):
        self.seen = rows
        return super().classify(rows, owner_name, trip_name)


def test_the_preview_is_exactly_what_the_model_gets(world):
    client, where, _ = world
    upload(client)
    preview = client.get("/api/statement/model-preview").json()["lines"]
    brain = RecordingBrain()
    trip = C.Trip(TRIP["trip_id"], __import__("datetime").date(2026, 10, 16), __import__("datetime").date(2026, 10, 19), ("asha", "bilal"))
    agent = PersonalAgent("asha", trip, {"asha": "Asha", "bilal": "Bilal"}, {"ASHA M": "asha", "BILAL K": "bilal"}, S.parse(where.statement("asha")), brain, "@h", "@c", "Coorg")
    agent.start()
    assert brain.seen == preview
    blob = json.dumps(preview)
    assert len(preview) == 2
    for absent in ("SALARY", "RENT", "UPI/", "92650", "XXXXXX", "C1", "U1"):
        assert absent not in blob, absent


def test_upload_never_calls_a_model(world, monkeypatch):
    import kitty.llm

    def explode(*a, **k):
        raise AssertionError("a model was called during upload")

    monkeypatch.setattr(kitty.llm, "complete", explode)
    monkeypatch.setattr("subprocess.run", explode)
    client, _, _ = world
    assert upload(client).status_code == 200
    assert client.get("/api/statement/model-preview").status_code == 200


def test_inr_prompt_is_unchanged_and_other_currencies_are_labelled():
    r = S.Row("r1", __import__("datetime").date(2026, 1, 1), "10:00", "SHOP 123456", "X", 12345, 0)
    assert S.model_view(r) == {"row_id": "r1", "date": "2026-01-01", "time": "10:00", "narration": "SHOP ####", "amount_rupees": 123.45}
    assert "amount_usd" in S.model_view(r, "USD")


def test_sample_file_is_valid_and_in_trip():
    from datetime import date

    text = SI.sample(date(2026, 10, 16), date(2026, 10, 19))
    accepted = SI.validate(text.encode())
    trip = C.Trip("t", date(2026, 10, 16), date(2026, 10, 19), ("a", "b"))
    assert SI.summary(accepted, trip, {})["in_trip"] == 3
