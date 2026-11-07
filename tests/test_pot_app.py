"""What a person's app shows on a Pot trip, read from the same text people see. A fake chat server, no network."""
from __future__ import annotations

from pathlib import Path

import pytest

from kitty import ledger as L
from kitty import person_app as PA
from kitty import pot as P
from tests.pot_helpers import PEOPLE, TOKEN_A, TOKEN_B, agent, claim, confirmed, group, held, human, in_group, make, match, on_desk, review, submitted

IDS = {p.title(): p for p in PEOPLE}
APP = Path(__file__).parent.parent / "app"


def story():
    """A clerk, and every line the group room showed, in order."""
    c, lines = make(), []
    def run(posts):
        lines.extend(in_group(posts))
    run(human(c, "asha", "call 5000"))
    run(human(c, "asha", "call 100 rohan"))
    run(human(c, "asha", "cancel k2"))
    run(human(c, "asha", "call 200 meera"))
    a = held(c, "rohan", ref_token=TOKEN_A, ref_last4="1001")
    run(human(c, "rohan", f"release {a}"))
    run(agent(c, "asha", match(a, credit=TOKEN_A)))
    run(human(c, "asha", f"confirm {a}"))
    b = held(c, "meera", amount=300000)
    run(human(c, "meera", f"release {b}"))
    run(agent(c, "asha", match(b, "none")))
    run(human(c, "asha", f"confirm {b} manual"))
    d = held(c, "dev", amount=100000)
    run(human(c, "dev", f"release {d}"))
    run(agent(c, "asha", match(d, "none")))
    run(human(c, "asha", f"reject {d} not_received"))
    return c, lines


def test_parse_room_reads_asked_and_status():
    _, lines = story()
    room = P.parse_room(lines, IDS)
    assert room.asked == {"asha": 0, "rohan": 500000, "meera": 520000, "dev": 500000}
    assert {k: v["status"] for k, v in room.contributions.items()} == {"p1": "confirmed", "p2": "confirmed", "p3": "rejected"}
    assert room.contributions["p1"]["basis"] == "records" and room.contributions["p2"]["basis"] == "word"
    assert room.contributions["p2"] == {"from": "meera", "to": "asha", "amount": 300000, "status": "confirmed", "basis": "word"}
    assert set(room.payments) == {L.Payment("rohan", "asha", 500000), L.Payment("meera", "asha", 300000)}


def test_parse_room_follows_corrections():
    c = make()
    lines = in_group(human(c, "asha", "call 5000"))
    pid = held(c)
    lines += in_group(human(c, "rohan", f"release {pid}"))
    agent(c, "asha", match(pid))
    lines += in_group(human(c, "asha", f"confirm {pid}"))
    assert P.parse_room(lines, IDS).payments == [L.Payment("rohan", "asha", 500000)]
    lines += in_group(human(c, "asha", f"correct {pid} reversed"))
    assert P.parse_room(lines, IDS).payments == [] and P.parse_room(lines, IDS).contributions[pid]["status"] == "rejected"
    lines += in_group(human(c, "asha", f"correct {pid} received"))
    assert P.parse_room(lines, IDS).payments == [L.Payment("rohan", "asha", 500000)]


def test_parse_room_ignores_a_chat_line_shaped_like_nothing():
    assert P.parse_room(["Kitty is open for **Goa**", "| Person | Asked |", "hello"], IDS).payments == []


def test_parse_room_matches_the_board_by_its_own_route():
    c, lines = story()
    room = P.parse_room(lines, IDS)
    view = c.pot.board_view(c)
    assert {IDS[r["name"]]: r["asked"] for r in view["roll"]} == {p: room.asked[p] for p in PEOPLE if p != "asha"}
    assert sum(p.amount_paise for p in room.payments) == view["position"]


def desk_posts(c, who, *runs) -> list[dict]:
    return [{"text": t, "mine": False} for posts in runs for t in on_desk(posts, who)]


def test_desk_builds_a_contribution_slip():
    c = make()
    pid_posts = agent(c, "rohan", claim())
    built = PA.build_desk(desk_posts(c, "rohan", pid_posts), [], {})
    slip = built["slips"][0]
    assert (slip["id"], slip["kind"], slip["to"], slip["amount"], slip["date"], slip["state"]) == ("p1", "contribution", "Asha", "₹5,000.00", "9 Oct", "waiting")


@pytest.mark.parametrize("status", ["submitted", "needs_clarification", "confirmed", "rejected", "withdrawn"])
def test_contribution_slip_takes_its_status_from_the_room(status):
    c = make()
    posts = agent(c, "rohan", claim())
    built = PA.build_desk(desk_posts(c, "rohan", posts, human(c, "rohan", "release p1")), [], {"p1": {"status": status, "basis": None}})
    assert built["slips"][0]["state"] == status


