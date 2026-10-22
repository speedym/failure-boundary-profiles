"""Family-aware scoring of evaluator runs (Demo B item 6a).

Turns evaluator output directories into labeled outcomes joined to measured
geometry: one row per (family, model, seed, variant, run), plus per-brain
aggregate tables. Offline - no CARLA; run under the scl_prototype env.

    conda run -n scl_prototype python \
        adapters/fail2drive/scripts/multi_model_eval/score_runs.py \
        ~/scl/render_farm_test_results/fm_004_seed0 --out /tmp/scored

Accepts both output layouts: legacy flat <run>/<model>/<scenario>.json and
current nested <run>/<model>/seed_<k>/<scenario>.json (legacy rows get
seed=0 with seed_source="legacy-default").

SCORING POLICY (the complete list of judgment constants and rules; anything
not listed here is mechanical fact from the inputs). Every output header
repeats this block so no table circulates without its policy provenance.

- Collision attribution is by ACTOR IDENTITY (F66, 2026-10-05). A
  leaderboard infraction names the partner's blueprint and its server
  actor id; the run's trajectory extract (<seed_dir>/trajectories/,
  written by the runner from the CARLA recorder) names every actor's
  role: "scenario" (spawned by the scenario: the object we placed),
  "background" (traffic and pedestrians), "default" (spawned by the
  harness outside the scenario: the route's parked static meshes),
  "hero". An event is the placed obstacle when its partner's role is
  scenario and its blueprint is one the variant places; a background or
  default partner is traffic / pedestrian / environment by blueprint
  class; a partner the recorder never created (id 0) with an unplaced
  static blueprint is the map (environment); a partner the extract cannot
  name (an id absent from the extract, a scenario actor whose blueprint
  the variant does not place, a role outside this list, an unparseable
  entry) is "unresolved", never silently binned. Every event and every
  run record attribution_source "identity".
- Position is DIAGNOSTIC on every event, never the decision when an
  extract exists: the nearest placed object, the nearest placed object OF
  THE EVENT'S BLUEPRINT with its distance and radius
      EGO_HALF_LENGTH_M + <that object's planar half-extent> + ATTRIB_SLACK_M
  (event coordinates are the ego location at the event, hence the ego
  half-length term), near_obstacle_zone when the event fell inside any
  placed object's radius (squeeze interactions while threading), and for
  an attributed obstacle the partner's planar displacement from its placed
  pose at the first recorded contact and at the end of the run (a pushed
  or carried obstacle is a number in the record, not a story).
- FALLBACK for a run without an extract (layouts that predate the
  recorder, the July 2026 fixture): position decides, attribution_source
  "position" on every event and on the run, counted per family table. An
  event is the placed obstacle when it lies within the radius of a placed
  object OF ITS OWN BLUEPRINT (the nearest object of any blueprint is not
  the test: a bin beside the vending machine masked 13 hero-family
  contacts until 2026-10-05); an event matching a placed blueprint outside
  every such radius is "ambiguous" (displaced obstacle or traffic twin);
  a blueprint no variant places is traffic / pedestrian / environment by
  class.
- EGO_HALF_LENGTH_M = 2.45 (Lincoln MKZ 2020 bbox half-length, the
  leaderboard ego).
- ATTRIB_SLACK_M = 1.0 (contact geometry: bumper corner vs center distance).
- There is NO fallback extent. Placed-object extents come from the
  measured universe: the countersign sidecar's spawned bbox, or the
  generated registry's spawn-probed footprint (statics AND vehicles). A
  placed blueprint resolving to neither disables POSITIONAL attribution
  for that scenario (every fallback event "ambiguous", a warning states
  the contract) because partial attribution would misclassify the
  unmeasured object's contacts as traffic; identity attribution needs no
  extent. Event evidence records which extent source applied.
- Obstacle world poses: countersign sidecar settled transforms when the
  variant has one; otherwise the variant XML's placement params composed
  planarly against the base extract's anchor waypoint - the same
  convention realize.variant_pose replays (planar-only here: attribution
  needs x/y, not the road-rest z model).
- Passability: variant assigned_family when the manifest sets it
  ("full_static_blockage" -> blockage); otherwise the variant is passable
  (true by design for fm_001/002/003/005). Passable variants with
  measured minimum_free_corridor_width < 2.297 m (the fm_002 policy
  floor) are additionally flagged check_passability for eyes.
- Late-commit: status Completed AND a scenario_timeouts infraction fired
  (the standing campaign-notes rule). Durations are evidence, not criteria.
- Outcome taxonomy (in evaluation order):
    no obstacle contact, but an unresolved
    or ambiguous collision                -> unresolved_contact (the run hit
                                             something the record cannot
                                             name; never read as clean)
    Completed + obstacle contact          -> contact_thread (passable)
                                             contact_past_blockage (blockage)
    Completed + late                      -> late_commit
    Completed, clean                      -> clean_thread (passable)
                                             anomaly_blockage_completed (blockage;
                                             circumvention suspect - v1 cannot
                                             prove sidewalk routes; recorder
                                             metrics (6b) will)
    not Completed + obstacle contact      -> contact_blocked
    not Completed, clean                  -> refusal (passable)
                                             correct_stop (blockage)
- A row is ONE RUN - a sample, never "the" value for a (variant, seed):
  same-seed outcomes bifurcate run-to-run (campaign notes, cross-campaign
  section, 2026-08-03). Aggregations report n.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from adapters.fail2drive.xml_tools import parse_route_xml  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trajectory_extract  # noqa: E402  (the shared extract readers, F66)
from scl_core import run_rows as rr  # noqa: E402  (the one row and the one stamp, F82)
from scl_core import run_wall_clock as rwc  # noqa: E402  (the run's wall clock from its log, F94)

EGO_HALF_LENGTH_M = 2.45
ATTRIB_SLACK_M = 1.0
PASSABILITY_EYE_FLOOR_M = 2.297

MANIFEST_DIR = REPO_ROOT / "artifacts/examples/solved_families"
# the threading chain's outcome vocabulary (F82): the labels this scorer writes and their codings, pinned in the stamp
VOCABULARY = REPO_ROOT / "artifacts/examples/scoring_instruments/outcome_vocabulary_threading.json"
FAMILY_AXES: dict = {}   # family -> (metric, kind), from the manifests load_manifest_index read
SIDECAR_DIR = REPO_ROOT / "artifacts/examples/carla_extracts/scenarios"
XML_DIR = REPO_ROOT / "artifacts/examples/backend_outputs"
REGISTRY_PATH = REPO_ROOT / "adapters/fail2drive/asset_registry.json"

# every file this scorer reads from the repository rather than from the run tree, by repo-relative
# path and sha256 (principle 13, 2026-09-21): the family manifests, the asset registry, the scenario
# sidecars and route XMLs; pinned file by file in scoring_provenance.canon_inputs_sha256 so an
# interpretation says exactly which canon bytes it depended on (a raw bundle with these members in
# its family snapshot is ledger F49; until then they are read here and pinned here)
CANON_READS: dict = {}


def _canon_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    try:
        key = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        key = str(path)
    CANON_READS[key] = hashlib.sha256(data).hexdigest()
    return data


def _canon_json(path: Path):
    return json.loads(_canon_bytes(path).decode())

COLLISION_KEYS = ("collisions_layout", "collisions_pedestrian", "collisions_vehicle")
EVENT_RE = re.compile(
    r"type=(?P<type>\S+) and id=(?P<id>\d+) at "
    r"\(x=(?P<x>-?[\d.]+), y=(?P<y>-?[\d.]+), z=(?P<z>-?[\d.]+)\)")

BLOCKAGE_LABEL = "full_static_blockage"


@dataclass
class PlacedObject:
    blueprint: str
    x: float
    y: float
    half_extent: float | None   # None: unmeasured (positional attribution disabled)
    extent_source: str
    pose_source: str


@dataclass
class RunRow:
    family: str
    model: str
    seed: int
    seed_source: str
    scenario_id: str
    variant_index: int | None
    target: object
    measured: dict
    assigned_family: str | None
    passable: bool
    check_passability: bool
    status: str
    completed: bool
    driving_score: float
    score_route: float
    duration_game: float
    late_commit: bool
    outcome: str
    obstacle_collisions: int
    traffic_collisions: int
    pedestrian_collisions: int
    environment_collisions: int
    ambiguous_collisions: int
    unresolved_collisions: int
    attribution_source: str      # "identity" (extract read) or "position" (fallback)
    min_speed_count: int
    events: list = field(default_factory=list)
    source: str = ""
    extract_source: str = ""     # run-dir-relative path of the extract read, "" without one
    wall_clock_s: float | None = None   # the run's wall clock from the log beside its record (F94), null with
    wall_clock_case: str = ""           # the case named in wall_clock_case (scl_core.run_wall_clock.CASES)


# --- the run's wall clock (F94, 2026-10-17) ---------------------------------
# the leaderboard writes one log beside each result record (<scenario>.log) and stamps every line; the wall
# clock is first to last timestamp, a retried run's attempts all between them (the cost of the run)
RUN_LOG_STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
WALL_CLOCK_METHOD = ("the run log beside the result record (<scenario>.log), first to last timestamp; a retried "
                     "run's attempts all lie between them")


def run_log_wall_clock(record_path: Path) -> dict:
    """The run's wall clock from the log beside its result record (the two fields of scl_core.run_wall_clock),
    the no-log case when the record stands alone (the committed fixtures, a run whose log was not kept)."""
    return rwc.read_log(Path(record_path).with_suffix(".log"), lambda text: rwc.between_stamps(text, RUN_LOG_STAMP))


def policy_header() -> str:
    doc = __doc__
    start = doc.index("SCORING POLICY")
    return doc[start:].rstrip() + "\n"


# --- input discovery -------------------------------------------------------

def discover_records(run_dir: Path):
    """Yield (model, seed, seed_source, scenario_id, checkpoint_record, path).

    Layouts: <run>/<model>/<scenario>.json (legacy, seed 0) and
    <run>/<model>/seed_<k>/<scenario>.json. viz/ and recordings/ subtrees
    never contain checkpoint JSONs but are excluded anyway.
    """
    for p in sorted(run_dir.rglob("*.json")):
        rel = p.relative_to(run_dir).parts
        if any(part in ("viz", "recordings") for part in rel):
            continue
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        records = (data.get("_checkpoint") or {}).get("records") or []
        if not records:
            continue
        model = rel[0] if len(rel) > 1 else "unknown"
        seed, seed_source = 0, "legacy-default"
        for part in rel:
            m = re.fullmatch(r"seed_(\d+)", part)
            if m:
                seed, seed_source = int(m.group(1)), "path"
        yield model, seed, seed_source, p.stem, records[0], p


# --- manifest join ---------------------------------------------------------

def _family_axis():
    """campaign_manifest.family_axis, the one axis reader, loaded from the sibling file by path: both adapters
    name their manifest module campaign_manifest, and a process that holds the other adapter's (the suite,
    the driver) must still reach this one."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fail2drive_campaign_manifest", Path(__file__).resolve().parent / "campaign_manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.family_axis


