#!/usr/bin/env python3
"""Audit every declared flow run with independent convex-cell SAT and duration checks.

--campaign (repeatable, required) and --zone are passed in, never named
here. The SUMMARY (--out, kind flow_commitment_audit/v0.5,
entries campaigns[<campaign>][<family>]) holds counts, dispositions and
hashes; the EVIDENCE
(<scores-root>/<campaign>/<family>/audit/commitment_audit_rows.json, kind
flow_commitment_audit_rows/v0.3, one record per run with its entry witness)
is pinned in the summary by hash and carried in the derived bundle. No
prior aggregation is compared against (2026-10-09: the runner's own
aggregation in an old raw tree is a deprecated member, read by nothing).
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
MME = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(MME)]
import derive_measured_go as instrument
import flow_aggregate as flow
from regenerate_flow_findings import key, read_rows, validate_coverage
from scl_core.campaign_declaration import load_declarations
from scl_core.records import portable_path, render, sha256_file as sha

AUDIT_KIND = "flow_commitment_audit/v0.5"        # 2026-09-20: entries after the flow named and counted
ROWS_KIND = "flow_commitment_audit_rows/v0.3"    # 2026-10-09: no prior_* fields, no baseline dispositions
ROWS_FILE = "commitment_audit_rows.json"


def cell_hits(track, polygon, hx, hy):
    """Independent SAT over lane cross-section cells; no Shapely predicates."""
    n = len(polygon) // 2
    inner, outer = np.asarray(polygon[:n]), np.asarray(polygon[n:][::-1])
    centers = np.column_stack((track["x"], track["y"]))
    yaw = np.radians(track["yaw"])
    u = np.column_stack((np.cos(yaw), np.sin(yaw)))
    v = np.column_stack((-np.sin(yaw), np.cos(yaw)))
    witnesses = np.full(len(centers), -1, dtype=int)
    cells = []
    for i in range(n - 1):
        c = np.array([inner[i], inner[i + 1], outer[i + 1], outer[i]])
        cells.append(c.tolist())
        edges = np.roll(c, -1, axis=0) - c
        if np.linalg.norm(edges[0]) < 1e-7:
            continue  # duplicate geometry-segment endpoint
        hit = np.ones(len(centers), dtype=bool)
        for edge in edges:
            axis = np.array([-edge[1], edge[0]], dtype=float)
            if np.linalg.norm(axis) < 1e-10:
                continue
            axis /= np.linalg.norm(axis)
            ego_mid = centers @ axis
            radius = hx * np.abs(u @ axis) + hy * np.abs(v @ axis)
            proj = c @ axis
            hit &= (ego_mid + radius >= proj.min()) & (ego_mid - radius <= proj.max())
        for axes, radius in ((u, hx), (v, hy)):
            proj = axes @ c.T
            mid = np.sum(centers * axes, axis=1)
            hit &= (mid + radius >= proj.min(axis=1)) & (mid - radius <= proj.max(axis=1))
        witnesses[(witnesses < 0) & hit] = i
    return witnesses, cells


def audit_campaign(family_root, rows_path, declared, declaration):
    """`declared` is the loaded zone document, `declaration` the campaign grid
    the rows must cover exactly. Returns (summary, rows_text)."""
    rows = read_rows(rows_path)
    validate_coverage(rows, declaration)
    records = []
    for row in rows:
        extract = family_root / row["model"] / f"seed_{row['seed']}" / "trajectories" / f"RouteScenario_{row['route_id']}_rep0.json.gz"
        with gzip.open(extract, "rt") as f:
            data = json.load(f)
        zone = instrument.flow_corridor(declared, row["scenario_id"])
        if zone is None:
            raise ValueError(f"no map zone for {key(row)}")
        track = data["tracks"][str(data["hero_id"])]
        hits, cells = cell_hits(track, zone["corners"], flow.EGO_HX_FLOW, flow.EGO_HY_FLOW)
        indices = np.flatnonzero(hits >= 0)
        first = int(indices[0]) if len(indices) else None
        frame = track["frames"][first] if first is not None else None
        entry_observed = bool(len(indices))
        if entry_observed != bool(row["measured_go_entry"]) or frame != row["measured_go_first_entry_frame"]:
            raise ValueError(f"independent SAT disagrees with instrument: {key(row)}")
        # the flow window (GO_RULE): a go is the entry inside it; an entry after it is not counted
        window = instrument.flow_window(data)
        if window is None:
            raise ValueError(f"no scenario-role actor recorded for {key(row)}: the flow window cannot be measured")
        counted = entry_observed and window[0] <= frame <= window[1]
        if counted != bool(row["p_go"]):
            raise ValueError(f"go does not follow the flow-window rule: {key(row)} (entry frame {frame}, window {window})")
        # ENTRY must not depend on oncoming evidence: strip every scenario track and re-derive
        stripped = {**data, "actors": {}, "tracks": {str(data["hero_id"]): track}}
        measured = instrument.derive_measured_go(stripped, zone["corners"], flow.EGO_HX_FLOW, flow.EGO_HY_FLOW)
        if measured["entry"] != entry_observed or measured["first_entry_frame"] != frame:
            raise ValueError("oncoming evidence changed entry")
        rec = {"run": list(key(row)), "extract_sha256": sha(extract),
               "primary_outcome": row["primary_outcome"], "stall": row["stall"],
               "flow_collision": row["flow_collision"], "p_go": row["p_go"],
               "first_entry_frame": frame, "independent_sat_agrees": True,
               "entry": entry_observed, "entry_after_flow": bool(entry_observed and not counted),
               "flow_window_frames": list(window),
               "nearest_miss_m": row["measured_go_nearest_miss_m"],
               "sample_tolerance_m": row["measured_go_sample_tolerance_m"],
               "disposition": ("entry_after_flow" if entry_observed and not counted else
                               "observed_entry" if first is not None else "no_observed_entry")}
        if first is not None:
            body = instrument.route_metrics.rect_corners(track["x"][first], track["y"][first],
                          track["yaw"][first], flow.EGO_HX_FLOW, flow.EGO_HY_FLOW)
            rec["entry_evidence"] = {"pose": [track[k][first] for k in ("x", "y", "yaw")],
                "ego_corners": [list(p) for p in body], "intersected_map_cell": cells[int(hits[first])],
                "previous_pose": [track[k][first-1] for k in ("x", "y", "yaw")] if first else None,
                "previous_frame": track["frames"][first-1] if first else None}
        if not entry_observed:
            rec["miss_exceeds_translation_sample_tolerance"] = (row["measured_go_nearest_miss_m"] >
                                                       (row["measured_go_sample_tolerance_m"] or 0))
        records.append(rec)
    rows_doc = {"rows_kind": ROWS_KIND, "campaign_id": declaration.campaign_id,
                "producer_sha256": sha(Path(__file__)), "geometry_sha256": declared.sha256,
                "runs": records}
    summary = {"campaign_declaration_sha256": declaration.sha256,
               "rows_sha256": sha(rows_path),
               "runs_checked": len(records), "independent_sat_mismatches": 0,
               "oncoming_evidence_removed_entry_changes": 0,
               "zone_depends_on_run_evidence": False,
               "entries_after_flow": sum(r["entry_after_flow"] for r in records),
               "go_rule": instrument.GO_RULE,
               "unresolved_count": sum(r["disposition"].startswith("unresolved") for r in records)}
    return summary, render(rows_doc)


def rows_record(declaration, rows_text):
    """How the summary pins its evidence rows: scores-relative path plus hash."""
    return {"path": f"{declaration.results_subtree}/audit/{ROWS_FILE}",
            "sha256": hashlib.sha256(rows_text.encode()).hexdigest()}


def audit(results, scores, out, declared, declarations):
    doc = {"audit_kind": AUDIT_KIND, "producer_sha256": sha(Path(__file__)),
           "geometry_sha256": declared.sha256, "zone_path": portable_path(declared.path, ROOT),
           "method": "Independent SAT of ego rectangles against map lane cross-section cells. Outcome labels are context, not the entry oracle. No interpolation between recorded poses.",
           "evidence": "per-run records (entry witnesses) are evidence, not findings: they live in each campaign's commitment_audit_rows.json, pinned here by hash and carried in the campaign bundle",
           "campaigns": {}}
    for declaration in declarations:
        campaign, family = declaration.campaign_id, declaration.family_id
        subtree = declaration.results_subtree
        summary, rows_text = audit_campaign(results / subtree, scores / subtree / "flow_stats/flow_runs.jsonl",
                                            declared, declaration)
        rows_path = scores / subtree / "audit" / ROWS_FILE
        rows_path.parent.mkdir(parents=True, exist_ok=True)
        rows_path.write_text(rows_text)
        doc["campaigns"].setdefault(campaign, {})[family] = {
            **summary, "campaign_declaration": declaration.record(ROOT), "rows": rows_record(declaration, rows_text)}
        print(f"Audited {campaign}/{family}: {summary['runs_checked']} runs, {summary['unresolved_count']} unresolved", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(doc))
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-root", type=Path, default=MME / "results",
                    help="location of the raw campaign trees; each declaration names its subtree")
    ap.add_argument("--campaign", action="append", required=True, type=Path, metavar="DECLARATION",
                    help="campaign declaration (artifacts/examples/campaigns/...); repeatable, required")
    ap.add_argument("--scores-root", type=Path, required=True,
                    help="the regeneration output root, the caller's work tree (no default under the checkout, "
                         "F72); the evidence rows are written beside each campaign's scores")
    ap.add_argument("--zone", type=Path, required=True,
                    help="declared conflict-zone document the rows were scored against")
    ap.add_argument("--out", type=Path, default=None,
                    help="summary document (default: <scores-root>/audit/commitment_audit.json)")
    args = ap.parse_args()
    try:
        declarations = load_declarations(args.campaign)
    except ValueError as exc:
        ap.error(str(exc))
    out = args.out or args.scores_root / "audit/commitment_audit.json"
    audit(args.results_root, args.scores_root, out, instrument.load_zone(args.zone), declarations)


if __name__ == "__main__":
    main()
