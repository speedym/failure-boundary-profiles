"""Recorder-log -> trajectory extract (public port, MIT).

Parses the server-side text dump of a CARLA recorder file
(`client.show_recorder_file_info(path, show_all=True)`) into a compact
per-run trajectory extract: ego pose + controls at the recorder rate,
every scenario actor's full trajectory (moved statics included - the
recorder tracks a displaced obstacle frame-by-frame), nearby traffic,
and hero collision events.

Entry point: analysis.extract_recorders walks run dirs and calls
from_recorder_log() against a running CARLA server (parse-only, no Town
load, ~1 s per log).

Pure stdlib + a live carla client only inside from_recorder_log();
parse_info() is offline-testable on a saved dump. Keep py3.10-compatible and
pydantic-free (fail2drive env rules).

Format facts (probe 2026-08-03 on fm_002 v02, and pinned in code):
  - dt is fixed 0.05 s; positions are cm, converted to m here.
  - Rotation triples in Positions lines are (ROLL, PITCH, YAW) -
    resolved empirically against the countersigned v01 sidecar
    (barrier: sidecar yaw 90.29/pitch -0.06 vs dump (0, -0.057, 90.288)).
  - Scenario statics are stashed deep underground pre-trigger and lift
    to road level at scenario trigger: a z jump > TRIGGER_LIFT_MIN_M
    between consecutive frames marks the trigger (recorded per actor).
  - Only the hero has a Vehicle-animations (controls) block.

Policy constants:
  - TRACK_RADIUS_M = 150.0: non-scenario actors are kept only if they
    ever come within this planar distance of the hero (TTC/squeeze
    context); dropped actors are counted, never silently absent.
  - Hero and scenario actors keep full 6-DoF at mm rounding; kept
    traffic keeps x/y/yaw at cm rounding.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from pathlib import Path

SCHEMA_VERSION = "trajectory_extract/1"
DT_S = 0.05
TRACK_RADIUS_M = 150.0
TRIGGER_LIFT_MIN_M = 50.0

_NUM = r"(-?[\d.eE+-]+)"
FRAME_RE = re.compile(r"^Frame (\d+) at ([\d.]+) seconds")
CREATE_RE = re.compile(
    rf"^ Create (\d+): (\S+) \(\d+\) at \({_NUM}, {_NUM}, {_NUM}\)")
ATTR_RE = re.compile(r"^  (\w+) = (.*)$")
DESTROY_RE = re.compile(r"^ Destroy (\d+)")
COLLISION_RE = re.compile(r"^ Collision id \d+ between (\d+) \(hero\)\s+with (\d+)")
SECTION_RE = re.compile(
    r"^ (Positions|State traffic lights|Vehicle animations|Walker animations): (\d+)")
POS_RE = re.compile(
    rf"^  Id: (\d+) Location: \({_NUM}, {_NUM}, {_NUM}\) "
    rf"Rotation: \({_NUM}, {_NUM}, {_NUM}\)")
ANIM_RE = re.compile(
    rf"^  Id: (\d+) Steering: {_NUM} Throttle: {_NUM} Brake: {_NUM} "
    rf"Handbrake: (\d+) Gear: (-?\d+)")


def parse_info(info_text: str) -> dict:
    """Parse a full-frame recorder dump into the extract dict (pure)."""
    header: dict = {}
    actors: dict[int, dict] = {}
    tracks: dict[int, dict] = {}
    controls = {"frames": [], "steer": [], "throttle": [], "brake": [],
                "handbrake": [], "gear": []}
    collisions: list[dict] = []
    hero_id: int | None = None
    frame, time_s = 0, 0.0
    section = None
    last_create: int | None = None
    n_frames, duration_s = 0, 0.0

    for line in info_text.splitlines():
        m = FRAME_RE.match(line)
        if m:
            frame, time_s = int(m.group(1)), float(m.group(2))
            n_frames = max(n_frames, frame)
            duration_s = max(duration_s, time_s)
            section = None
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        if section == "Positions":
            m = POS_RE.match(line)
            if m:
                aid = int(m.group(1))
                t = tracks.setdefault(aid, {
                    "frames": [], "x": [], "y": [], "z": [],
                    "roll": [], "pitch": [], "yaw": []})
                t["frames"].append(frame)
                t["x"].append(float(m.group(2)) / 100.0)
                t["y"].append(float(m.group(3)) / 100.0)
                t["z"].append(float(m.group(4)) / 100.0)
                # dump rotation order is (roll, pitch, yaw) - see docstring
                t["roll"].append(float(m.group(5)))
                t["pitch"].append(float(m.group(6)))
                t["yaw"].append(float(m.group(7)))
                continue
        if section == "Vehicle animations":
            m = ANIM_RE.match(line)
            if m and (hero_id is None or int(m.group(1)) == hero_id):
                controls["frames"].append(frame)
                controls["steer"].append(float(m.group(2)))
                controls["throttle"].append(float(m.group(3)))
                controls["brake"].append(float(m.group(4)))
                controls["handbrake"].append(int(m.group(5)))
                controls["gear"].append(int(m.group(6)))
                continue
        m = CREATE_RE.match(line)
        if m:
            aid = int(m.group(1))
            actors[aid] = {
                "blueprint": m.group(2),
                "create_frame": frame,
                "create_pos_m": [round(float(m.group(3)) / 100.0, 3),
                                 round(float(m.group(4)) / 100.0, 3),
                                 round(float(m.group(5)) / 100.0, 3)],
                "role_name": None,
            }
            last_create = aid
            continue
        m = ATTR_RE.match(line)
        if m and last_create is not None:
            if m.group(1) == "role_name":
                actors[last_create]["role_name"] = m.group(2)
                if m.group(2) == "hero":
                    hero_id = last_create
            continue
        m = COLLISION_RE.match(line)
        if m:
            collisions.append({"frame": frame,
                               "time_s": round(time_s, 3),
                               "hero_id": int(m.group(1)),
                               "other_id": int(m.group(2))})
            continue
        m = DESTROY_RE.match(line)
        if m:
            actors.get(int(m.group(1)), {}).setdefault("destroy_frame", frame)
            continue
        if line.startswith("Map: "):
            header["map"] = line[5:].strip()
        elif line.startswith("Date: "):
            header["recorded_date"] = line[6:].strip()

    if hero_id is None:
        raise ValueError("no actor with role_name=hero in recorder dump")

    hero_track = tracks.get(hero_id)
    if not hero_track:
        raise ValueError("hero has no position track")
    hero_xy = {f: (x, y) for f, x, y in
               zip(hero_track["frames"], hero_track["x"], hero_track["y"])}

    def is_scenario(aid: int) -> bool:
        return actors.get(aid, {}).get("role_name") == "scenario"

    def ever_near_hero(t: dict) -> bool:
        r2 = TRACK_RADIUS_M ** 2
        for f, x, y in zip(t["frames"], t["x"], t["y"]):
            h = hero_xy.get(f)
            if h and (x - h[0]) ** 2 + (y - h[1]) ** 2 <= r2:
                return True
        return False

    kept: dict[str, dict] = {}
    dropped = 0
    for aid, t in tracks.items():
        full = aid == hero_id or is_scenario(aid)
        if not full and not ever_near_hero(t):
            dropped += 1
            continue
        if full:
            kept[str(aid)] = {
                "frames": t["frames"],
                "x": [round(v, 3) for v in t["x"]],
                "y": [round(v, 3) for v in t["y"]],
                "z": [round(v, 3) for v in t["z"]],
                "roll": [round(v, 3) for v in t["roll"]],
                "pitch": [round(v, 3) for v in t["pitch"]],
                "yaw": [round(v, 3) for v in t["yaw"]],
            }
        else:
            kept[str(aid)] = {
                "frames": t["frames"],
                "x": [round(v, 2) for v in t["x"]],
                "y": [round(v, 2) for v in t["y"]],
                "yaw": [round(v, 2) for v in t["yaw"]],
            }

    # trigger lift: scenario statics jump from the underground stash to
    # road level in one frame
    for aid, t in tracks.items():
        if not is_scenario(aid):
            continue
        lift = None
        for i in range(1, len(t["z"])):
            if t["z"][i] - t["z"][i - 1] > TRIGGER_LIFT_MIN_M:
                lift = t["frames"][i]
                break
        actors[aid]["trigger_lift_frame"] = lift

    kept_actor_ids = {int(k) for k in kept}
    return {
        "schema_version": SCHEMA_VERSION,
        "map": header.get("map"),
        "recorded_date": header.get("recorded_date"),
        "dt_s": DT_S,
        "n_frames": n_frames,
        "duration_s": round(duration_s, 3),
        "hero_id": hero_id,
        "track_radius_m": TRACK_RADIUS_M,
        "dropped_far_actors": dropped,
        "actors": {str(aid): a for aid, a in sorted(actors.items())
                   if aid in kept_actor_ids},
        "tracks": kept,
        "ego_controls": controls,
        "collisions": collisions,
    }


def from_recorder_log(client, log_path: str | Path) -> dict:
    """Full-frame dump via the live server, parsed; adds source facts."""
    # resolve(): show_recorder_file_info resolves paths SERVER-side (against
    # the CARLA binary's cwd) - a relative path silently reads nothing
    log_path = Path(log_path).resolve()
    info = client.show_recorder_file_info(str(log_path), True)
    if "Frame" not in info:
        raise ValueError(
            f"server returned no frames for {log_path} - not a recorder "
            f"log, or unreadable server-side: {info[:200]!r}")
    data = parse_info(info)
    data["source_log"] = log_path.name
    data["source_log_sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()
    return data


def write_extract(data: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    return out_path


def read_extract(path: str | Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
