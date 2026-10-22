"""The one emitter (F82, principle 15): rows and fits of any chain become one findings document.

Until 2026-10-11 three writers produced one document kind in three shapes (points keyed `x` or
`width_m`, the fit under `fit` or `fit_passable`, the agent under `model` or `brain`), and a pool of
one campaign was written under a fourth kind. This module writes findings_summary/v0.3: one shape,
points keyed `x`, the agent under `model`, a `pooling` block naming the campaigns the document pools
(a campaign's own document is the pool of one), the chain named as data. Everything chain-specific
comes from the chain's outcome vocabulary (the curves' meanings, the per-point and per-document
sums) and from the rows' annex; nothing here branches on a chain.

The document's layers: `curves` (the data layer, the fit stage's points, and the inference layer,
its fit and bootstrap, per coding), `outcome_composition` (every run's label per x, the register's
cells included), `decomposition` (the rates over the document's runs, the exclusions, the anomalies,
the vocabulary's document sums), `decision_summary` (the scorer's decisions summed per scope and per
side, with the family's balanced accuracy and its 95% bootstrap interval, F87), `defects` (the register and the cells it took out), `provenance` (every input by hash).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scl_core import run_rows as rr
from scl_core.records import render
from scl_core.stats import decision as da

AXIS_UNITS = {"minimum_free_corridor_width": "m", "maximum_route_intrusion": "m", "measured_gap_m": "m",
              "crossing_time_gap_s": "s", "asset_identity": "identity"}
FIT_FIELDS = ("method", "reason", "orientation", "boundary", "interval", "bracket_fail_side_x", "bracket_pass_side_x",
              "slope", "slope_note", "bracket_note", "bracket_open", "level", "level_ci", "irls_converged", "irls_iters",
              "irls_boundary", "p10", "p90", "x_range", "n_runs", "n_strata", "mixed_strata",
              "monotonicity_violations", "excluded_rows", "defect_rows", "repetitions_present",
              "per_repetition_boundaries", "bootstrap_B", "bootstrap_seed")


def _rate(values) -> dict:
    counted = [v for v in values if v is not None]
    return {"n": len(counted), "k": sum(1 for v in counted if v is True),
            "rate": round(sum(1 for v in counted if v is True) / len(counted), 4) if counted else None}


def variant_order(manifest: dict) -> list:
    """The variant ids in manifest order (CARLA names a variant scenario_id, Isaac stem): the data
    order of a categorical ladder, never alphabetical."""
    return [v.get("scenario_id", v.get("stem")) for v in manifest.get("variants", []) if isinstance(v, dict)]


def axis_block(rows, manifest=None) -> dict:
    """{metric, basis, unit, kind, levels?} from the rows (one family has one axis) and, for a level
    axis, the manifest's variant order."""
    metrics, bases = {r["x_metric"] for r in rows}, {r["x_basis"] for r in rows}
    if len(metrics) != 1 or len(bases) != 1:
        raise ValueError(f"one family has one axis; rows carry metrics {sorted(metrics)} and bases {sorted(bases)}")
    metric, basis = metrics.pop(), bases.pop()
    axis = {"metric": metric, "basis": basis, "unit": AXIS_UNITS.get(metric),
            "kind": "categorical" if basis == "level" else "numeric"}
    if basis == "level":
        order = variant_order(manifest or {})
        by_variant = {r["variant"]: r["x"] for r in rows}
        levels = []
        for vid in order:
            if vid in by_variant and by_variant[vid] not in levels:
                levels.append(by_variant[vid])
        for x in sorted({r["x"] for r in rows}):
            if x not in levels:
                levels.append(x)
        axis["levels"] = levels
    return axis


def _ordered_points(points, axis):
    if axis["kind"] != "categorical":
        return points
    rank = {level: i for i, level in enumerate(axis["levels"])}
    return sorted(points, key=lambda p: rank.get(p["x"], len(rank)))


