# Candidate sweep axes

Working notes on the axis portfolio, organised around the question the profiles are meant to answer:
**where does driving score fail to explain driving behaviour?**

Every harness knob named here has been located and its semantics verified against the vendored tree
at `bceb18a`; line references are to that tree. **Certification status of every proposed axis:
none.** The generation pipeline validates candidate scenes in simulation before acceptance (see the
refusal rows in `fm_003_asset_swap/variants.csv`); none of these have been through it.

One construct has since moved past the page. **C. State recovery is built and has been run** - three
families, a working achievability gate for the injected pose, and a harness patch. Its results are
n=1 per variant on a single seed and it is still `status=pending`, so it is uncertified in exactly
the sense above; what changed is that section C now records measurements and mechanisms rather than
intentions. Everything else below is still design.

---

## The framing

Two different failures produce similar driving scores:

- **Competence gap** - the agent cannot do the thing. Perception misses it, the planner finds no
  maneuver.
- **Commitment gap** - the agent can do the thing and does not. It hesitates, aborts, over-yields,
  freezes. Or the inverse: it commits where it should not.

Driving score is `score_route * score_penalty` (`statistics_manager.py:395`) and cannot separate
these. The penalty constants make the collapse concrete:

| Behaviour | Event | Effect on DS |
|---|---|---|
| Waits forever for a gap that fits | `SCENARIO_TIMEOUT` | x0.7 |
| Merges into a gap that does not fit | `COLLISION_VEHICLE` | x0.6 |
| Freezes permanently | `VEHICLE_BLOCKED` | no multiplier; route truncated |
| Deviates off route | `ROUTE_DEVIATION` | no multiplier; route truncated |
| Blows a stop sign | `STOP_INFRACTION` | x0.8 - the mildest in the dict |
| Crawls the whole route | `MIN_SPEED_INFRACTION` | x0.7 worst case |
| Swerves wide to commit to a bypass | `OUTSIDE_ROUTE_LANES` | linear in route fraction: ~x0.97 for a 30 m bypass on a 1 km route |

Sources: `PENALTY_VALUE_DICT` and `PENALTY_PERC_DICT`, `statistics_manager.py:21-37`; the percentage
formula at `:336-345`.

The first two rows are the headline: opposite pathologies, near-identical score. The last row is
worth stating precisely - `OUTSIDE_ROUTE_LANES` is *not* free, its multiplier falls linearly to zero
at 100% of the route driven off-lane, but a brief decisive swerve costs almost nothing.

### The construct is implemented, not aspirational

`analysis/` (added in `65375f0`) is the scoring and curve layer, and it encodes this framing
directly. Anything proposed below has to land in its vocabulary to be scoreable.

- `analysis/curves.py` emits **commitment P(go)** and **competence P(clean pass)** per family, model
  and measured axis value, with n.
- Coding is fixed (`curves.py:10-27`). GO = `clean_thread`, `contact_thread`,
  `contact_past_blockage`, `contact_blocked`, `late_commit`. STOP = `refusal`, `correct_stop`.
  Competence counts only `clean_thread`; `late_commit` is *excluded* from competence; anomalies are
  excluded from both.
- x is the **measured** axis value of the run's variant, never the target (`curves.py:19`).
- `analysis/score_runs.py` produces the labeled rows: an eight-outcome taxonomy
  (`score_runs.py:251-261`) and positional collision attribution against countersigned poses and
  measured extents shipped per family in `attribution.json`. Attribution requires blueprint *and*
  position match, so traffic contacts cannot pollute an obstacle curve; a scenario with no
  attribution entries has attribution **disabled loudly** rather than degrading silently
  (`score_runs.py:39-44`, `:280-292`).
- A row is one run, never "the" value for a (variant, seed). Aggregations report n.

### The selection criterion

An axis is usable for this work only if it comes with an **independent ground truth for
achievability**. Given one, the reading is unambiguous:

- failure *before* the achievability boundary = commitment gap
- failure *after* it = competence limit, i.e. correct behaviour

The existing splits already have this and it is their single best property. It is carried in
`variants.csv` two ways: as measured, in-simulator verified geometry (`required_lateral_deviation`,
`closest_approach_distance`) and as an explicit label in the **`assigned_family`** column, which the
scorer reads at `score_runs.py:299` - `passable = assigned != BLOCKAGE_LABEL`, with
`BLOCKAGE_LABEL = "full_static_blockage"` at `:87`.

That label is what makes `correct_stop` distinguishable from `refusal`, and therefore what makes the
commitment curve mean anything. **Only fm_004 populates it today** (4 `full_static_blockage`, 8
`passable_static_obstacle`). fm_001, 002, 003 and 005 leave it empty on every row, which falls
through to `passable = True`, so every stop in those families codes as `refusal`. That is
substantively correct - those variants really are all passable - but it holds by default rather than
by declaration, and `check_passability` (`score_runs.py:302-303`) exists precisely to flag
undeclared rows sitting near the floor.

Every axis added to the portfolio needs an `assigned_family` label and an `attribution.json` export,
or its results cannot be attributed.

The second desirable property is **two-sided failure**: an axis where the agent can be wrong in both
directions yields a safe window rather than a single threshold, and timidity and recklessness become
separable rather than inferred.

---

## The portfolio: three constructs

| Construct | Status | Achievability oracle | Two-sided |
|---|---|---|---|
| A. Spatial margin | shipped (fm_001 - fm_005) | measured geometry + `assigned_family` (declared in fm_004 only) | no |
| B. Temporal margin | to build | gap arithmetic - authored gap is the realised gap | **yes** |
| C. State recovery | **built and run, uncertified** - `fm_006_recovery_oncoming_car` only, `status=pending` (three earlier families deleted 2026-09-08) | **half** - pose achievability from map data (`surface_envelope`); recoverability oracle still unbuilt | yes |

Note that intrusion and corridor width are one construct at two calibrations, not two axes. Presented
as separate axes the portfolio looks like four and is really two plus recovery.

### Rejected: rule compliance under occlusion (stop sign sight line)

A fourth construct was designed and partly built - a roadside occluder set back from a stop sign,
sweeping the distance at which the sign first becomes visible, scored on stopping compliance. It was
**dropped, and its code deleted** (`stop_sign_sightline.py`, `tools/survey_stop_sites.py`,
`tools/generate_stop_sightline_family.py`, and the `fm_007` entries in `curves.py` and
`boundary_stats.py`). Three reasons, in order of weight:

**Naming warning - two slugs have been reused, so always read them with their full name.**

- `fm_007` has meant three things: this deleted stop-sign family, then `fm_007_recovery_left` (also
  deleted). It is currently unused.
- `fm_006` has meant two: `fm_006_state_recovery` (deleted, site 1026) and, since 2026-09-08, the
  live `fm_006_recovery_oncoming_car`.

A bare `fm_006` or `fm_007` in anything written before 2026-09-08 refers to a deleted family. Below,
deleted families are always given their full slug and the live one is `fm_006_recovery_oncoming_car`.

