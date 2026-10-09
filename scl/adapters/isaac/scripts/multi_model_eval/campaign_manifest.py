#!/usr/bin/env python3
"""Mechanical completeness manifest for an Isaac campaign run tree
(mirror of adapters/fail2drive/scripts/multi_model_eval/campaign_manifest.py).

Completeness, not judgment: verifies that every expected
(variant x brain x rep) cell derived from the family manifest produced a
ledger with a terminal record plus its witness, collects statuses
VERBATIM (never mapped - a status this tool has never seen must survive
into the report unchanged), and pins provenance: per-cell ledger headers
must bind the family manifest's stage sha256 and certificate core hash,
and all brain cells must share one checkpoint sha (mixed-vintage check).
It contains no outcome vocabulary.

Exit codes: 0 complete; 1 missing cells/artifacts; 2 integrity errors
(hash drift, assignment mismatch, mixed checkpoint vintage, unparseable
ledgers).

--produce-bundle PATH freezes a VERIFIED campaign into a citable zip
(S15, Milan go 2026-09-02): refused unless verification exits 0 and
scoring outputs are co-located (--allow-unscored overrides, recorded).
The bundle holds the full run tree, a family evidence snapshot
(manifest, certs, countersignature, stages - the scan stays out,
referenced by sha), MANIFEST.sha256 over every data file, and
bundle_provenance.json. Zip entries are sorted with fixed timestamps:
re-bundling an unchanged tree the same day is byte-identical, and the
MANIFEST sha is the date-invariant content id to cite.

Stdlib-only; runs under any env, box or central - re-running it locally
after the fetch is the transport-integrity receipt.
"""
import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path as _P

_REPO_ROOT = _P(__file__).resolve().parents[4]   # adapters/isaac/scripts/multi_model_eval -> repo
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from scl_core import campaign_records  # noqa: E402  (stdlib-only core: declaration + meta, one shape on both backends)
from scl_core import campaign_bundles as cb  # noqa: E402  (stdlib-only core: the two-bundle discipline, F30)
from scl_core.campaign_declaration import load_declaration  # noqa: E402
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brain_models import cell_dir_name, cell_tag  # noqa: E402

TERMINAL = ("expert_run_footer", "brain_run_footer")

# fixed zip entry timestamp: bundle bytes depend only on contents
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# F30 (2026-09-14): a raw bundle holds the runner's evidence and nothing
# computed from it. What the Isaac runner writes at the family-run top level
# beside the declared cells (<variant>/<cell>/): the two records, the done
# marker, one server log per brain, and the verifier's receipt.
CAMPAIGN_FILES = frozenset({"campaign_declaration.json", "campaign_meta.json", "CAMPAIGN_DONE",
                            "campaign_manifest.json"})
DERIVED_SOURCES = ("score_runs.py", "derive_go_clean.py", "findings_v2.py", "brain_models.py")
SOURCE_MANIFEST = cb.SOURCE_MANIFEST


def derived_source_files() -> list:
    """Every file a derived folder's content depends on, resolved in this tree: the producers, the
    driver, and every in-repo module they import transitively (scl_core.campaign_bundles.source_closure,
    the same keying as the CARLA counterpart; F44, 2026-09-19)."""
    here = Path(__file__).resolve().parent
    seeds = {here / name for name in DERIVED_SOURCES}
    seeds.add(_REPO_ROOT / "scripts/score_campaign.py")
    return cb.source_closure(seeds, _REPO_ROOT)


