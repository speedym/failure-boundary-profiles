#!/usr/bin/env python3
"""Flow gap certificate (F55, 2026-09-28): the flow families' analog of the
corridor's passable / blocked assignment, kinematic and agent-independent,
measured from the raw bundles' trajectory extracts against the declared
conflict-zone document. Three bands per variant and seed (Milan 2026-09-28):

  usable       the usable window fits a crossing FROM REST at the junction
               (the kinematic floor plus the declared margin): go is right for
               any agent, a refusal is wrong;
  conditional  it fits a ROLLING crossing at the family's reference entry
               speed plus the margin, not one from rest: go is right for an
               agent that never stopped, wait is right for one that did (scored
               per run from the recorded entry state);
  wait         even a rolling crossing does not fit: go is wrong regardless of
               outcome (as threading a blocked width is). The blocked analog.

What is measured (every number names its method and sample count, principle 5):
  the crossing region      the ego's swept body inside the declared lane polygon
                           on the family's reference clean crossing (the expert's
                           first clean run; any agent's if none);
  per scenario car         its arrival at and departure from that region, hence
                           the LOCAL headways at the crossing (front to front) and
                           each car's OCCUPANCY of it; the usable window of a
                           seed and variant = median local headway - median
                           occupancy over the runs whose traffic the ego did not
                           perturb (no collision; either no entry during the flow
                           or a clean crossing);
  the vehicle              its peak acceleration over 0.5 s windows (a vehicle
                           constant: every agent measures the same), the ego's
                           registry extents, the crossing path length (duration x
                           entry speed over clean rolling crossings);
  the floor from rest      kinematic: the p10 of the measured standstill-to-exit
                           distances, covered from rest at the measured
                           acceleration capped at the speed limit; the measured
                           clearing times are reported beside it (no clean
                           clearing may beat the floor by more than the sampling
                           step);
  the rolling reference    crossing path length over the median entry speed of
                           the clean rolling crossings.
The label is the TRAFFIC's (deterministic per seed), so waits and collisions
are compared too; a variant gets one band only when its seeds agree, else it
is "mixed" (the transition-band analog). The margin is declared, never derived.
The realized headway per seed is what is compared, never the nominal: short
gaps stretch on congested seeds (flow_aggregate's axis_divergent). Honest
difference, stated wherever the label is used: a blocked width is a physical
fact; "wait" is a rule this certificate states.

    conda run -n scl_prototype python flow_gap_certificate.py \\
        --family artifacts/examples/solved_families/fm_011_flow_speed_sweep.json \\
        --zone artifacts/examples/scoring_instruments/carla_flow_conflict_zone_town03_j103_r144.json \\
        --bundles ~/scl/campaigns/campaign_carla_v04.zip ... --out <certificate.json> [--rows <runs.jsonl>]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import statistics
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import derive_measured_go as dmg  # noqa: E402
from scl_core.records import portable_path  # noqa: E402
from scl_core import scenario_defects  # noqa: E402
import flow_aggregate as fa       # noqa: E402
import route_metrics as rm        # noqa: E402
import score_runs as sr           # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from shapely.ops import unary_union   # noqa: E402

KIND = "flow_gap_certificate/v0.1"
BANDS = ("usable", "conditional", "wait")
DEFECTS_KIND = scenario_defects.REGISTER_KIND   # the one declared register of both axes (scl_core/scenario_defects.py)
DEFECT = "defect"                              # the band of a registered cell: outside BANDS, no decision, no scope
EGO_HX, EGO_HY = 2.446, 0.918      # the go tool's defaults: the registry's spawn-probed lincoln mkz half extents
STILL_MPS, STILL_MIN_S, STILL_NEAR_M = 0.2, 0.5, 20.0
ACCEL_WINDOW_S, ACCEL_SKIP_S = 0.5, 1.0
SPEED_LIMIT_MPS = 60.0 / 3.6       # the backend's max_speed_kmh default (backend_registry), stated here
METHOD = {
    "crossing_region": "union of the ego's oriented body polygons from zone entry to exit on the reference clean "
                       "crossing, clipped to the declared lane polygon",
    "local_headway": "front-to-front time between consecutive scenario cars' first intersection with the crossing "
                     "region; occupancy = a car's first to last intersection",
    "usable_window": "median local headway minus median occupancy over unperturbed runs (no collision; no entry "
                     "during the flow, or a clean crossing); spawn-interval headway when no local headway could be "
                     "measured (a platoon)",
    "standstill": f"the last stretch of >= {STILL_MIN_S} s below {STILL_MPS} m/s ending before the zone entry, within "
                  f"{STILL_NEAR_M} m of path of it",
    "acceleration": f"max speed change over {ACCEL_WINDOW_S} s windows after the first {ACCEL_SKIP_S} s, p50 across runs",
    "floor_from_rest": "time to cover the p10 standstill-to-exit distance from rest at the measured acceleration, "
                       "capped at the speed limit",
    "rolling_reference": "crossing path length (median of crossing duration x entry speed over clean rolling "
                         "crossings) over the median entry speed of those crossings",
    "bands": "usable: window >= floor_from_rest + margin; conditional: window >= rolling_reference + margin; "
             "else wait; a variant is mixed when its seeds disagree",
    "platoon": "a seed whose spawn-interval headway is no longer than a car's occupancy of the crossing has no "
               "window between cars (nose to tail); its window is spawn headway minus occupancy, never the per-car "
               "arrival measurement, hence wait",
}


DECISION_RULES = ("per run against the cell's band, on the measured go alone (the decision is the commitment; "
                  "whether the crossing was clean is p_clean's question, as on the threading side where a contact "
                  "on a passable width is still a go): usable - go is right, no-go is wrong; wait - no-go is right, "
                  "go is wrong whatever the outcome; conditional - go is right for a rolling entry and wrong for "
                  "an entry from rest, no-go is right; the balanced scope is usable + wait (the decision-accuracy "
                  "pools), the conditional band is reported beside it, never inside it; a cell the scenario-defect "
                  "register names carries the band defect: no decision, no scope (F57)")


def load_certificate(path):
    """(document, sha256) of a flow gap certificate; refuses any other kind."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    if doc.get("certificate_kind") != KIND:
        raise ValueError(f"{Path(path).name}: certificate_kind {doc.get('certificate_kind')!r}; this reader reads {KIND}")
    return doc, hashlib.sha256(raw).hexdigest()


