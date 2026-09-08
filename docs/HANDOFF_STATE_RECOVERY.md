# Handoff: state recovery (construct C)

Rewritten 2026-09-08. Everything below is **uncommitted working-tree state**. Nothing has been
pushed.

The family's own record - site, envelope, parameters, results, caveats, certification steps - lives
in
[`failure_boundary_profiling_splits/fm_006_recovery_oncoming_car/README.md`](../failure_boundary_profiling_splits/fm_006_recovery_oncoming_car/README.md).
This file is the session-level state: what exists, what was removed, where things run, and what to do
next. Do not duplicate the README here.

---

## 1. What this construct is

Displace the ego to a parameterised out-of-distribution pose relative to its own route, hand control
straight back, and measure whether the agent re-acquires the route. It is the only construct in the
portfolio that varies the **ego's state** rather than the world.

It exists because driving score conflates *can't* with *won't*. A frozen agent and an incapable agent
both score badly; this construct is meant to separate them.

---

## 2. Cleanup of 2026-09-08 - read this first

The construct had accumulated four families. **Three were deleted.** One survives.

| Family | Disposition |
|---|---|
| `fm_006_state_recovery` | **deleted** - site 1026 is a divided carriageway; 8 of 12 variants put the ego off the map surface into vegetation. It was evidence about the site, not a recovery measurement. |
| `fm_007_recovery_left` | **deleted** - same unusable site. Its bimodal-freeze finding is confounded by uncontrolled background traffic. |
| `fm_008_recovery_oncoming` | **deleted** - a strict subset of the survivor. |
| `fm_006_recovery_oncoming_car` | **kept.** The only live family. It took the freed `fm_006` slug on 2026-09-08, so a bare `fm_006` in anything older means `fm_006_state_recovery`. |

**Deleting fm_008 lost nothing.** Its route files were identical to the survivor's apart from a
six-line `oncoming_*` block, and `oncoming_enable` defaults to `0`, so running
`fm_006_recovery_oncoming_car`'s routes with `oncoming_enable=0` *is* fm_008. The no-traffic
baseline is one parameter away. Its numbers are recorded in that family's README and its 17/17 run
data is untouched at
`fbp_runs/fm_008_notraffic_gpu/`.

`fm_001` through `fm_005` (construct A) were **not touched** - they are committed, certified, and
outside the scope of this construct.

Consequent edits made in the same pass:

- `analysis/curves.py:48` - `FAMILY_METRIC` key repointed from the deleted `fm_006_state_recovery`
  to the live `fm_006_recovery_oncoming_car` (same slug prefix, different family); the docstring table above it updated to match.
- `analysis/boundary_stats.py:77` - `ORIENTATION_FALLBACK` comment repointed to the live family.
  The entry itself is keyed by metric (`lateral_displacement`), not by family, so no behaviour
  changed.
- `docs/CANDIDATE_AXES.md` section C - build-status table collapsed to one family, portfolio table
  updated, wiring caveat rewritten, and the historical results kept but marked as belonging to
  deleted families.
- the surviving family's README rewritten to be **self-contained**; it previously linked to fm_008's README for
  the site, envelope, background-traffic and certification sections.

**The wiring caveat still stands in a narrower form.** `curves.py` now names the surviving family
rather than a deleted one, but the family is still `status=pending`, so an analysis-layer key exists for
an uncertified family. It is inert - `score_runs.py` emits no row, so `curves.py` never sees one -
and it is not an assertion that the family is certified.

---

## 3. What the 2026-09-08 session found

**The sweep was interrupted at 14 of 17.** v15, v16 and v17 were never run. The previous
handoff claimed the controlled-traffic build was recorded in "section 14"; that section was never
written, and the run was never analysed.

**Analysed now, and it is a confirmed null.** Comparing the 14 sidecars against the no-traffic
baseline point for point: **13 of 14 variants reproduce it to within 0.01 m**, mean delta -0.029 m
over the 11 points above the measurement floor, the single exception being v14 at -0.36 m. Zero
collisions in all 14. Durations 22.40-23.35 s against the baseline's 22.40-23.25 s. The only
infraction anywhere is `outside_route_lanes`, which is the injected displacement being charged to the
agent.