def lay_source_manifest(folder: Path, declarations=(), extra_files=()) -> str:
    """Write the source manifest of a derived folder (`<folder>/source_snapshot/source_manifest.json`,
    scl_core.campaign_bundles.lay_source_manifest: every listed file by hash, the git identity of
    this checkout, the runtime; no copy travels since 2026-09-19, one shape on both backends).
    Listed: the one source list and `extra_files`. The declarations and the family manifests are
    NOT sources (principle 13, 2026-09-21): the writer records them as raw members by hash. Returns the manifest file's sha256, the snapshot id the derived bundle records."""
    files = set(derived_source_files())
    files.update(Path(p).resolve() for p in extra_files)
    # the declarations and the family manifests are NOT sources (principle 13, 2026-09-21): the
    # interpretation took them from the raw bundle, and the derived writer records them as raw
    # members by hash (cb.evidence_inputs); `declarations` is kept for the callers' shape
    del declarations
    return cb.lay_source_manifest(folder, files, _REPO_ROOT)


def raw_entries(family_dir: Path, arc_prefix: str, declaration) -> list:
    """The raw members of a DECLARED family-run tree: the declared
    (variant, cell) directories, the runner's campaign files and the
    family snapshot; anything else refuses (RawTreeViolation names it)."""
    brains = [m for m in declaration.models if m != declaration.reference_model]
    cell_names = {cell_dir_name(b, r) for b in brains for r in declaration.repetitions}
    if declaration.expert:
        cell_names.add("expert")
    cells = {v: set(cell_names) for v in declaration.variants}
    files = set(CAMPAIGN_FILES) | {f"{b}_server.log" for b in brains}
    return cb.raw_family_entries(family_dir, arc_prefix, files, cells, extra_dirs=("family_snapshot",))


def produce_derived(out_zip: Path, raw_zip: Path, folder: Path, sources, declarations=()) -> int:
    """Freeze the derived folder a scoring run left for one raw bundle
    (F30): keyed by the raw content id; refusals name the contract. The ONE
    writer for every campaign (F36, one shape on both backends): a folder
    that already carries a source manifest is frozen as it stands; otherwise
    the manifest is laid here from the one list plus `sources` and the
    declarations passed. With declarations, the folder's families must be
    the declared ones; the writer records each declaration and the family as run as raw
    members by hash (principle 13). The git
    identity is recorded, not gated here: `campaign publish` refuses a
    bundle frozen from a tree whose listed sources were modified."""
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
                  "manifest laid at the freeze, never to a staged one")
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
                                       writer_sha256=_sha256(Path(__file__).resolve()), declarations=declarations)
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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def produce_bundle(out_zip: Path, run_dir: Path, family: Path,
                   report: dict, allow_unscored: bool) -> int:
    """Freeze a VERIFIED campaign tree into a citable zip. Caller must
    have established exit 0 (complete, zero integrity) - this function
    only adds the scoring gate. Layout: <campaign_id>/<run tree...> +
    <campaign_id>/family_snapshot/<family evidence>. Writes
    MANIFEST.sha256 (every data file) and bundle_provenance.json; the
    MANIFEST sha is the date-invariant content id."""
    campaign_id = run_dir.name

    def _has_content(p: Path) -> bool:
        # score_runs.py --out writes a single JSON document; a directory
        # of outputs is equally acceptable evidence of scoring
        return (p.is_file() and p.stat().st_size > 0) or \
               (p.is_dir() and any(p.iterdir()))

    unfit_reason, _ = campaign_records.bundle_fitness(run_dir)
    if unfit_reason:
        print(f"BUNDLE REFUSED: {unfit_reason}")
        return 2
    if campaign_records.read_tree_expectation(run_dir)[0] is not None:
        print("BUNDLE REFUSED: a declared tree freezes at campaign level "
              "(--campaign-dir <results-root>/<campaign-id>, one raw zip per "
              "campaign, F30); --run-dir bundling serves pre-F18 trees")
        return 2
    scoring_dirs = [d for d in ("scoring_out", "scoring")
                    if _has_content(run_dir / d)]
    if not scoring_dirs and not allow_unscored:
        print("BUNDLE REFUSED: no scoring outputs beside the tree "
              "(scoring_out/); score first, or pass --allow-unscored "
              "to freeze an unscored campaign (recorded in provenance)")
        return 2

    entries = []  # (arcname, source_path)
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.resolve() == out_zip.resolve():
            continue
        entries.append((f"{campaign_id}/{p.relative_to(run_dir)}", p))
    for p in sorted(family.glob("*")):
        # the family's committed evidence, minus derived debug caches
        if not p.is_file() or p.name.endswith(".gz"):
            continue
        entries.append((f"{campaign_id}/family_snapshot/{p.name}", p))

    provenance = {
        "bundle_kind": "campaign_bundle/v0.1",
        "campaign_id": campaign_id,
        "family_id": report["family_id"],
        "produced_date": _date.today().isoformat(),
        "verification": {
            "expected_cells": report["expected_cells"],
            "terminal_cells": report["terminal_cells"],
            "missing": len(report["missing"]),
            "integrity": len(report["integrity"]),
        },
        "scoring_included": bool(scoring_dirs),
        "manifest_sha256": None,
        "bundler_sha256": _sha256(Path(__file__).resolve()),
        "source_git_describe": os.environ.get("SCL_GIT_DESCRIBE"),
        "note": ("zip entries sorted, timestamps fixed: re-bundling an "
                 "unchanged tree reproduces the MANIFEST sha exactly; "
                 "cite manifest_sha256 as the content id"),
    }
    provenance = cb.write_zip(out_zip, campaign_id, entries, provenance)
    manifest_sha = provenance["manifest_sha256"]
    print(f"bundle: {out_zip}")
    print(f"  files: {len(entries)} (+ manifest, provenance); "
          f"scoring_included={bool(scoring_dirs)}")
    print(f"  content id (MANIFEST sha256): {manifest_sha}")
    print(f"  zip sha256: {_sha256(out_zip)}")
    return 0


