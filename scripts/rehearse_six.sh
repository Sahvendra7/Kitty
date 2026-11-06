#!/usr/bin/env bash
# N full six-person runs, each a new trip through the home app. Stops at the first failure and keeps the logs.
#   scripts/rehearse_six.sh 3
set -u
cd "$(dirname "$0")/.."
N="${1:-3}"
OUT=docs/evidence/six_person_real_model.log
for i in $(seq 1 "$N"); do
  echo "=== six-person run $i of $N, $(date)" | tee -a "$OUT"
  run=$(mktemp)
  uv run python scripts/six_person.py > "$run" 2>&1
  code=$?
  grep -E 'provisioned|balances:|correct|resend|story finished|^FAIL|FAIL  |checks passed|Error' "$run" | cut -c1-160 | tee -a "$OUT"
  dir=$(grep -E '^TRIP_DIR=' "$run" | tail -1 | cut -d= -f2)
  [ -n "$dir" ] && uv run python - "$dir" <<'PY'
import sys
from pathlib import Path
from kitty import runtime
runtime.stop(Path(sys.argv[1]))
PY
  rm -f "$run"
  [ "$code" -eq 0 ] || { echo "run $i FAILED" | tee -a "$OUT"; exit 1; }
done
echo "ALL $N SIX-PERSON RUNS PASSED, $(date)" | tee -a "$OUT"