def outcome_composition(rows, axis) -> list:
    acc = {}
    for r in rows:
        entry = acc.setdefault(r["x"], {"x": r["x"], "assignment": set(), "n": 0, "outcomes": Counter()})
        entry["assignment"].add(r["assignment"])
        entry["n"] += 1
        entry["outcomes"][r["outcome"]] += 1
    out = []
    for x in sorted(acc, key=lambda v: (isinstance(v, str), v)):
        e = acc[x]
        a = sorted(e["assignment"])
        out.append({"x": x, "assignment": a[0] if len(a) == 1 else "mixed", "n": e["n"],
                    "outcomes": dict(sorted(e["outcomes"].items()))})
    return _ordered_points(out, axis)


def decomposition(rows, vocabulary, defect_cells) -> dict:
    kept = [r for r in rows if (r["variant"], r["repetition"]) not in defect_cells]
    passable = [r for r in kept if r["assignment"] == "passable"]
    committed = [r for r in passable if r["commitment"] is True]
    anomalies = Counter(r["outcome"] for r in kept
                        if r["commitment"] is None and r["clean"] is None and r["passage"] is None)
    out = {"runs": len(rows), "defect_runs": len(rows) - len(kept), "passable_runs": len(passable),
           "p_go": _rate([r["commitment"] for r in kept]),
           "p_clean": _rate([r["clean"] for r in kept]),
           "passage": _rate([r["passage"] for r in kept]),
           "p_go_passable": _rate([r["commitment"] for r in passable]),
           "p_clean_given_go_passable": _rate([r["clean"] for r in committed]),
           "excluded": {c: sum(1 for r in kept if r[c] is None) for c in rr.CODINGS},
           "anomalies": dict(sorted(anomalies.items())),
           "diagnosis_rule": ("p_go low: never committed (decision collapse); p_go high and passage low: "
                              "committed and failed inside the passage (contact or stall); p_clean is mission "
                              "success by any route, equal to passage where the family has one route")}
    out.update(rr.document_extras(vocabulary, kept))
    return out


def decision_summary(rows, defect_cells, family) -> dict:
    """The scorer's decisions summed per scope and per side, and the family's balanced accuracy with its 95%
    bootstrap interval (scl_core.stats.decision, F87 2026-10-14: the paper's Table 2 definition, runs resampled
    within each side; a family without a side carries value None and the case names the side)."""
    kept = [r for r in rows if (r["variant"], r["repetition"]) not in defect_cells]
    scopes = {}
    for scope in rr.DECISION_SCOPES:
        scoped = [r for r in kept if r["decision_scope"] == scope]
        scopes[scope] = _rate([r["decision_correct"] for r in scoped])
    balanced = [r for r in kept if r["decision_scope"] == "balanced"]
    sides = {side: _rate([r["decision_correct"] for r in balanced if r["assignment"] == side])
             for side in ("passable", "blocked")}
    accuracy = da.accuracy([{"family": family, "pass": sides["passable"], "wait": sides["blocked"]}])
    return {"scopes": scopes, "sides": sides, "accuracy": accuracy,
            "rule": ("decision_correct per run is the scorer's, through the one rule every chain applies "
                     "(scl_core.run_rows.decision): a clean pass is the right call on a passable (usable) variant, "
                     "no attempt at the passage on a blocked (wait) one; the balanced scope is those two sides, the "
                     "flow certificate's conditional scope is reported beside it; `accuracy` is the balanced mean of "
                     "the two sides with its 95% bootstrap interval (scl_core.stats.decision: runs resampled within "
                     "each side, B replicates and the seed recorded; degenerate sides named)")}


