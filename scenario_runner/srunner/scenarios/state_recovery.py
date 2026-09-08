#!/usr/bin/env python

#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

"""
State Recovery scenario (Social Chaos Lab fork addition).

Displaces the ego to a parameterised out-of-distribution pose relative to its
own route, hands control straight back to the agent, and measures whether the
agent re-acquires the route.

Unlike every other family in this repository, the swept factor is a property of
the EGO STATE, not of the world. By default nothing is spawned.

Optionally ONE oncoming vehicle can be placed under full authored control
(oncoming_enable), which turns background traffic from a confound into a second
axis: the gap and the speed are set values rather than inferred ones, so the
closing time-to-collision is authored rather than measured after the fact. It is
deliberately a single vehicle with collision avoidance DISABLED - it holds its
lane and its speed and does not react to the ego, so what is measured is the
ego's behaviour and not the interaction of two policies.

Route XML::

    <scenario name="StateRecovery" type="StateRecovery">
      <lateral_offset value="4.0"/>
      <heading_offset value="0.0"/>
      <handback_speed value="6.0"/>
      <trigger_point x="..." y="..." z="..." yaw="..."/>
    </scenario>

Parameters (all optional, defaults in brackets):
    lateral_offset   [0.0]  signed metres, + is right of the route heading
    heading_offset   [0.0]  signed degrees added to the route heading
    handback_speed   [0.0]  m/s along the DISPLACED heading at handback
    longitudinal_offset [0.0] signed metres along the route from the trigger
    trigger_distance [4.0]  how close the ego must be before displacement fires
    trigger_patience [5.0]  seconds after which displacement fires regardless
    recovery_distance [120.0] metres of driving that close the scenario window
    recovery_timeout [60.0] seconds that close the window if the ego does not drive
    reacquire_distance [1.5] lateral metres counted as "back on the route"
    require_surface  [1]    refuse the displacement if the ego body would not
                            fit on contiguous road surface at the target
    surface_margin   [0.0]  extra metres of surface demanded beyond the body
    clearance_patience [3.0] seconds to wait for traffic to vacate the target
                            before refusing

    oncoming_enable  [0]    place one controlled oncoming vehicle
    oncoming_model   ['vehicle.tesla.model3']  blueprint to place
    oncoming_lane    [1]    lanes to cross from the ego lane toward the
                            displacement side; 1 is the first lane past the
                            centreline
    oncoming_gap     [60.0] metres AHEAD of the displacement point along the
                            route, i.e. the closing distance at handback
    oncoming_speed   [8.0]  m/s the vehicle holds once released
    oncoming_start   ['handback']  'handback' holds it still until the ego is
                            displaced, so the gap at handback is exactly the
                            authored one; 'init' releases it at scenario start,
                            which is more physical but makes the gap emergent

A refused displacement is recorded, never silently clamped: clamping would
change the swept factor without changing the catalog, which is the one failure
mode this whole family cannot tolerate. Refusals set RouteReacquisitionTest to
ACCEPTABLE with actual_value 0.0 and are dumped to a sidecar JSON when
FBP_DISPLACEMENT_LOG points at a directory - the leaderboard results file
carries only TrafficEvents, so custom criteria never reach it.

Displacement is computed from the ROUTE WAYPOINT, not from the ego's actual
pose, so the target pose is deterministic and reproducible across agents. The
achieved displacement is what certification measures and records.

Keep |lateral_offset| under 15 m. InRouteTest runs with offroad_max=30 and
offroad_min=15 (route_scenario.py:430-431); beyond 15 m the out-of-route
distance starts accumulating and beyond 30 m the route is killed outright.
"""

import json
import math
import os

import py_trees
import carla

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.scenarioatomics.atomic_behaviors import (
    AtomicBehavior, WaypointFollower)
from srunner.scenariomanager.scenarioatomics.atomic_criteria import Criterion
from srunner.scenariomanager.scenarioatomics.atomic_trigger_conditions import (
    DriveDistance, InTriggerDistanceToLocation)
from srunner.scenariomanager.timer import TimeOut
from srunner.scenarios.basic_scenario import BasicScenario