def load_defects(path):
    """(document, sha256) of the scenario-defect register: the shared reader
    (scl_core.scenario_defects.load_register), one kind for both axes."""
    return scenario_defects.load_register(path)


def defect_for(defects, family_id, scenario_id, seed):
    """The register entry naming this cell, or None (scl_core.scenario_defects.entry_for)."""
    return scenario_defects.entry_for(defects, family_id, scenario_id, seed)


def seed_band(doc, scenario_id, seed):
    """The certificate's cell for one scenario and seed (seed as the runner's integer or 'seed_N'), or None."""
    key = seed if isinstance(seed, str) else f"seed_{seed}"
    for cell in doc.get("seeds", []):
        if cell["scenario_id"] == scenario_id and cell["seed"] == key:
            return cell
    return None


def decision_for(band, p_go, primary, entry_state):
    """{decision, correct, scope} for one run (DECISION_RULES). decision is read from the measured go
    (None when it was refused); correct and scope are None outside the three bands. `primary` (the
    outcome label) is taken so a caller passes the whole row's facts; the rule reads the commitment only."""
    del primary
    decision = None if p_go is None else ("go" if p_go else "wait")
    if band not in BANDS or decision is None:
        return {"decision": decision, "correct": None, "scope": None}
    if band == "usable":
        return {"decision": decision, "correct": decision == "go", "scope": "balanced"}
    if band == "wait":
        return {"decision": decision, "correct": decision == "wait", "scope": "balanced"}
    correct = decision == "wait" or entry_state == "rolling"
    return {"decision": decision, "correct": correct, "scope": "conditional"}


# --- measurement --------------------------------------------------------------

def _speeds(track, dt):
    xs, ys, fs = track["x"], track["y"], track["frames"]
    v = [0.0]
    for i in range(1, len(fs)):
        df = max(fs[i] - fs[i - 1], 1)
        v.append(math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]) / (df * dt))
    return v


def _peak_accel(v, dt):
    w = max(1, int(ACCEL_WINDOW_S / dt)); s = int(ACCEL_SKIP_S / dt); best = 0.0
    for i in range(s + w, len(v)):
        best = max(best, (v[i] - v[i - w]) / (w * dt))
    return round(best, 3)


