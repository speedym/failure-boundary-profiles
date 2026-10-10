#!/usr/bin/env python3
"""Isaac paper figure generator (mirror of
adapters/fail2drive/scripts/multi_model_eval/paper_figures.py): the
three-family picture from FindingsSummary v0.2 documents.

Two-layer output, by design (the CARLA discipline):
  figure_data/FI1.json  the exact numbers plotted (series, bands, fits,
                        composition, reference lines) with lineage -
                        deterministic, matplotlib-free, committed-golden
                        material. THE AUDITABLE LAYER.
  figures/FI1.{pdf,png,svg}  matplotlib renders consuming ONLY the
                        figure_data dict. Presentation, never truth.

    conda run -n scl_prototype python \\
        adapters/isaac/scripts/multi_model_eval/paper_figures.py \\
        --findings artifacts/examples/findings/*.v0.2.json \\
        --instrument artifacts/examples/scoring_instruments/isaac_aisle_gauntlet_two_line_go.json \\
        --robot-record artifacts/examples/imported_ir/robot_record_nova_carter.json \\
        --out <dir> [--no-render]

INPUT CONTRACT (S17): every findings document must be
findings_summary/v0.2 produced by the CURRENT instruments - its
provenance.estimator_sha256 must equal scl_core/stats/boundary.py at
this checkout and its emitter sha must equal findings_v2.py; otherwise
the builder REFUSES (figure data never consumes tables that outlived
their instrument).

FIGURE POLICY (shared with the CARLA generator)
- A curve panel draws the DATA (observed per-width rates as a step line
  with dots, Wilson bands) and only what the fit method produced:
  flat -> level line + Wilson fill + "no boundary in tested range";
  bracket -> the estimator's interval band (the bootstrap CI stays in the data layer; a reason names the case when
  active) + the boundary diamond, NEVER a curve through separated
  data; irls -> the logistic curve sampled in the data layer.
- Reference lines are DERIVED at build time from the declared scoring
  instrument and the robot record (scl_core/certification_band.py): the
  robot contact width (the record's pinch sweep) and the certification
  dead band from the instrument's eps and grid resolution. Never
  hardcoded; until 2026-10-08 read from the capability profile.
- Colors are fixed per family and per outcome (presentation map).
- Deterministic renders: SOURCE_DATE_EPOCH pinned.
"""
import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
from scl_core import family_versions as fv  # noqa: E402  (the family version registry: a label over the manifest hash)
from scl_core.certification_band import certification  # noqa: E402  (the reference band: one formula, one place)

ESTIMATOR = REPO_ROOT / "scl_core/stats/boundary.py"
EMITTER = Path(__file__).resolve().parent / "findings_v2.py"

FAMILY_ORDER = ["fm_isaac_001", "fm_isaac_003", "fm_isaac_004"]
FAMILY_LABELS = {"fm_isaac_001": "Slab arms",
                 "fm_isaac_003": "Floor stock",
                 "fm_isaac_004": "Composed stacks"}
