#!/usr/bin/env python3
"""NPC collision detector for the flow families (F57, 2026-09-29; Milan: "just to see which variants
are affected; if the first hit is ego vs NPC, that's fine"). Read-only over trajectory extracts.

Per run: every pair of scenario cars (actors with role_name "scenario" and a track) is tested frame
by frame for body overlap, oriented rectangles from the track's x, y, yaw with the registry's half
length and the certificate's width rule (the bodies flow_gap_certificate.py uses); the ego's first
contact is the earliest of the extract's recorded collision events and the first frame the ego body
overlaps a scenario car. A run is
  npc_first   two scenario cars touch before the ego touches anything: the scenario's traffic is not
              the declared one (the register's evidence);
  ego_first   the ego's contact comes first; car-car contacts after it are its consequence;
  none        no scenario car touches another.
Beside it, a speed-reset signature per car (a drop of more than DROP_MPS within one step, then a hold
within HOLD_TOL for HOLD_S) is REPORTED, never used as the label: scripted queue stops fire it too.

    conda run -n scl_prototype python flow_npc_collisions.py --runs <root> [<root> ...] --out DIR
where each root holds <campaign>/<family>/<agent>/<seed>/trajectories/*_rep0.json.gz (an unpacked raw
bundle). Writes DIR/runs.jsonl and DIR/summary.json; prints the per-variant, per-seed table."""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow_gap_certificate as fgc  # noqa: E402
import score_runs as sr  # noqa: E402

DROP_MPS, HOLD_S, HOLD_TOL = 3.0, 1.0, 0.05
SEEDS = ("seed_0", "seed_1", "seed_2")


def speeds(track, dt):
    v = [0.0]
    for i in range(1, len(track["frames"])):
        df = max(track["frames"][i] - track["frames"][i - 1], 1)
        v.append(math.hypot(track["x"][i] - track["x"][i - 1], track["y"][i] - track["y"][i - 1]) / (df * dt))
    return v


def speed_resets(track, dt):
    """Frames where the speed drops by more than DROP_MPS in one step and then holds for HOLD_S."""
    v = speeds(track, dt)
    hold = max(2, int(HOLD_S / dt))
    return [track["frames"][i] for i in range(1, len(v) - hold)
            if v[i - 1] - v[i] > DROP_MPS and max(v[i:i + hold]) - min(v[i:i + hold]) <= HOLD_TOL]


def first_overlap(ta, tb, hxa, hya, hxb, hyb):
    """The first common frame where the two bodies overlap (centre-distance prefilter, then the polygons)."""
    index_b = {f: i for i, f in enumerate(tb["frames"])}
    reach = math.hypot(hxa, hya) + math.hypot(hxb, hyb)
    for i, f in enumerate(ta["frames"]):
        j = index_b.get(f)
        if j is None or math.hypot(ta["x"][i] - tb["x"][j], ta["y"][i] - tb["y"][j]) > reach:
            continue
        if fgc._body(ta, i, hxa, hya).intersects(fgc._body(tb, j, hxb, hyb)):
            return f
    return None


def analyze(data, extents=None):
    """The classification of one extract (see the module docstring)."""
    extents = extents if extents is not None else {}
    dt = float(data.get("dt_s", 0.05))
    hero = data["tracks"][str(data["hero_id"])]
    cars = []
    for aid, meta in data["actors"].items():
        if meta.get("role_name") != "scenario" or not data["tracks"].get(aid, {}).get("frames"):
            continue
        hx, hy = fgc._car_half_extents(meta.get("blueprint", ""), extents)
        cars.append((aid, data["tracks"][aid], hx, hy))
    cars.sort(key=lambda c: c[1]["frames"][0])
    pair_hits = []
    for a in range(len(cars)):
        for b in range(a + 1, len(cars)):
            f = first_overlap(cars[a][1], cars[b][1], cars[a][2], cars[a][3], cars[b][2], cars[b][3])
            if f is not None:
                pair_hits.append({"frame": f, "cars": [cars[a][0], cars[b][0]]})
    pair_hits.sort(key=lambda h: h["frame"])
    ego_track = [first_overlap(hero, c[1], fgc.EGO_HX, fgc.EGO_HY, c[2], c[3]) for c in cars]
    recorded = data.get("collisions") or []
    recorded_frames = [e["frame"] for e in recorded if isinstance(e, dict) and e.get("frame") is not None]
    ego_first = min([f for f in ego_track + recorded_frames if f is not None], default=None)
    resets = {c[0]: [round(f * dt, 2) for f in speed_resets(c[1], dt)] for c in cars}
    resets = {k: v for k, v in resets.items() if v}
    npc_first = pair_hits[0]["frame"] if pair_hits else None
    if npc_first is not None and (ego_first is None or npc_first < ego_first):
        cls = "npc_first"
    elif npc_first is not None:
        cls = "ego_first"
    else:
        cls = "none"
    return {"class": cls, "n_scenario_cars": len(cars), "npc_pair_hits": pair_hits,
            "first_npc_npc_s": round(npc_first * dt, 2) if npc_first is not None else None,
            "first_ego_contact_s": round(ego_first * dt, 2) if ego_first is not None else None,
            "recorded_collisions": len(recorded), "speed_resets": resets}


