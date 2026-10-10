"""The scenario-defect register: one declared document, one kind, read by every scorer.

A row names a variant (and its seeds or repetitions) whose REALIZED scenario is
not the one it declares, with the measured evidence: a flow variant whose spawned
cars collide and reset before the ego arrives (fm_012 v01, F57, evidence from
flow_npc_collisions.py), a threading variant whose placed prop is pushed off its
certified pose the moment the harness lifts it (fm_005 v11, F66 part two and F68,
evidence from placement_rest_check.py). The readers take a registered cell out of
every band and every fit, keep its measured rows in the data layer under the
label `defect`, and pin the register by path and hash: the flow gap certificate
(adapters/fail2drive/.../flow_gap_certificate.py), the threading fits and
findings (boundary_stats.py, findings_carla.py), the Isaac findings
(adapters/isaac/.../findings_v2.py). Declared by hand, never derived: each row
names the tool that measured it; the declaration is Milan's.

Stdlib only, on the stdlib-only list (scl_core/tests/test_stdlib_only_modules.py):
the launcher and the Isaac tools read it under bare interpreters.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scl_core.records import portable_path

REGISTER_KIND = "scenario_defects/v0.2"
ENTRY_FIELDS = ("family_id", "scenario_id", "seeds", "reason", "evidence")


def seed_key(seed) -> str:
    """'seed_2' from 2, '2' or 'seed_2': the runner's directory name is the key
    (an Isaac repetition is keyed the same way by its label)."""
    s = str(seed)
    if s.startswith(("seed_", "rep_")) or s == "expert":
        return s
    return f"seed_{s}"


def load_register(path):
    """(document, sha256) of a register; refuses any other kind and any entry
    that does not name every field."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    kind = doc.get("register_kind")
    if kind != REGISTER_KIND:
        raise ValueError(f"{Path(path).name}: register_kind {kind!r}; this reader reads {REGISTER_KIND}")
    for e in doc.get("entries", []):
        missing = [k for k in ENTRY_FIELDS if not e.get(k)]
        if missing:
            raise ValueError(f"a defect entry names {', '.join(ENTRY_FIELDS)} "
                             f"(seeds \"all\" or a list of seed keys); missing {missing}")
        seeds = e["seeds"]
        if seeds != "all" and not (isinstance(seeds, list) and seeds
                                   and all(isinstance(s, (str, int)) for s in seeds)):
            raise ValueError(f"{e['scenario_id']}: seeds must be \"all\" or a non-empty list of seed keys")
        if not isinstance(e["evidence"], dict):
            raise ValueError(f"{e['scenario_id']}: evidence must be an object naming the tool and the measurement")
    return doc, hashlib.sha256(raw).hexdigest()


def entry_for(doc, family_id, scenario_id, seed):
    """The register entry naming this cell (family, variant, seed), or None."""
    for e in (doc or {}).get("entries", []):
        if e["family_id"] != family_id or e["scenario_id"] != scenario_id:
            continue
        if e["seeds"] == "all" or seed_key(seed) in {seed_key(s) for s in e["seeds"]}:
            return e
    return None


def entries_for_family(doc, family_id) -> list:
    return [e for e in (doc or {}).get("entries", []) if e["family_id"] == family_id]


def pin(path, sha256, root) -> dict:
    """What a document records about the register it read: repo-relative path,
    content hash, kind."""
    return {"path": portable_path(Path(path).resolve(), root), "sha256": sha256, "kind": REGISTER_KIND}
