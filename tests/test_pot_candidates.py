"""Finding a credit on the treasurer's own statement, in plain code."""
from __future__ import annotations

from datetime import date

import pytest

from kitty import pot as P
from kitty.pot_agent import find_candidate, taken_back
from kitty.statements import Row

DIRECTORY = {"ROHAN K": "rohan", "MEERA S": "meera"}
KEY = b"k"
DAY = date(2026, 10, 9)


def credit(rid="r1", name="ROHAN K", ref="U1", amount=500000, day=DAY, time="14:02") -> Row:
    return Row(rid, day, time, f"UPI/CR/{name}/1/GPAY", ref, 0, amount)


def find(rows, amount=500000, around=DAY, who="rohan", used=frozenset(), want=None):
    return find_candidate(rows, amount, around, who, DIRECTORY, set(used), want, KEY)


def test_exact_match_returns_the_row():
    got = find([credit()])
    assert got.result == "exact" and got.row.row_id == "r1"


def test_reversed_credit_is_not_a_candidate():
    rows = [credit(), Row("r2", DAY, "16:00", "UPI/REV/ROHAN K/1/GPAY", "U1", 500000, 0)]
    assert taken_back(rows, rows[0])
    assert find(rows).result == "none"
    assert find(rows[:1]).result == "exact", "control: the same credit with no reversal is found"


def test_a_plain_debit_with_the_same_reference_also_takes_it_back():
    rows = [credit(), Row("r2", DAY, "16:00", "CHARGEBACK", "U1", 500000, 0)]
    assert find(rows).result == "none"


def test_an_earlier_debit_with_the_same_reference_does_not_take_it_back():
    rows = [Row("r0", DAY, "10:00", "CHARGEBACK", "U1", 500000, 0), credit()]
    assert find(rows).result == "exact"


def test_used_row_is_skipped():
    rows = [credit()]
    assert find(rows).result == "exact"
    assert find(rows, used={"r1"}).result == "none"


def test_two_equal_credits_serve_two_claims_in_order():
    rows = [credit("r1", ref="U1"), credit("r2", ref="U2", time="15:00")]
    assert find(rows).row.row_id == "r1"
    assert find(rows, used={"r1"}).row.row_id == "r2"


def test_token_prefers_its_own_row():
    rows = [credit("r1", ref="U1"), credit("r2", ref="U2", time="15:00")]
    assert find(rows, want=P.token(KEY, "U2")).row.row_id == "r2"


def test_a_token_matching_nothing_still_reports_the_exact_credit():
    """The clerk then refuses it as reference_differs, and the treasurer asks."""
    assert find([credit()], want=P.token(KEY, "OTHER")).row.row_id == "r1"


def test_different_amount_reports_differs():
    got = find([credit(amount=450000)])
    assert got.result == "amount_differs" and got.row is None


def test_other_persons_credit_is_none():
    assert find([credit(name="DAD K")]).result == "none"
    assert find([credit(name="MEERA S")]).result == "none"


@pytest.mark.parametrize("row", [
    Row("r1", DAY, "14:02", "UPI/DR/ROHAN K/1/GPAY", "U1", 500000, 0),
    Row("r1", DAY, "14:02", "UPI/REV/ROHAN K/1/GPAY", "U1", 0, 500000),
])
def test_a_debit_or_a_reversal_is_never_a_credit(row):
    assert find([row]).result == "none"


@pytest.mark.parametrize("days, result", [(2, "exact"), (3, "none")])
def test_two_days_of_slack(days, result):
    assert find([credit(day=date(2026, 10, 9 + days))]).result == result


def test_salary_with_the_same_amount_is_not_a_candidate():
    assert find([Row("r1", DAY, "09:00", "SALARY NEFT", "N1", 0, 500000)]).result == "none"
