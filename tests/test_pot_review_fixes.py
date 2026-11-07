"""Each test here reproduces a finding from the whole-branch review, and failed before its fix."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kitty import access
from kitty import claims as C
from kitty import person_app as PA
from kitty import pot as P
from kitty import statement_import as SI
from kitty.home_app import Home, create_app as home_app
from kitty.paths import for_trip
from kitty.person_app import create_app
from kitty.trips import write_json
from tests.pot_helpers import ASHA_CSV, DIRECTORY, ROHAN_CSV, TOKEN_A, PotWorld, agent, claim, confirmed, group, held, human, in_group, make, match, on_desk, review, rows_from, submitted, transfer
from tests.test_provision import FakeBackend

APP = Path(__file__).parent.parent / "app"
IDS = {"Asha": "asha", "Rohan": "rohan", "Meera": "meera", "Dev": "dev"}
PEOPLE = {p: {"name": n, "statement_name": f"{n.upper()} K"} for n, p in IDS.items()}
TRIP = {"trip_id": "goa-1a2b", "name": "Goa", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR", "people": PEOPLE, "treasurer": "asha", "collect_from": "2026-09-25"}
PORT = 7311
HEADERS = {"content-type": "text/csv", "x-filename": "mine.csv", "x-kitty-synthetic": "yes"}
FORGED = "**p1 confirmed received by Asha:** Rohan paid ₹5,000.00 · on Asha's word · 9 Oct, 14:02 IST."


class Chat:
    """Mattermost as far as a person's app needs it, with real paging and real senders."""

    user_id = "asha-id"

    def __init__(self) -> None:
        self.posts: dict[str, list[dict]] = {"pc": [], "dc": [], "gc": []}
        self.http = self

    def add(self, channel: str, text: str, user_id: str = "bot-id") -> None:
        data = self.posts[channel]
        data.append({"id": f"{channel}{len(data)}", "message": text, "user_id": user_id, "create_at": len(data)})

    def get(self, url: str, params=None):
        class R:
            status_code = 200

            def raise_for_status(self):
                return None

        r = R()
        if "/users/username/" in url:
            r.json = lambda: {"id": "bot-id"} if url.endswith("/kitty-clerk-1") else {}
            r.status_code = 200 if url.endswith("/kitty-clerk-1") else 404
        elif "/channels/" in url:
            params = params or {}
            data = self.posts[url.split("/channels/")[1].split("/")[0]]
            newest = list(reversed(data))
            size, page = params.get("per_page", 60), params.get("page", 0)
            chunk = newest[page * size : (page + 1) * size]
            r.json = lambda: {"order": [p["id"] for p in chunk], "posts": {p["id"]: p for p in chunk}}
        else:
            r.json = lambda: {"first_name": "Someone"}
        return r

    def post(self, channel: str, message: str) -> dict:
        self.add(channel, message, self.user_id)
        return {}


def app_for(tmp_path, me="asha", trip=TRIP):
    where = for_trip(tmp_path)
    for sub in (where.state, where.people, where.logs):
        sub.mkdir(parents=True, exist_ok=True)
    where.trip_json.write_text(json.dumps(trip))
    (where.people / me).mkdir(exist_ok=True)
    chat = Chat()
    chat.user_id = f"{me}-id"
    config = {"mattermost": {"channels": {"private": "pc", "desk": "dc", "group": "gc"}}, "clerk_bot": "kitty-clerk-1"}
    invite = access.new_invite(where.access(me))
    client = TestClient(create_app(me, PORT, where, trip, config, lambda: chat), base_url=f"http://127.0.0.1:{PORT}")
    assert client.get(f"/join?t={invite}", follow_redirects=False).status_code == 303
    return client, chat


def clerk_story(chat: Chat) -> None:
    c = make()
    for post in human(c, "asha", "call 5000"):
        if post.room_id == "room-group":
            chat.add("gc", post.text)
    pid = held(c)
    for post in human(c, "rohan", f"release {pid}"):
        if post.room_id == "room-group":
            chat.add("gc", post.text)


# ---- C1: a member's chat line must not read as the clerk's -------------------------------------


