#!/usr/bin/env python3
"""Build a conflict-lane polygon from a pinned OpenDRIVE excerpt.

Generic: for a declared junction, connecting road and lane id, evaluate the
road's line/arc reference geometry and the lane's constant width, reflect
OpenDRIVE y into the CARLA world frame, sample both boundaries at
<= SAMPLING_STEP_M. WHICH junction, road and lane is the family's
declaration: it arrives on the command line and is written into the zone
document, never into this module.

    build_conflict_lane_zone.py --map-excerpt <xodr> --junction J --road R \\
        --lane L --scope <family manifest>... --definition "<meaning>" \\
        --out <zone.json> [--source-map <full map.xodr>] [--turn-relation Straight]

    build_conflict_lane_zone.py --check --out <zone.json> --map-excerpt <xodr> \\
        --scope <family manifest>...

--check regenerates the document from its own declared identity and refuses
unless every content field reproduces byte for byte; generator_sha256 names
the script that wrote the committed bytes, and an inert refactor does not
re-issue the document (principle 5). The extractor refuses what it does not
model: varying width, lane offset, banking, spiral/poly geometry, a boundary
reaching an arc's centre, a chord error above the declared bound.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
sys.path.insert(0, str(ROOT))
from scl_core.records import render, sha256_file as sha  # noqa: E402

ZONE_VERSION = "flow_conflict_zone/v3.map_lane"
SAMPLING_STEP_M = 0.25
# Declared chord-error bound of the sampled boundaries against the analytic
# curves; VERIFIED for every build, never assumed for a new lane.
BOUNDARY_ERROR_BOUND_M = 0.00004
LINEAGE_FIELDS = ("generator_sha256",)


def extract_source(source_map, excerpt, road, junction):
    """Retain exactly the road and junction elements a zone needs, plus the
    original map's name and content hash."""
    full = ET.parse(source_map).getroot()
    subset = ET.Element("OpenDRIVE", source_name=source_map.name, source_sha256=sha(source_map))
    for query in (f'road[@id="{road}"]', f'junction[@id="{junction}"]'):
        element = full.find(query)
        if element is None:
            raise ValueError(f"{source_map.name} has no {query}")
        subset.append(element)
    ET.indent(subset)
    excerpt.write_bytes(ET.tostring(subset, encoding="utf-8", xml_declaration=True) + b"\n")


def _constant(node, keys):
    return all(float(node.get(k, 0)) == 0 for k in keys)


def lane_offsets(section, lane_id):
    """Lateral offsets (OpenDRIVE t, positive left) of the lane's inner and
    outer boundaries, plus its width; every lane between the reference line
    and the target must carry one constant width."""
    if lane_id == 0:
        raise ValueError("lane 0 is the reference line; declare a driving lane (+n left, -n right)")
    side, sign = ("left", 1) if lane_id > 0 else ("right", -1)
    lanes = {int(node.get("id")): node for node in section.findall(f"{side}/lane")}
    target = lanes.get(lane_id)
    if target is None or target.get("type") != "driving":
        raise ValueError(f"lane {lane_id:+d} is not a driving lane of the section's {side} side: "
                         f"declared {sorted(lanes)}")
    widths = {}
    for k in range(1, abs(lane_id) + 1):
        node = lanes.get(sign * k)
        if node is None:
            raise ValueError(f"lane {sign * k:+d} is missing between the reference line and lane {lane_id:+d}")
        records = node.findall("width")
        values = {float(n.get("a")) for n in records}
        if not records or len(values) != 1 or any(not _constant(n, "bcd") for n in records):
            raise ValueError(f"lane {sign * k:+d} width is not a single constant; outside this extractor's scope")
        widths[sign * k] = values.pop()
    inner = sum(widths[sign * k] for k in range(1, abs(lane_id)))
    return sign * inner, sign * (inner + widths[lane_id]), widths[lane_id]


