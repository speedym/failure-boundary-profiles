"""The matplotlib renderer of the profile grid and the case-study graph (scl_core.figures.profiles builds
the data). Two designs over one figure data: `asis`, the paper's generated pgfplots figures as they
stand (steps, orange gap cells, grey blocked widths, a dashed line at each located boundary, numbers in
the subtitle only); `hybrid`, the same layout with the estimator's own marks kept where they add
information: the boundary diamond at p = 0.5, its interval as a light band (the bracket between
adjacent strata, else the bootstrap interval) and the converged logistic drawn thin.

Presentation constants are the paper's: the document's Times (Nimbus Roman, the URW font the CVPR
template embeds), tick labels at 5 pt and titles at 7 pt (LaTeX's tiny and scriptsize at 10 pt),
black commitment, blue (31, 90, 170) competence, orange!45 at 0.8 for the gap, gray!18 for the blocked
widths, gray!35 for an excluded defect stratum, gray!70 axis lines; panels 0.19 x 0.12 of the 7.0 in
text width (the warehouse 0.27 x 0.11, three columns). The text width is the unit of the panel sizes,
not the canvas: the canvas is as wide as the grid it holds, the left margin plus the columns plus a
right margin that mirrors the clearance the labels leave on the left, so the ink is centred in the
page box and the paper places each figure at its natural size (2026-10-17: a two-column grid on a
text-width canvas had left a fifth of the box blank on the right, and LaTeX centred the box, not
the ink). A row label (the warehouse grid names its rows) stands in the left margin, left of the y
label, and the right margin mirrors the blank that remains beside it (same day: anchored at a third
of the axes width to the left, it stood outside the canvas and was clipped in every paper PDF). The
case study's column-width canvas follows the same rule with its own label allowance.
"""
from __future__ import annotations

import math

BLUE = "#1f5aaa"
GAP = (1.0, 0.775, 0.55)        # orange!45
GAP_ALPHA = 0.8
BLOCKED = "#d1d1d1"             # gray!18
DEFECT = "#a6a6a6"              # gray!35
AXIS_GRAY = "#4d4d4d"           # gray!70
TEXT_WIDTH_IN = 7.0             # the CVPR two-column text width, the unit of the panel sizes
LEFT_IN = {False: 0.42, True: 0.55}       # the left margin by layout (road, warehouse): tick labels, the y label
SEP_IN = {False: 10 / 72, True: 12 / 72}  # the gap between columns by layout
Y_LABELS_IN = 0.22              # what the first column's tick labels and y label take of the left margin at
                                # 5 pt; the right margin is the clearance that remains, LEFT_IN - Y_LABELS_IN
ROW_LABEL_IN = 0.14             # what a row label takes of that clearance, left of the y label: its 6 pt bold
                                # line and the gap to the y label
COLUMN_WIDTH_IN = 3.25
CS_LEFT_IN = 0.455              # the case study's left margin: its two y labels at 7 pt and the tick labels
CS_LABELS_IN = 0.36             # what those take of it; the right margin is the clearance that remains
PAIR_COLORS = ("#1f5aaa", "#c8322d")   # the case study's two agents, his cTF and cBD
PAPER_RC = {"font.family": "serif", "font.serif": ["Nimbus Roman", "Times New Roman", "Times", "DejaVu Serif"],
            "mathtext.fontset": "stix", "font.size": 7, "axes.linewidth": 0.5, "xtick.major.width": 0.5,
            "ytick.major.width": 0.5, "xtick.major.size": 2, "ytick.major.size": 2, "axes.edgecolor": AXIS_GRAY,
            "xtick.color": AXIS_GRAY, "ytick.color": AXIS_GRAY, "xtick.labelcolor": "black", "ytick.labelcolor": "black",
            "svg.hashsalt": "scl", "pdf.compression": 9, "pdf.fonttype": 42, "svg.fonttype": "none"}
DESIGNS = ("asis", "hybrid", "marks")   # marks (2026-10-22): a located boundary is a diamond at p = 0.5 with its
# interval as a horizontal whisker, never a full-height line; the checkpoint traces' diamonds are hollow


def mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update(PAPER_RC)
    return plt


def steps(spec: dict, strata: list) -> tuple:
    """x and rate per family stratum, NaN where this trace has no counted run (a break in the line)."""
    at = {o[0]: o[1] for o in spec.get("observed") or []}
    return list(strata), [at.get(x, math.nan) for x in strata]


