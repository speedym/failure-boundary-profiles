#!/usr/bin/env python3
"""Render everything the paper prints from the pools and the raw bundles, as one command (F101, 2026-10-22).

    conda run -n scl_prototype python scripts/render_paper_figures.py \\
        --pooled DIR --bundles DIR --out DIR [--paper DIR] [--spec SPEC] [--replace] [--dry]

The render spec (default artifacts/examples/figure_specs/paper_render.json, kind figure_render/v0.1) names
the paper's choices: which pooling specs the driving figures read (the roster pools and the extension
pools), which figure and table ids, which brains and campaigns the warehouse figures compare, the
profile figures and their design, and the scenario family figure's spec. This tool turns the spec into
the tool runs, in order: the CARLA figure tool (figures, tables, the sensitivity table and the numbers
document TN), the Isaac figure tool per comparison, both profile renders into profiles/<design>/,
the scenario atlas from the raw bundles, and, with --paper, the number checker over the paper's source
against the fresh render. Every tool runs under this interpreter, from the repository this file lives
in, and writes only under --out; a run's log lands in <out>/logs/. --dry prints the commands and runs
nothing. Exit 0 when every run exited 0 (and the checker, when asked, found the paper clean).

--pooled is the directory the pool tool wrote (pool_campaigns.py --out), --bundles the directory
holding the raw zips the atlas reads, --out a fresh directory (--replace empties an existing one, since
a figure file left from an earlier render would be found by name beside the new one).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO_ROOT / "artifacts/examples/figure_specs/paper_render.json"
SPEC_KIND = "figure_render/v0.1"
CARLA_TOOL = REPO_ROOT / "adapters/fail2drive/scripts/multi_model_eval/paper_figures.py"
ISAAC_TOOL = REPO_ROOT / "adapters/isaac/scripts/multi_model_eval/paper_figures.py"
ATLAS_TOOL = REPO_ROOT / "scripts/render_scenario_atlas.py"
CHECKER = REPO_ROOT / "scripts/check_paper_numbers.py"
SPEC_KEYS = ("pooling_dir", "roster", "sensitivity", "paper_tables", "atlas", "design", "profiles_dir",
             "driving", "warehouse", "warehouse_profiles")


class RenderError(ValueError):
    """The spec is not the shape this tool runs, or names a file the tree does not hold."""


def load_spec(path) -> dict:
    path = Path(path)
    spec = json.loads(path.read_text())
    if spec.get("spec_kind") != SPEC_KIND:
        raise RenderError(f"{path}: spec_kind is {spec.get('spec_kind')!r}, expected {SPEC_KIND!r}")
    missing = [k for k in SPEC_KEYS if k not in spec]
    if missing:
        raise RenderError(f"{path}: lacks {missing}; a render spec holds {list(SPEC_KEYS)}")
    for key in ("roster", "sensitivity", "paper_tables", "atlas"):
        if not (REPO_ROOT / spec[key]).is_file():
            raise RenderError(f"{path}: {key} names {spec[key]}, not a file under {REPO_ROOT}")
    pooling = REPO_ROOT / spec["pooling_dir"]
    names = list(spec["driving"]["roster_specs"]) + list(spec["driving"]["extension_specs"])
    for run in list(spec["warehouse"]) + list(spec["warehouse_profiles"]):
        names += list(run["specs"])
    absent = sorted({n for n in names if not (pooling / n).is_file()})
    if absent:
        raise RenderError(f"{path}: pooling specs not under {spec['pooling_dir']}: {absent}")
    return spec


def _specs(spec: dict, names, flag="--spec") -> list:
    out = []
    for n in names:
        out += [flag, str(REPO_ROOT / spec["pooling_dir"] / n)]
    return out


def plan(spec: dict, pooled: Path, bundles: Path, out: Path, paper: Path | None, py: str) -> list:
    """[(label, argv)] in run order."""
    d = spec["driving"]
    roster, sens, tables = (str(REPO_ROOT / spec[k]) for k in ("roster", "sensitivity", "paper_tables"))
    design = spec["design"]
    runs = []
    runs.append(("driving", [py, str(CARLA_TOOL), "--pooled", str(pooled), *_specs(spec, d["roster_specs"]),
                             *_specs(spec, d["extension_specs"], "--extension-spec"), "--sensitivity", sens,
                             "--roster", roster, "--paper-tables", tables, "--figures", *d["figures"], "--out", str(out)]))
    for run in spec["warehouse"]:
        argv = [py, str(ISAAC_TOOL), "--pooled", str(pooled), *_specs(spec, run["specs"])]
        for b in run.get("brains", []):
            argv += ["--brain", b]
        if run.get("family"):
            argv += ["--family", run["family"]]
        if run.get("figures"):
            argv += ["--figures", *run["figures"]]
        if run.get("panel_titles"):
            argv += ["--panel-titles", *run["panel_titles"]]
        argv += ["--out", str(out / run["out"])]
        runs.append((f"warehouse:{run['out']}", argv))
    prof = out / spec["profiles_dir"] / design
    runs.append((f"profiles:{design}:carla", [py, str(CARLA_TOOL), "--pooled", str(pooled), *_specs(spec, d["roster_specs"]),
                                              *_specs(spec, d["extension_specs"], "--extension-spec"), "--roster", roster,
                                              "--figures", *d["profile_figures"], "--design", design, "--out", str(prof / "carla")]))
    for run in spec["warehouse_profiles"]:
        argv = [py, str(ISAAC_TOOL), "--pooled", str(pooled), *_specs(spec, run["specs"])]
        for b in run.get("brains", []):
            argv += ["--brain", b]
        argv += ["--roster", roster, "--figures", *run["figures"], "--design", design, "--out", str(prof / "isaac")]
        runs.append((f"profiles:{design}:isaac:{'+'.join(run['figures'])}", argv))
    runs.append(("atlas", [py, str(ATLAS_TOOL), "--spec", str(REPO_ROOT / spec["atlas"]), "--bundles", str(bundles), "--out", str(out)]))
    if paper is not None:
        runs.append(("checker", [py, str(CHECKER), "--paper", str(paper), "--numbers", str(out / "figure_data/TN.json"),
                                 "--render", str(out), "--strict"]))
    return runs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC, help="the figure_render/v0.1 spec")
    ap.add_argument("--pooled", type=Path, required=True, help="the pool tool's output directory")
    ap.add_argument("--bundles", type=Path, required=True, help="the directory holding the raw campaign zips (the atlas)")
    ap.add_argument("--out", type=Path, required=True, help="a fresh output directory")
    ap.add_argument("--paper", type=Path, default=None, help="the paper's LaTeX source root; runs the number checker against the render")
    ap.add_argument("--replace", action="store_true", help="empty an existing --out first")
    ap.add_argument("--dry", action="store_true", help="print the commands, run nothing")
    a = ap.parse_args(argv)
    try:
        spec = load_spec(a.spec)
    except RenderError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    out = a.out.resolve()
    runs = plan(spec, a.pooled.resolve(), a.bundles.resolve(), out, a.paper.resolve() if a.paper else None, sys.executable)
    if a.dry:
        for label, argv in runs:
            print(f"[{label}] {shlex.join(argv)}")
        return 0
    if out.exists() and any(out.iterdir()):
        if not a.replace:
            print(f"REFUSED: {out} is not empty; a figure left from an earlier render would be found by name beside the new "
                  f"one (--replace empties it)", file=sys.stderr)
            return 2
        shutil.rmtree(out)
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    failed = []
    for label, argv in runs:
        t0 = time.time()
        log = logs / (label.replace(":", "_").replace("+", "_") + ".log")
        with log.open("w") as f:
            f.write(shlex.join(argv) + "\n\n")
            f.flush()
            rc = subprocess.run(argv, stdout=f, stderr=subprocess.STDOUT, cwd=REPO_ROOT).returncode
        note = ""
        if rc != 0:
            failed.append(label)
            tail = [l for l in log.read_text().splitlines() if l.strip()][-1:]
            note = f"  <- {tail[0][:160]}" if tail else ""
        print(f"  {label:<34} exit={rc}  {time.time() - t0:6.1f} s{note}")
    print(f"  {'FAILED ' + str(failed) if failed else 'RENDER_DONE'}: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
