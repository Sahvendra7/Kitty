"""Gate 1, part A: do Switch's rooms actually isolate separately owned agents?

Everything here runs against a live switch-core using non-admin owners. Each check
records the real HTTP status. Nothing is inferred from prompts or from the UI.

    uv run python scripts/gate1_spike.py
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

sys.path.insert(0, str(Path(__file__).parent))
from switch_env import load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENV = load(ROOT.parent / "switch-src" / ".env")
BASE = "http://localhost:8000"
RUN = secrets.token_hex(3)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  [{detail}]", flush=True)


def gateway_login(email: str, password: str) -> httpx.Client:
    client = httpx.Client(base_url=BASE, timeout=30)
    r = client.post("/gateway/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return client


class Owner:
    """A real Switch user, with their own session and registration key."""

    def __init__(self, admin: httpx.Client, slug: str) -> None:
        self.email = f"{slug}-{RUN}@kitty.test"
        self.password = secrets.token_urlsafe(18)
        r = admin.post("/gateway/users", json={"email": self.email, "name": f"{slug}-{RUN}", "password": self.password, "role": "user"})
        r.raise_for_status()
        self.user = r.json()
        self.session = gateway_login(self.email, self.password)
        r = self.session.post("/gateway/api-keys", json={"label": "kitty-gate1"})
        r.raise_for_status()
        self.reg_key = r.json()["key"]


class Agent:
    def __init__(self, owner: Owner, slug: str, model: str = "always_on") -> None:
        self.owner = owner
        self.name = f"{slug}-{RUN}"
        r = httpx.post(
            f"{BASE}/agents",
            headers={"Authorization": f"Bearer {owner.reg_key}"},
            json={
                "name": self.name,
                "description": f"Kitty gate 1 {slug}",
                "connector_type": "external",
                "integration_profile": {
                    "connection_model": model,
                    "message_exchange": True,
                    "pre_invocation_mediation": [],
                    "post_invocation_mediation": [],
                    "event_reporting": [],
                },
            },
            timeout=30,
        )
        r.raise_for_status()
        self.id = r.json()["id"]
        self.key = r.json()["api_key"]
        self.http = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {self.key}"}, timeout=30)

    def op(self, operation: str, /, **args) -> httpx.Response:
        return self.http.post(f"/agents/{self.id}/ops/{operation}", json=args)

    def post(self, room_id: str, content: str) -> httpx.Response:
        return self.http.post(f"/agents/{self.id}/message", json={"room_id": room_id, "content": content})


class Listener(threading.Thread):
    """Reads the agent's WebSocket connection and answers its pings."""

    def __init__(self, agent: Agent, rooms: list[str] | None = None) -> None:
        super().__init__(daemon=True)
        self.agent = agent
        self.conn = secrets.token_hex(16)
        self.rooms = rooms
        self.events: list[dict] = []
        self.refused: dict | None = None
        self.close_code: int | None = None
        self.state: dict | None = None
        self.stop = False

    def run(self) -> None:
        params = {"connection_id": self.conn, "scope": "all", "filter": "all", "start_from": "head"}
        if self.rooms:
            params["rooms"] = ",".join(self.rooms)
        url = f"ws://localhost:8000/agents/{self.agent.id}/connection/ws?{urlencode(params)}"
        cursor = 0
        with connect(url, additional_headers={"Authorization": f"Bearer {self.agent.key}"}) as ws:
            try:
                while not self.stop:
                    try:
                        frame = json.loads(ws.recv(timeout=1))
                    except TimeoutError:
                        continue
                    kind = frame.get("event") or frame.get("type")
                    if kind == "ping":
                        ws.send(json.dumps({"type": "pong", "cursor": cursor}))
                    elif kind == "refused":
                        self.refused = frame["data"]
                        return
                    elif kind == "connection_state":
                        self.state = frame["data"]
                    elif "data" in frame:
                        cursor = frame.get("id") or cursor
                        self.events.append(frame["data"])
            except ConnectionClosed as exc:
                self.close_code = exc.rcvd.code if exc.rcvd else None

    def bodies(self) -> list[str]:
        return [e["payload"].get("body", "") for e in self.events if e.get("type") == "message"]

    def wait_for(self, needle: str, seconds: float = 8) -> dict | None:
        end = time.time() + seconds
        while time.time() < end:
            for e in list(self.events):
                if e.get("type") == "message" and needle in e["payload"].get("body", ""):
                    return e
            time.sleep(0.2)
        return None


