"""The one curve renderer (F82, register G6): a document curve becomes what a figure may draw.

The input is the shape scl_core.stats.emit_findings writes under `curves.<quantity>`: `points` (the
fit stage's data layer: per x the n, k, rate, Wilson interval and assignment, the vocabulary's point
extras beside), `fit` (the inference layer, the FIT_FIELDS of the one fit stage, or null where the
axis is categorical or no run counted) and `bootstrap` (the percentile interval over the replicates'
boundaries, or null). A stats row (stats_rows/v0.2) carries the same fields flat; `curve_from_stats_row`
lifts it into the document shape so the single-cohort form draws through the same function.

FIGURE POLICY (shared by every figure of both tools: F1, F1D, F2, F4, F8, F9, FI1, FI2):
- The DATA layer is always drawn: the observed per-x rates with their Wilson bands, counted by the fit
  stage from the rows' codings (never re-derived from outcome labels here).
- The INFERENCE layer draws only what the fit's case allows: `flat` is a level with its Wilson band and
  the note "no boundary in the tested range"; `bracket` is the boundary diamond with the estimator's
  interval (the bracket between adjacent tested x), never a curve through separated data; `irls` is
  the logistic sampled over the strata the fit used (never extrapolated past them), unless the slope
  is carried by inversions only (`slope_note`), which draws the point alone (`irls_pointonly`).
- The bootstrap interval, where the fit has one, travels beside the bracket as `band_ci` with its
  separation fraction; which of the two a renderer paints is presentation, decided in the tool.
- Every spec names the estimator's case (`reason`, principle 14) and the per-repetition boundaries
  the fit stage produced, so a figure of the spread across repetitions reads them, never refits.
- Numbers pass through at the fit stage's precision (x, rates and intervals to 4 decimals); nothing is
  re-rounded here.
"""
from __future__ import annotations

import math

from scl_core.stats.emit_findings import FIT_FIELDS
from scl_core.stats.fit_rows import QUANTITY

QUANTITIES = tuple(QUANTITY.values())            # ("p_go", "p_clean", "passage"): the document's curve keys
CODING_OF = {q: c for c, q in QUANTITY.items()}   # quantity -> the row coding it counts
KINDS = ("none", "flat", "bracket", "irls", "irls_pointonly")
OBSERVED_FIELDS = ("x", "rate", "n", "assignment", "wilson_95")   # the columns of a spec's `observed` rows
CURVE_SAMPLES = 61
FLAT_NOTE = "no boundary in the tested range"


def curve_from_stats_row(row: dict) -> dict:
    """A stats row in the document's curve shape ({curve, quantity, points, fit, bootstrap}): what
    emit_findings writes under curves.<quantity>, lifted from the flat row so one renderer serves both."""
    fitted = row.get("method") not in (None, "categorical", "empty")
    return {"curve": row["curve"], "quantity": row["quantity"], "points": row["points"],
            "fit": {k: row.get(k) for k in FIT_FIELDS} if fitted else None,
            "bootstrap": row.get("bootstrap") if fitted else None}


def observed(curve: dict) -> list:
    """The data layer: [x, rate, n, assignment, wilson_95] per counted stratum (OBSERVED_FIELDS); a
    stratum with no counted run (a declared defect, every run excluded) has no rate and is left out."""
    return [[p["x"], p["rate"], p["n"], p["assignment"], list(p["wilson_95"])]
            for p in curve["points"] if p.get("rate") is not None]


def defect_xs(curve: dict) -> list:
    """The x the declared scenario-defect register took out entirely (assignment `defect`, no rate)."""
    return [p["x"] for p in curve["points"] if p.get("assignment") == "defect"]


def per_repetition(fit: dict) -> list:
    """[{repetition, boundary, method}] from the fit stage's per-repetition fits, by repetition; empty
    where fewer than two repetitions exist (the fit stage writes none then)."""
    reps = fit.get("per_repetition_boundaries") or {}
    out = []
    for key in sorted(reps, key=lambda k: int(k)):
        boundary, method = reps[key]
        out.append({"repetition": int(key), "boundary": boundary, "method": method})
    return out


