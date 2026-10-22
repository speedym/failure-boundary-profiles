"""Flow-family aggregation: per-variant collision% / stall% vs the crossing
time gap, for the oncoming-flow behavior-scalar family (fm_011).

    conda run -n scl_prototype python \
        adapters/fail2drive/scripts/multi_model_eval/flow_aggregate.py \
        [<results_root>] --manifest <family manifest> --zone <zone.json> \
        --map-excerpt <excerpt.xodr> --out <dir> [--by-model]

Reads the multi-model campaign tree (`<results_root>/<model>/seed_<k>/
<scenario_id>.json` + sibling `trajectories/RouteScenario_<route_id>_rep0.json.gz`)
and the solved-family manifest, and emits per-variant failure-rate curves against
`crossing_time_gap_s`. Offline, deterministic; stdlib plus the shared
Wilson estimator (scl_core.stats.boundary). The conflict zone the measured go
is taken against is a declared instrument document (--zone, canon under
artifacts/examples/scoring_instruments/) with the map excerpt it was built
from (--map-excerpt); both are pinned by the hash of the file passed, and
nothing in this module names a map, junction or lane.

WHY A DEDICATED AGGREGATOR (not score_runs.py / boundary_stats.py). Those score
the static-obstacle *threading* families: their outcome vocabulary
(clean_thread / refusal / late_commit) and their axis (measured
minimum_free_corridor_width) are lateral-geometric. The flow family has no placed
obstacle and no passage width; its failure modes are (a) colliding with the
oncoming flow and (b) stalling at the junction. This script measures exactly
those two, against the behavioral axis.

POLICY (judgment constants and rules; all else is mechanical).

- The axis is the MEASURED crossing time gap of the variant (F96, 2026-10-18):
  the gap certificate's headway at the crossing, the front-to-front time between
  consecutive oncoming cars at the ego's crossing region over the runs the ego
  did not perturb, median over the variant's seeds (flow_gap_certificate.py,
  `variants[].headway_at_crossing_s`, read per run through `variant_axis`). The
  manifest's NOMINAL crossing_time_gap_s (= spacing / flow_speed) identifies the
  variant and rides beside the axis as x_nominal: it is the knob, not a
  measurement. Until F96 it was the axis, and the flow chain was the one chain
  fitting on a declared value (threading fits on the measured corridor width,
  the warehouse on the measured gap). Runs are joined to the manifest by
  `scenario_id` (the result filename stem); if the results predate the manifest
  generation the join is wrong -- regenerate together. `measure_flow` reads each
  run's trajectory and reports the run's own measured_flow_speed /
  measured_crossing_time_gap_s / measured_spacing_m beside it, over the cars
  spawned before the ego's first entry into the lane: the generator's held-knob
  check per run. What the ego did to the flow after it entered (the cars that
  back up behind a stalled ego, the spawning that stretches) is not a property
  of the variant and is not read.

- FLOW COLLISION is attributed from the trajectory extract, never from the raw
  leaderboard count (which carries no actor role): a run collides with the flow
  iff the hero contacts an actor whose `role_name == "scenario"`. Distinct
  partner ids are counted; sustained per-frame contact is one partner. The
  leaderboard `collisions_vehicle` count is reported alongside as a cross-check.
  A run whose extract is MISSING is attribution="unavailable": its collision, if
  any, is counted as `collision_unattributed`, never silently as a flow hit --
  backfill_trajectories.py can redo the extract.

- STALL fires when any of {scenario_timeouts, route_timeout, vehicle_blocked}
  is recorded (the agent sat at the junction past a timeout). These are the
  signalized-class stall signals; a "stall" mixes light-wait with gap-wait on a
  single run (DESIGN_NOTES 4.7), so it is a *behavioral* signal, not a scored
  defect.

- collision% and stall% are reported as INDEPENDENT per-run booleans (a run can
  both collide and time out), because the handoff asks for both curves; the
  mutually-exclusive `primary_outcome` (priority collision_flow > stall >
  collision_other > clean_cross > incomplete) is reported separately for a clean
  distribution table.

- The canonical GAP-ACCEPTANCE curves are named per the cross-backend contract
  (p_go / p_clean; the Isaac findings v0.2 vocabulary):
    p_go     = MEASURED sticky commitment (Decision D2 below), None when the
               extract is unreadable / no corridor is derivable.
    p_clean  = clean pass by any route (label-based: clean_cross -> 1,
               committed/waited -> 0), None only on `incomplete`.
  Because p_go is measured and p_clean is label-based, their None-masks - and
  hence their per-variant denominators - can DIFFER; `p_go_clean_risk_set_match`
  flags per-run agreement. p_clean <= p_go is therefore NOT guaranteed by
  construction (a run can be clean by label yet have an unreadable extract, or
  measured-go with an `incomplete` label); read the two curves as independent
  marginals with their own risk sets, not a nested pair.
  The pre-existing conditional competence P(clean | go) remains as a separately
  named diagnostic (`clean_given_go_*`, with `competence_*` compatibility
  aliases), defined only over runs that measured-committed. It answers a
  different question and must not be compared as though it were p_clean.

- red_light_rate per variant = fraction of runs that ran the frozen red light.
  Reported to SEPARATE the flow signal (collision%, unconfounded) from the
  signalized-policy behavior that depresses driving score independently of the
  flow (DESIGN_NOTES 4.7, 5B): DS alone conflates the two; collision% does not.

- Rates carry Wilson intervals (z=1.96); with the seeded 3-seed campaign each
  (variant) pools n = models x seeds runs. PRIVILEGED experts (ground-truth
  access, DEFAULT_REFERENCE_MODELS / --reference-models) are held OUT of the
  pooled curve and reported as a REFERENCE line: pooling an oracle's ~0%
  collisions with the policies under test makes the curve a meaningless mixture
  (issue #2). --by-model still spans every model. A single seed is a *draw*, not
  the value (campaign notes) -- read the pooled rate, not one cell. n is small
  (~models x seeds), so the Wilson CIs are wide: read the intervals, do not
  over-read curve shape (issue #5).

- AXIS INTEGRITY (issue #1; retired 2026-10-18 by F96). Until F96 the per-run
  realized gap, the median spawn interval over the WHOLE recording, flagged a
  variant `axis_divergent` when any run departed from the nominal by more than
  a quarter, and nothing read the flag. Of the 58 flagged runs of v04 to v09,
  57 had committed and 49 collided with the flow: the stretch was the ego's own
  blocking, measured into a diagnostic. The axis is now the certificate's
  measured headway, the per-run reading stops at the ego's entry, and the
  generator's own departure (the spacing sweep's long end, where every car
  spawns at 15 m/s and slows before the next spawn triggers at 60 m) sits on
  the axis itself instead of hiding behind the label.

- DETERMINISM SCOPE (issue #4). The FLOW is deterministic per seed (verified:
  100/100 identical reps), which is what makes measure_flow a single exact value.
  The EGO is NOT: the agent RNG is model-internal and not seed-plumbed (the
  ledger says so), so each (model, seed) cell is ONE draw of the driving outcome
  -- the collision/stall booleans are not reproducible the way the flow is. Do
  not let the flow's determinism halo the outcome.

SPACING ASSUMPTION (do not "fix"), NOW MEASURED. The realized gap is exactly
`spacing/speed` ONLY because the family pins `source_dist_interval = [25, 25]`:
the pinned fork's ActorFlow draws each car's spacing from `rng.uniform(min, max)`
on the seeded RNG, so a non-degenerate interval would make the gap a
seed-dependent distribution. Restoring [24, 26] "for realism" silently
re-introduces a cross-seed spacing confound (DESIGN_NOTES 2). This is no longer
just an assumption: `measure_flow` reads the trajectories and confirms it -
measured_spacing_m sits at ~25.4 m across every variant (3->20 m/s), realized
speed tracks the target within ~1%, and the flow is deterministic per seed
(100/100 identical reps), so the pinned [25, 25] holds in the actual runs.

- COMMITMENT SCORING (Decision D2). The canonical p_go is the MEASURED
  sticky commitment from derive_measured_go (the hero body enters the
  oncoming-lane conflict zone, read from the trajectory), NOT the
  label-inferred code_commitment(primary_outcome) - so a run that enters the
  conflict and then stalls or goes incomplete is still a go. The zone is the
  fixed OpenDRIVE oncoming driving lane (derive_measured_go v3): its geometry is
  independent of when the oncoming recording ends, correcting the v1 swept-
  corridor truncation that read go=false on completed slow-flow turns (see
  CAMPAIGN_NOTES "flow commitment v3"). The label-inferred values are retained
  under explicit names (p_go_label, clean_given_go_label) for readers that
  want the old inference.
- D2b (2026-09-20): a go is an entry WHILE THE FLOW IS ACTIVE
  (derive_measured_go.GO_RULE): the flow window is the first to the last
  recorded frame of any scenario-role actor, and an entry after it is recorded
  (measured_go_entry_after_flow) but not counted. Twenty runs of v04 and v05
  (the expert and BridgeDrive at the tightest gaps) had waited out the flow
  and crossed after the scenario timeout removed the cars; they were counted
  as commitments against traffic they never faced.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

# The ONE estimator (S15): Wilson comes from scl_core.stats.boundary, the
# same formula boundary_stats and the Isaac scorer use - no private copy.
from scl_core.records import portable_path  # noqa: E402
from scl_core.stats.boundary import WILSON_Z, wilson  # noqa: E402

# D2 measured sticky-go instrument (see the docstring); pure over an extract.
sys.path.insert(0, str(SCRIPT_DIR))
import derive_measured_go  # noqa: E402
import trajectory_extract  # noqa: E402
import flow_gap_certificate as fgc  # noqa: E402  (F55: bands and decisions)
from scl_core import run_rows as rr  # noqa: E402  (the one row and the one stamp, F82)
from score_runs import WALL_CLOCK_METHOD, run_log_wall_clock  # noqa: E402  (the run's wall clock, F94: one CARLA reader)

# the flow chain's outcome vocabulary (F82): the labels this scorer writes and their codings, pinned in the stamp
VOCABULARY = REPO_ROOT / "artifacts/examples/scoring_instruments/outcome_vocabulary_flow.json"
BAND_ASSIGNMENT = {"usable": "passable", "wait": "blocked", "conditional": "conditional", fgc.DEFECT: "defect"}

EGO_HX_FLOW = 2.446   # Lincoln MKZ half-length / half-width (leaderboard ego)
EGO_HY_FLOW = 0.918


def measure_commitment(extract_path: Path, scenario_id: str | None,
                       zone: derive_measured_go.DeclaredZone) -> dict | None:
    """Measure ego entry into the declared map conflict zone for this scenario."""
    if not extract_path.is_file():
        return None
    try:
        return derive_measured_go.measure_run(
            extract_path, "flow", scenario_id, zone=zone, ego_hx=EGO_HX_FLOW, ego_hy=EGO_HY_FLOW)
    except Exception:
        return None



DEFAULT_RESULTS = SCRIPT_DIR / "results" / "multi_model"

# The signalized-class stall signals (per-record infraction lists; truthy when
# an event fired). scenario_timeouts = flow ScenarioTimeout; route_timeout /
# vehicle_blocked = the agent stopped making progress.
STALL_INFRACTIONS = ("scenario_timeouts", "route_timeout", "vehicle_blocked")

# measure_flow speed estimator. Instantaneous speeds below MIN_MOVING_SPEED_MPS
# are the spawn ramp / a full stop and are dropped; FREE_FLOW_PERCENTILE then
# reads the free-flow cruise from what remains (p90, not median: a car backed up
# behind a stalled ego still hits its target speed upstream, and the high
# percentile recovers that rather than the ego-disrupted average).
MIN_MOVING_SPEED_MPS = 0.5
FREE_FLOW_PERCENTILE = 0.90

# Privileged experts (ground-truth access) are the REFERENCE, not a policy under
# test: pooling their ~0% collisions into the curve makes it an oracle+agent
# mixture. Excluded from the pooled aggregate by default; reported on their own.
DEFAULT_REFERENCE_MODELS = ("pdm_lite_expert",)

# Gap-acceptance decision coding (stall-vs-not, mirroring boundary_stats'
# GO/STOP split). COMMIT = the agent entered the conflict (crossed or crashed);
# WAIT = it held at the junction until a timeout. `incomplete` (route deviation
# / setup failure) is neither and is EXCLUDED from both curves, counted per
# variant. Coded off primary_outcome so a colliding run counts as a commit.
COMMIT_OUTCOMES = {"collision_flow", "collision_other",
                   "collision_unattributed", "clean_cross"}
WAIT_OUTCOMES = {"stall"}


def code_commitment(primary: str) -> int | None:
    """P(commit): did the agent go rather than wait? None = excluded."""
    if primary in WAIT_OUTCOMES:
        return 0
    if primary in COMMIT_OUTCOMES:
        return 1
    return None  # incomplete: ambiguous, excluded


def code_clean_pass(primary: str) -> int | None:
    """P(clean pass): did a resolved run cross cleanly?

    Uses the SAME risk set as code_commitment: waits are clean-pass failures,
    while incomplete setup/route results are excluded from both quantities.
    """
    if primary == "clean_cross":
        return 1
    if primary in (COMMIT_OUTCOMES | WAIT_OUTCOMES):
        return 0
    return None


def code_competence(primary: str) -> int | None:
    """Legacy conditional diagnostic P(clean | go), not p_clean."""
    if primary == "clean_cross":
        return 1
    if primary in COMMIT_OUTCOMES:  # committed but not clean
        return 0
    return None  # stall / incomplete: excluded


def measured_clean_given_go(p_go: int | None, primary: str) -> int | None:
    """P(clean | go) on the MEASURED go bit: defined only for runs that
    committed (p_go == 1). A run that did not go (p_go 0) or has no measured
    go (None) is not in this conditional's risk set -> None (excluded), never
    a 0 that would deflate the execution-competence diagnostic with refusals.
    Among committed runs the clean determination is code_clean_pass, so an
    `incomplete` (unscorable) outcome after a measured commit is EXCLUDED
    (None), not counted as a clean-pass failure - matching p_clean."""
    if p_go == 1:
        return code_clean_pass(primary)
    return None


# --- manifest join ----------------------------------------------------------

def load_manifest_index(manifest_path: Path) -> tuple[str, dict[str, dict]]:
    """scenario_id -> {index, route_id, swept_factor, flow_speed, spacing,
    crossing_time_gap_s}.

    These are the NOMINAL per-variant design values (what the plan specifies) -
    the axis and the targets to measure against. Reads the `nominal` block (the
    behavior-scalar family's design values); falls back to the legacy `measured`
    block for manifests that predate the honesty rename (same numbers, the rename
    only swapped the key). The genuinely observed values come from `measure_flow`,
    not from here.

    Family-agnostic: `nominal` carries BOTH knobs (swept + held companion), so
    the flow_speed family and the source_dist_interval (spacing) family are read
    the same way. `swept_factor` (from the manifest's effective_domain) names
    which knob is the axis, so aggregate() can report the residual that matters.
    """
    doc = json.loads(manifest_path.read_text())
    swept_factor = (doc.get("effective_domain") or {}).get("factor_id")
    index: dict[str, dict] = {}
    for var in doc["variants"]:
        nominal = var.get("nominal") or var.get("measured") or {}
        # legacy flow-only manifests carry only oncoming_flow_speed in nominal;
        # there var["target"] IS the speed, so the fallback stays correct.
        index[var["scenario_id"]] = {
            "index": var["index"],
            "route_id": str(var["route_id"]),
            "swept_factor": swept_factor,
            "flow_speed": nominal.get("oncoming_flow_speed", var.get("target")),
            "spacing": nominal.get("oncoming_flow_spacing"),
            "crossing_time_gap_s": nominal.get("crossing_time_gap_s"),
        }
    return doc.get("family_id", manifest_path.stem), index


# --- per-run classification -------------------------------------------------

def _record_for(result_doc: dict, route_id: str) -> dict | None:
    records = result_doc.get("_checkpoint", {}).get("records", [])
    for rec in records:
        if route_id in str(rec.get("route_id", "")):
            return rec
    return records[0] if records else None


def _resolve_extract(traj_dir: Path, variant: dict, rec: dict) -> Path:
    """Locate this run's trajectory .gz: the shared resolver over the manifest
    route_id, the result record's own route_id and a variant-index match on
    disk (trajectory_extract.resolve_extract - one locator for both chains).
    Returns the nominal (possibly missing) path when nothing matches, so
    callers treat it as 'unavailable'."""
    return trajectory_extract.resolve_extract(
        traj_dir,
        [variant["route_id"], trajectory_extract.route_id_digits(rec.get("route_id"))],
        variant.get("index"))


def _scenario_partners(extract_path: Path) -> tuple[set[int], set[int]] | None:
    """(scenario partner ids, other partner ids) from a trajectory extract, or
    None when the extract is missing/unreadable (attribution unavailable).
    The roles are read by trajectory_extract.scenario_partners, the same
    reader the threading scorer attributes obstacle contacts with (F66)."""
    if not extract_path.is_file():
        return None
    try:
        data = trajectory_extract.read_extract(extract_path)
    except (OSError, json.JSONDecodeError):
        return None
    return trajectory_extract.scenario_partners(data)


def _median_raw(xs: list[float]) -> float | None:
    """Unrounded median (the rounding _median applies loses precision the
    realized-speed residual needs)."""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def _percentile(xs: list[float], q: float) -> float | None:
    """q-th percentile (0..1), linear interpolation. None if empty."""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def measure_flow(extract_path: Path, until_frame: int | None = None) -> dict | None:
    """Realized flow kinematics from the trajectory extract - the GENUINELY
    measured counterparts of the manifest's nominal oncoming_flow_speed /
    crossing_time_gap_s. None when the extract is missing/unreadable.
    `until_frame` (F96, 2026-10-18): the ego's first entry into the conflict
    lane, in the extract's frame numbers (the same space as the cars'
    create_frame); only the cars spawned at or before it are read, so the
    reading is the traffic's before the ego acted. None reads the whole run
    (a run that never entered). The output names its basis (`realized_basis`:
    before_entry, whole_run, or unmeasured_before_entry when fewer than two
    cars had spawned by the entry, gap and spacing null) and the number of
    spawn intervals the gap median is over (`n_spawn_intervals`).

    The flow cars (role 'scenario') all spawn at one lane point at staggered
    frames and cruise at constant speed, which makes the measurement robust
    without picking a fragile conflict line:
      measured_flow_speed          = per-car FREE-FLOW speed (p90 of the car's
                                     instantaneous speed), pooled across cars.
                                     p90 not median ON PURPOSE: when the ego
                                     stalls at the junction the oncoming cars
                                     back up behind it and their MEDIAN speed
                                     collapses, but each car still reaches the
                                     generator's target speed upstream first -
                                     the high percentile recovers that free-flow
                                     value (the flow-generator property we are
                                     verifying), not the ego-disrupted average.
      measured_crossing_time_gap_s = median consecutive spawn interval. In FREE
                                     FLOW the spawn schedule equals the junction
                                     crossing schedule (constant speed carries the
                                     temporal spacing from spawn to junction
                                     unchanged) at short spacings. Under
                                     CONGESTION the cadence stretches as cars back
                                     up toward the spawn point, which is why the
                                     reading stops at the ego's entry (F96). At
                                     long spacings the next spawn waits for the
                                     car ahead to be the spacing away while it
                                     slows after spawning, so the cadence runs
                                     longer than spacing/speed on every run: the
                                     certificate's headway at the crossing, not
                                     this cadence, is the axis.
      measured_spacing_m           = speed * gap; cross-checks the pinned
                                     source_dist_interval (~25 m)
    Deterministic per seed (verified: 100/100 identical reps), so one run per
    (seed, variant) is the value, not a draw.
    """
    if not extract_path.is_file():
        return None
    try:
        with gzip.open(extract_path, "rt", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return measure_flow_data(data, until_frame)


def measure_flow_data(data: dict, until_frame: int | None = None) -> dict | None:
    """measure_flow over an already-loaded extract (the certificate holds its extracts in memory)."""
    dt = data.get("dt_s", 0.05)
    actors, tracks = data.get("actors", {}), data.get("tracks", {})
    cars = []  # (create_frame, track), spawn order
    for aid, meta in actors.items():
        if meta.get("role_name") != "scenario":
            continue
        track = tracks.get(aid)
        if track and track.get("frames"):
            cars.append((meta.get("create_frame", track["frames"][0]), track))
    if not cars:
        return None
    cars.sort(key=lambda c: c[0])
    basis = "whole_run"
    if until_frame is not None:
        cars = [c for c in cars if c[0] <= until_frame]
        basis = "before_entry" if len(cars) >= 2 else "unmeasured_before_entry"

    speeds = []
    for _cf, track in cars:
        xs, ys, fs = track["x"], track["y"], track["frames"]
        inst = [math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1])
                / ((fs[i] - fs[i - 1]) * dt)
                for i in range(1, len(fs)) if fs[i] > fs[i - 1]]
        inst = [s for s in inst if s > MIN_MOVING_SPEED_MPS]  # drop ramp / stop
        # free-flow cruise, robust to ego-induced back-up (see module constants)
        free_flow = _percentile(inst, FREE_FLOW_PERCENTILE)
        if free_flow is not None:
            speeds.append(free_flow)
    create_frames = [cf for cf, _ in cars]
    gaps = [(create_frames[i] - create_frames[i - 1]) * dt
            for i in range(1, len(create_frames))]

    speed = _median_raw(speeds)
    gap = _median_raw(gaps)
    spacing = (round(speed * gap, 2)
               if speed is not None and gap is not None else None)
    return {
        "measured_flow_speed": round(speed, 3) if speed is not None else None,
        "measured_crossing_time_gap_s": round(gap, 3) if gap is not None else None,
        "measured_spacing_m": spacing,
        "n_flow_cars": len(cars),
        "n_spawn_intervals": len(gaps),
        "realized_basis": basis,
    }


def classify_run(result_path: Path, variant: dict, model: str, seed: int,
                 traj_dir: Path, zone: derive_measured_go.DeclaredZone,
                 certificate: dict | None = None) -> dict | None:
    doc = json.loads(result_path.read_text())
    rec = _record_for(doc, variant["route_id"])
    if rec is None:
        return None
    infractions = rec.get("infractions", {})
    # leaderboard status vocabulary: "Perfect" = reached the goal flawlessly,
    # "Completed" = reached it with infractions; both are a completed crossing.
    # (score_runs.py documents a campaign that scored every "Perfect" run wrong
    # by omitting it -- do not drop it here.)
    completed = rec.get("status") in ("Completed", "Perfect")
    stall_kinds = [k for k in STALL_INFRACTIONS if infractions.get(k)]
    stall = bool(stall_kinds)

    extract = _resolve_extract(traj_dir, variant, rec)
    partners = _scenario_partners(extract)
    # D2: p_go = MEASURED sticky-go (trajectory commitment via the instrument),
    # NOT label-inferred go. p_clean stays label-based. clean_given_go is the
    # MEASURED conditional P(clean | go): defined ONLY for runs that committed
    # (go == 1). A run that never went is not in this conditional's risk set,
    # so it is None (excluded), not a 0 - otherwise refusals would deflate the
    # execution-competence diagnostic they are meant to be distinguished from.
    mc = measure_commitment(extract, result_path.stem, zone)
    # F96: the run's own flow reading stops at the ego's entry (None: the whole run)
    flow_kin = measure_flow(extract, mc.get("first_entry_frame") if mc else None) or {}
    leaderboard_vehicle = len(infractions.get("collisions_vehicle") or [])
    if partners is None:
        attribution = "unavailable"
        flow_collision = False
        other_collision = leaderboard_vehicle > 0
        n_scenario_partners = None
    else:
        attribution = "trajectory"
        scen, other = partners
        flow_collision = bool(scen)
        other_collision = bool(other)
        n_scenario_partners = len(scen)

    if flow_collision:
        primary = "collision_flow"
    elif stall:
        primary = "stall"
    elif attribution == "unavailable" and other_collision:
        primary = "collision_unattributed"
    elif other_collision:
        primary = "collision_other"
    elif completed:
        primary = "clean_cross"
    else:
        primary = "incomplete"

    n_red_light = len(infractions.get("red_light") or [])
    if mc is not None and mc.get("go") is not None:
        p_go = 1 if mc["go"] else 0            # sticky bit
    else:
        p_go = None                            # refusal: unreadable extract / no corridor
    p_clean = code_clean_pass(primary)          # label-based, unchanged
    clean_given_go = measured_clean_given_go(p_go, primary)
    # Legacy label-based diagnostics, retained under explicit names (NOT the
    # published p_go) for downstream readers that want the old inference.
    p_go_legacy = code_commitment(primary)
    label_based_cond = code_competence(primary)

    # F55: the flow gap certificate's band for this cell (scenario, seed) and the decision read
    # against it; the entry state (from rest at the junction, or rolling) from the recorded track
    gap = {"gap_band": None, "gap_window_s": None, "gap_headway_s": None, "axis_headway_s": None,
           "entry_state": None, "entry_speed_mps": None,
           "decision": None, "decision_correct": None, "decision_scope": None}
    if certificate is not None:
        cell = fgc.seed_band(certificate, result_path.stem, seed)
        if cell is not None:
            gap["gap_band"], gap["gap_window_s"] = cell["band"], cell["usable_window_s"]
            gap["gap_headway_s"] = cell.get("local_headway_s")          # this seed's headway at the crossing
        gap["axis_headway_s"] = fgc.variant_axis(certificate, result_path.stem)   # the variant's axis (F96)
        if mc is not None and mc.get("first_entry_frame") is not None and extract is not None and extract.is_file():
            try:
                gap["entry_state"], gap["entry_speed_mps"] = fgc.entry_state_from_extract(
                    derive_measured_go.trajectory_extract.read_extract(extract), mc["first_entry_frame"])
            except (OSError, ValueError, KeyError):
                pass
        d = fgc.decision_for(gap["gap_band"], p_go, None if p_clean is None else bool(p_clean), gap["entry_state"])
        gap.update({"decision": d["decision"], "decision_correct": d["correct"], "decision_scope": d["scope"]})


    return {
        "scenario_id": result_path.stem,
        "model": model,
        "seed": seed,
        "index": variant["index"],
        "route_id": variant["route_id"],
        "swept_factor": variant.get("swept_factor"),            # which knob is the axis
        "flow_speed": variant["flow_speed"],                    # nominal speed (swept or held)
        "spacing": variant.get("spacing"),                      # nominal spacing (swept or held)
        "crossing_time_gap_s": variant["crossing_time_gap_s"],  # nominal axis
        # GENUINELY measured from this run's trajectory (None if no extract):
        "measured_flow_speed": flow_kin.get("measured_flow_speed"),
        "measured_crossing_time_gap_s": flow_kin.get("measured_crossing_time_gap_s"),
        "measured_spacing_m": flow_kin.get("measured_spacing_m"),
        "n_flow_cars": flow_kin.get("n_flow_cars"),
        "n_spawn_intervals": flow_kin.get("n_spawn_intervals"),
        "realized_basis": flow_kin.get("realized_basis"),
        "status": rec.get("status"),
        "score_composed": rec.get("scores", {}).get("score_composed"),
        "duration_game_s": rec.get("meta", {}).get("duration_game"),
        "flow_collision": flow_collision,
        "n_scenario_partners": n_scenario_partners,
        "leaderboard_vehicle_collisions": leaderboard_vehicle,
        "other_collision": other_collision,
        "stall": stall,
        "stall_kinds": stall_kinds,
        "primary_outcome": primary,
        "attribution": attribution,
        "n_red_light": n_red_light,
        "ran_red_light": n_red_light > 0,
        "p_go": p_go,
        "p_clean": p_clean,
        "commitment": p_go,             # compatibility alias -> the published (measured) p_go
        "clean_given_go": clean_given_go,
        "competence": clean_given_go,
        # legacy label-inferred values, retained under explicit names for
        # readers that want the old inference (NOT the published measured p_go)
        "p_go_label": p_go_legacy,
        "clean_given_go_label": label_based_cond,
        # D2 measured sticky-go audit fields (None on refusal).
        "measured_go_entry":             mc.get("entry")             if mc else None,
        "measured_go_entry_after_flow":  mc.get("entry_after_flow")  if mc else None,
        "measured_go_flow_window_frames": mc.get("flow_window_frames") if mc else None,
        "measured_go_first_entry_frame": mc.get("first_entry_frame") if mc else None,
        "measured_go_first_entry_t_s":   mc.get("first_entry_t_s") if mc else None,
        "measured_go_nearest_miss_m":    mc.get("nearest_miss_m")   if mc else None,
        "measured_go_n_frames_tested":   mc.get("n_frames_tested")  if mc else None,
        "measured_go_corridor_aabb":     mc.get("corridor_aabb")    if mc else None,
        "measured_go_corridor_approximated": (mc.get("corridor_approximated")
                                              if mc else None),
        "measured_go_zone_version": (mc.get("zone") or {}).get("version") if mc else None,
        "measured_go_zone_sha256": (mc.get("zone") or {}).get("geometry_sha256") if mc else None,
        # sample tolerance + refusal note preserved for auditing marginal
        # detections (a go within one inter-frame step of the corridor face).
        "measured_go_sample_tolerance_m": mc.get("sample_tolerance_m") if mc else None,
        "measured_go_note":              mc.get("note")               if mc else None,
        # F55: the gap certificate's cell and the decision against it (None without a certificate)
        "gap_band": gap["gap_band"], "gap_window_s": gap["gap_window_s"],
        "gap_headway_s": gap["gap_headway_s"], "axis_headway_s": gap["axis_headway_s"],
        "entry_state": gap["entry_state"], "entry_speed_mps": gap["entry_speed_mps"],
        "decision": gap["decision"], "decision_correct": gap["decision_correct"],
        "decision_scope": gap["decision_scope"],
        # Trajectory evidence that produced the measured go / kinematics /
        # attribution above - collected into the provenance stamp, then popped
        # (never serialized into the rows). None when no extract was resolved.
        "_evidence_extract": str(extract) if extract else None,
    }


def to_run_row(r: dict, family: str, vocabulary: dict) -> dict:
    """A classified run in the one shape (scl_core.run_rows, F82): the axis is the certificate's measured
    headway at the crossing for the variant (F96; the manifest's nominal gap rides beside it as x_nominal),
    the certificate's band is the assignment (usable is passable, wait is blocked, conditional stays, a
    registered cell is a defect), the commitment is the MEASURED go, clean and passage come from the
    vocabulary, the decision is the certificate's; everything else this scorer measured sits under the
    annex, with the seed's own headway, the run's flow reading before the ego's entry, the stall placed
    against the go, and the clean-without-go count. A variant the certificate holds no headway for refuses:
    the flow chain has no axis without its certificate."""
    nominal, axis = r["crossing_time_gap_s"], r.get("axis_headway_s")
    if nominal is None:
        raise ValueError(f"{r['scenario_id']}: no nominal crossing_time_gap_s in the manifest; the variant has no identity")
    if axis is None:
        raise ValueError(f"{r['scenario_id']}: no headway_at_crossing_s in the gap certificate; the flow axis is the "
                         "certificate's measured headway (F96), never the nominal gap")
    p_go = r["p_go"]
    codes = rr.codings(vocabulary, r["primary_outcome"], {"commitment": None if p_go is None else bool(p_go)})
    annex = {k: v for k, v in r.items() if k not in ("scenario_id", "model", "seed", "primary_outcome",
                                                     "crossing_time_gap_s", "axis_headway_s",
                                                     "decision_correct", "decision_scope")}
    annex.update(stall_before_go=bool(r["stall"]) and p_go == 0, stall_after_go=bool(r["stall"]) and p_go == 1,
                 stall_go_unknown=bool(r["stall"]) and p_go is None,
                 clean_without_measured_go=r["p_clean"] == 1 and p_go == 0,
                 measured_go_corridor_approximated=bool(r.get("measured_go_corridor_approximated")))
    return rr.row(family=family, model=r["model"], variant=r["scenario_id"], repetition=r["seed"],
                  x=axis, x_metric="crossing_time_gap_s", x_basis="measured", x_nominal=nominal, x_measured=axis,
                  assignment=BAND_ASSIGNMENT.get(r.get("gap_band"), "unmeasured"), outcome=r["primary_outcome"], **codes,
                  decision_correct=r.get("decision_correct"), decision_scope=r.get("decision_scope"), annex=annex)


def discover_runs(results_root: Path, index: dict[str, dict],
                  models: list[str] | None,
                  zone: derive_measured_go.DeclaredZone,
                  certificate: dict | None = None) -> tuple[list[dict], list[Path]]:
    # Runs join to the manifest by scenario_id (the result filename stem).
    # Also returns the raw result checkpoints consumed, for the provenance
    # stamp (write_provenance).
    rows: list[dict] = []
    consumed: list[Path] = []
    for model_dir in sorted(p for p in results_root.iterdir() if p.is_dir()):
        model = model_dir.name
        if models and model not in models:
            continue
        for seed_dir in sorted(model_dir.glob("seed_*")):
            try:
                seed = int(seed_dir.name.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            traj_dir = seed_dir / "trajectories"
            for result_path in sorted(seed_dir.glob("*.json")):
                variant = index.get(result_path.stem)
                if variant is None:
                    continue  # not a variant of this family
                row = classify_run(result_path, variant, model, seed, traj_dir, zone, certificate)
                if row is not None:
                    row.update(run_log_wall_clock(result_path))      # the log beside the record (F94)
                    ev = row.pop("_evidence_extract", None)
                    rows.append(row)
                    consumed.append(result_path)
                    if ev is not None:
                        consumed.append(Path(ev))   # hashed (or 'absent') below
    return rows, consumed


def instrument_dependency_paths(zone_path: Path, map_excerpt: Path,
                                gap_certificate: Path | None = None) -> dict[str, Path]:
    """Code and data the measured go depends on, keyed by file name. The zone
    document and the map excerpt it was built from are whatever the caller
    passed: this module names no map. The flow gap certificate (F55) joins
    the dependencies when one was passed."""
    here = Path(__file__).resolve().parent
    extra = {Path(gap_certificate).name: Path(gap_certificate),
             "flow_gap_certificate.py": here / "flow_gap_certificate.py"} if gap_certificate else {}
    return extra | {
        "derive_measured_go.py": here / "derive_measured_go.py",
        "route_metrics.py": here / "route_metrics.py",
        "trajectory_extract.py": here / "trajectory_extract.py",
        "boundary.py": REPO_ROOT / "scl_core/stats/boundary.py",
        "asset_registry.json": derive_measured_go.score_runs.REGISTRY_PATH,
        Path(zone_path).name: Path(zone_path),
        Path(map_excerpt).name: Path(map_excerpt),
        "build_conflict_lane_zone.py": SCRIPT_DIR / "build_conflict_lane_zone.py",
        "score_runs.py": here / "score_runs.py",
    }


def write_stamp(consumed: list[Path], manifest: Path, results_root: Path, out_dir: Path,
                zone: derive_measured_go.DeclaredZone, map_excerpt: Path, gap_certificate: Path | None,
                n_rows: int, wall_clock_missing: int = 0) -> None:
    """The one stamp (scl_core.run_rows.write_stamp): the scorer and the vocabulary pinned, the zone, the map
    excerpt and the gap certificate as instruments, the family manifest, the raw inputs' digest; the measured-go
    instrument block, the instrument dependencies and the certificate's rules under `extra`."""
    zone_rel, excerpt_rel = portable_path(zone.path, REPO_ROOT), portable_path(map_excerpt, REPO_ROOT)
    lines, input_files = [], {}
    for p in consumed:
        try:
            rel = p.relative_to(results_root)
        except ValueError:
            rel = p.name
        digest = rr.sha256_file(p) if p.is_file() else "absent"
        input_files[str(rel)] = digest
        lines.append(f"{rel} {digest}")

    def _dep_sha(p: Path) -> str:
        return rr.sha256_file(p) if p and p.is_file() else "absent"
    instrument_deps = {name: _dep_sha(path)
                       for name, path in instrument_dependency_paths(zone.path, map_excerpt, gap_certificate).items()}
    gap_block = None
    if gap_certificate is not None:
        cert, cert_sha = fgc.load_certificate(gap_certificate)
        gap_block = {"kind": cert["certificate_kind"], "family_id": cert["family_id"], "margin_s": cert["margin_s"],
                     "thresholds_s": cert["thresholds_s"], "decision_rules": fgc.DECISION_RULES}
    instruments = [("zone", zone.path), ("map_excerpt", map_excerpt)]
    if gap_certificate is not None:
        instruments.append(("gap_certificate", gap_certificate))
    rr.write_stamp(out_dir, scorer=Path(__file__).resolve(), root=REPO_ROOT, vocabulary=VOCABULARY, instruments=instruments,
                   family_manifest_sha256=rr.sha256_file(manifest), input_files_sha256=rr.inputs_digest(lines),
                   n_rows=n_rows,
                   extra={"input_files": dict(sorted(input_files.items())), "n_inputs": len(consumed),
                          "measured_go_instrument": {
                              "module": "derive_measured_go", "mode": "flow", "go_rule": derive_measured_go.GO_RULE,
                              "ego_hx_m": EGO_HX_FLOW, "ego_hy_m": EGO_HY_FLOW,
                              "zone_version": zone.doc["zone_version"], "zone_source": zone.source_key,
                              "zone_path": zone_rel, "geometry_sha256": zone.sha256,
                              "map_excerpt_path": excerpt_rel, "map_excerpt_sha256": _dep_sha(Path(map_excerpt))},
                          "instrument_deps_sha256": instrument_deps, "gap_certificate": gap_block,
                          "wall_clock_method": WALL_CLOCK_METHOD, "wall_clock_missing": wall_clock_missing})


# --- aggregation ------------------------------------------------------------

def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def _median(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    mid = len(xs) // 2
    return round((xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2), 2)


def aggregate(rows: list[dict], key_fields: tuple[str, ...]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[tuple(r[f] for f in key_fields)].append(r)
    out = []
    for key, grp in groups.items():
        n = len(grp)
        n_coll = sum(1 for r in grp if r["flow_collision"])
        n_stall = sum(1 for r in grp if r["stall"])
        n_clean = sum(1 for r in grp if r["primary_outcome"] == "clean_cross")
        n_unattr = sum(1 for r in grp if r["attribution"] == "unavailable")
        n_redlight = sum(1 for r in grp if r["ran_red_light"])
        coll_lo, coll_hi = wilson(n_coll, n)
        stall_lo, stall_hi = wilson(n_stall, n)
        # Canonical pair. p_go is MEASURED (None on an unreadable extract),
        # p_clean is LABEL-based (None only on `incomplete`), so their risk
        # sets can DIFFER per run - each carries its own denominator. The
        # conditional clean-given-go diagnostic is retained separately.
        go_vals = [r["p_go"] for r in grp if r["p_go"] is not None]
        clean_vals = [r["p_clean"] for r in grp
                      if r["p_clean"] is not None]
        cond_vals = [r["clean_given_go"] for r in grp
                     if r["clean_given_go"] is not None]
        go_n, go_k = len(go_vals), sum(go_vals)
        clean_n, clean_k = len(clean_vals), sum(clean_vals)
        cond_n, cond_k = len(cond_vals), sum(cond_vals)
        # F55: the decisions read against the gap certificate, per scope
        balanced = [r for r in grp if r.get("decision_scope") == "balanced"]
        conditional = [r for r in grp if r.get("decision_scope") == "conditional"]
        # D2: measured-go and label-clean risk sets may be DISJOINT (a run can
        # be in one but not the other). Equal counts do NOT prove equal
        # membership, so match on per-run inclusion, not on go_n == clean_n.
        risk_set_match = all((r["p_go"] is None) == (r["p_clean"] is None)
                             for r in grp)

        go_lo, go_hi = wilson(go_k, go_n)
        clean_lo, clean_hi = wilson(clean_k, clean_n)
        cond_lo, cond_hi = wilson(cond_k, cond_n)
        # Realized kinematics: median over the group's runs (each deterministic
        # per seed, so the spread here is cross-seed/model, not run noise).
        measured_speed = _median([r.get("measured_flow_speed") for r in grp])
        measured_gap = _median([r.get("measured_crossing_time_gap_s") for r in grp])
        measured_spacing = _median([r.get("measured_spacing_m") for r in grp])
        nominal_speed = grp[0]["flow_speed"]
        nominal_spacing = grp[0].get("spacing")
        row = dict(zip(key_fields, key))
        row.update({
            "crossing_time_gap_s": grp[0]["crossing_time_gap_s"],  # NOMINAL: the variant's identity
            "axis_headway_s": grp[0].get("axis_headway_s"),         # the certificate's measured axis (F96)
            "swept_factor": grp[0].get("swept_factor"),           # which knob is swept
            "flow_speed": grp[0]["flow_speed"],                    # NOMINAL (swept or held)
            "spacing": nominal_spacing,                            # NOMINAL (swept or held)
            # nominal (plan) vs GENUINELY measured (trajectory). Both residuals
            # are reported for both families: the HELD knob's residual is ~0 (a
            # sanity check that it stayed put), the SWEPT knob's residual is the
            # realized-vs-nominal signal for the axis (swept_factor names it).
            "measured_flow_speed": measured_speed,
            "flow_speed_residual": (round(measured_speed - nominal_speed, 3)
                                    if measured_speed is not None
                                    and nominal_speed is not None else None),
            "spacing_residual_m": (round(measured_spacing - nominal_spacing, 3)
                                   if measured_spacing is not None
                                   and nominal_spacing is not None else None),
            "measured_crossing_time_gap_s": measured_gap,   # the runs' flow before the ego's entry (median)
            "measured_spacing_m": measured_spacing,
            "n_flow_cars_median": _median([r.get("n_flow_cars") for r in grp]),
            "n_runs": n,
            "n_collision_flow": n_coll,
            "collision_rate": round(n_coll / n, 3),
            "collision_wilson_lo": round(coll_lo, 3),
            "collision_wilson_hi": round(coll_hi, 3),
            "n_stall": n_stall,
            "stall_rate": round(n_stall / n, 3),
            "stall_wilson_lo": round(stall_lo, 3),
            "stall_wilson_hi": round(stall_hi, 3),
            "clean_rate": round(n_clean / n, 3),
            # Canonical p_go (MEASURED) / p_clean (label) - independent
            # marginals, each on its own denominator (p_go_n vs p_clean_n).
            "gap_bands": sorted({r["gap_band"] for r in grp if r.get("gap_band")}),
            "decision_balanced_n": len(balanced),
            "decision_balanced_k": sum(1 for r in balanced if r["decision_correct"]),
            "decision_conditional_n": len(conditional),
            "decision_conditional_k": sum(1 for r in conditional if r["decision_correct"]),
            "p_go_n": go_n, "p_go_k": go_k,
            "p_go_excluded": n - go_n,
            "p_go_rate": round(go_k / go_n, 3) if go_n else None,
            "p_go_wilson_lo": round(go_lo, 3) if go_n else None,
            "p_go_wilson_hi": round(go_hi, 3) if go_n else None,
            "p_clean_n": clean_n, "p_clean_k": clean_k,
            "p_clean_excluded": n - clean_n,
            "p_clean_rate": (round(clean_k / clean_n, 3)
                                  if clean_n else None),
            "p_clean_wilson_lo": (round(clean_lo, 3)
                                        if clean_n else None),
            "p_clean_wilson_hi": (round(clean_hi, 3)
                                        if clean_n else None),
            # Backward-compatible p(go) aliases.
            "commitment_n": go_n,
            "commitment_rate": round(go_k / go_n, 3) if go_n else None,
            "commitment_wilson_lo": round(go_lo, 3) if go_n else None,
            "commitment_wilson_hi": round(go_hi, 3) if go_n else None,
            # Conditional P(clean | go), explicitly named; `competence_*`
            # remains a compatibility alias for existing readers.
            "clean_given_go_n": cond_n,
            "clean_given_go_rate": round(cond_k / cond_n, 3) if cond_n else None,
            "clean_given_go_wilson_lo": (round(cond_lo, 3)
                                          if cond_n else None),
            "clean_given_go_wilson_hi": (round(cond_hi, 3)
                                          if cond_n else None),
            "competence_n": cond_n,
            "competence_rate": round(cond_k / cond_n, 3) if cond_n else None,
            "competence_wilson_lo": round(cond_lo, 3) if cond_n else None,
            "competence_wilson_hi": round(cond_hi, 3) if cond_n else None,
            "red_light_rate": round(n_redlight / n, 3),
            "mean_score_composed": _mean([r["score_composed"] for r in grp]),
            "median_score_composed": _median([r["score_composed"] for r in grp]),
            "mean_duration_s": _mean([r["duration_game_s"] for r in grp]),
            "n_unattributed": n_unattr,
            "p_go_clean_risk_set_match": risk_set_match,
        })
        out.append(row)
    # sort by the axis (gap), then by any remaining key field
    out.sort(key=lambda r: (r["crossing_time_gap_s"] if r["crossing_time_gap_s"]
                            is not None else math.inf,
                            *(str(r.get(f, "")) for f in key_fields
                              if f != "index")))
    return out


# --- output -----------------------------------------------------------------

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _variant_row(r: dict, pct) -> str:
    gap = r.get("axis_headway_s")
    return (f"   {r.get('index', ''):>3} "
            f"{(str(gap) if gap is not None else '?'):>7} "
            f"{r['flow_speed']:>5} {r['n_runs']:>3} "
            f"{pct(r['collision_rate']):>6} {pct(r['stall_rate']):>7} "
            f"{pct(r['p_go_rate']):>6} {pct(r['p_clean_rate']):>7} "
            f"{pct(r['clean_given_go_rate']):>9} "
            f"{pct(r['red_light_rate']):>7} "
            f"{r['mean_score_composed'] if r['mean_score_composed'] is not None else '?':>7}")


def _summary_table(family: str, per_variant: list[dict], n_runs: int,
                   n_unattr: int, pooled_models: list[str],
                   per_variant_ref: list[dict]) -> str:
    def pct(v):
        return f"{v * 100:.0f}%" if v is not None else "-"

    def by_gap(rows):
        return sorted(rows, key=lambda x: -(x["crossing_time_gap_s"]
                      if x["crossing_time_gap_s"] is not None else -1))

    n_variant = max((r["n_runs"] for r in per_variant), default=0)
    header = (f"   {'v':>3} {'gap_s':>7} {'m/s':>5} {'n':>3} "
              f"{'coll%':>6} {'stall%':>7} {'go%':>6} {'clean%':>7} "
              f"{'clean|go%':>9} "
              f"{'redlt%':>7} {'meanDS':>7}")
    lines = [f"== {family}: collision% / stall% vs the measured crossing time gap "
             + "=" * 18,
             f"   {n_runs} runs, pooled over UNDER-TEST models: "
             f"{', '.join(pooled_models) or '(none)'} (n~{n_variant}/variant)"
             + (f"; {n_unattr} without a trajectory extract -> collision "
                f"unattributed" if n_unattr else ""),
             "", header]
    lines += [_variant_row(r, pct) for r in by_gap(per_variant)]

    if per_variant_ref:
        ref_models = sorted({r_["model"] for r_ in per_variant_ref
                             if "model" in r_}) or ["reference"]
        lines += ["", f"   -- REFERENCE (oracle, NOT pooled): "
                  f"{', '.join(ref_models)} --", header]
        lines += [_variant_row(r, pct) for r in by_gap(per_variant_ref)]

    lines += [
        "",
        "   (gap DESC = easy->hard. go%=P(go), MEASURED; clean%=P(clean_pass),",
        "   label-based - independent marginals, each on its own denominator",
        "   (go% n may differ from clean% n). clean|go%=P(clean | go) over",
        "   measured-committed runs, a conditional diagnostic. A good agent's",
        "   go% falls on unsafe gaps while clean|go% stays high; a poor agent",
        "   keeps going and clean|go% collapses.)",
        "   gap_s is the certificate's measured headway at the crossing (F96);",
        "     the nominal gap is the variant's name, in the realized table below.",
        "   scope (issue #4): the FLOW is deterministic per seed (verified), but",
        "     the agent RNG is not plumbed - each (model,seed) cell is ONE draw of",
        "     the ego outcome, not reproducible the way the flow is.",
        f"   power (issue #5): n~{n_variant}/variant -> wide Wilson CIs; read the",
        "     intervals, not point rates, and do not over-read curve shape.",
    ]
    return "\n".join(lines) + "\n"


def _realized_table(per_variant: list[dict]) -> str:
    """Nominal (plan) vs measured flow kinematics per variant: the certificate's
    axis (headway at the crossing) and the runs' own spawn cadence before the
    ego's entry (median over the group), beside the nominal knob values."""
    lines = ["== realized vs nominal flow kinematics "
             + "=" * 30,
             f"   {'v':>3} | {'nom m/s':>8} {'meas m/s':>8} {'d':>6} | "
             f"{'nom gap':>8} {'axis':>8} {'pre-go':>8} | {'spacing_m':>9}"]

    def num(v, w, p=2):
        return f"{v:>{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"

    for r in sorted(per_variant, key=lambda x: x.get("index", 0)):
        lines.append(
            f"   {r.get('index', ''):>3} | "
            f"{num(r.get('flow_speed'), 8)} {num(r.get('measured_flow_speed'), 8)} "
            f"{num(r.get('flow_speed_residual'), 6)} | "
            f"{num(r.get('crossing_time_gap_s'), 8)} "
            f"{num(r.get('axis_headway_s'), 8)} "
            f"{num(r.get('measured_crossing_time_gap_s'), 8)} | "
            f"{num(r.get('measured_spacing_m'), 9)}")
    lines.append("")
    lines.append("   (axis = the certificate's headway at the crossing, the x every point is fitted and drawn on;")
    lines.append("    pre-go = median spawn interval over the cars spawned before the ego's entry; d = measured")
    lines.append("    minus nominal speed, the held knob's check.)")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results_root", nargs="?", type=Path, default=DEFAULT_RESULTS,
                    help=f"campaign tree (default: {DEFAULT_RESULTS})")
    ap.add_argument("--manifest", type=Path, required=True,
                    help="the solved-family manifest the runs are joined to (no default: the family is a declaration)")
    ap.add_argument("--zone", type=Path, required=True,
                    help="declared conflict-zone document the measured go is taken against "
                         "(artifacts/examples/scoring_instruments/...); pinned by hash")
    ap.add_argument("--map-excerpt", type=Path, required=True,
                    help="the pinned OpenDRIVE excerpt the zone declares as its source "
                         "(artifacts/examples/carla_extracts/maps/...); pinned by hash")
    ap.add_argument("--gap-certificate", type=Path, required=True,
                    help="the family's flow gap certificate (F55): every run gets its cell's band and its "
                         "decision against it, and the variant's measured headway at the crossing is the "
                         "axis (F96); pinned by hash in the provenance")
    ap.add_argument("--out", type=Path, default=SCRIPT_DIR / "flow_stats_out")
    ap.add_argument("--models", nargs="+", default=None,
                    help="restrict to these model dirs")
    ap.add_argument("--reference-models", nargs="+",
                    default=list(DEFAULT_REFERENCE_MODELS),
                    help="privileged/oracle models to hold OUT of the pooled "
                         "curve and report as a reference line instead "
                         f"(default: {' '.join(DEFAULT_REFERENCE_MODELS)}). "
                         "Pass with no names to pool everything.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.manifest.is_file():
        print(f"manifest not found: {args.manifest}", file=sys.stderr)
        return 1
    if not args.results_root.is_dir():
        print(f"results root not found: {args.results_root}", file=sys.stderr)
        return 1

    try:
        zone = derive_measured_go.load_zone(args.zone)
        derive_measured_go.check_map_excerpt(zone, args.map_excerpt)
        for path in (zone.path, args.map_excerpt):
            portable_path(path, REPO_ROOT)
    except (OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    family, index = load_manifest_index(args.manifest)
    certificate = None
    if args.gap_certificate is not None:
        try:
            certificate, _ = fgc.load_certificate(args.gap_certificate)
            manifest_id = json.loads(args.manifest.read_text()).get("manifest_id")
            if certificate["family_id"] != manifest_id:
                raise ValueError(f"the gap certificate is for {certificate['family_id']}, the manifest is {manifest_id}")
        except (OSError, ValueError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
    rows, consumed = discover_runs(args.results_root, index, args.models, zone, certificate)
    if not rows:
        print(f"no {family} runs found under {args.results_root} "
              f"(join by scenario_id against the manifest)", file=sys.stderr)
        return 1

    # Reference (oracle) models are held OUT of the pooled curve so it is a
    # policy-under-test aggregate, not an oracle+agent mixture (issue #2). They
    # still get their own reference rows. Guard: if the split would empty the
    # pool (only reference models present), pool everything and warn.
    reference_set = set(args.reference_models or ())
    present = {r["model"] for r in rows}
    test_rows = [r for r in rows if r["model"] not in reference_set]
    ref_rows = [r for r in rows if r["model"] in reference_set]
    if not test_rows:
        print(f"warning: every model is a reference model "
              f"({', '.join(sorted(present))}); pooling all into the curve",
              file=sys.stderr)
        test_rows, ref_rows = rows, []
    pooled_models = sorted({r["model"] for r in test_rows})

    per_variant = aggregate(test_rows, ("index",))
    per_variant_ref = aggregate(ref_rows, ("index",)) if ref_rows else []
    n_unattr = sum(1 for r in test_rows if r["attribution"] == "unavailable")

    args.out.mkdir(parents=True, exist_ok=True)
    vocabulary, _ = rr.load_vocabulary(VOCABULARY)
    lineage = json.loads(args.manifest.read_text()).get("manifest_id") or family
    run_rows = [to_run_row(r, lineage, vocabulary) for r in rows]
    rr.write_rows(args.out / Path(rr.ROWS_MEMBER).name, run_rows, vocabulary)
    write_stamp(consumed, args.manifest, args.results_root, args.out, zone, args.map_excerpt, args.gap_certificate,
                len(run_rows), sum(1 for r in rows if r.get("wall_clock_s") is None))

    table = _summary_table(family, per_variant,
                           sum(r["n_runs"] for r in per_variant), n_unattr,
                           pooled_models, per_variant_ref)
    realized = _realized_table(per_variant)
    (args.out / "flow_summary.txt").write_text(table + "\n" + realized)
    if not args.quiet:
        print(table)
        print(realized)
    excluded = sorted(reference_set & present)
    if excluded:
        print(f"NOTE: pooled curve EXCLUDES reference model(s) "
              f"{', '.join(excluded)} (reported separately as a reference line). "
              f"Pass '--reference-models' with no names to pool everything.",
              file=sys.stderr)
    print(f"{len(rows)} runs total ({sum(r['n_runs'] for r in per_variant)} "
          f"pooled), {len(per_variant)} variants -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