STYLE = {
    "family_colors": {"fm_isaac_001": "#1f77b4", "fm_isaac_003": "#ff7f0e",
                      "fm_isaac_004": "#d62728"},
    "curve_styles": {"passage": {"ls": "-", "lw": 1.8},
                     "p_go": {"ls": "--", "lw": 1.4},
                     "p_clean": {"ls": ":", "lw": 1.4}},
    "curve_labels": {"passage": "passage P(threaded clean)",
                     "p_go": "commitment P(go)",
                     "p_clean": "mission P(clean pass)"},
    "outcome_colors": {"commit_clean": "#2ca02c", "divert_clean": "#98df8a",
                       "timeout_clean": "#c7c7c7", "timeout_contact": "#d62728",
                       "commit_contact": "#ff9896", "divert_contact": "#e377c2",
                       "anomaly_blockage_completed": "#444444"},
    "outcome_order": ["commit_clean", "divert_clean", "timeout_clean",
                      "timeout_contact", "commit_contact", "divert_contact",
                      "anomaly_blockage_completed"],
    # the CARLA tool's typeset form of the one shared anomaly class (2026-10-03)
    "outcome_labels": {"anomaly_blockage_completed": "blocked width completed"},
    "ref_width": {"ls": "-", "color": "#333333"},
    "dead_band_color": "#999999",
    # FI2 (2026-10-02): one hue per brain, the deck grammar's full/light pair (the CARLA tool's F1D
    # pairs lead #1f77b4 with #aec7e8); the brain ids are the registry's
    "brain_colors": {"x_mobility": "#1f77b4", "x_mobility_vis_full": "#ff7f0e",
                     "x_mobility_fusion_v3": "#9467bd"},
    "brain_colors_light": {"x_mobility": "#aec7e8", "x_mobility_vis_full": "#ffbb78",
                           "x_mobility_fusion_v3": "#c5b0d5"},
}
BRAIN_LABELS = {"x_mobility": "stock checkpoint", "x_mobility_vis_full": "whole-model fine-tune",
                "x_mobility_fusion_v3": "head-only fine-tune"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_input_provenance(paths):
    """S17 guard + the lineage dict embedded in the figure data."""
    est, emit = _sha(ESTIMATOR), _sha(EMITTER)
    problems, docs = [], []
    for p in paths:
        d = json.loads(p.read_text())
        if d.get("findings_kind") != "findings_summary/v0.2":
            problems.append(f"{p}: not a findings_summary/v0.2 document")
            continue
        prov = d.get("provenance") or {}
        if prov.get("estimator_sha256") != est:
            problems.append(f"{p}: estimator_sha256 "
                            f"{str(prov.get('estimator_sha256'))[:12]} != "
                            f"current scl_core/stats/boundary.py {est[:12]}")
        if prov.get("emitter_sha256") != emit:
            problems.append(f"{p}: emitter_sha256 "
                            f"{str(prov.get('emitter_sha256'))[:12]} != "
                            f"current findings_v2.py {emit[:12]}")
        docs.append((p, d))
    if problems:
        sys.exit("REFUSED: derived input not produced by the current "
                 "instruments (contract: figure data never consumes "
                 "findings that outlived their instrument; re-emit v0.2 at "
                 "this checkout):\n  - " + "\n  - ".join(problems))
    return docs, {"estimator_sha256": est, "emitter_sha256": emit,
                  "findings": {p.name: _sha(p) for p, _ in docs}}


_REGISTRY = {}


def document_version(doc):
    """The version row of a document's realization (2026-10-03, the registry: a family id names a
    lineage, the manifest hash in the document's provenance names the realization); refused when
    the registry does not hold it, never guessed."""
    if "registry" not in _REGISTRY:
        _REGISTRY["registry"] = fv.load(REPO_ROOT)
    return fv.version_for_document(_REGISTRY["registry"], doc)


def reference_lines(instrument: dict, robot_record: dict) -> dict:
    c = certification(instrument, robot_record)
    return {"contact_width_m": c["contact_width_m"], "dead_band_m": c["dead_band_m"], "source": c["source"]}


def curve_render_spec(curve: dict, x_range) -> dict:
    """What the renderer may draw for one v0.2 curve."""
    fit, boot = curve.get("fit_passable"), curve.get("bootstrap")
    spec = {"observed": [[p["width_m"], p["rate"], p["n"], p["assignment"],
                          p["wilson_95"]] for p in curve["points"] if p.get("rate") is not None],
            # widths the declared scenario-defect register took out (assignment `defect`, no rate)
            "defect_widths": [p["width_m"] for p in curve["points"] if p.get("assignment") == "defect"]}
    if fit is None:
        spec["kind"] = "none"
        return spec
    m = fit["method"]
    spec["reason"] = fit.get("reason")           # the estimator's case, on every kind (principle 14)
    if m == "flat":
        spec.update(kind="flat", level=fit["level"],
                    level_ci=fit.get("level_ci"),
                    note="no boundary in the tested range")
        return spec
    spec.update(kind=m, boundary=fit.get("boundary"),
                band=list(fit.get("interval") or [None, None]))
    if boot and boot.get("ci"):
        spec["band_ci"] = [round(v, 3) for v in boot["ci"]]
        spec["separation_fraction"] = boot.get("separation_fraction")
    if m == "irls" and fit.get("slope") is not None and \
            not fit.get("slope_note"):
        b1, bd = fit["slope"], fit["boundary"]
        # sampled over the strata the fit used (the passable widths), never
        # extrapolated into the blocked region (2026-10-04)
        lo, hi = fit.get("x_range") or x_range
        xs = [lo + i * (hi - lo) / 60 for i in range(61)]
        spec["curve"] = [[round(x, 4), round(
            1 / (1 + math.exp(-max(-30, min(30, b1 * (x - bd))))), 4)]
            for x in xs]
    elif m == "irls":
        spec["kind"] = "irls_pointonly"
    return spec


def build_fi1(docs, refs, v1_docs):
    by_family = {d["family_id"]: (p, d) for p, d in docs}
    panels = []
    for fam in FAMILY_ORDER:
        if fam not in by_family:
            continue
        p, d = by_family[fam]
        widths = [pt["width_m"] for pt in d["curves"]["passage"]["points"]]
        x_range = [round(min(widths), 4), round(max(widths), 4)]
        curves = {cid: curve_render_spec(d["curves"][cid], x_range)
                  for cid in ("passage", "p_go", "p_clean")}
        comp = [{"width_m": c["width_m"], "assignment": c["assignment"],
                 "n": c["n"], "outcomes": c["outcomes"]}
                for c in d["outcome_composition"]]
        expert = None
        v1 = v1_docs.get(fam)
        if v1:
            expert = next((f.get("value") for f in v1["findings"]
                           if f.get("finding_id") == "fnd_expert_reference"),
                          None)
        ver = document_version(d)
        panels.append({
            "family": fam, "label": fv.display(ver, FAMILY_LABELS.get(fam, fam)),
            "family_version": fv.record(ver),
            "brain": d["brain"]["id"], "x_range": x_range,
            "curves": curves, "composition": comp,
            "decomposition": d["decomposition"],
            "expert_reference": expert,
            "findings_doc": p.name,
        })
    return {"figure": "FI1", "refs": refs, "panels": panels,
            "family_order": [pnl["family"] for pnl in panels],
            "note": ("Panel A: passage (solid), commitment P(go) (dashed) "
                     "and mission P(clean pass) (dotted) per family with "
                     "the fit the estimator produced; Panel B: verbatim "
                     "outcome composition per width")}


def _sides(doc):
    """The two decision sides of one document, verbatim counts: clean passes on the passable
    widths and contacts on the blocked ones (any outcome ending in _contact)."""
    comp = doc["outcome_composition"]
    pas = [c for c in comp if c["assignment"] == "passable"]
    blk = [c for c in comp if c["assignment"] == "blocked"]
    return {"passable_clean": {"k": sum(c["outcomes"].get("commit_clean", 0) for c in pas),
                               "n": sum(c["n"] for c in pas)},
            "blocked_contact": {"k": sum(v for c in blk for o, v in c["outcomes"].items() if o.endswith("_contact")),
                                "n": sum(c["n"] for c in blk)}}


def build_fi2(docs, refs, v1_docs, family=None, titles=None, mixed_versions=False):
    """One family, one panel per brain (2026-10-02, the technical deck's "v1 and v2 on the same
    aisle"): the documents in the order given, each panel the brain's commitment and passage
    curves with the fit the estimator produced, the verbatim outcome composition, and the two
    decision sides counted. The family is the one the documents share, or --family when the
    input holds several; the panel titles are passed (a deck's "v1 / v2" wording is a choice,
    never a default), else the brain labels. One family means one REALIZATION (2026-10-03, the
    registry): documents on different versions of the family are refused unless --mixed-versions
    says so, and then every panel title carries its version."""
    fams = sorted({d["family_id"] for _, d in docs})
    if family is None:
        if len(fams) != 1:
            sys.exit(f"REFUSED: FI2 draws one family; the documents span {fams} (give --family)")
        family = fams[0]
    chosen = [(p, d) for p, d in docs if d["family_id"] == family]
    if not chosen:
        sys.exit(f"REFUSED: no document of family {family} among the inputs ({fams})")
    if titles is not None and len(titles) != len(chosen):
        sys.exit(f"REFUSED: {len(titles)} panel titles for {len(chosen)} documents of {family}")
    brains = [d["brain"]["id"] for _, d in chosen]
    if len(set(brains)) != len(brains):
        sys.exit(f"REFUSED: one document per brain; got {brains}")
    widths = sorted({pt["width_m"] for _, d in chosen for pt in d["curves"]["passage"]["points"]})
    x_range = [round(widths[0], 4), round(widths[-1], 4)]
    versions = [document_version(d) for _, d in chosen]
    realizations = sorted({v["manifest_sha256"] for v in versions})
    if len(realizations) > 1 and not mixed_versions:
        sys.exit(f"REFUSED: FI2 draws one realization of {family}; the documents sit on "
                 f"{', '.join(fv.display(v, family) + ' (' + d['campaign']['id'] + ')' for v, (_, d) in zip(versions, chosen))} "
                 f"(give --mixed-versions to draw them side by side with the version in every title)")
    panels = []
    for i, (p, d) in enumerate(chosen):
        ver = versions[i]
        panels.append({
            "family": family, "label": fv.display(ver, FAMILY_LABELS.get(family, family)),
            "family_version": fv.record(ver),
            "brain": d["brain"]["id"], "campaign": d["campaign"]["id"],
            "title": titles[i] if titles else BRAIN_LABELS.get(d["brain"]["id"], d["brain"]["id"]),
            "x_range": x_range,
            "curves": {cid: curve_render_spec(d["curves"][cid], x_range) for cid in ("passage", "p_go")},
            "composition": [{"width_m": c["width_m"], "assignment": c["assignment"], "n": c["n"],
                             "outcomes": c["outcomes"]} for c in d["outcome_composition"]],
            "sides": _sides(d), "findings_doc": p.name})
    return {"figure": "FI2", "family": family, "refs": refs, "panels": panels,
            "brain_order": brains, "mixed_versions": len(realizations) > 1,
            "family_versions": {p["campaign"]: p["family_version"] for p in panels},
            "note": ("One family, one panel per brain in the deck grammar (the CARLA tool's F1D): "
                     "commitment P(go) solid, passage P(threaded clean) dashed and lighter on top, the "
                     "gap between them shaded; the boundary diamond with its interval where the "
                     "estimator located one; the robot contact width; the verbatim outcome "
                     "composition per width below")}


# --- renderers (presentation only) -------------------------------------------

def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "svg.hashsalt": "scl",
                         "pdf.compression": 9})
    return plt


