"""Every transition in the spec's state machine, and everything else being refused. No Switch, no model."""
from __future__ import annotations

import re

import pytest

from kitty import pot as P
from tests.pot_helpers import (GROUP, TOKEN_A, agent, claim, confirmed, expense, group, held, human, in_group, make, match, on_desk, review, submitted, transfer)


def test_held_claim_reaches_nobody():
    c = make()
    posts = agent(c, "rohan", claim())
    assert in_group(posts) == []
    assert on_desk(posts, "asha") == []
    assert re.match(r"^\*\*p1\*\* ready \[ref r\d+\]: you paid Asha ₹5,000\.00 on 9 Oct\.", on_desk(posts, "rohan")[0])
    assert c.pot.status("p1") == "held"


def test_drop_keeps_it_private():
    c = make()
    pid = held(c)
    posts = human(c, "rohan", f"drop {pid}")
    assert in_group(posts) == [] and c.pot.status(pid) == "dropped"


def test_release_posts_to_group_and_asks_the_treasurers_agent():
    c = make()
    pid = held(c)
    posts = human(c, "rohan", f"release {pid}")
    assert re.match(r"^\*\*p1\*\* Rohan says they paid Asha ₹5,000\.00 on 9 Oct\. Not counted yet\. \[d1 [0-9a-f]{12}\]$", in_group(posts)[0])
    assert on_desk(posts, "asha") == ["KITTY_POT_CHECK p1 from=rohan amount_paise=500000 date=2026-10-09 ref_token=-"]
    assert c.pot.status(pid) == "submitted"


def test_opted_in_last_four_shows_and_the_token_goes_to_the_agent_only():
    c = make()
    pid = held(c, ref_token=TOKEN_A, ref_last4="1001")
    posts = human(c, "rohan", f"release {pid}")
    assert "· ref …1001." in in_group(posts)[0] and TOKEN_A not in in_group(posts)[0]
    assert on_desk(posts, "asha")[0].endswith(f"ref_token={TOKEN_A}")


@pytest.mark.parametrize("steps", ["held", "submitted", "review", "clarifying"])
def test_nothing_counts_until_confirmed(steps):
    c = make()
    before = c.balances()
    if steps == "held":
        held(c)
    elif steps == "submitted":
        submitted(c)
    else:
        pid = review(c)
        if steps == "clarifying":
            human(c, "asha", f"ask {pid} not_found_yet")
    assert c.balances() == before


def test_confirm_needs_exact_and_counts():
    c = make()
    pid = review(c)
    posts = human(c, "asha", f"confirm {pid}")
    assert re.match(r"^\*\*p1 confirmed received by Asha:\*\* Rohan paid ₹5,000\.00 · checked against Asha's records · 9 Oct, 14:02 IST\. \[d2 [0-9a-f]{12}\]$", in_group(posts)[0])
    assert c.pot.status(pid) == "confirmed"
    assert c.balances()["rohan"] == 500000 and c.balances()["asha"] == -500000


def test_a_candidate_is_private_until_the_treasurer_decides():
    c = make()
    pid = submitted(c)
    posts = agent(c, "asha", match(pid))
    assert in_group(posts) == []
    assert on_desk(posts, "asha")[0].startswith("**p1** to check: Rohan says they paid you ₹5,000.00 on 9 Oct. Your agent: found it.")
    assert c.pot.status(pid) == "recipient_review"


def test_confirm_refused_on_none_without_manual():
    c = make()
    pid = review(c, result="none")
    posts = human(c, "asha", f"confirm {pid}")
    assert "no_candidate_use_manual" in on_desk(posts, "asha")[0] and c.pot.status(pid) == "recipient_review"


def test_manual_on_none_is_labelled_as_the_treasurers_word():
    c = make()
    pid = review(c, result="none")
    posts = human(c, "asha", f"confirm {pid} manual")
    assert "· on Asha's word ·" in in_group(posts)[0] and "records" not in in_group(posts)[0]
    assert c.pot.status(pid) == "confirmed" and c.balances()["rohan"] == 500000


@pytest.mark.parametrize("result", ["exact", "amount_differs"])
def test_manual_is_only_for_none(result):
    c = make()
    pid = review(c, result=result)
    posts = human(c, "asha", f"confirm {pid} manual")
    assert posts and posts[0].text.startswith("Refused:") and c.pot.status(pid) == "recipient_review"