COMMITMENT_LW = {True: 1.5, False: 0.75}   # the black curve is drawn wider and UNDER the blue one (2026-10-22): where the
COMPETENCE_LW = {True: 0.9, False: 0.45}   # two coincide the reader sees blue with a black rim, never one colour alone
Z_COMMITMENT, Z_COMPETENCE = 3.0, 3.2      # blue above black in every panel, the checkpoint traces included
Z_REFERENCE = 2.5                          # the body-width line above every shaded area, below the curves: one shade everywhere


def _trace(ax, spec, color, solid: bool, design: str, strata: list):
    xs, ys = steps(spec, strata)
    commitment = color == "black"
    ax.step(xs, ys, where="mid", color=color, lw=(COMMITMENT_LW if commitment else COMPETENCE_LW)[solid],
            ls="-" if solid else (0, (1.0, 1.6)), zorder=Z_COMMITMENT if commitment else Z_COMPETENCE)
    b = spec.get("boundary")
    if design == "marks":
        if b is not None:   # the located boundary as a diamond at p = 0.5; the interval stays in the panel title
            ax.plot([b], [0.5], "D", ms=3.2 if solid else 2.6, mfc=color if solid else "white", mec="white" if solid else color,
                    mew=0.4 if solid else 0.6, zorder=5)
        return
    if b is not None:
        ax.axvline(b, color=color, ls=(0, (2.2, 1.3)), lw=0.5, zorder=2)
    if design == "hybrid" and solid and spec.get("kind") not in (None, "none", "flat"):
        band = [v for v in (spec.get("band") or []) if v is not None]
        if len(band) != 2:
            band = spec.get("band_ci") or []
        if len(band) == 2 and band[1] != band[0]:
            ax.axvspan(min(band), max(band), color=color, alpha=0.10, lw=0, zorder=1)
        if spec.get("curve"):
            cx, cy = zip(*spec["curve"])
            ax.plot(cx, cy, color=color, lw=0.6, alpha=0.9, zorder=3)
        if b is not None:
            ax.plot([b], [0.5], "D", color=color, ms=3.4, mec="white", mew=0.3, zorder=5)


def _panel(ax, panel: dict, d: dict, design: str, first_column: bool):
    ax.set_xlim(d["axis"])
    ax.set_ylim(-0.05, 1.05)
    ax.set_yticks([0, 0.5, 1])
    ax.tick_params(labelsize=5, pad=1.5)
    ax.set_xlabel(f"{d['axis_metric'].replace('_', ' ')} ({d['unit']})" if not d.get("axis_label") else d["axis_label"],
                  fontsize=5, labelpad=1.5)
    if first_column:
        ax.set_ylabel("probability", fontsize=5, labelpad=1.5)
    else:
        ax.tick_params(labelleft=False)
    ax.set_title(panel["label"], fontsize=7, fontweight="bold", pad=15)
    ax.text(0.5, 1.03, "\n".join(panel.get("subtitle") or []), transform=ax.transAxes, ha="center", va="bottom",
            fontsize=5, linespacing=1.15)
    if panel.get("status") != "drawn":
        ax.text(0.5, 0.5, "no runs", transform=ax.transAxes, ha="center", va="center", fontsize=5, color=AXIS_GRAY)
        return
    for lo, hi in (d.get("floor_range") or panel["blocked"]):
        ax.axvspan(lo, hi, color=BLOCKED, lw=0, zorder=0)
    for lo, hi in panel["defect"]:
        ax.axvspan(lo, hi, color=DEFECT, lw=0, zorder=0)
    for key in ("body_limit_m", "contact_width_m"):
        if d["refs"].get(key) is not None:
            ax.axvline(d["refs"][key], color=AXIS_GRAY, ls=(0, (3, 2)), lw=0.5, zorder=Z_REFERENCE)
    from matplotlib.patches import Rectangle
    for lo, hi, y0, y1 in panel["gap_cells"]:
        ax.add_patch(Rectangle((lo, y0), hi - lo, y1 - y0, facecolor=GAP, alpha=GAP_ALPHA, lw=0, zorder=1))
    strata = panel.get("strata") or d["strata"]
    for t in panel["traces"]:
        solid = t["style"] == "solid"
        if design == "marks" and not solid:
            continue   # the checkpoint group's first member is drawn; the others' boundaries stay in the panel title (2026-10-22)
        _trace(ax, t["commitment"], "black", solid, design, strata)
        _trace(ax, t["competence"], BLUE, solid, design, strata)