def load_manifest_index() -> dict[str, tuple[str, dict]]:
    family_axis = _family_axis()
    index = {}
    for mf in sorted(MANIFEST_DIR.glob("*.json")):
        doc = _canon_json(mf)
        try:
            FAMILY_AXES[mf.stem] = family_axis(doc)
        except ValueError:
            pass   # a manifest without one scheduled axis: not a family this scorer scores
        # the manifest's family_id names the BACKEND scenario family
        # ("passable_static_obstacle"); tables want the fm_* name
        family = mf.stem
        for var in doc.get("variants", []):
            if var.get("verdict") not in (None, "accepted"):
                continue
            index[var["scenario_id"]] = (family, var)
    return index


# --- obstacle world poses --------------------------------------------------

def _registry_extents() -> dict[str, float]:
    """blueprint -> max planar HALF-extent, from the generated registry.

    Registry schema: rows[].binding.ref (blueprint id, when binding.kind is
    "blueprint") and rows[].footprint.half_extents (actor-frame half extents
    from the spawn-probe sweep). Covers the 41 vehicle blueprints too, so
    the fm_004 Chargers and background traffic resolve to measured extents
    like every static prop.
    """
    if not REGISTRY_PATH.exists():
        return {}
    doc = _canon_json(REGISTRY_PATH)
    out = {}
    for row in doc.get("rows", []):
        binding = row.get("binding") or {}
        fp = row.get("footprint") or {}
        half = fp.get("half_extents")
        if binding.get("kind") == "blueprint" and binding.get("ref") and half:
            out[binding["ref"]] = max(half[0], half[1])
    return out


