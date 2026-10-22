#!/usr/bin/env python3
"""Two-line go / clean_pass derivation + F8 contact re-coding over an
Isaac campaign run tree - the first executed DECLARED scoring
instrument (artifacts/examples/scoring_instruments/, parked ledger 22).

Per cell (one ledger): the robot's measured collision envelope (robot
record body.*) is placed at every recorded step pose; a line from the
instrument declaration is CROSSED when the path of any corner of that
rectangle between two consecutive samples passes through the line
segment from its near side (lines revision r3, 2026-10-14; bumper-in
counts, sticky). Presence beyond a line without that passage is NOT a
crossing (r2 read it as one: the expert's detours re-entered aisle A at
its far end and fired the gap and through lines, G33); it is kept per
row as `entered_past`. A track whose first sample lies past a line is
refused. Lines resolve against the variant sidecar's frame by FIELD
NAME - no coordinate lives in this tool or in the declaration.

  go            = gap line crossed OR detour line crossed
  clean_pass    = footer reached_goal AND zero RELEVANT touch contacts, any
                  route (F8: relevance by measured z; an unresolved partner
                  is never clean, counted in f8.unresolved_rows)
  threaded_clean= footer outcome == commit_clean (the passage quantity)
  route_at_goal = gap | detour | none (through line on the reference
                  point; detour line on the body)

F8 re-coding (contact relevance keyed on measured z, not prim names):
every recorded contact partner is resolved to a z-extent - procedural
arms from the sidecar (authored_obstacles + obstacle_spec.height_z),
composed parts from the sidecar (t_world z .. z_top_m), everything
else from the environment census by longest source_prim prefix
(support.z_min_m .. + extents z) - and is relevant iff
z_max > floor_contact_z + grid_res. Unresolved partners are COUNTED,
never guessed. The runtime substring rule only ever ADMITTED rows, so
this pass can only demote; contacts it filtered out at run time are not
recoverable from ledgers (stated in the summary).

Audit tolerance: step rows are 0.1 s apart; each row carries its
closest approach to every line and the cell's max speed so a reader
can bound what a sub-sample dwell could have missed.

  conda run -n scl_prototype python \\
      adapters/isaac/scripts/multi_model_eval/derive_go_clean.py \\
      --run-dir <dir with <variant>/<cell>/*.jsonl> \\
      --family <snapshot dir> --robot-record <record> \\
      --environment-record <record> --instrument <declaration> --out <dir>

The grid resolution comes from the instrument's `certification` block (2026-10-08; until then from
the capability profile, which this tool then pinned into every interpretation).
The three instruments this derivation is handed (the scoring instrument, the robot record, the
environment record) are pinned in the summary's provenance by repository-relative path AND sha256,
one entry per role (`provenance.instruments`), the shape every CARLA document pins files in, so a
checkout can be checked against the interpretation and a stale check can tell the pin from a content
id (2026-10-10, ledger F81; until then by hash alone). The emitter copies the block into every
findings document.
"""
import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from scl_core.certification_band import certification  # noqa: E402  (the band: one formula, one place)
from scl_core.records import portable_path  # noqa: E402  (stdlib-only core: a pin's path is repo-relative or refused)
from scl_core import run_rows as rr  # noqa: E402  (the one row and the one stamp, F82)
from scl_core import run_wall_clock as rwc  # noqa: E402  (the run's wall clock from its log, F94)

# the warehouse chain's outcome vocabulary (F82): the labels this scorer writes and their codings, pinned in the stamp
VOCABULARY = _ROOT / "artifacts/examples/scoring_instruments/outcome_vocabulary_warehouse.json"
# the run's wall clock (F94, 2026-10-17): the cell runner keeps the Kit log as cell.log; Kit prefixes its lines with
# the seconds since the app started and writes this line last (its ISO stamps sit on warning lines only)
KIT_SHUTDOWN_MARK = re.compile(r"^\[(\d+\.\d+)s\] Simulation App Shutting Down", re.M)
WALL_CLOCK_METHOD = "cell.log, the Kit log's elapsed-seconds counter on its Simulation App Shutting Down line"
ANOMALY_BLOCKAGE_COMPLETED = "anomaly_blockage_completed"

