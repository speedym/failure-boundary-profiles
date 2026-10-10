#!/usr/bin/env python3
"""Mechanical completeness manifest for a campaign run tree.

The farm ships RAW outputs; all interpretation happens centrally. This
tool is the edge's only check: it verifies that every expected
(variant x seed x model) cell produced its artifacts, collects harness
status strings VERBATIM (never mapped - a status this tool has never
seen must survive into the report unchanged), and pins provenance.
It deliberately contains no outcome vocabulary: completeness, not
judgment.

Layout expected under --run-dir (one family-run):
    <model>/seed_<n>/<scenario>_vNN.json   leaderboard result (per variant)
    <model>/seed_<n>/<scenario>_vNN.log
    <model>/seed_<n>/trajectories/<route_id>.json.gz
    <model>/seed_<n>/recordings/<route_id>.log   (when header recordings)
    <model>/seed_<n>/ledger.jsonl          run_header + attempt records

Exit codes: 0 complete; 1 missing cells/artifacts; 2 integrity or
vintage errors (unparseable results, corrupt extracts, mixed
upstream_pin_commit, per-variant route-XML sha divergence, a run header
whose agent-identity fingerprint is not the one campaign_meta recorded).

Since F18 (2026-09-13) a family-run tree carries what the runner wrote at
invocation: campaign_declaration.json (the EXPECTATION, campaign_declaration/
v0.2 - models, repetitions, variants, the family by path and hash) and
campaign_meta.json (the INSTRUMENT record, campaign_meta/v0.3 - tools,
routes, every agent's identity once, the simulator pin). Verification reads
the expected models and repetitions from the declaration when one is
present (CLI hints must agree), and --campaign <canon declaration> refuses
a tree whose declaration is not byte-for-byte the canon file. Bundling
refuses a subtree whose meta says smoke (a throwaway run can never become
evidence) and one that carries a declaration without its meta; a tree with
neither is legacy (pre-F18) and freezes with "instrument_record: legacy:
none" in the provenance.

--produce-bundle PATH freezes a VERIFIED campaign into a citable zip
(S15 archive discipline, 2026-09-02; mirror of the Isaac
campaign_manifest.py flag): refused unless verification exits 0 and
scoring outputs sit beside the tree (--allow-unscored overrides,
recorded). --snapshot-dir optionally embeds an evidence dir (e.g. the
solved family) as <campaign_id>/family_snapshot/. The bundle holds the
full run tree, MANIFEST.sha256 over every data file, and
bundle_provenance.json; zip entries are sorted with fixed timestamps,
so the MANIFEST sha is the date-invariant content id to cite.

Stdlib-only and py3.10-safe: runs under either conda env, farm or
central.
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date as _date
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from scl_core import campaign_records  # noqa: E402  (stdlib-only core: the two records, one shape on both backends)
from scl_core import campaign_bundles as cb  # noqa: E402  (stdlib-only core: the two-bundle discipline, F30)
from scl_core.agent_identity import identity_fingerprint  # noqa: E402,F401  (re-exported for the tests)
from scl_core.campaign_declaration import load_declaration  # noqa: E402  (stdlib-only core)
from scl_core.campaign_records import (META_KIND, agent_fingerprints, bundle_fitness,  # noqa: E402,F401
                                       read_tree_expectation)

MANIFEST_VERSION = 1
# The backend names its repetition axis honestly (shared campaign_meta
# contract, S19/F2); the same vocabulary the declaration reader accepts.
REPETITION_AXIS = {
    "kind": "traffic_seed_strata",
    "note": ("the sim is measurably nondeterministic: each run is a "
             "Bernoulli sample, seeds are strata - the Isaac analog axis "
             "is policy_reps")}

# fixed zip entry timestamp: bundle bytes depend only on contents
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# F30 (2026-09-14): a raw bundle holds the runner's evidence and nothing
# computed from it. What the CARLA runner writes at the family-run top level
# beside the declared cells (<model>/seed_<k>/); the verifier's own two
# files are the completeness receipt and travel with the evidence.
CAMPAIGN_FILES = frozenset({"campaign_declaration.json", "campaign_meta.json", "summary.csv",
                            "campaign_manifest.json", "campaign_manifest_summary.txt"})
# The producers a derived bundle's source manifest lists (F36, 2026-09-15: one
# list, one writer, one manifest shape for every campaign, whichever chains
# scored it): every tool that turns this backend's raw tree into rows, fits,
# findings, audits and records on both chains: the scoring driver's tools,
# the aggregator, the zone builder, the map checker, the emitters, the
# replay, and this writer (its scoring_chain decides which chain scores a
# family). The modules they import (the adapter's, the core's) are READ from
# the sources, never typed here (scl_core.campaign_bundles.source_closure,
# F44, 2026-09-19); the driver and environment.yml are seeds too. NOT the
# binders (certify_threading_findings.py, certify_flow_campaigns.py) nor the
# fixture extractor (extract_wilson_pairs.py): they read the derived bundle
# and pin their own hash in the record they write, so an edit to them
# re-stales no bundle. Tests are not sources. Instrument files and the
# campaign's declarations are added per campaign (lay_source_manifest).
DERIVED_SOURCES = ("score_runs.py", "boundary_stats.py", "findings_carla.py", "findings_flow.py",
                   "flow_aggregate.py", "derive_measured_go.py", "route_metrics.py", "trajectory_extract.py",
                   "regenerate_threading_findings.py", "build_conflict_lane_zone.py",
                   "regenerate_flow_findings.py", "audit_flow_commitment.py", "verify_flow_map.py",
                   "replay_flow_bundle.py", "campaign_manifest.py")
SOURCE_MANIFEST = cb.SOURCE_MANIFEST


def derived_source_files() -> list:
    """Every file a derived folder's content depends on, resolved in this tree: the producers, the
    driver, environment.yml, and every in-repo module they import transitively (the adapter's
    modules, the core's: scl_core.campaign_bundles.source_closure reads the imports; nothing
    beyond the producers is listed by hand). Measured 2026-09-19: 30 files, 9 of scl_core; the
    geometry, IR and solver stack is not among them, no producer imports it."""
    here = Path(__file__).resolve().parent
    seeds = {here / name for name in DERIVED_SOURCES}
    seeds.update((_REPO_ROOT / "environment.yml", _REPO_ROOT / "scripts/score_campaign.py"))
    return cb.source_closure(seeds, _REPO_ROOT)


def lay_source_manifest(folder: Path, declarations=(), extra_files=()) -> str:
    """Write the source manifest of a derived folder (`<folder>/source_snapshot/source_manifest.json`,
    scl_core.campaign_bundles.lay_source_manifest: every listed file by hash, the git identity of
    this checkout, the runtime; no copy travels since 2026-09-19). Listed: the one source list,
    the instrument files passed in `extra_files`. The campaign's declarations and their family
    manifests are NOT sources (principle 13, 2026-09-21): the writer records them as raw members by hash. Returns the manifest file's sha256: the snapshot id a derived bundle records and
    the replay verifies a checkout against. Deterministic for one tree, one interpreter and one
    git state, so the stage, the freeze and the bind lay the same manifest and agree by id."""
    files = set(derived_source_files())
    files.update(Path(p).resolve() for p in extra_files)
    # the declarations and the family manifests are NOT sources (principle 13, 2026-09-21): the
    # interpretation took them from the raw bundle, and the derived writer records them as raw
    # members by hash (cb.evidence_inputs); `declarations` is kept for the callers' shape
    del declarations
    return cb.lay_source_manifest(folder, files, _REPO_ROOT)


AXIS_KINDS = ("measured", "categorical", "behavior_scalar")


def family_axis(manifest: dict) -> tuple:
    """(factor id, kind) of a driving family's one scheduled axis, read from the family
    manifest alone, the document every bundle pins (until 2026-10-07 the chain read the
    authoring plan for the factor and the backend registry for its kind; the manifest's
    effective_domain.factor_id is that factor, checked equal for every canon family). The
    kind is the manifest's own data, never a name list:
      categorical      effective_domain.candidates is present: the axis is a set of identities;
      measured         the factor is a key of every variant's measured block: the chain's x is
                       that measured value, never the target;
      behavior_scalar  neither: the factor is declared per variant and realized as a behavior
                       parameter, measured only under simulation (the flow chain, at the zone).
    Refuses a manifest without a factor id or without variants, and one whose factor is
    measured in some variants but not all (a half-measured axis is no axis)."""
    name = manifest.get("manifest_id") or "<manifest>"
    domain = manifest.get("effective_domain") or {}
    factor = domain.get("factor_id")
    if not isinstance(factor, str) or not factor:
        raise ValueError(f"{name}: no effective_domain.factor_id; one scoring chain needs one scheduled axis")
    variants = [v for v in manifest.get("variants") or [] if isinstance(v, dict)]
    if not variants:
        raise ValueError(f"{name}: no variants; an axis is read from what the variants carry")
    if "candidates" in domain:
        return factor, "categorical"
    measured = [factor in (v.get("measured") or {}) for v in variants]
    if all(measured):
        return factor, "measured"
    if any(measured):
        raise ValueError(f"{name}: factor {factor} measured in {sum(measured)} of {len(variants)} variants; "
                         "a half-measured axis is no axis")
    return factor, "behavior_scalar"


def scoring_chain(declaration) -> str:
    """Which chain scores a family, read from the family manifest (family-ness
    is data, never a flag): a behavior-scalar axis (oncoming flow, measured at
    a declared conflict zone by the flow chain) or a measured or categorical
    one (measured on the designated passage by the static-threading chain);
    family_axis says which. Stdlib only, so any interpreter can ask."""
    manifest = json.loads((_REPO_ROOT / declaration.manifest_path).read_text())
    return "flow" if family_axis(manifest)[1] == "behavior_scalar" else "threading"


def raw_entries(family_dir: Path, arc_prefix: str, declaration) -> list:
    """The raw members of a DECLARED family-run tree: the declared
    (model, seed) cells, the runner's campaign files and the family
    snapshot; anything else refuses (RawTreeViolation names it)."""
    cells = {m: {f"seed_{k}" for k in declaration.repetitions} for m in declaration.models}
    return cb.raw_family_entries(family_dir, arc_prefix, CAMPAIGN_FILES, cells, extra_dirs=("family_snapshot",))


def produce_derived(out_zip: Path, raw_zip: Path, folder: Path, sources, declarations=()) -> int:
    """Freeze the derived folder a scoring run left for one raw bundle
    (F30): the core writer keys it by the raw content id; refusals name the
    contract. The ONE writer for every campaign (F36): a folder that already
    carries a source manifest (the flow certifier's stage lays one, with the
    instrument files) is frozen as it stands; otherwise the manifest is laid
    here from the one list plus `sources` and the declarations passed, so
    the frozen folder is exactly the bundle. When declarations are passed,
    the folder's families must be the declared ones; the writer records each
    declaration and the family as run as raw members by hash (principle 13). The git identity is measured and recorded, not
    gated here: `campaign publish` refuses a bundle frozen from a tree whose
    listed sources were modified against the commit."""
    folder = Path(folder)
    manifest = folder / cb.SNAPSHOT_DIR / SOURCE_MANIFEST
    families = sorted(p.name for p in folder.iterdir() if p.is_dir() and p.name != cb.SNAPSHOT_DIR) \
        if folder.is_dir() else []
    declared = sorted({d.family_id for d in declarations})
    if declarations and families != declared:
        print(f"DERIVED BUNDLE REFUSED: the folder holds families {families}, the declarations name {declared}")
        return 2
    if manifest.is_file():
        if sources:
            print("DERIVED BUNDLE REFUSED: the folder carries a staged source manifest; --source adds to a "
                  "manifest laid at the freeze, never to a staged one (re-stage with the files instead)")
            return 2
        pass   # a staged manifest lists sources only; the declarations are recorded as raw members below
    else:
        try:
            lay_source_manifest(folder, declarations, sources or ())
        except ValueError as exc:   # BundleError is one
            print(f"DERIVED BUNDLE REFUSED: {exc}")
            return 2
    try:
        prov = cb.write_derived_bundle(out_zip, folder, raw_zip, None, _REPO_ROOT,
                                       writer_sha256=sha256_file(Path(__file__).resolve()), declarations=declarations)
    except cb.BundleError as exc:
        print(f"DERIVED BUNDLE REFUSED: {exc}")
        return 2
    print(f"derived bundle: {out_zip}")
    print(f"  raw content id: {prov['raw_bundle']['content_id_sha256']} ({prov['raw_bundle']['bundle_kind']})")
    git = prov["source_snapshot"].get("git") or {}
    print(f"  families: {prov['families']}; sources listed: {len(prov['source_snapshot']['files'])} at commit "
          f"{(git.get('commit') or '?')[:12]}"
          + (f"; {len(git['modified'])} MODIFIED against it ({git['modified'][:3]}...): publish will refuse"
             if git.get("modified") else ""))
    print(f"  derived content id (MANIFEST sha256): {prov['manifest_sha256']}")
    return 0


def produce_bundle(out_zip: Path, run_dir: Path, snapshot_dir,
                   manifest: dict, allow_unscored: bool) -> int:
    """Freeze a VERIFIED campaign tree into a citable zip (caller must
    have established exit 0; this adds only the scoring gate)."""
    campaign_id = run_dir.name
    if read_tree_expectation(run_dir)[0] is not None:
        print("BUNDLE REFUSED: a declared tree freezes at campaign level "
              "(--campaign-dir <results-root>/<campaign-id>, one raw zip per "
              "campaign, F30); --run-dir bundling serves pre-F18 trees")
        return 2

    def _has_content(p: Path) -> bool:
        return (p.is_file() and p.stat().st_size > 0) or \
               (p.is_dir() and any(p.iterdir()))

    scoring = [d for d in ("scoring_out", "scoring", "stats_out")
               if _has_content(run_dir / d)]
    if not scoring and not allow_unscored:
        print("BUNDLE REFUSED: no scoring outputs beside the tree; score "
              "first, or pass --allow-unscored to freeze an unscored "
              "campaign (recorded in provenance)")
        return 2

    entries = []
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.resolve() == out_zip.resolve():
            continue
        entries.append((f"{campaign_id}/{p.relative_to(run_dir)}", p))
    if snapshot_dir is not None:
        for p in sorted(Path(snapshot_dir).glob("*")):
            if p.is_file() and not p.name.endswith(".gz"):
                entries.append(
                    (f"{campaign_id}/family_snapshot/{p.name}", p))

    provenance = {
        "bundle_kind": "campaign_bundle/v0.1",
        "campaign_id": campaign_id,
        "produced_date": _date.today().isoformat(),
        "verification": {"problems": len(manifest.get("problems", [])),
                         "severe": len(manifest.get("severe", []))},
        "scoring_included": bool(scoring),
        "manifest_sha256": None,
        "bundler_sha256": sha256_file(Path(__file__).resolve()),
        "source_git_describe": os.environ.get("SCL_GIT_DESCRIBE"),
        "note": ("zip entries sorted, timestamps fixed: re-bundling an "
                 "unchanged tree reproduces the MANIFEST sha exactly; "
                 "cite manifest_sha256 as the content id"),
    }
    provenance = cb.write_zip(out_zip, campaign_id, entries, provenance)
    manifest_sha = provenance["manifest_sha256"]
    print(f"bundle: {out_zip}")
    print(f"  files: {len(entries)} (+ manifest, provenance); "
          f"scoring_included={bool(scoring)}")
    print(f"  content id (MANIFEST sha256): {manifest_sha}")
    print(f"  zip sha256: {sha256_file(out_zip)}")
    return 0


# --- campaign-time instrument recording (S17, 2026-09-04) -------------------
# The runner records WHAT PRODUCED THE EVIDENCE at campaign start, so a
# later bundling certifies by lookup (recorded shas matched against git
# blobs) instead of forensics. Mirrors the Isaac runner's campaign_meta
# (adapters/isaac/scripts/multi_model_eval/evaluate_models.py); lives
# here (stdlib, no env guard) so it is testable under scl_prototype.

# driver-level closure: the adapter files that produce or shape the
# evidence. Campaign-specific inputs (routes, models) are recorded as
# data in the meta itself, never baked into this list (the F5 lesson:
# a driver must not hardcode one family's shape).
CAMPAIGN_CLOSURE = [
    "evaluate_models.py",
    "campaign_manifest.py",
    "score_runs.py",
    "trajectory_extract.py",
    "models.yaml",
    "agents/pdm_lite_agent.py",
    "agents/tfpp_viz_agent.py",
    "agents/simlingo_agent.py",
    "agents/hipad_agent.py",
    "agents/plant2_agent.py",
]


def build_campaign_meta(script_dir: Path, out_root: Path, routes,
                        models, seeds, upstream_pin_head=None,
                        model_identity=None, campaign_id=None, family_id=None,
                        declaration_sha256=None, smoke=False, family_coverage=None,
                        host=None, gpu=None, simulator_build=None) -> dict:
    """The run-time instrument record (campaign_meta/v0.4, built by
    scl_core.campaign_records for both backends) with CARLA's instruments:
    the closure tools and the route XML hashes; refuses (SystemExit) if any
    closure file is missing from the deployment - a campaign whose tools
    cannot be fingerprinted cannot be certified later. model_identity: every
    agent's measured identity (plus environment), recorded HERE once; run
    headers point at it by fingerprint."""
    absent = [n for n in CAMPAIGN_CLOSURE
              if not (script_dir / n).is_file()]
    if absent:
        raise SystemExit(
            "PREFLIGHT REFUSED: provenance closure files missing from "
            f"the deployment: {absent} - a campaign records the sha256 "
            "of every tool that produces its evidence (campaign_meta), "
            "and cannot start without them")
    return campaign_records.build_campaign_meta(
        campaign_id=campaign_id or out_root.name, family_id=family_id,
        tools_sha256={n: sha256_file(script_dir / n) for n in CAMPAIGN_CLOSURE},
        models=models, repetitions=seeds, repetition_axis=REPETITION_AXIS,
        cells_planned=len(models) * len(seeds) * len(routes),
        layout_template="{model}/seed_{seed}/{route_id}.json",
        agent_identity=model_identity,
        simulator={"kind": "fail2drive_fork", "pin": upstream_pin_head, "build": simulator_build},
        instruments={"routes_sha256": {Path(r).name: sha256_file(Path(r))
                                       for r in sorted(routes, key=lambda r: Path(r).name)}},
        declaration_sha256=declaration_sha256, smoke=smoke,
        started_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        python=sys.version.split()[0], source_git_describe=os.environ.get("SCL_GIT_DESCRIBE"),
        family_coverage=family_coverage, host=host, gpu=gpu)


def write_or_refuse_campaign_meta(out_root: Path, meta: dict) -> None:
    """scl_core.campaign_records.write_or_refuse_campaign_meta with the
    runner's exit: a resume under changed instruments refuses."""
    try:
        campaign_records.write_or_refuse_campaign_meta(out_root, meta)
    except campaign_records.RecordConflict as exc:
        raise SystemExit(f"REFUSED: {exc}")


