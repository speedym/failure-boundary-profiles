"""Paper figure generator (Demo B item 8): F1-F6 per the PAPER CUT.

Two-layer output, by design:
  figure_data/F*.json   the exact numbers plotted (series, bands,
                        annotations, reference lines) with provenance -
                        deterministic, matplotlib-free, committed-golden
                        material. THE AUDITABLE LAYER.
  figures/F*.{pdf,png,svg}  matplotlib renders consuming ONLY the
                        figure_data dicts. Presentation, not truth -
                        never byte-golden-tested.

    conda run -n scl_prototype python \
        adapters/fail2drive/scripts/multi_model_eval/paper_figures.py \
        --scored <dir> --stats <dir> --metrics <dir> --out <dir>
        [--figures F1 F2 ...] [--no-render]

FIGURE POLICY
- A figure may only draw what the stats method produced: method=flat
  renders a flat line + Wilson band + "no boundary" annotation;
  method=bracket renders the midpoint + shaded bracket band - NEVER a
  smooth curve through separated data; only method=irls draws a
  logistic curve (sampled in the data layer); a bracket that is not a clean separation (reason not_converged or fit_outside_bracket) renders the
  crossing without a curve.
- Reference lines are DERIVED at build time, never hardcoded: body
  limit = 2 x the registry's Lincoln half-width (1.837); policy floor
  = body + 2 x mirror allowance + 2 x clearance margin from the
  family's capability profile (2.297 on current profiles). Drawn only
  on corridor-width axes.
- Agent colors are fixed across ALL figures (the presentation-map
  rule): lead blue, tfpp green, bridgedrive red, simlingo purple,
  hipad orange, plant2 brown, expert gray.
- F4 (beat-the-teacher) requires the fm_001 sweep; until those rows
  exist the builder emits a status-placeholder figure_data and skips
  the render (queued on the farm 2026-08-05).
- Deterministic renders: SOURCE_DATE_EPOCH pinned so PDF metadata is
  byte-stable across reruns on one matplotlib version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import boundary_stats  # noqa: E402  (outcome codings - single source)
import route_metrics  # noqa: E402  (RouteAxis, profile walk)
import score_runs  # noqa: E402  (REGISTRY_PATH, XML_DIR, manifests)
import trajectory_extract  # noqa: E402
from scl_core import family_versions as fv  # noqa: E402  (the family version registry: a label over the manifest hash)
from scl_core import results_store  # noqa: E402  (flow and Isaac documents, read from the store)
from scl_core.certification_band import certification  # noqa: E402  (the Isaac reference band)
from scl_core.stats.boundary import analyze as estimator_analyze  # noqa: E402  (per-seed fits, the one estimator)

STYLE = {
    "colors": {"lead": "#1f77b4", "lead_regnety032": "#0b3d91", "tfpp": "#2ca02c",
               "bridgedrive": "#d62728", "simlingo": "#9467bd",
               "hipad": "#ff7f0e", "plant2": "#8c564b",
               "plant2_s2": "#a9786a", "plant2_s3": "#c69c8f",
               "pdm_lite_expert": "#7f7f7f"},
    # Agent display names. The evaluated sensor agent is "TFv6" — its
    # own paper reserves "LEAD" for the privileged planner (Table 5),
    # so labeling it LEAD would misattribute in front of its authors.
    # The internal model id stays "lead" everywhere (dirs, ledgers,
    # scored rows, models.yaml) — renamed at display time only.
    # lead_regnety032 (F52): the flagship instantiation; which of the two prints as plain "TFv6"
    # is Milan's and Ellington's call once it is measured, so the label is provisional
    "labels": {"lead": "TFv6", "lead_regnety032": "TFv6 (RegNetY-032)", "tfpp": "TF++",
               "bridgedrive": "BridgeDrive", "simlingo": "SimLingo",
               "hipad": "HiP-AD", "plant2": "PlanT 2.0 (seed 1)",
               "plant2_s2": "PlanT 2.0 (seed 2)", "plant2_s3": "PlanT 2.0 (seed 3)",
               "pdm_lite_expert": "expert",
               # the warehouse brains (2026-10-01, T8/T9): the stock X-Mobility is the roster brain, the
               # v07 fine-tunes are extensions (handoff_f54_scl_brains.txt names what each adapted)
               "x_mobility": "X-Mobility (stock)", "x_mobility_vis_full": "X-Mobility (whole-model fine-tune)",
               "x_mobility_fusion_v3": "X-Mobility (head-only fine-tune)"},
    "ref_body": {"ls": "-", "color": "#333333"},
    "ref_policy": {"ls": "--", "color": "#666666"},
    "mirror_band_color": "#f4c542",
}

TIED_PAIR = ("lead", "bridgedrive")
FM_ORDER = ["fm_002_corridor_pincer", "fm_004_threshold_sweep", "fm_005_hero"]

# Reader-facing family names for figures/tables. Internal ids stay in
# figure_data (the auditable layer remains pipeline-true); this map is
# canonical with the paper's Methods family table - change both or
# neither.
FAMILY_LABELS = {
    "fm_001_intrusion_sweep": "Machine intrusion",
    "fm_002_corridor_pincer": "Corridor width",      # the paper's name (section 3.3 and the Figure 2 caption, draft of 2026-10-01)
    "fm_003_asset_swap": "Identity ladder",
    "fm_004_threshold_sweep": "Charger gauntlet",
    "fm_005_hero": "Cluttered intrusion",
    "fm_011_flow_speed_sweep": "Oncoming flow: speed",
    "fm_012_source_dist_interval_sweep": "Oncoming flow: spacing",
    "fm_isaac_001": "Warehouse: slab pincer",
    "fm_isaac_003": "Warehouse: floor stock",
    "fm_isaac_004": "Warehouse: composed stacks",
}
# the atlas extension (2026-09-19): PlanT 2.0's three checkpoints are ONE agent drawn as three thin
# traces of one hue; the checkpoint spread is the subject of F8, not a legend
CHECKPOINT_GROUPS = {"plant2": ("plant2", "plant2_s2", "plant2_s3")}
CHECKPOINT_LABEL = {"plant2": "PlanT 2.0 (3 checkpoints)"}
FLOW_FAMILIES = ("fm_011_flow_speed_sweep", "fm_012_source_dist_interval_sweep")
AXIS_UNITS = {"crossing_time_gap_s": "s", "width_m": "m"}
AXIS_LABELS = {"crossing_time_gap_s": "crossing time gap", "width_m": "certified minimum free gap",
               "minimum_free_corridor_width": "minimum free corridor width", "maximum_route_intrusion": "maximum route intrusion"}


def axis_label(metric):
    return f"{AXIS_LABELS.get(metric, str(metric).replace('_', ' '))} ({AXIS_UNITS.get(metric, 'm')})"


UNCONSTRAINED_BAND = 0.6   # a bootstrap interval wider than this fraction of the tested range is not drawn as a band
OUTCOME_COLORS = {   # one palette across domains: clean greens, refusals greys, contacts reds
    "clean_thread": "#2ca02c", "late_commit": "#98df8a", "refusal": "#c7c7c7", "correct_stop": "#9e9e9e",
    "contact_thread": "#ff9896", "contact_blocked": "#d62728", "contact_past_blockage": "#8b0000",
    "clean_cross": "#2ca02c", "stall": "#c7c7c7", "incomplete": "#9e9e9e", "collision_flow": "#d62728",
    "collision_other": "#ff9896", "unresolved_contact": "#ffbb78",
    "commit_clean": "#2ca02c", "divert_clean": "#98df8a", "timeout_clean": "#c7c7c7",
    "timeout_contact": "#d62728", "commit_contact": "#ff9896", "divert_contact": "#e377c2"}
OUTCOME_ORDER = ["clean_thread", "clean_cross", "commit_clean", "late_commit", "divert_clean",
                 "refusal", "correct_stop", "stall", "incomplete", "timeout_clean",
                 "unresolved_contact", "contact_thread", "collision_other", "commit_contact", "divert_contact",
                 "contact_blocked", "collision_flow", "timeout_contact", "contact_past_blockage"]

# Typeset forms of the outcome taxonomy for legends (sync with the
# Methods definitions when that chapter is written)
OUTCOME_LABELS = {
    "clean_thread": "clean thread", "late_commit": "late commit",
    "refusal": "refusal", "correct_stop": "correct stop",
    "contact_thread": "contact (thread)",
    "contact_blocked": "contact (blocked)",
    "contact_past_blockage": "contact past blockage",
    "anomaly_blockage_completed": "blocked width completed",
    "unresolved_contact": "unresolved contact",
}


def family_label(family):
    """Never show a raw pipeline id to a reader."""
    return FAMILY_LABELS.get(family, str(family).replace("_", " "))


# --- family versions (2026-10-03, the registry) -------------------------------
# A family id names a lineage, its manifest hash a realization; every row, panel or column that comes from
# a document or a pool names the version its source binds (scl_core.family_versions), and the label carries
# the version where the registry holds more than one (fv.display). A source the registry cannot place is
# refused, never guessed; a cohort passed by --scored/--stats carries no pool record and stays unversioned
# (the data says None).

_REGISTRIES = {}


def family_registry(store_root):
    root = Path(store_root)
    if root not in _REGISTRIES:
        _REGISTRIES[root] = fv.load(root)
    return _REGISTRIES[root]


def document_version(store_root, doc):
    """The version row of a findings document's realization."""
    return fv.version_for_document(family_registry(store_root), doc, store_root)


def campaigns_version(store_root, family, campaign_ids):
    """The version row the campaigns' declarations bind for the family (pooled rows carry no document; the
    pool record names the campaigns); refused when they bind different realizations."""
    found = {fv.declared_realization(store_root, c, family) for c in campaign_ids}
    if len(found) != 1:
        raise ValueError(f"{family}: the pooled campaigns {sorted(campaign_ids)} bind {len(found)} realizations "
                         f"({sorted(s[:12] for _, s in found)}); one table cell is one realization")
    backend, sha = found.pop()
    return fv.version_for(family_registry(store_root), backend, family, sha)


def rows_version(inp, family):
    """The version row of a family's pooled rows, through the pool records the loader kept in
    inp['lineage']['pools']; None when the input holds no pool record for the family."""
    pools = (inp.get("lineage") or {}).get("pools") or {}
    campaigns = sorted({c for pool in pools.values() if pool.get("family") == family
                        for c in (pool.get("interpretations") or {})})
    return campaigns_version(inp.get("store_root", REPO_ROOT), family, campaigns) if campaigns else None


def versioned_label(family, row):
    return fv.display(row, family_label(family)) if row else family_label(family)


def version_leaf(row):
    return fv.record(row) if row else None


# --- shared loading --------------------------------------------------------

