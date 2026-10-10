# The measurement chain

This directory is a generated export of the measurement layer of the Social Chaos Lab scenario pipeline: every file the paper's scored results depend on, copied byte for byte from the private repository at the commit `PUBLIC_EXPORT.json` names. Nothing in it was written by hand for this repository; the private repository's export tool regenerates it, and it is never edited here. Which files belong is read from the results themselves, not typed: every derived bundle lists the sources it was computed from, every scoring stamp and document pins the files it read, and the reader tools bring their imports; the union is this tree.

## Vocabulary

The words the code uses, in the order a reader meets them.

- **Campaign**: one run of one or more scenario families with a set of agents on one backend, CARLA or Isaac Sim, named like `campaign_carla_v08`.
- **Raw bundle**: the simulator's evidence of a campaign, one zip, named by its content id, the sha256 of its member manifest. It holds the run logs, the trajectory extracts, the family as it was run and the campaign's declarations. Never rewritten.
- **Derived bundle**, also called an interpretation: everything scoring produced from one raw bundle at one commit, the scored tables, the fitted statistics, the findings documents and the certification records, with a source manifest listing every file the scoring read by path and hash. Re-scoring produces a new one beside the old; the store points at the current one per campaign.
- **Pin**: a path and sha256 a document records for a file it depends on. A pin that does not match refuses.
- **The store**: where results are kept today: the certification and pooling records under `artifacts/examples/findings/` and the archive of bundles by content id.
- **Chains**: on CARLA the static-obstacle families, the paper's families 1 to 5, take the threading chain, a run scorer, the boundary fit and a findings emitter; the oncoming-flow families, 6 and 7, take the flow chain, which measures the realized traffic against a declared conflict zone and a gap certificate. The warehouse families take the Isaac chain. Which chain a family takes is read from its manifest.
- **Freeze and bind**: freezing writes a derived bundle from a scoring run; binding writes the certification record that ties the findings documents to the bundles by hash.
- **Re-stamp and instrument change**: the launcher's verdict when two interpretations of one campaign are compared: a re-stamp when every value is equal and only provenance moved, an instrument change when any value moved.
- **Pool**: the scored rows of one family over the campaigns a pooling spec names, fitted together. The paper's figures read pools, never one campaign.
- **Family realization and version**: a family id names a lineage; its manifest's hash names one realization of it; the version registry gives each realization a readable label.
- **Verb**: a subcommand of the launcher `scl/scripts/campaign.py`. The verbs for this tree are `score`, `freeze-derived`, `store-derived`, `compare-lineages`, `prune-cache` and `stale`. The others serve the private side and stop here: `run` and `freeze-raw` start and freeze a simulator run (the runners are not released), `rebuild-register` rewrites the private campaign notes and a test fixture and runs the private test suite, `table` renders the private campaign notes, `next-id` numbers the next campaign.

## What is here

