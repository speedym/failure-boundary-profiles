#!/usr/bin/env python3
"""Cross-check a declared conflict lane with CARLA's offline Map API (fail2drive env).

Reads the zone document, the excerpt and the campaign declarations passed in
(nothing named here): the connector lane must exist once in the declared
junction with its centre line matching the zone's sampled boundaries; every
declared cell must hold exactly the declared variants' recordings; every
recorded ego spawn must reach the declared junction; every flow-actor spawn
must lie on an incoming lane the map declares for the connector.

The SUMMARY (--out, kind flow_map_check/v0.4, entries
campaigns[<campaign>][<family>]) holds counts, tolerances and hashes; the
EVIDENCE (<evidence-dir>/<campaign>/<family>/audit/map_check_recordings.json,
kind flow_map_check_recordings/v0.1, the per-recording hash inventory) is
pinned in the summary by hash and carried in the derived bundle. Stdlib
plus the carla wheel; carla is imported inside verify() so the helpers
stay importable elsewhere.
"""
import argparse
import gzip
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from importlib.metadata import version

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
sys.path.insert(0, str(ROOT))
from scl_core.campaign_declaration import check_family_manifest, load_declarations, resolve_family_manifest  # noqa: E402
from scl_core.records import portable_path, sha256_file as sha  # noqa: E402

# The zone document contract this check reads (build_conflict_lane_zone.ZONE_VERSION).
ZONE_VERSION = "flow_conflict_zone/v3.map_lane"
VERIFICATION_KIND = "flow_map_check/v0.4"
ROWS_KIND = "flow_map_check_recordings/v0.1"
ROWS_FILE = "map_check_recordings.json"
# a forward walk that enters no junction within this distance is refused, not looped
APPROACH_LIMIT_M = 200


def declared_sources(excerpt_root, junction_id, road_id, lane_id):
    """Incoming (road, lane) pairs the map's junction connections declare as
    feeding the connector lane."""
    junction = excerpt_root.find(f'junction[@id="{junction_id}"]')
    if junction is None:
        raise ValueError(f"excerpt has no junction {junction_id}")
    sources = {(int(connection.get("incomingRoad")), int(link.get("from")))
               for connection in junction if connection.get("connectingRoad") == str(road_id)
               for link in connection.findall(f'laneLink[@to="{lane_id}"]')}
    if not sources:
        raise ValueError(f"junction {junction_id} declares no incoming lane into road {road_id} lane {lane_id:+d}")
    return sorted(sources)


def stations(road, step_max):
    """The reference-line stations the zone builder sampled, in order."""
    out = []
    for geometry in road.findall("planView/geometry"):
        length = float(geometry.get("length"))
        count = math.ceil(length / step_max)
        out.extend(float(geometry.get("s")) + length * i / count for i in range(count + 1))
    return out


def expected_recordings(declaration, manifest):
    """{recording file name: scenario id} for every declared variant, named by
    the manifest's route id."""
    by_scenario = {v["scenario_id"]: v for v in manifest["variants"]}
    return {f"RouteScenario_{by_scenario[s]['route_id']}_rep0.json.gz": s for s in declaration.variants}


def declared_cells(subtree, declaration, expected):
    """Yield (model, seed, trajectories dir) per declared cell; refuse
    undeclared model or seed directories and any cell whose recordings are
    not exactly the declared variants'."""
    if not subtree.is_dir():
        raise ValueError(f"{declaration.campaign_id}: results subtree not found: {subtree}")
    on_disk = {p.name for p in subtree.iterdir() if p.is_dir() and (p / f"seed_{declaration.repetitions[0]}").is_dir()}
    undeclared = sorted(on_disk - set(declaration.models))
    if undeclared:
        raise ValueError(f"{declaration.campaign_id}: undeclared model directories on disk: {undeclared}")
    for model in declaration.models:
        model_dir = subtree / model
        if not model_dir.is_dir():
            raise ValueError(f"{declaration.campaign_id}: declared model {model!r} has no directory")
        seeds_on_disk = {p.name for p in model_dir.iterdir() if p.is_dir() and p.name.startswith("seed_")}
        extra_seeds = sorted(seeds_on_disk - {f"seed_{s}" for s in declaration.repetitions})
        if extra_seeds:
            raise ValueError(f"{declaration.campaign_id}: undeclared seed directories under {model}: {extra_seeds}")
        for seed in declaration.repetitions:
            traj = model_dir / f"seed_{seed}" / "trajectories"
            if not traj.is_dir():
                raise ValueError(f"{declaration.campaign_id}: declared cell {model}/seed_{seed} has no recordings")
            names = {p.name for p in traj.glob("*.json.gz")}
            missing, extra = sorted(set(expected) - names), sorted(names - set(expected))
            if missing or extra:
                raise ValueError(f"{declaration.campaign_id}: cell {model}/seed_{seed} recordings differ from the "
                                 f"declared variants - missing {missing}, undeclared {extra}")
            yield model, seed, traj