1. **It has no achievability oracle.** The doc previously named the `RunningStopTest` constants as
   one. That was wrong: those constants define what *counts* as compliance, not whether compliance
   was *achievable* from the available evidence. The achievability question is kinematic - can a
   vehicle at approach speed V stop within the sight-line distance - and approach speed is a site
   property that is not controllable from a route (see "Ego approach speed" below). A and B survive
   the same test because their oracles are **geometric** and hold regardless of who is driving; C
   needed a **perceptual** oracle, and no agent-independent source for one exists.
2. **The reference agent cannot calibrate it.** `data_agent.py:81` builds `RunStopSign(self._world)`,
   which reads `traffic.stop` actors directly within 50 m
   (`birds_eye_view/run_stop_sign.py:22-24`). The expert is structurally immune to occlusion, so its
   curve is flat by construction - a tautology, not a reference.
3. **It measures architecture, not behaviour.** `sensor_agent.py:676-679` applies stopping as a hard
   override (`throttle = 0.0; brake = True`) *after* the learned policy, driven by a detector output
   (`:729-731`, `if bb[7] == 3`) behind an env-var switch (`:126-128`). Sweeping occlusion against
   that measures detector recall vs. range. Agents that handle the rule end-to-end are not on the
   same axis at all, so cross-agent curves would not be comparable.

The underlying interest - behaviour as evidence quality degrades - is kept, but as a *modifier* on
constructs A and B rather than as a task of its own. See "Visibility" under Secondary axes.

---

## A. Spatial margin - shipped

All 60 accepted variants across fm_001 to fm_005 are the same scenario underneath:

| Property | Value across all 60 routes |
|---|---|
| Scenario type | `CustomObstacle` (single type, no exceptions) |
| Town | Town13 |
| Weather block | byte-identical (clear, `sun_altitude_angle="45"`, `fog_density="2.0"`) |
| Trigger points | 3 distinct |
| Lead distance | `<distance value="50"/>` in every route |
| Obstacle physics | disabled - `set_simulate_physics(False)`, `construction_crash_vehicle.py:604` |
| Swept factor | lateral `y` offset (fm_001, 002, 004, 005) or asset identity at fixed offset (fm_003) |

Placement frame, `construction_crash_vehicle.py:595-597`: `x` is forward along the route, `y` is
right of centreline, `z` is up. The swept knob is always `y`.

### The two calibrations do different jobs

| Family | Swept | Range | Role in this framing |
|---|---|---|---|
| `fm_002_corridor_pincer` | barrier pair `y`, 0.674 -> 1.416 | corridor 2.32 - 3.06 m | Ego is 1.9 m (`vehicle.lincoln.mkz_2020`, `route_scenario.py:149`). **Every variant is passable**, so every failure is a commitment failure by construction. A pure timidity probe. |
| `fm_004_threshold_sweep` | two `dodge.charger_2020` at +/-`y`, 0.94 -> 2.76 | corridor 0.0005 - 3.64 m | **Contains** the real boundary, including a fully blocked v01. Calibrates where the agent thinks the limit is against where it actually is. |
| `fm_001_intrusion_sweep` | vending machine `y`, -1.3731 -> -1.1787 | intrusion 0.49 - 0.68 m | 19 cm swept in 1.8 cm steps. Fine resolution near a single threshold. |
| `fm_005_hero` | vending machine `y`, 2.304 -> 1.2131 | intrusion 0.00 - 1.09 m | Same axis, ~10 cm steps, plus clutter. See caveat below. |
| `fm_003_asset_swap` | asset identity, `y` re-solved 1.50 - 3.32 to hold intrusion at ~0.50 | 14 accepted, 9 refused | `required_lateral_deviation` is **0.0 for every variant** - all of these are passable without leaving the lane. Not a fit question; a "does it get spooked" question. |

Two caveats to record:

1. **fm_005's clutter re-randomises.** The two bins change `x`, `y` and `yaw` on every variant (v01
   at yaw -95 and +38, v02 at -10 and -104, and so on). A behaviour flip at v08 cannot be attributed
   to the vending machine rather than to where the bins landed. The swept factor is confounded with
   the distractors.
2. **fm_003 refusals have two distinct causes.** Some candidates missed the geometric target
   (construction cone at 0.34, garden gnome at 0.38); others hit it exactly and were still refused
   (guitar case 0.4999, hay bale 0.4999), meaning they failed simulation validation - tipped, rolled,
   or sank. Worth separating in any writeup, since only the second kind documents a physics
   feasibility boundary.

Failure in this construct is **one-sided**: the agent either hits the obstacle or stops. There is no
way to be wrong in the other direction, which is why constructs B and C matter.

---

## B. Temporal margin - flow gap acceptance

The time-domain analogue of minimum free corridor width. `EnterActorFlow` (`actor_flow.py:60`) puts
the ego into a junction it must enter across a stream of vehicles.

### Why it is the strongest axis for this framing

**The flow does not yield.** Each spawned vehicle gets `ignore_vehicles_percentage(actor, 100)` and
`enable_constant_velocity` (`atomic_behaviors.py:3019-3020`), plus `ignore_lights/signs -> 100`
(`:3021-3022`). The stream proceeds at exactly `flow_speed` regardless of the ego, so the gap the
agent faces is exactly the gap that was authored - an unambiguous achievability oracle. The merge
decision sits entirely with the ego, with no cooperative-traffic contamination.
`HandleJunctionScenario(clear_junction=True, clear_ego_entry=True, remove_entries=source_wps)`
(`actor_flow.py:126-133`) also clears background traffic, so the flow is the only traffic present.

**Failure is two-sided, and DS provably cannot tell the sides apart.** In route mode the only
criterion is `ScenarioTimeoutTest` (`actor_flow.py:144-149`). So:

- never accepts a gap that fits -> `SCENARIO_TIMEOUT` -> x0.7
- accepts a gap that does not fit -> `COLLISION_VEHICLE` -> x0.6

That is the cleanest demonstration of the thesis available anywhere in this harness, and it falls out
of the existing code without modification.

### `flow_speed` alone is confounded

`source_dist_interval` is a **distance** interval, not a time one - spawning gates on
`self._source_location.distance(actor_location)` (`atomic_behaviors.py:3046-3050`). The time gap is
therefore approximately `spawn_dist / flow_speed`. Raising `flow_speed` at fixed spawn distance
changes three things at once: the time gap shrinks, the entry speed the ego must match rises, and the
follower's closing rate rises. The result is a clean threshold attributable to nothing - the same
confound class as fm_003 entangling `yaw` with asset identity, and as fm_005's re-randomised bins.

### Proposed decomposition

| Family | Held fixed | Swept | Target factor | Isolates |
|---|---|---|---|---|
| Primary | `flow_speed` | `source_dist_interval` (`from == to`) | time gap, s = `spawn_dist / flow_speed` | gap acceptance |
| Second | time gap (scale `source_dist` with `flow_speed`) | `flow_speed` | flow speed, m/s | speed matching |