def _draw_curve(ax, spec, color, style, x_range, label, dodge=0.0):
    if spec.get("observed"):
        xs = [o[0] for o in spec["observed"]]
        rs = [min(1 - dodge, max(dodge, o[1])) if o[1] in (0.0, 1.0)
              else o[1] for o in spec["observed"]]
        ax.step(xs, rs, where="mid", color=color, alpha=0.9, label=label,
                **style)
        for o, r in zip(spec["observed"], rs):
            blocked = o[3] == "blocked"
            ax.plot([o[0]], [r], "o", color=color, ms=3.5, alpha=0.85,
                    markerfacecolor="white" if blocked else color,
                    markeredgecolor=color if blocked else "white",
                    markeredgewidth=0.6, zorder=3)
    kind = spec.get("kind")
    if kind == "flat":
        if spec.get("level_ci"):
            ax.fill_between(x_range, spec["level_ci"][0], spec["level_ci"][1],
                            color=color, alpha=0.08)
    elif kind not in (None, "none"):
        # the estimator's interval (the bracket between adjacent tested widths: "intervals, never
        # points"); the bootstrap CI stays in the data layer (2026-10-04: it used to be preferred here
        # and, being a point on every separated sample, hid the band)
        band = sorted(v for v in spec["band"] if v is not None)
        if len(band) == 2:
            lo, hi = max(band[0], x_range[0]), min(band[1], x_range[1])
            if hi > lo:
                ax.axvspan(lo, hi, color=color, alpha=0.12)
        if spec.get("curve"):
            xs, ps = zip(*spec["curve"])
            ax.plot(xs, ps, color=color, lw=1.2, alpha=0.7)
        if spec.get("boundary") is not None:
            ax.plot([spec["boundary"]], [0.5], "D", color=color, ms=6,
                    zorder=4)