def get_value_parameter(config, name, p_type, default):
    if name in config.other_parameters:
        return p_type(config.other_parameters[name]['value'])
    return default


# Lane types the ego can be displaced onto and still be "on the road surface".
# Sidewalk and Median are deliberately excluded: landing on them is a scenery
# collision, not an out-of-distribution driving pose.
SURFACE_LANE_TYPES = (carla.LaneType.Driving, carla.LaneType.Shoulder,
                      carla.LaneType.Parking, carla.LaneType.Bidirectional)


def surface_envelope(waypoint, to_right, max_lanes=10):
    """Contiguous surface reach from this lane's centre, in metres.

    Walks the OpenDRIVE lane graph sideways, summing lane widths while the
    neighbour is still a surface lane. This is map data, not a probe: it is
    exact, agent-independent, and available before the run - which is what
    makes it usable as an achievability oracle for the displacement.

    A lane-id sign flip means the road centreline has been crossed, so the
    neighbour faces the other way and "further out" becomes its RIGHT rather
    than its left. The walk tracks that flip and swaps accessors; without it
    the walk turns round at the centreline and reports an undivided road as
    only half as wide as it is. Oncoming lanes are surface and count toward
    the reach - whether displacing onto them is a sane thing to ask is the
    experiment's business, not the geometry's.

    Returns (reach_m, [(lane_id, lane_type, width, 'oncoming'|'same'), ...]).
    """
    reach = waypoint.lane_width / 2.0
    lanes = []
    current = waypoint
    flipped = False
    for _ in range(max_lanes):
        if flipped:
            nxt = current.get_left_lane() if to_right else current.get_right_lane()
        else:
            nxt = current.get_right_lane() if to_right else current.get_left_lane()
        if nxt is None or nxt.lane_type not in SURFACE_LANE_TYPES:
            break
        if (nxt.lane_id * current.lane_id) < 0:
            flipped = not flipped
        reach += nxt.lane_width
        lanes.append((nxt.lane_id, str(nxt.lane_type).split('.')[-1],
                      round(nxt.lane_width, 2), 'oncoming' if flipped else 'same'))
        current = nxt
    return reach, lanes


def lane_across(waypoint, to_right, count):
    """The waypoint `count` surface lanes sideways, or None.

    Same flip-aware walk as surface_envelope: a lane-id sign change means the
    centreline has been crossed and the neighbour faces the other way, so
    "further out" becomes its right rather than its left. Returns
    (waypoint, crossed_centreline) for the lane that many steps across.
    """
    current = waypoint
    flipped = False
    for _ in range(max(0, count)):
        nxt = (current.get_left_lane() if flipped else current.get_right_lane()) \
            if to_right else \
            (current.get_right_lane() if flipped else current.get_left_lane())
        if nxt is None or nxt.lane_type not in SURFACE_LANE_TYPES:
            return None, flipped
        if (nxt.lane_id * current.lane_id) < 0:
            flipped = not flipped
        current = nxt
    return current, flipped


def place_ahead(waypoint, distance, forward):
    """Waypoint `distance` along `waypoint`'s lane, on the side `forward` points.

    An oncoming lane runs against the route, so whether "ahead of the ego" is
    .next() or .previous() depends on which way OpenDRIVE happens to have
    oriented that lane. Rather than assume a convention, take both candidates
    and keep the one that actually lies ahead of the ego. Returns
    (waypoint, used_next) or (None, None).
    """
    origin = waypoint.transform.location
    best, best_dot, used = None, 0.0, None
    for cand_list, is_next in ((waypoint.next(distance), True),
                               (waypoint.previous(distance), False)):
        if not cand_list:
            continue
        cand = cand_list[0]
        delta = cand.transform.location - origin
        dot = delta.x * forward.x + delta.y * forward.y
        if dot > best_dot:
            best, best_dot, used = cand, dot, is_next
    return best, used


def _rect_corners(transform, half_x, half_y):
    yaw = math.radians(transform.rotation.yaw)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    cx, cy = transform.location.x, transform.location.y
    return [(cx + cos_y * dx * half_x - sin_y * dy * half_y,
             cy + sin_y * dx * half_x + cos_y * dy * half_y)
            for dx, dy in ((1, 1), (1, -1), (-1, -1), (-1, 1))]