def test_amount_differs_blocks_confirm():
    c = make()
    pid = review(c, result="amount_differs")
    posts = human(c, "asha", f"confirm {pid}")
    assert "amount_differs" in on_desk(posts, "asha")[0] and c.pot.status(pid) == "recipient_review"


def test_recheck_sends_it_back_and_a_new_result_can_confirm():
    c = make()
    pid = review(c, result="none")
    posts = human(c, "asha", f"recheck {pid}")
    assert on_desk(posts, "asha")[0].startswith("KITTY_POT_CHECK p1 ") and c.pot.status(pid) == "submitted"
    agent(c, "asha", match(pid))
    human(c, "asha", f"confirm {pid}")
    assert c.pot.status(pid) == "confirmed"


def test_ask_code_stays_off_the_group():
    c = make()
    pid = review(c, result="amount_differs")
    posts = human(c, "asha", f"ask {pid} amount_differs")
    assert re.match(r"^\*\*p1 needs clarification\.\*\* Asha can't confirm it yet\. Not counted\. \[d\d+ [0-9a-f]{12}\]$", in_group(posts)[0])
    assert "amount_differs" not in " ".join(in_group(posts))
    assert any("amount_differs" in t for t in on_desk(posts, "rohan"))
    assert c.pot.status(pid) == "needs_clarification"


@pytest.mark.parametrize("code", ["send_proof", "because I said so", ""])
def test_ask_needs_a_listed_code(code):
    c = make()
    pid = review(c)
    posts = human(c, "asha", f"ask {pid} {code}")
    assert posts[0].text.startswith("Refused:") and c.pot.status(pid) == "recipient_review"
    assert "said so" not in posts[0].text


def test_reject_counts_nothing_and_rechecks_the_payer():
    c = make()
    pid = review(c, result="none")
    before = c.balances()
    posts = human(c, "asha", f"reject {pid} not_received")
    assert re.match(r"^\*\*p1 not received, says Asha\.\*\* Not counted\. \[d\d+ [0-9a-f]{12}\]$", in_group(posts)[0])
    assert "KITTY_RECHECK p1" in on_desk(posts, "rohan")
    assert c.pot.status(pid) == "rejected" and c.balances() == before


def test_none_stays_private():
    c = make()
    pid = submitted(c)
    assert in_group(agent(c, "asha", match(pid, "none"))) == []


@pytest.mark.parametrize("who, room", [("rohan", "desk-rohan"), ("asha", "desk-asha")])
@pytest.mark.parametrize("command", ["confirm p1", "confirm p1 manual", "reject p1 not_received", "ask p1 not_found_yet", "recheck p1", "correct p1 mistaken", "call 500", "cancel k1"])
def test_agents_cannot_decide(who, room, command):
    c = make()
    pid = review(c)
    posts = c.handle(room, f"@agent-{who}", command)
    assert "agent_cannot_approve" in posts[0].text and c.pot.status(pid) == "recipient_review"


@pytest.mark.parametrize("command", ["confirm p1", "confirm p1 manual", "reject p1 not_received", "ask p1 not_found_yet", "recheck p1", "correct p1 mistaken", "call 500", "cancel k1"])
def test_members_cannot_decide(command):
    c = make()
    pid = review(c)
    posts = human(c, "rohan", command)
    assert "only_treasurer" in posts[0].text and c.pot.status(pid) == "recipient_review"


@pytest.mark.parametrize("command", ["confirm p1", "reject p1 not_received", "call 500"])
def test_a_treasurers_command_in_the_group_room_is_refused(command):
    c = make()
    pid = review(c)
    posts = group(c, "asha", command)
    assert posts[0].room_id == GROUP and posts[0].text.startswith("Refused:") and c.pot.status(pid) == "recipient_review"


def test_the_treasurers_account_on_the_treasurers_desk_can_decide():
    c = make()
    pid = review(c)
    human(c, "asha", f"confirm {pid}")
    assert c.pot.status(pid) == "confirmed"


