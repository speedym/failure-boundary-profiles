#!/usr/bin/env python3
"""Family-aware scoring of Isaac campaign runs (mirror of
adapters/fail2drive/scripts/multi_model_eval/score_runs.py).

Turns a campaign run tree into labeled outcomes joined to certified
geometry: one row per (variant, agent, rep), plus per-agent aggregates,
the brain's boundary estimate, and an optional machine-readable
FindingsSummary (the cross-family reuse artifact, ledger S14). Offline,
stdlib-only; run under the scl_prototype env.

    conda run -n scl_prototype python \\
        adapters/isaac/scripts/multi_model_eval/score_runs.py \\
        ~/scl/campaign_isaac_v01 \\
        --family artifacts/examples/solved_families/isaac/fm_isaac_001 \\
        --findings fs_out.json

SCORING POLICY (the complete list of judgment rules; anything not listed
is mechanical fact from the ledgers. Every output repeats this block so
no table circulates without its policy provenance).
- Outcomes are read VERBATIM from run footers (route class x contact
  state, timeout split out by the runner); this tool never re-derives
  them from ticks.
- Conduct envelope is CONTACT-FREE (Milan, 2026-08-30): any touch
  contact is a failure. Grazes are NOT contacts - the impulse filter
  suppresses phantom contact reports (the contactOffset envelope), it is
  not leniency.
- Two success booleans per row, never collapsed:
    reached_clean  = goal reached with zero touch contacts within the
                     budget, by ANY route (commit or divert);
    threaded_clean = outcome commit_clean exactly (traversed the gap).
- Agent correctness vs the CERTIFIED assignment: passable ->
  threaded_clean OR (reached_clean via divert - legal but recorded as
  divert); blocked -> reached_clean via divert (threading is certified
  impossible). The expert reference row is expected correct everywhere.
- A commit_clean footer on a BLOCKED variant is recoded to
  anomaly_blockage_completed (the CARLA class, 2026-10-03): excluded from
  p_go, p_clean and passage by findings_v2, kept in the outcome
  composition, counted in decomposition.anomalies. The certificate stands;
  the engine let the body through (F61).
- The boundary curve quantity is threaded_clean rate over PASSABLE
  variants ordered by certified width. Boundary bracket = the adjacent
  scheduled widths between which the rate crosses 0.5; transition band =
  widths with mixed outcomes (0 < rate < 1). Reps are samples of GPU
  nondeterminism, not seeds.
- Wilson intervals at z=1.96, kept local (mirrors
  boundary_stats.WILSON_Z; flow_aggregate precedent).
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brain_models import cell_dir_name, cell_tag  # noqa: E402

POLICY = __doc__.split("SCORING POLICY")[1]

# the ONE estimator (S15, promoted 2026-09-07): this scorer's Wilson
# interval is the shared formula rounded to 4 decimals - the rounding
# keeps every v0.1 scored document byte-identical to its pre-promotion
# vintage (proven by re-scoring the frozen campaigns)
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from scl_core.stats.boundary import WILSON_Z, wilson as _wilson_shared  # noqa: E402


def wilson(k: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    lo, hi = _wilson_shared(k, n, z)
    return (round(lo, 4), round(hi, 4))


def read_ledger(path: Path):
    header = footer = refusal = None
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = rec.get("type", "")
        if t.endswith("_run_header"):
            header = rec
        elif t.endswith("_run_footer"):
            footer = rec
        elif t == "refusal":
            refusal = rec
    return header, footer, refusal


ANOMALY_BLOCKAGE_COMPLETED = "anomaly_blockage_completed"


def score_row(variant, agent, rep, footer):
    outcome = footer.get("outcome", "missing")
    # A contact-free completion of a certified-BLOCKED gap is impossible by the certificate (the
    # robot's measured width against the measured gap); when the engine lets it happen it is a
    # simulator finding, never a pass: the CARLA taxonomy's anomaly_blockage_completed, excluded
    # from every curve and counted (parity, 2026-10-03; the first instance is v07's two carton
    # cells at 0.45 m, ledger F61). The runner's verbatim outcome stays in the derived row.
    if variant["assignment"] == "blocked" and outcome == "commit_clean":
        outcome = ANOMALY_BLOCKAGE_COMPLETED
    route = outcome.rsplit("_", 1)[0] if "_" in outcome else outcome
    contact_state = outcome.rsplit("_", 1)[1] if "_" in outcome else "unknown"
    reached_clean = route in ("commit", "divert") and contact_state == "clean"
    threaded_clean = outcome == "commit_clean"
    correct = (threaded_clean or (reached_clean and route == "divert")) \
        if variant["assignment"] == "passable" \
        else (reached_clean and route == "divert")
    return {
        "variant": variant["stem"],
        "width_m": variant["target_width_m"],
        "assignment": variant["assignment"],
        "agent": agent, "rep": rep,
        "outcome": outcome,
        "reached_clean": reached_clean,
        "threaded_clean": threaded_clean,
        "correct_vs_assignment": correct,
        "t_goal_s": footer.get("t_goal_s"),
        "first_touch_contact_s": footer.get("first_touch_contact_s"),
        "n_touch_contacts": footer.get("n_touch_contacts"),
        "n_grazes": footer.get("n_grazes"),
    }



def summary_table(fam_variants, aggregates, agents):
    """Console summary, one line per family variant in MANIFEST order; each
    cell is looked up by variant stem in the agent's aggregate table (the
    aggregates follow the declared-grid order, which need not be the
    manifest order - the 2026-09-08 descending-schedule lesson: an index
    join printed the closed aisle's cells under the 2.5 m row)."""
    lines = [f"{'variant':<14}{'width':>6}  {'assign':<9}"
             + "".join(f"{a + ' clean':>18}" for a in agents)]
    by_stem = {a: {t["variant"]: t for t in aggregates.get(a, [])} for a in agents}
    for v in fam_variants:
        cells = []
        for a in agents:
            t = by_stem[a].get(v["stem"])
            if t is None:
                cells.append("-"); continue
            lo, hi = t["wilson_95"]
            cells.append(f"{t['threaded_clean']}/{t['n']} [{lo:.2f},{hi:.2f}]")
        lines.append(f"{v['stem']:<14}{v['target_width_m']:>6}  "
                     f"{v['assignment']:<9}" + "".join(f"{c:>18}" for c in cells))
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--family", required=True, type=Path)
    ap.add_argument("--campaign-id", default=None,
                    help="the campaign this tree belongs to, from its declaration, for a tree that carries no "
                         "campaign_declaration.json (a pre-F18 bundle declared after the fact); otherwise the tree's own")
    ap.add_argument("--out", type=Path, default=None,
                    help="scored rows + aggregates JSON "
                         "(default: <run_dir>/scored_runs.json)")
    ap.add_argument("--findings", type=Path, default=None,
                    help="also emit a FindingsSummary document here")
    args = ap.parse_args()

    fam = json.loads((args.family / "family_manifest.json").read_text())
    axis = fam.get("axis", "unknown_axis")
    if not all("target_width_m" in v for v in fam["variants"]):
        sys.exit(
            "ERROR: this scorer supports single-numeric-axis family "
            "manifests (per-variant target_width_m). This manifest's "
            f"axis '{axis}' has another shape - extend score_runs "
            "rather than mis-scoring (fail closed, never guess).")
    meta_path = args.run_dir / "campaign_meta.json"
    meta_grid, meta = None, None
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        meta_grid = meta.get("declared_grid")
    declaration_path = args.run_dir / "campaign_declaration.json"
    if declaration_path.is_file():
        # F18: the grid is the declaration the runner wrote at invocation
        # (campaign_declaration/v0.2); the expert is one of the models
        from scl_core.campaign_declaration import load_declaration
        declaration = load_declaration(declaration_path)
        brains = [m for m in declaration.models if m != declaration.reference_model]
        reps = list(declaration.repetitions)
        expect_expert = declaration.expert
        by_stem = {v["stem"]: v for v in fam["variants"]}
        unknown = [s for s in declaration.variants if s not in by_stem]
        if unknown:
            sys.exit(f"ERROR: declared variants absent from the family "
                     f"manifest: {unknown}")
        variants = [by_stem[s] for s in declaration.variants]
    elif meta_grid is not None:
        # F2 contract: grid policy is DECLARED at invocation and
        # recorded in campaign_meta.json; family manifests are evidence
        # (stages, certs, hashes), never grid policy. The legacy branch
        # reads pre-F2 manifests that still baked the policy in.
        reps = list(range(meta_grid["reps_per_brain"]))
        brains = list(meta_grid["brains"])
        expect_expert = bool(meta_grid.get("expert", True))
        by_stem = {v["stem"]: v for v in fam["variants"]}
        unknown = [s for s in meta_grid["variants"] if s not in by_stem]
        if unknown:
            sys.exit(f"ERROR: declared variants absent from the family "
                     f"manifest: {unknown}")
        variants = [by_stem[s] for s in meta_grid["variants"]]
    elif "n_reps_per_brain" in fam:
        reps = list(range(fam["n_reps_per_brain"]))
        brains = fam["brains"]
        expect_expert = True
        variants = fam["variants"]
    else:
        sys.exit("ERROR: no grid declaration - the run tree has no "
                 "campaign_meta.json with a declared_grid "
                 "(campaign_meta/v0.1) and the family manifest carries "
                 "no legacy policy fields (n_reps_per_brain, brains); "
                 "exactly one of the two shapes must declare the grid")
    rows, ckpts = [], {}   # ckpts: brain -> set of shas
    for v in variants:
        cells = [("expert", "expert", "expert", None)] \
            if expect_expert else []
        for brain in brains:
            cells += [(cell_dir_name(brain, k), cell_tag(brain, k), brain, k)
                      for k in reps]
        for dname, tag, agent, rep in cells:
            ledger = args.run_dir / v["stem"] / dname / \
                f"{v['stem']}_{tag}.jsonl"
            if not ledger.is_file():
                print(f"WARNING: missing ledger {ledger}", file=sys.stderr)
                continue
            header, footer, refusal = read_ledger(ledger)
            if footer is None:
                print(f"WARNING: no footer in {ledger} "
                      f"(refusal: {refusal})", file=sys.stderr)
                continue
            rows.append(score_row(v, agent, rep, footer))
            bm = (header or {}).get("brain_manifest")
            if bm:
                ckpts.setdefault(agent, set()).add(bm.get("ckpt_sha256"))
            elif meta and str(meta.get("meta_kind", "")).startswith("campaign_meta/v0.") \
                    and meta.get("meta_kind") not in ("campaign_meta/v0.1", "campaign_meta/v0.2"):
                # F18: the identity lives once in the meta; the header
                # points at it by fingerprint
                sha = meta.get("agent_identity", {}).get(agent, {}).get("weights", {}).get("sha256")
                if sha:
                    ckpts.setdefault(agent, set()).add(sha)

    # ---- aggregates -----------------------------------------------------
    def agg(agent):
        table = []
        for v in variants:
            grp = [r for r in rows
                   if r["agent"] == agent and r["variant"] == v["stem"]]
            n = len(grp)
            thread = sum(r["threaded_clean"] for r in grp)
            correct = sum(r["correct_vs_assignment"] for r in grp)
            lo, hi = wilson(thread, n)
            table.append({
                "variant": v["stem"], "width_m": v["target_width_m"],
                "assignment": v["assignment"], "n": n,
                "threaded_clean": thread,
                "threaded_clean_rate": round(thread / n, 4) if n else None,
                "wilson_95": [lo, hi],
                "correct_vs_assignment": correct,
                "outcomes": sorted(r["outcome"] for r in grp),
            })
        return table

    aggregates = {a: agg(a) for a in
                  ((["expert"] if expect_expert else []) + brains)}

    def boundary(table):
        passable = sorted((t for t in table if t["assignment"] == "passable"),
                          key=lambda t: t["width_m"])
        bracket = None
        for a, b in zip(passable, passable[1:]):
            if a["threaded_clean_rate"] < 0.5 <= b["threaded_clean_rate"]:
                bracket = [a["width_m"], b["width_m"]]
        mixed = [t["width_m"] for t in passable
                 if 0 < t["threaded_clean_rate"] < 1]
        return {"bracket_50pct_m": bracket,
                "transition_band_m": ([min(mixed), max(mixed)]
                                      if mixed else None)}

    # the campaign this family belongs to: the tree's own declaration (F18); a
    # pre-F18 tree was named for its campaign (found by the F39 smoke: under
    # <campaign>/<family>/ the directory name is the family, and the emitter's
    # fallback wrote it as the campaign id)
    declaration_path = args.run_dir / "campaign_declaration.json"
    if declaration_path.is_file():
        campaign_id = json.loads(declaration_path.read_text())["campaign_id"]
    elif args.campaign_id:
        campaign_id = args.campaign_id   # the declaration written after the fact names it (score_campaign.py)
    else:
        campaign_id = args.run_dir.name
    result = {
        # the run tree this family was scored from, as the declaration names it: <campaign id>/<family dir>,
        # relative to the results root; never the checkout's absolute path (F70, 2026-10-08: a derived member
        # that names where it was computed is not the same member twice)
        "results_subtree": f"{campaign_id}/{args.run_dir.name}",
        "campaign": {"id": campaign_id, "family_id": fam["family_id"]},
        "family_id": fam["family_id"],
        "scoring_policy": POLICY.strip(),
        # instrument identity (parity with the fail2drive scorer's
        # scoring_provenance.json): derived tables name the scorer that
        # produced them, so staleness is detectable, never silent
        "scoring_provenance": {
            "provenance_kind": "scoring_provenance/v0.1",
            "scorer_file": Path(__file__).name,
            "scorer_sha256": hashlib.sha256(
                Path(__file__).read_bytes()).hexdigest(),
            "family_manifest_sha256": hashlib.sha256(
                (args.family / "family_manifest.json").read_bytes()
            ).hexdigest(),
        },
        "rows": rows,
        "aggregates": aggregates,
        "boundary": {b: boundary(aggregates[b]) for b in brains},
        "checkpoint_shas": {b: sorted(x for x in v if x)
                            for b, v in ckpts.items()},
    }
    out = args.out or (args.run_dir / "scored_runs.json")
    out.parent.mkdir(parents=True, exist_ok=True)   # the driver names <out>/<campaign>/<family>/scoring_out/ (found by the F39 smoke)
    out.write_text(json.dumps(result, indent=1) + "\n")

    # ---- table ----------------------------------------------------------
    for line in summary_table(fam["variants"], aggregates, ["expert"] + brains):
        print(line)
    for b in brains:
        print(f"{b} boundary: {result['boundary'][b]}")
    exp_correct = sum(t["correct_vs_assignment"] for t in aggregates["expert"])
    print(f"expert correct vs assignment: {exp_correct}/"
          f"{len(aggregates['expert'])}")
    print(f"scored: {out}")

    # ---- FindingsSummary (ledger S14 cross-family reuse artifact) -------
    if args.findings:
        cm_path = args.run_dir / "campaign_manifest.json"
        multi = len(brains) > 1
        for brain in brains:
          out_path = (args.findings if not multi else
                      args.findings.with_name(
                          f"{args.findings.stem}_{brain}"
                          f"{args.findings.suffix}"))
          fs = {
            "findings_kind": "findings_summary/v0.1",
            "campaign": {
                "id": args.run_dir.name,
                "manifest_sha256": (hashlib.sha256(
                    cm_path.read_bytes()).hexdigest()
                    if cm_path.is_file() else None),
                "family_id": fam["family_id"],
                "family_manifest_sha256": hashlib.sha256(
                    (args.family / "family_manifest.json")
                    .read_bytes()).hexdigest(),
            },
            "brain": {"id": brain,
                      "ckpt_sha256": (sorted(ckpts.get(brain, set()))[0]
                                      if ckpts.get(brain) else None)},
            "scoring_policy": POLICY.strip(),
            "findings": [
                {"finding_id": f"fnd_{brain}_clean_rate_curve",
                 "quantity": f"threaded_clean_rate vs {axis}",
                 "unit_x": "m",
                 "points": [{"width_m": t["width_m"],
                             "assignment": t["assignment"],
                             "n": t["n"], "k": t["threaded_clean"],
                             "rate": t["threaded_clean_rate"],
                             "wilson_95": t["wilson_95"]}
                            for t in aggregates[brain]]},
                {"finding_id": f"fnd_{brain}_boundary_bracket",
                 "quantity": f"behavioral_boundary_{axis}",
                 "interval_m": result["boundary"][brain]["bracket_50pct_m"],
                 "criterion": "0.5 threaded_clean-rate crossing between "
                              "adjacent scheduled widths; intervals, "
                              "never points"},
                {"finding_id": f"fnd_{brain}_transition_band",
                 "quantity": f"mixed_outcome_band_{axis}",
                 "interval_m": result["boundary"][brain]["transition_band_m"],
                 "criterion": "widths with 0 < threaded_clean rate < 1 "
                              "(GPU-nondeterministic outcome flipping)"},
                {"finding_id": "fnd_expert_reference",
                 "quantity": "expert correct_vs_assignment",
                 "value": f"{exp_correct}/{len(aggregates['expert'])}",
                 "criterion": "privileged reference driver; blocked -> "
                              "divert_clean, passable -> commit_clean"},
            ],
        }
          out_path.write_text(json.dumps(fs, indent=1) + "\n")
          print(f"findings: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
