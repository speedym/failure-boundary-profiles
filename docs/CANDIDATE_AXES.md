# Candidate sweep axes

Working notes on factors we could sweep beyond the five families currently in
[`failure_boundary_profiling_splits/`](../failure_boundary_profiling_splits). Nothing here has been
generated, validated, or measured - these are proposals, with the harness knob for each one located
and its semantics verified against the vendored tree at `bceb18a`. Line references are to that tree.

Certification status of every axis below: **none**. The generation pipeline validates candidate
scenes in simulation before acceptance (see the refusal rows in `fm_003_asset_swap/variants.csv`);
none of these have been through it.

## What the current five families already cover

All 60 accepted variants across fm_001 to fm_005 are the same scenario underneath:

| Property | Value across all 60 routes |
|---|---|
| Scenario type | `CustomObstacle` (single type, no exceptions) |
| Town | Town13 |
| Weather block | byte-identical (clear, `sun_altitude_angle="45"`, `fog_density="2.0"`) |
| Trigger points | 3 distinct |
| Obstacle physics | disabled - `set_simulate_physics(False)`, `construction_crash_vehicle.py:604` |
| Swept factor | lateral `y` offset (fm_001, 002, 004, 005) or asset identity at fixed offset (fm_003) |

So the portfolio answers one question: *how narrow a gap will the agent squeeze through before it
stops.* Static, lateral, geometric. Every axis below is a different question.

Failure is also currently **one-sided** in all five families: the agent either hits the obstacle or
stops. There is no way to be wrong in the other direction.

---

## Tier 1 - same generator, one route-XML field, new axis

Cheapest to build: reuse fm_001 geometry and the existing authoring path, change one field.

### T1-A. Reaction distance

Sweep the trigger-to-obstacle distance at fixed, known-passable geometry.

- Knob: `<distance value="..."/>` on the `CustomObstacle` scenario block.
- Code: `construction_crash_vehicle.py:80` (`get_value_parameter(config, 'distance', float, 100)`);
  consumed at `:348` via `self._reference_waypoint.next(self._distance)`.
- Current families hardcode `distance=50`; the harness default is 100.

Why it matters: the current profiles measure *what* the agent will pass, never *when* it decides.
Sweeping range separates perception-limited failure (fails even at 100 m, where the obstacle is
plainly visible and there is ample room) from planner-limited failure (passes at 100 m, fails at
25 m). fm_001 to fm_005 structurally cannot make that distinction.

**Correction to an earlier note:** `<speed value="..."/>` on the same scenario block does *not*
sweep ego approach speed. It feeds `SetMaxSpeed` (`construction_crash_vehicle.py:256`), which writes
the `BA_SetMaxSpeed` blackboard key (`tools/background_manager.py:90`) and caps the **background
activity** vehicles (`background_activity.py:2518-2519`). There is no per-route ego speed knob; map
speed limits come from `scenario_runner/speed_limits/*.npy` and are map data, not route data. Ego
approach speed is therefore a *site* property - see T3-A.

### T1-B. Visibility

Sweep atmospherics at fixed geometry.

- Knob: the `<weather>` entries already present in every route XML, interpolated across the route by
  `route_percentage`. No scenario-block change, no harness change.
- Fields: `fog_density`, `fog_distance`, `precipitation` + `precipitation_deposits` + `wetness`, and
  `sun_altitude_angle` (low sun for glare; negative for night).

Why it matters: all 60 current routes are byte-identical here, so visibility is an entirely
unmeasured dimension. The high-value version is not a standalone family but a **re-run of the fm_001
intrusion sweep at 2-3 fog levels**, which turns a threshold *point* into a threshold *surface*:
does the intrusion boundary shift smoothly under fog, or does the agent fail catastrophically well
before its clear-weather threshold? That is a much stronger claim than any additional single-factor
sweep.

### T1-C. Vertical extent and orientation

- Knobs: `z`, `pitch`, `roll`, `yaw` in the `<objects a="id=... />` spec.
- Code: parsed at `construction_crash_vehicle.py:584-599`.
- Physics is disabled, so props can be floated and tilted deterministically - none of the tip-over
  or roll-away refusals that cost 9 of 23 assets in fm_003.

Two sub-axes:

1. **Vertical clearance** (`z`): at what height does an object stop being treated as blocking? A
   genuinely different decision from lateral fit.
2. **Yaw de-confounding**: fm_003 varies `yaw` between 0 and 90 across assets, entangled with asset
   identity. A yaw-only sweep on a single asset (`vendingmachine`, already pinned by fm_005)
   separates silhouette from identity at near-zero authoring cost.

---

## Tier 2 - new scenario type, new class of decision

### T2-A. Oncoming gap during bypass

- Scenario: `CustomObstacleTwoWays` (`construction_crash_vehicle.py:620`), already used 10 times in
  `fail2drive_split/`.
