#!/usr/bin/env bash
# N full runs from a fresh trip: setup, story, verify. Stops at the first failure and keeps the logs.
#   scripts/rehearse.sh 3
set -u
cd "$(dirname "$0")/.."
N="${1:-3}"
mkdir -p docs/evidence
OUT=docs/evidence/rehearsals_real_model.log
for i in $(seq 1 "$N"); do
  echo "=== rehearsal $i of $N, $(date)" | tee -a "$OUT"
  pkill -f 'scripts/run_all.py'; sleep 2; pkill -f 'kitty\.(person_app [a-z]+ [0-9]+|agent_runner [a-z]+|clerk_daemon)$'; sleep 1
  uv run python scripts/setup_trip.py > /tmp/kitty-setup.$$ 2>&1 || { tail -3 /tmp/kitty-setup.$$ | tee -a "$OUT"; echo "setup FAILED" | tee -a "$OUT"; exit 1; }
  rm -rf logs
  (nohup uv run python scripts/run_all.py > /dev/null 2>&1 &)
  sleep 8
  start=$(date +%s)
  uv run python scripts/play_story.py >> "$OUT" 2>&1
  [ $? -eq 0 ] || { echo "story FAILED on rehearsal $i" | tee -a "$OUT"; exit 1; }
  echo "story took $(( $(date +%s) - start ))s" | tee -a "$OUT"
  uv run python scripts/verify.py > /tmp/kitty-verify.$$ 2>&1
  code=$?
  grep -E '^FAIL|checks passed' /tmp/kitty-verify.$$ | tee -a "$OUT"
  rm -f /tmp/kitty-verify.$$
  [ "$code" -eq 0 ] || { echo "verify FAILED on rehearsal $i" | tee -a "$OUT"; exit 1; }
done
echo "ALL $N REHEARSALS PASSED, $(date)" | tee -a "$OUT"