def logistic_samples(fit: dict, x_range=None) -> list:
    """The converged logistic sampled at CURVE_SAMPLES points over the strata the fit used
    (`fit.x_range`; `x_range` only where the fit carries none), in natural coordinates: the fit's
    slope is oriented, so it is turned back by the orientation."""
    lo, hi = fit.get("x_range") or x_range
    b1 = fit["slope"] * (fit.get("orientation") or 1)
    bd = fit["boundary"]
    xs = [lo + i * (hi - lo) / (CURVE_SAMPLES - 1) for i in range(CURVE_SAMPLES)]
    return [[round(x, 4), round(1 / (1 + math.exp(-max(-30.0, min(30.0, b1 * (x - bd))))), 4)] for x in xs]


def render_spec(curve: dict, x_range=None) -> dict:
    """What a renderer may draw for one document curve. Always: `observed`, `defect_xs`, `kind`. With a
    fit: `reason`, `per_repetition`, then by kind: flat -> level, level_ci, note; bracket / irls ->
    boundary, band ([fail-side x, pass-side x], the estimator's interval), band_ci and
    separation_fraction where a bootstrap ran, and for irls the sampled `curve` (or the kind
    irls_pointonly when the slope is carried by inversions only)."""
    spec = {"observed": observed(curve), "defect_xs": defect_xs(curve), "kind": "none"}
    fit, boot = curve.get("fit"), curve.get("bootstrap")
    if fit is None:
        return spec
    spec["reason"] = fit.get("reason")
    spec["per_repetition"] = per_repetition(fit)
    method = fit["method"]
    if method == "flat":
        spec.update(kind="flat", level=fit.get("level"), level_ci=fit.get("level_ci"), note=FLAT_NOTE)
        return spec
    if method not in ("bracket", "irls"):
        raise ValueError(f"a fitted curve's method is flat, bracket or irls, got {method!r}")
    spec.update(kind=method, boundary=fit.get("boundary"),
                band=[fit.get("bracket_fail_side_x"), fit.get("bracket_pass_side_x")])
    if boot and boot.get("ci"):
        spec["band_ci"] = list(boot["ci"])
        spec["separation_fraction"] = boot.get("separation_fraction")
    if method == "irls":
        if fit.get("slope") is not None and fit.get("boundary") is not None and not fit.get("slope_note"):
            spec["curve"] = logistic_samples(fit, x_range)
        else:
            spec["kind"] = "irls_pointonly"
    return spec


def summary(spec: dict) -> dict:
    """The inference layer alone (no observed rows, no sampled curve): what a figure of boundaries
    keeps per curve (F8's checkpoints)."""
    return {k: spec.get(k) for k in ("kind", "reason", "boundary", "band", "band_ci", "level", "level_ci")}


def x_range_of(curves, x_range=None) -> list | None:
    """[min, max] over every numeric x any of the curves' points carry (the tested range), or the
    given range when the curves carry none; None for a categorical axis."""
    xs = [p["x"] for c in curves for p in c["points"] if not isinstance(p["x"], str)]
    if not xs:
        return list(x_range) if x_range else None
    return [round(min(xs), 4), round(max(xs), 4)]


def boundary_text(curve: dict, unit: str = "m") -> str:
    """One boundary as its quotable string: the quoting rules the tables follow (flat -> the level with
    its Wilson floor; a bracket -> the estimator's interval first, the bootstrap interval where it is
    not a point; a graded logistic (no separated replicate) -> approximate with the caveat; every case
    other than the plain ones named after a semicolon, principle 14)."""
    fit = curve.get("fit")
    if fit is None:
        return "-"
    why = fit.get("reason")
    tag = f"; {why.replace('_', ' ')}" if why and why not in ("all_success", "all_failure", "separated", "converged") else ""
    if fit["method"] == "flat":
        ci = fit.get("level_ci")
        if ci:
            return f"none in range (level {fit['level']:.2f}, >= {ci[0]:.2f}{tag})"
        return f"none in range (level {fit['level']:.2f}{tag})"
    bd = fit.get("boundary")
    boot = curve.get("bootstrap") or {}
    ci = boot.get("ci")
    ci_txt = f", CI [{ci[0]:.3f}, {ci[1]:.3f}]" if ci and ci[0] != ci[1] else ""
    bracket = f"bracket [{fit['bracket_fail_side_x']}, {fit['bracket_pass_side_x']}]"
    if fit["method"] == "bracket":
        return f"{bd:.3f} {unit}, {bracket}{ci_txt}{tag}"
    if boot.get("separation_fraction") == 0.0:
        return f"~{bd:.2f} {unit}, graded (wide CI{tag})"
    if ci_txt:
        return f"{bd:.3f} {unit}{ci_txt}{tag}"
    return f"{bd:.3f} {unit}, {bracket}{tag}"
