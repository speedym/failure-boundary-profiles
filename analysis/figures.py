"""Per-family boundary-curve figures (public, MIT). Requires matplotlib.

Renders one PNG per family: commitment and competence panels, observed
per-x rates (marker area ~ n) per model, with the fitted layer drawn
according to each curve's method - never beyond what the method claims:

    flat          horizontal level line + Wilson band; no boundary drawn.
    bracket       shaded x-band between the bracket edges + midpoint line.
    irls          logistic curve from (slope, boundary); bootstrap CI as a
                  light x-band when present.
    irls_guarded  midpoint step + bracket band; NO curve (slope
                  unidentifiable by policy).

    python -m analysis.figures --scored <scored_dir> [...] --stats <stats_dir> --out figs/
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from .boundary_stats import load_rows
from .curves import FAMILY_METRIC, observed_rates

# display names follow the naming canon used in the paper's materials
DISPLAY_NAMES = {"lead": "TFv6", "tfpp": "TF++", "bridgedrive": "BridgeDrive",
                 "pdm_lite_expert": "PDM-Lite expert"}
COLORS = ["#16657d", "#c2542e", "#5a7a3f", "#7d5a94", "#8a6d1f", "#4a4a4a"]


def sigmoid(v):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, v))))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scored", nargs="+", type=Path, required=True)
    ap.add_argument("--stats", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("figs"))
    args = ap.parse_args()

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is required for figures "
              "(pip install matplotlib)", file=sys.stderr)
        return 1

    rows = load_rows(args.scored)
    stats = [json.loads(line)
             for line in (args.stats / "stats_rows.jsonl").open()]
    stats = [s for s in stats if s.get("curve")]
    args.out.mkdir(parents=True, exist_ok=True)

    by_family = defaultdict(list)
    for s in stats:
        by_family[s["family"]].append(s)

    for family, fstats in sorted(by_family.items()):
        metric = FAMILY_METRIC.get(family)
        if metric is None:
            continue
        models = sorted({s["model"] for s in fstats})
        fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
        for ax, curve in zip(axes, ("commitment", "competence")):
            for ci_m, model in enumerate(models):
                color = COLORS[ci_m % len(COLORS)]
                label = DISPLAY_NAMES.get(model, model)
                pts = observed_rates(rows, family, model, curve, metric)
                if pts:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    ns = [p[2] for p in pts]
                    ax.scatter(xs, ys, s=[14 + 9 * n for n in ns],
                               color=color, alpha=0.75, label=label,
                               edgecolors="none", zorder=3)
                s = next((s for s in fstats
                          if s["model"] == model and s["curve"] == curve), None)
                if s is None:
                    continue
                if s["method"] == "flat":
                    ax.axhline(s["level"], color=color, lw=1.4, ls=":",
                               alpha=0.8, zorder=2)
                    if s.get("level_ci"):
                        ax.axhspan(s["level_ci"][0], s["level_ci"][1],
                                   color=color, alpha=0.06, zorder=1)
                    continue
                blo = s.get("bracket_fail_side_x")
                bhi = s.get("bracket_pass_side_x")
                if blo is not None and bhi is not None:
                    lo, hi = sorted((blo, bhi))
                    ax.axvspan(lo, hi, color=color, alpha=0.08, zorder=1)
                if s["method"] in ("bracket", "irls_guarded") \
                        and s.get("boundary") is not None:
                    ax.axvline(s["boundary"], color=color, lw=1.4, ls="--",
                               alpha=0.85, zorder=2)
                if s["method"] == "irls" and s.get("slope") is not None \
                        and s.get("boundary") is not None:
                    orient = s.get("orientation", 1)
                    x0 = min(p[0] for p in pts) if pts else s["boundary"] - 1
                    x1 = max(p[0] for p in pts) if pts else s["boundary"] + 1
                    span = (x1 - x0) or 1.0
                    grid = [x0 - 0.05 * span + i * 1.1 * span / 199
                            for i in range(200)]
                    ys_f = [sigmoid(s["slope"] * orient * (x - s["boundary"]))
                            for x in grid]
                    ax.plot(grid, ys_f, color=color, lw=1.6, zorder=2)
                boot = s.get("bootstrap")
                if boot and boot.get("ci"):
                    ax.axvspan(boot["ci"][0], boot["ci"][1], color=color,
                               alpha=0.05, zorder=0)
            ax.set_title(f"{curve}")
            ax.set_xlabel(metric.replace("_", " ") + " (m)")
            ax.set_ylim(-0.05, 1.05)
            ax.grid(alpha=0.2)
        axes[0].set_ylabel("observed rate")
        axes[0].legend(loc="best", fontsize=9)
        fig.suptitle(family)
        fig.tight_layout()
        out = args.out / f"{family}.png"
        fig.savefig(out, dpi=130)
        plt.close(fig)
        print(out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