def _jsonl(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def _tool_sha(name: str) -> str:
    return hashlib.sha256(
        (Path(__file__).resolve().parent / name).read_bytes()).hexdigest()


def check_input_provenance(scored_dir: Path, stats_dir: Path) -> dict:
    """Refuse derived inputs the current instruments did not produce.

    Contract: figure data draws only from scored tables stamped by the
    score_runs.py beside this tool and stats stamped by the
    boundary_stats.py beside it - a stale table silently carries a
    retired instrument's outcomes (the fm_004 "Perfect"-as-refusal
    incident). The single correct remedy is re-deriving from raw with
    the current tools. Returns the lineage dict embedded in every
    figure_data JSON built from these inputs.
    """
    problems = []
    sp = scored_dir / "scoring_provenance.json"
    tp = stats_dir / "stats_provenance.json"
    scorer_now = _tool_sha("score_runs.py")
    stats_now = _tool_sha("boundary_stats.py")
    scored_stamp = json.loads(sp.read_text()) if sp.is_file() else None
    stats_stamp = json.loads(tp.read_text()) if tp.is_file() else None
    if scored_stamp is None:
        problems.append(f"{scored_dir}: no scoring_provenance.json "
                        f"(unstamped scored tables)")
    elif scored_stamp.get("scorer_sha256") != scorer_now:
        problems.append(f"{scored_dir}: scorer_sha256 "
                        f"{scored_stamp.get('scorer_sha256')} != current "
                        f"score_runs.py {scorer_now}")
    if stats_stamp is None:
        problems.append(f"{stats_dir}: no stats_provenance.json "
                        f"(unstamped stats)")
    elif stats_stamp.get("stats_sha256") != stats_now:
        problems.append(f"{stats_dir}: stats_sha256 "
                        f"{stats_stamp.get('stats_sha256')} != current "
                        f"boundary_stats.py {stats_now}")
    if problems:
        sys.exit(
            "REFUSED: derived input not produced by the current "
            "instruments (contract: figure data never consumes tables "
            "that outlived their instrument; re-score raw and re-fit "
            "stats at this checkout):\n"
            + "\n".join(f"- {p}" for p in problems))
    return {"scorer_sha256": scorer_now,
            "scored_input_files_sha256": scored_stamp["input_files_sha256"],
            "stats_sha256": stats_now}


def pooled_pairs(pooled_root: Path, specs) -> list:
    """The (scored dir, stats dir, pool record) triples of pool_campaigns.py's output for the
    given pooling specs: one pool per family, its rows already gated per campaign, its stamp
    naming the interpretations it pooled (2026-09-19, TODO item 3: figures read the results
    store through the pooled folders, never a hand-made analysis directory)."""
    pairs = []
    for spec_path in specs:
        spec = json.loads(Path(spec_path).read_text())
        if spec.get("pooling_kind") != "campaign_pooling/v0.1":
            sys.exit(f"REFUSED: {spec_path} is not a campaign_pooling/v0.1 spec")
        pool_dir = Path(pooled_root) / spec["pool_id"]
        record_path = pool_dir / "pooling_record.json"
        if not record_path.is_file():
            sys.exit(f"REFUSED: {pool_dir} holds no pooling_record.json; run pool_campaigns.py --spec {spec_path}")
        record = json.loads(record_path.read_text())
        family = spec["family_id"]
        pairs.append((pool_dir / family / "scoring_out", pool_dir / family / "stats", record))
    return pairs


def _load_pools(pooled_root, specs, pools, metrics=None):
    scored, stats = [], []
    for scored_dir, stats_dir, record in pooled_pairs(pooled_root, specs):
        lineage = check_input_provenance(scored_dir, stats_dir)
        scored += _jsonl(scored_dir / "scored_rows.jsonl")
        stats += _jsonl(stats_dir / "stats_rows.jsonl")
        if metrics is not None:   # the pool's route metrics (2026-09-22), stamped by the tool beside this one
            metrics_dir = scored_dir.parent / "metrics"
            if (metrics_dir / "metrics_rows.jsonl").is_file():
                stamp = json.loads((metrics_dir / "metrics_provenance.json").read_text())
                if stamp.get("tool_sha256") != _tool_sha("route_metrics.py"):
                    raise SystemExit(f"ERROR: {metrics_dir}: metrics stamped by route_metrics {str(stamp.get('tool_sha256'))[:12]}, "
                                     f"the tool beside this one is {_tool_sha('route_metrics.py')[:12]}; re-pool from re-scored interpretations")
                metrics += _jsonl(metrics_dir / "metrics_rows.jsonl")
        pools[record["pooling"]["pool_id"]] = {
            "family": record["pooling"]["campaigns"][0]["declaration"]["path"].split("/")[-1][:-5],
            "spec_sha256": record["pooling"]["spec"]["sha256"],
            "interpretations": {i["campaign"]: i["derived_content_id_sha256"] for i in record["inputs"]},
            "scored_sha256": record["scored_sha256"], "stats_sha256": record["stats_sha256"],
            "scored_input_files_sha256": lineage["scored_input_files_sha256"]}
    return scored, stats


def load_inputs(args):
    if args.pooled is not None:
        pools = {}
        pooled_metrics = []
        scored, stats = _load_pools(args.pooled, args.spec, pools, pooled_metrics)
        # the roster extension (2026-09-19): agents that join the atlas, F8 and, since 2026-09-23, the
        # identity ladder F7, but not the paper's roster tables and F1/F4/F6 (the pooled folders of the
        # extension campaigns, one spec per family)
        ext_scored, ext_stats = _load_pools(args.pooled, args.extension_spec or [], pools)
        return {"scored": scored, "stats": stats, "extension_scored": ext_scored, "extension_stats": ext_stats,
                "store_root": Path(args.store_root) if args.store_root else REPO_ROOT,
                "metrics": (_jsonl(Path(args.metrics) / "metrics_rows.jsonl") if args.metrics else pooled_metrics),
                "lineage": {"scorer_sha256": _tool_sha("score_runs.py"), "stats_sha256": _tool_sha("boundary_stats.py"),
                            "metrics_sha256": _tool_sha("route_metrics.py"), "pools": pools}}
    lineage = check_input_provenance(Path(args.scored), Path(args.stats))
    return {
        "scored": _jsonl(Path(args.scored) / "scored_rows.jsonl"),
        "stats": _jsonl(Path(args.stats) / "stats_rows.jsonl"),
        "extension_scored": [], "extension_stats": [],
        "store_root": Path(args.store_root) if args.store_root else REPO_ROOT,
        "metrics": (_jsonl(Path(args.metrics) / "metrics_rows.jsonl")
                    if args.metrics else []),
        "lineage": lineage,
    }


def reference_lines(family: str) -> dict:
    """Derived body limit + policy floor for corridor-width families."""
    doc = json.loads(score_runs.REGISTRY_PATH.read_text())
    half_w = None
    for row in doc.get("rows", []):
        if (row.get("binding") or {}).get("ref") == "vehicle.lincoln.mkz_2020":
            half_w = row["footprint"]["half_extents"][1]
    if half_w is None:
        return {}
    manifest = json.loads(
        (score_runs.MANIFEST_DIR / f"{family}.json").read_text())
    # both constants from the manifest's provenance (the pinned document), never the profile (2026-10-07)
    mirror = route_metrics.mirror_allowance_for(manifest)
    margin = route_metrics.policy_constant(manifest, "ego_clearance_margin_m")
    body = 2 * half_w
    return {"body_limit_m": round(body, 3),
            "policy_floor_m": round(body + 2 * mirror + 2 * margin, 3)}


def axis_metric(stats_rows, family):
    for r in stats_rows:
        if r["family"] == family and "axis_metric" in r:
            return r["axis_metric"]
    return None


def measured_x(row, metric):
    return (row.get("measured") or {}).get(metric)


def observed_rates(scored, family, model, curve, metric):
    """Empirical per-x rates under the same coding boundary_stats uses -
    the DATA layer of every curve panel (the inference layer alone reads
    as missing data when the method is bracket/flat)."""
    coder = (boundary_stats.code_commitment if curve == "commitment"
             else boundary_stats.code_competence)
    acc = defaultdict(lambda: [0, 0])
    for r in scored:
        if r["family"] != family or r["model"] != model:
            continue
        y = coder(r["outcome"])
        x = measured_x(r, metric)
        if y is None or x is None:
            continue
        acc[x][0] += 1
        acc[x][1] += y
    return [[round(x, 4), round(k / n, 3), n]
            for x, (n, k) in sorted(acc.items())]


def curve_render_spec(stat_row, x_lo, x_hi):
    """What the renderer may draw for one (model, curve) stats row."""
    m = stat_row["method"]
    if m == "flat":
        return {"kind": "flat", "level": stat_row["level"],
                "level_ci": stat_row.get("level_ci")}
    spec = {"kind": m, "boundary": stat_row["boundary"],
            "band": [stat_row["bracket_fail_side_x"],
                     stat_row["bracket_pass_side_x"]]}
    if stat_row.get("bootstrap"):
        # multi-seed data: the bootstrap CI supersedes the bracket band
        spec["band_ci"] = [round(v, 3) for v in stat_row["bootstrap"]["ci"]]
        spec["separation_fraction"] = stat_row["bootstrap"]["separation_fraction"]
    if m == "irls" and stat_row.get("slope") is not None \
            and not stat_row.get("slope_note"):
        b1 = stat_row["slope"] * stat_row["orientation"]
        bd = stat_row["boundary"]
        xs = [x_lo + i * (x_hi - x_lo) / 60 for i in range(61)]
        spec["curve"] = [[round(x, 4), round(
            1 / (1 + math.exp(-max(-30, min(30, b1 * (x - bd))))), 4)]
            for x in xs]
    elif m == "irls":
        spec["kind"] = "irls_pointonly"  # inversion-only slope: no curve
    return spec


# --- figure builders (data layer) ------------------------------------------

def build_f1(inp):
    fam = "fm_004_threshold_sweep"
    metric = axis_metric(inp["stats"], fam)
    panels_ds, means_by_x = {}, {}
    for model in TIED_PAIR:
        pts = sorted(
            (measured_x(r, metric), r["driving_score"], r["outcome"])
            for r in inp["scored"]
            if r["family"] == fam and r["model"] == model)
        panels_ds[model] = [[round(x, 4), round(ds, 2), oc]
                            for x, ds, oc in pts]
        acc = defaultdict(list)
        for x, ds, _ in pts:
            acc[x].append(ds)
        means_by_x[model] = [[round(x, 4), round(sum(v) / len(v), 2)]
                             for x, v in sorted(acc.items())]
    # the inversion cell is PICKED MECHANICALLY: the same-(variant, seed)
    # pair maximizing bridgedrive's completed-with-contact score over
    # lead's correct stop - reproducible, never a hand-chosen exemplar
    cells, best = None, -math.inf
    by_vs = defaultdict(dict)
    for r in inp["scored"]:
        if r["family"] == fam and r["model"] in TIED_PAIR:
            by_vs[(r["variant_index"], r["seed"])][r["model"]] = r
    for (v, s), pair in by_vs.items():
        if len(pair) < 2:
            continue
        bd, ld = pair["bridgedrive"], pair["lead"]
        if bd["outcome"] == "contact_past_blockage" \
                and ld["outcome"] == "correct_stop" \
                and bd["driving_score"] - ld["driving_score"] > best:
            best = bd["driving_score"] - ld["driving_score"]
            cells = {m: {"x": round(measured_x(pair[m], metric), 4),
                         "ds": round(pair[m]["driving_score"], 2),
                         "outcome": pair[m]["outcome"]}
                     for m in TIED_PAIR}
            cells["variant_index"], cells["seed"] = v, s
    xs = [x for pts in panels_ds.values() for x, _, _ in pts]
    commit = {}
    for model in TIED_PAIR:
        row = next(r for r in inp["stats"]
                   if r["family"] == fam and r["model"] == model
                   and r["curve"] == "commitment")
        spec = curve_render_spec(row, min(xs), max(xs))
        spec["observed"] = observed_rates(
            inp["scored"], fam, model, "commitment", metric)
        commit[model] = spec
    means = {m: round(sum(p[1] for p in panels_ds[m]) / len(panels_ds[m]), 1)
             for m in TIED_PAIR}
    return {"figure": "F1", "family": fam, "axis_metric": metric,
            "x_range": [round(min(xs), 4), round(max(xs), 4)],
            "ds_points": panels_ds, "ds_mean_by_x": means_by_x,
            "mean_ds": means,
            "inversion_cell": cells, "commitment": commit,
            "refs": reference_lines(fam)}


def build_f1d(inp):
    """Deck-standalone variant of F1 (decks/talks - NOT a paper figure;
    the paper's F1 stays the prose-supported intro figure). Same DS
    panel; the bottom panel adds each agent's competence curve
    P(clean_pass) beside its commitment curve P(go), so the gap -
    committing where it cannot pass cleanly - is drawn, not narrated.
    Sourced from the same stats rows F2's atlas cells use."""
    d = build_f1(inp)
    fam, metric = d["family"], d["axis_metric"]
    comp = {}
    for model in TIED_PAIR:
        row = next(r for r in inp["stats"]
                   if r["family"] == fam and r["model"] == model
                   and r["curve"] == "competence")
        spec = curve_render_spec(row, *d["x_range"])
        spec["observed"] = observed_rates(
            inp["scored"], fam, model, "competence", metric)
        comp[model] = spec
    d["figure"] = "F1D"
    d["competence"] = comp
    return d


def doc_curve_spec(curve: dict, x_range) -> dict:
    """What the renderer may draw for one findings-document curve (flow, findings_summary/v0.2 with
    `fit` + `bootstrap`; Isaac, the same with `fit_passable`): the same grammar as curve_render_spec
    over a stats row, read from the document instead of the row (2026-09-19, the store)."""
    fit, boot = curve.get("fit") or curve.get("fit_passable"), curve.get("bootstrap")
    spec = {"observed": [[round(p.get("x", p.get("width_m")), 4), round(p["rate"], 3), p["n"]]
                         for p in curve["points"] if p.get("rate") is not None]}
    if fit is None:
        spec["kind"] = "none"
        return spec
    m = fit["method"]
    if m == "flat":
        spec.update(kind="flat", level=fit.get("level"), level_ci=fit.get("level_ci"))
        return spec
    spec.update(kind=m, boundary=fit.get("boundary"), band=list(fit.get("interval") or [None, None]))
    if boot and boot.get("ci"):
        spec["band_ci"] = [round(v, 3) for v in boot["ci"]]
        spec["separation_fraction"] = boot.get("separation_fraction")
    if m == "irls" and fit.get("slope") is not None and fit.get("boundary") is not None and not fit.get("slope_note"):
        sign = fit.get("orientation", 1) or 1
        b1, bd = fit["slope"] * sign, fit["boundary"]
        lo, hi = x_range
        xs = [lo + i * (hi - lo) / 60 for i in range(61)]
        spec["curve"] = [[round(x, 4), round(1 / (1 + math.exp(-max(-30, min(30, b1 * (x - bd))))), 4)] for x in xs]
    elif m == "irls":
        spec["kind"] = "irls_pointonly"
    return spec


def flow_documents(store_root, family):
    """The flow findings documents of one family in the store, every campaign and agent, newest
    campaign last: {model: (doc path, document)}; a model in two campaigns keeps the later one and
    the data layer names both."""
    out = {}
    for path, doc in results_store.findings_documents(store_root, family=family):
        if doc.get("findings_kind") != "findings_summary/v0.2" or doc.get("backend") != "fail2drive":
            continue
        out[doc["model"]["id"]] = (path, doc)
    return out


def flow_panel(store_root, family):
    docs = flow_documents(store_root, family)
    if not docs:
        return None
    xs = sorted({p["x"] for _, d in docs.values() for p in d["curves"]["p_go"]["points"]})
    x_range = [round(min(xs), 4), round(max(xs), 4)]
    panel = {"family": family, "axis_metric": "crossing_time_gap_s", "x_range": x_range, "refs": {},
             "source": "documents", "documents": {m: {"path": str(p.relative_to(store_root)), "sha256": _sha_file(p),
                                                       "campaign": d["campaign"]["id"]} for m, (p, d) in docs.items()},
             "curves": {}}
    for model, (_, d) in docs.items():
        panel["curves"][model] = {"commitment": doc_curve_spec(d["curves"]["p_go"], x_range),
                                  "competence": doc_curve_spec(d["curves"]["p_clean"], x_range)}
    return panel


def _sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_f2(inp):
    """The boundary atlas: threading rows from the pooled cohorts (the paper roster plus the
    extension agents), then the flow families from their findings documents in the store."""
    scored = inp["scored"] + inp.get("extension_scored", [])
    stats = inp["stats"] + inp.get("extension_stats", [])
    families = [f for f in FM_ORDER if any(r["family"] == f for r in stats)]
    grid = []
    for fam in families:
        metric = axis_metric(stats, fam)
        xs = [measured_x(r, metric) for r in scored
              if r["family"] == fam and measured_x(r, metric) is not None]
        if not xs:
            continue
        panel = {"family": fam, "axis_metric": metric,
                 "x_range": [round(min(xs), 4), round(max(xs), 4)],
                 "refs": (reference_lines(fam)
                          if metric == "minimum_free_corridor_width" else {}),
                 "source": "rows", "curves": {}}
        for r in stats:
            if r["family"] != fam or "curve" not in r:
                continue
            spec = curve_render_spec(r, min(xs), max(xs))
            spec["observed"] = observed_rates(scored, fam, r["model"], r["curve"], metric)
            panel["curves"].setdefault(r["model"], {})[r["curve"]] = spec
        grid.append(panel)
    for fam in FLOW_FAMILIES:
        panel = flow_panel(inp.get("store_root", REPO_ROOT), fam)
        if panel:
            grid.append(panel)
    return {"figure": "F2", "panels": grid,
            "checkpoint_groups": {k: list(v) for k, v in CHECKPOINT_GROUPS.items()}}


def build_f3(inp):
    out = []
    scored_ix = {(r["family"], r["model"], r["seed"], r["scenario_id"]): r
                 for r in inp["scored"]}
    for fam in ("fm_002_corridor_pincer", "fm_004_threshold_sweep"):
        metric = axis_metric(inp["stats"], fam)
        mirror = None
        pts = defaultdict(list)
        for m in inp["metrics"]:
            if m["family"] != fam or m.get("min_clearance_m") is None:
                continue
            s = scored_ix.get((m["family"], m["model"], m["seed"],
                               m["scenario_id"]))
            if not s:
                continue
            # passage margins only: a refusal run's "min clearance" is
            # its stop standoff - different quantity, not this figure's
            if not s["completed"]:
                continue
            x = measured_x(s, metric)
            if x is None:
                continue
            pts[m["model"]].append(
                [round(x, 4), m["min_clearance_m"], m["mirror_band"],
                 round(s["driving_score"], 1)])
        if not pts:
            continue
        manifest = json.loads(
            (score_runs.MANIFEST_DIR / f"{fam}.json").read_text())
        mirror = route_metrics.mirror_allowance_for(manifest)
        expert_floor = (min(p[1] for p in pts["pdm_lite_expert"])
                        if "pdm_lite_expert" in pts else None)
        out.append({"family": fam, "axis_metric": metric,
                    "mirror_allowance_m": mirror,
                    "expert_floor_m": expert_floor,
                    "points": {m: sorted(v) for m, v in pts.items()}})
    return {"figure": "F3", "panels": out}


def build_f4(inp):
    fam = "fm_001_intrusion_sweep"
    have = [r for r in inp["scored"] if r["family"] == fam]
    if not have:
        return {"figure": "F4", "status":
                "awaiting fm_001 sweep on current canon (farm-queued "
                "2026-08-05); July runs were pre-geometry-1.1 and "
                "never landed off-farm"}
    metric = axis_metric(inp["stats"], fam) or "maximum_route_intrusion"
    xs = [measured_x(r, metric) for r in have
          if measured_x(r, metric) is not None]
    panel = {"figure": "F4", "family": fam, "axis_metric": metric,
             "x_range": [round(min(xs), 4), round(max(xs), 4)], "curves": {}}
    for r in inp["stats"]:
        if r["family"] == fam and r.get("curve") == "competence":
            spec = curve_render_spec(r, min(xs), max(xs))
            spec["observed"] = observed_rates(
                inp["scored"], fam, r["model"], "competence", metric)
            panel["curves"][r["model"]] = spec
    return panel


F7_ROSTER = ["pdm_lite_expert", "lead", "tfpp", "bridgedrive"]   # the paper roster's column order


def _f7_column(model):
    """The column a row's model falls in: a grouped agent's checkpoints share one column (the atlas
    convention, CHECKPOINT_GROUPS); every other model is its own column."""
    for group, members in CHECKPOINT_GROUPS.items():
        if model in members:
            return group
    return model


def build_f7(inp):
    """Identity ladder (fm_003): asset x model outcome grid at
    certified-identical geometry. n=1 cells render outcome+DS; the same
    builder aggregates to rates when multi-seed data lands. Columns come
    from the rows (2026-09-23): the paper roster in its order, then every
    extension agent whose pool holds fm_003 rows, in order of appearance;
    the expert's spread stays the roster's null instrument."""
    fam = "fm_003_asset_swap"
    rows = [r for r in inp["scored"] + inp.get("extension_scored", []) if r["family"] == fam]
    if not rows:
        return {"figure": "F7", "status": "no fm_003 rows in scored input"}
    present = []
    for r in rows:
        col = _f7_column(r["model"])
        if col not in present:
            present.append(col)
    models = [m for m in F7_ROSTER if m in present] + [m for m in present if m not in F7_ROSTER]
    agg = defaultdict(lambda: defaultdict(lambda: {"n": 0, "ds": [],
                                                   "clean": 0, "traffic": 0,
                                                   "outcomes": defaultdict(int)}))
    for r in rows:
        asset = str(r["target"]).replace("asset_", "")
        c = agg[asset][_f7_column(r["model"])]
        c["n"] += 1
        c["ds"].append(r["driving_score"])
        c["clean"] += r["outcome"] == "clean_thread"
        c["traffic"] += r["traffic_collisions"] > 0
        c["outcomes"][r["outcome"]] += 1
    cells = {}
    for asset, per_model in agg.items():
        cells[asset] = {}
        for m, c in per_model.items():
            dominant = max(c["outcomes"].items(), key=lambda kv: kv[1])[0]
            cells[asset][m] = {
                "n": c["n"], "mean_ds": round(sum(c["ds"]) / len(c["ds"]), 1),
                "clean_rate": round(c["clean"] / c["n"], 2),
                "traffic_runs": c["traffic"], "dominant": dominant}
    order = sorted(cells, key=lambda a: min(
        v["mean_ds"] for v in cells[a].values()))
    # The expert's across-object spread is the null-instrument stat,
    # but it is only meaningful within a traffic-quiet stratum: on hot
    # seeds background traffic dominates every agent's DS (measured
    # 2026-08-19: s0 spread 0.14 with 14/14 traffic-free vs 21.6/38.9
    # on s1/s2). A pooled spread mixes strata and is not identity.
    per_seed = defaultdict(dict)
    for r in rows:
        if r["model"] == "pdm_lite_expert" and not r["traffic_collisions"]:
            asset = str(r["target"]).replace("asset_", "")
            per_seed[r["seed"]][asset] = r["driving_score"]
    n_assets = len(cells)
    spread_by_seed = {
        s: {"spread": round(max(v.values()) - min(v.values()), 2),
            "traffic_free_assets": len(v), "of": n_assets}
        for s, v in sorted(per_seed.items()) if len(v) >= 2}
    quiet = [s for s, v in spread_by_seed.items()
             if v["traffic_free_assets"] == n_assets]
    return {"figure": "F7", "family": fam, "assets": order,
            "models": models, "cells": cells,
            "roster_models": [m for m in models if m in F7_ROSTER],
            "extension_models": [m for m in models if m not in F7_ROSTER],
            "checkpoint_groups": {g: list(ms) for g, ms in CHECKPOINT_GROUPS.items() if g in models},
            "expert_spread_by_seed": spread_by_seed,
            "expert_spread_quiet_ds": (
                spread_by_seed[quiet[0]]["spread"] if quiet else None),
            "geometry_note": "all variants: certified 0.5 m intrusion, "
                             "~8 m free corridor (manifest-measured)"}


OUTCOME_FILL = {"clean_thread": "#e8f5e9", "late_commit": "#fff3cd",
                "refusal": "#fdecea", "contact_thread": "#ede7f6",
                "contact_blocked": "#d1c4e9", "correct_stop": "#e3f2fd",
                "contact_past_blockage": "#ede7f6"}


def render_f7(d, out):
    plt = _mpl()
    assets, models = d["assets"], d["models"]
    fig, ax = plt.subplots(
        figsize=(1.9 * len(models) + 2.2, 0.34 * len(assets) + 1.4))
    for i, asset in enumerate(assets):
        for j, m in enumerate(models):
            c = d["cells"][asset].get(m)
            if not c:
                continue
            ax.add_patch(plt.Rectangle(
                (j, i), 1, 1, facecolor=OUTCOME_FILL.get(c["dominant"],
                                                         "#eeeeee"),
                edgecolor="white", lw=1))
            noise = "~" if c["traffic_runs"] else ""
            txt = (f"{c['mean_ds']:.1f}{noise}" if c["n"] == 1 else
                   f"{c['clean_rate']:.0%} | {c['mean_ds']:.1f}{noise} (n={c['n']})")
            ax.text(j + 0.5, i + 0.5, txt, ha="center", va="center",
                    fontsize=7)
    ax.set_xlim(0, len(models))
    ax.set_ylim(len(assets), 0)
    ax.set_xticks([k + 0.5 for k in range(len(models))])
    ax.set_xticklabels([CHECKPOINT_LABEL[m] if m in d.get("checkpoint_groups", {}) else STYLE["labels"].get(m, m)
                        for m in models], fontsize=8)
    ax.set_yticks([k + 0.5 for k in range(len(assets))])
    ax.set_yticklabels([str(a).replace("_", " ") for a in assets],
                       fontsize=7)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    import matplotlib.patches as mp
    legend = [mp.Patch(facecolor=OUTCOME_FILL[k],
                       label=OUTCOME_LABELS.get(k, k))
              for k in ("clean_thread", "late_commit", "refusal")]
    ax.legend(handles=legend, fontsize=6, loc="lower right",
              bbox_to_anchor=(1.0, -0.12), ncol=3, frameon=False)
    # no in-figure title: geometry note + expert spread live in the
    # caption (venue convention - captions carry claims)
    fig.tight_layout()
    _save(fig, out, "F7")


def render_f4(d, out):
    """Beat-the-teacher (fm_001): one competence panel. The teacher's
    flat-ZERO ceiling with both inheritors coinciding on it; tfpp's
    boundary the only structure above zero. Draw order puts the expert
    last so the coincident zero lines show the teacher's color; the
    caption states the coincidence. No in-figure title."""
    if d.get("status"):
        print(f"F4 not rendered: {d['status']}", file=sys.stderr)
        return
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    order = [m for m in ("tfpp", "lead", "bridgedrive", "pdm_lite_expert")
             if m in d["curves"]]
    for model in order:
        _draw_curvespec(ax, d["curves"][model], STYLE["colors"][model],
                        d["x_range"], label=STYLE["labels"][model])
    x0, x1 = d["x_range"]
    ax.set_xlim(x0 - 0.01, x1 + 0.01)
    ax.set_ylim(-0.05, 1.05)
    ax.set_xlabel(FAMILY_AXIS_DESC["fm_001_intrusion_sweep"], fontsize=9)
    ax.set_ylabel("P(clean pass)", fontsize=9)
    ax.legend(fontsize=7, loc="center right")
    fig.tight_layout()
    _save(fig, out, "F4")


F8_FAMILIES = ("fm_002_corridor_pincer", "fm_004_threshold_sweep", "fm_005_hero", "fm_001_intrusion_sweep",
               "fm_011_flow_speed_sweep", "fm_012_source_dist_interval_sweep")
ISAAC_INSTRUMENT = REPO_ROOT / "artifacts/examples/scoring_instruments/isaac_aisle_gauntlet_two_line_go.json"
ISAAC_ROBOT_RECORD = REPO_ROOT / "artifacts/examples/imported_ir/robot_record_nova_carter.json"
F9_ISAAC_FAMILIES = ("fm_isaac_004", "fm_isaac_001")     # composed stacks (commit only when feasible), slabs (commit and collide)


def per_seed_boundary(rows, family, model, seed, curve, metric):
    """One seed's fit through the one estimator: strata per measured x (one run each at a fixed seed),
    oriented as boundary_stats orients them, the boundary returned in natural coordinates. No
    bootstrap: a stratum of one run has nothing to resample; the spread across seeds is the point."""
    coder = boundary_stats.code_commitment if curve == "commitment" else boundary_stats.code_competence
    acc = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["family"] != family or r["model"] != model or r["seed"] != seed:
            continue
        y, x = coder(r["outcome"]), measured_x(r, metric)
        if y is None or x is None:
            continue
        acc[x][0] += 1
        acc[x][1] += y
    natural = sorted((x, n, k) for x, (n, k) in acc.items())
    if len(natural) < 2:
        return None
    orient = boundary_stats.orientation_for(natural, metric)
    fit = estimator_analyze(sorted((orient * x, n, k) for x, n, k in natural))
    b = fit.get("boundary")
    return {"seed": seed, "method": fit["method"], "n_runs": fit["n_runs"],
            "boundary": round(orient * b, 4) if b is not None else None,
            "level": fit.get("level")}


def build_f8(inp):
    """Checkpoint versus seed (2026-09-19): for every family PlanT 2.0 ran, each checkpoint's pooled
    boundary with its interval beside the boundaries each traffic seed gives on its own. Threading
    families read the extension pools' rows; the flow families read their documents, whose points
    pool the seeds, so they show the checkpoints only until the document carries per-repetition
    strata (TODO item 10)."""
    store_root = inp.get("store_root", REPO_ROOT)
    ext_scored, ext_stats = inp.get("extension_scored", []), inp.get("extension_stats", [])
    panels = []
    for fam in F8_FAMILIES:
        for head, members in CHECKPOINT_GROUPS.items():
            checkpoints = []
            if fam in FLOW_FAMILIES:
                docs = flow_documents(store_root, fam)
                metric, source = "crossing_time_gap_s", "documents"
                for model in members:
                    if model not in docs:
                        continue
                    _, d = docs[model]
                    entry = {"model": model, "per_seed": None, "per_seed_note": "flow documents pool the seeds"}
                    for curve, cid in (("commitment", "p_go"), ("competence", "p_clean")):
                        spec = doc_curve_spec(d["curves"][cid], [0, 1])
                        entry[curve] = {k: spec.get(k) for k in ("kind", "boundary", "band", "band_ci", "level", "level_ci")}
                    checkpoints.append(entry)
            else:
                metric, source = axis_metric(ext_stats, fam), "rows"
                if metric is None:
                    continue
                seeds = sorted({r["seed"] for r in ext_scored if r["family"] == fam})
                for model in members:
                    rows = [r for r in ext_stats if r["family"] == fam and r["model"] == model and "curve" in r]
                    if not rows:
                        continue
                    entry = {"model": model, "per_seed": {}}
                    for curve in ("commitment", "competence"):
                        r = next((s for s in rows if s["curve"] == curve), None)
                        if r is None:
                            continue
                        spec = curve_render_spec(r, 0, 1)
                        entry[curve] = {k: spec.get(k) for k in ("kind", "boundary", "band", "band_ci", "level", "level_ci")}
                        entry["per_seed"][curve] = [per_seed_boundary(ext_scored, fam, model, s, curve, metric) for s in seeds]
                    checkpoints.append(entry)
            if not checkpoints:
                continue
            comp = [c["competence"]["boundary"] for c in checkpoints if c.get("competence", {}).get("boundary") is not None]
            seed_b = [b["boundary"] for c in checkpoints if c["per_seed"] for b in c["per_seed"].get("competence", [])
                      if b and b["boundary"] is not None]
            if fam in FLOW_FAMILIES:
                xs = sorted({pt["x"] for _, d in flow_documents(store_root, fam).values() for pt in d["curves"]["p_go"]["points"]})
            else:
                xs = [measured_x(r, metric) for r in ext_scored if r["family"] == fam and measured_x(r, metric) is not None]
            panels.append({"family": fam, "group": head, "axis_metric": metric, "unit": AXIS_UNITS.get(metric, "m"),
                           "x_range": [round(min(xs), 4), round(max(xs), 4)] if xs else None,
                           "source": source, "checkpoints": checkpoints,
                           "spread": {"across_checkpoints": (round(max(comp) - min(comp), 4) if len(comp) > 1 else None),
                                      "across_seeds_all_checkpoints": (round(max(seed_b) - min(seed_b), 4) if len(seed_b) > 1 else None),
                                      "quantity": "competence boundary"}})
    return {"figure": "F8", "panels": panels,
            "note": ("diamonds: each checkpoint's pooled competence boundary with its bootstrap interval "
                     "(or bracket); dots: the boundary each traffic seed gives alone (threading families; one "
                     "run per variant, so no interval); 'no boundary' where the pooled fit is flat")}


def isaac_reference_lines():
    """The Isaac reference band from the declared instrument and the robot record (2026-10-08; until
    then from the capability profile, which no scoring reader opens any more)."""
    c = certification(json.loads(ISAAC_INSTRUMENT.read_text()), json.loads(ISAAC_ROBOT_RECORD.read_text()))
    return {"contact_width_m": c["contact_width_m"], "dead_band_m": c["dead_band_m"],
            "instrument_sha256": _sha_file(ISAAC_INSTRUMENT), "robot_record_sha256": _sha_file(ISAAC_ROBOT_RECORD)}


def isaac_documents(store_root, family):
    """Every brain's newest v0.2 document of one Isaac family in the store, keyed by brain id (the
    widened campaigns supersede the first three, so the newest by name wins per brain; since v07 a
    family holds several brains' documents, so the brain is never inferred from the newest file)."""
    newest = {}
    for p, d in results_store.findings_documents(store_root, family=family):
        if d.get("findings_kind") != "findings_summary/v0.2":
            continue
        brain = (d.get("brain") or {}).get("id")
        if brain is None:
            continue
        if brain not in newest or p.name > newest[brain][0].name:
            newest[brain] = (p, d)     # fs_campaign_isaac_v06_... over ..._v03_...: the widened campaign
    return newest


def isaac_document(store_root, family, brain):
    """The newest v0.2 document of one Isaac family AND one brain, with its path, or None."""
    return isaac_documents(store_root, family).get(brain)


def _brains_in(docs):
    """The roster brain first, then the extension brains by name."""
    return [b for b in [ISAAC_ROSTER_BRAIN] if b in docs] + sorted(b for b in docs if b != ISAAC_ROSTER_BRAIN)


def build_f9(inp):
    """The cross-domain pair (2026-09-19): one driving row (the Charger gauntlet, every roster agent)
    and warehouse rows (X-Mobility on the composed stacks and the slab pincer) in the same two-curve
    grammar from the same estimator, each with an outcome-composition strip for one named agent."""
    store_root = inp.get("store_root", REPO_ROOT)
    rows = []
    fam = "fm_004_threshold_sweep"
    metric = axis_metric(inp["stats"], fam)
    xs = [measured_x(r, metric) for r in inp["scored"] if r["family"] == fam and measured_x(r, metric) is not None]
    if metric and xs:
        panel = {"domain": "driving", "family": fam, "axis_metric": metric, "unit": "m", "source": "rows",
                 "x_range": [round(min(xs), 4), round(max(xs), 4)], "refs": reference_lines(fam),
                 "curves": {}, "strip_agent": "lead", "composition": []}
        for r in inp["stats"]:
            if r["family"] != fam or "curve" not in r:
                continue
            spec = curve_render_spec(r, min(xs), max(xs))
            spec["observed"] = observed_rates(inp["scored"], fam, r["model"], r["curve"], metric)
            panel["curves"].setdefault(r["model"], {})[r["curve"]] = spec
        comp = defaultdict(lambda: defaultdict(int))
        for r in inp["scored"]:
            if r["family"] == fam and r["model"] == panel["strip_agent"] and measured_x(r, metric) is not None:
                comp[round(measured_x(r, metric), 4)][r["outcome"]] += 1
        panel["composition"] = [{"x": x, "n": sum(o.values()), "outcomes": dict(o)} for x, o in sorted(comp.items())]
        ver = rows_version(inp, fam)
        panel.update({"label": versioned_label(fam, ver), "family_version": version_leaf(ver)})
        rows.append(panel)
    refs = isaac_reference_lines()
    for fam in F9_ISAAC_FAMILIES:
        found = isaac_document(store_root, fam, ISAAC_ROSTER_BRAIN)
        if found is None:
            continue
        path, d = found
        widths = [pt["width_m"] for pt in d["curves"]["p_go"]["points"]]
        x_range = [round(min(widths), 4), round(max(widths), 4)]
        brain = d["brain"]["id"]
        ver = document_version(store_root, d)
        rows.append({"domain": "warehouse", "family": fam, "axis_metric": "width_m", "unit": "m", "source": "documents",
                     "label": versioned_label(fam, ver), "family_version": version_leaf(ver),
                     "document": {"path": str(path.relative_to(store_root)), "sha256": _sha_file(path),
                                  "campaign": d["campaign"]["id"]},
                     "x_range": x_range, "refs": refs,
                     "curves": {brain: {"commitment": doc_curve_spec(d["curves"]["p_go"], x_range),
                                        "competence": doc_curve_spec(d["curves"]["p_clean"], x_range)}},
                     "strip_agent": brain,
                     "composition": [{"x": c["width_m"], "n": c["n"], "outcomes": c["outcomes"], "assignment": c["assignment"]}
                                     for c in d["outcome_composition"]]})
    return {"figure": "F9", "rows": rows,
            "note": ("one grammar, two simulators: commitment P(go) and competence P(clean pass) per agent on the "
                     "family's certified axis, with the estimator's own method rendered (step traces observed, bands "
                     "where a bracket or bootstrap says so, a curve only where a logistic fit is real); the strip is "
                     "the verbatim outcome composition of the named agent per variant")}


def build_f5(extract_a: Path, extract_b: Path):
    """Bifurcation as a SEPARATION curve: |Delta position|(t) between the
    two same-seed branches - exactly zero until the divergence, then
    growing. Cancels road-geometry artifacts that drown the raw paths."""
    def hero_xy(p):
        d = trajectory_extract.read_extract(p)
        hero = d["tracks"][str(d["hero_id"])]
        return ({f: (x, y) for f, x, y in
                 zip(hero["frames"], hero["x"], hero["y"])},
                d["dt_s"],
                [c["time_s"] for c in d["collisions"]], p.name)
    a_xy, dt, a_coll, a_name = hero_xy(extract_a)
    b_xy, _, b_coll, b_name = hero_xy(extract_b)
    common = sorted(set(a_xy) & set(b_xy))
    sep = [[round(f * dt, 2),
            round(math.dist(a_xy[f], b_xy[f]), 4)] for f in common[::2]]
    first_nonzero = next((t for t, s in sep if s > 0.001), None)
    return {"figure": "F5",
            "scenario": "fm_003 v23 @ seed 1 - byte-identical invocations",
            "separation_m": sep,
            "first_divergence_t_s": first_nonzero,
            "collisions_53_7": a_coll, "collisions_32_2": b_coll,
            "n_events": {"branch_53_7": len(a_coll),
                         "branch_32_2": len(b_coll)},
            "sources": {"branch_53_7": a_name, "branch_32_2": b_name},
            "inputs": {"branch_53_7": {"path": str(extract_a), "sha256": _sha_file(extract_a)},
                       "branch_32_2": {"path": str(extract_b), "sha256": _sha_file(extract_b)}}}


def build_f6(inp):
    fam, model = "fm_002_corridor_pincer", "bridgedrive"
    metric = axis_metric(inp["stats"], fam)
    rows = [r for r in inp["scored"]
            if r["family"] == fam and r["model"] == model]
    pts = sorted(
        [[round(measured_x(r, metric), 4),
          1 if r["status"] == "Completed" else 0,
          1 if r["outcome"] == "clean_thread" else 0,
          r["outcome"]] for r in rows])
    n_hidden = sum(1 for p in pts if p[1] == 1 and p[2] == 0)
    comp = next(r for r in inp["stats"]
                if r["family"] == fam and r["model"] == model
                and r["curve"] == "competence")
    return {"figure": "F6", "family": fam, "model": model,
            "axis_metric": metric, "points": pts,
            "contacts_hidden_by_completion": n_hidden,
            "family_aware_bracket": [comp["bracket_fail_side_x"],
                                     comp["bracket_pass_side_x"]]}


# --- paper tables (data layer + preview renders) ---------------------------
# Tables follow the same two-layer discipline as figures: build_t*()
# emits auditable table data (exact printed strings + provenance);
# render_table() makes PREVIEW images (Notion/contact-sheet grade).
# LaTeX fragments for the manuscript come from the same data later -
# the numbers are never hand-copied.

FAMILY_SETTING = {
    "fm_001_intrusion_sweep": "construction machine intrudes into lane",
    "fm_002_corridor_pincer": "street barriers pinch from both sides",
    "fm_003_asset_swap": "14 roadside objects, certified-identical pose",
    "fm_004_threshold_sweep": "two parked Chargers narrow the corridor",
    "fm_005_hero": "roadside clutter intrudes into the corridor",
    "fm_011_flow_speed_sweep": "signalized left turn across an oncoming flow, flow speed swept",
    "fm_012_source_dist_interval_sweep": "the same left turn, flow spacing swept",
    "fm_isaac_001": "warehouse aisle pinched by two slab arms",
    "fm_isaac_003": "floor stock narrows the warehouse aisle",
    "fm_isaac_004": "composed stacks narrow the warehouse aisle",
}
FAMILY_AXIS_DESC = {
    "fm_001_intrusion_sweep": "intrusion depth (m)",
    "fm_002_corridor_pincer": "min free corridor width (m)",
    "fm_003_asset_swap": "object identity (geometry fixed)",
    "fm_004_threshold_sweep": "min free corridor width (m)",
    "fm_005_hero": "max route intrusion (m)",
    "fm_011_flow_speed_sweep": "crossing time gap (s), via flow speed",
    "fm_012_source_dist_interval_sweep": "crossing time gap (s), via flow spacing",
    "fm_isaac_001": "min free gap (m)",
    "fm_isaac_003": "min free gap (m)",
    "fm_isaac_004": "min free gap (m)",
}
T1_ISAAC_FAMILIES = ("fm_isaac_001", "fm_isaac_003", "fm_isaac_004")
REPETITION_LABELS = {"traffic_seed_strata": "traffic seeds", "policy_reps": "policy samples"}


def repetition_axis(store_root, campaign_id, family_id):
    """(label, count) of a campaign's repetition axis, read from its declaration in the store:
    a CARLA declaration lists `seeds` (traffic seed strata), an Isaac one `repetitions` with a
    named `repetition_axis.kind`; None when the store holds no declaration for the pair."""
    path = Path(store_root) / "artifacts/examples/campaigns" / campaign_id / f"{family_id}.json"
    if not path.is_file():
        return None
    grid = json.loads(path.read_text()).get("declared_grid") or {}
    if "seeds" in grid:
        return ("traffic seeds", len(grid["seeds"]))
    if "repetitions" in grid:
        kind = (grid.get("repetition_axis") or {}).get("kind", "repeats")
        return (REPETITION_LABELS.get(kind, kind), len(grid["repetitions"]))
    return None
STOP_OUTCOMES = {"refusal", "correct_stop", "late_commit"}
CONTACT_OUTCOMES = {"contact_thread", "contact_blocked",
                    "contact_past_blockage"}


def _stat_row(inp, fam, model, curve):
    for r in inp["stats"]:
        if (r.get("family") == fam and r.get("model") == model
                and r.get("curve") == curve):
            return r
    return None


def fmt_boundary(r):
    """One boundary as its quotable string - encodes the quoting rules
    (flat -> Wilson floor; separation -> bracket, never the degenerate
    bootstrap; graded -> approximate with the caveat)."""
    if r is None:
        return "-"
    why = r.get("reason")
    tag = f"; {why.replace('_', ' ')}" if why and why not in ("all_success", "all_failure", "separated", "converged") else ""
    if r["method"] == "flat":
        ci = r.get("level_ci")
        if ci:
            return f"none in range (level {r['level']:.2f}, >= {ci[0]:.2f}{tag})"
        return f"none in range (level {r['level']:.2f}{tag})"
    bd = r.get("boundary")
    boot = r.get("bootstrap") or {}
    ci = boot.get("ci")
    ci_txt = f", CI [{ci[0]:.3f}, {ci[1]:.3f}]" if ci and ci[0] != ci[1] else ""
    if r["method"] == "bracket":
        # the estimator's own interval first (the bracket between adjacent tested values); the bootstrap over
        # the replicates' bracket midpoints, where it is not a point, follows (2026-10-05)
        return (f"{bd:.3f} m, bracket [{r['bracket_fail_side_x']}, "
                f"{r['bracket_pass_side_x']}]{ci_txt}{tag}")
    if boot.get("separation_fraction") == 0.0:
        return f"~{bd:.2f} m, graded (wide CI{tag})"
    if ci_txt:
        return f"{bd:.3f} m{ci_txt}{tag}"
    return (f"{bd:.3f} m, bracket [{r['bracket_fail_side_x']}, "
            f"{r['bracket_pass_side_x']}]{tag}")


def build_t1(inp):
    """Methods family table: paper name <-> internal id <-> axis, with the executed scale
    (variants x repeats = runs per agent). The threading families come from the pooled rows;
    since 2026-09-23 the flow families and the warehouse families follow, read from their
    findings documents in the store (one document per family and agent: the variant count is
    the composition's rows, the runs per agent its n), with the repetition axis named from the
    campaign's declaration (traffic seeds on CARLA, policy samples on Isaac)."""
    store_root = inp.get("store_root", REPO_ROOT)
    rows, versions = [], {}
    for fam in sorted({r["family"] for r in inp["scored"]}):
        fr = [r for r in inp["scored"] if r["family"] == fam]
        models = {r["model"] for r in fr}
        per_agent = max(sum(1 for r in fr if r["model"] == m)
                        for m in models)
        ver = rows_version(inp, fam)
        versions[fam] = version_leaf(ver)
        rows.append([versioned_label(fam, ver), fam,
                     FAMILY_AXIS_DESC.get(fam, "?"),
                     FAMILY_SETTING.get(fam, "?"),
                     str(len({r["variant_index"] for r in fr})),
                     f"{len({r['seed'] for r in fr})} traffic seeds",
                     str(per_agent)])

    def document_row(fam, doc):
        comp = doc["outcome_composition"]
        axis = repetition_axis(store_root, doc["campaign"]["id"], fam)
        ver = document_version(store_root, doc)
        versions[fam] = version_leaf(ver)
        return [versioned_label(fam, ver), fam, FAMILY_AXIS_DESC.get(fam, "?"), FAMILY_SETTING.get(fam, "?"),
                str(len(comp)), f"{axis[1]} {axis[0]}" if axis else "?", str(sum(c["n"] for c in comp))]

    for fam in FLOW_FAMILIES:
        docs = flow_documents(store_root, fam)
        if docs:
            # every agent's document covers the family's grid; the largest n is the policy's
            _, doc = max(docs.values(), key=lambda pd: sum(c["n"] for c in pd[1]["outcome_composition"]))
            rows.append(document_row(fam, doc))
    for fam in T1_ISAAC_FAMILIES:
        found = isaac_document(store_root, fam, ISAAC_ROSTER_BRAIN)
        if found:
            rows.append(document_row(fam, found[1]))
    return {"figure": "T1", "title": "Table 1 - Scenario families",
            "col_labels": ["Family", "Internal id", "Swept axis",
                           "Setting", "Variants", "Repeats", "Runs/agent"],
            "rows": rows, "family_versions": versions}


def build_t2(inp):
    """The dissociation compressed (Results 6.2), tied pair."""
    fam = "fm_004_threshold_sweep"
    f1 = build_f1(inp)
    cell = f1["inversion_cell"] or {}

    def outcome_split(model):
        ocs = [r["outcome"] for r in inp["scored"]
               if r["family"] == fam and r["model"] == model]
        stops = sum(1 for o in ocs if o in STOP_OUTCOMES)
        contacts = sum(1 for o in ocs if o in CONTACT_OUTCOMES)
        return len(ocs), stops, contacts

    def worst_push(model):
        if not inp["metrics"]:
            return "n/a (no metrics dir)"
        worst = 0.0
        for r in inp["metrics"]:
            if r.get("family") != fam or r.get("model") != model:
                continue
            for po in r.get("per_obstacle") or []:
                if po.get("moved_by_ego"):
                    worst = max(worst, po.get("displacement_m") or 0.0)
        return f"{worst:.2f} m"

    n_l, stop_l, con_l = outcome_split("lead")
    n_b, stop_b, con_b = outcome_split("bridgedrive")
    labels = ["Mean driving score", "Commitment boundary",
              "Non-clean outcome vocabulary", f"Contact runs (of {n_l})",
              "Worst ego-attributed obstacle displacement",
              "Inversion cell (same variant, same seed)"]
    rows = [
        [f"{f1['mean_ds']['lead']}", f"{f1['mean_ds']['bridgedrive']}"],
        [fmt_boundary(_stat_row(inp, fam, "lead", "commitment")),
         fmt_boundary(_stat_row(inp, fam, "bridgedrive", "commitment"))],
        [f"stops only ({stop_l}/{stop_l + con_l})",
         f"contacts only ({con_b}/{stop_b + con_b})"],
        [str(con_l), str(con_b)],
        [worst_push("lead"), worst_push("bridgedrive")],
        [f"{cell.get('lead', {}).get('ds')} (correct stop)",
         f"{cell.get('bridgedrive', {}).get('ds')} "
         "(contact past blockage)"],
    ]
    return {"figure": "T2",
            "title": "Table 2 - The dissociation compressed "
                     "(Charger gauntlet, 60 runs/agent)",
            "col_labels": ["TFv6", "BridgeDrive"],
            "row_labels": labels, "rows": rows}


def build_t3(inp):
    """Outcome-taxonomy incidence per agent per family (Results 6.4): the paper roster in its
    order, then (2026-09-23) every extension agent with rows, in order of appearance; a grouped
    agent's checkpoints are summed into one column (CHECKPOINT_GROUPS, the atlas convention)."""
    scored = inp["scored"] + inp.get("extension_scored", [])
    fams = sorted({r["family"] for r in scored})
    models = ["lead", "tfpp", "bridgedrive", "pdm_lite_expert"]
    present = [m for m in models
               if any(r["model"] == m for r in inp["scored"])]
    for r in inp.get("extension_scored", []):
        col = _f7_column(r["model"])
        if col not in present:
            present.append(col)
    rows, labels = [], []
    for fam in fams:
        fr = [r for r in scored if r["family"] == fam]
        for oc in sorted({r["outcome"] for r in fr}):
            labels.append(f"{family_label(fam)} - "
                          f"{OUTCOME_LABELS.get(oc, oc)}")
            rows.append([str(sum(1 for r in fr if _f7_column(r["model"]) == m
                                 and r["outcome"] == oc))
                         for m in present])
    return {"figure": "T3",
            "title": "Table 3 - Outcome incidence per agent",
            "col_labels": [CHECKPOINT_LABEL[m] if m in CHECKPOINT_GROUPS else STYLE["labels"].get(m, m)
                           for m in present],
            "models": present,
            "extension_models": [m for m in present if m not in models],
            "row_labels": labels, "rows": rows}


def build_t5(inp):
    """Boundary table (Results 6.1): every boundary with its honest
    interval, formatted per the quoting rules."""
    rows, labels = [], []
    stats = {"stats": inp["stats"] + inp.get("extension_stats", [])}
    fams = [f for f in FM_ORDER
            if any(r.get("family") == f for r in stats["stats"])]
    for fam in fams:
        models = sorted({r["model"] for r in inp["stats"]
                         if r.get("family") == fam and "curve" in r})
        # the extension agents (2026-09-23) follow the roster in order of appearance, every
        # checkpoint of a grouped agent as its own row (the checkpoint spread is a measurement, F8)
        for r in inp.get("extension_stats", []):
            if r.get("family") == fam and "curve" in r and r["model"] not in models:
                models.append(r["model"])
        for m in models:
            labels.append(f"{family_label(fam)} - {STYLE['labels'].get(m, m)}")
            rows.append([
                fmt_boundary(_stat_row(stats, fam, m, "commitment")),
                fmt_boundary(_stat_row(stats, fam, m, "competence"))])
    return {"figure": "T5",
            "title": "Table 5 (working) - Boundaries with intervals",
            "col_labels": ["Commitment P(go)", "Competence P(clean pass)"],
            "row_labels": labels, "rows": rows}


T6_ROSTER = ["lead", "tfpp", "bridgedrive", "pdm_lite_expert"]
ISAAC_ROSTER_BRAIN = "x_mobility"   # the paper's warehouse agent (the stock brain); the fine-tunes of v07 are extensions


def _mean_ds(rows):
    return round(sum(r["driving_score"] for r in rows) / len(rows), 1) if rows else None


def _models_in(rows):
    """The paper roster in its order, then the extension models by appearance (each checkpoint
    its own row, as T5 lists them)."""
    models = [m for m in T6_ROSTER if any(r["model"] == m for r in rows)]
    for r in rows:
        if r["model"] not in models:
            models.append(r["model"])
    return models


def _gap(com, comp):
    if com is None or comp is None:
        return "-"
    if com["method"] == "flat":
        return "no commitment boundary" if (com.get("level") or 0) >= 0.5 else "never commits"
    if comp["method"] == "flat":
        return "always clean" if (comp.get("level") or 0) >= 0.5 else "never clean"
    return f"{abs(comp['boundary'] - com['boundary']):.3f} m"


def build_t6(inp):
    """The score beside the two boundaries (gold 33, 2026-09-25; Ellington's table): for every
    atlas family with stats rows, per agent (roster then extension, each checkpoint its own
    row), the mean driving score over all its runs beside its commitment and competence
    boundaries as T5 formats them and the gap between them; rows ordered by the mean score
    within a family. The caveats (a boundary located by contact with a prop that tips at
    trigger) live in the caption, not here."""
    scored = inp["scored"] + inp.get("extension_scored", [])
    stats = {"stats": inp["stats"] + inp.get("extension_stats", [])}
    fams = [f for f in FM_ORDER if any(r.get("family") == f for r in stats["stats"])]
    rows, labels = [], []
    for fam in fams:
        fr = [r for r in scored if r["family"] == fam]
        cells = []
        for m in _models_in(fr):
            mr = [r for r in fr if r["model"] == m]
            com, comp = _stat_row(stats, fam, m, "commitment"), _stat_row(stats, fam, m, "competence")
            cells.append((_mean_ds(mr), m, [str(len(mr)), f"{_mean_ds(mr):.1f}",
                                            fmt_boundary(com), fmt_boundary(comp), _gap(com, comp)]))
        for _, m, row in sorted(cells, key=lambda c: -c[0]):
            labels.append(f"{family_label(fam)} - {STYLE['labels'].get(m, m)}")
            rows.append(row)
    return {"figure": "T6",
            "title": "Table 6 - Mean driving score beside the two boundaries",
            "col_labels": ["Runs", "Mean DS", "Commitment boundary", "Competence boundary", "Gap"],
            "row_labels": labels, "rows": rows, "families": fams}


def build_t7(inp):
    """The score split by side (gold 33, 2026-09-25): for every family whose rows carry passable
    and blocked widths, per agent (roster then extension, each checkpoint its own row), the mean
    driving score over all runs, over the passable widths and over the blocked widths, and what
    happened on the blocked widths (the outcome counts there, in the taxonomy's vocabulary);
    rows ordered by the mean score within a family. `prices` carries the mean score per outcome
    class over all agents' runs of the family (the blocked-side price list), `sides` the measured
    widths of each side."""
    scored = inp["scored"] + inp.get("extension_scored", [])
    fams = [f for f in FM_ORDER if {r.get("passable") for r in scored if r["family"] == f} >= {True, False}]
    rows, labels, prices, sides = [], [], {}, {}
    for fam in fams:
        fr = [r for r in scored if r["family"] == fam]
        metric = axis_metric(inp["stats"] + inp.get("extension_stats", []), fam)
        sides[fam] = {"axis_metric": metric,
                      "passable_widths": sorted({round(measured_x(r, metric), 3) for r in fr if r["passable"]}),
                      "blocked_widths": sorted({round(measured_x(r, metric), 3) for r in fr if not r["passable"]})}
        by_class = defaultdict(list)
        for r in fr:
            by_class[r["outcome"]].append(r)
        prices[fam] = {oc: _mean_ds(rs) for oc, rs in sorted(by_class.items(), key=lambda kv: -_mean_ds(kv[1]))}
        cells = []
        for m in _models_in(fr):
            mr = [r for r in fr if r["model"] == m]
            blocked = [r for r in mr if not r["passable"]]
            counts = defaultdict(int)
            for r in blocked:
                counts[r["outcome"]] += 1
            on_blocked = ", ".join(f"{OUTCOME_LABELS.get(oc, oc)} {n}"
                                   for oc, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
            cells.append((_mean_ds(mr), m, [str(len(mr)), f"{_mean_ds(mr):.1f}",
                                            f"{_mean_ds([r for r in mr if r['passable']]):.1f}",
                                            f"{_mean_ds(blocked):.1f}", on_blocked]))
        for _, m, row in sorted(cells, key=lambda c: -c[0]):
            labels.append(f"{family_label(fam)} - {STYLE['labels'].get(m, m)}")
            rows.append(row)
    return {"figure": "T7",
            "title": "Table 7 - The score split by side (passable and blocked widths)",
            "col_labels": ["Runs", "Mean DS", "DS, passable widths", "DS, blocked widths",
                           "On the blocked widths"],
            "row_labels": labels, "rows": rows, "families": fams, "prices": prices, "sides": sides}


T8_ORDER = ["fm_001_intrusion_sweep", "fm_002_corridor_pincer", "fm_003_asset_swap", "fm_004_threshold_sweep",
            "fm_005_hero", *FLOW_FAMILIES]
T8_ISAAC_FAMILIES = T1_ISAAC_FAMILIES     # the warehouse families follow the driving ones (2026-10-01)
T8_RULE = ("Decision accuracy is the balanced mean of two correct-call rates, the decision read as the commitment "
           "(p_go) alone: on the threading families a go on a passable width and a stop on a blocked width are the "
           "correct calls (a contact on a passable width is still a go; anomaly outcomes are excluded and counted); "
           "on the flow families the bands of the declared gap certificate stand in for the widths: a go on a usable "
           "window and a no-go on a wait window are the correct calls, and the conditional band (a rolling go is "
           "right, a go from rest is wrong) is reported beside the accuracy, never inside it; on the warehouse "
           "families every width carries its assignment in the brain's document; a go on a passable width is the "
           "correct call, and on a blocked width the correct call is no crossing of the gap line (a stop or the "
           "detour, the expert's answer), read from the point's gate_k; a contact-free completion of a "
           "certified-blocked width is an anomaly on both backends, excluded and counted. A family without a "
           "blocked or wait side has no balanced accuracy; its passable rate is shown and the cell says so. Every "
           "row names the family version its document or pool binds (the registry over the manifest hash, "
           "artifacts/examples/family_versions/registry.json): two rows of one family on different versions are two realizations, never "
           "one family.")
T9_RULE = ("Per agent, the mean of its Table 8 decision accuracies over the families that have a wait side (each "
           "family is one question and weighs the same; the count of those families is printed beside the mean, "
           "and the pooled balanced accuracy over the same families is kept in the data), then one column per "
           "family: the decision accuracy where the family has a wait side, the go rate on the passable side "
           "where it has none (the column header carries '(go rate)', and those columns never enter the mean), "
           "and '-' where the agent did not run the family. A family whose agents sit on different versions "
           "(the registry over the manifest hash) splits into one column per version, so no column averages two "
           "realizations. Driving agents and warehouse brains are two blocks, each ordered by the mean, ties in "
           "roster order; PlanT 2.0's checkpoints stay separate rows.")


def _rate_cell(n, k):
    return f"{k}/{n} ({k / n:.2f})" if n else "no runs"


def _decision_sides_threading(rows):
    """(go side, wait side, anomalies) of threading rows: {n, k} per side on the commitment coding."""
    go, wait, anomalies = {"n": 0, "k": 0}, {"n": 0, "k": 0}, 0
    for r in rows:
        code = boundary_stats.code_commitment(r["outcome"])
        if code is None:
            anomalies += 1
            continue
        side = go if r["passable"] else wait
        side["n"] += 1
        side["k"] += int(code == 1) if r["passable"] else int(code == 0)
    return go, wait, anomalies


def _decision_sides_isaac(doc):
    """(go side, wait side, anomalies) of a warehouse brain's document: the p_go points carry each
    width's assignment and already exclude the anomaly cells (a contact-free completion of a
    certified-blocked width, findings_v2 since 2026-10-03). A go on a passable width (either line)
    is the correct call there; on a blocked width the correct call is NO GATE CROSSING, a stop or
    the detour, because the detour is the warehouse's right answer (the expert's divert_clean;
    Milan 2026-10-03): the point's gate_k counts the wrong calls. A document without the split
    (emitted before 2026-10-03) is refused, never read as if a detour were a wrong call. The
    anomalies are counted from the outcome composition, as the driving rows count theirs."""
    go, wait = {"n": 0, "k": 0}, {"n": 0, "k": 0}
    for pt in doc["curves"]["p_go"]["points"]:
        if pt["assignment"] == "defect":
            # a declared scenario defect (scl_core/scenario_defects.py): the width is in the
            # document's data layer and in no decision side, as a flow defect cell is in no band
            continue
        if pt["assignment"] not in ("passable", "blocked"):
            raise ValueError(f"width {pt['width_m']}: assignment {pt['assignment']!r} is neither passable nor blocked, nor a declared defect")
        if pt["assignment"] == "passable":
            go["n"] += pt["n"]
            go["k"] += pt["k"]
        else:
            if "gate_k" not in pt:
                raise ValueError(f"width {pt['width_m']}: p_go point without gate_k / detour_k (a document emitted "
                                 f"before the line split); re-emit it, a detour is not a wrong call")
            wait["n"] += pt["n"]
            wait["k"] += pt["n"] - pt["gate_k"]
    anomalies = sum(v for c in doc.get("outcome_composition", []) for o, v in c["outcomes"].items()
                    if o.startswith("anomaly_"))
    return go, wait, anomalies


def build_t8(inp):
    """Decision accuracy over the seven driving families and the three warehouse families (F55,
    2026-09-28; Ellington: "we only have 20 blocked routes, all from the charger corridor"; the
    warehouse rows 2026-10-01): per family and agent, the correct-call rate on the passable widths
    (usable windows), on the blocked widths (wait windows), their balanced mean, and for the flow
    families the conditional band beside it. Threading families are read from the pooled scored rows
    on the commitment coding; flow families from the store's flow findings documents (their
    decision_summary, scored against the declared gap certificate); warehouse families from every
    brain's newest document in the store (the p_go points by assignment). Rows ordered by family
    (T8_ORDER, then T8_ISAAC_FAMILIES) then agent (roster first)."""
    scored = inp["scored"] + inp.get("extension_scored", [])
    store_root = inp.get("store_root", REPO_ROOT)
    rows, labels, cells, documents = [], [], {}, {}

    def place(fam, agent, cell, ver):
        # every cell names the realization its source binds (the registry's version, 2026-10-03)
        cell.update({"family_version": version_leaf(ver), "family_label": versioned_label(fam, ver)})
        cells.setdefault(fam, {})[agent] = cell
        labels.append(f"{cell['family_label']} - {STYLE['labels'].get(agent, agent)}")

    for fam in T8_ORDER:
        if fam in FLOW_FAMILIES:
            docs = flow_documents(inp["store_root"], fam)
            if not docs:
                continue
            documents[fam] = {m: {"path": str(p.relative_to(inp["store_root"])), "sha256": _sha_file(p),
                                  "campaign": d["campaign"]["id"]} for m, (p, d) in docs.items()}
            models = [m for m in T6_ROSTER if m in docs] + [m for m in docs if m not in T6_ROSTER]
            for m in models:
                summary = docs[m][1].get("decision_summary") or {}
                if not summary.get("bands"):
                    continue   # scored without a certificate: no decision to report
                go, wait, cond = summary["usable"], summary["wait"], summary["conditional"]
                cell = {"go_side": {"n": go["n"], "k": go["k"]}, "wait_side": {"n": wait["n"], "k": wait["k"]},
                        "conditional": {"n": cond["n"], "k": cond["k"]}, "anomalies": 0,
                        "runs": sum(b["runs"] for b in summary["bands"].values())}
                place(fam, m, cell, document_version(inp["store_root"], docs[m][1]))
                rows.append(_t8_row(cell, "usable", "wait"))
            continue
        fr = [r for r in scored if r["family"] == fam]
        if not fr:
            continue
        ver = rows_version(inp, fam)
        for m in _models_in(fr):
            mr = [r for r in fr if r["model"] == m]
            go, wait, anomalies = _decision_sides_threading(mr)
            cell = {"go_side": go, "wait_side": wait, "conditional": None, "anomalies": anomalies, "runs": len(mr)}
            place(fam, m, cell, ver)
            rows.append(_t8_row(cell, "passable", "blocked"))
    for fam in T8_ISAAC_FAMILIES:
        docs = isaac_documents(store_root, fam)
        if not docs:
            continue
        documents[fam] = {b: {"path": str(p.relative_to(store_root)), "sha256": _sha_file(p),
                              "campaign": d["campaign"]["id"]} for b, (p, d) in docs.items()}
        for b in _brains_in(docs):
            go, wait, anomalies = _decision_sides_isaac(docs[b][1])
            cell = {"go_side": go, "wait_side": wait, "conditional": None, "anomalies": anomalies,
                    "runs": go["n"] + wait["n"] + anomalies}
            place(fam, b, cell, document_version(store_root, docs[b][1]))
            rows.append(_t8_row(cell, "passable", "blocked"))
    for fam in cells:
        for cell in cells[fam].values():
            go, wait = cell["go_side"], cell["wait_side"]
            cell["accuracy"] = (round((go["k"] / go["n"] + wait["k"] / wait["n"]) / 2, 4)
                                if go["n"] and wait["n"] else None)
    return {"figure": "T8",
            "title": "Table 8 - Decision accuracy over the ten families, seven driving and three warehouse (the "
                     "flow families' wait side from the gap certificates)",
            "col_labels": ["Runs", "Go on passable / usable", "Wait on blocked / wait", "Decision accuracy",
                           "Conditional band (rolling go)"],
            "row_labels": labels, "rows": rows, "families": list(cells), "cells": cells,
            "documents": documents, "rule": T8_RULE}


def build_t9(inp):
    """Table 8 pivoted per agent (Milan, 2026-10-01: "an average decision accuracy table for each
    model and per-family decision accuracies"): one row per agent, the mean of its decision
    accuracies over the families with a wait side (the count beside it; the pooled balanced
    accuracy in the data), then one column per family; families without a wait side show the go
    rate and say so in the header. Two blocks (driving agents, warehouse brains), each ordered by
    the mean, ties in roster order."""
    t8 = build_t8(inp)
    cells = t8["cells"]
    fams = [f for f in [*T8_ORDER, *T8_ISAAC_FAMILIES] if f in cells]
    # one column per REALIZATION (2026-10-03, the registry): a family whose cells all sit on one version keeps its
    # id; a family realized twice in the table (the stock brain on the pallet stacks, the fine-tunes on the
    # cartons) splits into one column per version, keyed <family>@vN, so no column averages two obstacles
    columns = []
    for fam in fams:
        versions = sorted({(c["family_version"] or {}).get("version") for c in cells[fam].values()},
                          key=lambda v: (v is None, v or 0))
        split = len(versions) > 1
        for v in versions:
            agents_on = {m for m, c in cells[fam].items() if (c["family_version"] or {}).get("version") == v}
            label = next(c["family_label"] for m, c in cells[fam].items() if m in agents_on)
            columns.append({"id": f"{fam}@v{v}" if split and v is not None else fam, "family": fam, "version": v,
                            "label": label, "agents": sorted(agents_on)})
    col_of = {(c["family"], m): c["id"] for c in columns for m in c["agents"]}
    wait_cols = [c["id"] for c in columns if any(cells[c["family"]][m]["wait_side"]["n"] for m in c["agents"])]
    agents = {}
    for fam in fams:
        for m, c in cells[fam].items():
            col = col_of[(fam, m)]
            a = agents.setdefault(m, {"domain": "warehouse" if fam in T8_ISAAC_FAMILIES else "driving",
                                      "families_with_wait_side": [], "per_family": {},
                                      "pooled_sides": {"go": {"n": 0, "k": 0}, "wait": {"n": 0, "k": 0}}})
            go, wait = c["go_side"], c["wait_side"]
            if col in wait_cols:
                a["per_family"][col] = {"accuracy": c["accuracy"], "go_side": dict(go), "wait_side": dict(wait),
                                        "family_version": c["family_version"]}
                if c["accuracy"] is not None:
                    a["families_with_wait_side"].append(col)
                    for side, src in (("go", go), ("wait", wait)):
                        a["pooled_sides"][side]["n"] += src["n"]
                        a["pooled_sides"][side]["k"] += src["k"]
            else:
                a["per_family"][col] = {"go_rate": round(go["k"] / go["n"], 4) if go["n"] else None, "go_side": dict(go),
                                        "family_version": c["family_version"]}
    for a in agents.values():
        accs = [a["per_family"][f]["accuracy"] for f in a["families_with_wait_side"]]
        a["mean_accuracy"] = round(sum(accs) / len(accs), 4) if accs else None
        pg, pw = a["pooled_sides"]["go"], a["pooled_sides"]["wait"]
        a["pooled_accuracy"] = round((pg["k"] / pg["n"] + pw["k"] / pw["n"]) / 2, 4) if pg["n"] and pw["n"] else None
    roster = [m for m in T6_ROSTER if m in agents]
    for fam in fams:
        roster += [m for m in cells[fam] if m not in roster]
    def block(domain):
        ids = [m for m in roster if agents[m]["domain"] == domain]
        return sorted(ids, key=lambda m: (-(agents[m]["mean_accuracy"] if agents[m]["mean_accuracy"] is not None else -1.0),
                                          ids.index(m)))
    order = block("driving") + block("warehouse")
    rows, labels = [], []
    for m in order:
        a = agents[m]
        row = [str(len(a["families_with_wait_side"])),
               f"{a['mean_accuracy']:.2f}" if a["mean_accuracy"] is not None else "n/a"]
        for col in columns:
            pf_ = a["per_family"].get(col["id"])
            if pf_ is None:
                row.append("-")
            elif col["id"] in wait_cols:
                row.append(f"{pf_['accuracy']:.2f}" if pf_["accuracy"] is not None else "n/a")
            else:
                row.append(f"{pf_['go_rate']:.2f}" if pf_["go_rate"] is not None else "no runs")
        rows.append(row)
        labels.append(STYLE["labels"].get(m, m))
    return {"figure": "T9",
            "title": "Table 9 - Decision accuracy per agent over the ten families (the mean over the families "
                     "with a wait side; the go rate where a family has none)",
            "col_labels": ["Families with a wait side", "Decision accuracy (mean)",
                           *[c["label"] + ("" if c["id"] in wait_cols else " (go rate)") for c in columns]],
            "row_labels": labels, "rows": rows, "row_ids": order, "families": fams, "columns": columns,
            "wait_side_columns": wait_cols, "agents": agents, "documents": t8["documents"], "rule": T9_RULE}


def _t8_row(cell, go_name, wait_name):
    go, wait, cond = cell["go_side"], cell["wait_side"], cell["conditional"]
    if go["n"] and wait["n"]:
        accuracy = f"{(go['k'] / go['n'] + wait['k'] / wait['n']) / 2:.2f}"
    elif go["n"]:
        accuracy = f"n/a (no {wait_name} side)"
    else:
        accuracy = f"n/a (no {go_name} side)"
    anomalies = f" (+{cell['anomalies']} anomalies excluded)" if cell["anomalies"] else ""
    return [f"{cell['runs']}{anomalies}", _rate_cell(go["n"], go["k"]), _rate_cell(wait["n"], wait["k"]), accuracy,
            _rate_cell(cond["n"], cond["k"]) if cond else "-"]


TABLE_BUILDERS = {"T1": build_t1, "T2": build_t2, "T3": build_t3,
                  "T5": build_t5, "T6": build_t6, "T7": build_t7, "T8": build_t8, "T9": build_t9}


# --- rendering (consumes figure_data only) ---------------------------------

def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "svg.hashsalt": "scl",
                         "pdf.compression": 9})
    return plt


