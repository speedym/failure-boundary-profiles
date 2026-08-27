# analysis/ — scoring, curves, boundary fits, figures, trajectories (MIT)

Post-run analysis for the failure-boundary families: evaluator output to labeled outcomes, empirical commitment/competence curves, boundary fits with honest uncertainty, figures, and recorder-based trajectory metrics. `score_runs`, `curves`, and `trajectory_metrics` are stdlib-only (Python 3.8+); `boundary_stats` needs numpy, `figures` needs matplotlib, `extract_recorders` needs the carla client wheel (see `requirements.txt`).

## Input contract

Point it at one or more run directories shaped like:

```
<run_dir>/<model>/seed_<k>/<scenario_id>.json    # leaderboard checkpoint files
<run_dir>/<model>/<scenario_id>.json             # legacy layout, seed 0
```

`viz/` and `recordings/` subtrees are ignored. Launch however you like (SLURM, local, containers) — only the output shape matters.

## Usage

```bash
python -m analysis.score_runs <run_dir> [<run_dir> ...] --out scored/
python -m analysis.curves scored/scored_rows.jsonl --out scored/
```

`score_runs` writes `scored_rows.{csv,jsonl}` (one row per run: outcome, attributed collision counts, measured geometry, driving score) and `scored_tables.txt` (human-readable per-family tables; every output embeds the full scoring policy, so no table circulates without its provenance). `curves` writes `curves.csv`: observed commitment P(go) and competence P(clean pass) per measured axis value, with n.

## What makes the scoring family-aware

- **Positional collision attribution**: infraction events are attributed to placed obstacles only when blueprint AND position match, against countersigned poses and measured extents shipped in each family's `attribution.json`. Traffic contacts never pollute the obstacle curves; unattributable events are reported as `ambiguous`, never silently binned. No-fallback contract: a scenario without attribution data has attribution disabled, loudly.
- **Passable vs blockage semantics** from the catalogs (`assigned_family`): refusal is coded `refusal` on passable variants and `correct_stop` on blockages — the outcome vocabulary is the two-curve construct's foundation.
- **A row is one run** — a sample, never "the" value for a (variant, seed). Aggregations report n.

## Fidelity

This is a direct port of the internal scorer used for the paper's campaigns. Validation: 96/96 rows of a held-out campaign slice (fm_001, seeds 1–2, four agents) reproduce the internal outputs exactly — outcome, driving score, and per-class collision counts.

## Boundary fitting and figures

```bash
python -m analysis.boundary_stats scored/ --out stats/                    # numpy
python -m analysis.figures --scored scored/ --stats stats/ --out figs/    # matplotlib
```

`boundary_stats` emits per-(family, model, curve) fits under the honesty tree: flat (no boundary in the tested range — a first-class result), bracket under complete separation, logistic IRLS with a quasi-separation guard; Wilson intervals everywhere, seeded bootstrap (B=10000) ONLY where some stratum has n>1, separation fraction reported. The full policy is embedded in every output. Fidelity: on the pooled fm_001 slice, all 8 curve fits reproduce the internal pipeline exactly, INCLUDING bootstrap CIs to six decimals (same seeded RNG). `figures` renders per-family panels that draw only what each method claims — no logistic curve for guarded fits, no boundary line for flats.

## Trajectory layer

```bash
python -m analysis.extract_recorders <run_dir> --port 2000    # carla wheel + a running server
python -m analysis.trajectory_metrics <run_dir> --out metrics/    # stdlib, offline
```

`extract_recorders` parses recorder logs (`recordings/*.log`) into compact per-run trajectory extracts; `trajectory_metrics` turns extracts into one row per run — oriented-box clearances against tracked AND placed obstacle poses, TTC, wobble (expert-baselined threshold), creep, stop offset, ego-attributed obstacle displacement, traffic clearances with measured-or-excluded extents — joinable with `scored_rows` on (family, model, seed, scenario_id). Enable the recorder in your runs to use this layer. Fidelity: 96/96 rows of the same held-out slice reproduce the internal pipeline exactly on every shared field; the one deliberate divergence (`mirror_band`, capability-profile-dependent) is documented in the policy header. `crossing_headways()` ships as the experimental realized-gap instrument for future traffic-flow families.

## Roadmap

- Headway CLI and flow-family joins, once the first dynamic-actor family lands.

## License

MIT (this directory). Scenario data and catalogs remain CC BY 4.0; the bundled harness remains MIT, as upstream.
