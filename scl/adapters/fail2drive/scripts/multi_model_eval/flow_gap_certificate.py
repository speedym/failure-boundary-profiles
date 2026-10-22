#!/usr/bin/env python3
"""Flow gap certificate (F55, 2026-09-28): the flow families' analog of the
corridor's passable / blocked assignment, kinematic and agent-independent,
measured from the raw bundles' trajectory extracts against the declared
conflict-zone document. Three bands per variant and seed (Milan 2026-09-28):

  usable       the usable window fits a crossing FROM REST at the junction
               (the kinematic floor plus the declared margin): a clean crossing
               is right for any agent, a refusal or a go that collides is wrong;
  conditional  it fits a ROLLING crossing at the family's reference entry
               speed plus the margin, not one from rest: a clean rolling crossing
               is right for an agent that never stopped, wait is right for one
               that did (scored per run from the recorded entry state);
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
                           occupancy over every run of the cell, each read up to
                           the ego's COMMITMENT, its first body contact with the
                           lane (2026-10-19): an arrival counts when it precedes
                           that contact, an occupancy when the car left the
                           region before it, a pair-gap frame when it precedes
                           it; a run whose ego never entered is read whole. The
                           label "unperturbed" that chose whole runs until then
                           (no collision; no entry or a clean exit) is gone: it
                           kept the traffic behind a clean crossing, dropped a
                           run's clean flow before a late collision, and called
                           a run unperturbed whose ego slowed the oncoming cars
                           for 100 s by standing at the lane edge;
  per pair of cars         the PAIR GAP (2026-10-19): while the first car is in the
                           region and the next car exists anywhere behind it, the
                           distance between them over the next car's speed at that
                           frame, averaged; a prediction of the arrival headway
                           from the present state, read when the recording ended
                           before the next car arrived. The headway a cell uses is
                           the local headway where two cars arrived, else the pair
                           gap, else the spawn cadence; `headway_source` says which;
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
The realized headway per seed is what is compared, never the nominal, and since
F96 (2026-10-18) it is the variant's AXIS as well: `variants[].headway_at_crossing_s`,
the median over the seeds of the local headway, is the x every flow row carries
and every profile is fitted and drawn on (flow_aggregate.to_run_row reads it
through `variant_axis`); the nominal gap names the variant and rides beside it.
Every reading of a run stops at the ego's first entry into the lane, the
per-run spawn cadence (flow_aggregate.measure_flow_data, the fallback headway
and the platoon test) as the crossing readings: the cars that back up behind a
stalled ego are not the traffic's cadence. Honest difference, stated wherever the label is used: a blocked width
is a physical fact; "wait" is a rule this certificate states.

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
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import derive_measured_go as dmg  # noqa: E402
from scl_core.records import portable_path  # noqa: E402
from scl_core import scenario_defects  # noqa: E402
from scl_core import run_rows as rr  # noqa: E402  (the one decision rule, 2026-10-12)
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
PAIR_GAP_MIN_SPEED_MPS = 0.5       # a car behind that is not moving has no arrival to predict: its frames are left out
ACCEL_WINDOW_S, ACCEL_SKIP_S = 0.5, 1.0
SPEED_LIMIT_MPS = 60.0 / 3.6       # the backend's max_speed_kmh default (backend_registry), stated here
METHOD = {
    "crossing_region": "union of the ego's oriented body polygons from zone entry to exit on the reference clean "
                       "crossing, clipped to the declared lane polygon",
    "commitment": "the ego's first frame whose body intersects the declared lane polygon (zone_contact); every "
                  "crossing reading of a run stops there (an arrival at or before it, an occupancy whose car left "
                  "the region before it, a pair-gap frame at or before it) and a run whose ego never entered is "
                  "read whole (2026-10-19; the per-run spawn cadence has stopped there since F96); every run of a "
                  "cell counts",
    "local_headway": "front-to-front time between consecutive scenario cars' first intersection with the crossing "
                     "region, both at or before the commitment; occupancy = a car's first to last intersection, "
                     "counted when the last precedes the commitment",
    "usable_window": "median local headway minus median occupancy over every run of the cell, each read up to its "
                     "ego's commitment (a run that never entered read whole); the pair gap when no local headway "
                     "could be measured (the recording ended, or the ego committed, before a second car arrived); "
                     "the spawn-interval headway when neither could (a platoon, or no car in the crossing before any "
                     "ego committed); a registered cell takes the spawn cadence, its flow never having formed; "
                     "headway_source names the one in use; a cell where no car left the crossing before an ego "
                     "committed has no occupancy, no window and the band unmeasured",
    "pair_gap": "for each car in the crossing region and the car spawned after it, wherever that car is: at every "
                "frame the first is inside and the second exists and moves (above 0.5 m/s), the distance between them "
                "over the second car's speed at that frame, averaged over those frames at or before the commitment; "
                "a prediction of the arrival "
                "headway from the present state (2026-10-19; within 0.25 s of the arrival headway on every cell of "
                "both families where both could be read)",
    "headway_at_crossing": "the variant's axis (F96): median over its seeds of the seed's local headway; the seed "
                           "values beside it (headway_by_seed_s); null when no seed measured one",
    "spawn_headway": "median consecutive spawn interval over the cars spawned at or before the ego's first entry "
                     "into the lane (the whole run when it never entered; null when fewer than two had spawned), "
                     "flow_aggregate.measure_flow_data",
    "standstill": f"the last stretch of >= {STILL_MIN_S} s below {STILL_MPS} m/s ending before the zone entry, within "
                  f"{STILL_NEAR_M} m of path of it",
    "acceleration": f"max speed change over {ACCEL_WINDOW_S} s windows after the first {ACCEL_SKIP_S} s, p50 across runs",
    "floor_from_rest": "time to cover the p10 standstill-to-exit distance from rest at the measured acceleration, "
                       "capped at the speed limit",
    "rolling_reference": "crossing path length (median of crossing duration x entry speed over clean rolling "
                         "crossings) over the median entry speed of those crossings",
    "bands": "usable: window >= floor_from_rest + margin; conditional: window >= rolling_reference + margin; "
             "else wait; a variant takes the band its measured seeds agree on and is mixed when they disagree; "
             "unmeasured when no seed measured a window, defect when every seed is registered",
    "platoon": "a seed whose spawn-interval headway is no longer than a car's occupancy of the crossing has no "
               "window between cars (nose to tail); its window is spawn headway minus occupancy, never the per-car "
               "arrival measurement, hence wait",
}


DECISION_RULES = ("per run against the cell's band, through the one rule every chain applies (scl_core.run_rows."
                  "decision, the paper's definition, 2026-10-12): usable - a clean crossing after a measured go is "
                  "right, a no-go and a go that collides or never completes are wrong; wait - no-go is right, go is "
                  "wrong whatever the outcome; conditional - a clean crossing after a rolling entry is right, a go "
                  "from rest or a go that collides is wrong, no-go is right; the balanced scope is usable + wait "
                  "(the decision-accuracy pools), the conditional band is reported beside it, never inside it; a "
                  "cell the scenario-defect register names carries the band defect: no decision, no scope (F57)")


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


def variant_axis(doc, scenario_id):
    """The variant's measured axis, its headway at the crossing (F96), or None when the certificate holds no
    such variant or no seed of it measured a headway."""
    for v in doc.get("variants", []):
        if v["scenario_id"] == scenario_id:
            return v.get("headway_at_crossing_s")
    return None


def seed_band(doc, scenario_id, seed):
    """The certificate's cell for one scenario and seed (seed as the runner's integer or 'seed_N'), or None."""
    key = seed if isinstance(seed, str) else f"seed_{seed}"
    for cell in doc.get("seeds", []):
        if cell["scenario_id"] == scenario_id and cell["seed"] == key:
            return cell
    return None


def band_for(window, floors, margin_s):
    """The band of one measured window (METHOD["bands"]), the one place the rule is written (2026-10-17, the
    decision-sensitivity sweep reads it at other margins): `floors` is the certificate's floors block
    (from_rest_s, rolling_reference_s), `window` the cell's usable window in seconds or None when none could
    be measured. usable: window >= from_rest + margin; conditional: window >= rolling_reference + margin; else
    wait; an unmeasured window is "unmeasured". The register's defect band is applied by the caller, never here."""
    if window is None:
        return "unmeasured"
    if window >= floors["from_rest_s"] + margin_s:
        return "usable"
    if window >= floors["rolling_reference_s"] + margin_s:
        return "conditional"
    return "wait"