def _rects_overlap(a, b):
    """Separating-axis test on two planar oriented rectangles."""
    for rect in (a, b):
        for i in range(len(rect)):
            x1, y1 = rect[i]
            x2, y2 = rect[(i + 1) % len(rect)]
            ax, ay = -(y2 - y1), (x2 - x1)
            norm = math.hypot(ax, ay)
            if norm == 0:
                continue
            ax, ay = ax / norm, ay / norm
            a_dots = [x * ax + y * ay for x, y in a]
            b_dots = [x * ax + y * ay for x, y in b]
            if max(a_dots) < min(b_dots) or max(b_dots) < min(a_dots):
                return False
    return True


def traffic_census(world, ego, transform, radii=(15.0, 30.0, 60.0)):
    """Vehicles near the displaced pose at the moment of handback.

    Background traffic cannot be switched off from a route file - the road
    actors are spawned in BackgroundBehavior.initialise() before any scenario
    trigger fires, and the parametrizer only caps later additions - so density
    is recorded as a measured covariate instead of being assumed away.
    Speeds are included because zeroing the parametrizer counts collapses the
    background speed radius and leaves the traffic crawling rather than gone.
    """
    census = {"counts": {}, "mean_speed_mps": None, "nearest_m": None}
    speeds, nearest = [], float('inf')
    for radius in radii:
        census["counts"][str(radius)] = 0
    for actor in world.get_actors().filter('vehicle.*'):
        if actor.id == ego.id:
            continue
        dist = actor.get_transform().location.distance(transform.location)
        nearest = min(nearest, dist)
        if dist <= max(radii):
            vel = actor.get_velocity()
            speeds.append(math.sqrt(vel.x ** 2 + vel.y ** 2))
        for radius in radii:
            if dist <= radius:
                census["counts"][str(radius)] += 1
    if speeds:
        census["mean_speed_mps"] = round(sum(speeds) / len(speeds), 2)
    if nearest < float('inf'):
        census["nearest_m"] = round(nearest, 2)
    return census


def occupying_actors(world, ego, transform, margin=0.5):
    """Vehicles and walkers whose footprint overlaps the ego's at `transform`."""
    extent = ego.bounding_box.extent
    ego_rect = _rect_corners(transform, extent.x + margin, extent.y + margin)
    hits = []
    for actor in list(world.get_actors().filter('vehicle.*')) + \
            list(world.get_actors().filter('walker.*')):
        if actor.id == ego.id:
            continue
        other_tf = actor.get_transform()
        if other_tf.location.distance(transform.location) > 25.0:
            continue
        other_extent = actor.bounding_box.extent
        if _rects_overlap(ego_rect, _rect_corners(
                other_tf, other_extent.x, other_extent.y)):
            hits.append(actor)
    return hits


class DisplacementReport(object):

    """Shared record of what the displacement actually did.

    DisplaceEgo writes it, RouteReacquisitionTest reads it so that a REFUSED
    displacement is never scored as an agent result, and the scenario dumps it
    to a sidecar JSON. It exists because the leaderboard checkpoint only
    carries TrafficEvents (statistics_manager_local.py:371-375) - a custom
    criterion's test_status and actual_value never reach the results file.
    """

    def __init__(self, requested_lateral, requested_heading):
        self.requested_lateral = requested_lateral
        self.requested_heading = requested_heading
        self.status = "pending"      # displaced | refused | pending
        self.reason = ""
        self.surface_reach = None
        self.surface_lanes = []
        self.required_reach = None
        self.blocked_by = []
        self.waited_s = 0.0
        self.achieved_lateral = None
        self.traffic_census = None
        # Controlled oncoming vehicle, when one is placed. actor_id is what
        # lets analysis join a collision to the experimental variable instead
        # of filing it as traffic noise - the family still declares_empty.
        self.oncoming = {"enabled": False}

    def as_dict(self):
        return {"requested_lateral": self.requested_lateral,
                "requested_heading": self.requested_heading,
                "status": self.status,
                "reason": self.reason,
                "surface_reach_m": self.surface_reach,
                "surface_lanes": self.surface_lanes,
                "required_reach_m": self.required_reach,
                "blocked_by": self.blocked_by,
                "clearance_wait_s": round(self.waited_s, 2),
                "achieved_lateral_m": self.achieved_lateral,
                "traffic_census": self.traffic_census,
                "oncoming": self.oncoming}


