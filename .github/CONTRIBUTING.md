# Contributing to Failure-Boundary Profiles

This repository is the public artifact of a paper: five certified scenario families, plus the [Fail2Drive](https://github.com/autonomousvision/fail2drive) harness they run under, vendored with our two fork patches applied. That shapes what contributions make sense here.

---

## The scenario data is frozen

Every route XML under `failure_boundary_profiling_splits/*/routes/` is byte-identical to the file used in our measurement campaigns, and `SHA256SUMS` at the repo root exists so anyone can verify that. **Pull requests that modify route XMLs or `variants.csv` will not be merged** - changing them silently invalidates every published number.

If you believe a variant is wrong, open an issue describing what you observed rather than a PR fixing it. A genuine error is worth a documented correction with new checksums and a note in the paper, not a quiet edit.

The scenario authoring system that generated these families is not part of this repository, so new families cannot be contributed here either.

---

## What is welcome

**Reproduction reports.** The most useful contribution: run a family, report what you got. Include the agent, the checkpoint revision, the traffic-manager seeds, and the per-seed outcomes. Divergence from our published profiles is interesting, not unwelcome - CARLA closed-loop runs are not deterministic, and pooling more samples is how these become measurements rather than anecdotes.

**Analysis and scoring tooling.** Boundary-profile fitting and scoring code are planned additions (see the README's scope section). If you have built something that consumes `variants.csv` and a directory of result JSONs, we would like to see it.

**Documentation fixes.** Anything in `docs/` or the READMEs that is wrong, stale, or unclear.

**Harness bugs that affect these families.** See below.

---

## Harness bugs go upstream

The `leaderboard/`, `scenario_runner/`, `team_code/`, `toolbox/`, `tools/`, `assets/` and `fail2drive_split/` directories are vendored Fail2Drive, MIT-licensed, at upstream commit `bceb18a`. A bug in that code is almost certainly an upstream bug: please report it at [autonomousvision/fail2drive](https://github.com/autonomousvision/fail2drive/issues) so every Fail2Drive user gets the fix.

Open an issue *here* as well only if the bug specifically affects running these scenario families - for example, something that interacts with the two patches in `patches/`.

Do not send PRs that re-apply `patches/*.patch`; those changes are already in the tree.

---

## Best practices

When opening a **pull request**, include:

- A clear description of what changed and why
- How to verify it - for tooling, a worked example against a real family

When reporting a **run**, include:

- Family and variant IDs, the agent and checkpoint revision, CARLA version
- Every traffic-manager seed you ran, and the outcome of each - not just the aggregate

---

## Contact

Social Chaos Lab - milan@socialchaoslab.com