def build(excerpt, junction, road, lane, scope_manifests, definition, turn_relation=None):
    root = ET.parse(excerpt).getroot()
    source_name, map_source_sha = root.get("source_name"), root.get("source_sha256")
    if not source_name or not map_source_sha:
        raise ValueError("map excerpt must carry source_name and source_sha256 of the original map")
    road_el = root.find(f'road[@id="{road}"]')
    if road_el is None or road_el.get("junction") != str(junction):
        raise ValueError(f"excerpt has no road {road} inside junction {junction}")
    if turn_relation and road_el.find(f'.//vectorSignal[@turnRelation="{turn_relation}"]') is None:
        raise ValueError(f"road {road} does not carry the map's turnRelation={turn_relation!r} designation")
    junction_el = root.find(f'junction[@id="{junction}"]')
    if junction_el is None or not any(
            c.get("connectingRoad") == str(road) and c.find(f'laneLink[@to="{lane}"]') is not None
            for c in junction_el):
        raise ValueError(f"junction {junction} declares no connection into road {road} lane {lane:+d}")
    sections = road_el.findall("lanes/laneSection")
    if len(sections) != 1:
        raise ValueError("expected one lane section; multi-section connectors are outside this extractor's scope")
    for node in road_el.findall("lanes/laneOffset") + road_el.findall("lateralProfile/superelevation"):
        if not _constant(node, "abcd"):
            raise ValueError("nonzero lane offset/banking is outside this extractor's scope")
    t_inner, t_outer, width = lane_offsets(sections[0], lane)
    inner, outer = [], []
    chord_error = 0.0
    for geom in road_el.findall("planView/geometry"):
        x, y, h, length = (float(geom.get(k)) for k in ("x", "y", "hdg", "length"))
        arc = geom.find("arc")
        if arc is None and geom.find("line") is None:
            raise ValueError("only line/arc reference geometry is supported")
        k = float(arc.get("curvature")) if arc is not None else 0
        n = math.ceil(length / SAMPLING_STEP_M)
        if k:
            # an offset boundary is an arc of curvature k / (1 - k t); the nearer one bounds the chord error
            for t in (t_inner, t_outer):
                if 1 - k * t <= 0:
                    raise ValueError(f"lane boundary at t={t} reaches the arc centre of a curvature {k} segment")
                chord_error = max(chord_error, abs(k / (1 - k * t)) * (length / n) ** 2 / 8)
        for i in range(n + 1):
            ds = length * i / n
            a = h + k * ds
            rx = x + ((math.sin(a) - math.sin(h)) / k if k else ds * math.cos(h))
            ry = y + ((math.cos(h) - math.cos(a)) / k if k else ds * math.sin(h))
            # OpenDRIVE is right-handed; CARLA world reflects its y coordinate.
            inner.append([round(rx - t_inner * math.sin(a), 8), round(-ry - t_inner * math.cos(a), 8)])
            outer.append([round(rx - t_outer * math.sin(a), 8), round(-ry - t_outer * math.cos(a), 8)])
    if chord_error > BOUNDARY_ERROR_BOUND_M:
        raise ValueError(f"sampled boundary chord error {chord_error:.2e} m exceeds the declared bound "
                         f"{BOUNDARY_ERROR_BOUND_M} m; a finer SAMPLING_STEP_M is a new zone version")
    scenarios, manifests = [], {}
    for path in scope_manifests:
        manifest = json.loads(Path(path).read_text())
        family = manifest.get("manifest_id")
        if not family:
            raise ValueError(f"{path} is not a family manifest (no manifest_id)")
        manifests[family] = sha(path)
        scenarios.extend(v["scenario_id"] for v in manifest["variants"] if v["verdict"] == "accepted")
    return {"zone_version": ZONE_VERSION, "map": Path(source_name).stem,
            "frame": "CARLA world planar", "units": "m", "junction_id": junction,
            "road_id": road, "lane_id": lane, "lane_width_m": width,
            "definition": definition,
            "scope": sorted(scenarios), "source_sha256": sha(excerpt),
            "map_source_sha256": map_source_sha,
            "generator_sha256": sha(Path(__file__)),
            "manifest_sha256": dict(sorted(manifests.items())),
            "sampling_step_max_m": SAMPLING_STEP_M,
            "boundary_error_bound_m": BOUNDARY_ERROR_BOUND_M,
            "polygon": inner + outer[::-1]}


