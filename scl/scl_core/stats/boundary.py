"""Boundary estimator: psychometric fit over Bernoulli strata.

Promoted VERBATIM from adapters/fail2drive/scripts/multi_model_eval/
boundary_stats.py (stats v1, Demo B item 7) on 2026-09-07 so that both
backends' scorers share one estimator (S15: "boundary estimator pinned
ONCE"). boundary_stats.py imports these names and its committed goldens
(adapters/fail2drive/tests/fixtures/fm_004_seed0_stats) are the
byte-for-byte inertness proof of the move.

Contract: strata are sorted [(oriented_x, n, k)] with success increasing
along x (the caller orients the axis). Fit decision tree, in order; every
result names its case in `reason` and carries the fit's own diagnostics
(irls_converged, irls_iters, irls_slope, irls_boundary, x_range), and the
DECISION keys on those diagnostics, never on a cap (2026-10-04, the FI2
diamond episode: the fit had run to its round cap on quasi-separated data,
a slope cap stood in for the convergence it had, and the diverged midpoint
was drawn as a measured boundary; principle 14):
  flat     no 50 % crossing inside the tested range; level = the pooled
           rate with its Wilson interval. Reasons: all_success,
           all_failure, empty, wrong_sign (the converged logistic falls
           with x: the data do not have the assumed shape), out_of_range
           (the converged midpoint lies outside the tested x), no_crossing
           (the logistic did not converge AND the bracket has no fail side
           or no pass side: nothing places a crossing in the range; e.g. a
           go at every gap but one mixed stratum)
  bracket  boundary = bracket midpoint, interval = the bracket. Reasons:
           separated (every failing stratum below every succeeding one),
           not_converged (the logistic has no finite solution: quasi-
           separation, the round cap or a singular matrix; the midpoint
           such a fit stops at is an artifact and is kept only as
           irls_boundary)
  irls     a converged logistic with a positive slope whose midpoint lies
           inside the tested range: boundary, slope, p10/p90; a fit carried
           only by inversions (no mixed stratum) is labeled slope_note. The
           prefix/suffix bracket travels as the interval and is a reading,
           not a veto: a midpoint outside a two-sided bracket (a stratum at
           exactly 0.5 sits on the bracket's fail side by convention; an
           inversion pulls a smooth fit) is labeled bracket_note
           (fit_below_bracket / fit_above_bracket) and keeps its fit; a
           bracket left open by an inversion at an edge of the range is
           labeled bracket_open (low / high / both) with the half-open
           interval (the flow families' time-axis fits are of this shape).
           Only a fit that did not apply (not converged, wrong sign, out of
           range) loses its number.
Wilson intervals at z = 1.96; bootstrap percentile CI over boundaries
with a fixed RNG seed, active only when some stratum has n > 1.
"""
import math

import numpy as np

IRLS_ROUNDS = 50
BOOTSTRAP_B = 10_000
BOOTSTRAP_SEED = 20260805
WILSON_Z = 1.96


