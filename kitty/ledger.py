"""Integer money. Balances and settlement. Pure: no I/O, no LLM, no floats.

Two numbers are kept apart on purpose:

    obligations: what the released expenses say each person is owed or owes.
    confirmed:   transfers that both sides' owners have confirmed.

A transfer changes a balance only once confirmed. A claimed payment that one side
cannot see changes nothing.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    payer: str
    amount_paise: int
    shares: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Payment:
    from_id: str
    to_id: str
    amount_paise: int


def obligations(participants: Sequence[str], lines: Iterable[Line]) -> dict[str, int]:
    """Net from expenses alone. Positive: the group owes this person."""
    net = {p: 0 for p in participants}
    for line in lines:
        total = sum(v for _, v in line.shares)
        if total != line.amount_paise:
            raise ValueError("shares do not sum to the expense total")
        net[line.payer] += line.amount_paise
        for person, value in line.shares:
            net[person] -= value
    return net


def balances(participants: Sequence[str], lines: Iterable[Line], confirmed: Iterable[Payment]) -> dict[str, int]:
    """Net after confirmed transfers. Paying someone moves you up and them down."""
    net = obligations(participants, lines)
    for pay in confirmed:
        net[pay.from_id] += pay.amount_paise
        net[pay.to_id] -= pay.amount_paise
    if sum(net.values()) != 0:
        raise AssertionError("ledger does not net to zero")
    return net


def settle(net: dict[str, int]) -> list[Payment]:
    """A short list of payments that clears every balance.

    Exact debtor and creditor matches go first, then largest debtor pays largest creditor.
    That takes at most n-1 payments. It is not guaranteed to be the global minimum, which
    is NP-hard in general, so we call it simplified rather than optimal.
    """
    if sum(net.values()) != 0:
        raise ValueError("cannot settle balances that do not net to zero")
    debtors = {p: -v for p, v in net.items() if v < 0}
    creditors = {p: v for p, v in net.items() if v > 0}
    plan: list[Payment] = []

    for debtor in sorted(debtors):
        match = next((c for c in sorted(creditors) if creditors[c] == debtors[debtor]), None)
        if match is not None:
            plan.append(Payment(debtor, match, debtors[debtor]))
            del creditors[match]
            debtors[debtor] = 0
    debtors = {p: v for p, v in debtors.items() if v}

    while debtors:
        debtor = max(sorted(debtors), key=debtors.__getitem__)
        creditor = max(sorted(creditors), key=creditors.__getitem__)
        amount = min(debtors[debtor], creditors[creditor])
        plan.append(Payment(debtor, creditor, amount))
        debtors[debtor] -= amount
        creditors[creditor] -= amount
        if not debtors[debtor]:
            del debtors[debtor]
        if not creditors[creditor]:
            del creditors[creditor]
    return plan
