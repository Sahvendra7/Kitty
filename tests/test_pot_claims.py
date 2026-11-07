"""The typed shape of everything that crosses in a Pot trip. No Switch, no model."""
from __future__ import annotations

import json
from datetime import date

import pytest

from kitty import claims as C
from kitty import pot as P

TRIP = C.Trip("goa-1", date(2026, 10, 16), date(2026, 10, 19), ("asha", "rohan", "meera"))
CFG = P.PotConfig("asha", date(2026, 9, 25))
TOKEN = "a" * 32


def contribution(**over) -> dict:
    obj = {"v": 1, "type": "contribution", "ref": "r1", "trip_id": "goa-1", "owner_id": "rohan", "to_id": "asha", "date": "2026-10-09", "amount_paise": 500000, "currency": "INR", "ref_token": None, "ref_last4": None}
    obj.update(over)
    return obj


def match(**over) -> dict:
    obj = {"v": 1, "type": "pot_match", "ref": "a1", "trip_id": "goa-1", "owner_id": "asha", "contribution_id": "p1", "result": "exact", "credit_token": TOKEN}
    obj.update(over)
    return obj


def parse(obj: dict):
    return P.parse_claim(C.PREFIX + json.dumps(obj), TRIP, CFG, obj["owner_id"])


def test_a_good_contribution_parses():
    got = parse(contribution(ref_token=TOKEN, ref_last4="1001"))
    assert isinstance(got, P.Contribution)
    assert (got.owner_id, got.to_id, got.amount_paise, got.date, got.ref_token, got.ref_last4) == ("rohan", "asha", 500000, date(2026, 10, 9), TOKEN, "1001")


@pytest.mark.parametrize("change, code", [
    ({"extra": 1}, "unknown_field"),
    ({"to_id": "rohan"}, "not_treasurer"),
    ({"to_id": "meera"}, "not_treasurer"),
    ({"owner_id": "asha", "to_id": "asha"}, "self_transfer"),
    ({"date": "2026-09-24"}, "date_outside_trip"),
    ({"date": "2026-11-19"}, "date_outside_trip"),
    ({"date": "9 Oct"}, "bad_date"),
    ({"amount_paise": 0}, "bad_amount"),
    ({"amount_paise": 100.0}, "bad_amount"),
    ({"amount_paise": True}, "bad_amount"),
    ({"currency": "USD"}, "bad_currency"),
    ({"ref_token": "XYZ"}, "bad_token"),
    ({"ref_token": "A" * 32}, "bad_token"),
    ({"ref_last4": "12345"}, "bad_last4"),
    ({"ref_last4": "1 34"}, "bad_last4"),
    ({"trip_id": "other"}, "wrong_trip"),
])
def test_bad_contributions_are_refused_with_a_code(change, code):
    with pytest.raises(C.ClaimError) as err:
        parse(contribution(**change))
    assert err.value.code == code


def test_a_missing_field_is_refused():
    obj = contribution()
    del obj["ref_token"]
    with pytest.raises(C.ClaimError) as err:
        parse(obj)
    assert err.value.code == "missing_field"


def test_pre_trip_date_is_accepted_once_collection_has_started():
    assert parse(contribution(date="2026-09-25")).date == date(2026, 9, 25)


def test_settling_date_past_the_window_gets_a_fixed_code():
    assert parse(contribution(date="2026-11-18")).date == date(2026, 11, 18)
    with pytest.raises(C.ClaimError) as err:
        parse(contribution(date="2026-11-25"))
    assert err.value.code == "date_outside_trip"


def test_rejection_never_echoes_the_offending_text():
    with pytest.raises(C.ClaimError) as err:
        parse(contribution(ref_last4="NETFLIX CANARY", merchant="TANISHQ"))
    assert "NETFLIX" not in str(err.value) and "TANISHQ" not in str(err.value)


@pytest.mark.parametrize("kind", ["expense", "transfer", "transfer_answer", "something_else"])
def test_other_types_are_left_to_the_clerk(kind):
    assert P.parse_claim(C.PREFIX + json.dumps({"v": 1, "type": kind}), TRIP, CFG, "rohan") is None


@pytest.mark.parametrize("body", ["release p1", "KITTY/1 not json", 'KITTY/1 ["list"]', "hello"])
def test_things_that_are_not_pot_claims_are_left_alone(body):
    assert P.parse_claim(body, TRIP, CFG, "rohan") is None


def test_a_good_match_parses():
    got = parse(match())
    assert isinstance(got, P.PotMatch)
    assert (got.contribution_id, got.result, got.credit_token) == ("p1", "exact", TOKEN)


@pytest.mark.parametrize("change, code", [
    ({"result": "exact", "credit_token": None}, "bad_token"),
    ({"result": "none", "credit_token": TOKEN}, "bad_token"),
    ({"result": "amount_differs", "credit_token": TOKEN}, "bad_token"),
    ({"result": "several", "credit_token": None}, "bad_result"),
    ({"contribution_id": "t1"}, "bad_contribution_id"),
    ({"owner_id": "rohan"}, "not_treasurer"),
    ({"narration": "RENT"}, "unknown_field"),
])
def test_bad_matches_are_refused_with_a_code(change, code):
    with pytest.raises(C.ClaimError) as err:
        parse(match(**change))
    assert err.value.code == code


@pytest.mark.parametrize("result", ["none", "amount_differs"])
def test_a_match_with_no_candidate_carries_no_token(result):
    assert parse(match(result=result, credit_token=None)).credit_token is None


def test_tokens_are_keyed_and_ignore_case_and_spacing():
    assert P.token(b"k", " u441001 ") == P.token(b"k", "U441001")
    assert P.token(b"k", "U441001") != P.token(b"other", "U441001")
    assert P.token(b"k", "U441001") != P.token(b"k", "U441002")
    assert len(P.token(b"k", "x")) == 32 and "U441001" not in P.token(b"k", "U441001")