That includes v11-v14, the regime where the car crosses the ego's return path - the one geometry
this family was built to reach.

**Why:** the agent re-acquires in ~0.7 s; the car needs 60 m / 14 m/s = 4.3 s to arrive. The ego is
back in its own lane about three and a half seconds before the encounter. As parameterised, the family
adds a car to the scene without putting it in the way.

The v07 smoke test had already predicted this (4.37 m with the car against 4.38 m without). The full
sweep confirms it across the whole axis rather than at one point.

**This is a result, not a failure.** It is the null control: merely having a vehicle present changes
nothing. It is just not a boundary.

---

## 4. State of the instrument

Built and exercised on real runs:

| Piece | Where |
|---|---|
| `StateRecovery` scenario + `DisplaceEgo` atomic | `scenario_runner/srunner/scenarios/state_recovery.py:493`, `:241` |
| `surface_envelope()` achievability gate | same file, `:87` |
| `RouteReacquisitionTest` criterion | same file, `:318` |
| Displacement sidecar | same file, `:444` |
| Controlled oncoming vehicle (`oncoming_*`, `lane_across`, `place_ahead`) | same file, `:590`+ |
| `FBP_NO_BACKGROUND` harness switch | `patches/0003-*.patch`, both `route_scenario.py` copies |
| Site survey | `tools/survey_recovery_sites.py` |
| Family runner | `tools/run_recovery_family.sh` |

Not built: **the recoverability oracle.** Still the blocker on certifying anything in this construct.

---

## 5. Measurement bugs already fixed - do not reintroduce

1. **Route index initialisation.** `RouteReacquisitionTest._offset_to_route` searches a window
   `[index-5, index+60]`. The index started at 0 while the scenario activates ~90 m into the route,
   so tick 1 reported the distance to the *start of the route*, latched `_displaced` before anything
   moved, and then measured the index catching up. Since `_reacquired` also latches, the real
   displacement was never measured. Signature: the 0.0 m control reporting exactly 1.66 m in two
   independent sweeps. Fixed by seeding the index with a full-route search on the first tick.

2. **Sidecar filename collision.** `FBP_DISPLACEMENT_NAME` is required because `config.name` is the
   *scenario* name from the route XML, identical across every variant - keying on it overwrites one
   file per family. That bug cost a full 12-route sweep.

3. **Centreline sign flip in `surface_envelope()`.** Crossing the centreline means the neighbour
   faces the other way, so "further out" becomes its *right*. Without tracking the lane-id sign flip
   the walk turns round at the centreline and reports an undivided road as half its width - it read
   4.88 m instead of 9.26 m on road 280. Caught by a smoke test.

4. **`place_ahead()` direction.** `.next(gap)` places the oncoming vehicle *behind* the ego on road
   280; the accessor that walks toward the ego is `.previous(gap)`. Which one it is depends on how
   OpenDRIVE oriented that lane, so `place_ahead()` takes both candidates and keeps whichever lies
   ahead along the route heading, deriving the spawn heading from the same dot product. Do not
   reintroduce the convention.

**Still open, deliberately not changed** - a measurement-definition call for a human:
`_offset_to_route` uses **nearest-route-point** distance, not perpendicular distance to the route
polyline. Against a 1.0 m-interpolated route that inflates the reported offset, but it is a **floor
effect, not proportional inflation**: `peak - requested` is +0.50 m at 0.0 m, +0.15 at 0.5 m, +0.07
at 1.0 m, and never more than +0.05 m at or above 1.5 m. It biases only the near-zero end, which
`reacquire_distance = 1.5 m` has already put below the instrument's floor.

---

## 6. Environment

### The GPU host - where to run

| | |
|---|---|
| CARLA 0.9.15 with Town13 | `/home/ekirby/workspace/carla_garage/carla` |
| GPUs | 8x RTX 2080 Ti, NVIDIA Vulkan ICD at `/etc/vulkan/icd.d/nvidia_icd.json`, so `-RenderOffscreen` renders on the GPU |
| Python | built from scratch at `/home/ekirby/workspace/envs/fbp310` |