def verify(map_path, results, zone_path, map_excerpt, declarations, evidence_dir):
    import carla

    zone = json.loads(zone_path.read_text())
    if zone.get("zone_version") != ZONE_VERSION:
        raise ValueError(f"{zone_path.name} declares {zone.get('zone_version')!r}; this check reads {ZONE_VERSION}")
    if sha(map_excerpt) != zone["source_sha256"]:
        raise ValueError(f"{map_excerpt.name} is not the source excerpt {zone_path.name} declares")
    if sha(map_path) != zone["map_source_sha256"]:
        raise ValueError("OpenDRIVE map identity mismatch")
    junction_id, road_id, lane_id = zone["junction_id"], zone["road_id"], zone["lane_id"]
    excerpt_root = ET.parse(map_excerpt).getroot()
    road = excerpt_root.find(f'road[@id="{road_id}"]')
    if road is None:
        raise ValueError(f"excerpt has no road {road_id}")
    sources = declared_sources(excerpt_root, junction_id, road_id, lane_id)
    length = float(road.get("length"))

    m = carla.Map(zone["map"], map_path.read_text())
    connector = m.get_waypoint_xodr(road_id, lane_id, length / 2)
    if connector is None or not connector.is_junction or connector.junction_id != junction_id:
        raise ValueError(f"CARLA does not place road {road_id} lane {lane_id:+d} inside junction {junction_id}")
    pairs = [(a, b) for a, b in connector.get_junction().get_waypoints(carla.LaneType.Driving)
             if a.road_id == road_id and a.lane_id == lane_id]
    if len(pairs) != 1:
        raise ValueError(f"road {road_id} lane {lane_id:+d} is not a unique connector of junction {junction_id}")
    n = len(zone["polygon"]) // 2
    inner, outer = zone["polygon"][:n], zone["polygon"][n:][::-1]
    maximum = 0
    for a, b, station in zip(inner, outer, stations(road, zone["sampling_step_max_m"])):
        x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        s = min(length - .0001, max(.0001, station))
        nearest = m.get_waypoint_xodr(road_id, lane_id, s)
        if nearest is None:
            raise ValueError(f"CARLA cannot resolve connector station {s}")
        loc = nearest.transform.location
        maximum = max(maximum, math.hypot(x - loc.x, y - loc.y))
    if maximum > 0.01:
        raise ValueError(f"map lane center mismatch: {maximum} m")

    def first_junction_ahead(pos):
        wp = m.get_waypoint(carla.Location(x=pos[0], y=pos[1]))
        for _ in range(APPROACH_LIMIT_M):
            if wp.is_junction:
                return wp.junction_id
            ahead = wp.next(1)
            if not ahead:
                return None
            wp = ahead[0]
        return None

    campaigns = {}
    totals = {"recordings_checked": 0, "ego_spawns_checked": 0, "scenario_spawns_checked": 0}
    for declaration in declarations:
        subtree = results / declaration.results_subtree
        manifest_path, _ = resolve_family_manifest(declaration, subtree, ROOT)   # the family as run (principle 13)
        manifest = check_family_manifest(declaration, manifest_path)
        expected = expected_recordings(declaration, manifest)
        actors, egos, runs = 0, 0, 0
        inputs = {}
        for model, seed, traj in declared_cells(subtree, declaration, expected):
            for name in sorted(expected):
                path = traj / name
                inputs[str(path.relative_to(results))] = sha(path)
                data = json.load(gzip.open(path, "rt"))
                runs += 1
                if data.get("map") != zone["map"]:
                    raise ValueError(f"recording map mismatch: {path}")
                hero = data["actors"].get(str(data["hero_id"])) or {}
                pos = hero.get("create_pos_m")
                if not pos:
                    raise ValueError(f"missing ego spawn: {path}")
                if first_junction_ahead(pos) != junction_id:
                    raise ValueError(f"recorded ego spawn does not approach junction {junction_id}: {path}")
                egos += 1
                for aid, actor in data["actors"].items():
                    if actor.get("role_name") != "scenario":
                        continue
                    pos = actor.get("create_pos_m")
                    if not pos:
                        raise ValueError(f"missing scenario spawn: {path} {aid}")
                    source = m.get_waypoint(carla.Location(x=pos[0], y=pos[1]))
                    if (source.road_id, source.lane_id) not in sources:
                        raise ValueError(f"flow actor source outside the declared incoming lanes {sources}: {path} {aid}")
                    actors += 1
        rows = {"rows_kind": ROWS_KIND, "campaign_id": declaration.campaign_id,
                "family_id": declaration.family_id,
                "producer_sha256": sha(Path(__file__)), "extract_files_sha256": inputs}
        rows_path = evidence_dir / declaration.results_subtree / "audit" / ROWS_FILE
        rows_path.parent.mkdir(parents=True, exist_ok=True)
        rows_path.write_text(json.dumps(rows, indent=1) + "\n")
        entry = {
            "campaign_declaration": declaration.record(ROOT),
            "family_manifest_sha256": declaration.manifest_sha256,
            "cells": len(declaration.models) * len(declaration.repetitions),
            "recordings_checked": runs, "ego_spawns_checked": egos, "scenario_spawns_checked": actors,
            "recordings": {"path": f"{declaration.results_subtree}/audit/{ROWS_FILE}", "sha256": sha(rows_path)}}
        campaigns.setdefault(declaration.campaign_id, {})[declaration.family_id] = entry
        for k in totals:
            totals[k] += entry[k]
    return {"verification_kind": VERIFICATION_KIND, "producer_sha256": sha(Path(__file__)),
            "map_sha256": sha(map_path), "zone_path": portable_path(zone_path, ROOT), "zone_sha256": sha(zone_path),
            "map_excerpt_sha256": sha(map_excerpt),
            "carla_version": version("carla"),
            "junction_id": junction_id, "connector_road_lane": [road_id, lane_id],
            "source_road_lanes": [list(pair) for pair in sources],
            **totals,
            "max_lane_center_error_m": maximum,
            "evidence": "the per-recording hash inventory is evidence, not a finding: it lives in each "
                        "family's audit/map_check_recordings.json, pinned here by hash and carried in the "
                        "derived bundle",
            "campaigns": campaigns}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--map", required=True, type=Path, help="the full original OpenDRIVE map the zone pins by hash")
    ap.add_argument("--zone", required=True, type=Path,
                    help="declared conflict-zone document (artifacts/examples/scoring_instruments/...)")
    ap.add_argument("--map-excerpt", required=True, type=Path,
                    help="the pinned OpenDRIVE excerpt that zone declares as its source")
    ap.add_argument("--campaign", action="append", required=True, type=Path, metavar="DECLARATION",
                    help="campaign declaration (artifacts/examples/campaigns/...); repeatable, required")
    ap.add_argument("--results", type=Path, default=ROOT / "adapters/fail2drive/scripts/multi_model_eval/results",
                    help="location of the raw campaign trees; each declaration names its subtree")
    ap.add_argument("--evidence-dir", type=Path, required=True,
                    help="where each family's audit/map_check_recordings.json goes (the scoring driver's --out)")
    ap.add_argument("--out", type=Path, required=True, help="the summary document")
    args = ap.parse_args()
    try:
        declarations = load_declarations(args.campaign)
    except ValueError as exc:
        ap.error(str(exc))
    report = verify(args.map, args.results, args.zone, args.map_excerpt, declarations, args.evidence_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
