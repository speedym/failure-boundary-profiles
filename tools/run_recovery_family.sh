#!/bin/bash
# Run PDM-Lite (team_code/autopilot.py, track MAP) over a recovery family with
# the CARLA recorder on. One invocation per route so score_runs.py sees one
# <scenario_id>.json per variant.
#   FAMDIR=<splits family dir> OUT=<run dir> SEED=n  run_fm006.sh <scenario_id>...
set -u
source "$(dirname "$0")/../fbp_env.sh"
SEED=${SEED:-0}
PORT=${PORT:-2000}
TM_PORT=${TM_PORT:-8000}
FAM=${FAMDIR:-$WORK_DIR/failure_boundary_profiling_splits/fm_006_recovery_oncoming_car}
OUT=${OUT:-/home/ekirby/workspace/fbp_runs/fm_006_oncoming_car/pdm_lite_expert/seed_$SEED}
mkdir -p "$OUT/recordings" "$OUT/displacement"
export FBP_DISPLACEMENT_LOG="$OUT/displacement"
export FBP_NO_BACKGROUND=${NO_BACKGROUND:-0}
for sid in "$@"; do
  echo "=== $sid ===" >&2
  export FBP_DISPLACEMENT_NAME="$sid"
  "$PYTHON" "$LEADERBOARD_ROOT/leaderboard/leaderboard_evaluator_local.py" \
    --routes "$FAM/routes/$sid.xml" \
    --agent "$WORK_DIR/team_code/autopilot.py" \
    --track MAP \
    --host localhost --port "$PORT" \
    --traffic-manager-port "$TM_PORT" \
    --traffic-manager-seed "$SEED" \
    --record "$OUT/recordings" \
    --checkpoint "$OUT/$sid.json" \
    --debug-checkpoint "$OUT/$sid.live.txt" \
    --timeout 600
  echo "=== $sid exit=$? ===" >&2
done