def wilson(k: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


# --- fit machinery (oriented axis: success increases with x) ---------------

def irls_full(strata):
    """IRLS logistic fit. Returns (b0, b1, rounds, converged): converged is
    False when the round cap was hit or the normal matrix went singular
    (quasi-separated data have no finite maximum-likelihood fit; the
    coefficients then keep climbing and the midpoint pins itself to the
    mixed stratum - a caller never reads that midpoint as a measurement)."""
    b0, b1, iters, converged = 0.0, 0.0, 0, False
    for iters in range(1, IRLS_ROUNDS + 1):
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
            converged = True
            break
    return b0, b1, iters, converged


def irls(strata):
    """(b0, b1, rounds) - the fit without its convergence flag; analyze()
    uses irls_full and decides on the flag."""
    return irls_full(strata)[:3]


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
    """strata: sorted [(oriented_x, n, k)]. Returns the fit dict (see the
    module docstring for the cases and their reasons)."""
    xs = [x for x, n, k in strata]
    inv = inversions(strata)
    n_tot = sum(n for _, n, _ in strata)
    k_tot = sum(k for _, _, k in strata)
    out = {"n_strata": len(strata),
           "n_runs": n_tot,
           "mixed_strata": sum(1 for _, n, k in strata if 0 < k < n),
           "monotonicity_violations": len(inv),
           "violation_pairs": inv,
           "x_range": (min(xs), max(xs)) if xs else None,
           "irls_converged": None, "irls_iters": None,
           "irls_slope": None, "irls_boundary": None}

    def flat(reason, level):
        out.update(method="flat", reason=reason, level=level,
                   level_ci=wilson(k_tot, n_tot), boundary=None,
                   interval=None, slope=None)
        return out

    def bracket_case(reason, lo, hi):
        out.update(method="bracket", reason=reason,
                   boundary=(lo + hi) / 2, interval=(lo, hi),
                   slope=None, level=None)
        return out

    if not strata:
        return flat("empty", None)
    if all(k == n for _, n, k in strata):
        return flat("all_success", 1.0)
    if all(k == 0 for _, n, k in strata):
        return flat("all_failure", 0.0)
    lo, hi = bracket(strata)
    fail_xs = [x for x, n, k in strata if k < n]
    succ_xs = [x for x, n, k in strata if k > 0]
    if max(fail_xs) < min(succ_xs):  # complete separation
        return bracket_case("separated", lo, hi)
    b0, b1, iters, converged = irls_full(strata)
    irls_boundary = -b0 / b1 if b1 else None
    out.update(irls_converged=converged, irls_iters=iters, irls_slope=b1,
               irls_boundary=irls_boundary)
    level = k_tot / n_tot
    two_sided = lo is not None and hi is not None
    if not converged:
        # no finite fit: the bracket answers when it has two sides; an open
        # bracket (success already at the lowest x, or failure still at the
        # highest) leaves nothing that places a crossing in the range
        return bracket_case("not_converged", lo, hi) if two_sided else flat("no_crossing", level)
    if b1 <= 0:
        return flat("wrong_sign", level)
    x_lo, x_hi = out["x_range"]
    if not (x_lo <= irls_boundary <= x_hi):
        return flat("out_of_range", level)
    out.update(method="irls", reason="converged", boundary=irls_boundary,
               interval=(lo, hi), slope=b1, level=None,
               p10=(math.log(0.1 / 0.9) - b0) / b1,
               p90=(math.log(0.9 / 0.1) - b0) / b1)
    if two_sided and not (lo <= irls_boundary <= hi):
        # the bracket is a reading beside the fit, never a veto (2026-10-04: a voiding rule here
        # wiped the non-monotone spacing sweep's boundaries); the disagreement is named
        out["bracket_note"] = "fit_below_bracket" if irls_boundary < lo else "fit_above_bracket"
    if not two_sided:
        # an inversion at an edge of the range leaves the direct reading
        # without a clean prefix or suffix; the converged fit stands, the
        # ragged edge is named
        out["bracket_open"] = "both" if lo is None and hi is None else ("low" if lo is None else "high")
    # a fit can be non-separated purely through rate INVERSIONS
    # (a v11: one clean pass inside the refusal zone) with no
    # bifurcating stratum anywhere - the slope is then carried by
    # anomalous single runs, not by observed randomness; label it
    if out["mixed_strata"] == 0:
        out["slope_note"] = "from_inversions_only"
    return out


def bootstrap(strata, rng):
    """Percentile CI over the replicates' headline boundaries; None when
    inactive: every stratum has n == 1, or every stratum is 0 or n (then a
    binomial resample returns the sample itself, every replicate is the
    original fit, and the "interval" would be a point: the bracket IS the
    interval). Replicates that come out flat carry no boundary and are
    dropped, counted in replicates_dropped; method_fractions says what the
    replicates were."""
    if not any(n > 1 for _, n, _ in strata):
        return None
    if all(k == 0 or k == n for _, n, k in strata):
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
    if not boundaries:
        return None
    arr = np.sort(np.array(boundaries))
    return {
        "ci": (float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))),
        "separation_fraction": sum(1 for m in methods if m != "irls") / len(methods),
        "method_fractions": {m: round(sum(1 for x in methods if x == m) / len(methods), 4)
                             for m in sorted(set(methods))},
        "replicates_used": len(boundaries),
        "replicates_dropped": len(methods) - len(boundaries),
    }