| Path | What it holds |
|---|---|
| `scl_core/` | the backend-neutral core the chain imports: the boundary estimator (`stats/boundary.py`: a logistic fit with its diagnostics where the outcomes overlap, an interval instead of a point where they separate, every failure mode a named case), the records, the campaign declarations and bundles, the results store reader, the family version registry reader, the scenario-defect register reader |
| `adapters/fail2drive/scripts/multi_model_eval/` | the CARLA chain: the run scorer with collision attribution by actor identity (`score_runs.py`, `trajectory_extract.py`), the boundary statistics and the findings emitter, the oncoming-flow chain (conflict zone, gap certificates, realized-flow measurement, decision accuracy), the certifiers, pooling, the figure and table generator, the check that a placed obstacle stayed where the scenario put it and the attribution audit |
| `adapters/isaac/scripts/multi_model_eval/` | the warehouse chain: the scorer of each cell (one variant run by one brain), the go and clean-pass derivation (did the robot commit to the gap, did it pass without contact), the findings emitter, the certifier, the figure generator |
| `adapters/fail2drive/asset_registry.json` | the measured CARLA asset catalog, one row per prop and vehicle blueprint with its spawn-probed footprint; the scorer takes an obstacle's extent from the scenario extract where one exists and from this catalog otherwise, and every CARLA scoring stamp pins it |
| `scripts/` | the campaign launcher (`campaign.py`), the bundle unpacker and verifier (`unpack_campaign_bundle.py`), the scoring driver (`score_campaign.py`) |
| `artifacts/examples/` | the measured family manifests and the compiled routes, the scoring instruments (the conflict zone, the map excerpt, the flow gap certificates, the scenario-defect register, the warehouse instrument that decides a go from the robot's crossing of two declared lines), the robot and environment records, every campaign's declarations and certification records, the pooling specs and records, the family version registry |
| `adapters/fail2drive/scripts/multi_model_eval/models.yaml`, `adapters/isaac/scripts/multi_model_eval/models.json` | the agent declarations of both backends: what ran, by repository, commit, checkpoint hash and environment recipe, in the form `scl_core/agent_identity.py` validates and the raw bundles pin per run. They declare; the runners and installers that read them are not released, so a campaign is re-scored from its bundle here, not re-run (the driving families run through the vendored harness, `docs/RUNNING.md`) |
| `environment.yml` | the conda environment the chain runs under (`scl_prototype`) |
| `PUBLIC_EXPORT.json` | the private commit, whether the exported files are its bytes, the derived bundles the set was read from (campaign id to content id), every exported path with its sha256, and the hash of every variant catalog |
| `PUBLIC_PROOF.json` | written by the end-to-end proof: per campaign, whether the raw bundle scored, froze and bound from this tree to the store's interpretation, the launcher's verdict and the differing members |
| `../scl_families/<family>/` | the generated per-family view the same export writes from the manifests here: a driving family's routes (byte-identical to `artifacts/examples/backend_outputs/`) and catalog, a warehouse family's manifest and catalog, one README table, one `SHA256SUMS` |

Not here, on purpose: the raw campaign bundles (the simulators' evidence, 1.5 to 14 GB each; hosted separately, named by content id), the findings and pooled documents (members of the derived bundles, reached through the certification and pooling records that are here), the tests and fixtures, and the scenario authoring system: its requests (the prompts), plans, scenario records and per-variant deltas. The family manifests here name those documents by id and hash (`plan_ref`, `request_ref`, `source_ir_ref`, each variant's `delta_ref`), and the names resolve to nothing in this tree; a manifest is the family as it was run, pinned by hash, so the names stay.

## How this tree was checked

The export carries what the results pin and what the exported code names; a file the code reads some other way would be missing, and only running the chain finds that. The private repository's prove tool does exactly that: raw bundles scored, frozen and bound from this clone through the launcher, each result compared with the store's interpretation by the launcher's own verdict and member by member. Every member but the source snapshot (which records the scoring checkout's commit, by construction not the private one) must be byte-identical and the verdict RE-STAMP. `PUBLIC_PROOF.json` beside this file says which bundles were last proven that way, from which private commit, and which members differed where one was not.

## Set up

Two environments; this tree ships one of them.

1. `scl_prototype`, the chain's environment, from `scl/environment.yml`. The file is the private pipeline's recipe byte for byte, because every derived bundle's source manifest pins it by hash; of its packages the chain imports numpy, shapely and matplotlib (the renders), the rest serve the private pipeline. It sets `PYTHONDONTWRITEBYTECODE` so the tree stays free of bytecode. From the repository root:

```sh
conda env create -f scl/environment.yml
conda run -n scl_prototype python -c "import numpy, shapely, matplotlib; print('scl_prototype ok')"
```

Every scoring, certification, pooling and figure step below runs under it. The launcher, `scl/scripts/campaign.py`, is standard library and spawns each tool through `conda run -n scl_prototype`; `verify_scl_export.py` at the root needs no environment at all.

2. `fail2drive`, the vendored harness's environment, built as [`docs/RUNNING.md`](../docs/RUNNING.md) section 2 describes from the `environment.yml` at the repository root. The chain needs it for one step only: the oncoming-flow map check, which reads the full Town03 OpenDRIVE through the CARLA Python API. A re-score of a campaign holding a flow family (`campaign_carla_v04`, `v05`, and the flow half of `v06` to `v09`) needs it and the CARLA map path in the instruments file; every other step, and every warehouse campaign, runs without it.

The raw and derived campaign bundles are not in this repository (the top-level README's scope section says where they are); the sections below take their paths as arguments.

## Verify the export

Every exported file's sha256 is in `PUBLIC_EXPORT.json`, and `verify_scl_export.py` at the repository root checks the tree against it (the repository's own file, not an exported one; standard library only, Python 3.8 or newer). From the repository root:

```sh
python3 verify_scl_export.py
```

It reports stale, missing and stray files over `scl/` and `scl_families/`, the private commit the export was made at and whether the files were that commit's bytes, and the campaigns `PUBLIC_PROOF.json` says were proven from this tree; exit 0 only when nothing is stale, missing or stray.

## Verify a derived bundle against this tree

