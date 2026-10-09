#!/usr/bin/env python3
"""Post-lift rest check: did every placed prop stay where the harness put it?

The CARLA leaderboard stashes a scenario's props about 200 m underground at
route start and teleports each one to its placed pose when the ego reaches the
trigger (the "lift", a height jump between two recorder frames that the
trajectory extract labels trigger_lift_frame). The countersign certifies the
pose a prop is teleported to; nobody had read what it does in the seconds after
it lands. This tool reads it from the recordings every raw bundle holds, after
the fact, for every scenario actor of every run under one or more results roots.

Per run and prop it reports, all relative to the LANDED pose (the lift frame):
  - the settle: the displacement and rotation (|roll| + |pitch|) at
    SETTLE_WINDOW_S after the lift; `settled` when both are within the
    reporting resolution (MOTION_XY_M, MOTION_DEG). A prop that tips at the
    lift is unsettled; a car that bounces on its suspension and comes to rest
    is settled;
  - the raw onset: the first frame after landing at which the prop is past the
    resolution, its time after the lift, and the nearest other tracked actor at
    that frame (3-D distance where heights are known; a stashed prop 200 m below
    is not a neighbour);
  - motion after the settle window, relative to the SETTLED pose: the first
    such frame, whether the hero had touched the prop by then (the recorder's
    hero collision events), the largest displacement before the hero's first
    contact, and the final displacement;
  - `kind`: "lifted" when the actor was stashed and teleported to its pose (a
    placed prop, or a vehicle placed as an obstacle: it has a trigger lift),
    "spawned_in_place" when it was created at road level (a flow car under
    its own control). The summary lists lifted props; --all lists every
    scenario actor.

Per family and variant: runs, unsettled runs, the median settle rotation, runs
that moved after settling before any hero contact, the median onset, runs with
nothing within NEIGHBOUR_M at the raw onset, the largest pre-contact
displacement.

It measures and reports; nothing here decides. The constants are reporting
resolution, printed in the output beside the raw numbers. A declared
scenario-defect row (scl_core/scenario_defects.py) cites this table as its
evidence; the declaration is Milan's. (F66 part two, F68; 2026-10-06.)

    conda run -n scl_prototype python \\
        adapters/fail2drive/scripts/multi_model_eval/placement_rest_check.py \\
        --results-root <unpacked>/campaign_carla_v06 [--results-root ...] \\
        [--family fm_005_hero] [--all] --out rest_check.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trajectory_extract as te  # noqa: E402

MOTION_XY_M = 0.05     # reporting resolution: planar displacement
MOTION_DEG = 5.0       # reporting resolution: |roll| + |pitch| change
SETTLE_WINDOW_S = 1.0  # the pose this long after the lift is the rest pose when it is within the resolution
NEIGHBOUR_M = 3.0      # "nothing nearby" at the onset: no tracked actor within this (3-D where heights are known)


def _rot(t, i, j):
    return abs(t["roll"][i] - t["roll"][j]) + abs(t["pitch"][i] - t["pitch"][j])


def _disp(t, i, j):
    return math.hypot(t["x"][i] - t["x"][j], t["y"][i] - t["y"][j])


def _index_at(fr, frame):
    return min(range(len(fr)), key=lambda k: abs(fr[k] - frame))


def _nearest(data, aid, t, i):
    f = t["frames"][i]
    best = None
    for oid, tk in (data.get("tracks") or {}).items():
        if oid == aid or f not in tk["frames"]:
            continue
        k = tk["frames"].index(f)
        d = math.hypot(tk["x"][k] - t["x"][i], tk["y"][k] - t["y"][i])
        if "z" in tk:
            d = math.hypot(d, tk["z"][k] - t["z"][i])
        if best is None or d < best[0]:
            a = (data.get("actors") or {}).get(oid, {})
            best = (round(d, 2), "hero" if oid == str(data.get("hero_id")) else a.get("role_name"),
                    a.get("blueprint"))
    return {"distance_m": best[0], "role": best[1], "blueprint": best[2]} if best else None


def check_run(data: dict) -> list:
    """One row per scenario actor of one extract."""
    rows = []
    dt = data["dt_s"]
    for aid, actor in sorted((data.get("actors") or {}).items()):
        if actor.get("role_name") != "scenario":
            continue
        t = (data.get("tracks") or {}).get(aid)
        if not t or not t.get("frames") or "roll" not in t:
            continue
        fr = t["frames"]
        lift = actor.get("trigger_lift_frame")
        i0 = fr.index(lift) if lift in fr else 0
        last = len(fr) - 1
        i_settle = min(last, _index_at(fr, fr[i0] + round(SETTLE_WINDOW_S / dt)))
        hits = sorted(c["frame"] for c in data.get("collisions") or [] if str(c.get("other_id")) == aid)
        i_hit = _index_at(fr, hits[0]) if hits else None
        settle_disp, settle_rot = _disp(t, i_settle, i0), _rot(t, i_settle, i0)
        raw_onset = next((i for i in range(i0 + 1, last + 1)
                          if _disp(t, i, i0) > MOTION_XY_M or _rot(t, i, i0) > MOTION_DEG), None)
        onset = next((i for i in range(i_settle + 1, last + 1)
                      if _disp(t, i, i_settle) > MOTION_XY_M or _rot(t, i, i_settle) > MOTION_DEG), None)
        before = range(i_settle, i_hit if i_hit is not None else last + 1)
        row = {
            "actor_id": int(aid), "blueprint": actor.get("blueprint"),
            "kind": "lifted" if lift in fr else "spawned_in_place",
            "lift_frame": lift, "lift_time_s": round(fr[i0] * dt, 2) if lift in fr else None,
            "landed_pose": [round(t["x"][i0], 3), round(t["y"][i0], 3), round(t["z"][i0], 3)],
            "settle_displacement_m": round(settle_disp, 3), "settle_rotation_deg": round(settle_rot, 1),
            "settled": settle_disp <= MOTION_XY_M and settle_rot <= MOTION_DEG,
            "raw_onset_s_after_lift": round((fr[raw_onset] - fr[i0]) * dt, 2) if raw_onset is not None else None,
            "raw_onset_displacement_m": round(_disp(t, raw_onset, i0), 3) if raw_onset is not None else None,
            "raw_onset_rotation_deg": round(_rot(t, raw_onset, i0), 1) if raw_onset is not None else None,
            "nearest_at_raw_onset": _nearest(data, aid, t, raw_onset) if raw_onset is not None else None,
            "moved_after_settle_s_after_lift": round((fr[onset] - fr[i0]) * dt, 2) if onset is not None else None,
            "moved_after_settle_before_hero_contact": (onset is not None
                                                       and (i_hit is None or onset < i_hit - 1)),
            "hero_first_contact_s": round(hits[0] * dt, 2) if hits else None,
            "max_displacement_before_contact_m": round(max((_disp(t, i, i_settle) for i in before), default=0.0), 3),
            "max_rotation_before_contact_deg": round(max((_rot(t, i, i_settle) for i in before), default=0.0), 1),
            "final_displacement_m": round(_disp(t, last, i0), 3),
        }
        rows.append(row)
    return rows


def _scan(root: Path, family: str | None):
    for p in sorted(root.glob("*/*/seed_*/trajectories/RouteScenario_*_rep0.json.gz")):
        fam, model, seed = p.parts[-5], p.parts[-4], p.parts[-3]
        if family and fam != family:
            continue
        yield fam, model, seed, p


def _scenario_id(seed_dir: Path, extract_path: Path) -> str | None:
    """The record beside the extract whose route id ends with this extract's variant digits."""
    digits = te.route_id_digits(extract_path.name)
    for rec in sorted(seed_dir.glob("*.json")):
        try:
            d = json.loads(rec.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        records = (d.get("_checkpoint") or {}).get("records") or []
        if records and te.route_id_digits(records[0].get("route_id")) == digits:
            return rec.stem
    return None


def summarise(runs: list) -> list:
    per = defaultdict(list)
    for r in runs:
        per[(r["family"], r["scenario_id"] or r["extract"], r["blueprint"], r["kind"])].append(r)
    summary = []
    for (fam, sid, bp, kind), rs in sorted(per.items()):
        unsettled = [r for r in rs if not r["settled"]]
        moved = [r for r in rs if r["moved_after_settle_before_hero_contact"]]
        alone = [r for r in rs if r["nearest_at_raw_onset"] and r["nearest_at_raw_onset"]["distance_m"] > NEIGHBOUR_M]
        onsets = [r["moved_after_settle_s_after_lift"] for r in moved]
        summary.append({
            "family": fam, "scenario_id": sid, "blueprint": bp, "kind": kind, "runs": len(rs),
            "runs_unsettled": len(unsettled),
            "median_settle_rotation_deg": round(statistics.median(r["settle_rotation_deg"] for r in rs), 1),
            "median_settle_displacement_m": round(statistics.median(r["settle_displacement_m"] for r in rs), 3),
            "median_raw_onset_s_after_lift": (round(statistics.median(r["raw_onset_s_after_lift"] for r in unsettled), 2)
                                              if unsettled and all(r["raw_onset_s_after_lift"] is not None for r in unsettled) else None),
            "runs_moved_after_settle_before_hero_contact": len(moved),
            "median_moved_after_settle_s": round(statistics.median(onsets), 2) if onsets else None,
            "runs_nothing_within_neighbour_m_at_raw_onset": len(alone),
            "max_displacement_before_contact_m": max(r["max_displacement_before_contact_m"] for r in rs),
            "max_rotation_before_contact_deg": max(r["max_rotation_before_contact_deg"] for r in rs),
            "campaigns": sorted({r["campaign"] for r in rs}),
        })
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-root", action="append", required=True, type=Path,
                    help="an unpacked campaign tree <campaign>/<family>/<model>/seed_<k>/...; repeatable")
    ap.add_argument("--family", default=None, help="restrict to one family id")
    ap.add_argument("--all", action="store_true", help="print every scenario actor, the spawned-in-place ones too")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    runs = []
    for root in args.results_root:
        root = root.resolve()
        for fam, model, seed, p in _scan(root, args.family):
            data = te.read_extract(p)
            sid = _scenario_id(p.parent.parent, p)
            for row in check_run(data):
                row.update({"campaign": root.name, "family": fam, "model": model, "seed": seed,
                            "scenario_id": sid, "extract": p.name})
                runs.append(row)
    summary = summarise(runs)
    doc = {
        "check_kind": "placement_rest_check/v0.2",
        "resolution": {"motion_xy_m": MOTION_XY_M, "motion_deg": MOTION_DEG, "settle_window_s": SETTLE_WINDOW_S,
                       "neighbour_m": NEIGHBOUR_M,
                       "note": "reporting resolution and classification only; nothing here decides"},
        "results_roots": [str(r.resolve()) for r in args.results_root],
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "extract_tool_sha256": hashlib.sha256(Path(te.__file__).read_bytes()).hexdigest(),
        "summary": summary, "runs": runs,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1) + "\n")
    hdr = f"{'family':24s} {'variant':44s} {'prop':16s} {'runs':>4} {'unsett':>6} {'rot':>6} {'onset':>6} {'moved':>5} {'alone':>5} {'maxdisp':>8}"
    print(hdr)
    for s in summary:
        if s["kind"] != "lifted" and not args.all:
            continue
        print(f"{s['family']:24s} {str(s['scenario_id'])[-44:]:44s} {str(s['blueprint']).split('.')[-1]:16s} "
              f"{s['runs']:>4} {s['runs_unsettled']:>6} {s['median_settle_rotation_deg']:>6.1f} "
              f"{str(s['median_raw_onset_s_after_lift']):>6} {s['runs_moved_after_settle_before_hero_contact']:>5} "
              f"{s['runs_nothing_within_neighbour_m_at_raw_onset']:>5} {s['max_displacement_before_contact_m']:>8.2f}")
    print(f"{len(runs)} prop-runs over {len(summary)} (family, variant, prop) cells -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