@pytest.mark.parametrize("who", ["asha", "meera"])
def test_only_the_owner_can_release_or_withdraw(who):
    c = make()
    pid = held(c)
    assert human(c, who, f"release {pid}")[0].text.startswith("Refused:") and c.pot.status(pid) == "held"
    human(c, "rohan", f"release {pid}")
    assert human(c, who, f"withdraw {pid}")[0].text.startswith("Refused:") and c.pot.status(pid) == "submitted"


def test_an_agent_cannot_release_its_own_claim():
    c = make()
    pid = held(c)
    assert "agent_cannot_approve" in agent(c, "rohan", f"release {pid}")[0].text and c.pot.status(pid) == "held"


def test_a_stranger_on_a_desk_is_refused_by_the_clerk():
    c = make()
    pid = review(c)
    posts = c.handle("desk-asha", "@human-rohan", f"confirm {pid}")
    assert posts[0].text.startswith("Refused:") and c.pot.status(pid) == "recipient_review"


def test_member_cannot_withdraw_a_confirmed_contribution():
    c = make()
    pid = confirmed(c)
    assert human(c, "rohan", f"withdraw {pid}")[0].text.startswith("Refused:") and c.pot.status(pid) == "confirmed"


@pytest.mark.parametrize("state", ["submitted", "review", "clarifying", "rejected"])
def test_member_can_withdraw_anything_still_open(state):
    c = make()
    pid = submitted(c) if state == "submitted" else review(c, result="none")
    if state == "clarifying":
        human(c, "asha", f"ask {pid} not_found_yet")
    if state == "rejected":
        human(c, "asha", f"reject {pid} not_received")
    posts = human(c, "rohan", f"withdraw {pid} payment_failed")
    assert c.pot.status(pid) == "withdrawn" and re.match(r"^\*\*p1 withdrawn by Rohan\.\*\* The payment failed and never arrived\. It isn't counted, so balances don't change\. \[d", in_group(posts)[0])


def test_correction_is_append_only():
    c = make()
    pid = confirmed(c)
    first = c.balances()
    decisions = list(c.pot.decisions)
    out = human(c, "asha", f"correct {pid} reversed")
    assert "**Correction of p1:** Asha no longer confirms it (reversed). Rohan's ₹5,000.00 no longer counts. Rohan's balance goes from ₹5,000.00 to ₹0.00." in in_group(out)[0]
    assert c.pot.status(pid) == "rejected" and c.balances()["rohan"] == 0
    assert c.pot.decisions[: len(decisions)] == decisions, "earlier decisions stay as they were"
    back = human(c, "asha", f"correct {pid} received")
    assert "**Correction of p1:** Asha says it arrived after all. Rohan paid ₹5,000.00. Rohan's balance goes from ₹0.00 to ₹5,000.00." in in_group(back)[0]
    assert c.pot.status(pid) == "confirmed" and c.balances() == first
    assert sum(c.balances().values()) == 0
    assert [d.kind for d in c.pot.decisions if d.target == pid and d.seq][-3:] == ["confirm", "correct_out", "correct_in"]


def test_correction_after_settlement_edits_nothing_else():
    c = make()
    pid = confirmed(c)
    agent(c, "asha", transfer("asha", "rohan", 100000))
    tid = next(iter(c.transfers))
    human(c, "asha", f"release {tid}")
    agent(c, "rohan", '''KITTY/1 {"v":1,"type":"transfer_answer","ref":"z1","trip_id":"goa-1","owner_id":"rohan","transfer_id":"%s","received":true}''' % tid)
    human(c, "rohan", f"release {tid}")
    assert c.transfers[tid].status == "confirmed"
    human(c, "asha", f"correct {pid} mistaken")
    assert c.transfers[tid].status == "confirmed", "the refund stands"
    assert c.balances() == {"asha": 100000, "rohan": -100000, "meera": 0, "dev": 0}


@pytest.mark.parametrize("code", ["received", "not_a_code"])
def test_correction_of_a_confirmed_one_needs_a_listed_code(code):
    c = make()
    pid = confirmed(c)
    assert human(c, "asha", f"correct {pid} {code}")[0].text.startswith("Refused:") and c.pot.status(pid) == "confirmed"