Run together they give a 2-D surface over (time gap x flow speed), answering the more interesting
question: does the agent's minimum acceptable gap grow with flow speed the way a human's does, or is
it speed-blind?

### Authoring notes

1. **Collapse the interval** (`from == to`). `_spawn_dist = self._rng.uniform(min, max)` is resampled
   on every spawn (`atomic_behaviors.py:2973`, `:3011`) from `CarlaDataProvider.get_random_seed()`.
   Left as a range, the realised gap varies per spawn and per seed, so there is no single measured
   value to record in `variants.csv` - which breaks the certification contract the other families
   rest on.
2. **`flow_speed` is m/s, not km/h** - `set_desired_speed(actor, 3.6 * self._speed)`
   (`atomic_behaviors.py:3009`). The default of 10 is 36 km/h. The toolbox defaults to 10 for
   `EnterActorFlow` and 20 for the junction-turn scenarios (`toolbox/scripts/config.py:3-19`, `:54-63`).
3. **Post-boundary dynamics change irreversibly.** Each flow vehicle carries a collision sensor wired
   to `stop_constant_velocity()`; the first contact *anywhere* in the flow flips
   `ignore_vehicles_percentage -> 0` for every actor for the rest of the episode
   (`atomic_behaviors.py:3053-3060`). Variants past the boundary are not running the same scenario as
   variants before it. Define the boundary on the *first* infraction event, not on final driving score.
4. **Pick a single-lane source.** `get_same_dir_lanes` (`actor_flow.py:104-105`) spawns a parallel
   flow on every same-direction lane; on a multi-lane source the gap is a 2-D pattern, not a scalar.
5. **`start_actor_flow` and `end_actor_flow` are mandatory** - read with no default at
   `actor_flow.py:83-84`, so a missing one is a `KeyError` at setup rather than a silent fallback.
   The GUI toolbox already registers both as `location driving` pickers
   (`toolbox/scripts/config.py:54-58`), so routes can be authored through the existing path.
6. **No flow scenario appears anywhere in `fail2drive_split/`.** A census of all 34 scenario types
   used across the benchmark split returns zero uses of `EnterActorFlow`, `EnterActorFlowV2`,
   `HighwayExit`, `MergerIntoSlowTraffic`, `MergerIntoSlowTrafficV2`, `InterurbanActorFlow` or
   `InterurbanAdvancedActorFlow`. Untested territory under this fork; needs a shakedown run before
   any campaign.

7. **`late_commit` is coded backwards for this construct - resolve before building.** The scorer
   labels a run `late_commit` when the route completes *and* a `scenario_timeouts` infraction fired
   (`score_runs.py:297`, `:251-258`), and `curves.py` puts `late_commit` in the GO set, i.e. counts
   it as *having committed* (`curves.py:38-39`). For fm_001 to fm_005 that reads correctly: the
   route was driven, the clock merely expired. For `EnterActorFlow` it inverts the meaning -
   `ScenarioTimeoutTest` is the *only* criterion in route mode (`actor_flow.py:144-149`), so timing
   out **is** the timid refusal. An agent that never accepts a gap would be scored as having gone.
   Either the family needs a scorer rule of its own, or it needs an end condition that is not the
   scenario clock. Note the competing path: if the ego sits still long enough,
   `ActorBlockedTest(min_speed=0.2, max_time=180.0)` terminates the route first and the run codes as
   `refusal` correctly - so the two timid failure modes currently land in opposite curve bins
   depending on whether the agent creeps. Verify empirically on a shakedown run.

Siblings with the identical knob surface, if the primary family works out: `MergerIntoSlowTraffic`
(`actor_flow.py:315`), `InterurbanAdvancedActorFlow` (`actor_flow.py:614`).

---

## C. State recovery - adversarial displacement

Start the route, force the ego to an out-of-distribution pose, hand control back, measure whether it
re-acquires the route. The only construct that varies the *ego's state* rather than the world.
Nothing is spawned.

**Status: built and run, not certified. One family.**
`fm_006_recovery_oncoming_car` is the sole surviving family, `status=pending`, so `score_runs.py`
refuses it ("no scoreable records found"). The teleport injection, a map-data achievability gate,
a controlled oncoming vehicle and a per-run sidecar are implemented and exercised on real runs; the
*recoverability* oracle is not, and that is why none of this is certified.

**Three earlier families were deleted on 2026-09-08**, and the survivor took the freed `fm_006`
slug. `fm_006_state_recovery` and `fm_007_recovery_left` used site 1026, a divided carriageway that
could not host the axis (8 of `fm_006_state_recovery`'s 12 variants put the ego off the map
surface). `fm_008_recovery_oncoming` was the no-traffic baseline and is a **strict subset** of the
survivor: the route files were identical apart from a six-line oncoming block, so
`oncoming_enable=0` on `fm_006_recovery_oncoming_car` reproduces it exactly. Its results are
preserved in that family's README and its run data remains at `fbp_runs/fm_008_notraffic_gpu/`.

Working detail and environment caveats are in
[`HANDOFF_STATE_RECOVERY.md`](HANDOFF_STATE_RECOVERY.md); the family's own record is in
[its README](../failure_boundary_profiling_splits/fm_006_recovery_oncoming_car/README.md). This
section records what the construct is and what building it changed about the portfolio.

### Build status

| Piece | Where | State |
|---|---|---|
| `StateRecovery` scenario + `DisplaceEgo` atomic | `scenario_runner/srunner/scenarios/state_recovery.py:493`, `:241` | built |
| `surface_envelope()` achievability gate | same file, `:87` | built |
| `RouteReacquisitionTest` criterion | same file, `:318` | built; invisible to `score_runs.py`, hence the sidecar |
| Displacement sidecar (`FBP_DISPLACEMENT_LOG` / `FBP_DISPLACEMENT_NAME`) | same file, `:444` | built |
| `FBP_NO_BACKGROUND` harness switch | `patches/0003-*.patch`, both `route_scenario.py` copies | built |
| Site survey | `tools/survey_recovery_sites.py` | built |
| Family runner | `tools/run_recovery_family.sh` | built |
| Controlled oncoming vehicle (`oncoming_*` params, `lane_across`, `place_ahead`) | `state_recovery.py` | built |
| Lateral sweep **left into oncoming**, 0-8 m, road 280, 17 variants, + **one authored oncoming car** at 60 m / 8 m/s / TTC 4.29 s | `.../fm_006_recovery_oncoming_car/` | `status=pending`, **the only live family**; 14/17 run |
| No-traffic baseline | same routes with `oncoming_enable=0` | run 17/17; was `fm_008`, now deleted as a strict subset |
| `declares_empty` attribution contract | `analysis/score_runs.py` | built; fm_006 ships `attribution.json` with `declares_empty: true` |
| `lateral_displacement` as a curve axis | `analysis/curves.py:48` | built - keyed to `fm_006`, inert while pending |
| Orientation fallback (success falls with x) | `analysis/boundary_stats.py:79` | built |
| Trajectory layer | `analysis/extract_recorders.py`, `analysis/trajectory_metrics.py` | built, but **yields nothing for this construct** - it skips runs with no `role_name == "scenario"` actors |
| `vehicle_extents.json` | `failure_boundary_profiling_splits/vehicle_extents.json` | measured 2026-09-03, 41/41 `vehicle.*` blueprints; `mkz_2020` reads (2.4462, 0.9184), matching the countersigned `ego_half_length_m` 2.45 |
| Recoverability oracle | - | **not built** - still the blocker |
| A genuine second curve | - | **not designed** - see below |

