#!/usr/bin/env python3
"""Agreement audit for collision attribution (F66, 2026-10-05).

Re-scores an unpacked raw campaign tree with the CURRENT threading scorer
and compares every run against a previous interpretation's scored rows:
outcome, the six collision counts, the attribution source. Prints a
confusion table of outcomes (previous -> current), every row that changed,
and the attribution sources used; writes the same as JSON with --out.

    conda run -n scl_prototype python \\
        adapters/fail2drive/scripts/multi_model_eval/audit_collision_attribution.py \\
        --raw <unpacked campaign dir> --against <derived dir> [--out audit.json]

<raw> holds <family>/<model>/seed_<k>/... as the runner wrote it; <against>
holds <family>/scoring_out/scored_rows.jsonl (a derived folder of the
store, or any earlier scoring). A family present on one side only is
reported, never silently skipped. Read-only, over folders (principle 12);
exits 0 - the table is the finding, the handoff quotes it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_runs  # noqa: E402

COUNT_FIELDS = ("obstacle_collisions", "traffic_collisions", "pedestrian_collisions",
                "environment_collisions", "ambiguous_collisions", "unresolved_collisions")


def _key(row: dict) -> tuple:
    return (row["model"], int(row["seed"]), row["scenario_id"])


def audit(raw: Path, against: Path) -> dict:
    prev_files = {p.parent.parent.name: p
                  for p in sorted(against.glob("*/scoring_out/scored_rows.jsonl"))}
    raw_families = {d.name for d in raw.iterdir()
                    if d.is_dir() and (any(d.glob("*/seed_*")) or any(d.glob("*/*.json")))}
    report = {"raw": str(raw), "against": str(against),
              "only_in_raw": sorted(raw_families - set(prev_files)),
              "only_in_against": sorted(set(prev_files) - raw_families),
              "families": {}, "confusion": Counter(), "changed": [],
              "attribution_sources": Counter(), "n_rows": 0, "n_unmatched": 0}
    for family in sorted(raw_families & set(prev_files)):
        prev = {}
        for line in prev_files[family].read_text().splitlines():
            r = json.loads(line)
            prev[_key(r)] = r
        rows, unmatched, warnings = score_runs.score_run_dirs([raw / family])
        fam = {"n_current": len(rows), "n_previous": len(prev), "n_unmatched": len(unmatched),
               "warnings": warnings, "changed": 0, "missing_previous": 0,
               "attribution_sources": Counter()}
        for row in rows:
            d = row.__dict__
            k = _key(d)
            fam["attribution_sources"][d["attribution_source"]] += 1
            report["attribution_sources"][d["attribution_source"]] += 1
            report["n_rows"] += 1
            p = prev.get(k)
            if p is None:
                fam["missing_previous"] += 1
                continue
            report["confusion"][(p["outcome"], d["outcome"])] += 1
            # a count the previous interpretation did not have (unresolved_collisions
            # before F66) reads as 0, so only real movement counts as a change
            diff = {f: (p.get(f, 0 if f in COUNT_FIELDS else None), d.get(f))
                    for f in ("outcome",) + COUNT_FIELDS
                    if p.get(f, 0 if f in COUNT_FIELDS else None) != d.get(f)}
            if diff:
                fam["changed"] += 1
                report["changed"].append({"family": family, "model": d["model"],
                                          "seed": d["seed"], "variant": d["variant_index"],
                                          "diff": diff,
                                          "events": [{k2: e.get(k2) for k2 in
                                                      ("class", "type", "actor_id", "partner_role",
                                                       "reason", "same_blueprint_distance_m",
                                                       "same_blueprint_radius_m",
                                                       "partner_displacement_at_contact_m",
                                                       "partner_displacement_final_m")}
                                                     for e in d["events"]]})
        fam["attribution_sources"] = dict(fam["attribution_sources"])
        report["families"][family] = fam
        report["n_unmatched"] += len(unmatched)
    report["attribution_sources"] = dict(report["attribution_sources"])
    report["confusion"] = {f"{a} -> {b}": n for (a, b), n in sorted(report["confusion"].items())}
    return report


def render(report: dict) -> str:
    lines = [f"collision attribution audit: {report['raw']} against {report['against']}",
             f"rows {report['n_rows']}, attribution sources {report['attribution_sources']}, "
             f"unmatched {report['n_unmatched']}"]
    for fam, f in report["families"].items():
        lines.append(f"  {fam}: current {f['n_current']} previous {f['n_previous']} "
                     f"changed {f['changed']} missing_previous {f['missing_previous']} "
                     f"sources {f['attribution_sources']}"
                     + (f" WARNINGS {len(f['warnings'])}" if f["warnings"] else ""))
    if report["only_in_raw"] or report["only_in_against"]:
        lines.append(f"  families only in raw: {report['only_in_raw']}; "
                     f"only in against: {report['only_in_against']}")
    lines.append("outcome confusion (previous -> current):")
    for k, n in report["confusion"].items():
        lines.append(f"  {k}: {n}")
    lines.append(f"changed rows: {len(report['changed'])}")
    for c in report["changed"]:
        lines.append(f"  {c['family']} {c['model']} seed {c['seed']} v{c['variant']:02d}: "
                     + ", ".join(f"{k} {a} -> {b}" for k, (a, b) in c["diff"].items()))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", required=True, type=Path)
    ap.add_argument("--against", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    report = audit(args.raw.resolve(), args.against.resolve())
    print(render(report))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
