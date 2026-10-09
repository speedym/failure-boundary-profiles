#!/usr/bin/env python3
"""Certify declared flow campaigns against their raw and derived bundles: stage, then bind.

--campaign (repeatable, required), --zone and --map-excerpt are passed in,
never named here; pass EVERY family of a campaign, whichever chain scored
it (the plan says which: campaign_manifest.scoring_chain). A campaign is
two bundles (F30): the RAW bundle <campaign>.zip in --bundles-dir, the
evidence, never rewritten here; and the DERIVED bundle
<campaign>_derived.zip, one interpretation keyed by the raw content id.
Since F36 (2026-09-15) this driver writes NO bundle: the one writer is
campaign_manifest.py --produce-derived, whichever chains scored the
campaign, and a threading family and a flow family of one campaign share
one derived zip. Two modes:

  --stage --scores-root <the scoring driver's --out>
      For every declared FLOW family: bind the derived members the driver
      left (<campaign>/<family>/flow_stats, findings, audit rows) to the
      summaries that pin them, write the family's audit slice, the whole
      remeasurement and map-check summaries into
      <campaign>/<family>/audit/, check every scored input against the raw
      tree and the raw bundle's MANIFEST; then lay ONE source manifest for
      the campaign (the one list, the instrument files, every declaration
      passed, each by hash, plus the git commit; no copies travel since
      2026-09-19) and REPRODUCE.md beside the families. Nothing is certified:
      the folder is now the derived bundle's content, and the command that
      freezes it is printed.

  (default) --bundles-dir <dir holding <campaign>.zip and <campaign>_derived.zip>
      Read both bundles, verify every member, the raw id the derived one
      keys on and its snapshot id against a manifest laid now from this
      tree (a stale bundle refuses and names the files that moved), unpack
      the derived bundle, compose each flow family's view (the raw trees
      from --results-root beside the frozen derived members), run the
      freshness gate and the raw binding, replay from THIS checkout verified
      against the bundle's manifest, verify the grid, and write flow_certification/v0.7 (one
      record per campaign, under artifacts/examples/findings/certification/
      <campaign>/; the driver's summaries are bound derived members and the
      entry carries the remeasurement facts, so canon holds no summary copy)
      (entries campaigns[<campaign>][<family>]) to --out. Every raw input
      the scoring stamp lists is a MANIFEST-listed member of the raw
      bundle; the report references evidence by both content ids, member
      path and member hash.
"""
import argparse
import hashlib
import json
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
MME = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(MME)]
import campaign_manifest
import derive_measured_go
import findings_flow
import flow_gap_certificate as fgc
from regenerate_flow_findings import parse_certificate
from scl_core import campaign_bundles as cb
from scl_core.campaign_declaration import check_family_manifest, load_declarations, resolve_family_manifest
from scl_core.records import portable_path, sha256_file as sha

RESULTS = MME / "results"
REPORT_KIND = "flow_certification/v0.7"   # one campaign per record file; the summaries are bound members (2026-09-17)
EVIDENCE_RULE = ("canon holds findings and declarations; the raw bundle holds the evidence and the derived "
                 "bundle holds one interpretation of it keyed by the raw content id, one derived bundle per "
                 "campaign whichever chains scored it; per-run rows are derived members, referenced here by "
                 "both content ids, member path and member hash; every scored input is a MANIFEST-listed raw "
                 "member; the derived bundle's snapshot id equals the one this tree lays")
# Evidence members of a bundle, under <campaign>/<family>/audit/, and the
# summary field that pins each one by hash.
EVIDENCE_MEMBERS = ("commitment_audit_rows.json", "remeasurement_rows.json", "map_check_recordings.json")
# the driver's three summaries travel as derived members beside the rows (stage_family copies them); since
# v0.6 the record binds them by hash and carries the remeasurement facts a reviewer needs (F37's
# gap_certificate among them), so canon holds no copy of a summary (nothing exists twice)
SUMMARY_MEMBERS = ("commitment_audit.json", "remeasurement.json", "map_verification.json")
REMEASUREMENT_SUMMARY_FIELDS = ("runs", "unmeasured_go",
                                "approximated_corridors", "clean_without_measured_go_count",
                                "repeated_outputs_identical", "repeated_findings_identical", "gap_certificate")
