#!/usr/bin/env python3
"""Prune the interpretation cache: remove every entry no certification record pins (F72).

The results store (scl_core/results_store.py) unpacks a campaign's interpretation once, into
`<archive>/interpretations/<content id>/` beside the zips (or the cache the caller names), keyed by
the derived zip's content id and verified member by member on the way. A re-stamp pins a new id in
the records; the old entry stays correct for its own id but nothing reads it any more, and a half
unpack (no marker) is refused by every reader. This removes both and prints what it removed;
anything removed is re-creatable on demand from the archive, its superseded folders included.
`scl campaign publish` runs it after the records move; `scl campaign prune-cache` is the verb.
Stdlib-only by contract: it runs on whatever python3 the launcher runs on.

    scl campaign prune-cache [--archive DIR] [--cache DIR]
    python3 -B -E -s scripts/prune_interpretation_cache.py [--repo-root R] [--archive DIR] [--cache DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import results_store as store  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", type=Path, default=ROOT, help="the checkout holding the certification records")
    ap.add_argument("--archive", type=Path, default=None, help="the archive directory (default: SCL_CAMPAIGN_BUNDLES or ~/scl/campaigns)")
    ap.add_argument("--cache", type=Path, default=None, help="the cache to prune (default: <archive>/interpretations)")
    args = ap.parse_args(argv)
    report = store.prune(args.repo_root.resolve(), args.archive, args.cache)
    freed = sum(r["bytes"] for r in report["removed"])
    print(f"cache {report['cache']}: kept {len(report['kept'])} pinned interpretation(s), removed "
          f"{len(report['removed'])} ({freed / 1e6:.1f} MB)")
    for r in report["removed"]:
        print(f"  removed {r['id'][:12]}: {r['reason']} ({r['bytes'] / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
