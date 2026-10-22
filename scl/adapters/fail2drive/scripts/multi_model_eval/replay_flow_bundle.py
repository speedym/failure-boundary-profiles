#!/usr/bin/env python3
"""Reproduce one packaged flow family from this checkout, verified against the bundle's source manifest.

--source-manifest is the derived bundle's source_snapshot/source_manifest.json
(every source by repo-relative path and sha256, the git commit, the runtime;
no copies travel since 2026-09-19): this checkout must hold every listed
file as hashed, and the interpreter must match the pinned runtime. --campaign
is the campaign declaration in this checkout (grid, family by path and hash);
--zone / --map-excerpt the declared instrument files here, all listed in the
manifest. The rows, the fits, the findings and the per-run audit are
recomputed through the one pipeline (F82: the flow scorer, scl_core's fit
and emitter) and compared with the packaged ones by hash.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
MME = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(MME)]
import derive_measured_go as instrument
from audit_flow_commitment import audit_campaign, rows_record
from scl_core import run_rows as rr
from scl_core.campaign_bundles import measure_runtime, source_differences
from scl_core.campaign_declaration import check_family_manifest, load_declaration, resolve_family_manifest
from scl_core.records import sha256_file as sha


def compare_files(actual, expected):
    a = {p.name: sha(p) for p in actual.iterdir() if p.is_file()}
    b = {p.name: sha(p) for p in expected.iterdir() if p.is_file()}
    if a != b:
        raise ValueError(f"replay differs from packaged output: {expected} ({sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))})")
    return a


def verify_import_origins(root):
    local_names = {"flow_aggregate", "derive_measured_go", "route_metrics", "trajectory_extract", "score_runs",
                   "audit_flow_commitment", "flow_gap_certificate"}
    for name, module in list(sys.modules.items()):
        if name in local_names or name.split(".")[0] in ("adapters", "scl_core"):
            location = getattr(module, "__file__", None)
            if location and not Path(location).resolve().is_relative_to(root):
                raise ValueError(f"replay imported source outside its checkout: {name}: {location}")


def replay(family_root, declaration_path, zone_path, map_excerpt, source_manifest_path, gap_certificate=None,
           defects=None):
    verify_import_origins(ROOT)
    source_manifest = json.loads(Path(source_manifest_path).read_text())
    moved = source_differences(source_manifest["files"], ROOT)
    if moved:
        commit = (source_manifest.get("git") or {}).get("commit", "?")
        raise ValueError(f"this checkout differs from the bundle's source manifest in {moved[:6]}"
                         f"{' ...' if len(moved) > 6 else ''}; check the repository out at commit {commit}")
    runtime = measure_runtime()   # the same function that pinned it (one function, both sides)
    if runtime != source_manifest["runtime"]:
        raise ValueError(f"replay requires the pinned runtime: {source_manifest['runtime']}")
    # Geometry and declaration are replayed from this verified checkout, and the
    # zone document must regenerate from its declared identity before it scores.
    for path in (zone_path, map_excerpt, declaration_path) + ((gap_certificate,) if gap_certificate else ()):
        if not Path(path).resolve().is_relative_to(ROOT):
            raise ValueError(f"replay inputs must come from the verified checkout: {path}")
    zone = instrument.load_zone(zone_path)
    instrument.check_map_excerpt(zone, map_excerpt)
    declaration = load_declaration(declaration_path)
    manifest, _ = resolve_family_manifest(declaration, family_root, ROOT)   # the family as run (principle 13)
    check_family_manifest(declaration, manifest)
    scope = [ROOT / "artifacts/examples/solved_families" / f"{name}.json" for name in zone.doc["manifest_sha256"]]
    subprocess.run([sys.executable, str(MME / "build_conflict_lane_zone.py"), "--check",
                    "--out", str(zone.path), "--map-excerpt", str(map_excerpt), "--scope", *map(str, scope)],
                   check=True)
    defect_args = ["--defects", str(defects)] if defects else []
    with tempfile.TemporaryDirectory(prefix="flow_replay_") as tmp:
        tmp = Path(tmp)
        rows_dir, stats_dir, findings = tmp / "scoring_out", tmp / "stats", tmp / "findings"
        subprocess.run([sys.executable, str(MME / "flow_aggregate.py"), str(family_root),
                        "--manifest", str(manifest), "--zone", str(zone.path), "--map-excerpt", str(map_excerpt),
                        "--out", str(rows_dir), "--quiet",
                        *(["--gap-certificate", str(gap_certificate)] if gap_certificate else [])], check=True)
        rows_hashes = compare_files(rows_dir, family_root / "scoring_out")
        subprocess.run([sys.executable, str(ROOT / "scripts/fit_rows.py"), "--rows", str(rows_dir), "--out", str(stats_dir),
                        "--quiet", *defect_args], check=True)
        stats_hashes = compare_files(stats_dir, family_root / "stats")
        subprocess.run([sys.executable, str(ROOT / "scripts/emit_findings.py"), "--rows", str(rows_dir), "--stats", str(stats_dir),
                        "--campaign", str(declaration_path), "--family-manifest", str(manifest), "--out", str(findings),
                        *defect_args], check=True)
        finding_hashes = compare_files(findings, family_root / "findings")
        summary, rows_text = audit_campaign(family_root, rows_dir / Path(rr.ROWS_MEMBER).name, zone, declaration)
        recomputed = {**summary, "campaign_declaration": declaration.record(ROOT),
                      "rows": rows_record(declaration, rows_text)}
        if recomputed != json.loads((family_root / "audit/commitment_audit.json").read_text()):
            raise ValueError("audit does not reproduce from packaged evidence")
        packaged_rows = family_root / "audit/commitment_audit_rows.json"
        if hashlib.sha256(packaged_rows.read_bytes()).hexdigest() != recomputed["rows"]["sha256"]:
            raise ValueError("audit evidence rows do not reproduce from packaged evidence")
        if summary["unresolved_count"]:
            raise ValueError("unresolved disagreement cases prevent certification")
    return {"rows_sha256": rows_hashes, "stats_sha256": stats_hashes, "findings_sha256": finding_hashes,
            "audit_reproduced": True, "evidence_rows_reproduced": True,
            "campaign_declaration_sha256": declaration.sha256,
            "source_snapshot_sha256": sha(Path(source_manifest_path))}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("family_root", type=Path)
    ap.add_argument("--campaign", required=True, type=Path,
                    help="the campaign declaration in this checkout (listed in the manifest)")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--zone", type=Path, required=True,
                    help="the declared conflict-zone document in this checkout (listed in the manifest)")
    ap.add_argument("--map-excerpt", type=Path, required=True,
                    help="the pinned OpenDRIVE excerpt in this checkout (listed in the manifest)")
    ap.add_argument("--source-manifest", type=Path, required=True,
                    help="the derived bundle's source_snapshot/source_manifest.json, as unpacked")
    ap.add_argument("--gap-certificate", type=Path, default=None,
                    help="the family's declared flow gap certificate in this checkout, exactly when the scoring passed one")
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register the fits and findings were produced with, exactly when one was")
    args = ap.parse_args()
    report = replay(args.family_root, args.campaign, args.zone, args.map_excerpt, args.source_manifest,
                    args.gap_certificate, args.defects)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1) + "\n")
    print(f"replayed {args.family_root}: {len(report['findings_sha256'])} documents reproduced")


if __name__ == "__main__":
    main()