def build_document(rows, stats, *, family, model, vocabulary, axis, pooling, provenance, reference_model=None,
                   defects=None) -> dict:
    """One document for (family, model): `rows` the family's rows of that model, `stats` the fit stage's
    three rows for it, `defects` (register document, pin) or None."""
    defects_doc, defects_pin = defects or (None, None)
    from scl_core import scenario_defects
    applied, defect_cells = [], set()
    for r in rows:
        e = scenario_defects.entry_for_cell(defects_doc, family, r["variant"], r["repetition"])
        if e is not None:
            applied.append({"variant": r["variant"], "repetition": r["repetition"], "reason": e["reason"]})
            defect_cells.add((r["variant"], r["repetition"]))
    by_curve = {s["curve"]: s for s in stats if s["family"] == family and s["model"] == model}
    missing = [c for c in rr.CODINGS if c not in by_curve]
    if missing:
        raise ValueError(f"no {'/'.join(missing)} fit for {family}/{model} in the stats; the rows and the fits must "
                         "come from the same cohort")
    curves = {}
    for curve in rr.CODINGS:
        s = by_curve[curve]
        curves[s["quantity"]] = {"curve": curve, "meaning": vocabulary.get("meanings", {}).get(curve),
                                 "points": _ordered_points(s["points"], axis),
                                 "fit": ({k: s.get(k) for k in FIT_FIELDS} if s["method"] not in ("categorical", "empty")
                                         else None),
                                 "bootstrap": s.get("bootstrap"),
                                 "fit_note": ("no fit: the axis is categorical, nothing is monotone in an identity; "
                                              "per-level Wilson rates are the whole result" if s["method"] == "categorical"
                                              else "no counted run" if s["method"] == "empty"
                                              else "fit over every stratum by the shared estimator (scl_core.stats.boundary), "
                                                   "oriented so success rises with x (orientation); the fit's case is `reason`")}
    return {
        "findings_kind": rr.DOCUMENT_KIND,
        "family_id": family,
        "model": {"id": model, "role": "reference" if model == reference_model else "policy"},
        "backend": vocabulary["backend"],
        "chain": vocabulary["chain"],
        "axis": axis,
        "pooling": pooling,
        "curves": curves,
        "outcome_composition": outcome_composition(rows, axis),
        "decomposition": decomposition(rows, vocabulary, defect_cells),
        "decision_summary": decision_summary(rows, defect_cells, family),
        "defects": {"register": defects_pin,
                    "applied": sorted(applied, key=lambda a: (a["variant"], -1 if a["repetition"] is None else a["repetition"])),
                    "note": ("a run the declared scenario-defect register names is out of every curve, the decomposition "
                             "and the decisions, kept in the outcome composition; a stratum with every run registered "
                             "carries the assignment `defect` (scl_core/scenario_defects.py)")},
        "provenance": provenance,
        "note": ("One document kind for every chain (F82, 2026-10-11): the data layer is the fit stage's points per "
                 "x, the inference layer its fit and bootstrap; the agent is `model`, the campaigns pooled are "
                 "`pooling.campaigns` (one for a campaign's own document); the chain's curve meanings and the "
                 "per-point and per-document sums come from its outcome vocabulary."),
    }


def gate_coverage(declarations, rows, repetitions=None) -> None:
    """Every campaign's rows against its own declared grid: one declaration gates the rows as they are;
    several (a pool) split the rows by repetition, which load_pooling keeps disjoint. Two campaigns that
    both declare a reference model running once would hold two copies of its run: refused, not defined.
    `repetitions`: {campaign_id: [ids]} the cohort takes from each campaign (a pool's declared lists, F88
    2026-10-15); a campaign absent from the map contributes its whole declared grid."""
    repetitions = repetitions or {}
    if len(declarations) == 1:
        rr.coverage(declarations[0], rows, repetitions=repetitions.get(declarations[0].campaign_id))
        return
    single = [d for d in declarations if rr.reference_runs_once(d)]
    if len(single) > 1:
        raise ValueError(f"pooling {[d.campaign_id for d in single]}: more than one campaign declares a reference "
                         "model that runs once per variant; its deterministic run would be counted once per campaign, "
                         "which is not defined (the first campaign's run would do, said out loud)")
    covered = set()
    for d in declarations:
        reps = set(repetitions.get(d.campaign_id, d.repetitions))
        mine = [r for r in rows if r["repetition"] in reps or (r["repetition"] is None and rr.reference_runs_once(d))]
        rr.coverage(d, mine, repetitions=repetitions.get(d.campaign_id))
        covered.update(id(r) for r in mine)
    stray = [r for r in rows if id(r) not in covered]
    if stray:
        raise ValueError(f"{len(stray)} row(s) belong to no pooled declaration's repetitions (e.g. {rr.cell(stray[0])})")


