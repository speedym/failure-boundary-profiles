#!/usr/bin/env python3
"""Emit FindingsSummary v0.2 documents for declared static-threading campaigns.

Per declaration (--campaign, repeatable, required): the family is bound by
path and hash against canon and against the snapshot in the results tree;
boundary_stats fits the certified scored rows (the fits carry the current
estimator's stamp); findings_carla emits one document per declared model
under the declaration's coverage gate, named fs_<campaign>_<family>_<model>.
Categorical families are refused by the emitter and recorded here with that
vocabulary (S15 debt), never silently skipped. Nothing here names a campaign.

Outputs: <out>/findings/, <out>/<campaign>/<family>/stats/ and
<out>/threading_findings.json (inputs and outputs by hash).

The scored rows are read from --scores-root/<campaign>/<family>/scoring_out/
(a scoring run's folder, F30); without --scores-root they are read from the
results tree itself, where the historical mixed bundles hold them.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
MME = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(MME)]
import boundary_stats as bs
from scl_core.campaign_declaration import check_family_manifest, load_declarations, resolve_family_manifest
from scl_core.records import portable_path, sha256_file as sha

RECORD_KIND = "threading_findings/v0.1"


def regenerate(results, out, declarations, scores_root=None, defects=None):
    axes = bs.family_axes()
    # the declared scenario-defect register (F66 part two) goes to the fits and the emitter and is pinned here
    defect_args = ["--defects", str(defects)] if defects is not None else []
    record = {"record_kind": RECORD_KIND, "generator_sha256": sha(Path(__file__)),
              "estimator_sha256": sha(ROOT / "scl_core/stats/boundary.py"),
              "stats_tool_sha256": sha(MME / "boundary_stats.py"),
              "emitter_sha256": sha(MME / "findings_carla.py"),
              "scenario_defects": ({"path": portable_path(Path(defects).resolve(), ROOT), "sha256": sha(Path(defects))}
                                   if defects is not None else None),
              "campaigns": {}}
    for declaration in declarations:
        campaign, family = declaration.campaign_id, declaration.family_id
        family_root = results / declaration.results_subtree
        # the family as run, from the raw tree (principle 13); canon only for a bundle predating its snapshot
        manifest, _ = resolve_family_manifest(declaration, family_root, ROOT)
        check_family_manifest(declaration, manifest)
        scored_dir = (scores_root or results) / declaration.results_subtree / "scoring_out"
        if family not in axes:
            raise ValueError(f"{family}: no single scheduled axis (known: {sorted(axes)})")
        metric, kind = axes[family]
        categorical = kind == "categorical"
        entry = {"campaign_declaration": declaration.record(ROOT),
                 "family_manifest_sha256": declaration.manifest_sha256,
                 "scored_sha256": sha(scored_dir / "scored_rows.jsonl"), "axis": metric}
        entry["axis_kind"] = "categorical" if categorical else "numeric"   # F35: both emit
        stats_dir = out / campaign / family / "stats"
        subprocess.run([sys.executable, str(MME / "boundary_stats.py"), str(scored_dir),
                        "--out", str(stats_dir), "--quiet", *defect_args], check=True)
        entry["stats_sha256"] = sha(stats_dir / "stats_rows.jsonl")
        documents = {}
        for model in declaration.models:
            name = f"fs_{campaign}_{family}_{model}.v0.2.json"
            path = out / "findings" / name
            subprocess.run([sys.executable, str(MME / "findings_carla.py"), "--scored", str(scored_dir),
                            "--stats", str(stats_dir), "--family", family, "--model", model,
                            "--campaign", str(declaration.path), "--family-manifest", str(manifest),
                            "--out", str(path), *defect_args], check=True)
            documents[name] = sha(path)
        entry["documents"] = documents
        record["campaigns"].setdefault(campaign, {})[family] = entry
        print(f"{campaign}/{family}: {len(documents)} documents on axis {metric}", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "threading_findings.json").write_text(json.dumps(record, indent=1) + "\n")
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-root", type=Path, default=MME / "results",
                    help="location of the certified campaign trees; each declaration names its subtree")
    ap.add_argument("--campaign", action="append", required=True, type=Path, metavar="DECLARATION",
                    help="campaign declaration (artifacts/examples/campaigns/<campaign>/<family>.json); repeatable")
    ap.add_argument("--out", type=Path, required=True,
                    help="the output root, the caller's work tree (no default under the checkout, F72)")
    ap.add_argument("--scores-root", type=Path, default=None,
                    help="root of the scoring run's folders (<campaign>/<family>/scoring_out/); "
                         "default: the results tree itself (historical mixed layout)")
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register (scl_core/scenario_defects.py), passed to "
                         "boundary_stats and findings_carla and pinned in the record")
    args = ap.parse_args()
    try:
        declarations = load_declarations(args.campaign)
    except ValueError as exc:
        ap.error(str(exc))
    regenerate(args.results_root.resolve(), args.out.resolve(), declarations,
               args.scores_root.resolve() if args.scores_root else None,
               args.defects.resolve() if args.defects else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