TERMINAL = ("expert_run_footer", "brain_run_footer")


def run_pins_check(meta: dict, robot_record_sha: str, family_manifest_sha: str, accept: bool) -> tuple:
    """The instruments the run pinned at run time (campaign_meta: robot_record_sha256,
    family_manifest_sha256) against the ones this derivation is handed. Returns (pinned,
    instrument_change): the change is {} when every pin matches or the meta pins nothing (a tree
    that predates the pins); otherwise it names run and scored hashes per instrument, and the
    derivation REFUSES unless `accept` (then the change is recorded in the summary's provenance,
    principle 13: an instrument change is said, never silent)."""
    pinned = {"robot_record": meta.get("robot_record_sha256"),
              "family_manifest": meta.get("family_manifest_sha256")}
    given = {"robot_record": robot_record_sha, "family_manifest": family_manifest_sha}
    change = {k: {"run": pinned[k], "scored": given[k]} for k in pinned
              if pinned[k] is not None and pinned[k] != given[k]}
    if change and not accept:
        sys.exit("REFUSED: instrument change - the run pinned " + ", ".join(
            f"{k} {v['run'][:12]}, given {v['scored'][:12]}" for k, v in change.items())
            + "; pass --accept-instrument-change to score under the given instrument and record the change")
    return pinned, change


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()




def resolve_ref(frame: dict, ref: str):
    """'constriction_zone_y[0]' -> frame['constriction_zone_y'][0];
    'aisle_a_x_faces' -> frame['aisle_a_x_faces']."""
    if ref.endswith("]"):
        name, idx = ref[:-1].split("[")
        return frame[name][int(idx)]
    return frame[ref]


def line_spec(decl: dict, frame: dict) -> dict:
    w = decl["within"]
    return {"axis": 0 if decl["axis"] == "x" else 1,
            "value": float(resolve_ref(frame, decl["value_ref"])),
            "sense": decl.get("sense", ">="),
            "within_axis": 0 if w["axis"] == "x" else 1,
            "within": [float(v) for v in resolve_ref(frame, w["range_ref"])],
            "test_on": decl.get("test_on", "body")}


def body_corners(x, y, yaw, front, rear, hw):
    c, s = math.cos(yaw), math.sin(yaw)
    out = []
    for bx, by in ((front, hw), (front, -hw), (-rear, hw), (-rear, -hw)):
        out.append((x + c * bx - s * by, y + s * bx + c * by))
    return out


def reach(line: dict, pts) -> float | None:
    """Signed reach past the line (>= 0 means crossed) over the points
    inside the declared band; None when no point is in the band."""
    lo, hi = line["within"]
    best = None
    for p in pts:
        if lo < p[line["within_axis"]] < hi:
            d = p[line["axis"]] - line["value"]
            if line["sense"] == "<=":
                d = -d
            best = d if best is None else max(best, d)
    return best



def crossing(line: dict, prev_pts, pts):
    """The first tested point whose path from the previous sample passes through the line segment from
    its near side (lines revision r3, 2026-10-14): its signed distance turns from negative to non-negative
    between the two samples and the crossing, interpolated along the path, lies inside the declared band.
    Returns (point index, band coordinate of the crossing) or None. Presence beyond the line is not a
    crossing: r2 read it as one, and a body entering the band from the side beyond the line fired it."""
    lo, hi = line["within"]
    sign = -1.0 if line["sense"] == "<=" else 1.0
    for i, (p0, p1) in enumerate(zip(prev_pts, pts)):
        d0 = sign * (p0[line["axis"]] - line["value"])
        d1 = sign * (p1[line["axis"]] - line["value"])
        if d0 < 0 <= d1:
            f = d0 / (d0 - d1)
            b = p0[line["within_axis"]] + f * (p1[line["within_axis"]] - p0[line["within_axis"]])
            if lo < b < hi:
                return i, b
    return None


