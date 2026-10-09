"""Sticky ego-body entry into a declared conflict zone.

Flow mode measures against the zone document passed as --zone (a scoring
instrument under artifacts/examples/scoring_instruments/, pinned by the hash
of the file passed); nothing here names a map, junction or lane. Scenarios
outside the document's scope refuse measurement. Static families retain their
placed-obstacle corridor. Entry is observed at recorded frames, not interpolated.

A GO IS AN ENTRY WHILE THE FLOW IS ACTIVE (2026-09-20, the post-timeout
crossings): the flow window is the first to the last recorded frame of any
scenario-role actor, and an entry after that window is recorded
(entry_after_flow) but not counted. The expert and BridgeDrive waited out the
flow at the tightest gaps of v04 and v05, crossed one or two seconds after the
scenario timeout removed the cars, and were counted as commitments against
traffic they never faced (20 of 594 runs). Entry itself is still read from the
hero track alone, so the audit's proof that the zone geometry never depends on
the oncoming recording stands: the window is WHEN the flow existed, not WHERE.
"""

from __future__ import annotations

GO_RULE = ("a go is an entry into the declared zone while the flow is active: the flow window is the first to "
           "the last recorded frame of any scenario-role actor; an entry after the last one is recorded "
           "(entry_after_flow) and not counted; entry itself is read from the hero track alone")


def flow_window(data: dict):
    """(first, last) recorded frame over the scenario-role actors' tracks; None when none is recorded."""
    frames = []
    for aid, actor in (data.get("actors") or {}).items():
        if (actor or {}).get("role_name") == "scenario":
            track = (data.get("tracks") or {}).get(str(aid))
            if track and track.get("frames"):
                frames += [track["frames"][0], track["frames"][-1]]
    return (min(frames), max(frames)) if frames else None


import hashlib
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import route_metrics  # noqa: E402
import score_runs  # noqa: E402
import trajectory_extract  # noqa: E402

from shapely.geometry import Polygon

# The zone document contract this reader implements (the generator's
# ZONE_VERSION); a document declaring anything else is refused, never read.
FLOW_ZONE_VERSION = "flow_conflict_zone/v3.map_lane"


class DeclaredZone:
    """A conflict-zone document plus the identity of the bytes it was read from.

    Every measurement records `sha256`, so a run is bound to exactly the
    geometry file that was passed - never to a path a module remembered."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.doc = json.loads(self.path.read_text())
        if self.doc.get("zone_version") != FLOW_ZONE_VERSION:
            raise ValueError(f"{self.path.name} declares zone_version "
                             f"{self.doc.get('zone_version')!r}; this reader implements {FLOW_ZONE_VERSION}")
        polygon = Polygon(self.doc["polygon"])
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError(f"{self.path.name}: invalid declared flow zone polygon")
        self.aabb = polygon.bounds
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()

    @property
    def source(self) -> str:
        d = self.doc
        return f"OpenDRIVE road {d['road_id']} lane {d['lane_id']:+d}, {d['map']} junction {d['junction_id']}"

    @property
    def source_key(self) -> str:
        d = self.doc
        return f"OpenDRIVE/{d['map']}/junction{d['junction_id']}/road{d['road_id']}/lane{d['lane_id']}"


def load_zone(path: Path) -> DeclaredZone:
    return DeclaredZone(path)


def check_map_excerpt(zone: DeclaredZone, map_excerpt: Path) -> str:
    """The zone names its source excerpt by hash; refuse any other file."""
    digest = hashlib.sha256(Path(map_excerpt).read_bytes()).hexdigest()
    if digest != zone.doc["source_sha256"]:
        raise ValueError(f"{Path(map_excerpt).name} is not the source excerpt {zone.path.name} declares "
                         f"(source_sha256 {zone.doc['source_sha256'][:12]}..., got {digest[:12]}...)")
    return digest


def flow_corridor(zone: DeclaredZone, scenario_id: str | None):
    """Return the declared polygon only for the document's declared scenario scope."""
    if scenario_id not in zone.doc["scope"]:
        return None
    return {"corners": zone.doc["polygon"], "aabb": zone.aabb,
            "approximated": False, "version": zone.doc["zone_version"],
            "source": zone.source,
            "geometry_sha256": zone.sha256,
            "boundary_error_bound_m": zone.doc["boundary_error_bound_m"]}