- Knob: `<frequency from=".." to=".."/>`, default `[20, 100]`, feeding `OppositeActorFlow`.
- The scenario deactivates the wrong-direction criterion for the duration of the bypass
  (`SwitchWrongDirectionTest(False)`), so crossing the centreline is not itself penalised.

Today the bypass is free - nothing occupies the left lane in any of the five families. Adding
oncoming traffic makes the agent decide *when* to cross, not just whether it fits. Note this
entangles the gap decision with the obstacle geometry; T2-D below isolates it more cleanly.

### T2-B. Time-to-collision on a moving hazard

- Scenario: `PedestriansOnRoad` (`pedestrian_on_road.py`), used 5 times in `fail2drive_split/`.
- Knobs: `walker_speed` (default 2) and `distance` (default 20), `pedestrian_on_road.py:38-39`.

Every obstacle we currently ship is static with physics off. Sweeping walker speed at fixed crossing
geometry sweeps time-to-collision directly, giving a *braking-decision* boundary rather than an
*avoidance-geometry* one. `VehicleOpensDoorTwoWays` (`distance`, `speed`, `frequency`) and the
`HardBrake` family have the same shape.

### T2-C. Crowd density

- Scenario: `PedestrianCrowd` (`pedestrian_crowd.py`), used 5 times in `fail2drive_split/`.
- Knobs: `pedestrians` (int, default 20), `pedestrian_radius` (20), `pedestrian_center` (40), `side`
  (`pedestrian_crowd.py:55-58`).

A clean scalar for "at what density does forward progress stop". This is the family where fork patch
`0001` (`AgentBlockedTest` min_speed 0.1 -> 0.2 m/s) actually bites: crowd-creeping is precisely the
crawl-forever regime the patch redefines, so the family doubles as evidence for that fork decision.

**Scoring caveat:** `TrafficEventType.VEHICLE_BLOCKED` has no entry in `PENALTY_VALUE_DICT`
(`leaderboard/leaderboard/utils/statistics_manager.py:21-30`). Blocked terminates the route rather
than scaling the score, so the boundary for this family must be defined on the termination event,
not on driving score.

### T2-D. Flow gap acceptance (`EnterActorFlow`)

Deepest-analysed candidate; detailed below.

---

## T2-D in detail: the actor-flow families

`EnterActorFlow` (`actor_flow.py:60`) puts the ego into a junction it must enter across a stream of
oncoming vehicles.

### Why the scenario suits boundary profiling

**The flow does not yield.** Each spawned vehicle gets `ignore_vehicles_percentage(actor, 100)` and
`enable_constant_velocity` (`atomic_behaviors.py:3019-3020`), plus `ignore_lights/signs -> 100`
(`:3021-3022`). The stream proceeds at exactly `flow_speed` regardless of the ego. The merge
decision therefore sits entirely with the ego - no cooperative-traffic contamination, which is the
usual thing that ruins gap-acceptance measurement. `HandleJunctionScenario(clear_junction=True,
clear_ego_entry=True, remove_entries=source_wps)` (`actor_flow.py:126-133`) also clears background
traffic, so the flow is the only traffic present.

**Failure is two-sided.** In route mode the only criterion is `ScenarioTimeoutTest`
(`actor_flow.py:144-149`), so the agent can fail by timing out (too timid, never accepts a gap) or
by colliding (too aggressive). This would be our first family yielding a *safe window*
[t_min, t_max] rather than a single threshold.

### `flow_speed` alone is confounded

`source_dist_interval` is a **distance** interval, not a time one - spawning gates on
`self._source_location.distance(actor_location)` (`atomic_behaviors.py:3046-3050`). The time gap the
ego must fit into is therefore approximately `spawn_dist / flow_speed`. Raising `flow_speed` at fixed
spawn distance changes three things at once:

- the time gap shrinks (harder decision),
- the speed the ego must match on entry rises (harder kinematics),
- the closing rate of the follower rises (less error margin).

The result would be a clean threshold that cannot be attributed to any one quantity - the same
confound class as fm_003 entangling `yaw` with asset identity.

### Proposed decomposition

| Family | Held fixed | Swept | Target factor | Isolates |
|---|---|---|---|---|
| Primary | `flow_speed` | `source_dist_interval` (`from == to`) | time gap, s = `spawn_dist / flow_speed` | gap acceptance |
| Second | time gap (scale `source_dist` with `flow_speed`) | `flow_speed` | flow speed, m/s | speed matching |

The primary family is the time-domain analogue of "minimum free corridor width", pairing naturally
with fm_002 and fm_004 in the writeup: same question, spatial versus temporal. Run together the two
give a 2-D boundary surface over (time gap x flow speed), which answers the more interesting
question: does the agent's minimum acceptable gap grow with flow speed the way a human's does, or is
it speed-blind?

