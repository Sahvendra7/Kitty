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
from kitty import claims as C  # noqa: E402
from kitty import ledger as L  # noqa: E402
from kitty import paths as P  # noqa: E402
from kitty import statements as S  # noqa: E402
from kitty.switch_client import Agent, Mattermost  # noqa: E402

# A trip made in the app: KITTY_TRIP_DIR=trips/<id> uv run python scripts/verify.py
WHERE = P.current()
DATA = ROOT / "data/synthetic"
TRIP = json.loads(WHERE.trip_json.read_text())
PEOPLE = TRIP["people"]
POT = TRIP.get("treasurer")  # Set on a Pot trip only. A trip without one runs exactly the checks it always did.
results: list[tuple[str, bool, str]] = []


PEOPLE_IDS = {d["name"]: pid for pid, d in PEOPLE.items()}


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


REFUSED_AGAIN = "refused repeatedly"


def status_of(agent: Agent, room_id: str) -> str:
    """How the server answered. After several 403s it answers 429 and says so, and retrying only extends that.

    That reply carries no room data, and it says the same request was already refused, so it counts as a
    refusal and is shown as its own value. Any other 429 proves nothing, and fails the check.
    """
    r = agent.op("read_context", room_id=room_id)
    if r.status_code == 429 and REFUSED_AGAIN in r.text and "threads" not in r.text:
        return "403-again"
    return str(r.status_code)


def refused(code: str) -> bool:
    return code in ("403", "403-again")


def body_of(m: dict) -> str:
    return m["body"]


def forbidden_terms() -> set[str]:
    """Every merchant word, canary, ref and account number from all three synthetic statements."""
    names = {d["name"].upper() for d in PEOPLE.values()}
    noise = {"UPI", "GPAY", "PHONEPE", "NEFT", "ORDER", "TRANSFER", "SYNTHETIC", "SESSION", "INDIA", "ROOM", "FAILED"} | names
    terms: set[str] = set()
    for pid in PEOPLE:
        path = WHERE.statement(pid) if WHERE.in_app else DATA / f"{pid}.csv"
        if POT and not path.exists():  # On a Pot trip a member may have typed their claims instead.
            continue
        with path.open() as handle:
            for row in csv.DictReader(handle):
                terms.add(row["ref"])
                terms.add(row["account"])
                if POT and pid == POT and row["balance"]:
                    terms.update((row["balance"], C.money(S.to_paise(row["balance"]))))
                for word in re.findall(r"[A-Za-z0-9-]{4,}", row["narration"]):
                    if word.upper() not in noise and not word.isdigit():
                        terms.add(word)
    return terms


def rupees_to_paise(text: str) -> int:
    whole, _, frac = re.sub(r"[₹$€£,]", "", text).partition(".")
    return int(whole) * 100 + int(frac or 0)


LINE = re.compile(r"^\*\*(c\d+)\*\* ([₹$€£][\d,.]+) · (\w+) · (\d+ \w+) · paid by (\w+) · (.+)$")
CONFIRMED = re.compile(r"^\*\*(t\d+) confirmed by both sides:\*\* (\w+) paid (\w+) ([₹$€£][\d,.]+)\.$")
CONFLICT = re.compile(r"^\*\*Possible conflict:\*\* (c\d+) and (c\d+) ")
CLEARED = re.compile(r"^Conflict cleared: \w+ withdrew (c\d+)\. \*\*(c\d+)\*\* now counts: (.+)$")
SHARE = re.compile(r"(\w+) ([₹$€£][\d,]*\d\.\d{2})")


def board_url() -> str:
    if WHERE.in_app:
        return f"http://127.0.0.1:{json.loads((WHERE.state / 'runtime.json').read_text())['board']}/api/board"
    return "http://127.0.0.1:7100/api/board"