def _base_extract_for(scenario_id: str) -> dict | None:
    """Longest base-extract filename that prefixes the scenario id."""
    best = None
    for p in SIDECAR_DIR.glob("*.json"):
        if scenario_id != p.stem and scenario_id.startswith(p.stem) \
                and (best is None or len(p.stem) > len(best.stem)):
            best = p
    return _canon_json(best) if best else None


def placed_objects(scenario_id: str, registry_extents: dict,
                   warnings: list[str]) -> list[PlacedObject]:
    def unmeasured(blueprint: str) -> None:
        # no fallback extent by policy: partial POSITIONAL attribution would
        # classify the unmeasured object's contacts as traffic, so the
        # scenario's positional attribution is refused and the reason named;
        # the placed set stays (identity attribution needs no extent)
        warnings.append(
            f"{scenario_id}: placed object '{blueprint}' has no measured "
            "extent - placed-object extents must come from the countersign "
            "sidecar bbox or the generated registry footprint (rebuild the "
            "registry or countersign the variant); POSITIONAL obstacle "
            "attribution DISABLED for this scenario (identity attribution "
            "from the trajectory extract is unaffected)")
        return None

    sidecar = SIDECAR_DIR / f"{scenario_id}.json"
    if sidecar.exists():
        doc = _canon_json(sidecar)
        out = []
        for bs in doc.get("backend_scenarios", []):
            for obj in bs.get("objects", []):
                tf = obj.get("spawned_transform") or obj.get("computed_transform")
                ext = obj.get("bbox_extent")
                out.append(PlacedObject(
                    blueprint=obj["blueprint"], x=tf["x"], y=tf["y"],
                    half_extent=(max(ext[0], ext[1]) if ext
                                 else unmeasured(obj["blueprint"])),
                    extent_source="sidecar",
                    pose_source="sidecar"))
        return out

    xml_path = XML_DIR / f"{scenario_id}.xml"
    base = _base_extract_for(scenario_id)
    if not xml_path.exists() or base is None:
        warnings.append(f"{scenario_id}: no sidecar, and "
                        f"{'no variant XML' if not xml_path.exists() else 'no base extract'}"
                        " - obstacle attribution DISABLED for this scenario")
        return []
    _canon_bytes(xml_path)   # pinned by hash; parsed below
    route = parse_route_xml(xml_path)
    out = []
    for sc in route.scenarios:
        bs = min(base.get("backend_scenarios", []),
                 key=lambda b: math.dist(
                     (b["trigger_waypoint"]["x"], b["trigger_waypoint"]["y"]),
                     (sc.trigger["x"], sc.trigger["y"])),
                 default=None)
        if bs is None or math.dist(
                (bs["trigger_waypoint"]["x"], bs["trigger_waypoint"]["y"]),
                (sc.trigger["x"], sc.trigger["y"])) > 5.0:
            warnings.append(f"{scenario_id}: scenario '{sc.name}' has no "
                            "base-extract match within 5 m - skipped")
            continue
        if sc.distance is not None and bs.get("distance_m") is not None \
                and abs(sc.distance - bs["distance_m"]) > 1e-6:
            warnings.append(
                f"{scenario_id}: <distance> {sc.distance} != base extract "
                f"{bs['distance_m']} - anchor stale, poses suspect")
        anchor = bs["anchor_waypoint"]
        yaw = math.radians(anchor["yaw"])
        fwd = (math.cos(yaw), math.sin(yaw))
        right = (math.cos(yaw + math.pi / 2), math.sin(yaw + math.pi / 2))
        for obj in sc.objects:
            px, py = obj.params["x"], obj.params["y"]
            ext = registry_extents.get(obj.blueprint)
            out.append(PlacedObject(
                blueprint=obj.blueprint,
                x=anchor["x"] + fwd[0] * px + right[0] * py,
                y=anchor["y"] + fwd[1] * px + right[1] * py,
                half_extent=ext if ext is not None else unmeasured(obj.blueprint),
                extent_source="registry",
                pose_source="xml+anchor"))
    return out


