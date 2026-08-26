# Running the scenario families

The routes are standard CARLA leaderboard route XMLs and run under the [Fail2Drive](https://github.com/autonomousvision/fail2drive) evaluation harness, which is bundled in this repository. Every version below is pinned in [VERSIONS.md](VERSIONS.md).

## 1. CARLA

Install **CARLA 0.9.15** (the release that ships Town13, which all routes use). A GPU with 8 GB+ VRAM is recommended. Run the server headless:

```bash
${CARLA_ROOT}/CarlaUE4.sh -RenderOffscreen
```

## 2. Fail2Drive harness + patches

The harness is vendored in this repository and **both patches are already applied** - there is nothing to clone and nothing to `git am`. Set up its Python environment following the bundled upstream README, [README.fail2drive.md](../README.fail2drive.md):

```bash
conda env create -f environment.yml
conda activate fail2drive
source env_vars.sh
```

The patches are retained under `patches/` as a provenance record of the exact deltas against upstream `bceb18a`. Re-applying them will fail; the changes are already in the tree.

The patches matter for reproduction:

1. `AgentBlockedTest min_speed 0.1 -> 0.2 m/s` - a creeping agent (between 0.1 and 0.2 m/s) never triggers the 180 game-second blocked termination; with the patch, crawl-forever scores as blocked, the same as a full stop.
2. `float() the CustomObstacle actor sort key` - fixes a type error in obstacle actor ordering.

## 3. Agents

The agents we evaluated, with exact sources and checkpoint revisions, are listed in [VERSIONS.md](VERSIONS.md). The two sensor agents run directly through the harness:

- **TFv6** - the evaluated sensor agent of the LEAD repository (`kesai-labs/lead`, `cvpr2026` branch; the repository is named after its privileged planner, LEAD; the sensor agent is TFv6). Checkpoints from Hugging Face `ln2697/tfv6` (`tfv6_resnet34`). Note: the inference loader loads every `model*.pth` file in the checkpoint directory, so the three training-seed files run as a three-seed ensemble.
- **BridgeDrive** - overlay on a pinned LEAD commit; code from `shuliu-ethz/BridgeDrive`, checkpoint from Hugging Face `liushu-ethz/BridgeDrive`. Follow its own setup instructions for the overlay transfer step.

## 4. Invocation

Run one route through the harness evaluator (leaderboard-style flags):

```bash
python leaderboard/leaderboard/leaderboard_evaluator.py \
  --routes ${WORK_DIR}/scenarios/fm_001_intrusion_sweep/routes/Generalization_PassableObstacles_1060_001_intrusion_sweep_v01.xml \
  --agent <agent entry point> \
  --agent-config <checkpoint dir> \
  --track SENSORS \
  --traffic-manager-seed 0
```

The evaluator writes a result JSON per route (score, infractions, termination cause).

## 5. Seeds and repeats

CARLA closed-loop runs are not deterministic: byte-identical invocations can diverge inside physics contact resolution. Treat every run as one sample. Our campaigns repeat each variant across several traffic-manager seeds (0, 1, 2, ...) and pool per-seed results; single runs are anecdotes, not measurements.

## 6. Interpreting a family

Sort `variants.csv` by the swept factor and place each run's outcome at its measured (not target) factor value. The measured columns are in-simulator verified geometry: they were read back from the spawned scene, not assumed from the request.