There was no usable interpreter on the host: no conda, the sibling repos' `.venv`s point at an
`/opt/conda` that does not exist in this container, and system Python is 3.6/3.8 while
`team_code/requirements.txt` needs 3.10. Rebuilt with `uv`. Two deliberate deviations:

- **`pyQt6` dropped** - builds from source and needs `qmake`, which is absent. Only `toolbox/` uses
  it, not the evaluator or the agent.
- **torch/torchvision/torchaudio 2.5.0 from the CPU index.** PDM-Lite imports torch transitively
  through `team_code/transfuser_utils.py` but never runs a network on `--track MAP`. A sensor agent
  would need the CUDA build.

```bash
export CARLA_ROOT=/home/ekirby/workspace/carla_garage/carla
export PYTHON=/home/ekirby/workspace/envs/fbp310/bin/python
source fbp_env.sh
${CARLA_ROOT}/CarlaUE4.sh -RenderOffscreen -nosound -carla-rpc-port=2000 &
```

Town13 loads per route and is slow: ~2 minutes of load plus a 0.21x realtime ratio, so a 22 s route
costs about 8 minutes wall-clock. Budget ~2.5 h for a 17-variant sweep.

### Check which host you are on

**As of 2026-09-08 the working container has no GPU** - no `/dev/nvidia*`, no Vulkan ICD, no
`nvidia-smi`. CARLA falls through to lavapipe, Mesa's software rasterizer, at ~0.09x realtime.

This does **not** invalidate PDM-Lite runs: it drives `--track MAP` from privileged simulator state
and never reads a rendered frame. Verified - fm_008 v01/v02 reproduce the software-rendered runs
exactly. It **would** invalidate sensor-agent runs (TFv6, BridgeDrive), whose camera images would
come from a software rasterizer at `-quality-level=Low`.

On a GPU-less host `-quality-level=Epic` segfaults the server on Town13; use `-quality-level=Low`.

---

## 7. Gotchas

- `tools/run_recovery_family.sh` loops over routes in one process group; to stop it, kill the
  **process group** of the parent bash, not the evaluator - the loop just launches the next route.
- `analysis/extract_recorders.py` must run **while a server is up**; the recordings are useless
  without it later. It needs the world in a sane state - the leaderboard leaves the server in
  synchronous mode after the last route, and a `load_world` against that segfaulted the server once.
  Restart the server rather than trying to reset it. **The 14 completed runs have no extracts pulled.**
- `failure_boundary_profiling_splits/vehicle_extents.json` was measured 2026-09-03
  (`tools/measure_blueprint_extents.py --blueprint-filter 'vehicle.*'`, CARLA 0.9.15, 41/41
  blueprints, 0 failures). `vehicle.lincoln.mkz_2020` measures (2.4462, 0.9184), matching the
  `ego_half_length_m` 2.45 countersigned in every `attribution.json`. Regenerate it against the CARLA
  build a campaign actually ran on.
- **The trajectory layer skips object-free runs.** `compute_row` collects actors with
  `role_name == "scenario"` and returns `None` when there are none
  (`analysis/trajectory_metrics.py:261-263`). This family spawns a `scenario`-roled car, so its runs are
  processed; running with `oncoming_enable=0` reintroduces the gap. `compute_row` has already
  projected the ego onto the route axis and computed per-frame station and lateral offset (`:234-237`)
  before it bails - which *is* the re-acquisition measurement this construct wants.

---

## 8. What to do next