# --- attribution -----------------------------------------------------------

COLLISION_CLASSES = ("obstacle", "traffic", "pedestrian", "environment",
                     "ambiguous", "unresolved")


def _blueprint_class(blueprint: str) -> str:
    if blueprint.startswith("vehicle."):
        return "traffic"
    if blueprint.startswith("walker."):
        return "pedestrian"
    return "environment"


def _extract_for(record_path: Path, variant: dict, record: dict):
    """(extract dict, path) of the run's trajectory extract, or (None, None)
    when the layout carries none: the positional fallback is then named on
    every event of the run."""
    traj_dir = record_path.parent / "trajectories"
    if not traj_dir.is_dir():
        return None, None
    p = trajectory_extract.resolve_extract(
        traj_dir,
        [variant.get("route_id"), trajectory_extract.route_id_digits(record.get("route_id"))],
        variant.get("index"))
    if not p.is_file():
        return None, None
    return trajectory_extract.read_extract(p), p


def classify_events(record: dict, objects: list[PlacedObject], extract: dict | None = None):
    """One event per leaderboard collision entry. With an extract the
    partner's recorded identity decides (attribution_source "identity");
    without one, position decides ("position"). Position facts are kept
    on every event as diagnostics either way (policy block above)."""
    counts = {k: 0 for k in COLLISION_CLASSES}
    events = []
    infractions = record.get("infractions", {})
    placed_bps = {o.blueprint for o in objects}
    measured = all(o.half_extent is not None for o in objects)
    roles = trajectory_extract.partner_roles(extract) if extract is not None else None
    source = "identity" if roles is not None else "position"

    def radius(obj):
        if obj is None or obj.half_extent is None:
            return None
        return EGO_HALF_LENGTH_M + ATTRIB_SLACK_M + obj.half_extent

    for key in COLLISION_KEYS:
        for text in infractions.get(key) or []:
            m = EVENT_RE.search(text)
            if not m:
                counts["unresolved"] += 1
                events.append({"kind": key, "class": "unresolved",
                               "attribution_source": source,
                               "reason": "unparseable", "text": text})
                continue
            etype, aid = m["type"], int(m["id"])
            ex, ey = float(m["x"]), float(m["y"])
            nearest, nearest_d = None, math.inf
            same, same_d = None, math.inf
            for obj in objects:
                d = math.hypot(ex - obj.x, ey - obj.y)
                if d < nearest_d:
                    nearest, nearest_d = obj, d
                if obj.blueprint == etype and d < same_d:
                    same, same_d = obj, d
            in_any_radius = bool(nearest is not None and measured
                                 and nearest_d <= radius(nearest))
            same_hit = bool(same is not None and measured and same_d <= radius(same))
            partner = roles.get(aid) if roles is not None else None
            reason = None
            if roles is not None:
                if partner is None:
                    if aid == 0 and etype not in placed_bps \
                            and _blueprint_class(etype) == "environment":
                        cls = "environment"
                        reason = "id 0: a map static the recorder never created"
                    else:
                        cls = "unresolved"
                        reason = f"actor id {aid} is not in the extract"
                elif partner["role"] == "scenario":
                    if partner["blueprint"] != etype:
                        cls = "unresolved"
                        reason = (f"actor {aid} is {partner['blueprint']} in the extract, "
                                  f"{etype} in the record")
                    elif etype in placed_bps:
                        cls = "obstacle"
                    else:
                        cls = "unresolved"
                        reason = (f"scenario actor {aid} ({etype}) is not a placed "
                                  "blueprint of this variant")
                elif partner["role"] in ("background", "default"):
                    cls = _blueprint_class(partner["blueprint"] or etype)
                    if cls == "environment":
                        reason = (f"actor {aid} role {partner['role']}: spawned by the "
                                  "harness, not by the scenario (not a placed object)")
                else:
                    cls = "unresolved"
                    reason = f"actor {aid} has role {partner['role']!r}"
            else:
                if not measured:
                    cls = "ambiguous"
                    reason = "positional attribution disabled: an unmeasured placed extent"
                elif etype in placed_bps:
                    if same_hit:
                        cls = "obstacle"
                    else:
                        cls = "ambiguous"
                        reason = ("matches a placed blueprint outside its radius - "
                                  "displaced obstacle or traffic twin")
                else:
                    cls = _blueprint_class(etype)
            counts[cls] += 1
            ev = {
                "kind": key, "class": cls, "type": etype, "actor_id": aid,
                "at": [ex, ey],
                "attribution_source": source,
                "partner_role": partner["role"] if partner else None,
                "reason": reason,
                "near_obstacle_zone": bool(in_any_radius and cls != "obstacle"),
                "nearest_obstacle": nearest.blueprint if nearest else None,
                "nearest_distance_m": round(nearest_d, 2) if nearest else None,
                "radius_m": round(radius(nearest), 2) if radius(nearest) is not None else None,
                "same_blueprint_distance_m": round(same_d, 2) if same else None,
                "same_blueprint_radius_m": round(radius(same), 2) if radius(same) is not None else None,
                "pose_source": nearest.pose_source if nearest else None,
                "extent_source": nearest.extent_source if nearest else None,
            }
            if roles is not None and cls == "obstacle":
                at_hit, final = trajectory_extract.partner_displacement(extract, aid)
                ev["partner_displacement_at_contact_m"] = (
                    round(at_hit, 2) if at_hit is not None else None)
                ev["partner_displacement_final_m"] = (
                    round(final, 2) if final is not None else None)
            events.append(ev)
    return counts, events