def build_declaration(campaign_id, manifest_path, repo_root, routes, models, seeds,
                      reference_model, note, repetition_axis=None, version=None) -> dict:
    """CARLA's arguments as a declaration: the variant ids are the route
    file stems (scl_core.campaign_records.build_declaration does the rest;
    `version` is the launcher's registry claim, 2026-10-03)."""
    return campaign_records.build_declaration(
        campaign_id, "fail2drive", manifest_path, repo_root, [Path(r).stem for r in routes],
        models, seeds, repetition_axis or REPETITION_AXIS, reference_model, note, version=version)


def write_or_refuse_declaration(out_root: Path, doc: dict) -> str:
    try:
        return campaign_records.write_or_refuse_declaration(out_root, doc)
    except campaign_records.RecordConflict as exc:
        raise SystemExit(f"REFUSED: {exc}")


def check_written_declaration(out_root: Path, manifest_path) -> None:
    campaign_records.check_written_declaration(out_root, manifest_path)


def write_or_refuse_family_snapshot(out_root: Path, manifest_path, family_id: str) -> str:
    """The tree's family_snapshot/<family_id>.json: the manifest the
    declaration pins, byte for byte (raw member; the certifiers bind it)."""
    try:
        return campaign_records.write_or_refuse_family_snapshot(out_root, manifest_path, f"{family_id}.json")
    except campaign_records.RecordConflict as exc:
        raise SystemExit(f"REFUSED: {exc}")


