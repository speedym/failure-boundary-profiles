#!/usr/bin/env python3
"""Isaac paper figure generator (mirror of
adapters/fail2drive/scripts/multi_model_eval/paper_figures.py): the
three-family picture (FI1) and the per-brain panels (FI2) from the pooled
findings documents.

Two-layer output, by design (the CARLA discipline):
  figure_data/FI1.json  the exact numbers plotted (series, bands, fits,
                        composition, reference lines) with lineage -
                        deterministic, matplotlib-free, committed-golden
                        material. THE AUDITABLE LAYER.
  figures/FI1.{pdf,png,svg}  matplotlib renders consuming ONLY the
                        figure_data dict. Presentation, never truth.

    conda run -n scl_prototype python \\
        adapters/isaac/scripts/multi_model_eval/paper_figures.py \\
        --pooled <root> --spec <pooling spec>... [--brain x_mobility ...] \\
        --out <dir> [--figures FI1 FI2] [--no-render]

INPUT CONTRACT (F82, principle 15, 2026-10-11; the same form as the CARLA tool): every family is
read from the pool tool's folder for its pooling spec (pool_campaigns.py --out <root>:
`<root>/<pool_id>/findings/<document>` and `pooling_record.json`), the documents of the brain(s)
named by `--brain` (default x_mobility, the roster brain) plus the pool's reference document for
the expert row. A document is findings_summary/v0.3 whose emitter, fit, estimator, scorer and
vocabulary pins hash to this tree (scl_core.figures.documents); otherwise the builder REFUSES
(figure data never consumes a document that outlived its instrument). Until F82 this tool read
v0.2 documents by path and the expert row from a v0.1 sibling.

FIGURE POLICY (scl_core.figures.curves, shared with the CARLA generator)
- A curve panel draws the DATA (observed per-width rates as a step line
  with dots, Wilson bands) and only what the fit method produced:
  flat -> level line + Wilson fill + "no boundary in tested range";
  bracket -> the estimator's interval band (the bootstrap CI stays in the data layer; a reason names the case when
  active) + the boundary diamond, NEVER a curve through separated
  data; irls -> the logistic curve sampled in the data layer.
- Reference lines are DERIVED at build time from the scoring instrument and the robot record the
  rows were scored against, pinned by role in every document's rows_provenance and read from this
  tree under those pins (scl_core/certification_band.py): the robot contact width (the record's
  pinch sweep) and the certification dead band from the instrument's eps and grid resolution.
  Never hardcoded; until 2026-10-08 read from the capability profile, until F82 from paths passed
  by hand.
- Colors are fixed per family and per outcome (presentation map).
- Deterministic renders: SOURCE_DATE_EPOCH pinned.
"""
import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
from scl_core import family_versions as fv  # noqa: E402  (the family version registry: a label over the manifest hash)
from scl_core.certification_band import certification  # noqa: E402  (the reference band: one formula, one place)
from scl_core.figures import curves as fc  # noqa: E402  (the one curve renderer)
from scl_core.figures import documents as fd
from scl_core.figures import draw_profiles as dp  # noqa: E402  (the paper's profile grid, two designs)
from scl_core.figures import profiles as fp  # noqa: E402  # noqa: E402  (the one reader of the pool folders)

ROSTER_BRAIN = "x_mobility"     # the paper's warehouse agent (the stock brain); the fine-tunes are extensions
FAMILY_ORDER = ["fm_isaac_001", "fm_isaac_003", "fm_isaac_004"]
FAMILY_LABELS = {"fm_isaac_001": "Slab pincer",     # the paper's name (V20, 2026-10-21; "Slab arms" was ours)
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
                       "anomaly_blockage_completed": "#444444", "missing": "#eeeeee"},
    "outcome_order": ["commit_clean", "divert_clean", "timeout_clean",
                      "timeout_contact", "commit_contact", "divert_contact",
                      "anomaly_blockage_completed", "missing"],
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
                "x_mobility_fusion_v3": "head-only fine-tune", "expert": "expert"}
DESIGN = ["hybrid"]   # set by --design; the profile renderers read it


# --- inputs ------------------------------------------------------------------

