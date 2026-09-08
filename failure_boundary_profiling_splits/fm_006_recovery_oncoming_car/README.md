# fm_006_recovery_oncoming_car

**Status: PENDING CERTIFICATION. Not scoreable yet, by design.**

Every row in `variants.csv` carries `status=pending`, so `score_runs.py` skips
the family ("no scoreable records found"). That is the certification gate
working, not a bug.

This is the **only surviving family of construct C** (state recovery). The three
earlier ones were removed on 2026-09-08 and this family took the freed `fm_006`
slug, so **a bare `fm_006` in anything written before that date means
`fm_006_state_recovery`, a different family on a different site.**

| Removed | Why |
|---|---|
| `fm_006_state_recovery` | Site 1026 is a divided carriageway; 8 of 12 variants put the ego off the map surface into vegetation. Evidence about the site, not a recovery measurement. |
| `fm_007_recovery_left` | Same unusable site. Its bimodal-freeze finding is confounded by uncontrolled background traffic. |
| `fm_008_recovery_oncoming` | **Superseded by this family, which is a strict superset.** See below. |

**fm_008 is reproducible from this family.** Its routes were byte-identical to
these apart from the six-line oncoming block, so setting `oncoming_enable=0`
here *is* fm_008. The no-traffic baseline is one parameter away and nothing was
lost by deleting it; its 17/17 run data remains outside the repo at
`fbp_runs/fm_008_notraffic_gpu/`, and its numbers are recorded below.

## What this construct is

The ego is teleported to a parameterised off-route pose, control is handed
straight back to the agent, and the question is whether the agent gets back on
its route. The swept factor is a property of the **ego state**, not of the
world. It is the only construct in the portfolio that varies the ego rather than
the scene.

It exists because driving score conflates *can't* with *won't*: a frozen agent
and an incapable agent both score badly, and this construct is meant to separate
them.

| | |
|---|---|
| Scenario type | `StateRecovery` (`scenario_runner/srunner/scenarios/state_recovery.py`) |
| Town | Town13 |
| Site | road 280, lane -1; trigger `(4403.6, 4636.6, 147.4)` yaw `-89.7`, route index 89 |
| Route | `(4403.2, 4726.6) -> (4404.6, 4406.6)`, 320 interpolated points |
| Primary axis | `lateral_offset`, 0.0 to 8.0 m in 0.5 m steps, **left** of route heading (into oncoming space) |
| Pinned ego | `heading_offset` 0 deg, `handback_speed` 6.0 m/s, `recovery_distance` 120 m, `recovery_timeout` 60 s, `reacquire_distance` 1.5 m, `require_surface` 1, `clearance_patience` 3.0 s |
| **Oncoming car** | `vehicle.tesla.model3`, near oncoming lane (`oncoming_lane=1`), **60 m** ahead, **8 m/s**, released at handback |
| Closing TTC at handback | **4.29 s** (60 m / (8 + 6) m/s) |
| Variants | 17 |

## Why road 280

`tools/survey_recovery_sites.py` scans a town for sites that are straight,
junction-free, wide, and have oncoming lanes to the left. On Town13: 39,481
waypoints -> 527 qualifying roads -> 18 with adequate run length. Road 280 was
the flattest (0.74 m of elevation change; the other wide candidates had 5-10 m
of grade). Straight run -230 / +240 m, heading deviation 0.0 deg.

Cross-section from the ego lane centre, as reported by `surface_envelope()`:

| offset | what is there |
|---|---|
| 0.00 - 1.62 m | own lane |
| **1.62 m** | **centreline** |
| 1.62 - 4.88 m | near oncoming lane |
| 4.88 - 8.12 m | far oncoming lane |
| 8.12 - 9.26 m | shoulders |
| max usable | **8.34 m** (9.26 m surface - 0.92 m ego half-width) |

The axis crosses **qualitative** boundaries - own lane, centreline, near
oncoming, far oncoming - rather than merely going further sideways.
`variants.csv` records which zone each variant lands in.

**v01 at 0.0 m is the teleport artifact control.** The ego is snapped to the
route centreline and nothing else changes. Any behaviour change at v01 is the
displacement mechanism itself - discontinuous dynamics, zeroed then re-injected
velocity, a break in the agent's sensor history - not the axis. Read every other
variant against v01, never against an un-displaced run.

## Why one axis produces three regimes

The car is pinned to the near oncoming lane, whose centre sits 2.97 m left of
the ego lane centre. Sweeping the ego across a fixed car walks through three
qualitatively different geometries without changing anything but the swept
factor:

| variants | offset | ego is | relationship to the car |
|---|---|---|---|
| v01-v04 | 0.0 - 1.5 m | own lane | car passes normally in the oncoming lane - **control** |
| v05-v10 | 2.0 - 4.5 m | near oncoming lane | **head-on**, ego is in the car's lane |
| v11-v17 | 5.0 - 8.0 m | far oncoming lane | car passes **between the ego and its route** - the obstacle is in the recovery path |

The third regime does not exist in any other family here: the thing in the way
is not between the ego and its goal in the travel direction, it is across the
ego's return path.

---

## Results

### The no-traffic baseline (was fm_008; PDM-Lite, seed 0, n=1)

17/17 on 2026-09-03, `FBP_NO_BACKGROUND=1`, into
`fbp_runs/fm_008_notraffic_gpu/`.

**No boundary.** Zero refusals - the surface gate accepted every pose up to 8.0 m
against the 8.34 m usable envelope. Zero collisions of any class, confirmed
independently by the recorder extracts. Route completion 100% everywhere. Game
duration moved 22.40 -> 23.25 s across the whole sweep, against a 60 s recovery
timeout.

Re-acquisition distance is **linear in displacement**:

```
driven = 1.244 * offset + 0.269      R^2 = 0.995, max residual 0.38 m   (fit over offset >= 1.5 m)
```

**There is no step at the 1.62 m centreline.** Fitting the near-oncoming and
far-oncoming segments separately gives slopes of 1.336 and 1.277 - the same
line. Crossing into oncoming space costs PDM-Lite nothing detectable.

Two consequences, and they are why this family exists:

1. The displacement axis alone **does not bound this agent**. PDM-Lite drives
   back from the far oncoming lane as readily as from its own, so the axis
   measures a *cost* (metres to re-acquire), not a *threshold*.
2. A boundary therefore needs a second controlled variable - traffic,
   `heading_offset`, or `handback_speed`. That reframes the construct: traffic
   is the experimental variable, not the nuisance.

### This family at a 60 m gap: a confirmed null (14/17)

Run into `fbp_runs/fm_006_oncoming_car/`, `FBP_NO_BACKGROUND=1`, seed 0.
**The sweep is incomplete - v15, v16 and v17 were never run.**

Re-acquisition distance against the no-traffic baseline, point for point:

| variant | offset | baseline driven | with car | delta |
|---|---|---|---|---|
| v01-v03 | 0.0 - 1.0 m | 0.00 | 0.00 | 0.00 (below the floor, see caveats) |
| v04 | 1.5 m | 1.82 | 1.82 | 0.00 |
| v05 | 2.0 m | 2.73 | 2.73 | 0.00 |
| v06 | 2.5 m | 3.36 | 3.37 | +0.01 |
| v07 | 3.0 m | 4.38 | 4.39 | +0.01 |
| v08 | 3.5 m | 4.72 | 4.73 | +0.01 |
| v09 | 4.0 m | 5.45 | 5.46 | +0.01 |
| v10 | 4.5 m | 5.79 | 5.79 | 0.00 |
| v11 | 5.0 m | 6.55 | 6.55 | 0.00 |
| v12 | 5.5 m | 6.91 | 6.91 | 0.00 |
| v13 | 6.0 m | 7.67 | 7.67 | 0.00 |
| v14 | 6.5 m | 8.39 | 8.03 | **-0.36** |
| v15-v17 | 7.0 - 8.0 m | 8.76 / 9.53 / 10.40 | *not run* | - |

Mean delta over offset >= 1.5 m: **-0.029 m** across 11 points. Zero collisions
in all 14. Route completion 100%, durations 22.40-23.35 s against the baseline's
22.40-23.25 s. The only infraction anywhere is `outside_route_lanes`, which is
the injected displacement being charged to the agent.

**Thirteen of fourteen variants reproduce the no-traffic baseline to within a
centimetre**, including v11-v14, the regime where the car crosses the ego's
return path. At this parameterisation the car is not in the way and the family
measures nothing the baseline did not.

**This is a real result, not a failed run.** It establishes that merely having a
vehicle present changes nothing, which is the null control any informative gap
has to be read against. But it is not a boundary.

### Why 60 m was the wrong gap

The agent re-acquires in roughly **0.7 s** after handback (4.39 m driven at
6 m/s at v07). The car needs 60 m / 14 m/s = **4.3 s** to arrive. The ego is
therefore back in its own lane about three and a half seconds *before* the
encounter, and the closest approach is an ordinary lane-to-lane pass rather than
a conflict during recovery.