# --- campaign-level bundling (S19 ruling, Milan 2026-09-05) -----------------
# A campaign zip is the exchange unit on BOTH backends (one file, one
# content id: transfers are complete-or-absent). Layout parity:
# <campaign_id>/<family_id>/<family run tree in backend format>, with
# campaign-level files at the root. Family subtrees are discovered
# mechanically, each verified by the UNCHANGED per-family checks, and
# the campaign refuses whole if any family fails.

def discover_family_subtrees(campaign_dir: Path):
    """Child dirs that look like family-run trees (<model>/seed_*)."""
    return [d for d in sorted(p for p in campaign_dir.iterdir()
                              if p.is_dir())
            if discover_grid(d)]


def produce_campaign_bundle(out_zip: Path, campaign_dir: Path,
                            reports: dict, allow_unscored: bool,
                            reproducible_source: str | None = None) -> int:
    """Freeze a VERIFIED multi-family campaign into one citable zip.
    Caller must have established exit 0 for EVERY family subtree; this
    adds only the per-family scoring gate. bundle_provenance carries a
    families block with a per-family sub-id (sha over that subtree's
    MANIFEST lines) so figures can cite one family without a separate
    zip."""
    campaign_id = campaign_dir.name

    def _has_content(p: Path) -> bool:
        return (p.is_file() and p.stat().st_size > 0) or \
               (p.is_dir() and any(p.iterdir()))

    unfit, instrument_record, declarations = [], {}, {}
    for name in reports:
        unfit_reason, instrument_record[name] = bundle_fitness(campaign_dir / name)
        if unfit_reason:
            unfit.append(f"{name}: {unfit_reason}")
        declarations[name] = read_tree_expectation(campaign_dir / name)[0]
    if unfit:
        print("BUNDLE REFUSED: " + "; ".join(unfit))
        return 2
    declared = sorted(n for n, d in declarations.items() if d is not None)
    if declared and len(declared) != len(reports):
        print(f"BUNDLE REFUSED: families {declared} carry a declaration, "
              f"{sorted(set(reports) - set(declared))} do not - one campaign "
              "is one generation of records")
        return 2

    if declared:
        # F30: RAW ONLY. The declared cells, the runner's campaign files and
        # the family snapshot; derived outputs left beside a tree refuse
        # (they freeze separately: --produce-derived). No scoring gate: the
        # evidence id must not depend on any interpretation.
        entries = []
        for name in sorted(reports):
            try:
                entries.extend(raw_entries(campaign_dir / name, f"{campaign_id}/{name}",
                                           declarations[name]))
            except cb.RawTreeViolation as exc:
                print(f"BUNDLE REFUSED: {name}: {exc}")
                return 2
        if allow_unscored:
            print("note: --allow-unscored has no meaning for a raw bundle (it never holds scoring)")
        unscored = list(reports)
        bundle_kind = cb.RAW_KIND
    else:
        unscored = [name for name in reports
                    if not any(_has_content(campaign_dir / name / d)
                               for d in ("scoring_out", "scoring", "stats_out",
                                         "flow_stats"))]
        if unscored and not allow_unscored:
            print(f"BUNDLE REFUSED: family subtrees without scoring outputs: "
                  f"{unscored}; score first, or pass --allow-unscored to "
                  f"freeze unscored (recorded in provenance)")
            return 2
        entries = []
        for p in sorted(campaign_dir.rglob("*")):
            if not p.is_file() or p.resolve() == out_zip.resolve():
                continue
            entries.append((f"{campaign_id}/{p.relative_to(campaign_dir)}", p))
        bundle_kind = "campaign_bundle/v0.2"

    sub_ids = cb.family_sub_ids(cb.manifest_text(entries), campaign_id, sorted(reports))
    families = {}
    for name, rep in sorted(reports.items()):
        families[name] = {
            "family_sub_id_sha256": sub_ids[name],
            "verification": {
                "expected_cells": rep["coverage"]["expected_cells"],
                "found_cells": rep["coverage"]["found_cells"],
                "problems": len(rep["problems"]),
                "severe": len(rep["severe"]),
            },
            "instrument_record": instrument_record[name],
        }
        if not declared:
            families[name]["scoring_included"] = name not in unscored
            # legacy provenance key order: scoring_included before instrument_record
            families[name] = {k: families[name][k] for k in
                              ("family_sub_id_sha256", "verification", "scoring_included", "instrument_record")}

    provenance = {
        "bundle_kind": bundle_kind,
        "campaign_id": campaign_id,
        "produced_date": None if reproducible_source else _date.today().isoformat(),
        "families": families,
        "manifest_sha256": None,
        "bundler_sha256": sha256_file(Path(__file__).resolve()),
        "source_git_describe": None if reproducible_source else os.environ.get("SCL_GIT_DESCRIBE"),
        "source_snapshot_sha256": reproducible_source,
        "note": ("campaign-level bundle: one zip per campaign, family "
                 "subtrees inside (layout parity across backends, "
                 "ruling 2026-09-05); zip entries sorted, timestamps "
                 "fixed - re-bundling an unchanged tree reproduces the "
                 "MANIFEST sha exactly; cite manifest_sha256 as the "
                 "campaign content id, families.*.family_sub_id_sha256 "
                 "for one family"
                 + ("; RAW ONLY (F30): the evidence id, nothing derived inside" if declared else "")),
    }
    provenance = cb.write_zip(out_zip, campaign_id, entries, provenance)
    manifest_sha = provenance["manifest_sha256"]
    print(f"bundle: {out_zip}")
    print(f"  families: {sorted(families)}")
    print(f"  files: {len(entries)} (+ manifest, provenance)")
    print(f"  content id (MANIFEST sha256): {manifest_sha}")
    for name, f in sorted(families.items()):
        print(f"  {name} sub-id: {f['family_sub_id_sha256']}")
    print(f"  zip sha256: {sha256_file(out_zip)}")
    return 0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_grid(run_dir: Path):
    """(model, seed) pairs from the directory tree."""
    grid = []
    for model_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        seeds = sorted(p for p in model_dir.iterdir()
                       if p.is_dir() and p.name.startswith("seed_"))
        if seeds:
            grid.extend((model_dir.name, s) for s in seeds)
    return grid


