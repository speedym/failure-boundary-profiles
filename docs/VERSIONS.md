# Pinned versions

Everything our measurement campaigns ran with. Route XML integrity is verifiable against `SHA256SUMS` at the repo root.

| Component | Pin |
|---|---|
| CARLA | 0.9.15 (Town13) |
| Fail2Drive harness | `autonomousvision/fail2drive` @ `72fea2777424d1c92ebbac79e720e9f6725c2a4d` + the two patches in `patches/` (applied in order) |
| LEAD repository (TFv6 sensor agent) | `kesai-labs/lead`, branch `cvpr2026` |
| TFv6 checkpoints | Hugging Face `ln2697/tfv6`, revision `2b3173e7af78392d4472c53cf2e1c2b003b53105`, variant `tfv6_resnet34` (ResNet-34, 140 deg camera + LiDAR + Radar; three training-seed files, loaded together as an ensemble by the LEAD inference loader) |
| BridgeDrive code | `shuliu-ethz/BridgeDrive`, overlay applied onto `kesai-labs/lead` @ `156afed46562884be77ec51f2b09aa60b7634c98` |
| BridgeDrive checkpoint | Hugging Face `liushu-ethz/BridgeDrive`, revision `d2b4b992504f260c90d7a7c5a6c48174b5679c55`, file `model_BridgeDrive_m1_k60_0030.pth` |
| TF++ checkpoint | Hugging Face `SimonGer/TFv5`, `all_towns/model_0030_0.pth` + `all_towns/config.json` |
| PDM-Lite expert | Fail2Drive's own privileged autopilot (`team_code/autopilot.py` in the harness), Track MAP, no checkpoint. Reference instrument, not a competing agent: it drives from ground-truth simulator state. |
| Traffic-manager seeds | per-run, passed via `--traffic-manager-seed`; campaigns used seeds starting at 0 |

Notes:

- Display naming: the LEAD repository is named after its privileged planner; the evaluated sensor agent is TFv6, and that is the name used in all our figures and prose.
- The `cvpr2026` branch is required for the public TFv6 checkpoints; the main branch expects a checkpoint format the released weights do not have.