def _car_half_extents(blueprint, extents):
    h = extents.get(blueprint)
    return (h, h * 0.42) if h else (2.0, 0.9)


def _body(track, i, hx, hy):
    return Polygon(rm.rect_corners(track["x"][i], track["y"][i], track["yaw"][i], hx, hy))


def junction_standstill(hero, entry_idx, dt):
    """Index of the last frame of the ego's standstill before the zone entry (a stretch of at least
    STILL_MIN_S below STILL_MPS, within STILL_NEAR_M of path of the entry), or None: a rolling entry."""
    xs, ys = hero["x"], hero["y"]
    v = _speeds(hero, dt)
    still_end = None; run_len = 0
    for i in range(entry_idx, -1, -1):
        if v[i] < STILL_MPS:
            run_len += 1
            if run_len * dt >= STILL_MIN_S:
                still_end = i + run_len - 1
                break
        else:
            run_len = 0
    if still_end is not None:
        path = sum(math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]) for i in range(still_end + 1, entry_idx + 1))
        if path > STILL_NEAR_M:
            still_end = None
    return still_end


def entry_state_from_extract(data, entry_frame):
    """("rest" | "rolling", entry speed m/s) for the ego's recorded entry frame, or (None, None)."""
    hero = data["tracks"][str(data["hero_id"])]
    dt = data.get("dt_s", 0.05)
    try:
        idx = hero["frames"].index(entry_frame)
    except ValueError:
        return None, None
    still = junction_standstill(hero, idx, dt)
    return ("rest" if still is not None else "rolling"), round(_speeds(hero, dt)[idx], 3)


def zone_contact(hero, zone_poly, hx=EGO_HX, hy=EGO_HY):
    """(entry index, exit index): first and last frame index whose body touches the zone."""
    entry = exit_ = None
    for i in range(len(hero["frames"])):
        if _body(hero, i, hx, hy).intersects(zone_poly):
            if entry is None:
                entry = i
            exit_ = i
    return entry, exit_


def crossing_region(data, zone_poly, hx=EGO_HX, hy=EGO_HY):
    """The ego's swept body inside the zone on this run, or None when it did not cross."""
    hero = data["tracks"][str(data["hero_id"])]
    e, x = zone_contact(hero, zone_poly, hx, hy)
    if e is None or x is None or x >= len(hero["frames"]) - 1:
        return None
    return unary_union([_body(hero, i, hx, hy) for i in range(e, x + 1)]).intersection(zone_poly)


def measure_run(data, zone_poly, region, extents=None, hx=EGO_HX, hy=EGO_HY):
    """Everything the certificate reads from one run (see the module docstring)."""
    extents = extents if extents is not None else {}
    dt = data.get("dt_s", 0.05)
    hero = data["tracks"][str(data["hero_id"])]
    fs, xs, ys = hero["frames"], hero["x"], hero["y"]
    v = _speeds(hero, dt)
    entry, exit_ = zone_contact(hero, zone_poly, hx, hy)
    window = dmg.flow_window(data)
    exited = exit_ is not None and exit_ < len(fs) - 1
    r = {"entry_frame": fs[entry] if entry is not None else None,
         "exit_frame": fs[exit_] if exited else None,
         "flow_window": list(window) if window else None,
         "go": bool(window is not None and entry is not None and window[0] <= fs[entry] <= window[1]),
         "entry_after_flow": bool(window is not None and entry is not None and fs[entry] > window[1]),
         "entry_speed_mps": round(v[entry], 3) if entry is not None else None,
         "peak_accel_mps2": _peak_accel(v, dt),
         "collisions": len(data.get("collisions") or []),
         "standstill_at_junction": False}
    if entry is not None:
        still_end = junction_standstill(hero, entry, dt)
        r["standstill_at_junction"] = still_end is not None
        if exited:
            r["crossing_duration_s"] = round((fs[exit_] - fs[entry]) * dt, 2)
            if still_end is not None:
                r["clearing_time_from_rest_s"] = round((fs[exit_] - fs[still_end]) * dt, 2)
                r["clearing_distance_m"] = round(sum(math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1])
                                                     for i in range(still_end + 1, exit_ + 1)), 2)
    arrivals = []
    if region is not None:
        for aid, meta in data["actors"].items():
            if meta.get("role_name") != "scenario":
                continue
            t = data["tracks"].get(aid)
            if not t or not t.get("frames"):
                continue
            chx, chy = _car_half_extents(meta.get("blueprint", ""), extents)
            first = last = None
            for i in range(len(t["frames"])):
                if _body(t, i, chx, chy).intersects(region):
                    if first is None:
                        first = t["frames"][i]
                    last = t["frames"][i]
            if first is not None:
                arrivals.append((first, last))
    arrivals.sort()
    r["local_headways_s"] = [round((arrivals[i][0] - arrivals[i - 1][0]) * dt, 2) for i in range(1, len(arrivals))]
    r["occupancy_s"] = [round((b - a) * dt, 2) for a, b in arrivals]
    if r["go"] and arrivals:
        e = fs[entry]
        nxt = [a for a, _ in arrivals if a > e]; prev = [b for _, b in arrivals if b <= e]
        if nxt and prev:
            r["usable_window_taken_s"] = round((min(nxt) - max(prev)) * dt, 2)
    return r


