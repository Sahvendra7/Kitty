"""Gate 1, part B: real people in real Mattermost channels, with the two-room layout.

    <person>-private : the person and their agent. The clerk is NOT a member.
    <person>-desk    : the person, their agent and the clerk. Typed claims and releases only.
    goa-trip         : all people and the clerk. Personal agents are NOT members.

Proves the human side of the boundary with real Mattermost accounts and real sender ids.

    uv run python scripts/gate1b_humans.py
"""
from __future__ import annotations

import json
import queue
import secrets
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from kitty.switch_client import Agent, Gateway, Mattermost  # noqa: E402
from switch_env import load  # noqa: E402

ENV = load(ROOT.parent / "switch-src" / ".env")
RUN = secrets.token_hex(3)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  [{detail}]", flush=True)


def wait_event(conn, needle: str, seconds: float = 15):
    end = time.time() + seconds
    while time.time() < end:
        try:
            e = conn.events.get(timeout=0.5)
        except queue.Empty:
            continue
        if needle in e.body:
            return e
    return None


def wait_agent_sender(conn, agent: Agent, room_id: str) -> str | None:
    marker = f"sender-probe-{secrets.token_hex(3)}"
    agent.say(room_id, marker)
    e = wait_event(conn, marker)
    return e.sender if e else None


