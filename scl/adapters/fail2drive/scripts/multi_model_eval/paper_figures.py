"""Paper figure generator (Demo B item 8): F1-F9 and the tables T1-T9 per the PAPER CUT, TS (the sensitivity table),
TL (the identity ladder as a table) and PT (the paper's three tables as LaTeX bodies, --paper-tables).

Two-layer output, by design:
  figure_data/TN.json   the paper's numbers: every quantity the text and captions cite that no table prints,
                        by name, with the strings the paper prints for it and its source (F89, 2026-10-16;
                        scripts/check_paper_numbers.py checks a LaTeX source against it, both directions)
  figure_data/F*.json   the exact numbers plotted (series, bands,
                        annotations, reference lines) with lineage -
                        deterministic, matplotlib-free, committed-golden
                        material. THE AUDITABLE LAYER.
  figures/F*.{pdf,png,svg}  matplotlib renders consuming ONLY the
                        figure_data dicts. Presentation, not truth -
                        never byte-golden-tested.

    conda run -n scl_prototype python \\
        adapters/fail2drive/scripts/multi_model_eval/paper_figures.py \\
        --pooled <root> --spec <pooling spec>... [--extension-spec <spec>...] [--metrics <dir>] \\
        --out <dir> [--figures F1 F2 ...] [--no-render]
    conda run -n scl_prototype python \\
        adapters/fail2drive/scripts/multi_model_eval/paper_figures.py \\
        --scored <dir> --stats <dir> [--metrics <dir>] --out <dir> [--figures ...] [--no-render]

INPUTS (F82, principle 15, 2026-10-11: one pipeline, one shape). Every figure reads findings
documents (findings_summary/v0.3, the one emitter's shape: the fit stage's points and fit per
curve, the outcome composition, the decision summary) and, for per-run facts (the driving score,
the outcome labels, the completion flag, the route metrics), the run rows beside them
(scl_core.run_rows). Two forms:
- `--pooled ROOT --spec S...`: the pool tool's folders (pool_campaigns.py --out ROOT), one spec per
  family drawn, flow and warehouse families included (their pooled documents; the store's
  single-campaign documents are never read); `--extension-spec` names pools of agents that join the
  atlas (F2), F7, F8 and the extension columns of T3, T5-T9 but not the paper's roster figures
  F1/F4/F6/F9 and T1/T2.
- `--scored DIR --stats DIR`: one cohort (its rows and its fits), built into the same document shape
  through the one emitter, with no pooling block (a cohort pins no declaration, so it carries no
  family version).
Admission (scl_core.figures.documents): a document is the one kind and its emitter, fit, estimator,
scorer and vocabulary pins hash to this tree; rows and fits pass the stamps' checks; the lineage
block embedded in every figure_data JSON carries those pins.

FIGURE POLICY (scl_core.figures.curves, shared with the Isaac tool)
- A figure may only draw what the fit method produced: method=flat
  renders a flat line + Wilson band + "no boundary" annotation;
  method=bracket renders the midpoint + shaded bracket band - NEVER a
  smooth curve through separated data; only method=irls draws a
  logistic curve (sampled in the data layer, over the strata the fit used).
- Codings come from the rows (the scorer's, under the pinned vocabulary) and are counted by the fit
  stage: the observed rates are the documents' points, never re-derived from outcome labels here.
  The three curves are named by the document's quantities: p_go (commitment), p_clean (mission
  success by any route) and passage (clean traversal of the designated gap: the boundary quantity,
  the "competence" of the paper's prose).
- Reference lines are DERIVED at build time, never hardcoded: body
  limit = 2 x the registry's Lincoln half-width (1.837); policy floor
  = body + 2 x mirror allowance + 2 x clearance margin from the
  family's manifest (2.297 on current manifests). Drawn only
  on corridor-width axes. The warehouse band (F9) comes from the instrument and the robot record
  the warehouse rows were scored against (pinned in the document), through scl_core.certification_band.
- Agent colors are fixed across ALL figures (the presentation-map
  rule): lead blue, tfpp green, bridgedrive red, simlingo purple,
  hipad orange, plant2 brown, expert gray.
- F4 (beat-the-teacher) requires the fm_001 sweep; without its pool the builder emits a
  status-placeholder figure_data and skips the render.
- Deterministic renders: SOURCE_DATE_EPOCH pinned so PDF metadata is
  byte-stable across reruns on one matplotlib version.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

REPO_ROOT = Path(__file__).resolve().parents[4]
INSTRUMENTS_PATH = REPO_ROOT / "artifacts/examples/campaigns/instruments.json"   # the declared instruments (F78); the gap
# certificates' campaign samples live in its fail2drive.gap_certificate_campaigns table (F99, 2026-10-21)
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import route_metrics  # noqa: E402  (mirror allowance and clearance margin: the reference lines)
import score_runs  # noqa: E402  (REGISTRY_PATH, MANIFEST_DIR: the reference lines)
import trajectory_extract  # noqa: E402  (F5 reads the two bifurcation extracts)
import flow_gap_certificate as fg  # noqa: E402  (the band rule at other margins: the sensitivity table)
from scl_core import family_versions as fv  # noqa: E402  (the family version registry: a label over the manifest hash)
from scl_core.certification_band import certification  # noqa: E402  (the Isaac reference band)
from scl_core.figures import curves as fc  # noqa: E402  (the one curve renderer)
from scl_core.figures import draw_profiles as dp  # noqa: E402  (the paper's profile grid and case-study graph, two designs)
from scl_core.figures import profiles as fp  # noqa: E402  (the profile figure data)
from scl_core.figures import documents as fd  # noqa: E402  (the one reader of the pool folders)
from scl_core.figures import latex_tables as lt  # noqa: E402  (the paper's tables as LaTeX bodies: PT)
from scl_core.records import portable_path, sha256_file as _sha_file  # noqa: E402
from scl_core.stats import boundary as bd  # noqa: E402  (the bootstrap's declared constants: TN)
from scl_core.stats import decision as da  # noqa: E402  (decision accuracy with its bootstrap interval, F87)
from scl_core.stats import sensitivity as ss  # noqa: E402  (decision accuracy under the perturbed constants: TS)

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
    # run rows, models.yaml) — renamed at display time only.
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
               "x_mobility_fusion_v3": "X-Mobility (head-only fine-tune)",
               # the warehouse reference model (the one emitter writes its document too, so T8/T9 carry its
               # row beside the driving expert's, pdm_lite_expert); the label keeps the two apart
               "expert": "warehouse expert"},
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
# the paper's profile figures (2026-10-11, reverse-engineered from the paper's generated pgfplots sources): one
# figure per family, one panel per roster agent (the roster is data: --roster, artifacts/examples/figure_specs/),
# rendered in the paper's design as it stands (--design asis) or with the estimator's marks kept (hybrid)
PROFILE_FAMILIES = {"P-gauntlet": "fm_004_threshold_sweep", "P-corridor": "fm_002_corridor_pincer",
                    "P-machine": "fm_001_intrusion_sweep", "P-cluttered": "fm_005_hero",
                    "P-speed": "fm_011_flow_speed_sweep", "P-spacing": "fm_012_source_dist_interval_sweep"}
DESIGN = ["hybrid"]   # set by --design; the renderers read it
FLOW_FAMILIES = ("fm_011_flow_speed_sweep", "fm_012_source_dist_interval_sweep")
AXIS_LABELS = {"crossing_time_gap_s": "crossing time gap", "measured_gap_m": "certified minimum free gap",
               "minimum_free_corridor_width": "minimum free corridor width", "maximum_route_intrusion": "maximum route intrusion"}
# the paper's two curves, by the document's quantity: the columns of F2 and F9, the pair of F1D
PAPER_CURVES = ("p_go", "passage")
CURVE_TITLES = {"p_go": "Commitment P(go)", "passage": "Competence P(clean pass)", "p_clean": "Mission P(clean by any route)"}


def axis_label(metric, unit="m"):
    return f"{AXIS_LABELS.get(metric, str(metric).replace('_', ' '))} ({unit or 'm'})"


UNCONSTRAINED_BAND = 0.6   # a bootstrap interval wider than this fraction of the tested range is not drawn as a band
OUTCOME_COLORS = {   # one palette across domains: clean greens, refusals greys, contacts reds
    "clean_thread": "#2ca02c", "late_commit": "#98df8a", "refusal": "#c7c7c7", "correct_stop": "#9e9e9e",
    "contact_thread": "#ff9896", "contact_blocked": "#d62728", "contact_past_blockage": "#8b0000",
    "clean_cross": "#2ca02c", "stall": "#c7c7c7", "incomplete": "#9e9e9e", "collision_flow": "#d62728",
    "collision_other": "#ff9896", "collision_unattributed": "#ffbb78", "unresolved_contact": "#ffbb78",
    "anomaly_blockage_completed": "#444444", "missing": "#eeeeee",
    "commit_clean": "#2ca02c", "divert_clean": "#98df8a", "timeout_clean": "#c7c7c7",
    "timeout_contact": "#d62728", "commit_contact": "#ff9896", "divert_contact": "#e377c2"}
OUTCOME_ORDER = ["clean_thread", "clean_cross", "commit_clean", "late_commit", "divert_clean",
                 "refusal", "correct_stop", "stall", "incomplete", "timeout_clean",
                 "unresolved_contact", "collision_unattributed", "contact_thread", "collision_other", "commit_contact", "divert_contact",
                 "contact_blocked", "collision_flow", "timeout_contact", "contact_past_blockage",
                 "anomaly_blockage_completed", "missing"]

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
# a document names the version its pinned declarations bind (scl_core.family_versions through
# scl_core.figures.documents.version_of), and the label carries the version where the registry holds more
# than one (fv.display). A document the registry cannot place is refused, never guessed; a cohort passed by
# --scored/--stats pins no declaration and stays unversioned (the data says None).

def versioned_label(family, row):
    return fv.display(row, family_label(family)) if row else family_label(family)


def labelled(inp, family, row=None):
    """The family label with its version and the roster pool's cohort note (F88, 2026-10-15): a pool that takes
    a strict subset of a campaign's repetitions reads "Charger gauntlet (seeds 0 to 2)", so no table or panel
    names a cohort without saying which runs it counted."""
    return fd.with_cohort(versioned_label(family, row), (inp.get("cohorts") or {}).get(family))


def version_leaf(row):
    return fv.record(row) if row else None


# --- the inputs --------------------------------------------------------------
# inp = {"root", "documents": {family: {model: doc}}, "refs": {family: {model: {name, sha256, pool}}},
#        "extension_documents", "extension_refs", "rows", "extension_rows", "metrics", "lineage"}

def _tool_sha(name: str) -> str:
    return _sha_file(Path(__file__).resolve().parent / name)


def _check_metrics_stamp(stamp, where):
    """The route metrics are stamped by the tool beside this one (2026-09-22): another tool's rows refuse."""
    if stamp.get("tool_sha256") != _tool_sha("route_metrics.py"):
        raise ValueError(f"{where}: metrics stamped by route_metrics {str(stamp.get('tool_sha256'))[:12]}, the tool beside "
                         f"this one is {_tool_sha('route_metrics.py')[:12]}; re-pool from re-scored interpretations")


def _metrics_dir(path):
    rows = [json.loads(l) for l in (Path(path) / "metrics_rows.jsonl").read_text().splitlines() if l.strip()]
    stamp_path = Path(path) / "metrics_provenance.json"
    if not stamp_path.is_file():
        raise ValueError(f"{path}: no metrics_provenance.json (unstamped route metrics)")
    _check_metrics_stamp(json.loads(stamp_path.read_text()), str(path))
    return rows


def inputs_from_pools(pools, extension, root, metrics_dir=None) -> dict:
    """The figures' input from pool folders already read (scl_core.figures.documents.read_pool): one
    roster pool per family (two refuse), the extension pools beside (several per family, disjoint
    models), the rows where the pools hold them, the route metrics from --metrics or from the roster
    pools' metrics members, the lineage."""
    def place(pool_list, documents, refs, rows, roster):
        """The roster: one pool per family (a second refuses). The extension: several pools of one family
        join when their models are disjoint (the extension agents ran as separate campaigns of one family,
        one model set each: v06 PlanT, v07 SimLingo, v08 HiP-AD, v09 RegNetY, 2026-10-11); a model drawn
        twice for one family refuses by name, whichever list."""
        for pool in pool_list:
            fam = pool["family"]
            if roster and fam in documents:
                raise ValueError(f"{pool['pool_id']}: a second roster pool of {fam} ({documents[fam].pool_id!r}); one pool per family "
                                 f"in the roster (the extension joins several pools of one family with disjoint models)")
            docs = documents.setdefault(fam, OrderedDict())
            if not hasattr(docs, "pool_id"):
                docs.pool_id = pool["pool_id"]
            fam_refs = refs.setdefault(fam, {})
            for m, doc in pool["documents"].items():
                if m in docs:
                    raise ValueError(f"{pool['pool_id']}: {fam} {m} is already drawn from pool {fam_refs[m]['pool']!r}; "
                                     f"one pool per family and model")
                docs[m] = doc
                fam_refs[m] = {"name": pool["paths"][m].name, "sha256": pool["record"]["documents"][pool["paths"][m].name],
                               "pool": pool["pool_id"]}
            rows += pool["rows"] or []
    documents, refs, rows = {}, {}, []
    ext_documents, ext_refs, ext_rows = {}, {}, []
    place(pools, documents, refs, rows, roster=True)
    place(extension, ext_documents, ext_refs, ext_rows, roster=False)
    cohorts = {p["family"]: p.get("cohort") for p in pools}              # the roster pool's cohort note per family
    ext_cohorts = {p["pool_id"]: p.get("cohort") for p in extension}
    # the route metrics are read where they are consumed (F3, T2): a member stamped by another route_metrics
    # refuses those figures by name and leaves the rest drawable (the member is beside the pool, not bound by
    # its record); the refusal is kept, never dropped
    metrics, refusal = [], None
    try:
        if metrics_dir:
            metrics = _metrics_dir(metrics_dir)
        else:
            for pool in pools:
                if pool["metrics"]:
                    _check_metrics_stamp(pool["metrics"]["stamp"], f"{pool['dir']}/{pool['family']}/metrics")
                    metrics += pool["metrics"]["rows"]
    except ValueError as exc:
        metrics, refusal = [], str(exc)
    ext_metrics = []
    for pool in extension:
        pool["lineage"]["extension"] = True
        if pool["metrics"] and not refusal:
            try:
                _check_metrics_stamp(pool["metrics"]["stamp"], f"{pool['dir']}/{pool['family']}/metrics")
                ext_metrics += pool["metrics"]["rows"]
            except ValueError as exc:
                refusal = str(exc)
    lineage = fd.lineage(Path(__file__).resolve(), root, pools + extension, extra=_metrics_lineage(metrics + ext_metrics))
    return {"root": root, "documents": documents, "refs": refs, "extension_documents": ext_documents,
            "extension_refs": ext_refs, "cohorts": cohorts, "extension_cohorts": ext_cohorts,
            "rows": rows, "extension_rows": ext_rows, "metrics": metrics, "extension_metrics": ext_metrics,
            "metrics_refusal": refusal, "lineage": lineage}


def _metrics_lineage(metrics):
    if not metrics:
        return None
    return {"metrics_tool": {"path": "adapters/fail2drive/scripts/multi_model_eval/route_metrics.py",
                             "sha256": _tool_sha("route_metrics.py")}}


def metrics_of(inp, figure) -> list:
    """The route metrics rows, or a refusal naming the figure when the member was stamped by another tool."""
    if inp.get("metrics_refusal"):
        raise ValueError(f"{figure} reads the route metrics: {inp['metrics_refusal']}")
    return inp["metrics"]


def inputs_from_cohort(cohort, root, metrics_dir=None) -> dict:
    """The figures' input from one cohort read through the emitter (scl_core.figures.documents.read_cohort)."""
    documents = {fam: OrderedDict(docs) for fam, docs in cohort["documents"].items()}
    refs = {fam: {m: {"name": None, "sha256": None, "pool": None} for m in docs} for fam, docs in documents.items()}
    metrics, refusal = [], None
    if metrics_dir:
        try:
            metrics = _metrics_dir(metrics_dir)
        except ValueError as exc:
            refusal = str(exc)
    lineage = fd.lineage(Path(__file__).resolve(), root, cohort=cohort, extra=_metrics_lineage(metrics))
    return {"root": root, "documents": documents, "refs": refs, "extension_documents": {}, "extension_refs": {},
            "cohorts": {}, "extension_cohorts": {},
            "rows": cohort["rows"], "extension_rows": [], "metrics": metrics, "extension_metrics": [], "metrics_refusal": refusal,
            "lineage": lineage}


def load_inputs(args):
    if args.pooled is not None:
        pools = [fd.read_pool(args.pooled, s, REPO_ROOT) for s in args.spec]
        extension = [fd.read_pool(args.pooled, s, REPO_ROOT) for s in (args.extension_spec or [])]
        return inputs_from_pools(pools, extension, REPO_ROOT, args.metrics)
    return inputs_from_cohort(fd.read_cohort(args.scored, args.stats, REPO_ROOT), REPO_ROOT, args.metrics)


def docs_of(inp, family, extension=False) -> OrderedDict:
    """{model: document} of one family: the roster pool's documents, then (with extension) the
    extension pools' for models the roster lacks, in the pools' order."""
    out = OrderedDict(inp["documents"].get(family) or {})
    if extension:
        for m, d in (inp["extension_documents"].get(family) or {}).items():
            out.setdefault(m, d)
    return out


def refs_of(inp, family, extension=False) -> dict:
    out = dict(inp["refs"].get(family) or {})
    if extension:
        for m, ref in (inp["extension_refs"].get(family) or {}).items():
            out.setdefault(m, ref)
    return out


def pool_of(inp, family):
    docs = inp["documents"].get(family)
    return getattr(docs, "pool_id", None) if docs else None


