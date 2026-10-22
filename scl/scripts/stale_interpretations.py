#!/usr/bin/env python3
"""Which interpretations this checkout no longer verifies: every pinned derived bundle against the tree.

The re-stamp set is read, never inferred (ledger F81, 2026-10-09: the scoring driver both backends
share moved by two lines in its Isaac branch, only the eight Isaac campaigns were re-stamped because
only Isaac values could move, and every CARLA derived bundle, which lists the driver in its source
manifest, went stale against the tree with nothing standing saying so; the unpacker's
--verify-sources found it by hand the next day). A derived bundle's source manifest is its contract
with the checkout: a listed file whose bytes moved stales the bundle whatever the change does and
whichever backend it was aimed at.

For every campaign the certification records pin (results_store.interpretation_id, the strict
reader), the pinned derived zip is read from the archive without unpacking, its content id verified
against the records, and three classes of binding compared with the tree at --repo-root:

  sources       every file of source_snapshot/source_manifest.json, by sha256
                (campaign_bundles.source_differences, the unpacker's check)
  canon inputs  every path -> sha256 of every scoring stamp's canon_inputs_sha256 (the one stamp of
                every chain since F82, scoring_out/scoring_provenance.json per family; the warehouse
                chain's instruments are pinned by path and sha256 in it and in its documents since
                2026-10-10, so a moved instrument file is caught under "path pins" below)
  path pins     every {"path": ..., "sha256": ...} in any JSON member (the declarations and
                instruments the documents pin); a pin that names a member of the bundle itself (the
                flow chain's audit files) is counted and not checked: a derived bundle is
                self-contained, every member is verified against its manifest at each unpack and the
                manifest is bound by the content id, so the verb asks nothing about the inside of a
                bundle (Milan, 2026-10-10); a machine-local path (a run tree, an absolute path) is
                reported as unresolved, never as moved

One row per campaign: backend (from the canon declarations), the frozen commit, the counts and the
moved files. A campaign is STALE when any source, canon input or path pin differs from the tree or
is missing from it. Exit 0 when every interpretation verifies, 1 when any is stale, 2 on a refusal
(no record, records that disagree, an archive zip that is not the pinned interpretation). --out
writes the rows as a stale_interpretations/v0.1 report. Both backends by construction: the set is
every campaign with records, and the verb is the only way a re-stamp set is scoped.

    scl campaign stale --bundles ~/scl/campaigns [--campaign ID ...] [--out report.json]

Stdlib-only (results_store, campaign_bundles and campaign_declaration are on the admitted list):
runs under any env; the launcher routes it through scl_prototype like the other central verbs.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import campaign_bundles as cb  # noqa: E402
from scl_core import results_store as store  # noqa: E402
from scl_core.campaign_declaration import load_declaration  # noqa: E402
from scl_core.records import sha256_file  # noqa: E402

REPORT_KIND = "stale_interpretations/v0.1"
CANON_CAMPAIGNS = "artifacts/examples/campaigns"
STAMP_NAME = "scoring_provenance.json"          # the one stamp per family (scl_core.run_rows.STAMP_MEMBER): canon_inputs_sha256 {path: sha256}
JSON_MEMBER_CAP = 4_000_000                     # the export's cap: a larger JSON member is data, not a document that pins
UNRESOLVABLE_PREFIXES = ("artifacts/runs/", "/", "~")   # machine-local: a run tree or an absolute path, which no tree holds
METHOD = ("every campaign under the certification records; the pinned derived zip read without unpacking and its "
          "content id checked; its source manifest's files, its scoring stamps' canon_inputs_sha256 and every "
          "path+sha256 pin in its JSON members compared with the files at --repo-root by sha256")


class StaleError(ValueError):
    """The verb cannot answer for a campaign: the archive holds no pinned zip, or another one."""


def backend_of(root, campaign) -> str:
    """The backend the canon declarations of a campaign state (a data value, principle 8);
    'undeclared' when this checkout holds none, so the row still prints."""
    base = Path(root) / CANON_CAMPAIGNS / campaign
    backends = sorted({load_declaration(p).backend for p in base.glob("*.json")}) if base.is_dir() else []
    return backends[0] if len(backends) == 1 else ("undeclared" if not backends else "+".join(backends))


def path_pins(obj, out: dict) -> None:
    """{repo path: {sha256, ...}} over every {"path": ..., "sha256": ...} a document carries, wherever it sits."""
    if isinstance(obj, dict):
        if isinstance(obj.get("path"), str) and isinstance(obj.get("sha256"), str):
            out.setdefault(obj["path"], set()).add(obj["sha256"])
        for v in obj.values():
            path_pins(v, out)
    elif isinstance(obj, list):
        for v in obj:
            path_pins(v, out)


def compare_pins(pins: dict, root) -> tuple:
    """(moved, unresolved) over {path: {sha256, ...}}: moved when the tree holds the path with none of the
    pinned hashes or does not hold it; unresolved when the path is machine-local and no tree can hold it."""
    moved, unresolved = [], []
    for rel, digests in sorted(pins.items()):
        if rel.startswith(UNRESOLVABLE_PREFIXES):
            unresolved.append(rel)
            continue
        target = Path(root) / rel
        if not target.is_file() or sha256_file(target) not in digests:
            moved.append(rel)
    return moved, unresolved


def check_campaign(root, bundles, campaign) -> dict:
    """One row: the pinned interpretation of `campaign` against the tree at `root`."""
    root, bundles = Path(root).resolve(), Path(bundles).resolve()
    zip_path, bundle = store.interpretation_zip(root, campaign, bundles)   # the pinned zip, verified (H10)
    manifest_member = f"{campaign}/{cb.SNAPSHOT_DIR}/{cb.SOURCE_MANIFEST}"
    if manifest_member not in bundle["listed"]:
        raise StaleError(f"{zip_path.name} lists no {manifest_member}: a derived bundle of the current kind "
                         f"({cb.DERIVED_KIND}) lays one; re-score and re-freeze")
    canon_inputs, pins = {}, {}
    with zipfile.ZipFile(zip_path) as z:
        files = json.loads(z.read(manifest_member)).get("files") or {}
        for name in bundle["listed"]:
            if not name.endswith(".json") or z.getinfo(name).file_size >= JSON_MEMBER_CAP:
                continue
            doc = json.loads(z.read(name))
            if name.endswith("/" + STAMP_NAME):
                for rel, digest in (doc.get("canon_inputs_sha256") or {}).items():
                    canon_inputs.setdefault(rel, set()).add(digest)
            path_pins(doc, pins)
    sources_moved = cb.source_differences(files, root)
    canon_moved, canon_unresolved = compare_pins(canon_inputs, root)
    # a pin that names a member of the bundle itself is the bundle's own business (self-contained, verified at
    # unpack, bound by the content id): counted, never checked here
    member_pins = {rel for rel in pins if rel in bundle["listed"]}
    pins_moved, pins_unresolved = compare_pins({rel: d for rel, d in pins.items() if rel not in member_pins}, root)
    git = (bundle["provenance"].get("source_snapshot") or {}).get("git") or {}
    return {"campaign": campaign, "backend": backend_of(root, campaign), "zip": zip_path.name,
            "content_id_sha256": bundle["content_id_sha256"], "frozen_commit": git.get("commit"),
            "sources": {"listed": len(files), "moved": sources_moved},
            "canon_inputs": {"listed": len(canon_inputs), "moved": canon_moved, "unresolved": canon_unresolved},
            "path_pins": {"listed": len(pins), "members": len(member_pins), "moved": pins_moved, "unresolved": pins_unresolved},
            "stale": bool(sources_moved or canon_moved or pins_moved)}


def check_store(root, bundles, campaigns=None) -> dict:
    """The report: one row per campaign with records (or the ones named), the stale ones listed."""
    root = Path(root).resolve()
    names = list(campaigns) if campaigns else store.campaigns_with_records(root)
    rows = [check_campaign(root, bundles, c) for c in names]
    return {"report_kind": REPORT_KIND, "repo_root": str(root), "bundles": str(Path(bundles).resolve()),
            "method": METHOD, "campaigns": rows, "stale": [r["campaign"] for r in rows if r["stale"]]}


def print_table(report: dict) -> None:
    rows = report["campaigns"]
    width = max([len(r["campaign"]) for r in rows] + [8])
    print(f"{'campaign':<{width}}  {'backend':<10}  {'frozen':<9}  {'sources':<16}  {'canon inputs':<16}  {'path pins':<16}  verdict")
    for r in rows:
        cells = [f"{r['sources']['listed']} moved {len(r['sources']['moved'])}",
                 f"{r['canon_inputs']['listed']} moved {len(r['canon_inputs']['moved'])}",
                 f"{r['path_pins']['listed']} moved {len(r['path_pins']['moved'])}"]
        print(f"{r['campaign']:<{width}}  {r['backend']:<10}  {(r['frozen_commit'] or '?')[:9]:<9}  {cells[0]:<16}  "
              f"{cells[1]:<16}  {cells[2]:<16}  {'STALE' if r['stale'] else 'ok'}")
        for label, key in (("source moved", "sources"), ("canon input moved", "canon_inputs"), ("path pin moved", "path_pins")):
            for rel in r[key]["moved"]:
                print(f"    {label}: {rel}")
        unresolved = r["canon_inputs"]["unresolved"] + r["path_pins"]["unresolved"]
        if unresolved:
            print(f"    unresolved here (machine-local, not counted): {len(unresolved)} e.g. {unresolved[:3]}")
    print(f"{len(rows)} interpretation(s) checked against {report['repo_root']}: {len(report['stale'])} stale"
          + (f" ({', '.join(report['stale'])}): re-stamp them" if report["stale"] else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", default=str(ROOT), help="the checkout the interpretations are checked against")
    ap.add_argument("--bundles", required=True, help="the archive directory holding the derived zips the records pin")
    ap.add_argument("--campaign", action="append", default=None, help="one campaign (repeatable); default every one with records")
    ap.add_argument("--out", default=None, help=f"write the rows as a {REPORT_KIND} report here")
    args = ap.parse_args(argv)
    try:
        report = check_store(args.repo_root, args.bundles, args.campaign)
    except ValueError as exc:    # StoreError, BundleError, StaleError and a declaration that does not load: refusals
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print_table(report)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
        print(f"report: {args.out}")
    return 1 if report["stale"] else 0


if __name__ == "__main__":
    sys.exit(main())
