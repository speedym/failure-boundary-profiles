"""The one reader of the pool tool's output for the figure tools (F82, register G6), and the lineage
block every figure_data JSON embeds.

A pool folder is what adapters/fail2drive/scripts/multi_model_eval/pool_campaigns.py writes for one
pooling spec: `<root>/<pool_id>/pooling_record.json` (campaign_pooling_record/v0.3),
`<root>/<pool_id>/findings/<document>` (findings_summary/v0.3, one per declared model, named by
scl_core.run_rows.document_name) and `<root>/<pool_id>/<family>/{scoring_out,stats,metrics}/` (the
one members). A figure reads the documents; the CARLA tool also reads the rows (the driving score per
run, the outcome labels) and the route metrics beside them.

ADMISSION (figure data never consumes a document, rows or fits that outlived their instrument):
- a document is `findings_summary/v0.3`, its emitter, fit and estimator pins hash to the files in this
  tree, and the scoring stamp it embeds (rows_provenance) passes scl_core.run_rows.check_stamp_document
  (the scorer and the vocabulary hash to the tree);
- rows, when read, pass scl_core.run_rows.check_stamp; fits scl_core.stats.fit_rows.check_stats against
  the rows' stamp; both files hash to what the pool record and the documents bind;
- the record is `campaign_pooling_record/v0.3` (its campaigns carry the repetitions the pool took, F88), its
  documents are the ones it lists, each named by the
  one rule and carrying the pool id and family of its spec.
A refusal states the contract and the vocabulary, never a remedy for one case.

The single-cohort form (one scored folder and its stats folder, no pool) is built through the one
emitter (scl_core.stats.emit_findings.build_document): the same document shape, with no pooling block,
so every figure builder reads one shape whatever the form.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from scl_core import family_versions as fv
from scl_core import run_rows as rr
from scl_core import scenario_defects
from scl_core.campaign_declaration import load_declaration
from scl_core.records import sha256_file
from scl_core.stats import emit_findings as ef
from scl_core.stats import fit_rows as fr

from scl_core.pooling import SPEC_KIND, RECORD_KIND, load_spec, cohort_note, with_cohort  # noqa: E402,F401  (the one spec reader, F88)

TOOL_PINS = ("emitter", "fit", "estimator")
SHARED = ("scl_core/figures/curves.py", "scl_core/figures/documents.py")


# --- pins --------------------------------------------------------------------

def check_pin(pin: dict, root, where: str, role: str, remedy: str) -> dict:
    """A {path, sha256} pin must hash to the file at its path under `root` now; a moved or absent
    file refuses with both hashes."""
    target = Path(root) / pin["path"]
    now = sha256_file(target) if target.is_file() else "absent"
    if now != pin["sha256"]:
        raise ValueError(f"{where}: {role} {pin['path']} hashes {now[:12]} in this tree, the input was produced "
                         f"under {pin['sha256'][:12]}; {remedy}")
    return pin


def read_pinned(pin: dict, root, where: str, role: str) -> dict:
    """The JSON document a pin names, read from the tree after the hash check."""
    check_pin(pin, root, where, role, "the pinned instrument moved; re-score under the current chain")
    return json.loads((Path(root) / pin["path"]).read_text())


# --- documents ---------------------------------------------------------------

def check_document(doc: dict, root, where: str) -> dict:
    """The admission check for one findings document (see the module docstring). Returns the document."""
    kind = doc.get("findings_kind")
    if kind != rr.DOCUMENT_KIND:
        raise ValueError(f"{where}: findings_kind {kind!r}; this reader reads {rr.DOCUMENT_KIND}")
    prov = doc.get("provenance") or {}
    for role in TOOL_PINS:
        if not isinstance(prov.get(role), dict):
            raise ValueError(f"{where}: provenance pins no {role} (a {rr.DOCUMENT_KIND} document pins {TOOL_PINS})")
        check_pin(prov[role], root, where, role, "re-emit the document under the current chain")
    rr.check_stamp_document(prov.get("rows_provenance") or {}, root, f"{where}: rows_provenance")
    return doc


def instruments_by_role(doc: dict) -> dict:
    """{role: pin} of the instruments the document's rows were scored against (rows_provenance.instruments)."""
    return {p["role"]: {"path": p["path"], "sha256": p["sha256"]}
            for p in (doc.get("provenance") or {}).get("rows_provenance", {}).get("instruments", [])}


