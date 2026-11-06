"""Gate 1, part D: claim-release enforcement against the live clerk, then a test of the verifier.

Run against a running trip. It pollutes that trip's rooms on purpose, so run setup_trip.py afterwards.

    uv run python scripts/gate1d_release_live.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kitty import claims as C  # noqa: E402
from kitty.switch_client import Agent, Mattermost  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "pass": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}  [{detail}]", flush=True)


def main() -> int:
    cfg = {p: json.loads((ROOT / "state" / f"{p}.json").read_text()) for p in ("priya", "arjun", "meera")}
    clerk_cfg = json.loads((ROOT / "state/clerk.json").read_text())
    clerk = Agent(clerk_cfg["agent_id"], clerk_cfg["api_key"], clerk_cfg["name"])
    meera = Agent(cfg["meera"]["agent_id"], cfg["meera"]["api_key"], cfg["meera"]["name"])
    arjun_mm = Mattermost(cfg["arjun"]["mattermost"]["username"], cfg["arjun"]["mattermost"]["password"])
    meera_mm = Mattermost(cfg["meera"]["mattermost"]["username"], cfg["meera"]["mattermost"]["password"])
    desk, group = cfg["meera"]["desk_room"], clerk_cfg["group_room"]

    def texts(room):
        r = clerk.op("read_context", room_id=room, limit=500).json()["result"]["threads"]
        return [t["root"]["body"] for t in r]

    def claim(**over):
        obj = {"v": 1, "type": "expense", "ref": "attack1", "trip_id": "goa-2026", "owner_id": "meera", "payer_id": "meera", "date": "2026-10-11", "amount_paise": 77700, "currency": "INR", "category": "food", "participant_ids": ["meera", "arjun"], "shares": {"meera": 38850, "arjun": 38850}}
        obj.update(over)
        return C.PREFIX + json.dumps(obj)

    before = len(texts(group))
    meera.say(desk, claim(merchant="ATTACK-MERCHANT"))
    meera.say(desk, claim(ref="attack2", category="bribes"))
    meera.say(desk, claim(ref="attack3", owner_id="arjun"))
    meera.say(desk, "ATTACK-FREE-TEXT my friend bought a secret gift")
    time.sleep(2)
    desk_text = "\n".join(texts(desk))
    check("malformed claims rejected on the live desk", "Rejected: unknown_field" in desk_text and "Rejected: bad_category" in desk_text and "Rejected: owner_mismatch" in desk_text and "Rejected: not_a_claim" not in desk_text)
    check("group room did not change", len(texts(group)) == before, f"{before} messages")

    meera.say(desk, claim())
    time.sleep(1.5)
    card = next((t for t in reversed(texts(desk)) if "[ref attack1]" in t), None)
    check("a well-formed claim is held on the desk, not published", card is not None and len(texts(group)) == before)
    cid = card.split("**")[1]

    meera.say(desk, f"release {cid}")
    time.sleep(1.5)
    check(f"DENIED: the agent itself typing release {cid}", not any(t.startswith(f"**{cid}**") for t in texts(group)), "still unpublished")
    refusal = [t for t in texts(desk) if t.startswith("Refused") and "agent can't" in t]
    check("the refusal is visible on the desk", bool(refusal), refusal[-1] if refusal else "")

    arjun_mm.post(cfg["arjun"]["mattermost"]["channels"]["group"], f"release {cid}")
    time.sleep(1.5)
    check(f"DENIED: Arjun typing release {cid} in the group room", not any(t.startswith(f"**{cid}**") for t in texts(group)), "still unpublished")
    arjun_mm.post(cfg["arjun"]["mattermost"]["channels"]["desk"], f"release {cid}")
    time.sleep(1.5)
    check(f"DENIED: Arjun typing release {cid} on his own desk", not any(t.startswith(f"**{cid}**") for t in texts(group)), "still unpublished")

    meera_mm.post(cfg["meera"]["mattermost"]["channels"]["desk"], f"release {cid}")
    time.sleep(2)
    published = next((t for t in texts(group) if t.startswith(f"**{cid}**")), None)
    check(f"Meera's own release publishes {cid}", published is not None, published or "")
    check("no attack text reached the group room", not any("ATTACK" in t for t in texts(group)))

    print("\nPositive control: plant a real leak, then require verify to fail.", flush=True)
    clerk.say(group, "Planted leak: TANISHQ GOA JEWELLERS")
    p = subprocess.run([sys.executable, "scripts/verify.py"], cwd=ROOT, capture_output=True, text=True, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    caught = [line for line in p.stdout.splitlines() if line.startswith("FAIL") and "statement text in group room" in line]
    check("verify detects a merchant name planted in the group room", p.returncode != 0 and bool(caught), caught[0][:120] if caught else p.stdout[-200:])

    (ROOT / "docs/evidence/gate1d_release_live.json").write_text(json.dumps({"results": results}, indent=1, ensure_ascii=False))
    failed = [r for r in results if not r["pass"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