`boundary_stats.py` and `figures.py` pick a family up through `FAMILY_METRIC`, which `FAMILY_AXES`
is derived from (`boundary_stats.py:84`). The only addition needed was an `ORIENTATION_FALLBACK`
entry: recovery gets harder as displacement grows, so success falls with x, and without the entry a
degenerate stratum set would silently default to `+1` and report the bracket sides backwards.

**Caveat - the analysis wiring is ahead of the certification.** `FAMILY_METRIC` now carries
`fm_006_recovery_oncoming_car`, repointed from the deleted `fm_006` key. The wiring is inert
(`status=pending` means `score_runs.py` emits no row, so `curves.py` never sees one), and it names
the surviving family rather than a deleted one - but it is still an analysis-layer key for a family
that has not been certified, which is the shape of mistake the stop-sign axis made. It is not an
assertion that fm_006 is certified.

### Site selection: an axis has to cross something

Site 1026, which `fm_006_state_recovery` and `fm_007_recovery_left` used, is a **divided
carriageway** - two same-direction lanes with
shoulders outside. It cannot provide an oncoming-lane displacement at all, and its right-side surface
runs out at 3.25 m, so 8 of its 12 variants placed the ego off the map surface entirely, into
vegetation. That result is not a recovery measurement; it is the evidence that the site could not
host the axis.

`tools/survey_recovery_sites.py` scans a town for sites that are straight, junction-free, wide, and
have **oncoming** lanes to the left. On Town13: 39,481 waypoints -> 527 qualifying roads -> 18 with
adequate run length. Road 280 was the flattest (0.74 m of elevation change; the other wide candidates
had 5-10 m of grade).

Road 280 cross-section, ego lane -1 (3.25 m wide), straight run -230 / +240 m, heading deviation
0.0 deg:

| offset from lane centre | what is there |
|---|---|
| 0.00 - 1.62 m | own lane |
| **1.62 m** | **centreline** |
| 1.62 - 4.88 m | near oncoming lane |
| 4.88 - 8.12 m | far oncoming lane |
| 8.12 - 9.26 m | shoulders |
| max usable | **8.34 m** (9.26 surface - 0.92 ego half-width) |

The point is that the axis crosses **qualitative** boundaries - own lane, centreline, near oncoming,
far oncoming, shoulder - rather than merely going further sideways. That is what makes a step in the
curve interpretable, and it is the property site 1026 could never supply.

### The achievability gate is map data, not a probe

`surface_envelope()` walks the OpenDRIVE lane graph sideways from the route waypoint, summing lane
widths while the neighbour is still a surface lane. It is exact, agent-independent, and available
*before* the run - which is what makes it usable as an oracle at all, and it is precisely what the
rejected stop-sign axis never had.

It tracks lane-id sign flips: crossing the centreline means the neighbour faces the other way, so
"further out" becomes its *right*. Without that the walk turns round at the centreline and reports an
undivided road as half its true width. **This bug was live and was caught by a smoke test** - the
gate read 4.88 m instead of 9.26 m on road 280. After the fix, road 280 left = 9.26 m and site 1026
left = 6.75 m (unchanged, because it is divided and never flips).

Occupancy is a separate, dynamic check: a separating-axis test of the ego footprint against every
vehicle and walker within 25 m, waiting up to `clearance_patience` seconds for the target to vacate.

Be clear about what this oracle does and does not answer. It answers **"was the pose on contiguous
road surface, and was that surface free?"**. It does not answer "was recovery from that pose
achievable?" - see the blocker below.

### Refused, never clamped

If a pose is not achievable the displacement is **skipped and recorded**, never clamped to a smaller
offset. Clamping would change the swept factor without changing the catalog, which is the one failure
mode this family cannot tolerate - it would silently break the `curves.py:19` rule that x is the
measured axis value. A refusal sets `RouteReacquisitionTest` to ACCEPTABLE/0.0 and suppresses the
criterion's update loop, so the ego's ordinary wander cannot be scored as a recovery that was never
requested.

### The criterion is invisible to the scorer, so it writes a sidecar

**The leaderboard results JSON carries only `TrafficEvent`s** (`statistics_manager_local.py:371-375`).
A custom criterion's `test_status` and `actual_value` never reach it, so `RouteReacquisitionTest`
results cannot be read by `score_runs.py` at all. This is a general constraint on adding criteria to
this harness, not a quirk of this family.

Setting `FBP_DISPLACEMENT_LOG=<dir>` and `FBP_DISPLACEMENT_NAME=<scenario_id>` makes the criterion
dump a per-run JSON with: requested offset, displaced/refused plus reason, surface reach and the lane
list it walked, reacquisition status and driven metres, peak offset, and a **traffic census** at the
moment of handback (vehicle counts within 15/30/60 m, mean speed, nearest vehicle).

`FBP_DISPLACEMENT_NAME` is required because `config.name` is the *scenario* name from the route XML
and is identical across every variant in a family; keying on it overwrites one file per family and
loses the sweep.

### Background traffic had to be made removable

`patches/0003-*.patch` adds `FBP_NO_BACKGROUND`, applied to **both** `route_scenario.py` copies
(leaderboard and scenario_runner), matching patch 0001's convention.

Background traffic cannot be disabled from a route file. `BackgroundBehavior` spawns its road actors
in `initialise()`, before any scenario trigger fires, and `BackgroundActivityParametrizer` only caps
later additions rather than despawning. Worse, zeroing its counts feeds `_get_road_radius()`
(`background_activity.py:298-306`) and collapses the speed-control radius to one spawn distance:
measured on Town13, the same ten vehicles remained but **mean speed halved, 16.43 -> 7.68 m/s**,
turning moving traffic into crawling obstacles. That is strictly worse than leaving it alone, and it
is worth recording as a general warning about the parametrizer.

`FBP_NO_BACKGROUND=1` omits the activity at behaviour-tree construction. Verified: traffic census
`{15 m: 0, 30 m: 0, 60 m: 0}`, zero collisions, 100% route.

**Caveat:** it removes junction and opposite-flow activity too, not just same-lane road vehicles. The
no-traffic condition is an ablation of *all* background activity, not "your lane emptied". Campaigns
run with and without the flag are not comparable, exactly as with patch 0001.

### Results so far - anecdotes, not measurements

All n=1 per variant on a single traffic-manager seed. Per `RUNNING.md` section 5 that makes every
number below hypothesis-generating.