def _draw_refs(ax, refs):
    lo, hi = refs["dead_band_m"]
    ax.axvspan(lo, hi, color=STYLE["dead_band_color"], alpha=0.18, lw=0)
    ax.axvline(refs["contact_width_m"], **STYLE["ref_width"], lw=1)


def render_fi1(d, out):
    plt = _mpl()
    from matplotlib.patches import Patch
    n = len(d["panels"])
    fig, axes = plt.subplots(2, n, figsize=(2.6 * n + 0.8, 4.6), squeeze=False)
    # the outcome legend covers every outcome present in ANY panel (a
    # per-panel legend would hide outcomes the first family never shows)
    present = [oc for oc in STYLE["outcome_order"]
               if any(c["outcomes"].get(oc) for p in d["panels"]
                      for c in p["composition"])]
    for i, panel in enumerate(d["panels"]):
        color = STYLE["family_colors"][panel["family"]]
        ax = axes[0][i]
        for k, cid in enumerate(("passage", "p_go", "p_clean")):
            _draw_curve(ax, panel["curves"][cid], color,
                        STYLE["curve_styles"][cid], panel["x_range"],
                        STYLE["curve_labels"][cid], dodge=0.012 * (k + 1))
        _draw_refs(ax, d["refs"])
        spec = panel["curves"]["passage"]
        if spec.get("kind") == "flat":
            ax.text(0.5, 0.55, spec["note"], transform=ax.transAxes,
                    ha="center", fontsize=6.5, color=color)
        ax.set_ylim(-0.05, 1.05)
        ax.set_xlim(panel["x_range"][0] - 0.05, panel["x_range"][1] + 0.05)
        title = panel["label"]
        if panel.get("expert_reference"):
            title += f"   (expert {panel['expert_reference']})"
        ax.set_title(title, fontsize=8.5)
        if i == 0:
            ax.set_ylabel("rate over 5 reps", fontsize=7.5)
            ax.legend(fontsize=5.8, loc="center left",
                      bbox_to_anchor=(0.02, 0.62), framealpha=0.85)
        ax.set_xlabel("certified minimum free gap (m)", fontsize=6.5)
        ax.tick_params(labelsize=6.5)
        # Panel B: composition
        bx = axes[1][i]
        comp = panel["composition"]
        xs = [c["width_m"] for c in comp]
        bw = 0.032
        bottoms = [0.0] * len(comp)
        for oc in STYLE["outcome_order"]:
            vals = [c["outcomes"].get(oc, 0) / c["n"] if c["n"] else 0
                    for c in comp]
            if not any(vals):
                continue
            bx.bar(xs, vals, width=bw, bottom=bottoms,
                   color=STYLE["outcome_colors"][oc],
                   edgecolor="white", linewidth=0.3)
            bottoms = [b + v for b, v in zip(bottoms, vals)]
        _draw_refs(bx, d["refs"])
        bx.set_ylim(0, 1.0)
        bx.set_xlim(panel["x_range"][0] - 0.05, panel["x_range"][1] + 0.05)
        bx.set_xlabel("certified minimum free gap (m)", fontsize=6.5)
        bx.tick_params(labelsize=6.5)
        if i == 0:
            bx.set_ylabel("outcome share", fontsize=7.5)
    axes[1][-1].legend(handles=[Patch(color=STYLE["outcome_colors"][oc],
                                      label=STYLE["outcome_labels"].get(oc, oc.replace("_", " ")))
                                for oc in present],
                       fontsize=5.8, loc="lower right", framealpha=0.9)
    fig.tight_layout()
    _save(fig, out, "FI1")