A derived bundle (`campaign_<id>_derived.zip`) is one interpretation of a raw bundle: it carries the scored tables, the findings documents, the records, and `source_snapshot/source_manifest.json`, the repo-relative path and sha256 of every source file its content depends on. The unpacker checks every member against the bundle's manifest and, with `--verify-sources`, every listed source against a checkout:

```sh
conda run -n scl_prototype python scl/scripts/unpack_campaign_bundle.py campaign_carla_v01_derived.zip \
    --into /work/verify --verify-sources "$(pwd)/scl"
```

A source the tree does not hold at the listed hash is reported by path and the unpack refuses.

To ask the same question of every bundle at once, the launcher's `stale` verb reads each derived bundle the certification records name, without unpacking it, and compares its listed sources, the inputs its scoring stamps name and the files its documents name with this tree, by hash:

```sh
python scl/scripts/campaign.py --repo-root "$(pwd)/scl" stale --bundles /path/to/derived/zips
```

One row per campaign; exit 0 when every bundle verifies, 1 when any lists a file this tree does not hold as the bundle recorded it. The private pipeline runs it before every export, so the bundles that ship with a tree verify against that tree.

## Re-score a raw bundle

The launcher runs the chain the store ran: unpack the raw zip, score it with the declared instruments, freeze the derived zip and bind the records. It is stdlib-only and spawns every tool through `conda run -n scl_prototype`. The instruments come from the file the tree ships, `scl/artifacts/examples/campaigns/instruments.json`, one section per backend with every path relative to the repository root (the launcher resolves them against `--repo-root`); nothing in it needs editing. The one instrument outside the repository is the full CARLA OpenDRIVE map the oncoming-flow map check reads: `--map <carla 0.9.15>/CarlaUE4/Content/Carla/Maps/OpenDrive/Town03.xodr`, required for a campaign holding a flow family (`campaign_carla_v04`, `v05`, and `v06` to `v09`), refused for any other.

```sh
R="$(pwd)/scl"
I="$R/artifacts/examples/campaigns/instruments.json"
python3 scl/scripts/campaign.py --repo-root "$R" score \
    --raw /bundles/campaign_isaac_v08.zip --work /work/i08 --instruments "$I"
python3 scl/scripts/campaign.py --repo-root "$R" freeze-derived \
    --scores /work/i08/scores --campaign-id campaign_isaac_v08 --bundles /bundles \
    --results-root /work/i08/unpacked --instruments "$I" --work /work/i08_bind
python3 scl/scripts/campaign.py --repo-root "$R" store-derived \
    --campaign-id campaign_isaac_v08 --work /work/i08_bind --bundles /bundles
# a driving campaign with a flow family adds the map:
python3 scl/scripts/campaign.py --repo-root "$R" score \
    --raw /bundles/campaign_carla_v08.zip --work /work/c08 --instruments "$I" \
    --map "$CARLA_ROOT/CarlaUE4/Content/Carla/Maps/OpenDrive/Town03.xodr"
```

One campaign needs one more flag: `campaign_isaac_v03`'s run pinned an earlier robot record than the one shipped, so its score is an instrument change by the chain's own rule and refuses until `score --accept-instrument-change` says so; the change is recorded in the derived bundle, and the verdict against ours still reads RE-STAMP. Every verb prints the commands it runs before running them (`--plan-only` prints and stops). Each run takes fresh work directories: `score` refuses to unpack over a campaign directory that is already there (what is present is not verified) and `freeze-derived` refuses an existing `--work`, so a second attempt uses new directories or empties the old ones. `score --jobs N` runs a campaign's families side by side (the driver measures its first step's memory and admits the rest against what the box has free); the scoring-inputs unpack leaves the videos and the recorder logs in the zip, verified in place. `score` adopts the raw bundle's declarations into `artifacts/examples/campaigns/<id>/` (already present here for the store's seventeen campaigns) and writes `scores/scoring_record.json`: the chains, the steps, the tools by hash. `freeze-derived` writes `/bundles/campaign_<id>_derived.zip` and prints its content id; the id the store holds for that campaign is `derived_from` in `PUBLIC_EXPORT.json`. `store-derived` copies the records and findings documents the bind wrote into this tree's store (`artifacts/examples/findings/`), so from then on pooling and the figures read your interpretation of that campaign instead of ours; `compare-lineages` says whether the two agree. Work from a git clone with a clean checkout: the freeze records the commit the listed sources were read from, and `store-derived` refuses a derived zip that names no commit (a downloaded archive of the repository has none) or whose listed sources were modified against it. The two agree when the tree, the instruments and the runtime (`environment.yml`) are the store's; a different tree gives a different interpretation, which the launcher's `compare-lineages` verb classifies as a re-stamp (every value equal) or an instrument change.