def decision_for(band, p_go, clean, entry_state):
    """{decision, correct, scope} for one run (DECISION_RULES). decision is read from the measured go
    (None when it was refused); correct and scope are None outside the three bands. `clean` is the
    vocabulary's clean coding of the run (True, False, or None for an incomplete run). The usable and
    wait bands are the passable and blocked sides of the one rule (run_rows.decision); the conditional
    band is this certificate's own scope, under the same reading of a go."""
    decision = None if p_go is None else ("go" if p_go else "wait")
    if band not in BANDS or decision is None:
        return {"decision": decision, "correct": None, "scope": None}
    if band in ("usable", "wait"):
        correct, scope = rr.decision("passable" if band == "usable" else "blocked", decision == "go", clean)
        return {"decision": decision, "correct": correct, "scope": scope}
    correct = decision == "wait" or (entry_state == "rolling" and clean is True)
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


def pair_gaps(cars, dt, min_speed_mps=PAIR_GAP_MIN_SPEED_MPS):
    """The gap between each car in the crossing region and the car spawned after it, wherever that car is
    (2026-10-19): at every frame the first car's body is inside the region and the second car exists, the distance
    between the two over the second car's speed at that frame, the time it would take to close the distance at its
    present speed; the pair's gap is the mean over those frames. It needs no arrival of the second car, so it reads a
    gap the arrival headway cannot when the recording ends first; it is a prediction from the present state, so the
    arrival headway is used wherever both cars did arrive. Frames where the second car has not spawned, or moves at
    or below min_speed_mps (a car at rest has no arrival to predict), are left out; a pair with no frame is left out.
    `cars`: (spawn frame, id, track, indices inside the region) in spawn order."""
    out = []
    for (_, _, t1, inside), (_, _, t2, _) in zip(cars, cars[1:]):
        if not inside:
            continue
        idx2 = {f: j for j, f in enumerate(t2["frames"])}
        v2 = _speeds(t2, dt)
        samples = []
        for i in inside:
            j = idx2.get(t1["frames"][i])
            if j is None or v2[j] <= min_speed_mps:
                continue
            samples.append(math.hypot(t1["x"][i] - t2["x"][j], t1["y"][i] - t2["y"][j]) / v2[j])
        if samples:
            out.append(round(statistics.mean(samples), 3))
    return out


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
    # the commitment (METHOD["commitment"], 2026-10-19): every crossing reading stops at the ego's first body contact
    # with the lane; a run whose ego never entered is read whole
    cut = fs[entry] if entry is not None else None
    arrivals, cars = [], []   # arrivals: every car's (first, last) frame in the region; cars: (spawn frame, id,
    if region is not None:    # track, the indices inside the region at or before the cut), for the pair gap
        for aid, meta in data["actors"].items():
            if meta.get("role_name") != "scenario":
                continue
            t = data["tracks"].get(aid)
            if not t or not t.get("frames"):
                continue
            chx, chy = _car_half_extents(meta.get("blueprint", ""), extents)
            inside = [i for i in range(len(t["frames"])) if _body(t, i, chx, chy).intersects(region)]
            cars.append((meta.get("create_frame", t["frames"][0]), aid, t,
                         [i for i in inside if cut is None or t["frames"][i] <= cut]))
            if inside:
                arrivals.append((t["frames"][inside[0]], t["frames"][inside[-1]]))
    arrivals.sort()
    cars.sort(key=lambda c: (c[0], c[1]))
    before = [(a, b) for a, b in arrivals if cut is None or a <= cut]       # arrived at or before the commitment
    cleared = [(a, b) for a, b in before if cut is None or b < cut]         # ...and left the region before it
    r["local_headways_s"] = [round((before[i][0] - before[i - 1][0]) * dt, 2) for i in range(1, len(before))]
    r["occupancy_s"] = [round((b - a) * dt, 2) for a, b in cleared]
    r["pair_gaps_s"] = pair_gaps(cars, dt)
    if r["go"] and arrivals:
        # the window the ego took, from the last car that cleared before it to the first arrival after it: the ego's
        # own reading, the one field that looks past the commitment
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
    # the one place the builder reads the scorer (each run's spawn cadence before the ego's entry); the scorer
    # imports this module at load for the bands and the decisions (F82), so the back-import stays inside the function
    from flow_aggregate import measure_flow_data
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
                loaded.append({"campaign": campaign_id, "agent": agent, "seed": seed, "variant": vnum, "scenario_id": sid,
                               "data": data, "zone_poly": Polygon(corridor["corners"])})
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
        # the run's spawn cadence and free-flow speed over the cars spawned before the ego's entry (F96)
        flow = measure_flow_data(r["data"], m["entry_frame"]) or {}
        m.update(spawn_headway_s=flow.get("measured_crossing_time_gap_s"), flow_speed_mps=flow.get("measured_flow_speed"),
                 spawn_basis=flow.get("realized_basis"))
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