def read_ledger(seed_dir: Path):
    header, attempts, bad = None, [], []
    path = seed_dir / "ledger.jsonl"
    if not path.exists():
        return None, [], [f"missing ledger: {path.parent.name}"]
    for i, line in enumerate(path.read_text().splitlines()):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            bad.append(f"unparseable ledger line {i} in {path}")
            continue
        if rec.get("type") == "run_header":
            header = rec
        elif rec.get("type") == "attempt":
            attempts.append(rec)
    return header, attempts, bad


def check_extract(path: Path):
    """Mechanical health only: gzip opens, JSON parses, frames > 0."""
    try:
        with gzip.open(path, "rt") as f:
            doc = json.load(f)
        frames = doc.get("n_frames")
        if not isinstance(frames, int) or frames <= 0:
            return False, f"n_frames={frames!r}"
        return True, frames
    except Exception as exc:  # corrupt gz / truncated json
        return False, f"{type(exc).__name__}: {exc}"


def inspect_cell(seed_dir: Path, result_json: Path, do_hash: bool):
    """One (model, seed, variant) cell -> facts dict + problem strings."""
    cell = {"variant": result_json.stem, "result_json": result_json.name}
    problems, severe = [], []
    try:
        doc = json.loads(result_json.read_text())
        records = (doc.get("_checkpoint") or {}).get("records") or []
        cell["entry_status"] = doc.get("entry_status")
        cell["statuses"] = [r.get("status") for r in records]
        cell["route_ids"] = [r.get("route_id") for r in records]
    except Exception as exc:
        severe.append(f"unparseable result json {result_json.name}: {exc}")
        cell["statuses"], cell["route_ids"] = [], []
    if do_hash:
        cell["result_sha256"] = sha256_file(result_json)
    log = result_json.with_suffix(".log")
    cell["log_bytes"] = log.stat().st_size if log.exists() else None
    if cell["log_bytes"] in (None, 0):
        problems.append("route log missing or empty")

    cell["extracts"] = []
    for route_id in cell["route_ids"]:
        if not route_id:
            continue
        ex = seed_dir / "trajectories" / f"{route_id}.json.gz"
        entry = {"route_id": route_id, "present": ex.exists()}
        if not ex.exists():
            problems.append(f"missing extract {route_id}")
        else:
            ok, detail = check_extract(ex)
            entry["ok"] = ok
            if ok:
                entry["n_frames"] = detail
            else:
                severe.append(f"corrupt extract {route_id}: {detail}")
            if do_hash:
                entry["sha256"] = sha256_file(ex)
            entry["bytes"] = ex.stat().st_size
        cell["extracts"].append(entry)
    return cell, problems, severe


