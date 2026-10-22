"""The pooling spec (campaign_pooling/v0.2) and the pooling record kind: the one place their shape is read
(F88, 2026-10-15; until then the pool tool and the figure reader each validated the spec).

A pool is the declaration of a cohort: one family, and for each campaign it names the repetitions it takes
(traffic seeds on CARLA, policy samples on Isaac). The list is required: a spec never says "every
repetition the campaign holds", which is a count, not a contract. The pool tool (pool_campaigns.py) filters
each campaign's rows to its list and gates them against exactly those cells; the pooling record binds the
lists; the figure readers name a cohort that takes a strict subset ("seeds 0 to 2") on its labels.
"""
from __future__ import annotations

import json
from pathlib import Path

from scl_core.campaign_declaration import load_declaration

SPEC_KIND = "campaign_pooling/v0.2"            # v0.2: campaigns as {id, repetitions}, the cohort declared
RECORD_KIND = "campaign_pooling_record/v0.3"   # v0.3: the record's campaigns carry the repetitions the pool took
AXIS_WORD = {"traffic_seed_strata": "seeds", "policy_reps": "samples"}   # how a cohort note names a repetition


def load_spec(path) -> dict:
    """A pooling spec: pool_id, family_id and campaigns, each {id, repetitions} with a non-empty list of
    distinct ints; ids distinct; refused by name otherwise."""
    path = Path(path)
    spec = json.loads(path.read_text())
    if spec.get("pooling_kind") != SPEC_KIND:
        raise ValueError(f"{path}: pooling_kind {spec.get('pooling_kind')!r}; a pooling spec is {SPEC_KIND} (campaigns as "
                         f"{{id, repetitions}})")
    for k in ("pool_id", "family_id", "campaigns"):
        if not spec.get(k):
            raise ValueError(f"{path}: a pooling spec names pool_id, family_id and campaigns")
    ids = []
    for c in spec["campaigns"]:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c.get("id"):
            raise ValueError(f"{path}: a campaign entry is {{id, repetitions}}, got {c!r}")
        reps = c.get("repetitions")
        if (not isinstance(reps, list) or not reps or any(isinstance(r, bool) or not isinstance(r, int) for r in reps)
                or len(set(reps)) != len(reps)):
            raise ValueError(f"{path}: {c['id']}: repetitions must be a non-empty list of distinct ints (the cohort is "
                             f"declared, never 'every repetition'), got {reps!r}")
        ids.append(c["id"])
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: a pooling names distinct campaigns, got {ids}")
    return spec


def taken(spec: dict) -> dict:
    """{campaign id: the repetitions the pool takes from it}, in the spec's order."""
    return {c["id"]: list(c["repetitions"]) for c in spec["campaigns"]}


def ids_phrase(ids) -> str:
    ids = sorted(ids)
    if len(ids) > 1 and ids == list(range(ids[0], ids[-1] + 1)):
        return f"{ids[0]} to {ids[-1]}"
    return ", ".join(str(i) for i in ids)


def cohort_note(record: dict, root) -> str | None:
    """How a pool's cohort differs from its campaigns' declared grids, for a label: "seeds 0 to 2" when a
    campaign's list is a strict subset of its declaration's repetitions (the word by the declaration's axis,
    AXIS_WORD; the campaign named when the pool has several), None when every campaign is whole."""
    campaigns = record["pooling"]["campaigns"]
    notes = []
    for c in campaigns:
        d = load_declaration(Path(root) / c["declaration"]["path"])
        if sorted(c["repetitions"]) != sorted(d.repetitions):
            word = AXIS_WORD.get(d.repetition_axis["kind"], "repetitions")
            notes.append(f"{word} {ids_phrase(c['repetitions'])}" + (f" of {c['id']}" if len(campaigns) > 1 else ""))
    return "; ".join(notes) or None


def with_cohort(label: str, cohort) -> str:
    """A family label with its cohort note appended, "Charger gauntlet (seeds 0 to 2)"; the label alone when
    the cohort is whole (None)."""
    return f"{label} ({cohort})" if cohort else label
