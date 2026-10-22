#!/usr/bin/env python3
"""Bind the committed Isaac findings to the certified bundles they came from.

Canon holds findings and declarations; bundles hold evidence. The CARLA
documents are bound by threading_certification and flow_certification;
this driver writes the same binding for the Isaac documents (PARITY B8,
closed 2026-09-15): per declared (campaign, family), the declaration by
path and hash, the RAW bundle by zip hash and content id with the family
snapshot as a MANIFEST-listed member, the DERIVED bundle by zip hash and
content id with the scored table and the go/clean derivation as members,
and every document by name and hash, byte-equal to its derived member.
Every link is checked before it is written: a document whose recorded
hashes are not the members', whose emitter or estimator is not the current
one, or whose declaration hash is not the file's refuses the whole record.

The record kind is chain-neutral (findings_certification/v0.1, principle
8: a kind never carries a backend's name); the two CARLA records can
converge to it later. Nothing here names a campaign, a family or a bundle:
the declarations and --bundles-dir are passed in. Stdlib only.

    python3 adapters/isaac/scripts/multi_model_eval/certify_findings.py \\
        --campaign artifacts/examples/campaigns/campaign_isaac_v07/fm_isaac_003.json ... \\
        --bundles-dir <dir holding <campaign>.zip and <campaign>_derived.zip> \\
        --findings-dir artifacts/examples/findings \\
        --out artifacts/examples/findings/certification/<campaign>/findings_certification.v0.1.json   (one campaign per record, 2026-09-17)
"""
import argparse
import hashlib
import json
import zipfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]   # adapters/isaac/scripts/multi_model_eval -> repo
MME = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import campaign_bundles as cb  # noqa: E402  (stdlib-only core)
from scl_core.campaign_declaration import load_declarations  # noqa: E402  (stdlib-only core)
from scl_core import run_rows as rr  # noqa: E402  (stdlib-only core: the one members and the one document kind, F82)
from scl_core.stats import emit_findings, fit_rows  # noqa: E402  (the current tools, pinned once: K11)

REPORT_KIND = "findings_certification/v0.1"
FINDINGS_KIND = rr.DOCUMENT_KIND
CHAIN = "isaac"
EVIDENCE_RULE = ("canon holds findings and declarations; the raw bundle holds the evidence and the derived "
                 "bundle holds one interpretation keyed by the raw content id; every entry binds the declaration "
                 "by hash, the family snapshot as a raw member, the run rows and the fits as derived members, and every "
                 "document by hash, byte-equal to its derived member")
# derived members of one family, by the provenance field of the document that pins each one
DERIVED_MEMBERS = (("rows_sha256", rr.ROWS_MEMBER), ("stats_sha256", rr.STATS_MEMBER))


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_bundle(zip_path: Path, campaign_id: str, kinds) -> dict:
    """The core opener as a refusal: campaign and kind checked, members re-verified on read (until
    2026-10-11 this returned the listed hash without reading the bytes, register K14)."""
    if not zip_path.is_file():
        raise SystemExit(f"ERROR: no bundle at {zip_path}")
    return cb.open_bundle(zip_path, campaign_id, kinds, refuse=lambda m: SystemExit(f"ERROR: {m}"))