def test_a_dropped_slip_stays_dropped():
    c = make()
    posts = agent(c, "rohan", claim())
    built = PA.build_desk(desk_posts(c, "rohan", posts, human(c, "rohan", "drop p1")), [], {})
    assert built["slips"][0]["state"] == "dropped"


def test_the_reason_reaches_the_slip():
    c = make()
    pid = review(c, result="amount_differs")
    posts = [{"text": f"**{pid}** ready [ref r1]: you paid Asha ₹5,000.00 on 9 Oct.", "mine": False}, *desk_posts(c, "rohan", human(c, "asha", f"ask {pid} amount_differs"))]
    built = PA.build_desk(posts, [], {pid: {"status": "needs_clarification", "basis": None}})
    assert "amount" in built["slips"][0]["note"]


@pytest.mark.parametrize("result, said", [("exact", "found it"), ("amount_differs", "a different amount"), ("none", "nothing yet")])
def test_desk_builds_a_review_slip(result, said):
    c = make()
    pid = submitted(c)
    built = PA.build_desk(desk_posts(c, "asha", agent(c, "asha", match(pid, result))), [], {pid: {"status": "submitted", "basis": None}})
    slip = built["slips"][0]
    assert (slip["kind"], slip["from"], slip["amount"], slip["date"], slip["candidate"], slip["state"]) == ("pot_review", "Rohan", "₹5,000.00", "9 Oct", said, "waiting")


def test_a_recheck_replaces_the_review_slip():
    c = make()
    pid = review(c, result="none")
    posts = [{"text": f"**{pid}** to check: Rohan says they paid you ₹5,000.00 on 9 Oct. Your agent: nothing yet.", "mine": False}, *desk_posts(c, "asha", human(c, "asha", f"recheck {pid}"), agent(c, "asha", match(pid)))]
    built = PA.build_desk(posts, [], {pid: {"status": "submitted", "basis": None}})
    assert len(built["slips"]) == 1 and built["slips"][0]["candidate"] == "found it"


def test_machine_messages_are_kept_out_of_the_slips():
    built = PA.build_desk([{"text": "KITTY_POT_CHECK p1 from=rohan amount_paise=1 date=2026-10-09 ref_token=-", "mine": False}], [], {})
    assert not built["slips"] and built["raw"]


def test_review_slip_carries_no_statement_text():
    c = make()
    pid = submitted(c)
    built = PA.build_desk(desk_posts(c, "asha", agent(c, "asha", match(pid))), [], {})
    assert TOKEN_A not in str(built["slips"]) and "UPI" not in str(built["slips"])


def test_mode_a_build_desk_is_unchanged_without_a_pot():
    posts = [{"text": "**c1** ready [ref a1]: ₹900.00 · food · 10 Oct · paid by Priya · Priya ₹450.00, Arjun ₹450.00\nReply `release c1`", "mine": False}, {"text": "c1 released.", "mine": False}]
    built = PA.build_desk(posts, ["**c1** ₹900.00 · food · 10 Oct · paid by Priya · Priya ₹450.00, Arjun ₹450.00"])
    assert [(s["id"], s["kind"], s["state"]) for s in built["slips"]] == [("c1", "expense", "released")]


def test_pot_figures_for_a_person():
    _, lines = story()
    assert PA.pot_view(lines, "meera", {p: {"name": p.title()} for p in PEOPLE}, "asha") == {
        "treasurer": "Asha", "is_treasurer": False,
        "mine": {"asked": 520000, "credited": 300000, "outstanding": 220000, "above": 0},
        "roll": [
            {"name": "Rohan", "asked": 500000, "credited": 500000, "outstanding": 0, "above": 0},
            {"name": "Meera", "asked": 520000, "credited": 300000, "outstanding": 220000, "above": 0},
            {"name": "Dev", "asked": 500000, "credited": 0, "outstanding": 500000, "above": 0},
        ],
    }


def test_pot_figures_for_the_treasurer():
    _, lines = story()
    view = PA.pot_view(lines, "asha", {p: {"name": p.title()} for p in PEOPLE}, "asha")
    assert view["is_treasurer"] is True and view["mine"]["asked"] == 0


@pytest.mark.parametrize("page", ["person.html", "board.html", "home.html"])
def test_pages_contain_no_banned_words(page):
    text = (APP / page).read_text().lower()
    for word in ("verified", "bank-confirmed", "fake", "fraud", "suspicious", "proven"):
        assert word not in text, word


def test_the_person_page_has_the_treasurers_and_members_controls():
    text = (APP / "person.html").read_text()
    for needed in ("Your contribution", "I paid", "Ask for money", "Confirm by hand", "Correct this", "Show the last 4 in the group"):
        assert needed in text, needed


def test_the_board_labels_the_position_as_records():
    assert "by the records" in str(make().pot.board_view(make())) and "pot" in (APP / "board.html").read_text()
