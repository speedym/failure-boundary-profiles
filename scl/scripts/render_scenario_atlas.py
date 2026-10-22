#!/usr/bin/env python3
"""Render the paper's scenario family figure from the raw bundles' recordings (F100, 2026-10-21).

    conda run -n scl_prototype python scripts/render_scenario_atlas.py \\
        --bundles ~/scl/campaigns --out ~/scl/figures_paper_three_seeds_2026-10-19 [--spec SPEC] [--work DIR] [--no-render]

The spec (default artifacts/examples/figure_specs/scenario_atlas.json) names each panel's raw bundle
by campaign id and content id, the recording inside it, the second and the pixel window to cut, and
the rows. The tool refuses a bundle whose content id differs from the spec's, a member the bundle's
MANIFEST does not list or whose bytes do not hash to it, a second past the recording's end, a window
outside the frame, and a box without ffmpeg. Outputs under --out: <figure>.pdf/.png/.svg,
figure_data/<figure>.json (the panels with their bundle, member hash, second, window and panel hash;
the layout in inches; the lineage) and atlas_panels/<id>.png. The PDF's bytes depend only on these
inputs and the matplotlib version (SOURCE_DATE_EPOCH pinned), so scripts/check_paper_numbers.py
--render compares it with the paper's copy by hash like every other generated figure.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from scl_core.figures import atlas  # noqa: E402

DEFAULT_SPEC = REPO_ROOT / "artifacts/examples/figure_specs/scenario_atlas.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC, help="the figure_atlas/v0.1 spec")
    ap.add_argument("--bundles", type=Path, required=True, help="directory holding campaign_<id>.zip raw bundles")
    ap.add_argument("--out", type=Path, required=True, help="output directory (the paper render directory)")
    ap.add_argument("--work", type=Path, default=None, help="where the recordings are streamed to (default: a temporary directory)")
    ap.add_argument("--ffmpeg", default=None, help="the ffmpeg binary (default: the one on PATH)")
    ap.add_argument("--no-render", action="store_true", help="write figure_data and the panels only")
    a = ap.parse_args(argv)
    work_ctx = tempfile.TemporaryDirectory(prefix="scenario_atlas_") if a.work is None else None
    work = Path(work_ctx.name) if work_ctx else a.work
    try:
        data = atlas.build(a.spec, a.bundles, a.out, work, REPO_ROOT, Path(__file__), a.ffmpeg)
    except atlas.AtlasError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    finally:
        if work_ctx:
            work_ctx.cleanup()
    for p in data["panels"]:
        print(f"  ({p['id']}) {p['label']:<28} {p['campaign_id']}  {p['member'].split('/')[-1][:60]}  t={p['time_s']} s  "
              f"{p['panel_size'][0]}x{p['panel_size'][1]}  {p['panel_sha256'][:12]}")
    lay = data["layout"]
    for row in lay["rows"]:
        print(f"  row {row['ids']}: height {row['height_in'] * 2.54:.2f} cm, widths " +
              ", ".join(f"{w * 2.54:.2f}" for w in row["widths_in"]) + " cm")
    print(f"  figure {lay['text_width_in'] * 2.54:.2f} x {lay['total_height_in'] * 2.54:.2f} cm; {data['lineage']['ffmpeg']}")
    if not a.no_render:
        for path in atlas.render(data, a.out):
            print(f"  wrote {path}")
    print(f"  wrote {a.out / 'figure_data' / (data['figure'] + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