AUDIT_SUMMARY_FIELDS = ("runs_checked", "independent_sat_mismatches", "oncoming_evidence_removed_entry_changes",
                        "zone_depends_on_run_evidence", "unresolved_count")
MAP_CHECK_SUMMARY_FIELDS = ("cells", "recordings_checked", "ego_spawns_checked", "scenario_spawns_checked")


def verify_raw_identity(stamp, family_root):
    files = stamp.get("input_files")
    if not files or len(files) != stamp.get("n_inputs"):
        raise ValueError("certification requires a complete raw-input inventory")
    lines = []
    for name, expected in sorted(files.items()):
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("raw input paths must remain inside the family")
        actual = sha(family_root / rel) if (family_root / rel).is_file() else "absent"
        if actual == "absent" or actual != expected:
            raise ValueError(f"raw evidence identity mismatch: {name}")
        lines.append(f"{name} {actual}")
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    if digest != stamp.get("input_files_sha256"):
        raise ValueError("raw evidence digest mismatch")


def freshness_gate(dst_family, declaration, zone, map_excerpt, results, gap_certificate=None):
    # the family as run, from the raw tree (principle 13); canon only for a bundle predating its snapshot
    manifest, _ = resolve_family_manifest(declaration, results / declaration.results_subtree, ROOT)
    check_family_manifest(declaration, manifest)
    # the rows carry the certificate they were scored with exactly when one is passed here (load_flow)
    rows, manifest_doc, stamp = findings_flow.load_flow(dst_family / "flow_stats", manifest, zone.path,
                                                        map_excerpt, declaration, gap_certificate)
    verify_raw_identity(stamp, dst_family)
    if any(r["p_go"] is None or r.get("measured_go_corridor_approximated") or
           r.get("measured_go_zone_version") != zone.doc["zone_version"] or
           r.get("measured_go_zone_sha256") != zone.sha256 for r in rows):
        raise ValueError("certification requires measured entry against the declared map zone that was passed")
    return {"rows": len(rows), "input_files_sha256": stamp["input_files_sha256"],
            "scorer_sha256": stamp["scorer_sha256"],
            "geometry_sha256": zone.sha256,
            "zone_path": portable_path(zone.path, ROOT),
            "instrument_zone_version": zone.doc["zone_version"],
            "gap_certificate": ({"path": portable_path(Path(gap_certificate), ROOT), "sha256": sha(gap_certificate),
                                 "kind": fgc.load_certificate(gap_certificate)[0]["certificate_kind"]}
                                if gap_certificate else None),
            "family_manifest_sha256": declaration.manifest_sha256,
            "campaign_declaration": declaration.record(ROOT)}


def instrument_files(zone, map_excerpt, certificates=None):
    """The instrument's files the source manifest lists beside the one source list:
    the zone document, the excerpt and what the measured go depends on, plus
    the family manifests the zone declares as its scope (the replay's
    freshness check regenerates the zone from them), plus every declared flow
    gap certificate with its reader (F55)."""
    files = list(findings_flow.flow.instrument_dependency_paths(zone.path, map_excerpt).values())
    files += [ROOT / "artifacts/examples/solved_families" / f"{name}.json" for name in zone.doc["manifest_sha256"]]
    for certificate in (certificates or {}).values():
        for path in findings_flow.flow.instrument_dependency_paths(zone.path, map_excerpt, certificate).values():
            if path not in files:
                files.append(path)
    return files


def _pinned_in_place(scores_root, pin, expected, what):
    """An evidence file the driver left where its summary pins it, hashing to
    what the summary recorded; nothing is copied."""
    src = scores_root / pin["path"]
    if src.resolve() != Path(expected).resolve():
        raise ValueError(f"{what} are pinned at {pin['path']}, not at {expected}")
    if not src.is_file() or sha(src) != pin["sha256"]:
        raise ValueError(f"{what} are not the file their summary pins ({src})")


def read_raw(bundles_dir, campaign):
    path = Path(bundles_dir) / f"{campaign}.zip"
    if not path.is_file():
        raise ValueError(f"no raw bundle at {path}")
    raw = cb.read_bundle(path)
    if raw["campaign_id"] != campaign or raw["kind"] not in cb.RAW_KINDS:
        raise ValueError(f"{path.name} is not a raw bundle of {campaign} ({raw['campaign_id']}, {raw['kind']})")
    return raw