def _draw_curvespec(ax, spec, color, x_range, label=None, dodge=0.0, lw=None):
    """ONE grammar for every agent: observed dots + a step line through
    the agent's own rates (the DATA), with the inference overlaid -
    shaded band for a bracket, diamond at its midpoint, logistic curve
    where the fit is real, Wilson fill for flat. Asymmetric grammars
    (line for one agent, dots for another) read as missing data."""
    if spec.get("observed"):
        xs = [p[0] for p in spec["observed"]]
        # presentation-only dodge so coincident 0/1 series from
        # different agents stay visible; the data layer is untouched
        rs = [min(1 - dodge, max(dodge, p[1])) if p[1] in (0.0, 1.0)
              else p[1] for p in spec["observed"]]
        ax.step(xs, rs, where="mid", color=color, lw=lw or 1.6, alpha=0.9,
                label=label)
        ax.plot(xs, rs, "o", color=color, ms=3.5, alpha=0.8,
                markeredgecolor="white", markeredgewidth=0.4, zorder=3)
        label = None  # legend entry attaches to the step line
    if spec["kind"] == "flat":
        if spec.get("level_ci"):
            ax.fill_between(x_range, spec["level_ci"][0],
                            spec["level_ci"][1], color=color, alpha=0.10)
        if not spec.get("observed"):
            ax.hlines(spec["level"], *x_range, color=color, lw=2,
                      label=label)
    else:
        band = sorted(v for v in (spec.get("band_ci") or spec["band"])
                      if v is not None)
        span = x_range[1] - x_range[0]
        unconstrained = len(band) == 2 and (band[1] - band[0]) > UNCONSTRAINED_BAND * max(span, 1e-9)
        if len(band) == 2 and not unconstrained:
            # render clipped to the tested range - a bootstrap CI can
            # extrapolate beyond it (data layer keeps the true interval);
            # an interval wider than most of the range says "unconstrained"
            # and is not painted over the other agents' data
            lo = max(band[0], x_range[0])
            hi = min(band[1], x_range[1])
            if hi > lo:
                ax.axvspan(lo, hi, color=color,
                           alpha=0.25 if spec.get("band_ci") else 0.15)
        if spec.get("curve"):
            xs, ps = zip(*spec["curve"])
            ax.plot(xs, ps, color=color, lw=lw or 2, label=label)
            label = None  # legend entry attaches to the curve
        if spec.get("boundary") is not None:
            # the boundary estimate gets its diamond whether the cell
            # renders point-only or as a fitted curve (there it marks
            # the p=0.5 crossing explicitly - a wide band needs its
            # center stated). near-coincident boundaries from
            # different agents: dodge the diamond vertically
            # (presentation only - x stays truthful; overlapping CIs
            # mean no ordering is implied)
            y = 0.5 + (dodge - 0.045 if dodge else 0.0)
            b = spec["boundary"]
            if x_range[0] <= b <= x_range[1]:
                # hollow when the interval spans most of the tested range: the estimate exists, the
                # data does not pin it
                ax.plot([b], [y], "D", color=color, ms=6, label=label,
                        markerfacecolor="white" if unconstrained else color)
            else:
                # outside the tested range: a hollow diamond at the nearer edge (the data layer
                # keeps the estimate; the axis stays the tested range)
                edge = x_range[0] if b < x_range[0] else x_range[1]
                ax.plot([edge], [y], "D", color=color, ms=6, markerfacecolor="white", label=label)