def check_recordings(seed_dir: Path, header, cells, problems, in_zip=None):
    """The runner's header promises a recorder log per route; each is on disk and non-empty, or, when
    `in_zip` is given ({member path relative to the family subtree: size}, the raw zip's listing),
    listed there with a size above zero: the scoring unpack leaves the logs in the zip, verified in
    place (F67 item 6, 2026-10-08), and the bind reads the listing of the zip it binds."""
    if not header or not header.get("recordings"):
        return
    rec_dir = seed_dir / "recordings"
    for cell in cells:
        for route_id in cell["route_ids"]:
            if not route_id:
                continue
            rec = rec_dir / f"{route_id}.log"
            if rec.exists() and rec.stat().st_size > 0:
                continue
            if in_zip is not None and in_zip.get(f"{seed_dir.parent.name}/{seed_dir.name}/recordings/{route_id}.log", 0) > 0:
                continue
            problems.append(f"recordings promised but missing/empty: {route_id}"
                            + (" (not on disk, not in the zip)" if in_zip is not None else ""))


def build_manifest(run_dir: Path, models, seeds, do_hash, variants=None, fingerprints=None, in_zip=None):
    """`variants`, when given, is the DECLARED variant universe (a campaign
    declaration's grid): coverage is judged against it, and a variant found
    on disk that nobody declared is a problem. Without it the universe is
    discovered from the tree (legacy: it cannot catch an omitted variant).
    `fingerprints` ({model: sha256}, from campaign_meta.agent_identity): every
    run header of that model must carry it as agent_identity_sha256, or the
    cells ran under an identity the meta never recorded (severe). `in_zip`,
    when given, is the raw zip's listing under this family (member path
    relative to the subtree: size) for the members the scoring unpack left
    in the zip (the recorder logs, F67 item 6)."""
    grid = discover_grid(run_dir)
    if models:
        grid = [(m, s) for m, s in grid if m in models]
        missing_models = set(models) - {m for m, _ in grid}
        model_problems = [f"model dir missing entirely: {m}"
                          for m in sorted(missing_models)]
    else:
        model_problems = []
    if seeds is not None:
        want = {f"seed_{n}" for n in seeds}
        found_by_model = defaultdict(set)
        for m, s in grid:
            found_by_model[m].add(s.name)
        for m in sorted(found_by_model):
            for miss in sorted(want - found_by_model[m]):
                model_problems.append(f"{m}: {miss} missing entirely")
        grid = [(m, s) for m, s in grid if s.name in want]

    out = {"manifest_version": MANIFEST_VERSION,
           "generated": datetime.now(timezone.utc).isoformat(),
           "run_dir": str(run_dir), "cells": []}
    problems = list(model_problems)
    severe = []
    status_counts = Counter()
    headers = []
    variant_universe = set()
    xml_sha_by_variant = defaultdict(set)

    for model, seed_dir in grid:
        header, attempts, bad = read_ledger(seed_dir)
        severe.extend(bad)
        if header:
            headers.append({"model": model, "seed": seed_dir.name,
                            "upstream_pin_commit":
                                header.get("upstream_pin_commit"),
                            "agent_identity_sha256":
                                header.get("agent_identity_sha256"),
                            "host": header.get("host"),
                            "tm_seed": header.get("tm_seed"),
                            "conda_env": header.get("conda_env"),
                            "ts": header.get("ts")})
            if fingerprints and model in fingerprints and \
                    header.get("agent_identity_sha256") != fingerprints[model]:
                severe.append(f"IDENTITY VINTAGE: {model}/{seed_dir.name} ran under agent identity "
                              f"{header.get('agent_identity_sha256')}, campaign_meta records "
                              f"{fingerprints[model]}")
        result_jsons = sorted(seed_dir.glob("*.json"))
        seed_cells = []
        for rj in result_jsons:
            cell, cell_problems, cell_severe = inspect_cell(
                seed_dir, rj, do_hash)
            cell["model"], cell["seed"] = model, seed_dir.name
            seed_cells.append(cell)
            variant_universe.add(cell["variant"])
            for st in cell["statuses"]:
                status_counts[str(st)] += 1
            problems.extend(f"{model}/{seed_dir.name}: {p}"
                            for p in cell_problems)
            severe.extend(f"{model}/{seed_dir.name}: {p}"
                          for p in cell_severe)
        rec_problems = []
        check_recordings(seed_dir, header, seed_cells, rec_problems, in_zip)
        problems.extend(f"{model}/{seed_dir.name}: {p}"
                        for p in rec_problems)
        # route-XML vintage: same variant must hash identically in
        # every cell of the campaign
        for att in attempts:
            sha = att.get("route_xml_sha256")
            var = (att.get("route_id") or att.get("route")
                   or att.get("scenario"))
            if sha and var:
                xml_sha_by_variant[var].add(sha)
            elif sha:
                severe.append(f"{model}/{seed_dir.name}: attempt with "
                              "route_xml_sha256 but no route identity "
                              "field - cannot vintage-check it")
        out["cells"].extend(seed_cells)

    # coverage: every variant of the universe expected in every (model,
    # seed). The universe is the DECLARED grid when one was passed, else
    # (legacy) whatever the tree holds.
    cells_by_key = {(c["model"], c["seed"], c["variant"])
                    for c in out["cells"]}
    if variants is not None:
        undeclared = sorted(variant_universe - set(variants))
        problems.extend(f"undeclared variant on disk: {var}" for var in undeclared)
        variant_universe = set(variants)
    missing_cells = []
    for model, seed_dir in grid:
        for var in sorted(variant_universe):
            if (model, seed_dir.name, var) not in cells_by_key:
                missing_cells.append(
                    {"model": model, "seed": seed_dir.name, "variant": var})
                problems.append(
                    f"missing cell: {model}/{seed_dir.name}/{var}")

    commits = {h["upstream_pin_commit"] for h in headers}
    if len(commits) > 1:
        severe.append(f"MIXED VINTAGE: upstream_pin_commit values "
                      f"{sorted(str(c) for c in commits)}")
    for var, shas in sorted(xml_sha_by_variant.items()):
        if len(shas) > 1:
            severe.append(f"route-XML sha divergence for {var}: "
                          f"{len(shas)} distinct hashes across cells")

    out.update({
        "coverage": {"models_x_seeds": len(grid),
                     "variants": sorted(variant_universe),
                     "expected_cells": len(grid) * len(variant_universe),
                     "found_cells": len(out["cells"]),
                     "missing_cells": missing_cells},
        "status_counts_verbatim": dict(
            sorted(status_counts.items(), key=lambda kv: -kv[1])),
        "provenance": {"run_headers": headers,
                       "upstream_pin_commits":
                           sorted(str(c) for c in commits)},
        "problems": problems,
        "severe": severe,
    })
    return out


