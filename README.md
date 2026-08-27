# Failure-Boundary Profiles: Scenario Families

Certified scenario families for measuring failure-boundary profiles of autonomous driving agents in CARLA. This repository is the public artifact of the paper "Beyond Driving Scores: Failure-Boundary Profiles" (in preparation, arXiv fall 2026) by Social Chaos Lab.

Instead of a single score, each family sweeps one physical factor of a hard scenario (for example, how far a fallen obstacle intrudes into the driving corridor) across a set of measured, validated variants. Running an agent across a family locates where its behavior flips, and repeating across traffic seeds turns that location into a distribution rather than an anecdote.

This repository bundles everything needed to run the families: the scenario data **and** the [Fail2Drive](https://github.com/autonomousvision/fail2drive) evaluation harness they run under, vendored with our two fork patches already applied. See [Bundled harness](#bundled-harness).

## Scenario families

The families live under [`failure_boundary_profiling_splits/`](failure_boundary_profiling_splits), one directory per family - the profiling counterpart to the harness's own `fail2drive_split/`.

All families run in CARLA Town13 under fixed clear weather. Each variant is a standard leaderboard-format route XML.

| Family | Swept factor | Range | Variants |
|---|---|---|---|
| `fm_001_intrusion_sweep` | maximum route intrusion (m) | 0.49 to 0.68 | 12 accepted |
| `fm_002_corridor_pincer` | minimum free corridor width (m) | 2.32 to 3.06 | 10 accepted |
| `fm_003_asset_swap` | obstacle asset identity (categorical) | 23 assets | 14 accepted, 9 refused |
| `fm_004_threshold_sweep` | minimum free corridor width (m) | 0.00 to 3.64 | 12 accepted |
| `fm_005_hero` | maximum route intrusion (m) | 0.00 to 1.09 | 12 accepted |

Each family directory - for example `failure_boundary_profiling_splits/fm_001_intrusion_sweep/` - contains:

- `routes/` - one route XML per accepted variant, unmodified from the files used in our measurement campaigns. `SHA256SUMS` at the repo root lets you verify byte-identity: run `sha256sum -c SHA256SUMS` from the repo root.
- `variants.csv` - the variant catalog: target factor value, and the measured, in-simulator verified geometry of the realized scene (free corridor width, route intrusion, required lateral deviation, closest approach distance).

A variant marked `refused` in `variants.csv` has no route XML: the generation pipeline validates every candidate scene in simulation before accepting it, and candidates that fail validation (for example, obstacle assets that tip over, roll away, or sink under physics) are refused rather than shipped. Refusals are retained in the catalog because they document the certified feasibility boundary of each family.

## Running the scenarios

See [docs/RUNNING.md](docs/RUNNING.md) for step-by-step instructions: CARLA setup, the harness environment, agent checkpoints, and the exact invocation. [docs/VERSIONS.md](docs/VERSIONS.md) pins every component we used, down to checkpoint revisions.

Short version, once CARLA 0.9.15 and the conda environment are up:

```bash
source env_vars.sh

python leaderboard/leaderboard/leaderboard_evaluator.py \
  --routes ${WORK_DIR}/failure_boundary_profiling_splits/fm_001_intrusion_sweep/routes/Generalization_PassableObstacles_1060_001_intrusion_sweep_v01.xml \
  --agent <agent entry point> \
  --agent-config <checkpoint dir> \
  --track SENSORS \
  --traffic-manager-seed 0
```

## Bundled harness

The [Fail2Drive](https://github.com/autonomousvision/fail2drive) benchmark harness (Gerstenecker, Geiger and Renz, IROS 2026) is vendored in this repository at upstream commit `bceb18a`, under `leaderboard/`, `scenario_runner/`, `team_code/`, `toolbox/`, `tools/`, `assets/` and `fail2drive_split/`. Its own documentation - installation, the full Fail2Drive benchmark split, the SLURM evaluation tooling, the route-authoring toolbox - is unchanged and still applies. Upstream's README is preserved verbatim as [README.fail2drive.md](README.fail2drive.md) ([简体中文](README.fail2drive.zh-CN.md), [日本語](README.fail2drive.ja.md)) and is the reference for all of it; follow its installation section to build the conda environment and fetch the CARLA build.

Two fork patches are **already applied** to the vendored tree. Both are retained under `patches/` as a provenance record, so the exact deltas against upstream stay auditable:

1. `0001` - `AgentBlockedTest` `min_speed` 0.1 -> 0.2 m/s, in both the `leaderboard` and `scenario_runner` copies of `route_scenario.py`. A creeping agent (between 0.1 and 0.2 m/s) never triggers the 180 game-second blocked termination; with the patch, crawl-forever scores as blocked, the same as a full stop. **This is a scoring-semantics change: campaigns run across this boundary are not comparable.**
2. `0002` - `float()` the `CustomObstacle` actor sort key in `construction_crash_vehicle.py`. Fixes a `'<' not supported between instances of 'str' and 'int'` crash in scenarios mixing obstacles with and without an `x=` key.

Do not re-apply the patches; `git am patches/*.patch` against this tree will fail because the changes are already present.

## Scope of this release

This release contains the scenario families, everything needed to run them, and the scoring, curves, boundary-fitting, and figures layers under [`analysis/`](analysis) (see its README; scoring and curves are stdlib-only, fitting needs numpy, figures matplotlib). Planned additions: recorder-based trajectory metrics. The scenario authoring system that generated these families is not part of this repository.

## License

Scenario data, catalogs, and documentation: CC BY 4.0. The bundled Fail2Drive harness and the patches in `patches/`: MIT, as upstream. The `analysis/` tools: MIT. See [LICENSE](LICENSE).

## Citation

For the scenario families, see [CITATION.cff](CITATION.cff). A paper reference will replace this once the preprint is public.

If you use the bundled harness, please also cite Fail2Drive:

```bibtex
@inproceedings{Gerstenecker2026Fail2Drive,
  author    = {Gerstenecker, Simon and Geiger, Andreas and Renz, Katrin},
  title     = {Fail2Drive: Benchmarking Closed-Loop Driving Generalization},
  booktitle = {IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)},
  year      = {2026},
  note      = {to appear}
}
```

## Contact

Social Chaos Lab - milan@socialchaoslab.com