ALLOWED = {
    # status: commands that may succeed
    "held": {"release", "drop"},
    "dropped": set(),
    "submitted": {"withdraw"},
    "recipient_review": {"confirm", "ask", "reject", "recheck", "withdraw"},
    "needs_clarification": {"reject", "recheck", "withdraw"},
    "confirmed": {"correct_out"},
    "rejected": {"correct_in", "withdraw"},
    "withdrawn": set(),
}
COMMANDS = {
    "release": ("rohan", "release {}"), "drop": ("rohan", "drop {}"), "withdraw": ("rohan", "withdraw {} mistaken"),
    "confirm": ("asha", "confirm {}"), "ask": ("asha", "ask {} not_found_yet"), "reject": ("asha", "reject {} not_received"), "recheck": ("asha", "recheck {}"),
    "correct_out": ("asha", "correct {} mistaken"), "correct_in": ("asha", "correct {} received"),
}


def in_state(c, state: str) -> str:
    if state == "held":
        return held(c)
    if state == "dropped":
        pid = held(c)
        human(c, "rohan", f"drop {pid}")
    elif state == "submitted":
        pid = submitted(c)
    elif state == "recipient_review":
        pid = review(c)
    elif state == "needs_clarification":
        pid = review(c)
        human(c, "asha", f"ask {pid} not_found_yet")
    elif state == "confirmed":
        pid = confirmed(c)
    elif state == "rejected":
        pid = review(c, result="none")
        human(c, "asha", f"reject {pid} not_received")
    else:
        pid = submitted(c)
        human(c, "rohan", f"withdraw {pid}")
    return pid


@pytest.mark.parametrize("state", list(ALLOWED))
@pytest.mark.parametrize("command", list(COMMANDS))
def test_every_transition_is_either_listed_or_refused(state, command):
    c = make()
    pid = in_state(c, state)
    assert c.pot.status(pid) == state
    decisions = len(c.pot.decisions)
    who, text = COMMANDS[command]
    posts = human(c, who, text.format(pid))
    if command in ALLOWED[state]:
        assert c.pot.status(pid) != state or len(c.pot.decisions) > decisions, f"{command} should work from {state}"
    else:
        assert posts[0].text.startswith(("Refused:", "Rejected:")), f"{command} should be refused from {state}: {posts}"
        assert c.pot.status(pid) == state and len(c.pot.decisions) == decisions


def test_retry_is_refused_with_a_fixed_code():
    c = make()
    pid = confirmed(c)
    posts = human(c, "asha", f"confirm {pid}")
    assert "already_confirmed" in posts[0].text
    assert c.balances()["rohan"] == 500000


def test_confirm_crossing_withdraw_first_wins():
    c = make()
    pid = review(c)
    human(c, "rohan", f"withdraw {pid}")
    assert human(c, "asha", f"confirm {pid}")[0].text.startswith("Refused:")
    assert c.pot.status(pid) == "withdrawn" and c.balances()["rohan"] == 0


def test_a_match_for_nothing_open_is_refused():
    c = make()
    assert "no_such_question" in agent(c, "asha", match("p9"))[0].text
    pid = held(c)
    assert "no_such_question" in agent(c, "asha", match(pid))[0].text


def test_a_match_from_a_member_is_refused():
    c = make()
    pid = submitted(c)
    posts = agent(c, "rohan", match(pid).replace('"owner_id": "asha"', '"owner_id": "rohan"'))
    assert "not_treasurer" in posts[0].text and c.pot.status(pid) == "submitted"


def test_transfer_to_treasurer_is_refused():
    c = make()
    posts = agent(c, "rohan", transfer("rohan", "asha"))
    assert "use_contribution" in posts[0].text and not c.transfers


def test_transfer_from_treasurer_and_between_members_still_work():
    c = make()
    agent(c, "asha", transfer("asha", "rohan"))
    agent(c, "meera", transfer("meera", "dev"))
    assert len(c.transfers) == 2


def test_a_contribution_to_someone_else_is_refused():
    c = make()
    assert "not_treasurer" in agent(c, "rohan", claim(to="meera"))[0].text


def test_a_bad_claim_gets_a_plain_reason_and_no_echo():
    c = make()
    text = agent(c, "rohan", claim(day="2026-10-25"))[0].text
    assert "date_outside_trip" in text and "25 Sep to 19 Oct" in text and text.startswith("Rejected:")