def check_stamp_member(bundle: dict, member: str) -> dict:
    """The rows' stamp as frozen in the derived bundle, admitted by the one check
    (run_rows.check_stamp_document, K9): the scorer and the vocabulary it names must be the files in
    this tree; a record never binds rows a retired scorer produced (the threading certifier's twin)."""
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
               "fit_sha256": pins["fit"]["sha256"]}
    report = {"report_kind": REPORT_KIND, "driver_sha256": sha(Path(__file__)), **current,
              "evidence_rule": EVIDENCE_RULE, "campaigns": {}}
    bundles = {}
    for declaration in declarations:
        campaign, family = declaration.campaign_id, declaration.family_id
        if declaration.backend != CHAIN:
            raise SystemExit(f"ERROR: {declaration.path.name} declares backend {declaration.backend!r}; this driver "
                             f"binds {CHAIN} campaigns (the CARLA records bind fail2drive)")
        if campaign not in bundles:
            raw = read_bundle(bundles_dir / f"{campaign}.zip", campaign, cb.RAW_KINDS)
            derived_path = bundles_dir / f"{campaign}_derived.zip"
            if not derived_path.is_file():
                raise SystemExit(f"ERROR: {raw['path'].name} has no {derived_path.name}; score it (score_campaign.py) "
                                 "and freeze the derived folder with campaign_manifest.py --produce-derived")
            derived = read_bundle(derived_path, campaign, (cb.DERIVED_KIND,))
            if derived["provenance"]["raw_bundle"]["content_id_sha256"] != raw["content_id_sha256"]:
                raise SystemExit(f"ERROR: {derived_path.name} was computed from raw "
                                 f"{derived['provenance']['raw_bundle']['content_id_sha256'][:12]}, "
                                 f"{raw['path'].name} is {raw['content_id_sha256'][:12]}")
            bundles[campaign] = (raw, derived)
        raw, derived = bundles[campaign]
        families = raw["provenance"].get("families", {})
        if family not in families:
            raise SystemExit(f"ERROR: {campaign}.zip: bundle_provenance lists no family {family!r} (has {sorted(families)})")
        if family not in derived["provenance"].get("families", []):
            raise SystemExit(f"ERROR: {campaign}_derived.zip holds no family {family!r}")
        snapshot_member = f"{campaign}/{family}/family_snapshot/family_manifest.json"
        snapshot_sha = raw["member_hash"](snapshot_member)
        if snapshot_sha != declaration.manifest_sha256:
            raise SystemExit(f"ERROR: {campaign}/{family}: the bundle's family snapshot hashes to {snapshot_sha}, "
                             f"the declaration pins {declaration.manifest_sha256}")
        derived_members = {f"{campaign}/{family}/{rel}": derived["member_hash"](f"{campaign}/{family}/{rel}")
                           for _, rel in DERIVED_MEMBERS}
        check_stamp_member(derived, f"{campaign}/{family}/{rr.STAMP_MEMBER}")
        documents = {}
        for brain in declaration.models:   # the reference model has its own document since F82
            name = rr.document_name(campaign, family, brain)
            path = findings_dir / name
            if not path.is_file():
                raise SystemExit(f"ERROR: {campaign}/{family}: declared brain {brain!r} has no document {name}")
            doc = json.loads(path.read_text())
            prov = doc.get("provenance", {})
            member = f"{campaign}/{family}/findings/{name}"
            pooled = (doc.get("pooling") or {}).get("campaigns") or [{}]
            checks = [
                (doc.get("findings_kind") == FINDINGS_KIND, f"findings_kind {doc.get('findings_kind')!r}"),
                (len(pooled) == 1 and pooled[0].get("id") == campaign, "pooling.campaigns: the campaign alone"),
                (pooled[0].get("declaration") == declaration.record(ROOT), "pooling.campaigns[0].declaration"),
                (doc.get("family_id") == family, "family_id"),
                (doc.get("model", {}).get("id") == brain, "model.id"),
                ((prov.get("emitter") or {}).get("sha256") == current["emitter_sha256"], "emitter is not the current emitter"),
                ((prov.get("estimator") or {}).get("sha256") == current["estimator_sha256"],
                 "estimator is not the current shared estimator"),
                ((prov.get("fit") or {}).get("sha256") == current["fit_sha256"], "fit is not the current fit stage"),
                (prov.get("family_manifest_sha256") == declaration.manifest_sha256,
                 "family_manifest_sha256 is not the declaration's"),
            ]
            for field, rel in DERIVED_MEMBERS:
                checks.append((prov.get(field) == derived_members[f"{campaign}/{family}/{rel}"],
                               f"{field} {prov.get(field)} != derived member {rel}"))
            checks.append((derived["member_hash"](member) == sha(path),
                           f"the canon document is not byte-equal to the derived member {member}"))
            failed = [why for ok, why in checks if not ok]
            if failed:
                raise SystemExit(f"ERROR: {name}: " + "; ".join(failed) + ". Nothing written.")
            documents[name] = sha(path)
        entry = {"campaign_declaration": declaration.record(ROOT), "chain": CHAIN,
                 "bundle": {"path": f"{campaign}.zip", "bundle_kind": raw["kind"], "zip_sha256": raw["zip_sha256"],
                            "content_id_sha256": raw["content_id_sha256"],
                            "family_sub_id_sha256": families[family].get("family_sub_id_sha256")},
                 "derived_bundle": {"path": f"{campaign}_derived.zip", "bundle_kind": derived["kind"],
                                    "zip_sha256": derived["zip_sha256"], "content_id_sha256": derived["content_id_sha256"],
                                    "source_snapshot_sha256": derived["provenance"]["source_snapshot"]["sha256"],
                                    "families": derived["provenance"]["families"]},
                 "family_manifest_sha256": declaration.manifest_sha256,
                 "coverage": {"models": list(declaration.models), "repetitions": list(declaration.repetitions),
                              "variants": len(declaration.variants)},
                 "evidence": {"bundle_content_id_sha256": raw["content_id_sha256"],
                              "members": {snapshot_member: snapshot_sha},
                              "derived_content_id_sha256": derived["content_id_sha256"],
                              "derived_members": derived_members},
                 "documents": documents}
        report["campaigns"].setdefault(campaign, {})[family] = entry
        print(f"{campaign}/{family}: {len(documents)} documents bound to raw {raw['content_id_sha256'][:12]} and "
              f"derived {derived['content_id_sha256'][:12]}", flush=True)
    return report


def render(report: dict) -> str:
    return json.dumps(report, indent=1, sort_keys=True) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--campaign", action="append", required=True, type=Path, metavar="DECLARATION",
                    help="campaign declaration (artifacts/examples/campaigns/<campaign>/<family>.json); repeatable")
    ap.add_argument("--bundles-dir", required=True, type=Path,
                    help="directory holding the certified <campaign>.zip and <campaign>_derived.zip bundles (never in the repo)")
    ap.add_argument("--findings-dir", required=True, type=Path,
                    help="the canon findings directory holding the fs_<campaign>_<brain>.v0.2.json documents")
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
