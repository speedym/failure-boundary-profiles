# analysis/ — scoring and curves (MIT)

Post-run analysis for the failure-boundary families: turn evaluator output into labeled outcomes and empirical commitment/competence curves. Stdlib-only (Python 3.8+), no CARLA, no GPU.

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

## Roadmap

- Boundary fitting with honest uncertainty (Wilson intervals, brackets under separation, bootstrap over seed strata) and per-family figures.
- Trajectory layer: recorder-based metrics — gate-crossing commitment evidence and realized-gap measurement for dynamic-actor families. Enable the recorder in your runs now if you want that analysis later.

## License

MIT (this directory). Scenario data and catalogs remain CC BY 4.0; the bundled harness remains MIT, as upstream.