**The `fm_006_state_recovery`, `fm_007_recovery_left` and `fm_008_recovery_oncoming` directories
were deleted on 2026-09-08.** Their findings are kept here as the record of how the construct
reached its current shape; the run artifacts remain outside the repo under `fbp_runs/`. Only
`fm_006_recovery_oncoming_car` still exists as a runnable family - note it took the freed `fm_006`
slug, so the entries below are *not* about it.

- **`fm_006_state_recovery` (right sweep, site 1026)** - not a recovery result. 8 of 12 variants put the ego off the
  map surface, up to 9 scenery collisions per run, three blocked terminations. Its value is as
  evidence about the site.
- **fm_007 (left sweep, site 1026, with background traffic)** - zero scenery collisions once the
  clearance gate was in, all 12 completed. The finding was **bimodal recovery**: either back on route
  within ~4 s, or frozen ~100 s, nothing in between, stretching a 28 s route past two minutes. Fast
  at 1.5, 2.0, 3.0, 4.5, 5.5 m; slow at 2.5, 3.5, 4.0, 5.0 m - **interleaved, so displacement
  magnitude does not predict the mode.**
- **fm_008 (oncoming sweep, road 280, no background traffic)** - the clean competence baseline, run
  17/17. Reproducible today as fm_006 with `oncoming_enable=0`. **No boundary.** Every pose was achievable (zero refusals up to 8.0 m against an 8.34 m
  usable envelope), every run recovered, zero collisions of any class, 100% route on all 17, and
  game duration moved 22.4 -> 23.25 s across the whole sweep. Re-acquisition distance is linear in
  displacement: `driven = 1.24 * offset + 0.27`, R^2 = 0.995, max residual 0.38 m over 1.5-8.0 m.
  **No step at the 1.62 m centreline** - the near-oncoming and far-oncoming segments fit slopes of
  1.34 and 1.28, and the one own-lane point above the measurement floor sits 0.42 m *below* the
  extrapolation from the oncoming points, i.e. on the easy side rather than the hard one. Variants
  below ~1.5 m are under the instrument's floor: `reacquire_distance` is 1.5 m, so v01-v03 report
  0.0 m driven by construction, not because recovery was instant.

  Two things follow. First, **removing background traffic removed the bimodality**: fm_007's freezes
  stretched a 28 s route past two minutes, and nothing here exceeds 23.25 s. That is a between-family
  comparison across two sites, so it is support for the traffic-deadlock hypothesis rather than proof
  of it, but it is the right direction and it is large. Second, **the displacement axis alone has no
  boundary for this agent on this site** - PDM-Lite drives back from the far oncoming lane as
  readily as from its own. The axis as swept measures a *cost* (metres to re-acquire), not a
  *threshold*. To get a boundary the family needs the other knobs - `heading_offset`,
  `handback_speed` - or traffic as a controlled second axis. Which makes traffic the experimental
  variable rather than the nuisance, and reframes what this construct is for.

  Caveat the same as everything else here: n=1 per variant, one seed.

Working hypothesis for the fm_007 freeze, not yet tested: background vehicles are TrafficManager
autopilots with `auto_lane_change=False` and `distance_to_leading_vehicle=10` m
(`background_activity.py:2129-2131`). They brake for the displaced ego but will never steer around
it, so traffic queues behind the ego while it waits for a gap that the queue keeps refilling. That is
a deadlock geometry, and it is why the traffic census was added to the sidecar. If it holds, "traffic
present" is not a nuisance variable here - it is a second axis, and section "Controlled oncoming
traffic" in the handoff is the design.

### Two measurement bugs, fixed - do not reintroduce

1. **Route index initialisation.** `RouteReacquisitionTest._offset_to_route` searches a window
   `[index-5, index+60]`. The index started at 0 while the scenario activates ~90 m into the route,
   so tick 1 reported the distance to the *start of the route*, latched `_displaced` before anything
   moved, and then measured the index catching up. Since `_reacquired` also latches, the real
   displacement was never measured. Signature: the 0.0 m control reporting exactly 1.66 m in two
   independent sweeps. Fixed by seeding the index with a full-route search on the first tick.
2. **Sidecar filename collision.** See above; cost a full 12-route sweep.

**Still open, deliberately unchanged** - a measurement-definition call for a human: `_offset_to_route`
uses **nearest-route-point** distance, not perpendicular distance to the route polyline. Against a
1.0 m-interpolated route that inflates the reported offset.

The full fm_008 sweep bounds the error, and it is milder than the fm_007 sidecars suggested. It is a
**floor effect, not a proportional inflation**: `peak - requested` is +0.50 m at 0.0 m, +0.15 at
0.5 m, +0.07 at 1.0 m, and never more than +0.05 m for any requested offset at or above 1.5 m
(+0.01 m across 5.0-8.0 m). fm_007's "0.5-5.52 m against 0.0-5.5 m" is the same pattern read the
alarming way round: the 0.5 is the floor at x=0, and the 5.52 is +0.02 at x=5.5.

So the open question is narrower than it looked. It biases only the near-zero end of the axis, where
`reacquire_distance = 1.5 m` has already put the measurement below its own floor. Changing it still
changes what `actual_value` means, so it is still a decision rather than a fix.

### The two curves collapse into one for this family

This is a structural problem, not a bug, and it needs a decision before the family is worth running.

`outcome_for` keys on **obstacle** contacts (`score_runs.py:251-261`). The recovery families place no
obstacles - they declare it, via `declares_empty` - so `contact_thread`, `contact_past_blockage` and
`contact_blocked` are all unreachable. `late_commit` is unreachable too, because no scenario in these
routes installs a `ScenarioTimeout`: `StateRecovery` closes on a plain `TimeOut`, and the
`BackgroundActivityParametrizer` block that those routes used to carry returns an empty criteria list
(`background_activity_parametrizer.py:77-82`). That leaves exactly three coded outcomes - `clean_thread`,
`refusal`, `correct_stop` - and on all three the commitment and competence coders agree. Verified by
enumeration, and reproduced on a synthetic campaign: both curves fit an identical boundary, bracket
and slope.

So as built, the family yields **one** curve - P(recovered) - not two. It can separate
refusal-to-recover from a correct refusal, which is the commitment/competence dissociation the
construct exists for. It cannot separate *recovered* from *recovered safely*.

The fix worth considering: for a family that declares no placed objects, competence should count a
clean recovery as one with zero collisions of **any** class. `score_runs.py` already records
`traffic_collisions`, `pedestrian_collisions` and `environment_collisions` per row; the coder simply
does not read them. That is a change to the fixed coding `curves.py:10-27` calls "identical to the
internal machinery the paper uses", so it is a policy decision, not a patch to make quietly.

Related mismatch: on an unrecoverable variant that the agent completes anyway, the taxonomy emits
`anomaly_blockage_completed` and both curves exclude it. For an obstacle family that genuinely is an
anomaly. Here it means the agent beat the reference controller - a result, not a glitch - and
silently dropping it biases the family against strong agents.