def test_call_asks_everyone_but_the_treasurer():
    c = make()
    posts = human(c, "asha", "call 5000")
    assert re.match(r"^\*\*k1\*\* Asha asked each person for ₹5,000\.00\. \[d1 [0-9a-f]{12}\]$", in_group(posts)[0])
    f = c.pot.view(c)
    assert f.asked == {"asha": 0, "rohan": 500000, "meera": 500000, "dev": 500000}
    assert c.balances() == {"asha": 0, "rohan": 0, "meera": 0, "dev": 0}, "a call never creates a debt"


def test_call_can_name_people():
    c = make()
    posts = human(c, "asha", "call 500.50 rohan Meera")
    assert "**k1** Asha asked Rohan, Meera for ₹500.50." in in_group(posts)[0]
    assert c.pot.view(c).asked["meera"] == 50050 and c.pot.view(c).asked["dev"] == 0


@pytest.mark.parametrize("text", ["call 0", "call -5", "call abc", "call", "call 500 nobody", "call 500 asha", "call 99999999999"])
def test_bad_calls_are_refused(text):
    c = make()
    posts = human(c, "asha", text)
    assert posts[0].text.startswith("Refused:") and not c.pot.calls and "nobody" not in posts[0].text


def test_cancelled_call_drops_from_asked():
    c = make()
    human(c, "asha", "call 5000")
    posts = human(c, "asha", "cancel k1")
    assert "**k1 cancelled by Asha.**" in in_group(posts)[0] and c.pot.view(c).asked["rohan"] == 0
    assert human(c, "asha", "cancel k1")[0].text.startswith("Refused:")
    assert human(c, "asha", "cancel k9")[0].text.startswith("Refused:")


def test_pot_command_shows_the_roll():
    c = make()
    human(c, "asha", "call 5000")
    confirmed(c, amount=300000)
    text = group(c, "meera", "pot")[0].text
    assert "| Rohan | ₹5,000.00 | ₹3,000.00 | ₹2,000.00 |" in text and "| Meera | ₹5,000.00 | ₹0.00 | ₹5,000.00 |" in text
    assert "Asha should be holding ₹3,000.00, by the records." in text


def test_negative_position_says_the_treasurer_put_money_in():
    c = make()
    agent(c, "asha", expense("asha", 400000))
    human(c, "asha", f"release {next(iter(c.expenses))}")
    assert "Asha has put in ₹4,000.00 of their own, by the records." in group(c, "dev", "pot")[0].text


def test_hub_plan_replaces_settle_text():
    c = make()
    agent(c, "asha", expense("asha", 400000))
    human(c, "asha", f"release {next(iter(c.expenses))}")
    text = group(c, "dev", "settle")[0].text
    assert "Rohan pays Asha **₹1,000.00**" in text and "Meera pays Asha" in text and "Dev pays Asha" in text
    assert all("Asha" in line for line in text.splitlines() if line.startswith("- "))
    assert {(p.from_id, p.to_id) for p in c.pot.plan(c)} == {("rohan", "asha"), ("meera", "asha"), ("dev", "asha")}


def everyone_square(c) -> list[str]:
    agent(c, "asha", expense("asha", 400000))
    human(c, "asha", f"release {next(iter(c.expenses))}")
    posts = []
    for i, who in enumerate(("rohan", "meera", "dev")):
        pid = review(c, who, credit=str(i) * 32, amount=100000, day=f"2026-10-1{i}")
        posts += human(c, "asha", f"confirm {pid}")
    return in_group(posts)


def test_square_when_everything_is_paid():
    c = make()
    assert any("Everyone's square" in t for t in everyone_square(c))


def test_a_rejected_contribution_blocks_square():
    c = make()
    pid = review(c, "meera", result="none", amount=50000)
    human(c, "asha", f"reject {pid} not_received")
    assert pid in c.pot.open_ids() and pid in c.open_items()
    assert not any("Everyone's square" in t for t in everyone_square(c))
    assert any("Everyone's square" in t for t in in_group(human(c, "meera", f"withdraw {pid} mistaken")))