def load_inputs(pooled_root, specs, root=REPO_ROOT) -> dict:
    """{pools: [pool...], lineage}: every spec's pool folder read and admitted (documents only; the
    rows and fits are the CARLA tool's concern on its side and are not needed to draw a document)."""
    pools = [fd.read_pool(pooled_root, s, root, rows=False) for s in specs]
    return {"root": root, "pools": pools, "lineage": fd.lineage(Path(__file__).resolve(), root, pools)}


def brain_documents(pools, brains, family=None) -> list:
    """[(pool, brain, doc)] for every (pool, brain) that holds a document, pools in the given order,
    brains in the given order within a pool; a brain with no document in any pool refuses (a typo
    is loud), as does a selection that leaves nothing to draw."""
    out = []
    for pool in pools:
        if family is not None and pool["family"] != family:
            continue
        for brain in brains:
            if brain in pool["documents"]:
                out.append((pool, brain, pool["documents"][brain]))
    for brain in brains:
        if not any(b == brain for _, b, _ in out):
            held = sorted({m for p in pools for m in p["documents"]})
            raise ValueError(f"no document of brain {brain!r} in the pools given (their documents name {held})")
    if not out:
        raise ValueError("no document to draw among the pools given")
    return out


def reference_lines(docs, root) -> dict:
    """The reference band from the instrument and the robot record the documents' rows were scored
    against (pinned by role in rows_provenance.instruments); every document of a figure must pin the
    same pair (one band per figure), else refused."""
    pins = {json.dumps({k: fd.instruments_by_role(d).get(k) for k in ("instrument", "robot_record")}, sort_keys=True)
            for d in docs}
    if len(pins) != 1:
        raise ValueError("the documents pin different scoring instruments or robot records; one reference band per figure")
    first = docs[0]
    c = certification(fd.instrument(first, "instrument", root), fd.instrument(first, "robot_record", root))
    roles = fd.instruments_by_role(first)
    return {"contact_width_m": c["contact_width_m"], "dead_band_m": c["dead_band_m"], "source": c["source"],
            "instrument": roles["instrument"], "robot_record": roles["robot_record"]}


def expert_reference(pool) -> dict | None:
    """The pool's reference document (the expert, run once per variant): its clean completions by
    any route over its runs, the number FI1's panel titles carry ("12/12"; until F82 read from the
    v0.1 sibling's fnd_expert_reference row)."""
    model = fd.reference_model(pool["documents"])
    if model is None:
        return None
    rate = pool["documents"][model]["decomposition"]["p_clean"]
    return {"model": model, "p_clean": {"n": rate["n"], "k": rate["k"]}, "text": f"{rate['k']}/{rate['n']}"}


def composition(doc) -> list:
    return [{"x": c["x"], "assignment": c["assignment"], "n": c["n"], "outcomes": c["outcomes"]}
            for c in doc["outcome_composition"]]


def x_range_of(docs) -> list:
    xs = sorted({c["x"] for d in docs for c in d["outcome_composition"]})
    return [round(xs[0], 4), round(xs[-1], 4)]


# --- builders ----------------------------------------------------------------