def by_campaign(declarations):
    grouped = {}
    for declaration in declarations:
        grouped.setdefault(declaration.campaign_id, []).append(declaration)
    return grouped


def _write_or_match(path, text, restage):
    """A stage writes once per scoring run; a re-stage (the snapshot re-laid after a tool moved,
    2026-09-17) must find every staged file as this scoring run would write it."""
    if restage:
        if path.read_text() != text:
            raise ValueError(f"{path}: staged from another scoring run; re-score before re-staging")
        return
    path.write_text(text)


def _copy_or_match(src, dst, restage):
    if restage:
        if not dst.is_file() or sha(dst) != sha(src):
            raise ValueError(f"{dst}: staged from another scoring run; re-score before re-staging")
        return
    shutil.copyfile(src, dst)


def stage_family(declaration, scores_root, zone, results, raw):
    """One flow family's derived members, bound in place to the summaries
    that pin them (F36): the driver's <campaign>/<family>/ gains its audit
    slice and the whole summaries under audit/. No raw
    tree enters it (F30). A family staged already by THIS scoring run is
    re-verified, not rewritten (the campaign's snapshot is re-laid when a
    listed tool moved); one staged from another run refuses."""
    campaign, family = declaration.campaign_id, declaration.family_id
    dst = scores_root / campaign / family
    for needed in ("flow_stats", "findings"):
        if not (dst / needed).is_dir():
            raise ValueError(f"{campaign}/{family}: no {needed}/ under the scores root; score it first "
                             "(score_campaign.py)")
    audit_dir = dst / "audit"
    restage = (audit_dir / "commitment_audit.json").exists()
    audit = json.loads((scores_root / "audit/commitment_audit.json").read_text())
    if audit["producer_sha256"] != sha(MME / "audit_flow_commitment.py") or audit["geometry_sha256"] != zone.sha256:
        raise ValueError("audit producer/geometry is stale")
    campaign_audit = audit["campaigns"][campaign][family]
    if campaign_audit["campaign_declaration"]["sha256"] != declaration.sha256:
        raise ValueError(f"{campaign}/{family}: the audit was computed against another campaign declaration")
    _pinned_in_place(scores_root, campaign_audit["rows"], audit_dir / "commitment_audit_rows.json",
                     "commitment audit rows")
    report = json.loads((scores_root / "remeasurement.json").read_text())
    if report["generator_sha256"] != sha(MME / "regenerate_flow_findings.py"):
        raise ValueError("remeasurement report is stale")
    campaign_report = report["campaigns"][campaign][family]
    if campaign_report["campaign_declaration"]["sha256"] != declaration.sha256:
        raise ValueError(f"{campaign}/{family}: the remeasurement was computed against another campaign declaration")
    _pinned_in_place(scores_root, campaign_report["rows"], audit_dir / "remeasurement_rows.json",
                     "remeasurement rows")
    map_check = json.loads((scores_root / "audit/map_verification.json").read_text())
    if map_check["producer_sha256"] != sha(MME / "verify_flow_map.py") or map_check["zone_sha256"] != zone.sha256:
        raise ValueError("map cross-check is stale")
    campaign_check = map_check["campaigns"][campaign][family]
    if campaign_check["campaign_declaration"]["sha256"] != declaration.sha256:
        raise ValueError(f"{campaign}/{family}: the map check was computed against another campaign declaration")
    _pinned_in_place(scores_root, campaign_check["recordings"], audit_dir / "map_check_recordings.json",
                     "map-check recordings")
    recordings = json.loads((audit_dir / "map_check_recordings.json").read_text())["extract_files_sha256"]
    stamp = json.loads((dst / "flow_stats/scoring_provenance.json").read_text())
    for name, digest in stamp["input_files"].items():
        if name.endswith(".json.gz") and recordings.get(f"{declaration.results_subtree}/{name}") != digest:
            raise ValueError("map cross-check is not bound to the scored trajectories")
    verify_raw_identity(stamp, results / declaration.results_subtree)
    raw_inputs = bind_raw_inputs(stamp, raw, f"{campaign}/{family}/")
    # every binding held: write the slice and the summaries beside the rows
    _write_or_match(audit_dir / "commitment_audit.json", json.dumps(campaign_audit, indent=1) + "\n", restage)
    _copy_or_match(scores_root / "remeasurement.json", audit_dir / "remeasurement.json", restage)
    _copy_or_match(scores_root / "audit/map_verification.json", audit_dir / "map_verification.json", restage)
    return {"audit": campaign_audit, "report": campaign_report, "map_check": campaign_check,
            "raw_inputs": raw_inputs}


