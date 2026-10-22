#!/usr/bin/env python3
"""Score one campaign: the whole derived folder from one raw results root.

The one scoring entry point on both backends (F30, Milan ruling 2026-09-14).
It takes an unpacked results root (`unpack_campaign_bundle.py`, or the
runner's own results root), the campaign's declarations, and the
instruments the chains need AS ARGUMENTS, and runs the existing tools as
steps, always recomputing into a fresh --out (present is not fresh). It
dispatches on data: the declaration's backend, and on CARLA each family's
PLAN (F36, 2026-09-15: its one scheduled factor is a behavior-scalar
binding, so the flow chain measures it at the declared conflict zone, or a
geometric or categorical one, so the static-threading chain does; nobody
writes which chain a family takes, `campaign_manifest.scoring_chain` reads
it). A campaign holding both kinds runs both chains into ONE --out.

    threading (fail2drive):  score_runs -> route_metrics -> fit_rows -> emit_findings
    flow (fail2drive):       flow_aggregate -> fit_rows -> emit_findings, then
                             audit_flow_commitment -> verify_flow_map (under
                             the fail2drive env); needs --zone --map-excerpt --map
    isaac:                   derive_go_clean -> fit_rows -> emit_findings

One pipeline (F82, 2026-10-11): each chain's scorer writes the one row
(scl_core.run_rows) under the one stamp, and the fit and the emitter are
the same two scripts for every chain. After the lanes, the fit and the
emitter run once more into <out>/repeat/ and every document must come
out byte-identical (the determinism repeat, one for every chain).

The steps are LANES (F67 item 5, 2026-10-08): a family's steps run in order
inside its lane, lanes are independent and run side by side under --jobs,
and a barrier (the threading regeneration) waits for the lanes it reads.
The first step runs alone and its peak resident size, read from the
children's rusage, admits the rest: a lane launches only while (running + 1)
x peak stays under the memory available at start, re-read after every step;
a wait is a named case in the record. Each step's output lands in
<out>/logs/ and is echoed when the step ends. --jobs 1 (the default) is the
same code, one step at a time. Memory per step measured 2026-10-08 on the
largest v08 family: 113 MB (score_runs), 114 MB (route_metrics), 113 MB
(the Isaac scorer); v01 goes from 293 s serial to about 195 s (its route
metrics of the 12-variant family), v08 from 87 s to about 33 s.

Every chain leaves <out>/<campaign_id>/<family>/... - ONE derived folder
per campaign, whichever chains ran: scoring_out/ (run_rows.jsonl and its
stamp), stats/, findings/, the threading route metrics under metrics/,
the flow audit rows under audit/ (which the flow certifier's --stage
completes in place before the freeze). `campaign_manifest.py --produce-derived` freezes it against the
raw bundle. Every run writes <out>/scoring_record.json: the chains, the
steps, their tools by hash, the declarations by hash, the runtime.

    conda run -n scl_prototype python scripts/score_campaign.py \\
        --results-root /work/rescore --out /work/scores/v06 \\
        --campaign artifacts/examples/campaigns/campaign_carla_v06/fm_001_intrusion_sweep.json
"""
import argparse
import json
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core.campaign_bundles import measure_runtime  # noqa: E402
from scl_core.campaign_declaration import check_family_manifest, load_declarations, resolve_family_manifest  # noqa: E402
from scl_core.records import portable_path, render, sha256_file as sha  # noqa: E402

CARLA_MME = ROOT / "adapters/fail2drive/scripts/multi_model_eval"
ISAAC_MME = ROOT / "adapters/isaac/scripts/multi_model_eval"
FIT = ROOT / "scripts/fit_rows.py"
EMIT = ROOT / "scripts/emit_findings.py"
RECORD_KIND = "scoring_record/v0.2"


class Plan:
    """The steps of one scoring run, in plan order: label, argv, env, lane, after. Steps of one lane
    run in that order; lanes run side by side under --jobs; `after` names the lanes a step waits for."""

    def __init__(self):
        self.steps = []

    def add(self, label, argv, env=None, lane=None, after=()):
        self.steps.append({"label": label, "argv": [str(a) for a in argv], "env": env,
                           "lane": lane or label, "after": tuple(after)})

    def lanes(self) -> dict:
        """{lane: [step indices]} in plan order."""
        out = {}
        for i, s in enumerate(self.steps):
            out.setdefault(s["lane"], []).append(i)
        return out