To make the encounter coincide with the recovery, the car has to arrive inside
the recovery window. At 14 m/s closing that means a gap on the order of
**10-20 m**, not 60 - and because deeper displacements recover more slowly
(v17 needs ~1.7 s, so ~24 m), the informative gap scales with the displacement.

**The next parameterisation is the gap, swept downward from about 25 m.** Keep
the 60 m sweep as the null control it turned out to be.

---

## The car is deliberately not a driver

`WaypointFollower(..., avoid_collision=False)`. It holds its lane and its speed
and does **not** react to the ego. An oncoming vehicle that braked or swerved
for the displaced ego would make this a measurement of two policies interacting,
and the authored TTC would stop meaning anything. What is measured is the ego's
behaviour against a known, fixed, closing threat.

It is also held **stationary until handback** (`oncoming_start=handback`), so the
gap when the agent takes over is exactly the authored 60 m rather than whatever
it decayed to during the ~90 m of route before the trigger. Less physical, more
controlled - the right trade for a swept factor. `oncoming_start=init` releases
it at scenario start if a physically continuous approach is ever wanted more
than an exact gap.

## Placement is derived, not assumed

`lane_across()` walks the OpenDRIVE lane graph sideways with the same
centreline-sign-flip handling as `surface_envelope()`, so `oncoming_lane=1` means
"the first surface lane past the centreline" regardless of how the road is
authored.

`place_ahead()` then takes **both** `.next(gap)` and `.previous(gap)` from that
lane waypoint and keeps whichever actually lies ahead of the ego along the route
heading. This is not defensive coding for its own sake: an oncoming lane runs
against the route, so which accessor means "toward the ego" depends on how
OpenDRIVE happened to orient that lane. Measured on road 280 it is
**`.previous()`** - the opposite of what the original design note assumed. The
spawn heading is derived the same way, from the dot product with the ego's
forward vector, rather than trusting a convention. **Do not reintroduce the
assumption.**

Verified on road 280: lane id 1, spawn `(4400.63, 4576.59, 147.52)`, measured gap
**+60.00 m** along the route, `crossed_centreline: true`, reproduced identically
across all 14 runs.

Refusal, not approximation, on the same principle as the displacement itself: if
the lane does not exist or the road runs out within the gap, the placement is
recorded as `refused` with a reason rather than nudged to somewhere that fits.

## The displacement is refused, never clamped

`require_surface=1` makes `surface_envelope()` refuse any pose whose ego body
would leave contiguous surface. Occupancy is checked separately by a
separating-axis test against every vehicle and walker within 25 m, waiting up to
`clearance_patience` for the target to clear. A refusal is recorded in the
sidecar and sets `RouteReacquisitionTest` to ACCEPTABLE/0.0 with its update loop
suppressed, so the ego's ordinary wander can never be scored as a recovery that
was not requested.

Clamping to a smaller offset would change the swept factor without changing the
catalog - the one failure mode this construct cannot tolerate.

`surface_envelope()` tracks lane-id sign flips, because crossing the centreline
means the neighbour faces the other way and "further out" becomes its right.
Without that the walk turns round at the centreline and reports an undivided road
as half its width. *That bug was live and was caught by a smoke test* - the gate
read 4.88 m instead of 9.26 m on this road.

The site, not `InRouteTest`, is the binding constraint: the sweep stops at 8.0 m
because the road surface does, well inside the 15 m at which out-of-route
distance starts accumulating and the 30 m at which `InRouteTest` terminates the
route with `ROUTE_DEVIATION` (`route_scenario.py:430-431`). Displace laterally or
forward, never backwards along the route: `InRouteTest` only searches forward
from its running index (`atomic_criteria.py:1466-1468`).

## Background traffic: `FBP_NO_BACKGROUND=1` is mandatory

The point of this family is that the authored car is the **only** vehicle in the
world. Without the flag it is one vehicle among a background population and the
experiment is meaningless.

There is no route-file way to control traffic. `BackgroundBehavior` spawns its
road actors in `initialise()` before any scenario trigger fires, and
`BackgroundActivityParametrizer` only caps later additions rather than
despawning. Worse, zeroed counts feed `_get_road_radius()`
(`background_activity.py:298-306`) and collapse the speed-control radius to one
spawn distance: measured on Town13, the same ten vehicles remained but mean speed
**halved, 16.43 -> 7.68 m/s**, turning moving traffic into crawling obstacles. A
route file that looks like it disables traffic actually produces the worst
available condition. These routes therefore carry **no** `BackgroundOff` block.