Registration needs nothing: `route_scenario.get_all_scenario_classes` globs `srunner/scenarios/*.py`
and keys the result by class name (`route_scenario.py:271-292`), so a new file is picked up
automatically. The flat keying does mean a duplicate class name anywhere in that directory silently
shadows - `StateRecovery`, `DisplaceEgo` and `RouteReacquisitionTest` were checked against the tree.

### The mechanism already exists

`scenario_manager.py:191` applies the agent's control; `:196` publishes it as `AV_control` with the
comment "in case some behaviors want to change it"; `:197` ticks the behaviour tree. Any behaviour
calling `apply_control()` in its `update()` runs after the agent that same tick and wins before the
next `world.tick()`.

`AddNoiseToRouteEgo` (`atomic_behaviors.py:1889-1934`) is the upstream implementation, and
`ControlLoss` (`control_loss.py:29`) the scenario that uses it, gated on `route_mode` at `:128`.
Force-then-handback is a supported shape. What does not exist is a sweepable version.

### Why `ControlLoss` cannot be used as shipped

- Noise magnitudes hardcoded in `__init__` (`control_loss.py:50-53`); no `get_value_parameter` calls.
  `toolbox/scripts/config.py:277` confirms it: `"ControlLoss": []`, zero route-XML knobs.
- `_get_noise_parameters()` (`:106-113`) randomises the *sign* of the mean, so push direction is not
  addressable.
- Windows fixed at `[14, 48, 74]` m (`:68`), each exiting on leaving a 2 m trigger radius
  (`:130-131`), so duration is an emergent function of speed rather than a parameter.
- Absent from `fail2drive_split/` entirely, same blind spot as the flow families.

The fork is small: same tree, `get_value_parameter` for the noise terms, an explicit direction sign,
an explicit duration, and drop the debris props (cosmetic, and they add `COLLISION_STATIC` risk).

### Hard constraint: displacement caps at ~30 m

`InRouteTest(offroad_max=30, terminate_on_failure=True)` (`route_scenario.py:430-431`). Beyond 30 m
from the nearest of the next 5 route waypoints (`WINDOWS_SIZE = 5`, `atomic_criteria.py:1402`), the
route terminates with `ROUTE_DEVIATION`, a hard stop that truncates completion
(`statistics_manager.py:380-382`). A second trip-wire: past `offroad_min` (= `offroad_max / 2` = 15 m,
`:1414`), out-of-route distance accumulates, and more than 30% of route length beyond 15 m also
terminates (`:1485`).

Usable envelope: **0-15 m free, 15-30 m on a budget, above 30 m an instant kill.** That still covers
wrong lane, oncoming, shoulder, sidewalk and median. Note `WINDOWS_SIZE` only looks *forward* from
`_current_index` (`:1466-1468`), so displace laterally or forward, never backwards along the route.

### The attribution problem

Every route criterion is blind to who was driving. `OutsideRouteLanesTest`, `CollisionTest`,
`RunningRedLightTest` and `MinimumSpeedRouteTest` are installed on the ego for the whole route
(`route_scenario.py:420-433`). If the injection steers the ego onto the sidewalk, the agent is charged
for it.

The harness already provides the fix: `route_scenario.py:465-487` wraps scenario-local criteria in
`WaitForBlackboardVariable(var_name, True, False)` so they are live only while that scenario is
active. **This is what shipped** - `RouteReacquisitionTest` is returned from
`StateRecovery._create_test_criteria()` (`state_recovery.py:610`), so it is live only for the
recovery window and sees only post-handback behaviour by construction.

What this does *not* fix is the route-level criteria. `OutsideRouteLanesTest` and `CollisionTest`
remain installed on the ego for the whole route, so a displacement into the oncoming lane still
charges the agent for being out of lane during the seconds before it recovers. That is a known
distortion of `score_penalty` for this construct, and it is a further reason the family's dependent
variable is re-acquisition, not driving score.

### The blocker: pose achievability is solved, recoverability is not

Constructs A and B get achievability free from geometry. Recovery gets **half** of it free, and the
distinction matters:

- **Solved.** *Was the requested pose legal?* `surface_envelope()` answers this exactly, from map
  data, before the run. Displacements that fail it are refused and recorded rather than silently
  clamped, so the catalog and the swept factor stay in agreement.
- **Not solved.** *Was recovery from that pose achievable?* Nothing in the scene description tells
  you whether a competent driver gets back on route from 8 m lateral at 30 degrees of heading error.
  Without it a failure cannot be attributed, which is the whole point of including the construct.

**Required build**, in the terms the scoring layer already uses:

1. **Establish recoverability empirically.** For each displaced pose, run a reference controller that
   is not the agent under test - CARLA's `BasicAgent`, or a scripted return trajectory - and record
   whether it re-acquires the route. This is the step with no shortcut, and it should be budgeted.
   Note that the fm_007 freeze hypothesis makes this harder than it looks: if the reference
   controller is run with background traffic it inherits the same deadlock, so recoverability has to
   be established under `FBP_NO_BACKGROUND=1` and traffic then reintroduced as its own axis.
2. **Emit it as `assigned_family`.** Label each variant `recoverable_displacement` or
   `unrecoverable_displacement`, matching the `passable_static_obstacle` / `full_static_blockage`
   pattern. `score_runs.py` then needs only the blockage label added at `:87` to separate a genuine
   refusal-to-recover from a correct refusal, and the whole `refusal` / `correct_stop` split works
   unchanged. The `assigned_family` column is present and empty in all three `variants.csv` files,
   waiting for exactly this.
3. ~~Ship an `attribution.json`.~~ **Done.** All three families ship one with `declares_empty: true`,
   which asserts the family places no objects at all, so every contact is traffic, pedestrian or
   environment. This was the right resolution of the question the earlier draft left open: the
   empty-list no-fallback contract (`score_runs.py:39-44`) would have disabled attribution and
   reported every collision as `ambiguous`, whereas `declares_empty` states the fact positively.
4. **The outcome taxonomy needs one new terminal state.** Neither `clean_thread` nor `refusal`
   describes "recovered late, off-route for 40 m". Re-acquisition distance is a continuous
   measurement, not a terminal label, and it belongs in the trajectory layer.

### Injection method

| Method | Mechanism | Trade-off |
|---|---|---|
| Noise (ControlLoss shape) | `AddNoiseToRouteEgo` | Physically plausible, continuous dynamics. But the resulting pose is an *outcome* that depends on the agent counter-steering, so displacement cannot be an input. Not built. |
| **Teleport - built** | `DisplaceEgo` (`state_recovery.py:241`): zeroes linear and angular velocity, `set_transform` to the authored pose, then optionally `set_target_velocity` along the displaced heading for `handback_speed` | Pose is exact and authored in the route XML, which is what a sweep needs. Costs: discontinuous dynamics and discontinuous sensor history. |