def _variants_rug(ax, specs):
    """Axis ticks marking the (non-uniform) variant schedule. No legend
    entry: the '|' glyph reads as a stray pipe there - the caption
    explains the ticks instead."""
    from matplotlib.transforms import blended_transform_factory
    xs = sorted({p[0] for s in specs if s.get("observed")
                 for p in s["observed"]})
    if xs:
        # x in data coords, y pinned to the axis frame - the rug must
        # not collide with near-zero data series
        tf = blended_transform_factory(ax.transData, ax.transAxes)
        ax.plot(xs, [0.015] * len(xs), "|", color="#555555", ms=8,
                transform=tf, clip_on=False, zorder=1)


def _draw_refs(ax, refs):
    if refs.get("body_limit_m"):
        ax.axvline(refs["body_limit_m"], **STYLE["ref_body"], lw=1)
    if refs.get("policy_floor_m"):
        ax.axvline(refs["policy_floor_m"], **STYLE["ref_policy"], lw=1)


PAPER_OUTCOME_LABELS = {"contact_past_blockage": "completes by pushing through",
                        "correct_stop": "refuses and stops"}


def render_f1(d, out):
    plt = _mpl()
    fig, (top, bot) = plt.subplots(2, 1, figsize=(6.5, 5.2), sharex=True)
    for model, pts in d["ds_points"].items():
        xs, ds = [p[0] for p in pts], [p[1] for p in pts]
        top.plot(xs, ds, "o", color=STYLE["colors"][model], ms=3,
                 alpha=0.35)  # every run: a sample, shown as such
        mx = [p[0] for p in d["ds_mean_by_x"][model]]
        my = [p[1] for p in d["ds_mean_by_x"][model]]
        top.plot(mx, my, "o-", color=STYLE["colors"][model], ms=4,
                 label=f"{STYLE['labels'][model]} (mean {d['mean_ds'][model]})")
    if d.get("inversion_cell"):
        # ring the pair and lead a line to each label (same-width
        # placement of the rings is the point); outcome text is short
        # prose - the taxonomy codes are introduced in Methods, after
        # this figure appears
        cell = d["inversion_cell"]
        offs = {"lead": (-58, 26), "bridgedrive": (-66, 30)}
        for m in TIED_PAIR:
            c = cell[m]
            oc = PAPER_OUTCOME_LABELS.get(c["outcome"], c["outcome"])
            top.plot([c["x"]], [c["ds"]], "o", ms=10, mfc="none",
                     mec="#222222", mew=0.9, zorder=5)
            top.annotate(f"{c['ds']} — {oc}", (c["x"], c["ds"]),
                         fontsize=7, ha="center", xytext=offs[m],
                         textcoords="offset points", zorder=5,
                         arrowprops={"arrowstyle": "-", "lw": 0.7,
                                     "color": "#333333", "shrinkB": 8})
    top.set_ylabel("driving score")
    top.tick_params(labelbottom=True)  # contact-sheet legibility: label
    top.legend(fontsize=7)             # the shared axis on BOTH panels
    for i, (model, spec) in enumerate(d["commitment"].items()):
        _draw_curvespec(bot, spec, STYLE["colors"][model], d["x_range"],
                        STYLE["labels"][model], dodge=0.018 * (i + 1))
        if spec["kind"] == "flat":
            bot.annotate(f"{STYLE['labels'][model]}: no decision "
                         "boundary in tested range",
                         (d["x_range"][0] + 0.05, 0.88),
                         fontsize=7, color=STYLE["colors"][model])
    _variants_rug(bot, d["commitment"].values())
    _draw_refs(bot, d["refs"])
    bot.set_ylim(-0.06, 1.05)
    bot.set_ylabel("P(go)")
    bot.set_xlabel(d["axis_metric"].replace("_", " ") + " (m)")
    bot.legend(fontsize=7)
    # no in-figure suptitle: the caption carries the claim (venue
    # convention); the deck variant F1D keeps a title because decks
    # have no captions
    _save(fig, out, "F1")