`FBP_NO_BACKGROUND=1` (`patches/0003-*.patch`, applied to both
`route_scenario.py` copies) omits the background activity at behaviour-tree
construction. Verified: traffic census `{15 m: 0, 30 m: 0, 60 m: 0}` with the
authored car the only entry, zero collisions, 100% route.

**Caveat:** the flag removes junction and opposite-flow activity too, not just
same-lane road vehicles. It is an ablation of *all* background activity, not
"your lane emptied". Campaigns run with and without it are not comparable, so
record the flag state with the campaign.

## How to run

```bash
export CARLA_ROOT=/path/to/CARLA_0.9.15
export PYTHON=/path/to/python-with-carla-wheel
source fbp_env.sh
${CARLA_ROOT}/CarlaUE4.sh -RenderOffscreen -nosound -carla-rpc-port=2000 &

NO_BACKGROUND=1 \
FAMDIR=$PWD/failure_boundary_profiling_splits/fm_006_recovery_oncoming_car \
OUT=<run dir>/pdm_lite_expert/seed_0 SEED=0 \
tools/run_recovery_family.sh \
  Generalization_StateRecoveryOncomingCar_0280_006_lateral_sweep_v{01..17}
```

Then, **while the server is still up**:

```bash
python -m analysis.extract_recorders <OUT> --port 2000   # recordings are useless without this
python -m analysis.score_runs <OUT>/.. --out scored/     # refuses while status=pending
python -m analysis.trajectory_metrics <OUT> --out metrics/
```

To reproduce the no-traffic baseline, run the same routes with
`oncoming_enable=0`.

## What is measured

- **Terminal outcome**, via the existing taxonomy - what feeds the commitment and
  competence curves once the family is certified.
- **Re-acquisition distance**, via `RouteReacquisitionTest`: metres driven
  between handback and the first tick whose lateral offset to the route is under
  `reacquire_distance`. Live only while the scenario is active, so the injected
  displacement is never charged to the agent.
- **The displacement sidecar.** The leaderboard checkpoint JSON carries only
  `TrafficEvent`s (`statistics_manager_local.py:371-375`), so a custom
  criterion's `test_status` and `actual_value` never reach `score_runs.py` at
  all. Set `FBP_DISPLACEMENT_LOG=<dir>` and `FBP_DISPLACEMENT_NAME=<scenario_id>`
  and the criterion writes a per-run JSON with the requested offset,
  displaced/refused plus reason, surface reach and the lane list it walked,
  reacquisition status and driven metres, peak offset, a traffic census at
  handback, and the `oncoming` block including the spawned `actor_id` and
  `measured_gap_m`.

  `FBP_DISPLACEMENT_NAME` is **not optional**: `config.name` is the scenario name
  from the route XML and is identical across all 17 variants, so keying on it
  overwrites one file per family and loses the sweep. That bug cost a full
  12-route sweep once.

`tools/run_recovery_family.sh` sets both variables and runs one evaluator
invocation per route, so `score_runs.py` sees one `<scenario_id>.json` per
variant.

## Attribution - read this, it is a contract question

`attribution.json` carries `declares_empty: true` **and this family places a
vehicle**, so the flag is used in a wider sense than the wording in
`score_runs.py` ("asserts that its scenarios place no objects at all").

The reasoning: `declares_empty` exists to distinguish *"the list is empty because
nothing is placed"* from *"the list is empty because someone failed to measure
what is placed"*. For a **moving** actor, positional attribution is inapplicable
in principle rather than merely unmeasured - the car is not at its placed pose
when a contact happens, so a countersigned pose would never match and every
contact would report `ambiguous`. The empty list is still a declaration, not
missing data.

What identifies the car instead:

- **`oncoming.actor_id` in the sidecar** - recorded per run, so analysis can join
  a collision to the experimental variable directly.
- **`FBP_NO_BACKGROUND=1` being mandatory** - it is the only vehicle in the
  world, so `traffic_collisions >= 1` identifies it uniquely with no join at all.

**The decision this needs from a human:** either widen the `declares_empty`
wording in `score_runs.py` to "places no object requiring positional
attribution", or add a separate flag for scenario-controlled moving actors. The
code does the right thing; the docstring does not describe this case.

## Certification

Half of achievability is solved and half is not, and the difference is the whole
certification question.

**Solved - was the pose legal?** `surface_envelope()` is exact map data:
agent-independent and available before the run.