def reproduce_text(campaign, raw, flow_declarations, zone_rel, excerpt_rel, certificates=None):
    lines = [f"Two bundles: the RAW bundle {campaign}.zip (content id {raw['content_id_sha256']}) holds the "
             "evidence; this DERIVED bundle holds its interpretation, whichever scoring chains produced it, "
             "and names the sources that produced it: source_snapshot/source_manifest.json lists every file "
             "by repo-relative path and sha256 and the commit (git.commit) of the scl_prototype repository "
             "they were read from. No source travels in the bundle (2026-09-19): check the repository out at "
             "that commit and verify the checkout against the manifest when you unpack:", "",
             "```sh", "conda run -n scl_prototype python3 scripts/unpack_campaign_bundle.py "
             f"{campaign}_derived.zip --into <dir> --verify-sources <checkout>", "```", "",
             "Run in a scl_prototype environment matching the manifest's runtime. Unpack both bundles into "
             "one directory, compose the view of each flow family below (the raw <family>/<model>/ trees "
             "beside this bundle's <family>/flow_stats, findings and audit), then from the checkout:", ""]
    for declaration in flow_declarations:
        family, declaration_rel = declaration.family_id, declaration.record(ROOT)["path"]
        certificate = (certificates or {}).get(family)
        certificate_arg = f"--gap-certificate {portable_path(certificate, ROOT)} " if certificate else ""
        lines += ["```sh", "conda run --no-capture-output -n scl_prototype python3 "
                  "adapters/fail2drive/scripts/multi_model_eval/replay_flow_bundle.py "
                  f"<view>/{family} --campaign {declaration_rel} --zone {zone_rel} --map-excerpt {excerpt_rel} "
                  f"{certificate_arg}"
                  f"--source-manifest <dir>/{campaign}/source_snapshot/source_manifest.json "
                  "--report /tmp/flow_replay.json", "```", ""]
    lines += ["The replay verifies the checkout against the manifest, regenerates geometry, scores, findings and "
              "the per-run audit (audit/commitment_audit_rows.json and audit/remeasurement_rows.json are the "
              "evidence rows the canon summaries pin by hash). No simulator is needed. Entry is sampled "
              "body contact with a declared map lane; this certifies reproducible campaign analysis, not "
              "variant passability or continuous-time entry. Threading families of this campaign, if any, are "
              "bound by threading_certification and reproduce through score_campaign.py from the same "
              "checkout.", ""]
    return "\n".join(lines)


