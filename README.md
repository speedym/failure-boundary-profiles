# Failure-Boundary Profiles: Scenario Families

Certified scenario families for measuring failure-boundary profiles of autonomous driving agents in CARLA. This repository is the public artifact of the paper "Beyond Driving Scores: Failure-Boundary Profiles" (in preparation, arXiv fall 2026) by Social Chaos Lab.

Instead of a single score, each family sweeps one physical factor of a hard scenario (for example, how far a fallen obstacle intrudes into the driving corridor) across a set of measured, validated variants. Running an agent across a family locates where its behavior flips, and repeating across traffic seeds turns that location into a distribution rather than an anecdote.

## Scenario families

All families run in CARLA Town13 under fixed clear weather. Each variant is a standard leaderboard-format route XML.

| Family | Swept factor | Range | Variants |
|---|---|---|---|
| `fm_001_intrusion_sweep` | maximum route intrusion (m) | 0.49 to 0.68 | 12 accepted |
| `fm_002_corridor_pincer` | minimum free corridor width (m) | 2.32 to 3.06 | 10 accepted |
| `fm_003_asset_swap` | obstacle asset identity (categorical) | 23 assets | 14 accepted, 9 refused |
| `fm_004_threshold_sweep` | minimum free corridor width (m) | 0.00 to 3.64 | 12 accepted |
| `fm_005_hero` | maximum route intrusion (m) | 0.00 to 1.09 | 12 accepted |

Each family directory contains:

- `routes/` - one route XML per accepted variant, unmodified from the files used in our measurement campaigns (`SHA256SUMS` at the repo root lets you verify byte-identity).
- `variants.csv` - the variant catalog: target factor value, and the measured, in-simulator verified geometry of the realized scene (free corridor width, route intrusion, required lateral deviation, closest approach distance).

A variant marked `refused` in `variants.csv` has no route XML: the generation pipeline validates every candidate scene in simulation before accepting it, and candidates that fail validation (for example, obstacle assets that tip over, roll away, or sink under physics) are refused rather than shipped. Refusals are retained in the catalog because they document the certified feasibility boundary of each family.

## Running the scenarios

See [docs/RUNNING.md](docs/RUNNING.md) for step-by-step instructions: CARLA setup, the Fail2Drive harness plus the two patches in `patches/`, agent checkpoints, and the exact invocation. [docs/VERSIONS.md](docs/VERSIONS.md) pins every component we used, down to checkpoint revisions.

## Scope of this release

This release contains the scenario families and the information needed to run them under the public Fail2Drive harness. Planned additions: scoring and analysis tooling, and boundary-profile fitting code. The scenario authoring system that generated these families is not part of this repository.

## License

Scenario data, catalogs, and documentation: CC BY 4.0. The patches in `patches/` apply to the MIT-licensed Fail2Drive harness and are provided under MIT. See [LICENSE](LICENSE).

## Citation

See [CITATION.cff](CITATION.cff). A paper reference will replace this once the preprint is public.

## Contact

Social Chaos Lab - milan@socialchaoslab.com