def render_fi2(d, out):
    """The deck grammar of the CARLA tool's F1D mini-panels (2026-10-02): no dots, no Wilson
    bands, commitment solid and heavy in the brain's hue, passage lighter and dashed drawn on
    top, the gap shaded; the located boundary as a diamond with its interval band; the robot
    width as the one reference line; the outcome strip below each panel."""
    plt = _mpl()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    n = len(d["panels"])
    fig = plt.figure(figsize=(3.6 * n, 4.6))
    gs = fig.add_gridspec(2, n, height_ratios=[1.0, 0.42], hspace=0.28, wspace=0.08)
    top = [fig.add_subplot(gs[0, i]) for i in range(n)]
    bot = [fig.add_subplot(gs[1, i], sharex=top[i]) for i in range(n)]
    x0, x1 = d["panels"][0]["x_range"]
    pad = 0.05 * (x1 - x0)
    dodge = 0.018

    def _steps(spec):
        obs = spec.get("observed") or []
        xs = [o[0] for o in obs]
        ys = [min(1 - dodge, max(dodge, o[1])) if o[1] in (0.0, 1.0) else o[1] for o in obs]
        return xs, ys

    present = [oc for oc in STYLE["outcome_order"]
               if any(c["outcomes"].get(oc) for p in d["panels"] for c in p["composition"])]
    for i, panel in enumerate(d["panels"]):
        ax, bx = top[i], bot[i]
        color = STYLE["brain_colors"].get(panel["brain"], "#8c564b")
        light = STYLE["brain_colors_light"].get(panel["brain"], "#c49c94")
        gx, gy = _steps(panel["curves"]["p_go"])
        cx, cy = _steps(panel["curves"]["passage"])
        ax.step(gx, gy, where="mid", color=color, lw=2.2, zorder=2)
        ax.step(cx, cy, where="mid", color=light, lw=1.5, ls=(0, (4, 3)), zorder=3)
        g_at, c_at = dict(zip(gx, gy)), dict(zip(cx, cy))
        common = sorted(set(gx) & set(cx))
        if common:
            ax.fill_between(common, [c_at[x] for x in common], [g_at[x] for x in common],
                            step="mid", color=color, alpha=0.15, lw=0, zorder=1)
        spec = panel["curves"]["passage"]
        if spec.get("kind") == "flat":
            ax.text(0.64, 0.56, "no boundary in the tested range", transform=ax.transAxes,
                    ha="center", fontsize=7.5, color=color, zorder=4)
        elif spec.get("kind") not in (None, "none") and spec.get("boundary") is not None:
            # the band is the estimator's interval (the bracket between adjacent scheduled widths:
            # "intervals, never points"); the bootstrap CI stays in the data layer
            band = sorted(v for v in spec["band"] if v is not None)
            if len(band) == 2 and band[1] > band[0]:
                ax.axvspan(band[0], band[1], color=color, alpha=0.12, lw=0, zorder=0)
            # the diamond and its band carry the boundary; the numbers live in figure_data (Milan, 2026-10-04:
            # no boundary text, no robot-width text, the lines stay)
            ax.plot([spec["boundary"]], [0.5], "D", color=color, ms=7, zorder=4)
        w = d["refs"]["contact_width_m"]
        ax.axvline(w, color=STYLE["ref_width"]["color"], lw=1, zorder=1)
        title = panel["title"] + (f"\n{panel['label']}" if d.get("mixed_versions") else "")
        ax.set_title(title, fontsize=9, color=color, pad=3)
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(-0.06, 1.05)
        ax.tick_params(labelsize=7)      # the gap widths read on the curves too (Milan, 2026-10-04), not only under the strip
        if i == 0:
            ax.set_ylabel("rate over 5 samples")
            ax.legend([Line2D([], [], color="#444444", lw=2.2),
                       Line2D([], [], color="#999999", lw=1.5, ls=(0, (4, 3)))],
                      ["decides to go", "passes cleanly"], fontsize=6.5, loc="upper left", framealpha=0.9)
        else:
            plt.setp(ax.get_yticklabels(), visible=False)
        comp = panel["composition"]
        xs = [c["width_m"] for c in comp]
        bottoms = [0.0] * len(comp)
        for oc in STYLE["outcome_order"]:
            vals = [c["outcomes"].get(oc, 0) / c["n"] if c["n"] else 0 for c in comp]
            if not any(vals):
                continue
            bx.bar(xs, vals, width=0.032, bottom=bottoms, color=STYLE["outcome_colors"][oc],
                   edgecolor="white", linewidth=0.3)
            bottoms = [b + v for b, v in zip(bottoms, vals)]
        bx.axvline(w, color=STYLE["ref_width"]["color"], lw=1, zorder=1)
        bx.set_ylim(0, 1.0)
        bx.set_xlim(x0 - pad, x1 + pad)
        bx.tick_params(labelsize=7)
        bx.set_xlabel("certified minimum free gap (m)", fontsize=8)
        if i == 0:
            bx.set_ylabel("outcome", fontsize=8)
        else:
            plt.setp(bx.get_yticklabels(), visible=False)
    fig.legend(handles=[Patch(color=STYLE["outcome_colors"][oc], label=STYLE["outcome_labels"].get(oc, oc.replace("_", " ")))
                        for oc in present],
               fontsize=6.5, loc="lower center", ncol=len(present), frameon=False,
               bbox_to_anchor=(0.5, 0.0))
    # fixed margins, not tight_layout: a figure-level legend below the axes is outside what
    # tight_layout measures, and the x labels landed on it
    fig.subplots_adjust(left=0.09, right=0.985, top=0.93, bottom=0.17, hspace=0.36, wspace=0.08)   # room for the top row's tick labels
    _save(fig, out, "FI2")


