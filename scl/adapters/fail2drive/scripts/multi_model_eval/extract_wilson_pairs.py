#!/usr/bin/env python3
"""Extract every Wilson interval the flow aggregator printed, with counts derived from the certified bundles' rows.

S15(a) evidence. Opens the bundles the certification pins (ZIP hash, content
id, every member read against the bundle's MANIFEST), regroups the bundled
per-run rows the way the aggregator did for each interval-bearing table
(policy / reference split from the bundle's figure_data), refuses if a
printed count disagrees with the rows, and records one occurrence per
printed interval: campaign, table, group key, quantity, k, n, lo, hi (three
decimals as printed). Nothing is collapsed. The fixture is pinned to the
bundles by content id and member hash; tests/test_wilson_swap_inert.py
checks every occurrence and re-extracts live when the bundles are present.

    conda run -n scl_prototype python3 adapters/fail2drive/scripts/multi_model_eval/extract_wilson_pairs.py \\
        --certification artifacts/examples/findings/certification/*/flow_certification.v0.6.json \\
        --bundles-dir <dir holding the certified derived bundles> \\
        --out adapters/fail2drive/tests/fixtures/flow_wilson_pairs.json
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[4]   # adapters/fail2drive/scripts/multi_model_eval -> repo (F32)
sys.path.insert(0, str(ROOT))
from scl_core.records import portable_path, sha256_file as sha  # noqa: E402
from scl_core import campaign_bundles as cb  # noqa: E402

FIXTURE_KIND = "wilson_pairs/v0.4"   # one certification record per campaign (2026-09-17); bundles keyed <campaign>/<family> (F36)
# table -> (group key fields, row population as flow_aggregate.main groups them)
TABLES = {
    "flow_per_variant.jsonl": (("index",), "pooled"),
    "flow_reference_per_variant.jsonl": (("index",), "reference"),
    "flow_per_variant_model.jsonl": (("index", "model"), "all"),
    "flow_realized_per_seed.jsonl": (("index", "seed"), "all"),
}


def _defined(rows, field):
    values = [r[field] for r in rows if r.get(field) is not None]
    return sum(values), len(values)


# quantity -> (k, n from a group's rows; the table's printed k field or None; n field; interval fields)
QUANTITIES = {
    "collision": (lambda rows: (sum(1 for r in rows if r["flow_collision"]), len(rows)),
                  "n_collision_flow", "n_runs", "collision_wilson_lo", "collision_wilson_hi"),
    "stall": (lambda rows: (sum(1 for r in rows if r["stall"]), len(rows)),
              "n_stall", "n_runs", "stall_wilson_lo", "stall_wilson_hi"),
    "p_go": (lambda rows: _defined(rows, "p_go"), "p_go_k", "p_go_n", "p_go_wilson_lo", "p_go_wilson_hi"),
    "p_clean": (lambda rows: _defined(rows, "p_clean"), "p_clean_k", "p_clean_n",
                "p_clean_wilson_lo", "p_clean_wilson_hi"),
    "clean_given_go": (lambda rows: _defined(rows, "clean_given_go"), None, "clean_given_go_n",
                       "clean_given_go_wilson_lo", "clean_given_go_wilson_hi"),
}
COLUMNS = ("campaign", "table", "key", "quantity", "k", "n", "lo", "hi")


def _member(z, listed, member):
    data = z.read(member)
    if hashlib.sha256(data).hexdigest() != listed[member]:
        raise ValueError(f"{member} does not hash to the bundle's MANIFEST")
    return data


def extract(certification_paths, bundles_dir):
    """Every certification record given (one per campaign); the fixture names each by repo-relative path and hash."""
    certification_paths = [Path(p) for p in certification_paths]
    occurrences, bundles = [], {}
    entries = []
    for path in certification_paths:
        certification = json.loads(path.read_text())
        entries += [(campaign, family, entry) for campaign, families in certification["campaigns"].items()
                    for family, entry in families.items()]
    for campaign, family, entry in entries:
        # F30: the aggregator's rows are derived members; a certification
        # entry with a derived bundle names it, an older one holds them in
        # the mixed bundle; since F36 one derived bundle holds every family
        # of the campaign, so an entry is (campaign, family)
        pinned = entry.get("derived_bundle") or entry["bundle"]
        zip_path = Path(bundles_dir) / Path(pinned["path"]).name
        if not zip_path.is_file() or cb.zip_sha256(zip_path) != pinned["zip_sha256"]:   # the sidecar-backed hash (F71)
            raise ValueError(f"{campaign}: the certified bundle is not at {zip_path} with the pinned hash")
        with zipfile.ZipFile(zip_path) as z:
            prefix = f"{campaign}/"
            manifest = z.read(prefix + "MANIFEST.sha256").decode()
            listed = dict(line.split("  ", 1)[::-1] for line in manifest.splitlines())
            content_id = hashlib.sha256(manifest.encode()).hexdigest()
            if content_id != pinned["content_id_sha256"]:
                raise ValueError(f"{campaign}: bundle content id differs from the certification's pin")
            base = f"{prefix}{family}/flow_stats/"
            runs = [json.loads(line) for line in _member(z, listed, base + "flow_runs.jsonl").decode().splitlines()]
            figure = json.loads(_member(z, listed, base + "flow_figure_data.json"))
            # the reference table exists only when a reference model ran (a campaign without one, v06
            # on, declares reference_model null and the aggregator writes no reference rows)
            tables = {name: spec for name, spec in TABLES.items()
                      if spec[1] != "reference" or figure["reference_models"]}
            members = {name: listed[base + name] for name in list(tables) + ["flow_runs.jsonl", "flow_figure_data.json"]}
            populations = {"all": runs,
                           "pooled": [r for r in runs if r["model"] in set(figure["pooled_models"])],
                           "reference": [r for r in runs if r["model"] in set(figure["reference_models"])]}
            if set(figure["pooled_models"]) | set(figure["reference_models"]) != {r["model"] for r in runs}:
                raise ValueError(f"{campaign}: figure_data's model split does not cover the rows' models")
            for table, (key_fields, population) in tables.items():
                rows = [json.loads(line) for line in _member(z, listed, base + table).decode().splitlines()]
                groups = {}
                for r in populations[population]:
                    groups.setdefault(tuple(r[f] for f in key_fields), []).append(r)
                if {tuple(row[f] for f in key_fields) for row in rows} != set(groups):
                    raise ValueError(f"{campaign}/{table}: the table's groups are not the rows' groups")
                for row in rows:
                    key = tuple(row[f] for f in key_fields)
                    group = groups[key]
                    if row["n_runs"] != len(group):
                        raise ValueError(f"{campaign}/{table}/{key}: n_runs {row['n_runs']} != {len(group)} rows")
                    for quantity, (derive, k_field, n_field, lo_field, hi_field) in QUANTITIES.items():
                        k, n = derive(group)
                        if row[n_field] != n or (k_field is not None and row[k_field] != k):
                            raise ValueError(f"{campaign}/{table}/{key}/{quantity}: printed counts "
                                             f"({row.get(k_field)}, {row[n_field]}) != rows' ({k}, {n})")
                        if not n:
                            if row[lo_field] is not None:
                                raise ValueError(f"{campaign}/{table}/{key}/{quantity}: interval printed for n = 0")
                            continue
                        occurrences.append([campaign, table, list(key), quantity, k, n, row[lo_field], row[hi_field]])
        bundles[f"{campaign}/{family}"] = {
            "zip_sha256": pinned["zip_sha256"], "content_id_sha256": content_id,
            "raw_content_id_sha256": entry["bundle"]["content_id_sha256"],
            "campaign": campaign, "family": family, "members": members, "runs": len(runs),
            "pooled_models": figure["pooled_models"], "reference_models": figure["reference_models"]}
    return {"fixture_kind": FIXTURE_KIND, "extractor_sha256": sha(Path(__file__)),
            "certification": [{"path": portable_path(p, ROOT), "sha256": sha(p)} for p in certification_paths],
            "bundles": bundles, "tables": list(TABLES), "quantities": list(QUANTITIES),
            "interval_decimals": 3, "columns": list(COLUMNS),
            "occurrences_count": len(occurrences),
            "distinct_pairs": len({(o[4], o[5]) for o in occurrences}),
            "occurrences": occurrences}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--certification", required=True, type=Path, nargs="+",
                    help="the flow certification records (one per campaign)")
    ap.add_argument("--bundles-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    fixture = extract(args.certification, args.bundles_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=None, separators=(",", ":")) + "\n")
    print(f"{fixture['occurrences_count']} printed intervals over {fixture['distinct_pairs']} distinct (k, n), "
          f"counts derived from {sum(b['runs'] for b in fixture['bundles'].values())} bundled rows "
          f"in {len(fixture['bundles'])} certified bundles -> {args.out}")


if __name__ == "__main__":
    main()