def stage(scores_root, declarations, flow, zone, map_excerpt, results, bundles_dir, certificates=None):
    """Stage every campaign among the declarations; returns {campaign: {family: bindings}}.
    `certificates` {family_id: path}: the declared flow gap certificates (F55), snapshot members."""
    zone_rel, excerpt_rel = (portable_path(p, ROOT) for p in (zone.path, map_excerpt))
    staged = {}
    for campaign, decls in by_campaign(declarations).items():
        raw = read_raw(bundles_dir, campaign)
        folder = scores_root / campaign
        if not folder.is_dir():
            raise ValueError(f"no derived folder {folder}; score the campaign first (score_campaign.py)")
        flow_decls = [d for d in decls if d in flow]
        if not flow_decls:
            print(f"{campaign}: no family scored by the flow chain; nothing to stage (a campaign without flow "
                  "families freezes without staging)", flush=True)
            continue
        missing = [d.family_id for d in decls if not (folder / d.family_id).is_dir()]
        if missing:
            raise ValueError(f"{campaign}: the derived folder lacks {missing}; every declared family is "
                             "scored before the stage, so one freeze holds the whole campaign")
        snapshot = folder / cb.SNAPSHOT_DIR
        if snapshot.exists():
            # staged already: the stage is idempotent when this tree lays the same manifest (a
            # freeze-derived re-run after a failed bind); a different tree refuses, re-score.
            # The same FILES under another identity (the tree was committed since the stage, so
            # the git block no longer lists modified sources; or the runtime moved) re-lays the
            # manifest in place: the freeze then names this tree, not the one that staged
            # (2026-09-20, the F48 re-issue: publish had refused the bundle of the modified tree).
            check = Path(tempfile.mkdtemp(prefix="stage_check_"))
            laid = campaign_manifest.lay_source_manifest(check, decls, instrument_files(zone, map_excerpt, certificates))
            staged_path = snapshot / cb.SOURCE_MANIFEST
            recorded = json.loads(staged_path.read_text())
            moved = snapshot_differences(check / cb.SNAPSHOT_DIR / cb.SOURCE_MANIFEST, recorded["files"])
            fresh = (check / cb.SNAPSHOT_DIR / cb.SOURCE_MANIFEST).read_bytes()
            shutil.rmtree(check, ignore_errors=True)
            if moved:
                raise ValueError(f"{campaign}: {snapshot} exists and differs from what this tree lays "
                                 f"({moved[:6]}{' ...' if len(moved) > 6 else ''}); re-score and re-stage")
            if fresh != staged_path.read_bytes():
                was = (recorded.get("git") or {})
                staged_path.write_bytes(fresh)
                print(f"{campaign}: staged already; the same files under another identity (git "
                      f"{str(was.get('commit', '?'))[:12]}, {len(was.get('modified') or [])} modified): source manifest "
                      f"re-laid as this tree lays it ({laid[:12]})", flush=True)
            else:
                print(f"{campaign}: staged already with the manifest this tree lays ({laid[:12]}); unchanged", flush=True)
            continue
        staged[campaign] = {}
        for declaration in flow_decls:
            print(f"Staging {campaign}/{declaration.family_id}", flush=True)
            staged[campaign][declaration.family_id] = stage_family(declaration, scores_root, zone, results, raw)
        source_id = campaign_manifest.lay_source_manifest(folder, decls, instrument_files(zone, map_excerpt, certificates))
        (folder / "REPRODUCE.md").write_text(reproduce_text(campaign, raw, flow_decls, zone_rel, excerpt_rel,
                                                            certificates))
        campaigns = " ".join(f"--campaign {portable_path(d.path, ROOT)}" for d in decls)
        print(f"staged {campaign}: {sorted(staged[campaign])} bound, source manifest {source_id[:12]}; freeze with\n"
              f"  conda run -n scl_prototype python3 {portable_path(MME / 'campaign_manifest.py', ROOT)} "
              f"--produce-derived {Path(bundles_dir) / (campaign + '_derived.zip')} "
              f"--raw {raw['path']} --derived-dir {folder} {campaigns}", flush=True)
    return staged


def compose_view(declaration, derived_family, results, view_root):
    """The family as the replay and the verifier see it: the raw model trees
    hard-linked from the unpacked raw bundle, the derived folder's files
    copied beside them. A working view, never frozen."""
    view = view_root / declaration.campaign_id / declaration.family_id
    view.mkdir(parents=True, exist_ok=False)
    for model in declaration.models:
        subprocess.run(["cp", "-al", str(results / declaration.results_subtree / model), str(view / model)], check=True)
    for child in derived_family.iterdir():
        shutil.copytree(child, view / child.name) if child.is_dir() else shutil.copyfile(child, view / child.name)
    return view


def bind_raw_inputs(stamp, raw, prefix):
    """Every raw input the scoring stamp lists must be a MANIFEST-listed member
    of the raw bundle with the same hash: the scored evidence IS the bundle."""
    files = stamp["input_files"]
    missing = [rel for rel in files if f"{prefix}{rel}" not in raw["listed"]]
    if missing:
        raise ValueError(f"scored inputs not in the raw bundle {raw['path'].name}: {missing[:3]}")
    drifted = [rel for rel, digest in files.items() if raw["listed"][f"{prefix}{rel}"] != digest]
    if drifted:
        raise ValueError(f"scored inputs whose hash is not the raw bundle's: {drifted[:3]}")
    return {"count": len(files), "input_files_sha256": stamp["input_files_sha256"], "all_listed": True}


def bundle_content_id(path):
    return cb.read_bundle(path)["content_id_sha256"]


def verify_bundle(path, source_id):
    """A derived bundle whose every member verifies and whose source manifest
    is the one this tree lays; the provenance as read."""
    bundle = cb.read_bundle(path)
    for member in bundle["listed"]:
        cb.member_hash(bundle, member)
    provenance = bundle["provenance"]
    recorded = (provenance.get("source_snapshot") or {}).get("sha256", provenance.get("source_snapshot_sha256"))
    if recorded != source_id:
        raise ValueError("bundle source identity mismatch")
    return provenance