DECK_OUTCOME_LABELS = {"contact_past_blockage": "rams through",
                       "correct_stop": "correctly refuses"}


def render_f1d(d, out):
    """Deck grammar, standalone legibility: no dots, no CI bands, and
    ONE MINI-PANEL PER AGENT under the DS panel - the commitment/
    competence gap must read as THAT agent's property; in a shared
    overlay the dominant gap turns into what looks like a page
    background under everyone's curves. Plain-language labels."""
    plt = _mpl()
    fig = plt.figure(figsize=(7.2, 5.4))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.1], hspace=0.3,
                          wspace=0.08)
    top = fig.add_subplot(gs[0, :])
    panes = [fig.add_subplot(gs[1, 0])]
    panes.append(fig.add_subplot(gs[1, 1], sharey=panes[0]))
    for model, pts in d["ds_points"].items():
        top.plot([p[0] for p in pts], [p[1] for p in pts], "o",
                 color=STYLE["colors"][model], ms=3, alpha=0.35)
        mx = [p[0] for p in d["ds_mean_by_x"][model]]
        my = [p[1] for p in d["ds_mean_by_x"][model]]
        top.plot(mx, my, "o-", color=STYLE["colors"][model], ms=4,
                 label=f"{STYLE['labels'][model]} (mean {d['mean_ds'][model]})")
    if d.get("inversion_cell"):
        # ring the actual pair of samples and lead a line to each
        # label - "59" floating near a cloud of dots names nothing
        # both labels up-left of their rings: parallel leader lines,
        # and the lead label stays out of the low dot cloud
        offs = {"lead": (-58, 26), "bridgedrive": (-66, 30)}
        for m in TIED_PAIR:
            c = d["inversion_cell"][m]
            oc = DECK_OUTCOME_LABELS.get(c["outcome"], c["outcome"])
            top.plot([c["x"]], [c["ds"]], "o", ms=10, mfc="none",
                     mec="#222222", mew=0.9, zorder=5)
            top.annotate(f"{c['ds']:.0f} - {oc}", (c["x"], c["ds"]),
                         fontsize=7.5, fontweight="bold", ha="center",
                         xytext=offs[m], textcoords="offset points",
                         zorder=5,
                         arrowprops={"arrowstyle": "-", "lw": 0.7,
                                     "color": "#333333", "shrinkB": 8})
    top.set_ylabel("driving score")
    top.tick_params(labelbottom=True)
    top.legend(fontsize=7)

    def _steps(spec, dodge=0.018):
        obs = spec.get("observed") or []
        xs = [p[0] for p in obs]
        ys = [min(1 - dodge, max(dodge, p[1])) if p[1] in (0.0, 1.0)
              else p[1] for p in obs]
        return xs, ys

    x0, x1 = d["x_range"]
    pad = 0.05 * (x1 - x0)
    light = {"lead": "#aec7e8", "bridgedrive": "#ff9896"}
    notes = {"lead": "goes only where\nit can pass cleanly",
             "bridgedrive": "goes where it\ncannot pass cleanly"}
    for ax, model in zip(panes, TIED_PAIR):
        color = STYLE["colors"][model]
        gx, gy = _steps(d["commitment"][model])
        cx, cy = _steps(d["competence"][model])
        ax.step(gx, gy, where="mid", color=color, lw=2.2, zorder=2)
        # competence: LIGHTER shade of the same hue, dashed, drawn ON
        # TOP - where the curves coincide the pair reads as one striped
        # line (two entities, same path), not a hidden line
        ax.step(cx, cy, where="mid", color=light[model], lw=1.5,
                ls=(0, (4, 3)), zorder=3)
        g_at, c_at = dict(zip(gx, gy)), dict(zip(cx, cy))
        common = sorted(set(gx) & set(cx))
        if common:
            ys_g = [g_at[x] for x in common]
            ys_c = [c_at[x] for x in common]
            # No `where` mask: competence <= commitment by coding (a
            # clean pass requires going), so the fill is zero-height
            # wherever they coincide - and a where-mask would drop the
            # half-interval after the last gap sample, leaving an
            # unshaded sliver before the dashed riser (Milan's catch,
            # 2026-08-20: the 3.02->3.14 m sliver on the BridgeDrive
            # panel).
            ax.fill_between(common, ys_c, ys_g,
                            step="mid", color=color, alpha=0.15,
                            lw=0, zorder=1)
        # verdict placement: centered in the agent's own shaded gap
        # when there is one (the overcommitment REGIME - integrated
        # gap, never one marginal stratum); otherwise a fixed spot in
        # the open mid-left band, clear of the riser
        gap_area = sum(max(0.0, g_at[x] - c_at[x]) for x in common)
        gap_x = [x for x in common if g_at[x] - c_at[x] > 0.3]
        if gap_area > 1.0 and gap_x:
            ax.annotate(notes[model],
                        ((min(gap_x) + max(gap_x)) / 2, 0.5),
                        ha="center", fontsize=7.5, color=color,
                        fontweight="bold", zorder=4)
        else:
            ax.annotate(notes[model], (x0 + 0.33 * (x1 - x0), 0.5),
                        ha="center", fontsize=7.5, color=color,
                        zorder=4)
        ax.set_title(STYLE["labels"][model], fontsize=9, color=color,
                     pad=3)
        # no schedule rug, no reference lines/zones here: deck figures
        # carry no caption to explain them (F1/F2 keep theirs)
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(-0.06, 1.05)
    from matplotlib.lines import Line2D
    panes[0].legend([Line2D([], [], color="#444444", lw=2.2),
                     Line2D([], [], color="#999999", lw=1.5,
                            ls=(0, (4, 3)))],
                    ["decides to go", "passes cleanly"],
                    fontsize=6.5, loc="upper left", framealpha=0.9)
    panes[0].set_ylabel("probability")
    plt.setp(panes[1].get_yticklabels(), visible=False)
    top.set_xlim(x0 - pad, x1 + pad)
    fig.supxlabel(d["axis_metric"].replace("_", " ") + " (m)",
                  fontsize=9)
    fig.suptitle("Going vs. passing cleanly - the gap is the failure zone",
                 fontsize=10)
    _save(fig, out, "F1D")