Teleport is what shipped. The displaced pose is built from the route waypoint rather than a raw world
coordinate, so it inherits the road surface and needs no `ground_projection` fix-up.

### Axes

Route XML parameters on the `StateRecovery` block, defaults in brackets:

```
lateral_offset   [0.0]  signed metres, + is RIGHT of route heading
heading_offset   [0.0]  signed degrees added to route heading
handback_speed   [0.0]  m/s along the displaced heading at handback
trigger_distance [4.0] / trigger_patience   [5.0]
recovery_distance [120.0] / recovery_timeout [60.0]
reacquire_distance [1.5]  lateral metres counted as "back on route"
require_surface  [1]    refuse if the ego body would leave contiguous surface
surface_margin   [0.0]
clearance_patience [3.0] seconds to wait for traffic to vacate the target
```

| Axis | Swept so far | Notes |
|---|---|---|
| Lateral displacement at handback | fm_006: 0 -> 8 m left, 0.5 m steps, `handback_speed` pinned at 6.0 m/s | Primary. Site-limited, not limited by the 30 m route rule. |
| Heading error at handback | not swept | `heading_offset` exists; separates "wrong place" from "wrong heading" |
| Speed at handback | not swept | `handback_speed` exists; the only realistic route to an ego-speed axis |
| Semantic context | own lane / near oncoming / far oncoming / shoulder | Stratifier, not a sweep. Recorded per variant in `variants.csv:zone`; the surface gate supplies the boundaries. |
| Oncoming vehicle gap / speed | fm_006 pins gap 60 m, speed 8 m/s (TTC 4.29 s) against the same displacement sweep | **The controlled traffic axis.** One vehicle, `avoid_collision=False`, held still until handback so the authored gap is the gap at handback. **60 m proved to be a null** - the ego recovers in ~0.7 s and the car arrives at 4.3 s, so it reproduces the no-traffic baseline to within a centimetre on 13 of 14 variants. The informative sweep is the gap, downward from ~25 m. |
| Background traffic present / absent | fm_007 with, fm_008 without (both deleted) | A between-family confound, *not* an axis - the reason `FBP_NO_BACKGROUND` exists and the reason fm_006 replaces it with one countable vehicle. |

### Risks

- **Background traffic does not know the ego teleported.** Confirmed as a real effect, not a
  hypothetical: this is the leading explanation for fm_007's bimodal recovery, and removing the
  traffic removed the bimodality. It is the reason patch 0003 exists, the reason the clean baseline
  runs with no background activity, and the reason traffic re-enters as one authored vehicle
  (`fm_006_recovery_oncoming_car`) rather than as a population.
- **Certification will refuse more often** than the obstacle families: validity depends on the
  injected pose being on surface *and* on traffic not being where the ego lands. Both are now checked
  and both refuse rather than clamp.
- **The site can silently define the axis.** `fm_006_state_recovery` is the cautionary case - two thirds of its sweep
  measured "the ego is in a hedge", not recovery. Survey the cross-section before authoring the
  sweep, and record it, as in the road 280 table above.
- **The boundary may be trivially sharp.** Several leaderboard agents plan in a route-relative frame
  and will simply drive at the next route waypoint. Still a result, just a thinner one. The fm_007
  bimodality is weak evidence against this - the failures there look like a stuck controller rather
  than a clean geometric threshold.
- **Results are GPU-host-dependent for sensor agents.** PDM-Lite runs `--track MAP` from privileged
  state and never reads a rendered frame, so software-rendered runs are valid for it. They would not
  be valid for TFv6 or BridgeDrive. See the environment note in the handoff.

---

## Instrumentation - what exists and what is still missing

**Terminal state is solved.** The eight-outcome taxonomy in `score_runs.py:251-261` is a finer
partition than a hand-rolled pass / stop / collide split, and it is joined to measured geometry,
driving score, and per-class attributed collision counts on every row. `stopped-permanently` arrives
via `ActorBlockedTest(min_speed=0.2, max_time=180.0)` (`route_scenario.py:432-433`) terminating the
route, which surfaces as `refusal` or `correct_stop`. Nothing further is needed here.

**Timing was the gap, and the trajectory layer now closes most of it.** The *scorer* still reads only
leaderboard checkpoint JSON, so every measure `score_runs.py` can produce is a terminal state, and
the harness itself persists nothing: `InRouteTest` computes lateral offset to the route every tick
(`atomic_criteria.py:1466-1474`) and discards it after thresholding. The route around that is the
CARLA recorder plus `analysis/extract_recorders.py` (recorder logs -> per-run trajectory extracts,
needs a running server and the carla wheel) and `analysis/trajectory_metrics.py` (extracts -> one row
per run, stdlib, offline, joinable with `scored_rows` on family/model/seed/scenario_id). **The
recorder must be on at run time**; runs made without it cannot be retrofitted.

| Measure | Definition | Separates | Status |
|---|---|---|---|
| Terminal state | The outcome taxonomy | The categories DS collapses into one number | **done** (`score_runs.py`) |
| Time-to-commit | Hazard entering sensor range -> first decisive control change | Hesitation from inability | needs trajectories |
| Abort rate | Began the maneuver, then reversed | Commitment instability | needs trajectories |
| Margin taken | Actual closest approach vs certified `closest_approach_distance` | Excess margin = timidity; deficit = over-commitment | needs trajectories |
| Re-acquisition distance | Displaced pose -> lateral offset back under ~1.5 m | Slow recovery from no recovery (construct C) | **done, but only via the sidecar** - `RouteReacquisitionTest` measures it live and the displacement sidecar persists it. The trajectory layer does *not* supply it; see below |
| Realised gap on a flow family | `crossing_headways()` in `trajectory_metrics` | What gap the agent actually faced, vs the authored one | built, **experimental**, never run against a flow family |

This still gates the portfolio unevenly:

- **A** is fully scoreable today. Its curves need only terminal state.
- **B** needs `crossing_headways()` exercised for real to certify what gap the agent actually faced,
  and needs the `late_commit` question in authoring note 7 resolved.
- **C** is instrumented. `RouteReacquisitionTest` measures re-acquisition during the run, and because
  a custom criterion's result never reaches the leaderboard JSON (only `TrafficEvent`s do,
  `statistics_manager_local.py:371-375`) it is persisted through the displacement sidecar instead.
  What C is blocked on is the *oracle*, not the instrument.
- **B and C both measure *when* the agent decides**, not just what it ended up doing:
  gap-acceptance latency and time-to-re-acquisition are the dependent variables. Neither is
  recoverable from a run made with the recorder off.

Two practical limits on the trajectory layer, both found by running it:

1. It needs `failure_boundary_profiling_splits/vehicle_extents.json` - measured planar half-extents
   per blueprint, produced by the spawn probe. Without it `trajectory_metrics.py` raises on startup.
