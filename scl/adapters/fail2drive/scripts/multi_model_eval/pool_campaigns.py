#!/usr/bin/env python3
"""Pool the run rows of several campaigns of one family and emit the pooled findings documents.

A paper figure reads one family over several campaigns (the intrusion sweep:
seed 0 from v02, seeds 1 and 2 from v03). Which campaigns pool is a paper-level
choice, so it is DATA: a pooling spec (campaign_pooling/v0.2, scl_core.pooling) under
artifacts/examples/findings/pooling/. Since F82 (2026-10-11) every chain is
pooled the same way, over the one row shape (scl_core.run_rows):

  1. loads the spec and its declarations (load_pooling: per campaign the
     REPETITIONS the pool takes, required and declared, F88 2026-10-15; every
     listed repetition declared, disjoint across campaigns, one family
     manifest, one model set, else refused);
  2. fetches every campaign's INTERPRETATION through the results store
     (scl_core.results_store: the derived zip the records pin, unpacked once,
     verified) and checks its rows were produced by the scorer and the
     vocabulary this tree holds (scl_core.run_rows.check_stamp; a stale
     interpretation refuses: re-score it);
  3. concatenates the family's rows of the listed repetitions (the reference
     model's single run on a policy-sample axis belongs to every cohort) into
     <out>/<pool_id>/<family>/scoring_out/
     under a stamp of the same shape the scorers write (the scorer and the
     vocabulary carried from the inputs, a digest over the inputs' digests,
     the inputs by campaign and interpretation id);
  4. fits them with the one fit stage (scl_core.stats.fit_rows, the same
     code path as any single campaign) into <out>/<pool_id>/<family>/stats/;
  5. emits one document per declared model with the one emitter
     (scl_core.stats.emit_findings, findings_summary/v0.3 with the pooling
     block naming every campaign) into <out>/<pool_id>/findings/;
  6. pools the threading route metrics of the listed seeds when every input
     has them;
  7. writes <out>/<pool_id>/pooling_record.json (inputs, repetitions and
     outputs by hash; campaign_pooling_record/v0.3)
     and, with --publish, copies the documents into the store
     (artifacts/examples/findings/pooled/), removing the documents the pool's previous record
     there bound and the new one does not.

A pool of ONE campaign is the identity: its documents carry the campaign's
points and fits unchanged, with the pooling block naming the one campaign.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
MME = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(MME)]
from scl_core import run_rows as rr  # noqa: E402
from scl_core import scenario_defects  # noqa: E402
from scl_core import family_versions as fv  # noqa: E402  (the refusal names the versions the campaigns bind)
from scl_core import pooling as pl  # noqa: E402  (the one reader of the pooling spec, F88)
from scl_core import results_store as store  # noqa: E402
from scl_core.campaign_declaration import load_declaration  # noqa: E402
from scl_core.records import render, sha256_file as sha  # noqa: E402
from scl_core.stats import emit_findings as ef  # noqa: E402
from scl_core.stats import fit_rows as fr  # noqa: E402

RECORD_KIND = pl.RECORD_KIND   # v0.3 (F88): the campaigns carry the repetitions taken; v0.2 was the one pipeline (F82)
STORE_POOLED = "artifacts/examples/findings/pooled"


def load_pooling(path, repo_root):
    """A pooling spec (campaign_pooling/v0.2, scl_core.pooling; data under artifacts/examples/findings/pooling/):
    one family, the campaigns whose rows pool and the repetitions taken from each. Returns (spec,
    declarations, record) where record is the `pooling` block of the pooling record (the campaigns with
    their declaration pins and repetitions; the documents carry it without the repetitions and pin the
    spec). Refused: another kind or shape (scl_core.pooling.load_spec), a campaign without a canon
    declaration for the family, a repetition the declaration does not hold, a repetition taken from two
    campaigns (a run would be counted twice), declarations that pin different family manifests, model
    sets that differ, a spec outside the repository."""
    path, repo_root = Path(path).resolve(), Path(repo_root).resolve()
    spec = pl.load_spec(path)
    family, taken = spec["family_id"], pl.taken(spec)
    declarations = []
    for campaign in taken:
        decl_path = repo_root / "artifacts/examples/campaigns" / campaign / f"{family}.json"
        if not decl_path.is_file():
            raise ValueError(f"{path.name}: canon holds no declaration for {campaign}/{family}")
        declarations.append(load_declaration(decl_path))
    for d in declarations:
        extra = sorted(set(taken[d.campaign_id]) - set(d.repetitions))
        if extra:
            raise ValueError(f"{path.name}: {d.campaign_id} takes repetitions {extra} that its declaration of {family} does "
                             f"not hold (declared: {list(d.repetitions)})")
    manifests = {d.manifest_sha256 for d in declarations}
    if len(manifests) != 1:
        registry = fv.load(repo_root) if (repo_root / fv.REGISTRY_PATH).is_file() else None

        def named(d):
            try:
                return f"{d.campaign_id} {fv.label(fv.version_for(registry, d.backend, family, d.manifest_sha256))} ({d.manifest_sha256[:12]})"
            except (ValueError, TypeError):
                return f"{d.campaign_id} ({d.manifest_sha256[:12]}, unregistered)"
        raise ValueError(f"{path.name}: the campaigns pin different realizations of {family}: "
                         f"{', '.join(named(d) for d in declarations)}; one realization pools (a family id names a "
                         f"lineage, its manifest hash the realization; the registry is {fv.REGISTRY_PATH})")
    seen = {}
    for d in declarations:
        for rep in taken[d.campaign_id]:
            if rep in seen:
                raise ValueError(f"{path.name}: repetition {rep} of {family} is taken from both {seen[rep]} and "
                                 f"{d.campaign_id}; pooled repetitions must be disjoint")
            seen[rep] = d.campaign_id
    models = {tuple(sorted(d.models)) for d in declarations}
    if len(models) != 1:
        raise ValueError(f"{path.name}: the campaigns declare different model sets for {family}: "
                         f"{sorted(models)}; a pooled curve needs every campaign's rows for every model")
    try:
        rel = path.relative_to(repo_root).as_posix()
    except ValueError:
        raise ValueError(f"a pooling spec must live in the repository: {path}") from None
    record = {"pool_id": spec["pool_id"], "spec": {"path": rel, "sha256": sha(path)},
              "campaigns": [{"id": d.campaign_id, "declaration": d.record(repo_root), "repetitions": taken[d.campaign_id]}
                            for d in declarations]}
    return spec, declarations, record


def family_manifest_doc(inp, archive) -> dict:
    """The family manifest the interpretation was scored against, as a document (principle 13):
    from the raw bundle in the archive, the member and hash the interpretation's provenance
    recorded (raw_members, derived_bundle/v0.3); an interpretation of the previous kind recorded
    no raw members, so its family is read from canon at the declaration's pinned hash, the way
    its writer read it (canon input)."""
    declaration = inp["declaration"]
    prov_path = Path(inp["folder"]) / "bundle_provenance.json"
    prov = json.loads(prov_path.read_text()) if prov_path.is_file() else {}
    members = (prov.get("raw_members") or {}).get(declaration.family_id) or {}
    snapshot = [m for m in members if "/family_snapshot/" in m]
    if snapshot:
        data = store.raw_member(archive if archive is not None else store.default_archive(),
                                prov["raw_bundle"]["path"], snapshot[0], members[snapshot[0]])
        return json.loads(data)
    from scl_core.campaign_declaration import check_family_manifest
    return check_family_manifest(declaration, ROOT / declaration.manifest_path)


def pool_metrics(inputs, family, metrics_dir, pool_id, record) -> None:
    """The route metrics rows of every input interpretation, concatenated for the family under one stamp
    (2026-09-22): every input has them or none does; a pool mixing interpretations with and without
    metrics refuses, naming the campaigns (one shape per pool). Nothing written when none has them."""
    have = [inp for inp in inputs if (inp["metrics_dir"] / "metrics_rows.jsonl").is_file()]
    if not have:
        return
    if len(have) != len(inputs):
        missing = sorted(i["campaign"] for i in inputs if i not in have)
        raise ValueError(f"{pool_id}: interpretations without route metrics beside ones with them: {missing}; "
                         "re-score them under the current chain (score_campaign.py runs route_metrics)")
    rows, stamps = [], []
    for inp in have:
        reps = set(inp["repetitions"])
        for r in store.read_jsonl(inp["metrics_dir"] / "metrics_rows.jsonl"):
            if r["family"] != family:
                continue
            if "seed" not in r:
                raise ValueError(f"{pool_id}: a route metrics row of {inp['campaign']}/{family} carries no seed; the cohort's "
                                 f"repetitions {sorted(reps)} cannot be applied to it")
            if r["seed"] in reps:
                rows.append(r)
        stamps.append({"campaign": inp["campaign"], **{k: v for k, v in json.loads((inp["metrics_dir"] / "metrics_provenance.json").read_text()).items()
                                                         if k in ("tool_sha256", "input_files_sha256", "n_rows")}})
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "metrics_rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (metrics_dir / "metrics_provenance.json").write_text(json.dumps({
        "provenance_kind": "metrics_provenance/v0.1", "tool_file": "route_metrics.py",
        "tool_sha256": sha(Path(__file__).resolve().parent / "route_metrics.py"),
        "input_files_sha256": hashlib.sha256("\n".join(sorted(f"{s['campaign']} {s['input_files_sha256']}" for s in stamps)).encode()).hexdigest(),
        "n_rows": len(rows), "pooling": {"pool_id": pool_id, "spec": record["spec"], "inputs": stamps},
        "note": "pooled route metrics: the inputs' rows concatenated under the current tool; the figures read them from here"},
        indent=1) + "\n")


def pooled_inputs(spec_path, archive=None, cache=None) -> tuple:
    """(spec, record, inputs): one input per campaign with its interpretation folder, rows folder, stamp
    and interpretation id; refused when the rows were not produced under this tree's scorer and vocabulary."""
    spec, declarations, record = load_pooling(spec_path, ROOT)
    family = spec["family_id"]
    taken = {c["id"]: c["repetitions"] for c in record["campaigns"]}
    inputs = []
    for declaration in declarations:
        folder = store.interpretation(ROOT, declaration.campaign_id, archive, cache)
        rows_dir = store.rows_dir(folder, family)
        try:
            stamp = rr.check_stamp(rows_dir, ROOT)
        except ValueError as exc:
            raise ValueError(f"{declaration.campaign_id}/{family}: {exc} (score, freeze-derived, store-derived before pooling)")
        pinned = store.interpretation_id(ROOT, declaration.campaign_id)
        inputs.append({"campaign": declaration.campaign_id, "declaration": declaration, "folder": folder,
                       "repetitions": taken[declaration.campaign_id], "rows_dir": rows_dir, "stamp": stamp, "metrics_dir": store.stats_dir(folder, family).parent / "metrics",
                       "derived_content_id_sha256": pinned["content_id_sha256"],
                       "rows_sha256": sha(rows_dir / Path(rr.ROWS_MEMBER).name)})
    return spec, record, inputs


