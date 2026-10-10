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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]   # adapters/isaac/scripts/multi_model_eval -> repo
MME = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import campaign_bundles as cb  # noqa: E402  (stdlib-only core)
from scl_core.campaign_declaration import load_declarations  # noqa: E402  (stdlib-only core)

REPORT_KIND = "findings_certification/v0.1"
FINDINGS_KIND = "findings_summary/v0.2"
CHAIN = "isaac"
EVIDENCE_RULE = ("canon holds findings and declarations; the raw bundle holds the evidence and the derived "
                 "bundle holds one interpretation keyed by the raw content id; every entry binds the declaration "
                 "by hash, the family snapshot as a raw member, the scored table and the go/clean derivation as "
                 "derived members, and every document by hash, byte-equal to its derived member")
# derived members of one family, by the provenance field of the document that pins each one
DERIVED_MEMBERS = (("scored_sha256", "scoring_out/scored_runs.json"),
                   ("derived_rows_sha256", "go_clean/go_clean_rows.jsonl"),
                   ("derived_summary_sha256", "go_clean/go_clean_summary.json"))


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_bundle(zip_path: Path, campaign_id: str, kinds) -> dict:
    if not zip_path.is_file():
        raise SystemExit(f"ERROR: no bundle at {zip_path}")
    bundle = cb.read_bundle(zip_path)
    if bundle["campaign_id"] != campaign_id:
        raise SystemExit(f"ERROR: {zip_path.name} is campaign {bundle['campaign_id']!r}, not {campaign_id!r}")
    if bundle["kind"] not in kinds:
        raise SystemExit(f"ERROR: {zip_path.name} is {bundle['kind']!r}, not one of {list(kinds)}")

    def member_hash(path: str) -> str:
        if path not in bundle["listed"]:
            raise SystemExit(f"ERROR: {zip_path.name} lists no member {path}")
        return bundle["listed"][path]
    bundle["member_hash"] = member_hash
    return bundle


def certify(declarations, bundles_dir: Path, findings_dir: Path) -> dict:
    current = {"emitter_sha256": sha(MME / "findings_v2.py"),
               "estimator_sha256": sha(ROOT / "scl_core/stats/boundary.py")}
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
        documents = {}
        for brain in (m for m in declaration.models if m != declaration.reference_model):
            name = f"fs_{campaign}_{family}_{brain}.v0.2.json"   # C9: campaign, family, model, as on CARLA
            path = findings_dir / name
            if not path.is_file():
                raise SystemExit(f"ERROR: {campaign}/{family}: declared brain {brain!r} has no document {name}")
            doc = json.loads(path.read_text())
            prov = doc.get("provenance", {})
            member = f"{campaign}/{family}/findings/{name}"
            checks = [
                (doc.get("findings_kind") == FINDINGS_KIND, f"findings_kind {doc.get('findings_kind')!r}"),
                (doc.get("campaign", {}).get("id") == campaign, "campaign.id"),
                (doc.get("family_id") == family, "family_id"),
                (doc.get("brain", {}).get("id") == brain, "brain.id"),
                (prov.get("emitter_sha256") == current["emitter_sha256"], "emitter_sha256 is not the current emitter"),
                (prov.get("estimator_sha256") == current["estimator_sha256"],
                 "estimator_sha256 is not the current shared estimator"),
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