**Not solved - was recovery achievable?** Nothing in the scene description says
whether a competent driver gets back on route from 8 m lateral. Until something
does, a failure to recover cannot be attributed to commitment rather than
competence.

This family poses that question better than the no-traffic baseline could.
**"Was there time to clear?"** is answerable from geometry and speed, unlike "was
recovery achievable?". Given the displaced pose, the car's lane, gap and speed,
and a bound on ego lateral acceleration, whether a clean return existed is a
kinematics problem with an agent-independent answer. That is much closer to the
geometric oracles constructs A and B enjoy, and it is the strongest argument for
this family being the one that gets certified.

Steps:

1. **Run the recoverability oracle.** For each variant, drive the displaced pose
   with a reference controller that is *not* the agent under test - CARLA's
   `BasicAgent`, or a scripted return trajectory - and record whether it
   re-acquires. Not yet built.
2. **Update `variants.csv`.** Set `status=accepted`, fill `lateral_displacement`
   with the *achieved* displacement measured in simulation (the target is
   authored; the measurement is what curves plot), fill `oncoming_measured_gap`
   from the sidecar's `measured_gap_m`, and set `assigned_family` to
   `passable_static_obstacle` where the oracle recovered and
   `full_static_blockage` where it did not.

The `assigned_family` labels reuse the existing vocabulary deliberately, so no
scorer change is needed: `score_runs.py:299` reads them into `passable`, and the
outcome taxonomy then separates a genuine refusal-to-recover (`refusal`, a
commitment gap) from a correct refusal on an unrecoverable pose (`correct_stop`).

Note the baseline makes step 1 easier here: a reference controller only has to
beat a bar PDM-Lite cleared at every point, so every variant at this gap is very
likely to label `recoverable_displacement`. The oracle's real work starts
wherever the axis is extended - a smaller gap, heading error, or handback speed.

`analysis/curves.py:48` and `analysis/boundary_stats.py` are wired to this
family on `lateral_displacement`. **The wiring is inert while `status=pending`** -
`score_runs.py` emits no row, so `curves.py` never sees one. It is pointed here
rather than at a deleted family, not an assertion that the family is certified.

## Known caveats

- **v01-v03 are below the instrument's floor, not instant recoveries.**
  `reacquire_distance` is 1.5 m, so a displacement under it is already "on route"
  at handback and the criterion reports 0.0 m driven by construction. The usable
  axis starts at v04. Either lower `reacquire_distance` in the next authoring
  pass or treat the first three variants as controls rather than points.
- **The sweep is 14/17.** v15-v17 were never run.
- **`_offset_to_route` uses nearest-route-point distance**, not perpendicular
  distance to the route polyline. Against a 1.0 m-interpolated route that
  inflates the reported offset - but it is a **floor effect, not proportional
  inflation**: `peak - requested` is +0.50 m at 0.0 m, +0.15 at 0.5 m, +0.07 at
  1.0 m, and never more than +0.05 m at or above 1.5 m. It biases only the
  near-zero end, which `reacquire_distance` has already put below the floor.
  Changing it alters what `actual_value` means, so it is a measurement-definition
  call for a human rather than a fix.
- **Route-level criteria charge the agent for the injected displacement.** Every
  variant from v05 up reports one `outside_route_lanes` infraction that the agent
  did not cause. `RouteReacquisitionTest` is unaffected; the leaderboard DS is
  not.
- **The occupancy gate is not a 25 m exclusion radius**, so small gaps are legal.
  `occupying_actors` uses 25 m only as a broad-phase cull and then does a
  separating-axis test of the actual footprints, so a car 10 m away does not
  overlap the displacement target and does not trigger "destination occupied". A
  refusal only happens on a real body overlap. This is what makes the 10-20 m gap
  sweep authorable.
- **One gap and one speed.** Displacement x TTC is the 2-D version and is the
  obvious follow-up; this family fixes TTC so it stays directly comparable to the
  baseline point for point.
- **n=1 per variant, single seed.** Per `docs/RUNNING.md` section 5, single runs
  are anecdotes. Treat everything above as hypothesis-generating.
- **The trajectory layer works here but not for the baseline.** `compute_row`
  collects actors with `role_name == "scenario"` and returns `None` when there
  are none (`analysis/trajectory_metrics.py:261-263`), so it skips every
  object-free run. Because this family spawns a `scenario`-roled car, its runs are
  processed. Running with `oncoming_enable=0` reintroduces the gap.