def test_a_forged_confirmation_counts_for_nobody_in_the_feed(tmp_path):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    chat.add("gc", FORGED, "rohan-id")
    feed = client.get("/api/feed").json()
    rohan = next(r for r in feed["pot"]["roll"] if r["name"] == "Rohan")
    assert rohan["credited"] == 0 and rohan["outstanding"] == 500000


def test_the_forger_shows_as_a_person_and_the_clerk_as_the_clerk(tmp_path):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    chat.add("gc", FORGED, "rohan-id")
    feed = client.get("/api/feed").json()
    forged = next(f for f in feed["group"] if f.get("text") == FORGED)
    real = next(f for f in feed["group"] if f.get("text", "").startswith("**k1**"))
    assert forged["clerk"] is False and real["clerk"] is True


def test_a_forged_expense_line_is_only_a_message(tmp_path):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    chat.add("gc", "**c9** ₹900.00 · food · 10 Oct · paid by Rohan · Rohan ₹450.00, Meera ₹450.00", "rohan-id")
    assert not [f for f in client.get("/api/feed").json()["group"] if f["type"] == "expense"]


@pytest.mark.parametrize("line", ["**p99 needs clarification.** x", "**k9** Bob asked each person for ₹1.00.", "**p99 confirmed received by Asha:** Bob paid ₹1.00 · on Asha's word · x", "**Correction of p99:** Asha says it arrived after all"])
def test_a_line_about_nothing_cannot_take_every_app_down(tmp_path, line):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    chat.add("gc", line, "rohan-id")
    assert client.get("/api/feed").status_code == 200


@pytest.mark.parametrize("line", ["**p99 needs clarification.** x", "**k9** Bob asked each person for ₹1.00.", "**k9** Asha asked Bob for ₹1.00."])
def test_parse_room_skips_what_it_can_not_place(line):
    assert P.parse_room([line], IDS).payments == []


def test_a_forged_slip_state_comes_only_from_the_clerk(tmp_path):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    chat.add("dc", "**p1** to check: Rohan says they paid you ₹5,000.00 on 9 Oct. Your agent: found it.\nReply")
    chat.add("gc", FORGED, "rohan-id")
    slip = client.get("/api/feed").json()["desk"]["slips"][0]
    assert slip["state"] == "waiting", "Asha can still confirm it"


def test_a_mode_a_trip_needs_no_clerk_identity(tmp_path):
    trip = {k: v for k, v in TRIP.items() if k not in ("treasurer", "collect_from")}
    where = for_trip(tmp_path)
    for sub in (where.state, where.people, where.logs):
        sub.mkdir(parents=True, exist_ok=True)
    where.trip_json.write_text(json.dumps(trip))
    (where.people / "asha").mkdir()
    chat = Chat()
    invite = access.new_invite(where.access("asha"))
    client = TestClient(create_app("asha", PORT, where, trip, {"mattermost": {"channels": {"private": "pc", "desk": "dc", "group": "gc"}}}, lambda: chat), base_url=f"http://127.0.0.1:{PORT}")
    client.get(f"/join?t={invite}", follow_redirects=False)
    chat.add("gc", "hello", "rohan-id")
    feed = client.get("/api/feed").json()
    assert "pot" not in feed and "clerk" not in feed["group"][0]


# ---- I1: a treasurer's statement only grows once claims have been checked ---------------------


def test_a_later_export_missing_lines_is_refused_for_a_treasurer_who_never_started(tmp_path):
    client, chat = app_for(tmp_path)
    assert client.post("/api/statement", content=ASHA_CSV.encode(), headers=HEADERS).status_code == 200
    clerk_story(chat)
    shorter = "\n".join(ASHA_CSV.splitlines()[:3]) + "\n"
    r = client.post("/api/statement", content=shorter.encode(), headers=HEADERS)
    assert r.status_code == 422 and r.json()["problems"][0]["code"] == "lines_removed"