def main() -> int:
    state = WHERE.state
    clerk_cfg = json.loads((state / "clerk.json").read_text())
    agents_cfg = {p: json.loads((state / f"{p}.json").read_text()) for p in PEOPLE}
    clerk = Agent(clerk_cfg["agent_id"], clerk_cfg["api_key"], clerk_cfg["name"])
    agents = {p: Agent(c["agent_id"], c["api_key"], c["name"]) for p, c in agents_cfg.items()}
    group = clerk_cfg["group_room"]
    log_path = state / f"clerk-events-{clerk_cfg['run']}.jsonl"
    events = [json.loads(x) for x in log_path.read_text().splitlines()]

    group_msgs = history(clerk, group)
    group_text = [body_of(m) for m in group_msgs]
    # On a Pot trip the ledger is read from the clerk's own posts. A person can type anything in the group room.
    clerk_sender = next(iter(agents_cfg.values()))["clerk_sender"] if POT else None
    counted_text = [body_of(m) for m in group_msgs if m["sender"] == clerk_sender] if POT else group_text
    check("group room history was readable and is not empty", len(group_text) > 3, f"{len(group_text)} messages")

    # 1. Re-derive the ledger from the group room's own words.
    ids = {d["name"]: pid for pid, d in PEOPLE.items()}
    counted: dict[str, L.Line] = {}
    shown: dict[str, dict] = {}
    withdrawn_or_held = set()
    conflicts: list[tuple[str, str]] = []
    cleared: list[tuple[str, str]] = []

    def count(text: str) -> str | None:
        m = LINE.match(text)
        if not m:
            return None
        shares = tuple((ids[n], rupees_to_paise(v)) for n, v in SHARE.findall(m.group(6)))
        shown[m.group(1)] = {"payer": ids[m.group(5)]}
        counted[m.group(1)] = L.Line(ids[m.group(5)], rupees_to_paise(m.group(2)), shares)
        return m.group(1)

    # In the order the room shows them, because a conflict takes a line back out.
    for text in counted_text:
        if count(text):
            continue
        if m := CONFLICT.match(text):
            conflicts.append((m.group(1), m.group(2)))
            for cid in m.groups():
                counted.pop(cid, None)
                shown.pop(cid, None)
        elif m := CLEARED.match(text):
            kept = count(f"**{m.group(2)}** {m.group(3)}")
            cleared.append((m.group(1), kept or ""))
    lines = list(counted.values())
    payments: list[L.Payment] = []
    confirmed_ids: list[str] = []
    for text in counted_text:
        if m := CONFIRMED.match(text):
            confirmed_ids.append(m.group(1))
            payments.append(L.Payment(ids[m.group(2)], ids[m.group(3)], rupees_to_paise(m.group(4))))
        if m := re.match(r"^\*\*(t\d+) withdrawn by", text):
            withdrawn_or_held.add(m.group(1))
    room = None
    if POT:
        from kitty.pot import parse_room

        room = parse_room(counted_text, ids)
        payments.extend(room.payments)
    check("group lines were actually parsed (otherwise everything below is vacuous)", len(lines) >= 1 and len(lines) == len(shown), f"{len(lines)} lines")
    check("allocated shares sum to the total on every group line", all(sum(v for _, v in x.shares) == x.amount_paise for x in lines), f"{len(lines)} lines")
    net = L.balances(list(PEOPLE), lines, payments)
    check("net balances, recomputed from room text, sum to zero", sum(net.values()) == 0, str(net))

    try:
        board = httpx.get(board_url(), timeout=10).json()
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

    if conflicts:
        drops = {m.group(1).lower(): e["sender"] for e in events if (m := re.match(r"^\s*drop\s+(c\d+)\s*$", e["body"], re.I))}
        check("every conflict was cleared in the group room", len(conflicts) == len(cleared), f"{len(conflicts)} raised, {len(cleared)} cleared")
        check("a conflicted expense counts exactly once", all(kept in counted and dropped not in counted for dropped, kept in cleared), str(cleared))
        check("each dropped claim was dropped by a person's own human account", all(drops.get(dropped) in humans.values() for dropped, _ in cleared), f"{len(cleared)} drops")

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
        code = status_of(ag, group)
        check(f"{pid}'s agent is refused the group room", refused(code), code)
        code = status_of(clerk, agents_cfg[pid]["private_room"])
        check(f"the clerk is refused {pid}'s private room", refused(code), code)
        others = [q for q in PEOPLE if q != pid]
        codes = [status_of(ag, agents_cfg[q]["private_room"]) for q in others]
        check(f"{pid}'s agent is refused everyone else's private room", all(refused(c) for c in codes), str(codes))

    # Membership again, this time as Mattermost reports it, seen by each person's own session.
    def mm_members(person: str, room: str) -> set[str]:
        login = agents_cfg[person]["mattermost"]
        mm = Mattermost(login["username"], login["password"])
        r = mm.http.get(f"/api/v4/channels/{login['channels'][room]}/members", params={"per_page": 200})
        r.raise_for_status()
        ids = [m["user_id"] for m in r.json()]
        return {u["username"] for u in mm.http.post("/api/v4/users/ids", json=ids).json()}

    group_members = mm_members(next(iter(PEOPLE)), "group")
    personal_agents = {c["name"] for c in agents_cfg.values()}
    check("no personal agent's bot is in the group's Mattermost channel", not {u for u in group_members if any(a in u for a in personal_agents)} and clerk.name in group_members, f"{sorted(group_members)}")
    for pid in PEOPLE:
        members = mm_members(pid, "private")
        check(f"the clerk's bot is not in {pid}'s private Mattermost channel", clerk.name not in members and agents_cfg[pid]["name"] in members, f"{sorted(members)}")

    # 5. Reversal and two-sided confirmation.
    check("a withdrawn payment was not counted as confirmed", not withdrawn_or_held & set(confirmed_ids), f"withdrawn {sorted(withdrawn_or_held)}, confirmed {confirmed_ids}")
    has_dispute = any("NOT CONFIRMED" in t for t in counted_text)
    if has_dispute:
        check("the dispute was raised and then closed in the group room", bool(withdrawn_or_held))
    for tid in confirmed_ids:
        senders = {e["sender"] for e in events if re.match(rf"^\s*release\s+{tid}\s*$", e["body"], re.I)}
        check(f"{tid} was released by both people's human accounts", len(senders) == 2 and senders <= set(humans.values()), f"{len(senders)} releases")
    if not any(net.values()):
        check("balances are zero, and only because of confirmed payments", any("Everyone's square" in t for t in counted_text) and bool(confirmed_ids or (room and room.payments)))
    else:
        print(f"NOTE  not square yet: {net}. Balanced on paper is not the same as paid.")

    if POT:
        pot_checks(room, counted_text, events, clerk_cfg, agents, agents_cfg, board, net, lines, terms)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    out = WHERE.logs / "verify_output.json" if WHERE.in_app else ROOT / "docs/evidence/verify_output.json"
    out.write_text(json.dumps({"results": [{"check": n, "pass": ok, "detail": d} for n, ok, d in results], "net": net}, indent=1, ensure_ascii=False))
    return 1 if failed else 0