def render_f2(d, out):
    plt = _mpl()
    n = len(d["panels"])
    fig, axes = plt.subplots(n, 2, figsize=(7.0, 2.3 * n), squeeze=False)
    for i, panel in enumerate(d["panels"]):
        for j, curve in enumerate(("commitment", "competence")):
            ax = axes[i][j]
            slots = []
            for model in sorted(panel["curves"]):
                slot = _checkpoint_head(model) or model
                if slot not in slots:
                    slots.append(slot)
            for model, curves in sorted(panel["curves"].items()):
                if curve in curves:
                    head = _checkpoint_head(model)
                    label = (CHECKPOINT_LABEL[head] if head and model == head else
                             None if head else STYLE["labels"][model]) if j == 0 else None
                    _draw_curvespec(ax, curves[curve],
                                    STYLE["colors"].get(model, "#8c564b"),
                                    panel["x_range"], label,
                                    dodge=0.014 * (slots.index(head or model) + 1), lw=0.9 if head else None)
            _draw_refs(ax, panel["refs"])
            ax.set_ylim(-0.05, 1.05)
            if i == 0:
                # column headers once: the paper's two curves,
                # introduced in F1 and reused here without re-teaching
                ax.set_title("Commitment P(go)" if j == 0
                             else "Competence P(clean pass)", fontsize=9)
            if j == 0:
                # row label once: the family's paper name (see
                # FAMILY_LABELS / the Methods family table)
                ax.set_ylabel(family_label(panel["family"]), fontsize=8)
                ax.legend(fontsize=6)
            lo, hi = panel["x_range"]
            for ref in (panel["refs"].get("body_limit_m"), panel["refs"].get("policy_floor_m")):
                if ref is not None:
                    lo = min(lo, ref)         # the reference lines stay on the axis
            pad = 0.03 * (hi - lo)
            ax.set_xlim(lo - pad, hi + pad)
            ax.set_xlabel(axis_label(panel["axis_metric"]), fontsize=6.5)
    fig.tight_layout()
    _save(fig, out, "F2")