def static_corridor(scenario_id: str) -> tuple[float, float, float, float] | None:
    """Zone for a static threading family: the bounding box over the
    placed-obstacle row (score_runs.placed_objects, each x/y +- its
    half_extent). None when the scenario has no placed objects (caller
    then reports go refused)."""
    reg_extents = score_runs._registry_extents()
    placed = score_runs.placed_objects(scenario_id, reg_extents, [])
    if not placed:
        return None
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for obj in placed:
        minx = min(minx, obj.x - obj.half_extent)
        miny = min(miny, obj.y - obj.half_extent)
        maxx = max(maxx, obj.x + obj.half_extent)
        maxy = max(maxy, obj.y + obj.half_extent)
    return (minx, miny, maxx, maxy)


def _aabb_corners(aabb: tuple) -> list:
    """Four ordered corners of an axis-aligned box (minx, miny, maxx, maxy)."""
    minx, miny, maxx, maxy = aabb
    return [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]


def derive_measured_go(data: dict, zone_corners: list, ego_hx: float, ego_hy: float,
                       flow_window: tuple | None = None) -> dict:
    """Observe sticky body/polygon contact and first frame, or None without ego.

    `entry` is the contact, read from the hero track alone. `go` is the entry
    counted as a commitment: the entry itself without a flow window (static
    mode), or the entry inside the window (flow mode, GO_RULE). Nearest miss is
    the minimum recorded body-to-zone distance. Sample tolerance is the largest
    recorded translation step, not a bound on unrecorded motion.
    """
    no_go = {"go": None, "entry": None, "entry_after_flow": None,
             "flow_window_frames": list(flow_window) if flow_window else None,
             "first_entry_frame": None, "first_entry_t_s": None,
             "nearest_miss_m": None, "n_frames_tested": 0,
             "sample_tolerance_m": None}
    hero_track = data["tracks"].get(str(data["hero_id"]))
    if not hero_track:
        return dict(no_go)

    dt_s = data.get("dt_s", 0.05)
    frames, xs, ys, yaws = (hero_track["frames"], hero_track["x"],
                            hero_track["y"], hero_track["yaw"])
    n_frames = len(frames)
    if n_frames == 0:
        return dict(no_go)

    # Translation-only sampling diagnostic; rotation and unrecorded motion
    # prevent treating it as proof of continuous-time non-entry.
    max_speed = max((math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]) / dt_s
                     for i in range(1, n_frames)), default=0.0)
    sample_tolerance_m = round(max_speed * dt_s, 3) if max_speed > 0 else None

    zone_polygon = Polygon(zone_corners)
    go = False
    first_entry_frame = None
    nearest_miss_m = math.inf
    for i in range(n_frames):
        ego_corners = route_metrics.rect_corners(xs[i], ys[i], yaws[i],
                                                 ego_hx, ego_hy)
        ego_polygon = Polygon(ego_corners)
        if not go and ego_polygon.intersects(zone_polygon):
            go = True
            first_entry_frame = frames[i]   # sticky: never unset below
        nearest_miss_m = min(nearest_miss_m,
                             ego_polygon.distance(zone_polygon))
    entry_after_flow = None
    counted = go
    if flow_window is not None and go:
        entry_after_flow = first_entry_frame > flow_window[1]
        counted = flow_window[0] <= first_entry_frame <= flow_window[1]
    return {
        "go": counted,
        "entry": go,
        "entry_after_flow": entry_after_flow,
        "flow_window_frames": list(flow_window) if flow_window else None,
        "first_entry_frame": first_entry_frame,
        "first_entry_t_s": round(first_entry_frame * dt_s, 2) if first_entry_frame is not None else None,
        "nearest_miss_m": round(nearest_miss_m, 3),
        "n_frames_tested": n_frames,
        "sample_tolerance_m": sample_tolerance_m
    }