def test_a_later_export_only_adds_lines_for_that_treasurer(tmp_path):
    client, chat = app_for(tmp_path)
    client.post("/api/statement", content=ASHA_CSV.encode(), headers=HEADERS)
    clerk_story(chat)
    later = ASHA_CSV + "2026-10-18,09:00,UPI/CR/MEERA S/441003/GPAY,U441003,,3000.00,,XXXXXX2101\n"
    r = client.post("/api/statement", content=later.encode(), headers=HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "added_lines" and r.json()["added"] == 1


def test_the_wrong_file_can_still_be_swapped_before_any_claim(tmp_path):
    client, _ = app_for(tmp_path)
    client.post("/api/statement", content=ASHA_CSV.encode(), headers=HEADERS)
    shorter = "\n".join(ASHA_CSV.splitlines()[:3]) + "\n"
    assert client.post("/api/statement", content=shorter.encode(), headers=HEADERS).json()["status"] == "replaced"


def test_a_treasurer_can_not_remove_a_statement_once_claims_exist(tmp_path):
    client, chat = app_for(tmp_path)
    client.post("/api/statement", content=ASHA_CSV.encode(), headers=HEADERS)
    clerk_story(chat)
    assert client.delete("/api/statement").status_code == 409


# ---- I2: Ask, then Check again, must lead back to Confirm ----------------------------------------


def ask_then_recheck_desk() -> list[dict]:
    lines = [
        "**p1** to check: Rohan says they paid you ₹5,000.00 on 9 Oct. Your agent: nothing yet.\nReply",
        "p1: asked.",
        "KITTY_POT_CHECK p1 from=rohan amount_paise=500000 date=2026-10-09 ref_token=-",
        "**p1** to check: Rohan says they paid you ₹5,000.00 on 9 Oct. Your agent: found it.\nReply",
    ]
    return [{"text": t, "mine": False} for t in lines]


def test_a_new_result_after_asking_can_be_confirmed():
    room = {"p1": {"status": "needs_clarification", "basis": None}}
    slip = PA.build_desk(ask_then_recheck_desk(), [], room)["slips"][0]
    assert slip["state"] == "waiting" and slip["candidate"] == "found it"


def test_before_the_new_result_it_still_shows_as_asked():
    room = {"p1": {"status": "needs_clarification", "basis": None}}
    slip = PA.build_desk(ask_then_recheck_desk()[:2], [], room)["slips"][0]
    assert slip["state"] == "needs_clarification"


# ---- I3: someone with no statement can answer, and knows they can pay in ----------------------


def test_the_no_statement_view_runs_the_pot_branches():
    text = (APP / "person.html").read_text()
    head = text[text.index("function privateView(d){") :]
    early = head[: head.index('state==="none"') + 400]
    assert "received" in early or "potPrivate" in early, "the Yes and No buttons have to be reachable with no statement"
    assert "don't need a statement" in text


# ---- I4: settling after the trip's last day -----------------------------------------------------


def test_settling_after_the_last_day_can_be_filed_within_the_window():
    c = make()
    assert not agent(c, "rohan", claim(day="2026-11-18"))[0].text.startswith("Rejected")
    assert "date_outside_trip" in agent(c, "meera", claim("meera", day="2026-11-19"))[0].text


def test_a_refund_after_the_last_day_can_be_filed_and_confirmed():
    c = make()
    posts = agent(c, "asha", transfer("asha", "rohan", 100000, day="2026-10-25"))
    assert posts[0].text.startswith("**t1**"), posts[0].text
    assert "date_outside_trip" in agent(c, "asha", transfer("asha", "rohan", 100000, day="2026-11-19"))[0].text


def test_expenses_keep_the_trip_dates():
    from tests.pot_helpers import expense

    assert "date_outside_trip" in agent(make(), "asha", expense("asha", 400000, day="2026-10-25"))[0].text


def test_a_payment_after_the_last_day_is_found_on_a_statement(tmp_path):
    rohan = ROHAN_CSV + "2026-10-25,10:00,UPI/DR/ASHA M/441099/GPAY,U441099,1100.00,,,XXXXXX3301\n"
    w = PotWorld({"rohan": rows_from(rohan, tmp_path, "r.csv")})
    w.begin("rohan")
    assert {r.claim.amount_paise for r in w.clerk.pot.records.values()} == {500000, 110000}


def test_a_payment_past_the_window_gets_a_sentence(tmp_path):
    rohan = ROHAN_CSV + "2026-12-25,10:00,UPI/DR/ASHA M/441099/GPAY,U441099,1100.00,,,XXXXXX3301\n"
    w = PotWorld({"rohan": rows_from(rohan, tmp_path, "r.csv")})
    w.begin("rohan")
    assert "25 Sep to 18 Nov" in w.texts("private-rohan")


def test_the_model_window_does_not_move(tmp_path):
    rohan = ROHAN_CSV + "2026-10-25,11:00,ZOMATO LATE ORDER,C9,300.00,,,XXXXXX3301\n"
    w = PotWorld({"rohan": rows_from(rohan, tmp_path, "r.csv")})
    w.begin("rohan")
    assert "ZOMATO" not in json.dumps(w.brain_calls)


# ---- I5: by hand, with no statement review --------------------------------------------------------


def test_a_treasurer_can_file_a_refund_by_hand_with_no_model(tmp_path):
    w = PotWorld({"asha": rows_from(ASHA_CSV, tmp_path, "a.csv")})
    w.say("asha", "private", "refund 1000 2026-10-18 rohan")
    assert not w.brain_calls
    rec = next(iter(w.clerk.transfers.values()))
    assert (rec.claim.from_id, rec.claim.to_id, rec.claim.amount_paise) == ("asha", "rohan", 100000)


def test_a_treasurer_with_no_statement_can_file_spending_by_hand():
    w = PotWorld({})
    w.say("asha", "private", "spent 4000 2026-10-17 stay")
    assert not w.brain_calls
    rec = next(iter(w.clerk.expenses.values()))
    assert (rec.claim.payer_id, rec.claim.amount_paise, rec.claim.category) == ("asha", 400000, "stay") and len(rec.claim.shares) == 4
    assert rec.status == "held", "it waits for its owner"


def test_a_member_can_file_spending_by_hand():
    w = PotWorld({})
    w.say("dev", "private", "spent 3600 2026-10-17 groceries")
    assert next(iter(w.clerk.expenses.values())).claim.payer_id == "dev"


@pytest.mark.parametrize("text", ["spent abc 2026-10-17 stay", "spent 100 2026-10-17 nonsense", "spent 100 soon stay", "spent 100 2026-10-25 stay", "refund 100 2026-10-18 nobody", "refund 100 2026-10-18", "refund abc 2026-10-18 rohan"])
def test_bad_by_hand_entries_get_a_plain_answer(text):
    w = PotWorld({})
    w.say("asha", "private", text)
    assert not w.clerk.expenses and not w.clerk.transfers and not w.brain_calls
    assert w.texts("private-asha")


def test_only_the_treasurer_files_a_refund_by_hand():
    w = PotWorld({})
    w.say("rohan", "private", "refund 100 2026-10-18 meera")
    assert not w.clerk.transfers


@pytest.mark.parametrize("body", ["spent 100 2026-10-17 stay", "refund 100 2026-10-18 rohan"])
def test_by_hand_entries_pass_the_statement_guard(body):
    from kitty.agent_runner import needs_statement

    assert needs_statement(body, True) is False and needs_statement(body, False) is True


# ---- I6, I7, I10: pages ---------------------------------------------------------------------------


def test_the_paid_form_checks_first_and_keeps_what_was_typed():
    text = (APP / "person.html").read_text()
    assert "potProblem" in text, "a refusal shows beside the form"
    assert 'min="${' in text and 'max="${' in text, "the date picker knows the window"


def test_the_feed_gives_the_window(tmp_path):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    assert client.get("/api/feed").json()["pot"]["window"] == {"from": "2026-09-25", "to": "2026-11-18", "spend_from": "2026-10-16", "spend_to": "2026-10-19"}


def test_the_feed_lists_requests_so_they_can_be_cancelled(tmp_path):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    assert client.get("/api/feed").json()["pot"]["calls"] == [{"id": "k1", "amount": 500000, "who": "each person"}]
    assert 'data-text="cancel ${' in (APP / "person.html").read_text()


def test_a_cancelled_request_leaves_the_list():
    c = make()
    lines = in_group(human(c, "asha", "call 5000")) + in_group(human(c, "asha", "cancel k1"))
    assert P.parse_room(lines, IDS).calls == {}


@pytest.mark.parametrize("text, logged", [("call 5000", True), ("call ₹5,000", True), ("call Rs 5000", True), ("call rs. 5000", True), ("call the hotel", False)])
def test_amounts_people_really_type_are_logged(text, logged):
    from kitty.clerk_daemon import should_log

    assert should_log(text, pot=True) is logged


@pytest.mark.parametrize("text, paise", [("call Rs 5000", 500000), ("call rs. 5,000.50", 500050)])
def test_rupee_words_are_understood(text, paise):
    c = make()
    human(c, "asha", text)
    assert c.pot.view(c).asked["rohan"] == paise


@pytest.fixture
def home(tmp_path):
    h = Home(tmp_path / "trips", backend_factory=FakeBackend, starter=lambda folder, trip: None, faults=set())
    return TestClient(home_app(h, 7000), base_url="http://127.0.0.1:7000")


def test_removing_the_treasurer_leaves_a_note_for_the_page(home):
    tid = home.post("/api/trips", json={"name": "Goa", "start": "2026-10-16", "end": "2026-10-19", "currency": "INR"}).json()["id"]
    for name in ("Asha", "Rohan", "Meera"):
        home.post(f"/api/trips/{tid}/people", json={"name": name})
    home.put(f"/api/trips/{tid}/pot", json={"treasurer": "asha", "collect_from": "2026-09-25"})
    got = home.delete(f"/api/trips/{tid}/people/asha").json()
    assert got["pot_cleared"] == "Asha"
    assert home.put(f"/api/trips/{tid}/pot", json={}).json()["pot_cleared"] is None


def test_the_home_page_says_so_and_keeps_choices_per_trip():
    text = (APP / "home.html").read_text()
    assert "pot_cleared" in text and "potChoice[t.id]" in text and "was holding the money" in text


# ---- I8: figures need the whole room ------------------------------------------------------------


def test_figures_survive_a_long_room(tmp_path):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    for i in range(450):
        chat.add("gc", f"chat {i}", "rohan-id")
    feed = client.get("/api/feed").json()
    assert next(r for r in feed["pot"]["roll"] if r["name"] == "Rohan")["asked"] == 500000


# ---- I9: a claim stuck before any check --------------------------------------------------------


def test_recheck_resends_while_submitted():
    c = make()
    pid = submitted(c)
    posts = human(c, "asha", f"recheck {pid}")
    assert on_desk(posts, "asha")[0].startswith("KITTY_POT_CHECK") and c.pot.status(pid) == "submitted"


def test_a_treasurer_sees_a_claim_no_result_came_back_for(tmp_path):
    client, chat = app_for(tmp_path)
    clerk_story(chat)
    slip = client.get("/api/feed").json()["desk"]["slips"][0]
    assert (slip["id"], slip["kind"], slip["candidate"], slip["from"], slip["amount"]) == ("p1", "pot_review", "not checked yet", "Rohan", "₹5,000.00")


# ---- Re-graded up: the app must never say something false in public --------------------------------


def test_board_square_is_the_servers_word():
    c = make()
    pid = submitted(c)
    human(c, "rohan", f"withdraw {pid}")
    assert c.pot.board_view(c)["square"] is False, "nothing was ever counted"
    d = make()
    confirmed(d, amount=100000)
    assert d.pot.board_view(d)["square"] is False, "control: a balance is open"


def test_the_board_page_uses_it():
    assert "d.pot.square" in (APP / "board.html").read_text()


def test_settle_text_does_not_call_it_square_with_a_claim_open():
    from tests.pot_helpers import expense

    c = make()
    agent(c, "asha", expense("asha", 300000))
    human(c, "asha", f"release {next(iter(c.expenses))}")
    for i, who in enumerate(("rohan", "meera", "dev")):
        confirmed(c, who, credit=str(i) * 32, amount=75000)
    pid = review(c, "dev", result="none", amount=50000, day="2026-10-11")
    human(c, "asha", f"reject {pid} not_received")
    assert not any(c.balances().values())
    text = group(c, "dev", "settle")[0].text
    assert "square" not in text.lower() and "open" in text


def test_status_counts_contributions():
    c = make()
    confirmed(c)
    assert "1 payments confirmed" in group(c, "dev", "status")[0].text
