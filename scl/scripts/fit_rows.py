#!/usr/bin/env python3
"""Fit the curves of one scored folder: run rows of any chain become stats rows (F82, the one fit stage).

    conda run -n scl_prototype python scripts/fit_rows.py \\
        --rows <scoring_out dir> --out <stats dir> [--defects <register>]

Reads <rows>/run_rows.jsonl under its stamp (scl_core.run_rows.check_stamp: a scorer or vocabulary that
moved against this tree refuses), the vocabulary the stamp pins, and the declared scenario-defect
register when given; writes <out>/stats_rows.jsonl and stats_provenance.json (scl_core.stats.fit_rows).
The driver runs it per family for every chain; the pool tool runs it over pooled rows.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import run_rows as rr  # noqa: E402
from scl_core import scenario_defects  # noqa: E402
from scl_core.stats import fit_rows as fr  # noqa: E402


def table(stats) -> str:
    lines = []
    for s in stats:
        if s["method"] == "categorical":
            desc = f"categorical ladder over {s['n_strata']} levels"
        elif s["method"] == "flat":
            desc = f"FLAT P={s['level']} ({s['reason']}) - no boundary in the tested range"
        elif s["method"] == "empty":
            desc = "no counted run"
        else:
            bs = s.get("bootstrap")
            desc = (f"{s['method']} boundary={s['boundary']} bracket fail@{s['bracket_fail_side_x']} / "
                    f"pass@{s['bracket_pass_side_x']}" + (f" slope={s['slope']}" if s.get("slope") else "")
                    + (f" CI[{bs['ci'][0]},{bs['ci'][1]}] sep_frac={bs['separation_fraction']}" if bs else "")
                    + (f" VIOLATIONS={s['monotonicity_violations']}" if s.get("monotonicity_violations") else "")
                    + (f" ({s['reason']})" if s.get("reason") not in (None, "converged", "separated") else ""))
        lines.append(f"  {s['family']} {s['model']:>18} {s['curve']:<10} {desc}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", required=True, type=Path, help="the scored folder holding run_rows.jsonl and its stamp")
    ap.add_argument("--out", required=True, type=Path, help="the stats folder to write")
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register (scl_core/scenario_defects.py): a registered cell's "
                         "rows leave every fit and are counted; the stats provenance pins the register")
    ap.add_argument("--repo-root", type=Path, default=ROOT, help="the tree the stamp's pins are checked against")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    root = args.repo_root.resolve()
    try:
        stamp = rr.check_stamp(args.rows, root)
        rows = rr.read_rows(args.rows / Path(rr.ROWS_MEMBER).name)
        vocabulary, _ = rr.load_vocabulary(root / stamp["vocabulary"]["path"])
        defects_doc, defects_pin = None, None
        if args.defects is not None:
            defects_doc, defects_sha = scenario_defects.load_register(args.defects)
            defects_pin = scenario_defects.pin(args.defects, defects_sha, root)
        stats = fr.fit_rows(rows, vocabulary, defects_doc)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if not rows:
        print(f"REFUSED: no rows under {args.rows}", file=sys.stderr)
        return 2
    fr.write_stats(args.out, stats, rows_stamp=stamp, root=root, defects=defects_pin)
    if not args.quiet:
        print(table(stats))
    print(f"{len(stats)} stats rows over {len(rows)} runs -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
