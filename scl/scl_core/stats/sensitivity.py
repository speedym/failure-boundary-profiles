"""Decision accuracy under perturbed certification constants (2026-10-17): the backend-neutral core of the
sensitivity sweep the paper's robustness paragraph reports.

The decision-accuracy ranking (Table 2) rests on declared constants inside the labels: a width is blocked
below a policy floor, a flow window is usable above the measured crossing floor plus a margin. The sweep
re-reads the same run rows under moved constants and overridden labels and asks whether the ranking holds.
Nothing runs again and no stored number moves: the sweep is a reader of the one rows shape
(scl_core.run_rows) and of a declaration (decision_sensitivity/v0.1, artifacts/examples/figure_specs/), the
one place that says what is moved and how far.

The declaration holds two tables and a design. A KNOB row names an instrument kind, a parameter of it and
the values to try; an OVERRIDE row names a family, its realization (manifest sha256), a variant id and the
label it takes. The design `one_at_a_time` (the paper's "separately") makes one setting per knob value
with the overrides off, and one setting per override at the instruments' own values; the BASELINE (own
values, no override) is the reference and must reproduce Table 2 (the adapter's test). What a knob does
to a row is the backend's: the adapter supplies `relabel(row, setting) -> assignment`, the core applies the
one decision rule (run_rows.decision) to the relabelled row, sums the balanced sides per family, computes
every agent's accuracy per setting through the one accuracy function (stats.decision.accuracy, the same
bootstrap as Table 2), and reports per agent the band over the perturbation settings and, per setting, the
order of agents. The declaration's REPORT block says which positions the paper's sentence claims (the top
ones held one agent at a time, the bottom ones held as a set); the sweep checks each claim at every
setting and derives the groups' bands, so "first", "second", "last two" and "the middle group" and their
numbers are read from the data, never typed; the record also names how deep the top and the bottom are
stable in fact.

Named cases (principle 14): a setting where an agent's family lacks a side carries no value and its case;
the band is over the settings with a value and lists the ones without; a declaration with no knob value and
no override has no perturbation and the record says so; an override that matches no row refuses; an
instrument or parameter the adapter does not admit refuses with the admitted names.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scl_core import run_rows as rr
from scl_core.stats import decision as da
from scl_core.stats.boundary import BOOTSTRAP_B, BOOTSTRAP_SEED

KIND = "decision_sensitivity/v0.1"
DESIGNS = ("one_at_a_time",)
BASELINE = "baseline"
OVERRIDE_KEYS = ("family_id", "manifest_sha256", "variant", "assignment")
REPORT_KEYS = ("top", "bottom")     # how many positions from the top (one agent each) and from the bottom (as a set) the paper claims
METHOD = ("one setting per knob value (overrides off) and one per override (the instruments' own values), the baseline "
          "beside them; per setting every run is relabelled by the backend's rule and decided by the one decision rule "
          "(run_rows.decision), the balanced sides are summed per family and the agent's accuracy is the equal-weight "
          "mean over its families with their bootstrap interval (stats.decision.accuracy, Table 2's); the band is the "
          "lowest and highest mean over the perturbation settings; the order per setting is by the mean, ties in roster "
          "order; a position is stable when one agent holds it at every setting (from the top) or one set of agents "
          "holds a block of positions at every setting (from the bottom)")


# --- the declaration -----------------------------------------------------------

def load_declaration(path) -> tuple:
    """(document, sha256) of a decision_sensitivity/v0.1 declaration; refuses any other kind."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    if doc.get("sensitivity_kind") != KIND:
        raise ValueError(f"{Path(path).name}: sensitivity_kind {doc.get('sensitivity_kind')!r}; this reader reads {KIND}")
    return doc, hashlib.sha256(raw).hexdigest()


