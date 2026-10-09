#!/usr/bin/env python3
"""Pool the scored rows of several campaigns of one family and emit pooled findings documents.

A paper figure reads one family over several campaigns (the intrusion sweep:
seed 0 from v02, seeds 1 and 2 from v03). Which campaigns pool is a paper-level
choice, so it is DATA: a pooling spec (campaign_pooling/v0.1) under
artifacts/examples/findings/pooling/. This tool (2026-09-19, TODO item 3):

  1. loads the spec and its declarations (load_pooling here: one family
     manifest, one model set, disjoint repetitions, else refused);
  2. fetches every campaign's INTERPRETATION through the results store
     (scl_core.results_store: the derived zip the records pin, unpacked once,
     verified) and checks its scored tables were produced by the CURRENT
     scorer (a stale interpretation refuses; re-score it);
  3. concatenates the family's scored rows into <out>/<pool_id>/<family>/
     scoring_out/ with a scoring_provenance stamp of the same kind the scorer
     writes (scorer sha, a digest over the inputs' digests, the inputs by
     campaign and interpretation id);
  4. runs boundary_stats.py over it (the one estimator, the same code path as
     any single campaign) into <out>/<pool_id>/<family>/stats/;
  5. emits one document per declared model with findings_carla's own
     builders (the emitter file is a PRODUCER in every derived manifest and
     is not edited for a reader-side feature; this tool gates the pooled
     rows per declaration and writes findings_pooled/v0.1, the v0.2 body
     with a `pooling` block in place of `campaign`) into
     <out>/<pool_id>/findings/fp_<pool_id>_<model>.v0.1.json;
  6. writes <out>/<pool_id>/pooling_record.json (inputs and outputs by hash)
     and, with --publish, copies the documents into the store
     (artifacts/examples/findings/pooled/).

A pool of ONE campaign is the identity: its documents carry the campaign's
curves, points and fits unchanged under the pooled kind (a test holds that).
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
MME = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(MME)]
import boundary_stats as bs  # noqa: E402
import findings_carla as fc  # noqa: E402  (its builders; the file itself is not edited)
from scl_core import scenario_defects  # noqa: E402
from scl_core import family_versions as fv  # noqa: E402  (the refusal names the versions the campaigns bind)
from scl_core import results_store as store  # noqa: E402
from scl_core.campaign_declaration import coverage_gate, load_declaration  # noqa: E402
from scl_core.records import sha256_file as sha  # noqa: E402

RECORD_KIND = "campaign_pooling_record/v0.1"
POOLED_KIND = "findings_pooled/v0.1"      # the v0.2 body over several campaigns of one family: `pooling` in place of `campaign`
STORE_POOLED = "artifacts/examples/findings/pooled"


def load_pooling(path, repo_root):
    """A pooling spec (campaign_pooling/v0.1, data under artifacts/examples/findings/pooling/): one
    family, the campaigns whose rows pool. Returns (spec, declarations, record) where record is the
    `pooling` block a pooled document carries. Refused: another kind, a campaign without a canon
    declaration for the family, declarations that pin different family manifests, model sets that
    differ, repetitions that overlap (a run would be counted twice), a spec outside the repository."""
    path, repo_root = Path(path).resolve(), Path(repo_root).resolve()
    spec = json.loads(path.read_text())
    if spec.get("pooling_kind") != "campaign_pooling/v0.1":
        raise ValueError(f"{path.name}: pooling_kind {spec.get('pooling_kind')!r} is not campaign_pooling/v0.1")
    family, campaigns = spec.get("family_id"), spec.get("campaigns") or []
    if not family or not campaigns or len(set(campaigns)) != len(campaigns):
        raise ValueError(f"{path.name}: a pooling names one family_id and a list of distinct campaigns")
    declarations = []
    for campaign in campaigns:
        decl_path = repo_root / "artifacts/examples/campaigns" / campaign / f"{family}.json"
        if not decl_path.is_file():
            raise ValueError(f"{path.name}: canon holds no declaration for {campaign}/{family}")
        declarations.append(load_declaration(decl_path))
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
    models = {tuple(sorted(d.models)) for d in declarations}
    if len(models) != 1:
        raise ValueError(f"{path.name}: the campaigns declare different model sets for {family}: "
                         f"{sorted(models)}; a pooled curve needs every campaign's rows for every model")
    seen = {}
    for d in declarations:
        for rep in d.repetitions:
            if rep in seen:
                raise ValueError(f"{path.name}: repetition {rep} of {family} is declared by both {seen[rep]} and "
                                 f"{d.campaign_id}; pooled repetitions must be disjoint")
            seen[rep] = d.campaign_id
    try:
        rel = path.relative_to(repo_root).as_posix()
    except ValueError:
        raise ValueError(f"a pooling spec must live in the repository: {path}") from None
    record = {"pool_id": spec["pool_id"], "spec": {"path": rel, "sha256": sha(path)},
              "campaigns": [{"id": d.campaign_id, "declaration": d.record(repo_root)} for d in declarations]}
    return spec, declarations, record


def family_manifest_doc(inp, archive) -> dict:
    """The family manifest the interpretation was scored against, as a document (principle 13):
    from the raw bundle in the archive, the member and hash the interpretation's provenance
    recorded (raw_members, derived_bundle/v0.3); an interpretation of the previous kind recorded
    no raw members, so its family is read from canon at the declaration's pinned hash, the way
    its writer read it (canon input)."""
    declaration = inp["declaration"]
    prov_path = Path(inp["folder"]) / "bundle_provenance.json"
    # a folder without a provenance document (a scoring driver's output, a synthetic interpretation) has
    # recorded no raw members either: its family is read the way its writer read it, canon at the pin
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
        rows += [r for r in store.read_jsonl(inp["metrics_dir"] / "metrics_rows.jsonl") if r["family"] == family]
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


def emit_pooled(scored_dir, stats_dir, family, model, declarations, pooling, out, manifest_doc, defects=None):
    """One pooled document, with the emitter's own gates and builders: the fits bound to this pooled
    cohort (findings_carla.bind_provenance), every campaign's rows gated against its own grid, a row
    no declaration covers refused, the fit gate per curve; then build_findings_carla's body under
    the pooled kind with `pooling` in place of `campaign`."""
    axes = bs.family_axes()
    metric, kind = axes[family]
    is_cat = kind == "categorical"
    declaration = declarations[0]
    levels = fc._levels_doc(declaration, manifest_doc) if is_cat else None
    scored_stamp, stats_prov = fc.bind_provenance(Path(scored_dir), Path(stats_dir))
    # the register is an instrument: the pooled fits and this document read the same one or none (F66)
    pinned = (stats_prov.get("scenario_defects") or {}).get("sha256")
    given = defects[1]["sha256"] if defects else None
    if pinned != given:
        raise ValueError(f"the pooled stats under {stats_dir} were fitted with scenario-defect register "
                         f"{pinned or 'none'}, the emitter was given {given or 'none'}")
    scored_p, stats_p = Path(scored_dir) / "scored_rows.jsonl", Path(stats_dir) / "stats_rows.jsonl"
    family_rows = [r for r in store.read_jsonl(scored_p) if r["family"] == family]
    covered = set()
    for d in declarations:
        mine = [r for r in family_rows if r["seed"] in set(d.repetitions)]
        coverage_gate(d, ((r["model"], r["seed"], r["scenario_id"]) for r in mine))
        covered.update(id(r) for r in mine)
    stray = [r for r in family_rows if id(r) not in covered]
    if stray:
        raise ValueError(f"{len(stray)} scored row(s) of {family} belong to no declared repetition "
                         f"(e.g. model {stray[0]['model']} seed {stray[0]['seed']})")
    scored_rows = [r for r in family_rows if r["model"] == model]
    if not scored_rows:
        raise ValueError(f"no scored rows for {family}/{model} under {scored_p}")
    stats_rows = store.read_jsonl(stats_p)
    if levels is None:
        missing = [cid for cid, curve_name, _, _ in fc.CURVES
                   if fc._fit_from_stats(stats_rows, family, model, curve_name) is None]
        if missing:
            raise ValueError(f"no {'/'.join(missing)} fit for {family}/{model} in {stats_p}")
    else:
        for cid, curve_name, coder, _ in fc.CURVES:
            ladder = fc._ladder_from_stats(stats_rows, family, model, curve_name)
            kept = [r for r in scored_rows if scenario_defects.entry_for(
                defects[0] if defects else None, family, r["scenario_id"], r["seed"]) is None]
            mine = {pt["level"]: (pt["n"], pt["k"]) for pt in fc._points_categorical(kept, coder, levels)}
            if ladder is None or any(ladder.get(level) != mine[level] for level in levels if mine[level][0]):
                raise ValueError(f"the {curve_name} ladder in {stats_p} does not hold this cohort's per-level "
                                 f"counts for {family}/{model}")
    provenance = {"emitter_sha256": sha(MME / "findings_carla.py"),
                  "estimator_sha256": sha(ROOT / "scl_core/stats/boundary.py"),
                  "scored_sha256": sha(scored_p), "stats_sha256": sha(stats_p),
                  "scored_provenance": scored_stamp, "stats_provenance": stats_prov,
                  "pooling_tool_sha256": sha(Path(__file__).resolve()),
                  "scenario_defects": defects[1] if defects else None}
    doc = fc.build_findings_carla(scored_rows, stats_rows, family, model, metric, provenance,
                                  {"id": None, "declaration": None}, levels, defects)
    body = {"findings_kind": POOLED_KIND, "family_id": family, "pooling": dict(pooling)}
    body.update({k: v for k, v in doc.items() if k not in ("findings_kind", "family_id", "campaign")})
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=1) + "\n")
    return body


def scorer_sha256() -> str:
    return sha(MME / "score_runs.py")


def pooled_inputs(spec_path, archive=None, cache=None) -> tuple:
    """(spec, record, inputs): one input per campaign with its interpretation folder, scored dir,
    stamp and interpretation id; refused when a scored table was not produced by the current scorer."""
    spec, declarations, record = load_pooling(spec_path, ROOT)
    family, current = spec["family_id"], scorer_sha256()
    inputs = []
    for declaration in declarations:
        folder = store.interpretation(ROOT, declaration.campaign_id, archive, cache)
        scored = store.scored_dir(folder, family)
        stamp_path = scored / "scoring_provenance.json"
        if not stamp_path.is_file():
            raise ValueError(f"{declaration.campaign_id}/{family}: the interpretation carries no scoring_provenance.json")
        stamp = json.loads(stamp_path.read_text())
        if stamp.get("scorer_sha256") != current:
            raise ValueError(f"{declaration.campaign_id}/{family}: its interpretation was scored by "
                             f"{stamp.get('scorer_sha256', '?')[:12]}, the current scorer is {current[:12]}; "
                             "re-score the campaign (score, freeze-derived, publish) before pooling")
        pinned = store.interpretation_id(ROOT, declaration.campaign_id)
        inputs.append({"campaign": declaration.campaign_id, "declaration": declaration, "folder": folder,
                       "scored_dir": scored, "stamp": stamp, "metrics_dir": scored.parent / "metrics",
                       "derived_content_id_sha256": pinned["content_id_sha256"],
                       "scored_sha256": sha(scored / "scored_rows.jsonl")})
    return spec, record, inputs


def pool(spec_path, out, archive=None, cache=None, publish=False, defects_path=None) -> dict:
    spec, record, inputs = pooled_inputs(spec_path, archive, cache)
    defects = None
    if defects_path is not None:
        doc_d, sha_d = scenario_defects.load_register(defects_path)
        defects = (doc_d, scenario_defects.pin(defects_path, sha_d, ROOT))
    family, pool_id = spec["family_id"], spec["pool_id"]
    out = Path(out) / pool_id
    scored_dir, stats_dir, findings_dir = out / family / "scoring_out", out / family / "stats", out / "findings"
    scored_dir.mkdir(parents=True, exist_ok=True)
    manifest_doc = family_manifest_doc(inputs[0], archive)   # one certified geometry pools (load_pooling)
    rows = []
    for inp in inputs:
        rows += [r for r in store.read_jsonl(inp["scored_dir"] / "scored_rows.jsonl") if r["family"] == family]
    (scored_dir / "scored_rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    pool_metrics(inputs, family, out / family / "metrics", pool_id, record)
    lines = sorted(f"{i['campaign']} {i['stamp']['input_files_sha256']}" for i in inputs)
    stamp = {
        "provenance_kind": "scoring_provenance/v0.1",
        "scorer_file": "score_runs.py",
        "scorer_sha256": scorer_sha256(),
        "outcome_vocabulary": inputs[0]["stamp"].get("outcome_vocabulary"),
        "input_files_sha256": hashlib.sha256("\n".join(lines).encode()).hexdigest(),
        "n_rows": len(rows),
        "pooling": {"pool_id": pool_id, "spec": record["spec"],
                    "inputs": [{"campaign": i["campaign"], "derived_content_id_sha256": i["derived_content_id_sha256"],
                                "scored_sha256": i["scored_sha256"], "input_files_sha256": i["stamp"]["input_files_sha256"],
                                "n_rows": i["stamp"].get("n_rows")} for i in inputs]},
        "note": ("content identity only, no timestamps; a POOLED cohort: the rows of the listed interpretations "
                 "concatenated, input_files_sha256 a digest over their input digests by campaign; consumers "
                 "refuse on scorer_sha256 mismatch"),
    }
    (scored_dir / "scoring_provenance.json").write_text(json.dumps(stamp, indent=1) + "\n")
    subprocess.run([sys.executable, str(MME / "boundary_stats.py"), str(scored_dir), "--out", str(stats_dir),
                    "--quiet", *(["--defects", str(defects_path)] if defects_path is not None else [])], check=True)
    documents = {}
    declarations = [i["declaration"] for i in inputs]
    for model in declarations[0].models:
        name = f"fp_{pool_id}_{model}.v0.1.json"
        path = findings_dir / name
        emit_pooled(scored_dir, stats_dir, family, model, declarations, record, path, manifest_doc, defects)
        documents[name] = sha(path)
    pooling_record = {"record_kind": RECORD_KIND, "generator_sha256": sha(Path(__file__)),
                      "scorer_sha256": scorer_sha256(), "estimator_sha256": sha(ROOT / "scl_core/stats/boundary.py"),
                      "stats_tool_sha256": sha(MME / "boundary_stats.py"), "emitter_sha256": sha(MME / "findings_carla.py"),
                      "pooling": record, "inputs": stamp["pooling"]["inputs"], "n_rows": len(rows),
                      "scenario_defects": defects[1] if defects else None,
                      "scored_sha256": sha(scored_dir / "scored_rows.jsonl"), "stats_sha256": sha(stats_dir / "stats_rows.jsonl"),
                      "documents": documents}
    (out / "pooling_record.json").write_text(json.dumps(pooling_record, indent=1) + "\n")
    if publish:
        dest = ROOT / STORE_POOLED
        dest.mkdir(parents=True, exist_ok=True)
        for name in documents:
            shutil.copyfile(findings_dir / name, dest / name)
        shutil.copyfile(out / "pooling_record.json", dest / f"{pool_id}.pooling_record.json")
    print(f"{pool_id}: {family} over {[i['campaign'] for i in inputs]}: {len(rows)} rows, "
          f"{len(documents)} documents -> {out}" + (f"; published to {STORE_POOLED}" if publish else ""), flush=True)
    return pooling_record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", action="append", required=True, type=Path,
                    help="a pooling spec (campaign_pooling/v0.1); repeatable")
    ap.add_argument("--out", required=True, type=Path, help="root for <pool_id>/ folders (a scratch place)")
    ap.add_argument("--archive", type=Path, default=None,
                    help="the derived zips (default SCL_CAMPAIGN_BUNDLES or ~/scl/campaigns)")
    ap.add_argument("--cache", type=Path, default=None,
                    help="where interpretations unpack (default <archive>/interpretations, beside the zips; F72)")
    ap.add_argument("--publish", action="store_true",
                    help=f"copy the pooled documents and the pooling record into {STORE_POOLED}")
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