def _save(fig, out_dir, name):
    for ext in ("pdf", "png", "svg"):
        fig.savefig(out_dir / f"{name}.{ext}", dpi=150)


def contact_sheet(out_dir):
    """Index every figure PNG present in out_dir/figures on one page, the CARLA tool's sheet in the
    same shape (parity, 2026-09-23): a partial regeneration never shrinks the sheet, a retired
    figure lingers until its PNG is deleted."""
    names = sorted(p.stem for p in (out_dir / "figures").glob("*.png"))
    imgs = "\n".join(f'<h3>{n}</h3><img src="figures/{n}.png" style="max-width:100%">' for n in names)
    (out_dir / "contact_sheet.html").write_text(
        f"<html><body><h2>SCL paper figures (Isaac)</h2>{imgs}</body></html>")


RENDERERS = {"FI1": render_fi1, "FI2": render_fi2}
BUILDERS = {"FI1": build_fi1, "FI2": build_fi2}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--findings", nargs="+", required=True, type=Path,
                    help="findings_summary/v0.2 documents (one per family)")
    ap.add_argument("--instrument", required=True, type=Path,
                    help="the declared scoring instrument: the reference band from its certification block")
    ap.add_argument("--robot-record", required=True, type=Path,
                    help="the robot record: the contact width the band is centred on")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--figures", nargs="+", default=["FI1"])
    ap.add_argument("--family", default=None,
                    help="FI2: the family to draw when the documents span several")
    ap.add_argument("--panel-titles", nargs="+", default=None,
                    help="FI2: one title per document, in the documents' order (default: the brain labels)")
    ap.add_argument("--mixed-versions", action="store_true",
                    help="FI2: draw documents that sit on different versions of the family side by side "
                         "(refused otherwise); every panel title then carries its version")
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()

    docs, lineage = check_input_provenance(args.findings)
    refs = reference_lines(json.loads(args.instrument.read_text()), json.loads(args.robot_record.read_text()))
    lineage["instrument_sha256"] = _sha(args.instrument)
    lineage["robot_record_sha256"] = _sha(args.robot_record)
    lineage["builder_sha256"] = _sha(Path(__file__).resolve())
    # optional: the pinned v0.1 sibling beside each v0.2 doc supplies the
    # expert reference row (v0.2 documents are per brain); since C9
    # (2026-09-27) the sibling is named by the one rule, <stem>.v0.1.json
    v1_docs = {}
    for p, d in docs:
        v1 = p.with_name(p.name.replace(".v0.2.json", ".v0.1.json"))
        if v1.is_file():
            v1d = json.loads(v1.read_text())
            v1_docs[d["family_id"]] = v1d
            lineage["findings"][v1.name] = _sha(v1)

    (args.out / "figure_data").mkdir(parents=True, exist_ok=True)
    (args.out / "figures").mkdir(parents=True, exist_ok=True)
    for name in args.figures:
        extra = ({"family": args.family, "titles": args.panel_titles, "mixed_versions": args.mixed_versions}
                 if name == "FI2" else {})
        data = BUILDERS[name](docs, refs, v1_docs, **extra)
        data["lineage"] = lineage
        (args.out / "figure_data" / f"{name}.json").write_text(
            json.dumps(data, indent=1, sort_keys=True) + "\n")
        if not args.no_render:
            RENDERERS[name](data, args.out / "figures")
    if not args.no_render:
        contact_sheet(args.out)
    print(f"figure_data: {args.figures} -> {args.out / 'figure_data'}; "
          f"rendered: {[] if args.no_render else args.figures}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