def fit_and_emit(plan, d, sub_out, manifest, defects, repeat=None):
    """The two stages every chain shares, after its scorer: the fit over the one row shape and the one
    emitter, into <sub_out>/stats and <sub_out>/findings (or under `repeat`, the determinism repeat)."""
    base = Path(repeat) / d.results_subtree if repeat else sub_out
    defect_args = ["--defects", defects] if defects is not None else []
    plan.add(f"fit_rows {d.family_id}" + (" (repeat)" if repeat else ""),
             [FIT, "--rows", sub_out / "scoring_out", "--out", base / "stats", "--quiet", *defect_args], lane=d.family_id)
    plan.add(f"emit_findings {d.family_id}" + (" (repeat)" if repeat else ""),
             [EMIT, "--rows", sub_out / "scoring_out", "--stats", base / "stats", "--campaign", d.path,
              "--family-manifest", manifest, "--out", base / "findings", *defect_args], lane=d.family_id)


def threading_plan(results, out, declarations, manifests, plan, defects=None):
    for d in declarations:
        run_dir, sub_out = results / d.results_subtree, out / d.results_subtree
        plan.add(f"score_runs {d.family_id}",
                 [CARLA_MME / "score_runs.py", run_dir, "--out", sub_out / "scoring_out", "--quiet"], lane=d.family_id)
        # the route metrics (minimum clearance, mirror band, TTC, displacements) are an interpretation of the
        # same runs and travel in the same derived bundle (2026-09-22; F3 reads them from the pools)
        plan.add(f"route_metrics {d.family_id}",
                 [CARLA_MME / "route_metrics.py", run_dir, "--out", sub_out / "metrics", "--quiet"], lane=d.family_id)
        fit_and_emit(plan, d, sub_out, manifests[d.family_id], defects)
    return plan


def flow_plan(results, out, declarations, manifests, args, plan, defects=None):
    certificates = {}
    for spec in args.gap_certificate or []:
        family, _, path = spec.partition("=")
        if not family or not path:
            raise ValueError(f"--gap-certificate takes FAMILY=PATH, got {spec!r}")
        certificates[family] = Path(path)
    undeclared = sorted(set(certificates) - {d.family_id for d in declarations})
    if undeclared:
        raise ValueError(f"--gap-certificate names families this run does not declare: {undeclared}")
    campaigns = sum((["--campaign", d.path] for d in declarations), [])
    for d in declarations:
        run_dir, sub_out = results / d.results_subtree, out / d.results_subtree
        cert = certificates.get(d.family_id)
        if cert is None:
            print(f"{d.campaign_id}/{d.family_id}: no gap certificate declared; scored without bands or decisions", flush=True)
        plan.add(f"flow_aggregate {d.family_id}",
                 [CARLA_MME / "flow_aggregate.py", run_dir, "--manifest", manifests[d.family_id], "--zone", args.zone,
                  "--map-excerpt", args.map_excerpt, "--out", sub_out / "scoring_out", "--quiet",
                  *(["--gap-certificate", cert] if cert else [])], lane=d.family_id)
        fit_and_emit(plan, d, sub_out, manifests[d.family_id], defects)
    # the audits read every flow family's rows and extracts: one lane after the families
    plan.add("audit_flow_commitment",
             [CARLA_MME / "audit_flow_commitment.py", "--results-root", results, "--scores-root", out,
              "--zone", args.zone, *campaigns], lane="flow_audit", after=[d.family_id for d in declarations])
    plan.add("verify_flow_map",
             [CARLA_MME / "verify_flow_map.py", "--map", args.map, "--zone", args.zone, "--map-excerpt",
              args.map_excerpt, "--results", results, "--evidence-dir", out,
              "--out", out / "audit/map_verification.json", *campaigns], env="fail2drive", lane="flow_audit")
    return plan


def isaac_plan(results, out, declarations, manifests, args, plan, defects=None):
    for d in declarations:
        run_dir, sub_out = results / d.results_subtree, out / d.results_subtree
        plan.add(f"derive_go_clean {d.family_id}",
                 [ISAAC_MME / "derive_go_clean.py", "--run-dir", run_dir, "--family", Path(manifests[d.family_id]).parent,
                  "--robot-record", args.robot_record, "--environment-record", args.environment_record,
                  "--instrument", args.instrument, "--out", sub_out / "scoring_out",
                  *(["--accept-instrument-change"] if getattr(args, "accept_instrument_change", False) else [])],
                 lane=d.family_id)
        fit_and_emit(plan, d, sub_out, manifests[d.family_id], defects)
    return plan