def _legend(ax, d: dict, design: str, below: bool = False):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    ax.axis("off")
    handles, labels = [], []
    glyphs = {"commitment P(go)": Line2D([], [], color="black", lw=COMMITMENT_LW[True]),
              "competence P(clean pass)": Line2D([], [], color=BLUE, lw=COMPETENCE_LW[True]),
              "commits but not clean": Patch(facecolor=GAP, alpha=GAP_ALPHA, lw=0),
              "boundary (p = 0.5), where located": Line2D([], [], color="black", ls=(0, (2.2, 1.3)), lw=0.5),
              "widths certified blocked": Patch(facecolor=BLOCKED, lw=0),
              "scenario defect, excluded": Patch(facecolor=DEFECT, lw=0),
              "body width, dashed grey": Line2D([], [], color=AXIS_GRAY, ls=(0, (3, 2)), lw=0.5),
              "robot contact width, dashed grey": Line2D([], [], color=AXIS_GRAY, ls=(0, (3, 2)), lw=0.5)}
    shown = {}
    if design == "marks":
        glyphs["boundary (p = 0.5), where located"] = Line2D([], [], color="black", marker="D", ms=3.2, lw=0, mec="white", mew=0.4)
        shown["checkpoint 1 solid, 2 and 3 dotted"] = "PlanT 2.0: checkpoint 1 drawn,\ncheckpoints 2 and 3 in the title"
    notes = []
    for line in d["legend"]:
        if line in glyphs:
            handles.append(glyphs[line]); labels.append(line)
        else:
            notes.append(shown.get(line, line))
    if design == "hybrid":
        handles.append(Line2D([], [], color=BLUE, marker="D", ms=3.4, lw=0, mec="white", mew=0.3))
        labels.append("boundary and its interval (band)")
    if below:
        # up to four entries in one row; more wrap into two rows of equal width, so the legend stays inside the
        # grid's width on the two-column layout (the contact-width entry made a four-column row overflow, 2026-10-22)
        ncol = len(handles) if len(handles) <= 4 else math.ceil(len(handles) / 2)
        ax.legend(handles, labels, loc="upper left", fontsize=5, frameon=False, handlelength=1.6, borderaxespad=0,
                  bbox_to_anchor=(0.0, 1.0), ncol=ncol, columnspacing=1.2, handletextpad=0.5)
        ax.text(0.0, 0.0, "; ".join(n.replace("\n", " ") for n in notes) + ".", transform=ax.transAxes, ha="left", va="bottom", fontsize=5)
        return
    ax.legend(handles, labels, loc="upper left", fontsize=4.8, frameon=False, handlelength=1.6, borderaxespad=0,
              bbox_to_anchor=(0.0, 1.05), labelspacing=0.35, handletextpad=0.5)
    ax.text(0.0, -0.1, "\n".join(notes), transform=ax.transAxes, ha="left", va="top", fontsize=4.6, linespacing=1.15)


def right_margin_in(warehouse: bool, row_labels: bool) -> float:
    """The right margin: the blank the first column's labels, and the row labels where the grid has them,
    leave on the left of the canvas, so the ink is centred."""
    return LEFT_IN[bool(warehouse)] - Y_LABELS_IN - (ROW_LABEL_IN if row_labels else 0.0)


def canvas_width_in(cols: int, warehouse: bool, row_labels: bool = False) -> float:
    """The canvas width that holds a grid of `cols` columns: the left margin, the columns with their gaps,
    and the right margin that mirrors the blank on the left."""
    aw = 0.27 if warehouse else 0.19
    axes_w = aw * TEXT_WIDTH_IN
    left, sep = LEFT_IN[bool(warehouse)], SEP_IN[bool(warehouse)]
    return left + cols * axes_w + (cols - 1) * sep + right_margin_in(warehouse, row_labels)


