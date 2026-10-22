#!/usr/bin/env python3
"""Compare two interpretations of a campaign: is the difference lineage only, or did a number move?

Every time a producer changes, the interpretations built with the old one are re-issued, and one
question decides what that re-issue is: did a number move (an INSTRUMENT CHANGE: a real
re-certification, the golds may move) or only the paperwork (a RE-STAMP: the same numbers under
new hashes and identities)? The certifiers hash whole files and say "changed", never where. This
tool walks every member of two interpretation FOLDERS (F34, ledger; the verb `scl campaign
compare-lineages`), puts each member in a class, compares the content classes row by row or leaf
by leaf with the provenance fields set aside, and writes one report (lineage_comparison/v0.1):

    member table    per class: identical, differing, only in A, only in B, content moved
    differences     per content member that moved: the rows or leaves, old and new value
    verdict         RE-STAMP (no content moved) or INSTRUMENT CHANGE (a content field moved);
                    NEW MATERIAL (members in B that A never had) is reported beside, never a
                    verdict on its own

Each side is named the way the store names interpretations (principle 12): a campaign id (the
interpretation its records pin), `<campaign>@<content id>` (a specific one, the archive's
superseded folders included), a derived zip (unpacked and verified into the cache), or a folder
with the interpretation's layout (a scoring driver's output, a staged tree). The comparison reads
folders only. Two different campaign ids refuse unless --across-campaigns (then everything is
new material by class, a shape comparison, never a verdict on numbers).

    scl campaign compare-lineages --a campaign_carla_v06 --b artifacts/runs/rescore/scores/campaign_carla_v06
    scl campaign compare-lineages --a ~/scl/campaigns/superseded_2026-09-19/campaign_carla_v06_derived.zip \\
        --b campaign_carla_v06 --out /tmp/v06_flow_window.json

Exit 0 on RE-STAMP, 1 on INSTRUMENT CHANGE (0 with --accept-instrument-change, which records the
acceptance in the report; `freeze-derived --against` uses it), 2 on a refusal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scl_core import results_store as store  # noqa: E402

REPORT_KIND = "lineage_comparison/v0.1"

# A field whose name carries one of these is PROVENANCE: expected to move on every re-issue, set
# aside when content is compared and reported as identity. The list this session's ad hoc
# comparisons converged on (2026-09-19..21); a constant with a test, not a judgment per re-issue.
PROVENANCE_TOKENS = ("sha", "provenance", "scorer", "source", "path", "version", "stamp", "file", "emitter",
                     "estimator", "tool", "bootstrap_seed", "date", "git", "runtime", "pooling", "run_pins",
                     "instrument_change", "canon_inputs", "writer", "bundler", "generator", "driver", "note",
                     "campaign_dir", "_dir", "written_after_the_fact", "kind")

# The class table: (class, member pattern, mode, row keys). Mode `rows`: a JSONL compared row by
# row keyed by the fields listed (the ones present); `json`: a document compared leaf by leaf;
# `hash`: compared by hash only, reported, never a verdict. The one member table of every chain (F82):
# scoring_out/run_rows.jsonl, stats/stats_rows.jsonl, findings/, the threading route metrics under
# metrics/, the flow audit under audit/.
CLASSES = (
    ("run_rows", r"scoring_out/run_rows\.jsonl$", "rows", ("family", "model", "variant", "repetition")),
    ("stats", r"stats/stats_rows\.jsonl$", "rows", ("family", "model", "curve")),
    ("metrics", r"metrics/metrics_rows\.jsonl$", "rows", ("family", "model", "seed", "scenario_id")),
    ("evidence_rows", r"audit/(commitment_audit_rows|map_check_recordings)\.json$", "json", ()),
    ("summaries", r"audit/(commitment_audit|map_verification)\.json$", "json", ()),
    ("findings", r"findings/fs_.*\.json$", "json", ()),
    ("text", r"\.(txt|csv|md)$", "hash", ()),
    ("provenance", r"scoring_provenance\.json$|stats_provenance\.json$|metrics_provenance\.json$|bundle_provenance\.json$"
                   r"|MANIFEST\.sha256$|source_manifest\.json$|scoring_record\.json$|lineage_comparison.*\.json$"
                   r"|^source_snapshot/|/source_snapshot/", "hash", ()),   # v0.1 bundles carried source copies
)
CONTENT_CLASSES = ("run_rows", "stats", "metrics", "evidence_rows", "summaries", "findings")
VERDICTS = ("RE-STAMP", "INSTRUMENT CHANGE")


def classify(rel: str) -> tuple:
    for name, pattern, mode, keys in CLASSES:
        if re.search(pattern, rel):
            return name, mode, keys
    return "other", "hash", ()


def is_provenance(key: str) -> bool:
    k = key.lower()
    return any(t in k for t in PROVENANCE_TOKENS)


def leaves(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if is_provenance(str(k)):
                continue
            yield from leaves(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def diff_json(a, b) -> list:
    """Leaves that differ, each with its kind: `changed` (both sides, different values), `added`
    (only in B: new material at leaf level, a field the producer gained) or `removed` (only in A)."""
    la, lb = dict(leaves(a)), dict(leaves(b))
    out = []
    for k in sorted(set(la) | set(lb)):
        if k not in la:
            out.append({"leaf": k, "kind": "added", "a": "<absent>", "b": lb[k]})
        elif k not in lb:
            out.append({"leaf": k, "kind": "removed", "a": la[k], "b": "<absent>"})
        elif la[k] != lb[k]:
            out.append({"leaf": k, "kind": "changed", "a": la[k], "b": lb[k]})
    return out


def read_rows(path: Path) -> list:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def row_key(row: dict, keys: tuple) -> str:
    return "/".join(f"{k}={row.get(k)}" for k in keys if k in row) or json.dumps(row, sort_keys=True)[:80]


def diff_rows(pa: Path, pb: Path, keys: tuple) -> list:
    ia = {row_key(r, keys): r for r in read_rows(pa)}
    ib = {row_key(r, keys): r for r in read_rows(pb)}
    out = []
    for k in sorted(set(ia) | set(ib)):
        if k not in ia:
            out.append({"row": k, "kind": "added"})
        elif k not in ib:
            out.append({"row": k, "kind": "removed"})
        else:
            for d in diff_json(ia[k], ib[k]):
                out.append({"row": k, **d})
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def label(folder: Path) -> dict:
    """What a side depends on: campaign id, content id when known, the producers' commit, the
    scorers' hashes. Read from the folder's own documents; absent ones are null, never guessed."""
    out = {"folder": str(folder), "campaign_id": folder.name, "content_id_sha256": None, "git_commit": None,
           "scorer_sha256": sorted({})}
    prov = folder / "bundle_provenance.json"
    if prov.is_file():
        doc = json.loads(prov.read_text())
        out["campaign_id"] = doc.get("campaign_id", out["campaign_id"])
        out["content_id_sha256"] = doc.get("manifest_sha256")
        out["git_commit"] = ((doc.get("source_snapshot") or {}).get("git") or {}).get("commit")
    manifest = folder / "source_snapshot" / "source_manifest.json"
    if out["git_commit"] is None and manifest.is_file():
        out["git_commit"] = (json.loads(manifest.read_text()).get("git") or {}).get("commit")
    scorers = set()
    for stamp in folder.rglob("scoring_provenance.json"):
        doc = json.loads(stamp.read_text())
        scorers.add((doc.get("scorer") or {}).get("sha256") or doc.get("scorer_sha256"))   # the one stamp (v0.2) or a superseded bundle's
    out["scorer_sha256"] = sorted(s for s in scorers if s)
    return out


def resolve(spec: str, root: Path, archive, cache) -> Path:
    """A side by campaign id, `<campaign>@<content id>`, a derived zip, or a folder (see the module doc)."""
    p = Path(spec).expanduser()
    if p.is_dir():
        return p.resolve()
    if p.is_file() and p.suffix == ".zip":
        return store.interpretation_from_zip(p, cache)
    if "@" in spec:
        campaign, content_id = spec.split("@", 1)
        if len(content_id) < 12:
            raise store.StoreError(f"{spec}: a content id needs at least 12 hex digits")
        return store.interpretation_by_id(root, campaign, content_id, archive, cache)
    if re.fullmatch(r"campaign_[a-z0-9_]+", spec):
        return store.interpretation(root, spec, archive, cache)
    raise store.StoreError(f"{spec!r} is neither a campaign id, <campaign>@<content id>, a derived zip nor a folder")


def compare(a: Path, b: Path, across_campaigns: bool = False) -> dict:
    fa = {p.relative_to(a).as_posix(): p for p in a.rglob("*") if p.is_file()}
    fb = {p.relative_to(b).as_posix(): p for p in b.rglob("*") if p.is_file()}
    for side, files, folder in (("a", fa, a), ("b", fb, b)):
        if not any(classify(rel)[0] in CONTENT_CLASSES for rel in files):
            raise ValueError(f"side {side} ({folder}) has no member of a content class "
                             f"({', '.join(CONTENT_CLASSES)}): not an interpretation folder")
    la, lb = label(a), label(b)
    if la["campaign_id"] != lb["campaign_id"] and not across_campaigns:
        raise ValueError(f"the sides are interpretations of different campaigns ({la['campaign_id']} and "
                         f"{lb['campaign_id']}); a lineage is per campaign (--across-campaigns compares shapes only)")
    table, moved, added_fields = {}, {}, {}
    for rel in sorted(set(fa) | set(fb)):
        cls, mode, keys = classify(rel)
        t = table.setdefault(cls, {"identical": 0, "differ": 0, "only_a": 0, "only_b": 0, "content_moved": 0})
        if rel not in fa:
            t["only_b"] += 1
            continue
        if rel not in fb:
            t["only_a"] += 1
            continue
        if sha256_file(fa[rel]) == sha256_file(fb[rel]):
            t["identical"] += 1
            continue
        t["differ"] += 1
        if mode == "hash" or cls not in CONTENT_CLASSES:
            continue
        if mode == "rows":
            d = diff_rows(fa[rel], fb[rel], keys)
        else:
            d = diff_json(json.loads(fa[rel].read_text()), json.loads(fb[rel].read_text()))
        kinds = {}
        for x in d:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        if kinds.get("changed") or kinds.get("removed"):   # a value moved or vanished: content moved
            t["content_moved"] += 1
            moved[rel] = {"class": cls, "count": len(d), "kinds": kinds,
                          "differences": [x for x in d if x["kind"] != "added"][:200]}
        elif kinds.get("added"):                             # fields or rows the producer gained: new material
            added_fields[rel] = {"class": cls, "added": kinds["added"],
                                 "examples": [x.get("leaf") or x.get("row") for x in d[:5]]}
    new_members = [rel for rel in sorted(set(fb) - set(fa)) if classify(rel)[0] in CONTENT_CLASSES]
    verdict = "INSTRUMENT CHANGE" if moved else "RE-STAMP"
    return {"report_kind": REPORT_KIND, "a": la, "b": lb, "across_campaigns": across_campaigns,
            "table": dict(sorted(table.items())), "content_moved": moved,
            "new_material": {"members_only_in_b": new_members, "fields_added": added_fields}, "verdict": verdict,
            "provenance_tokens": list(PROVENANCE_TOKENS), "tool_sha256": sha256_file(Path(__file__).resolve()),
            "note": ("RE-STAMP: every content member identical, or moved only in provenance fields or by GAINING "
                     "fields; INSTRUMENT CHANGE: a content value changed or vanished (rows or leaves named). "
                     "new_material lists content members only in B and fields the producers gained; it is "
                     "reported beside the verdict, never a verdict on its own")}


def render(report: dict) -> str:
    lines = [f"verdict: {report['verdict']}" + ("  (accepted)" if report.get("accepted") else ""),
             f"  a: {report['a']['campaign_id']} {str(report['a']['content_id_sha256'] or '-')[:12]} "
             f"commit {str(report['a']['git_commit'] or '-')[:8]}",
             f"  b: {report['b']['campaign_id']} {str(report['b']['content_id_sha256'] or '-')[:12]} "
             f"commit {str(report['b']['git_commit'] or '-')[:8]}"]
    for cls, t in report["table"].items():
        lines.append(f"  {cls:14} identical {t['identical']:3}  differ {t['differ']:3}  only A {t['only_a']:2}  "
                     f"only B {t['only_b']:2}  content moved {t['content_moved']}")
    for rel, m in report["content_moved"].items():
        first = m["differences"][0]
        where = (first.get("row") or "") + ("." if first.get("row") and first.get("leaf") else "") + (first.get("leaf") or "")
        lines.append(f"  MOVED {rel} ({m['class']}): {m['kinds']}, first {first['kind']} at {where}: "
                     f"{str(first.get('a', ''))[:60]} -> {str(first.get('b', ''))[:60]}")
    nm = report["new_material"]
    if nm["members_only_in_b"]:
        lines.append(f"  new material: {len(nm['members_only_in_b'])} member(s) only in B, e.g. {nm['members_only_in_b'][:3]}")
    for rel, f in nm["fields_added"].items():
        lines.append(f"  new fields in {rel} ({f['class']}): {f['added']}, e.g. {f['examples'][:3]}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--a", required=True, help="campaign id | <campaign>@<content id> | derived zip | folder")
    ap.add_argument("--b", required=True, help="the same forms")
    ap.add_argument("--out", type=Path, default=None, help="write the report (lineage_comparison/v0.1) here")
    ap.add_argument("--across-campaigns", action="store_true", help="compare the shapes of two campaigns' interpretations")
    ap.add_argument("--accept-instrument-change", action="store_true",
                    help="exit 0 on an INSTRUMENT CHANGE and record the acceptance in the report (the freeze hook)")
    ap.add_argument("--repo-root", type=Path, default=ROOT)
    ap.add_argument("--archive", type=Path, default=None, help="the derived zips (default: SCL_CAMPAIGN_BUNDLES or ~/scl/campaigns)")
    ap.add_argument("--cache", type=Path, default=None,
                    help="where interpretations unpack (default: <archive>/interpretations, beside the zips; F72)")
    args = ap.parse_args(argv)
    try:
        a = resolve(args.a, args.repo_root, args.archive, args.cache)
        b = resolve(args.b, args.repo_root, args.archive, args.cache)
        report = compare(a, b, args.across_campaigns)
    except (store.StoreError, ValueError, OSError) as exc:
        print(f"REFUSED: {exc}")
        return 2
    if report["verdict"] == "INSTRUMENT CHANGE" and args.accept_instrument_change:
        report["accepted"] = True
    print(render(report))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, default=str) + "\n")
        print(f"report: {args.out}")
    return 0 if report["verdict"] == "RE-STAMP" or report.get("accepted") else 1


if __name__ == "__main__":
    sys.exit(main())
