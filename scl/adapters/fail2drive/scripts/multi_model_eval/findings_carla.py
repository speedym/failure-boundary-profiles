#!/usr/bin/env python3
"""CARLA FindingsSummary v0.2 emitter (the §5c "lining up" work) - the
CARLA sibling of adapters/isaac/scripts/multi_model_eval/findings_v2.py.

Writes static-threading findings by joining scored rows to existing fits.
Consumers use scl_core.stats.findings.read_findings to read CARLA and Isaac
through one view with explicit axes, units, and natural-coordinate fits.

    conda run -n scl_prototype python \\
        adapters/fail2drive/scripts/multi_model_eval/findings_carla.py \\
        --scored <score_runs out dir> --stats <boundary_stats out dir> \\
        --family fm_004_threshold_sweep --model lead --out <path>

CURVE SEMANTICS (the CARLA outcome coding, named inside the document):
- p_go     = commitment (code_commitment): refusal/correct_stop -> 0, else
             -> 1. The decision curve.
- p_clean  = competence (code_competence): clean_thread -> 1, late_commit
             dropped, else -> 0. The mission curve.
- passage  = clean traversal of the designated gap. These threading
             families have ONE route, so passage COINCIDES with p_clean
             (clean-by-any-route == clean-through-the-gap when there is only
             the gap). What separates p_clean from passage is an alternate
             route (Isaac's detour); what separates p_go from passage is a
             committed run that does NOT pass cleanly - a committed collision
             does this on a single route.

SCOPE: threading families - numeric-geometric (a measured axis metric,
oriented fits from boundary_stats) and, since F35 (2026-09-14),
CATEGORICAL (asset_identity: the same document with an axis of kind
categorical, one point per declared level in manifest order, per-level
Wilson rates, no fit - nothing is monotone in an identity; the emitter's
per-level counts must equal boundary_stats' ladder rows for the cohort).
Behavior-scalar flow families (fm_011/012, scored by flow_aggregate.py) are
REFUSED here with the vocabulary stated; findings_flow.py emits those. All
formats use the common findings reader (curve_series / ladder_series).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scl_core.stats.boundary import wilson  # noqa: E402
import boundary_stats as bs  # noqa: E402  (code_commitment/competence, family_axes, sets)
from scl_core import scenario_defects  # noqa: E402
from scl_core.campaign_declaration import check_family_manifest, coverage_gate, load_declaration  # noqa: E402

FINDINGS_KIND = "findings_summary/v0.2"
# (curve id, boundary_stats curve name, coder, meaning)
CURVES = (
    ("p_go", "commitment", bs.code_commitment,
     "committed to the passage (did not refuse/stop) - the decision curve"),
    ("p_clean", "competence", bs.code_competence,
     "reached the goal contact-free - the mission curve (single route here, "
     "so == passage)"),
    ("passage", "competence", bs.code_competence,
     "clean traversal of the designated gap - the boundary quantity "
     "(coincides with p_clean for single-route threading families)"),
)


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def _points(scored, coder, metric, defects_doc=None, family=None):
    """Per-x observed rates under the shared coding (the DATA layer). A run the
    scenario-defect register names (F66 part two) leaves n and k and is counted
    on its stratum as defect_runs; a stratum with every run registered stays in
    the document with n 0 under the assignment `defect`, never silently gone."""
    acc = defaultdict(lambda: [0, 0, None, 0])  # x -> [n, k, assignment, defect_runs]
    for r in scored:
        x = (r.get("measured") or {}).get(metric)
        if x is None:
            continue
        if defects_doc is not None and scenario_defects.entry_for(
                defects_doc, family or r.get("family"), r["scenario_id"], r["seed"]) is not None:
            acc[x][3] += 1
            if acc[x][2] is None:
                acc[x][2] = "passable" if r.get("passable") else "blockage"
            continue
        y = coder(r["outcome"])
        if y is None:
            continue
        acc[x][0] += 1
        acc[x][1] += y
        acc[x][2] = "passable" if r.get("passable") else "blockage"
    pts = []
    for x, (n, k, assign, defect_runs) in sorted(acc.items()):
        if n == 0 and defect_runs:
            lo, hi = wilson(0, 0)   # the uninformative interval of an empty stratum, never null
            pts.append({"x": _r(x), "assignment": "defect", "n": 0, "k": 0, "rate": None,
                        "wilson_95": [_r(lo), _r(hi)], "defect_runs": defect_runs})
            continue
        if n == 0:
            continue
        lo, hi = wilson(k, n)
        pt = {"x": _r(x), "assignment": assign, "n": n, "k": k,
              "rate": _r(k / n) if n else None,
              "wilson_95": [_r(lo), _r(hi)]}
        if defect_runs:
            pt["defect_runs"] = defect_runs
        pts.append(pt)
    return pts


def _levels(declaration, manifest_path):
    """The categorical levels in MANIFEST order, restricted to the declared
    variants: the ladder's x axis is data, never sorted alphabetically."""
    return _levels_doc(declaration, json.loads(Path(manifest_path).read_text()))