def render_summary(m):
    lines = [f"campaign manifest v{m['manifest_version']} - {m['run_dir']}",
             f"generated {m['generated']}", ""]
    cells = {(c["model"], c["seed"], c["variant"]): c for c in m["cells"]}
    pairs = sorted({(c["model"], c["seed"]) for c in m["cells"]})
    variants = m["coverage"]["variants"]

    def glyph(c):
        if c is None:
            return "X"
        if not c["statuses"]:
            return "!"
        if any(e.get("present") and not e.get("ok", True)
               for e in c["extracts"]):
            return "!"
        if any(not e.get("present") for e in c["extracts"]) \
                or not c["extracts"]:
            return "t"
        return "."

    col_hdrs = [f"{mdl[:10]}:{sd.removeprefix('seed_')}"
                for mdl, sd in pairs]
    width = max((len(h) for h in col_hdrs), default=4) + 1
    lines.append(" " * 42 + "".join(h.rjust(width) for h in col_hdrs))
    for var in variants:
        row = "".join(
            glyph(cells.get((mdl, sd, var))).rjust(width)
            for mdl, sd in pairs)
        lines.append(var[-40:].rjust(42) + row)
    lines.append("")
    lines.append("glyphs: . ok | t extract missing | ! integrity | X cell missing")
    lines.append("")
    lines.append("statuses (VERBATIM, uninterpreted):")
    for status, n in m["status_counts_verbatim"].items():
        lines.append(f"  {n:>4}  {status}")
    lines.append("")
    prov = m["provenance"]
    lines.append(f"upstream_pin_commit: {prov['upstream_pin_commits']}")
    hosts = sorted({h.get("host") for h in prov["run_headers"]})
    lines.append(f"hosts: {hosts}")
    cov = m["coverage"]
    lines.append(f"coverage: {cov['found_cells']}/{cov['expected_cells']} "
                 f"cells, {len(cov['missing_cells'])} missing")
    if m["problems"]:
        lines.append("")
        lines.append(f"PROBLEMS ({len(m['problems'])}):")
        lines.extend(f"  - {p}" for p in m["problems"])
    if m["severe"]:
        lines.append("")
        lines.append(f"SEVERE ({len(m['severe'])}):")
        lines.extend(f"  - {p}" for p in m["severe"])
    if not m["problems"] and not m["severe"]:
        lines.append("COMPLETE - no problems found")
    return "\n".join(lines) + "\n"