def _bundle_provenance(zf, campaign_id):
    try:
        doc = json.loads(zf.read(f"{campaign_id}/bundle_provenance.json"))
    except KeyError:
        return {}
    return {"manifest_sha256": doc.get("manifest_sha256"), "bundler_sha256": doc.get("bundler_sha256"),
            "produced_date": doc.get("produced_date")}


def measure_bundles(zips, family_id, zone, extents=None):
    """Rows for every run of the family in the given raw zips, the crossing region and the inputs."""
    extents = extents if extents is not None else sr._registry_extents()
    tag = family_id.split("_")[1]; name = "_".join(family_id.split("_")[2:])
    loaded, inputs = [], []
    for zpath in zips:
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            campaign_id = names[0].split("/")[0]
            members = sorted(n for n in names if f"/{family_id}/" in n and n.endswith("_rep0.json.gz")
                             and "/trajectories/" in n)
            if not members:
                continue
            inputs.append({"zip": Path(zpath).name, "campaign_id": campaign_id, "runs": len(members),
                           **_bundle_provenance(zf, campaign_id)})
            for n in members:
                parts = n.split("/"); agent, seed = parts[2], parts[3]
                vnum = int(n.rsplit("/", 1)[1].replace("_rep0.json.gz", "")[-2:])
                sid = f"route_002269_{tag}_{name}_v{vnum:02d}"
                corridor = dmg.flow_corridor(zone, sid)
                if corridor is None:
                    continue
                data = json.load(gzip.open(io.BytesIO(zf.read(n)), "rt"))
                with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tmp:
                    tmp.write(zf.read(n)); tmp_path = Path(tmp.name)
                flow = fa.measure_flow(tmp_path); tmp_path.unlink()
                loaded.append({"campaign": campaign_id, "agent": agent, "seed": seed, "variant": vnum, "scenario_id": sid,
                               "data": data, "zone_poly": Polygon(corridor["corners"]),
                               "spawn_headway_s": flow.get("measured_crossing_time_gap_s") if flow else None,
                               "flow_speed_mps": flow.get("measured_flow_speed") if flow else None})
    if not loaded:
        raise SystemExit(f"no runs of {family_id} in {[Path(z).name for z in zips]}")
    ordered = sorted(loaded, key=lambda r: (r["agent"] != "pdm_lite_expert", r["campaign"], r["seed"], r["variant"]))
    region, reference = None, None
    for r in ordered:
        if r["data"].get("collisions"):
            continue
        region = crossing_region(r["data"], r["zone_poly"])
        if region is not None:
            reference = {"campaign": r["campaign"], "agent": r["agent"], "seed": r["seed"], "variant": r["variant"]}
            break
    if region is None:
        raise SystemExit(f"{family_id}: no clean crossing to build the crossing region from")
    rows = []
    for r in loaded:
        m = measure_run(r["data"], r["zone_poly"], region, extents)
        rows.append({k: v for k, v in r.items() if k not in ("data", "zone_poly")} | m)
    return rows, {"reference_run": reference, "area_m2": round(region.area, 2),
                  "bounds": [round(b, 2) for b in region.bounds]}, inputs


# --- certification -------------------------------------------------------------

def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def _quantile(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[int(p * (len(xs) - 1))], 3) if xs else None