def test_square_with_no_expenses_after_a_full_refund():
    c = make()
    confirmed(c, amount=100000)
    agent(c, "asha", transfer("asha", "rohan", 100000))
    tid = next(iter(c.transfers))
    human(c, "asha", f"release {tid}")
    agent(c, "rohan", '''KITTY/1 {"v":1,"type":"transfer_answer","ref":"z2","trip_id":"goa-1","owner_id":"rohan","transfer_id":"%s","received":true}''' % tid)
    assert any("Everyone's square" in t for t in in_group(human(c, "rohan", f"release {tid}")))


TEMPLATES = [
    r"^\*\*k\d+\*\* Asha asked (each person|[A-Z][a-z]+(, [A-Z][a-z]+)*) for ₹[\d,.]+\.",
    r"^\*\*k\d+ cancelled by Asha\.\*\*",
    r"^\*\*p\d+\*\* [A-Z][a-z]+ says they paid Asha ₹[\d,.]+ on \d+ \w+( · ref …[A-Za-z0-9]{4})?\. Not counted yet\.",
    r"^\*\*p\d+ confirmed received by Asha:\*\* [A-Z][a-z]+ paid ₹[\d,.]+ · (checked against Asha's records|on Asha's word) · \d+ \w+, \d\d:\d\d IST\.",
    r"^\*\*p\d+ needs clarification\.\*\* Asha can't confirm it yet\. Not counted\.",
    r"^\*\*p\d+ not received, says Asha\.\*\* Not counted\.",
    r"^\*\*p\d+ withdrawn by [A-Z][a-z]+\.\*\* .+ It isn't counted, so balances don't change\.",
    r"^\*\*Correction of p\d+:\*\* ",
    r"^\*\*Everyone's square\.\*\*",
]


def test_group_posts_match_templates_only():
    c = make()
    seen = []
    def run(posts):
        seen.extend(in_group(posts))
    run(human(c, "asha", "call 5000"))
    run(human(c, "asha", "call 100 rohan"))
    run(human(c, "asha", "cancel k2"))
    pid = held(c, ref_token=TOKEN_A, ref_last4="1001")
    run(human(c, "rohan", f"release {pid}"))
    run(agent(c, "asha", match(pid)))
    run(human(c, "asha", f"ask {pid} not_found_yet"))
    run(human(c, "asha", f"recheck {pid}"))
    run(agent(c, "asha", match(pid)))
    run(human(c, "asha", f"confirm {pid}"))
    run(human(c, "asha", f"correct {pid} mistaken"))
    run(human(c, "asha", f"correct {pid} received"))
    other = held(c, "meera")
    run(human(c, "meera", f"release {other}"))
    run(agent(c, "asha", match(other, "none")))
    run(human(c, "asha", f"reject {other} reversed"))
    run(human(c, "meera", f"withdraw {other} payment_failed"))
    assert len(seen) >= 10
    for text in seen:
        assert any(re.match(t, text) for t in TEMPLATES), text


def test_no_banned_words_in_any_post():
    c = make()
    posts = list(human(c, "asha", "call 5000"))
    pid = review(c, result="none")
    posts += human(c, "asha", f"confirm {pid} manual") + human(c, "asha", f"correct {pid} mistaken") + group(c, "dev", "pot")
    joined = " ".join(p.text for p in posts).lower()
    for word in ("verified", "bank-confirmed", "fake", "fraud", "suspicious", "proven"):
        assert word not in joined


def test_chain_survives_a_whole_flow():
    c = make()
    posts = list(human(c, "asha", "call 5000"))
    pid = held(c)
    posts += human(c, "rohan", f"release {pid}")
    agent(c, "asha", match(pid))
    posts += human(c, "asha", f"confirm {pid}") + human(c, "asha", f"correct {pid} mistaken")
    lines = in_group(posts)
    assert len(lines) == 4 and P.chain_check(lines) is None


def test_board_view_has_the_roll_and_no_tokens():
    c = make()
    human(c, "asha", "call 5000")
    confirmed(c)
    view = c.pot.board_view(c)
    assert view["treasurer"] == "Asha" and view["position"] == 500000
    assert {"name": "Rohan", "asked": 500000, "credited": 500000, "outstanding": 0, "above": 0} in view["roll"]
    assert view["contributions"][0]["status"] == "confirmed" and view["contributions"][0]["basis"] == "records"
    assert TOKEN_A not in str(view)