def render_profiles(d: dict, out_dir, name: str, design: str = "hybrid"):
    """The grid: the panels in order, the legend in the cell after the last panel (or below the grid when
    the grid is full), optional outcome strips under the panels (d['strip'])."""
    if design not in DESIGNS:
        raise ValueError(f"design is one of {DESIGNS}, got {design!r}")
    plt = mpl()
    panels = d["panels"]
    cols = d["layout"]["columns"]
    warehouse = d["layout"].get("warehouse")
    strip = d.get("strip")
    n_cells = len(panels) + (0 if warehouse and len(panels) % cols == 0 else 1)
    rows = math.ceil(n_cells / cols)
    aw, ah = (0.27, 0.11) if warehouse else (0.19, 0.12)
    axes_w, axes_h = aw * TEXT_WIDTH_IN, ah * TEXT_WIDTH_IN
    strip_h = 0.45 if strip else 0.0
    row_pitch = axes_h + strip_h + 0.72
    legend_below = n_cells == len(panels)
    row_labels = any(p.get("row_label") for p in panels)
    fig_w = canvas_width_in(cols, bool(warehouse), row_labels)
    fig_h = rows * row_pitch + 0.2 + (0.45 if legend_below else 0) + (0.2 if strip else 0)
    fig = plt.figure(figsize=(fig_w, fig_h))
    left_in = LEFT_IN[bool(warehouse)]
    left = left_in / fig_w
    right = right_margin_in(bool(warehouse), row_labels) / fig_w
    sep = SEP_IN[bool(warehouse)] / fig_w
    width = axes_w / fig_w
    top_pad = 0.48 / fig_h
    for i, panel in enumerate(panels):
        r, c = divmod(i, cols)
        x0 = left + c * (width + sep)
        y_top = 1 - top_pad - r * (row_pitch / fig_h)
        ax = fig.add_axes([x0, y_top - axes_h / fig_h, width, axes_h / fig_h])
        _panel(ax, panel, d, design, first_column=(c == 0))
        if c == 0 and panel.get("row_label"):
            # in the left margin, left of the y label; the label's box starts where the blank margin ends
            fig.text((left_in - Y_LABELS_IN - ROW_LABEL_IN) / fig_w, y_top - axes_h / (2 * fig_h), panel["row_label"],
                     rotation=90, fontsize=6, fontweight="bold", ha="left", va="center")
        if strip and panel.get("composition"):
            bx = fig.add_axes([x0, y_top - (axes_h + strip_h + 0.08) / fig_h, width, (strip_h - 0.08) / fig_h], sharex=ax)
            _strip(bx, panel["composition"], strip, d, first_column=(c == 0))
            ax.tick_params(labelbottom=False)
            ax.set_xlabel("")
    i = len(panels)
    if n_cells > len(panels):
        r, c = divmod(i, cols)
        x0 = left + c * (width + sep)
        y_top = 1 - top_pad - r * (row_pitch / fig_h)
        lax = fig.add_axes([x0, y_top - axes_h / fig_h, width, axes_h / fig_h])
        _legend(lax, d, design)
    else:
        lax = fig.add_axes([left, (0.05 + (0.2 if strip else 0)) / fig_h, 1 - left - right, 0.4 / fig_h])
        _legend(lax, d, design, below=True)
    if strip:
        _strip_legend(fig, strip, panels)
    _save(fig, out_dir, name)


def _strip(bx, composition, strip, d, first_column):
    xs = [c["x"] for c in composition]
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    width = 0.55 * min(gaps) if gaps else 0.05
    bottoms = [0.0] * len(composition)
    for oc in strip["order"]:
        vals = [c["outcomes"].get(oc, 0) / c["n"] if c["n"] else 0 for c in composition]
        if not any(vals):
            continue
        bx.bar(xs, vals, width=width, bottom=bottoms, color=strip["colors"][oc], edgecolor="white", linewidth=0.2)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    for key in ("body_limit_m", "contact_width_m"):
        if d["refs"].get(key) is not None:
            bx.axvline(d["refs"][key], color=AXIS_GRAY, ls=(0, (3, 2)), lw=0.5, zorder=1)
    bx.set_ylim(0, 1)
    bx.set_yticks([0, 1])
    bx.tick_params(labelsize=5, pad=1.5)
    bx.set_xlabel(f"{d['axis_metric'].replace('_', ' ')} ({d['unit']})" if not d.get("axis_label") else d["axis_label"],
                  fontsize=5, labelpad=1.5)
    if first_column:
        bx.set_ylabel("outcome", fontsize=5, labelpad=1.5)
    else:
        bx.tick_params(labelleft=False)


def _strip_legend(fig, strip, panels):
    from matplotlib.patches import Patch
    present = [oc for oc in strip["order"]
               if any(c["outcomes"].get(oc) for p in panels for c in p.get("composition") or [])]
    fig.legend(handles=[Patch(color=strip["colors"][oc], label=strip.get("labels", {}).get(oc, oc.replace("_", " ")))
                        for oc in present],
               fontsize=5, loc="lower center", ncol=min(len(present), 6), frameon=False, bbox_to_anchor=(0.5, 0.0))


