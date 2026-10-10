"""The two records a runner writes into a family-run tree, one shape on both
backends (F18, 2026-09-13; stdlib only, on the admitted list for adapters/isaac).

  campaign_declaration.json  the EXPECTATION, campaign_declaration/v0.2,
                             written at invocation from the runner's
                             arguments, before any cell runs (runner-first);
                             a resume must carry it byte for byte.
  campaign_meta.json         the INSTRUMENT record, campaign_meta/v0.4: the
                             tools, the agents' measured identities ONCE
                             (every run header points at its agent's block
                             by fingerprint), the simulator pin and build,
                             backend-only instrument hashes under
                             `instruments`, and since 2026-09-27 (F52) the
                             HOST and the GPU the campaign ran on, measured
                             at start; a resume under changed instruments,
                             host or GPU refuses (one campaign, one box).

Both readers of these files (each backend's bundler and scorer) come here
too: read_tree_expectation, agent_fingerprints, bundle_fitness. Conflicts
raise RecordConflict; the tools turn that into their exit codes.
"""
from __future__ import annotations

import json
from pathlib import Path

from scl_core.agent_identity import identity_fingerprint
from scl_core.campaign_declaration import (DECLARATION_KIND, REPETITION_AXIS_KINDS,
                                           check_family_manifest, load_declaration)
from scl_core.records import portable_path, sha256_file

META_KIND = "campaign_meta/v0.4"
META_KINDS = ("campaign_meta/v0.3", META_KIND)   # the shapes the readers accept; v0.3 lacks host and gpu


class RecordConflict(ValueError):
    """The tree already holds a different record of this kind."""


def _render(doc) -> str:
    return json.dumps(doc, indent=1) + "\n"


# --- the declaration ------------------------------------------------------------

def build_declaration(campaign_id, backend, manifest_path, repo_root, variants, models,
                      repetitions, repetition_axis, reference_model, note, version=None) -> dict:
    """A campaign_declaration/v0.2 document from a runner's arguments: the
    family by repo-relative path and hash (it must be canon), the variant ids,
    the models, the repetition labels with the axis this backend names, the
    reference agent as declared (never inferred), and since 2026-10-03 the
    family VERSION the launcher resolved from the registry (a claim beside
    the hash, checked when the declaration enters canon; absent when the
    runner was started by hand). ValueError names what is wrong; nothing is
    written here."""
    if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 1):
        raise ValueError(f"family version must be a positive integer, got {version!r}")
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_file():
        raise ValueError(f"family manifest not found: {manifest_path}")
    try:
        rel = portable_path(manifest_path, repo_root)
    except ValueError:
        raise ValueError(f"the family manifest must be canon inside the repository, got {manifest_path}") from None
    manifest = json.loads(manifest_path.read_text())
    family_id = manifest.get("manifest_id") or manifest.get("family_id")
    if not family_id:
        raise ValueError(f"{manifest_path.name} has neither manifest_id nor family_id; not a family manifest")
    variants = sorted(variants)
    if len(set(variants)) != len(variants):
        raise ValueError(f"duplicate variant ids: {sorted({v for v in variants if variants.count(v) > 1})}")
    models = sorted(models)
    if reference_model is not None and reference_model not in models:
        raise ValueError(f"reference model {reference_model!r} is not one of the models {models}")
    if repetition_axis.get("kind") not in REPETITION_AXIS_KINDS:
        raise ValueError(f"repetition_axis.kind must be one of {list(REPETITION_AXIS_KINDS)}")
    grid = {"variants": variants, "models": models, "repetitions": sorted(repetitions),
            "repetition_axis": {"kind": repetition_axis["kind"], "note": repetition_axis["note"]},
            "expert": reference_model is not None}
    if reference_model is not None:
        grid["reference_model"] = reference_model
    family = {"family_id": family_id, "manifest_path": rel, "manifest_sha256": sha256_file(manifest_path)}
    if version is not None:
        family["version"] = version
    return {"declaration_kind": DECLARATION_KIND, "campaign_id": campaign_id, "backend": backend,
            "family": family, "declared_grid": grid, "results_subtree": f"{campaign_id}/{family_id}", "note": note}


def write_or_refuse_declaration(out_root, doc) -> str:
    """First invocation writes campaign_declaration.json; a resume must carry
    the same declaration byte for byte, or it is another campaign
    (RecordConflict). Returns the file's sha256, which the meta records."""
    out_root = Path(out_root)
    path = out_root / "campaign_declaration.json"
    text = _render(doc)
    if path.is_file():
        if path.read_text() != text:
            raise RecordConflict(
                f"{path} already declares a different campaign (models, repetitions, variants, "
                "family or note differ) - a campaign directory holds one declaration; pass the "
                "same arguments, or start a fresh campaign directory")
    else:
        out_root.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return sha256_file(path)