class DisplaceEgo(AtomicBehavior):

    """
    Teleport the ego to a fixed transform, then give it a velocity along its new
    heading. Two ticks: the transform lands first, the velocity is injected on
    the following tick so the simulator does not overwrite it while resolving
    the move.

    The leaderboard applies the agent's control BEFORE ticking the scenario tree
    (scenario_manager.py:191 then :197), so on the displacement tick a control
    computed at the old pose has already been applied. One tick of stale control
    at the new pose is unavoidable and harmless at these speeds.
    """

    def __init__(self, actor, transform, speed=0.0, report=None,
                 clearance_patience=0.0, name="DisplaceEgo"):
        super(DisplaceEgo, self).__init__(name, actor)
        self._transform = transform
        self._speed = speed
        self._report = report
        self._clearance_patience = clearance_patience
        self._stage = 0
        self._waited = 0.0

    def initialise(self):
        self._stage = 0
        self._waited = 0.0
        super(DisplaceEgo, self).initialise()

    def _refuse(self, reason, blocked_by=None):
        if self._report is not None:
            self._report.status = "refused"
            self._report.reason = reason
            self._report.blocked_by = blocked_by or []
            self._report.waited_s = self._waited
        print("[StateRecovery] displacement REFUSED: %s" % reason)
        return py_trees.common.Status.SUCCESS

    def update(self):
        if self._stage == 0:
            # Surface refusal is static: the map either has room or it does
            # not, so there is nothing to wait for.
            if self._report is not None and self._report.status == "refused":
                return self._refuse(self._report.reason)

            # Occupancy is dynamic. Background traffic moves, so give it a
            # moment to clear rather than deleting actors out of the scene or
            # materialising the ego inside a car.
            world = CarlaDataProvider.get_world()
            blockers = occupying_actors(world, self._actor, self._transform)
            if blockers:
                if self._waited < self._clearance_patience:
                    self._waited += CarlaDataProvider.get_world().get_snapshot(
                        ).timestamp.delta_seconds
                    return py_trees.common.Status.RUNNING
                return self._refuse(
                    "destination occupied",
                    sorted({a.type_id for a in blockers}))

            self._actor.set_target_velocity(carla.Vector3D(0, 0, 0))
            self._actor.set_target_angular_velocity(carla.Vector3D(0, 0, 0))
            self._actor.set_transform(self._transform)
            if self._report is not None:
                self._report.traffic_census = traffic_census(
                    world, self._actor, self._transform)
                self._report.status = "displaced"
                self._report.waited_s = self._waited
            self._stage = 1
            return py_trees.common.Status.RUNNING

        if self._speed:
            fwd = self._transform.rotation.get_forward_vector()
            self._actor.set_target_velocity(carla.Vector3D(
                fwd.x * self._speed, fwd.y * self._speed, fwd.z * self._speed))
        return py_trees.common.Status.SUCCESS