def pool(spec_path, out, archive=None, cache=None, publish=False, defects_path=None) -> dict:
    spec, record, inputs = pooled_inputs(spec_path, archive, cache)
    defects = None
    if defects_path is not None:
        doc_d, sha_d = scenario_defects.load_register(defects_path)
        defects = (doc_d, scenario_defects.pin(defects_path, sha_d, ROOT))
    family, pool_id = spec["family_id"], spec["pool_id"]
    out = Path(out) / pool_id
    rows_dir, stats_dir, findings_dir = out / family / "scoring_out", out / family / "stats", out / "findings"
    manifest_doc = family_manifest_doc(inputs[0], archive)   # one certified geometry pools (load_pooling)
    scorers = {json.dumps(i["stamp"]["scorer"], sort_keys=True) for i in inputs}
    vocabularies = {json.dumps(i["stamp"]["vocabulary"], sort_keys=True) for i in inputs}
    if len(scorers) != 1 or len(vocabularies) != 1:
        raise ValueError(f"{pool_id}: the interpretations were scored by different scorers or vocabularies; one chain pools")
    rows = []
    for inp in inputs:
        reps = set(inp["repetitions"])     # the cohort's repetitions of this campaign; the reference's single run (None) always
        rows += [r for r in rr.read_rows(inp["rows_dir"] / Path(rr.ROWS_MEMBER).name)
                 if r["family"] == family and (r["repetition"] is None or r["repetition"] in reps)]
    vocabulary, _ = rr.load_vocabulary(ROOT / inputs[0]["stamp"]["vocabulary"]["path"])
    rr.write_rows(rows_dir / Path(rr.ROWS_MEMBER).name, rows, vocabulary)
    lines = sorted(f"{i['campaign']} {i['stamp']['input_files_sha256']}" for i in inputs)
    stamp = rr.write_stamp(
        rows_dir, scorer=ROOT / inputs[0]["stamp"]["scorer"]["path"], root=ROOT,
        vocabulary=ROOT / inputs[0]["stamp"]["vocabulary"]["path"],
        instruments=[(p["role"], ROOT / p["path"]) for p in inputs[0]["stamp"]["instruments"]],
        family_manifest_sha256=inputs[0]["declaration"].manifest_sha256,
        input_files_sha256=hashlib.sha256("\n".join(lines).encode()).hexdigest(), n_rows=len(rows),
        extra={"pooling": {"pool_id": pool_id, "spec": record["spec"],
                           "inputs": [{"campaign": i["campaign"], "derived_content_id_sha256": i["derived_content_id_sha256"],
                                       "repetitions": i["repetitions"], "rows_sha256": i["rows_sha256"], "input_files_sha256": i["stamp"]["input_files_sha256"],
                                       "n_rows": i["stamp"].get("n_rows")} for i in inputs]}},
        note=("a POOLED cohort: the rows of the listed interpretations concatenated under the scorer and vocabulary "
              "they share, input_files_sha256 a digest over their input digests by campaign"))
    # instruments carried from the inputs must agree (one chain, one instrument set per family)
    pins = {json.dumps(i["stamp"]["instruments"], sort_keys=True) for i in inputs}
    if len(pins) != 1:
        raise ValueError(f"{pool_id}: the interpretations pin different instruments; one instrument set pools")
    pool_metrics(inputs, family, out / family / "metrics", pool_id, record)
    stats = fr.fit_rows(rows, vocabulary, defects[0] if defects else None)
    fr.write_stats(stats_dir, stats, rows_stamp=stamp, root=ROOT, defects=defects[1] if defects else None)
    content_ids = {i["campaign"]: i["derived_content_id_sha256"] for i in inputs}
    paths = ef.emit(rows_dir, stats_dir, [i["declaration"] for i in inputs], ROOT, findings_dir, manifest=manifest_doc,
                    defects=defects, pool_id=pool_id, spec=record["spec"], content_ids=content_ids,
                    extra_provenance={"pooling_tool": rr.pin(Path(__file__).resolve(), ROOT)},
                    repetitions={i["campaign"]: i["repetitions"] for i in inputs})
    documents = {p.name: sha(p) for p in paths}
    pooling_record = {"record_kind": RECORD_KIND, "generator_sha256": sha(Path(__file__)),
                      **fr.tool_pins(ROOT), "emitter": ef.emitter_pin(ROOT),
                      "pooling": record, "inputs": stamp["extra"]["pooling"]["inputs"], "n_rows": len(rows),
                      "scenario_defects": defects[1] if defects else None,
                      "rows_sha256": sha(rows_dir / Path(rr.ROWS_MEMBER).name),
                      "stats_sha256": sha(stats_dir / Path(rr.STATS_MEMBER).name),
                      "documents": documents}
    (out / "pooling_record.json").write_text(render(pooling_record))
    superseded = publish_pool(out, pool_id, documents, ROOT / STORE_POOLED) if publish else []
    print(f"{pool_id}: {family} over {[i['campaign'] for i in inputs]}: {len(rows)} rows, "
          f"{len(documents)} documents -> {out}"
          + (f"; published to {STORE_POOLED}, {len(superseded)} superseded document(s) removed" if publish else ""),
          flush=True)
    return pooling_record


