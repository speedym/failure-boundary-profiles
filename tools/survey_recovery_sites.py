"""Find recovery-family sites: straight, wide, with ONCOMING lanes to the left."""
import carla, math, json, sys

SURFACE = {carla.LaneType.Driving, carla.LaneType.Shoulder,
           carla.LaneType.Parking, carla.LaneType.Bidirectional}

def outward_envelope(wp, max_lanes=10):
    """Surface reach walking LEFT, continuing across the centreline.

    Once the walk crosses into an opposite-direction lane that waypoint faces
    the other way, so 'further out' becomes its RIGHT. Track the flip and
    switch accessors, otherwise the walk turns around and comes back.
    """
    reach = wp.lane_width / 2.0
    lanes, flipped, cur = [], False, wp
    for _ in range(max_lanes):
        nxt = cur.get_right_lane() if flipped else cur.get_left_lane()
        if nxt is None or nxt.lane_type not in SURFACE:
            break
        if (nxt.lane_id * cur.lane_id) < 0:
            flipped = True
        reach += nxt.lane_width
        lanes.append((nxt.lane_id, str(nxt.lane_type).split('.')[-1],
                      round(nxt.lane_width, 2), 'oncoming' if flipped else 'same'))
        cur = nxt
    return reach, lanes

def straightness(wp, span=60.0, step=10.0):
    """Max |heading deviation| in degrees over +/- span metres."""
    base = wp.transform.rotation.yaw
    worst = 0.0
    for d in range(int(step), int(span) + 1, int(step)):
        for nxts in (wp.next(d), wp.previous(d)):
            if not nxts:
                return None
            dev = abs((nxts[0].transform.rotation.yaw - base + 180) % 360 - 180)
            worst = max(worst, dev)
    return worst

def main():
    client = carla.Client('localhost', 2000); client.set_timeout(180.0)
    town = sys.argv[1] if len(sys.argv) > 1 else 'Town13'
    world = client.get_world()
    if town not in world.get_map().name:
        world = client.load_world(town)
    m = world.get_map()
    wps = m.generate_waypoints(25.0)
    print(f"{town}: {len(wps)} sampled waypoints", flush=True)

    cands = {}
    for wp in wps:
        if wp.lane_type != carla.LaneType.Driving or wp.is_junction:
            continue
        left = wp.get_left_lane()
        if left is None or left.lane_type != carla.LaneType.Driving:
            continue
        if (left.lane_id * wp.lane_id) > 0:      # same direction -> not what we want
            continue
        reach, lanes = outward_envelope(wp)
        if reach < 6.0:
            continue
        if not wp.next(140.0) or not wp.previous(100.0):
            continue
        s = straightness(wp)
        if s is None or s > 3.0:
            continue
        key = wp.road_id
        entry = {"road_id": wp.road_id, "lane_id": wp.lane_id,
                 "x": round(wp.transform.location.x, 1),
                 "y": round(wp.transform.location.y, 1),
                 "z": round(wp.transform.location.z, 1),
                 "yaw": round(wp.transform.rotation.yaw, 1),
                 "lane_width": round(wp.lane_width, 2),
                 "left_reach_m": round(reach, 2),
                 "max_heading_dev_deg": round(s, 2),
                 "lanes_outward": lanes}
        if key not in cands or reach > cands[key]["left_reach_m"]:
            cands[key] = entry
    out = sorted(cands.values(), key=lambda e: (-e["left_reach_m"], e["max_heading_dev_deg"]))
    print(f"{len(out)} candidate roads (straight, oncoming-left, >=6 m outward reach)", flush=True)
    for e in out[:15]:
        print(f"  road {e['road_id']:>5} lane {e['lane_id']:>3} reach {e['left_reach_m']:>5} m "
              f"dev {e['max_heading_dev_deg']:>4}deg at ({e['x']}, {e['y']}) :: {e['lanes_outward']}")
    json.dump(out, open(sys.argv[2] if len(sys.argv) > 2 else "sites.json", "w"), indent=1)

main()