def snapshot_differences(laid_manifest, recorded_files):
    """Which listed sources differ between the manifest the tree lays now and a frozen bundle's."""
    laid = json.loads(Path(laid_manifest).read_text())["files"]
    return sorted(set(laid) ^ set(recorded_files)) + sorted(
        name for name in set(laid) & set(recorded_files) if laid[name] != recorded_files[name])


def evidence_references(zip_path, family_id, pinned):
    """The certification's binding to evidence rows: the bundle's content id
    plus, per evidence member, its path inside the bundle and the hash the
    bundle's own MANIFEST lists for it. Each member must be present and must
    hash to what its summary pinned, so canon -> summary hash -> bundle
    member is one unbroken chain. `pinned`: {member name: sha256 the summary
    recorded}."""
    bundle = cb.read_bundle(zip_path)
    prefix = f"{bundle['campaign_id']}/"
    listed = bundle["listed"]
    members = {}
    for member, expected in pinned.items():
        path = f"{prefix}{family_id}/audit/{member}"
        if path not in listed:
            raise ValueError(f"evidence member missing from the bundle: {path}")
        if listed[path] != expected:
            raise ValueError(f"evidence member {path} hashes {listed[path]} in the bundle; its summary pins {expected}")
        members[path] = listed[path]
    return {"bundle_content_id_sha256": bundle["content_id_sha256"], "members": members}