1. **Sweep the gap downward.** This is the main line. 60 m is a confirmed null; the informative
   range is **10-20 m**, because the car has to arrive inside the ~0.7 s recovery window. Deeper
   displacements recover more slowly (v17 needs ~1.7 s, so ~24 m), so the informative gap scales
   with displacement. Author it as a gap sweep at a fixed displacement in the head-on regime
   (v07's 3.0 m keeps it directly comparable to an existing measured point), or as a 2-D
   displacement x gap grid if the run budget allows. The occupancy gate does **not** block small
   gaps - it is a footprint separating-axis test, not a 25 m exclusion radius.

2. **Finish or retire v15-v17.** The 60 m sweep is 14/17. Either complete it so the null control is
   citable as a whole, or record it as deliberately partial. Three points of a known null is low
   value; the decision is whether the control needs to be complete.

3. **Pull recorder extracts for the 14 completed runs**, or accept that the trajectory layer will never
   see them. Needs a running server.

4. **Build the recoverability oracle.** Still the blocker on certifying anything. This family poses it
   better than the no-traffic baseline could: **"was there time to clear?"** is answerable from
   geometry and speed, unlike "was recovery achievable?". Given the displaced pose, the car's lane,
   gap and speed, and a bound on ego lateral acceleration, whether a clean return existed is a
   kinematics problem with an agent-independent answer - much closer to the geometric oracles
   constructs A and B enjoy. Run it under `FBP_NO_BACKGROUND=1`.

5. **Decide the two open policy calls.** Both block turning this construct into curves rather than
   tables:
   - Should `trajectory_metrics.compute_row` emit ego-only metrics for object-free families? It is a
     direct port of the internal scorer with documented 96/96 fidelity, so this is a policy decision,
     not a quiet patch.
   - Should `declares_empty` be widened to "places no object requiring positional attribution", or
     should scenario-controlled moving actors get a separate flag? The family places a moving vehicle and
     declares empty; the code does the right thing and the docstring does not describe the case.

6. **Lower `reacquire_distance`, or drop v01-v03 as points.** At 1.5 m the first three variants sit
   below the instrument's floor and report 0.0 m driven by construction. Three of 17 points carrying
   no information is cheap to fix in the next authoring pass.

7. **Multiple seeds.** Still n=1 per variant. Buys an error bar on the
   `driven = 1.244 * offset + 0.269` slope and confirmation that "recovers from everything" is not a
   one-seed accident. Worth doing before quoting the slope anywhere.

---

## 9. Uncommitted files

Modified: `.gitignore`, `analysis/{boundary_stats,curves,score_runs,trajectory_metrics}.py`,
`docs/CANDIDATE_AXES.md`, `leaderboard/leaderboard/scenarios/route_scenario.py`,
`scenario_runner/srunner/scenarios/route_scenario.py`,
`toolbox/scripts/{config,scenario_selection_dialog}.py`

New: `fbp_env.sh`, `docs/HANDOFF_STATE_RECOVERY.md`, `patches/0003-*.patch`,
`scenario_runner/srunner/scenarios/state_recovery.py`, `tools/run_recovery_family.sh`,
`tools/survey_recovery_sites.py`,
`failure_boundary_profiling_splits/fm_006_recovery_oncoming_car/`,
`failure_boundary_profiling_splits/vehicle_extents.json`

Also modified earlier, unrelated to recovery: the stop-sign sight-line axis was removed
(`analysis/curves.py`, `analysis/boundary_stats.py`, `toolbox/scripts/*`, plus a rejection record in
`docs/CANDIDATE_AXES.md`), and `analysis/trajectory_metrics.py:492` was fixed to unpack
`load_catalogs`'s three return values after `score_runs.py` grew the `declared_empty` set.

Run artifacts are **outside the repo** at `/home/ekirby/workspace/fbp_runs/` and will not transfer
with a git push:

| Dir | Contents |
|---|---|
| `fm_008_notraffic_gpu/` | the complete no-traffic baseline, 17/17, 95 MB - **the reference the live family is read against** |
| `fm_006_oncoming_car/` | the 60 m sweep, 14/17, 79 MB, no extracts pulled |
| `fm_006_pdm/`, `fm_007_left/` | 202 MB each, for the deleted `fm_006_state_recovery` / `fm_007_recovery_left` - candidates for removal |
| `fm_008_notraffic/`, `fm_008_smoke/`, `fm_006_smoke/`, `refusal_test/` | superseded partials and smoke tests |