def check(zone_path, excerpt, scope_manifests, turn_relation=None):
    """Regenerate a zone document from its own declared identity; refuse
    unless the content reproduces byte for byte (lineage fields aside)."""
    zone_path = Path(zone_path)
    doc = json.loads(zone_path.read_text())
    if doc.get("zone_version") != ZONE_VERSION:
        raise ValueError(f"{zone_path.name} declares {doc.get('zone_version')!r}; this generator writes {ZONE_VERSION}")
    expected = build(Path(excerpt), doc["junction_id"], doc["road_id"], doc["lane_id"],
                     scope_manifests, doc["definition"], turn_relation)
    lineage = {field: doc.get(field) for field in LINEAGE_FIELDS}
    if render({**expected, **lineage}) != zone_path.read_text():
        stale = sorted(k for k in set(expected) | set(doc)
                       if k not in LINEAGE_FIELDS and expected.get(k) != doc.get(k))
        raise ValueError(f"{zone_path.name} is stale: {stale or ['formatting']} do not reproduce from "
                         f"{Path(excerpt).name}; regenerate under a new zone version")
    return {"zone": zone_path.name, "content_reproduced": True,
            "generator_sha256": doc["generator_sha256"],
            "current_generator_sha256": expected["generator_sha256"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 epilog="Identity arguments are required to generate; --check reads them from --out.")
    ap.add_argument("--map-excerpt", required=True, type=Path,
                    help="pinned OpenDRIVE excerpt (one road + its junction) the zone is built from")
    ap.add_argument("--out", required=True, type=Path, help="zone document to write, or to verify with --check")
    ap.add_argument("--scope", required=True, nargs="+", type=Path, metavar="MANIFEST",
                    help="family manifests whose accepted variants the zone applies to")
    ap.add_argument("--junction", type=int, help="OpenDRIVE junction id")
    ap.add_argument("--road", type=int, help="connecting road id inside the junction")
    ap.add_argument("--lane", type=int, help="driving lane id on that road (+n left, -n right)")
    ap.add_argument("--definition", help="the declared meaning of the zone, recorded verbatim")
    ap.add_argument("--turn-relation", help="refuse unless the connector carries this turnRelation (e.g. Straight)")
    ap.add_argument("--source-map", type=Path,
                    help="full OpenDRIVE map: re-extract --map-excerpt from it first (a deliberate source refresh)")
    ap.add_argument("--check", action="store_true", help="verify --out reproduces from its own declared identity")
    args = ap.parse_args()
    if args.check:
        given = [name for name in ("junction", "road", "lane", "definition", "source_map")
                 if getattr(args, name) is not None]
        if given:
            ap.error("--check reads the identity from --out; drop " + ", ".join(f"--{n.replace('_', '-')}" for n in given))
        print(json.dumps(check(args.out, args.map_excerpt, args.scope, args.turn_relation)))
        return
    missing = [name for name in ("junction", "road", "lane", "definition") if getattr(args, name) is None]
    if missing:
        ap.error("required to generate: " + ", ".join(f"--{n}" for n in missing))
    if args.source_map:
        extract_source(args.source_map, args.map_excerpt, args.road, args.junction)
    doc = build(args.map_excerpt, args.junction, args.road, args.lane, args.scope,
                args.definition, args.turn_relation)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(doc))


if __name__ == "__main__":
    main()