def _checkpoint_head(model):
    for head, members in CHECKPOINT_GROUPS.items():
        if model in members:
            return head
    return None


def render_f8(d, out):
    plt = _mpl()
    n = len(d["panels"])
    fig, axes = plt.subplots(1, n, figsize=(2.6 * n, 2.8), squeeze=False)
    for i, panel in enumerate(d["panels"]):
        ax = axes[0][i]
        cps = panel["checkpoints"]
        for y, cp in enumerate(cps):
            color = STYLE["colors"].get(cp["model"], "#8c564b")
            comp = cp.get("competence") or {}
            b = comp.get("boundary")
            if b is not None:
                band = [v for v in (comp.get("band_ci") or comp.get("band") or []) if v is not None]
                if len(band) == 2:
                    ax.plot(sorted(band), [y, y], "-", color=color, lw=3, alpha=0.35, solid_capstyle="butt")
                ax.plot([b], [y], "D", color=color, ms=6, zorder=4)
            else:
                ax.text(0.5, y, "no boundary", transform=ax.get_yaxis_transform(), fontsize=6, color=color,
                        va="center", ha="center")
            for s in (cp.get("per_seed") or {}).get("competence", []) or []:
                if s and s["boundary"] is not None:
                    ax.plot([s["boundary"]], [y + 0.22], "o", color=color, ms=3, alpha=0.9, zorder=5)
        ax.set_yticks(range(len(cps)))
        ax.set_yticklabels([f"checkpoint {k + 1}" for k in range(len(cps))], fontsize=6.5)
        ax.set_ylim(-0.6, len(cps) - 0.2)
        if panel.get("x_range"):
            pad = 0.03 * (panel["x_range"][1] - panel["x_range"][0])
            ax.set_xlim(panel["x_range"][0] - pad, panel["x_range"][1] + pad)   # the tested range, so coincident
        ax.ticklabel_format(axis="x", useOffset=False)                           # boundaries read as one point on it
        ax.set_title(family_label(panel["family"]), fontsize=8)
        ax.set_xlabel(axis_label(panel["axis_metric"]), fontsize=6.5)
        ax.tick_params(labelsize=6.5)
        sp, u = panel["spread"], panel["unit"]
        parts = []
        if sp["across_checkpoints"] is not None:
            parts.append(f"\u0394 checkpoints {sp['across_checkpoints']:.3f} {u}")
        if sp["across_seeds_all_checkpoints"] is not None:
            parts.append(f"\u0394 seeds {sp['across_seeds_all_checkpoints']:.3f} {u}")
        ax.text(0.03, 0.97, "\n".join(parts), transform=ax.transAxes, fontsize=5.8, va="top")
    fig.suptitle("Competence boundary per PlanT 2.0 checkpoint (diamond, interval) and per traffic seed (dots)", fontsize=8)
    fig.tight_layout()
    _save(fig, out, "F8")


