#!/usr/bin/env python3
"""campaign: run, freeze, score, store and register a campaign through the tools that exist.

One entry point for the campaign procedure on both backends (F39, Milan
ruling 2026-09-15). Every verb is orchestration over the tools that exist
(the runners, the manifest tools, the unpack tool, the scoring driver, the
certifiers, the Wilson extractor); no tool underneath changes its
contract, every verb prints the commands it runs before running them
(--plan-only prints and stops), and the first failure stops the verb with
the tool's own message. Nothing here names a campaign, a family, a model
or an instrument: they come from the spec, the trees, the bundles and the
canon directory.

    run           <spec>            the runner per family (resumable; --families is the multi-node split)
    freeze-raw    --results-root R --campaign-id ID --out DIR
                                    verify + freeze the raw zip (the declarations travel inside it)
    score         --raw ZIP --work W --instruments FILE [--jobs N] [--map XODR]
                                    unpack (scoring inputs only) + the scoring driver (adopts the declarations into
                                    canon; a bundle from before in-bundle declarations takes canon's, written after
                                    the fact; families as lanes, --jobs at once, admitted by the measured step peak)
    freeze-derived --scores W/scores --campaign-id ID --bundles DIR --results-root W/unpacked --instruments FILE --work W2
                                    stage (flow), freeze the ONE derived zip, bind this campaign's records
                                    into W2/records/<id>/ (the real documents, not yet in canon)
    store-derived --campaign-id ID --work W2 --bundles DIR
                                    the findings and the records W2 holds into the store (one record file per
                                    campaign and chain under findings/certification/<id>/), the interpretation
                                    cache pruned; touches no other campaign and nothing outside the store, so it
                                    works in any checkout that holds the store (a reviewer's clone included)
    rebuild-register --bundles DIR [--no-suite]
                                    what derives from the store in THIS repository: the register tables of
                                    both CAMPAIGN_NOTES, the Wilson fixture (every flow record and the
                                    bundles), then the suite; once after a chain, not per campaign
    prune-cache   [--archive DIR] [--cache DIR]
                                    remove the interpretation-cache entries no certification record pins
                                    (the cache sits beside the archive, F72; store-derived runs this)
    stale         --bundles DIR [--campaign ID ...] [--out FILE]
                                    every interpretation the records pin against this checkout: its listed
                                    sources, its scoring stamps' canon inputs and its documents' path pins by
                                    hash, both backends; exit 1 when any is stale. The re-stamp set is read
                                    here, never inferred from where the values could move (F81)
    table         [--write] [--check]  the campaign register blocks of both CAMPAIGN_NOTES from canon
    next-id       --backend fail2drive|isaac

THE SPEC is a LAUNCH file outside canon (campaign_spec/v0.1): the runner's
per-family declaration is the record of what ran, so the spec says it once
more only for `run`; every later verb reads the tree's declarations, the
raw zip's, or canon. Fields: spec_kind, campaign_id, backend, note,
families, models, repetitions, reference_model (or null), results_root (optional:
where THIS machine puts the tree is a run-line fact, --results-root),
launch {backend-specific: fail2drive gpu/timeout/runner_args; isaac
agent_pairs {brain: path}, robot_record, brains_root, cell_timeout},
and optionally variants {family: [scenario ids or stems]} for a declared
subset campaign; on Isaac optionally simulator {kind: isaacsim, version}
naming an admitted Isaac Sim version (adapters/isaac/ISAACSIM_PIN maps it
to the env; required the day two versions are admitted) (a smoke that can still be frozen: the declaration says
what ran, the coverage gate holds it to that).

Stdlib only and 3.10-compatible: this script runs on any python3, and every
tool it spawns runs through the conda env that tool is keyed to, inferred,
never configured: the runner-side verbs (run, freeze-raw) use the backend's
runner env (fail2drive, isaacsim: a farm node or the Isaac box needs nothing
else), the central verbs (score, freeze-derived, store-derived, rebuild-register, stale) use scl_prototype.
Each agent's own env is the runner's business (models.yaml). --conda-exe
names the launcher (conda, micromamba).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import campaign_bundles as cb  # noqa: E402  (stdlib-only core)
from scl_core import campaign_records  # noqa: E402  (stdlib-only core)
from scl_core import family_versions as fv  # noqa: E402  (stdlib-only core)
from scl_core.campaign_declaration import load_declaration  # noqa: E402  (stdlib-only core)

SPEC_KIND = "campaign_spec/v0.1"
BACKENDS = ("fail2drive", "isaac")
RUNNER_ENV = {"fail2drive": "fail2drive", "isaac": "isaacsim"}   # where each backend's runner and manifest tool run;
# on Isaac the env is keyed by the simulator version the spec declares (adapters/isaac/ISAACSIM_PIN, F45)
CENTRAL_ENV = "scl_prototype"                                       # where the driver, certifiers and extractor run
CARLA_MME = "adapters/fail2drive/scripts/multi_model_eval"
ISAAC_MME = "adapters/isaac/scripts/multi_model_eval"
MME = {"fail2drive": CARLA_MME, "isaac": ISAAC_MME}
CANON_CAMPAIGNS = "artifacts/examples/campaigns"
SCORING_RECORD_KIND = "scoring_record/v0.2"   # the driver's record (scripts/score_campaign.py RECORD_KIND)
CANON_FINDINGS = "artifacts/examples/findings"
CANON_RECORDS = f"{CANON_FINDINGS}/certification"   # <campaign_id>/<record file>: one record per campaign and chain
RECORD_FILES = {   # chain -> (backend, the record file the chain's certifier writes)
    "threading": ("fail2drive", "threading_certification.v0.2.json"),
    "flow": ("fail2drive", "flow_certification.v0.8.json"),
    "isaac": ("isaac", "findings_certification.v0.1.json"),
}
ID_PATTERN = {"fail2drive": re.compile(r"^campaign_carla_v(\d+)$"), "isaac": re.compile(r"^campaign_isaac_v(\d+)$")}
ID_PREFIX = {"fail2drive": "campaign_carla_v", "isaac": "campaign_isaac_v"}
REGISTER_START = "<!-- campaign register: generated by scripts/campaign.py table from the canon declarations and the certification records; edit those and regenerate -->"
REGISTER_END = "<!-- end campaign register -->"
NOTES = {"fail2drive": f"{CARLA_MME}/CAMPAIGN_NOTES.md", "isaac": f"{ISAAC_MME}/CAMPAIGN_NOTES.md"}


class CampaignError(ValueError):
    pass


def sha256_file(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- the spec ----------------------------------------------------------------------

def load_spec(path, root=ROOT) -> dict:
    """The launch file, validated against the contract and the canon families."""
    path = Path(path)
    try:
        spec = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CampaignError(f"{path}: not a readable JSON document ({exc})") from None
    if spec.get("spec_kind") != SPEC_KIND:
        raise CampaignError(f"{path.name}: spec_kind {spec.get('spec_kind')!r}; this tool reads {SPEC_KIND}")
    required = ("campaign_id", "backend", "note", "families", "models", "repetitions")
    missing = [k for k in required if k not in spec]
    if missing:
        raise CampaignError(f"{path.name}: missing {missing} (contract {SPEC_KIND}: {list(required)}, "
                            "reference_model, launch; results_root optional, the run line's --results-root "
                            "names the tree's location otherwise)")
    if "results_root" in spec and (not isinstance(spec["results_root"], str) or not spec["results_root"].strip()):
        raise CampaignError(f"{path.name}: results_root, when given, is a directory path")
    if spec["backend"] not in BACKENDS:
        raise CampaignError(f"{path.name}: backend {spec['backend']!r}; one of {list(BACKENDS)}")
    for field in ("families", "models", "repetitions"):
        values = spec[field]
        if not isinstance(values, list) or not values or len(set(map(str, values))) != len(values):
            raise CampaignError(f"{path.name}: {field} must be a non-empty list without duplicates")
    if not all(isinstance(r, int) for r in spec["repetitions"]):
        raise CampaignError(f"{path.name}: repetitions are integer labels (traffic seeds on CARLA, policy samples on Isaac)")
    if not isinstance(spec["campaign_id"], str) or not spec["campaign_id"]:
        raise CampaignError(f"{path.name}: campaign_id must be a non-empty string")
    if not isinstance(spec["note"], str) or not spec["note"].strip():
        raise CampaignError(f"{path.name}: note must say why the campaign exists")
    reference = spec.get("reference_model")
    if reference is not None and reference not in spec["models"]:
        raise CampaignError(f"{path.name}: reference_model {reference!r} is not one of models {spec['models']}")
    for family in spec["families"]:
        if family_manifest(spec["backend"], family, root) is None:
            raise CampaignError(f"{path.name}: family {family!r} is not in canon for {spec['backend']} "
                                f"({family_home(spec['backend'])})")
    launch = spec.get("launch") or {}
    if not isinstance(launch, dict):
        raise CampaignError(f"{path.name}: launch must be an object")
    variants = spec.get("variants") or {}
    if not isinstance(variants, dict) or any(f not in spec["families"] or not isinstance(v, list) or not v
                                             for f, v in variants.items()):
        raise CampaignError(f"{path.name}: variants must map a declared family to a non-empty list of its "
                            "scenario ids (CARLA) or stems (Isaac)")
    for family, wanted in variants.items():
        known = family_variants(spec["backend"], family_manifest(spec["backend"], family, root))
        unknown = [v for v in wanted if v not in known]
        if unknown:
            raise CampaignError(f"{path.name}: variants {unknown} are not accepted variants of {family} "
                                f"({len(known)} known)")
    if spec["backend"] == "isaac":
        pairs = launch.get("agent_pairs")
        if not isinstance(pairs, dict) or not pairs or "robot_record" not in launch:
            raise CampaignError(f"{path.name}: an isaac launch block names agent_pairs {{brain: pair document}} "
                                "and robot_record (the runner takes the grid from them)")
        brains = sorted(pairs)
        expected = sorted(brains + ([reference] if reference else []))
        if sorted(spec["models"]) != expected:
            raise CampaignError(f"{path.name}: models {sorted(spec['models'])} are not the agent pairs' brains "
                                f"{brains} plus the reference {reference!r}")
        simulator = spec.get("simulator")
        if simulator is not None:
            if not isinstance(simulator, dict) or simulator.get("kind") != "isaacsim" \
                    or not isinstance(simulator.get("version"), str):
                raise CampaignError(f"{path.name}: simulator, when given, is {{kind: isaacsim, version: <admitted>}}")
            try:
                isaac_pin(root).entry_for(simulator["version"])
            except ValueError as exc:
                raise CampaignError(f"{path.name}: {exc}") from None
    spec["_path"] = str(path)
    return spec


def isaac_pin(root=ROOT):
    """adapters/isaac/isaacsim_pin.py loaded by file (stdlib), bound to this root's ISAACSIM_PIN."""
    import importlib.util
    path = Path(root) / "adapters/isaac/isaacsim_pin.py"
    spec = importlib.util.spec_from_file_location("isaac_isaacsim_pin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PIN_PATH = Path(root) / "adapters/isaac/ISAACSIM_PIN"
    return module


def isaac_simulator(spec, root=ROOT) -> dict:
    """The admitted Isaac Sim entry a spec is keyed to: its declared version, or the only
    admitted one; several admitted and none declared refuse (F45)."""
    pin = isaac_pin(root)
    try:
        return pin.resolve((spec.get("simulator") or {}).get("version"), pin.PIN_PATH)
    except ValueError as exc:
        raise CampaignError(f"{spec.get('_path', 'spec')}: {exc}") from None


def isaac_env_for_tree(results_root, declarations, root=ROOT) -> str:
    """The env that holds the version a frozen tree ran under (campaign_meta.simulator.pin)."""
    meta_path = Path(results_root) / declarations[0].results_subtree / "campaign_meta.json"
    pin = ((json.loads(meta_path.read_text()).get("simulator") or {}).get("pin")) if meta_path.is_file() else None
    if not pin:
        raise CampaignError(f"{meta_path}: no simulator pin (the runner records it since F21)")
    try:
        return isaac_pin(root).env_for(pin, isaac_pin(root).PIN_PATH)
    except ValueError as exc:
        raise CampaignError(str(exc)) from None


def family_home(backend) -> str:
    return "artifacts/examples/solved_families" + ("/isaac/<family>/family_manifest.json" if backend == "isaac"
                                                   else "/<family>.json")


def family_manifest(backend, family, root=ROOT):
    """The canon family manifest (a file on CARLA, a directory on Isaac), or None."""
    root = Path(root)
    if backend == "isaac":
        d = root / "artifacts/examples/solved_families/isaac" / family
        return d if (d / "family_manifest.json").is_file() else None
    p = root / "artifacts/examples/solved_families" / f"{family}.json"
    return p if p.is_file() else None


def family_variants(backend, manifest_path) -> list:
    """The accepted variants a family manifest declares: scenario ids on
    CARLA, stems on Isaac (a variant without a verdict is accepted)."""
    if backend == "isaac":
        doc = json.loads((Path(manifest_path) / "family_manifest.json").read_text())
        return [v["stem"] for v in doc["variants"] if v.get("verdict", "accepted") == "accepted"]
    doc = json.loads(Path(manifest_path).read_text())
    return [v["scenario_id"] for v in doc["variants"] if v.get("verdict") == "accepted"]


def route_files(manifest_path, root=ROOT, only=None) -> list:
    """The route files of a CARLA family's ACCEPTED variants, from the
    manifest's own output_file entries (data, never a name glob); `only`
    restricts them to the named scenario ids."""
    doc = json.loads(Path(manifest_path).read_text())
    files = []
    for variant in doc["variants"]:
        if variant.get("verdict") != "accepted":
            continue
        if only is not None and variant.get("scenario_id") not in only:
            continue
        out = variant.get("output_file")
        if not out:
            raise CampaignError(f"{Path(manifest_path).name}: variant {variant.get('scenario_id')} names no output_file")
        path = Path(root) / "artifacts/examples" / out
        if not path.is_file():
            raise CampaignError(f"{Path(manifest_path).name}: route file missing: {path}")
        files.append(path)
    if not files:
        raise CampaignError(f"{Path(manifest_path).name}: no accepted variant")
    return files


# --- running commands ----------------------------------------------------------------

class Plan:
    def __init__(self):
        self.steps = []

    def add(self, label, argv, cwd=None, env=None):
        self.steps.append((label, [str(a) for a in argv], cwd, env))


def conda_python(env, conda_exe="conda"):
    return [conda_exe, "run", "-n", env, "--no-capture-output", "python"]


def show(plan, notes=()):
    for note in notes:
        print(f"  {note}")
    for label, argv, cwd, _ in plan.steps:
        where = f" (in {cwd})" if cwd else ""
        print(f"  [{label}]{where}\n    {' '.join(argv)}")


def execute(plan, plan_only, notes=()):
    show(plan, notes)
    if plan_only:
        return True
    for label, argv, cwd, env in plan.steps:
        print(f"--- {label}", flush=True)
        r = subprocess.run(argv, cwd=cwd, env={**os.environ, **(env or {})} if env else None)
        if r.returncode != 0:
            print(f"STEP FAILED: {label} (exit {r.returncode}); stopping here", file=sys.stderr)
            return False
    return True


# --- run ------------------------------------------------------------------------------

def run_plan(spec, families, root, conda_exe, gpu=None, results_root=None) -> Plan:
    """results_root: where THIS machine puts the tree, a launch-line fact (--results-root); a spec
    may carry one for a single-machine campaign, and a spec shared between nodes carries none."""
    where = results_root or spec.get("results_root")
    if not where:
        raise CampaignError("the results tree's location is not in the spec (results_root is optional there): "
                            "pass --results-root on the run line")
    backend, results = spec["backend"], Path(os.path.expanduser(str(where))).resolve()
    launch = spec.get("launch") or {}
    plan = Plan()
    mme = Path(root) / MME[backend]
    subsets = spec.get("variants") or {}
    for family in families:
        manifest = family_manifest(backend, family, root)
        only = subsets.get(family)
        # the preflight of the family version registry (2026-10-03): the manifest's hash must be a registered
        # realization, and its version travels into the declaration as a checked claim
        try:
            version = fv.declared_version(root, backend, family, manifest)
        except ValueError as exc:
            raise CampaignError(f"{family}: {exc}") from None
        if backend == "fail2drive":
            argv = conda_python(RUNNER_ENV[backend], conda_exe) + [
                "evaluate_models.py", "--campaign-id", spec["campaign_id"], "--family", manifest,
                "--family-version", version,
                "--routes", *route_files(manifest, root, only), "--models", *spec["models"],
                "--seeds", *spec["repetitions"], "--results-root", results, "--note", spec["note"]]
            if spec.get("reference_model"):
                argv += ["--reference-model", spec["reference_model"]]
            if gpu is not None or launch.get("gpu") is not None:
                argv += ["--gpu", gpu if gpu is not None else launch["gpu"]]
            for key in ("timeout", "route_wall_timeout", "max_retries"):
                if launch.get(key) is not None:
                    argv += [f"--{key.replace('_', '-')}", launch[key]]
            argv += list(launch.get("runner_args") or [])
        else:
            simulator = isaac_simulator(spec, root)
            argv = conda_python(simulator["env"], conda_exe) + [
                "evaluate_models.py", "--family", manifest, "--family-version", version,
                "--campaign-id", spec["campaign_id"],
                "--note", spec["note"], "--results-root", results, "--reps", len(spec["repetitions"]),
                "--robot-record", os.path.expanduser(str(launch["robot_record"])),
                "--isaacsim-version", simulator["version"]]
            for brain in sorted(launch["agent_pairs"]):
                argv += ["--agent-pair", os.path.expanduser(str(launch["agent_pairs"][brain]))]
            if not spec.get("reference_model"):
                argv += ["--no-expert"]
            if only:
                argv += ["--variants", *only]
            argv += ["--conda-exe", conda_exe]   # the runner measures each brain's env through it; a bare `conda` is a shell function under nohup
            for key in ("brains_root", "gauntlet", "cell_timeout", "repo_root"):
                if launch.get(key) is not None:
                    argv += [f"--{key.replace('_', '-')}", os.path.expanduser(str(launch[key]))]
            argv += list(launch.get("runner_args") or [])
        plan.add(f"run {family}", argv, cwd=str(mme))
    return plan


def cmd_run(args):
    root = Path(args.repo_root).resolve()
    spec = load_spec(args.spec, root)
    canon = root / CANON_CAMPAIGNS / spec["campaign_id"]
    if canon.is_dir():
        raise CampaignError(f"{spec['campaign_id']} already has declarations in canon ({canon}); a campaign id runs once. "
                            f"Next unused: {next_id(spec['backend'], root, args.bundles)}")
    families = args.families or spec["families"]
    unknown = [f for f in families if f not in spec["families"]]
    if unknown:
        raise CampaignError(f"--families {unknown} are not in the spec ({spec['families']})")
    plan = run_plan(spec, families, root, args.conda_exe, args.gpu, args.results_root)
    ok = execute(plan, args.plan_only, [f"campaign {spec['campaign_id']} on {spec['backend']}: {len(families)} of "
                                        f"{len(spec['families'])} families, {len(spec['models'])} models x "
                                        f"{len(spec['repetitions'])} repetitions; re-run the same line to resume"])
    return 0 if ok else 1



# --- freeze-raw -------------------------------------------------------------------------

def tree_declarations(results_root, campaign_id) -> list:
    tree = Path(results_root) / campaign_id
    if not tree.is_dir():
        raise CampaignError(f"no campaign tree at {tree}")
    declarations = []
    for family_dir in sorted(p for p in tree.iterdir() if p.is_dir()):
        path = family_dir / "campaign_declaration.json"
        if path.is_file():
            declarations.append(load_declaration(path))
    if not declarations:
        raise CampaignError(f"{tree}: no family subtree carries a campaign_declaration.json")
    ids = {d.campaign_id for d in declarations}
    if ids != {campaign_id}:
        raise CampaignError(f"{tree}: declarations name {sorted(ids)}, the tree is {campaign_id}")
    return declarations


def vintage_check(results_root, declarations) -> list:
    """One campaign, one instrument vintage: every family's meta must record
    the same runner tools, per agent the same identity fingerprint and, since
    F58, the same box (GPU name, driver, CUDA driver, simulator kind, pin and
    build version). Returns the differences (empty when the campaign is one
    vintage)."""
    tools, identities, seen, boxes = {}, {}, {}, {}
    for d in declarations:
        meta_path = Path(results_root) / d.results_subtree / "campaign_meta.json"
        if not meta_path.is_file():
            return [f"{d.family_id}: no campaign_meta.json (the runner's instrument record)"]
        meta = json.loads(meta_path.read_text())
        tools[d.family_id] = meta.get("tools_sha256", {})
        identities[d.family_id] = campaign_records.agent_fingerprints(meta)
        boxes[d.family_id] = campaign_records.box_vintage(meta)
    problems = []
    first = declarations[0].family_id
    for fam, t in tools.items():
        moved = sorted(k for k in set(t) | set(tools[first]) if t.get(k) != tools[first].get(k))
        if moved:
            problems.append(f"runner tools differ between {first} and {fam}: {moved}")
    for fam, b in boxes.items():
        moved = [f"{k} {boxes[first][k]} vs {b[k]}" for k in b if b[k] != boxes[first][k]]
        if moved:
            problems.append(f"box differs between {first} and {fam}: " + "; ".join(moved))
    for fam, ids in identities.items():
        for agent, fp in ids.items():
            if agent in seen and seen[agent][1] != fp:
                problems.append(f"agent {agent} ran as {seen[agent][1][:12]} in {seen[agent][0]} and {fp[:12]} in {fam}")
            seen.setdefault(agent, (fam, fp))
    return problems


def cmd_freeze_raw(args):
    root = Path(args.repo_root).resolve()
    results = Path(args.results_root).resolve()
    declarations = tree_declarations(results, args.campaign_id)
    backend = {d.backend for d in declarations}
    if len(backend) != 1:
        raise CampaignError(f"one backend per campaign; the tree declares {sorted(backend)}")
    backend = backend.pop()
    problems = vintage_check(results, declarations)
    if problems and not args.allow_mixed_vintage:
        raise CampaignError("the campaign is not one instrument vintage:\n  " + "\n  ".join(problems) +
                            "\n  (re-run the families under one runner and one environment, or pass "
                            "--allow-mixed-vintage to freeze it as it is; the metas record the difference)")
    out = Path(args.out).resolve()
    notes = [f"campaign {args.campaign_id} on {backend}: families {[d.family_id for d in declarations]}"]
    if problems:
        notes += ["WARNING mixed vintage, frozen as it is: " + "; ".join(problems)]
    # the tree's own declarations are what the verifier certifies against; they travel inside
    # the zip (score adopts them from there), so nothing is laid beside it (ruling 2026-09-15:
    # the archive holds zips, nothing else)
    plan = Plan()
    runner_env = isaac_env_for_tree(results, declarations, root) if backend == "isaac" else RUNNER_ENV[backend]
    plan.add("verify and freeze raw", conda_python(runner_env, args.conda_exe) +
             [root / MME[backend] / "campaign_manifest.py",
                                      "--campaign-dir", results / args.campaign_id,
                                      *sum((["--campaign", d.path] for d in declarations), []),
                                      "--produce-bundle", out / f"{args.campaign_id}.zip"])
    ok = execute(plan, args.plan_only, notes)
    if ok and not args.plan_only:
        print(f"raw bundle: {out / (args.campaign_id + '.zip')}; the zip alone goes to the archive, "
              "with the content id and the zip sha256 printed above")
    return 0 if ok else 1


# --- instruments and chains --------------------------------------------------------------

def load_instruments(path, backend, root=None) -> dict:
    """One file, a section per backend; only the campaign's backend is read. A relative path resolves
    against `root` (the repository root), so the file the repository ships
    (artifacts/examples/campaigns/instruments.json) works from any working directory and from a clone
    (2026-10-09, F79); `~` is expanded. The full CARLA map is the launcher's --map argument since that
    day, the one file outside the repository: a `map` entry refuses."""
    doc = json.loads(Path(path).read_text())
    section = doc.get(backend)
    if not isinstance(section, dict):
        raise CampaignError(f"{path}: no {backend!r} section (an instruments file holds one object per backend)")
    if "map" in section:
        raise CampaignError(f"{path}: the {backend} section names `map`; the full CARLA OpenDRIVE map is the "
                            "launcher's --map argument (the one instrument outside the repository), not a file entry")

    def resolve(v):
        v = os.path.expanduser(str(v))
        return str(Path(root) / v) if root is not None and not os.path.isabs(v) else v
    # `gap_certificate_campaigns` declares each certificate's campaign sample (F99, 2026-10-21): campaign ids and a note,
    # not files; it travels as written (the figure tool reads it; the launcher passes no argument from it)
    return {k: (v if k == "gap_certificate_campaigns" else
                {f: resolve(p) for f, p in v.items()} if isinstance(v, dict) else resolve(v) if isinstance(v, str) else v)
            for k, v in section.items()}


def defects_args(instruments) -> list:
    """`scenario_defects` of the backend's section: the one declared register, passed to the fit and emit
    steps at scoring and to the flow bind (the replay must fit with the register the scoring fitted with)."""
    path = instruments.get("scenario_defects")
    return ["--defects", os.path.expanduser(path)] if path else []


def gap_certificate_args(instruments, families) -> list:
    """`gap_certificates` {family_id: path} of the fail2drive section: the declared flow gap
    certificates (F55), a table over families; the rows of the families this campaign declares
    go out as --gap-certificate FAMILY=PATH, one each, to the score, the stage and the bind (the
    drivers refuse a certificate for a family the run does not declare, so the join is here)."""
    table = instruments.get("gap_certificates") or {}
    if not isinstance(table, dict) or not all(isinstance(v, str) for v in table.values()):
        raise CampaignError("gap_certificates must be an object {family_id: certificate path}")
    return sum((["--gap-certificate", f"{family}={os.path.expanduser(path)}"]
                for family, path in sorted(table.items()) if family in set(families)), [])


def instrument_args(backend, instruments, flow_needed, families, map_path=None) -> list:
    # the declared scenario-defect register (scl_core/scenario_defects.py, F66 part two) goes to every
    # scoring chain that has one declared: the fits and findings read it, the documents pin it
    defects = ["--defects", instruments["scenario_defects"]] if instruments.get("scenario_defects") else []
    if backend == "isaac":
        if map_path is not None:
            raise CampaignError("--map is the CARLA OpenDRIVE map of the oncoming-flow map check; the warehouse chain reads "
                                "nothing outside the repository (its records and instrument ship with it)")
        # the capability profile left the chain 2026-10-08: the instrument declares the certification constants
        missing = [k for k in ("robot_record", "environment_record", "instrument") if not instruments.get(k)]
        if missing:
            raise CampaignError(f"the isaac chain needs instruments {missing}")
        return sum((["--" + k.replace("_", "-"), instruments[k]] for k in
                    ("robot_record", "environment_record", "instrument")), []) + defects
    if not flow_needed:
        if map_path is not None:
            raise CampaignError("--map given, but no declared family's plan schedules a behavior-scalar factor: "
                                "nothing here is scored by the flow chain, the map check never runs")
        return defects
    missing = [k for k in ("zone", "map_excerpt") if not instruments.get(k)]
    if missing:
        raise CampaignError(f"the flow chain needs instruments {missing}")
    if map_path is None:
        raise CampaignError("a campaign holding a flow family needs --map, the full CARLA OpenDRIVE map the flow map "
                            "check reads (CarlaUE4/Content/Carla/Maps/OpenDrive/Town03.xodr of a CARLA 0.9.15 install)")
    argv = sum((["--" + k.replace("_", "-"), instruments[k]] for k in ("zone", "map_excerpt")), [])
    argv += ["--map", os.path.expanduser(str(map_path))]
    return argv + gap_certificate_args(instruments, families) + defects


def chains_of(declarations, root) -> dict:
    """{family_id: chain} - isaac for the Isaac backend, threading or flow on
    CARLA read from the family's plan (campaign_manifest.scoring_chain, a
    stdlib lookup over the registry's vocabulary and the plan document)."""
    out = {}
    carla_manifest = None
    for d in declarations:
        if d.backend == "isaac":
            out[d.family_id] = "isaac"
            continue
        if carla_manifest is None:
            # the CARLA manifest tool by file (stdlib at import); both backends name their tool
            # campaign_manifest.py, so a bare import could return whichever a process holds
            import importlib.util
            path = Path(root) / CARLA_MME / "campaign_manifest.py"
            spec = importlib.util.spec_from_file_location("carla_campaign_manifest", path)
            carla_manifest = importlib.util.module_from_spec(spec)
            mme = str(path.parent)
            if mme not in sys.path:
                sys.path.insert(0, mme)
            spec.loader.exec_module(carla_manifest)
        out[d.family_id] = carla_manifest.scoring_chain(d)
    return out


def canon_declarations(root, campaign_id=None) -> list:
    base = Path(root) / CANON_CAMPAIGNS
    dirs = [base / campaign_id] if campaign_id else sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    declarations = []
    for d in dirs:
        for path in sorted(d.glob("*.json")):
            declarations.append(load_declaration(path))
    return declarations


def decl_args(declarations) -> list:
    return sum((["--campaign", d.path] for d in declarations), [])


# --- score ---------------------------------------------------------------------------------

def adopt_declarations(raw_zip, root, plan_only) -> tuple:
    """The declarations inside the raw zip become canon (byte copies of the
    runner's records, each verified against the zip's MANIFEST); canon that
    already holds different bytes for a family refuses. A bundle that carries
    none (the campaigns before in-bundle declarations) takes the ones canon
    holds for its id, written after the fact and marked so by the freeze
    (principle 13's corollary; 2026-10-09, one verb for every campaign);
    with none in either place it refuses."""
    bundle = cb.read_bundle(raw_zip)
    campaign = bundle["campaign_id"]
    canon = Path(root) / CANON_CAMPAIGNS / campaign
    members = sorted(m for m in bundle["listed"] if m.endswith("/campaign_declaration.json")
                     and len(m.split("/")) == 3)
    if not members:
        held = sorted(canon.glob("*.json")) if canon.is_dir() else []
        if not held:
            raise CampaignError(f"{Path(raw_zip).name} holds no <family>/campaign_declaration.json (a bundle from before "
                                f"in-bundle declarations) and canon holds none under {CANON_CAMPAIGNS}/{campaign}/")
        return campaign, held, [f"declarations from canon, written after the fact: {Path(raw_zip).name} predates "
                                f"in-bundle declarations; {len(held)} under {CANON_CAMPAIGNS}/{campaign}/"]
    import zipfile
    adopted, notes = [], []
    with zipfile.ZipFile(raw_zip) as z:
        for member in members:
            family = member.split("/")[1]
            data = z.read(member)
            if cb.sha256_bytes(data) != bundle["listed"][member]:
                raise CampaignError(f"{member} does not hash to the bundle's MANIFEST")
            try:   # the version the runner declared is a claim; canon admits it only when the registry agrees
                fv.check_declared_version(root, json.loads(data))
            except ValueError as exc:
                raise CampaignError(f"{member}: {exc}") from None
            target = canon / f"{family}.json"
            if target.is_file():
                if target.read_bytes() != data:
                    raise CampaignError(f"canon holds a different declaration for {campaign}/{family}: {target}")
                notes.append(f"canon already holds {target.relative_to(root)}")
            else:
                notes.append(f"adopt {member} -> {target.relative_to(root)}")
                if not plan_only:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            adopted.append(target)
    return campaign, adopted, notes


def cmd_score(args):
    root = Path(args.repo_root).resolve()
    raw = Path(args.raw).resolve()
    work = Path(args.work).resolve()
    campaign, targets, notes = adopt_declarations(raw, root, args.plan_only)
    if args.plan_only and not all(t.is_file() for t in targets):
        # canon does not hold them yet: read the zip's copies from a scratch directory for the plan
        import tempfile
        import zipfile
        scratch = Path(tempfile.mkdtemp(prefix="campaign_plan_"))
        with zipfile.ZipFile(raw) as z:
            for t in targets:
                (scratch / t.name).write_bytes(z.read(f"{campaign}/{t.stem}/campaign_declaration.json"))
        declarations = [load_declaration(scratch / t.name) for t in targets]
    else:
        declarations = [load_declaration(t) for t in targets]
    backend = {d.backend for d in declarations}.pop()
    instruments = load_instruments(args.instruments, backend, root)
    chains = chains_of(declarations, root)
    notes.append(f"chains: {chains}")
    flow_needed = "flow" in chains.values()
    central = conda_python(CENTRAL_ENV, args.conda_exe)
    plan = Plan()
    # scoring inputs only: the evidence members no chain tool reads (videos, per-frame metas) are verified in
    # place and never written (F67, 2026-10-07: 13.5 GB and 173,832 files of a 16.7 GB CARLA campaign; the
    # recorder logs stay, the bind's completeness check promises them per route)
    plan.add("unpack raw", central + [root / "scripts/unpack_campaign_bundle.py", raw, "--into", work / "unpacked",
                                      "--scoring-inputs"])
    plan.add("score", central + [root / "scripts/score_campaign.py", "--results-root", work / "unpacked",
                       "--out", work / "scores", "--jobs", str(args.jobs), *sum((["--campaign", t] for t in targets), []),
                       *instrument_args(backend, instruments, flow_needed, [d.family_id for d in declarations], args.map),
                       *(["--accept-instrument-change"] if getattr(args, "accept_instrument_change", False) else [])])
    ok = execute(plan, args.plan_only, notes)
    return 0 if ok else 1


# --- freeze-derived, store-derived and rebuild-register --------------------------------------------------------------

def gather_findings(scores, campaign_id, into) -> list:
    """The findings documents the driver left per family, in one directory
    (what a certifier's --findings-dir holds)."""
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in sorted((Path(scores) / campaign_id).glob("*/findings/fs_*.json")):
        shutil.copyfile(path, into / path.name)
        copied.append(into / path.name)
    if not copied:
        raise CampaignError(f"no findings documents under {Path(scores) / campaign_id}/<family>/findings/")
    return copied


def bind_plan(declarations, chains, root, bundles, results_root, instruments, findings_dir, work, records, plan,
              central):
    """The certifier calls that bind `declarations` (the whole set the records
    cover) into `records` {chain: record path}; CARLA runs the threading
    certifier over every declaration (it skips flow families by their plan)
    and the flow certifier over the campaigns holding a flow family."""
    backend = declarations[0].backend
    if backend == "isaac":
        plan.add("bind isaac record", central + [root / ISAAC_MME / "certify_findings.py", *decl_args(declarations),
                                                 "--bundles-dir", bundles, "--findings-dir", findings_dir, "--out", records["isaac"]])
        return
    if any(c == "threading" for c in chains.values()):
        plan.add("bind threading record", central + [root / CARLA_MME / "certify_threading_findings.py",
                                                     *decl_args(declarations), "--bundles-dir", bundles,
                                                     "--findings-dir", findings_dir, "--out", records["threading"]])
    flow_campaigns = {d.campaign_id for d in declarations if chains.get(d.family_id) == "flow"}
    if flow_campaigns:
        flow_decls = [d for d in declarations if d.campaign_id in flow_campaigns]
        plan.add("bind flow record", central + [root / CARLA_MME / "certify_flow_campaigns.py", *decl_args(flow_decls),
                                      "--zone", instruments["zone"], "--map-excerpt", instruments["map_excerpt"],
                                      "--results-root", results_root, "--bundles-dir", bundles,
                                      "--work", Path(work) / "flow_bind", "--out", records["flow"],
                                      *gap_certificate_args(instruments, [d.family_id for d in flow_decls]),
                                      *defects_args(instruments)])


def check_scoring_record(scores, declarations):
    """The scoring driver's record (`scoring_record/v0.2`): the run complete and every declared family's
    fit and emit repeated byte for byte (F82: the determinism repeat runs on every chain and is checked
    here once, before a stage or a freeze; the flow stage reads the same record again)."""
    path = Path(scores) / "scoring_record.json"
    if not path.is_file():
        raise CampaignError(f"{scores}: no scoring_record.json; score the campaign first (`campaign score`)")
    record = json.loads(path.read_text())
    if record.get("record_kind") != SCORING_RECORD_KIND:
        raise CampaignError(f"{path}: record_kind {record.get('record_kind')!r}; this verb reads {SCORING_RECORD_KIND}")
    if not record.get("complete"):
        raise CampaignError(f"{path}: the scoring run is incomplete; re-score")
    repeated = record.get("repeated_findings_identical") or {}
    moved = [d.family_id for d in declarations if repeated.get(d.family_id) is not True]
    if moved:
        raise CampaignError(f"{path}: the findings of {moved} did not repeat byte for byte "
                            "(repeated_findings_identical); the chain is not deterministic over these rows, re-score")


def cmd_freeze_derived(args):
    root = Path(args.repo_root).resolve()
    declarations = canon_declarations(root, args.campaign_id)
    if not declarations:
        raise CampaignError(f"canon holds no declarations for {args.campaign_id} under {CANON_CAMPAIGNS}; "
                            "`campaign score` adopts them from the raw zip")
    backend = declarations[0].backend
    chains = chains_of(declarations, root)
    scores, bundles, work = Path(args.scores).resolve(), Path(args.bundles).resolve(), Path(args.work).resolve()
    instruments = load_instruments(args.instruments, backend, root)
    raw = bundles / f"{args.campaign_id}.zip"
    if not raw.is_file():
        raise CampaignError(f"no raw bundle {raw}")
    if not args.plan_only:
        check_scoring_record(scores, declarations)
    central = conda_python(CENTRAL_ENV, args.conda_exe)
    plan = Plan()
    if "flow" in chains.values():
        plan.add("stage flow families", central + [root / CARLA_MME / "certify_flow_campaigns.py", "--stage",
                                         *decl_args(declarations), "--zone", instruments["zone"],
                                         "--map-excerpt", instruments["map_excerpt"], "--results-root", args.results_root,
                                         "--bundles-dir", bundles, "--scores-root", scores,
                                         *gap_certificate_args(instruments, [d.family_id for d in declarations])])
    if args.against:
        # the re-issue question, on the record (F34): the fresh folder against a previous interpretation,
        # BEFORE the freeze writes the archive (2026-09-22: a comparison after the freeze found the previous
        # zip already replaced); an INSTRUMENT CHANGE stops the plan here unless --instrument-change accepts
        # it, and the report lands in the work directory beside the records (store-derived does not copy it)
        # the previous interpretation is read from the bundles directory (the archive in a re-issue; its
        # superseded folders are searched by id) and unpacked under THIS work tree, never under the
        # checkout (F72: a freeze run from the public clone wrote the store's cache into it)
        plan.add("compare lineages", central + [root / "scripts/compare_lineages.py", "--a", args.against,
                                                "--b", scores / args.campaign_id, "--repo-root", root,
                                                "--archive", bundles, "--cache", work / "interpretations",
                                                "--out", work / "lineage_comparison.v0.1.json",
                                                *(["--accept-instrument-change"] if args.instrument_change else [])])
    plan.add("freeze derived", central + [root / MME[backend] / "campaign_manifest.py",
                                          "--produce-derived", bundles / f"{args.campaign_id}_derived.zip", "--raw", raw,
                                "--derived-dir", scores / args.campaign_id, *decl_args(declarations)])
    findings_dir = work / "findings"
    records_dir = work / "records" / args.campaign_id
    notes = [f"campaign {args.campaign_id} on {backend}: chains {chains}",
             f"the bind writes this campaign's records into {records_dir} (store-derived copies them into the store)"]
    if not args.plan_only:
        if work.exists():
            raise CampaignError(f"{work} exists; the bind takes a new directory")
        gather_findings(scores, args.campaign_id, findings_dir)
    records = {chain: records_dir / name for chain, (_, name) in RECORD_FILES.items()}
    bind_plan(declarations, chains, root, bundles, Path(args.results_root).resolve(), instruments, findings_dir,
              work, records, plan, central)
    ok = execute(plan, args.plan_only, notes)
    if ok and not args.plan_only:
        print(f"derived bundle: {bundles / (args.campaign_id + '_derived.zip')}; findings under {findings_dir}, "
              f"records under {records_dir}; store with `campaign store-derived --campaign-id {args.campaign_id} "
              f"--work {work} --bundles {bundles}`")
    return 0 if ok else 1


def read_record(path) -> dict:
    """A certification record: one campaign per file (the directory names it)."""
    path = Path(path)
    doc = json.loads(path.read_text())
    named = set(doc.get("campaigns", {}))
    if named != {path.parent.name}:
        raise CampaignError(f"{path}: the record names campaigns {sorted(named)}; a record file under "
                            f"{path.parent.name}/ binds that campaign alone")
    return doc


def bound_documents(record) -> dict:
    """{document name: sha256} over every family entry of a record."""
    out = {}
    for families in record.get("campaigns", {}).values():
        for entry in families.values():
            out.update(entry.get("documents") or {})
    return out


def committed_sources_or_refuse(derived_zip):
    """Canon binds a committed tree: the derived bundle's source manifest names the commit its
    listed sources were read from, and none of them may have been modified against it (the
    bundle carries hashes, the repository carries the bytes; 2026-09-19). A bundle of the copy-
    carrying kind (derived_bundle/v0.1) names no commit and re-freezes under the current kind."""
    derived_zip = Path(derived_zip)
    if not derived_zip.is_file():
        raise CampaignError(f"no {derived_zip}; store-derived copies what freeze-derived bound, and the bind froze that zip")
    bundle = cb.read_bundle(derived_zip)
    git = (bundle["provenance"].get("source_snapshot") or {}).get("git") or {}
    if bundle["kind"] != cb.DERIVED_KIND or not git.get("commit"):
        raise CampaignError(f"{derived_zip.name} is {bundle['kind']!r} and names no commit in its source manifest; "
                            f"canon binds {cb.DERIVED_KIND}: re-score and re-freeze")
    if git.get("modified"):
        raise CampaignError(f"{derived_zip.name} was frozen from a tree whose listed sources were modified against "
                            f"commit {git['commit'][:12]}: {git['modified'][:4]}{' ...' if len(git['modified']) > 4 else ''}; "
                            "canon binds a committed tree: commit, re-score, re-freeze")
    return git["commit"]


def cmd_store_derived(args):
    """The store receives what freeze-derived bound: the findings documents and the
    campaign's record files, copied as they are (the records name each document by
    hash, checked here before anything is copied), then the interpretation cache
    pruned. No other campaign is touched, and nothing outside the store moves: the
    verb works in any checkout that holds the store, a reviewer's clone included
    (split from publish 2026-10-09; what derives from the store in THIS repository
    is rebuild-register's)."""
    root = Path(args.repo_root).resolve()
    work, bundles = Path(args.work).resolve(), Path(args.bundles).resolve()
    mine = canon_declarations(root, args.campaign_id)
    if not mine:
        raise CampaignError(f"canon holds no declarations for {args.campaign_id}")
    backend = mine[0].backend
    findings_src, records_src = work / "findings", work / "records" / args.campaign_id
    if not records_src.is_dir() or not findings_src.is_dir():
        raise CampaignError(f"{work} holds no records/{args.campaign_id}/ and findings/; run freeze-derived into it first")
    record_paths = sorted(p for p in records_src.iterdir()
                          if p.name in {name for _, name in RECORD_FILES.values()})
    if not record_paths:
        raise CampaignError(f"{records_src}: no record file ({sorted(name for _, name in RECORD_FILES.values())})")
    bound = {}
    for path in record_paths:
        bound.update(bound_documents(read_record(path)))
    docs = sorted(p for p in findings_src.glob("fs_*.json"))
    stale = [p.name for p in docs if bound.get(p.name) not in (None, sha256_file(p))]
    unbound = sorted(set(bound) - {p.name for p in docs})
    if stale or unbound:
        raise CampaignError(f"{work}: the records and the findings disagree (stale {stale}, bound but absent "
                            f"{unbound}); re-run freeze-derived")
    committed_sources_or_refuse(bundles / f"{args.campaign_id}_derived.zip")
    findings = root / CANON_FINDINGS
    records_dst = root / CANON_RECORDS / args.campaign_id
    # the documents the campaign's PREVIOUS records bound and the new ones do not: the superseded
    # interpretation's, which leave the store with it (2026-10-11: under the one document kind every
    # document changed its name, and the first re-stamp left every old document beside its new one)
    previous = {}
    if records_dst.is_dir():
        for path in sorted(records_dst.iterdir()):
            if path.name in {name for _, name in RECORD_FILES.values()}:
                previous.update(bound_documents(read_record(path)))
    superseded = sorted(name for name in previous if name not in bound and (findings / name).is_file())
    notes = [f"store-derived {args.campaign_id} on {backend}: {len(docs)} findings -> {findings.relative_to(root)}, "
             f"{len(record_paths)} record(s) -> {records_dst.relative_to(root)}, {len(superseded)} superseded document(s) removed"]
    for doc in docs:
        target = findings / doc.name
        state = "new" if not target.is_file() else ("same" if target.read_bytes() == doc.read_bytes() else "replaced")
        notes.append(f"findings {doc.name}: {state}")
    for name in superseded:
        notes.append(f"findings {name}: removed (bound by the previous records only)")
    for path in record_paths:
        target = records_dst / path.name
        state = "new" if not target.is_file() else ("same" if target.read_bytes() == path.read_bytes() else "replaced")
        notes.append(f"record {path.name}: {state}")
    if not args.plan_only:
        for name in superseded:
            (findings / name).unlink()
        for doc in docs:
            shutil.copyfile(doc, findings / doc.name)
        records_dst.mkdir(parents=True, exist_ok=True)
        for path in record_paths:
            shutil.copyfile(path, records_dst / path.name)
    plan = Plan()
    # the records moved: the interpretation they pinned before is an orphan in the cache beside the archive
    plan.add("prune interpretation cache", prune_cache_argv(root, bundles, None))
    ok = execute(plan, args.plan_only, notes)
    return 0 if ok else 1


def cmd_rebuild_register(args):
    """What derives from the store in THIS repository, rebuilt from the whole store: the
    register tables of both CAMPAIGN_NOTES (the Wilson fixture of the flow chain retired with F82:
    the one fit stage's intervals are tested against the threading golden), then the suite unless --no-suite. Once after a
    chain of store-derived runs, never per campaign (until 2026-10-09 publish did all of
    this per campaign). In a checkout without the notes the table step refuses by name
    before anything is written."""
    root = Path(args.repo_root).resolve()
    bundles = Path(args.bundles).resolve()
    central = conda_python(CENTRAL_ENV, args.conda_exe)
    plan = Plan()
    plan.add("register table", [sys.executable, "-B", Path(__file__).resolve(), "--repo-root", root, "table", "--write"])
    if not args.no_suite:
        plan.add("suite", central + ["-m", "pytest", "-q"], cwd=str(root))
    notes = [f"rebuild-register: the register tables of {len(NOTES)} notes" + ("" if args.no_suite else "; then the suite")]
    ok = execute(plan, args.plan_only, notes)
    return 0 if ok else 1

def prune_cache_argv(root, archive, cache) -> list:
    """scripts/prune_interpretation_cache.py on the launcher's own interpreter: stdlib-only by
    contract (it imports results_store and campaign_bundles, both on the admitted list), like the
    register table step."""
    argv = [sys.executable, "-B", "-E", "-s", Path(__file__).resolve().parent / "prune_interpretation_cache.py",
            "--repo-root", root]
    for flag, value in (("--archive", archive), ("--cache", cache)):
        if value is not None:
            argv += [flag, value]
    return argv


def cmd_prune_cache(args):
    """Remove the interpretation-cache entries no certification record pins (F72): the cache sits
    beside the archive (`<archive>/interpretations/<content id>/`) or where --cache says; an entry
    is keyed by content id, so a re-stamp leaves the previous one as an orphan, re-creatable from
    the archive's superseded folder on demand."""
    root = Path(args.repo_root).resolve()
    plan = Plan()
    plan.add("prune interpretation cache", prune_cache_argv(root, args.archive, args.cache))
    return 0 if execute(plan, args.plan_only) else 1


# --- the register table and next-id -----------------------------------------------------------

def load_records(root) -> dict:
    """{campaign: {family: {"raw": id, "derived": id or None, "records": [names]}}} from the record
    files under CANON_RECORDS/<campaign>/ (one campaign per file, the directory names it)."""
    out = {}
    base = Path(root) / CANON_RECORDS
    for path in sorted(base.glob("*/*.json")) if base.is_dir() else []:
        name = path.name
        if name not in {n for _, n in RECORD_FILES.values()}:
            continue
        doc = read_record(path)
        for campaign, families in doc.get("campaigns", {}).items():
            for family, entry in families.items():
                if not isinstance(entry, dict) or "bundle" not in entry:
                    continue
                slot = out.setdefault(campaign, {}).setdefault(family, {"raw": None, "derived": None, "records": []})
                slot["raw"] = entry["bundle"]["content_id_sha256"]
                derived = entry.get("derived_bundle")
                slot["derived"] = derived["content_id_sha256"] if derived else slot["derived"]
                if name not in slot["records"]:
                    slot["records"].append(name)
    return out


def family_cell(registry, backend, declaration) -> str:
    """'<family id> vN (<variants>)': the version the registry assigns to the manifest hash the declaration
    binds (2026-10-03; a family id names a lineage, the hash a realization); a hash the registry does not hold
    is marked, loudly, never guessed; without a registry (a scratch tree) the cell is unversioned."""
    cell = declaration.family_id
    if registry is not None:
        try:
            cell += f" {fv.label(fv.version_for(registry, backend, declaration.family_id, declaration.manifest_sha256))}"
        except ValueError:
            cell += f" (manifest {declaration.manifest_sha256[:12]} unregistered)"
    return f"{cell} ({len(declaration.variants)})"


def render_register(backend, root=ROOT) -> str:
    declarations = [d for d in canon_declarations(root) if d.backend == backend]
    records = load_records(root)
    registry = fv.load(root) if (Path(root) / fv.REGISTRY_PATH).is_file() else None
    lines = [REGISTER_START, "",
             "| campaign | families (version, variants) | models | repetitions | reference | raw content id | derived content id | records | note |",
             "|---|---|---|---|---|---|---|---|---|"]
    by_campaign = {}
    for d in declarations:
        by_campaign.setdefault(d.campaign_id, []).append(d)
    for campaign in sorted(by_campaign, key=lambda c: (len(c), c)):
        decls = by_campaign[campaign]
        fams = ", ".join(family_cell(registry, backend, d) for d in decls)
        models = ", ".join(decls[0].models)
        reps = f"{len(decls[0].repetitions)} ({decls[0].repetition_axis['kind']})"
        reference = decls[0].reference_model or "none"
        bound = records.get(campaign, {})
        raws = sorted({v["raw"][:12] for v in bound.values() if v["raw"]})
        deriveds = sorted({v["derived"][:12] for v in bound.values() if v["derived"]})
        names = sorted({n.split(".")[0] for v in bound.values() for n in v["records"]})
        unbound = [d.family_id for d in decls if d.family_id not in bound]
        raw_cell = ", ".join(raws) if raws else "unpublished"
        derived_cell = ", ".join(deriveds) if deriveds else ("mixed raw" if raws else "unpublished")
        rec_cell = ", ".join(names) if names else "none"
        if unbound and bound:
            rec_cell += f"; unbound: {', '.join(unbound)}"
        note = decls[0].doc.get("note", "").replace("|", "/")
        lines.append(f"| {campaign} | {fams} | {models} | {reps} | {reference} | {raw_cell} | {derived_cell} | {rec_cell} | {note} |")
    if not by_campaign:
        lines.append("| (no campaign under the new records yet) | | | | | | | | |")
    lines += ["", REGISTER_END]
    return "\n".join(lines)


def register_block(text) -> tuple:
    start, end = text.find(REGISTER_START), text.find(REGISTER_END)
    if start < 0 or end < 0 or end < start:
        return None
    return start, end + len(REGISTER_END)


def cmd_compare_lineages(args):
    """Compare two interpretations (scripts/compare_lineages.py, lineage_comparison/v0.1): exit 0 on
    RE-STAMP, 1 on INSTRUMENT CHANGE, 2 on a refusal; the tool's own report, not a plan."""
    root = Path(args.repo_root).resolve()
    argv = conda_python(CENTRAL_ENV, args.conda_exe) + [str(root / "scripts/compare_lineages.py"), "--a", args.a, "--b", args.b,
                                                         "--repo-root", str(root)]
    for flag, value in (("--out", args.out), ("--archive", args.archive), ("--cache", args.cache)):
        if value:
            argv += [flag, str(value)]
    if args.across_campaigns:
        argv.append("--across-campaigns")
    if args.accept_instrument_change:
        argv.append("--accept-instrument-change")
    if args.plan_only:
        print("  " + " ".join(argv))
        return 0
    return subprocess.run(argv).returncode


def cmd_stale(args):
    """Every pinned interpretation against this checkout (scripts/stale_interpretations.py): exit 0 when
    all verify, 1 when any is stale, 2 on a refusal; the tool's own table, not a plan. The re-stamp set
    is read here over both backends, never inferred from where the values could move (F81, 2026-10-09:
    the shared driver moved with an Isaac-only re-stamp and staled all nine CARLA bundles)."""
    root = Path(args.repo_root).resolve()
    argv = conda_python(CENTRAL_ENV, args.conda_exe) + [str(root / "scripts/stale_interpretations.py"),
                                                         "--repo-root", str(root), "--bundles", str(Path(args.bundles).resolve())]
    for campaign in args.campaign or []:
        argv += ["--campaign", campaign]
    if args.out:
        argv += ["--out", str(args.out)]
    if args.plan_only:
        print("  " + " ".join(argv))
        return 0
    return subprocess.run(argv).returncode


def cmd_table(args):
    root = Path(args.repo_root).resolve()
    drift = []
    for backend, rel in NOTES.items():
        rendered = render_register(backend, root)
        notes_path = root / rel
        text = notes_path.read_text()
        span = register_block(text)
        if span is None:
            raise CampaignError(f"{rel} has no register block ({REGISTER_START[:40]}...); place the markers once")
        current = text[span[0]:span[1]]
        if current == rendered:
            print(f"{rel}: register up to date")
            continue
        drift.append(rel)
        if args.write:
            notes_path.write_text(text[:span[0]] + rendered + text[span[1]:])
            print(f"{rel}: register rewritten")
        else:
            print(f"{rel}: register differs from canon (run `campaign table --write`)")
            if args.verbose:
                print(rendered)
    return 1 if (drift and args.check) else 0


def next_id(backend, root=ROOT, bundles=None) -> str:
    """The next unused campaign id for a backend: one past the highest number
    seen in canon declarations and in the bundle directory given (the local
    archive); the notes' register is the human check."""
    seen = []
    for d in canon_declarations(root):
        m = ID_PATTERN[backend].match(d.campaign_id)
        if m:
            seen.append(int(m.group(1)))
    for directory in ([Path(bundles)] if bundles else []):
        if directory.is_dir():
            for p in directory.iterdir():
                m = ID_PATTERN[backend].match(p.name.replace("_derived", "").rsplit(".", 1)[0])
                if m:
                    seen.append(int(m.group(1)))
    return f"{ID_PREFIX[backend]}{(max(seen) + 1) if seen else 1:02d}"


def cmd_next_id(args):
    root = Path(args.repo_root).resolve()
    print(next_id(args.backend, root, args.bundles))
    return 0


# --- main -------------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", default=str(ROOT), help="the checkout whose canon and notes the verbs read and write")
    ap.add_argument("--conda-exe", default="conda", help="the launcher every spawned tool runs through (conda, micromamba)")
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("run", help="the runner per family, from a launch spec")
    p.add_argument("spec", type=Path)
    p.add_argument("--families", nargs="+", default=None, help="a subset of the spec's families (the multi-node split)")
    p.add_argument("--gpu", type=int, default=None)
    p.add_argument("--results-root", default=None,
                   help="where this machine puts the results tree (a machine fact, not a campaign fact; required when the spec carries no results_root)")
    p.add_argument("--bundles", default=None, help="the archive directory next-id also reads")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("freeze-raw", help="verify the tree and freeze the raw zip, declarations laid beside it")
    p.add_argument("--results-root", required=True)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--allow-mixed-vintage", action="store_true")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_freeze_raw)

    p = sub.add_parser("score", help="unpack a raw zip and run the scoring driver; adopts the declarations into canon")
    p.add_argument("--raw", required=True)
    p.add_argument("--work", required=True, help="a new directory: unpacked/ and scores/ go here")
    p.add_argument("--instruments", required=True,
                   help="one JSON file, a section per backend, paths relative to the repository root; the repository "
                        "ships it at artifacts/examples/campaigns/instruments.json")
    p.add_argument("--map", default=None,
                   help="the full CARLA OpenDRIVE map the oncoming-flow map check reads (outside the repository); "
                        "required for a campaign holding a flow family, refused otherwise")
    p.add_argument("--jobs", type=int, default=1,
                   help="the driver's lanes side by side, at most this many steps at once (default 1); the driver "
                        "admits them by the measured peak of its finished steps against the memory available (F67)")
    p.add_argument("--accept-instrument-change", action="store_true",
                   help="isaac: score under a robot record or family that differ from what the run pinned, recording "
                        "the change (principle 13); refused otherwise")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("freeze-derived", help="stage, freeze the one derived zip, scratch-bind the records")
    p.add_argument("--scores", required=True, help="the scoring driver's --out")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--bundles", required=True, help="holds <id>.zip; <id>_derived.zip is written here")
    p.add_argument("--results-root", required=True, help="the unpacked raw tree (score's work/unpacked)")
    p.add_argument("--instruments", required=True)
    p.add_argument("--work", required=True, help="a new directory for the scratch bind")
    p.add_argument("--against", default=None,
                   help="a previous interpretation to compare the fresh folder with after the freeze (campaign id, "
                        "<campaign>@<content id>, derived zip or folder); an INSTRUMENT CHANGE stops before the bind")
    p.add_argument("--instrument-change", action="store_true",
                   help="with --against: accept an INSTRUMENT CHANGE verdict, recorded in the report, and bind anyway")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_freeze_derived)

    p = sub.add_parser("store-derived", help="the findings and the records freeze-derived bound into the store, the cache "
                                              "pruned; works in any checkout that holds the store")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--work", required=True, help="freeze-derived's --work (holds findings/ and records/<id>/)")
    p.add_argument("--bundles", required=True, help="the archive directory holding the zips (the committed-sources check reads the derived zip)")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_store_derived)

    p = sub.add_parser("rebuild-register", help="what derives from the store in this repository: the register tables, "
                                                 "the Wilson fixture, then the suite; once after a chain")
    p.add_argument("--bundles", required=True, help="the archive directory holding the zips (the Wilson fixture reads them)")
    p.add_argument("--no-suite", action="store_true")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_rebuild_register)

    p = sub.add_parser("compare-lineages", help="two interpretations of a campaign: RE-STAMP or INSTRUMENT CHANGE, "
                                                "members by class, rows and leaves that moved (F34)")
    p.add_argument("--a", required=True, help="campaign id | <campaign>@<content id> | derived zip | folder")
    p.add_argument("--b", required=True)
    p.add_argument("--out", default=None, help="write the lineage_comparison/v0.1 report here")
    p.add_argument("--archive", default=None)
    p.add_argument("--cache", default=None)
    p.add_argument("--across-campaigns", action="store_true")
    p.add_argument("--accept-instrument-change", action="store_true")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_compare_lineages)
    p = sub.add_parser("prune-cache", help="remove the interpretation-cache entries no certification record pins "
                                            "(the cache beside the archive, or --cache; F72)")
    p.add_argument("--archive", default=None, help="the archive directory (default: SCL_CAMPAIGN_BUNDLES or ~/scl/campaigns)")
    p.add_argument("--cache", default=None, help="the cache to prune (default: <archive>/interpretations)")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_prune_cache)
    p = sub.add_parser("stale", help="every interpretation the records pin against this checkout, both backends: "
                                      "listed sources, canon inputs and path pins by hash; exit 1 when any is stale (F81)")
    p.add_argument("--bundles", required=True, help="the archive directory holding the derived zips the records pin")
    p.add_argument("--campaign", action="append", default=None, help="one campaign (repeatable); default every one with records")
    p.add_argument("--out", default=None, help="write the rows as a stale_interpretations/v0.1 report here")
    p.add_argument("--plan-only", action="store_true")
    p.set_defaults(func=cmd_stale)
    p = sub.add_parser("table", help="the campaign register blocks of both CAMPAIGN_NOTES")
    p.add_argument("--write", action="store_true")
    p.add_argument("--check", action="store_true", help="exit 1 when a block is stale")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_table)

    p = sub.add_parser("next-id", help="the next unused campaign id for a backend")
    p.add_argument("--backend", required=True, choices=BACKENDS)
    p.add_argument("--bundles", default=None)
    p.set_defaults(func=cmd_next_id)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except CampaignError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