def run_files(roots):
    files = []
    for root in roots:
        files += glob.glob(str(Path(root) / "**/trajectories/*_rep0.json.gz"), recursive=True)
    return sorted(set(files))


def describe(path):
    """(campaign, family, agent, seed, variant) from an unpacked raw bundle's path."""
    parts = Path(path).parts
    family = next(p for p in parts if p.startswith("fm_"))
    i = parts.index(family)
    campaign = next(p for p in parts if p.startswith("campaign_"))
    variant = int(Path(path).name.replace("_rep0.json.gz", "")[-2:])
    return campaign, family, parts[i + 1], parts[i + 2], variant


def summarize(rows):
    """{family: {variant: {seed: {npc_first, ego_first, runs}}}} and the affected cells."""
    out = {}
    for r in rows:
        cell = out.setdefault(r["family"], {}).setdefault(r["variant"], {}).setdefault(
            r["seed"], {"npc_first": 0, "ego_first": 0, "runs": 0, "speed_resets": 0})
        cell["runs"] += 1
        if r["class"] in ("npc_first", "ego_first"):
            cell[r["class"]] += 1
        cell["speed_resets"] += bool(r["speed_resets"])
    return out


def render(summary):
    lines = []
    for family, variants in sorted(summary.items()):
        lines.append(f"== {family}   (per seed: npc_first / ego_first / runs)")
        lines.append(f"{'variant':8} " + " ".join(f"{s:>12}" for s in SEEDS) + "   verdict")
        for variant, seeds in sorted(variants.items()):
            cells, affected = [], []
            for s in SEEDS:
                c = seeds.get(s, {"npc_first": 0, "ego_first": 0, "runs": 0})
                cells.append(f"{c['npc_first']}/{c['ego_first']}/{c['runs']}")
                if c["npc_first"]:
                    affected.append(s)
            verdict = ("npc_first on " + ", ".join(affected)) if affected else "clean"
            lines.append(f"v{variant:02d}     " + " ".join(f"{c:>12}" for c in cells) + f"   {verdict}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", nargs="+", required=True, type=Path, help="unpacked raw bundle roots (repeatable)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    files = run_files(args.runs)
    if not files:
        print("REFUSED: no <campaign>/<family>/<agent>/<seed>/trajectories/*_rep0.json.gz under the roots", file=sys.stderr)
        return 1
    extents = sr._registry_extents()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    with open(args.out / "runs.jsonl", "w") as f:
        for n, path in enumerate(files):
            campaign, family, agent, seed, variant = describe(path)
            data = json.load(gzip.open(path, "rt"))
            r = {"campaign": campaign, "family": family, "agent": agent, "seed": seed, "variant": variant,
                 **analyze(data, extents)}
            rows.append(r)
            f.write(json.dumps(r) + "\n")
            if n % 50 == 0:
                print(f"{n}/{len(files)} {campaign} {family} {agent} {seed} v{variant:02d}: {r['class']}", flush=True)
    summary = summarize(rows)
    (args.out / "summary.json").write_text(json.dumps(
        {"tool": Path(__file__).name, "tool_sha256": fgc.hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         "roots": [str(r) for r in args.runs], "n_runs": len(rows), "summary": summary}, indent=1) + "\n")
    print(f"\n{len(rows)} runs\n" + render(summary) + f"\n\nrows -> {args.out / 'runs.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
