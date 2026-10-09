#!/usr/bin/env python3
"""FindingsSummary v0.2 emitter (S15 decomposition; 2026-09-07).

Builds the two-curve findings document from documents that already
exist - never re-scores, never re-derives:
  the scored document      score_runs.py --out (rows: threaded_clean,
                           reached_clean per cell; the v0.1 aggregates)
  the derived rows         derive_go_clean.py (go / clean_pass / gate /
                           detour / route_at_goal per cell + summary)
  the family snapshot      family_manifest.json (variants, widths,
                           assignments)
and fits every curve with the ONE estimator (scl_core.stats.boundary).

Three curves per (family, brain), each over ALL variants with their
certified assignment, fitted over the PASSABLE strata (oriented so
success increases with width):
  p_go        committed to any admissible route (gap OR detour line
              crossed by the body) - the decision curve
  p_clean     reached the goal contact-free by any route - the mission
              curve (clean_pass)
  passage     threaded_clean: through the gap contact-free - the
              boundary quantity (S15 p_clean on the passage)
plus the decomposition: competence given commitment on the passable
strata, and the timeout split (no line crossed vs entered then timed
out) from the derivation summary - the diagnosis in one block.

v0.1 findings stay pinned; v0.2 documents are SIBLINGS
(<name>.v0.2.json), additive. Naming p_go / p_clean is the proposed
cross-backend contract (Gino's S15 entry; DM pending); a rename is a
sibling regeneration, never an edit of a pinned document.

  conda run -n scl_prototype python \\
      adapters/isaac/scripts/multi_model_eval/findings_v2.py \\
      --scored <scoring_out json> --go-clean <go_clean dir> \\
      --family <snapshot dir> --brain x_mobility --out <path>
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from scl_core import scenario_defects  # noqa: E402
from scl_core.stats.boundary import (  # noqa: E402
    BOOTSTRAP_B, BOOTSTRAP_SEED, analyze, bootstrap, wilson)

FINDINGS_KIND = "findings_summary/v0.2"
CURVES = (
    ("p_go", "go", "committed to any admissible route: the body crossed "
                   "the gap line or the detour line (sticky) - decision"),
    ("p_clean", "clean_pass", "reached the goal contact-free by any route "
                              "- the conduct envelope (mission)"),
    ("passage", "threaded_clean", "through the gap contact-free "
                                  "(commit_clean) - the boundary quantity"),
)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def curve_from_cells(cells, variants, key):
    """cells: per-cell dicts carrying `variant` and the boolean `key`;
    variants: ordered list of {stem, target_width_m, measured_gap_m,
    assignment}. The `width_m` axis is the CERTIFIED measured gap
    (measured_gap_m) - never the nominal target ("targets are never
    trusted") - so a tolerance-offset variant lands on its true x; the
    nominal target stays in the family manifest, not duplicated per point
    (keeps the point shape byte-stable against the committed v0.1/v0.2
    records). A variant with NO measured gap is NOT placed on the axis: its
    width_m is None, it is flagged x_unmeasured, and it is EXCLUDED from the
    fit - a nominal target never silently occupies the measured axis."""
    points = []
    for v in variants:
        grp = [c for c in cells if c["variant"] == v["stem"]]
        # an anomaly cell (a contact-free completion of a certified-blocked gap, score_runs) leaves
        # every stratum and is counted: the CARLA coding's None (2026-10-03). A width with no
        # scored cell left has no point, as on CARLA; it stays in the outcome composition.
        scored = [c for c in grp if not c.get("excluded")]
        excluded = len(grp) - len(scored)
        if grp and not scored:
            if all(c.get("excluded") == "declared defect" for c in grp):
                # every cell of the width is a declared scenario defect (scl_core/scenario_defects.py):
                # the width stays in the document under the assignment `defect`, out of every fit
                lo0, hi0 = wilson(0, 0)   # the uninformative interval of an empty stratum, never null
                points.append({"width_m": v.get("measured_gap_m"), "assignment": "defect", "n": 0, "k": 0,
                               "rate": None, "wilson_95": [_r(lo0), _r(hi0)], "excluded": excluded,
                               "defect_runs": excluded})
            continue
        n, k = len(scored), sum(1 for c in scored if c[key])
        lo, hi = wilson(k, n)
        x = v.get("measured_gap_m")
        point = {"width_m": x,
                 "assignment": v["assignment"], "n": n, "k": k,
                 "rate": _r(k / n) if n else None,
                 "wilson_95": [_r(lo), _r(hi)]}
        if excluded:
            point["excluded"] = excluded
        if x is None:              # never fabricate a measured x from target
            point["x_unmeasured"] = True
        points.append(point)
    strata = sorted(((p["width_m"], p["n"], p["k"]) for p in points
                     if p["assignment"] == "passable" and p["n"] > 0
                     and p["width_m"] is not None),
                    key=lambda s: s[0])
    fit = analyze(strata) if strata else None
    boot = None
    if fit is not None:
        boot = bootstrap(strata, np.random.default_rng(BOOTSTRAP_SEED))
    if fit is not None:
        fit = {k2: (_r(v2) if isinstance(v2, float) else
                    ([_r(x) for x in v2] if isinstance(v2, (list, tuple))
                     and all(isinstance(x, (int, float)) or x is None for x in v2)
                     else v2))
               for k2, v2 in fit.items()}
        if boot:
            boot = {"ci": [_r(boot["ci"][0]), _r(boot["ci"][1])],
                    "separation_fraction": _r(boot["separation_fraction"]),
                    "method_fractions": boot.get("method_fractions"),
                    "replicates_used": boot["replicates_used"],
                    "replicates_dropped": boot.get("replicates_dropped")}
    return {"points": points, "fit_passable": fit, "bootstrap": boot,
            "fit_note": ("fit over PASSABLE strata, axis oriented so "
                         "success increases with width; blocked strata "
                         "are reported as points (p_go there is the "
                         "false-commit rate); anomaly cells are excluded "
                         "from every stratum and counted (decomposition."
                         "anomalies, the outcome composition)")}


def build_findings_v2(scored: dict, derived_rows: list, derived_summary: dict,
                      family_manifest: dict, brain: str, provenance: dict, defects=None):
    """`defects`: (register document, its pin) or None (F66 part two): a cell the
    scenario-defect register names (family id, variant stem, repetition label
    rep_<k> or expert, or "all") is excluded as a declared defect, out of every
    curve and the decomposition, listed under `defects`, and a width whose every
    cell is registered stays in the document under the assignment `defect`."""
    defects_doc, defects_pin = defects or (None, None)
    register_family = family_manifest.get("manifest_id") or family_manifest["family_id"]
    applied = []
    variants = [{"stem": v["stem"], "target_width_m": v["target_width_m"],
                 "measured_gap_m": v.get("measured_gap_m"),
                 "assignment": v["assignment"]}
                for v in family_manifest["variants"]
                if v.get("verdict", "accepted") == "accepted"]
    scored_rows = [r for r in scored["rows"] if r["agent"] == brain]
    derived = [r for r in derived_rows if r["agent"] == brain]
    # join scored (threaded_clean) with derived (go, clean_pass, gate /
    # detour) per cell on (variant, rep): the derived cell id is
    # "<variant>/expert" or "<variant>/<slug>_rep<k>"; the scored row
    # carries rep None (expert) or the integer k
    def rep_of(cell_id: str):
        tail = cell_id.rsplit("/", 1)[-1]
        return None if tail == "expert" else int(tail.rsplit("rep", 1)[-1])
    by_key = {(r["variant"], rep_of(r["cell"])): r for r in derived}
    cells = []
    for r in scored_rows:
        d = by_key.get((r["variant"], r.get("rep")))
        if d is None:
            raise SystemExit(f"REFUSED: no derived row for scored cell "
                             f"{r['variant']} rep {r.get('rep')} - the "
                             f"derivation and the scoring must cover the "
                             f"same cells")
        rep_label = "expert" if r.get("rep") is None else f"rep_{r['rep']}"
        entry = scenario_defects.entry_for(defects_doc, register_family, r["variant"], rep_label)
        if entry is not None:
            applied.append({"variant": r["variant"], "rep": rep_label, "reason": entry["reason"]})
        cells.append({"variant": r["variant"],
                      "threaded_clean": bool(r["threaded_clean"]),
                      "outcome": r.get("outcome", "missing"),
                      "excluded": ("declared defect" if entry is not None
                                   else str(r.get("outcome", "")).startswith("anomaly_")),
                      "go": bool(d["go"]), "clean_pass": bool(d["clean_pass"]),
                      "gate_attempt": bool(d["gate_attempt"]),
                      "detour_attempt": bool(d["detour_attempt"])})
    curves = {}
    for cid, key, meaning in CURVES:
        c = curve_from_cells(cells, variants, key)
        c["meaning"] = meaning
        curves[cid] = c
    # p_go counts a crossing of EITHER line (the two-line instrument); the decision on a blocked
    # width is about the gap line alone, because the detour is the correct answer there (the
    # expert's divert_clean, Milan 2026-10-03). Each p_go point therefore also carries the split:
    # gate_k (crossed the gap line) and detour_k (crossed the detour line), over the scored cells.
    by_stem = {}
    for c in cells:
        if c.get("excluded"):
            continue
        g = by_stem.setdefault(c["variant"], {"gate_k": 0, "detour_k": 0})
        g["gate_k"] += int(c["gate_attempt"])
        g["detour_k"] += int(c["detour_attempt"])
    for p in curves["p_go"]["points"]:
        stem = next(v["stem"] for v in variants if v.get("measured_gap_m") == p["width_m"])
        p.update(by_stem.get(stem, {"gate_k": 0, "detour_k": 0}))
    curves["p_go"]["meaning"] += ("; per point gate_k / detour_k split the goes by the line crossed "
                                  "(a blocked width's correct call is no gate crossing: a stop or the detour)")
    # per-width outcome composition (verbatim footer outcomes, counted):
    # the fact behind every curve point, and what a composition panel
    # draws - a finding in its own right ("timeout_contact dominant
    # above 1.05 m"), additive to the curves. Keyed on the same measured
    # gap the curves use, never the nominal target.
    composition = []
    for v in variants:
        grp = [c for c in cells if c["variant"] == v["stem"]]
        counts = {}
        for c in grp:
            counts[c["outcome"]] = counts.get(c["outcome"], 0) + 1
        composition.append({"width_m": v.get("measured_gap_m"),
                            "assignment": v["assignment"], "n": len(grp),
                            "outcomes": dict(sorted(counts.items()))})
    passable = [c for c in cells if not c.get("excluded") and next(
        v for v in variants if v["stem"] == c["variant"])["assignment"] == "passable"]
    anomalies = {}
    for c in cells:
        if c.get("excluded") is True:   # an anomaly cell; a declared defect is listed under `defects`
            anomalies[c["outcome"]] = anomalies.get(c["outcome"], 0) + 1
    n_go = sum(c["go"] for c in passable)
    n_clean_given_go = sum(c["clean_pass"] for c in passable if c["go"])
    by_agent = derived_summary["by_agent"].get(brain, {})
    decomposition = {
        "passable_cells": len(passable),
        "p_go_passable": _r(n_go / len(passable)) if passable else None,
        "p_clean_given_go_passable": _r(n_clean_given_go / n_go) if n_go else None,
        "timeouts": by_agent.get("timeouts"),
        "detour_attempts": by_agent.get("detour_attempt"),
        "anomalies": dict(sorted(anomalies.items())),
        "diagnosis_rule": ("p_go low -> decision collapse (never committed); "
                           "p_go high & passage low -> competence failure "
                           "inside the passage (contact or stall); p_clean "
                           "reports mission success regardless of route"),
    }
    return {
        "findings_kind": FINDINGS_KIND,
        "campaign": scored.get("campaign") or {
            "id": str(scored.get("results_subtree", "")).split("/")[0],
            "family_id": family_manifest["family_id"]},
        "family_id": family_manifest["family_id"],
        "brain": {"id": brain},
        "curves": curves,
        "outcome_composition": composition,
        "decomposition": decomposition,
        "instrument": derived_summary.get("provenance", {}).get("instrument_id"),
        "defects": {"register": defects_pin,
                    "applied": sorted(applied, key=lambda a: (a["variant"], a["rep"])),
                    "note": ("a cell the declared scenario-defect register names is out of every curve and "
                             "the decomposition, kept in the outcome composition; a width with every cell "
                             "registered carries the assignment `defect` (scl_core/scenario_defects.py)")},
        "provenance": provenance,
        "note": ("v0.2 is ADDITIVE beside the pinned v0.1 findings: the "
                 "passage curve is v0.1's threaded_clean curve re-fitted "
                 "by the shared estimator; p_go / p_clean come from the "
                 "declared two-line instrument's derived rows"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scored", required=True, type=Path)
    ap.add_argument("--go-clean", required=True, type=Path,
                    help="derive_go_clean.py output dir")
    ap.add_argument("--family", required=True, type=Path)
    ap.add_argument("--brain", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register (scl_core/scenario_defects.py, one register "
                         "for both backends); registered cells leave every fit and the document pins it")
    args = ap.parse_args()
    defects = None
    if args.defects is not None:
        try:
            defects_doc, defects_sha = scenario_defects.load_register(args.defects)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(f"REFUSED: --defects: {exc}")
        defects = (defects_doc, scenario_defects.pin(args.defects, defects_sha, ROOT))
    scored = json.loads(args.scored.read_text())
    rows = [json.loads(l) for l in
            (args.go_clean / "go_clean_rows.jsonl").read_text().splitlines()]
    summary = json.loads((args.go_clean / "go_clean_summary.json").read_text())
    fam = json.loads((args.family / "family_manifest.json").read_text())
    provenance = {
        "emitter_sha256": _sha(Path(__file__).resolve()),
        "estimator_sha256": _sha(ROOT / "scl_core/stats/boundary.py"),
        "scored_sha256": _sha(args.scored),
        "scoring_provenance": scored.get("scoring_provenance"),
        "derived_rows_sha256": _sha(args.go_clean / "go_clean_rows.jsonl"),
        "derived_summary_sha256": _sha(args.go_clean / "go_clean_summary.json"),
        "derivation_provenance": summary.get("provenance"),
        "family_manifest_sha256": _sha(args.family / "family_manifest.json"),
        "bootstrap": {"B": BOOTSTRAP_B, "seed": BOOTSTRAP_SEED},
        "scenario_defects": defects[1] if defects else None,
    }
    doc = build_findings_v2(scored, rows, summary, fam, args.brain, provenance, defects)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1) + "\n")
    d = doc["decomposition"]
    print(f"{fam['family_id']} {args.brain}: p_go(passable) {d['p_go_passable']} "
          f"| p_clean|go {d['p_clean_given_go_passable']} | passage fit "
          f"{doc['curves']['passage']['fit_passable'] and doc['curves']['passage']['fit_passable']['method']} "
          f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
