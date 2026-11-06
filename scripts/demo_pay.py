"""Add one synthetic payment to two synthetic statements, as if it had happened.

Kitty never moves money. This is a stand-in for people paying each other in their bank app.

    uv run python scripts/demo_pay.py        # the next suggested payment on the board
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
TRIP = json.loads((ROOT / "data/synthetic/trip.json").read_text())


def main() -> int:
    board = httpx.get("http://127.0.0.1:7100/api/board", timeout=10).json()
    if not board["plan"]:
        print("Nothing to pay.")
        return 0
    pay = board["plan"][0]
    ids = {d["name"]: p for p, d in TRIP["people"].items()}
    names = {d["name"]: d["statement_name"] for d in TRIP["people"].values()}
    tag = str(len(board["transfers"]) + 700)
    amount = f"{pay['paise'] / 100:.2f}"
    ref = f"U{tag}001"
    with (ROOT / "people" / ids[pay["from"]] / "statement.csv").open("a") as f:
        f.write(f"2026-10-12,20:00,UPI/DR/{names[pay['to']]}/{tag}001/GPAY,{ref},{amount},,,XXXXXX0000\n")
    with (ROOT / "people" / ids[pay["to"]] / "statement.csv").open("a") as f:
        f.write(f"2026-10-12,20:00,UPI/CR/{names[pay['from']]}/{tag}001/GPAY,{ref},,{amount},,XXXXXX0000\n")
    print(f"Synthetic payment added: {pay['from']} to {pay['to']}, ₹{amount}. {pay['from']} can now ask their agent to check again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
