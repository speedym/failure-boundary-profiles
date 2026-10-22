"""The one fit stage (F82, principle 15): run rows of any chain become curves.

Until 2026-10-11 the counting per variant, the orientation, the bootstrap call, the diagnostics and
their rounding lived three times, around the one estimator (scl_core.stats.boundary): the threading
statistics tool, the flow emitter and the warehouse emitter. This module is the one place, over the
one row shape (scl_core.run_rows), and the three codings on the row are what it counts; nothing here
reads an outcome label, an axis name list or a chain.

STATS POLICY (the judgment constants and rules; all else is mechanical):
- A row is ONE RUN, one Bernoulli draw. Curves estimate P(coding | x) per (family, model) for each
  of the three codings: commitment (quantity p_go), clean (p_clean), passage. A row whose coding is
  null is excluded from that curve and counted (excluded_rows); a row the scenario-defect register
  names is out of every curve and counted (defect_rows), its stratum kept under the assignment
  `defect` with the uninformative Wilson interval, never silently gone.
- The axis is the row's x, with its basis: a numeric axis (measured or nominal) is fitted; a level
  axis (categorical) is a ladder of per-level Wilson rates, nothing is monotone in an identity.
- Orientation (does success rise or fall with x?) is derived from the data trend, with the
  metric's semantics as the fallback (ORIENTATION_FALLBACK: a width rises, an intrusion falls, a
  crossing gap rises); outputs report natural units with the fail-side and pass-side bracket edges
  named.
- The fit decision tree, the bracket construction, the Wilson intervals and the bootstrap are the
  estimator's (scl_core.stats.boundary); the bootstrap runs for irls and bracket fits with a FRESH
  generator per fit (BOOTSTRAP_SEED), so an interval depends on its own strata alone, never on which
  fits ran before it (register G21; the threading tool shared one generator across its loop).
- Per-repetition fits accompany the pooled estimate wherever two or more repetitions exist:
  descriptive, the spread across repetitions.
- One precision: x to 4 decimals, rates and intervals to 4, as the warehouse emitter rounded.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from scl_core import run_rows as rr
from scl_core import scenario_defects
from scl_core.records import render
from scl_core.stats.boundary import BOOTSTRAP_B, BOOTSTRAP_SEED, analyze, bootstrap, wilson

STATS_KIND = "stats_rows/v0.2"
STATS_STAMP_KIND = "stats_provenance/v0.2"
QUANTITY = {"commitment": "p_go", "clean": "p_clean", "passage": "passage"}
ORIENTATION_FALLBACK = {"minimum_free_corridor_width": +1, "maximum_route_intrusion": -1,
                        "measured_gap_m": +1, "crossing_time_gap_s": +1}


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def orientation_for(strata_natural, metric):
    """+1 success rises with x, -1 falls: the data trend, the metric's semantics as the fallback."""
    pts = [(x, k / n) for x, n, k in strata_natural if n]
    if len(pts) >= 2:
        mx = sum(p[0] for p in pts) / len(pts)
        mr = sum(p[1] for p in pts) / len(pts)
        cov = sum((x - mx) * (r - mr) for x, r in pts)
        if abs(cov) > 1e-9:
            return 1 if cov > 0 else -1
    return ORIENTATION_FALLBACK.get(metric, 1)


def natural_edges(fit, orientation):
    """The bracket edges back in natural units, named by side (fail-side x, pass-side x). A half-open
    bracket (`bracket_open` names the missing side) keeps its known edge: the first build of the one
    fit dropped both edges when one was open, and the flow families' one-sided brackets ([4.032, open],
    [open, 2.5]) left the documents and the figures (found by the figure conversion, 2026-10-11)."""
    interval = fit.get("interval")
    if not interval:
        return None, None
    lo, hi = interval
    return (None if lo is None else orientation * lo), (None if hi is None else orientation * hi)


