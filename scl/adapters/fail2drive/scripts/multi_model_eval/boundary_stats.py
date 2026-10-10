"""Boundary statistics v1 (Demo B item 7): psychometric curves over
scored runs.

Consumes score_runs.py output directories (scored_rows.jsonl; multiple
dirs pool - July seed-0 + multi-seed sweeps combine), emits per
(family, model, curve) boundary estimates with honest intervals.
Offline, numpy-only, deterministic (fixed bootstrap RNG recorded below):

    conda run -n scl_prototype python \
        adapters/fail2drive/scripts/multi_model_eval/boundary_stats.py \
        <scored_dir> [<scored_dir>...] --out <dir>

STATS POLICY (judgment constants and rules; all else is mechanical).

- A row of scored_rows is ONE RUN - one Bernoulli draw (same-seed
  outcomes bifurcate; campaign notes). Curves estimate P(outcome | x).
- Two quantities per (family, model), carried under both the legacy
  curve name and the canonical cross-backend quantity id:
    commitment / quantity p_go:    refusal/correct_stop -> 0;
                clean_thread, contact_*, late_commit -> 1 (the agent
                committed).
    competence / quantity p_clean: clean_thread -> 1; late_commit ->
                stratum DROPPED (neither passed nor failed the passage:
                the obstacle despawned - the standing campaign rule);
                other outcomes -> 0.
  For these THREADING families the designated passage is the only route,
  so p_clean (clean pass) coincides with the passage quantity; the
  distinction (any-route mission vs designated passage) only bites where
  a backend has alternate routes (Isaac). anomaly_* outcomes
  (circumvention suspects) are EXCLUDED from both and counted per row.
- The axis is DERIVED, never hardcoded: the family manifest's
  effective_domain.factor_id and what the variants carry decide its kind
  (campaign_manifest.family_axis; until 2026-10-07 the plan's scheduled
  factor and the registry's name lists):
    measured (fm_001/005 maximum_route_intrusion, fm_002/004
      minimum_free_corridor_width: the factor is in every variant's
      measured block): fit on the MEASURED value, never the target; BOTH
      quantities.
    categorical (asset_identity: effective_domain.candidates): per-level
      Wilson for BOTH quantities, no fitting.
    behavior_scalar (fm_011/012: declared per variant, measured only under
      simulation): NOT scored here - no threading passage; SKIPPED with a
      note, scored by flow_aggregate.py (which emits the same p_go/p_clean
      pair against its NOMINAL crossing_time_gap_s axis, measured gap
      reported alongside).
- Orientation (does success rise or fall with x?) is derived from the
  data trend, with metric semantics as fallback (width: rises,
  intrusion: falls). Internally the axis is oriented so success is
  increasing; outputs report natural units with fail-side and
  pass-side bracket edges named explicitly.
- Fit decision tree, in order:
    flat       all-success or all-failure: no boundary in the tested
               range - a first-class result (bridgedrive's commitment).
    bracket    complete separation (max fail-x < min success-x on the
               oriented axis, checked BEFORE fitting): boundary =
               bracket midpoint, interval = the bracket itself.
    irls       mixed strata: logistic MLE by IRLS. Quasi-separation
               a logistic that does not converge (quasi-separation: no finite
               maximum-likelihood fit) -> method "bracket" with reason
               not_converged: the crossing reported as the bracket, the slope
               UNIDENTIFIABLE, the diverged midpoint kept only as irls_boundary
               (2026-10-04; the slope-cap proxy is retired).
- Bracket: prefix/suffix-clean construction (lower edge = last x whose
  entire fail-side prefix has rate <= 0.5; upper = first x whose entire
  pass-side suffix has rate > 0.5) - non-monotone points (a v11) widen
  the bracket instead of being averaged away. Monotonicity violations
  are counted and listed.
- Wilson intervals (z=1.96) for all per-stratum and per-level rates -
  never Wald (degenerate at p-hat 0/1 with small n).
- Bootstrap activates ONLY when some stratum has n>1 (with n=1
  everywhere, within-stratum resampling is an identity and any CI
  would be fake - the honest interval is the bracket). When active:
  B=10000 replicates resampling runs within strata, refit via the
  same tree, percentile 95% CI over boundaries, with the separation
  fraction and method stability reported alongside.
  RNG: numpy default_rng(20260805).
- Per-seed rows accompany the pooled estimate wherever >=2 seeds
  exist; the seed-variance table reports boundary spread across seeds
  (R4). Descriptive only - whether mixed-effects modeling is warranted
  is a question this table answers, not assumes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from adapters.fail2drive.scripts.multi_model_eval.campaign_manifest import family_axis  # noqa: E402
# the ONE boundary estimator (S15): promoted verbatim to scl_core on
# 2026-09-07; this tool's goldens are the inertness proof of the move.
from scl_core import scenario_defects  # noqa: E402
from scl_core.stats.boundary import (  # noqa: E402,F401
    BOOTSTRAP_B, BOOTSTRAP_SEED, IRLS_ROUNDS, WILSON_Z, analyze, bootstrap,
    bracket, inversions, irls, wilson)

# +1 success rises with x, -1 falls. Used only when the data trend is
# flat (orientation_for's fallback).
ORIENTATION_FALLBACK = {"minimum_free_corridor_width": +1,
                        "maximum_route_intrusion": -1}
MANIFEST_DIR = REPO_ROOT / "artifacts/examples/solved_families"

GO = {"clean_thread", "contact_thread", "contact_past_blockage",
      "contact_blocked", "late_commit"}
STOP = {"refusal", "correct_stop"}
# a run that hit something the record could not name (score_runs F66,
# 2026-10-05): neither a go-clean nor a stop, excluded from both codings
# and counted in excluded_rows, never read as clean
UNRESOLVED = {"unresolved_contact"}


def code_commitment(outcome: str):
    if outcome in STOP:
        return 0
    if outcome in GO:
        return 1
    return None  # anomaly / unknown: excluded, counted by caller


def code_competence(outcome: str):
    if outcome == "clean_thread":
        return 1
    if outcome == "late_commit":
        return None
    if outcome in GO | STOP:
        return 0
    return None


# --- fit machinery: scl_core.stats.boundary (irls, bracket, inversions,
# analyze, bootstrap) - imported above, one estimator for both backends


# --- input plumbing --------------------------------------------------------

HERE = Path(__file__).resolve().parent


def check_scoring_provenance(dirs):
    """Refuse scored input the current scorer did not produce.

    Contract: derived score tables are consumed only when their recorded
    scorer_sha256 matches the score_runs.py beside this tool - a stale
    table silently carries a retired instrument's outcomes (the fm_004
    "Perfect"-as-refusal incident). Returns {dir_basename: stamp} for
    the stats output's own provenance; exits 2 on any violation, naming
    every offending directory with both shas. The single correct remedy
    is re-scoring the raw runs with the current scorer.
    """
    current = hashlib.sha256((HERE / "score_runs.py").read_bytes()).hexdigest()
    stamps, problems = {}, []
    for d in dirs:
        sp = Path(d) / "scoring_provenance.json"
        if not sp.is_file():
            problems.append(f"{d}: no scoring_provenance.json (unstamped "
                            f"scored tables)")
            continue
        stamp = json.loads(sp.read_text())
        if stamp.get("scorer_sha256") != current:
            problems.append(
                f"{d}: scorer_sha256 {stamp.get('scorer_sha256')} != "
                f"current score_runs.py {current}")
            continue
        stamps[Path(d).name] = stamp
    if problems:
        sys.exit(
            "REFUSED: scored input not produced by the current scorer "
            "(contract: derived tables never outlive their instrument; "
            "re-score the raw runs with score_runs.py at this checkout):\n"
            + "\n".join(f"- {p}" for p in problems))
    return stamps


def estimator_sha256() -> str:
    """The shared estimator that actually produced every fit and interval
    (scl_core/stats/boundary.py): pinned beside this tool's own hash, so a
    change to the estimator invalidates existing stats even when this file
    is unchanged."""
    import importlib
    return hashlib.sha256(
        Path(importlib.import_module("scl_core.stats.boundary").__file__).read_bytes()).hexdigest()


def write_stats_provenance(out_dir: Path, stamps: dict, defects_pin: dict | None = None) -> None:
    doc = {
        "provenance_kind": "stats_provenance/v0.1",
        "stats_file": Path(__file__).name,
        "stats_sha256": hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest(),
        "estimator_file": "scl_core/stats/boundary.py",
        "estimator_sha256": estimator_sha256(),
        "scored_inputs": {
            name: {"scorer_sha256": s["scorer_sha256"],
                   "input_files_sha256": s["input_files_sha256"]}
            for name, s in sorted(stamps.items())},
        # the declared scenario-defect register the fits excluded by (F66 part two), or null
        "scenario_defects": defects_pin,
        "note": ("content identity only, no timestamps; consumers refuse "
                 "stats whose stats_sha256 does not match the "
                 "boundary_stats.py beside them or whose estimator_sha256 "
                 "does not match the shared estimator that produced the fits"),
    }
    (out_dir / "stats_provenance.json").write_text(
        json.dumps(doc, indent=1) + "\n")


def load_rows(dirs):
    rows = []
    for d in dirs:
        p = Path(d) / "scored_rows.jsonl"
        if not p.exists():
            print(f"WARNING: no scored_rows.jsonl under {d}", file=sys.stderr)
            continue
        rows += [json.loads(line) for line in p.read_text().splitlines()]
    return rows


def family_axes():
    """family -> (factor_ref, kind), kind one of campaign_manifest.AXIS_KINDS, read from
    the family manifests alone (family_axis). A manifest with no single axis is not a
    family this tool scores and is left out, as a plan without one scheduled factor was."""
    out = {}
    for mf in sorted(MANIFEST_DIR.glob("*.json")):
        doc = json.loads(mf.read_text())
        try:
            out[mf.stem] = family_axis(doc)
        except ValueError:
            continue
    return out


def orientation_for(strata_natural, metric):
    """+1 success rises with x, -1 falls; data trend, semantics fallback."""
    pts = [(x, k / n) for x, n, k in strata_natural]
    if len(pts) >= 2:
        mx = sum(p[0] for p in pts) / len(pts)
        mr = sum(p[1] for p in pts) / len(pts)
        cov = sum((x - mx) * (r - mr) for x, r in pts)
        if abs(cov) > 1e-9:
            return 1 if cov > 0 else -1
    return ORIENTATION_FALLBACK.get(metric, 1)


def natural_edges(fit, orientation):
    """Bracket edges back in natural units, named by side."""
    if not fit.get("interval") or None in fit["interval"]:
        return None, None
    lo, hi = fit["interval"]
    if orientation == 1:
        return lo, hi          # fail-side x, pass-side x
    return -lo, -hi


# --- main ------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scored_dirs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("stats_out"))
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register (scl_core/scenario_defects.py): a registered "
                         "cell's rows leave every fit and are counted per row as defect_rows; the stats "
                         "provenance pins the register")
    args = ap.parse_args()

    stamps = check_scoring_provenance(args.scored_dirs)
    rows = load_rows(args.scored_dirs)
    if not rows:
        print("no scored rows found", file=sys.stderr)
        return 1
    defects_doc, defects_pin = None, None
    if args.defects is not None:
        try:
            defects_doc, defects_sha = scenario_defects.load_register(args.defects)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: --defects: {exc}", file=sys.stderr)
            return 1
        defects_pin = scenario_defects.pin(args.defects, defects_sha, REPO_ROOT)
    # a declared defect cell leaves every fit here, counted per (family, model); the row keeps its
    # outcome in the scored tables and the findings document shows the stratum as `defect`
    defect_counts = defaultdict(int)
    kept = []
    for r in rows:
        if scenario_defects.entry_for(defects_doc, r["family"], r["scenario_id"], r["seed"]) is not None:
            defect_counts[(r["family"], r["model"])] += 1
        else:
            kept.append(r)
    rows = kept
    axes = family_axes()
    rng = np.random.default_rng(BOOTSTRAP_SEED)

    stats_rows, cat_rows, skipped_flow = [], [], []
    lines = [__doc__[__doc__.index("STATS POLICY"):].rstrip(), ""]

    by_fm = defaultdict(list)
    for r in rows:
        by_fm[(r["family"], r["model"])].append(r)

    for (family, model) in sorted(by_fm):
        frows = by_fm[(family, model)]
        if family not in axes:
            continue
        metric, kind = axes[family]

        if kind == "behavior_scalar":
            # flow families (fm_011/012) have no threading passage; their
            # outcomes (collision_flow/stall/clean_cross) and
            # crossing_time_gap_s axis are flow_aggregate.py's, which emits
            # the same p_go/p_clean pair. Skip here loudly rather than
            # mis-scoring under the threading taxonomy (fail closed).
            skipped_flow.append((family, metric))
            continue

        if kind == "categorical":
            # both marginal quantities per level: p_go = commitment,
            # p_clean = clean thread. Geometry is held on the categorical
            # ladder, so these are per-level Wilson rates, no fitting.
            for curve, quantity, coder in (
                    ("commitment", "p_go", code_commitment),
                    ("competence", "p_clean", code_competence)):
                per_level = defaultdict(lambda: [0, 0])
                excluded = 0
                for r in frows:
                    y = coder(r["outcome"])
                    if y is None:
                        excluded += 1
                        continue
                    per_level[str(r["target"])][0] += 1
                    per_level[str(r["target"])][1] += y
                for level, (n, k) in sorted(per_level.items()):
                    lo, hi = wilson(k, n)
                    cat_rows.append({
                        "family": family, "model": model,
                        "curve": curve, "quantity": quantity,
                        "level": level,
                        "n": n, "k": k, "rate": round(k / n, 3),
                        "wilson_lo": round(lo, 3), "wilson_hi": round(hi, 3),
                        "excluded_rows": excluded,
                        "defect_rows": defect_counts[(family, model)],
                        "method": "categorical"})
            continue

        for curve, quantity, coder in (
                ("commitment", "p_go", code_commitment),
                ("competence", "p_clean", code_competence)):
            def build(rows_subset):
                s = defaultdict(lambda: [0, 0])
                excluded = 0
                for r in rows_subset:
                    y = coder(r["outcome"])
                    if y is None:
                        excluded += 1
                        continue
                    x = (r.get("measured") or {}).get(metric)
                    if x is None:
                        continue
                    s[x][0] += 1
                    s[x][1] += y
                nat = sorted((x, n, k) for x, (n, k) in s.items())
                return nat, excluded

            nat, excluded = build(frows)
            if not nat:
                continue
            orient = orientation_for(nat, metric)
            oriented = sorted((orient * x, n, k) for x, n, k in nat)
            fit = analyze(oriented)
            boot = (bootstrap(oriented, rng)
                    if fit["method"] in ("irls", "bracket")
                    else None)
            if boot:
                # bootstrap works on the ORIENTED axis; report the CI in
                # natural units (found by the first multi-seed run: a
                # falling-orientation fit printed a negative-width CI)
                boot["ci"] = tuple(sorted((orient * boot["ci"][0],
                                           orient * boot["ci"][1])))
            fail_x, pass_x = natural_edges(fit, orient)
            seeds = sorted({r["seed"] for r in frows})
            per_seed = {}
            if len(seeds) >= 2:
                for sd in seeds:
                    nat_s, _ = build([r for r in frows if r["seed"] == sd])
                    if nat_s:
                        f = analyze(sorted((orient * x, n, k)
                                           for x, n, k in nat_s))
                        b = f["boundary"]   # the method's own headline: a bracket's midpoint, None for flat
                        per_seed[sd] = (orient * b if b is not None else None,
                                        f["method"])
            stats_rows.append({
                "family": family, "model": model, "curve": curve,
                "quantity": quantity,
                "axis_metric": metric, "orientation": orient,
                "method": fit["method"],
                "reason": fit.get("reason"),
                "irls_converged": fit.get("irls_converged"),
                "irls_iters": fit.get("irls_iters"),
                "irls_boundary": (round(orient * fit["irls_boundary"], 3)
                                  if fit.get("irls_boundary") is not None else None),
                "boundary": (round(orient * fit["boundary"], 3)
                             if fit.get("boundary") is not None else None),
                "bracket_fail_side_x": round(fail_x, 3) if fail_x is not None else None,
                "bracket_pass_side_x": round(pass_x, 3) if pass_x is not None else None,
                "slope": (round(fit["slope"], 3)
                          if fit.get("slope") is not None else None),
                "slope_note": fit.get("slope_note"),
                "level": fit.get("level"),
                "level_ci": fit.get("level_ci"),
                "bootstrap": boot,
                "n_runs": fit["n_runs"], "n_strata": fit["n_strata"],
                "mixed_strata": fit["mixed_strata"],
                "monotonicity_violations": fit["monotonicity_violations"],
                "excluded_rows": excluded,
                "defect_rows": defect_counts[(family, model)],
                "seeds_present": seeds,
                "per_seed_boundaries": {str(s): v for s, v in per_seed.items()},
            })

    # tables
    for family in sorted({r["family"] for r in stats_rows}):
        lines.append(f"== {family} " + "=" * max(0, 66 - len(family)))
        for r in [r for r in stats_rows if r["family"] == family]:
            if r["method"] == "flat":
                desc = (f"FLAT P={r['level']:.0f} over tested range "
                        f"(CI {r['level_ci'][0]:.2f}-{r['level_ci'][1]:.2f})"
                        " - no boundary")
            else:
                edge = (f" bracket fail@{r['bracket_fail_side_x']} / "
                        f"pass@{r['bracket_pass_side_x']}")
                bs = r["bootstrap"]
                desc = (f"{r['method']} boundary={r['boundary']}{edge}"
                        + (f" slope={r['slope']}" if r["slope"] else "")
                        + (" [slope from inversions only]"
                           if r.get("slope_note") else "")
                        + (f" CI[{bs['ci'][0]:.3f},{bs['ci'][1]:.3f}] "
                           f"sep_frac={bs['separation_fraction']:.2f}"
                           if bs else "")
                        + (f" VIOLATIONS={r['monotonicity_violations']}"
                           if r["monotonicity_violations"] else ""))
            lines.append(f"  {r['model']:>15} {r['curve']:<10} {desc}")
            if r["per_seed_boundaries"]:
                vals = [f"s{k}:{v[0]}" for k, v in
                        r["per_seed_boundaries"].items()]
                lines.append(f"  {'':>15} per-seed: " + " ".join(vals))
        lines.append("")
    if cat_rows:
        lines.append("== categorical ladders " + "=" * 44)
        for r in cat_rows:
            lines.append(
                f"  {r['family']} {r['model']:>15} {r['quantity']:<8} "
                f"{r['level']:<28} "
                f"{r['k']}/{r['n']} rate={r['rate']} "
                f"Wilson[{r['wilson_lo']},{r['wilson_hi']}]"
                + (f" excluded={r['excluded_rows']}"
                   if r["excluded_rows"] else ""))
        lines.append("")
    if skipped_flow:
        lines.append("== behavior-scalar families SKIPPED " + "=" * 31)
        for fam, metric in sorted(set(skipped_flow)):
            lines.append(f"  {fam} ({metric}): scored by flow_aggregate.py "
                         "(flow taxonomy + crossing_time_gap_s axis), "
                         "not the threading stats")
        lines.append("")

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "stats_rows.jsonl").open("w") as f:
        for r in stats_rows + cat_rows:
            f.write(json.dumps(r) + "\n")
    flat_fields = ["family", "model", "curve", "quantity", "axis_metric",
                   "orientation", "method", "boundary", "bracket_fail_side_x",
                   "bracket_pass_side_x", "slope", "n_runs", "n_strata",
                   "mixed_strata", "monotonicity_violations",
                   "excluded_rows"]
    with (args.out / "stats_rows.csv").open("w", newline="") as f:
        # LF line endings (repo policy); csv defaults to CRLF, which
        # git diff --check flags as trailing whitespace.
        w = csv.DictWriter(f, fieldnames=flat_fields, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for r in stats_rows:
            w.writerow(r)
    text = "\n".join(lines) + "\n"
    (args.out / "stats_tables.txt").write_text(text)
    write_stats_provenance(args.out, stamps, defects_pin)
    if not args.quiet:
        print(text)
    print(f"{len(stats_rows)} curve rows + {len(cat_rows)} categorical rows "
          f"-> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