def read_ledger(path: Path):
    """(header, status_verbatim) - either may be None."""
    header, status = None, None
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return header, "UNPARSEABLE_LEDGER_LINE"
        t = rec.get("type", "")
        if t.endswith("_run_header"):
            header = rec
        elif t in TERMINAL:
            status = rec.get("outcome", "footer_without_outcome")
        elif t == "refusal":
            status = f"refusal: {rec.get('reason')}"
    return header, status


def verify_family(run_dir: Path, family: Path):
    """The per-family completeness/integrity checks (unchanged from the
    original inline main). Writes campaign_manifest.json into run_dir;
    returns (report, exit_code)."""
    fam = json.loads((family / "family_manifest.json").read_text())

    missing, integrity, cells = [], [], []
    meta_path = run_dir / "campaign_meta.json"
    meta_grid = None
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else None
    if meta is not None:
        meta_grid = meta.get("declared_grid")
    declaration, _ = campaign_records.read_tree_expectation(run_dir)
    fingerprints = campaign_records.agent_fingerprints(meta)
    if declaration is not None:
        # F18: the EXPECTATION is the declaration the runner wrote at
        # invocation (campaign_declaration/v0.2); the expert is one of the
        # models, named by reference_model; repetitions are labels
        brains = [m for m in declaration.models if m != declaration.reference_model]
        reps = list(declaration.repetitions)
        expect_expert = declaration.expert
        by_stem = {v["stem"]: v for v in fam["variants"]}
        unknown = [s for s in declaration.variants if s not in by_stem]
        if unknown:
            integrity.append(f"declared variants absent from the family "
                             f"manifest: {unknown}")
        variants = [by_stem[s] for s in declaration.variants if s in by_stem]
        if declaration.manifest_sha256 != _sha256(family / "family_manifest.json"):
            integrity.append("the declaration pins another family manifest "
                             "than the one beside the tree - declaration and "
                             "evidence disagree")
    elif meta_grid is not None:
        # F2 contract: the grid is DECLARED at invocation and recorded
        # in campaign_meta.json - completeness means every declared
        # cell. Family manifests are evidence (stages, certs, hashes),
        # never grid policy; the legacy branch below reads the pre-F2
        # manifests that still baked the policy in.
        reps = list(range(meta_grid["reps_per_brain"]))
        brains = list(meta_grid["brains"])
        expect_expert = bool(meta_grid.get("expert", True))
        by_stem = {v["stem"]: v for v in fam["variants"]}
        unknown = [s for s in meta_grid["variants"] if s not in by_stem]
        if unknown:
            integrity.append(f"declared variants absent from the family "
                             f"manifest: {unknown}")
        variants = [by_stem[s] for s in meta_grid["variants"]
                    if s in by_stem]
        declared_fam_sha = meta.get("family_manifest_sha256")
        actual_fam_sha = _sha256(family / "family_manifest.json")
        if declared_fam_sha and declared_fam_sha != actual_fam_sha:
            integrity.append("campaign_meta family_manifest_sha256 does "
                             "not match the family manifest beside the "
                             "tree - declaration and evidence disagree")
    elif "n_reps_per_brain" in fam:
        reps = list(range(fam["n_reps_per_brain"]))
        brains = fam["brains"]
        expect_expert = True
        variants = fam["variants"]
    else:
        print("REFUSED: no grid declaration - the run tree has no "
              "campaign_meta.json with a declared_grid "
              "(campaign_meta/v0.1) and the family manifest carries no "
              "legacy policy fields (n_reps_per_brain, brains); exactly "
              "one of the two shapes must declare the expected grid")
        report = {"family_id": fam.get("family_id"),
                  "refused": "no grid declaration (campaign_meta "
                             "declared_grid or legacy family-manifest "
                             "policy fields)"}
        return report, 2
    ckpt_shas = set()
    # F21: the simulator vintage the meta pins (campaign_meta/v0.3); every
    # header must have run under it, and the set across cells is reported
    # (CARLA: upstream_pin_commit). Legacy trees carry no pin and stay
    # byte-identical.
    sim_pin = ((meta or {}).get("simulator") or {}).get("pin")
    sim_versions = set()
    for v in variants:
        expected = [("expert", "expert")] if expect_expert else []
        for brain in brains:
            for rep in reps:
                expected.append((cell_dir_name(brain, rep),
                                 cell_tag(brain, rep)))
        for dname, tag in expected:
            cell = run_dir / v["stem"] / dname
            cid = f"{v['stem']}/{dname}"
            ledger = cell / f"{v['stem']}_{tag}.jsonl"
            row = {"cell": cid, "status": None}
            cells.append(row)
            if not ledger.is_file():
                missing.append(f"{cid}: no ledger")
                continue
            header, status = read_ledger(ledger)
            row["status"] = status
            if status is None:
                missing.append(f"{cid}: ledger has no terminal record")
            if status == "UNPARSEABLE_LEDGER_LINE":
                integrity.append(f"{cid}: unparseable ledger")
            if header is None:
                integrity.append(f"{cid}: ledger has no header")
                continue
            if header.get("stage_sha256") != v["stage_sha256"]:
                integrity.append(f"{cid}: header stage sha != family manifest")
            if header.get("cert_core_sha256") != v["cert_core_sha256"]:
                integrity.append(f"{cid}: header cert core != family manifest")
            if header.get("assignment") != v["assignment"]:
                integrity.append(f"{cid}: header assignment != family manifest")
            if header.get("witness") and not (cell / "witness.mp4").is_file():
                missing.append(f"{cid}: header claims witness, file absent")
            bm = header.get("brain_manifest")
            if bm:
                ckpt_shas.add(bm.get("ckpt_sha256"))
            agent = "expert" if dname == "expert" else next(
                (b for b in brains if dname.startswith(cell_dir_name(b, 0).split("_rep")[0] + "_rep")), None)
            if fingerprints and agent in fingerprints and \
                    header.get("agent_identity_sha256") != fingerprints[agent]:
                integrity.append(f"IDENTITY VINTAGE: {cid} ran under agent identity "
                                 f"{header.get('agent_identity_sha256')}, campaign_meta "
                                 f"records {fingerprints[agent]}")
            if sim_pin:
                sim_versions.add(header.get("isaacsim_version"))
                if header.get("isaacsim_version") != sim_pin:
                    integrity.append(f"SIMULATOR VINTAGE: {cid} ran under isaacsim "
                                     f"{header.get('isaacsim_version')}, campaign_meta "
                                     f"pins {sim_pin}")
    if len(ckpt_shas) > 1:
        integrity.append(f"mixed checkpoint vintage: {sorted(ckpt_shas)}")
    if len(sim_versions) > 1:
        integrity.append(f"MIXED VINTAGE: isaacsim_version values "
                         f"{sorted(str(s) for s in sim_versions)}")

    report = {
        "family_id": fam["family_id"],
        "expected_cells": len(cells),
        "terminal_cells": sum(1 for c in cells if c["status"] is not None),
        "statuses_verbatim": {c["cell"]: c["status"] for c in cells},
        "checkpoint_shas": sorted(s for s in ckpt_shas if s),
        "missing": missing,
        "integrity": integrity,
    }
    if declaration is not None:
        report["grid_source"] = "campaign_declaration"
        report["checkpoint_shas"] = sorted(
            block.get("weights", {}).get("sha256") for block in (meta or {}).get("agent_identity", {}).values()
            if block.get("weights", {}).get("sha256"))
        report["isaacsim_versions"] = sorted(str(s) for s in sim_versions)
    elif meta_grid is not None:
        # recorded only for declared-grid trees: legacy reports stay
        # byte-identical (frozen campaign content ids regenerate exactly)
        report["grid_source"] = "campaign_meta.declared_grid"
    (run_dir / "campaign_manifest.json").write_text(
        json.dumps(report, indent=1) + "\n")
    print(f"{report['terminal_cells']}/{report['expected_cells']} cells "
          f"terminal; {len(missing)} missing, {len(integrity)} integrity")
    for line in (missing + integrity)[:20]:
        print(f"- {line}")
    if integrity:
        code = 2
    else:
        code = 1 if missing else 0
    return report, code