def kinematic_time(distance_m, accel_mps2, v_max_mps):
    """Time to cover a distance from rest at constant acceleration, capped at v_max."""
    if accel_mps2 <= 0 or distance_m <= 0:
        return None
    d_accel = v_max_mps ** 2 / (2 * accel_mps2)
    if distance_m <= d_accel:
        return round(math.sqrt(2 * distance_m / accel_mps2), 3)
    return round(v_max_mps / accel_mps2 + (distance_m - d_accel) / v_max_mps, 3)


def unperturbed(r):
    """A run whose traffic the ego did not perturb: no collision and either no entry during the flow
    or a clean crossing."""
    return not r["collisions"] and (not r["go"] or r.get("exit_frame") is not None)


def certify(rows, family_id, margin_s, zone_sha256, region, inputs, speed_limit_mps=SPEED_LIMIT_MPS, defects=None):
    """`defects`: (register document, its sha256, its repo-relative path) or None. A registered cell keeps its
    measured window and takes the band `defect`; a variant whose remaining seeds agree keeps their band, one
    with every seed registered is `defect`."""
    defects_doc, defects_sha, defects_path = defects or (None, None, None)
    applied = []
    clean_go = [r for r in rows if r["go"] and not r["collisions"] and r.get("crossing_duration_s")]
    rest = [r for r in clean_go if r.get("clearing_time_from_rest_s")]
    rolling = [r for r in clean_go if not r["standstill_at_junction"] and r.get("entry_speed_mps")]
    accel = _median([r["peak_accel_mps2"] for r in rows])
    d_p10 = _quantile([r["clearing_distance_m"] for r in rest], 0.10)
    floor_rest = kinematic_time(d_p10, accel, speed_limit_mps) if d_p10 and accel else None
    path_len = _median([r["crossing_duration_s"] * r["entry_speed_mps"] for r in rolling])
    v_roll = _median([r["entry_speed_mps"] for r in rolling])
    rolling_ref = round(path_len / v_roll, 3) if path_len and v_roll else None
    if floor_rest is None or rolling_ref is None:
        raise SystemExit(f"{family_id}: the floors cannot be measured (from-rest crossings {len(rest)}, rolling {len(rolling)})")
    measured_rest = [r["clearing_time_from_rest_s"] for r in rest]
    by_vs = {}
    for r in rows:
        by_vs.setdefault((r["variant"], r["seed"]), []).append(r)
    table = []
    for (variant, seed), grp in sorted(by_vs.items()):
        ok = [r for r in grp if unperturbed(r)]
        local = [h for r in ok for h in r["local_headways_s"]]
        occ = [o for r in ok for o in r["occupancy_s"]]
        source = "local"
        headway = _median(local)
        if headway is None:
            headway = _median([r["spawn_headway_s"] for r in ok or grp]); source = "spawn_interval"
        occupancy = _median(occ) if occ else _median([o for r in grp for o in r["occupancy_s"]])
        spawn = _median([r["spawn_headway_s"] for r in ok or grp])
        window = round(headway - occupancy, 3) if headway is not None and occupancy is not None else None
        platoon = spawn is not None and occupancy is not None and spawn <= occupancy
        if platoon:
            # cars nose to tail: no window exists between them whatever the per-car arrivals say
            window = round(spawn - occupancy, 3); source = "platoon"
        if window is None:
            band = "unmeasured"
        elif window >= floor_rest + margin_s:
            band = "usable"
        elif window >= rolling_ref + margin_s:
            band = "conditional"
        else:
            band = "wait"
        entry = defect_for(defects_doc, family_id, grp[0]["scenario_id"], seed)
        if entry is not None:
            band = DEFECT
            applied.append({"variant": variant, "seed": seed, "scenario_id": grp[0]["scenario_id"], "reason": entry["reason"]})
        table.append({"variant": variant, "seed": seed, "scenario_id": grp[0]["scenario_id"],
                      "flow_speed_mps": _median([r["flow_speed_mps"] for r in grp]),
                      "spawn_headway_s": _median([r["spawn_headway_s"] for r in grp]),
                      "local_headway_s": headway, "headway_source": source,
                      "local_headway_spread_s": [min(local), max(local)] if local else None,
                      "occupancy_s": occupancy, "usable_window_s": window, "band": band,
                      "n_runs": len(grp), "n_unperturbed": len(ok)})
    variants = {}
    for t in table:
        variants.setdefault(t["variant"], []).append(t)
    per_variant = []
    for variant, ts in sorted(variants.items()):
        bands = sorted({t["band"] for t in ts if t["band"] != DEFECT}) or [DEFECT]
        per_variant.append({"variant": variant, "scenario_id": ts[0]["scenario_id"],
                            "band": bands[0] if len(bands) == 1 else "mixed",
                            "seed_bands": {t["seed"]: t["band"] for t in ts},
                            "usable_window_s": {t["seed"]: t["usable_window_s"] for t in ts}})
    counts = {b: sum(1 for v in per_variant if v["band"] == b) for b in BANDS + ("mixed", "unmeasured", DEFECT)}
    return {
        "certificate_kind": KIND, "family_id": family_id, "produced_date": time.strftime("%Y-%m-%d"),
        "definition": ("three bands per variant and seed from the realized traffic at the crossing against the "
                       "vehicle's kinematic floors: usable (fits a crossing from rest), conditional (fits a rolling "
                       "crossing only), wait (fits neither); 'wait' is the blocked analog and a rule, not a physical "
                       "impossibility"),
        "margin_s": margin_s,
        "zone": {"sha256": zone_sha256},
        "crossing_region": region,
        "vehicle": {"ego_half_extents_m": [EGO_HX, EGO_HY], "peak_accel_mps2": accel,
                    "peak_accel_n_runs": len(rows), "speed_limit_mps": round(speed_limit_mps, 3),
                    "crossing_path_m": path_len, "crossing_path_n_runs": len(rolling)},
        "floors": {"from_rest_s": floor_rest, "from_rest_distance_p10_m": d_p10,
                   "measured_clearing_from_rest": {"n": len(rest), "min_s": min(measured_rest) if rest else None,
                                                   "p10_s": _quantile(measured_rest, 0.10), "median_s": _median(measured_rest),
                                                   "expert_median_s": _median([r["clearing_time_from_rest_s"] for r in rest
                                                                               if r["agent"] == "pdm_lite_expert"])},
                   "rolling_reference_s": rolling_ref, "rolling_reference_entry_speed_mps": v_roll,
                   "rolling_n_runs": len(rolling)},
        "thresholds_s": {"usable_at_least": round(floor_rest + margin_s, 3),
                         "conditional_at_least": round(rolling_ref + margin_s, 3)},
        "variants": per_variant, "seeds": table, "counts": counts,
        "defects": {"register": ({"path": defects_path, "sha256": defects_sha, "kind": DEFECTS_KIND}
                                 if defects_doc is not None else None),
                    "applied": applied},
        "method": METHOD,
        "provenance": {"tool": Path(__file__).name,
                       "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       "inputs": inputs, "n_runs": len(rows)},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", required=True, type=Path, help="the family manifest (its manifest_id, the fm_ id, is read from it)")
    ap.add_argument("--zone", required=True, type=Path, help="the declared conflict-zone document")
    ap.add_argument("--bundles", required=True, nargs="+", type=Path, help="raw campaign zips holding the family")
    ap.add_argument("--margin-s", type=float, default=0.5)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rows", type=Path, default=None, help="also write the per-run measurements (jsonl)")
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register (scoring_instruments/carla_scenario_defects.json, "
                         "F57, one register for both axes since F66); its cells take the band defect and the "
                         "document pins it")
    args = ap.parse_args()
    manifest = json.loads(args.family.read_text())
    family_id = manifest.get("manifest_id") or manifest["family_id"]   # the fm_ id; family_id is the semantic family
    zone = dmg.load_zone(args.zone)
    defects = None
    if args.defects is not None:
        doc_d, sha_d = load_defects(args.defects)
        defects = (doc_d, sha_d, portable_path(args.defects.resolve(), REPO_ROOT))
    rows, region, inputs = measure_bundles(args.bundles, family_id, zone)
    doc = certify(rows, family_id, args.margin_s, hashlib.sha256(args.zone.read_bytes()).hexdigest(), region, inputs,
                  defects=defects)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1) + "\n")
    if args.rows:
        with open(args.rows, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    print(f"{family_id}: {len(rows)} runs; floors from rest {doc['floors']['from_rest_s']} s, rolling "
          f"{doc['floors']['rolling_reference_s']} s, margin {args.margin_s} s; variants {doc['counts']} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