def _levels_doc(declaration, manifest):
    """_levels over a loaded manifest document (a raw member read from the archive, pool_campaigns)."""
    declared = set(declaration.variants)
    levels = []
    for v in manifest["variants"]:
        if v.get("scenario_id") in declared and v.get("target") not in levels:
            levels.append(v["target"])
    return levels


def _points_categorical(scored, coder, levels):
    """Per-level observed rates under the shared coding: the ladder's DATA."""
    acc = {level: [0, 0, None] for level in levels}
    for r in scored:
        y = coder(r["outcome"])
        level = str(r.get("target"))
        if y is None or level not in acc:
            continue
        acc[level][0] += 1
        acc[level][1] += y
        acc[level][2] = "passable" if r.get("passable") else "blockage"
    pts = []
    for level in levels:
        n, k, assign = acc[level]
        lo, hi = wilson(k, n)
        pts.append({"level": level, "x": None, "assignment": assign, "n": n, "k": k,
                    "rate": _r(k / n) if n else None, "wilson_95": [_r(lo), _r(hi)]})
    return pts


def _ladder_from_stats(stats_rows, family, model, curve):
    """boundary_stats' categorical ladder for this family/model/curve:
    {level: (n, k)}, or None when the stats hold no ladder for it."""
    rows = [r for r in stats_rows if r.get("family") == family and r.get("model") == model
            and r.get("curve") == curve and r.get("method") == "categorical"]
    return {str(r["level"]): (r["n"], r["k"]) for r in rows} or None


def _fit_from_stats(stats_rows, family, model, curve):
    """The fit the shared estimator already produced, reshaped to the v0.2
    fit block (never re-fit here)."""
    row = next((r for r in stats_rows
                if r.get("family") == family and r.get("model") == model
                and r.get("curve") == curve), None)
    if row is None:
        return None
    fail_x, pass_x = row.get("bracket_fail_side_x"), row.get("bracket_pass_side_x")
    # orientation: +1 success rises with x, -1 falls (e.g. route intrusion) -
    # carried through so the note is true for a decreasing axis, and slope /
    # slope_note so an IRLS fit is reconstructable from this document.
    orientation = row.get("orientation")
    sense = ("rises" if orientation == 1 else "falls" if orientation == -1
             else "rises/falls per orientation")
    return {
        "method": row["method"],
        "reason": row.get("reason"),
        "irls_converged": row.get("irls_converged"),
        "irls_iters": row.get("irls_iters"),
        "irls_boundary": row.get("irls_boundary"),
        "boundary": row.get("boundary"),
        "interval": ([fail_x, pass_x] if fail_x is not None or pass_x is not None
                     else None),
        "level": row.get("level"),
        "level_ci": row.get("level_ci"),
        "slope": row.get("slope"),
        "slope_note": row.get("slope_note"),
        "orientation": orientation,
        "bootstrap": row.get("bootstrap"),
        "excluded_rows": row.get("excluded_rows"),
        "fit_note": (f"fit over ALL strata, passable and blocked, by the shared estimator "
                     f"(scl_core.stats.boundary; the Isaac emitter fits the passable strata only, "
                     f"adapters/PARITY.md B43); success {sense} with the metric (orientation "
                     f"{orientation}); the fit's case is `reason`"),
    }


def _outcome_composition(scored, metric, levels=None):
    acc = defaultdict(lambda: defaultdict(int))
    if levels is not None:
        for r in scored:
            level = str(r.get("target"))
            if level in levels:
                acc[level][r["outcome"]] += 1
        return {level: dict(sorted(acc[level].items())) for level in levels if level in acc}
    for r in scored:
        x = (r.get("measured") or {}).get(metric)
        if x is None:
            continue
        acc[_r(x)][r["outcome"]] += 1
    return {str(x): dict(sorted(c.items())) for x, c in sorted(acc.items())}


