"""Start the clerk and every personal agent as separate processes, and keep them in logs/.

    uv run python scripts/run_all.py            # real model
    KITTY_BRAIN=rules uv run python scripts/run_all.py   # no model at all
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRIP = json.loads((ROOT / "data/synthetic/trip.json").read_text())


def main() -> int:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    procs = []

    def spawn(label: str, *args: str) -> None:
        handle = (logs / f"{label}.log").open("a")
        procs.append(subprocess.Popen([sys.executable, "-m", *args], cwd=ROOT, stdout=handle, stderr=handle, env={**__import__("os").environ, "PYTHONPATH": str(ROOT)}))
        print(f"started {label}, logging to logs/{label}.log")

    spawn("clerk", "kitty.clerk_daemon")
    time.sleep(2)
    for i, person in enumerate(TRIP["people"]):
        spawn(person, "kitty.agent_runner", person)
        spawn(f"{person}-app", "kitty.person_app", person, str(7101 + i))

    def stop(*_: object) -> None:
        for p in procs:
            p.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print("Demo stage: http://127.0.0.1:7100/stage   Trip board: http://127.0.0.1:7100   Mattermost: http://localhost:8065   Ctrl-C stops everything.")
    while True:
        for p in procs:
            if p.poll() is not None:
                print(f"a process exited with {p.returncode}; see logs/. Stopping.", file=sys.stderr)
                stop()
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