def check_written_declaration(out_root, manifest_path):
    """The declaration just written loads through the shared reader and binds
    its family: every declared variant accepted by the manifest. Returns the
    declaration; ValueError names the offending variants."""
    declaration = load_declaration(Path(out_root) / "campaign_declaration.json")
    check_family_manifest(declaration, manifest_path)
    return declaration


def write_or_refuse_family_snapshot_dir(out_root, family_dir) -> dict:
    """The whole family directory the declaration pins (manifest, variant
    documents, certificates, deltas, stages: what the Isaac scoring tools
    read beside the manifest, and what the historical Isaac bundles carry),
    copied file by file into family_snapshot/ with the one-file rule of
    write_or_refuse_family_snapshot: written once, held on resume, another
    family refuses. Returns {name: sha256}. Found by the F39 smoke campaign
    (2026-09-15): the manifest alone let the verifier pass and the go/clean
    derivation fail on the first variant document."""
    family_dir = Path(family_dir)
    if not family_dir.is_dir():
        raise ValueError(f"{family_dir}: no such family directory")
    files = sorted(p for p in family_dir.iterdir() if p.is_file())
    if not files:
        raise ValueError(f"{family_dir}: no files to snapshot")
    return {p.name: write_or_refuse_family_snapshot(out_root, p, p.name) for p in files}


def write_or_refuse_family_snapshot(out_root, manifest_path, name) -> str:
    """The family manifest the declaration pins, copied byte for byte into the
    run tree as family_snapshot/<name> (the raw bundle carries it, the
    certifiers bind it by hash against declaration.manifest_sha256). A
    resume must find the same bytes, or it is another family
    (RecordConflict). Returns the copy's sha256. Found by the F36 smoke
    campaign (2026-09-15): the CARLA runner declared the family by path and
    hash but laid no snapshot, and every threading tool refused the tree."""
    out_root, manifest_path = Path(out_root), Path(manifest_path)
    path = out_root / "family_snapshot" / name
    data = manifest_path.read_bytes()
    if path.is_file():
        if path.read_bytes() != data:
            raise RecordConflict(
                f"{path} already holds a different family manifest than {manifest_path} - a campaign "
                "directory holds one family; pass the same family, or start a fresh campaign directory")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return sha256_file(path)


# --- the meta -----------------------------------------------------------------------

def build_campaign_meta(campaign_id, family_id, tools_sha256, models, repetitions, repetition_axis,
                        cells_planned, layout_template, agent_identity, simulator, instruments,
                        declaration_sha256, smoke, started_utc, python, source_git_describe,
                        family_coverage=None, host=None, gpu=None) -> dict:
    """campaign_meta/v0.4, the one top level both backends write. Backend-only
    instrument hashes (route XMLs, the robot record, agent pairs) go under
    `instruments`; the expectation is NOT here, it is the declaration. `host`
    and `gpu` are the records agent_identity.host_record and gpu_record
    measure at campaign start (F52); `simulator` carries the declared pin and,
    under `build`, what simulator bytes ran (agent_identity.simulator_build)."""
    return {
        "meta_kind": META_KIND,
        "campaign_id": campaign_id,
        "family_id": family_id,
        "started_utc": started_utc,
        "smoke": bool(smoke),
        "declaration_sha256": declaration_sha256,
        "tools_sha256": dict(tools_sha256),
        "models": sorted(models),
        "repetitions": sorted(repetitions),
        "repetition_axis": {"kind": repetition_axis["kind"], "note": repetition_axis["note"]},
        "cells_planned": cells_planned,
        "layout_template": layout_template,
        "family_coverage": family_coverage,
        "agent_identity": dict(agent_identity or {}),
        "simulator": dict(simulator),
        "instruments": dict(instruments),
        "host": dict(host) if host else None,
        "gpu": dict(gpu) if gpu else None,
        "source_git_describe": source_git_describe,
        "python": python,
    }