def pot_checks(room, counted_text, events, clerk_cfg, agents, agents_cfg, board, net, lines, terms) -> None:
    """A Pot trip's own promises, checked from the room's text, the log and the live server."""
    from kitty.clerk_daemon import apply_event, build_clerk
    from kitty.pot import chain_check

    humans = {p: d["human_sender"] for p, d in clerk_cfg["people"].items()}
    desk = clerk_cfg["people"][POT]["desk_room"]
    agent_senders = {d["agent_sender"] for d in clerk_cfg["people"].values()}
    confirmed = [cid for cid, c in room.contributions.items() if c["status"] == "confirmed"]
    check("pot: contributions were actually read from the room", bool(room.contributions) and bool(confirmed), f"{len(room.contributions)} read, {len(confirmed)} confirmed")

    if board:
        check("pot: balances recomputed from room text equal the board", {PEOPLE_IDS[b["name"]]: b["paise"] for b in board["balances"]} == net)
        shares = sum(dict(x.shares).get(POT, 0) for x in lines)
        check("pot: board pot position equals -net[treasurer] - treasurer's shares", board["pot"]["position"] == -net[POT] - shares, f"{board['pot']['position']}")
        check("pot: the board's roll-call equals the room's asked amounts", all(r["asked"] == room.asked[PEOPLE_IDS[r["name"]]] for r in board["pot"]["roll"]))
        check("pot: every hub plan payment involves the treasurer", all(PEOPLE[POT]["name"] in (p["from"], p["to"]) for p in board["plan"]), f"{len(board['plan'])} payments")

    def decided_by_treasurer(cid: str) -> bool:
        last = [e for e in events if re.match(rf"^\s*(confirm\s+{cid}(\s+manual)?|correct\s+{cid}\s+received)\s*$", e["body"], re.I)]
        return bool(last) and any(e["room"] == desk and e["sender"] == humans[POT] for e in last)

    check("pot: every confirmed contribution traces to the treasurer's account on the treasurer's desk", all(decided_by_treasurer(c) for c in confirmed), f"{len(confirmed)} confirmed")

    # Rebuild the book from the log alone, so nothing here trusts the running clerk.
    rebuilt = build_clerk(clerk_cfg, TRIP)
    for event in events:
        apply_event(rebuilt, event)
    book = rebuilt.pot
    actors = {d.actor for d in book.decisions if d.kind not in ("match",)}
    check("pot: no decision command from any agent took effect", not actors & agent_senders, f"{len(book.decisions)} decisions")
    # The room can't show a private step, such as a claim under review, so what's compared is whether it counts.
    check("pot: rebuilt from the log, every contribution counts or not as the room shows", all((book.status(cid) == "confirmed") == (c["status"] == "confirmed") for cid, c in room.contributions.items()), f"{len(room.contributions)} compared")
    tokens = [r.credit_token for r in book.records.values() if book.status(r.id) == "confirmed" and r.credit_token]
    check("pot: no credit token was confirmed twice", len(tokens) == len(set(tokens)), f"{len(tokens)} tokens")

    tagged = [t for t in counted_text if re.search(r" \[d\d+ [0-9a-f]{12}\]$", t)]
    broken = chain_check(counted_text)
    check("pot: decision chain is unbroken", bool(tagged) and broken is None, f"{len(tagged)} decisions" + (f", broken at {broken}" if broken else ""))

    statement_refs = {t for t in terms if re.fullmatch(r"[A-Z]\d{5,}", t)}
    rooms = [*counted_text, *(m["body"] for p in PEOPLE for m in history(agents[p], clerk_cfg["people"][p]["desk_room"]))]
    check("pot: no full statement reference in any room the clerk can read", bool(statement_refs) and not any(r in t for r in statement_refs for t in rooms), f"{len(statement_refs)} refs")
    digits = [t for t in tagged if re.search(r"\d{5,}", re.sub(r" \[d\d+ [0-9a-f]{12}\]$", "", t))]
    check("pot: no run of 5 or more digits in a group pot line", not digits, str(digits[:2]) if digits else "")

    private = "\n".join(m["body"] for m in history(agents[POT], agents_cfg[POT]["private_room"]))
    leaks = [w for w in ("SALARY", "LANDLORD", "NEHACANARY", "UPI/CR", "UPI/DR") if w in private]
    check("pot: the treasurer's private chits carry no narration", bool(private) and not leaks, str(leaks) if leaks else f"{len(private)} characters")

    clerk_text = (WHERE.state / "clerk.json").read_text()
    keys = {agents_cfg[p].get("pot_key") for p in PEOPLE}
    check("pot: clerk.json holds no pot_key, and each person's file does", "pot_key" not in clerk_text and len(keys) == 1 and None not in keys)

    login = agents_cfg[POT]["mattermost"]
    session = Mattermost(login["username"], login["password"])
    codes = {p: session.channel(agents_cfg[p]["mattermost"]["channels"]["private"]).status_code for p in PEOPLE if p != POT}
    check("pot: the treasurer is refused every other person's private room", all(c in (403, 404) for c in codes.values()), str(codes))
    own = session.channel(login["channels"]["private"]).status_code
    check("pot: control, the treasurer can open their own private room", own == 200, str(own))


def cid_of(body: str) -> str:
    m = re.match(r"^\s*release\s+([ct]\d+)", body, re.I)
    return m.group(1).lower() if m else ""


def cid_published(body: str, shown: dict) -> bool:
    return cid_of(body) in shown


if __name__ == "__main__":
    raise SystemExit(main())