def _draw_isaac_refs(ax, refs):
    lo, hi = refs["dead_band_m"]
    ax.axvspan(lo, hi, color="#999999", alpha=0.18, lw=0)
    ax.axvline(refs["contact_width_m"], ls="-", color="#333333", lw=1)


def render_f9(d, out):
    plt = _mpl()
    from matplotlib.patches import Patch
    n = len(d["rows"])
    fig, axes = plt.subplots(n, 3, figsize=(9.0, 2.4 * n), squeeze=False)
    present = []
    for i, row in enumerate(d["rows"]):
        for j, curve in enumerate(("commitment", "competence")):
            ax = axes[i][j]
            for k, (model, curves) in enumerate(sorted(row["curves"].items())):
                if curve in curves:
                    label = STYLE["labels"].get(model, model.replace("_", " ")) if j == 0 else None
                    _draw_curvespec(ax, curves[curve], STYLE["colors"].get(model, "#17becf"), row["x_range"], label,
                                    dodge=0.018 * (k + 1))
            if row["domain"] == "driving":
                _draw_refs(ax, row["refs"])
            else:
                _draw_isaac_refs(ax, row["refs"])
            ax.set_ylim(-0.05, 1.05)
            if i == 0:
                ax.set_title("Commitment P(go)" if j == 0 else "Competence P(clean pass)", fontsize=9)
            if j == 0:
                ax.set_ylabel(row.get("label") or family_label(row["family"]), fontsize=8)
                ax.legend(fontsize=6)
                pad = 0.03 * (row["x_range"][1] - row["x_range"][0])
            ax.set_xlim(row["x_range"][0] - pad, row["x_range"][1] + pad)
            ax.set_xlabel(axis_label(row["axis_metric"]), fontsize=6.5)
        bx = axes[i][2]
        comp = row["composition"]
        xs = [c["x"] for c in comp]
        dx = max(min((b - a for a, b in zip(xs, xs[1:])), default=0.1) or 0.1, 0.02 * (row["x_range"][1] - row["x_range"][0]))
        bottoms = [0.0] * len(comp)
        for oc in OUTCOME_ORDER:
            vals = [c["outcomes"].get(oc, 0) / c["n"] if c["n"] else 0 for c in comp]
            if not any(vals):
                continue
            present.append(oc)
            bx.bar(xs, vals, width=0.7 * dx, bottom=bottoms, color=OUTCOME_COLORS[oc], edgecolor="white", linewidth=0.3)
            bottoms = [b + v for b, v in zip(bottoms, vals)]
        bx.set_ylim(0, 1.0)
        bx.set_xlim(row["x_range"][0] - dx, row["x_range"][1] + dx)
        if i == 0:
            bx.set_title("Outcome composition", fontsize=9)
        bx.set_xlabel(axis_label(row["axis_metric"]), fontsize=6.5)
        bx.set_ylabel(STYLE["labels"].get(row["strip_agent"], row["strip_agent"].replace("_", " ")), fontsize=7)
        bx.tick_params(labelsize=6.5)
    seen = []
    for oc in present:
        if oc not in seen:
            seen.append(oc)
    axes[-1][2].legend(handles=[Patch(color=OUTCOME_COLORS[oc], label=oc.replace("_", " ")) for oc in seen],
                       fontsize=5.5, loc="lower right", framealpha=0.9)
    fig.tight_layout()
    _save(fig, out, "F9")


def render_f3(d, out):
    plt = _mpl()
    n = len(d["panels"])
    fig, axes = plt.subplots(1, n, figsize=(3.5 * n, 3.2), squeeze=False)
    for i, panel in enumerate(d["panels"]):
        ax = axes[0][i]
        ax.axhspan(0, panel["mirror_allowance_m"],
                   color=STYLE["mirror_band_color"], alpha=0.35,
                   label="mirror band")
        if panel.get("expert_floor_m") is not None:
            ax.axhline(panel["expert_floor_m"], color="#7f7f7f", ls=":",
                       lw=1.5, label="expert floor")
        for model, pts in sorted(panel["points"].items()):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ax.plot(xs, ys, "o", ms=4, color=STYLE["colors"][model],
                    label=STYLE["labels"][model])
        n_band = sum(1 for m2 in panel["points"].values()
                     for p in m2 if p[2])
        if n_band:
            band_ds = [p[3] for m2 in panel["points"].values()
                       for p in m2 if p[2]]
            ax.set_title(family_label(panel["family"])
                         + f"  ({n_band} in-band passes, "
                         f"DS {min(band_ds):.0f}-{max(band_ds):.0f})",
                         fontsize=8)
        else:
            ax.set_title(family_label(panel["family"]), fontsize=8)
        ax.set_xlabel(panel["axis_metric"].replace("_", " ") + " (m)")
        ax.set_ylabel("min clearance (m)")
        if i == 0:
            ax.legend(fontsize=6)
    fig.tight_layout()
    _save(fig, out, "F3")


def render_f5(d, out):
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    ts = [p[0] for p in d["separation_m"]]
    ss = [max(p[1], 1e-4) for p in d["separation_m"]]
    ax.semilogy(ts, ss, color="#333333", lw=1.5,
                label="inter-branch ego separation")
    if d.get("first_divergence_t_s"):
        ax.axvline(d["first_divergence_t_s"], color="k", ls=":",
                   label=f"divergence t={d['first_divergence_t_s']}s")
    for t in d["collisions_53_7"]:
        ax.plot([t], [8e-5], "|", color="#2ca02c", ms=8)
    for t in d["collisions_32_2"]:
        ax.plot([t], [6e-5], "|", color="#d62728", ms=8)
    ax.plot([], [], "|", color="#2ca02c",
            label=f"contacts, branch DS 53.7 (n={d['n_events']['branch_53_7']})")
    ax.plot([], [], "|", color="#d62728",
            label=f"contacts, branch DS 32.2 (n={d['n_events']['branch_32_2']})")
    ax.set_xlabel("game time (s)")
    ax.set_ylabel("ego separation (m, log)")
    # no in-figure title: the identical-until-divergence claim is the
    # caption's (venue convention)
    ax.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    _save(fig, out, "F5")


def render_f6(d, out):
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(4.5, 3.0))
    xs = [p[0] for p in d["points"]]
    ax.step(xs, [p[1] for p in d["points"]], where="mid", lw=2,
            color="#999999", label="completion-based (raw)")
    ax.step(xs, [p[2] for p in d["points"]], where="mid", lw=2,
            color=STYLE["colors"][d["model"]], label="family-aware clean")
    band = d["family_aware_bracket"]
    ax.axvspan(band[0], band[1], color=STYLE["colors"][d["model"]],
               alpha=0.15)
    ax.set_ylim(-0.05, 1.05)
    # no in-figure title: the completion-hides-contacts claim (and its
    # count) belongs to the caption (venue convention)
    ax.set_xlabel(d["axis_metric"].replace("_", " ") + " (m)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    _save(fig, out, "F6")


def _save(fig, out_dir, name):
    for ext in ("pdf", "png", "svg"):
        fig.savefig(out_dir / f"{name}.{ext}", dpi=150)


def render_table(d, out):
    """Preview-grade table image (Notion / contact sheet). The print
    version is LaTeX, generated later from the same table data. NO
    baked title (venue exemplar: LEAD captions tables BELOW, typeset,
    never in the image) - captions live in paper_captions_draft.md;
    d["title"] survives in table_data for identification."""
    plt = _mpl()
    rows, cols = d["rows"], d["col_labels"]
    # the width follows the longest line of each column (2026-10-05: Table 5's bracket-plus-CI strings clipped
    # at the old fixed width); about 0.075 in per character at 8 pt, the row labels' column included
    per_col = [max([len(str(c))] + [len(str(r[j])) for r in rows if j < len(r)]) for j, c in enumerate(cols)]
    label_w = max((len(str(l)) for l in d.get("row_labels") or []), default=0)
    width = 0.6 + 0.075 * (sum(per_col) + label_w) + 0.3 * (len(cols) + (1 if label_w else 0))
    width = min(max(width, 6), 36)
    fig, ax = plt.subplots(figsize=(width, 0.42 * (len(rows) + 2)))
    ax.axis("off")
    tbl = ax.table(cellText=rows, colLabels=cols,
                   rowLabels=d.get("row_labels"), loc="center",
                   cellLoc="left")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.auto_set_column_width(list(range(len(cols))))
    tbl.scale(1, 1.45)
    for j in range(len(cols)):
        tbl[0, j].set_text_props(weight="bold")
        tbl[0, j].set_facecolor("#f0ece1")
    fig.tight_layout()
    _save(fig, out, d["figure"])


RENDERERS = {"F1": render_f1, "F1D": render_f1d, "F2": render_f2,
             "F3": render_f3, "F4": render_f4, "F5": render_f5,
             "F6": render_f6, "F7": render_f7, "F8": render_f8, "F9": render_f9,
             **{name: render_table for name in TABLE_BUILDERS}}


def contact_sheet(out_dir):
    """Index every figure PNG present in out_dir/figures, not just the
    names rendered by this invocation - a partial regeneration must
    never shrink the sheet. (Retired figures linger until their PNG is
    deleted; that is the acceptable direction of error for a preview
    index.)"""
    names = sorted(p.stem for p in (out_dir / "figures").glob("*.png"))
    imgs = "\n".join(
        f'<h3>{n}</h3><img src="figures/{n}.png" style="max-width:100%">'
        for n in names)
    (out_dir / "contact_sheet.html").write_text(
        f"<html><body><h2>SCL paper figures</h2>{imgs}</body></html>")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scored", default=None, help="one scored dir (a single cohort); or use --pooled")
    ap.add_argument("--stats", default=None, help="its stats dir")
    ap.add_argument("--pooled", type=Path, default=None,
                    help="pool_campaigns.py's --out root: the figures read every family from its pooled "
                         "folder there (the results store, by interpretation id), one --spec per family")
    ap.add_argument("--spec", action="append", type=Path, default=None,
                    help="a pooling spec (campaign_pooling/v0.1); repeatable; with --pooled")
    ap.add_argument("--extension-spec", action="append", type=Path, default=None,
                    help="pooling specs of agents that join the atlas (F2) and F8 but not the roster tables "
                         "and F1/F4/F6/F7 (2026-09-19: PlanT 2.0's campaign, one spec per family)")
    ap.add_argument("--store-root", type=Path, default=None,
                    help="the results store (findings documents for the flow rows and the Isaac rows); "
                         "default: this repository")
    ap.add_argument("--metrics", default=None)
    # F5's two inputs live in the results store since 2026-09-23 (artifacts/examples/findings/
    # bifurcation/, with bifurcation.json naming the runs, the extractor and the hashes)
    ap.add_argument("--extract-53", type=Path, default=REPO_ROOT /
                    "artifacts/examples/findings/bifurcation/branch_a_ds53_7.json.gz")
    ap.add_argument("--extract-32", type=Path, default=REPO_ROOT /
                    "artifacts/examples/findings/bifurcation/branch_b_ds32_2.json.gz")
    ap.add_argument("--figures", nargs="+",
                    default=["F1", "F2", "F3", "F4", "F5", "F6", "F7"])
    ap.add_argument("--out", type=Path, default=Path("paper_figures_out"))
    ap.add_argument("--no-render", action="store_true",
                    help="figure_data only (no matplotlib)")
    args = ap.parse_args()
    if args.pooled is not None:
        if args.scored or args.stats or not args.spec:
            ap.error("--pooled takes one or more --spec and neither --scored nor --stats")
    elif not (args.scored and args.stats):
        ap.error("give --scored and --stats (one cohort) or --pooled with --spec (the store)")

    inp = load_inputs(args)
    data_dir = args.out / "figure_data"
    fig_dir = args.out / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    built = {}
    for name in args.figures:
        if name == "F1":
            built[name] = build_f1(inp)
        elif name == "F1D":
            built[name] = build_f1d(inp)
        elif name == "F2":
            built[name] = build_f2(inp)
        elif name == "F3":
            built[name] = build_f3(inp) if inp["metrics"] else \
                {"figure": "F3", "status": "no metrics dir given"}
        elif name == "F4":
            built[name] = build_f4(inp)
        elif name == "F5":
            if args.extract_53.exists() and args.extract_32.exists():
                built[name] = build_f5(args.extract_53, args.extract_32)
            else:
                built[name] = {"figure": "F5",
                               "status": "bifurcation extracts not found"}
        elif name == "F6":
            built[name] = build_f6(inp)
        elif name == "F7":
            built[name] = build_f7(inp)
        elif name == "F8":
            built[name] = build_f8(inp)
        elif name == "F9":
            built[name] = build_f9(inp)
        elif name in TABLE_BUILDERS:
            built[name] = TABLE_BUILDERS[name](inp)

    rendered = []
    for name, d in built.items():
        if name != "F5":
            # data lineage (F5 builds from trajectory extracts, not from
            # the scored/stats inputs, so stamping it would overstate)
            d["provenance"] = inp["lineage"]
        (data_dir / f"{name}.json").write_text(
            json.dumps(d, sort_keys=True, indent=1) + "\n")
        if d.get("status"):
            print(f"{name}: SKIPPED render - {d['status']}")
            continue
        if not args.no_render and name in RENDERERS:
            RENDERERS[name](d, fig_dir)
            rendered.append(name)
    if rendered:
        contact_sheet(args.out)
    print(f"figure_data: {sorted(built)} -> {data_dir}; "
          f"rendered: {rendered} -> {fig_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