def rows_of(inp, family, model=None, extension=False) -> list:
    rows = [r for r in inp["rows"] if r["family"] == family]
    if extension:
        rows += [r for r in inp["extension_rows"] if r["family"] == family]
    return [r for r in rows if model is None or r["model"] == model]


def require_rows(inp, family, figure, extension=False):
    rows = rows_of(inp, family, extension=extension)
    if not rows:
        pools = [p for p in (pool_of(inp, family),) if p]
        raise ValueError(f"{figure} reads the run rows of {family} (the driving score and outcome per run) and the input "
                         f"holds none{' (document-only pool ' + pools[0] + ')' if pools else ''}; give a pool that holds "
                         f"{family}/scoring_out/run_rows.jsonl")
    return rows


def require_docs(inp, family, figure, models=()):
    docs = docs_of(inp, family)
    if not docs:
        raise ValueError(f"{figure} draws {family}; the input holds documents of {sorted(inp['documents'])} (give its pooling spec)")
    missing = [m for m in models if m not in docs]
    if missing:
        raise ValueError(f"{figure} draws {family} for {list(models)}; the pool holds documents of {list(docs)} (missing {missing})")
    return docs


def annex(row, key):
    return row["annex"].get(key)


def family_x_range(docs) -> list:
    """The tested range of a family: every run's x in the documents' outcome compositions."""
    xs = [c["x"] for d in docs.values() for c in d["outcome_composition"] if not isinstance(c["x"], str)]
    return [round(min(xs), 4), round(max(xs), 4)] if xs else None


def curve_specs(doc, x_range, quantities=fc.QUANTITIES) -> dict:
    return {q: fc.render_spec(doc["curves"][q], x_range) for q in quantities}


def models_in(docs, roster) -> list:
    """The paper roster in its order, then the other models in the documents' order (the roster pool's,
    then the extension pools')."""
    return [m for m in roster if m in docs] + [m for m in docs if m not in roster]


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


def warehouse_reference_lines(doc, root) -> dict:
    """The warehouse reference band from the instrument and the robot record the document's rows were
    scored against (pinned by role in its rows_provenance, read from the tree and hash-checked), through
    scl_core.certification_band (2026-10-08; until then from the capability profile)."""
    c = certification(fd.instrument(doc, "instrument", root), fd.instrument(doc, "robot_record", root))
    pins = fd.instruments_by_role(doc)
    return {"contact_width_m": c["contact_width_m"], "dead_band_m": c["dead_band_m"],
            "instrument": pins["instrument"], "robot_record": pins["robot_record"]}


# --- figure builders (data layer) ------------------------------------------

def build_f1(inp, pair=TIED_PAIR, figure="F1"):
    """The tied pair's driving score and commitment on the gauntlet. `pair` is (the agent that stops, the
    agent that continues): F1 draws TIED_PAIR; the case study CS draws the roster spec's pair (2026-10-11)."""
    fam = "fm_004_threshold_sweep"
    docs = docs_of(inp, fam, extension=True)   # the pair may sit in an extension pool (the case study's RegNetY run, v09)
    missing = [m for m in pair if m not in docs]
    if missing:
        raise ValueError(f"{figure} draws {fam} for {list(pair)}; the pools hold documents of {list(docs)} (missing {missing})")
    rows = rows_of(inp, fam, extension=True)
    if not rows:
        require_rows(inp, fam, figure)
    metric = docs[pair[0]]["axis"]["metric"]
    panels_ds, means_by_x = {}, {}
    for model in pair:
        pts = sorted((r["x"], annex(r, "driving_score"), r["outcome"]) for r in rows if r["model"] == model)
        panels_ds[model] = [[round(x, 4), round(ds, 2), oc] for x, ds, oc in pts]
        acc = defaultdict(list)
        for x, ds, _ in pts:
            acc[x].append(ds)
        means_by_x[model] = [[round(x, 4), round(sum(v) / len(v), 2)] for x, v in sorted(acc.items())]
    # the inversion cell is PICKED MECHANICALLY: the same-(variant, repetition)
    # pair maximizing bridgedrive's completed-with-contact score over
    # lead's correct stop - reproducible, never a hand-chosen exemplar
    cells, best = None, -math.inf
    stopper, pusher = pair
    by_vr = defaultdict(dict)
    for r in rows:
        if r["model"] in pair:
            by_vr[(r["variant"], r["repetition"])][r["model"]] = r
    for (v, rep), cell in sorted(by_vr.items(), key=lambda kv: (kv[0][0], -1 if kv[0][1] is None else kv[0][1])):
        if len(cell) < 2:
            continue
        bd, ld = cell[pusher], cell[stopper]
        if bd["outcome"] == "contact_past_blockage" and ld["outcome"] == "correct_stop" \
                and annex(bd, "driving_score") - annex(ld, "driving_score") > best:
            best = annex(bd, "driving_score") - annex(ld, "driving_score")
            cells = {m: {"x": round(cell[m]["x"], 4), "ds": round(annex(cell[m], "driving_score"), 2),
                         "outcome": cell[m]["outcome"]} for m in pair}
            cells.update(variant=v, variant_index=annex(bd, "variant_index"), repetition=rep)
    xs = [x for pts in panels_ds.values() for x, _, _ in pts]
    x_range = [round(min(xs), 4), round(max(xs), 4)]
    p_go = {model: fc.render_spec(docs[model]["curves"]["p_go"], x_range) for model in pair}
    means = {m: round(sum(p[1] for p in panels_ds[m]) / len(panels_ds[m]), 1) for m in pair}
    return {"figure": figure, "family": fam, "axis_metric": metric, "x_range": x_range,
            "ds_points": panels_ds, "ds_mean_by_x": means_by_x, "mean_ds": means,
            "inversion_cell": cells, "p_go": p_go, "refs": reference_lines(fam),
            "source": "documents", "pool": pool_of(inp, fam),
            "documents": {m: refs_of(inp, fam, extension=True)[m]["name"] for m in pair}}


def load_roster(path) -> dict:
    """The figure roster spec (figure_roster/v0.1, artifacts/examples/figure_specs/): the agents the
    profile figures draw in panel order with the paper's labels, the case study's pair."""
    if path is None:
        raise ValueError("the profile figures and the case study take a roster spec (--roster "
                         "artifacts/examples/figure_specs/paper_roster.json)")
    doc = json.loads(Path(path).read_text())
    if doc.get("roster_kind") != "figure_roster/v0.1":
        raise ValueError(f"{path}: roster_kind {doc.get('roster_kind')!r} is not figure_roster/v0.1")
    return doc


def build_profiles_family(inp, figure, fam, roster):
    """One family's profile figure: a panel per roster agent (a checkpoint group is one panel, its
    members one trace each), drawn from the roster and extension pools' documents, empty where the
    agent did not run the family; the gauntlet's and the corridor's body width and policy floor as
    the reference lines."""
    docs = docs_of(inp, fam, extension=True)
    if not docs:
        raise ValueError(f"{figure} draws {fam}; the input holds documents of {sorted(set(inp['documents']) | set(inp['extension_documents']))}")
    refs_all = refs_of(inp, fam, extension=True)
    axis = next(iter(docs.values()))["axis"]
    panels = []
    for agent in roster["agents"]:
        members = agent.get("checkpoints") or [agent["model"]]
        held = [(m, docs[m], refs_all[m]["pool"], refs_all[m]["name"]) for m in members if m in docs]
        panels.append({"label": agent["label"], "model": agent["model"], "docs": held,
                       "campaigns": sorted({c for _, d, _, _ in held for c in fd.campaign_ids(d)}),
                       "family_version": version_leaf(fd.version_of(held[0][1], inp["root"])) if held else None})
    geometry = axis["metric"] == "minimum_free_corridor_width"   # blocked by geometry: shaded; a flow family's wait windows are not (his design)
    refs = reference_lines(fam) if geometry else {}
    d = fp.build_profiles(figure, family=fam, label=labelled(inp, fam), axis=axis, panels=panels, refs=refs,
                          shade_blocked=geometry)
    d.update(axis_label=axis_label(axis["metric"], axis.get("unit")), source="documents", pool=pool_of(inp, fam),
             documents={m: ref["name"] for m, ref in refs_all.items()}, roster=roster.get("note"))
    return d


def build_cs(inp, roster):
    """The case study (the paper's Figure 2): F1's data for the roster spec's pair, labelled as the paper prints them."""
    pair = tuple(roster["case_study_pair"])
    d = build_f1(inp, pair, "CS")
    labels = {a["model"]: a["label"] for a in roster["agents"]}
    d["labels"] = {m: labels.get(m, STYLE["labels"].get(m, m)) for m in pair}
    return d


def build_f1d(inp):
    """Deck-standalone variant of F1 (decks/talks - NOT a paper figure;
    the paper's F1 stays the prose-supported intro figure). Same DS
    panel; the bottom panel adds each agent's passage curve
    P(clean pass) beside its commitment curve P(go), so the gap -
    committing where it cannot pass cleanly - is drawn, not narrated.
    Sourced from the same documents F2's atlas cells use."""
    d = build_f1(inp)
    docs = docs_of(inp, d["family"])
    d["figure"] = "F1D"
    d["passage"] = {model: fc.render_spec(docs[model]["curves"]["passage"], d["x_range"]) for model in TIED_PAIR}
    return d


def build_f2(inp):
    """The boundary atlas: the threading families then the flow families, every one from the pooled
    documents of its spec (the paper roster plus the extension agents), three curves per agent."""
    present = set(inp["documents"]) | set(inp["extension_documents"])
    families = [f for f in [*FM_ORDER, *FLOW_FAMILIES] if f in present]
    grid = []
    for fam in families:
        docs = docs_of(inp, fam, extension=True)
        axis = next(iter(docs.values()))["axis"]
        x_range = family_x_range(docs)
        panel = {"family": fam, "axis_metric": axis["metric"], "unit": axis["unit"], "x_range": x_range,
                 "refs": (reference_lines(fam) if axis["metric"] == "minimum_free_corridor_width" else {}),
                 "source": "documents", "pool": pool_of(inp, fam),
                 "documents": {m: ref["name"] for m, ref in refs_of(inp, fam, extension=True).items()},
                 "curves": {m: curve_specs(d, x_range) for m, d in docs.items()}}
        grid.append(panel)
    return {"figure": "F2", "panels": grid,
            "checkpoint_groups": {k: list(v) for k, v in CHECKPOINT_GROUPS.items()}}


def build_f3(inp):
    """Minimum clearance per passage (fm_002, fm_004): the route metrics rows (stamped by the route_metrics.py
    beside this tool) joined to the run rows on (family, model, repetition, variant), completed runs only."""
    out = []
    metrics = metrics_of(inp, "F3")
    rows_ix = {(r["family"], r["model"], r["repetition"], r["variant"]): r for r in inp["rows"]}
    for fam in ("fm_002_corridor_pincer", "fm_004_threshold_sweep"):
        docs = docs_of(inp, fam)
        metric = next(iter(docs.values()))["axis"]["metric"] if docs else None
        pts = defaultdict(list)
        for m in metrics:
            if m["family"] != fam or m.get("min_clearance_m") is None:
                continue
            s = rows_ix.get((m["family"], m["model"], m["seed"], m["scenario_id"]))
            if not s:
                continue
            # passage margins only: a refusal run's "min clearance" is
            # its stop standoff - different quantity, not this figure's
            if not annex(s, "completed"):
                continue
            pts[m["model"]].append([round(s["x"], 4), m["min_clearance_m"], m["mirror_band"],
                                    round(annex(s, "driving_score"), 1)])
        if not pts:
            continue
        manifest = json.loads((score_runs.MANIFEST_DIR / f"{fam}.json").read_text())
        mirror = route_metrics.mirror_allowance_for(manifest)
        expert_floor = (min(p[1] for p in pts["pdm_lite_expert"]) if "pdm_lite_expert" in pts else None)
        out.append({"family": fam, "axis_metric": metric,
                    "mirror_allowance_m": mirror,
                    "expert_floor_m": expert_floor,
                    "points": {m: sorted(v) for m, v in pts.items()}})
    return {"figure": "F3", "panels": out}


def build_f4(inp):
    fam = "fm_001_intrusion_sweep"
    docs = docs_of(inp, fam)
    if not docs:
        return {"figure": "F4", "status": "awaiting fm_001 sweep: no pool of fm_001_intrusion_sweep among the inputs"}
    axis = next(iter(docs.values()))["axis"]
    x_range = family_x_range(docs)
    return {"figure": "F4", "family": fam, "axis_metric": axis["metric"], "unit": axis["unit"], "x_range": x_range,
            "source": "documents", "pool": pool_of(inp, fam),
            "curves": {m: fc.render_spec(d["curves"]["passage"], x_range) for m, d in docs.items()}}


F7_ROSTER = ["pdm_lite_expert", "lead", "tfpp", "bridgedrive"]   # the paper roster's column order


def _f7_column(model):
    """The column a row's model falls in: a grouped agent's checkpoints share one column (the atlas
    convention, CHECKPOINT_GROUPS); every other model is its own column."""
    for group, members in CHECKPOINT_GROUPS.items():
        if model in members:
            return group
    return model


def _asset(row):
    """The identity a level-axis row sits on, as the ladder names it (the manifest's `asset_<name>` targets)."""
    return str(row["x"]).replace("asset_", "")