def repeat_plan(out, declarations, manifests, defects=None) -> Plan:
    """The determinism repeat (one for every chain): the fit and the emitter once more from the same rows into
    <out>/repeat/, compared document by document by the caller."""
    plan = Plan()
    for d in declarations:
        fit_and_emit(plan, d, out / d.results_subtree, manifests[d.family_id], defects, repeat=out / "repeat")
    return plan


def repeat_identical(out, declarations) -> dict:
    """{family: True} when every document of the repeat hashes to the lanes' document; a difference names the file."""
    verdict = {}
    for d in declarations:
        first, again = out / d.results_subtree / "findings", out / "repeat" / d.results_subtree / "findings"
        a = {p.name: sha(p) for p in sorted(first.glob("*.json"))}
        b = {p.name: sha(p) for p in sorted(again.glob("*.json"))}
        verdict[d.family_id] = a == b and bool(a)
        if a != b:
            print(f"NON-DETERMINISTIC findings for {d.campaign_id}/{d.family_id}: "
                  f"{sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))}", file=sys.stderr, flush=True)
    return verdict


def mem_available_kb():
    """MemAvailable of /proc/meminfo in kB, or None where there is none (the admission then has no
    measure of the box and --jobs alone decides; recorded as such)."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except OSError:
        pass
    return None


def children_peak_kb() -> int:
    """The largest resident set any finished child reached so far (rusage, kB on Linux)."""
    return int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)


def step_command(step, conda_exe) -> list:
    env = step["env"]
    return ([conda_exe, "run", "-n", env, "--no-capture-output", "python"] if env else [sys.executable]) + step["argv"]


def run_plan(plan, conda_exe, jobs=1, logs=None, mem_kb=None, peak_of=children_peak_kb, root=ROOT):
    """Run the plan's lanes side by side, at most `jobs` steps at once (F67 item 5). The first step
    runs alone; the peak resident size of the finished children (`peak_of`) then admits a launch only
    while (running + 1) x peak <= the memory available at start (`mem_kb`, MemAvailable unless given),
    re-read after every step; a launch held back by that rule is recorded (`admission_waited`). Each
    step's output goes to <logs>/<n>_<label>.log and is echoed when the step ends, so the driver's
    stdout reads the same at any width. A failed step stops new launches, the running ones finish,
    and the run is incomplete; a single step always runs, so the serial path never waits. Returns
    (steps in plan order, ok, the admission record); `root` is what the tools' paths are recorded
    relative to."""
    logs = Path(logs) if logs is not None else None
    if logs is not None:
        logs.mkdir(parents=True, exist_ok=True)
    lanes = plan.lanes()
    mem = mem_available_kb() if mem_kb is None else mem_kb
    records = [None] * len(plan.steps)
    done, done_lanes, running = set(), set(), {}
    peak, waited, max_running, failed = None, False, 0, False

    def ready(i):
        s = plan.steps[i]
        earlier = [j for j in lanes[s["lane"]] if j < i]
        return all(j in done for j in earlier) and all(lane in done_lanes for lane in s["after"])

    def admitted():
        if len(running) >= jobs:
            return False
        if not running:
            return True                              # one step always runs: the serial path never waits
        if peak is None:
            return False                             # the first step runs alone: it is the measurement
        if mem is None:
            return True                              # no measure of the box: --jobs alone decides
        return (len(running) + 1) * peak <= mem

    while len(done) < len(plan.steps):
        if not failed:
            for i, step in enumerate(plan.steps):
                if i in done or i in running or not ready(i):
                    continue
                if not admitted():
                    if running and peak is not None and mem is not None and (len(running) + 1) * peak > mem:
                        waited = True
                    break
                log = logs / f"{i:02d}_{step['label'].replace(' ', '_')}.log" if logs is not None else None
                handle = log.open("wb") if log is not None else subprocess.DEVNULL
                print(f">>> {step['label']} (lane {step['lane']})", flush=True)
                proc = subprocess.Popen(step_command(step, conda_exe), stdout=handle, stderr=subprocess.STDOUT)
                running[i] = (proc, time.time(), handle, log)
                max_running = max(max_running, len(running))
        if not running:
            if failed:
                break
            raise RuntimeError("no step can run and none is running: a lane waits for a lane that never completes")
        time.sleep(0.05)
        for i, (proc, t0, handle, log) in list(running.items()):
            rc = proc.poll()
            if rc is None:
                continue
            if handle is not subprocess.DEVNULL:
                handle.close()
            step = plan.steps[i]
            records[i] = {"label": step["label"], "lane": step["lane"],
                          "tool": portable_path(Path(step["argv"][0]), root), "tool_sha256": sha(step["argv"][0]),
                          "env": step["env"], "argv": step["argv"][1:], "returncode": rc,
                          "seconds": round(time.time() - t0, 1)}
            del running[i]
            done.add(i)
            if all(j in done for j in lanes[step["lane"]]):
                done_lanes.add(step["lane"])
            peak = max(peak or 0, peak_of())
            print(f"--- {step['label']}: exit {rc} in {records[i]['seconds']} s", flush=True)
            if log is not None:
                text = log.read_bytes().decode("utf-8", errors="replace")
                if text.strip():
                    print(text.rstrip(), flush=True)
            if rc != 0:
                failed = True
                print(f"STEP FAILED: {step['label']} (exit {rc}); the chain stops here, nothing is bundled",
                      file=sys.stderr, flush=True)
    admission = {"requested": jobs, "lanes": len(lanes), "max_concurrent": max_running, "step_peak_rss_kb": peak,
                 "mem_available_kb": mem, "admission_waited": waited,
                 "not_run": [s["label"] for i, s in enumerate(plan.steps) if i not in done]}
    return [r for r in records if r is not None], not failed, admission


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-root", type=Path, required=True,
                    help="the unpacked raw results root (<root>/<campaign>/<family>/...)")
    ap.add_argument("--campaign", action="append", required=True, type=Path, metavar="DECLARATION",
                    help="the campaign's declaration(s), one per family (repeatable); one campaign per run")
    ap.add_argument("--out", type=Path, required=True,
                    help="a NEW directory for the derived folder (<out>/<campaign>/<family>/...); refused if present")
    ap.add_argument("--plan-only", action="store_true", help="print the steps, run nothing")
    ap.add_argument("--conda-exe", default="conda", help="for the steps that run under another env")
    ap.add_argument("--jobs", type=int, default=1,
                    help="lanes run side by side, at most this many steps at once (default 1: one step at a time); "
                         "admitted by the measured peak of the finished steps against the memory available (F67)")
    flow = ap.add_argument_group("flow instruments (fail2drive; required when a declared family's plan "
                                 "schedules a behavior-scalar factor, refused otherwise)")
    flow.add_argument("--zone", type=Path, help="the conflict-zone instrument document")
    flow.add_argument("--map-excerpt", type=Path, help="the pinned OpenDRIVE excerpt")
    flow.add_argument("--map", type=Path, help="the full OpenDRIVE map, for the map check")
    flow.add_argument("--gap-certificate", action="append", metavar="FAMILY=PATH",
                      help="the declared flow gap certificate of one flow family (F55), repeatable; a flow "
                           "family without one is scored without bands or decisions")
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register (scl_core/scenario_defects.py, one register for "
                         "both backends): the fit and the emitter of every chain take a registered cell out of "
                         "every curve and pin the register (the flow chain also reads it through the gap certificates)")
    isaac = ap.add_argument_group("isaac instruments")
    isaac.add_argument("--robot-record", type=Path)
    isaac.add_argument("--environment-record", type=Path)
    isaac.add_argument("--instrument", type=Path, help="the declared scoring instrument document")
    isaac.add_argument("--accept-instrument-change", action="store_true",
                       help="score under a robot record or family that differ from what the run pinned, recording "
                            "the change (derive_go_clean.py); refused otherwise")
    args = ap.parse_args()

    try:
        declarations = load_declarations(args.campaign)
    except ValueError as exc:
        ap.error(str(exc))
    backends = {d.backend for d in declarations}
    campaigns = {d.campaign_id for d in declarations}
    if len(backends) != 1:
        ap.error(f"one backend per run; got {sorted(backends)}")
    backend = backends.pop()
    results, out = args.results_root.resolve(), args.out.resolve()
    # the family as run, from each raw tree (principle 13), checked against the declaration's pin before any step
    manifests = {}
    for d in declarations:
        path, _ = resolve_family_manifest(d, results / d.results_subtree, ROOT)
        if not args.plan_only:
            try:
                check_family_manifest(d, path)
            except (OSError, ValueError) as exc:
                ap.error(str(exc))
        manifests[d.family_id] = path
    plan, chains = Plan(), []
    if backend == "fail2drive":
        if str(CARLA_MME) not in sys.path:
            sys.path.insert(0, str(CARLA_MME))
        import campaign_manifest as carla_manifest   # noqa: E402  (the family's plan says which chain)
        by_chain = {"threading": [], "flow": []}
        try:
            for d in declarations:
                by_chain[carla_manifest.scoring_chain(d)].append(d)
        except (ValueError, OSError, KeyError) as exc:
            ap.error(f"cannot read which chain scores a declared family: {exc}")
        chains = [c for c in ("threading", "flow") if by_chain[c]]
        flow_instruments = ("zone", "map_excerpt", "map")
        given = [f for f in flow_instruments if getattr(args, f) is not None]
        if by_chain["flow"]:
            missing = [f for f in flow_instruments if getattr(args, f) is None]
            if missing:
                ap.error("the flow chain needs " + ", ".join("--" + m.replace("_", "-") for m in missing))
        elif given or args.gap_certificate:
            ap.error("flow instruments passed (" + ", ".join("--" + g.replace("_", "-") for g in given) +
                     ") but no declared family's plan schedules a behavior-scalar factor: nothing here is "
                     "scored by the flow chain")
        # the threading record is one file per campaign, so a run holding
        # threading families takes one campaign; a flow-only run may
        # remeasure several campaigns into one scores root
        if by_chain["threading"] and len(campaigns) != 1:
            ap.error(f"one campaign per run when threading families are declared; got {sorted(campaigns)}")
        if by_chain["threading"]:
            threading_plan(results, out, by_chain["threading"], manifests, plan, args.defects)
        if by_chain["flow"]:
            try:
                flow_plan(results, out, by_chain["flow"], manifests, args, plan, args.defects)
            except ValueError as exc:
                ap.error(str(exc))
    elif backend == "isaac":
        if len(campaigns) != 1:
            ap.error(f"one campaign per run for the isaac chain; got {sorted(campaigns)}")
        missing = [f for f in ("robot_record", "environment_record", "instrument") if getattr(args, f) is None]
        if missing:
            ap.error("the isaac chain needs " + ", ".join("--" + m.replace("_", "-") for m in missing))
        chains = ["isaac"]
        isaac_plan(results, out, declarations, manifests, args, plan, args.defects)
    else:
        ap.error(f"no scoring chain for backend {backend!r} (known: fail2drive, isaac)")

    if args.jobs < 1:
        ap.error("--jobs takes a positive count")
    print(f"campaign {', '.join(sorted(campaigns))} on {backend}: {'+'.join(chains)} chain, "
          f"{len(plan.steps)} steps in {len(plan.lanes())} lanes, --jobs {args.jobs} -> {out}")
    for s in plan.steps:
        where = f"lane {s['lane']}" + (f", after {', '.join(s['after'])}" if s["after"] else "")
        print(f"  [{s['env'] or 'scl_prototype'}] {s['label']} ({where}): {Path(s['argv'][0]).name} {' '.join(s['argv'][1:])}")
    if args.plan_only:
        return 0
    if out.exists():
        print(f"REFUSED: {out} exists; a scoring run recomputes everything into a fresh directory "
              "(present is not fresh)", file=sys.stderr)
        return 2
    missing_trees = [str(results / d.results_subtree) for d in declarations if not (results / d.results_subtree).is_dir()]
    if missing_trees:
        print(f"REFUSED: no run tree at {missing_trees} under --results-root", file=sys.stderr)
        return 2
    out.mkdir(parents=True)
    steps, ok, admission = run_plan(plan, args.conda_exe, args.jobs, out / "logs")
    repeat = {}
    if ok:
        again, ok_again, _ = run_plan(repeat_plan(out, declarations, manifests, args.defects), args.conda_exe, args.jobs,
                                      out / "logs/repeat")
        steps += again
        repeat = repeat_identical(out, declarations) if ok_again else {d.family_id: False for d in declarations}
        ok = ok_again and all(repeat.values())
    record = {"record_kind": RECORD_KIND, "driver_sha256": sha(Path(__file__)), "backend": backend, "chains": chains,
              "campaign_id": sorted(campaigns),
              "declarations": [d.record(ROOT) for d in declarations],
              "results_root": str(results), "complete": ok, "jobs": admission, "steps": steps,
              "repeated_findings_identical": repeat,
              "runtime": measure_runtime()}
    (out / "scoring_record.json").write_text(render(record))
    print(f"{'complete' if ok else 'INCOMPLETE'}: {out / 'scoring_record.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