def points_for(rows, curve, vocabulary=None):
    """The data layer of one curve over one (family, model): per x, n and k under the coding, the
    rate and its Wilson interval, the variant's assignment (`mixed` when its runs disagree), the
    defect runs, and the vocabulary's point extras. A stratum with every run registered keeps its
    place under the assignment `defect`."""
    by_x = defaultdict(list)
    for r in rows:
        by_x[r["x"]].append(r)
    pts = []
    for x in sorted(by_x, key=lambda v: (isinstance(v, str), v)):
        group = by_x[x]
        counted = [r for r in group if not r.get("_defect") and r[curve] is not None]
        defect_runs = sum(1 for r in group if r.get("_defect"))
        assignments = sorted({r["assignment"] for r in group})
        assignment = assignments[0] if len(assignments) == 1 else "mixed"
        n, k = len(counted), sum(1 for r in counted if r[curve] is True)
        if n == 0 and defect_runs and defect_runs == len(group):
            lo, hi = wilson(0, 0)
            pt = {"x": x, "assignment": "defect", "n": 0, "k": 0, "rate": None, "wilson_95": [_r(lo), _r(hi)],
                  "defect_runs": defect_runs}
        else:
            if n == 0:
                continue
            lo, hi = wilson(k, n)
            pt = {"x": x, "assignment": assignment, "n": n, "k": k, "rate": _r(k / n), "wilson_95": [_r(lo), _r(hi)]}
            if defect_runs:
                pt["defect_runs"] = defect_runs
            excluded = len(group) - n - defect_runs
            if excluded:
                pt["excluded"] = excluded
        if vocabulary is not None:
            # the extras over the runs IN the point (the counted ones: a run the coding excludes, an anomaly
            # or a registered cell, is out of n, k and the split alike; the first build summed the whole
            # stratum and the fine-tune's blocked width read five gate crossings over three counted runs,
            # found by the 2026-10-11 re-stamp's proof); a stratum with every run registered keeps the
            # extras of its registered runs (the flow band `defect` is theirs)
            pt.update(rr.point_extras(vocabulary, counted or group))
        pts.append(pt)
    return pts


def _strata(points):
    return sorted((p["x"], p["n"], p["k"]) for p in points if p["n"] and not isinstance(p["x"], str))


def fit_curve(rows, curve, metric, vocabulary=None):
    """One (family, model, curve): the points, the fit in natural units, the bootstrap with a fresh
    generator, the per-repetition fits. Rows carry `_defect` when the register names them."""
    points = points_for(rows, curve, vocabulary)
    excluded = sum(1 for r in rows if not r.get("_defect") and r[curve] is None)
    defect_rows = sum(1 for r in rows if r.get("_defect"))
    base = {"curve": curve, "quantity": QUANTITY[curve], "axis_metric": metric, "points": points,
            "excluded_rows": excluded, "defect_rows": defect_rows}
    if rows and rows[0]["x_basis"] == "level":
        return dict(base, method="categorical", reason="categorical_axis", orientation=None, boundary=None,
                    n_runs=sum(p["n"] for p in points), n_strata=len(points))
    nat = _strata(points)
    if not nat:
        return dict(base, method="empty", reason="no_counted_run", orientation=None, boundary=None, n_runs=0, n_strata=0)
    orient = orientation_for(nat, metric)
    oriented = sorted((orient * x, n, k) for x, n, k in nat)
    fit = analyze(oriented)
    boot = bootstrap(oriented, np.random.default_rng(BOOTSTRAP_SEED)) if fit["method"] in ("irls", "bracket") else None
    if boot:
        boot = dict(boot, ci=[_r(v) for v in sorted((orient * boot["ci"][0], orient * boot["ci"][1]))],
                    separation_fraction=_r(boot["separation_fraction"]))
    fail_x, pass_x = natural_edges(fit, orient)
    reps = sorted({r["repetition"] for r in rows if r["repetition"] is not None})
    per_rep = {}
    if len(reps) >= 2:
        for rep in reps:
            nat_r = _strata(points_for([r for r in rows if r["repetition"] == rep], curve))
            if nat_r:
                f = analyze(sorted((orient * x, n, k) for x, n, k in nat_r))
                b = f["boundary"]
                per_rep[str(rep)] = [_r(orient * b) if b is not None else None, f["method"]]
    return dict(base,
                orientation=orient, method=fit["method"], reason=fit.get("reason"),
                irls_converged=fit.get("irls_converged"), irls_iters=fit.get("irls_iters"),
                irls_boundary=_r(orient * fit["irls_boundary"]) if fit.get("irls_boundary") is not None else None,
                boundary=_r(orient * fit["boundary"]) if fit.get("boundary") is not None else None,
                interval=[_r(fail_x), _r(pass_x)] if fail_x is not None or pass_x is not None else None,
                bracket_fail_side_x=_r(fail_x), bracket_pass_side_x=_r(pass_x),
                slope=_r(fit["slope"]) if fit.get("slope") is not None else None,
                slope_note=fit.get("slope_note"), bracket_note=fit.get("bracket_note"), bracket_open=fit.get("bracket_open"),
                level=_r(fit.get("level")), level_ci=[_r(v) for v in fit["level_ci"]] if fit.get("level_ci") else None,
                p10=_r(orient * fit["p10"]) if fit.get("p10") is not None else None,
                p90=_r(orient * fit["p90"]) if fit.get("p90") is not None else None,
                x_range=[_r(min(x for x, _, _ in nat)), _r(max(x for x, _, _ in nat))],
                bootstrap=boot, bootstrap_B=BOOTSTRAP_B, bootstrap_seed=BOOTSTRAP_SEED,
                n_runs=fit["n_runs"], n_strata=fit["n_strata"], mixed_strata=fit["mixed_strata"],
                monotonicity_violations=fit["monotonicity_violations"],
                repetitions_present=reps, per_repetition_boundaries=per_rep)


