"""Extract trajectories from recorder logs in existing run dirs (public, MIT).

Walks <run_dir>/**/recordings/*.log, parses each through a RUNNING CARLA
server (parse-only - no Town load, ~1 s per log; any idle server works),
and writes trajectories/<name>.json.gz next to each recordings/ dir -
the input analysis.trajectory_metrics consumes. Skips logs whose extract
already exists (--force to redo).

Needs the carla client wheel (the harness conda env has it) and a CARLA
server already listening:

    ${CARLA_ROOT}/CarlaUE4.sh -RenderOffscreen &   # or any running server
    python -m analysis.extract_recorders <run_dir> [...] --port 2000

Recorder paths resolve SERVER-side: run the server on the same machine
as the logs, and pass run dirs the server's filesystem can see.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import trajectory


def find_logs(run_dirs):
    for run_dir in run_dirs:
        for log in sorted(Path(run_dir).rglob("recordings/*.log")):
            out = log.parent.parent / "trajectories" / (log.stem + ".json.gz")
            yield log, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--force", action="store_true",
                    help="re-extract even when the .json.gz exists")
    args = ap.parse_args()

    todo = [(log, out) for log, out in find_logs(args.run_dirs)
            if args.force or not out.exists()]
    done = sum(1 for _, out in find_logs(args.run_dirs) if out.exists())
    if not todo:
        print(f"nothing to do ({done} extracts already present)")
        return 0
    print(f"{len(todo)} logs to extract ({done} already done)")

    try:
        import carla as carla_api
    except ImportError:
        print("the carla client wheel is required (use the harness conda "
              "env)", file=sys.stderr)
        return 1

    client = carla_api.Client(args.host, args.port)
    client.set_timeout(120.0)
    failures = 0
    for i, (log, out) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] extracting {log.name} "
              f"({log.stat().st_size / 1e6:.1f} MB)...", flush=True)
        t0 = time.time()
        try:
            data = trajectory.from_recorder_log(client, log)
            trajectory.write_extract(data, out)
            print(f"[{i}/{len(todo)}] done in {time.time() - t0:.1f}s "
                  f"({data['n_frames']} frames, "
                  f"{len(data['collisions'])} hero collisions)", flush=True)
        except Exception as exc:
            failures += 1
            print(f"[{i}/{len(todo)}] FAILED after {time.time() - t0:.1f}s "
                  f"{log}: {exc}", flush=True)
    print(f"done: {len(todo) - failures} extracted, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
