"""One payment can't be counted twice, by mistake or on purpose. Positive controls beside each refusal."""
from __future__ import annotations

from tests.pot_helpers import TOKEN_A, TOKEN_B, agent, claim, confirmed, held, human, make, match, review, submitted


def test_same_day_and_amount_without_a_reference_is_refused():
    c = make()
    held(c)
    assert "possible_duplicate" in agent(c, "rohan", claim())[0].text
    assert len(c.pot.records) == 1


def test_a_reference_tells_two_payments_apart():
    c = make()
    held(c, ref_token=TOKEN_A)
    held(c, ref_token=TOKEN_B)
    assert len(c.pot.records) == 2


def test_a_different_day_is_a_different_claim():
    c = make()
    held(c)
    held(c, day="2026-10-10")
    assert len(c.pot.records) == 2


def test_same_reference_from_anyone_is_refused_and_names_nobody():
    c = make()
    held(c, "rohan", ref_token=TOKEN_A)
    text = agent(c, "meera", claim("meera", amount=700000, ref_token=TOKEN_A))[0].text
    assert "duplicate_reference" in text and "Rohan" not in text and "p1" not in text
    assert len(c.pot.records) == 1


def test_credit_token_single_use_at_the_clerk():
    """Even when a wiped agent offers the same credit again."""
    c = make()
    confirmed(c, "rohan", credit=TOKEN_A)
    second = review(c, "meera", credit=TOKEN_A)
    assert "credit_already_used" in human(c, "asha", f"confirm {second}")[0].text
    assert c.pot.status(second) == "recipient_review" and c.balances()["meera"] == 0


def test_a_different_credit_confirms():
    c = make()
    confirmed(c, "rohan", credit=TOKEN_A)
    second = confirmed(c, "meera", credit=TOKEN_B)
    assert c.pot.status(second) == "confirmed"


def test_token_is_free_after_correct_out():
    c = make()
    first = confirmed(c, "rohan", credit=TOKEN_A)
    human(c, "asha", f"correct {first} mistaken")
    assert c.pot.status(confirmed(c, "meera", credit=TOKEN_A)) == "confirmed"


def test_correct_back_in_checks_the_token():
    c = make()
    first = confirmed(c, "rohan", credit=TOKEN_A)
    human(c, "asha", f"correct {first} mistaken")
    confirmed(c, "meera", credit=TOKEN_A)
    assert "credit_already_used" in human(c, "asha", f"correct {first} received")[0].text
    assert c.pot.status(first) == "rejected"


def test_reference_mismatch_refuses_confirm():
    c = make()
    pid = review(c, credit=TOKEN_B, ref_token=TOKEN_A)
    assert "reference_differs" in human(c, "asha", f"confirm {pid}")[0].text
    assert c.pot.status(pid) == "recipient_review"


def test_equal_tokens_confirm():
    c = make()
    assert c.pot.status(confirmed(c, credit=TOKEN_A, ref_token=TOKEN_A)) == "confirmed"


def test_withdrawn_or_dropped_claims_do_not_block_a_new_claim():
    c = make()
    pid = submitted(c, ref_token=TOKEN_A)
    human(c, "rohan", f"withdraw {pid}")
    other = held(c, ref_token=TOKEN_A)
    human(c, "rohan", f"drop {other}")
    held(c, ref_token=TOKEN_A)
    assert len(c.pot.records) == 3


def test_repeated_internal_ref_is_refused():
    c = make()
    body = claim()
    agent(c, "rohan", body)
    assert "duplicate_ref" in agent(c, "rohan", body)[0].text


def test_a_match_with_an_earlier_candidate_is_replaced_on_recheck():
    c = make()
    pid = review(c, credit=TOKEN_A)
    human(c, "asha", f"recheck {pid}")
    agent(c, "asha", match(pid, credit=TOKEN_B))
    assert c.pot.records[pid].credit_token == TOKEN_B