# --- taxonomy --------------------------------------------------------------

def outcome_for(completed: bool, obstacle_contacts: int, late: bool,
                passable: bool, unresolved_contacts: int = 0) -> str:
    if not obstacle_contacts and unresolved_contacts:
        return "unresolved_contact"
    if completed:
        if obstacle_contacts:
            return "contact_thread" if passable else "contact_past_blockage"
        if late:
            return "late_commit"
        return "clean_thread" if passable else "anomaly_blockage_completed"
    if obstacle_contacts:
        return "contact_blocked"
    return "refusal" if passable else "correct_stop"


# --- scoring ---------------------------------------------------------------

def score_run_dirs(run_dirs: list[Path]):
    manifest_index = load_manifest_index()
    registry_extents = _registry_extents()
    object_cache: dict[str, list[PlacedObject]] = {}
    rows, unmatched, warnings = [], [], []

    for run_dir in run_dirs:
        for model, seed, seed_source, scenario_id, record, path in \
                discover_records(run_dir):
            hit = manifest_index.get(scenario_id)
            if hit is None:
                unmatched.append(f"{path} (no manifest variant '{scenario_id}')")
                continue
            family, var = hit
            if scenario_id not in object_cache:
                object_cache[scenario_id] = placed_objects(
                    scenario_id, registry_extents, warnings)
            objects = object_cache[scenario_id]
            extract, extract_path = _extract_for(path, var, record)
            counts, events = classify_events(record, objects, extract)
            if not objects and extract is None:
                # no placed set and no recording: nothing can name a partner
                total = sum(counts.values())
                counts = {k: 0 for k in COLLISION_CLASSES}
                counts["ambiguous"] = total
                for ev in events:
                    ev["class"] = "ambiguous"
                    ev["reason"] = "attribution disabled: no placed objects resolved and no extract"

            infractions = record.get("infractions", {})
            status = record.get("status", "")
            # leaderboard status vocabulary: "Perfect" = completed with a
            # flawless score (emitted by the current harness; absent from
            # the July datasets, which is why the golden never covered it -
            # found by the 2026-08-06 multi-seed campaign, where every
            # flawless run scored as "refusal")
            completed = status in ("Completed", "Perfect")
            late = completed and bool(infractions.get("scenario_timeouts"))
            assigned = var.get("assigned_family") or None
            passable = assigned != BLOCKAGE_LABEL
            measured = var.get("measured") or {}
            width = measured.get("minimum_free_corridor_width")
            check = bool(passable and width is not None
                         and width < PASSABILITY_EYE_FLOOR_M and not assigned)

            rows.append(RunRow(
                family=family, model=model, seed=seed, seed_source=seed_source,
                scenario_id=scenario_id,
                variant_index=var.get("index"), target=var.get("target"),
                measured=measured, assigned_family=assigned,
                passable=passable, check_passability=check,
                status=status, completed=completed,
                driving_score=record["scores"]["score_composed"],
                score_route=record["scores"].get("score_route", math.nan),
                duration_game=record.get("meta", {}).get("duration_game", math.nan),
                late_commit=late,
                outcome=outcome_for(completed, counts["obstacle"], late, passable,
                                    counts["ambiguous"] + counts["unresolved"]),
                obstacle_collisions=counts["obstacle"],
                traffic_collisions=counts["traffic"],
                pedestrian_collisions=counts["pedestrian"],
                environment_collisions=counts["environment"],
                ambiguous_collisions=counts["ambiguous"],
                unresolved_collisions=counts["unresolved"],
                attribution_source="identity" if extract is not None else "position",
                min_speed_count=len(infractions.get("min_speed_infractions") or []),
                events=events,
                # run-dir-relative: keeps committed golden outputs
                # machine-independent
                source=f"{run_dir.name}/{path.relative_to(run_dir)}",
                extract_source=(f"{run_dir.name}/{extract_path.relative_to(run_dir)}"
                                if extract_path is not None else ""),
                **run_log_wall_clock(path)))
    return rows, unmatched, warnings