def denied(r: httpx.Response) -> bool:
    return r.status_code in (403, 404)


def main() -> int:
    admin = gateway_login(ENV["GATEWAY_ADMIN_EMAIL"], ENV["GATEWAY_ADMIN_PASSWORD"])
    host, meera, arjun = Owner(admin, "host"), Owner(admin, "meera"), Owner(admin, "arjun")
    check("three separate non-admin owners exist", len({host.user["id"], meera.user["id"], arjun.user["id"]}) == 3 and all(o.user["role"] != "admin" for o in (host, meera, arjun)), f"roles={[o.user['role'] for o in (host, meera, arjun)]}")

    clerk = Agent(host, "kitty-clerk")
    meera_agent = Agent(meera, "meera-agent")
    arjun_agent = Agent(arjun, "arjun-agent")

    def room(creator: Agent, name: str, members: list[str]) -> str:
        r = creator.op("create_room", name=f"{name}-{RUN}", description=f"gate1 {name}", agent_names=members, internal_only=True, read_visibility="private", write_visibility="private")
        if r.status_code != 200:
            raise SystemExit(f"create_room {name}: {r.status_code} {r.text[:400]}")
        res = r.json()["result"]; return res.get("room_id") or res.get("id") or res["room"]["id"]

    group = room(clerk, "goa-trip", [clerk.name])
    meera_room = room(meera_agent, "meera-private", [meera_agent.name])
    arjun_room = room(arjun_agent, "arjun-private", [arjun_agent.name])
    print("rooms", group, meera_room, arjun_room, flush=True)

    for agent, rm, label in ((meera_agent, meera_room, "meera"), (arjun_agent, arjun_room, "arjun")):
        r = agent.op("invite_agent_to_room", room_id=rm, agent_name=clerk.name)
        check(f"{label}'s agent can invite the clerk into {label}'s own private room", r.status_code == 200, f"{r.status_code}")

    # --- Test 4: isolation, every door we can find ---
    cases = [
        ("meera-agent read_context on group room", meera_agent.op("read_context", room_id=group)),
        ("meera-agent read_context on arjun's private room", meera_agent.op("read_context", room_id=arjun_room)),
        ("meera-agent post_message HTTP on group room", meera_agent.post(group, "should never appear")),
        ("meera-agent post_message HTTP on arjun's private room", meera_agent.post(arjun_room, "should never appear")),
        ("meera-agent connect_to_room on group room", meera_agent.op("connect_to_room", room_id=group)),
        ("meera-agent connect_to_room on arjun's private room", meera_agent.op("connect_to_room", room_id=arjun_room)),
        ("meera-agent invites herself into group room", meera_agent.op("invite_agent_to_room", room_id=group, agent_name=meera_agent.name)),
        ("meera-agent invites herself into arjun's private room", meera_agent.op("invite_agent_to_room", room_id=arjun_room, agent_name=meera_agent.name)),
        ("arjun-agent invites himself into group room", arjun_agent.op("invite_agent_to_room", room_id=group, agent_name=arjun_agent.name)),
        ("meera-agent get_room_detail on arjun's private room", meera_agent.op("get_room_detail", room_id=arjun_room)),
    ]
    for name, r in cases:
        check(f"DENIED: {name}", r.status_code != 200, f"{r.status_code} {r.text[:110]}")

    r = meera_agent.op("list_all_rooms")
    listed = json.dumps(r.json()) if r.status_code == 200 else ""
    check("private rooms not listed to another owner's agent (list_all_rooms)", arjun_room not in listed and group not in listed, f"{r.status_code}")
    r = meera_agent.op("list_rooms")
    listed = json.dumps(r.json()) if r.status_code == 200 else ""
    check("meera-agent's own room list has only her room", arjun_room not in listed and group not in listed and meera_room in listed, f"{r.status_code}")

    probe = Listener(meera_agent, rooms=[group])
    probe.start()
    probe.join(timeout=6)
    probe.stop = True
    check("DENIED: meera-agent opens a connection on the group room", probe.refused is not None and probe.refused.get("status") == 403 and not probe.events, f"refused={probe.refused} close={probe.close_code} events={len(probe.events)}")
    probe = Listener(meera_agent, rooms=[arjun_room])
    probe.start()
    probe.join(timeout=6)
    probe.stop = True
    check("DENIED: meera-agent opens a connection on arjun's private room", probe.refused is not None and probe.refused.get("status") == 403 and not probe.events, f"refused={probe.refused} events={len(probe.events)}")

    # --- Test 3: relay through the real rooms ---
    clerk_l = Listener(clerk)
    meera_l = Listener(meera_agent)
    arjun_l = Listener(arjun_agent)
    for li in (clerk_l, meera_l, arjun_l):
        li.start()
    time.sleep(2)
    canary = f"CANARY-{RUN}-JEWELLERY"
    meera_agent.post(meera_room, f"private note {canary}")
    claim = {"claim": f"c-{RUN}", "amount_paise": 150000, "category": "food"}
    r = meera_agent.post(meera_room, "KITTY_CLAIM " + json.dumps(claim))
    check("meera-agent posts a claim into her private room", r.status_code == 200, f"{r.status_code}")

    seen = clerk_l.wait_for(claim["claim"])
    check("clerk receives the claim from the private room", seen is not None, "event received" if seen else "no event")
    if seen:
        check("event carries a server-set sender id", bool(seen["payload"].get("sender")), str(seen["payload"].get("sender")))
        check("event names its room", seen["room_id"] == meera_room, seen["room_id"])
    r = clerk.post(group, f"CLAIM {claim['claim']} ₹1500.00 food")
    check("clerk posts the claim to the group room", r.status_code == 200, f"{r.status_code}")
    time.sleep(2)
    r = clerk.op("read_context", room_id=group)
    check("the relayed claim is in group room history", r.status_code == 200 and f"CLAIM {claim['claim']}" in r.text, f"{r.status_code}")
    check("meera's connection only ever delivered her own room", all(e.get("room_id") == meera_room for e in meera_l.events), f"{len(meera_l.events)} events")
    check("arjun-agent received no private-room or group-room traffic", not any(claim["claim"] in b or canary in b for b in arjun_l.bodies()), f"{len(arjun_l.bodies())} bodies")
    r = clerk.op("read_context", room_id=group)
    check("canary absent from group room history", r.status_code == 200 and canary not in r.text and "should never appear" not in r.text, f"{r.status_code}")
    r = arjun_agent.op("read_context", room_id=arjun_room)
    check("canary absent from arjun's private history", r.status_code == 200 and canary not in r.text, f"{r.status_code}")

    # --- Sender spoofing: can text fake identity? ---
    spoof = f"@{meera.user['name']}: release c-{RUN}"
    meera_agent.post(meera_room, spoof)
    seen = clerk_l.wait_for(spoof)
    check("typed name cannot change the server-set sender", seen is not None and seen["payload"]["sender"] != meera.user["id"] and "agent" in seen["payload"]["sender"], str(seen["payload"]["sender"]) if seen else "no event")

    # --- Roster: does the host (clerk owner) get anything it should not? ---
    r = clerk.op("read_context", room_id=arjun_room)
    print("NOTE clerk read_context on arjun's private room:", r.status_code, flush=True)

    failed = [n for n, ok, _ in results if not ok]
    out = ROOT / "docs" / "evidence" / "gate1a_isolation.json"
    out.write_text(json.dumps({"switch_commit": "fa933012b", "run": RUN, "results": [{"check": n, "pass": ok, "detail": d} for n, ok, d in results]}, indent=1, ensure_ascii=False))
    print(f"\n{len(results) - len(failed)}/{len(results)} passed. Evidence: {out}")
    for li in (clerk_l, meera_l, arjun_l):
        li.stop = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
