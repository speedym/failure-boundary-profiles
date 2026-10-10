"""Campaign declarations: what a campaign was EXPECTED to run.

One document per (campaign, family) under artifacts/examples/campaigns/,
passed to every driver as a required `--campaign` argument and recorded by
path and hash. The gates read only the declaration: coverage (models x
repetitions x variants, missing and undeclared cells named), the family by
path AND hash, the expert flag against its named reference agent. See
artifacts/examples/campaigns/README.md. Stdlib only: the CARLA map check
imports this under the fail2drive env, the Isaac runner under isaacsim.

Two kinds are read (ledger F29, 2026-09-13):
  campaign_declaration/v0.2 - `declared_grid.repetitions` (the labels) and
      `declared_grid.repetition_axis` ({kind, note}: what a repetition IS on
      that backend, the same vocabulary both campaign metas use). Every new
      declaration on either backend.
  campaign_declaration/v0.1 - `declared_grid.seeds`, CARLA vocabulary; the
      nine canon files of campaign_carla_v01-v05 stay this kind for good,
      frozen with their certifications and bundles. Read-only: a v0.1 file
      must declare backend fail2drive, since only there a repetition is a
      seed.
One neutral accessor (`repetitions`) serves both. The v0.1-only `seeds`
handle the three record-pinned certifiers kept until their first v0.2
campaign retired on 2026-09-15, when the F36 smoke campaign (the first v0.2
flow campaign) reached the map check and it refused; every consumer reads
`repetitions` now.
"""
from __future__ import annotations

import json
from pathlib import Path

from scl_core.records import portable_path, sha256_file

DECLARATION_KIND = "campaign_declaration/v0.2"
DECLARATION_KIND_V1 = "campaign_declaration/v0.1"
KNOWN_KINDS = (DECLARATION_KIND, DECLARATION_KIND_V1)
REQUIRED_FIELDS = ("declaration_kind", "campaign_id", "backend", "family",
                   "declared_grid", "results_subtree", "note")
FAMILY_FIELDS = ("family_id", "manifest_path", "manifest_sha256")
GRID_FIELDS = ("variants", "models", "repetitions", "repetition_axis", "expert")
GRID_FIELDS_V1 = ("variants", "models", "seeds", "expert")
# The repetition axis vocabulary is the one the two campaign metas already
# write (campaign_manifest.py on CARLA, evaluate_models.py on Isaac).
REPETITION_AXIS_KINDS = ("traffic_seed_strata", "policy_reps")
# How a repetition directory is named per axis, for messages that name cells
# on disk (CARLA: <model>/seed_<k>/, Isaac: <slug>_rep<k>).
_AXIS_DIR_PREFIX = {"traffic_seed_strata": "seed_", "policy_reps": "rep"}
_V1_AXIS = {"kind": "traffic_seed_strata",
            "note": "v0.1 declaration: `seeds` are CARLA traffic-manager seeds (strata)"}
_NAMED_LIMIT = 12


def _relative(value, field):
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"{field} must be a relative path without '..', got {value!r}")
    return rel


