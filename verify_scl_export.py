#!/usr/bin/env python3
"""Verify the exported measurement chain against its own manifest: every listed file present with its
sha256, no stray file.

This file belongs to the public repository, not to the export: scl/ and scl_families/ are generated and
pruned by the private pipeline's export, this script checks them from outside. Run from anywhere; the
repository root is this script's directory:

    python3 verify_scl_export.py              # from the repository's root
    python3 verify_scl_export.py --root DIR   # another checkout

Reports, for the scl/ tree and the generated scl_families/ view: stale (bytes differ from the manifest),
missing, stray (a file under scl/ the manifest does not list, apart from the manifest, the proof and the
README); then the private commit the export was made at, whether the exported files were that commit's
bytes, and the campaigns scl/PUBLIC_PROOF.json says were proven from this tree. Exit 0 when nothing is
stale, missing or stray, 1 otherwise, 2 when the manifest cannot be read. Standard library only, Python
3.8 or newer (2026-10-09: the README's inline snippet became this tool).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

KEPT = ("PUBLIC_EXPORT.json", "PUBLIC_PROOF.json", "README.md")   # the three files scl/ holds beside the set


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(listed, base):
    """(stale, missing) over {relative path: sha256} under base."""
    stale, missing = [], []
    for rel, digest in sorted(listed.items()):
        p = base / rel
        if not p.is_file():
            missing.append(rel)
        elif sha256(p) != digest:
            stale.append(rel)
    return stale, missing


def verify(root):
    scl = root / "scl"
    manifest = json.loads((scl / "PUBLIC_EXPORT.json").read_text())
    prefix = manifest.get("prefix", "scl")
    stale, missing = check(manifest["files"], root / prefix)
    present = {p.relative_to(root / prefix).as_posix() for p in (root / prefix).rglob("*") if p.is_file()}
    stray = sorted(present - set(manifest["files"]) - set(KEPT))
    view_stale, view_missing, view_stray = [], [], []
    view = manifest.get("scl_families") or {}
    if view:
        view_stale, view_missing = check(view, root)
        view_root = root / next(iter(view)).split("/")[0]
        if view_root.is_dir():
            view_stray = sorted({p.relative_to(root).as_posix() for p in view_root.rglob("*") if p.is_file()} - set(view))
    return {"manifest": manifest, "stale": stale + view_stale, "missing": missing + view_missing,
            "stray": stray + view_stray}


def main():
    ap = argparse.ArgumentParser(description="Verify the exported measurement chain against its manifest.")
    ap.add_argument("--root", type=Path, default=None,
                    help="the repository root holding scl/ and scl_families/ (default: this script's directory)")
    args = ap.parse_args()
    root = args.root.resolve() if args.root else Path(__file__).resolve().parent
    try:
        r = verify(root)
    except (OSError, ValueError, KeyError) as exc:
        print("cannot read the export manifest under {}: {}".format(root / "scl", exc), file=sys.stderr)
        return 2
    m = r["manifest"]
    for key in ("stale", "missing", "stray"):
        items = r[key]
        print("{}: {}".format(key, "none" if not items else "{} e.g. {}".format(len(items), items[:5])))
    src = m.get("source", {})
    print("export: {} files under {}/, {} under the family view, private commit {} ({})".format(
        m.get("n_files"), m.get("prefix", "scl"), len(m.get("scl_families") or {}), str(src.get("commit", "?"))[:12],
        "the commit's bytes" if src.get("clean") else "NOT the commit's bytes"))
    proof_path = root / "scl" / "PUBLIC_PROOF.json"
    if proof_path.is_file():
        proof = json.loads(proof_path.read_text())
        same = proof.get("export_commit") == src.get("commit")
        print("proof: at export commit {}{}".format(str(proof.get("export_commit", "?"))[:12],
                                                     "" if same else " (NOT this export's commit)"))
        for campaign, row in sorted((proof.get("campaigns") or {}).items()):
            print("  {}: {} {} members {}/{}{}".format(
                campaign, "PROVEN" if row.get("proven") else "NOT PROVEN", (row.get("verdict") or "").replace("verdict: ", ""),
                row.get("members_identical"), row.get("members_total"),
                ", wrote into the clone" if row.get("wrote_into_clone") else ""))
    else:
        print("proof: no scl/PUBLIC_PROOF.json")
    return 0 if not (r["stale"] or r["missing"] or r["stray"]) else 1


if __name__ == "__main__":
    sys.exit(main())