# --- the one row (F82) -----------------------------------------------------

def _finite(v):
    """A measurement that is not a number is null in the row (the stamp's writer refuses NaN)."""
    return None if isinstance(v, float) and math.isnan(v) else v


def to_run_row(r, vocabulary: dict) -> dict:
    """The scorer's row in the one shape (scl_core.run_rows): the family's axis from its manifest (a measured
    axis takes the variant's measurement, a categorical one its level), the codings from the vocabulary, the
    decision through the one rule (run_rows.decision: a clean pass is right on a passable variant, a stop on a
    blocked one), everything else this scorer measured under the annex. A behavior-scalar family refuses: the flow
    chain scores it."""
    if r.family not in FAMILY_AXES:
        raise ValueError(f"{r.family}: no single scheduled axis in its manifest; not a family this scorer scores")
    metric, kind = FAMILY_AXES[r.family]
    if kind == "behavior_scalar":
        raise ValueError(f"{r.family}: a behavior-scalar axis ({metric}) is the flow chain's (flow_aggregate.py), "
                         "not this scorer's")
    if kind == "categorical":
        x, basis, x_nominal, x_measured = str(r.target), "level", None, None
    else:
        x = (r.measured or {}).get(metric)
        if x is None:
            raise ValueError(f"{r.family}/{r.scenario_id}: no measured {metric} for the variant; a measured axis "
                             "never takes the target in its place")
        basis, x_nominal, x_measured = "measured", _finite(r.target), x
    codes = rr.codings(vocabulary, r.outcome)
    assignment = "passable" if r.passable else "blocked"
    correct, scope = rr.decision(assignment, codes["commitment"], codes["clean"])
    annex = {k: _finite(v) for k, v in r.__dict__.items()
             if k not in ("family", "model", "scenario_id", "seed", "outcome", "passable")}
    return rr.row(family=r.family, model=r.model, variant=r.scenario_id, repetition=r.seed,
                  x=x, x_metric=metric, x_basis=basis, x_nominal=x_nominal, x_measured=x_measured,
                  assignment=assignment, outcome=r.outcome, **codes,
                  decision_correct=correct, decision_scope=scope, annex=annex)