2. **It cannot process an object-free family at all.** `compute_row` collects actors with
   `role_name == "scenario"` and returns `None` when there are none
   (`trajectory_metrics.py:261-263`), so every run of a family that declares `declares_empty: true`
   is skipped and the tool exits "no scoreable extracts found". Construct C places nothing, so the
   layer yields zero rows for it even with the extents file present and the catalog certified.

   The interesting part is that `compute_row` has already projected the ego onto the route axis and
   computed the per-frame station and lateral offset (`:234-237`) before it bails - which *is* the
   re-acquisition measurement this construct wants. Emitting the ego-only metrics when there are no
   scenario actors would unblock it. That is a change to a direct port of the internal scorer with
   documented 96/96 fidelity, so it is a policy decision rather than a patch to make quietly - the
   same class of call as the two-curves fix above.

Boundary fitting with honest uncertainty - Wilson intervals, brackets under separation, bootstrap
over seed strata - is no longer on the roadmap; it is `analysis/boundary_stats.py`. Thresholds quoted
from a certified family carry intervals. Nothing in construct C is certified, so nothing in it does.

---

## Secondary axes

Not part of the three constructs. Recorded because they are cheap, and because two of them turn a 1-D
boundary into a surface.

### Visibility (cheapest high-value addition)

- Knob: the `<weather>` entries already in every route XML, interpolated by `route_percentage`. No
  scenario-block change, no harness change.
- Fields: `fog_density`, `fog_distance`, `precipitation` + `precipitation_deposits` + `wetness`,
  `sun_altitude_angle` (low for glare, negative for night).

All 60 current routes are byte-identical here. The high-value version is not a standalone family but
a **re-run of fm_001 at 2-3 fog levels**, turning a threshold point into a threshold surface: does the
intrusion boundary shift smoothly under fog, or does the agent fail well before its clear-weather
threshold? Under the framing above this is a competence-degradation axis, and since the occlusion
construct was rejected it is now the **only** degraded-evidence probe in the portfolio. Crossing it
with A costs no new certification - fm_001's geometry is already certified and weather does not move
it - and crossing it with B keeps the two-sided structure. It is architecture-agnostic in a way the
stop-sign axis was not: fog degrades every sensing stack, with no rule-specific override path to
short-circuit it.

### Reaction distance

- Knob: `<distance value="..."/>` on the `CustomObstacle` block.
- Code: `construction_crash_vehicle.py:80` (default 100); consumed at `:348` via
  `self._reference_waypoint.next(self._distance)`. All current families hardcode 50.

Separates perception-limited failure (fails even at 100 m with ample room) from planner-limited
failure (passes at 100 m, fails at 25 m). A direct competence-side probe.

### Ego approach speed - not available as a route knob

`<speed value="..."/>` on the scenario block does **not** sweep ego approach speed. It feeds
`SetMaxSpeed` (`construction_crash_vehicle.py:256`), which writes the `BA_SetMaxSpeed` blackboard key
(`tools/background_manager.py:90`) and caps **background activity** vehicles
(`background_activity.py:2518-2519`). There is no per-route ego speed knob; map speed limits live in
`scenario_runner/speed_limits/*.npy` and are map data, not route data. Ego approach speed is
therefore a site property, or - as in construct C - a handback parameter.

### Vertical extent and orientation

- Knobs: `z`, `pitch`, `roll`, `yaw` in the `<objects a="id=..."/>` spec, parsed at
  `construction_crash_vehicle.py:584-599`. Physics is disabled, so props can be floated and tilted
  deterministically, with none of the tip-over refusals that cost 9 of 23 assets in fm_003.
- **Vertical clearance** (`z`): at what height does an object stop being treated as blocking?
- **Yaw de-confounding**: fm_003 varies `yaw` between 0 and 90 across assets, entangled with identity.
  A yaw-only sweep on `vendingmachine` (already pinned by fm_005) separates silhouette from identity
  at near-zero authoring cost.

### Site replication

Three trigger points serve all five families, partly shared. We currently cannot distinguish "this
agent's threshold is 0.55 m" from "this agent's threshold *at this one spot* is 0.55 m". Replicating
fm_001 verbatim across N sites - straight vs curve, junction approach, narrow vs wide lane, with vs
without a legal left lane - is the cheapest way to show a boundary is a property of the agent rather
than the location.

### Other dynamic scenarios considered

| Axis | Scenario | Knobs | Why it is secondary |
|---|---|---|---|
| Oncoming gap during bypass | `CustomObstacleTwoWays` (`construction_crash_vehicle.py:620`) | `<frequency from/to>`, default `[20,100]` | Entangles the gap decision with obstacle geometry; construct B isolates it cleanly. Note `SwitchWrongDirectionTest(False)` means crossing the centreline is not itself penalised. |
| Time-to-collision on a moving hazard | `PedestriansOnRoad` | `walker_speed` (2), `distance` (20), `pedestrian_on_road.py:38-39` | A braking-decision boundary rather than an avoidance-geometry one. Good axis, but one-sided. |
| Crowd density | `PedestrianCrowd` | `pedestrians` (20), `pedestrian_radius` (20), `pedestrian_center` (40), `side`, `pedestrian_crowd.py:55-58` | Clean scalar for "at what density does forward progress stop", and the family where patch `0001` actually bites. But `VEHICLE_BLOCKED` has no `PENALTY_VALUE_DICT` entry, so the boundary must be defined on the termination event. |
| Background traffic density | `BackgroundActivityParametrizer` | `num_front_vehicles`, `num_back_vehicles`, `road_spawn_dist`, `opposite_source_dist`, `opposite_max_actors`, `junction_source_dist`, `junction_max_actors` (`background_activity_parametrizer.py:39-53`) | A nuisance axis: does the boundary hold under distraction, or does it move? |

---

## Cross-cutting cautions

- **Do not mix scoring semantics.** Patch `0001` changes `AgentBlockedTest` `min_speed` from 0.1 to
  0.2 m/s. Campaigns run across that boundary are not comparable, as the README states. Any new
  family must be run entirely under the patched harness. Patch `0003` (`FBP_NO_BACKGROUND`) creates a
  second such boundary, and it is a *runtime* one rather than a build one: runs with the flag set
  have no background activity of any kind - road, junction or opposite flow - and are not comparable
  with runs without it. Record the flag state with the campaign, not just the patch level.
- **Stochastic knobs need collapsing.** Several candidate knobs are intervals sampled from
  `CarlaDataProvider.get_random_seed()` (`source_dist_interval`, `frequency`). Certified variants need
  a single measured target value, so collapse `from == to` and let the traffic-manager seed remain the
  only source of run-to-run variation. fm_005's re-randomised bins are an existing instance of this
  problem inside a shipped family.
- **Check what the boundary is measured on.** Driving score, first infraction, and route termination
  are different instruments. Constructs B and C each require a non-default choice, and the crowd
  density axis does too.
- **Every axis needs its achievability oracle named before the campaign, not after.** For A it is
  measured geometry; for B, gap arithmetic; for C it does not exist yet and must be built. The
  rejected occlusion axis is the cautionary case: it was designed and coded before anyone asked what
  its oracle was, and it turned out not to have one.