def instrument(doc: dict, role: str, root) -> dict:
    """The instrument of one role the document pins, read from the tree and hash-checked; refused
    when the document's rows pinned no instrument of that role (the roles are the chain's)."""
    pins = instruments_by_role(doc)
    if role not in pins:
        raise ValueError(f"{doc.get('family_id')}/{(doc.get('model') or {}).get('id')}: the rows pinned no "
                         f"instrument of role {role!r} (pinned roles: {sorted(pins)})")
    return read_pinned(pins[role], root, f"{doc.get('family_id')}/{(doc.get('model') or {}).get('id')}", role)


def campaign_ids(doc: dict) -> list:
    return [c["id"] for c in (doc.get("pooling") or {}).get("campaigns") or []]


def declarations(doc: dict, root) -> list:
    """The declarations a pooled document pins (pooling.campaigns[].declaration), each hash-checked
    against the tree; a cohort document (no pooling block) pins none."""
    out = []
    for c in (doc.get("pooling") or {}).get("campaigns") or []:
        pin = c["declaration"]
        check_pin(pin, root, f"{doc['family_id']} ({c['id']})", "declaration",
                  "the declaration changed after the document was emitted")
        out.append(load_declaration(Path(root) / pin["path"]))
    return out


_REGISTRIES = {}


def registry(root) -> dict:
    root = Path(root).resolve()
    if root not in _REGISTRIES:
        _REGISTRIES[root] = fv.load(root)
    return _REGISTRIES[root]


def version_of(doc: dict, root):
    """The family version row of a document's realization (the registry over the manifest hash the
    pinned declarations bind), or None for a cohort document that pins no declaration."""
    if not (doc.get("pooling") or {}).get("campaigns"):
        return None
    return fv.version_for_document(registry(root), doc, root)


def reference_model(docs: dict):
    """The model whose document carries the role `reference` among {model: doc}, or None."""
    return next((m for m, d in docs.items() if (d.get("model") or {}).get("role") == "reference"), None)


# --- pools -------------------------------------------------------------------

def read_record(pool_dir: Path, spec_path) -> dict:
    record_path = Path(pool_dir) / "pooling_record.json"
    if not record_path.is_file():
        raise ValueError(f"{pool_dir}: no pooling_record.json; the figures read pool_campaigns.py's folders "
                         f"(pool_campaigns.py --spec {spec_path} --out <root>)")
    record = json.loads(record_path.read_text())
    if record.get("record_kind") != RECORD_KIND:
        raise ValueError(f"{record_path}: record_kind {record.get('record_kind')!r}; this reader reads {RECORD_KIND}")
    return record