def certify(rows, family_id, margin_s, zone_sha256, region, inputs, speed_limit_mps=SPEED_LIMIT_MPS, defects=None):
    """`defects`: (register document, its sha256, its repo-relative path) or None. A registered cell keeps its
    measured window and takes the band `defect`, its headway being the spawn cadence (the register's evidence is
    that the flow never formed, so no crossing reading describes the cell); a variant whose remaining seeds agree
    keeps their band, one with every seed registered is `defect`."""
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
        # every run of the cell, each already read up to its ego's commitment (measure_run, 2026-10-19)
        local = [h for r in grp for h in r["local_headways_s"]]
        occ = [o for r in grp for o in r["occupancy_s"]]
        pair = [g for r in grp for g in r.get("pair_gaps_s", [])]
        entry = defect_for(defects_doc, family_id, grp[0]["scenario_id"], seed)
        source = "local"
        headway = _median(local)
        if entry is not None:
            # a registered cell: its flow never formed (the register's evidence), so no crossing reading describes
            # it; its headway is the spawn cadence, the one measurement that precedes the defect
            headway = _median([r["spawn_headway_s"] for r in grp]); source = "spawn_interval"
        elif headway is None and pair:
            headway = _median(pair); source = "pair_gap"      # the recording ended, or the ego committed, first
        elif headway is None:
            headway = _median([r["spawn_headway_s"] for r in grp]); source = "spawn_interval"
        occupancy = _median(occ)      # None when no car left the region before an ego committed: no window, unmeasured
        spawn = _median([r["spawn_headway_s"] for r in grp])
        window = round(headway - occupancy, 3) if headway is not None and occupancy is not None else None
        platoon = spawn is not None and occupancy is not None and spawn <= occupancy
        if platoon:
            # cars nose to tail: no window exists between them whatever the per-car arrivals say
            window = round(spawn - occupancy, 3); source = "platoon"
        band = band_for(window, {"from_rest_s": floor_rest, "rolling_reference_s": rolling_ref}, margin_s)
        if entry is not None:
            band = DEFECT
            applied.append({"variant": variant, "seed": seed, "scenario_id": grp[0]["scenario_id"], "reason": entry["reason"]})
        table.append({"variant": variant, "seed": seed, "scenario_id": grp[0]["scenario_id"],
                      "flow_speed_mps": _median([r["flow_speed_mps"] for r in grp]),
                      "spawn_headway_s": _median([r["spawn_headway_s"] for r in grp]),
                      "pair_gap_s": _median(pair),
                      "local_headway_s": headway, "headway_source": source,
                      "local_headway_spread_s": [min(local), max(local)] if local else None,
                      "occupancy_s": occupancy, "usable_window_s": window, "band": band,
                      "n_runs": len(grp), "n_headways": len(local), "n_occupancies": len(occ), "n_pair_gaps": len(pair)})
    variants = {}
    for t in table:
        variants.setdefault(t["variant"], []).append(t)
    per_variant = []
    for variant, ts in sorted(variants.items()):
        # the band its measured seeds agree on; a registered or unmeasured seed has no say (the variant is defect
        # only when every seed is registered, unmeasured only when no seed measured a window)
        measured = sorted({t["band"] for t in ts if t["band"] in BANDS})
        none = "unmeasured" if any(t["band"] == "unmeasured" for t in ts) else DEFECT
        per_variant.append({"variant": variant, "scenario_id": ts[0]["scenario_id"],
                            "band": (measured[0] if len(measured) == 1 else "mixed") if measured else none,
                            "seed_bands": {t["seed"]: t["band"] for t in ts},
                            "usable_window_s": {t["seed"]: t["usable_window_s"] for t in ts},
                            # the variant's axis (F96): the median over its seeds of the headway at the crossing
                            "headway_at_crossing_s": _median([t["local_headway_s"] for t in ts]),
                            "headway_by_seed_s": {t["seed"]: t["local_headway_s"] for t in ts}})
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