def _refused(mode: str, scenario_id: str, note: str) -> dict:
    """Helper to create a refused result dict with consistent structure."""
    return {
        "mode": mode,
        "scenario_id": scenario_id,
        "entry": None,
        "entry_after_flow": None,
        "flow_window_frames": None,
        "go": None,
        "first_entry_frame": None,
        "first_entry_t_s": None,
        "nearest_miss_m": None,
        "n_frames_tested": 0,
        "sample_tolerance_m": None,
        "corridor_aabb": None,
        "corridor_approximated": False,
        "zone": None,
        "note": note
    }


def measure_run(extract_path: Path, mode: str, scenario_id: str | None = None,
                zone: DeclaredZone | None = None,
                ego_hx: float = 2.446, ego_hy: float = 0.918) -> dict | None:
    """Measure recorded entry; missing geometry is refused, never inferred.

    Flow mode measures against `zone`, the declared document the caller
    loaded (load_zone); there is no default zone."""
    if mode == "flow" and zone is None:
        raise ValueError("flow mode requires a declared conflict-zone document (--zone)")
    try:
        data = trajectory_extract.read_extract(extract_path)
    except Exception:
        return None

    approximated = False
    zone_meta = None
    if mode == "flow":
        if data.get("map", "").split("/")[-1] != zone.doc["map"]:
            return _refused(mode, scenario_id, "commitment REFUSED: recording map is unavailable or outside the declared zone scope")
        corridor = flow_corridor(zone, scenario_id)
        if corridor is None:
            return _refused(mode, scenario_id,
                            "no declared map conflict zone for scenario - commitment REFUSED")
        corners = corridor["corners"]
        aabb = corridor["aabb"]
        approximated = corridor["approximated"]
        zone_meta = {k: v for k, v in corridor.items() if k not in ("corners", "aabb", "approximated")}
        window = flow_window(data)
        if window is None:
            return _refused(mode, scenario_id, "no scenario-role actor recorded: the flow window cannot be "
                                               "measured - commitment REFUSED")
    elif mode == "static":
        if scenario_id is None:
            raise ValueError("scenario_id required for static mode")
        aabb = static_corridor(scenario_id)
        if aabb is None:
            return _refused(mode, scenario_id, "no corridor derivable (no placed objects) - commitment REFUSED, not guessed")
        corners = _aabb_corners(aabb)
    else:
        raise ValueError("mode must be 'flow' or 'static'")

    r = derive_measured_go(data, corners, ego_hx, ego_hy, flow_window=window if mode == "flow" else None)
    r["mode"] = mode
    if mode == "flow":
        r["go_rule"] = GO_RULE
    r["scenario_id"] = scenario_id
    r["corridor_aabb"] = list(aabb) if aabb is not None else None
    r["corridor_approximated"] = approximated
    r["zone"] = zone_meta
    return r


def main() -> int:
    """CLI interface for derive_measured_go."""
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("extracts", nargs="+", type=Path,
                    help="Extract files to process")
    ap.add_argument("--mode", choices=["flow", "static"], required=True,
                    help="Processing mode: flow or static")
    ap.add_argument("--scenario",
                    help="Scenario ID (required to select a declared zone)")
    ap.add_argument("--zone", type=Path,
                    help="declared conflict-zone document (artifacts/examples/scoring_instruments/); "
                         "required for --mode flow")
    ap.add_argument("--out", type=Path, default=Path("measured_go_out"),
                    help="Output directory")

    args = ap.parse_args()
    if args.mode == "flow" and args.zone is None:
        ap.error("--mode flow requires --zone <declared conflict-zone document>")
    zone = load_zone(args.zone) if args.zone else None

    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / "measured_go_rows.jsonl"

    n_written = 0
    with open(out_file, "w") as f:
        for extract_path in args.extracts:
            result = measure_run(extract_path, args.mode, args.scenario, zone=zone)
            if result is not None:
                f.write(json.dumps(result) + "\n")
                n_written += 1
                go_str = "None" if result["go"] is None else str(result["go"])
                print(f"{args.mode} {args.scenario or 'N/A'} {go_str} "
                      f"{result.get('first_entry_frame', 'N/A')} "
                      f"{result.get('nearest_miss_m', 'N/A')}")
            else:
                print(f"Failed to process {extract_path}")

    print(f"Processed {n_written} extracts, wrote to {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
