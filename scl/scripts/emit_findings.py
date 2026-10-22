#!/usr/bin/env python3
"""Emit the findings documents of one family from its scored folder and its stats folder (F82, the one emitter).

    conda run -n scl_prototype python scripts/emit_findings.py \\
        --rows <scoring_out dir> --stats <stats dir> --campaign <declaration> \\
        --family-manifest <the family as run> --out <findings dir> [--defects <register>]

One document per declared model, findings_summary/v0.3, named fs_<campaign>_<family>_<model>.v0.3.json;
the rows gated against the declaration, the stats bound to the rows, the register the stats were fitted
with (or none) pinned. The pool tool calls the same emitter in process with its pooling record.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import scenario_defects  # noqa: E402
from scl_core.campaign_declaration import check_family_manifest, load_declaration  # noqa: E402
from scl_core.stats import emit_findings as ef  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", required=True, type=Path)
    ap.add_argument("--stats", required=True, type=Path)
    ap.add_argument("--campaign", required=True, type=Path, help="the campaign's declaration of this family")
    ap.add_argument("--family-manifest", required=True, type=Path,
                    help="the family manifest the rows were scored against, as run (the raw snapshot), checked "
                         "against the declaration's hash")
    ap.add_argument("--out", required=True, type=Path, help="the findings directory")
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register the fits were produced with")
    ap.add_argument("--repo-root", type=Path, default=ROOT)
    args = ap.parse_args()
    root = args.repo_root.resolve()
    try:
        declaration = load_declaration(args.campaign)
        manifest = check_family_manifest(declaration, args.family_manifest)
        defects = None
        if args.defects is not None:
            doc, sha = scenario_defects.load_register(args.defects)
            defects = (doc, scenario_defects.pin(args.defects, sha, root))
        paths = ef.emit(args.rows, args.stats, [declaration], root, args.out, manifest=manifest, defects=defects,
                        extra_provenance={"family_manifest_sha256": declaration.manifest_sha256})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    for p in paths:
        doc = json.loads(p.read_text())
        d = doc["decomposition"]
        print(f"{declaration.family_id} {doc['model']['id']}: p_go(passable) {d['p_go_passable']['rate']} | "
              f"p_clean|go {d['p_clean_given_go_passable']['rate']} | passage fit "
              f"{(doc['curves']['passage']['fit'] or {}).get('method', 'none')} -> {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