def _decomposition(scored):
    passable = [r for r in scored if r.get("passable")]
    go = [bs.code_commitment(r["outcome"]) for r in passable]
    go = [g for g in go if g is not None]
    n_go = sum(go)
    clean_given_go = [bs.code_competence(r["outcome"]) for r in passable
                      if bs.code_commitment(r["outcome"]) == 1]
    cgg = [c for c in clean_given_go if c is not None]
    return {
        "passable_runs": len(passable),
        "p_go_passable": _r(n_go / len(go)) if go else None,
        "p_clean_given_go_passable": _r(sum(cgg) / len(cgg)) if cgg else None,
        "diagnosis_rule": ("p_go low -> refuses (never committed); p_go high "
                           "& passage low -> commits but contacts/late inside "
                           "the passage; p_clean is mission success by any "
                           "route (== passage for single-route families)"),
    }


def build_findings_carla(scored_rows, stats_rows, family, model, metric,
                         provenance, campaign, levels=None, defects=None):
    """`campaign`: {"id": ..., "declaration": {"path": ..., "sha256": ...}} -
    the declared grid the family's scored rows were gated against. `levels`
    (manifest order) makes the document categorical (F35): points per level,
    no fit, an explicit axis block; numeric documents keep their exact shape.
    `defects`: (register document, its pin) or None (F66 part two): the rows a
    register entry names leave every curve and the decomposition, stay in the
    outcome composition (the data layer) and are listed under `defects`."""
    defects_doc, defects_pin = defects or (None, None)
    applied = []
    kept_rows = []
    for r in scored_rows:
        e = scenario_defects.entry_for(defects_doc, family, r["scenario_id"], r["seed"])
        if e is None:
            kept_rows.append(r)
        else:
            applied.append({"scenario_id": r["scenario_id"], "seed": r["seed"], "reason": e["reason"]})
    curves = {}
    for cid, curve_name, coder, meaning in CURVES:
        if levels is not None:
            curves[cid] = {
                "axis_metric": metric,
                "points": _points_categorical(kept_rows, coder, levels),
                "fit_passable": None,
                "fit_scope": "none",
                "fit_note": "no fit: the axis is categorical, nothing is monotone in an identity; "
                            "per-level Wilson rates are the whole result",
                "meaning": meaning,
            }
        else:
            curves[cid] = {
                "axis_metric": metric,
                "points": _points(scored_rows, coder, metric, defects_doc, family),
                "fit_passable": _fit_from_stats(stats_rows, family, model, curve_name),
                "meaning": meaning,
            }
    doc = {
        "findings_kind": FINDINGS_KIND,
        "family_id": family,
        "campaign": dict(campaign),
        "backend": "fail2drive",
        "model": {"id": model},
        "axis_metric": metric,
    }
    if levels is not None:
        doc["axis"] = {"metric": metric, "kind": "categorical", "unit": "identity",
                       "basis": "scheduled asset identity at matched geometry", "levels": list(levels)}
    doc.update({
        "curves": curves,
        "outcome_composition": _outcome_composition(scored_rows, metric, levels),
        "decomposition": _decomposition(kept_rows),
        "defects": {"register": defects_pin,
                    "applied": sorted(applied, key=lambda a: (a["scenario_id"], a["seed"])),
                    "note": ("a run the declared scenario-defect register names is out of every curve and "
                             "the decomposition, kept in the outcome composition; a stratum with every run "
                             "registered carries the assignment `defect` (scl_core/scenario_defects.py)")},
        "provenance": provenance,
        "note": ("Static-threading findings; p_clean equals passage. Existing "
                 "fits are joined to scored rows via provenance. Read through "
                 "scl_core.stats.findings for the common backend-neutral view."
                 + (" Categorical axis (F35): one point per declared level in manifest order, "
                    "per-level Wilson rates, no fit; read with ladder_series." if levels is not None else "")),
    })
    return doc


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def bind_provenance(scored_dir: Path, stats_dir: Path) -> tuple[dict, dict]:
    """Refuse scored+stats inputs that are unstamped, stale, or from
    DIFFERENT campaigns, and return their provenance docs.

    Name/family/model matching alone joins fits to points that never
    described the same runs. The producers already stamp identity:
    score_runs writes scoring_provenance (scorer_sha256 + input_files_sha256
    of the raw), and boundary_stats records, per scored input it fit, that
    same (scorer_sha256, input_files_sha256) under `scored_inputs`. So a fit
    is bound to a scored cohort iff the scored dir's stamp appears in the
    stats' scored_inputs. Exits non-zero (nothing written) on any violation.
    """
    # 1. scored dir must be stamped by the CURRENT scorer (reuses the
    #    boundary_stats gate: unstamped or stale scored tables are refused).
    scored_stamp = bs.check_scoring_provenance([scored_dir])[scored_dir.name]

    # 2. stats dir must be stamped by the current boundary_stats AND by the
    #    shared estimator that actually produced the fits: a change to
    #    scl_core/stats/boundary.py invalidates existing stats even when
    #    boundary_stats.py itself is unchanged.
    sp = stats_dir / "stats_provenance.json"
    if not sp.is_file():
        sys.exit(f"ERROR: no stats_provenance.json under {stats_dir} "
                 f"(unstamped fits); nothing written.")
    stats_prov = json.loads(sp.read_text())
    current_stats = _sha(Path(bs.__file__).resolve())
    if stats_prov.get("stats_sha256") != current_stats:
        sys.exit(f"ERROR: stats_sha256 {stats_prov.get('stats_sha256')} != "
                 f"current boundary_stats.py {current_stats}; the fits were "
                 f"produced by a different estimator. Nothing written.")
    current_estimator = _sha(REPO_ROOT / "scl_core/stats/boundary.py")
    if stats_prov.get("estimator_sha256") != current_estimator:
        sys.exit(f"ERROR: estimator_sha256 {stats_prov.get('estimator_sha256')} != "
                 f"current scl_core/stats/boundary.py {current_estimator}; the "
                 f"fits were produced by a different (or unrecorded) shared "
                 f"estimator. Re-run boundary_stats. Nothing written.")

    # 3. bind the fits to EXACTLY this scored cohort: every fit selected must
    #    describe the scored observations being emitted and nothing else. A
    #    stats run pooled over several cohorts (two campaigns of one family)
    #    fits more observations than this document's points, so membership
    #    is not enough - the recorded cohort set must be exactly this one.
    cohort = (scored_stamp.get("scorer_sha256"),
              scored_stamp.get("input_files_sha256"))
    recorded = {(v.get("scorer_sha256"), v.get("input_files_sha256"))
                for v in stats_prov.get("scored_inputs", {}).values()}
    if recorded != {cohort}:
        sys.exit(f"ERROR: the fits in {stats_dir} were not produced from exactly "
                 f"the scored observations in {scored_dir}: stats scored_inputs "
                 f"hold {len(recorded)} cohort(s) "
                 f"{sorted(c[1] for c in recorded)}, this document's cohort is "
                 f"{cohort[1]} - a fit pooled across cohorts, or from another "
                 f"campaign, must not be joined to these points. Nothing written.")
    return scored_stamp, stats_prov


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scored", required=True, type=Path,
                    help="score_runs.py output dir (scored_rows.jsonl)")
    ap.add_argument("--stats", required=True, type=Path,
                    help="boundary_stats.py output dir (stats_rows.jsonl)")
    ap.add_argument("--family", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--campaign", required=True, type=Path,
                    help="campaign declaration (artifacts/examples/campaigns/<campaign>/<family>.json): "
                         "the grid the family's scored rows are gated against, recorded by hash")
    ap.add_argument("--family-manifest", required=True, type=Path,
                    help="the family manifest the rows were scored against, as run: the raw tree's "
                         "family_snapshot/ member (principle 13), checked against the declaration's hash")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register the fits were produced with "
                         "(scl_core/scenario_defects.py); must be the one the stats provenance pins")
    args = ap.parse_args()

    try:
        declaration = load_declaration(args.campaign)
    except ValueError as exc:
        sys.exit(f"ERROR: {exc}")
    if declaration.family_id != args.family:
        sys.exit(f"ERROR: the declaration {args.campaign.name} names family {declaration.family_id!r}, "
                 f"not {args.family!r}; nothing written.")

    axes = bs.family_axes()
    if args.family not in axes:
        sys.exit(f"ERROR: no single scheduled axis for {args.family} "
                 f"(known: {sorted(axes)})")
    metric, kind = axes[args.family]
    is_cat = kind == "categorical"
    if kind == "behavior_scalar":
        sys.exit(f"ERROR: {args.family} is a behavior-scalar flow family "
                 f"(axis {metric}); its v0.2 findings come from "
                 f"flow_aggregate.py output, not this threading emitter.")
    try:   # the family as run, checked against the declaration's pin (principle 13)
        check_family_manifest(declaration, args.family_manifest)
    except ValueError as exc:
        sys.exit(f"ERROR: {exc}")
    levels = _levels(declaration, args.family_manifest) if is_cat else None
    if is_cat and not levels:
        sys.exit(f"ERROR: {args.family} is categorical (axis {metric}) but the declared variants "
                 "name no levels in the family manifest; nothing written.")

    # Bind the fits to the scored cohort BEFORE reading either (unstamped,
    # stale, or cross-campaign inputs are refused, nothing written).
    scored_stamp, stats_prov = bind_provenance(args.scored, args.stats)
    # the register is an instrument: the fits and this document read the same one or none (F66 part two)
    defects = None
    if args.defects is not None:
        try:
            defects_doc, defects_sha = scenario_defects.load_register(args.defects)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.exit(f"ERROR: --defects: {exc}; nothing written.")
        defects = (defects_doc, scenario_defects.pin(args.defects, defects_sha, REPO_ROOT))
    pinned = (stats_prov.get("scenario_defects") or {}).get("sha256")
    given = defects[1]["sha256"] if defects else None
    if pinned != given:
        sys.exit(f"ERROR: the stats under {args.stats} were fitted with scenario-defect register "
                 f"{pinned or 'none'}, this emitter was given {given or 'none'}; the fits and the "
                 "document read one register or none. Nothing written.")

    scored_p = args.scored / "scored_rows.jsonl"
    stats_p = args.stats / "stats_rows.jsonl"
    family_rows = [r for r in (json.loads(l) for l in scored_p.read_text().splitlines())
                   if r["family"] == args.family]
    # The coverage gate reads only the declaration: the family's scored rows
    # must be exactly its declared models x seeds x variants.
    try:
        coverage_gate(declaration, ((r["model"], r["seed"], r["scenario_id"]) for r in family_rows))
    except ValueError as exc:
        sys.exit(f"ERROR: {exc}; nothing written.")
    scored_rows = [r for r in family_rows if r["model"] == args.model]
    stats_rows = [json.loads(l) for l in stats_p.read_text().splitlines()]
    if not scored_rows:
        sys.exit(f"ERROR: no scored rows for {args.family}/{args.model} "
                 f"under {scored_p}")

    provenance = {
        "emitter_sha256": _sha(Path(__file__).resolve()),
        "estimator_sha256": _sha(REPO_ROOT / "scl_core/stats/boundary.py"),
        "scored_sha256": _sha(scored_p),
        "stats_sha256": _sha(stats_p),
        # upstream identity carried forward: the fits are bound to this cohort
        "scored_provenance": scored_stamp,
        "stats_provenance": stats_prov,
        "scenario_defects": defects[1] if defects else None,
    }
    # Every curve must carry the fit the shared estimator produced for THIS
    # family/model; a missing fit means the scored and stats inputs disagree
    # (wrong family/model, or stats not produced from these observations).
    # Refuse BEFORE building or writing - never leave a half-formed document.
    if levels is None:
        missing = [cid for cid, curve_name, _, _ in CURVES
                   if _fit_from_stats(stats_rows, args.family, args.model, curve_name) is None]
        if missing:
            sys.exit(f"ERROR: no {'/'.join(missing)} fit for {args.family}/"
                     f"{args.model} in {stats_p} - the scored cohort and the "
                     f"stats fits must come from the same campaign; nothing written.")
    else:
        # the categorical twin of the fit gate: the ladder boundary_stats produced
        # for this cohort must hold exactly the per-level counts this document states
        for cid, curve_name, coder, _ in CURVES:
            ladder = _ladder_from_stats(stats_rows, args.family, args.model, curve_name)
            kept = [r for r in scored_rows if scenario_defects.entry_for(
                defects[0] if defects else None, args.family, r["scenario_id"], r["seed"]) is None]
            mine = {p["level"]: (p["n"], p["k"]) for p in _points_categorical(kept, coder, levels)}
            if ladder is None:
                sys.exit(f"ERROR: no categorical ladder for {args.family}/{args.model}/{curve_name} in "
                         f"{stats_p} - the scored cohort and the stats must come from the same campaign; "
                         "nothing written.")
            if any(ladder.get(level) != mine[level] for level in levels if mine[level][0]):
                sys.exit(f"ERROR: the {curve_name} ladder in {stats_p} does not hold this cohort's per-level "
                         f"counts for {args.family}/{args.model}; nothing written.")
    # The declaration is recorded by repo-relative path and hash (portable
    # provenance): a declaration outside the repository is refused here.
    try:
        campaign = {"id": declaration.campaign_id, "declaration": declaration.record(REPO_ROOT)}
    except ValueError as exc:
        sys.exit(f"ERROR: {exc}; nothing written.")
    doc = build_findings_carla(scored_rows, stats_rows, args.family,
                               args.model, metric, provenance, campaign, levels, defects)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1) + "\n")
    dc = doc["decomposition"]
    passage = doc["curves"]["passage"]
    shape = (f"{len(levels)} levels, no fit" if levels is not None
             else f"passage fit {passage['fit_passable']['method']}")
    print(f"{args.family} {args.model}: p_go(passable) {dc['p_go_passable']} "
          f"| p_clean|go {dc['p_clean_given_go_passable']} | {shape} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