class RouteReacquisitionTest(Criterion):

    """
    Measures whether, and how expensively, the ego returns to its route after
    being displaced.

    Runs only while the scenario is active - route_scenario._create_criterion_tree
    gates scenario criteria on the scenario's blackboard variable, so this sees
    post-handback behaviour only, and the injected displacement itself is never
    charged to the agent.

    test_status is SUCCESS if the route was re-acquired, FAILURE if the ego was
    displaced and never came back, and ACCEPTABLE if the ego was never seen off
    route at all - expected on the 0.0 m control, a harness failure anywhere
    else.

    Reports actual_value = metres driven between the displacement and the first
    tick whose lateral offset to the route is under reacquire_distance. The
    accumulator is reset the moment the ego is first seen off route, so the
    metres spent approaching the trigger point are not counted. Never
    re-acquired leaves test_status FAILURE and actual_value at the distance
    driven while off route.

    Always returns RUNNING: the criteria node is a SUCCESS_ON_ONE parallel, so a
    criterion that returns a completed status would end the route.
    """

    BACK_WINDOW = 5    # route indices searched behind the running index
    FORWARD_WINDOW = 60  # and ahead of it
    TELEPORT_GATE_M = 5.0  # per-tick jumps above this are the displacement itself

    def __init__(self, actor, route, reacquire_distance=1.5, report=None,
                 sidecar_name=None, name="RouteReacquisitionTest"):
        super(RouteReacquisitionTest, self).__init__(name, actor)
        self._report = report
        self._sidecar_name = sidecar_name
        self.units = "m"
        self.success_value = 0
        self._reacquire_distance = reacquire_distance
        self._route_transforms, _ = zip(*route)
        self._route_length = len(self._route_transforms)

        self._index = 0
        self._index_locked = False
        self._prev_location = None
        self._driven = 0.0
        self._reacquired = False
        self._displaced = False
        self._peak_offset = 0.0

    def _offset_to_route(self, location):
        """Lateral distance to the nearest route point, with a monotone index.

        The window is seeded by a full-route search on the first tick. The
        scenario activates wherever its trigger sits - typically far along the
        route - so a window anchored at index 0 would report the distance to
        the start of the route, latch the ego as "displaced" before anything
        had been displaced, and then measure the index catching up instead of
        the recovery. Assumes the route does not revisit its own start within
        FORWARD_WINDOW; true for the straight sites this family uses.
        """
        if not self._index_locked:
            lower, upper = 0, self._route_length
        else:
            lower = max(0, self._index - self.BACK_WINDOW)
            upper = min(self._index + self.FORWARD_WINDOW + 1, self._route_length)

        best_d, best_i = float('inf'), -1
        for i in range(lower, upper):
            ref = self._route_transforms[i].location
            d = math.sqrt((location.x - ref.x) ** 2 + (location.y - ref.y) ** 2)
            if d <= best_d:
                best_d, best_i = d, i

        if best_i == -1:
            return None
        if not self._index_locked:
            self._index = best_i
            self._index_locked = True
        else:
            self._index = max(self._index, best_i)
        return best_d

    def update(self):
        new_status = py_trees.common.Status.RUNNING

        # A refused displacement is a harness outcome, not agent behaviour.
        # Measuring the ego's wander against the route here would manufacture a
        # recovery number for a recovery that was never asked for.
        if self._report is not None and self._report.status == "refused":
            return new_status

        location = CarlaDataProvider.get_location(self.actor)
        if location is None:
            return new_status

        # Accumulate driven distance, discarding the displacement jump itself
        if self._prev_location is not None:
            step = math.sqrt((location.x - self._prev_location.x) ** 2 +
                             (location.y - self._prev_location.y) ** 2)
            if step < self.TELEPORT_GATE_M:
                self._driven += step
        self._prev_location = location

        offset = self._offset_to_route(location)
        if offset is None:
            return new_status

        self._peak_offset = max(self._peak_offset, offset)

        # First tick that sees the ego off route is the displacement. Zero the
        # accumulator there so the measurement starts at handback.
        if not self._displaced and offset > self._reacquire_distance:
            self._displaced = True
            self._driven = 0.0

        if self._displaced and not self._reacquired:
            self.actual_value = round(self._driven, 2)
            if offset <= self._reacquire_distance:
                self._reacquired = True
                self.test_status = "SUCCESS"
            else:
                self.test_status = "RUNNING"

        return new_status

    def _write_sidecar(self):
        """Dump the displacement record next to the run's other outputs.

        The leaderboard results JSON only carries TrafficEvents, so a refused
        or clamped displacement would otherwise be invisible to any downstream
        scoring. Opt in by pointing FBP_DISPLACEMENT_LOG at a directory.
        """
        out_dir = os.environ.get("FBP_DISPLACEMENT_LOG")
        # config.name is the SCENARIO name from the route XML, identical across
        # every variant of a family, so it would overwrite one file per run.
        # The runner passes the per-route id instead.
        name = os.environ.get("FBP_DISPLACEMENT_NAME") or self._sidecar_name
        if not out_dir or self._report is None or not name:
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            payload = self._report.as_dict()
            payload["reacquisition_status"] = self.test_status
            payload["reacquisition_driven_m"] = self.actual_value
            payload["peak_offset_m"] = round(self._peak_offset, 2)
            with open(os.path.join(
                    out_dir, name + ".displacement.json"), "w") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            print("[StateRecovery] could not write displacement sidecar: %s" % exc)

    def terminate(self, new_status):
        if self._report is not None and self._report.status == "refused":
            # Never displaced on purpose: not a pass, not a failure, no result.
            self.test_status = "ACCEPTABLE"
            self.actual_value = 0.0
            self._write_sidecar()
            py_trees.behaviour.Behaviour.terminate(self, new_status)
            return
        if not self._displaced:
            # The ego was never observed further than reacquire_distance from
            # the route. Expected on the 0.0 m artifact control; on any other
            # variant it means the displacement did not fire, and the run is a
            # harness failure rather than an agent result.
            self.test_status = "ACCEPTABLE"
            self.actual_value = 0.0
        elif not self._reacquired:
            self.test_status = "FAILURE"
            self.actual_value = round(self._driven, 2)
        self._write_sidecar()
        # Criterion.terminate would flip RUNNING/INIT to SUCCESS; skip it.
        py_trees.behaviour.Behaviour.terminate(self, new_status)