def certify(declarations, flow, bundles_dir, results, zone, map_excerpt, work, record_path, certificates=None):
    """Bind: every flow family of every campaign among the declarations
    against the frozen pair; writes the record and returns it. `certificates`
    {family_id: path}: the declared flow gap certificates the rows were scored
    with (F55), passed to the freshness gate and the replay."""
    certificates = dict(certificates or {})
    work = Path(work)
    if work.exists():
        raise ValueError("the bind's --work must be a new directory; preserve prior evidence")
    work.mkdir(parents=True)
    zone_rel, excerpt_rel = (portable_path(p, ROOT) for p in (zone.path, map_excerpt))
    report = {"report_kind": REPORT_KIND, "driver_sha256": sha(Path(__file__)),
              "evidence_rule": EVIDENCE_RULE, "campaigns": {}}
    for campaign, decls in by_campaign(declarations).items():
        if not any(d in flow for d in decls):
            print(f"{campaign}: no family scored by the flow chain; bound by the threading record", flush=True)
            continue
        print(f"Verifying {campaign}", flush=True)
        raw = read_raw(bundles_dir, campaign)
        derived_path = Path(bundles_dir) / f"{campaign}_derived.zip"
        if not derived_path.is_file():
            raise ValueError(f"no {derived_path}: stage the scoring run (--stage) and freeze it "
                             "(campaign_manifest.py --produce-derived) first")
        derived = cb.read_bundle(derived_path)
        if derived["kind"] != cb.DERIVED_KIND:
            raise ValueError(f"{derived_path.name} is {derived['kind']!r}, not {cb.DERIVED_KIND}")
        if derived["provenance"]["raw_bundle"]["content_id_sha256"] != raw["content_id_sha256"]:
            raise ValueError(f"{derived_path.name} was computed from raw "
                             f"{derived['provenance']['raw_bundle']['content_id_sha256'][:12]}, "
                             f"{raw['path'].name} is {raw['content_id_sha256'][:12]}")
        check_dir = work / "snapshot_check" / campaign
        source_id = campaign_manifest.lay_source_manifest(check_dir, decls, instrument_files(zone, map_excerpt, certificates))
        recorded = derived["provenance"]["source_snapshot"]
        if recorded["sha256"] != source_id:
            moved = snapshot_differences(check_dir / cb.SNAPSHOT_DIR / cb.SOURCE_MANIFEST, recorded["files"])
            if not moved:
                git = recorded.get("git") or {}
                raise ValueError(f"{derived_path.name} names the same files under another identity than this tree "
                                 f"(frozen at git {str(git.get('commit', '?'))[:12]} with {len(git.get('modified') or [])} "
                                 "modified, or another runtime); re-run freeze-derived: the stage re-lays the manifest")
            raise ValueError(f"{derived_path.name} is stale against this tree: its snapshot differs in "
                             f"{moved[:6]}{' ...' if len(moved) > 6 else ''}; re-score, re-stage, re-freeze")
        provenance = verify_bundle(derived_path, source_id)
        root = cb.unpack(derived_path, work / "unpacked")["root"]
        absent = [d.family_id for d in decls if d.family_id not in provenance["families"]]
        if absent:
            raise ValueError(f"{derived_path.name} holds no families {absent} (has {provenance['families']})")
        raw_families = raw["provenance"].get("families") or {}
        for declaration in [d for d in decls if d in flow]:
            family = declaration.family_id
            frozen = root / family
            declaration_rel = declaration.record(ROOT)["path"]
            view = compose_view(declaration, frozen, results, work / "replay_view")
            certificate = certificates.get(family)
            fresh = freshness_gate(view, declaration, zone, map_excerpt, results, certificate)
            stamp = json.loads((frozen / "flow_stats/scoring_provenance.json").read_text())
            raw_inputs = bind_raw_inputs(stamp, raw, f"{campaign}/{family}/")
            # the replay runs from THIS checkout (the one the stale gate above proved equal to the
            # bundle's manifest) and verifies it against the bundle's own manifest, the same way a
            # third party does from a checkout at the recorded commit
            replay_report = work / f"{campaign}_{family}.replay.json"
            subprocess.run([sys.executable, str(MME / "replay_flow_bundle.py"),
                            str(view), "--campaign", str(ROOT / declaration_rel), "--report", str(replay_report),
                            "--zone", str(ROOT / zone_rel), "--map-excerpt", str(ROOT / excerpt_rel),
                            "--source-manifest", str(root / cb.SNAPSHOT_DIR / cb.SOURCE_MANIFEST),
                            *(["--gap-certificate", str(certificate)] if certificate else [])],
                           check=True, cwd=ROOT)
            # the recorder logs the header promises stay in the raw zip (the scoring unpack verifies them in
            # place, F67 item 6): the completeness check reads their presence from the listing of the zip bound here
            verification = campaign_manifest.build_manifest(view, declaration.models, declaration.repetitions,
                                                            do_hash=True, variants=declaration.variants,
                                                            in_zip=cb.member_sizes(raw["path"], f"{campaign}/{family}/"))
            if verification["severe"] or verification["problems"]:
                raise ValueError(f"campaign completeness failed: {verification['problems']} {verification['severe']}")
            audit_slice = json.loads((frozen / "audit/commitment_audit.json").read_text())
            report_slice = json.loads((frozen / "audit/remeasurement.json").read_text())["campaigns"][campaign][family]
            check_slice = json.loads((frozen / "audit/map_verification.json").read_text())["campaigns"][campaign][family]
            for what, doc in (("audit", audit_slice), ("remeasurement", report_slice), ("map check", check_slice)):
                if doc["campaign_declaration"]["sha256"] != declaration.sha256:
                    raise ValueError(f"{campaign}/{family}: the frozen {what} was computed against another declaration")
            evidence = evidence_references(derived_path, family, {
                "commitment_audit_rows.json": audit_slice["rows"]["sha256"],
                "remeasurement_rows.json": report_slice["rows"]["sha256"],
                "map_check_recordings.json": check_slice["recordings"]["sha256"],
                # the summaries as unpacked (every member verified against the MANIFEST at unpack)
                **{member: sha(frozen / "audit" / member) for member in SUMMARY_MEMBERS}})
            report["campaigns"].setdefault(campaign, {})[family] = {
                "campaign_declaration": declaration.record(ROOT),
                "coverage": verification["coverage"],
                "freshness": fresh, "replay": json.loads(replay_report.read_text()),
                # the findings documents canon receives, by hash, as the replay reproduced them (the
                # threading record's `documents`, so every record binds documents the same way)
                "documents": json.loads(replay_report.read_text())["findings_sha256"],
                "audit": {k: audit_slice[k] for k in AUDIT_SUMMARY_FIELDS},
                "remeasurement": {k: report_slice[k] for k in REMEASUREMENT_SUMMARY_FIELDS},
                "map_check": {k: check_slice[k] for k in MAP_CHECK_SUMMARY_FIELDS},
                "bundle": {"path": raw["path"].name, "bundle_kind": raw["kind"], "zip_sha256": raw["zip_sha256"],
                           "content_id_sha256": raw["content_id_sha256"],
                           "family_sub_id_sha256": (raw_families.get(family) or {}).get("family_sub_id_sha256")},
                "derived_bundle": {"path": derived_path.name, "bundle_kind": provenance["bundle_kind"],
                                   "zip_sha256": derived["zip_sha256"],
                                   "content_id_sha256": derived["content_id_sha256"],
                                   "source_snapshot_sha256": source_id,
                                   "writer_sha256": provenance.get("writer_sha256"),
                                   "families": provenance["families"],
                                   "reproduced_raw_members": provenance.get("reproduced_raw_members")},
                "evidence": {"bundle_content_id_sha256": raw["content_id_sha256"], "raw_inputs": raw_inputs,
                             "derived_content_id_sha256": evidence["bundle_content_id_sha256"],
                             "derived_members": evidence["members"]}}
            print(f"{campaign}/{family}: replayed and bound to derived {derived['content_id_sha256'][:12]}",
                  flush=True)
    shutil.rmtree(work / "replay_view", ignore_errors=True)
    record_path = Path(record_path)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(report, indent=1) + "\n")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--campaign", action="append", required=True, type=Path, metavar="DECLARATION",
                    help="campaign declaration (artifacts/examples/campaigns/...); repeatable, required; pass "
                         "every family of a campaign, the flow ones are staged and bound")
    ap.add_argument("--zone", type=Path, required=True,
                    help="declared conflict-zone document the scores were measured against")
    ap.add_argument("--map-excerpt", type=Path, required=True,
                    help="the pinned OpenDRIVE excerpt that zone declares as its source")
    ap.add_argument("--results-root", type=Path, default=RESULTS,
                    help="location of the raw campaign trees (the unpacked raw bundle); each declaration names its subtree")
    ap.add_argument("--bundles-dir", type=Path, required=True,
                    help="directory holding <campaign>.zip (the raw evidence, F30) and, for the bind, "
                         "<campaign>_derived.zip (frozen by campaign_manifest.py --produce-derived)")
    ap.add_argument("--stage", action="store_true",
                    help="stage the scoring driver's --out in place and lay the source manifest; nothing certified")
    ap.add_argument("--scores-root", type=Path, default=None, help="--stage: the scoring driver's --out")
    ap.add_argument("--out", type=Path, default=None, help="bind: the certification record to write (canon)")
    ap.add_argument("--work", type=Path, default=None,
                    help="bind: a NEW directory for the unpacked derived bundle, the replay views and reports")
    ap.add_argument("--gap-certificate", action="append", default=[], metavar="FAMILY=PATH",
                    help="the declared flow gap certificate a family was scored against (F55), repeatable; "
                         "passed exactly when the scoring passed it (the rows say which)")
    args = ap.parse_args()
    try:
        declarations = load_declarations(args.campaign)
        certificates = dict(parse_certificate(spec) for spec in args.gap_certificate)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))
    if len(certificates) != len(args.gap_certificate):
        ap.error("one --gap-certificate per family")
    undeclared = sorted(set(certificates) - {d.family_id for d in declarations})
    if undeclared:
        ap.error(f"--gap-certificate names families this run does not declare: {undeclared}")
    certificates = {family: path.resolve() for family, path in certificates.items()}
    if args.stage:
        if args.scores_root is None or args.out is not None or args.work is not None:
            ap.error("--stage takes --scores-root (the scoring driver's --out) and neither --out nor --work")
    elif args.out is None or args.work is None or args.scores_root is not None:
        ap.error("the bind takes --out (the record) and --work (a new directory), not --scores-root")
    zone = derive_measured_go.load_zone(args.zone.resolve())
    derive_measured_go.check_map_excerpt(zone, args.map_excerpt)
    flow = [d for d in declarations if campaign_manifest.scoring_chain(d) == "flow"]
    if not flow:
        ap.error("no declared family is scored by the flow chain (its plan schedules no behavior-scalar factor)")
    if args.stage:
        stage(args.scores_root.resolve(), declarations, flow, zone, args.map_excerpt.resolve(),
              args.results_root.resolve(), args.bundles_dir.resolve(), certificates)
    else:
        certify(declarations, flow, args.bundles_dir.resolve(), args.results_root.resolve(), zone,
                args.map_excerpt.resolve(), args.work.resolve(), args.out.resolve(), certificates)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