class CampaignDeclaration:
    """A declaration document plus the identity of the bytes it was read from."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.sha256 = sha256_file(self.path)
        doc = json.loads(self.path.read_text())
        missing = [k for k in REQUIRED_FIELDS if k not in doc]
        if missing:
            raise ValueError(f"{self.path.name}: not a campaign declaration, missing {missing} "
                             f"(contract {DECLARATION_KIND}: {list(REQUIRED_FIELDS)})")
        kind = doc["declaration_kind"]
        if kind not in KNOWN_KINDS:
            raise ValueError(f"{self.path.name} declares {kind!r}; this reader implements {list(KNOWN_KINDS)}")
        self.kind = kind
        self._rep_field = "seeds" if kind == DECLARATION_KIND_V1 else "repetitions"
        family, grid = doc["family"], doc["declared_grid"]
        for field in FAMILY_FIELDS:
            if field not in family:
                raise ValueError(f"{self.path.name}: family.{field} is required ({list(FAMILY_FIELDS)})")
        version = family.get("version")     # optional since 2026-10-03: the registry's version, a checked claim
        if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 1):
            raise ValueError(f"{self.path.name}: family.version, when present, is a positive integer (the family "
                             f"version registry's number for family.manifest_sha256), got {version!r}")
        grid_fields = GRID_FIELDS_V1 if kind == DECLARATION_KIND_V1 else GRID_FIELDS
        for field in grid_fields:
            if field not in grid:
                raise ValueError(f"{self.path.name}: declared_grid.{field} is required for {kind} "
                                 f"({list(grid_fields)})")
        for name in ("campaign_id", "backend"):
            if not isinstance(doc[name], str) or not doc[name]:
                raise ValueError(f"{self.path.name}: {name} must be a non-empty string")
        if kind == DECLARATION_KIND_V1:
            if "repetitions" in grid or "repetition_axis" in grid:
                raise ValueError(f"{self.path.name}: a {DECLARATION_KIND_V1} document names `seeds`, "
                                 f"not `repetitions`/`repetition_axis` (those are {DECLARATION_KIND})")
            if doc["backend"] != "fail2drive":
                raise ValueError(f"{self.path.name}: {DECLARATION_KIND_V1} is CARLA vocabulary (`seeds` are "
                                 f"traffic seeds); a {doc['backend']!r} declaration must be {DECLARATION_KIND}")
            self._axis = dict(_V1_AXIS)
        else:
            if "seeds" in grid:
                raise ValueError(f"{self.path.name}: {DECLARATION_KIND} names `repetitions`, not `seeds`")
            axis = grid["repetition_axis"]
            if (not isinstance(axis, dict) or axis.get("kind") not in REPETITION_AXIS_KINDS
                    or not isinstance(axis.get("note"), str) or not axis["note"]):
                raise ValueError(f"{self.path.name}: declared_grid.repetition_axis must be "
                                 f"{{kind: one of {list(REPETITION_AXIS_KINDS)}, note: text}}")
            self._axis = {"kind": axis["kind"], "note": axis["note"]}
        for field in ("variants", "models", self._rep_field):
            values = grid[field]
            if not isinstance(values, list) or not values or len(set(values)) != len(values):
                raise ValueError(f"{self.path.name}: declared_grid.{field} must be a non-empty list without duplicates")
        if not isinstance(grid["expert"], bool):
            raise ValueError(f"{self.path.name}: declared_grid.expert must be true or false")
        reference = grid.get("reference_model")
        if grid["expert"]:
            if not reference or reference not in grid["models"]:
                raise ValueError(f"{self.path.name}: declared_grid.expert is true but reference_model "
                                 f"{reference!r} is not one of models {grid['models']}; a declared expert "
                                 f"must be a declared agent")
        elif reference in grid["models"]:
            raise ValueError(f"{self.path.name}: declared_grid.expert is false but reference_model "
                             f"{reference!r} is one of models {grid['models']}")
        self.manifest_path = _relative(family["manifest_path"], "family.manifest_path")
        self.results_subtree = _relative(doc["results_subtree"], "results_subtree")
        self.doc = doc

    @property
    def campaign_id(self) -> str:
        return self.doc["campaign_id"]

    @property
    def backend(self) -> str:
        return self.doc["backend"]

    @property
    def family_id(self) -> str:
        return self.doc["family"]["family_id"]

    @property
    def manifest_sha256(self) -> str:
        return self.doc["family"]["manifest_sha256"]

    @property
    def variants(self) -> list:
        return list(self.doc["declared_grid"]["variants"])

    @property
    def models(self) -> list:
        return list(self.doc["declared_grid"]["models"])

    @property
    def repetitions(self) -> list:
        """The repetition labels, whatever the kind names them."""
        return list(self.doc["declared_grid"][self._rep_field])

    @property
    def repetition_axis(self) -> dict:
        """{kind, note}: what one repetition IS on this backend."""
        return dict(self._axis)

    @property
    def expert(self) -> bool:
        return self.doc["declared_grid"]["expert"]

    @property
    def reference_model(self):
        return self.doc["declared_grid"].get("reference_model")

    @property
    def note(self) -> str:
        return self.doc["note"]

    def expected_cells(self) -> set:
        return {(m, r, v) for m in self.models for r in self.repetitions for v in self.variants}

    def cell_name(self, model, repetition, variant) -> str:
        """A cell as it is laid out on this backend's disk."""
        return f"{model}/{_AXIS_DIR_PREFIX[self._axis['kind']]}{repetition}/{variant}"

    def record(self, root) -> dict:
        """How a consumer records what it was passed."""
        return {"path": portable_path(self.path, root), "sha256": self.sha256}


def load_declaration(path) -> CampaignDeclaration:
    return CampaignDeclaration(path)


