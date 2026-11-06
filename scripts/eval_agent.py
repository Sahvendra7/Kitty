"""Score the model's unassisted guesses against the human-labelled answer key.

No person answers anything here. "unclear" is counted separately, because asking is the correct
behaviour when a line could go either way. Results are reported as they come out.

    uv run python scripts/eval_agent.py [--runs 3]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kitty import statements as S  # noqa: E402
from kitty.llm import MODEL  # noqa: E402
from kitty.llm_brain import LlmBrain  # noqa: E402

DATA = ROOT / "data/synthetic"
TRIP = json.loads((DATA / "trip.json").read_text())
KEY = json.loads((DATA / "answer_key.json").read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    runs = ap.parse_args().runs
    directory = {d["statement_name"]: p for p, d in TRIP["people"].items()}
    start, end = date.fromisoformat(TRIP["start"]), date.fromisoformat(TRIP["end"])
    brain = LlmBrain()
    tally = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "unclear_shared": 0, "unclear_personal": 0, "missing": 0}
    detail = []
    for run in range(runs):
        for person, doc in TRIP["people"].items():
            rows = S.spending_candidates(S.in_window(S.parse(DATA / f"{person}.csv"), start, end), directory)
            guesses = {g.get("row_id"): g for g in brain.classify([S.model_view(r) for r in rows], doc["name"], TRIP["name"]) if isinstance(g, dict)}
            for r in rows:
                truth = KEY[person][r.narration]
                label = guesses.get(r.row_id, {}).get("label")
                detail.append({"run": run, "person": person, "line": r.narration, "truth": truth, "guess": label})
                if label == "shared":
                    tally["tp" if truth == "shared" else "fp"] += 1
                elif label == "personal":
                    tally["tn" if truth == "personal" else "fn"] += 1
                elif label == "unclear":
                    tally[f"unclear_{truth}"] += 1
                else:
                    tally["missing"] += 1
    decided = tally["tp"] + tally["fp"]
    actual = tally["tp"] + tally["fn"] + tally["unclear_shared"]
    report = {"model": MODEL, "runs": runs, "lines_scored": len(detail), **tally, "precision_shared_when_decided": round(tally["tp"] / decided, 3) if decided else None, "recall_shared_without_asking": round(tally["tp"] / actual, 3) if actual else None, "wrong_personal_would_lose_money": tally["fn"], "wrong_shared_still_needs_release": tally["fp"], "detail": detail}
    (ROOT / "docs/evidence/eval_agent.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "detail"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