def read_pool(pooled_root, spec_path, root, rows="auto") -> dict:
    """One pool as the figures use it: {pool_id, family, spec, record, documents (OrderedDict model ->
    doc, the record's order), paths (model -> document path), rows, stats, stamp, stats_stamp, metrics
    (the route metrics rows and their stamp, or None), lineage}. `rows`: True reads the rows and fits
    (refused when absent), False reads the documents alone, "auto" reads them when the folder holds
    them (a document-only pool, such as a committed fixture of documents, then carries rows None)."""
    pooled_root, root = Path(pooled_root), Path(root)
    spec = load_spec(spec_path)
    pool_id, family = spec["pool_id"], spec["family_id"]
    pool_dir = pooled_root / pool_id
    record = read_record(pool_dir, spec_path)
    if record["pooling"]["pool_id"] != pool_id:
        raise ValueError(f"{pool_dir}: the record is of pool {record['pooling']['pool_id']!r}, the spec names {pool_id!r}")
    for role in TOOL_PINS:
        check_pin(record[role], root, f"{pool_dir}/pooling_record.json", role, "re-pool under the current chain")
    documents, paths = OrderedDict(), {}
    for name, digest in sorted(record["documents"].items()):
        path = pool_dir / rr.FINDINGS_DIR / name
        if not path.is_file():
            raise ValueError(f"{pool_dir}: the record lists {name}, which is not under {rr.FINDINGS_DIR}/")
        if sha256_file(path) != digest:
            raise ValueError(f"{path}: hashes {sha256_file(path)[:12]}, the record binds {digest[:12]}; the pool is not "
                             f"the one its record describes (re-pool)")
        doc = check_document(json.loads(path.read_text()), root, str(path))
        model = (doc.get("model") or {}).get("id")
        if doc.get("family_id") != family or (doc.get("pooling") or {}).get("pool_id") != pool_id:
            raise ValueError(f"{path}: a document of {doc.get('family_id')}/{(doc.get('pooling') or {}).get('pool_id')}, "
                             f"the spec names {family}/{pool_id}")
        if name != rr.document_name(pool_id, family, model):
            raise ValueError(f"{path}: a document of {family}/{model} is named {rr.document_name(pool_id, family, model)}")
        if doc["provenance"].get("rows_sha256") != record.get("rows_sha256"):
            raise ValueError(f"{path}: emitted from rows {str(doc['provenance'].get('rows_sha256'))[:12]}, the record binds "
                             f"{str(record.get('rows_sha256'))[:12]}; one pool is one rows file")
        documents[model] = doc
        paths[model] = path
    if not documents:
        raise ValueError(f"{pool_dir}: the record lists no document")
    rows_dir, stats_dir = pool_dir / family / "scoring_out", pool_dir / family / "stats"
    have_rows = (rows_dir / Path(rr.ROWS_MEMBER).name).is_file()
    if rows is True and not have_rows:
        raise ValueError(f"{pool_dir}: no {rr.ROWS_MEMBER} for {family} (a document-only pool); this figure reads the rows")
    pool = {"pool_id": pool_id, "family": family, "spec": spec, "spec_path": str(spec_path), "dir": pool_dir,
            "record": record, "documents": documents, "paths": paths, "cohort": cohort_note(record, root),
            "rows": None, "stats": None, "stamp": None, "stats_stamp": None, "metrics": None}
    if rows is not False and have_rows:
        stamp = rr.check_stamp(rows_dir, root)
        stats_stamp = fr.check_stats(stats_dir, root, stamp)
        for member, key in ((rows_dir / Path(rr.ROWS_MEMBER).name, "rows_sha256"),
                            (stats_dir / Path(rr.STATS_MEMBER).name, "stats_sha256")):
            if sha256_file(member) != record.get(key):
                raise ValueError(f"{member}: hashes {sha256_file(member)[:12]}, the record binds {str(record.get(key))[:12]}; "
                                 f"the pool is not the one its record describes (re-pool)")
        pool.update(rows=[r for r in rr.read_rows(rows_dir / Path(rr.ROWS_MEMBER).name) if r["family"] == family],
                    stats=[s for s in fr.read_stats(stats_dir) if s["family"] == family],
                    stamp=stamp, stats_stamp=stats_stamp)
        metrics_dir = pool_dir / family / "metrics"
        if (metrics_dir / "metrics_rows.jsonl").is_file():
            pool["metrics"] = {"rows": [json.loads(l) for l in (metrics_dir / "metrics_rows.jsonl").read_text().splitlines() if l.strip()],
                               "stamp": json.loads((metrics_dir / "metrics_provenance.json").read_text())}
    pool["lineage"] = pool_lineage(pool)
    return pool


def pool_lineage(pool: dict) -> dict:
    """The lineage entry of one pool: every pin the pool's record and stamps carry (the scorer, the
    vocabulary and the instruments by path and hash, the fit, the estimator and the emitter, the rows
    and stats digests, the documents by hash, the campaigns by declaration and interpretation id)."""
    record = pool["record"]
    first = next(iter(pool["documents"].values()))
    stamp = pool["stamp"] or first["provenance"]["rows_provenance"]
    return {"form": "pooled", "family": pool["family"], "spec": record["pooling"]["spec"],
            "campaigns": {c["id"]: {"declaration": c["declaration"], "repetitions": c.get("repetitions"),
                                    "derived_content_id_sha256": next((i.get("derived_content_id_sha256") for i in record.get("inputs", [])
                                                                       if i.get("campaign") == c["id"]), None)}
                          for c in record["pooling"]["campaigns"]},
            "cohort": pool.get("cohort"),
            "scorer": stamp["scorer"], "vocabulary": stamp["vocabulary"], "instruments": stamp.get("instruments", []),
            "input_files_sha256": stamp.get("input_files_sha256"), "n_rows": stamp.get("n_rows"),
            "fit": record["fit"], "estimator": record["estimator"], "emitter": record["emitter"],
            "rows_sha256": record.get("rows_sha256"), "stats_sha256": record.get("stats_sha256"),
            "scenario_defects": record.get("scenario_defects"),
            "documents": dict(sorted(record["documents"].items())),
            "pooling_record_sha256": sha256_file(Path(pool["dir"]) / "pooling_record.json"),
            "rows_read": pool["rows"] is not None}