def load_declarations(paths) -> list:
    """The unit is (campaign, family): a campaign may appear once per family."""
    declarations = [CampaignDeclaration(p) for p in paths]
    pairs = [(d.campaign_id, d.family_id) for d in declarations]
    if len(set(pairs)) != len(pairs):
        raise ValueError(f"duplicate (campaign, family) declarations passed: {sorted(pairs)}")
    return declarations


SNAPSHOT_MANIFEST_NAMES = ("family_manifest.json",)   # Isaac lays the family directory; CARLA lays <family_id>.json


def resolve_family_manifest(declaration: CampaignDeclaration, family_root, root) -> tuple:
    """Where the family a declaration pins is read from (principle 13, 2026-09-21): the raw
    evidence first, `<family_root>/family_snapshot/` (the family as run, bound by the raw content
    id), and canon (`root / manifest_path`) only for a bundle that predates its snapshot. Returns
    (path, source) with source "raw_snapshot" or "canon"; the hash is checked by the caller with
    check_family_manifest, whichever the source. Canon's copy is what the next campaign runs and
    has no say over scoring a past one; a canon read is recorded by the derived writer as a
    canon input at the pinned hash."""
    snapshot = Path(family_root) / "family_snapshot"
    for name in (*SNAPSHOT_MANIFEST_NAMES, f"{declaration.family_id}.json"):
        if (snapshot / name).is_file():
            return snapshot / name, "raw_snapshot"
    return Path(root) / declaration.manifest_path, "canon"


def check_family_manifest(declaration: CampaignDeclaration, manifest_path) -> dict:
    """The family by path AND by hash. Returns the manifest document."""
    manifest_path = Path(manifest_path)
    actual = sha256_file(manifest_path)
    if actual != declaration.manifest_sha256:
        raise ValueError(f"{declaration.campaign_id}: family manifest drift - the declaration pins "
                         f"{declaration.manifest_sha256}, {manifest_path.name} hashes {actual}")
    manifest = json.loads(manifest_path.read_text())
    # Two manifest shapes bind the same way (F24 keeps the shapes apart):
    # CARLA names the family `manifest_id` and its variants `scenario_id`
    # with a `verdict`; Isaac names them `family_id` and `stem`, and a
    # variant without a verdict is accepted (only "rejected" excludes).
    manifest_id = manifest.get("manifest_id", manifest.get("family_id"))
    if manifest_id != declaration.family_id:
        raise ValueError(f"{declaration.campaign_id}: the pinned manifest is {manifest_id!r}, "
                         f"the declaration names family {declaration.family_id!r}")
    accepted = {v.get("scenario_id", v.get("stem")) for v in manifest["variants"]
                if v.get("verdict", "accepted") == "accepted"}
    unaccepted = sorted(set(declaration.variants) - accepted)
    if unaccepted:
        raise ValueError(f"{declaration.campaign_id}: declared variants the family manifest does not accept: "
                         f"{unaccepted}")
    return manifest


def _named(declaration, cells) -> str:
    cells = sorted(cells)
    shown = ", ".join(declaration.cell_name(*c) for c in cells[:_NAMED_LIMIT])
    return shown + (f" (+{len(cells) - _NAMED_LIMIT} more)" if len(cells) > _NAMED_LIMIT else "")


def coverage_gate(declaration: CampaignDeclaration, cells) -> set:
    """`cells`: the (model, repetition, variant) triples present. Refuses
    missing, undeclared and duplicate cells, naming them; returns the
    declared variants."""
    cells = list(cells)
    expected = declaration.expected_cells()
    actual = set(cells)
    seen, duplicates = set(), set()
    for cell in cells:
        if cell in seen:
            duplicates.add(cell)
        seen.add(cell)
    missing, extra = expected - actual, actual - expected
    if missing or extra or duplicates:
        parts = []
        absent_models = sorted(set(declaration.models) - {c[0] for c in actual})
        if absent_models:
            parts.append(f"models with no cells at all: {absent_models}")
        if missing:
            parts.append(f"missing cells: {_named(declaration, missing)}")
        if extra:
            parts.append(f"undeclared cells: {_named(declaration, extra)}")
        if duplicates:
            parts.append(f"duplicate cells: {_named(declaration, duplicates)}")
        raise ValueError(f"{declaration.campaign_id}: coverage gate against the declared grid "
                         f"({len(declaration.models)} models x {len(declaration.repetitions)} repetitions "
                         f"({declaration.repetition_axis['kind']}) x {len(declaration.variants)} variants = "
                         f"{len(expected)} cells) - " + "; ".join(parts))
    return set(declaration.variants)