What this tree re-scores: every campaign in the store, from its raw bundle, with one requirement this tree cannot carry. The oncoming-flow chain's map check runs under the Fail2Drive environment with the CARLA Python API and reads the full Town03 OpenDRIVE file from a CARLA 0.9.15 installation (the pinned excerpt here covers the junction the families use). The five campaigns that predate in-bundle declarations (`campaign_carla_v01` to `v05`) take their declarations from `artifacts/examples/campaigns/<id>/`, where every campaign's declarations ship; the launcher says so in its plan and the derived bundle records them as written after the fact. The command is the same. Which chain scores a driving family, and on which axis, is read from the family manifest alone (`campaign_manifest.family_axis`: the factor in `effective_domain`, its kind from what the variants carry), so no authoring document is needed. A family's version must be registered in `artifacts/examples/family_versions/registry.json` for the launcher to run it; it is, for every realization the store holds.

## Pool campaigns and render the figures

The figures read pools, not campaigns: which campaigns pool is data (`artifacts/examples/findings/pooling/*.json`). Pooling fetches each campaign's interpretation from the directory holding the derived zips through its certification record (ours as shipped, or yours after `store-derived`), verifies it, refuses an interpretation produced by another scorer than this tree's, and refits the boundary statistics over the pooled rows with the scenario-defect register applied. Without `--cache`, interpretations unpack beside the archive, under `<archive>/interpretations/<content id>/`; no tool in this tree writes under the checkout. The warehouse figure tool reads the findings documents of an unpacked derived bundle. Both run from this tree as shown (`F2` and `T5` below are the paper's figure and table ids; `--figures` lists them). The table rows produced this way from one driving pool were checked equal to the store's on 2026-10-07:

```sh
conda run -n scl_prototype python scl/adapters/fail2drive/scripts/multi_model_eval/pool_campaigns.py \
    --spec scl/artifacts/examples/findings/pooling/fm_005_v01.json --out /work/pooled --archive /bundles \
    --cache /work/cache --defects scl/artifacts/examples/scoring_instruments/carla_scenario_defects.json
conda run -n scl_prototype python scl/adapters/fail2drive/scripts/multi_model_eval/paper_figures.py \
    --pooled /work/pooled --spec scl/artifacts/examples/findings/pooling/fm_005_v01.json --figures F2 T5 --out /work/figures
conda run -n scl_prototype python scl/adapters/isaac/scripts/multi_model_eval/paper_figures.py \
    --findings /work/verify/campaign_isaac_v08/*/findings/fs_*.json \
    --instrument scl/artifacts/examples/scoring_instruments/isaac_aisle_gauntlet_two_line_go.json \
    --robot-record scl/artifacts/examples/imported_ir/robot_record_nova_carter.json --out /work/figures_isaac
```

Each figure tool writes two layers: `figure_data/*.json`, the exact numbers plotted with a provenance block naming the pools, the interpretations by content id and the tools by hash, and the renders, which consume only the figure data. The figure data is the citation for every number; a render is never the source of truth.

## Conventions the code states

- **Provenance notes cite the private engineering ledger by number** (`F12`, `F66`, ...) and attribute rulings and declarations to the people who made them, with dates. The ledger itself is not published; the numbers are stable cross-references, nothing more.
- **Every number carries its diagnostics**: converged or not, rounds, slope sign, the tested range, replicates used, rows dropped. A decision keys on them, never on a cap or a default. A diverged, wrong-sign, out-of-range or empty result is a named case with its honest fallback, the bracket, "no boundary in the tested range", "unmeasured", never a number sitting in the measurement's slot.
- **A collision is attributed to what the recorder says the car hit**, by actor identity through the run's trajectory extract. Position is a diagnostic, and the named fallback for a run that has no extract.
- **A scenario that did not realize as declared is a defect, declared in a register.** In CARLA the leaderboard keeps a scenario's props stashed far underground and teleports each one to its placed pose the moment the ego reaches the scenario's trigger; in the code that moment is called the lift. The pipeline certified the pose the prop lands in. A prop that tips or slides after landing, so the agent meets a different scene than the one declared, is a declared defect in the scenario-defect register; so is an oncoming-flow variant whose recorded traffic is not the declared traffic. A defect's runs stay in the documents as a group with no counted runs, excluded from every fit, never silently dropped.
- **Every refusal names the contract it enforces.**
