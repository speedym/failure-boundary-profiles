"""Boundary statistics: psychometric curves over scored runs (public, MIT).

Consumes analysis.score_runs output directories (scored_rows.jsonl;
multiple dirs pool) and emits per (family, model, curve) boundary
estimates with honest intervals. Offline; requires numpy (the one
dependency - the seeded bootstrap RNG is part of the method's identity).

    python -m analysis.boundary_stats <scored_dir> [<scored_dir>...] --out stats/

STATS POLICY (judgment constants and rules; all else is mechanical).

- A row of scored_rows is ONE RUN - one Bernoulli draw (same-seed
  outcomes can bifurcate). Curves estimate P(outcome | x).
- Two curves per (family, model):
    commitment  P(go):    refusal/correct_stop -> 0; clean_thread,
                contact_*, late_commit -> 1 (the agent committed).
    competence  P(clean_thread): clean_thread -> 1; late_commit ->
                stratum DROPPED (neither passed nor failed the passage);
                other outcomes -> 0.
  anomaly_* outcomes are EXCLUDED from both curves and counted per
  row - reported, never coded.
- The axis is the family's swept MEASURED metric (see FAMILY_AXES),
  never the target. Categorical families route to per-level Wilson
  estimates, no fitting.
- Orientation (does success rise or fall with x?) is derived from the
  data trend, with metric semantics as fallback (width: rises,
  intrusion: falls). Internally the axis is oriented so success is
  increasing; outputs report natural units with fail-side and
  pass-side bracket edges named explicitly.
- Fit decision tree, in order:
    flat       all-success or all-failure: no boundary in the tested
               range - a first-class result.
    bracket    complete separation (max fail-x < min success-x on the
               oriented axis, checked BEFORE fitting): boundary =
               bracket midpoint, interval = the bracket itself.
    irls       mixed strata: logistic MLE by IRLS. Quasi-separation
               guard: |slope| * axis_range > SLOPE_CAP -> method
               "irls_guarded": crossing reported, slope
               UNIDENTIFIABLE, interval from the bracket.
- Bracket: prefix/suffix-clean construction (lower edge = last x whose
  entire fail-side prefix has rate <= 0.5; upper = first x whose entire
  pass-side suffix has rate > 0.5) - non-monotone points widen the
  bracket instead of being averaged away. Violations are counted.
- Wilson intervals (z=1.96) for all per-stratum and per-level rates -
  never Wald.
- Bootstrap activates ONLY when some stratum has n>1 (with n=1
  everywhere, within-stratum resampling is an identity and any CI
  would be fake - the honest interval is the bracket). When active:
  B=10000 replicates resampling runs within strata, refit via the
  same tree, percentile 95% CI over boundaries, with the separation
  fraction and method stability reported alongside.
  RNG: numpy default_rng(20260805).
- Per-seed rows accompany the pooled estimate wherever >=2 seeds
  exist. Descriptive only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from .curves import (CATEGORICAL_FAMILIES, FAMILY_METRIC, code_commitment,
                     code_competence)

SLOPE_CAP = 50.0
BOOTSTRAP_B = 10_000
BOOTSTRAP_SEED = 20260805
WILSON_Z = 1.96
ORIENTATION_FALLBACK = {"minimum_free_corridor_width": +1,
                        "maximum_route_intrusion": -1}

# family -> (axis metric, categorical?). Derived in the internal pipeline
# from each family's plan (the scheduled factor); frozen here for the
# public release. fm_003's axis is the categorical asset identity.
FAMILY_AXES = {fam: (metric, False) for fam, metric in FAMILY_METRIC.items()}
for fam in CATEGORICAL_FAMILIES:
    FAMILY_AXES[fam] = ("asset_identity", True)


def wilson(k, n, z=WILSON_Z):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c / d - h), min(1.0, c / d + h))


# --- fit machinery (oriented axis: success increases with x) ---------------

def irls(strata):
    b0, b1, iters = 0.0, 0.0, 0
    for iters in range(1, 51):
        Sw = Swx = Swxx = Swz = Swxz = 0.0
        for x, n, k in strata:
            eta = b0 + b1 * x
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, eta))))
            w = max(n * p * (1 - p), 1e-12)
            z = eta + (k - n * p) / w
            Sw += w; Swx += w * x; Swxx += w * x * x
            Swz += w * z; Swxz += w * x * z
        det = Sw * Swxx - Swx * Swx
        if abs(det) < 1e-12:
            break
        nb0 = (Swxx * Swz - Swx * Swxz) / det
        nb1 = (Sw * Swxz - Swx * Swz) / det
        done = abs(nb0 - b0) < 1e-8 and abs(nb1 - b1) < 1e-8
        b0, b1 = nb0, nb1
        if done:
            break
    return b0, b1, iters


def bracket(strata):
    """Prefix/suffix-clean bracket on the oriented axis."""
    lo = None
    for x, n, k in strata:
        if k / n <= 0.5:
            lo = x
        else:
            break
    hi = None
    for x, n, k in reversed(strata):
        if k / n > 0.5:
            hi = x
        else:
            break
    return lo, hi


def inversions(strata):
    bad = []
    prev_x, prev_r = None, None
    for x, n, k in strata:
        r = k / n
        if prev_r is not None and r < prev_r - 1e-9:
            bad.append((prev_x, x))
        prev_x, prev_r = x, r
    return bad


def analyze(strata):
    """strata: sorted [(oriented_x, n, k)]. Returns the fit dict."""
    xs = [x for x, n, k in strata]
    axis_range = max(xs) - min(xs) if len(xs) > 1 else 0.0
    inv = inversions(strata)
    out = {"n_strata": len(strata),
           "n_runs": sum(n for _, n, _ in strata),
           "mixed_strata": sum(1 for _, n, k in strata if 0 < k < n),
           "monotonicity_violations": len(inv),
           "violation_pairs": inv}
    if all(k == n for _, n, k in strata):
        n_tot = out["n_runs"]
        out.update(method="flat", level=1.0,
                   level_ci=wilson(n_tot, n_tot),
                   boundary=None, interval=None, slope=None)
        return out
    if all(k == 0 for _, n, k in strata):
        out.update(method="flat", level=0.0,
                   level_ci=wilson(0, out["n_runs"]),
                   boundary=None, interval=None, slope=None)
        return out
    lo, hi = bracket(strata)
    fail_xs = [x for x, n, k in strata if k < n]
    succ_xs = [x for x, n, k in strata if k > 0]
    if max(fail_xs) < min(succ_xs):  # complete separation
        out.update(method="bracket",
                   boundary=(lo + hi) / 2 if lo is not None and hi is not None else None,
                   interval=(lo, hi), slope=None, level=None)
        return out
    b0, b1, iters = irls(strata)
    boundary = -b0 / b1 if b1 else None
    if b1 and abs(b1) * axis_range > SLOPE_CAP:
        out.update(method="irls_guarded", boundary=boundary,
                   interval=(lo, hi), slope=None, level=None,
                   irls_iters=iters)
    else:
        out.update(method="irls", boundary=boundary, interval=(lo, hi),
                   slope=b1, level=None, irls_iters=iters,
                   p10=(math.log(0.1 / 0.9) - b0) / b1 if b1 else None,
                   p90=(math.log(0.9 / 0.1) - b0) / b1 if b1 else None)
        if out["mixed_strata"] == 0:
            out["slope_note"] = "from_inversions_only"
    return out


def bootstrap(strata, rng):
    """Percentile CI over boundaries; None when inactive (all n==1)."""
    if not any(n > 1 for _, n, _ in strata):
        return None
    boundaries, methods = [], []
    for _ in range(BOOTSTRAP_B):
        rep = []
        for x, n, k in strata:
            kk = int(rng.binomial(n, k / n)) if n > 1 else k
            rep.append((x, n, kk))
        r = analyze(rep)
        methods.append(r["method"])
        if r["boundary"] is not None:
            boundaries.append(r["boundary"])
        elif r["interval"] and None not in r["interval"]:
            boundaries.append(sum(r["interval"]) / 2)
    if not boundaries:
        return None
    arr = np.sort(np.array(boundaries))
    return {
        "ci": (float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))),
        "separation_fraction": sum(1 for m in methods if m != "irls") / len(methods),
        "replicates_used": len(boundaries),
    }


# --- input plumbing --------------------------------------------------------

def load_rows(dirs):
    rows = []
    for d in dirs:
        p = Path(d) / "scored_rows.jsonl"
        if not p.exists():
            print(f"WARNING: no scored_rows.jsonl under {d}", file=sys.stderr)
            continue
        rows += [json.loads(line) for line in p.read_text().splitlines()]
    return rows


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
    args = ap.parse_args()

    rows = load_rows(args.scored_dirs)
    if not rows:
        print("no scored rows found", file=sys.stderr)
        return 1
    rng = np.random.default_rng(BOOTSTRAP_SEED)

    stats_rows, cat_rows = [], []
    lines = [__doc__[__doc__.index("STATS POLICY"):].rstrip(), ""]

    by_fm = defaultdict(list)
    for r in rows:
        by_fm[(r["family"], r["model"])].append(r)

    for (family, model) in sorted(by_fm):
        frows = by_fm[(family, model)]
        if family not in FAMILY_AXES:
            print(f"NOTE: {family} not in FAMILY_AXES - skipped",
                  file=sys.stderr)
            continue
        metric, is_cat = FAMILY_AXES[family]

        if is_cat:
            per_level = defaultdict(lambda: [0, 0])
            for r in frows:
                y = code_competence(r["outcome"])
                if y is None:
                    continue
                per_level[str(r["target"])][0] += 1
                per_level[str(r["target"])][1] += y
            for level, (n, k) in sorted(per_level.items()):
                lo, hi = wilson(k, n)
                cat_rows.append({
                    "family": family, "model": model, "level": level,
                    "n": n, "k": k, "rate": round(k / n, 3),
                    "wilson_lo": round(lo, 3), "wilson_hi": round(hi, 3),
                    "method": "categorical"})
            continue

        for curve, coder in (("commitment", code_commitment),
                             ("competence", code_competence)):
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
                    if fit["method"] in ("irls", "irls_guarded", "bracket")
                    else None)
            if boot:
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
                        b = f["boundary"]
                        if b is None and f.get("interval") \
                                and None not in f["interval"]:
                            b = sum(f["interval"]) / 2
                        per_seed[sd] = (orient * b if b is not None else None,
                                        f["method"])
            stats_rows.append({
                "family": family, "model": model, "curve": curve,
                "axis_metric": metric, "orientation": orient,
                "method": fit["method"],
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
                f"  {r['family']} {r['model']:>15} {r['level']:<28} "
                f"{r['k']}/{r['n']} rate={r['rate']} "
                f"Wilson[{r['wilson_lo']},{r['wilson_hi']}]")
        lines.append("")

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "stats_rows.jsonl").open("w") as f:
        for r in stats_rows + cat_rows:
            f.write(json.dumps(r) + "\n")
    flat_fields = ["family", "model", "curve", "axis_metric", "orientation",
                   "method", "boundary", "bracket_fail_side_x",
                   "bracket_pass_side_x", "slope", "n_runs", "n_strata",
                   "mixed_strata", "monotonicity_violations",
                   "excluded_rows"]
    with (args.out / "stats_rows.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=flat_fields, extrasaction="ignore")
        w.writeheader()
        for r in stats_rows:
            w.writerow(r)
    text = "\n".join(lines) + "\n"
    (args.out / "stats_tables.txt").write_text(text)
    if not args.quiet:
        print(text)
    print(f"{len(stats_rows)} curve rows + {len(cat_rows)} categorical rows "
          f"-> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
