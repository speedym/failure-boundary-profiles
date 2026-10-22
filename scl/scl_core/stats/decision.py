"""Decision accuracy with its bootstrap interval (F87, 2026-10-14): one function for every grain.

The paper (Methods, Decision Accuracy): per family, the balanced mean of the clean-pass rate on the passable
(usable) side and the stop rate on the blocked (wait) side; per agent, the equal-weight mean over the families
with a stop side; "its 95% interval is a bootstrap that resamples runs within each family and side (10,000
replicates)". Resampling a side's n runs with replacement, k of them correct, is a binomial(n, k/n) draw, so a
replicate redraws every side that way and recomputes each family's balanced accuracy and the mean. The emitter
calls this with one family (the document's decision_summary.accuracy); Table 9 calls it with an agent's
families (the mean's interval). B and the seed are the fits' (scl_core.stats.boundary), one generator per
call, so an interval depends on its own sides alone and the determinism repeat holds.

Named cases (principle 14): a family without a side has no accuracy (value None, the case names the side);
a side where every run is the same (k == 0 or k == n) has no bootstrap variance and is listed as degenerate;
when every side is, the interval is the point ([0.50, 0.50] for an agent that passes every passable variant
cleanly and never stops, the paper's PlanT 2.0 at its third checkpoint) and the case says so.
"""
from __future__ import annotations

import numpy as np

from scl_core.stats.boundary import BOOTSTRAP_B, BOOTSTRAP_SEED

METHOD = ("balanced accuracy per family = (clean passes / passable runs + stops / blocked runs) / 2; the value is the "
          "equal-weight mean over the families given; the interval is the 2.5 and 97.5 percentiles of B bootstrap "
          "replicates, each redrawing every side as binomial(n, k/n) (that side's n runs resampled with replacement) "
          "and recomputing the family accuracies and their mean")


def accuracy(sides, B: int = BOOTSTRAP_B, seed: int = BOOTSTRAP_SEED) -> dict:
    """`sides`: one entry per family, {"family": id, "pass": {"n", "k"}, "wait": {"n", "k"}}, the balanced scope's
    two sides as the emitter sums them (clean passes on the passable or usable side, stops on the blocked or wait
    side). Returns the block the document and Table 9 carry: value, interval, case, the families, the degenerate
    sides, B, the seed and the method sentence."""
    sides = list(sides)
    base = {"value": None, "interval": None, "case": None, "families": [s["family"] for s in sides],
            "degenerate_sides": [], "bootstrap_B": int(B), "bootstrap_seed": int(seed), "method": METHOD}
    if not sides:
        return {**base, "case": "no accuracy: no family"}
    missing = [(s["family"], side) for s in sides for side in ("pass", "wait") if not s[side]["n"]]
    if missing:
        return {**base, "case": "no accuracy: a family lacks a side (" + ", ".join(f"{f} {side}" for f, side in missing) + ")",
                "missing_sides": [[f, side] for f, side in missing]}
    counts = [(int(s["pass"]["n"]), int(s["pass"]["k"]), int(s["wait"]["n"]), int(s["wait"]["k"])) for s in sides]
    for pn, pk, wn, wk in counts:
        if not (0 <= pk <= pn and 0 <= wk <= wn):
            raise ValueError(f"a side's k exceeds its n: {counts}")
    value = sum((pk / pn + wk / wn) / 2 for pn, pk, wn, wk in counts) / len(counts)
    degenerate = [[s["family"], side] for s, (pn, pk, wn, wk) in zip(sides, counts)
                  for side, n, k in (("pass", pn, pk), ("wait", wn, wk)) if k == 0 or k == n]
    rng = np.random.default_rng(seed)
    reps = np.zeros(int(B))
    for pn, pk, wn, wk in counts:
        reps += (rng.binomial(pn, pk / pn, size=int(B)) / pn + rng.binomial(wn, wk / wn, size=int(B)) / wn) / 2
    reps /= len(counts)
    lo, hi = float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))
    every = len(degenerate) == 2 * len(counts)
    return {**base, "value": round(value, 4), "interval": [round(lo, 4), round(hi, 4)],
            "case": "degenerate: every side is 0 or n, the interval is the point" if every else "measured",
            "degenerate_sides": degenerate}
