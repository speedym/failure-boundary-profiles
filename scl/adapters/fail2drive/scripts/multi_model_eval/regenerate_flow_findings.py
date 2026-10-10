#!/usr/bin/env python3
"""Re-measure declared flow campaigns twice and emit findings.

Inputs are declarations passed in, never named here: --campaign (the grid,
the family by path and hash, the results subtree; several families of one
campaign, or several campaigns), --zone / --map-excerpt (the instrument).
Raw evidence is read-only.

Outputs, split by what they are and keyed by campaign AND family (F36):
<out>/remeasurement.json is the SUMMARY (canon-bound, entries
campaigns[<campaign>][<family>]); <out>/<campaign>/<family>/flow_stats and
findings are the derived members the scoring driver leaves for the bundle;
<out>/<campaign>/<family>/audit/remeasurement_rows.json is the EVIDENCE
(the residual runs, pinned by hash, carried in the derived bundle). The repeat
run lives under <out>/repeat/, outside the derived folder.

A raw tree from before the scoring chain (campaign_carla_v04, v05) carries the
runner's own aggregation under flow_stats/: a deprecated raw member, read by
nothing since 2026-10-09 (until then it was diffed against as historical_raw;
the difference between an old reader's numbers and today's is not a finding).
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
MME = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(MME)]
import findings_flow
import flow_gap_certificate as fgc
from scl_core.campaign_declaration import check_family_manifest, coverage_gate, load_declarations, resolve_family_manifest
from scl_core.records import portable_path, render, sha256_file as sha

REPORT_KIND = "flow_remeasurement/v0.7"       # 2026-10-09: no comparison baselines (vs_*, baselines, prior_aggregation gone)
ROWS_KIND = "flow_remeasurement_rows/v0.2"    # 2026-10-09: no comparisons block
ROWS_FILE = "remeasurement_rows.json"

def parse_certificate(spec):
    """FAMILY=PATH: the declared flow gap certificate of one family (F55), read once here so a
    wrong kind or a certificate for another family refuses before anything is scored."""
    family, sep, path = spec.partition("=")
    if not sep or not family or not path:
        raise ValueError(f"--gap-certificate expects FAMILY=PATH, got {spec!r}")
    doc, _ = fgc.load_certificate(path)
    if doc["family_id"] != family:
        raise ValueError(f"--gap-certificate {spec!r}: the certificate is for {doc['family_id']}")
    return family, Path(path)


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def key(row):
    return row["model"], row["seed"], row["scenario_id"]


def rates(rows):
    result = {}
    for model in sorted({r["model"] for r in rows}):
        group = [r for r in rows if r["model"] == model]
        result[model] = {quantity: findings_flow._rate([r.get(quantity) for r in group])
                         for quantity in ("p_go", "p_clean", "clean_given_go")}
    return result


def validate_coverage(rows, declaration):
    """The coverage gate over scored rows; returns the declared variant set."""
    return coverage_gate(declaration, (key(r) for r in rows))


def summarize_after(after, scenarios):
    """(summary, residual rows): residual clean-cross / no-measured-go
    disagreements are counted in the summary and listed in the evidence."""
    disagreements = [{"run": list(key(r)), "primary_outcome": r["primary_outcome"],
                      "nearest_miss_m": r["measured_go_nearest_miss_m"],
                      "sample_tolerance_m": r["measured_go_sample_tolerance_m"]}
                     for r in after if r["p_clean"] == 1 and r["p_go"] == 0]
    summary = {
        "runs": len(after), "models": sorted({r["model"] for r in after}),
        "seeds": sorted({r["seed"] for r in after}), "variants": len(scenarios),
        "after_rates": rates(after),
        "unmeasured_go": sum(r["p_go"] is None for r in after),
        "approximated_corridors": sum(bool(r["measured_go_corridor_approximated"]) for r in after),
        "zone_versions": dict(sorted(Counter(r.get("measured_go_zone_version") for r in after).items())),
        "clean_without_measured_go_count": len(disagreements),
        "outcomes": dict(sorted(Counter(r["primary_outcome"] for r in after).items())),
    }
    return summary, disagreements


def regenerate(results, out, zone, map_excerpt, declarations, certificates=None):
    """`certificates` {family_id: certificate path}: the declared flow gap certificate a family is
    scored against (F55); a family without one is scored without bands, and the report says so."""
    certificates = dict(certificates or {})
    undeclared = sorted(set(certificates) - {d.family_id for d in declarations})
    if undeclared:
        raise ValueError(f"--gap-certificate names families this run does not declare: {undeclared}")
    report = {"report_kind": REPORT_KIND,
              "generator_sha256": sha(Path(__file__)), "campaigns": {}}
    for declaration in declarations:
        campaign, family = declaration.campaign_id, declaration.family_id
        certificate = certificates.get(family)
        if certificate is None:
            print(f"{campaign}/{family}: no gap certificate declared; scored without bands or decisions", flush=True)
        source = results / declaration.results_subtree
        # the family as run, from the raw tree (principle 13); the hash check is the same either way
        manifest, _ = resolve_family_manifest(declaration, source, ROOT)
        check_family_manifest(declaration, manifest)
        derived = out / campaign / family
        outputs = [derived / "flow_stats", out / "repeat" / campaign / family / "flow_stats"]
        for dest in outputs:
            print(f"Re-aggregating {campaign}/{family} -> {dest.relative_to(out)}", flush=True)
            subprocess.run([sys.executable, str(MME / "flow_aggregate.py"), str(source),
                            "--manifest", str(manifest), "--zone", str(zone), "--map-excerpt", str(map_excerpt),
                            "--out", str(dest), "--by-model", "--quiet",
                            *(["--gap-certificate", str(certificate)] if certificate else [])], check=True)
        identities = [{p.name: sha(p) for p in dest.iterdir() if p.is_file()} for dest in outputs]
        if identities[0] != identities[1]:
            raise ValueError(f"non-deterministic flow output for {campaign}/{family}")
        current = read_rows(outputs[0] / "flow_runs.jsonl")
        scenarios = validate_coverage(current, declaration)

        summary, residual = summarize_after(current, scenarios)
        rows_doc = {"rows_kind": ROWS_KIND, "campaign_id": campaign,
                    "generator_sha256": report["generator_sha256"],
                    "clean_without_measured_go": residual}
        rows_path = derived / "audit" / ROWS_FILE
        rows_path.parent.mkdir(parents=True, exist_ok=True)
        rows_path.write_text(render(rows_doc))
        summary.update(family_id=family,
                       campaign_declaration=declaration.record(ROOT),
                       repeated_outputs_identical=True, output_sha256=identities[0],
                       rows={"path": f"{campaign}/{family}/audit/{ROWS_FILE}", "sha256": sha(rows_path)})
        paths = findings_flow.emit(outputs[0], manifest, declaration, derived / "findings", zone, map_excerpt,
                                   certificate)
        repeat_paths = findings_flow.emit(outputs[1], manifest, declaration, outputs[1].parent / "findings",
                                          zone, map_excerpt, certificate)
        if {p.name: sha(p) for p in paths} != {p.name: sha(p) for p in repeat_paths}:
            raise ValueError(f"non-deterministic findings for {campaign}/{family}")
        summary["findings_sha256"] = {p.name: sha(p) for p in paths}
        summary["gap_certificate"] = ({"path": portable_path(certificate, ROOT), "sha256": sha(certificate)}
                                      if certificate else None)
        summary["repeated_findings_identical"] = True
        report["campaigns"].setdefault(campaign, {})[family] = summary
        print(f"Verified {campaign}/{family}: {summary['runs']} runs, "
              f"{summary['clean_without_measured_go_count']} residual clean/no-go", flush=True)
    report["interpretation"] = (
        "Outcome labels and clean-pass rates are unchanged. Commitment (p_go) and conditional "
        "competence use the fixed OpenDRIVE oncoming driving-lane zone (derive_measured_go v3), whose "
        "geometry is independent of oncoming recording length - unlike the v1 swept corridor, which "
        "could end before the junction when the slow flow had not arrived and read go=false on a "
        "completed turn. clean_without_measured_go_count is the residual clean-cross / no-measured-go "
        "disagreement AFTER the fix. The residual runs are evidence: they live in "
        "each family's audit/remeasurement_rows.json (pinned here by hash, carried in the derived "
        "bundle), never in canon. These documents are reproducible measurements against a defined "
        "lane geometry, not a claim that any variant is a certified passable crossing.")
    (out / "remeasurement.json").write_text(render(report))
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-root", type=Path, default=MME / "results",
                    help="location of the raw campaign trees; each declaration names its subtree")
    ap.add_argument("--campaign", action="append", required=True, type=Path, metavar="DECLARATION",
                    help="campaign declaration (artifacts/examples/campaigns/...); repeatable, required")
    ap.add_argument("--zone", type=Path, required=True,
                    help="declared conflict-zone document (artifacts/examples/scoring_instruments/...)")
    ap.add_argument("--map-excerpt", type=Path, required=True,
                    help="the pinned OpenDRIVE excerpt that zone declares as its source")
    ap.add_argument("--gap-certificate", action="append", default=[], metavar="FAMILY=PATH",
                    help="the declared flow gap certificate of one family (F55; scoring_instruments/"
                         "carla_flow_gap_certificate_<family>.json), repeatable; a family without one is "
                         "scored without bands or decisions and the report says so")
    ap.add_argument("--out", type=Path, required=True,
                    help="the output root, the caller's work tree (no default under the checkout, F72)")
    args = ap.parse_args()
    try:
        certificates = [parse_certificate(spec) for spec in args.gap_certificate]
        declarations = load_declarations(args.campaign)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))
    if len({family for family, _ in certificates}) != len(certificates):
        ap.error("one --gap-certificate per family")
    try:
        regenerate(args.results_root, args.out, args.zone.resolve(), args.map_excerpt.resolve(),
                   declarations, {family: path.resolve() for family, path in certificates})
    except ValueError as exc:
        ap.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