def fit_rows(rows, vocabulary=None, defects_doc=None) -> list:
    """Every (family, model) in the rows, the three curves each: the stats rows in (family, model,
    curve) order. The register marks rows before anything is counted."""
    marked = []
    for r in rows:
        m = dict(r)
        m["_defect"] = scenario_defects.entry_for_cell(defects_doc, r["family"], r["variant"], r["repetition"]) is not None
        marked.append(m)
    by_fm = defaultdict(list)
    for r in marked:
        by_fm[(r["family"], r["model"])].append(r)
    out = []
    for (family, model) in sorted(by_fm):
        frows = by_fm[(family, model)]
        metrics = {r["x_metric"] for r in frows}
        if len(metrics) != 1:
            raise ValueError(f"{family}/{model}: rows on {len(metrics)} axis metrics {sorted(metrics)}; one family has one axis")
        bases = {r["x_basis"] for r in frows}
        if len(bases) != 1:
            raise ValueError(f"{family}/{model}: rows on {len(bases)} axis bases {sorted(bases)}; one family has one axis")
        metric = next(iter(metrics))
        for curve in rr.CODINGS:
            out.append({"stats_kind": STATS_KIND, "family": family, "model": model, "x_basis": frows[0]["x_basis"],
                        **fit_curve(frows, curve, metric, vocabulary)})
    return out


def tool_pins(root) -> dict:
    """The fit stage and the estimator it calls, pinned by path and hash in this tree: the ONE place
    their current identity is computed (register K11); the stats stamp, the pool record and both
    certifiers read it here."""
    here = Path(__file__).resolve()
    return {"fit": rr.pin(here, root), "estimator": rr.pin(here.with_name("boundary.py"), root)}


def write_stats(out_dir, stats, *, rows_stamp, root, defects=None, note="") -> dict:
    """<out>/stats_rows.jsonl and stats_provenance.json: the fit tool and the estimator pinned, the rows'
    stamp carried (scorer, inputs digest, n_rows), the register pinned or null."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stats_rows.jsonl").write_text("".join(json.dumps(s, allow_nan=False) + "\n" for s in stats))
    doc = {"provenance_kind": STATS_STAMP_KIND,
           **tool_pins(root),
           "rows": {"scorer": rows_stamp["scorer"], "vocabulary": rows_stamp["vocabulary"],
                    "input_files_sha256": rows_stamp["input_files_sha256"], "n_rows": rows_stamp["n_rows"]},
           "scenario_defects": defects,
           "n_stats_rows": len(stats),
           "note": note or ("content identity only, no timestamps; a reader refuses stats whose fit or estimator "
                            "moved against the tree (scl_core.stats.fit_rows.check_stats)")}
    (out_dir / "stats_provenance.json").write_text(render(doc))
    return doc


def read_stats(stats_dir) -> list:
    return [json.loads(l) for l in (Path(stats_dir) / "stats_rows.jsonl").read_text().splitlines() if l.strip()]


def check_stats(stats_dir, root, rows_stamp=None) -> dict:
    """The one admission check for a stats folder: the fit tool and the estimator hash to the tree
    now, and, when the rows' stamp is given, the stats came from exactly those rows (scorer,
    vocabulary and inputs digest equal). Returns the stamp."""
    path = Path(stats_dir) / "stats_provenance.json"
    if not path.is_file():
        raise ValueError(f"{stats_dir}: no stats_provenance.json (unstamped fits)")
    doc = json.loads(path.read_text())
    if doc.get("provenance_kind") != STATS_STAMP_KIND:
        raise ValueError(f"{path}: provenance_kind {doc.get('provenance_kind')!r}; this reader reads {STATS_STAMP_KIND}")
    for role in ("fit", "estimator"):
        p = doc[role]
        target = Path(root) / p["path"]
        now = rr.sha256_file(target) if target.is_file() else "absent"
        if now != p["sha256"]:
            raise ValueError(f"{stats_dir}: {role} {p['path']} hashes {now[:12]} in this tree, the fits were produced "
                             f"under {p['sha256'][:12]}; re-fit the rows under the current chain")
    if rows_stamp is not None:
        mine = {k: doc["rows"][k] for k in ("scorer", "vocabulary", "input_files_sha256", "n_rows")}
        theirs = {k: rows_stamp[k] for k in mine}
        if mine != theirs:
            raise ValueError(f"{stats_dir}: the fits were produced from other rows (scorer, vocabulary or inputs "
                             f"differ): {json.dumps(mine)[:200]} vs {json.dumps(theirs)[:200]}")
    return doc
