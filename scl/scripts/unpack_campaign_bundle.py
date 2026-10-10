#!/usr/bin/env python3
"""Unpack a campaign bundle into the layout the runner wrote, verified.

The one tool that opens a bundle for the scoring chain (F30, 2026-09-14):
every existing tool keeps taking directories, so a re-score is unpack,
score_campaign, certify. Every data member is checked against
MANIFEST.sha256 on the way out and the provenance must name the manifest's
hash; a member that does not verify refuses the whole unpack. Works on raw
bundles (campaign_bundle/v0.3), on the historical mixed ones (v0.1, v0.2:
the old derived rows come out with the tree and sit there inert, the
scorer writes elsewhere) and on derived bundles.

    conda run -n scl_prototype python scripts/unpack_campaign_bundle.py \\
        ~/scl/campaigns/campaign_carla_v03.zip --into /work/rescore
    # -> /work/rescore/campaign_carla_v03/<family>/...  (a results root)

--scoring-inputs (2026-10-07, ledger F67): extract only the members the scoring chain reads and
verify the rest in place. The runners' evidence members no chain tool reads are the bulk of a raw
bundle: CARLA's viz/ with the videos and per-frame metas (v08: 13.5 GB in 173,832 files against
70 MB of checkpoint JSONs, trajectory extracts and tables) and an Isaac cell's witness.mp4 (0.7 GB
against 20 MB of ledgers), and CARLA's recordings/ (the recorder logs: v08 3.1 GB in 246 files, v01
16.6 GB in 530 files; F67 item 6, 2026-10-08): no scorer reads them (the chain reads the trajectory
extracts under trajectories/), and the bind's completeness check, which verifies the runner's promise
of a recording per route, reads their presence and size from the raw zip's listing
(campaign_manifest.check_recordings with the members in the zip; until 2026-10-08 they were written for
that check alone). Every member is still hashed against MANIFEST.sha256, streamed in chunks (never a
whole member in memory); the evidence members are verified and not written. The unpack's time is that
hash over every member, written or not (v08: 60.6 s with the logs written, 57.5 s without); what the
rule saves is the writes. The launcher's score verb passes it; a full unpack stays the default.

Stdlib-only: runs under any env.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import campaign_bundles as cb  # noqa: E402


# the runners' evidence members no chain tool reads: CARLA's viz/ (videos and per-frame metas) and
# recordings/ (the recorder logs; the scorer's own exclusion in score_runs.py names both, and the bind's
# completeness check reads the logs' presence from the zip's listing since F67 item 6), and the Isaac
# runner's one witness video per cell.
EVIDENCE_ONLY_DIRS = ("viz", "recordings")
EVIDENCE_ONLY_FILES = ("witness.mp4",)


def is_evidence_only(name: str) -> bool:
    parts = name.split("/")
    return any(p in EVIDENCE_ONLY_DIRS for p in parts[:-1]) or parts[-1] in EVIDENCE_ONLY_FILES


def unpack_scoring_inputs(zip_path: Path, into: Path) -> dict:
    """cb.unpack with the scoring-chain policy: the evidence-only members are hashed and left in the zip."""
    return cb.unpack(zip_path, into, verify_only=is_evidence_only)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle", type=Path, help="a campaign bundle zip (raw, mixed or derived)")
    ap.add_argument("--into", type=Path, required=True,
                    help="the directory to unpack under; the campaign id becomes its subdirectory "
                         "(refused when that subdirectory exists: present is not verified)")
    ap.add_argument("--verify-sources", type=Path, default=None, metavar="CHECKOUT",
                    help="derived bundles: a checkout of the repository to verify against the bundle's "
                         "source manifest (every listed file by hash; the manifest names the commit); "
                         "exit 3 when the checkout differs")
    ap.add_argument("--scoring-inputs", action="store_true",
                    help="extract only the members the scoring chain reads; the runners' evidence members (CARLA "
                         "viz/ and recordings/, Isaac witness.mp4) are verified in place and not written (the bind "
                         "reads the recorder logs' presence from the zip's listing)")
    args = ap.parse_args()
    try:
        info = unpack_scoring_inputs(args.bundle, args.into) if args.scoring_inputs else cb.unpack(args.bundle, args.into)
    except cb.BundleError as exc:
        print(f"UNPACK REFUSED: {exc}", file=sys.stderr)
        return 2
    families = info["provenance"].get("families")
    print(f"unpacked: {info['root']}")
    print(f"  kind: {info['kind']}; campaign: {info['campaign_id']}; members verified: {len(info['listed'])}")
    if args.scoring_inputs:
        print(f"  scoring inputs: {info['extracted']} members extracted ({info['bytes_extracted'] / 1e6:.0f} MB); "
              f"{info['verified_in_place']} evidence members verified in place, not written "
              f"({info['bytes_verified_in_place'] / 1e9:.1f} GB)")
    print(f"  content id (MANIFEST sha256): {info['content_id_sha256']}")
    print(f"  zip sha256: {info['zip_sha256']}")
    if families:
        for name, fam in sorted(families.items()):
            sub = fam.get("family_sub_id_sha256") if isinstance(fam, dict) else fam
            print(f"  {name}: sub-id {sub}")
    if info["kind"] in (cb.DERIVED_KIND,) + cb.LEGACY_DERIVED_KINDS:
        raw = info["provenance"]["raw_bundle"]
        print(f"  interpretation of raw {raw['content_id_sha256']} ({raw['bundle_kind']})")
        snapshot = info["provenance"].get("source_snapshot") or {}
        git = snapshot.get("git") or {}
        print(f"  sources listed: {len(snapshot.get('files') or {})}; commit: {git.get('commit') or 'none recorded'}"
              + (f"; modified against it when frozen: {git['modified']}" if git.get("modified") else ""))
        if args.verify_sources is not None:
            moved = cb.source_differences(snapshot.get("files") or {}, args.verify_sources)
            if moved:
                print(f"CHECKOUT DIFFERS from the source manifest in {len(moved)} file(s): {moved[:8]}"
                      f"{' ...' if len(moved) > 8 else ''}; check out commit {git.get('commit') or '?'}", file=sys.stderr)
                return 3
            print(f"  checkout {args.verify_sources} verified against the source manifest")
    elif args.verify_sources is not None:
        print("--verify-sources applies to a derived bundle only", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