def check_declaration(doc: dict, admitted: dict) -> dict:
    """The declaration's shape against what the caller admits: `admitted` is {instrument kind: [parameters]}, the
    backend's table of what a knob can move. A knob naming another instrument or parameter refuses with the
    admitted names; a knob without a non-empty list of numbers, an override missing a key or naming a label
    outside the rows' vocabulary, and a design outside DESIGNS refuse by name."""
    design = doc.get("design")
    if design not in DESIGNS:
        raise ValueError(f"design {design!r}; the designs are {list(DESIGNS)}")
    knobs, overrides = doc.get("knobs"), doc.get("overrides")
    if not isinstance(knobs, list) or not isinstance(overrides, list):
        raise ValueError("a declaration holds two tables, knobs and overrides (empty lists admitted)")
    for i, k in enumerate(knobs):
        inst, par, values = k.get("instrument"), k.get("parameter"), k.get("values")
        if inst not in admitted:
            raise ValueError(f"knobs[{i}]: instrument {inst!r} is not admitted; the admitted instruments are {sorted(admitted)}")
        if par not in admitted[inst]:
            raise ValueError(f"knobs[{i}]: parameter {par!r} of {inst!r} is not admitted; its parameters are {sorted(admitted[inst])}")
        if not isinstance(values, list) or not values or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            raise ValueError(f"knobs[{i}]: values must be a non-empty list of numbers (got {values!r})")
    report = doc.get("report")
    if not isinstance(report, dict) or any(not isinstance(report.get(k), int) or isinstance(report.get(k), bool) or report[k] < 0 for k in REPORT_KEYS):
        raise ValueError(f"report must hold {list(REPORT_KEYS)} as non-negative integers (the positions the paper's sentence claims); got {report!r}")
    for i, o in enumerate(overrides):
        missing = [key for key in OVERRIDE_KEYS if not o.get(key)]
        if missing:
            raise ValueError(f"overrides[{i}]: missing {missing}; an override names {list(OVERRIDE_KEYS)}")
        if o["assignment"] not in rr.ASSIGNMENTS:
            raise ValueError(f"overrides[{i}]: assignment {o['assignment']!r}; the assignments are {list(rr.ASSIGNMENTS)}")
    return doc


def settings(doc: dict) -> list:
    """The settings of a checked declaration under its design, the baseline first: {id, knob: {instrument,
    parameter, value} or None, overrides: [override rows]}. Under one_at_a_time every knob value is a setting
    with no override and every override a setting with no knob."""
    out = [{"id": BASELINE, "knob": None, "overrides": []}]
    for k in doc["knobs"]:
        for v in k["values"]:
            out.append({"id": f"{k['parameter']}={v:g}", "knob": {"instrument": k["instrument"], "parameter": k["parameter"], "value": v},
                        "overrides": []})
    for o in doc["overrides"]:
        out.append({"id": f"override:{o['family_id']}:{o['variant']}={o['assignment']}", "knob": None, "overrides": [dict(o)]})
    ids = [s["id"] for s in out]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"settings named twice: {dup} (a knob value or an override listed twice)")
    return out


# --- the sweep ------------------------------------------------------------------

def _sides(rows, relabel, setting, passage_attempt):
    """{family: {"pass": {n, k}, "wait": {n, k}}} of one agent's rows under one setting, every row relabelled
    and decided by the one rule; rows outside the balanced scope (conditional, mixed, unmeasured, defect, no
    measured commitment) count in neither side."""
    per = {}
    for r in rows:
        label = relabel(r, setting)
        correct, scope = rr.decision(label, r["commitment"], r["clean"],
                                     passage_attempt=passage_attempt(r) if passage_attempt else None)
        if scope != "balanced":
            continue
        side = per.setdefault(r["family"], {"pass": {"n": 0, "k": 0}, "wait": {"n": 0, "k": 0}})["pass" if label == "passable" else "wait"]
        side["n"] += 1
        side["k"] += int(bool(correct))
    return per


def _order(values: dict, roster) -> list:
    """Agents by value, highest first; ties in roster order, then by id; an agent without a value last."""
    rank = {m: i for i, m in enumerate(roster)}
    return sorted(values, key=lambda m: (values[m] is None, -(values[m] if values[m] is not None else 0.0), rank.get(m, len(rank)), m))