def render_case_study(d: dict, out_dir, name: str, design: str = "hybrid"):
    """Figure 2's design: the driving score per run (dots) and its mean per width (lines) above the
    commitment rate per width, the circled pair, the body-width and floor lines, the located boundary
    as a diamond with its bootstrap interval; the hybrid adds the converged logistic and the bracket."""
    if design not in DESIGNS:
        raise ValueError(f"design is one of {DESIGNS}, got {design!r}")
    plt = mpl()
    pair = list(d["ds_points"])
    labels = d.get("labels") or {m: m for m in pair}
    colors = dict(zip(pair, PAIR_COLORS))
    fig = plt.figure(figsize=(COLUMN_WIDTH_IN, 0.42 * COLUMN_WIDTH_IN * 2 + 0.95))
    left = CS_LEFT_IN / COLUMN_WIDTH_IN
    right = 1 - (CS_LEFT_IN - CS_LABELS_IN) / COLUMN_WIDTH_IN    # the blank on the left, mirrored
    gs = fig.add_gridspec(2, 1, hspace=0.08, left=left, right=right, top=0.98, bottom=0.21)
    top = fig.add_subplot(gs[0]); bot = fig.add_subplot(gs[1], sharex=top)
    lo, hi = d["x_range"]
    pad = 0.03 * (hi - lo)
    for ax, ymax in ((top, 105), (bot, 1.05)):
        for key, ls in (("body_limit_m", "-"), ("policy_floor_m", (0, (3, 2)))):
            if d["refs"].get(key) is not None:
                ax.axvline(d["refs"][key], color="black", ls=ls, lw=0.6, zorder=1)
        ax.tick_params(labelsize=7, pad=2)
    for model in pair:
        pts = d["ds_points"][model]
        top.plot([p[0] for p in pts], [p[1] for p in pts], "o", color=colors[model], ms=1.6, alpha=0.35, lw=0, zorder=2)
        mean = d["ds_mean_by_x"][model]
        top.plot([p[0] for p in mean], [p[1] for p in mean], "-", color=colors[model], lw=1.0, zorder=3,
                 label=f"{labels[model]} (mean {d['mean_ds'][model]})")
    cell = d.get("inversion_cell")
    if cell:
        for model in pair:
            c = cell.get(model)
            if c:
                top.plot([c["x"]], [c["ds"]], "o", ms=5.5, mfc="none", mec="black", mew=0.6, zorder=5)
    top.set_ylim(0, 105)
    top.set_ylabel("driving score", fontsize=7)
    top.tick_params(labelbottom=False)
    for model in pair:
        spec = d["p_go"][model]
        obs = sorted(spec.get("observed") or [])
        xs, ys = [o[0] for o in obs], [o[1] for o in obs]
        bot.plot(xs, ys, "-", color=colors[model], lw=1.0, marker="o", ms=1.8, zorder=3)
        b = spec.get("boundary")
        if b is not None:
            ci = spec.get("band_ci")
            if ci and ci[0] != ci[1]:
                bot.errorbar([b], [0.5], xerr=[[b - min(ci)], [max(ci) - b]], fmt="none", ecolor=colors[model],
                             elinewidth=0.9, capsize=2.5, capthick=0.9, zorder=4)
            if design == "hybrid":
                band = [v for v in (spec.get("band") or []) if v is not None]
                if len(band) == 2 and band[0] != band[1]:
                    bot.axvspan(min(band), max(band), color=colors[model], alpha=0.10, lw=0, zorder=1)
                if spec.get("curve"):
                    cx, cy = zip(*spec["curve"])
                    bot.plot(cx, cy, color=colors[model], lw=0.6, alpha=0.9, zorder=3)
            bot.plot([b], [0.5], "D", color=colors[model], ms=4.5, zorder=5)
    bot.set_ylim(-0.05, 1.05)
    bot.set_yticks([0, 0.5, 1])
    bot.set_ylabel("commitment P(go)", fontsize=7)
    bot.set_xlabel(f"{d['axis_metric'].replace('_', ' ')} (m)", fontsize=7)
    bot.set_xlim(lo - pad - 0.05, hi + pad)
    fig.legend(*top.get_legend_handles_labels(), loc="lower center", ncol=2, frameon=False, fontsize=7,
               bbox_to_anchor=((left + right) / 2, 0.0))
    _save(fig, out_dir, name)


def _save(fig, out_dir, name):
    for ext in ("pdf", "png", "svg"):
        fig.savefig(out_dir / f"{name}.{ext}", dpi=200)
    import matplotlib.pyplot as plt
    plt.close(fig)