def pooling_block(declarations, root, pool_id=None, spec=None, content_ids=None) -> dict:
    """The campaigns a document pools, each by its declaration (path and hash) and, when known, the
    derived content id its rows came from; a campaign's own document names itself alone."""
    content_ids = content_ids or {}
    return {"pool_id": pool_id, "spec": spec,
            "campaigns": [{"id": d.campaign_id, "declaration": d.record(root),
                           "derived_content_id_sha256": content_ids.get(d.campaign_id)} for d in declarations]}


def emitter_pin(root) -> dict:
    """This emitter pinned by path and hash in the given tree: the one place its current identity is
    computed (the documents' provenance, the pool record, both certifiers)."""
    return rr.pin(Path(__file__).resolve(), root)


def provenance_block(rows_dir, stats_dir, root, defects_pin=None, extra=None) -> dict:
    from scl_core.stats import fit_rows as fr
    rows_stamp = rr.check_stamp(rows_dir, root)
    stats_stamp = fr.check_stats(stats_dir, root, rows_stamp)
    out = {"emitter": emitter_pin(root),
           "fit": stats_stamp["fit"], "estimator": stats_stamp["estimator"],
           "rows_sha256": rr.sha256_file(Path(rows_dir) / Path(rr.ROWS_MEMBER).name),
           "stats_sha256": rr.sha256_file(Path(stats_dir) / Path(rr.STATS_MEMBER).name),
           "rows_provenance": rows_stamp, "stats_provenance": stats_stamp,
           "scenario_defects": defects_pin}
    out.update(extra or {})
    return out


def emit(rows_dir, stats_dir, declarations, root, out_dir, *, manifest, defects=None, pool_id=None, spec=None,
         content_ids=None, extra_provenance=None, models=None, repetitions=None) -> list:
    """Every declared model's document of one family from a scored folder and its stats folder, into
    <out_dir>/fs_<campaign or pool>_<family>_<model>.v0.3.json; the coverage gate per campaign, the
    register the stats were fitted with (or none), the pooling block. Returns the paths."""
    from scl_core.stats import fit_rows as fr
    family = declarations[0].family_id
    if any(d.family_id != family for d in declarations):
        raise ValueError(f"one family per emit; declarations name {sorted({d.family_id for d in declarations})}")
    rows = [r for r in rr.read_rows(Path(rows_dir) / Path(rr.ROWS_MEMBER).name) if r["family"] == family]
    gate_coverage(declarations, rows, repetitions)
    stamp = rr.check_stamp(rows_dir, root)
    vocabulary, _ = rr.load_vocabulary(Path(root) / stamp["vocabulary"]["path"])
    stats = fr.read_stats(stats_dir)
    stats_stamp = fr.check_stats(stats_dir, root, stamp)
    defects_pin = defects[1] if defects else None
    if (stats_stamp.get("scenario_defects") or {}).get("sha256") != (defects_pin or {}).get("sha256"):
        raise ValueError(f"the stats under {stats_dir} were fitted with scenario-defect register "
                         f"{(stats_stamp.get('scenario_defects') or {}).get('sha256') or 'none'}, the emitter was "
                         f"given {(defects_pin or {}).get('sha256') or 'none'}; the fits and the document read one register or none")
    axis = axis_block(rows, manifest)
    pooling = pooling_block(declarations, root, pool_id, spec, content_ids)
    provenance = provenance_block(rows_dir, stats_dir, root, defects_pin, extra_provenance)
    reference = declarations[0].reference_model
    label = pool_id or declarations[0].campaign_id
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for model in (models or declarations[0].models):
        mine = [r for r in rows if r["model"] == model]
        if not mine:
            raise ValueError(f"no rows for {family}/{model} under {rows_dir}")
        doc = build_document(mine, stats, family=family, model=model, vocabulary=vocabulary, axis=axis,
                             pooling=pooling, provenance=provenance, reference_model=reference, defects=defects)
        path = out_dir / rr.document_name(label, family, model)
        path.write_text(render(doc))
        paths.append(path)
    return paths