def sweep(rows, settings_list, relabel, families=None, passage_attempt=None, roster=(), report=None, B=BOOTSTRAP_B, seed=BOOTSTRAP_SEED) -> dict:
    """The sweep over rows of several agents (the one rows shape: family, model, variant, commitment, clean,
    assignment, annex). `relabel(row, setting) -> assignment` is the backend's; `families` is {model: [family
    ids]} entering each agent's mean (Table 2's families with a wait side), derived at the baseline when None
    (the families with both sides); `passage_attempt(row)` the chain's attempt bit where it is not the
    commitment; `roster` the tie order; `report` the declaration's {top, bottom} claim (None claims nothing).
    Returns the settings, the perturbations (every setting but the baseline), per agent the accuracy per
    setting with its sides, the baseline value, the band with the settings at its ends and the settings
    without a value, the order per setting, every agent's positions over the perturbations, the stable
    depths in fact, and the report: per claimed top position its baseline holder, whether it holds at every
    perturbation and its band; the claimed bottom set likewise; the middle (every other agent) with its band."""
    settings_list = list(settings_list)
    if not settings_list or settings_list[0]["id"] != BASELINE:
        raise ValueError(f"the settings start with the baseline {BASELINE!r} (stats.sensitivity.settings)")
    by_model = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
    for s in settings_list:
        for o in s["overrides"]:
            if not any(r["family"] == o["family_id"] and r["variant"] == o["variant"] for r in rows):
                raise ValueError(f"override {o['family_id']}/{o['variant']} matches no row (the rows hold "
                                 f"{sorted({r['variant'] for r in rows if r['family'] == o['family_id']})} of that family)")
    perturbations = [s["id"] for s in settings_list[1:]]
    agents, order = {}, {}
    for m, mrows in by_model.items():
        if families is not None and m not in families:
            continue
        base = _sides(mrows, relabel, settings_list[0], passage_attempt)
        fams = list(families[m]) if families is not None else [f for f, s in base.items() if s["pass"]["n"] and s["wait"]["n"]]
        per = {}
        for s in settings_list:
            sides = base if s["id"] == BASELINE else _sides(mrows, relabel, s, passage_attempt)
            block = da.accuracy([{"family": f, "pass": sides.get(f, {}).get("pass", {"n": 0, "k": 0}),
                                  "wait": sides.get(f, {}).get("wait", {"n": 0, "k": 0})} for f in fams], B=B, seed=seed)
            per[s["id"]] = {"value": block["value"], "interval": block["interval"], "case": block["case"],
                            "sides": {f: sides.get(f, {"pass": {"n": 0, "k": 0}, "wait": {"n": 0, "k": 0}}) for f in fams}}
        valued = [sid for sid in perturbations if per[sid]["value"] is not None]
        band = None
        if valued:
            lo, hi = min(per[s]["value"] for s in valued), max(per[s]["value"] for s in valued)
            band = {"low": lo, "high": hi, "at_low": [s for s in valued if per[s]["value"] == lo],
                    "at_high": [s for s in valued if per[s]["value"] == hi], "settings": len(valued)}
        agents[m] = {"families": fams, "settings": per, "baseline": per[BASELINE]["value"], "band": band,
                     "band_case": ("no perturbation: the declaration lists no knob value and no override" if not perturbations
                                   else "no value at any perturbation setting" if not valued
                                   else f"over {len(valued)} of {len(perturbations)} perturbation settings" if len(valued) < len(perturbations)
                                   else "measured"),
                     "settings_without_value": [sid for sid in per if per[sid]["value"] is None]}
    for s in settings_list:
        order[s["id"]] = _order({m: agents[m]["settings"][s["id"]]["value"] for m in agents}, roster)
    # positions are read at the settings where every agent has a value (a setting where one agent's family lost a
    # side ranks nobody: a named case, listed, never a position)
    ranked = [sid for sid in perturbations if all(agents[m]["settings"][sid]["value"] is not None for m in agents)]
    unranked = [sid for sid in perturbations if sid not in ranked]
    positions = {m: sorted({order[sid].index(m) + 1 for sid in ranked}) for m in agents}
    n = len(agents)
    baseline_order = order[BASELINE]

    def top_holds(p):
        """Position p (1-based) is held by one agent at every ranked perturbation setting."""
        return bool(ranked) and len({order[sid][p - 1] for sid in ranked}) == 1

    def bottom_holds(q):
        """The last q positions are held by one set of agents at every ranked perturbation setting."""
        return bool(ranked) and q > 0 and len({tuple(sorted(order[sid][n - q:])) for sid in ranked}) == 1

    top_depth = 0
    while top_depth < n and top_holds(top_depth + 1):
        top_depth += 1
    stable_bottom = [q for q in range(1, n + 1) if bottom_holds(q)]

    def group_band(members):
        bands = [agents[m]["band"] for m in members if agents[m]["band"]]
        return {"low": min(b["low"] for b in bands), "high": max(b["high"] for b in bands), "members": list(members)} if bands else None

    rep = None
    if report is not None and n:
        t, b = int(report["top"]), int(report["bottom"])
        if t + b > n:
            raise ValueError(f"the report claims {t} top and {b} bottom positions over {n} agents")
        top = [{"position": p, "agent": baseline_order[p - 1], "holds": top_holds(p), "band": group_band([baseline_order[p - 1]])}
               for p in range(1, t + 1)]
        bottom_agents = baseline_order[n - b:] if b else []
        middle = [m for m in baseline_order[t:n - b]]
        rep = {"top": top,
               "bottom": {"positions": list(range(n - b + 1, n + 1)), "agents": bottom_agents, "holds": bottom_holds(b) if b else None,
                          "band": group_band(bottom_agents)},
               "middle": {"positions": list(range(t + 1, n - b + 1)), "agents": middle, "band": group_band(middle),
                          "order_changes": len({tuple(m for m in order[sid] if m in middle) for sid in ranked}) > 1},
               "holds": all(x["holds"] for x in top) and (bottom_holds(b) if b else True)}

    return {"settings": [s["id"] for s in settings_list], "perturbations": perturbations, "ranked_perturbations": ranked,
            "unranked_perturbations": unranked, "agents": agents, "order": order, "positions": positions,
            "stable": {"top_depth": top_depth, "top": baseline_order[:top_depth], "bottom_depths": stable_bottom},
            "report": rep, "bootstrap": {"B": int(B), "seed": int(seed)}, "method": METHOD}
