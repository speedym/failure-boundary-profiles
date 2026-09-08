# Environment for the state-recovery families.
# Adapt CARLA_ROOT / PYTHON to the host; the rest is repo-relative.
# The documented setup (conda + f2d_carla, see docs/RUNNING.md) is preferred
# where it exists - this file only exists because the dev container had
# neither, and it records what the runs so far actually used.
export WORK_DIR="${WORK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
export CARLA_ROOT="${CARLA_ROOT:?set CARLA_ROOT to the CARLA 0.9.15 tree}"
export LEADERBOARD_ROOT=$WORK_DIR/leaderboard
export SCENARIO_RUNNER_ROOT=$WORK_DIR/scenario_runner
export PYTHON="${PYTHON:-python}"
export PYTHONPATH=$CARLA_ROOT/PythonAPI/carla:$LEADERBOARD_ROOT:$SCENARIO_RUNNER_ROOT:$WORK_DIR/team_code${EXTRA_SITE_PACKAGES:+:$EXTRA_SITE_PACKAGES}
export PYTHONDONTWRITEBYTECODE=1