def write_or_refuse_campaign_meta(out_root, meta) -> None:
    """First run writes campaign_meta.json; a resume under the SAME
    instruments keeps the record untouched; a resume under CHANGED
    instruments (tools, instrument hashes, the declaration, an agent's
    identity) raises RecordConflict - one campaign's evidence is produced
    by one recorded instrument set. An agent first run in a later session
    is APPENDED. A tree recorded under another meta kind refuses: the shape
    is the contract."""
    out_root = Path(out_root)
    path = out_root / "campaign_meta.json"
    if not path.is_file():
        out_root.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(meta))
        return
    recorded = json.loads(path.read_text())
    if recorded.get("meta_kind") != meta["meta_kind"]:
        raise RecordConflict(
            f"{path} was recorded as {recorded.get('meta_kind')!r}, this runner writes "
            f"{meta['meta_kind']!r} - a campaign directory holds one meta shape; start a fresh "
            "campaign directory")
    drifted = []
    rec, cur = recorded.get("tools_sha256", {}), meta.get("tools_sha256", {})
    drifted += [f"tools_sha256/{k}" for k in sorted(set(rec) | set(cur)) if rec.get(k) != cur.get(k)]
    rec_i, cur_i = recorded.get("instruments", {}), meta.get("instruments", {})
    for key in sorted(set(rec_i) | set(cur_i)):
        rec_v, cur_v = rec_i.get(key), cur_i.get(key)
        if isinstance(rec_v, dict) or isinstance(cur_v, dict):
            rec_v, cur_v = rec_v or {}, cur_v or {}
            drifted += [f"instruments/{key}/{k}" for k in sorted(set(rec_v) | set(cur_v))
                        if rec_v.get(k) != cur_v.get(k)]
        elif rec_v != cur_v:
            drifted.append(f"instruments/{key}")
    if recorded.get("declaration_sha256") != meta.get("declaration_sha256"):
        drifted.append("declaration_sha256")
    if recorded.get("simulator") != meta.get("simulator"):
        drifted.append("simulator")
    for block in ("host", "gpu"):   # F52: one campaign's evidence comes from one box
        if recorded.get(block) != meta.get(block):
            drifted.append(block)
    rec_id, cur_id = recorded.get("agent_identity", {}), meta.get("agent_identity", {})
    drifted += [f"agent_identity/{m}" for m in sorted(set(rec_id) & set(cur_id)) if rec_id[m] != cur_id[m]]
    new_agents = {m: cur_id[m] for m in cur_id if m not in rec_id}
    if drifted:
        raise RecordConflict(
            f"resuming campaign '{recorded.get('campaign_id')}' with instruments that differ from its "
            f"recorded campaign_meta.json: {drifted} - one campaign's evidence is produced by one "
            "recorded instrument set; restore the recorded tools, or start a fresh campaign directory")
    if new_agents:
        recorded.setdefault("agent_identity", {}).update(new_agents)
        recorded["models"] = sorted(set(recorded.get("models", [])) | set(new_agents))
        path.write_text(_render(recorded))


# --- readers shared by the bundlers and scorers ----------------------------------------

def read_tree_expectation(run_dir):
    """(declaration, meta) the runner left in a family-run tree, each None
    when absent. The declaration is validated by the shared reader; the meta
    is read as data (its kind is checked where it matters)."""
    run_dir = Path(run_dir)
    declaration = meta = None
    d = run_dir / "campaign_declaration.json"
    if d.is_file():
        declaration = load_declaration(d)
    m = run_dir / "campaign_meta.json"
    if m.is_file():
        meta = json.loads(m.read_text())
    return declaration, meta


def agent_fingerprints(meta) -> dict:
    """{agent: fingerprint} from a campaign_meta/v0.3 or v0.4 record (empty for
    older or absent metas): what every run header of that agent must carry."""
    if not meta or meta.get("meta_kind") not in META_KINDS:
        return {}
    return {m: identity_fingerprint(block) for m, block in meta.get("agent_identity", {}).items()}


BOX_VINTAGE_FIELDS = {"gpu": ("name", "driver", "cuda_driver"), "simulator": ("kind", "pin")}


def box_vintage(meta) -> dict:
    """F58: the box facts a campaign's instrument vintage depends on, flattened from a campaign_meta
    record: the GPU's name, driver and CUDA driver and the simulator's kind and pin (the build
    version when the meta carries one). Every key is present, None when the meta lacks it, so two
    families compare field by field; a v0.3 meta (no gpu block) compares as all None."""
    out = {}
    for block, fields in BOX_VINTAGE_FIELDS.items():
        src = (meta or {}).get(block) or {}
        for f in fields:
            out[f"{block}.{f}"] = src.get(f)
    out["simulator.build.version"] = (((meta or {}).get("simulator") or {}).get("build") or {}).get("version")
    return out


def bundle_fitness(run_dir):
    """(reason, instrument_record) for freezing a family-run tree. Refused: a
    declaration without its meta (a runner tree whose instrument record is
    missing), and a smoke meta (a throwaway run can never become evidence).
    A tree with neither is LEGACY, written before the runner recorded
    anything, and freezes with that fact in the provenance: historical
    campaigns are never re-run (ruling 2026-09-08)."""
    declaration, meta = read_tree_expectation(run_dir)
    if meta is None and declaration is not None:
        return ("campaign_declaration.json without campaign_meta.json (the runner's instrument record "
                "is missing)", None)
    if meta is None:
        return (None, "legacy: none")
    if meta.get("smoke"):
        return ("a smoke run (campaign_meta.smoke) can never become evidence", None)
    return (None, meta.get("meta_kind"))
