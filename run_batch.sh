#!/bin/bash
# Run tasks x {on,off} arms inside one shared Runta runtime.
# Each task runs both arms in parallel (2 containers); PAIRS controls how many
# tasks run concurrently (PAIRS=2 -> up to 4 containers).
# Usage: run_batch.sh [runner] [pairs] [task...]   (default: all 30 tasks)
set -uo pipefail
{

RUNNER="${1:-jev-eval-runner}"
PAIRS="${2:-1}"
shift 2 2>/dev/null || shift $#
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${FH_REPO:-/tmp/frontier-harness-eval}"
TASKS=("$@")
[ ${#TASKS[@]} -eq 0 ] && TASKS=($(ls "$REPO/tasks"))

echo "== batch: ${#TASKS[@]} tasks x {on,off} on $RUNNER, $PAIRS task(s) at a time"

i=0
for t in "${TASKS[@]}"; do
  i=$((i + 1))
  (
    "$HERE/run_task.sh" "$t" on "$RUNNER" "$REPO" &
    "$HERE/run_task.sh" "$t" off "$RUNNER" "$REPO" &
    wait
    echo "== [$i/${#TASKS[@]}] $t done: $(cat "$HERE/runs/$t-on/out/verifier/reward.json" "$HERE/runs/$t-on/out/verifier/reward.txt" 2>/dev/null | head -c80) / $(cat "$HERE/runs/$t-off/out/verifier/reward.json" "$HERE/runs/$t-off/out/verifier/reward.txt" 2>/dev/null | head -c80)"
  ) &
  while [ "$(jobs -r | wc -l)" -ge "$PAIRS" ]; do sleep 10; done
done
wait
echo "== batch complete -> $HERE/runs/"
exit
}