def publish_pool(out, pool_id, documents, dest) -> list:
    """Copy a pool's documents and its record from the work tree `out` into `dest` (the store's pooled
    directory) and remove the documents the pool's PREVIOUS record there bound and the new one does
    not: the superseded pooling's, which leave the store with it (2026-10-11: under the one document
    kind every pooled document changed its name, and a publish beside the old ones held every pool
    twice). A document the previous record did not bind is not touched. Returns the names removed."""
    out, dest = Path(out), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    record_path = dest / f"{pool_id}.pooling_record.json"
    previous = (json.loads(record_path.read_text()).get("documents") or {}) if record_path.is_file() else {}
    superseded = sorted(name for name in previous if name not in documents and (dest / name).is_file())
    for name in superseded:
        (dest / name).unlink()
    for name in documents:
        shutil.copyfile(out / "findings" / name, dest / name)
    shutil.copyfile(out / "pooling_record.json", record_path)
    return superseded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", action="append", required=True, type=Path,
                    help="a pooling spec (campaign_pooling/v0.2: the campaigns and the repetitions taken); repeatable")
    ap.add_argument("--out", required=True, type=Path, help="root for <pool_id>/ folders (a scratch place)")
    ap.add_argument("--archive", type=Path, default=None,
                    help="the derived zips (default SCL_CAMPAIGN_BUNDLES or ~/scl/campaigns)")
    ap.add_argument("--cache", type=Path, default=None,
                    help="where interpretations unpack (default <archive>/interpretations, beside the zips; F72)")
    ap.add_argument("--publish", action="store_true",
                    help=f"copy the pooled documents and the pooling record into {STORE_POOLED}, removing the "
                         "documents the pool's previous record there bound and the new one does not")
    ap.add_argument("--defects", type=Path, default=None,
                    help="the declared scenario-defect register (scl_core/scenario_defects.py): the pooled fits "
                         "and documents exclude its cells exactly as the per-campaign ones do and pin it")
    args = ap.parse_args()
    for spec in args.spec:
        try:
            pool(spec, args.out, args.archive, args.cache, args.publish, args.defects)
        except (ValueError, OSError, store.StoreError) as exc:
            print(f"POOLING REFUSED: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