# --- output ----------------------------------------------------------------

OUTCOME_CODE = {
    "clean_thread": "T", "late_commit": "L", "contact_thread": "C",
    "contact_past_blockage": "C!", "contact_blocked": "K",
    "refusal": "R", "correct_stop": "S", "anomaly_blockage_completed": "A?",
    "unresolved_contact": "U?",
}

CSV_FIELDS = [
    "family", "model", "seed", "seed_source", "scenario_id", "variant_index",
    "target", "assigned_family", "passable", "check_passability", "status",
    "outcome", "driving_score", "score_route", "duration_game", "late_commit",
    "obstacle_collisions", "traffic_collisions", "pedestrian_collisions",
    "environment_collisions", "ambiguous_collisions", "unresolved_collisions",
    "attribution_source", "min_speed_count",
    "minimum_free_corridor_width", "maximum_route_intrusion", "source",
]


def write_outputs(rows: list[RunRow], unmatched, warnings, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    vocabulary, _ = rr.load_vocabulary(VOCABULARY)
    rr.write_rows(out_dir / Path(rr.ROWS_MEMBER).name, [to_run_row(r, vocabulary) for r in rows], vocabulary)

    with (out_dir / "scored_rows.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            d = dict(r.__dict__)
            d["minimum_free_corridor_width"] = r.measured.get(
                "minimum_free_corridor_width")
            d["maximum_route_intrusion"] = r.measured.get(
                "maximum_route_intrusion")
            w.writerow(d)

    lines = [policy_header(), ""]
    by_family = defaultdict(list)
    for r in rows:
        by_family[r.family].append(r)
    for family in sorted(by_family):
        frows = by_family[family]
        lines.append(f"== {family} " + "=" * max(0, 66 - len(family)))
        variants = sorted({(r.variant_index, r.scenario_id, str(r.target))
                           for r in frows})
        models = sorted({r.model for r in frows})
        lines.append("")
        lines.append(f"{'idx':>3} {'target':>22} " +
                     " ".join(f"{m:>18}" for m in models))
        for idx, sid, target in variants:
            cells = []
            for m in models:
                cell_rows = [r for r in frows
                             if r.scenario_id == sid and r.model == m]
                if not cell_rows:
                    cells.append(f"{'-':>18}")
                    continue
                parts = []
                for r in sorted(cell_rows, key=lambda r: (r.seed, r.source)):
                    code = OUTCOME_CODE.get(r.outcome, "?")
                    noise = "~" if r.traffic_collisions else ""
                    parts.append(f"s{r.seed}:{code}{noise} {r.driving_score:.1f}")
                cells.append(f"{'|'.join(parts):>18}")
            lines.append(f"{idx if idx is not None else '?':>3} "
                         f"{target:>22} " + " ".join(cells))
        lines.append("")
        lines.append("  per-model over this family:")
        for m in models:
            mrows = [r for r in frows if r.model == m]
            n = len(mrows)
            mean_ds = sum(r.driving_score for r in mrows) / n
            oc = defaultdict(int)
            for r in mrows:
                oc[r.outcome] += 1
            noise = sum(1 for r in mrows if r.traffic_collisions)
            amb = sum(r.ambiguous_collisions + r.unresolved_collisions for r in mrows)
            pos = sum(1 for r in mrows if r.attribution_source == "position")
            lines.append(
                f"    {m:>14}: n={n} mean_DS={mean_ds:.1f} " +
                " ".join(f"{k}={v}" for k, v in sorted(oc.items())) +
                (f" traffic_noise_runs={noise}" if noise else "") +
                (f" UNRESOLVED_EVENTS={amb}" if amb else "") +
                (f" position_attributed_runs={pos}" if pos else ""))
        lines.append("")
    lines.append("legend: T clean_thread, L late_commit, C contact_thread, "
                 "C! contact_past_blockage, K contact_blocked, R refusal, "
                 "S correct_stop, A? anomaly, U? unresolved_contact; "
                 "'~' = traffic collision in run")
    if unmatched:
        lines += ["", "UNMATCHED RESULT FILES (no manifest variant):"] + \
                 [f"  {u}" for u in unmatched]
    if warnings:
        lines += ["", "WARNINGS:"] + [f"  {w}" for w in warnings]
    text = "\n".join(lines) + "\n"
    (out_dir / "scored_tables.txt").write_text(text)
    return text


def write_provenance(rows: list, run_dirs: list, out_dir: Path) -> None:
    """Stamp the scored output with its instrument and input identity.

    Content-identity only - no timestamps - so scoring the same raw with
    the same scorer reproduces this file byte-for-byte (the golden chain
    stays deterministic). Consumers (boundary_stats, paper_figures)
    refuse scored input whose scorer_sha256 does not match the scorer
    beside them: derived tables cannot silently outlive the instrument
    that produced them (the fm_004 "Perfect"-as-refusal incident,
    2026-09-04).
    """
    by_name = {Path(d).name: Path(d) for d in run_dirs}

    def get(r, key, default=""):
        return getattr(r, key, default) if hasattr(r, "__dict__") else r.get(key, default)

    def digest(src):
        top, _, rel = src.partition("/")
        p = by_name.get(top)
        return (hashlib.sha256((p / rel).read_bytes()).hexdigest()
                if p is not None and (p / rel).is_file() else "absent")

    lines = []
    for r in rows:
        src = get(r, "source")
        lines.append(f"{src} {digest(src)}")
        esrc = get(r, "extract_source")
        if esrc:   # the extract the identity attribution read (F66)
            lines.append(f"{esrc} {digest(esrc)}")
    sources = [get(r, "attribution_source", "position") for r in rows]
    families = sorted({get(r, "family") for r in rows})
    if len(families) != 1:
        raise ValueError(f"one family per scoring run (the stamp names its manifest); the rows hold {families}")
    manifest_rel = f"artifacts/examples/solved_families/{families[0]}.json"
    rr.write_stamp(out_dir, scorer=Path(__file__).resolve(), root=REPO_ROOT, vocabulary=VOCABULARY, instruments=[],
                   family_manifest_sha256=CANON_READS.get(manifest_rel),
                   input_files_sha256=rr.inputs_digest(lines), n_rows=len(rows),
                   canon_inputs=CANON_READS,
                   extra={"extract_tool_sha256": hashlib.sha256(Path(trajectory_extract.__file__).read_bytes()).hexdigest(),
                          "collision_classes": list(COLLISION_CLASSES),
                          "attribution": {"identity_runs": sources.count("identity"),
                                          "position_runs": sources.count("position")},
                          "wall_clock_method": WALL_CLOCK_METHOD,
                          "wall_clock_missing": sum(1 for r in rows if get(r, "wall_clock_s", None) is None)})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("scored_out"))
    ap.add_argument("--quiet", action="store_true",
                    help="suppress table echo to stdout")
    args = ap.parse_args()

    rows, unmatched, warnings = score_run_dirs(args.run_dirs)
    if not rows:
        print("no scoreable records found", file=sys.stderr)
        return 1
    text = write_outputs(rows, unmatched, warnings, args.out)
    write_provenance(rows, args.run_dirs, args.out)
    if not args.quiet:
        print(text)
    print(f"{len(rows)} rows -> {args.out}/run_rows.jsonl (the one row, F82), scored_rows.csv, "
          f"scored_tables.txt", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
