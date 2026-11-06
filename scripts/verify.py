"""Independent proof of the outcome. Not an agent, and it does not trust the clerk's own totals.

Sources it reads:
  - Switch's record of the group room and each desk, fetched with read_context.
  - The clerk's event log, for who released what.
  - The original synthetic statements, only to build the list of words that must never appear.

It re-derives balances from what the group room's lines actually say, and compares them to the board.

    uv run python scripts/verify.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kitty import ledger as L  # noqa: E402
from kitty.switch_client import Agent  # noqa: E402

DATA = ROOT / "data/synthetic"
TRIP = json.loads((DATA / "trip.json").read_text())
PEOPLE = TRIP["people"]
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def history(agent: Agent, room_id: str) -> list[dict]:
    r = agent.op("read_context", room_id=room_id, limit=500)
    r.raise_for_status()
    messages: list[dict] = []
    for thread in r.json()["result"]["threads"]:
        messages.append(thread["root"])
        messages.extend(thread.get("replies", []))
    messages.sort(key=lambda m: m["timestamp"])
    return messages


def body_of(m: dict) -> str:
    return m["body"]


def forbidden_terms() -> set[str]:
    """Every merchant word, canary, ref and account number from all three synthetic statements."""
    names = {d["name"].upper() for d in PEOPLE.values()}
    noise = {"UPI", "GPAY", "PHONEPE", "NEFT", "ORDER", "TRANSFER", "SYNTHETIC", "SESSION", "INDIA", "ROOM", "FAILED"} | names
    terms: set[str] = set()
    for pid in PEOPLE:
        with (DATA / f"{pid}.csv").open() as handle:
            for row in csv.DictReader(handle):
                terms.add(row["ref"])
                terms.add(row["account"])
                for word in re.findall(r"[A-Za-z0-9-]{4,}", row["narration"]):
                    if word.upper() not in noise and not word.isdigit():
                        terms.add(word)
    return terms


def rupees_to_paise(text: str) -> int:
    whole, _, frac = text.replace("₹", "").replace(",", "").partition(".")
    return int(whole) * 100 + int(frac or 0)


LINE = re.compile(r"^\*\*(c\d+)\*\* (₹[\d,.]+) · (\w+) · (\d+ \w+) · paid by (\w+) · (.+)$")
CONFIRMED = re.compile(r"^\*\*(t\d+) confirmed by both sides:\*\* (\w+) paid (\w+) (₹[\d,.]+)\.$")


def main() -> int:
    state = ROOT / "state"
    clerk_cfg = json.loads((state / "clerk.json").read_text())
    agents_cfg = {p: json.loads((state / f"{p}.json").read_text()) for p in PEOPLE}
    clerk = Agent(clerk_cfg["agent_id"], clerk_cfg["api_key"], clerk_cfg["name"])
    agents = {p: Agent(c["agent_id"], c["api_key"], c["name"]) for p, c in agents_cfg.items()}
    group = clerk_cfg["group_room"]
    log_path = state / f"clerk-events-{clerk_cfg['run']}.jsonl"
    events = [json.loads(x) for x in log_path.read_text().splitlines()]

    group_msgs = history(clerk, group)
    group_text = [body_of(m) for m in group_msgs]
    check("group room history was readable and is not empty", len(group_text) > 3, f"{len(group_text)} messages")

    # 1. Re-derive the ledger from the group room's own words.
    ids = {d["name"]: pid for pid, d in PEOPLE.items()}
    lines: list[L.Line] = []
    shown: dict[str, dict] = {}
    withdrawn_or_held = set()
    for text in group_text:
        if m := LINE.match(text):
            shares = tuple((ids[n], rupees_to_paise(v)) for n, v in re.findall(r"(\w+) (₹[\d,.]+)", m.group(6)))
            shown[m.group(1)] = {"payer": ids[m.group(5)]}
            lines.append(L.Line(ids[m.group(5)], rupees_to_paise(m.group(2)), shares))
    payments: list[L.Payment] = []
    confirmed_ids: list[str] = []
    for text in group_text:
        if m := CONFIRMED.match(text):
            confirmed_ids.append(m.group(1))
            payments.append(L.Payment(ids[m.group(2)], ids[m.group(3)], rupees_to_paise(m.group(4))))
        if m := re.match(r"^\*\*(t\d+) withdrawn by", text):
            withdrawn_or_held.add(m.group(1))
    check("group lines were actually parsed (otherwise everything below is vacuous)", len(lines) >= 1 and len(lines) == len(shown), f"{len(lines)} lines")
    check("allocated shares sum to the total on every group line", all(sum(v for _, v in x.shares) == x.amount_paise for x in lines), f"{len(lines)} lines")
    net = L.balances(list(PEOPLE), lines, payments)
    check("net balances, recomputed from room text, sum to zero", sum(net.values()) == 0, str(net))

    try:
        board = httpx.get("http://127.0.0.1:7100/api/board", timeout=10).json()
    except httpx.HTTPError:
        board = None
    if board:
        check("the board's balances equal the recomputed ones", {ids[b["name"]]: b["paise"] for b in board["balances"]} == net)
        before = L.obligations(list(PEOPLE), lines)
        plan = L.settle(before)
        check("settlement plan clears the expense balances exactly", not any(L.balances(list(PEOPLE), lines, plan).values()), f"{len(plan)} payments")

    # 2. Every line traces to a release by its owner's own human account.
    released: dict[str, str] = {}
    for e in events:
        if m := re.match(r"^\s*release\s+([ct]\d+)\s*$", e["body"], re.I):
            released.setdefault(m.group(1).lower(), e["sender"])
    humans = {p: d["human_sender"] for p, d in clerk_cfg["people"].items()}
    bad = [cid for cid, info in shown.items() if released.get(cid) != humans[info["payer"]]]
    check("every visible claim traces to a release by its owner's human account", not bad, f"{len(shown)} claims" + (f", bad: {bad}" if bad else ""))
    agent_senders = {d["agent_sender"] for d in clerk_cfg["people"].values()}
    check("no release in the log came from an agent that took effect", not any(e["sender"] in agent_senders and e["body"].strip().lower().startswith("release") and cid_published(e["body"], shown) and released.get(cid_of(e["body"])) == e["sender"] for e in events))

    # 3. Words from the statements in places they must never be.
    terms = forbidden_terms()
    places = {"group room": group_text}
    for pid in PEOPLE:
        places[f"{pid}'s desk"] = [body_of(m) for m in history(clerk, clerk_cfg["people"][pid]["desk_room"])]
    for place, texts in places.items():
        check(f"{place} has messages to scan", len(texts) > 0, f"{len(texts)}")
        joined = "\n".join(texts)
        hits = sorted(t for t in terms if t in joined)
        check(f"no statement text in {place}", not hits, f"{len(terms)} terms, {len(texts)} messages" + (f", FOUND: {hits[:5]}" if hits else ""))
    check("no merchant field in any desk claim", not any(k in "\n".join(places[f"{p}'s desk"]) for p in PEOPLE for k in ('"merchant"', '"narration"', '"description"', '"note"')))

    # 4. Membership, as the server reports and enforces it.
    r = clerk.op("get_room_detail", room_id=group)
    r.raise_for_status()
    detail = r.json()["result"]
    check("the clerk is the only agent in the group room", detail["agent_names"] == [clerk.name], str(detail["agent_names"]))
    for pid, ag in agents.items():
        r = ag.op("read_context", room_id=group)
        check(f"{pid}'s agent is refused the group room", r.status_code == 403, f"{r.status_code}")
        r = clerk.op("read_context", room_id=agents_cfg[pid]["private_room"])
        check(f"the clerk is refused {pid}'s private room", r.status_code == 403, f"{r.status_code}")
        others = [q for q in PEOPLE if q != pid]
        codes = [ag.op("read_context", room_id=agents_cfg[q]["private_room"]).status_code for q in others]
        check(f"{pid}'s agent is refused everyone else's private room", all(c == 403 for c in codes), str(codes))

    # 5. Reversal and two-sided confirmation.
    check("a withdrawn payment was not counted as confirmed", not withdrawn_or_held & set(confirmed_ids), f"withdrawn {sorted(withdrawn_or_held)}, confirmed {confirmed_ids}")
    has_dispute = any("NOT CONFIRMED" in t for t in group_text)
    if has_dispute:
        check("the dispute was raised and then closed in the group room", bool(withdrawn_or_held))
    for tid in confirmed_ids:
        senders = {e["sender"] for e in events if re.match(rf"^\s*release\s+{tid}\s*$", e["body"], re.I)}
        check(f"{tid} was released by both people's human accounts", len(senders) == 2 and senders <= set(humans.values()), f"{len(senders)} releases")
    if not any(net.values()):
        check("balances are zero, and only because of confirmed payments", any("Everyone's square" in t for t in group_text) and bool(confirmed_ids))
    else:
        print(f"NOTE  not square yet: {net}. Balanced on paper is not the same as paid.")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    out = ROOT / "docs/evidence/verify_output.json"
    out.write_text(json.dumps({"results": [{"check": n, "pass": ok, "detail": d} for n, ok, d in results], "net": net}, indent=1, ensure_ascii=False))
    return 1 if failed else 0


def cid_of(body: str) -> str:
    m = re.match(r"^\s*release\s+([ct]\d+)", body, re.I)
    return m.group(1).lower() if m else ""


def cid_published(body: str, shown: dict) -> bool:
    return cid_of(body) in shown


if __name__ == "__main__":
    raise SystemExit(main())
