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

- The axis is the manifest's per-variant NOMINAL crossing_time_gap_s
  (= spacing / flow_speed), NOT the raw flow_speed target. This is the D1
  claimed axis: a variant's difficulty is its crossing time gap. Runs are joined
  to the manifest by `scenario_id` (the result filename stem); if the results
  predate the manifest generation the gap join is wrong -- regenerate together.
  `nominal` is the seed-independent EXPECTED value E[measured] the plan designs
  for (~ the knob); it names no measurement by construction. `measure_flow` reads
  each run's trajectory and reports the GENUINELY measured measured_flow_speed /
  measured_crossing_time_gap_s / measured_spacing_m beside it (flow_realized_
  per_seed.csv, the realized-vs-nominal table) - the per-seed draw, obtainable
  only by running that seed.

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

- AXIS INTEGRITY (issue #1). The plotted x is the NOMINAL crossing_time_gap_s.
  measure_flow reports the realized gap; when it departs from nominal by more
  than AXIS_DIVERGENCE_FRAC on any run (the fast variants congest and never
  deliver the labelled difficulty, seed-dependently), the variant is
  `axis_divergent` and marked '*' -- plot it at its measured gap, not nominal.
  The pooled median hides this; the per-run worst case (gap_residual_abs_max_s)
  does not.

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

# A variant's plotted x is its NOMINAL crossing_time_gap_s. Flag it when the
# realized gap on ANY run in the group departs from nominal by more than this
# fraction: the flow CONGESTED (ego blocks the junction, cars pile up behind it,
# spawning stretches) and did not deliver the labelled difficulty, so the point
# is mis-placed on the axis. The 0.25 default is CALIBRATED to campaign 4 (not a
# first-principles constant): there the fast variants stretch 50-160% when they
# congest (real) while the slow end wobbles ~10% from low-car-count quantization
# (noise), and 0.25 cleanly separates them. It is a labelling threshold only -
# the raw residual is always in gap_residual_s / gap_residual_abs_max_s
# regardless - and is exposed as --axis-divergence-frac so it is a knob, not a
# magic number. The pooled median hides the seed that congests; the per-run
# worst case (what this flag uses) does not.
AXIS_DIVERGENCE_FRAC = 0.25

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


def measure_flow(extract_path: Path) -> dict | None:
    """Realized flow kinematics from the trajectory extract - the GENUINELY
    measured counterparts of the manifest's nominal oncoming_flow_speed /
    crossing_time_gap_s. None when the extract is missing/unreadable.

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
                                     unchanged). Under CONGESTION the cadence
                                     stretches as cars back up toward the spawn
                                     point - that stretch is exactly what the
                                     axis_divergent flag detects (issue #1).
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
    flow_kin = measure_flow(extract) or {}
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
    # D2: p_go = MEASURED sticky-go (trajectory commitment via the instrument),
    # NOT label-inferred go. p_clean stays label-based. clean_given_go is the
    # MEASURED conditional P(clean | go): defined ONLY for runs that committed
    # (go == 1). A run that never went is not in this conditional's risk set,
    # so it is None (excluded), not a 0 - otherwise refusals would deflate the
    # execution-competence diagnostic they are meant to be distinguished from.
    mc = measure_commitment(extract, result_path.stem, zone)
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
    gap = {"gap_band": None, "gap_window_s": None, "entry_state": None, "entry_speed_mps": None,
           "decision": None, "decision_correct": None, "decision_scope": None}
    if certificate is not None:
        cell = fgc.seed_band(certificate, result_path.stem, seed)
        if cell is not None:
            gap["gap_band"], gap["gap_window_s"] = cell["band"], cell["usable_window_s"]
        if mc is not None and mc.get("first_entry_frame") is not None and extract is not None and extract.is_file():
            try:
                gap["entry_state"], gap["entry_speed_mps"] = fgc.entry_state_from_extract(
                    derive_measured_go.trajectory_extract.read_extract(extract), mc["first_entry_frame"])
            except (OSError, ValueError, KeyError):
                pass
        d = fgc.decision_for(gap["gap_band"], p_go, primary, gap["entry_state"])
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
        "entry_state": gap["entry_state"], "entry_speed_mps": gap["entry_speed_mps"],
        "decision": gap["decision"], "decision_correct": gap["decision_correct"],
        "decision_scope": gap["decision_scope"],
        # Trajectory evidence that produced the measured go / kinematics /
        # attribution above - collected into the provenance stamp, then popped
        # (never serialized into the rows). None when no extract was resolved.
        "_evidence_extract": str(extract) if extract else None,
    }


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


def write_provenance(consumed: list[Path], manifest: Path,
                     results_root: Path, out_dir: Path,
                     zone: derive_measured_go.DeclaredZone, map_excerpt: Path,
                     gap_certificate: Path | None = None) -> None:
    """Bind scored rows to exact raw inputs, geometry, and implementation (and the flow gap
    certificate the decisions were read against, F55)."""
    zone_rel, excerpt_rel = portable_path(zone.path, REPO_ROOT), portable_path(map_excerpt, REPO_ROOT)
    lines = []
    input_files = {}
    for p in consumed:
        try:
            rel = p.relative_to(results_root)
        except ValueError:
            rel = p.name
        digest = (hashlib.sha256(p.read_bytes()).hexdigest()
                  if p.is_file() else "absent")
        input_files[str(rel)] = digest
        lines.append(f"{rel} {digest}")
    inputs_digest = hashlib.sha256(
        "\n".join(sorted(set(lines))).encode()).hexdigest()

    def _dep_sha(p: Path) -> str:
        return (hashlib.sha256(p.read_bytes()).hexdigest()
                if p and p.is_file() else "absent")
    instrument_deps = {name: _dep_sha(path)
                       for name, path in instrument_dependency_paths(zone.path, map_excerpt, gap_certificate).items()}
    gap_block = None
    if gap_certificate is not None:
        cert, cert_sha = fgc.load_certificate(gap_certificate)
        gap_block = {"kind": cert["certificate_kind"], "path": portable_path(Path(gap_certificate), REPO_ROOT),
                     "sha256": cert_sha, "family_id": cert["family_id"], "margin_s": cert["margin_s"],
                     "thresholds_s": cert["thresholds_s"], "decision_rules": fgc.DECISION_RULES}
    doc = {
        "provenance_kind": "scoring_provenance/v0.1",
        "scorer_file": Path(__file__).name,
        "scorer_sha256": hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "input_files_sha256": inputs_digest,
        "input_files": dict(sorted(input_files.items())),
        "flow_runs_sha256": _dep_sha(out_dir / "flow_runs.jsonl"),
        "n_inputs": len(consumed),
        "measured_go_instrument": {
            "module": "derive_measured_go", "mode": "flow",
            "go_rule": derive_measured_go.GO_RULE,
            "ego_hx_m": EGO_HX_FLOW, "ego_hy_m": EGO_HY_FLOW,
            "zone_version": zone.doc["zone_version"],
            "zone_source": zone.source_key,
            "zone_path": zone_rel,
            "geometry_sha256": zone.sha256,
            "map_excerpt_path": excerpt_rel,
            "map_excerpt_sha256": _dep_sha(Path(map_excerpt))},
        "instrument_deps_sha256": instrument_deps,
        "gap_certificate": gap_block,
        "note": ("content identity only, no timestamps; consumers refuse on "
                 "scorer_sha256 mismatch - re-aggregate raw with the current "
                 "flow_aggregate.py instead of reusing stale flow tables"),
    }
    (out_dir / "scoring_provenance.json").write_text(
        json.dumps(doc, indent=1) + "\n")


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


def aggregate(rows: list[dict], key_fields: tuple[str, ...],
              axis_divergence_frac: float = AXIS_DIVERGENCE_FRAC) -> list[dict]:
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
        nominal_gap = grp[0]["crossing_time_gap_s"]
        # Worst-case (not median) gap departure from nominal across the group's
        # runs - the median washes out a single congested seed; the axis flag
        # must not (issue #1).
        run_gap_resids = [abs(r["measured_crossing_time_gap_s"] - nominal_gap)
                          for r in grp
                          if r.get("measured_crossing_time_gap_s") is not None
                          and nominal_gap is not None]
        gap_resid_abs_max = round(max(run_gap_resids), 3) if run_gap_resids else None
        axis_divergent = bool(gap_resid_abs_max is not None and nominal_gap
                              and gap_resid_abs_max / nominal_gap
                              > axis_divergence_frac)
        row = dict(zip(key_fields, key))
        row.update({
            "crossing_time_gap_s": grp[0]["crossing_time_gap_s"],  # NOMINAL axis
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
            "measured_crossing_time_gap_s": measured_gap,
            "gap_residual_s": (round(measured_gap - nominal_gap, 3)
                               if measured_gap is not None
                               and nominal_gap is not None else None),
            # worst per-run departure + the axis-integrity flag (issue #1)
            "gap_residual_abs_max_s": gap_resid_abs_max,
            "axis_divergent": axis_divergent,
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
    gap = r["crossing_time_gap_s"]
    # '*' = the nominal x is off (issue #1): the realized gap departed from
    # nominal on some run; read the point at its measured gap, not this one.
    flag = "*" if r.get("axis_divergent") else " "
    return (f"   {r.get('index', ''):>3} "
            f"{(str(gap) if gap is not None else '?') + flag:>7} "
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
    lines = [f"== {family}: collision% / stall% vs crossing time gap "
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
        "   * axis note (issue #1): realized gap departed from nominal on some",
        "     run (flow congested); plot the point at measured_crossing_time_gap_s.",
        "   scope (issue #4): the FLOW is deterministic per seed (verified), but",
        "     the agent RNG is not plumbed - each (model,seed) cell is ONE draw of",
        "     the ego outcome, not reproducible the way the flow is.",
        f"   power (issue #5): n~{n_variant}/variant -> wide Wilson CIs; read the",
        "     intervals, not point rates, and do not over-read curve shape.",
    ]
    return "\n".join(lines) + "\n"


def _realized_table(per_variant: list[dict]) -> str:
    """Nominal (plan) vs GENUINELY measured (trajectory) flow kinematics - the
    record that replaces the manifest's old 'measured' echo with an observation."""
    lines = ["== realized vs nominal flow kinematics "
             + "=" * 30,
             f"   {'v':>3} | {'nom m/s':>8} {'meas m/s':>8} {'d':>6} | "
             f"{'nom gap':>8} {'meas gap':>8} {'d':>6} | {'spacing_m':>9}"]

    def num(v, w, p=2):
        return f"{v:>{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"

    for r in sorted(per_variant, key=lambda x: x.get("index", 0)):
        lines.append(
            f"   {r.get('index', ''):>3} | "
            f"{num(r.get('flow_speed'), 8)} {num(r.get('measured_flow_speed'), 8)} "
            f"{num(r.get('flow_speed_residual'), 6)} | "
            f"{num(r.get('crossing_time_gap_s'), 8)} "
            f"{num(r.get('measured_crossing_time_gap_s'), 8)} "
            f"{num(r.get('gap_residual_s'), 6)} | "
            f"{num(r.get('measured_spacing_m'), 9)}")
    lines.append("")
    lines.append("   (measured spacing ~ pinned source_dist_interval confirms "
                 "gap = spacing/speed; residuals are realized-minus-nominal.)")
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
    ap.add_argument("--gap-certificate", type=Path, default=None,
                    help="the family's flow gap certificate (F55): every run gets its cell's band and its "
                         "decision against it; pinned by hash in the provenance")
    ap.add_argument("--out", type=Path, default=SCRIPT_DIR / "flow_stats_out")
    ap.add_argument("--models", nargs="+", default=None,
                    help="restrict to these model dirs")
    ap.add_argument("--reference-models", nargs="+",
                    default=list(DEFAULT_REFERENCE_MODELS),
                    help="privileged/oracle models to hold OUT of the pooled "
                         "curve and report as a reference line instead "
                         f"(default: {' '.join(DEFAULT_REFERENCE_MODELS)}). "
                         "Pass with no names to pool everything.")
    ap.add_argument("--by-model", action="store_true",
                    help="also emit per (variant, model) rows")
    ap.add_argument("--axis-divergence-frac", type=float,
                    default=AXIS_DIVERGENCE_FRAC,
                    help="flag a variant axis_divergent when its realized gap "
                         "departs from nominal by more than this fraction on any "
                         f"run (default {AXIS_DIVERGENCE_FRAC}, calibrated to "
                         "campaign 4; labelling only, raw residual always kept)")
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

    frac = args.axis_divergence_frac
    per_variant = aggregate(test_rows, ("index",), frac)
    per_variant_ref = aggregate(ref_rows, ("index",), frac) if ref_rows else []
    n_unattr = sum(1 for r in test_rows if r["attribution"] == "unavailable")

    args.out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(args.out / "flow_runs.jsonl", rows)
    write_provenance(consumed, args.manifest, args.results_root, args.out, zone, args.map_excerpt, args.gap_certificate)
    _write_jsonl(args.out / "flow_per_variant.jsonl", per_variant)
    variant_fields = ["index", "swept_factor", "crossing_time_gap_s",
                      "flow_speed", "measured_flow_speed", "flow_speed_residual",
                      "spacing", "measured_spacing_m", "spacing_residual_m",
                      "measured_crossing_time_gap_s", "gap_residual_s",
                      "gap_residual_abs_max_s", "axis_divergent",
                      "n_flow_cars_median", "n_runs",
                      "n_collision_flow", "collision_rate", "collision_wilson_lo",
                      "collision_wilson_hi", "n_stall", "stall_rate",
                      "stall_wilson_lo", "stall_wilson_hi", "clean_rate",
                      "p_go_n", "p_go_k", "p_go_excluded", "p_go_rate",
                      "p_go_wilson_lo", "p_go_wilson_hi",
                      "p_clean_n", "p_clean_k",
                      "p_clean_excluded", "p_clean_rate",
                      "p_clean_wilson_lo", "p_clean_wilson_hi",
                      "clean_given_go_n", "clean_given_go_rate",
                      "clean_given_go_wilson_lo", "clean_given_go_wilson_hi",
                      "commitment_n", "commitment_rate", "commitment_wilson_lo",
                      "commitment_wilson_hi", "competence_n", "competence_rate",
                      "competence_wilson_lo", "competence_wilson_hi",
                      "red_light_rate", "mean_score_composed",
                      "median_score_composed", "mean_duration_s",
                      "n_unattributed", "p_go_clean_risk_set_match"]
    _write_csv(args.out / "flow_per_variant.csv", per_variant, variant_fields)

    if per_variant_ref:
        # Reference (oracle) curve, kept OUT of the pooled aggregate (issue #2).
        _write_jsonl(args.out / "flow_reference_per_variant.jsonl", per_variant_ref)
        _write_csv(args.out / "flow_reference_per_variant.csv", per_variant_ref,
                   variant_fields)

    if args.by_model:
        # per-model spans ALL models (reference included) so the oracle stays
        # inspectable even though it is out of the pooled curve.
        per_variant_model = aggregate(rows, ("index", "model"), frac)
        _write_jsonl(args.out / "flow_per_variant_model.jsonl", per_variant_model)
        _write_csv(args.out / "flow_per_variant_model.csv", per_variant_model,
                   ["model"] + variant_fields)

    # Realized flow kinematics per (variant, seed) - the honest measurement unit:
    # the flow is deterministic within a seed, so this is the exact realized
    # value, and the seed spread shows how much the seed alone moves the flow.
    per_variant_seed = aggregate(rows, ("index", "seed"), frac)
    realized_fields = ["index", "seed", "swept_factor",
                       "flow_speed", "measured_flow_speed", "flow_speed_residual",
                       "spacing", "measured_spacing_m", "spacing_residual_m",
                       "crossing_time_gap_s",
                       "measured_crossing_time_gap_s", "gap_residual_s",
                       "n_flow_cars_median", "n_runs"]
    _write_csv(args.out / "flow_realized_per_seed.csv", per_variant_seed,
               realized_fields)
    _write_jsonl(args.out / "flow_realized_per_seed.jsonl", per_variant_seed)

    # figure_data: curve points for plotting (gap ascending). Carries BOTH the
    # nominal axis and the measured gap so a plot can use the realized x where
    # they diverge (issue #1); axis_divergent flags points to treat with care.
    figure_data = {
        "family": family,
        "axis": "crossing_time_gap_s",
        "axis_note": ("x = NOMINAL crossing_time_gap_s (= spacing/nominal speed). "
                      "measured_crossing_time_gap_s is the realized value; where "
                      "axis_divergent the flow congested and the point is "
                      "mis-placed on the nominal axis - prefer the measured x."),
        "pooled_models": pooled_models,
        "reference_models": sorted(reference_set & present),
        "n_runs": sum(r["n_runs"] for r in per_variant),
        "points": [
            {"index": r["index"], "crossing_time_gap_s": r["crossing_time_gap_s"],
             "measured_crossing_time_gap_s": r["measured_crossing_time_gap_s"],
             "axis_divergent": r["axis_divergent"],
             "gap_residual_abs_max_s": r["gap_residual_abs_max_s"],
             "flow_speed": r["flow_speed"],
             "measured_flow_speed": r["measured_flow_speed"], "n": r["n_runs"],
             "collision_rate": r["collision_rate"],
             "collision_ci": [r["collision_wilson_lo"], r["collision_wilson_hi"]],
             "stall_rate": r["stall_rate"],
             "stall_ci": [r["stall_wilson_lo"], r["stall_wilson_hi"]],
             "p_go_rate": r["p_go_rate"], "p_go_n": r["p_go_n"],
             "p_go_ci": [r["p_go_wilson_lo"], r["p_go_wilson_hi"]],
             "p_clean_rate": r["p_clean_rate"],
             "p_clean_n": r["p_clean_n"],
             "p_clean_ci": [r["p_clean_wilson_lo"],
                                 r["p_clean_wilson_hi"]],
             "clean_given_go_rate": r["clean_given_go_rate"],
             "clean_given_go_n": r["clean_given_go_n"],
             "clean_given_go_ci": [r["clean_given_go_wilson_lo"],
                                   r["clean_given_go_wilson_hi"]],
             # compatibility aliases
             "commitment_rate": r["commitment_rate"],
             "commitment_n": r["commitment_n"],
             "commitment_ci": [r["commitment_wilson_lo"],
                               r["commitment_wilson_hi"]],
             "competence_rate": r["competence_rate"],
             "competence_n": r["competence_n"],
             "competence_ci": [r["competence_wilson_lo"],
                               r["competence_wilson_hi"]],
             "red_light_rate": r["red_light_rate"],
             "mean_score_composed": r["mean_score_composed"]}
            for r in sorted(per_variant,
                            key=lambda x: (x["crossing_time_gap_s"]
                                           if x["crossing_time_gap_s"] is not None
                                           else math.inf))
        ],
    }
    (args.out / "flow_figure_data.json").write_text(
        json.dumps(figure_data, indent=2) + "\n")

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
