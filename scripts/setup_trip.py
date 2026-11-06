"""One command: real users, real agents, real rooms. Safe to re-run; each run gets a new suffix.

    uv run python scripts/setup_trip.py

Every person ends up as:
  - a Switch owner (non-admin) with their own registration key,
  - a Mattermost account linked to that owner by the account's own session,
  - an agent registered with their key,
  - a private room (person + agent) and a desk (person + agent + clerk).

The clerk belongs to a neutral non-admin host user and is the only agent in the group room.

Credentials go to state/, one file per process, mode 600. Rooms are never deleted (Switch issue
#1202). `reset` is just running this again.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from kitty.switch_client import Agent, Gateway, Mattermost  # noqa: E402
from switch_env import load  # noqa: E402

ENV = load(Path(os.environ.get("SWITCH_SRC", ROOT.parent / "switch-src")) / ".env")
TRIP = json.loads((ROOT / "data/synthetic/trip.json").read_text())
STATE = ROOT / "state"


def write_private(name: str, doc: dict) -> None:
    path = STATE / name
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    os.chmod(path, 0o600)


def await_sender(conn, needle: str, seconds: float = 20) -> str:
    end = time.time() + seconds
    while time.time() < end:
        try:
            e = conn.events.get(timeout=0.5)
        except queue.Empty:
            continue
        if needle in e.body:
            return e.sender
    raise RuntimeError(f"never saw {needle!r}. Is the Mattermost bridge healthy?")


def main() -> int:
    STATE.mkdir(exist_ok=True)
    run = secrets.token_hex(2)
    admin = Gateway(ENV["GATEWAY_ADMIN_EMAIL"], ENV["GATEWAY_ADMIN_PASSWORD"])
    bridge = admin.default_bridge()
    mm_admin = Mattermost(ENV["MATTERMOST_ADMIN_USER"], ENV["MATTERMOST_ADMIN_PASSWORD"])
    team = ENV["MATTERMOST_TEAM_NAME"]
    # Mattermost's default is 50 members per team, and each run adds eight accounts. Past the limit,
    # agent bots silently fail to join channels and nothing is delivered (found the hard way).
    r = mm_admin.http.put("/api/v4/config/patch", json={"TeamSettings": {"MaxUsersPerTeam": 2000}})
    r.raise_for_status()

    def person(slug: str, display: str, linked: bool = True) -> dict:
        password = "Kt1!" + secrets.token_urlsafe(16)
        email = f"{slug}-{run}@kitty.test"
        username = f"{slug}{run}"
        user = admin.create_user(email, display, password)
        gateway = Gateway(email, password)
        mm_admin.create_user(username, email, password, display, team)
        mm = Mattermost(username, password)
        if linked:
            gateway.link_mattermost(bridge, mm.user_id, username, mm.token)
        return {"slug": slug, "name": display, "email": email, "username": username, "password": password, "user": user, "gw": gateway, "mm": mm, "reg": gateway.registration_key("kitty")}

    print(f"run suffix: {run}")
    host = person("kittyhost", "Kitty Host", linked=False)
    people = {slug: person(slug, doc["name"]) for slug, doc in TRIP["people"].items()}
    print("owners and Mattermost accounts ready:", ", ".join(p["username"] for p in people.values()))

    clerk = Agent.register(host["reg"], f"kitty-clerk-{run}", "The trip's clerk. Ordinary code, not an LLM. Holds claims until their owner releases them, and does the maths.", "Kitty Clerk")
    agents = {slug: Agent.register(p["reg"], f"{slug}-agent-{run}", f"{p['name']}'s private agent.", f"{p['name']}'s agent") for slug, p in people.items()}

    def room(creator: Agent, name: str, description: str, members: list[str], users: list[str]) -> str:
        res = creator.result("create_room", name=f"{name}-{run}", description=description, agent_names=members, user_names=users, bridge_id=bridge, channel_type="channel_private", read_visibility="private", write_visibility="private")
        if res.get("failed_users"):
            raise RuntimeError(f"{name}: could not add {res['failed_users']}")
        return res["id"]

    group = room(clerk, "goa-trip", "The trip's group room. Only owner-released claims appear here.", [clerk.name], [p["username"] for p in people.values()])
    rooms = {}
    for slug, p in people.items():
        rooms[slug] = {
            "private": room(agents[slug], f"{slug}-private", f"{p['name']} and their agent. Merchant names stay here.", [agents[slug].name], [p["username"]]),
            "desk": room(agents[slug], f"{slug}-desk", f"Typed claims wait here until {p['name']} releases them.", [agents[slug].name], [p["username"]]),
        }
        agents[slug].result("invite_agent_to_room", room_id=rooms[slug]["desk"], agent_name=clerk.name)
    print("rooms ready: 1 group, and a private room and a desk for each person")

    time.sleep(3)

    def channel(mm: Mattermost, name: str) -> str:
        r = mm.http.get("/api/v4/users/me/channels")
        r.raise_for_status()
        for c in r.json():
            if f"{name}-{run}" in (c.get("display_name", ""), c.get("name", "")) or f"{name}-{run}" in c.get("display_name", ""):
                return c["id"]
        raise RuntimeError(f"no Mattermost channel for {name}")

    channels = {slug: {"private": channel(p["mm"], f"{slug}-private"), "desk": channel(p["mm"], f"{slug}-desk"), "group": channel(p["mm"], "goa-trip")} for slug, p in people.items()}

    # Learn each server-set sender id by observing a real post. Ids are compared, never parsed.
    clerk_conn = clerk.listen()
    clerk_conn.ready.wait(15)
    agent_conns = {slug: a.listen() for slug, a in agents.items()}
    for c in agent_conns.values():
        c.ready.wait(15)
    time.sleep(1)
    senders: dict[str, dict] = {}
    for slug, p in people.items():
        hello = f"Hi, it's {p['name']}. [{secrets.token_hex(3)}]"
        p["mm"].post(channels[slug]["desk"], hello)
        human = await_sender(clerk_conn, hello)
        probe = f"{p['name']}'s agent checking in. [{secrets.token_hex(3)}]"
        agents[slug].say(rooms[slug]["desk"], probe)
        agent_sender = await_sender(clerk_conn, probe)
        senders[slug] = {"human": human, "agent": agent_sender}
    probe = "Kitty clerk here. Claims wait on this desk until you release them."
    for slug in people:
        clerk.say(rooms[slug]["desk"], probe)
    clerk_sender = await_sender(agent_conns[next(iter(people))], "Kitty clerk here")
    for c in (clerk_conn, *agent_conns.values()):
        c.stop()

    clerk.say(group, f"Kitty is open for **{TRIP['name']}**. Only claims their owner has released appear here, and they carry a date, an amount, a category and who shared it. Say `balances`, `settle` or `status`. All data in this demo is synthetic.")

    write_private("clerk.json", {
        "agent_id": clerk.id, "api_key": clerk.key, "name": clerk.name, "group_room": group,
        "people": {slug: {"id": slug, "name": p["name"], "human_sender": senders[slug]["human"], "agent_sender": senders[slug]["agent"], "desk_room": rooms[slug]["desk"]} for slug, p in people.items()},
        "run": run,
    })
    for slug, p in people.items():
        write_private(f"{slug}.json", {
            "me": slug, "agent_id": agents[slug].id, "api_key": agents[slug].key, "name": agents[slug].name,
            "private_room": rooms[slug]["private"], "desk_room": rooms[slug]["desk"],
            "owner_sender": senders[slug]["human"], "clerk_sender": clerk_sender,
            "mattermost": {"username": p["username"], "password": p["password"], "channels": channels[slug]},
            "run": run,
        })
    write_private("verify.json", {"run": run, "agents": {slug: agents[slug].name for slug in people}, "private_rooms": {slug: rooms[slug]["private"] for slug in people}})
    for slug in people:
        folder = ROOT / "people" / slug
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "data/synthetic" / f"{slug}.csv", folder / "statement.csv")
    print(f"\nDone. Mattermost: http://localhost:8065  Team: {team}")
    print("Logins are in state/<person>.json. Next: uv run python scripts/run_all.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