def outcome_for(assignment: str, footer_outcome) -> str:
    """The run's label: the runner's footer outcome (route and contact state), with a contact-free completion
    of a certified-BLOCKED gap recoded to anomaly_blockage_completed (impossible by the certificate, the
    robot's measured width against the measured gap: a simulator finding, never a pass; the CARLA class,
    2026-10-03, ledger F61), and a missing footer outcome named `missing`. Until F82 this recode lived in the
    Isaac score_runs.py, retired that day: one warehouse scorer."""
    outcome = footer_outcome or "missing"
    return ANOMALY_BLOCKAGE_COMPLETED if assignment == "blocked" and outcome == "commit_clean" else outcome


def repetition_of(cell_name: str):
    """The cell directory's repetition: `expert` is the one deterministic reference run (null), a brain cell is
    `<slug>_rep<k>` (brain_models.cell_dir_name)."""
    if cell_name == "expert":
        return None
    tail = cell_name.rsplit("rep", 1)[-1]
    if not tail.isdigit():
        raise ValueError(f"cell {cell_name!r} is neither `expert` nor `<brain>_rep<k>`")
    return int(tail)


def clean_pass(reached: bool, contact_state: str) -> bool:
    """The mission verdict of one cell from the F8 re-coded contact state: clean exactly when the goal was
    reached and every recorded contact partner measured as irrelevant; a contact, and an unresolved partner
    (its z-extent unmeasurable), are never clean. Until 2026-10-11 the row read the footer's runtime count
    instead and the re-coding was consumed by nothing (register G13; equal on every pinned interpretation,
    1008 cells, 0 flips, 0 unresolved)."""
    return bool(reached) and contact_state == "clean"

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--family", required=True, type=Path)
    ap.add_argument("--robot-record", required=True, type=Path)
    ap.add_argument("--environment-record", required=True, type=Path)
    ap.add_argument("--instrument", required=True, type=Path,
                    help="the declared scoring instrument (lines, contact relevance, certification.grid_res_m)")
    ap.add_argument("--out", required=True, type=Path, help="the scoring_out folder: run_rows.jsonl and its stamp")
    ap.add_argument("--accept-instrument-change", action="store_true",
                    help="proceed when the robot record or the family given differ from what the run pinned in "
                         "campaign_meta.json, recording the mismatch as an instrument change (principle 13); "
                         "without it such a run is refused, never scored silently under another instrument")
    args = ap.parse_args()

    # the run pinned its instruments at run time (campaign_meta/v0.1: robot_record_sha256,
    # family_manifest_sha256); scoring under another one is an instrument change, said out loud
    meta_path = args.run_dir / "campaign_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    pinned, instrument_change = run_pins_check(meta, _sha(args.robot_record), _sha(args.family / "family_manifest.json"),
                                               args.accept_instrument_change)

    inst = json.loads(args.instrument.read_text())
    rec = json.loads(args.robot_record.read_text())
    body = rec["body"]
    if body.get("status") != "measured":
        sys.exit("REFUSED: robot record body envelope is not measured")
    rest = rec["rest"]
    if rest.get("status") != "measured":
        sys.exit("REFUSED: robot record rest stage is pending - the floor "
                 "contact plane is a measured fact, never assumed")
    floor_z = float(rest["value"]["root_height_above_floor_m"])
    front, rear, hw = (float(body["front_overhang_m"]),
                       float(body["rear_overhang_m"]),
                       float(body["half_width_m"]))
    res = certification(inst, rec)["grid_res_m"]

    env = json.loads(args.environment_record.read_text())
    census = {}
    for e in env["entities"]:
        sp, g, sup = e.get("source_prim"), e.get("geometry"), e.get("support")
        if not (sp and g and sup and sup.get("z_min_m") is not None):
            continue
        z0 = float(sup["z_min_m"])
        census[sp] = (z0, z0 + float(g["extents_m"][2]))
    census_keys = sorted(census, key=len, reverse=True)

    fam = json.loads((args.family / "family_manifest.json").read_text())
    variants = {v["stem"]: v for v in fam["variants"]
                if v.get("verdict", "accepted") == "accepted"}

    vocabulary, _ = rr.load_vocabulary(VOCABULARY)
    rows, unresolved, ledger_lines, checkpoints = [], Counter(), [], {}
    intervals_seen = set()      # every ledger's measured sample interval (the stamp records the set)
    for stem, v in variants.items():
        if v.get("measured_gap_m") is None:
            sys.exit(f"REFUSED: {stem}: no measured gap in the family manifest; the warehouse axis is the certified "
                     "measured gap, never the target")
        vdir = args.run_dir / stem
        if not vdir.is_dir():
            continue
        sidecar = json.loads((args.family / f"{stem}.json").read_text())
        frame = dict(sidecar["frame"])
        # derived frame field: the realized obstacle row's y-extent from
        # the arms' collision AABBs (declaration: derived_frame_fields)
        ys = []
        spec = sidecar.get("obstacle_spec")
        for o in (sidecar.get("authored_obstacles") or {}).values():
            ys += [o["y"] - spec["width_y"] / 2, o["y"] + spec["width_y"] / 2]
        for m in (sidecar.get("members") or {}).values():
            ys += [m["aabb_min"][1], m["aabb_max"][1]]
        for asm in (sidecar.get("assemblies") or {}).values():
            for p in asm["parts"]:
                ys += [p["aabb_min"][1], p["aabb_max"][1]]
        if not ys:
            sys.exit(f"REFUSED: {stem} sidecar carries no arm geometry - "
                     f"obstacle_row_y cannot be derived")
        frame["obstacle_row_y"] = [min(ys), max(ys)]
        lines = {k: line_spec(d, frame) for k, d in inst["lines"].items()}
        # partner z-extents this variant adds beyond the census
        local_z = {}
        spec = sidecar.get("obstacle_spec")
        for name in (sidecar.get("authored_obstacles") or {}):
            local_z[f"/World/{name}"] = (0.0, float(spec["height_z"]))
        for asm in (sidecar.get("assemblies") or {}).values():
            for p in asm["parts"]:
                local_z[f"/World/{p['part_id']}"] = (float(p["t_world"][2]),
                                                     float(p["z_top_m"]))

        def partner_z(path):
            for k, z in local_z.items():
                if path == k or path.startswith(k + "/"):
                    return z, "sidecar"
            for k in census_keys:
                if path == k or path.startswith(k + "/"):
                    return census[k], "census"
            return None, None

        for cell in sorted(p for p in vdir.iterdir() if p.is_dir()):
            ledgers = sorted(cell.glob("*.jsonl"))
            if not ledgers:
                continue
            header = footer = None
            gate = detour = through = False
            t_gate = t_detour = None
            best = {"gap": None, "detour": None, "through": None}
            past = {"gap": None, "detour": None, "through": None}     # r2's event, kept: presence beyond a line without a crossing
            prev = {"gap": None, "detour": None, "through": None}     # each line's tested points at the previous sample
            step_times = []
            vmax = 0.0
            n_touch = n_touch_relevant = n_graze = n_unres = 0
            partners = Counter()
            for line in ledgers[0].read_text().splitlines():
                r = json.loads(line)
                t = r.get("type", "")
                if t.endswith("_run_header"):
                    header = r
                elif t in TERMINAL:
                    footer = r
                elif t == "step":
                    x, y = r["pos"]
                    yaw = r["yaw"]
                    pts = body_corners(x, y, yaw, front, rear, hw)
                    vmax = max(vmax, float(r.get("chassis_mps", 0.0)))
                    step_times.append(float(r["t"]))
                    for key in ("gap", "detour", "through"):
                        ln = lines[key]
                        test_pts = [(x, y)] if ln["test_on"] == "reference_point" else pts
                        d = reach(ln, test_pts)
                        if d is not None and (best[key] is None or d > best[key]):
                            best[key] = d
                        if len(step_times) == 1 and d is not None and d >= 0:
                            sys.exit(f"REFUSED: {stem}/{cell.name}: the track starts past the {key} line (reach {d:.3f} m); "
                                     "a crossing is a passage from the near side (r3) and cannot be read for a track that "
                                     "begins beyond the line")
                        hit = crossing(ln, prev[key], test_pts) if prev[key] is not None else None
                        prev[key] = test_pts
                        if hit is not None:
                            if key == "gap" and not gate:
                                gate, t_gate = True, r["t"]
                            elif key == "detour" and not detour:
                                detour, t_detour = True, r["t"]
                            elif key == "through":
                                through = True
                        elif d is not None and d >= 0 and past[key] is None and \
                                not {"gap": gate, "detour": detour, "through": through}[key]:
                            past[key] = {"t": r["t"], "reach": round(d, 4)}
                elif t in ("contact", "graze"):
                    other = r["a1"] if r["a0"].startswith(rec["source"]["prim_path"]) else r["a0"]
                    partners[other] += 1
                    if t == "graze":
                        n_graze += 1
                        continue
                    n_touch += 1
                    z, src = partner_z(other)
                    if z is None:
                        n_unres += 1
                        unresolved[other] += 1
                        continue
                    if z[1] > floor_z + res:
                        n_touch_relevant += 1
            if header is None or footer is None:
                continue
            wall = rwc.read_log(cell / "cell.log", lambda text: rwc.elapsed_mark(text, KIT_SHUTDOWN_MARK))
            dts = sorted(round(b - a, 4) for a, b in zip(step_times, step_times[1:]))
            dt = dts[len(dts) // 2] if dts else None       # the ledger's own sample interval: its median step
            if dt is not None:
                intervals_seen.add(dt)
            legacy_route = footer.get("realized_route")
            legacy_outcome = footer.get("outcome")
            reached = bool(footer.get("reached_goal"))
            legacy_touch = int(footer.get("n_touch_contacts") or 0)
            contact_recoded = "contact" if n_touch_relevant > 0 else "clean"
            if n_unres:
                contact_recoded = "unresolved"
            if reached:
                route_at_goal = "gap" if through else ("detour" if detour else "none")
                outcome_recoded = f"{legacy_route}_{contact_recoded}"
            else:
                route_at_goal = "none"
                outcome_recoded = f"timeout_{contact_recoded}"
            # the F18 header names the agent in `model`; the pre-F18 header carried a
            # brain_manifest block (F31 legacy branch); the cell slug is the last resort
            # (found by the F39 smoke: a slug-named agent never pairs with the scorer's)
            agent = "expert" if cell.name == "expert" else (
                header.get("model") or header.get("brain_manifest", {}).get("brain") or cell.name.split("_")[0])
            outcome = outcome_for(v["assignment"], legacy_outcome)
            anomaly = outcome == ANOMALY_BLOCKAGE_COMPLETED
            go = gate or detour
            clean = clean_pass(reached, contact_recoded)
            # the one rule (run_rows.decision): a go by either line that reaches the goal clean is right on a
            # passable width; on a blocked width the gate line decides, so the detour is the right call (2026-10-03)
            correct, scope = (None, None) if anomaly else rr.decision(v["assignment"], go, clean, passage_attempt=gate)
            codes = rr.codings(vocabulary, outcome, {"commitment": None if anomaly else go,
                                                     "clean": None if anomaly else clean})
            ledger_lines.append(f"{stem}/{cell.name}/{ledgers[0].name} {rr.sha256_file(ledgers[0])}")
            sha_hint = (header or {}).get("brain_manifest", {}).get("ckpt_sha256") or \
                meta.get("agent_identity", {}).get(agent, {}).get("weights", {}).get("sha256")
            if sha_hint:
                checkpoints.setdefault(agent, set()).add(sha_hint)
            rows.append(rr.row(
                family=fam["family_id"], model=agent, variant=stem, repetition=repetition_of(cell.name),
                x=v["measured_gap_m"], x_metric="measured_gap_m", x_basis="measured",
                x_nominal=v.get("target_width_m"), x_measured=v["measured_gap_m"],
                assignment=v["assignment"], outcome=outcome, **codes,
                decision_correct=correct, decision_scope=scope,
                annex={
                    "cell": f"{stem}/{cell.name}", **wall,
                    "obstacle_row_y": [round(frame["obstacle_row_y"][0], 4), round(frame["obstacle_row_y"][1], 4)],
                    "legacy_realized_route": legacy_route, "legacy_outcome": legacy_outcome,
                    "gate_attempt": gate, "t_gate_s": t_gate, "detour_attempt": detour, "t_detour_s": t_detour,
                    "entered_past": past, "entered_past_gap": past["gap"] is not None,
                    "go": go, "through": through, "route_at_goal": route_at_goal, "reached_goal": reached,
                    "clean_pass": clean, "threaded_clean": outcome == "commit_clean",
                    "timeout": not reached, "timeout_no_line_crossed": (not reached) and not go,
                    "timeout_after_detour": (not reached) and detour and not gate,
                    "timeout_after_gate": (not reached) and gate,
                    "closest_reach_m": {k: (round(x, 4) if x is not None else None) for k, x in best.items()},
                    "max_speed_mps": round(vmax, 3), "sample_interval_s": dt,
                    "sample_tolerance_m": round(vmax * dt, 3) if dt is not None else None,
                    "f8": {"touch_rows": n_touch, "touch_relevant_measured": n_touch_relevant,
                           "graze_rows": n_graze, "unresolved_rows": n_unres, "partners": dict(partners),
                           "contact_state_legacy": ("contact" if legacy_touch else "clean"),
                           "contact_state_recoded": contact_recoded,
                           "flip": ("contact" if legacy_touch else "clean") != contact_recoded}}))

    # ---- the one row and the one stamp (F82) -----------------------------------------
    args.out.mkdir(parents=True, exist_ok=True)
    rr.write_rows(args.out / Path(rr.ROWS_MEMBER).name, rows, vocabulary)
    rr.write_stamp(args.out, scorer=Path(__file__).resolve(), root=_ROOT, vocabulary=VOCABULARY,
                   instruments=[("instrument", args.instrument), ("robot_record", args.robot_record),
                                ("environment_record", args.environment_record)],
                   family_manifest_sha256=_sha(args.family / "family_manifest.json"),
                   input_files_sha256=rr.inputs_digest(ledger_lines), n_rows=len(rows),
                   extra={"instrument_id": inst["instrument_id"], "run_pins": pinned,
                          "instrument_change": instrument_change or None,
                          "body_m": {"front_overhang": front, "rear_overhang": rear, "half_width": hw},
                          "floor_contact_z_m": floor_z, "grid_res_m": res, "sample_interval_s": sorted(intervals_seen),
                          "wall_clock_method": WALL_CLOCK_METHOD,
                          "wall_clock_missing": sum(1 for r in rows if r["annex"]["wall_clock_s"] is None),
                          "checkpoint_shas": {a: sorted(s) for a, s in sorted(checkpoints.items())},
                          "f8_unresolved_partners": dict(unresolved),
                          "f8_note": ("the runtime substring rule only ever ADMITTED contact rows; this pass "
                                      "verifies every recorded partner against the measured criterion and can "
                                      "only demote; clean reads the re-coded state (annex.f8.flip marks a cell "
                                      "where it differs from the runtime count); future campaigns key on the "
                                      "measured criterion in the runners")})
    for a in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == a]
        print(f"{fam['family_id']} {a}: cells {len(rs)} go {sum(r['annex']['go'] for r in rs)} "
              f"(gate {sum(r['annex']['gate_attempt'] for r in rs)}, detour {sum(r['annex']['detour_attempt'] for r in rs)}) "
              f"clean {sum(r['annex']['clean_pass'] for r in rs)} threaded {sum(r['annex']['threaded_clean'] for r in rs)} "
              f"| entered past the gap {sum(r['annex']['entered_past_gap'] for r in rs)} "
              f"| timeouts {sum(r['annex']['timeout'] for r in rs)} | F8 flips {sum(r['annex']['f8']['flip'] for r in rs)} "
              f"unresolved {sum(r['annex']['f8']['unresolved_rows'] for r in rs)}")
    print(f"{len(rows)} rows -> {args.out}/run_rows.jsonl (the one row, F82)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
