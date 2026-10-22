#!/usr/bin/env python3
"""Bind the committed threading findings to the certified bundles they came from.

Canon holds findings and declarations; bundles hold evidence. The flow
documents are bound to their bundles by flow_certification; this driver
writes the same binding for the static-threading documents: per declared
(campaign, family), the declaration by path and hash, the bundle by zip hash
and content id (the sha256 of its MANIFEST), the scored table and the family
snapshot as bundle members by path and MANIFEST-listed hash, the stats hash
the documents record, and every document by name and hash. Every link is
checked before it is written: a document whose recorded scored hash is not
the bundle member's, whose declaration hash is not the file's, or whose
emitter, estimator or stats tool is not the current one refuses the whole
record. Nothing here names a campaign, a family or a bundle: the
declarations and --bundles-dir are passed in.

    conda run -n scl_prototype python3 adapters/fail2drive/scripts/multi_model_eval/certify_threading_findings.py \\
        --campaign artifacts/examples/campaigns/campaign_carla_v01/fm_004_threshold_sweep.json ... \\
        --bundles-dir <dir holding campaign_<id>.zip> \\
        --findings-dir artifacts/examples/findings \\
        --out artifacts/examples/findings/threading_certification.v0.1.json

Categorical families (asset_identity) bind like any other since F35
(2026-09-14): their documents carry an axis of kind categorical.

Two bundle shapes (F30, 2026-09-14). A campaign frozen the new way is a
RAW bundle (<campaign>.zip, evidence only) plus a DERIVED bundle
(<campaign>_derived.zip, the scoring run keyed by the raw content id): the
scored table is a derived member, the family snapshot a raw one, and the
record binds both content ids. This driver writes no bundle (F36,
2026-09-15): one derived bundle per campaign holds every family the
campaign ran, threading and flow alike, frozen by the one writer
(campaign_manifest.py --produce-derived); a raw bundle without its derived
one refuses with that command. The historical mixed bundles (v01 to v03)
hold the scored table inside the raw zip; their entries bind one id and
record derived_bundle: null.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
MME = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(MME)]
from scl_core import run_rows as rr  # noqa: E402
import campaign_manifest  # noqa: E402  (which chain scored a family: its plan)
from scl_core import campaign_bundles as cb  # noqa: E402
from scl_core.campaign_declaration import load_declarations  # noqa: E402
from scl_core.records import render, sha256_file as sha  # noqa: E402
from scl_core.stats import emit_findings, fit_rows  # noqa: E402  (the current tools, pinned once: K11)

REPORT_KIND = "threading_certification/v0.2"
EVIDENCE_RULE = ("canon holds findings and declarations; the scored tables and family snapshots are "
                 "evidence and live in the certified campaign bundles, referenced here by bundle content "
                 "id, member path and MANIFEST-listed member hash")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bundle(zip_path: Path, campaign_id: str) -> dict:
    """The core opener (provenance must name the MANIFEST hash, every member listed, members re-verified
    on read), as a refusal."""
    if not zip_path.is_file():
        raise SystemExit(f"ERROR: bundle {zip_path} not found; pass --bundles-dir holding {campaign_id}.zip")
    return cb.open_bundle(zip_path, campaign_id, refuse=lambda m: SystemExit(f"ERROR: {m}"))


def derived_for(raw: dict, bundles_dir: Path) -> dict | None:
    """The derived bundle beside a raw one: read when present, None for a
    historical mixed bundle that carries its scored table inside; a raw-only
    bundle without one refuses, naming the one writer (F36)."""
    campaign = raw["campaign_id"]
    path = bundles_dir / f"{campaign}_derived.zip"
    if not path.is_file():
        if raw["kind"] == cb.RAW_KIND:
            raise SystemExit(f"ERROR: {raw['path'].name} is a raw bundle and no {path.name} exists; score it "
                             "(score_campaign.py) and freeze the derived folder with "
                             "campaign_manifest.py --produce-derived (the one writer)")
        return None
    derived = read_bundle(path, campaign)
    if derived["kind"] != cb.DERIVED_KIND:
        raise SystemExit(f"ERROR: {path.name} is {derived['kind']!r}, not {cb.DERIVED_KIND}")
    if derived["provenance"]["raw_bundle"]["content_id_sha256"] != raw["content_id_sha256"]:
        raise SystemExit(f"ERROR: {path.name} was computed from raw {derived['provenance']['raw_bundle']['content_id_sha256'][:12]}, "
                         f"{raw['path'].name} is {raw['content_id_sha256'][:12]}")
    return derived


def check_stamp_member(bundle: dict, member: str) -> dict:
    """The rows' stamp as frozen in the bundle, admitted by the one check (run_rows.check_stamp_document,
    K9): the scorer and the vocabulary it names must be the files in this tree; a record never binds rows
    a retired scorer produced."""
    bundle["member_hash"](member)
    with zipfile.ZipFile(bundle["path"]) as z:
        stamp = json.loads(z.read(member))
    try:
        return rr.check_stamp_document(stamp, ROOT, f"{bundle['path'].name}:{member}")
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from None


def certify(declarations, bundles_dir: Path, findings_dir: Path) -> dict:
    pins = fit_rows.tool_pins(ROOT)
    current = {"emitter_sha256": emit_findings.emitter_pin(ROOT)["sha256"],
               "estimator_sha256": pins["estimator"]["sha256"],
               "stats_tool_sha256": pins["fit"]["sha256"]}
    report = {"report_kind": REPORT_KIND, "driver_sha256": sha(Path(__file__)), **current,
              "evidence_rule": EVIDENCE_RULE, "campaigns": {}}
    bundles, deriveds = {}, {}
    for declaration in declarations:
        campaign, family = declaration.campaign_id, declaration.family_id
        if campaign_manifest.scoring_chain(declaration) != "threading":
            # F39: every declaration may be passed; the plan says which chain scored the
            # family, and a flow family is bound by the flow record
            print(f"{campaign}/{family}: scored by the flow chain, bound by flow_certification; skipped", flush=True)
            continue
        if campaign not in bundles:
            bundles[campaign] = read_bundle(bundles_dir / f"{campaign}.zip", campaign)
            deriveds[campaign] = derived_for(bundles[campaign], bundles_dir)
        bundle, derived = bundles[campaign], deriveds[campaign]
        families = bundle["provenance"].get("families", {})
        if family not in families:
            raise SystemExit(f"ERROR: {campaign}.zip: bundle_provenance lists no family {family!r} "
                             f"(has {sorted(families)})")
        declaration_record = declaration.record(ROOT)
        snapshot_member = f"{campaign}/{family}/family_snapshot/{family}.json"
        snapshot_sha = bundle["member_hash"](snapshot_member)
        if snapshot_sha != declaration.manifest_sha256:
            raise SystemExit(f"ERROR: {campaign}/{family}: the bundle's family snapshot hashes to "
                             f"{snapshot_sha}, the declaration pins {declaration.manifest_sha256}")
        entry = {"campaign_declaration": declaration_record,
                 "bundle": {"path": f"{campaign}.zip", "bundle_kind": bundle["kind"],
                            "zip_sha256": bundle["zip_sha256"],
                            "content_id_sha256": bundle["content_id_sha256"],
                            "family_sub_id_sha256": families[family].get("family_sub_id_sha256")},
                 "derived_bundle": None if derived is None else {
                     "path": f"{campaign}_derived.zip", "bundle_kind": derived["kind"],
                     "zip_sha256": derived["zip_sha256"], "content_id_sha256": derived["content_id_sha256"],
                     "source_snapshot_sha256": derived["provenance"]["source_snapshot"]["sha256"]},
                 "family_manifest_sha256": declaration.manifest_sha256,
                 "coverage": {"models": list(declaration.models), "seeds": list(declaration.repetitions),
                              "variants": len(declaration.variants)},
                 "evidence": {"bundle_content_id_sha256": bundle["content_id_sha256"],
                              "members": {snapshot_member: snapshot_sha}}}
        if derived is not None:
            entry["evidence"]["derived_content_id_sha256"] = derived["content_id_sha256"]
            entry["evidence"]["derived_members"] = {}
        scored_member = f"{campaign}/{family}/{rr.ROWS_MEMBER}"
        stats_member = f"{campaign}/{family}/{rr.STATS_MEMBER}"
        if derived is None:      # historical mixed bundle: the scored table sits beside the evidence
            scored_sha = bundle["member_hash"](scored_member)
            entry["evidence"]["members"][scored_member] = scored_sha
            holder = bundle
        else:                    # F30: the scored table is a derived member, keyed by the raw id
            scored_sha = derived["member_hash"](scored_member)
            entry["evidence"]["derived_members"][scored_member] = scored_sha
            stats_member_sha = derived["member_hash"](stats_member)
            entry["evidence"]["derived_members"][stats_member] = stats_member_sha
            holder = derived
        check_stamp_member(holder, f"{campaign}/{family}/{rr.STAMP_MEMBER}")
        documents, stats_hashes = {}, set()
        for model in declaration.models:
            name = rr.document_name(campaign, family, model)
            path = findings_dir / name
            if not path.is_file():
                raise SystemExit(f"ERROR: {campaign}/{family}: declared model {model!r} has no document {name}")
            doc = json.loads(path.read_text())
            prov = doc.get("provenance", {})
            pooled = (doc.get("pooling") or {}).get("campaigns") or [{}]
            checks = [
                (doc.get("findings_kind") == rr.DOCUMENT_KIND, f"findings_kind {doc.get('findings_kind')!r}"),
                (len(pooled) == 1 and pooled[0].get("id") == campaign, "pooling.campaigns: the campaign alone"),
                (pooled[0].get("declaration") == declaration_record, "pooling.campaigns[0].declaration"),
                (doc.get("family_id") == family, "family_id"),
                (doc.get("model", {}).get("id") == model, "model.id"),
                (prov.get("rows_sha256") == scored_sha,
                 f"rows_sha256 {prov.get('rows_sha256')} != bundle member {scored_sha}"),
                (prov.get("stats_sha256") == stats_member_sha if derived is not None else True,
                 f"stats_sha256 {prov.get('stats_sha256')} != bundle member {stats_member_sha if derived is not None else None}"),
                ((prov.get("emitter") or {}).get("sha256") == current["emitter_sha256"], "emitter is not the current emitter"),
                ((prov.get("estimator") or {}).get("sha256") == current["estimator_sha256"],
                 "estimator is not the current shared estimator"),
                ((prov.get("fit") or {}).get("sha256") == current["stats_tool_sha256"], "fit is not the current fit stage"),
            ]
            failed = [why for ok, why in checks if not ok]
            if failed:
                raise SystemExit(f"ERROR: {name}: " + "; ".join(failed) + ". Nothing written.")
            stats_hashes.add(prov.get("stats_sha256"))
            documents[name] = sha(path)
        if len(stats_hashes) != 1:
            raise SystemExit(f"ERROR: {campaign}/{family}: documents record {len(stats_hashes)} different "
                             f"stats tables {sorted(map(str, stats_hashes))}; one family, one fit run")
        entry["stats_sha256"] = stats_hashes.pop()
        entry["documents"] = documents
        entry["axis"] = json.loads((findings_dir / next(iter(documents))).read_text())["axis"]   # the documents' axis
        report["campaigns"].setdefault(campaign, {})[family] = entry
        print(f"{campaign}/{family}: {len(documents)} documents bound to bundle {bundle['content_id_sha256'][:12]}",
              flush=True)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--campaign", action="append", required=True, type=Path, metavar="DECLARATION",
                    help="campaign declaration (artifacts/examples/campaigns/<campaign>/<family>.json); repeatable")
    ap.add_argument("--bundles-dir", required=True, type=Path,
                    help="directory holding the certified <campaign>.zip and <campaign>_derived.zip bundles "
                         "(never in the repo)")
    ap.add_argument("--findings-dir", required=True, type=Path,
                    help="the canon findings directory holding the fs_<campaign>_<family>_<model>.v0.2.json documents")
    ap.add_argument("--out", required=True, type=Path, help="the certification record to write (canon)")
    args = ap.parse_args()
    try:
        declarations = load_declarations(args.campaign)
    except ValueError as exc:
        ap.error(str(exc))
    report = certify(declarations, args.bundles_dir.resolve(), args.findings_dir.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(report))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