# --- the single-cohort form --------------------------------------------------

def read_cohort(scored_dir, stats_dir, root) -> dict:
    """One scored folder and its stats folder as documents through the one emitter: {documents
    ({family: {model: doc}}), rows, stats, stamp, stats_stamp, vocabulary, lineage}. Admission is the
    emitter's own (check_stamp, check_stats against the rows' stamp, the register the fits were made
    with read from its pin or none)."""
    scored_dir, stats_dir, root = Path(scored_dir), Path(stats_dir), Path(root)
    stamp = rr.check_stamp(scored_dir, root)
    stats_stamp = fr.check_stats(stats_dir, root, stamp)
    vocabulary, _ = rr.load_vocabulary(root / stamp["vocabulary"]["path"])
    defects = None
    pin = stats_stamp.get("scenario_defects")
    if pin:
        check_pin(pin, root, str(stats_dir), "scenario_defects", "the register the fits were made with moved")
        doc_d, sha_d = scenario_defects.load_register(root / pin["path"])
        defects = (doc_d, scenario_defects.pin(root / pin["path"], sha_d, root))
    provenance = ef.provenance_block(scored_dir, stats_dir, root, defects[1] if defects else None)
    rows = rr.read_rows(scored_dir / Path(rr.ROWS_MEMBER).name)
    stats = fr.read_stats(stats_dir)
    documents = cohort_documents(rows, stats, vocabulary, provenance, defects)
    lineage = {"form": "cohort", "families": sorted(documents),
               "scorer": stamp["scorer"], "vocabulary": stamp["vocabulary"], "instruments": stamp.get("instruments", []),
               "input_files_sha256": stamp.get("input_files_sha256"), "n_rows": stamp.get("n_rows"),
               "fit": stats_stamp["fit"], "estimator": stats_stamp["estimator"], "emitter": provenance["emitter"],
               "rows_sha256": provenance["rows_sha256"], "stats_sha256": provenance["stats_sha256"],
               "scenario_defects": stats_stamp.get("scenario_defects"), "rows_read": True}
    return {"documents": documents, "rows": rows, "stats": stats, "stamp": stamp, "stats_stamp": stats_stamp,
            "vocabulary": vocabulary, "lineage": lineage}


def cohort_documents(rows, stats, vocabulary, provenance=None, defects=None) -> dict:
    """{family: {model: document}} built by the one emitter over rows and fits of one cohort, with no
    pooling block (a cohort pins no declaration: its documents carry no realization)."""
    out = {}
    for family in sorted({r["family"] for r in rows}):
        frows = [r for r in rows if r["family"] == family]
        axis = ef.axis_block(frows)
        out[family] = OrderedDict()
        for model in sorted({r["model"] for r in frows}):
            out[family][model] = ef.build_document([r for r in frows if r["model"] == model], stats, family=family,
                                                   model=model, vocabulary=vocabulary, axis=axis, pooling=None,
                                                   provenance=provenance, defects=defects)
    return out


# --- lineage -----------------------------------------------------------------

def lineage(builder_path, root, pools=(), cohort=None, extra=None) -> dict:
    """The block every figure_data JSON embeds: the builder and this package by path and hash, then one
    entry per input (a pool under its pool id, a cohort under `cohort`), each carrying the stamps' pins
    (scorer, vocabulary, instruments, fit, estimator, emitter) and the digests of what was read."""
    root = Path(root)
    out = {"builder": rr.pin(builder_path, root),
           "shared": {rel: sha256_file(root / rel) for rel in SHARED},
           "inputs": {}}
    for pool in pools:
        out["inputs"][pool["pool_id"]] = pool["lineage"]
    if cohort is not None:
        out["inputs"]["cohort"] = cohort["lineage"]
    out.update(extra or {})
    return out