### Authoring notes

1. **Collapse the interval** (`from == to`). `_spawn_dist = self._rng.uniform(min, max)` is resampled
   on every spawn (`atomic_behaviors.py:2973`, `:3011`) from `CarlaDataProvider.get_random_seed()`.
   Left as a range, the realised gap varies per spawn and per seed, so there is no single measured
   value to record in `variants.csv` - which breaks the certification contract the other families
   rest on.
2. **`flow_speed` is m/s, not km/h** - `set_desired_speed(actor, 3.6 * self._speed)`
   (`atomic_behaviors.py:3009`). `EnterActorFlow`'s default of 10 is 36 km/h. The toolbox defaults to
   10 for `EnterActorFlow` and 20 for the junction-turn scenarios (`toolbox/scripts/config.py:3-19`, `:54-63`).
3. **Post-boundary dynamics change irreversibly.** Each flow vehicle carries a collision sensor wired
   to `stop_constant_velocity()`; the first contact *anywhere* in the flow flips
   `ignore_vehicles_percentage -> 0` for every actor for the rest of the episode
   (`atomic_behaviors.py:3053-3060`). Variants past the boundary are not running the same scenario as
   variants before it. Define the boundary on the *first* infraction event, not on final driving
   score.
4. **Pick a single-lane source.** `get_same_dir_lanes` (`actor_flow.py:104-105`) spawns a parallel
   flow on every same-direction lane; on a multi-lane source the "gap" is a 2-D pattern, not a scalar.
5. **`start_actor_flow` and `end_actor_flow` are mandatory** - read with no default at
   `actor_flow.py:83-84`, so a missing one is a `KeyError` at setup rather than a silent fallback.
   The GUI toolbox already registers both as `location driving` pickers
   (`toolbox/scripts/config.py:54-58`), so routes can be authored through the existing path.
6. **No flow scenario appears anywhere in `fail2drive_split/`.** A census of all 34 scenario types
   used across the benchmark split returns zero uses of `EnterActorFlow`, `EnterActorFlowV2`,
   `HighwayExit`, `MergerIntoSlowTraffic`, `MergerIntoSlowTrafficV2`, `InterurbanActorFlow` or
   `InterurbanAdvancedActorFlow`. This is untested territory under our fork and should get a
   shakedown run before any campaign.

Siblings with the identical `flow_speed` / `source_dist_interval` surface, if the primary family
works out: `MergerIntoSlowTraffic` (`actor_flow.py:315`), `InterurbanAdvancedActorFlow`
(`actor_flow.py:614`).

---

## Tier 3 - context axes that turn a 1-D boundary into a distribution

### T3-A. Site replication

Three trigger points serve all five families, partly shared. We currently cannot distinguish "this
agent's threshold is 0.55 m" from "this agent's threshold *at this one spot* is 0.55 m". Replicating
the fm_001 sweep verbatim across N sites - straight versus curve, junction approach, narrow versus
wide lane, with versus without a legal left lane - is the cheapest way to show a boundary is a
property of the agent rather than of the location. This is also the only route to an ego
approach-speed axis, since speed limits are map data (see the correction under T1-A).

### T3-B. Background traffic density

Add a second scenario block, `<scenario type="BackgroundActivityParametrizer">`, to an existing route.
Knobs include `num_front_vehicles`, `num_back_vehicles`, `road_spawn_dist`, `opposite_source_dist`,
`opposite_max_actors`, `junction_source_dist`, `junction_max_actors`
(`background_activity_parametrizer.py:39-53`). A nuisance axis: does the boundary hold under
distraction, or does it move?

---

## Priority

If two families get built next, T1-A (reaction distance) and T1-B (visibility) give the most per unit
of work: both reuse fm_001 geometry and the existing generator, both need only route-XML fields, and
together they lift the existing 1-D result to a 3-D surface over (intrusion x range x visibility).

T2-D (flow gap acceptance) is the highest-value *new* scenario - it is the first two-sided failure in
the portfolio and the first genuinely dynamic decision - but it carries the most build risk, per
authoring note 6.

## Cross-cutting cautions

- **Do not mix scoring semantics.** Patch `0001` changes `AgentBlockedTest` `min_speed`; campaigns
  run across that boundary are not comparable, as stated in the README. Any new family must be run
  entirely under the patched harness.
- **Stochastic knobs need collapsing.** Several candidate knobs are intervals sampled from
  `CarlaDataProvider.get_random_seed()` (`source_dist_interval`, `frequency`). Certified variants need
  a single measured target value, so collapse `from == to` and let the traffic-manager seed remain the
  only source of run-to-run variation.
- **Check what the boundary is measured on.** Driving score, first infraction, and route termination
  are different instruments, and T2-C and T2-D each need a non-default choice.