class StateRecovery(BasicScenario):

    """
    Displace the ego off its route, hand back, measure the recovery.
    """

    def __init__(self, world, ego_vehicles, config, randomize=False,
                 debug_mode=False, criteria_enable=True, timeout=180):
        self.timeout = timeout
        self._world = world
        self._map = CarlaDataProvider.get_map()

        self._lateral = get_value_parameter(config, 'lateral_offset', float, 0.0)
        self._heading = get_value_parameter(config, 'heading_offset', float, 0.0)
        self._speed = get_value_parameter(config, 'handback_speed', float, 0.0)
        self._longitudinal = get_value_parameter(config, 'longitudinal_offset', float, 0.0)
        self._trigger_dist = get_value_parameter(config, 'trigger_distance', float, 4.0)
        self._trigger_patience = get_value_parameter(config, 'trigger_patience', float, 5.0)
        self._recovery_distance = get_value_parameter(config, 'recovery_distance', float, 120.0)
        self._recovery_timeout = get_value_parameter(config, 'recovery_timeout', float, 60.0)
        self._reacquire_distance = get_value_parameter(config, 'reacquire_distance', float, 1.5)
        self._require_surface = get_value_parameter(config, 'require_surface', int, 1)
        self._surface_margin = get_value_parameter(config, 'surface_margin', float, 0.0)
        self._clearance_patience = get_value_parameter(config, 'clearance_patience', float, 3.0)

        self._oncoming_enable = get_value_parameter(config, 'oncoming_enable', int, 0)
        self._oncoming_model = get_value_parameter(
            config, 'oncoming_model', str, 'vehicle.tesla.model3')
        self._oncoming_lane = get_value_parameter(config, 'oncoming_lane', int, 1)
        self._oncoming_gap = get_value_parameter(config, 'oncoming_gap', float, 60.0)
        self._oncoming_speed = get_value_parameter(config, 'oncoming_speed', float, 8.0)
        self._oncoming_start = get_value_parameter(
            config, 'oncoming_start', str, 'handback')

        self._origin_location = None
        self._target_transform = None
        self._oncoming_actor = None
        self._report = DisplacementReport(self._lateral, self._heading)

        super(StateRecovery, self).__init__("StateRecovery", ego_vehicles, config,
                                            world, debug_mode,
                                            criteria_enable=criteria_enable)

    def _initialize_actors(self, config):
        """No actors are spawned. This resolves the displaced pose instead."""
        origin_wp = self._map.get_waypoint(config.trigger_points[0].location)

        if self._longitudinal > 0:
            nxt = origin_wp.next(self._longitudinal)
            if nxt:
                origin_wp = nxt[0]
        elif self._longitudinal < 0:
            prv = origin_wp.previous(-self._longitudinal)
            if prv:
                origin_wp = prv[0]

        origin_tf = origin_wp.transform
        self._origin_location = origin_tf.location

        # Achievability gate. The ego half-width is taken from the spawned
        # hero, not assumed, so the check is against the body that will
        # actually be placed there.
        half_width = self.ego_vehicles[0].bounding_box.extent.y
        reach, lanes = surface_envelope(origin_wp, to_right=self._lateral > 0)
        required = abs(self._lateral) + half_width + self._surface_margin
        self._report.surface_reach = round(reach, 2)
        self._report.surface_lanes = lanes
        self._report.required_reach = round(required, 2)
        if self._require_surface and required > reach:
            self._report.status = "refused"
            self._report.reason = (
                "off surface: needs %.2f m of surface (|offset| %.2f + half-width "
                "%.2f + margin %.2f), site has %.2f m" %
                (required, abs(self._lateral), half_width,
                 self._surface_margin, reach))

        location = carla.Location(origin_tf.location.x,
                                  origin_tf.location.y,
                                  origin_tf.location.z)
        location += origin_tf.rotation.get_right_vector() * self._lateral

        # Drop the pose onto whatever surface is actually there - verge, median,
        # sidewalk - rather than assuming the lane plane extends sideways.
        ground = self._world.ground_projection(location + carla.Location(z=1.0), 3)
        if ground:
            location = ground.location
        location.z += 0.3

        rotation = carla.Rotation(pitch=0.0,
                                  yaw=origin_tf.rotation.yaw + self._heading,
                                  roll=0.0)
        self._target_transform = carla.Transform(location, rotation)

        if self._oncoming_enable:
            self._place_oncoming(origin_wp, origin_tf)

    def _place_oncoming(self, origin_wp, origin_tf):
        """Put one authored vehicle in an oncoming lane ahead of the ego.

        Refused rather than approximated, on the same principle as the
        displacement itself: a car placed in the wrong lane, or at a gap the
        road could not provide, would change the experimental variable without
        changing the catalog.
        """
        rec = {"enabled": True,
               "model": self._oncoming_model,
               "requested_lane": self._oncoming_lane,
               "requested_gap_m": self._oncoming_gap,
               "speed_mps": self._oncoming_speed,
               "start": self._oncoming_start,
               "status": "pending", "reason": "",
               "actor_id": None, "lane_id": None, "crossed_centreline": None,
               "measured_gap_m": None, "spawn_xyz": None, "closing_ttc_s": None}
        self._report.oncoming = rec

        lane_wp, flipped = lane_across(
            origin_wp, to_right=self._lateral > 0, count=self._oncoming_lane)
        if lane_wp is None:
            rec["status"] = "refused"
            rec["reason"] = ("no surface lane %d across from the ego lane"
                             % self._oncoming_lane)
            return
        rec["lane_id"] = lane_wp.lane_id
        rec["crossed_centreline"] = bool(flipped)

        forward = origin_tf.rotation.get_forward_vector()
        spawn_wp, _used_next = place_ahead(lane_wp, self._oncoming_gap, forward)
        if spawn_wp is None:
            rec["status"] = "refused"
            rec["reason"] = ("lane runs out within %.1f m ahead"
                             % self._oncoming_gap)
            return

        # Point it back down its own lane toward the ego. The lane's own
        # orientation is whichever way OpenDRIVE authored it, so derive the
        # heading from the geometry rather than trusting the convention.
        spawn_tf = carla.Transform(spawn_wp.transform.location,
                                   spawn_wp.transform.rotation)
        lane_fwd = spawn_tf.rotation.get_forward_vector()
        if (lane_fwd.x * forward.x + lane_fwd.y * forward.y) > 0:
            spawn_tf.rotation.yaw += 180.0

        actor = CarlaDataProvider.request_new_actor(
            self._oncoming_model, spawn_tf, rolename='scenario')
        if actor is None:
            rec["status"] = "refused"
            rec["reason"] = "spawn failed at the target pose"
            return

        delta = spawn_tf.location - origin_tf.location
        rec["measured_gap_m"] = round(
            delta.x * forward.x + delta.y * forward.y, 2)
        rec["actor_id"] = actor.id
        rec["spawn_xyz"] = [round(spawn_tf.location.x, 2),
                            round(spawn_tf.location.y, 2),
                            round(spawn_tf.location.z, 2)]
        closing = self._oncoming_speed + max(self._speed, 0.0)
        if closing > 0:
            rec["closing_ttc_s"] = round(rec["measured_gap_m"] / closing, 2)
        rec["status"] = "placed"

        self._oncoming_actor = actor
        self.other_actors.append(actor)

    def _create_behavior(self):
        root = py_trees.composites.Parallel(
            "StateRecovery", policy=py_trees.common.ParallelPolicy.SUCCESS_ON_ONE)

        sequence = py_trees.composites.Sequence("Displace and recover")
        # The route trigger has already fired (DIST_THRESHOLD = 2.0 m on the
        # ego's ROUTE-PROJECTED position, route_parser.py:18). This tightens the
        # longitudinal placement using the ego's actual position - but an agent
        # running wide could never come within trigger_dist of a route waypoint,
        # so patience fires the displacement anyway rather than letting the
        # scenario silently expire without ever displacing anything.
        precise_trigger = py_trees.composites.Parallel(
            "Await displacement point",
            policy=py_trees.common.ParallelPolicy.SUCCESS_ON_ONE)
        precise_trigger.add_child(InTriggerDistanceToLocation(
            self.ego_vehicles[0], self._origin_location, self._trigger_dist))
        precise_trigger.add_child(TimeOut(self._trigger_patience))
        sequence.add_child(precise_trigger)
        sequence.add_child(DisplaceEgo(
            self.ego_vehicles[0], self._target_transform, self._speed,
            report=self._report,
            clearance_patience=self._clearance_patience))
        # DriveDistance accumulates per-tick displacement, so it must start
        # AFTER the teleport or the jump would be counted as driving.
        drive = DriveDistance(self.ego_vehicles[0], self._recovery_distance)

        if self._oncoming_actor is not None and self._oncoming_start != 'init':
            # Release the oncoming vehicle at handback, so the authored gap is
            # the gap that actually obtains when the agent takes over.
            # Releasing it at scenario start ('init') is more physical but
            # makes the gap emergent, which is the confound this construct
            # exists to remove.
            #
            # WaypointFollower never returns SUCCESS, so it must run BESIDE
            # DriveDistance, not before it in the sequence - a sequence would
            # sit on the follower forever and the recovery window would never
            # close.
            post = py_trees.composites.Parallel(
                "Recovery window",
                policy=py_trees.common.ParallelPolicy.SUCCESS_ON_ONE)
            post.add_child(drive)
            post.add_child(self._oncoming_behavior())
            sequence.add_child(post)
        else:
            sequence.add_child(drive)

        root.add_child(sequence)
        if self._oncoming_actor is not None and self._oncoming_start == 'init':
            root.add_child(self._oncoming_behavior())
        # A plain TimeOut, not ScenarioTimeout: a scenario timeout would emit a
        # SCENARIO_TIMEOUT infraction, which score_runs.py reads as late_commit
        # and curves.py counts as GO. Failing to recover is not committing.
        root.add_child(TimeOut(self._recovery_timeout))
        return root

    def _oncoming_behavior(self):
        # avoid_collision=False on purpose: an oncoming vehicle that brakes for
        # the displaced ego would make this a measurement of two policies
        # interacting. The authored TTC only means something if the vehicle
        # holds its lane and its speed regardless of what the ego does.
        return WaypointFollower(self._oncoming_actor, self._oncoming_speed,
                                avoid_collision=False,
                                name="OncomingVehicle")

    def _create_test_criteria(self):
        return [RouteReacquisitionTest(
            self.ego_vehicles[0], self.config.route,
            reacquire_distance=self._reacquire_distance,
            report=self._report,
            sidecar_name=getattr(self.config, 'name', None))]

    def __del__(self):
        self.remove_all_actors()