def build_fi1(inp, brains, **_):
    """The three-family picture: one panel per family (FAMILY_ORDER, the pools that hold one), the
    one brain's three curves with the fit the estimator produced, the verbatim outcome composition,
    the decomposition, the pool's expert reference."""
    root = inp["root"]
    if len(brains) != 1:
        raise ValueError(f"FI1 draws one brain per panel; --brain names {brains} (FI2 draws several)")
    brain = brains[0]
    by_family = {}
    for pool in inp["pools"]:
        if pool["family"] in by_family:
            raise ValueError(f"two pools of {pool['family']} ({by_family[pool['family']]['pool_id']}, {pool['pool_id']}); "
                             f"FI1 draws one pool per family")
        by_family[pool["family"]] = pool
    chosen = [(by_family[f], brain, by_family[f]["documents"][brain]) for f in FAMILY_ORDER
              if f in by_family and brain in by_family[f]["documents"]]
    if not chosen:
        raise ValueError(f"no document of brain {brain!r} among the pools given (families {sorted(by_family)})")
    refs = reference_lines([d for _, _, d in chosen], root)
    panels = []
    for pool, brain, d in chosen:
        fam = pool["family"]
        x_range = x_range_of([d])
        ver = fd.version_of(d, root)
        panels.append({
            "family": fam, "label": fd.with_cohort(fv.display(ver, FAMILY_LABELS.get(fam, fam)), pool.get("cohort")),
            "family_version": fv.record(ver), "cohort": pool.get("cohort"),
            "brain": brain, "pool": pool["pool_id"], "campaigns": fd.campaign_ids(d), "x_range": x_range,
            "curves": {q: fc.render_spec(d["curves"][q], x_range) for q in fc.QUANTITIES},
            "composition": composition(d),
            "decomposition": d["decomposition"],
            "decision_summary": d["decision_summary"],
            "expert_reference": expert_reference(pool),
            "findings_doc": pool["paths"][brain].name,
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


def build_fi2(inp, brains, family=None, titles=None, mixed_versions=False):
    """One family, one panel per (pool, brain) (2026-10-02, the technical deck's "v1 and v2 on the
    same aisle"): the pools in the order given, the brains in the order given within a pool, each
    panel the brain's commitment and passage curves with the fit the estimator produced, the
    verbatim outcome composition, and the two decision sides counted. The family is the one the
    pools share, or --family when they span several; the panel titles are passed (a deck's "v1 /
    v2" wording is a choice, never a default), else the brain labels. One family means one
    REALIZATION (2026-10-03, the registry): pools on different versions of the family are refused
    unless --mixed-versions says so, and then every panel title carries its version."""
    root = inp["root"]
    fams = sorted({p["family"] for p in inp["pools"]})
    if family is None:
        if len(fams) != 1:
            raise ValueError(f"FI2 draws one family; the pools span {fams} (give --family)")
        family = fams[0]
    if family not in fams:
        raise ValueError(f"no pool of family {family} among the inputs ({fams})")
    chosen = brain_documents(inp["pools"], brains, family)
    if titles is not None and len(titles) != len(chosen):
        raise ValueError(f"{len(titles)} panel titles for {len(chosen)} panels of {family} "
                         f"({[(p['pool_id'], b) for p, b, _ in chosen]})")
    x_range = x_range_of([d for _, _, d in chosen])
    versions = [fd.version_of(d, root) for _, _, d in chosen]
    realizations = sorted({v["manifest_sha256"] for v in versions})
    if len(realizations) > 1 and not mixed_versions:
        raise ValueError(f"FI2 draws one realization of {family}; the pools sit on "
                         f"{', '.join(fv.display(v, family) + ' (' + p['pool_id'] + ')' for v, (p, _, _) in zip(versions, chosen))} "
                         f"(give --mixed-versions to draw them side by side with the version in every title)")
    refs = reference_lines([d for _, _, d in chosen], root)
    panels = []
    for i, (pool, brain, d) in enumerate(chosen):
        ver = versions[i]
        panels.append({
            "family": family, "label": fd.with_cohort(fv.display(ver, FAMILY_LABELS.get(family, family)), pool.get("cohort")),
            "family_version": fv.record(ver), "cohort": pool.get("cohort"),
            "brain": brain, "pool": pool["pool_id"], "campaigns": fd.campaign_ids(d),
            "title": titles[i] if titles else BRAIN_LABELS.get(brain, brain),
            "x_range": x_range,
            "curves": {q: fc.render_spec(d["curves"][q], x_range) for q in ("passage", "p_go")},
            "composition": composition(d),
            "sides": _sides(d), "findings_doc": pool["paths"][brain].name})
    return {"figure": "FI2", "family": family, "refs": refs, "panels": panels,
            "brain_order": [p["brain"] for p in panels], "pool_order": [p["pool"] for p in panels],
            "mixed_versions": len(realizations) > 1,
            "family_versions": {p["pool"]: p["family_version"] for p in panels},
            "note": ("One family, one panel per brain in the deck grammar (the CARLA tool's F1D): "
                     "commitment P(go) solid, passage P(threaded clean) dashed and lighter on top, the "
                     "gap between them shaded; the boundary diamond with its interval where the "
                     "estimator located one; the robot contact width; the verbatim outcome "
                     "composition per width below")}


def load_roster(path) -> dict:
    """The figure roster spec (figure_roster/v0.1): the warehouse columns (the brains in order, with the
    paper's labels), the family labels, and the stock-versus-fine-tune pair of PV."""
    if path is None:
        raise ValueError("PW and PV take a roster spec (--roster artifacts/examples/figure_specs/paper_roster.json)")
    doc = json.loads(Path(path).read_text())
    if doc.get("roster_kind") != "figure_roster/v0.1":
        raise ValueError(f"{path}: roster_kind {doc.get('roster_kind')!r} is not figure_roster/v0.1")
    return doc


def _document_of(pools, family, brain):
    """(pool, doc) of the one pool among those given that holds the brain's document of the family;
    two refuse (one pool per family and brain), none gives (None, None)."""
    hits = [(p, p["documents"][brain]) for p in pools if p["family"] == family and brain in p["documents"]]
    if len(hits) > 1:
        raise ValueError(f"{family} {brain}: two pools hold its document ({[p['pool_id'] for p, _ in hits]}); one pool per family and brain")
    return hits[0] if hits else (None, None)


def _one_realization(figure, family, docs, root, mixed_versions):
    versions = [fd.version_of(d, root) for d in docs]
    if len({v["manifest_sha256"] for v in versions}) > 1 and not mixed_versions:
        raise ValueError(f"{figure} draws one realization of {family}; the documents sit on "
                         f"{sorted({fv.display(v, family) for v in versions})} (give --mixed-versions)")
    return versions


def build_pw(inp, brains, roster=None, mixed_versions=False, **_):
    """The warehouse profile grid (the paper's Figure 10): rows the families in FAMILY_ORDER among the
    pools given, columns the roster spec's warehouse brains (the released checkpoint and its two
    fine-tunes), each cell the brain's document from the one pool that holds it, the subtitles the
    decision counts and the competence boundary, the robot's contact width the reference line."""
    root = inp["root"]
    columns = roster["warehouse"]
    labels = roster.get("warehouse_family_labels") or FAMILY_LABELS
    families = [f for f in FAMILY_ORDER if any(p["family"] == f for p in inp["pools"])]
    panels, docs_all, row_labels = [], [], []
    for fam in families:
        fam_docs = []
        for col in columns:
            pool, doc = _document_of(inp["pools"], fam, col["model"])
            held = [(col["model"], doc, pool["pool_id"], pool["paths"][col["model"]].name)] if doc is not None else []
            panels.append({"label": col["label"], "model": col["model"], "docs": held,
                           "campaigns": fd.campaign_ids(doc) if doc is not None else None,
                           "family_version": fv.record(fd.version_of(doc, root)) if doc is not None else None})
            if doc is not None:
                fam_docs.append(doc)
        _one_realization("PW", fam, fam_docs, root, mixed_versions)
        docs_all += fam_docs
        row_labels.append(labels.get(fam, fam))
    if not docs_all:
        raise ValueError("PW: no document to draw among the pools given")
    refs = reference_lines(docs_all, root)
    axis = docs_all[0]["axis"]
    d = fp.build_profiles("PW", family="warehouse", label="Warehouse", axis=axis, panels=panels, refs=refs,
                          columns=len(columns), warehouse=True)
    for i, panel in enumerate(d["panels"]):
        panel["family"] = families[i // len(columns)]
        if i % len(columns) == 0:
            panel["row_label"] = row_labels[i // len(columns)]
    d.update(axis_label="minimum free gap (m)", families=families, roster=roster.get("note"))
    return d


def pair_titles(roster):
    """The pair's panel titles are the names the roster gives those two brains for the warehouse grid (the paper's
    Figure 10), so Figure 11 names them the same way (V21, 2026-10-21; a separate `titles` list had said "v1 stock
    checkpoint", a form the text never uses); a brain the roster's warehouse list does not name falls back to
    BRAIN_LABELS."""
    pair = roster["warehouse_pair"]
    named = {w["model"]: w["label"] for w in roster.get("warehouse", [])}
    return [named.get(b) or BRAIN_LABELS.get(b, b) for b in (pair["stock"], pair["fine_tune"])]


def build_pv(inp, brains, roster=None, family=None, titles=None, mixed_versions=False, **_):
    """The stock-versus-fine-tune pair on one family (the paper's Figure 11 in the profile grammar): two
    panels, the released checkpoint and the whole-model fine-tune from the pools that hold them, with
    the outcome composition strip under each (FI2's strip, the same palette)."""
    root = inp["root"]
    pair = roster["warehouse_pair"]
    fams = sorted({p["family"] for p in inp["pools"]})
    if family is None:
        if len(fams) != 1:
            raise ValueError(f"PV draws one family; the pools span {fams} (give --family)")
        family = fams[0]
    titles = titles or pair_titles(roster)
    panels, docs = [], []
    for brain, title in zip((pair["stock"], pair["fine_tune"]), titles):
        pool, doc = _document_of(inp["pools"], family, brain)
        if doc is None:
            raise ValueError(f"PV: no document of {brain} for {family} among the pools given")
        panels.append({"label": title, "model": brain, "docs": [(brain, doc, pool["pool_id"], pool["paths"][brain].name)],
                       "campaigns": fd.campaign_ids(doc), "family_version": fv.record(fd.version_of(doc, root))})
        docs.append(doc)
    _one_realization("PV", family, docs, root, mixed_versions)
    refs = reference_lines(docs, root)
    strip = {"colors": STYLE["outcome_colors"], "order": STYLE["outcome_order"], "labels": STYLE["outcome_labels"]}
    d = fp.build_profiles("PV", family=family, label=FAMILY_LABELS.get(family, family), axis=docs[0]["axis"], panels=panels,
                          refs=refs, columns=2, warehouse=True, strip=strip)
    d.update(axis_label="certified minimum free gap (m)", roster=roster.get("note"))
    return d


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
            title += f"   (expert {panel['expert_reference']['text']})"
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
        xs = [c["x"] for c in comp]
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
        xs = [c["x"] for c in comp]
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


RENDERERS = {"FI1": render_fi1, "FI2": render_fi2,
             "PW": lambda d, out: dp.render_profiles(d, out, "PW", DESIGN[0]),
             "PV": lambda d, out: dp.render_profiles(d, out, "PV", DESIGN[0])}
BUILDERS = {"FI1": build_fi1, "FI2": build_fi2, "PW": build_pw, "PV": build_pv}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pooled", required=True, type=Path,
                    help="pool_campaigns.py's --out root: every family is read from its pooled folder there")
    ap.add_argument("--spec", action="append", required=True, type=Path,
                    help="a pooling spec (campaign_pooling/v0.1); repeatable, one per family drawn (FI1) or "
                         "one per pool drawn side by side (FI2)")
    ap.add_argument("--brain", action="append", default=None,
                    help=f"the brain(s) whose documents are drawn (default {ROSTER_BRAIN}, the roster brain); "
                         f"FI1 takes one, FI2 one panel per (pool, brain) that holds a document")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--figures", nargs="+", default=["FI1"])
    ap.add_argument("--family", default=None,
                    help="FI2: the family to draw when the pools span several")
    ap.add_argument("--panel-titles", nargs="+", default=None,
                    help="FI2: one title per panel, in the panels' order (default: the brain labels)")
    ap.add_argument("--mixed-versions", action="store_true",
                    help="FI2: draw pools that sit on different versions of the family side by side "
                         "(refused otherwise); every panel title then carries its version")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--roster", type=Path, default=None,
                    help="PW, PV: the figure roster spec (figure_roster/v0.1) naming the warehouse columns, the family "
                         "labels and the stock-versus-fine-tune pair")
    ap.add_argument("--design", choices=list(dp.DESIGNS), default="hybrid",
                    help="PW, PV: the paper's design as it stands (asis) or with the estimator's marks kept (hybrid)")
    args = ap.parse_args()
    DESIGN[0] = args.design
    brains = args.brain or [ROSTER_BRAIN]
    unknown = [f for f in args.figures if f not in BUILDERS]
    if unknown:
        ap.error(f"no figure {unknown}; the figures are {sorted(BUILDERS)}")

    try:
        inp = load_inputs(args.pooled, args.spec)
    except ValueError as exc:
        sys.exit(f"REFUSED: {exc}")
    (args.out / "figure_data").mkdir(parents=True, exist_ok=True)
    (args.out / "figures").mkdir(parents=True, exist_ok=True)
    for name in args.figures:
        extra = ({"family": args.family, "titles": args.panel_titles, "mixed_versions": args.mixed_versions}
                 if name == "FI2" else {})
        try:
            if name in ("PW", "PV"):
                extra = {"roster": load_roster(args.roster), "family": args.family, "titles": args.panel_titles,
                         "mixed_versions": args.mixed_versions}
            data = BUILDERS[name](inp, brains, **extra)
        except ValueError as exc:
            sys.exit(f"REFUSED: {name}: {exc}")
        data["lineage"] = inp["lineage"]
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