def tree_expectation(run_dir: Path, args):
    """What to verify a family-run tree against: the declaration the runner
    wrote there (models, repetitions, variants) and the agent fingerprints
    its meta recorded. CLI hints are accepted only when they agree with the
    declaration; --campaign pins the tree's declaration to the canon file of
    the same family, byte for byte. Legacy trees without a declaration keep
    the hints (or discovery)."""
    declaration, meta = read_tree_expectation(run_dir)
    models, seeds, variants = args.models, args.seeds, None
    if declaration is not None:
        if models and set(models) != set(declaration.models):
            raise ValueError(f"{run_dir.name}: --models {sorted(models)} disagree with the tree's "
                             f"declaration {declaration.models}")
        if seeds is not None and set(seeds) != set(declaration.repetitions):
            raise ValueError(f"{run_dir.name}: --seeds {sorted(seeds)} disagree with the tree's "
                             f"declaration {declaration.repetitions}")
        models, seeds, variants = declaration.models, declaration.repetitions, declaration.variants
        if args.campaign:
            canon = {d.family_id: d for d in (load_declaration(c) for c in args.campaign)}
            if declaration.family_id not in canon:
                raise ValueError(f"{run_dir.name}: no --campaign file declares family "
                                 f"{declaration.family_id!r} (given: {sorted(canon)})")
            if canon[declaration.family_id].sha256 != declaration.sha256:
                raise ValueError(f"{run_dir.name}: the tree's declaration ({declaration.sha256[:12]}) is not "
                                 f"the canon file {canon[declaration.family_id].path.name} "
                                 f"({canon[declaration.family_id].sha256[:12]}) - a bundle certifies against "
                                 "the declaration the runner wrote; canon holds a copy of it or refuses")
    elif args.campaign:
        raise ValueError(f"{run_dir.name}: --campaign given but the tree holds no campaign_declaration.json")
    return models, seeds, variants, agent_fingerprints(meta)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", type=Path, default=None,
                    help="one family-run tree: <model>/seed_<n>/...")
    ap.add_argument("--campaign-dir", type=Path, default=None,
                    help="a campaign dir holding family-run subtrees "
                         "(<campaign>/<family>/<model>/seed_<n>/...); "
                         "verifies every family, bundles ONE zip")
    ap.add_argument("--models", nargs="+", default=None,
                    help="expected models (default: discovered)")
    ap.add_argument("--seeds", nargs="+", type=int, default=None,
                    help="expected seeds (default: discovered)")
    ap.add_argument("--no-hash", action="store_true",
                    help="skip sha256 (faster)")
    ap.add_argument("--out", type=Path, default=None,
                    help="manifest JSON path (default: "
                         "<run-dir>/campaign_manifest.json)")
    ap.add_argument("--summary", type=Path, default=None,
                    help="summary txt path (default: "
                         "<run-dir>/campaign_manifest_summary.txt)")
    ap.add_argument("--produce-bundle", type=Path, default=None,
                    help="freeze the VERIFIED campaign into this zip "
                         "(refused unless verification exits 0)")
    ap.add_argument("--allow-unscored", action="store_true",
                    help="bundle even without scoring outputs beside the "
                         "tree (recorded in bundle_provenance.json)")
    ap.add_argument("--snapshot-dir", type=Path, default=None,
                    help="optional evidence dir to embed as "
                         "family_snapshot/ in the bundle")
    ap.add_argument("--campaign", action="append", type=Path, default=None,
                    help="canon declaration(s), one per family: the tree's "
                         "campaign_declaration.json must be byte-identical "
                         "to the canon file of its family (repeatable); with "
                         "--produce-derived, the folder's families and the "
                         "declarations its snapshot carries")
    ap.add_argument("--produce-derived", type=Path, default=None,
                    help="F30: freeze a scoring run's derived folder "
                         "(<dir>/<campaign-id>/<family>/...) into this zip, "
                         "keyed by the raw bundle's content id; needs --raw "
                         "and --derived-dir, no tree")
    ap.add_argument("--raw", type=Path, default=None,
                    help="the raw campaign bundle the derived folder was computed from; with --run-dir or "
                         "--campaign-dir, the zip whose listing stands in for the members the scoring unpack "
                         "left in place (the recorder logs the completeness check promises; F67 item 6)")
    ap.add_argument("--derived-dir", type=Path, default=None,
                    help="the derived folder, named for the campaign (score_campaign.py --out/<campaign-id>)")
    ap.add_argument("--source", action="append", type=Path, default=None,
                    help="a file to snapshot into the derived bundle beside the one source list "
                         "(repeatable; e.g. the instruments a flow scoring ran against); refused when the "
                         "folder carries a staged snapshot already")
    args = ap.parse_args(argv)

    if args.produce_derived is not None:
        if args.raw is None or args.derived_dir is None or args.run_dir or args.campaign_dir:
            print("--produce-derived takes --raw and --derived-dir and no tree", file=sys.stderr)
            return 2
        try:
            declarations = [load_declaration(p) for p in (args.campaign or [])]
        except ValueError as exc:
            print(f"--campaign: {exc}", file=sys.stderr)
            return 2
        return produce_derived(args.produce_derived, args.raw, args.derived_dir.resolve(), args.source,
                               declarations)
    if (args.run_dir is None) == (args.campaign_dir is None):
        print("exactly one of --run-dir / --campaign-dir is required",
              file=sys.stderr)
        return 2

    if args.campaign_dir is not None:
        campaign_dir = args.campaign_dir.resolve()
        if not campaign_dir.is_dir():
            print(f"not a directory: {campaign_dir}", file=sys.stderr)
            return 2
        subtrees = discover_family_subtrees(campaign_dir)
        if not subtrees:
            print(f"REFUSED: no family-run subtrees "
                  f"(<family>/<model>/seed_<n>/...) under {campaign_dir}",
                  file=sys.stderr)
            return 2
        reports, worst = {}, 0
        for sub in subtrees:
            try:
                models, seeds, variants, fingerprints = tree_expectation(sub, args)
            except ValueError as exc:
                print(f"REFUSED: {exc}", file=sys.stderr)
                return 2
            m = build_manifest(sub, models, seeds, do_hash=not args.no_hash,
                               variants=variants, fingerprints=fingerprints,
                               in_zip=cb.member_sizes(args.raw, f"{campaign_dir.name}/{sub.name}/") if args.raw else None)
            (sub / "campaign_manifest.json").write_text(
                json.dumps(m, indent=1, sort_keys=True) + "\n")
            (sub / "campaign_manifest_summary.txt").write_text(
                render_summary(m))
            code = 2 if m["severe"] else (1 if m["problems"] else 0)
            print(f"{sub.name}: {m['coverage']['found_cells']}"
                  f"/{m['coverage']['expected_cells']} cells, "
                  f"{len(m['problems'])} problems, "
                  f"{len(m['severe'])} severe -> exit {code}")
            reports[sub.name] = m
            worst = max(worst, code)
        if args.produce_bundle is not None:
            if worst != 0:
                print("BUNDLE REFUSED: verification did not pass for "
                      "every family - a frozen campaign must mean every "
                      "family proven complete")
                return worst
            return produce_campaign_bundle(args.produce_bundle,
                                           campaign_dir, reports,
                                           args.allow_unscored)
        return worst

    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        print(f"not a directory: {run_dir}", file=sys.stderr)
        return 2
    try:
        models, seeds, variants, fingerprints = tree_expectation(run_dir, args)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    manifest = build_manifest(run_dir, models, seeds, do_hash=not args.no_hash,
                              variants=variants, fingerprints=fingerprints,
                              in_zip=cb.member_sizes(args.raw, f"{run_dir.parent.name}/{run_dir.name}/") if args.raw else None)
    out = args.out or run_dir / "campaign_manifest.json"
    summary_path = args.summary or run_dir / "campaign_manifest_summary.txt"
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    summary = render_summary(manifest)
    summary_path.write_text(summary)
    print(summary)
    print(f"manifest: {out}", file=sys.stderr)
    if manifest["severe"]:
        code = 2
    elif manifest["problems"]:
        code = 1
    else:
        code = 0
    if args.produce_bundle is not None:
        if code != 0:
            print("BUNDLE REFUSED: verification did not pass - a frozen "
                  "bundle must mean a proven-complete campaign")
            return code
        unfit_reason, _ = bundle_fitness(run_dir)
        if unfit_reason:
            print(f"BUNDLE REFUSED: {unfit_reason}")
            return 2
        return produce_bundle(args.produce_bundle, run_dir,
                              args.snapshot_dir, manifest,
                              args.allow_unscored)
    return code


if __name__ == "__main__":
    sys.exit(main())