def discover_family_subtrees(campaign_dir: Path):
    """Child dirs carrying their own family evidence
    (<family>/family_snapshot/family_manifest.json)."""
    return [d for d in sorted(p for p in campaign_dir.iterdir()
                              if p.is_dir())
            if (d / "family_snapshot/family_manifest.json").is_file()]


def produce_campaign_bundle(out_zip: Path, campaign_dir: Path,
                            reports: dict, allow_unscored: bool) -> int:
    """Freeze a VERIFIED multi-family campaign into one citable zip
    (S19 layout parity: <campaign>/<family>/<run tree> on both
    backends). Caller must have established exit 0 for EVERY family;
    this adds only the per-family scoring gate. bundle_provenance
    carries a families block with per-family sub-ids."""
    campaign_id = campaign_dir.name

    def _has_content(p: Path) -> bool:
        return (p.is_file() and p.stat().st_size > 0) or \
               (p.is_dir() and any(p.iterdir()))

    unfit, instrument_record, declarations = [], {}, {}
    for name in reports:
        unfit_reason, instrument_record[name] = campaign_records.bundle_fitness(campaign_dir / name)
        if unfit_reason:
            unfit.append(f"{name}: {unfit_reason}")
        declarations[name] = campaign_records.read_tree_expectation(campaign_dir / name)[0]
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
        # F30: RAW ONLY - the declared cells, the runner's campaign files
        # and the family snapshot; derived outputs left beside a tree
        # refuse (they freeze separately: --produce-derived). No scoring
        # gate: the evidence id must not depend on any interpretation.
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
                               for d in ("scoring_out", "scoring"))
                    and not _has_content(campaign_dir / name /
                                         "scored_runs.json")]
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
            "family_id": rep["family_id"],
            "family_sub_id_sha256": sub_ids[name],
            "verification": {
                "expected_cells": rep["expected_cells"],
                "terminal_cells": rep["terminal_cells"],
                "missing": len(rep["missing"]),
                "integrity": len(rep["integrity"]),
            },
        }
        if not declared:
            families[name]["scoring_included"] = name not in unscored
        families[name]["instrument_record"] = instrument_record[name]

    provenance = {
        "bundle_kind": bundle_kind,
        "campaign_id": campaign_id,
        "produced_date": _date.today().isoformat(),
        "families": families,
        "manifest_sha256": None,
        "bundler_sha256": _sha256(Path(__file__).resolve()),
        "source_git_describe": os.environ.get("SCL_GIT_DESCRIBE"),
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
    print(f"  zip sha256: {_sha256(out_zip)}")
    return 0


def canon_mismatch(run_dir, canon_paths):
    """--campaign pins the tree's declaration to the canon file of the same
    family, byte for byte; None when nothing was asked or all agree."""
    if not canon_paths:
        return None
    declaration, _ = campaign_records.read_tree_expectation(run_dir)
    if declaration is None:
        return f"{run_dir.name}: --campaign given but the tree holds no campaign_declaration.json"
    canon = {d.family_id: d for d in (load_declaration(c) for c in canon_paths)}
    if declaration.family_id not in canon:
        return (f"{run_dir.name}: no --campaign file declares family {declaration.family_id!r} "
                f"(given: {sorted(canon)})")
    if canon[declaration.family_id].sha256 != declaration.sha256:
        return (f"{run_dir.name}: the tree's declaration ({declaration.sha256[:12]}) is not the canon "
                f"file {canon[declaration.family_id].path.name} ({canon[declaration.family_id].sha256[:12]}) "
                "- a bundle certifies against the declaration the runner wrote; canon holds a copy of it or refuses")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--family", default=None,
                    help="family dir holding family_manifest.json "
                         "(required with --run-dir)")
    ap.add_argument("--campaign-dir", type=Path, default=None,
                    help="a campaign dir holding family subtrees "
                         "(<campaign>/<family>/... each with its own "
                         "family_snapshot/); verifies every family, "
                         "bundles ONE zip")
    ap.add_argument("--produce-bundle", type=Path, default=None,
                    help="freeze the VERIFIED campaign into this zip "
                         "(refused unless verification exits 0)")
    ap.add_argument("--allow-unscored", action="store_true",
                    help="bundle even without scoring outputs beside the "
                         "tree (recorded in bundle_provenance.json)")
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
                    help="the raw campaign bundle the derived folder was computed from")
    ap.add_argument("--derived-dir", type=Path, default=None,
                    help="the derived folder, named for the campaign (score_campaign.py --out/<campaign-id>)")
    ap.add_argument("--source", action="append", type=Path, default=None,
                    help="a file to snapshot into the derived bundle beside the one source list "
                         "(repeatable); refused when the folder carries a staged snapshot already")
    args = ap.parse_args()

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
            print(f"REFUSED: no family subtrees (each needs "
                  f"family_snapshot/family_manifest.json) under "
                  f"{campaign_dir}", file=sys.stderr)
            return 2
        reports, worst = {}, 0
        for sub in subtrees:
            print(f"--- {sub.name}")
            problem = canon_mismatch(sub, args.campaign)
            if problem:
                print(f"REFUSED: {problem}", file=sys.stderr)
                return 2
            report, code = verify_family(sub, sub / "family_snapshot")
            reports[sub.name] = report
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

    if args.family is None:
        print("--family is required with --run-dir", file=sys.stderr)
        return 2
    run_dir, family = Path(args.run_dir), Path(args.family)
    problem = canon_mismatch(run_dir, args.campaign)
    if problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 2
    report, code = verify_family(run_dir, family)
    if args.produce_bundle is not None:
        if code != 0:
            print("BUNDLE REFUSED: verification did not pass - a frozen "
                  "bundle must mean a proven-complete campaign")
            return code
        return produce_bundle(args.produce_bundle, run_dir, family,
                              report, args.allow_unscored)
    return code


if __name__ == "__main__":
    sys.exit(main())
