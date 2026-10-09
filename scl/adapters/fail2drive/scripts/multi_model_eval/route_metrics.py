"""Route metrics from trajectory extracts (Demo B item 6b, stage 2).

Consumes the trajectory extracts written by save_trajectory_extract /
backfill_trajectories.py and emits one metrics row per RUN, keyed
(family, model, seed, scenario_id, source) - joinable with the scorer's
scored_rows on equality. Offline - no CARLA; scl_prototype env:

    conda run -n scl_prototype python \
        adapters/fail2drive/scripts/multi_model_eval/route_metrics.py \
        <run_dir> [<run_dir>...] --out <dir>

METRICS POLICY (judgment constants and rules; everything else is
mechanical fact from extracts, manifests, sidecars, and the registry).

- Dimensions are MEASURED OR REFUSED, never invented:
  ego oriented box = the registry's spawn-probed vehicle.lincoln.mkz_2020
  half-extents; placed obstacles = countersign sidecar bbox, else
  registry footprint - a placed blueprint resolving to neither fails the
  run's obstacle metrics with a stated contract; traffic actors with no
  registry row are EXCLUDED from traffic clearance/TTC and listed
  per-row in traffic_excluded.
- Obstacle clearance uses the obstacle's TRACKED per-frame pose from its
  trigger-lift onward (a bulldozed barrier is measured where it IS, not
  where it was placed). min_clearance_placed_m is also reported against
  the placed pose for the approach phase.
- mirror_band flag: 0 < min clearance < ego_mirror_allowance_m, read
  from the family manifest's provenance.policy_constants (0.13 on every
  current family; until 2026-10-07 from the capability profile the manifest
  names, a canon read no interpretation pinned) - the near-miss band the
  driving score cannot see.
- TTC: clearance / closing-rate (3-frame smoothed finite difference),
  minimum over frames with clearance > 0 and closing > 0.1 m/s.
- Approach zone: [first obstacle station - 60 m, last obstacle station
  + 10 m] along the route axis (variant XML waypoints polyline).
- wobble: extrema-to-extrema swings of the ego's signed lateral offset
  inside the approach zone, counted when a swing exceeds
  WOBBLE_AMP_M = 0.05 - DERIVED, not chosen: 5x the expert's maximum
  lateral-noise swing on straight approaches (0.0098 m over 1170 swings,
  fm_004 seed 0, measured 2026-08-04); anything above is deliberate
  lateral motion. NOTE: a deliberate avoidance maneuver also counts -
  wobble is interpreted relative to the expert baseline on the same
  family, never as an absolute. steer_reversals counts steering-sign
  flips with swing > 0.05 from the control channel.
- obstacle displacement is EGO-ATTRIBUTED per obstacle: moved_by_ego is
  true when the hero has a recorder collision event with that actor
  (direct evidence; also catches chain shoves - ego rams barrier A into
  barrier B), OR the ego was within reach (ego half-length + obstacle
  max half-extent + 1.0 m) at the displacement onset frame (first
  post-lift frame with > 0.05 m motion from the lift pose). Background
  traffic also shoves our props (fm_005 bins), and the two causes must
  not be conflated.
- creep: intervals at 0.05-0.5 m/s lasting >= 2 s.
- stop_offset_m: final station minus first obstacle station, reported
  only when the final speed < 0.05 m/s (negative = stopped short).
- off_corridor: max |lateral offset| in-zone; flagged above 4.0 m -
  HEURISTIC v1 pending curb-line integration from the geometry layer.
- tipped: an obstacle whose final roll or pitch deviates > 45 deg from
  upright (folded to [0,180]); overturned reports the deviation.
- A row is one RUN - a sample (same-seed outcomes bifurcate; see
  campaign notes). Aggregations report n.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import score_runs  # noqa: E402  (manifest index, sidecar/registry paths)
import trajectory_extract  # noqa: E402
from adapters.fail2drive.xml_tools import parse_route_xml  # noqa: E402

EGO_BLUEPRINT = "vehicle.lincoln.mkz_2020"
APPROACH_WINDOW_M = 60.0
ZONE_TAIL_M = 10.0
# 5x the expert's max lateral-noise swing (0.0098 m, n=1170, fm_004 seed 0)
WOBBLE_AMP_M = 0.05
STEER_REV_MIN = 0.05
CREEP_LO_MPS, CREEP_HI_MPS, CREEP_MIN_S = 0.05, 0.5, 2.0
STOP_SPEED_MPS = 0.05
OFF_CORRIDOR_M = 4.0         # heuristic v1 - pending curb-line integration
TTC_MIN_CLOSING_MPS = 0.1
TIP_DEG = 45.0


# --- oriented-box geometry -------------------------------------------------

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


# --- measured extents ------------------------------------------------------

def registry_full_extents() -> dict[str, tuple[float, float]]:
    doc = score_runs._canon_json(score_runs.REGISTRY_PATH)   # pinned in the stamp (principle 13)
    out = {}
    for row in doc.get("rows", []):
        b, fp = row.get("binding") or {}, row.get("footprint") or {}
        half = fp.get("half_extents")
        if b.get("kind") == "blueprint" and b.get("ref") and half:
            out[b["ref"]] = (half[0], half[1])
    return out


def sidecar_extents(scenario_id: str) -> dict[tuple[str, int], tuple[float, float]]:
    """(blueprint, ordinal) -> (hx, hy) from the countersign sidecar."""
    p = score_runs.SIDECAR_DIR / f"{scenario_id}.json"
    if not p.exists():
        return {}
    out, counts = {}, defaultdict(int)
    for bs in json.loads(p.read_text()).get("backend_scenarios", []):
        for obj in bs.get("objects", []):
            ext = obj.get("bbox_extent")
            if ext:
                out[(obj["blueprint"], counts[obj["blueprint"]])] = (ext[0], ext[1])
                counts[obj["blueprint"]] += 1
    return out


def policy_constant(manifest: dict, cid: str) -> float:
    """A policy constant the family was solved under, read from the family manifest's provenance
    (provenance.policy_constants, the document every bundle pins). Until 2026-10-07 the chain
    opened the capability profile the manifest names for the same value, a canon read no
    interpretation pinned. A manifest without the constant refuses: a metric is never computed
    against a default standing in for a solved constant."""
    constants = (manifest.get("provenance") or {}).get("policy_constants") or {}
    if cid not in constants:
        raise ValueError(f"{manifest.get('manifest_id')}: provenance.policy_constants carries no {cid}; "
                         f"known: {sorted(constants)}")
    return float(constants[cid])


def mirror_allowance_for(manifest: dict) -> float:
    return policy_constant(manifest, "ego_mirror_allowance_m")


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
    """Alternating local-extrema swing magnitudes of a series."""
    ext = []
    for i in range(1, len(series) - 1):
        if (series[i] - series[i - 1]) * (series[i + 1] - series[i]) < 0:
            ext.append(series[i])
    return [abs(b - a) for a, b in zip(ext, ext[1:])]


def compute_row(extract_path, scenario_id, family, var, model, seed,
                seed_source, reg_extents, mirror_allowance, warnings):
    data = trajectory_extract.read_extract(extract_path)
    dt = data["dt_s"]
    hero = data["tracks"][str(data["hero_id"])]
    hf, hx_, hy_ = hero["frames"], hero["x"], hero["y"]
    hyaw = hero["yaw"]
    n = len(hf)
    if n < 5:
        warnings.append(f"{extract_path}: hero track too short - skipped")
        return None

    ego_hx, ego_hy = reg_extents[EGO_BLUEPRINT]
    speed = _smooth3([math.hypot(hx_[min(i + 1, n - 1)] - hx_[max(i - 1, 0)],
                                 hy_[min(i + 1, n - 1)] - hy_[max(i - 1, 0)])
                      / (dt * (min(i + 1, n - 1) - max(i - 1, 0) or 1))
                      for i in range(n)])

    axis = RouteAxis(parse_route_xml(
        score_runs.XML_DIR / f"{scenario_id}.xml").waypoints)
    proj = [axis.project(hx_[i], hy_[i]) for i in range(n)]
    stations = [p[0] for p in proj]
    lats = [p[1] for p in proj]

    # placed obstacles: scenario-role actors, extents measured or refused
    side_ext = sidecar_extents(scenario_id)
    side_counts = defaultdict(int)
    obstacles = []
    for aid, a in sorted(data["actors"].items(), key=lambda kv: int(kv[0])):
        if a.get("role_name") != "scenario":
            continue
        bp = a["blueprint"]
        ext = side_ext.get((bp, side_counts[bp])) or reg_extents.get(bp)
        side_counts[bp] += 1
        if ext is None:
            warnings.append(
                f"{scenario_id}: placed object '{bp}' has no measured extent "
                "(sidecar bbox or registry footprint) - obstacle metrics "
                "REFUSED for this run")
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
                # hero-collision events with THIS actor are direct evidence,
                # and they catch chain shoves (ego rams barrier A into
                # barrier B: B's onset happens outside ego reach)
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

    # TTC from the min-clearance series
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

    # traffic: measured extents or excluded
    min_traffic, min_traffic_zone, excluded = math.inf, math.inf, []
    for aid, t in data["tracks"].items():
        a = data["actors"].get(aid, {})
        bp = a.get("blueprint", "")
        if int(aid) == data["hero_id"] or a.get("role_name") == "scenario" \
                or not bp.startswith("vehicle."):
            continue
        ext = reg_extents.get(bp)
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
                rect_corners(t["x"][j], t["y"][j], t.get("yaw", [0] * len(t["frames"]))[j],
                             ext[0], ext[1]))
            min_traffic = min(min_traffic, d)
            if zone_lo <= stations[i] <= zone_hi:
                min_traffic_zone = min(min_traffic_zone, d)

    # speed-vs-distance profile (jsonl only): 1 m grid over the last 100 m
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
        "mirror_band": bool(min_clear is not math.inf
                            and 0 < min_clear < mirror_allowance),
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

def write_stamp(out_dir: Path, rows: list, run_dirs) -> None:
    """metrics_provenance.json (metrics_provenance/v0.1, 2026-09-22, ledger F26): this tool by hash,
    a digest over every extract the rows came from (`<source> <sha256>` lines, sorted), every
    repository file read for the join (family manifests, the registry, sidecars, route XMLs) by
    path and sha256, the row count. Consumers refuse a stamp whose tool hash is not the tool
    beside them, the scored-table rule."""
    import hashlib
    by_name = {Path(r).name: Path(r) for r in run_dirs}
    lines = []
    for r in rows:
        top, _, rel = r["source"].partition("/")
        p = by_name.get(top)
        digest = hashlib.sha256((p / rel).read_bytes()).hexdigest() if p is not None and (p / rel).is_file() else "absent"
        lines.append(f"{r['source']} {digest}")
    doc = {"provenance_kind": "metrics_provenance/v0.1",
           "tool_file": Path(__file__).name,
           "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "extract_tool_sha256": hashlib.sha256(Path(trajectory_extract.__file__).read_bytes()).hexdigest(),
           "input_files_sha256": hashlib.sha256("\n".join(sorted(set(lines))).encode()).hexdigest(),
           "canon_inputs_sha256": dict(sorted(score_runs.CANON_READS.items())),
           "n_rows": len(rows),
           "note": ("content identity only, no timestamps; the rows join the scored rows on (family, model, seed, "
                    "scenario_id); consumers refuse on tool_sha256 mismatch - re-derive from raw with the current tool")}
    (out_dir / "metrics_provenance.json").write_text(json.dumps(doc, indent=1) + "\n")


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
    "min_clearance_m", "min_clearance_obstacle", "mirror_band",
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
    ap.add_argument("--out", type=Path, default=Path("metrics_out"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    manifest_index = score_runs.load_manifest_index()
    rid_to_sid = {}
    manifests = {}
    for sid, (family, var) in manifest_index.items():
        rid_to_sid[str(var.get("route_id"))] = sid
    for mf in sorted(score_runs.MANIFEST_DIR.glob("*.json")):
        manifests[mf.stem] = json.loads(mf.read_text())

    reg_extents = registry_full_extents()
    if EGO_BLUEPRINT not in reg_extents:
        print(f"registry has no footprint for {EGO_BLUEPRINT} - cannot "
              "build the ego box; rebuild the registry", file=sys.stderr)
        return 1
    mirror_cache = {}
    rows, warnings = [], []
    for run_dir in args.run_dirs:
        for model, seed, seed_source, p in discover_extracts(run_dir):
            m = re.match(r"RouteScenario_(\d+)_rep\d+", p.stem)
            sid = rid_to_sid.get(m.group(1)) if m else None
            if sid is None:
                warnings.append(f"{p}: no manifest variant for this route id")
                continue
            family, var = manifest_index[sid]
            if family not in mirror_cache:
                mirror_cache[family] = mirror_allowance_for(manifests[family])
            row = compute_row(p, sid, family, var, model, seed, seed_source,
                              reg_extents, mirror_cache[family], warnings)
            if row is not None:
                # run-dir-relative: keeps committed goldens machine-independent
                row["source"] = f"{run_dir.name}/{p.relative_to(run_dir)}"
                rows.append(row)

    if not rows:
        print("no scoreable extracts found", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    write_stamp(args.out, rows, args.run_dirs)
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
            lines.append(
                f"  {mmodel:>15}: n={nn}"
                f" contact_runs={sum(1 for r in mrows if r['contact_duration_s'] > 0)}"
                f" mirror_band={sum(1 for r in mrows if r['mirror_band'])}"
                f" min_clear_min={min(clear):.3f}" if clear else
                f"  {mmodel:>15}: n={nn} (no clearance data)")
            if clear:
                lines[-1] += (
                    f" wobble>0={sum(1 for r in mrows if r['wobble_reversals'])}"
                    f" creep_runs={sum(1 for r in mrows if r['creep_segments'])}"
                    f" moved_by_ego={sum(1 for r in mrows if r['obstacles_moved_by_ego'])}"
                    f" moved_by_other={sum(1 for r in mrows if r['obstacles_moved_by_other'])}"
                    + (f" hesit_median={med:.1f}x" if med is not None else ""))
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
