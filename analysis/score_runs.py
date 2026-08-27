"""Family-aware scoring of evaluator runs (public port, MIT).

Turns evaluator output directories into labeled outcomes joined to measured
geometry: one row per (family, model, seed, variant, run), plus per-brain
aggregate tables. Offline - no CARLA needed. Python 3.8+ stdlib only.

    python -m analysis.score_runs <run_dir> [<run_dir> ...] --out scored/

Expected run layout (the evaluate_models convention; SLURM launchers that
produce the same shape work unchanged):
    <run_dir>/<model>/<scenario_id>.json                (legacy, seed 0)
    <run_dir>/<model>/seed_<k>/<scenario_id>.json
Each JSON is a leaderboard checkpoint file (_checkpoint.records[0]).

Family catalogs are read from failure_boundary_profiling_splits/:
variants.csv (join + measured geometry + passable/blockage split) and
attribution.json (placed-object poses and measured extents, exported from
the countersigned measurement layer).

SCORING POLICY (the complete list of judgment constants and rules; anything
not listed here is mechanical fact from the inputs). Every output header
repeats this block so no table circulates without its policy provenance.

- Collision attribution is positional: an infraction event is attributed to
  a placed obstacle when its coordinate lies within
      EGO_HALF_LENGTH_M + <that object's planar half-extent> + ATTRIB_SLACK_M
  of the object's world pose, AND the event's blueprint matches the object's.
  An event whose blueprint matches NO placed object cannot be ours
  regardless of position: classified traffic / pedestrian / environment by
  blueprint class, with near_obstacle_zone flagged when it fell inside a
  radius (squeeze interactions while threading). An event that MATCHES a
  placed blueprint but lies outside every radius is "ambiguous" (our
  object displaced post-contact - rammers bulldoze barriers - or a
  blueprint twin in traffic), never silently binned. Event coordinates
  are the ego location at the event, hence the ego half-length term.
- EGO_HALF_LENGTH_M = 2.45 (Lincoln MKZ 2020 bbox half-length, the
  leaderboard ego).
- ATTRIB_SLACK_M = 1.0 (contact geometry: bumper corner vs center distance).
- There is NO fallback extent. Placed-object extents come from the
  measured universe (attribution.json, exported from countersign sidecars
  and the spawn-probed registry). A scenario whose attribution list is
  empty has attribution DISABLED - every collision reports "ambiguous"
  and a warning states the contract - because partial attribution would
  misclassify the unmeasured object's contacts as traffic.
- Passability: variant assigned_family "full_static_blockage" -> blockage;
  otherwise passable. Passable variants with measured
  minimum_free_corridor_width < 2.297 m (the fm_002 policy floor) are
  additionally flagged check_passability for eyes.
- Late-commit: status Completed AND a scenario_timeouts infraction fired.
  Durations are evidence, not criteria.
- Outcome taxonomy (in evaluation order):
    Completed + obstacle contact          -> contact_thread (passable)
                                             contact_past_blockage (blockage)
    Completed + late                      -> late_commit
    Completed, clean                      -> clean_thread (passable)
                                             anomaly_blockage_completed (blockage)
    not Completed + obstacle contact      -> contact_blocked
    not Completed, clean                  -> refusal (passable)
                                             correct_stop (blockage)
- A row is ONE RUN - a sample, never "the" value for a (variant, seed):
  same-seed outcomes can bifurcate run-to-run. Aggregations report n.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

EGO_HALF_LENGTH_M = 2.45
ATTRIB_SLACK_M = 1.0
PASSABILITY_EYE_FLOOR_M = 2.297

DEFAULT_SPLITS = Path(__file__).resolve().parents[1] / \
    "failure_boundary_profiling_splits"

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
    half_extent: float
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
    min_speed_count: int
    events: list = field(default_factory=list)
    source: str = ""


def policy_header() -> str:
    doc = __doc__
    start = doc.index("SCORING POLICY")
    return doc[start:].rstrip() + "\n"


# --- catalog loading -------------------------------------------------------

MEASURED_NUMERIC = ("minimum_free_corridor_width", "maximum_route_intrusion",
                    "required_lateral_deviation", "closest_approach_distance")


def load_catalogs(splits_dir: Path):
    """scenario_id -> (family, variant dict); scenario_id -> [PlacedObject]."""
    variants: dict[str, tuple[str, dict]] = {}
    attribution: dict[str, list[PlacedObject]] = {}
    for fam_dir in sorted(p for p in splits_dir.iterdir() if p.is_dir()):
        cat = fam_dir / "variants.csv"
        if not cat.exists():
            continue
        with cat.open() as f:
            for row in csv.DictReader(f):
                if row.get("status") != "accepted":
                    continue
                measured = {}
                for k in MEASURED_NUMERIC:
                    v = row.get(k)
                    if v not in (None, ""):
                        measured[k] = float(v)
                variants[row["scenario_id"]] = (fam_dir.name, {
                    "index": int(row["index"]) if row.get("index") else None,
                    "target": row.get("target"),
                    "assigned_family": row.get("assigned_family") or None,
                    "measured": measured,
                })
        attr = fam_dir / "attribution.json"
        if attr.exists():
            doc = json.loads(attr.read_text())
            for sid, objs in (doc.get("scenarios") or {}).items():
                attribution[sid] = [PlacedObject(**o) for o in objs]
    return variants, attribution


# --- input discovery -------------------------------------------------------

def discover_records(run_dir: Path):
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


# --- attribution -----------------------------------------------------------

def classify_events(record: dict, objects: list[PlacedObject]):
    counts = dict(obstacle=0, traffic=0, pedestrian=0, environment=0, ambiguous=0)
    events = []
    infractions = record.get("infractions", {})
    for key in COLLISION_KEYS:
        for text in infractions.get(key) or []:
            m = EVENT_RE.search(text)
            if not m:
                counts["ambiguous"] += 1
                events.append({"kind": key, "class": "ambiguous",
                               "reason": "unparseable", "text": text})
                continue
            etype, ex, ey = m["type"], float(m["x"]), float(m["y"])
            nearest, nearest_d = None, math.inf
            for obj in objects:
                d = math.hypot(ex - obj.x, ey - obj.y)
                if d < nearest_d:
                    nearest, nearest_d = obj, d
            radius = (EGO_HALF_LENGTH_M + ATTRIB_SLACK_M +
                      (nearest.half_extent if nearest else 0.0))
            position_hit = nearest is not None and nearest_d <= radius
            placed_blueprints = {o.blueprint for o in objects}
            reason = None
            if etype in placed_blueprints:
                if position_hit and etype == nearest.blueprint:
                    cls = "obstacle"
                else:
                    cls = "ambiguous"
                    reason = "matches placed blueprint outside radius - " \
                             "displaced obstacle or traffic twin"
            elif etype.startswith("vehicle."):
                cls = "traffic"
            elif etype.startswith("walker."):
                cls = "pedestrian"
            else:
                cls = "environment"
            counts[cls] += 1
            events.append({
                "kind": key, "class": cls, "type": etype,
                "at": [ex, ey],
                "near_obstacle_zone": bool(position_hit and cls != "obstacle"),
                "reason": reason,
                "nearest_obstacle": nearest.blueprint if nearest else None,
                "nearest_distance_m": round(nearest_d, 2) if nearest else None,
                "radius_m": round(radius, 2) if nearest else None,
                "pose_source": nearest.pose_source if nearest else None,
                "extent_source": nearest.extent_source if nearest else None,
            })
    return counts, events


# --- taxonomy --------------------------------------------------------------

def outcome_for(completed: bool, obstacle_contacts: int, late: bool,
                passable: bool) -> str:
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

def score_run_dirs(run_dirs: list[Path], splits_dir: Path):
    variants, attribution = load_catalogs(splits_dir)
    rows, unmatched, warnings = [], [], []
    warned_disabled = set()

    for run_dir in run_dirs:
        for model, seed, seed_source, scenario_id, record, path in \
                discover_records(run_dir):
            hit = variants.get(scenario_id)
            if hit is None:
                unmatched.append(f"{path} (no catalog variant '{scenario_id}')")
                continue
            family, var = hit
            objects = attribution.get(scenario_id, [])
            if not objects and scenario_id not in warned_disabled:
                warned_disabled.add(scenario_id)
                warnings.append(
                    f"{scenario_id}: no attribution entries - placed-object "
                    "extents must come from the exported attribution.json "
                    "(countersign sidecar bbox or spawn-probed registry "
                    "footprint); obstacle attribution DISABLED for this "
                    "scenario")
            counts, events = classify_events(record, objects)
            if not objects:
                total = sum(counts.values())
                counts = dict(obstacle=0, traffic=0, pedestrian=0,
                              environment=0, ambiguous=total)

            infractions = record.get("infractions", {})
            status = record.get("status", "")
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
                outcome=outcome_for(completed, counts["obstacle"], late, passable),
                obstacle_collisions=counts["obstacle"],
                traffic_collisions=counts["traffic"],
                pedestrian_collisions=counts["pedestrian"],
                environment_collisions=counts["environment"],
                ambiguous_collisions=counts["ambiguous"],
                min_speed_count=len(infractions.get("min_speed_infractions") or []),
                events=events,
                source=f"{run_dir.name}/{path.relative_to(run_dir)}"))
    return rows, unmatched, warnings


# --- output ----------------------------------------------------------------

OUTCOME_CODE = {
    "clean_thread": "T", "late_commit": "L", "contact_thread": "C",
    "contact_past_blockage": "C!", "contact_blocked": "K",
    "refusal": "R", "correct_stop": "S", "anomaly_blockage_completed": "A?",
}

CSV_FIELDS = [
    "family", "model", "seed", "seed_source", "scenario_id", "variant_index",
    "target", "assigned_family", "passable", "check_passability", "status",
    "outcome", "driving_score", "score_route", "duration_game", "late_commit",
    "obstacle_collisions", "traffic_collisions", "pedestrian_collisions",
    "environment_collisions", "ambiguous_collisions", "min_speed_count",
    "minimum_free_corridor_width", "maximum_route_intrusion", "source",
]


def write_outputs(rows: list[RunRow], unmatched, warnings, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "scored_rows.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r.__dict__) + "\n")

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
        variants_seen = sorted({(r.variant_index, r.scenario_id, str(r.target))
                                for r in frows})
        models = sorted({r.model for r in frows})
        lines.append("")
        lines.append(f"{'idx':>3} {'target':>22} " +
                     " ".join(f"{m:>18}" for m in models))
        for idx, sid, target in variants_seen:
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
            amb = sum(r.ambiguous_collisions for r in mrows)
            lines.append(
                f"    {m:>14}: n={n} mean_DS={mean_ds:.1f} " +
                " ".join(f"{k}={v}" for k, v in sorted(oc.items())) +
                (f" traffic_noise_runs={noise}" if noise else "") +
                (f" AMBIGUOUS_EVENTS={amb}" if amb else ""))
        lines.append("")
    lines.append("legend: T clean_thread, L late_commit, C contact_thread, "
                 "C! contact_past_blockage, K contact_blocked, R refusal, "
                 "S correct_stop, A? anomaly; '~' = traffic collision in run")
    if unmatched:
        lines += ["", "UNMATCHED RESULT FILES (no catalog variant):"] + \
                 [f"  {u}" for u in unmatched]
    if warnings:
        lines += ["", "WARNINGS:"] + [f"  {w}" for w in warnings]
    text = "\n".join(lines) + "\n"
    (out_dir / "scored_tables.txt").write_text(text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--splits", type=Path, default=DEFAULT_SPLITS,
                    help="failure_boundary_profiling_splits directory")
    ap.add_argument("--out", type=Path, default=Path("scored_out"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    rows, unmatched, warnings = score_run_dirs(args.run_dirs, args.splits)
    if not rows:
        print("no scoreable records found", file=sys.stderr)
        return 1
    text = write_outputs(rows, unmatched, warnings, args.out)
    if not args.quiet:
        print(text)
    print(f"{len(rows)} rows -> {args.out}/scored_rows.{{csv,jsonl}}, "
          f"scored_tables.txt", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
