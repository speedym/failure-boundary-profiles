"""Route metrics from trajectory extracts (public port, MIT).

Consumes trajectory extracts (analysis.extract_recorders output) and emits
one metrics row per RUN, keyed (family, model, seed, scenario_id, source) -
joinable with analysis.score_runs scored_rows on equality. Offline, stdlib.

    python -m analysis.trajectory_metrics <run_dir> [<run_dir>...] --out metrics/

METRICS POLICY (judgment constants and rules; everything else is
mechanical fact from extracts and the public catalogs).

- Dimensions are MEASURED OR REFUSED, never invented:
  ego oriented box = the spawn-probed pair in vehicle_extents.json;
  placed obstacles = half_extents in attribution.json (countersign
  sidecar bbox or spawn-probed registry footprint) - a placed blueprint
  with no measured pair fails the run's obstacle metrics with a stated
  contract; traffic actors absent from vehicle_extents.json are
  EXCLUDED from traffic clearance and listed per-row in
  traffic_excluded.
- Obstacle clearance uses the obstacle's TRACKED per-frame pose from its
  trigger-lift onward (a bulldozed barrier is measured where it IS, not
  where it was placed). min_clearance_placed_m is also reported against
  the placed pose for the approach phase.
- TTC: clearance / closing-rate (3-frame smoothed finite difference),
  minimum over frames with clearance > 0 and closing > 0.1 m/s.
- Approach zone: [first obstacle station - 60 m, last obstacle station
  + 10 m] along the route axis (variant XML waypoints polyline).
- wobble: extrema-to-extrema swings of the ego's signed lateral offset
  inside the approach zone, counted when a swing exceeds
  WOBBLE_AMP_M = 0.05 - DERIVED, not chosen: 5x the expert's maximum
  lateral-noise swing on straight approaches (0.0098 m over 1170 swings,
  measured on the fm_004 seed-0 campaign). NOTE: a deliberate avoidance
  maneuver also counts - wobble is interpreted relative to the expert
  baseline on the same family, never as an absolute. steer_reversals
  counts steering-sign flips with swing > 0.05 from the control channel.
- obstacle displacement is EGO-ATTRIBUTED per obstacle: moved_by_ego is
  true when the hero has a recorder collision event with that actor
  (direct evidence; also catches chain shoves), OR the ego was within
  reach (ego half-length + obstacle max half-extent + 1.0 m) at the
  displacement onset frame (first post-lift frame with > 0.05 m motion).
  Background traffic also shoves props; the causes must not be conflated.
- creep: intervals at 0.05-0.5 m/s lasting >= 2 s.
- stop_offset_m: final station minus first obstacle station, reported
  only when the final speed < 0.05 m/s (negative = stopped short).
- off_corridor: max |lateral offset| in-zone; flagged above 4.0 m -
  HEURISTIC pending curb-line integration.
- tipped: an obstacle whose final roll or pitch deviates > 45 deg from
  upright (folded to [0,180]).
- DIVERGENCE from the internal pipeline (documented, deliberate): the
  mirror_band flag is NOT computed here - it reads the family's
  capability profile (ego_mirror_allowance_m), which is not part of the
  public release. Every other field matches the internal pipeline.
- A row is one RUN - a sample (same-seed outcomes bifurcate).
  Aggregations report n.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from . import trajectory
from .score_runs import DEFAULT_SPLITS, load_catalogs

APPROACH_WINDOW_M = 60.0
ZONE_TAIL_M = 10.0
WOBBLE_AMP_M = 0.05          # 5x expert max lateral-noise swing (see policy)
STEER_REV_MIN = 0.05
CREEP_LO_MPS, CREEP_HI_MPS, CREEP_MIN_S = 0.05, 0.5, 2.0
STOP_SPEED_MPS = 0.05
OFF_CORRIDOR_M = 4.0
TTC_MIN_CLOSING_MPS = 0.1
TIP_DEG = 45.0


# --- oriented-box geometry (identical to the internal implementation) ------

def rect_corners(cx, cy, yaw_deg, hx, hy):
    y = math.radians(yaw_deg)
    c, s = math.cos(y), math.sin(y)
    return [(cx + c * dx * hx - s * dy * hy, cy + s * dx * hx + c * dy * hy)
            for dx, dy in ((1, 1), (1, -1), (-1, -1), (-1, 1))]


def _project(corners, ax, ay):
    dots = [x * ax + y * ay for x, y in corners]
    return min(dots), max(dots)


def _sat_overlap(a, b):
    for poly in (a, b):
        for i in range(4):
            ex, ey = (poly[(i + 1) % 4][0] - poly[i][0],
                      poly[(i + 1) % 4][1] - poly[i][1])
            ax, ay = -ey, ex
            a0, a1 = _project(a, ax, ay)
            b0, b1 = _project(b, ax, ay)
            if a1 < b0 or b1 < a0:
                return False
    return True


def _seg_dist(p, q, r, s):
    def pt_seg(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
        gx, gy = ax + t * dx - px, ay + t * dy - py
        return math.hypot(gx, gy)
    return min(pt_seg(*p, *r, *s), pt_seg(*q, *r, *s),
               pt_seg(*r, *p, *q), pt_seg(*s, *p, *q))


def obb_distance(a, b):
    """Planar distance between two oriented boxes (corner lists); 0 = overlap."""
    if _sat_overlap(a, b):
        return 0.0
    best = math.inf
    for i in range(4):
        for j in range(4):
            best = min(best, _seg_dist(a[i], a[(i + 1) % 4],
                                       b[j], b[(j + 1) % 4]))
    return best


class RouteAxis:
    """Arc-length + signed lateral offset against the XML waypoint polyline."""

    def __init__(self, waypoints):
        self.pts = [(w["x"], w["y"]) for w in waypoints]
        self.cum = [0.0]
        for i in range(1, len(self.pts)):
            self.cum.append(self.cum[-1] + math.dist(self.pts[i - 1], self.pts[i]))

    def project(self, x, y):
        best = (math.inf, 0.0, 0.0)  # (dist2, station, signed_lat)
        for i in range(len(self.pts) - 1):
            ax, ay = self.pts[i]
            bx, by = self.pts[i + 1]
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
            px, py = ax + t * dx, ay + t * dy
            d2 = (x - px) ** 2 + (y - py) ** 2
            if d2 < best[0]:
                seg = math.sqrt(l2) or 1.0
                cross = (dx * (y - ay) - dy * (x - ax)) / seg
                best = (d2, self.cum[i] + t * seg, cross)
        return best[1], best[2]


def route_waypoints(xml_path: Path):
    root = ET.parse(xml_path).getroot()
    wps = []
    for pos in root.iter("position"):
        wps.append({"x": float(pos.get("x")), "y": float(pos.get("y"))})
    return wps


# --- public measured extents -----------------------------------------------

def load_vehicle_extents(splits_dir: Path):
    p = splits_dir / "vehicle_extents.json"
    doc = json.loads(p.read_text())
    return doc["ego_blueprint"], {bp: tuple(v)
                                  for bp, v in doc["vehicles"].items()}


def attribution_pairs(splits_dir: Path):
    """scenario_id -> [(blueprint, ordinal) -> (hx, hy)] via ordered lists."""
    out = {}
    for fam_dir in sorted(p for p in splits_dir.iterdir() if p.is_dir()):
        attr = fam_dir / "attribution.json"
        if not attr.exists():
            continue
        doc = json.loads(attr.read_text())
        for sid, objs in (doc.get("scenarios") or {}).items():
            pairs, counts = {}, defaultdict(int)
            for o in objs:
                he = o.get("half_extents")
                pairs[(o["blueprint"], counts[o["blueprint"]])] = (
                    tuple(he) if he else None)
                counts[o["blueprint"]] += 1
            out[sid] = pairs
    return out


# --- per-run computation ---------------------------------------------------

def _smooth3(xs):
    if len(xs) < 3:
        return list(xs)
    out = [xs[0]]
    for i in range(1, len(xs) - 1):
        out.append((xs[i - 1] + xs[i] + xs[i + 1]) / 3.0)
    out.append(xs[-1])
    return out


def _extrema_swings(series):
    ext = []
    for i in range(1, len(series) - 1):
        if (series[i] - series[i - 1]) * (series[i + 1] - series[i]) < 0:
            ext.append(series[i])
    return [abs(b - a) for a, b in zip(ext, ext[1:])]


def compute_row(extract_path, scenario_id, family, var, model, seed,
                seed_source, splits_dir, ego_pair, vehicle_extents,
                obstacle_pairs, warnings):
    data = trajectory.read_extract(extract_path)
    dt = data["dt_s"]
    hero = data["tracks"][str(data["hero_id"])]
    hf, hx_, hy_ = hero["frames"], hero["x"], hero["y"]
    hyaw = hero["yaw"]
    n = len(hf)
    if n < 5:
        warnings.append(f"{extract_path}: hero track too short - skipped")
        return None

    ego_hx, ego_hy = ego_pair
    speed = _smooth3([math.hypot(hx_[min(i + 1, n - 1)] - hx_[max(i - 1, 0)],
                                 hy_[min(i + 1, n - 1)] - hy_[max(i - 1, 0)])
                      / (dt * (min(i + 1, n - 1) - max(i - 1, 0) or 1))
                      for i in range(n)])

    xml_path = (splits_dir / family / "routes" / f"{scenario_id}.xml")
    axis = RouteAxis(route_waypoints(xml_path))
    proj = [axis.project(hx_[i], hy_[i]) for i in range(n)]
    stations = [p[0] for p in proj]
    lats = [p[1] for p in proj]

    side_ext = obstacle_pairs.get(scenario_id, {})
    side_counts = defaultdict(int)
    obstacles = []
    for aid, a in sorted(data["actors"].items(), key=lambda kv: int(kv[0])):
        if a.get("role_name") != "scenario":
            continue
        bp = a["blueprint"]
        ext = side_ext.get((bp, side_counts[bp])) or vehicle_extents.get(bp)
        side_counts[bp] += 1
        if ext is None:
            warnings.append(
                f"{scenario_id}: placed object '{bp}' has no measured "
                "half-extent pair (attribution.json / vehicle_extents.json)"
                " - obstacle metrics REFUSED for this run")
            return None
        t = data["tracks"].get(aid)
        if not t:
            continue
        lift = a.get("trigger_lift_frame")
        obstacles.append({"id": aid, "blueprint": bp, "ext": ext,
                          "track": t, "lift": lift,
                          "placed_xy": (t["x"][0], t["y"][0])})
    if not obstacles:
        warnings.append(f"{scenario_id}: no scenario actors in extract - skipped")
        return None

    ob_stations = [axis.project(*o["placed_xy"])[0] for o in obstacles]
    zone_lo = min(ob_stations) - APPROACH_WINDOW_M
    zone_hi = max(ob_stations) + ZONE_TAIL_M
    first_ob_station = min(ob_stations)

    frame_index = {f: i for i, f in enumerate(hf)}
    per_obstacle = []
    min_clear, min_clear_bp = math.inf, None
    min_clear_placed = math.inf
    contact_frames = 0
    first_contact_t = None
    clearance_series = [math.inf] * n
    for o in obstacles:
        t = o["track"]
        idx = {f: j for j, f in enumerate(t["frames"])}
        start = o["lift"] if o["lift"] is not None else t["frames"][0]
        o_min, o_contact = math.inf, 0
        placed_c = rect_corners(*o["placed_xy"],
                                t["yaw"][idx.get(start, 0)],
                                o["ext"][0], o["ext"][1])
        for f, j in idx.items():
            if f < start or f not in frame_index:
                continue
            i = frame_index[f]
            ego_c = rect_corners(hx_[i], hy_[i], hyaw[i], ego_hx, ego_hy)
            d = obb_distance(ego_c, rect_corners(
                t["x"][j], t["y"][j], t["yaw"][j], o["ext"][0], o["ext"][1]))
            clearance_series[i] = min(clearance_series[i], d)
            dp = obb_distance(ego_c, placed_c)
            min_clear_placed = min(min_clear_placed, dp)
            if d < o_min:
                o_min = d
            if d == 0.0:
                o_contact += 1
                if first_contact_t is None:
                    first_contact_t = round(f * dt, 2)
        if o_min < min_clear:
            min_clear, min_clear_bp = o_min, o["blueprint"]
        contact_frames += o_contact
        post = sorted(j for f, j in idx.items() if f >= start)
        disp = 0.0
        tipped_dev = 0.0
        onset_t, moved_by_ego = None, None
        if post:
            j0, j1 = post[0], post[-1]
            disp = math.hypot(t["x"][j1] - t["x"][j0], t["y"][j1] - t["y"][j0])
            for angle in (t["roll"][j1], t["pitch"][j1]):
                dev = abs(angle) % 360.0
                tipped_dev = max(tipped_dev, min(dev, 360.0 - dev))
            if disp > 0.05:
                reach = ego_hx + max(o["ext"]) + 1.0
                hit_by_hero = any(c["other_id"] == int(o["id"])
                                  for c in data["collisions"])
                for j in post:
                    if math.hypot(t["x"][j] - t["x"][j0],
                                  t["y"][j] - t["y"][j0]) > 0.05:
                        f = t["frames"][j]
                        onset_t = round(f * dt, 2)
                        i = frame_index.get(f)
                        moved_by_ego = bool(hit_by_hero or (
                            i is not None and math.hypot(
                                t["x"][j] - hx_[i], t["y"][j] - hy_[i]) <= reach))
                        break
        per_obstacle.append({
            "blueprint": o["blueprint"], "actor_id": o["id"],
            "min_clearance_m": None if o_min is math.inf else round(o_min, 3),
            "contact_s": round(o_contact * dt, 2),
            "displacement_m": round(disp, 2),
            "displacement_onset_s": onset_t,
            "moved_by_ego": moved_by_ego,
            "upright_dev_deg": round(tipped_dev, 1),
            "tipped": tipped_dev > TIP_DEG,
        })

    cs = [c if c is not math.inf else None for c in clearance_series]
    min_ttc = math.inf
    for i in range(1, n - 1):
        if cs[i - 1] is None or cs[i + 1] is None or not cs[i]:
            continue
        closing = (cs[i - 1] - cs[i + 1]) / (2 * dt)
        if closing > TTC_MIN_CLOSING_MPS and cs[i] > 0:
            min_ttc = min(min_ttc, cs[i] / closing)

    in_zone = [i for i in range(n) if zone_lo <= stations[i] <= zone_hi]
    zone_lats = [lats[i] for i in in_zone]
    wobble_swings = [s for s in _extrema_swings(_smooth3(zone_lats))
                     if s > WOBBLE_AMP_M]
    steer = data.get("ego_controls", {}).get("steer", [])
    steer_swings = [s for s in _extrema_swings(steer) if s > STEER_REV_MIN]

    creep_time, creep_segments, run = 0.0, 0, 0
    for v in speed:
        if CREEP_LO_MPS <= v <= CREEP_HI_MPS:
            run += 1
        else:
            if run * dt >= CREEP_MIN_S:
                creep_segments += 1
                creep_time += run * dt
            run = 0
    if run * dt >= CREEP_MIN_S:
        creep_segments += 1
        creep_time += run * dt

    final_speed = speed[-1]
    stop_offset = (round(stations[-1] - first_ob_station, 2)
                   if final_speed < STOP_SPEED_MPS else None)
    off_corridor = max((abs(v) for v in zone_lats), default=0.0)

    min_traffic, min_traffic_zone, excluded = math.inf, math.inf, []
    for aid, t in data["tracks"].items():
        a = data["actors"].get(aid, {})
        bp = a.get("blueprint", "")
        if int(aid) == data["hero_id"] or a.get("role_name") == "scenario" \
                or not bp.startswith("vehicle."):
            continue
        ext = vehicle_extents.get(bp)
        if ext is None:
            excluded.append(bp)
            continue
        idx = {f: j for j, f in enumerate(t["frames"])}
        for f, j in idx.items():
            i = frame_index.get(f)
            if i is None:
                continue
            cd = math.hypot(t["x"][j] - hx_[i], t["y"][j] - hy_[i])
            if cd - 6.0 > min(min_traffic, min_traffic_zone):
                continue  # coarse prune
            d = obb_distance(
                rect_corners(hx_[i], hy_[i], hyaw[i], ego_hx, ego_hy),
                rect_corners(t["x"][j], t["y"][j],
                             t.get("yaw", [0] * len(t["frames"]))[j],
                             ext[0], ext[1]))
            min_traffic = min(min_traffic, d)
            if zone_lo <= stations[i] <= zone_hi:
                min_traffic_zone = min(min_traffic_zone, d)

    profile = []
    for target in range(100, -1, -1):
        want = first_ob_station - target
        best_i = min(range(n), key=lambda i: abs(stations[i] - want))
        if abs(stations[best_i] - want) <= 1.0:
            profile.append([target, round(speed[best_i], 2)])

    def _clean(v):
        return None if v is math.inf else round(v, 3)

    return {
        "family": family, "model": model, "seed": seed,
        "seed_source": seed_source, "scenario_id": scenario_id,
        "variant_index": var.get("index"), "target": var.get("target"),
        "source": str(extract_path),
        "n_frames": data["n_frames"], "duration_s": data["duration_s"],
        "min_clearance_m": _clean(min_clear),
        "min_clearance_obstacle": min_clear_bp,
        "min_clearance_placed_m": _clean(min_clear_placed),
        "contact_duration_s": round(contact_frames * dt, 2),
        "first_contact_time_s": first_contact_t,
        "recorder_collision_events": len(data["collisions"]),
        "min_ttc_s": _clean(min_ttc),
        "wobble_reversals": len(wobble_swings),
        "wobble_max_swing_m": round(max(wobble_swings), 3) if wobble_swings else 0.0,
        "steer_reversals": len(steer_swings),
        "time_in_zone_s": round(len(in_zone) * dt, 2),
        "creep_time_s": round(creep_time, 2),
        "creep_segments": creep_segments,
        "stop_offset_m": stop_offset,
        "final_speed_mps": round(final_speed, 2),
        "off_corridor_max_m": round(off_corridor, 2),
        "off_corridor_flag": off_corridor > OFF_CORRIDOR_M,
        "obstacle_moved": any(o["displacement_m"] > 0.05 for o in per_obstacle),
        "obstacles_moved_by_ego": sum(
            1 for o in per_obstacle if o["moved_by_ego"] is True),
        "obstacles_moved_by_other": sum(
            1 for o in per_obstacle
            if o["displacement_m"] > 0.05 and o["moved_by_ego"] is not True),
        "max_obstacle_displacement_m": max(
            (o["displacement_m"] for o in per_obstacle), default=0.0),
        "obstacles_tipped": sum(1 for o in per_obstacle if o["tipped"]),
        "min_traffic_clearance_m": _clean(min_traffic),
        "min_traffic_clearance_in_zone_m": _clean(min_traffic_zone),
        "traffic_excluded": sorted(set(excluded)),
        "per_obstacle": per_obstacle,
        "speed_profile": profile,
    }


# --- discovery + output ----------------------------------------------------

def discover_extracts(run_dir: Path):
    for p in sorted(run_dir.rglob("trajectories/*.json.gz")):
        rel = p.relative_to(run_dir).parts
        model = rel[0] if len(rel) > 1 else "unknown"
        seed, seed_source = 0, "legacy-default"
        for part in rel:
            m = re.fullmatch(r"seed_(\d+)", part)
            if m:
                seed, seed_source = int(m.group(1)), "path"
        yield model, seed, seed_source, p


CSV_FIELDS = [
    "family", "model", "seed", "scenario_id", "variant_index", "target",
    "min_clearance_m", "min_clearance_obstacle",
    "contact_duration_s", "first_contact_time_s", "min_ttc_s",
    "wobble_reversals", "wobble_max_swing_m", "steer_reversals",
    "time_in_zone_s", "creep_time_s", "creep_segments", "stop_offset_m",
    "final_speed_mps", "off_corridor_max_m", "off_corridor_flag",
    "obstacle_moved", "obstacles_moved_by_ego", "obstacles_moved_by_other",
    "max_obstacle_displacement_m", "obstacles_tipped",
    "min_traffic_clearance_m", "min_traffic_clearance_in_zone_m",
    "recorder_collision_events", "source",
]


def policy_header() -> str:
    doc = __doc__
    return doc[doc.index("METRICS POLICY"):].rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    ap.add_argument("--out", type=Path, default=Path("metrics_out"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    variants, _ = load_catalogs(args.splits)
    rid_to_sid = {}
    for sid, (family, var) in variants.items():
        if var.get("route_id"):
            rid_to_sid[str(var["route_id"])] = sid
    ego_bp, vehicle_extents = load_vehicle_extents(args.splits)
    if ego_bp not in vehicle_extents:
        print(f"vehicle_extents.json has no pair for {ego_bp}", file=sys.stderr)
        return 1
    ego_pair = vehicle_extents[ego_bp]
    obstacle_pairs = attribution_pairs(args.splits)

    rows, warnings = [], []
    for run_dir in args.run_dirs:
        for model, seed, seed_source, p in discover_extracts(run_dir):
            m = re.match(r"RouteScenario_(\d+)_rep\d+", p.stem)
            sid = rid_to_sid.get(m.group(1)) if m else None
            if sid is None:
                warnings.append(f"{p}: no catalog variant for this route id")
                continue
            family, var = variants[sid]
            row = compute_row(p, sid, family, var, model, seed, seed_source,
                              args.splits, ego_pair, vehicle_extents,
                              obstacle_pairs, warnings)
            if row is not None:
                row["source"] = f"{run_dir.name}/{p.relative_to(run_dir)}"
                rows.append(row)

    if not rows:
        print("no scoreable extracts found", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "metrics_rows.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    with (args.out / "metrics_rows.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (";".join(v) if isinstance(v, list) else v)
                        for k, v in r.items() if k in CSV_FIELDS})

    lines = [policy_header(), ""]
    by_family = defaultdict(list)
    for r in rows:
        by_family[r["family"]].append(r)
    for family in sorted(by_family):
        frows = by_family[family]
        models = sorted({r["model"] for r in frows})
        lines.append(f"== {family} " + "=" * max(0, 66 - len(family)))
        lines.append("")
        expert_zone = {(r["scenario_id"], r["seed"]): r["time_in_zone_s"]
                       for r in frows if r["model"] == "pdm_lite_expert"}
        for mmodel in models:
            mrows = [r for r in frows if r["model"] == mmodel]
            nn = len(mrows)
            clear = [r["min_clearance_m"] for r in mrows
                     if r["min_clearance_m"] is not None]
            ratios = [r["time_in_zone_s"] / expert_zone[(r["scenario_id"], r["seed"])]
                      for r in mrows
                      if expert_zone.get((r["scenario_id"], r["seed"]))]
            med = (sorted(ratios)[len(ratios) // 2] if ratios else None)
            if clear:
                lines.append(
                    f"  {mmodel:>15}: n={nn}"
                    f" contact_runs={sum(1 for r in mrows if r['contact_duration_s'] > 0)}"
                    f" min_clear_min={min(clear):.3f}"
                    f" wobble>0={sum(1 for r in mrows if r['wobble_reversals'])}"
                    f" creep_runs={sum(1 for r in mrows if r['creep_segments'])}"
                    f" moved_by_ego={sum(1 for r in mrows if r['obstacles_moved_by_ego'])}"
                    f" moved_by_other={sum(1 for r in mrows if r['obstacles_moved_by_other'])}"
                    + (f" hesit_median={med:.1f}x" if med is not None else ""))
            else:
                lines.append(f"  {mmodel:>15}: n={nn} (no clearance data)")
        lines.append("")
    if warnings:
        lines += ["WARNINGS:"] + [f"  {w}" for w in warnings]
    text = "\n".join(lines) + "\n"
    (args.out / "metrics_tables.txt").write_text(text)
    if not args.quiet:
        print(text)
    print(f"{len(rows)} rows -> {args.out}/metrics_rows.{{csv,jsonl}}, "
          f"metrics_tables.txt", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --- flow headways (experimental; the dynamic-family measuring tool) -------

def crossing_headways(extract: dict, px: float, py: float,
                      nx: float, ny: float, blueprint_prefix: str = "vehicle."):
    """Per-actor crossing times of the oriented line through (px, py) with
    normal (nx, ny), and the sorted time gaps between consecutive crossings.

    The realized-gap instrument for dynamic-actor (traffic flow) families:
    variations are authored as spawn parameters, but the certified axis is
    what the simulator actually produced - measure it here, per run, from
    the recording. Crossing = the actor's signed distance to the line
    changes sign between consecutive frames; the crossing time is linearly
    interpolated. The hero is excluded; pass blueprint_prefix="" to include
    walkers. EXPERIMENTAL until the first flow family lands.
    """
    dt = extract["dt_s"]
    hero_id = extract["hero_id"]
    crossings = []
    for aid, t in extract["tracks"].items():
        if int(aid) == hero_id:
            continue
        bp = extract["actors"].get(aid, {}).get("blueprint", "")
        if blueprint_prefix and not bp.startswith(blueprint_prefix):
            continue
        prev = None
        for j, f in enumerate(t["frames"]):
            s = (t["x"][j] - px) * nx + (t["y"][j] - py) * ny
            if prev is not None and (prev[1] < 0) != (s < 0):
                f0, s0 = prev
                frac = abs(s0) / (abs(s0) + abs(s)) if (abs(s0) + abs(s)) else 0.0
                crossings.append({"actor_id": aid, "blueprint": bp,
                                  "time_s": round((f0 + frac * (f - f0)) * dt, 3)})
                break  # first crossing per actor
            prev = (f, s)
    crossings.sort(key=lambda c: c["time_s"])
    gaps = [round(b["time_s"] - a["time_s"], 3)
            for a, b in zip(crossings, crossings[1:])]
    return {"crossings": crossings, "gaps_s": gaps}