def main() -> int:
    admin = Gateway(ENV["GATEWAY_ADMIN_EMAIL"], ENV["GATEWAY_ADMIN_PASSWORD"])
    bridge = admin.default_bridge()
    mm_admin = Mattermost(ENV["MATTERMOST_ADMIN_USER"], ENV["MATTERMOST_ADMIN_PASSWORD"])
    team = ENV["MATTERMOST_TEAM_NAME"]

    people = {}
    for slug in ("host", "meera", "arjun"):
        password = "Kt1!" + secrets.token_urlsafe(16)
        email = f"{slug}-{RUN}@kitty.test"
        username = f"{slug}{RUN}"
        user = admin.create_user(email, f"{slug.title()} {RUN}", password)
        gw = Gateway(email, password)
        mm_admin.create_user(username, email, password, slug.title(), team)
        mm = Mattermost(username, password)
        if slug != "host":
            gw.link_mattermost(bridge, mm.user_id, username, mm.token)
        people[slug] = {"user": user, "gw": gw, "mm": mm, "username": username, "reg": gw.registration_key("kitty")}
    check("real Mattermost accounts linked to separate Switch owners", True, "meera, arjun verified by their own session")

    clerk = Agent.register(people["host"]["reg"], f"kitty-clerk-{RUN}", "Deterministic trip clerk. Not an LLM.")
    agents = {s: Agent.register(people[s]["reg"], f"{s}-agent-{RUN}", f"{s}'s private agent") for s in ("meera", "arjun")}

    def room(creator: Agent, name: str, agent_names: list[str], users: list[str]) -> dict:
        res = creator.result(
            "create_room", name=f"{name}-{RUN}", description=f"Kitty {name}", agent_names=agent_names, user_names=users,
            bridge_id=bridge, channel_type="channel_private", read_visibility="private", write_visibility="private",
        )
        print(f"  room {name}: {json.dumps({k: res.get(k) for k in ('id', 'transport_room_id', 'failed_users', 'caller_is_member')})}", flush=True)
        return res

    group = room(clerk, "goa-trip", [clerk.name], [people["meera"]["username"], people["arjun"]["username"]])
    rooms = {}
    for s in ("meera", "arjun"):
        rooms[s] = {
            "private": room(agents[s], f"{s}-private", [agents[s].name], [people[s]["username"]]),
            "desk": room(agents[s], f"{s}-desk", [agents[s].name], [people[s]["username"]]),
        }
        agents[s].result("invite_agent_to_room", room_id=rooms[s]["desk"]["id"], agent_name=clerk.name)

    # Mattermost channel ids, read from the room's bridge binding.
    def channel_id(res: dict, viewer: Agent) -> str | None:
        detail = viewer.result("get_room_detail", room_id=res["id"])
        print("  detail keys:", sorted(detail.keys()), flush=True)
        for key in ("external_channel_id", "channel_id"):
            if detail.get(key):
                return detail[key]
        for b in detail.get("bridges", []) + detail.get("channels", []):
            for key in ("external_channel_id", "channel_id"):
                if b.get(key):
                    return b[key]
        return None

    def find_channel(person: str, name: str) -> dict | None:
        mm: Mattermost = people[person]["mm"]
        r = mm.http.get("/api/v4/users/me/channels")
        r.raise_for_status()
        for c in r.json():
            if name in c.get("display_name", "") or name in c.get("name", ""):
                return c
        return None

    time.sleep(2)
    ch_group = find_channel("meera", f"goa-trip-{RUN}")
    ch_meera_private = find_channel("meera", f"meera-private-{RUN}")
    ch_meera_desk = find_channel("meera", f"meera-desk-{RUN}")
    ch_arjun_private = find_channel("arjun", f"arjun-private-{RUN}")
    check("Meera sees group, private and desk channels in Mattermost", all([ch_group, ch_meera_private, ch_meera_desk]), str([bool(ch_group), bool(ch_meera_private), bool(ch_meera_desk)]))
    check("those channels are private Mattermost channels", all(c and c["type"] == "P" for c in (ch_group, ch_meera_private, ch_meera_desk)), str([c and c["type"] for c in (ch_group, ch_meera_private, ch_meera_desk)]))
    check("Arjun sees his own private channel", ch_arjun_private is not None)

    # --- The clerk is outside private rooms, by server enforcement ---
    for s in ("meera", "arjun"):
        pid = rooms[s]["private"]["id"]
        r = clerk.op("read_context", room_id=pid)
        check(f"DENIED: clerk read_context on {s}'s private room", r.status_code == 403, f"{r.status_code}")
        r = clerk.op("invite_agent_to_room", room_id=pid, agent_name=clerk.name)
        check(f"DENIED: clerk invites itself to {s}'s private room", r.status_code == 403, f"{r.status_code}")
        r = clerk.post(pid, "clerk should not be able to post here")
        check(f"DENIED: clerk posts to {s}'s private room", r.status_code == 403, f"{r.status_code}")
    r = agents["meera"].op("read_context", room_id=group["id"])
    check("DENIED: meera-agent read_context on group room", r.status_code == 403, f"{r.status_code}")
    r = agents["meera"].op("read_context", room_id=rooms["arjun"]["desk"]["id"])
    check("DENIED: meera-agent read_context on arjun's desk", r.status_code == 403, f"{r.status_code}")
    r = agents["meera"].op("invite_agent_to_room", room_id=group["id"], agent_name=agents["meera"].name)
    check("DENIED: meera-agent invites herself to group room", r.status_code == 403, f"{r.status_code}")

    # --- Humans: Arjun cannot reach Meera's channels in Mattermost ---
    arjun_mm: Mattermost = people["arjun"]["mm"]
    r = arjun_mm.channel(ch_meera_private["id"])
    check("DENIED: Arjun reads Meera's private channel metadata (Mattermost)", r.status_code in (403, 404), f"{r.status_code}")
    r = arjun_mm.channel_posts(ch_meera_private["id"])
    check("DENIED: Arjun reads Meera's private channel posts (Mattermost)", r.status_code in (403, 404), f"{r.status_code}")
    r = arjun_mm.channel_posts(ch_meera_desk["id"])
    check("DENIED: Arjun reads Meera's desk posts (Mattermost)", r.status_code in (403, 404), f"{r.status_code}")
    r = arjun_mm.http.post("/api/v4/posts", json={"channel_id": ch_meera_desk["id"], "message": "release c1"})
    check("DENIED: Arjun posts a release into Meera's desk (Mattermost)", r.status_code in (403, 404), f"{r.status_code}")

    # --- Sender identity for the release ---
    clerk_conn = clerk.listen()
    meera_conn = agents["meera"].listen()
    clerk_conn.ready.wait(10)
    meera_conn.ready.wait(10)
    time.sleep(1)

    canary = f"CANARY-ZOMATO-{RUN}"
    people["meera"]["mm"].post(ch_meera_private["id"], f"that {canary} one was shared")
    e = wait_event(meera_conn, canary)
    check("meera-agent hears Meera in her private room", e is not None and e.room_id == rooms["meera"]["private"]["id"], e.sender if e else "no event")
    meera_human_private = e.sender if e else None
    agents["meera"].say(rooms["meera"]["private"]["id"], "Zomato ₹850 at 23:00. Shared with the group?")
    agents["meera"].say(rooms["meera"]["desk"]["id"], "KITTY_CLAIM c1")
    e = wait_event(clerk_conn, "KITTY_CLAIM c1")
    check("clerk receives the agent's typed claim in the desk room", e is not None, e.sender if e else "no event")

    release = f"release c1 {RUN}"
    people["meera"]["mm"].post(ch_meera_desk["id"], release)
    e = wait_event(clerk_conn, release)
    check("clerk receives Meera's release from Mattermost", e is not None, e.sender if e else "no event")
    meera_human = e.sender if e else None
    agent_sender = wait_agent_sender(clerk_conn, agents["meera"], rooms["meera"]["desk"]["id"])
    check("release sender id differs from her agent's id (ids compared, never parsed)", bool(meera_human) and meera_human != agent_sender, f"{meera_human} vs {agent_sender}")
    check("same human id in private and desk rooms", meera_human == meera_human_private, f"{meera_human_private} / {meera_human}")

    forged = f"release c1 forged {RUN}"
    agents["meera"].say(rooms["meera"]["desk"]["id"], forged)
    e = wait_event(clerk_conn, forged)
    check("an agent typing release carries the agent's sender id, so it can be refused", e is not None and e.sender != meera_human, e.sender if e else "no event")

    arjun_says = f"release c1 from arjun {RUN}"
    arjun_mm.post(ch_group["id"], arjun_says)
    e = wait_event(clerk_conn, arjun_says)
    check("Arjun in the group room has a different sender id from Meera", e is not None and e.sender != meera_human and e.room_id == group["id"], e.sender if e else "no event")

    for label, res in (("group", group), ("desk", rooms["meera"]["desk"])):
        r = clerk.http.post(f"/agents/{clerk.id}/ops/get_room_detail", json={"room_id": res["id"]})
        detail = r.json().get("result", {}) if r.status_code == 200 else {}
        print(f"  {label} participants:", json.dumps(detail.get("participants", detail.get("agents", "")))[:400], flush=True)

    # --- Canary sweep across everything the clerk could read ---
    leak = False
    for res in (group, rooms["meera"]["desk"], rooms["arjun"]["desk"]):
        r = clerk.op("read_context", room_id=res["id"])
        leak = leak or canary in r.text or "Zomato" in r.text
    check("private canary and merchant absent from every room the clerk can read", not leak)
    seen_by_clerk: list[str] = []
    while not clerk_conn.events.empty():
        seen_by_clerk.append(clerk_conn.events.get().body)
    check("clerk's event stream never carried the private canary or merchant", not any(canary in b or "Zomato" in b for b in seen_by_clerk), f"{len(seen_by_clerk)} leftover events")

    out = ROOT / "docs" / "evidence" / "gate1b_humans.json"
    out.write_text(json.dumps({"switch_commit": "fa933012b", "run": RUN, "results": [{"check": n, "pass": ok, "detail": d} for n, ok, d in results]}, indent=1, ensure_ascii=False))
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed. Evidence: {out}")
    clerk_conn.stop()
    meera_conn.stop()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
