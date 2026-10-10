#!/usr/bin/env python3
"""Emit per-model CARLA flow FindingsSummary v0.2 from stamped flow rows.

The axis is nominal crossing_time_gap_s; measured gaps and divergence flags
remain alongside each point. These families have no geometric passability
certificate: fits cover tested variants, never an invented passable subset.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

import numpy as np
import boundary_stats as bs
import flow_aggregate as flow
from scl_core.campaign_declaration import coverage_gate
from scl_core.records import sha256_file  # noqa: E402  (the one streaming hash, F71)
from scl_core.stats.boundary import BOOTSTRAP_B, BOOTSTRAP_SEED, analyze, bootstrap, wilson
from scl_core.stats.findings import normalize_findings

AXIS = {"metric": "crossing_time_gap_s", "unit": "s", "basis": "nominal"}


def sha(path):
    path = Path(path)
    return sha256_file(path) if path.is_file() else "absent"   # streamed (F71); "absent" for a missing input


def load_flow(directory: Path, manifest_path: Path, zone_path: Path, map_excerpt: Path, declaration,
              gap_certificate: Path | None = None):
    """Bind the row content to the current instrument code, the zone document
    and map excerpt that were passed, the family the rows were scored
    against, and the campaign declaration's grid (coverage gate: every
    declared cell exactly once, nothing undeclared)."""
    stamp = json.loads((directory / "scoring_provenance.json").read_text())
    expected = {"scorer_sha256": sha(Path(flow.__file__)),
                "manifest_sha256": sha(manifest_path),
                "flow_runs_sha256": sha(directory / "flow_runs.jsonl")}
    for key, digest in expected.items():
        if digest == "absent" or stamp.get(key) != digest:
            raise ValueError(f"flow provenance requires current {key}")
    if stamp["manifest_sha256"] != declaration.manifest_sha256:
        raise ValueError(f"{declaration.campaign_id}: the rows were scored against family manifest "
                         f"{stamp['manifest_sha256']}, the declaration pins {declaration.manifest_sha256}")
    if bool(stamp.get("gap_certificate")) != bool(gap_certificate):
        raise ValueError("flow provenance requires the gap certificate the rows were scored with, exactly when "
                         "they were scored with one (F55)")
    deps = {name: sha(path) for name, path in
            flow.instrument_dependency_paths(zone_path, map_excerpt, gap_certificate).items()}
    if stamp.get("instrument_deps_sha256") != deps:
        raise ValueError("flow provenance requires the current instrument dependencies "
                         "(instrument code, the zone document, the map excerpt and the gap certificate as passed)")
    rows = [json.loads(line) for line in (directory / "flow_runs.jsonl").read_text().splitlines()]
    manifest = json.loads(manifest_path.read_text())
    accepted = {v["scenario_id"]: v for v in manifest["variants"] if v["verdict"] == "accepted"}
    for row in rows:
        if row["scenario_id"] not in accepted:
            raise ValueError("flow rows must identify accepted family runs")
        nominal = accepted[row["scenario_id"]]["nominal"]["crossing_time_gap_s"]
        if row["crossing_time_gap_s"] != nominal:
            raise ValueError("flow row axis must match the stamped manifest")
    coverage_gate(declaration, ((r["model"], r["seed"], r["scenario_id"]) for r in rows))
    return rows, manifest, stamp


def fit_points(points):
    strata = [(p["x"], p["n"], p["k"]) for p in points if p["n"] and p["x"] is not None]
    if not strata:
        return None, None
    orientation = bs.orientation_for(strata, AXIS["metric"])
    oriented = sorted((orientation * x, n, k) for x, n, k in strata)
    fit = analyze(oriented)
    boot = bootstrap(oriented, np.random.default_rng(BOOTSTRAP_SEED)) if fit["method"] != "flat" else None
    for key in ("boundary", "p10", "p90", "slope"):
        if fit.get(key) is not None:
            fit[key] *= orientation
    if fit.get("interval") is not None:
        fit["interval"] = [orientation * x if x is not None else None for x in fit["interval"]]
    fit["violation_pairs"] = [[orientation * x for x in pair] for pair in fit["violation_pairs"]]
    fit.update(orientation=orientation, slope_axis="natural")
    if boot:
        boot["ci"] = sorted(orientation * x for x in boot["ci"])
    return fit, boot


def _rate(values):
    values = [v for v in values if v is not None]
    return {"n": len(values), "k": sum(values),
            "rate": sum(values) / len(values) if values else None}


def build_findings(rows, manifest, model, campaign, provenance):
    """`campaign`: {"id": ..., "declaration": {"path": ..., "sha256": ...}} -
    the declared grid the rows were gated against, recorded by hash."""
    rows = [r for r in rows if r["model"] == model]
    if not rows:
        raise ValueError(f"no flow rows for model {model}")
    groups = {r["index"]: r for r in flow.aggregate(rows, ("index",))}
    variants = sorted((v for v in manifest["variants"] if v["verdict"] == "accepted"),
                      key=lambda v: v["nominal"]["crossing_time_gap_s"])
    curves = {}
    meanings = {
        "p_go": ("Ego body enters the map-defined oncoming driving-lane conflict zone while the flow is active; "
                 "sticky measured commitment (an entry after the flow's last recorded frame is recorded, not counted)."),
        "p_clean": "clean_cross under flow primary-outcome coding; incomplete outcomes excluded.",
    }
    for quantity, meaning in meanings.items():
        points = []
        for variant in variants:
            group = groups.get(variant["index"], {})
            n, k = group.get(f"{quantity}_n", 0), group.get(f"{quantity}_k", 0)
            bands = group.get("gap_bands") or []
            points.append({
                "x": variant["nominal"]["crossing_time_gap_s"], "assignment": "tested",
                # F55: the certificate's band for the variant (mixed when its seeds disagree) and the
                # balanced-scope decisions read against it
                "band": (bands[0] if len(bands) == 1 else "mixed") if bands else None,
                "decision": {"n": group.get("decision_balanced_n", 0), "k": group.get("decision_balanced_k", 0),
                             "rate": (group["decision_balanced_k"] / group["decision_balanced_n"]
                                      if group.get("decision_balanced_n") else None)},
                "n": n, "k": k, "rate": k / n if n else None,
                "wilson_95": list(wilson(k, n)),
                "excluded": group.get("n_runs", 0) - n,
                "measured_x": group.get("measured_crossing_time_gap_s"),
                "axis_divergent": group.get("axis_divergent"),
                "gap_residual_abs_max_s": group.get("gap_residual_abs_max_s"),
                "variant_index": variant["index"],
            })
        fit, boot = fit_points(points)
        curves[quantity] = {"points": points, "fit": fit, "bootstrap": boot,
                            "fit_scope": "tested", "meaning": meaning}
    curves["passage"] = deepcopy(curves["p_clean"])
    curves["passage"]["meaning"] = "Clean designated crossing (clean_cross); the single crossing coincides with p_clean."
    composition = []
    for v in variants:
        grp = [r for r in rows if r["index"] == v["index"]]
        composition.append({"x": v["nominal"]["crossing_time_gap_s"], "assignment": "tested",
                            "n": len(grp), "outcomes": dict(sorted(Counter(r["primary_outcome"] for r in grp).items()))})
    doc = {
        "findings_kind": "findings_summary/v0.2", "backend": "fail2drive",
        "family_id": manifest["manifest_id"], "campaign": dict(campaign),
        "model": {"id": model, "role": "reference" if model in flow.DEFAULT_REFERENCE_MODELS else "policy"},
        "axis": dict(AXIS), "axis_metric": AXIS["metric"],
        "curves": curves, "outcome_composition": composition,
        "decomposition": {
            "scope": "tested", "runs": len(rows),
            "p_go": _rate([r["p_go"] for r in rows]),
            "p_clean": _rate([r["p_clean"] for r in rows]),
            "p_clean_given_go": _rate([r["clean_given_go"] for r in rows]),
            "risk_set_match": all((r["p_go"] is None) == (r["p_clean"] is None) for r in rows),
            "stalls": dict(sorted(Counter("go_unknown" if r["p_go"] is None else "after_go" if r["p_go"] else "before_go"
                                          for r in rows if r["stall"]).items())),
            "go_unmeasured": sum(r["p_go"] is None for r in rows),
            "clean_without_measured_go": sum(r["p_clean"] == 1 and r["p_go"] == 0 for r in rows),
            "corridor_approximated": sum(bool(r.get("measured_go_corridor_approximated")) for r in rows),
        },
        "instrument": provenance.get("scoring_provenance", {}).get("measured_go_instrument"),
        "gap_certificate": provenance.get("scoring_provenance", {}).get("gap_certificate"),
        "decision_summary": decision_summary(rows),
        "provenance": provenance,
        "note": ("p_go is measured (the hero body enters the declared conflict zone, read from the trajectory) "
                 "and p_clean is label-based (clean_cross), so their undefined masks and therefore their "
                 "per-variant denominators can differ; p_clean at or below p_go is not guaranteed, the two are "
                 "independent marginals over their own risk sets, and the nested question is answered only by "
                 "P(clean | go), reported separately. Nominal gap axis; measured gaps retained. No geometric "
                 "passability claim. p_go uses the map-defined oncoming driving-lane zone (derive_measured_go v3), "
                 "whose geometry is independent of oncoming recording length; clean_without_measured_go reports "
                 "any residual clean-cross / no-measured-go disagreement."),
    }
    normalize_findings(doc)
    return doc


def decision_summary(rows):
    """The runs' decisions against the gap certificate, per band and per scope (F55); the balanced
    scope (usable + wait) is what the decision-accuracy pools read, the conditional band sits beside."""
    bands = {}
    for r in rows:
        b = r.get("gap_band")
        if b:
            bands.setdefault(b, {"runs": 0, "scored": 0, "correct": 0})
            bands[b]["runs"] += 1
            if r.get("decision_correct") is not None:
                bands[b]["scored"] += 1
                bands[b]["correct"] += int(bool(r["decision_correct"]))
    out = {"bands": dict(sorted(bands.items())),
           "rules": ff_rules() if bands else None}
    for scope in ("balanced", "conditional"):
        scoped = [r for r in rows if r.get("decision_scope") == scope]
        k = sum(1 for r in scoped if r["decision_correct"])
        out[scope] = {"n": len(scoped), "k": k, "rate": k / len(scoped) if scoped else None}
    for band in ("usable", "wait"):
        scoped = [r for r in rows if r.get("gap_band") == band and r.get("decision_correct") is not None]
        k = sum(1 for r in scoped if r["decision_correct"])
        out[band] = {"n": len(scoped), "k": k, "rate": k / len(scoped) if scoped else None}
    return out


def ff_rules():
    from flow_gap_certificate import DECISION_RULES
    return DECISION_RULES


def emit(directory, manifest_path, declaration, out, zone_path, map_excerpt, gap_certificate=None):
    rows, manifest, stamp = load_flow(directory, manifest_path, zone_path, map_excerpt, declaration, gap_certificate)
    campaign = {"id": declaration.campaign_id, "declaration": declaration.record(ROOT)}
    provenance = {
        "emitter_sha256": sha(Path(__file__)), "estimator_sha256": sha(ROOT / "scl_core/stats/boundary.py"),
        "orientation_tool_sha256": sha(Path(bs.__file__)),
        "scoring_provenance": stamp, "family_manifest_sha256": sha(manifest_path),
        "flow_runs_sha256": sha(directory / "flow_runs.jsonl"),
        "bootstrap": {"B": BOOTSTRAP_B, "seed": BOOTSTRAP_SEED},
    }
    paths = []
    for model in sorted({r["model"] for r in rows}):
        doc = build_findings(rows, manifest, model, campaign, provenance)
        out.mkdir(parents=True, exist_ok=True)
        # One naming rule for every CARLA v0.2 document: campaign, family, model.
        path = out / f"fs_{campaign['id']}_{manifest['manifest_id']}_{model}.v0.2.json"
        path.write_text(json.dumps(doc, indent=1, allow_nan=False) + "\n")
        paths.append(path)
        print(f"{campaign['id']} {model}: {doc['decomposition']}", flush=True)
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--flow", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--campaign", required=True, type=Path,
                    help="campaign declaration (artifacts/examples/campaigns/...) the rows are gated against")
    ap.add_argument("--zone", type=Path, required=True,
                    help="declared conflict-zone document the rows were scored against")
    ap.add_argument("--map-excerpt", type=Path, required=True,
                    help="the pinned OpenDRIVE excerpt that zone declares as its source")
    ap.add_argument("--gap-certificate", type=Path, default=None,
                    help="the family's flow gap certificate the rows were scored with (F55)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    try:
        from scl_core.campaign_declaration import load_declaration
        emit(args.flow, args.manifest, load_declaration(args.campaign), args.out, args.zone, args.map_excerpt,
             args.gap_certificate)
    except (ValueError, OSError) as exc:
        ap.exit(2, f"REFUSED: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