def build_f7(inp):
    """Identity ladder (fm_003): asset x model outcome grid at
    certified-identical geometry. n=1 cells render outcome+DS; the same
    builder aggregates to rates when multi-repetition data lands. Columns come
    from the rows (2026-09-23): the paper roster in its order, then every
    extension agent whose pool holds fm_003 rows, in order of appearance;
    the expert's spread stays the roster's null instrument."""
    fam = "fm_003_asset_swap"
    rows = rows_of(inp, fam, extension=True)
    if not rows:
        return {"figure": "F7", "status": "no fm_003 rows in the input"}
    present = []
    for r in rows:
        col = _f7_column(r["model"])
        if col not in present:
            present.append(col)
    models = [m for m in F7_ROSTER if m in present] + [m for m in present if m not in F7_ROSTER]
    agg = defaultdict(lambda: defaultdict(lambda: {"n": 0, "ds": [], "clean": 0, "traffic": 0,
                                                   "outcomes": defaultdict(int)}))
    for r in rows:
        c = agg[_asset(r)][_f7_column(r["model"])]
        c["n"] += 1
        c["ds"].append(annex(r, "driving_score"))
        c["clean"] += r["outcome"] == "clean_thread"
        c["traffic"] += (annex(r, "traffic_collisions") or 0) > 0
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
    order = sorted(cells, key=lambda a: min(v["mean_ds"] for v in cells[a].values()))
    # The expert's across-object spread is the null-instrument stat,
    # but it is only meaningful within a traffic-quiet stratum: on hot
    # seeds background traffic dominates every agent's DS (measured
    # 2026-08-19: s0 spread 0.14 with 14/14 traffic-free vs 21.6/38.9
    # on s1/s2). A pooled spread mixes strata and is not identity.
    per_rep = defaultdict(dict)
    for r in rows:
        if r["model"] == "pdm_lite_expert" and not annex(r, "traffic_collisions"):
            per_rep[r["repetition"]][_asset(r)] = annex(r, "driving_score")
    n_assets = len(cells)
    spread_by_rep = {
        str(s): {"spread": round(max(v.values()) - min(v.values()), 2),
                 "traffic_free_assets": len(v), "of": n_assets}
        for s, v in sorted(per_rep.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)) if len(v) >= 2}
    quiet = [s for s, v in spread_by_rep.items() if v["traffic_free_assets"] == n_assets]
    return {"figure": "F7", "family": fam, "assets": order,
            "models": models, "cells": cells,
            "roster_models": [m for m in models if m in F7_ROSTER],
            "extension_models": [m for m in models if m not in F7_ROSTER],
            "checkpoint_groups": {g: list(ms) for g, ms in CHECKPOINT_GROUPS.items() if g in models},
            "expert_spread_by_repetition": spread_by_rep,
            "expert_spread_quiet_ds": (spread_by_rep[quiet[0]]["spread"] if quiet else None),
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
    """Beat-the-teacher (fm_001): one passage panel. The teacher's
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
F9_ISAAC_FAMILIES = ("fm_isaac_004", "fm_isaac_001")     # composed stacks (commit only when feasible), slabs (commit and collide)


def build_f8(inp):
    """Checkpoint versus repetition (2026-09-19): for every family PlanT 2.0 ran, each checkpoint's
    pooled boundary with its interval beside the boundaries each traffic seed gives on its own, read
    from the extension pools' documents: the fit stage's per-repetition fits (since F82 every chain
    carries them, the flow families included; until then the flow documents pooled the seeds and
    this figure refitted the threading rows itself)."""
    panels = []
    for fam in F8_FAMILIES:
        docs = inp["extension_documents"].get(fam) or {}
        if not docs:
            continue
        for head, members in CHECKPOINT_GROUPS.items():
            checkpoints = []
            for model in members:
                if model not in docs:
                    continue
                entry = {"model": model, "per_repetition": {}}
                for q in PAPER_CURVES:
                    spec = fc.render_spec(docs[model]["curves"][q])
                    entry[q] = fc.summary(spec)
                    entry["per_repetition"][q] = spec.get("per_repetition") or []
                checkpoints.append(entry)
            if not checkpoints:
                continue
            axis = next(iter(docs.values()))["axis"]
            comp = [c["passage"]["boundary"] for c in checkpoints if c["passage"].get("boundary") is not None]
            rep_b = [b["boundary"] for c in checkpoints for b in c["per_repetition"]["passage"] if b["boundary"] is not None]
            panels.append({"family": fam, "group": head, "axis_metric": axis["metric"], "unit": axis["unit"] or "m",
                           "x_range": family_x_range(docs), "source": "documents",
                           "pool": getattr(inp["extension_documents"][fam], "pool_id", None),
                           "checkpoints": checkpoints,
                           "spread": {"across_checkpoints": (round(max(comp) - min(comp), 4) if len(comp) > 1 else None),
                                      "across_repetitions_all_checkpoints": (round(max(rep_b) - min(rep_b), 4) if len(rep_b) > 1 else None),
                                      "quantity": "passage boundary"}})
    return {"figure": "F8", "panels": panels,
            "note": ("diamonds: each checkpoint's pooled passage boundary with its bootstrap interval "
                     "(or bracket); dots: the boundary each repetition (traffic seed) gives alone, the fit "
                     "stage's per-repetition fits (one run per variant, so no interval); 'no boundary' where "
                     "the pooled fit is flat")}


def _composition(doc) -> list:
    return [{"x": c["x"], "n": c["n"], "outcomes": c["outcomes"], "assignment": c["assignment"]} for c in doc["outcome_composition"]]


def build_f9(inp):
    """The cross-domain pair (2026-09-19): one driving row (the Charger gauntlet, every roster agent)
    and warehouse rows (the roster brain on the composed stacks and the slab pincer) in the same
    two-curve grammar from the same estimator, each from its pooled documents, each with an
    outcome-composition strip for one named agent (the agent's document's verbatim composition)."""
    root = inp["root"]
    rows = []
    fam = "fm_004_threshold_sweep"
    docs = docs_of(inp, fam)
    if docs:
        x_range = family_x_range(docs)
        axis = next(iter(docs.values()))["axis"]
        strip = "lead" if "lead" in docs else next(iter(docs))
        ver = fd.version_of(docs[strip], root)
        rows.append({"domain": "driving", "family": fam, "axis_metric": axis["metric"], "unit": axis["unit"], "source": "documents",
                     "pool": pool_of(inp, fam), "documents": {m: ref["name"] for m, ref in refs_of(inp, fam).items()},
                     "label": labelled(inp, fam, ver), "family_version": version_leaf(ver),
                     "x_range": x_range, "refs": reference_lines(fam),
                     "curves": {m: curve_specs(d, x_range, PAPER_CURVES) for m, d in docs.items()},
                     "strip_agent": strip, "composition": _composition(docs[strip])})
    for fam in F9_ISAAC_FAMILIES:
        docs = docs_of(inp, fam)
        if ISAAC_ROSTER_BRAIN not in docs:
            continue
        d = docs[ISAAC_ROSTER_BRAIN]
        x_range = family_x_range({ISAAC_ROSTER_BRAIN: d})
        ver = fd.version_of(d, root)
        rows.append({"domain": "warehouse", "family": fam, "axis_metric": d["axis"]["metric"], "unit": d["axis"]["unit"],
                     "source": "documents", "pool": pool_of(inp, fam),
                     "documents": {ISAAC_ROSTER_BRAIN: refs_of(inp, fam)[ISAAC_ROSTER_BRAIN]["name"]},
                     "label": labelled(inp, fam, ver), "family_version": version_leaf(ver),
                     "x_range": x_range, "refs": warehouse_reference_lines(d, root),
                     "curves": {ISAAC_ROSTER_BRAIN: curve_specs(d, x_range, PAPER_CURVES)},
                     "strip_agent": ISAAC_ROSTER_BRAIN, "composition": _composition(d)})
    return {"figure": "F9", "rows": rows,
            "note": ("one grammar, two simulators: commitment P(go) and passage P(clean pass) per agent on the "
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
    """Completion hides contacts (fm_002, BridgeDrive): per run the leaderboard's completion flag
    (the row's annex `completed`, a bool; until F82 the string `status == "Completed"` was tested,
    which mis-counted) beside the family-aware clean coding (the row's `clean`, the scorer's under
    the pinned vocabulary), and the passage bracket from the document."""
    fam, model = "fm_002_corridor_pincer", "bridgedrive"
    docs = require_docs(inp, fam, "F6", (model,))
    rows = require_rows(inp, fam, "F6")
    pts = sorted([[round(r["x"], 4), 1 if annex(r, "completed") is True else 0, 1 if r["clean"] is True else 0, r["outcome"]]
                  for r in rows if r["model"] == model])
    n_hidden = sum(1 for p in pts if p[1] == 1 and p[2] == 0)
    fit = docs[model]["curves"]["passage"]["fit"] or {}
    return {"figure": "F6", "family": fam, "model": model,
            "axis_metric": docs[model]["axis"]["metric"], "points": pts,
            "contacts_hidden_by_completion": n_hidden,
            "family_aware_bracket": [fit.get("bracket_fail_side_x"), fit.get("bracket_pass_side_x")],
            "source": "documents", "pool": pool_of(inp, fam)}


# --- paper tables (data layer + preview renders) ---------------------------
# Tables follow the same two-layer discipline as figures: build_t*()
# emits auditable table data (exact printed strings + lineage);
# render_table() makes PREVIEW images (Notion/contact-sheet grade).
# LaTeX fragments for the manuscript come from the same data later -
# the numbers are never hand-copied.

NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]


def number_word(n) -> str:
    """The word the prose prints for a small count ("seven", "fourteen"); the digits beyond twenty."""
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


FAMILY_SETTING = {
    "fm_001_intrusion_sweep": "vending machine intrudes into lane",
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
    "fm_011_flow_speed_sweep": "measured crossing time gap (s)",
    "fm_012_source_dist_interval_sweep": "measured crossing time gap (s)",
    "fm_isaac_001": "min free gap (m)",
    "fm_isaac_003": "min free gap (m)",
    "fm_isaac_004": "min free gap (m)",
}
T1_ISAAC_FAMILIES = ("fm_isaac_001", "fm_isaac_003", "fm_isaac_004")
REPETITION_LABELS = {"traffic_seed_strata": "traffic seeds", "policy_reps": "policy samples"}
STOP_OUTCOMES = {"refusal", "correct_stop", "late_commit"}
CONTACT_OUTCOMES = {"contact_thread", "contact_blocked",
                    "contact_past_blockage"}


def repetition_axis(doc, root):
    """(label, count) of the repetition axis a document's pooled campaigns declare (the declarations
    pinned in its pooling block, hash-checked): the kind named (traffic seeds on CARLA, policy samples
    on Isaac), the repetitions summed over the pooled campaigns (disjoint by the pool tool's gate);
    None for a cohort document, which pins no declaration."""
    decls = fd.declarations(doc, root)
    if not decls:
        return None
    kinds = {d.repetition_axis["kind"] for d in decls}
    kind = kinds.pop() if len(kinds) == 1 else "mixed"
    return (REPETITION_LABELS.get(kind, kind), sum(len(d.repetitions) for d in decls), len(decls[0].variants))


def build_t1(inp):
    """Methods family table: paper name <-> internal id <-> axis, with the executed scale
    (variants x repeats = runs per agent), every family of the paper from its pooled documents:
    the variants and the repetition axis from the declarations the document pins, the runs per
    agent from the policy documents' decomposition; a cohort (no declaration) counts its rows."""
    root = inp["root"]
    rows, versions = [], {}
    present = list(inp["documents"])
    fams = (sorted(f for f in present if f not in FLOW_FAMILIES and f not in T1_ISAAC_FAMILIES)
            + [f for f in FLOW_FAMILIES if f in present] + [f for f in T1_ISAAC_FAMILIES if f in present])
    for fam in fams:
        docs = docs_of(inp, fam)
        first = next(iter(docs.values()))
        ver = fd.version_of(first, root)
        versions[fam] = version_leaf(ver)
        axis = repetition_axis(first, root)
        if axis:
            label, repeats, variants = axis
        else:
            frows = rows_of(inp, fam)
            label, repeats, variants = "repetitions", len({r["repetition"] for r in frows if r["repetition"] is not None}), len({r["variant"] for r in frows})
        per_agent = max(d["decomposition"]["runs"] for d in docs.values())
        rows.append([labelled(inp, fam, ver), fam, FAMILY_AXIS_DESC.get(fam, "?"), FAMILY_SETTING.get(fam, "?"),
                     str(variants), f"{repeats} {label}", str(per_agent)])
    return {"figure": "T1", "title": "Table 1 - Scenario families",
            "col_labels": ["Family", "Internal id", "Swept axis",
                           "Setting", "Variants", "Repeats", "Runs/agent"],
            "rows": rows, "family_versions": versions}


def build_t2(inp):
    """The dissociation compressed (Results 6.2), tied pair."""
    fam = "fm_004_threshold_sweep"
    f1 = build_f1(inp)
    docs = docs_of(inp, fam)
    cell = f1["inversion_cell"] or {}

    def outcome_split(model):
        ocs = [r["outcome"] for r in rows_of(inp, fam, model)]
        stops = sum(1 for o in ocs if o in STOP_OUTCOMES)
        contacts = sum(1 for o in ocs if o in CONTACT_OUTCOMES)
        return len(ocs), stops, contacts

    def worst_push(model):
        metrics = metrics_of(inp, "T2")
        if not metrics:
            return "n/a (no route metrics)"
        worst = 0.0
        for r in metrics:
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
        [fc.boundary_text(docs["lead"]["curves"]["p_go"]), fc.boundary_text(docs["bridgedrive"]["curves"]["p_go"])],
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


def chain_of(inp, family):
    """The chain a family's documents name (threading, flow, warehouse): data on the document, never a family list."""
    docs = docs_of(inp, family, extension=True)
    return next(iter(docs.values()))["chain"] if docs else None


def build_t3(inp):
    """Outcome-taxonomy incidence per agent per family (Results 6.4), the threading chain's families
    (the chain named on their documents; the flow and warehouse rows pool the same way since F82 but
    their labels are another taxonomy and their agents other columns): the paper roster in its
    order, then (2026-09-23) every extension agent with rows, in order of appearance; a grouped
    agent's checkpoints are summed into one column (CHECKPOINT_GROUPS, the atlas convention)."""
    roster_rows, ext_rows = inp["rows"], inp["extension_rows"]
    scored = roster_rows + ext_rows
    fams = sorted(f for f in {r["family"] for r in scored} if chain_of(inp, f) == "threading")
    scored = [r for r in scored if r["family"] in fams]
    roster_rows = [r for r in roster_rows if r["family"] in fams]
    ext_rows = [r for r in ext_rows if r["family"] in fams]
    models = ["lead", "tfpp", "bridgedrive", "pdm_lite_expert"]
    present = [m for m in models if any(r["model"] == m for r in roster_rows)]
    for r in ext_rows:
        col = _f7_column(r["model"])
        if col not in present:
            present.append(col)
    rows, labels = [], []
    for fam in fams:
        fr = [r for r in scored if r["family"] == fam]
        for oc in sorted({r["outcome"] for r in fr}):
            labels.append(f"{labelled(inp, fam)} - "
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
    interval, formatted per the quoting rules (scl_core.figures.curves.boundary_text)."""
    rows, labels = [], []
    fams = [f for f in FM_ORDER if docs_of(inp, f, extension=True)]
    for fam in fams:
        docs = docs_of(inp, fam, extension=True)
        # the roster models sorted, then the extension agents (2026-09-23) in order of appearance, every
        # checkpoint of a grouped agent as its own row (the checkpoint spread is a measurement, F8)
        models = sorted(inp["documents"].get(fam) or {}) + [m for m in docs if m not in (inp["documents"].get(fam) or {})]
        for m in models:
            labels.append(f"{labelled(inp, fam)} - {STYLE['labels'].get(m, m)}")
            rows.append([fc.boundary_text(docs[m]["curves"]["p_go"]), fc.boundary_text(docs[m]["curves"]["passage"])])
    return {"figure": "T5",
            "title": "Table 5 (working) - Boundaries with intervals",
            "col_labels": ["Commitment P(go)", "Competence P(clean pass)"],
            "row_labels": labels, "rows": rows}


T6_ROSTER = ["lead", "tfpp", "bridgedrive", "pdm_lite_expert"]
ISAAC_ROSTER_BRAIN = "x_mobility"   # the paper's warehouse agent (the stock brain); the fine-tunes of v07 are extensions


def _mean_ds(rows):
    return round(sum(annex(r, "driving_score") for r in rows) / len(rows), 1) if rows else None


def _models_in(docs):
    """The paper roster in its order, then the extension models by appearance (each checkpoint
    its own row, as T5 lists them)."""
    return models_in(docs, T6_ROSTER)


def _gap(com, comp):
    """The distance between the two boundaries, or the word for the side that has none."""
    com, comp = com.get("fit"), comp.get("fit")
    if com is None or comp is None:
        return "-"
    if com["method"] == "flat":
        return "no commitment boundary" if (com.get("level") or 0) >= 0.5 else "never commits"
    if comp["method"] == "flat":
        return "always clean" if (comp.get("level") or 0) >= 0.5 else "never clean"
    return f"{abs(comp['boundary'] - com['boundary']):.3f} m"


def build_t6(inp):
    """The score beside the two boundaries (gold 33, 2026-09-25; Ellington's table): for every
    atlas family with documents, per agent (roster then extension, each checkpoint its own
    row), the mean driving score over all its runs beside its commitment and passage
    boundaries as T5 formats them and the gap between them; rows ordered by the mean score
    within a family. The caveats (a boundary located by contact with a prop that tips at
    trigger) live in the caption, not here."""
    fams = [f for f in FM_ORDER if docs_of(inp, f, extension=True)]
    rows, labels = [], []
    for fam in fams:
        docs = docs_of(inp, fam, extension=True)
        fr = rows_of(inp, fam, extension=True)
        cells = []
        for m in _models_in(docs):
            mr = [r for r in fr if r["model"] == m]
            com, comp = docs[m]["curves"]["p_go"], docs[m]["curves"]["passage"]
            cells.append((_mean_ds(mr) if mr else -math.inf, m,
                          [str(len(mr)), f"{_mean_ds(mr):.1f}" if mr else "no rows",
                           fc.boundary_text(com), fc.boundary_text(comp), _gap(com, comp)]))
        for _, m, row in sorted(cells, key=lambda c: -c[0]):
            labels.append(f"{labelled(inp, fam)} - {STYLE['labels'].get(m, m)}")
            rows.append(row)
    return {"figure": "T6",
            "title": "Table 6 - Mean driving score beside the two boundaries",
            "col_labels": ["Runs", "Mean DS", "Commitment boundary", "Competence boundary", "Gap"],
            "row_labels": labels, "rows": rows, "families": fams}


T7_CELLS = {"mean_all": "mean driving score over the agent's runs of the family",
            "mean_passable": "mean driving score over its runs on the passable widths",
            "mean_blocked": "mean driving score over its runs on the blocked widths",
            "blocked_runs": "its runs on the blocked widths",
            "correct_stop": "of those, the runs whose outcome is correct_stop (the agent stops before the blockage)",
            "contact_any": "of those, the runs whose outcome is contact_blocked or contact_past_blockage (any contact with the "
                           "blockage, whether the agent continued past it or not)",
            "blocked_completed": "of those, the runs whose outcome is anomaly_blockage_completed (the width completed "
                                 "without contact, a run in no curve)"}


def build_t7(inp):
    """The score split by side (gold 33, 2026-09-25): for every family whose rows carry passable
    and blocked widths (the rows' assignment), per agent (roster then extension, each checkpoint
    its own row), the mean driving score over all runs, over the passable widths and over the
    blocked widths, and what happened on the blocked widths (the outcome counts there, in the
    taxonomy's vocabulary); rows ordered by the mean score within a family. `prices` carries the
    mean score per outcome class over all agents' runs of the family (the blocked-side price list),
    `sides` the measured widths of each side."""
    fams = [f for f in FM_ORDER if {r["assignment"] for r in rows_of(inp, f, extension=True)} >= {"passable", "blocked"}]
    rows, labels, prices, sides, structured = [], [], {}, {}, {}
    for fam in fams:
        docs = docs_of(inp, fam, extension=True)
        fr = rows_of(inp, fam, extension=True)
        sides[fam] = {"axis_metric": next(iter(docs.values()))["axis"]["metric"] if docs else fr[0]["x_metric"],
                      "passable_widths": sorted({round(r["x"], 3) for r in fr if r["assignment"] == "passable"}),
                      "blocked_widths": sorted({round(r["x"], 3) for r in fr if r["assignment"] == "blocked"})}
        by_class = defaultdict(list)
        for r in fr:
            by_class[r["outcome"]].append(r)
        prices[fam] = {oc: _mean_ds(rs) for oc, rs in sorted(by_class.items(), key=lambda kv: -_mean_ds(kv[1]))}
        cells = []
        structured[fam] = {}
        for m in models_in({r["model"]: None for r in fr}, T6_ROSTER):
            mr = [r for r in fr if r["model"] == m]
            blocked = [r for r in mr if r["assignment"] == "blocked"]
            passable = [r for r in mr if r["assignment"] == "passable"]
            counts = defaultdict(int)
            for r in blocked:
                counts[r["outcome"]] += 1
            on_blocked = ", ".join(f"{OUTCOME_LABELS.get(oc, oc)} {n}"
                                   for oc, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
            cells.append((_mean_ds(mr), m, [str(len(mr)), f"{_mean_ds(mr):.1f}",
                                            f"{_mean_ds(passable):.1f}" if passable else "no runs",
                                            f"{_mean_ds(blocked):.1f}" if blocked else "no runs", on_blocked]))
            # the same split as typed numbers (2026-10-17, the paper's Table 1 as a generated body, PT): the
            # means, the blocked run count and the blocked-side outcome counts by the paper's three headings
            structured[fam][m] = {"runs": len(mr), "passable_runs": len(passable), "blocked_runs": len(blocked),
                                  "mean_all": _mean_ds(mr) if mr else None,
                                  "mean_passable": _mean_ds(passable) if passable else None,
                                  "mean_blocked": _mean_ds(blocked) if blocked else None,
                                  "correct_stop": counts.get("correct_stop", 0),
                                  "contact_any": counts.get("contact_blocked", 0) + counts.get("contact_past_blockage", 0),
                                  "blocked_completed": counts.get("anomaly_blockage_completed", 0),
                                  "outcomes_on_blocked": dict(sorted(counts.items()))}
        for _, m, row in sorted(cells, key=lambda c: -c[0]):
            labels.append(f"{labelled(inp, fam)} - {STYLE['labels'].get(m, m)}")
            rows.append(row)
    return {"figure": "T7",
            "title": "Table 7 - The score split by side (passable and blocked widths)",
            "col_labels": ["Runs", "Mean DS", "DS, passable widths", "DS, blocked widths",
                           "On the blocked widths"],
            "row_labels": labels, "rows": rows, "families": fams, "prices": prices, "sides": sides,
            "cells": structured, "cell_definitions": T7_CELLS}


T8_ORDER = ["fm_001_intrusion_sweep", "fm_002_corridor_pincer", "fm_003_asset_swap", "fm_004_threshold_sweep",
            "fm_005_hero", *FLOW_FAMILIES]
T8_ISAAC_FAMILIES = T1_ISAAC_FAMILIES     # the warehouse families follow the driving ones (2026-10-01)
T8_RULE = ("Decision accuracy is the balanced mean of two correct-call rates: the clean passes on the passable "
           "side and the stops on the blocked side. Every run's call is the scorer's (the row's decision_correct, set "
           "through the one rule every chain applies, scl_core.run_rows.decision, the paper's definition adopted "
           "2026-10-12) and the emitter sums them into the document's decision_summary, which this table reads: on "
           "a passable width (a usable window) the right call is a commitment that passes clean, so a go that ends "
           "in contact, times out or comes late is a miss, as a refusal is; on a blocked width (a wait window) the "
           "right call is no attempt at the passage, whatever follows; on the warehouse families the attempt is the "
           "gate line, so on a blocked width a stop or the detour (the expert's answer) is right, and on a passable "
           "width a go by the gap or the detour that reaches the goal contact-free is right; on the flow families "
           "the conditional band (a clean crossing after a rolling entry is right, a go from rest is wrong) is "
           "reported beside the accuracy, never inside it. A run in no curve (an anomaly: a contact-free completion "
           "of a certified-blocked width on both backends) is excluded from both sides and counted; a run the "
           "declared scenario-defect register names is out of the table. A family without a blocked or wait side "
           "has no balanced accuracy; its clean-pass rate is shown and the cell says so. Every row names the family "
           "version its document binds (the registry over the manifest hash, "
           "artifacts/examples/family_versions/registry.json): two rows of one family on different versions are "
           "two realizations, never one family. The accuracy and its 95% interval are the document's own "
           "(decision_summary.accuracy, F87 2026-10-14: a bootstrap that resamples runs within each side, 10,000 "
           "replicates, the seed recorded; a side where every run is the same has no variance, and when both are "
           "the interval is the point).")
T9_RULE = ("Per agent, the mean of its Table 8 decision accuracies over the families that have a wait side (each "
           "family is one question and weighs the same; the count of those families is printed beside the mean, "
           "and the pooled balanced accuracy over the same families is kept in the data), then one column per "
           "family: the decision accuracy where the family has a wait side, the GO RATE on the passable side "
           "where it has none (the paper's convention: the commitment, read from the document's decomposition, "
           "never the clean-pass side; the column header carries '(go rate)', and those columns never enter the "
           "mean), and '-' where the agent did not run the family. A family whose agents sit on different versions "
           "(the registry over the manifest hash) splits into one column per version, so no column averages two "
           "realizations. Driving agents and warehouse brains are two blocks, each ordered by the mean, ties in "
           "roster order; PlanT 2.0's checkpoints stay separate rows. The mean carries its 95% interval (F87 "
           "2026-10-14, the paper's Table 2): the same bootstrap the documents use, every family's sides resampled "
           "as runs within the side and the equal-weight mean recomputed per replicate, 10,000 replicates, the seed "
           "recorded in the data.")


def _rate_cell(n, k):
    return f"{k}/{n} ({k / n:.2f})" if n else "no runs"


def decision_cell(doc) -> dict:
    """One document's decision sides as the table reads them: the balanced scope's passable side (clean
    passes) and blocked side (stops), the passable go rate from the decomposition (the commitments: T9's
    column for a family without a wait side), the conditional scope where the chain has one (the flow
    families), the anomalies (runs in no curve) and the runs the sides were summed over (the register's
    cells out)."""
    summary, decomposition = doc["decision_summary"], doc["decomposition"]
    sides, go = summary["sides"], decomposition["p_go_passable"]
    cond = summary["scopes"].get("conditional") or {}
    block = summary.get("accuracy")
    if block is None:
        raise ValueError("the document's decision_summary carries no accuracy block (the emitter writes it since F87, "
                         "2026-10-14: the family's balanced accuracy with its bootstrap interval); re-emit the document")
    return {"pass_side": {"n": sides["passable"]["n"], "k": sides["passable"]["k"]},
            "wait_side": {"n": sides["blocked"]["n"], "k": sides["blocked"]["k"]},
            "go_passable": {"n": go["n"], "k": go["k"]},
            "conditional": {"n": cond["n"], "k": cond["k"]} if cond.get("n") else None,
            "anomalies": sum(decomposition.get("anomalies", {}).values()),
            "runs": decomposition["runs"] - decomposition.get("defect_runs", 0),
            # the family's accuracy is the document's (one computation, the emitter's), with its interval
            "accuracy": block["value"], "interval": block["interval"], "accuracy_case": block["case"],
            "bootstrap": {"B": block["bootstrap_B"], "seed": block["bootstrap_seed"]}}


def build_t8(inp):
    """Decision accuracy over the seven driving families and the three warehouse families (F55,
    2026-09-28; Ellington: "we only have 20 blocked routes, all from the charger corridor"; the
    warehouse rows 2026-10-01): per family and agent, the correct-call rate on the passable widths
    (usable windows), on the blocked widths (wait windows), their balanced mean, and for the flow
    families the conditional band beside it. Every family is read the same way since F82: the
    pooled documents' decision_summary (the scorer's per-run decisions summed by the emitter), the
    roster pool's documents then the extension pools'. Rows ordered by family (T8_ORDER, then
    T8_ISAAC_FAMILIES) then agent (roster first)."""
    root = inp["root"]
    rows, labels, cells, documents = [], [], {}, {}

    def place(fam, agent, cell, ver):
        # every cell names the realization its source binds (the registry's version, 2026-10-03)
        cell.update({"family_version": version_leaf(ver), "family_label": labelled(inp, fam, ver)})
        cells.setdefault(fam, {})[agent] = cell
        labels.append(f"{cell['family_label']} - {STYLE['labels'].get(agent, agent)}")

    for fam in [*T8_ORDER, *T8_ISAAC_FAMILIES]:
        docs = docs_of(inp, fam, extension=True)
        if not docs:
            continue
        refs = refs_of(inp, fam, extension=True)
        documents[fam] = {m: dict(refs[m]) for m in docs}
        roster = T6_ROSTER if fam not in T8_ISAAC_FAMILIES else [ISAAC_ROSTER_BRAIN]
        for m in models_in(docs, roster):
            cell = decision_cell(docs[m])
            place(fam, m, cell, fd.version_of(docs[m], root))
            rows.append(_t8_row(cell, "usable" if fam in FLOW_FAMILIES else "passable",
                                "wait" if fam in FLOW_FAMILIES else "blocked"))
    return {"figure": "T8",
            "title": "Table 8 - Decision accuracy over the ten families, seven driving and three warehouse (the "
                     "flow families' wait side from the gap certificates)",
            "col_labels": ["Runs", "Clean pass on passable / usable", "Stop on blocked / wait",
                           "Decision accuracy [95% bootstrap interval]", "Conditional band (clean rolling go)"],
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
                                      "pooled_sides": {"pass": {"n": 0, "k": 0}, "wait": {"n": 0, "k": 0}}})
            pas, wait, go = c["pass_side"], c["wait_side"], c["go_passable"]
            if col in wait_cols:
                a["per_family"][col] = {"accuracy": c["accuracy"], "interval": c["interval"], "pass_side": dict(pas),
                                        "wait_side": dict(wait), "family_version": c["family_version"]}
                if c["accuracy"] is not None:
                    a["families_with_wait_side"].append(col)
                    for side, src in (("pass", pas), ("wait", wait)):
                        a["pooled_sides"][side]["n"] += src["n"]
                        a["pooled_sides"][side]["k"] += src["k"]
            else:
                # the paper's convention for a family with no wait side: the go rate (the commitment), never
                # the clean-pass side
                a["per_family"][col] = {"go_rate": round(go["k"] / go["n"], 4) if go["n"] else None, "go_passable": dict(go),
                                        "family_version": c["family_version"]}
    for a in agents.values():
        # the equal-weight mean over the families with a wait side and its 95% bootstrap interval (F87: the paper's
        # Table 2, runs resampled within each family and side), the one function the emitter uses per family
        block = da.accuracy([{"family": f, "pass": a["per_family"][f]["pass_side"], "wait": a["per_family"][f]["wait_side"]}
                             for f in a["families_with_wait_side"]])
        a["mean_accuracy"] = block["value"]
        a["mean_accuracy_interval"] = block["interval"]
        a["mean_accuracy_case"] = block["case"]
        a["bootstrap"] = {"B": block["bootstrap_B"], "seed": block["bootstrap_seed"], "method": block["method"],
                          "degenerate_sides": block["degenerate_sides"]}
        pg, pw = a["pooled_sides"]["pass"], a["pooled_sides"]["wait"]
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
               (f"{a['mean_accuracy']:.2f} [{a['mean_accuracy_interval'][0]:.2f}, {a['mean_accuracy_interval'][1]:.2f}]"
                if a["mean_accuracy"] is not None else "n/a")]
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
            "col_labels": ["Families with a wait side", "Decision accuracy (mean) [95% bootstrap interval]",
                           *[c["label"] + ("" if c["id"] in wait_cols else " (go rate)") for c in columns]],
            "row_labels": labels, "rows": rows, "row_ids": order, "families": fams, "columns": columns,
            "wait_side_columns": wait_cols, "agents": agents, "documents": t8["documents"], "rule": T9_RULE}


def _t8_row(cell, pass_name, wait_name):
    pas, wait, cond = cell["pass_side"], cell["wait_side"], cell["conditional"]
    if cell["accuracy"] is not None:
        lo, hi = cell["interval"]
        accuracy = f"{cell['accuracy']:.2f} [{lo:.2f}, {hi:.2f}]"
    elif pas["n"]:
        accuracy = f"n/a (no {wait_name} side)"
    else:
        accuracy = f"n/a (no {pass_name} side)"
    anomalies = f" (+{cell['anomalies']} anomalies excluded)" if cell["anomalies"] else ""
    return [f"{cell['runs']}{anomalies}", _rate_cell(pas["n"], pas["k"]), _rate_cell(wait["n"], wait["k"]), accuracy,
            _rate_cell(cond["n"], cond["k"]) if cond else "-"]



# --- TN: the paper's numbers (F89, 2026-10-16) --------------------------------------------------------------
# --- TS: decision accuracy under the certification constants (2026-10-17) ---

SENSITIVITY_ADMITTED = {fg.KIND: ("margin_s",)}   # the knobs this backend can turn: the gap certificate's margin
TS_RULE = ("The robustness check of Table 2's ranking (the paper's 'Robustness to the certification constants'): the same "
           "run rows re-read under moved constants and overridden labels, one perturbation at a time, as the declaration "
           "(decision_sensitivity/v0.1, artifacts/examples/figure_specs/) lists them. A knob moves one parameter of one "
           "instrument kind for every family that holds that instrument: at each value of the gap certificate's margin "
           "every (variant, seed) window of the flow families is re-banded by the certificate's own rule "
           "(flow_gap_certificate.band_for over the pinned certificate's per-seed windows and floors; usable is the "
           "passable side, wait the blocked side, conditional stays in its own scope, a registered defect cell stays "
           "out) and every run is decided again by the one decision rule (scl_core.run_rows.decision). An override "
           "gives one variant of one realization (family, manifest hash, variant id) the label it names, the other "
           "labels as the manifest certified them. Per agent and setting, the accuracy is Table 2's: the equal-weight "
           "mean over the same families with a wait side, through the same accuracy function and bootstrap. The "
           "baseline setting (the instruments' own values, no override) must equal Table 2 exactly or the table "
           "refuses. The band per agent is the lowest and highest mean over the perturbation settings; the order per "
           "setting is by the mean, ties in Table 2's order. The report block of the declaration names the positions "
           "the paragraph claims: each claimed top position is held by one agent at every setting or the record says "
           "it does not, the claimed bottom positions by one set, and the middle group is every other agent; the "
           "groups' bands are the lowest low and the highest high of their members. The warehouse families hold no "
           "knob: their labels carry measurement tolerances, not a caution constant (adapters/PARITY.md G36).")


def declared_certificate_campaigns(path=INSTRUMENTS_PATH) -> dict:
    """{family_id: [campaign ids]} the fail2drive section of the instruments file declares as each gap certificate's
    sample (F99, 2026-10-21); {} when the file has no such table."""
    try:
        table = json.loads(Path(path).read_text()).get("fail2drive", {}).get("gap_certificate_campaigns") or {}
    except FileNotFoundError:
        return {}
    return {fam: list(ids) for fam, ids in table.items() if fam != "note"}


def check_certificate_sample(family: str, cert: dict, declared: dict, path="") -> None:
    """A gap certificate's floors are measured from the recordings it was given, so the campaigns it was measured over are
    part of the instrument: the set in its provenance.inputs must equal the set the instruments file declares for the
    family (F99, 2026-10-21: the committed certificates had been measured over a hand-typed set that no reader checked).
    Refuses, naming both sets, on a difference, on a certificate without recorded inputs, and on a flow family without a
    declaration."""
    recorded = sorted({i.get("campaign_id") for i in (cert.get("provenance") or {}).get("inputs") or []} - {None})
    if not recorded:
        raise ValueError(f"{family}: the gap certificate {path} records no input campaigns (provenance.inputs); the sample "
                         f"it was measured over is unknown")
    if family not in declared:
        raise ValueError(f"{family}: no declared campaign sample for its gap certificate (fail2drive.gap_certificate_campaigns "
                         f"in {INSTRUMENTS_PATH.relative_to(REPO_ROOT)}); the certificate records {recorded}")
    wanted = sorted(declared[family])
    if recorded != wanted:
        raise ValueError(f"{family}: the gap certificate {path} was measured over {recorded} but the instruments file declares "
                         f"{wanted}; regenerate the certificate over the declared sample or change the declaration deliberately")


def flow_certificates(inp, families) -> dict:
    """{family: (certificate document, pin)} of the gap certificate every document of a flow family pins (role
    gap_certificate, hash-checked against the tree); two documents of one family pinning two certificates refuse."""
    out = {}
    for fam in families:
        docs = docs_of(inp, fam, extension=True)
        pins = {m: fd.instruments_by_role(d).get("gap_certificate") for m, d in docs.items()}
        missing = [m for m, pin in pins.items() if pin is None]
        if missing:
            raise ValueError(f"TS: the documents of {fam} for {missing} pin no gap certificate (role gap_certificate)")
        shas = {pin["sha256"] for pin in pins.values()}
        if len(shas) != 1:
            raise ValueError(f"TS: the documents of {fam} pin {len(shas)} gap certificates ({sorted(s[:12] for s in shas)}); one family, one certificate")
        pin = next(iter(pins.values()))
        cert = fd.read_pinned(pin, inp["root"], fam, "gap_certificate")
        check_certificate_sample(fam, cert, declared_certificate_campaigns(), pin.get("path", ""))
        out[fam] = (cert, pin)
    return out


def load_sensitivity(inp) -> tuple:
    """(declaration, pin) of the sensitivity declaration the run names (--sensitivity), checked against the knobs this
    backend admits."""
    path = inp.get("sensitivity")
    if path is None:
        raise ValueError("TS takes a sensitivity declaration (--sensitivity artifacts/examples/figure_specs/decision_sensitivity.json)")
    doc, sha = ss.load_declaration(path)
    try:
        ss.check_declaration(doc, SENSITIVITY_ADMITTED)
    except ValueError as exc:
        raise ValueError(f"{Path(path).name}: {exc}") from exc
    try:
        rel = portable_path(Path(path).resolve(), inp["root"])
    except ValueError:
        rel = str(Path(path).resolve())      # a declaration outside the tree (a test's): pinned by its absolute path
    return doc, {"path": rel, "sha256": sha}


def roster_models(roster) -> list:
    """The models the roster spec names, a checkpoint group expanded to its members, in the roster's order."""
    return [m for a in roster["agents"] for m in (a.get("checkpoints") or [a["model"]])]


def reference_agents(roster) -> list:
    """The roster's reference agents (`reference: true`: the privileged expert the learned agents are measured
    against), checkpoints expanded; [] when the roster marks none. The one home of who the reference is (2026-10-17):
    the paper tables' bold exclusion and the numbers document's "learned agents" both read it."""
    out = []
    for a in [*roster.get("agents", []), *roster.get("warehouse", [])]:
        if a.get("reference"):
            out += list(a.get("checkpoints") or [a["model"]])
    return out


def build_ts(inp, roster):
    """Decision accuracy under the perturbed certification constants (TS_RULE): the declaration's settings over the
    roster's driving agents as Table 2 prints them (the paper's agents and labels are the roster spec's, the way
    the profile figures take them; Table 9 also carries the agents the paper does not print) and their families
    with a wait side; the baseline must equal Table 2."""
    root = inp["root"]
    decl, decl_pin = load_sensitivity(inp)
    settings = ss.settings(decl)
    t9 = build_t9(inp)
    labels_of = {}
    for a in roster["agents"]:
        members = a.get("checkpoints") or [a["model"]]
        for i, m in enumerate(members, 1):
            labels_of[m] = a["label"] if len(members) == 1 else f"{a['label']} (ckpt {i})"
    agents = [m for m in t9["row_ids"] if m in labels_of and t9["agents"][m]["domain"] == "driving"
              and t9["agents"][m]["mean_accuracy"] is not None]
    absent = [m for m in roster_models(roster) if m not in t9["agents"]]
    if absent:
        raise ValueError(f"TS: the roster names {absent} and Table 9 holds no row for them (the input's documents are of "
                         f"{sorted(t9['agents'])})")
    if not agents:
        raise ValueError("TS: Table 2 holds no driving agent of the roster with a decision accuracy")
    col_family = {c["id"]: c for c in t9["columns"]}
    families = {}
    for m in agents:
        fams = []
        for col in t9["agents"][m]["families_with_wait_side"]:
            c = col_family[col]
            if c["id"] != c["family"]:
                raise ValueError(f"TS: {c['family']} is realized more than once among the agents ({c['id']}); the sweep keys rows "
                                 f"by family and cannot tell the realizations apart (a named gap: one realization per driving family)")
            fams.append(c["family"])
        families[m] = fams
    fams_all = sorted({f for fs in families.values() for f in fs})
    flow = [f for f in fams_all if f in FLOW_FAMILIES]
    certs = flow_certificates(inp, flow)
    # every override names a realization the family's documents bind (the registry's hash), checked here
    realization = {}
    for o in decl["overrides"]:
        fam = o["family_id"]
        if fam not in fams_all:
            raise ValueError(f"TS: override {fam}/{o['variant']} names a family outside Table 2's families with a wait side {fams_all}")
        if fam not in realization:
            shas = set()
            for d in docs_of(inp, fam, extension=True).values():
                try:
                    shas.add(fv.document_realization(d, root)[2])
                except ValueError as exc:
                    raise ValueError(f"TS: the override of {fam} names a realization and the input's documents bind none ({exc})") from exc
            realization[fam] = shas
        if o["manifest_sha256"] not in realization[fam]:
            raise ValueError(f"TS: override {fam}/{o['variant']} names realization {o['manifest_sha256'][:12]}; the documents of {fam} "
                             f"bind {sorted(s[:12] for s in realization[fam])}")
    rows = [r for fam in fams_all for r in rows_of(inp, fam, extension=True) if r["model"] in families]

    def relabel(r, setting):
        fam = r["family"]
        if fam in certs:
            if r["assignment"] == fg.DEFECT:
                return fg.DEFECT
            cert = certs[fam][0]
            margin = cert["margin_s"]
            k = setting["knob"]
            if k and k["instrument"] == cert["certificate_kind"] and k["parameter"] == "margin_s":
                margin = k["value"]
            band = fg.band_for(annex(r, "gap_window_s"), cert["floors"], margin)
            return {"usable": "passable", "wait": "blocked"}.get(band, band)
        for o in setting["overrides"]:
            if o["family_id"] == fam and o["variant"] == r["variant"]:
                return o["assignment"]
        return r["assignment"]

    sweep = ss.sweep(rows, settings, relabel, families=families, roster=agents, report=decl["report"])
    # the baseline is Table 2, to the digit, or the sweep does not read the rows the way the scorer did
    off = {m: (sweep["agents"][m]["baseline"], t9["agents"][m]["mean_accuracy"]) for m in agents
           if sweep["agents"][m]["baseline"] != t9["agents"][m]["mean_accuracy"]}
    if off:
        raise ValueError(f"TS: the baseline setting differs from Table 2 for {off} (sweep value, Table 2 value); the relabelling does "
                         f"not reproduce the scorer's decisions")
    # the settings' printed labels: a knob by its value, an override by the variant's measured x and its label
    labels = {}
    for st in settings:
        if st["id"] == ss.BASELINE:
            labels[st["id"]] = "Table 2 (baseline)"
        elif st["knob"]:
            labels[st["id"]] = f"{st['knob']['parameter']} = {st['knob']['value']:g}"
        else:
            o = st["overrides"][0]
            xs = sorted({r["x"] for r in rows if r["family"] == o["family_id"] and r["variant"] == o["variant"]})
            labels[st["id"]] = f"{family_label(o['family_id'])} {fp.fmt(xs[0])} as {o['assignment']}"
    pert = sweep["perturbations"]
    table_rows, row_labels = [], []
    for m in agents:
        a = sweep["agents"][m]
        cells = [f"{a['settings'][sid]['value']:.2f}" if a["settings"][sid]["value"] is not None else "n/a" for sid in sweep["settings"]]
        band = f"{a['band']['low']:.2f} to {a['band']['high']:.2f}" if a["band"] else a["band_case"]
        pos = sweep["positions"][m]
        table_rows.append(cells + [band, ", ".join(str(p) for p in pos)])
        row_labels.append(labels_of[m])
    return {"figure": "TS",
            "title": f"Table S - Decision accuracy under the certification constants ({len(pert)} perturbation settings, one at a "
                     f"time; the band per agent over them; the positions each agent holds)",
            "col_labels": [labels[sid] for sid in sweep["settings"]] + [f"Band over {len(pert)} settings", "Positions held"],
            "row_labels": row_labels, "rows": table_rows, "row_ids": agents, "settings": sweep["settings"], "setting_labels": labels,
            "perturbations": pert, "ranked_perturbations": sweep["ranked_perturbations"], "unranked_perturbations": sweep["unranked_perturbations"],
            "agents": sweep["agents"], "order": sweep["order"], "positions": sweep["positions"],
            "stable": sweep["stable"], "report": sweep["report"], "families": families, "bootstrap": sweep["bootstrap"],
            "declaration": decl_pin, "knobs": decl["knobs"], "overrides": decl["overrides"], "roster": roster.get("note"),
            "certificates": {fam: pin for fam, (doc, pin) in certs.items()},
            "documents": t9["documents"], "method": sweep["method"], "rule": TS_RULE}


LADDER_FAMILY = "fm_003_asset_swap"
TL_RULE = ("The identity ladder as a table: per object (the family's variants in the manifest's order, each one roadside "
           "object at the certified-identical pose) and per agent, the clean passes of the runs and the other outcomes "
           "beside them, read from the pooled rows; the paper's Table 3 (the identity ladder) is written from these cells "
           "by the paper-tables spec (PT).")


def ladder_cells(rows) -> OrderedDict:
    """{object: {model: {clean, n, other_outcomes}}} over the ladder family's rows, the objects in the family's order
    (the rows' variant index, the manifest's numbering; a row without one follows, by name): the one derivation TN's
    per-object entries and the TL table share."""
    per, order = {}, {}
    for r in rows:
        asset = _asset(r)
        idx = annex(r, "variant_index")
        key = (idx is None, idx if idx is not None else asset)
        order[asset] = min(order.get(asset, key), key)
        d = per.setdefault((asset, r["model"]), {"n": 0, "clean": 0, "outcomes": Counter()})
        d["n"] += 1
        d["clean"] += int(bool(r["clean"]))
        d["outcomes"][r["outcome"]] += 1
    out = OrderedDict()
    for asset in sorted(order, key=lambda a: order[a]):
        out[asset] = {}
        for (a, m), d in per.items():
            if a == asset:
                out[asset][m] = {"clean": d["clean"], "n": d["n"],
                                 "other_outcomes": {o: v for o, v in sorted(d["outcomes"].items()) if o != "clean_thread"}}
    return out


def build_tl(inp):
    """The identity ladder as a table (TL_RULE): objects by agents, clean passes of runs with the other outcomes."""
    rows = rows_of(inp, LADDER_FAMILY, extension=True)
    if not rows:
        return {"figure": "TL", "status": f"no {LADDER_FAMILY} rows in the input"}
    cells = ladder_cells(rows)
    models = models_in({r["model"]: None for r in rows}, F7_ROSTER)
    table = []
    for asset, per in cells.items():
        line = []
        for m in models:
            c = per.get(m)
            if c is None:
                line.append("-")
            else:
                other = ", ".join(f"{OUTCOME_LABELS.get(o, o)} {v}" for o, v in c["other_outcomes"].items())
                line.append(f"{c['clean']}/{c['n']}" + (f" ({other})" if other else ""))
        table.append(line)
    return {"figure": "TL", "title": "Table L - Identity ladder: clean passes of runs per object and agent",
            "col_labels": [STYLE["labels"].get(m, m) for m in models], "row_labels": list(cells),
            "rows": table, "objects": list(cells), "models": models, "cells": cells, "family": LADDER_FAMILY,
            "pool": pool_of(inp, LADDER_FAMILY), "rule": TL_RULE}


TN_RULE = ("Every quantity the paper's text and captions cite that no table prints, by a stable name, with its value, "
           "the strings the paper would print for it (`prints`) and its source (the documents' fields, a row filter "
           "over the pooled run rows or the route metrics, a manifest's measured field, or a declared constant and "
           "where it is declared). The pools and instruments are the same the tables read; the lineage block pins "
           "them. A number the text prints that this document cannot name is a number to derive or to retract "
           "(scripts/check_paper_numbers.py reports both directions). Sums over families weigh runs, never families; "
           "the per-agent decision accuracy is Table 9's (equal weight over families).")
TN_STOP_SIDE_FAMILIES = ("fm_004_threshold_sweep", "fm_011_flow_speed_sweep", "fm_012_source_dist_interval_sweep")


def run_score(r):
    """A run's driving score by chain: the threading rows' `driving_score`, the flow rows' `score_composed`."""
    v = annex(r, "driving_score")
    return v if v is not None else annex(r, "score_composed")


def candidate_counts(manifest: dict) -> dict:
    """{entry key: (count, its field)} over a family manifest's variants: every candidate the solver built, the accepted,
    and the rejected grouped by the first token of their reject reasons (a table keyed by the reason, never a slot)."""
    variants = manifest.get("variants") or []
    if not variants:
        return {}
    out = {"candidates": (len(variants), "variants (every candidate the solver built, accepted or rejected)")}
    accepted = sum(1 for v in variants if v.get("verdict", "accepted") == "accepted")
    out["candidates.accepted"] = (accepted, "variants[].verdict == accepted")
    reasons = Counter()
    for v in variants:
        if v.get("verdict", "accepted") != "accepted":
            tokens = sorted({str(r).split(":")[0].strip() for r in (v.get("reject_reasons") or ["unstated"])})
            reasons["+".join(tokens)] += 1
    for reason, k in sorted(reasons.items()):
        out[f"candidates.rejected.{reason}"] = (k, f"variants[].verdict == rejected whose reject_reasons start with {reason!r}")
    return out


def defect_spawn_headways(cert: dict) -> dict:
    """{variant stem: (scenario id, {seed: spawn_headway_s})} over the certificate's seeds table, for the cells it bands
    as defect: one entry per defect variant."""
    out = {}
    for s in cert.get("seeds") or []:
        if s.get("band") != "defect":
            continue
        stem = s["scenario_id"].rsplit("_", 1)[-1] if "_" in s["scenario_id"] else s["scenario_id"]
        out.setdefault(stem, (s["scenario_id"], {}))[1][s["seed"]] = s.get("spawn_headway_s")
    return out


def learned_flow_range(t8cells: dict, roster) -> dict | None:
    """The lowest and highest per-family decision accuracy over the flow families and the roster's agents that are not
    the reference; None when no such cell has a value."""
    learned = [m for m in roster_models(roster) if m not in reference_agents(roster)]
    vals = [(fam, m, t8cells[fam][m]["accuracy"]) for fam in FLOW_FAMILIES if fam in t8cells
            for m in learned if m in t8cells[fam] and t8cells[fam][m]["accuracy"] is not None]
    if not vals:
        return None
    lo, hi = min(vals, key=lambda t: t[2]), max(vals, key=lambda t: t[2])
    return {"low": lo[2], "high": hi[2], "at_low": {"family": lo[0], "agent": lo[1]}, "at_high": {"family": hi[0], "agent": hi[1]},
            "agents": learned, "families": [f for f in FLOW_FAMILIES if f in t8cells]}


def two_decimal_forms(phrase: str) -> list:
    """The boundary phrase's numbers re-rounded to two decimals, as the paper prints metre boundaries: the whole phrase,
    the boundary and the interval. The subtitle keeps its three decimals below 1 m; both forms are printed forms."""
    nums = re.findall(r"\d+\.\d+", phrase)
    if not nums:
        return []
    two = phrase
    for n in nums:
        two = two.replace(n, f"{float(n):.2f}", 1)
    out = [two, two.split(" ")[0]]
    if "[" in two:
        out.append(two[two.index("["):two.index("]") + 1])
    return out


def build_tn(inp):
    """TN - the paper's numbers: see TN_RULE. One document of named numbers, grouped by family and agent, each
    with the strings the paper prints for it, so the text can be checked mechanically against the pipeline."""
    root = inp["root"]
    numbers = {}
    roster = load_roster(inp["roster"]) if inp.get("roster") else None

    def put(name, value, prints, **source):
        numbers[name] = {"value": value, "prints": list(dict.fromkeys(str(x) for x in prints)), "source": source}

    def kn(k, n):
        return [f"{k} of {n}", f"{k} of its {n}", f"{k} of the {n}", f"{k}/{n}"]

    fams = [f for f in [*T8_ORDER, *T8_ISAAC_FAMILIES] if docs_of(inp, f, extension=True)]
    # 1. per family: the tested axis range, the variant and blocked counts, the manifest's minimum free corridor
    for fam in fams:
        docs = docs_of(inp, fam, extension=True)
        first = next(iter(docs.values()))
        comp = first["outcome_composition"]
        xs = sorted({c["x"] for c in comp if not isinstance(c["x"], str)})
        blocked = sorted({c["x"] for c in comp if c["assignment"] == "blocked"})
        pool = pool_of(inp, fam)
        label = labelled(inp, fam)
        if xs:
            put(f"{fam}.axis_range", [xs[0], xs[-1]], [f"{xs[0]:.2f}", f"{xs[-1]:.2f}", f"{xs[0]:.1f}", f"{xs[-1]:.1f}"],
                kind="documents", pool=pool, field="outcome_composition[].x", family_label=label)
        put(f"{fam}.variants", len(comp), [str(len(comp))], kind="documents", pool=pool, field="outcome_composition (one entry per variant)")
        put(f"{fam}.blocked_variants", len(blocked), [str(len(blocked))], kind="documents", pool=pool,
            field="outcome_composition[].assignment == blocked", values=blocked)
        passable = sorted({c["x"] for c in comp if c["assignment"] == "passable"})
        put(f"{fam}.passable_variants", len(passable), list(dict.fromkeys([str(len(passable)), number_word(len(passable))])),
            kind="documents", pool=pool, field="outcome_composition[].assignment == passable")
        if blocked:                                              # F97: the paper names the blocked widths' range and its top
            put(f"{fam}.blocked_axis_range", [blocked[0], blocked[-1]],
                list(dict.fromkeys([f"{blocked[0]:.2f} to {blocked[-1]:.2f}", f"{blocked[-1]:.2f}", f"{blocked[0]:.2f}", f"{blocked[-1]:.1f}"])),
                kind="documents", pool=pool, field="outcome_composition[].x of the blocked variants, the lowest and the highest")
        reps = {}
        for r in rows_of(inp, fam):
            reps.setdefault((r["model"], r["variant"]), set()).add(r["repetition"])
        if reps:                                                 # F97: "12 variants, 3 seeds" / "5 policy samples"
            counts = Counter(len(s) for s in reps.values())      # a scripted reference runs once per variant, a policy several times
            n = max(counts)
            put(f"{fam}.repetitions_per_variant", {"value": n, "counts": {str(k): v for k, v in sorted(counts.items())}},
                list(dict.fromkeys([str(n), number_word(n)])), kind="rows", pool=pool,
                field="distinct repetitions per (agent, variant) over the pool's rows (traffic seeds on CARLA, policy samples on Isaac): the most any cell has, what the family offers an agent; counts lists every count with how many (agent, variant) cells have it (a deterministic reference runs once)")
        decls = fd.declarations(first, root) if (first.get("pooling") or {}).get("campaigns") else []
        if decls:
            manifest = json.loads((Path(root) / decls[0].manifest_path).read_text())
            widths = [v["measured"]["minimum_free_corridor_width"] for v in manifest.get("variants", [])
                      if v.get("verdict", "accepted") == "accepted" and isinstance(v.get("measured"), dict)
                      and v["measured"].get("minimum_free_corridor_width") is not None]
            if widths:
                put(f"{fam}.min_free_corridor_m", round(min(widths), 4), [f"{min(widths):.2f}", f"{min(widths):.0f}"], kind="manifest",
                    path=str(decls[0].manifest_path), field="variants[].measured.minimum_free_corridor_width, the minimum over accepted variants")
            for key, (value, field) in candidate_counts(manifest).items():
                put(f"{fam}.{key}", value, list(dict.fromkeys([str(value), number_word(value)])), kind="manifest", path=str(decls[0].manifest_path), field=field)
            intr = [v["measured"]["maximum_route_intrusion"] for v in manifest.get("variants", [])
                    if v.get("verdict", "accepted") == "accepted" and isinstance(v.get("measured"), dict)
                    and v["measured"].get("maximum_route_intrusion") is not None]
            if len(intr) >= 2:                                   # F97: one intrusion for the whole family (the identity ladder)
                med = sorted(intr)[len(intr) // 2]
                within = sum(abs(x - med) <= 0.01 for x in intr)
                if within >= 0.75 * len(intr):
                    put(f"{fam}.max_route_intrusion_m", {"median": round(med, 4), "min": round(min(intr), 4), "max": round(max(intr), 4),
                                                         "within_1cm_of_median": within, "variants": len(intr)},
                        list(dict.fromkeys([f"{med:.1f}", f"{med:.2f}"])), kind="manifest", path=str(decls[0].manifest_path),
                        field="variants[].measured.maximum_route_intrusion over accepted variants: the median, printed when at least three quarters of the variants lie within 1 cm of it; min and max beside")
    # 2. per agent: the sums over the families with a stop side, the decision accuracy, the driving scores
    t9 = build_t9(inp)
    wait_windows_n = set()
    for agent, a in t9["agents"].items():
        if a["mean_accuracy"] is not None:
            lo, hi = a["mean_accuracy_interval"]
            put(f"{agent}.decision_accuracy", {"value": a["mean_accuracy"], "interval": a["mean_accuracy_interval"],
                                                "families": a["families_with_wait_side"]},
                [f"{a['mean_accuracy']:.2f}", f"[{lo:.2f}, {hi:.2f}]"], kind="table", table="T9", field="mean_accuracy, mean_accuracy_interval")
        pk = sum(a["per_family"][f]["pass_side"]["k"] for f in a["families_with_wait_side"])
        pn = sum(a["per_family"][f]["pass_side"]["n"] for f in a["families_with_wait_side"])
        wk = sum(a["per_family"][f]["wait_side"]["k"] for f in a["families_with_wait_side"])
        wn = sum(a["per_family"][f]["wait_side"]["n"] for f in a["families_with_wait_side"])
        flow = [f for f in a["families_with_wait_side"] if f.split("@")[0] in FLOW_FAMILIES]
        fk = sum(a["per_family"][f]["wait_side"]["k"] for f in flow)
        fn = sum(a["per_family"][f]["wait_side"]["n"] for f in flow)
        if pn:
            put(f"{agent}.clean_passes_on_passable_and_usable", {"k": pk, "n": pn}, kn(pk, pn) + [str(pk)], kind="table", table="T8",
                field="pass_side summed over the families with a wait side", families=a["families_with_wait_side"])
        if wn:
            put(f"{agent}.stops_on_blocked_and_wait", {"k": wk, "n": wn}, kn(wk, wn) + [str(wk)], kind="table", table="T8",
                field="wait_side summed over the families with a wait side", families=a["families_with_wait_side"])
        if fn:
            put(f"{agent}.waits_on_wait_windows", {"k": fk, "n": fn}, kn(fk, fn) + [str(fk)], kind="table", table="T8",
                field="wait_side summed over the oncoming-flow families (the wait windows)", families=flow)
            wait_windows_n.add(fn)
        for col, pf in a["per_family"].items():                  # F97: each side's k of n per family ("stops on 3 of 9")
            sides = {s: pf.get(s) for s in ("pass_side", "wait_side") if isinstance(pf.get(s), dict) and pf[s].get("n")}
            for side, kv in sides.items():
                put(f"{col}.{agent}.{side}", dict(kv), kn(kv["k"], kv["n"]), kind="table", table="T8",
                    field=f"{side} of the decision rule in this family: k correct of the n runs the side counts (anomalous and defect runs out)")
            if sides:
                ck, cn = sum(v["k"] for v in sides.values()), sum(v["n"] for v in sides.values())
                put(f"{col}.{agent}.correct_calls", {"k": ck, "n": cn}, kn(ck, cn), kind="table", table="T8",
                    field="both sides of the decision rule summed: correct calls of the runs the rule counts")
        if a["domain"] == "driving":
            means = {}
            for fam in fams:
                rs = [r for r in rows_of(inp, fam, agent, extension=True) if r["assignment"] != "defect" and run_score(r) is not None]
                if rs:
                    means[fam] = round(sum(run_score(r) for r in rs) / len(rs), 4)
                    put(f"{fam}.{agent}.mean_driving_score", {"value": means[fam], "runs": len(rs)}, [f"{means[fam]:.1f}"],
                        kind="rows", pool=pool_of(inp, fam), field="mean of annex.driving_score (threading) or annex.score_composed (flow) over the agent's runs, defect variants out")
            stop = [f for f in TN_STOP_SIDE_FAMILIES if f in means]
            if len(stop) == len(TN_STOP_SIDE_FAMILIES):
                avg = round(sum(means[f] for f in stop) / len(stop), 4)
                put(f"{agent}.avg_driving_score", {"value": avg, "families": stop}, [f"{avg:.1f}"], kind="rows",
                    field="the equal-weight mean over the three families with a stop side of the per-family mean driving score (the paper's Table 2, Avg. DS)")
    if len(wait_windows_n) == 1 and 0 not in wait_windows_n:          # F97: "the 29 wait windows per agent"
        n = next(iter(wait_windows_n))
        put("flow_families.wait_windows_per_agent", n, [str(n)], kind="table", table="T8",
            field="the wait side's n summed over the oncoming-flow families, the same for every agent of the roster")
    # 3. the corridor-width near misses: clean passes that cleared a barrier by less than the mirror allowance
    fam = "fm_002_corridor_pincer"
    metrics_all = list(inp["metrics"]) + list(inp.get("extension_metrics") or [])
    if fam in fams and metrics_all:
        runs = {(r["model"], r["variant"], r["repetition"]): r for r in rows_of(inp, fam, extension=True)}
        docs = docs_of(inp, fam, extension=True)
        decls = fd.declarations(next(iter(docs.values())), root) if (next(iter(docs.values())).get("pooling") or {}).get("campaigns") else []
        mirror = route_metrics.mirror_allowance_for(json.loads((Path(root) / decls[0].manifest_path).read_text())) if decls else None
        if mirror is not None:
            put(f"{fam}.mirror_allowance_m", mirror, [f"{mirror:.2f}"], kind="manifest", path=str(decls[0].manifest_path),
                field="provenance ego_mirror_allowance_m (route_metrics.mirror_allowance_for)")
            per = {}
            for m in metrics_all:
                if m.get("family") != fam or m.get("min_clearance_m") is None:
                    continue
                r = runs.get((m["model"], m.get("scenario_id"), m.get("seed")))
                if r is None or not r["clean"]:
                    continue
                d = per.setdefault(m["model"], {"near": 0, "clean": 0, "scores": []})
                d["clean"] += 1
                if m["min_clearance_m"] < mirror:
                    d["near"] += 1
                    d["scores"].append(run_score(r))
            for agent, d in sorted(per.items()):
                sc = sorted(d["scores"])
                put(f"{fam}.{agent}.near_misses", {"k": d["near"], "clean_passes": d["clean"],
                                                    "score_range": [round(sc[0], 1), round(sc[-1], 1)] if sc else None},
                    kn(d["near"], d["clean"]) + ([f"{sc[0]:.0f} to {sc[-1]:.0f}"] if sc else []), kind="metrics",
                    field="clean runs whose metrics min_clearance_m < the mirror allowance; the driving-score range of those runs")
            trio = [m for m in ("lead_regnety032", "tfpp", "bridgedrive") if m in per]
            if trio:
                k = sum(per[m]["near"] for m in trio); n = sum(per[m]["clean"] for m in trio)
                sc = sorted(x for m in trio for x in per[m]["scores"])
                put(f"{fam}.near_misses.sensor_roster", {"k": k, "clean_passes": n, "agents": trio,
                                                          "score_range": [round(sc[0], 1), round(sc[-1], 1)] if sc else None},
                    kn(k, n) + ([f"{sc[0]:.0f} to {sc[-1]:.0f}"] if sc else []), kind="metrics", field="the sum over the three sensor agents of the roster")
    # 4. the gauntlet's constants and the largest obstacle displacement per agent
    fam = "fm_004_threshold_sweep"
    if fam in fams:
        refs = reference_lines(fam)
        for key, value in refs.items():
            put(f"{fam}.{key}", value, [f"{value:.2f}"], kind="instrument",
                field="paper_figures.reference_lines: the vehicle's footprint from the asset registry, the mirror allowance and the clearance margin from the manifest's provenance")
        disp = {}
        for m in list(inp["metrics"]) + list(inp.get("extension_metrics") or []):
            if m.get("family") == fam and isinstance(m.get("max_obstacle_displacement_m"), (int, float)):
                disp[m["model"]] = max(disp.get(m["model"], 0.0), m["max_obstacle_displacement_m"])
        for agent, v in sorted(disp.items()):
            put(f"{fam}.{agent}.max_obstacle_displacement_m", round(v, 2), [f"{v:.0f}", f"{v:.1f}"], kind="metrics",
                field="max over the agent's runs of max_obstacle_displacement_m")
    # 5. the flow families' conditional windows: entry states and clean crossings
    for fam in FLOW_FAMILIES:
        if fam not in fams:
            continue
        for agent in sorted({r["model"] for r in rows_of(inp, fam, extension=True)}):
            cond = [r for r in rows_of(inp, fam, agent, extension=True) if r["assignment"] == "conditional"]
            if not cond:
                continue
            c = Counter((annex(r, "entry_state"), bool(r["clean"])) for r in cond)
            rolling = sum(v for (e, _), v in c.items() if e == "rolling")
            put(f"{fam}.{agent}.conditional_windows", {"n": len(cond), "rolling": rolling, "rolling_clean": c.get(("rolling", True), 0),
                                                        "rest": len(cond) - rolling, "rest_clean": c.get(("rest", True), 0)},
                kn(rolling, len(cond)), kind="rows", pool=pool_of(inp, fam), field="assignment == conditional: annex.entry_state and clean")
    # 6. the warehouse brains: outcomes per side, contacts, detours, the commitment per gap, the sums over families
    sums = {}
    for fam in [f for f in T8_ISAAC_FAMILIES if f in fams]:
        docs = docs_of(inp, fam, extension=True)
        for brain, doc in docs.items():
            rs = rows_of(inp, fam, brain, extension=True)
            if not rs:
                continue
            for side in ("passable", "blocked"):
                sr = [r for r in rs if r["assignment"] == side]
                oc = Counter(r["outcome"] for r in sr)
                contacts = sum(v for o, v in oc.items() if o.endswith("_contact"))
                put(f"{fam}.{brain}.{side}_outcomes", {"n": len(sr), "outcomes": dict(oc), "contacts": contacts},
                    [f"{contacts} of {len(sr)}", f"{contacts} of its {len(sr)}", str(contacts)], kind="rows", pool=pool_of(inp, fam),
                    field=f"outcome counts on the {side} side; contacts = outcomes ending in _contact")
            contacts_all = sum(1 for r in rs if r["outcome"].endswith("_contact"))
            put(f"{fam}.{brain}.contacts", {"k": contacts_all, "n": len(rs)}, kn(contacts_all, len(rs)), kind="rows", pool=pool_of(inp, fam),
                field="runs whose outcome ends in _contact, both sides")
            put(f"{fam}.{brain}.detours", sum(1 for r in rs if annex(r, "detour_attempt")), [str(sum(1 for r in rs if annex(r, "detour_attempt")))],
                kind="rows", pool=pool_of(inp, fam), field="annex.detour_attempt summed")
            pts = [(p["x"], p["k"], p["n"]) for p in doc["curves"]["p_go"]["points"]]
            full = [x for x, k, n in pts if n and k == n]
            put(f"{fam}.{brain}.commitment_per_gap", [{"x": x, "k": k, "n": n} for x, k, n in pts],
                [f"{min(full):.1f}"] if full else [], kind="documents", pool=pool_of(inp, fam),
                field="curves.p_go.points (k of n goes per gap); prints: the narrowest gap where every sample goes")
            cell = build_t8(inp)["cells"].get(fam, {}).get(brain)
            if cell:
                s = sums.setdefault(brain, {"pk": 0, "pn": 0, "wk": 0, "wn": 0, "families": []})
                s["pk"] += cell["pass_side"]["k"]; s["pn"] += cell["pass_side"]["n"]
                s["wk"] += cell["wait_side"]["k"]; s["wn"] += cell["wait_side"]["n"]; s["families"].append(fam)
    for brain, s in sums.items():
        put(f"{brain}.warehouse_clean_passes", {"k": s["pk"], "n": s["pn"], "families": s["families"]}, kn(s["pk"], s["pn"]),
            kind="table", table="T8", field="pass_side summed over the warehouse families")
        put(f"{brain}.warehouse_stops", {"k": s["wk"], "n": s["wn"], "families": s["families"]}, kn(s["wk"], s["wn"]),
            kind="table", table="T8", field="wait_side summed over the warehouse families")
    # 7. the identity ladder: clean passes of runs per object and agent, with the other outcome
    fam = LADDER_FAMILY
    if fam in fams:
        cells = ladder_cells(rows_of(inp, fam, extension=True))      # the derivation TL shares
        for asset, agent, d in sorted((a, m, c) for a, per in cells.items() for m, c in per.items()):
            put(f"{fam}.{asset}.{agent}", dict(d), [f"{d['clean']}/{d['n']}"], kind="rows", pool=pool_of(inp, fam),
                field="clean runs of the object, the non-clean outcomes beside (TL's cells)")
    # 8. the boundaries with their intervals, per family, agent and curve, printed as the figure subtitles print them
    #    (scl_core.figures.profiles.boundary_phrase over the one curve renderer's spec: nothing re-rounded here)
    for fam in fams:
        docs = docs_of(inp, fam, extension=True)
        x_range = family_x_range(docs)
        for agent, doc in docs.items():
            unit = (doc.get("axis") or {}).get("unit") or ""
            for q, quantity in (("p_go", "commitment"), ("p_clean", "competence"), ("passage", "passage")):
                if q not in doc["curves"] or x_range is None:
                    continue
                spec = fc.render_spec(doc["curves"][q], x_range)
                phrase = fp.boundary_phrase(spec, unit, quantity if quantity != "passage" else "competence")
                clean = phrase.replace("$^{b}$", "")
                tokens = [clean]
                if spec.get("boundary") is not None:
                    tokens.append(clean.split(" ")[0])
                    if "[" in clean:
                        tokens.append(clean[clean.index("["):clean.index("]") + 1])
                    if unit == "m":
                        tokens += two_decimal_forms(clean)      # the paper prints metre boundaries with two decimals
                tokens = list(dict.fromkeys(tokens))
                put(f"{fam}.{agent}.{q}.boundary", {"phrase": phrase, "boundary": spec.get("boundary"), "kind": spec.get("kind"),
                                                     "reason": spec.get("reason"), "interval": spec.get("band_ci") or spec.get("interval")},
                    tokens, kind="documents", pool=pool_of(inp, fam), field=f"curves.{q} through fc.render_spec and fp.boundary_phrase (the subtitle's string)")
            go, clean = numbers.get(f"{fam}.{agent}.p_go.boundary"), numbers.get(f"{fam}.{agent}.p_clean.boundary")
            if go and clean and go["value"]["boundary"] is not None and clean["value"]["boundary"] is not None:
                gap = clean["value"]["boundary"] - go["value"]["boundary"]      # F97: "boundaries 2 cm apart" is a number of its own
                prints = ([f"{gap * 100:.0f} cm", f"{gap * 100:.0f}", f"{gap:.2f} m", f"{gap:.2f}"] if unit == "m"
                          else [f"{gap:.2f} s", f"{gap:.2f}"])
                put(f"{fam}.{agent}.boundary_gap", {"gap": round(gap, 4), "unit": unit}, prints, kind="documents", pool=pool_of(inp, fam),
                    field="competence boundary minus commitment boundary, both located")
    # 9. per family, agent and side: the outcome counts of that side (k of the side's runs), and the PlanT checkpoints summed
    for fam in fams:
        agents = sorted({r["model"] for r in rows_of(inp, fam, extension=True)})
        group_k, group_n = Counter(), Counter()
        for agent in agents:
            rs = rows_of(inp, fam, agent, extension=True)
            for side in sorted({r["assignment"] for r in rs}):
                sr = [r for r in rs if r["assignment"] == side]
                oc = Counter(r["outcome"] for r in sr)
                for outcome, k in sorted(oc.items()):
                    put(f"{fam}.{agent}.{side}.{outcome}", {"k": k, "n": len(sr)}, kn(k, len(sr)) + [str(k)], kind="rows",
                        pool=pool_of(inp, fam), field=f"rows with assignment {side}: outcome == {outcome}, of the side's runs")
                    if agent.startswith("plant2"):
                        group_k[(side, outcome)] += k
                put(f"{fam}.{agent}.{side}.runs", len(sr), [str(len(sr))], kind="rows", pool=pool_of(inp, fam), field=f"runs with assignment {side}")
                if agent.startswith("plant2"):
                    group_n[side] += len(sr)
            put(f"{fam}.{agent}.runs", len(rs), [str(len(rs))], kind="rows", pool=pool_of(inp, fam), field="the agent's runs in the family, every assignment")
        for (side, outcome), k in group_k.items():
            put(f"{fam}.plant2.checkpoints.{side}.{outcome}", {"k": k, "n": group_n[side]}, kn(k, group_n[side]), kind="rows", pool=pool_of(inp, fam),
                field=f"PlanT 2.0's three checkpoints summed: outcome == {outcome} on the {side} side, of their {side} runs")
    # 10. the score split by side (Table 1 of the paper): mean driving score over all runs, the passable and the blocked widths
    for fam in fams:
        for agent in sorted({r["model"] for r in rows_of(inp, fam, extension=True)}):
            rs = [r for r in rows_of(inp, fam, agent, extension=True) if run_score(r) is not None and r["assignment"] != "defect"]
            pas = [r for r in rs if r["assignment"] == "passable"]; blk = [r for r in rs if r["assignment"] == "blocked"]
            if not blk:
                continue
            m = lambda v: round(sum(run_score(r) for r in v) / len(v), 4) if v else None
            put(f"{fam}.{agent}.score_split", {"all": m(rs), "passable": m(pas), "blocked": m(blk), "n": [len(rs), len(pas), len(blk)]},
                [f"{m(rs):.1f}", f"{m(pas):.1f}", f"{m(blk):.1f}"], kind="rows", pool=pool_of(inp, fam),
                field="mean driving score over all runs, over the passable widths, over the blocked widths (the paper's Table 1)")
    # 11. the tied pair's inversion cell (the case study: the same width and seed, one stops, one continues)
    if "fm_004_threshold_sweep" in fams:
        try:
            f1 = build_f1(inp)
            for agent, c in (f1.get("cells") or {}).items():
                put(f"fm_004_threshold_sweep.{agent}.inversion_cell_score", c, [f"{c['ds']:.1f}"], kind="rows",
                    field="F1 / the case study: the driving score of the agent's run at the inversion cell (same width and seed as its pair)")
        except Exception as exc:    # the pair is a roster choice; a cohort without it has no cell
            numbers["fm_004_threshold_sweep.inversion_cell"] = {"value": None, "prints": [], "source": {"kind": "rows", "note": str(exc)[:160]}}
    # 12. the declared constants the method cites, read from the instruments the documents pin
    for fam in [f for f in FLOW_FAMILIES if f in fams]:
        doc = next(iter(docs_of(inp, fam, extension=True).values()))
        pins = fd.instruments_by_role(doc)
        if "gap_certificate" in pins:
            cert = fd.read_pinned(pins["gap_certificate"], root, fam, "gap_certificate")
            path = pins["gap_certificate"]["path"]
            check_certificate_sample(fam, cert, declared_certificate_campaigns(), path)
            put(f"{fam}.certificate.margin_s", cert["margin_s"], [f"{cert['margin_s']:.1f}", f"{cert['margin_s']}"], kind="instrument", path=path, field="margin_s")
            put(f"{fam}.certificate.peak_accel_mps2", cert["vehicle"]["peak_accel_mps2"], [f"{cert['vehicle']['peak_accel_mps2']:.2f}"], kind="instrument", path=path, field="vehicle.peak_accel_mps2")
            put(f"{fam}.certificate.from_rest_s", cert["floors"]["from_rest_s"], [f"{cert['floors']['from_rest_s']:.2f}"], kind="instrument", path=path, field="floors.from_rest_s (standstill to exit)")
            put(f"{fam}.certificate.rolling_reference_s", cert["floors"]["rolling_reference_s"], [f"{cert['floors']['rolling_reference_s']:.2f}"], kind="instrument", path=path, field="floors.rolling_reference_s")
            for stem, (sid, seeds) in defect_spawn_headways(cert).items():
                vals = sorted({v for v in seeds.values() if v is not None})
                put(f"{fam}.defect_{stem}.spawn_headway_s", {"scenario_id": sid, "per_seed": seeds},
                    list(dict.fromkeys([f"{v:.2f}" for v in vals] + [f"{v:g}" for v in vals])), kind="instrument", path=path,
                    field="seeds[].spawn_headway_s of the cells the certificate bands as defect (the declared register's cells), per seed")
            decls = fd.declarations(doc, root) if (doc.get("pooling") or {}).get("campaigns") else []
            if decls:                                            # F97: the nominal gap that identifies the defect variant
                by_sid = {v.get("scenario_id"): v for v in json.loads((Path(root) / decls[0].manifest_path).read_text()).get("variants", [])}
                for stem, (sid, _seeds) in defect_spawn_headways(cert).items():
                    g = ((by_sid.get(sid) or {}).get("nominal") or {}).get("crossing_time_gap_s")
                    if g is not None:
                        put(f"{fam}.defect_{stem}.nominal_gap_s", g, list(dict.fromkeys([f"{g:g}", f"{g:.2f}", f"{g:.1f}"])), kind="manifest",
                            path=str(decls[0].manifest_path), field="variants[].nominal.crossing_time_gap_s of the defect cell's scenario (the nominal gap identifies the variant, never a measurement)")
    for fam in fams:
        doc = next(iter(docs_of(inp, fam, extension=True).values()))
        defects = [c for c in doc["outcome_composition"] if c["assignment"] == "defect"]
        for c in defects:
            put(f"{fam}.defect_variant_x", c["x"], [fp.fmt(c["x"]), f"{c['x']:.2f}", f"{c['x']:.1f}"], kind="documents", pool=pool_of(inp, fam),
                field="outcome_composition[].assignment == defect (the declared scenario-defect register's cell)")
        applied = (doc.get("defects") or {}).get("applied") or []
        if applied and not defects:
            # the threading chain keeps the registered cell's measured assignment in the composition and takes it out of
            # the fits, the decisions and the tables (defect_rows); its axis value is read from the rows
            xs = sorted({r["x"] for r in rows_of(inp, fam, extension=True) if r["variant"] in {a["variant"] for a in applied}})
            for x in xs:
                put(f"{fam}.defect_variant_x", x, [fp.fmt(x), f"{x:.2f}", f"{x:.1f}"], kind="rows", pool=pool_of(inp, fam),
                    field="the rows of the variants the document's defects.applied names (the declared scenario-defect register)")
    for fam in [f for f in T8_ISAAC_FAMILIES if f in fams]:
        doc = next(iter(docs_of(inp, fam, extension=True).values()))
        try:
            w = warehouse_reference_lines(doc, root)
            put(f"{fam}.robot_contact_width_m", w["contact_width_m"], [f"{w['contact_width_m']:.3f}", f"{w['contact_width_m']:.2f}"],
                kind="instrument", path=w["robot_record"]["path"], field="the robot record's contact width through scl_core.certification_band")
            inst = fd.read_pinned(fd.instruments_by_role(doc)["instrument"], root, fam, "instrument")
            g = inst["certification"]["grid_res_m"]
            put(f"{fam}.grid_res_m", g, [f"{g * 100:.0f} cm", f"{g}"], kind="instrument", path=fd.instruments_by_role(doc)["instrument"]["path"], field="certification.grid_res_m")
        except (KeyError, ValueError):
            pass
    # 12b. the robustness paragraph: every agent's band under the perturbed constants, the claimed positions and the
    # groups' bands (Table S, built when the run names a sensitivity declaration)
    if inp.get("sensitivity"):
        ts = build_ts(inp, load_roster(inp.get("roster")))
        src = {"kind": "table", "table": "TS", "declaration": ts["declaration"]}

        def band_prints(b):
            return [f"{b['low']:.2f} to {b['high']:.2f}", f"({b['low']:.2f} to {b['high']:.2f})", f"{b['low']:.2f}", f"{b['high']:.2f}"]
        for agent in ts["row_ids"]:
            a = ts["agents"][agent]
            if a["band"]:
                put(f"{agent}.decision_accuracy_band", {**a["band"], "baseline": a["baseline"]}, band_prints(a["band"]),
                    field="the lowest and highest mean accuracy over the perturbation settings (agents[].band)", **src)
        rep = ts["report"]
        for t in rep["top"]:
            put(f"sensitivity.position_{t['position']}", {"agent": t["agent"], "holds": t["holds"], "band": t["band"]},
                band_prints(t["band"]) if t["band"] else [], field="report.top[]: the agent holding the position at the baseline, "
                "whether it holds at every perturbation setting, its band", **src)
        if rep["bottom"]["agents"]:
            put("sensitivity.bottom_group", {"agents": rep["bottom"]["agents"], "positions": rep["bottom"]["positions"],
                                             "holds": rep["bottom"]["holds"], "band": rep["bottom"]["band"]},
                band_prints(rep["bottom"]["band"]) if rep["bottom"]["band"] else [],
                field="report.bottom: the agents holding the last positions as a set, whether that holds at every setting, their band", **src)
        if rep["middle"]["agents"]:
            put("sensitivity.middle_group", {"agents": rep["middle"]["agents"], "positions": rep["middle"]["positions"],
                                             "order_changes": rep["middle"]["order_changes"], "band": rep["middle"]["band"]},
                band_prints(rep["middle"]["band"]) if rep["middle"]["band"] else [],
                field="report.middle: every other agent, whether their order changes across the settings, their band", **src)
        put("sensitivity.claims_hold", rep["holds"], ["hold" if rep["holds"] else "do not hold"],
            field="report.holds: every claimed position holds at every perturbation setting", **src)
        for k in ts["knobs"]:
            vs = k["values"]
            put(f"sensitivity.{k['parameter']}.values", {"instrument": k["instrument"], "values": vs, "settings": len(vs)},
                [f"{min(vs):g} to {max(vs):g}", f"{min(vs):.1f} to {max(vs):.1f}", str(len(vs))], field="knobs[].values", **src)
    # 13. per family and agent: the decision accuracy with its interval and the go rate on the passable side (Table 8's cells)
    t8cells = build_t8(inp)["cells"]
    for fam, per in t8cells.items():
        for agent, c in per.items():
            if c["accuracy"] is not None:
                lo, hi = c["interval"]
                put(f"{fam}.{agent}.decision_accuracy", {"value": c["accuracy"], "interval": c["interval"], "case": c["accuracy_case"]},
                    [f"{c['accuracy']:.2f}", f"[{lo:.2f}, {hi:.2f}]"], kind="table", table="T8", field="the cell's accuracy and interval (the document's decision_summary.accuracy)")
            go = c["go_passable"]
            if go["n"]:
                put(f"{fam}.{agent}.go_on_passable", {"k": go["k"], "n": go["n"]}, kn(go["k"], go["n"]), kind="table", table="T8",
                    field="the commitments on the passable side (the document's decomposition.p_go_passable)")
    # 13b. the learned agents' range on the flow families: the lowest and highest per-family accuracy over the roster's
    #      agents that are not the reference (the roster's `reference` flag), when the run names a roster
    if roster is not None:
        rng = learned_flow_range(t8cells, roster)
        if rng is not None:
            put("flow_families.learned.decision_accuracy_range", rng,
                [f"{rng['low']:.2f} to {rng['high']:.2f}", f"{rng['low']:.2f}", f"{rng['high']:.2f}"], kind="table", table="T8",
                field="min and max of the per-family decision accuracy over the flow families and the roster's non-reference agents")
    # 14. the gauntlet's closed corridor: every agent's driving score per seed (the case study's circled pair lives here)
    fam = "fm_004_threshold_sweep"
    if fam in fams:
        rs = rows_of(inp, fam, extension=True)
        closed = min(r["x"] for r in rs)
        per = defaultdict(dict)
        for r in rs:
            if r["x"] == closed and run_score(r) is not None:
                per[r["model"]][str(r["repetition"])] = round(run_score(r), 2)
        for agent, scores in sorted(per.items()):
            put(f"{fam}.{agent}.closed_corridor_scores", {"x": closed, "by_repetition": scores}, [f"{v:.1f}" for v in scores.values()],
                kind="rows", pool=pool_of(inp, fam), field="annex.driving_score of the agent's runs at the narrowest (closed) width, by traffic seed")
    # 15. the cost of running (F94, 2026-10-17): every run's wall-clock seconds (annex.wall_clock_s, read by the scorers
    #     from the run log the raw bundle holds, each backend by its own reading, the stamp naming it), summed into hours
    #     over the paper's cohorts per family and agent, per agent, per family, over the roster's driving agents and over
    #     its warehouse brains. Every run of a cohort counts, defect variants included: the sentence is the cost of
    #     running; a run without a reading is counted beside the sum, never defaulted (principle 14). The totals follow
    #     the roster when the run names one (an agent the roster does not name is not the paper's), every agent otherwise.
    def hours(name, rs, field, **src):
        if not rs:
            return
        measured = [annex(r, "wall_clock_s") for r in rs if annex(r, "wall_clock_s") is not None]
        h = sum(measured) / 3600
        prints = list(dict.fromkeys([f"{h:.1f} hours", f"{h:.0f} hours"]           # with the unit: a bare "2" is any count
                                    + ([f"{round(h, -1):.0f} hours"] if h >= 10 else []))) if measured else []
        put(name, {"hours": round(h, 4), "runs": len(rs), "runs_without_wall_clock": len(rs) - len(measured)}, prints,
            kind="rows", field=field, **src)
    all_rows = [r for fam in fams for r in rows_of(inp, fam, extension=True)]
    wall = "annex.wall_clock_s summed, in hours,"
    for fam in fams:
        for agent in sorted({r["model"] for r in rows_of(inp, fam, extension=True)}):
            hours(f"{fam}.{agent}.wall_hours", rows_of(inp, fam, agent, extension=True),
                  f"{wall} over the agent's runs of the family", pool=pool_of(inp, fam))
    for agent in sorted({r["model"] for r in all_rows}):
        hours(f"{agent}.wall_hours", [r for r in all_rows if r["model"] == agent], f"{wall} over the agent's runs of every family in the input")
    if roster is not None:
        driving, brains, who = roster_models(roster), [a["model"] for a in roster.get("warehouse", [])], "the roster's"
    else:
        driving = sorted({r["model"] for r in all_rows if r["family"] not in T8_ISAAC_FAMILIES})
        brains = sorted({r["model"] for r in all_rows if r["family"] in T8_ISAAC_FAMILIES})
        who = "every"
    for fam in fams:
        agents = brains if fam in T8_ISAAC_FAMILIES else driving
        hours(f"{fam}.wall_hours", [r for r in rows_of(inp, fam, extension=True) if r["model"] in agents],
              f"{wall} over the family's runs of {who} agents", pool=pool_of(inp, fam), agents=agents)
    hours("driving_agents.wall_hours", [r for r in all_rows if r["family"] not in T8_ISAAC_FAMILIES and r["model"] in driving],
          f"{wall} over the driving families and {who} driving agents, the reference included (the paper's cost of running all "
          "driving agents; an agent the roster does not name is not counted)",
          agents=driving, families=[f for f in fams if f not in T8_ISAAC_FAMILIES])
    hours("warehouse.wall_hours", [r for r in all_rows if r["family"] in T8_ISAAC_FAMILIES and r["model"] in brains],
          f"{wall} over the warehouse families and {who} warehouse brains", agents=brains, families=[f for f in fams if f in T8_ISAAC_FAMILIES])
    # 14. F97 (2026-10-19): what a group of families shares, named once for the sentence that speaks of the group; an
    #     entry exists only when every member prints the same forms
    def shared(name, members, field):
        es = [numbers.get(m) for m in members]
        if members and all(es) and len({json.dumps(e["prints"]) for e in es}) == 1:
            put(name, es[0]["value"], es[0]["prints"], kind=es[0]["source"].get("kind", "documents"), members=members, field=field)
    flow = [f for f in FLOW_FAMILIES if f in fams]
    for q in ("margin_s", "peak_accel_mps2", "rolling_reference_s", "from_rest_s"):
        shared(f"flow_families.certificate.{q}", [f"{f}.certificate.{q}" for f in flow], f"certificate {q}, the same in every flow family")
    isaac = [f for f in T8_ISAAC_FAMILIES if f in fams]
    shared("warehouse.robot_contact_width_m", [f"{f}.robot_contact_width_m" for f in isaac], "the robot's contact width, the same in every warehouse family's instrument")
    shared("warehouse.grid_res_m", [f"{f}.grid_res_m" for f in isaac], "the occupancy grid's resolution, the same in every warehouse family's instrument")
    shared("warehouse.axis_range", [f"{f}.axis_range" for f in isaac], "the tested gap range, the same in every warehouse family")
    shared("warehouse.repetitions_per_variant", [f"{f}.repetitions_per_variant" for f in isaac], "policy samples per variant, the same in every warehouse family")
    shared("driving_agents.repetitions_per_variant", [f"{f}.repetitions_per_variant" for f in fams if f not in T8_ISAAC_FAMILIES], "traffic seeds per variant, the same in every driving family")
    # 15. F97: the declared constants of the statistics and of the decision rule, with where they are declared
    put("bootstrap.replicates", bd.BOOTSTRAP_B, ["10,000", f"{bd.BOOTSTRAP_B}", f"{bd.BOOTSTRAP_B:,}"], kind="declared",
        path="scl_core/stats/boundary.py", field="BOOTSTRAP_B; scl_core/stats/decision.py accuracy(B=BOOTSTRAP_B) draws the same number")
    put("bootstrap.level_percent", 95, ["95", "95%"], kind="declared", path="scl_core/stats/decision.py",
        field="the 2.5 and 97.5 percentiles of the replicates (accuracy); scl_core/stats/boundary.py WILSON_Z = 1.96 for a flat curve's level")
    put("decision_accuracy.one_sided_score", 0.5, ["0.50"], kind="declared", path="scl_core/stats/decision.py",
        field="the balanced mean of the two sides: an agent perfect on one side and never right on the other scores one half")
    # 16. 2026-10-20 (D9): the two runs of one byte-identical command behind the variance paragraph are the F5 figure
    #     inputs the store holds (findings/bifurcation); their driving scores are pipeline numbers read from that record,
    #     not quotes. A tree without the record puts nothing, and the checker then refuses the paper's binding by name.
    bif = REPO_ROOT / "artifacts/examples/findings/bifurcation/bifurcation.json"
    if bif.exists():
        rec = json.loads(bif.read_text())
        for branch, b in sorted(rec["branches"].items()):
            put(f"bifurcation.{branch}.driving_score", b["driving_score"], [f"{b['driving_score']:.1f}"], kind="documents",
                path=str(bif.relative_to(REPO_ROOT)), field=f"branches.{branch}.driving_score", experiment=rec["experiment"])
    rows = [[name, json.dumps(e["value"]) if not isinstance(e["value"], (int, float)) else str(e["value"]), "; ".join(e["prints"][:3]), e["source"].get("kind", "")]
            for name, e in numbers.items()]
    return {"figure": "TN", "title": "TN - the paper's numbers (every quantity the text cites that no table prints, by name)",
            "col_labels": ["Name", "Value", "Prints", "Source"], "rows": rows, "row_labels": [r[0] for r in rows],
            "numbers": numbers, "cohorts": inp.get("cohorts"), "rule": TN_RULE}


PT_RULE = ("The paper's tables as LaTeX bodies (2026-10-17): for every row of the paper-tables spec (paper_tables/v0.1, "
           "artifacts/examples/figure_specs/), the cells are resolved from the pipeline's tables the row names (the score "
           "split T7, the per-agent accuracy T9, the numbers document TN, the ladder TL) for the agents the row lists under "
           "the printed labels (the roster's, overridden per table), and scl_core.figures.latex_tables writes the tabular "
           "body under the row's rules (order, bold with its exclusions, shading with outcome codes, the collapse row, the "
           "footnote marker, the declared dash for a missing value). An agent the row lists that the source holds no row "
           "for refuses; a value the source lacks for an agent it holds prints the dash. The spec is pinned by path and "
           "sha256; the bodies are written beside the figure data (<out>/tables/) for the paper to input.")
PT_PRESENCE = {     # how each source table says an agent (or an object) is in it at all, before a value is looked up
    "T7": lambda t, agent, names: agent in (t["cells"].get(names.get("family") or "", {})),
    "T9": lambda t, agent, names: agent in t["agents"],
    "TS": lambda t, agent, names: agent in t["agents"],
    "TN": lambda t, agent, names: any(k.startswith(agent + ".") for k in t),
    "TL": lambda t, agent, names: any(agent in per for per in t["cells"].values()),
}


def load_paper_tables(inp) -> tuple:
    """(spec, pin) of the paper-tables spec the run names (--paper-tables), checked by the writer's contract."""
    path = inp.get("paper_tables")
    if path is None:
        raise ValueError("PT takes a paper-tables spec (--paper-tables artifacts/examples/figure_specs/paper_tables.json)")
    try:
        spec, sha = lt.load_spec(path)
    except ValueError as exc:
        raise ValueError(f"{Path(path).name}: {exc}") from exc
    try:
        rel = portable_path(Path(path).resolve(), inp["root"])
    except ValueError:
        rel = str(Path(path).resolve())      # a spec outside the tree (a test's): pinned by its absolute path
    return spec, {"path": rel, "sha256": sha}


def roster_labels(roster) -> dict:
    """{model: printed label} over the roster's agents and warehouse lists; a checkpoint group's members share its label."""
    out = {}
    for a in [*roster.get("agents", []), *roster.get("warehouse", [])]:
        for m in a.get("checkpoints") or [a["model"]]:
            out[m] = a["label"]
    return out


def pt_row_with_roster_bold(row: dict, roster) -> dict:
    """The spec row with its bold exclusion derived from the roster's reference agents. A row that lists its own
    `exclude` must agree with the roster, else PT refuses: who the reference is has one home, the roster flag."""
    bold = row.get("bold")
    if not bold:
        return row
    ref = reference_agents(roster)
    listed = bold.get("exclude")
    if listed is not None and sorted(listed) != sorted(ref):
        raise ValueError(f"PT {row['table']}: bold.exclude {sorted(listed)} disagrees with the roster's reference agents "
                         f"{sorted(ref)}; the roster's `reference` flag is the one home of who the reference is (drop the list)")
    return {**row, "bold": {**bold, "exclude": ref}}


def object_label(obj: str, object_labels) -> str:
    """An object's printed name: the spec's override, else from the id (underscores to spaces, the first letter up)."""
    if isinstance(object_labels, dict) and obj in object_labels:
        return object_labels[obj]
    if not (isinstance(object_labels, dict) and object_labels.get("from_id", True)):
        raise ValueError(f"object {obj!r} has no printed label and object_labels.from_id is off")
    text = obj.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _lookup(tables, ref, present, who, **names):
    """A cell's source object: None when the source holds the agent (object) but not this value, a refusal when it
    holds no row for it at all."""
    tid, _ = lt.parse_source(ref)
    if tid not in tables:
        raise ValueError(f"source {ref!r}: table {tid!r} is not among the built tables {sorted(tables)}")
    if not present:
        raise ValueError(f"{who} has no row in {tid} (source {ref!r})")
    try:
        return lt.resolve(tables, ref, **names)
    except ValueError:
        return None


def _agent_grid(row, tables, labels):
    names = {"family": row.get("family")}
    columns = row["columns"]
    blocks = []
    for b in row["blocks"]:
        rows_ = []
        for agent in b["agents"]:
            label = (row.get("labels") or {}).get(agent) or labels.get(agent)
            if label is None:
                raise ValueError(f"PT {row['table']}: agent {agent!r} has no printed label (the roster's agents and warehouse, the row's labels)")
            cells = []
            for c in columns[1:]:
                tid, _ = lt.parse_source(c["source"])
                present = tid in tables and PT_PRESENCE.get(tid, lambda t, a, n: True)(tables[tid], agent, names)
                obj = _lookup(tables, c["source"], present, f"PT {row['table']}: agent {agent!r}", agent=agent, **names)
                cell = lt.cell_of(obj, c["format"])
                if cell is not None and c.get("interval"):
                    cell["interval"] = _lookup(tables, c["interval"], present, f"PT {row['table']}: agent {agent!r}", agent=agent, **names)
                cells.append(cell)
            fv = None
            if row.get("footnote") is not None:
                ref = row["footnote"]["when"]
                tid, _ = lt.parse_source(ref)
                present = tid in tables and PT_PRESENCE.get(tid, lambda t, a, n: True)(tables[tid], agent, names)
                fv = _lookup(tables, ref, present, f"PT {row['table']}: agent {agent!r}", agent=agent, **names)
            rows_.append({"id": agent, "label": label, "cells": cells, "footnote_value": fv})
        blocks.append({"label": b.get("label"), "rows": rows_})
    return {"columns": columns, "blocks": blocks, "column_spec": row["column_spec"], "tabcolsep_pt": row["tabcolsep_pt"]}


def _object_grid(row, tables, labels):
    template = row["columns"][1]
    tid, _ = lt.parse_source(template["source"])
    if tid not in tables:
        raise ValueError(f"PT {row['table']}: source table {tid!r} is not among the built tables {sorted(tables)}")
    t = tables[tid]
    columns = [row["columns"][0]]
    for agent in row["agents"]:
        if not PT_PRESENCE.get(tid, lambda t_, a, n: True)(t, agent, {}):
            raise ValueError(f"PT {row['table']}: agent {agent!r} has no row in {tid}")
        label = (row.get("labels") or {}).get(agent) or labels.get(agent)
        if label is None:
            raise ValueError(f"PT {row['table']}: agent {agent!r} has no printed label (the roster's agents, the row's labels)")
        columns.append({**{k: v for k, v in template.items() if k != "header"}, "header": label, "agent": agent})
    rows_ = []
    for obj in t["objects"]:
        cells = []
        for c in columns[1:]:
            present = c["agent"] in t["cells"].get(obj, {})
            obj_ = lt.resolve(tables, c["source"], object=obj, agent=c["agent"]) if present else None
            cells.append(lt.cell_of(obj_, c["format"]))
        rows_.append({"id": obj, "label": object_label(obj, row.get("object_labels")), "cells": cells, "footnote_value": None})
    return {"columns": columns, "blocks": [{"label": None, "rows": rows_}], "column_spec": row["column_spec"],
            "tabcolsep_pt": row["tabcolsep_pt"]}


def build_pt(inp, roster):
    """The paper's tables as LaTeX bodies (PT_RULE): the spec's rows resolved against the built source tables and
    written by the one writer; the bodies come back in the document (main writes them to <out>/tables/)."""
    spec, pin = load_paper_tables(inp)
    needed = []
    for row in spec["tables"]:
        for tid in row["sources"]:
            if tid not in needed:
                needed.append(tid)
    tables, sources = {}, {}
    for tid in needed:
        if tid not in TABLE_BUILDERS:
            raise ValueError(f"PT: source table {tid!r} is not among the tables {sorted(TABLE_BUILDERS)}")
        d = build_ts(inp, roster) if tid == "TS" else TABLE_BUILDERS[tid](inp)
        if d.get("status"):
            raise ValueError(f"PT: source table {tid} has no data in this input: {d['status']}")
        tables[tid] = d["numbers"] if tid == "TN" else d
        sources[tid] = {"title": d.get("title"), "rule": d.get("rule")}
    labels = roster_labels(roster)
    out = OrderedDict()
    for row in spec["tables"]:
        row = pt_row_with_roster_bold(row, roster)
        grid = _object_grid(row, tables, labels) if row.get("rows_are", "agents") == "objects" else _agent_grid(row, tables, labels)
        try:
            text = lt.body(grid, row, spec["missing"])
        except ValueError as exc:
            raise ValueError(f"PT {row['table']}: {exc}") from exc
        out[row["table"]] = {"body_file": f"{spec['body_root']}/{row['body']}", "body_name": row["body"], "body": text,
                             "grid": grid, "sources": row["sources"]}
    return {"figure": "PT", "title": "The paper's tables as LaTeX bodies (one per spec row)", "spec": pin,
            "body_root": spec["body_root"], "tables": out, "source_tables": sources, "roster": roster.get("note"), "rule": PT_RULE}


TABLE_BUILDERS = {"T1": build_t1, "TN": build_tn, "T2": build_t2, "T3": build_t3,
                  "T5": build_t5, "T6": build_t6, "T7": build_t7, "T8": build_t8, "T9": build_t9, "TS": build_ts,
                  "TL": build_tl}


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
    elif spec["kind"] != "none":
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
    for i, (model, spec) in enumerate(d["p_go"].items()):
        _draw_curvespec(bot, spec, STYLE["colors"][model], d["x_range"],
                        STYLE["labels"][model], dodge=0.018 * (i + 1))
        if spec["kind"] == "flat":
            bot.annotate(f"{STYLE['labels'][model]}: no decision "
                         "boundary in tested range",
                         (d["x_range"][0] + 0.05, 0.88),
                         fontsize=7, color=STYLE["colors"][model])
    _variants_rug(bot, d["p_go"].values())
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
    passage gap must read as THAT agent's property; in a shared
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
        gx, gy = _steps(d["p_go"][model])
        cx, cy = _steps(d["passage"][model])
        ax.step(gx, gy, where="mid", color=color, lw=2.2, zorder=2)
        # passage: LIGHTER shade of the same hue, dashed, drawn ON
        # TOP - where the curves coincide the pair reads as one striped
        # line (two entities, same path), not a hidden line
        ax.step(cx, cy, where="mid", color=light[model], lw=1.5,
                ls=(0, (4, 3)), zorder=3)
        g_at, c_at = dict(zip(gx, gy)), dict(zip(cx, cy))
        common = sorted(set(gx) & set(cx))
        if common:
            ys_g = [g_at[x] for x in common]
            ys_c = [c_at[x] for x in common]
            # No `where` mask: passage <= commitment by coding (a
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
        for j, curve in enumerate(PAPER_CURVES):
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
                             None if head else STYLE["labels"].get(model, model)) if j == 0 else None
                    _draw_curvespec(ax, curves[curve],
                                    STYLE["colors"].get(model, "#8c564b"),
                                    panel["x_range"], label,
                                    dodge=0.014 * (slots.index(head or model) + 1), lw=0.9 if head else None)
            _draw_refs(ax, panel["refs"])
            ax.set_ylim(-0.05, 1.05)
            if i == 0:
                # column headers once: the paper's two curves,
                # introduced in F1 and reused here without re-teaching
                ax.set_title(CURVE_TITLES[curve], fontsize=9)
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
            ax.set_xlabel(axis_label(panel["axis_metric"], panel.get("unit")), fontsize=6.5)
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
            comp = cp.get("passage") or {}
            b = comp.get("boundary")
            if b is not None:
                band = [v for v in (comp.get("band_ci") or comp.get("band") or []) if v is not None]
                if len(band) == 2:
                    ax.plot(sorted(band), [y, y], "-", color=color, lw=3, alpha=0.35, solid_capstyle="butt")
                ax.plot([b], [y], "D", color=color, ms=6, zorder=4)
            else:
                ax.text(0.5, y, "no boundary", transform=ax.get_yaxis_transform(), fontsize=6, color=color,
                        va="center", ha="center")
            for s in (cp.get("per_repetition") or {}).get("passage", []) or []:
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
        ax.set_xlabel(axis_label(panel["axis_metric"], panel.get("unit")), fontsize=6.5)
        ax.tick_params(labelsize=6.5)
        sp, u = panel["spread"], panel["unit"]
        parts = []
        if sp["across_checkpoints"] is not None:
            parts.append(f"Δ checkpoints {sp['across_checkpoints']:.3f} {u}")
        if sp["across_repetitions_all_checkpoints"] is not None:
            parts.append(f"Δ seeds {sp['across_repetitions_all_checkpoints']:.3f} {u}")
        ax.text(0.03, 0.97, "\n".join(parts), transform=ax.transAxes, fontsize=5.8, va="top")
    fig.suptitle("Passage boundary per PlanT 2.0 checkpoint (diamond, interval) and per traffic seed (dots)", fontsize=8)
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
        for j, curve in enumerate(PAPER_CURVES):
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
                ax.set_title(CURVE_TITLES[curve], fontsize=9)
            if j == 0:
                ax.set_ylabel(row.get("label") or family_label(row["family"]), fontsize=8)
                ax.legend(fontsize=6)
                pad = 0.03 * (row["x_range"][1] - row["x_range"][0])
            ax.set_xlim(row["x_range"][0] - pad, row["x_range"][1] + pad)
            ax.set_xlabel(axis_label(row["axis_metric"], row.get("unit")), fontsize=6.5)
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
        bx.set_xlabel(axis_label(row["axis_metric"], row.get("unit")), fontsize=6.5)
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
        ax.set_xlabel(str(panel["axis_metric"]).replace("_", " ") + " (m)")
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
    if None not in band:
        ax.axvspan(min(band), max(band), color=STYLE["colors"][d["model"]], alpha=0.15)
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
             **{name: render_table for name in TABLE_BUILDERS},
             **{name: (lambda d, out: dp.render_profiles(d, out, d["figure"], DESIGN[0])) for name in PROFILE_FAMILIES},
             "CS": lambda d, out: dp.render_case_study(d, out, "CS", DESIGN[0])}
FIGURE_BUILDERS = {"F1": build_f1, "F1D": build_f1d, "F2": build_f2, "F4": build_f4, "F6": build_f6,
                   "F7": build_f7, "F8": build_f8, "F9": build_f9}


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


def build(name, inp, args):
    if name in FIGURE_BUILDERS:
        return FIGURE_BUILDERS[name](inp)
    if name == "F3":
        if not inp["metrics"] and not inp.get("metrics_refusal"):
            return {"figure": "F3", "status": "no route metrics among the inputs"}
        return build_f3(inp)
    if name == "F5":
        if args.extract_53.exists() and args.extract_32.exists():
            return build_f5(args.extract_53, args.extract_32)
        return {"figure": "F5", "status": "bifurcation extracts not found"}
    if name == "TS":
        return build_ts(inp, load_roster(args.roster))
    if name == "PT":
        return build_pt(inp, load_roster(args.roster))
    if name in TABLE_BUILDERS:
        return TABLE_BUILDERS[name](inp)
    if name in PROFILE_FAMILIES:
        return build_profiles_family(inp, name, PROFILE_FAMILIES[name], load_roster(args.roster))
    if name == "CS":
        return build_cs(inp, load_roster(args.roster))
    raise ValueError(f"no figure {name!r}; the figures are "
                     f"{sorted([*FIGURE_BUILDERS, 'F3', 'F5', *TABLE_BUILDERS, *PROFILE_FAMILIES, 'CS', 'PT'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scored", type=Path, default=None, help="one scored folder (a single cohort); or use --pooled")
    ap.add_argument("--stats", type=Path, default=None, help="its stats folder")
    ap.add_argument("--pooled", type=Path, default=None,
                    help="pool_campaigns.py's --out root: the figures read every family from its pooled "
                         "folder there (the documents, the rows and the route metrics), one --spec per family")
    ap.add_argument("--spec", action="append", type=Path, default=None,
                    help="a pooling spec (campaign_pooling/v0.2); repeatable; with --pooled; the flow and "
                         "warehouse families are read from their pooled documents the same way")
    ap.add_argument("--extension-spec", action="append", type=Path, default=None,
                    help="pooling specs of agents that join the atlas (F2), F7 and F8 and the extension columns of "
                         "the tables, but not the roster figures F1/F4/F6/F9 and T1/T2 (2026-09-19: PlanT 2.0's "
                         "campaign, one spec per family)")
    ap.add_argument("--metrics", type=Path, default=None,
                    help="a route metrics folder (metrics_rows.jsonl and its stamp); in pooled form the pools' "
                         "metrics members are read when this is not given")
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
    ap.add_argument("--roster", type=Path, default=None,
                    help="a figure roster spec (figure_roster/v0.1): the agents the profile figures (P-*) draw in "
                         "panel order with their printed names, the case study's (CS) pair, and the agents the "
                         "sensitivity table (TS) sweeps")
    ap.add_argument("--sensitivity", type=Path, default=None,
                    help="a decision-sensitivity declaration (decision_sensitivity/v0.1): the knobs and overrides the "
                         "sensitivity table (TS) sweeps and the bands TN prints for the robustness paragraph")
    ap.add_argument("--paper-tables", type=Path, default=None,
                    help="a paper-tables spec (paper_tables/v0.1): the paper's tables as LaTeX bodies (PT), written to "
                         "<out>/tables/ for the paper to input; takes --roster for the printed labels")
    ap.add_argument("--design", choices=list(dp.DESIGNS), default="hybrid",
                    help="the profile figures' and the case study's rendering: the paper's design as it stands "
                         "(asis), with the estimator's diamond, band and logistic kept (hybrid, the default), or with a diamond per located "
                         "boundary and no boundary lines (marks, the paper's design since 2026-10-22)")
    args = ap.parse_args()
    DESIGN[0] = args.design
    if args.pooled is not None:
        if args.scored or args.stats or not args.spec:
            ap.error("--pooled takes one or more --spec and neither --scored nor --stats")
    elif not (args.scored and args.stats):
        ap.error("give --scored and --stats (one cohort) or --pooled with --spec (the pool folders)")

    try:
        inp = load_inputs(args)
    except ValueError as exc:
        sys.exit(f"REFUSED: {exc}")
    inp["sensitivity"], inp["roster"] = args.sensitivity, args.roster   # the sweep enters TS and TN through the declaration and the roster
    inp["paper_tables"] = args.paper_tables                             # the paper's tables enter PT through the spec
    data_dir = args.out / "figure_data"
    fig_dir = args.out / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    built = {}
    for name in args.figures:
        try:
            built[name] = build(name, inp, args)
        except ValueError as exc:
            sys.exit(f"REFUSED: {name}: {exc}")

    rendered = []
    for name, d in built.items():
        if name != "F5":
            # data lineage (F5 builds from trajectory extracts, not from
            # the documents and rows, so stamping it would overstate)
            d["lineage"] = inp["lineage"]
        (data_dir / f"{name}.json").write_text(
            json.dumps(d, sort_keys=True, indent=1) + "\n")
        if d.get("status"):
            print(f"{name}: SKIPPED render - {d['status']}")
            continue
        if name == "PT":
            tdir = args.out / "tables"
            tdir.mkdir(parents=True, exist_ok=True)
            for t in d["tables"].values():
                (tdir / t["body_name"]).write_text(t["body"])
            print(f"PT: {len(d['tables'])} table bodies -> {tdir}", file=sys.stderr)
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
