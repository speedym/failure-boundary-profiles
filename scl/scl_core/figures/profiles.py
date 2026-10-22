"""The paper's profile grid (2026-10-11): one agent per panel, the two curves of the paper on one axis.

Reverse-engineered from the paper's generated pgfplots sources (fig/atlas/charger_gaunlet.tex and its
siblings, fig/atlas/warehouse.tex, read 2026-10-11): each panel draws the observed commitment rate
(black) and competence rate (blue) per stratum as mid-point steps, an orange cell wherever the agent
commits more often than it passes cleanly (the cell runs from mid-point to mid-point between
neighbouring strata, from the competence rate up to the commitment rate), grey below the certified
floor (or over the certified-blocked strata where the family has no floor constant), a darker grey
over a stratum the defect register excluded, a dashed line at each located boundary, PlanT's second
and third checkpoints dotted, and a three-line title: the agent, "go: <boundary>", "clean: <boundary>"
(the warehouse: the decision counts, then "clean from: <boundary>").

This module is the DATA layer (backend-neutral, no matplotlib): it turns pooled documents into a
figure-data JSON a renderer draws (scl_core.figures.draw_profiles) in either design, the paper's as-is
or the hybrid that keeps the estimator's diamond and band. Every number comes from
scl_core.figures.curves.render_spec (the one curve renderer); nothing is refitted or re-rounded here
beyond the printed subtitle's three significant digits.
"""
from __future__ import annotations

import math

from scl_core.figures import curves as fc

KIND = "profile_figure/v0.1"
PAPER_QUANTITIES = {"commitment": "p_go", "competence": "p_clean"}   # eq. 3 and 4 of the paper
AXIS_PAD = 0.04            # the axis runs 4 % of the tested range past the first and last stratum (his xmin/xmax)
SUBTITLE_VERBS = {"commitment": ("always goes", "never goes"), "competence": ("always passes clean", "never passes clean")}


def fmt(v) -> str:
    """A boundary or interval edge as the subtitles print it: two decimals from 1 up, three below."""
    if v is None:
        return "open"
    return f"{v:.2f}" if abs(v) >= 1 else f"{v:.3f}"


def cells(xs: list, axis: list) -> list:
    """[(lo, hi, x)] per stratum: the mid-points between neighbouring strata, the first and last cell
    running to the axis ends (his orange and grey rectangles)."""
    xs = sorted(xs)
    out = []
    for i, x in enumerate(xs):
        lo = axis[0] if i == 0 else (xs[i - 1] + x) / 2
        hi = axis[1] if i == len(xs) - 1 else (x + xs[i + 1]) / 2
        out.append((round(lo, 4), round(hi, 4), x))
    return out


def axis_of(x_range: list) -> list:
    pad = AXIS_PAD * (x_range[1] - x_range[0])
    return [round(x_range[0] - pad, 4), round(x_range[1] + pad, 4)]


def rates(spec: dict) -> dict:
    """{x: rate} over the counted strata of a rendered spec (a defect stratum, no rate, is absent)."""
    return {o[0]: o[1] for o in spec.get("observed") or []}


def gap_cells(commitment: dict, competence: dict, strata_cells: list) -> list:
    """[[lo, hi, competence rate, commitment rate]] wherever the agent commits more often than it passes
    cleanly (both rates counted at that stratum)."""
    go, clean = rates(commitment), rates(competence)
    out = []
    for lo, hi, x in strata_cells:
        if x in go and x in clean and go[x] > clean[x]:
            out.append([lo, hi, clean[x], go[x]])
    return out


def ranges(flags: list, strata_cells: list) -> list:
    """Merged [lo, hi] ranges over the cells whose stratum is flagged (blocked, or a declared defect)."""
    out = []
    for (lo, hi, x), flag in zip(strata_cells, flags):
        if not flag:
            continue
        if out and abs(out[-1][1] - lo) < 1e-9:
            out[-1][1] = hi
        else:
            out.append([lo, hi])
    return out


def boundary_phrase(spec: dict, unit: str, quantity: str = "commitment") -> str:
    """One curve's boundary as the subtitle prints it, one phrase per named case of the fit stage
    (principle 14): a located logistic with its bootstrap interval; a bracket with its interval and the
    superscript marker; a flat curve by its reason; an open-sided bracket with the open side named."""
    kind = spec.get("kind")
    if kind in (None, "none"):
        return "no fit"
    reason = spec.get("reason") or ""
    if kind == "flat":
        level = spec.get("level")
        always, never = SUBTITLE_VERBS.get(quantity, SUBTITLE_VERBS["commitment"])
        if reason == "all_success":
            return "flat at 1.00, no boundary"
        if reason == "all_failure":
            return "flat at 0.00, no boundary"
        if reason == "out_of_range":
            return "not located in range"
        if reason == "no_crossing":
            return f"below tested range ({always})" if (level or 0) >= 0.5 else f"above tested range ({never})"
        if reason == "wrong_sign":
            return f"flat at {level:.2f}, wrong sign"
        return f"flat at {level:.2f}, {reason.replace('_', ' ')}"
    b = spec.get("boundary")
    if b is None:
        return f"not located ({reason.replace('_', ' ')})"
    if kind == "irls_pointonly":
        return f"{fmt(b)} {unit}, slope by inversions only"
    if kind == "irls" and spec.get("band_ci"):
        lo, hi = spec["band_ci"]
        return f"{fmt(b)} {unit} [{fmt(lo)}, {fmt(hi)}]"
    band = spec.get("band") or [None, None]
    lo, hi = sorted(band, key=lambda v: (v is None, v))
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return f"{fmt(b)} {unit} [{fmt(lo)}, {fmt(hi)}]$^{{b}}$"   # the superscript b of his legend: a bracket (mathtext)


def _short(spec: dict) -> str:
    """A checkpoint's boundary in the compressed form of a group subtitle: the number or 'none'."""
    b = spec.get("boundary")
    return fmt(b) if b is not None else "none"


def group_phrase(specs: list, unit: str, quantity: str) -> str:
    """The subtitle of a checkpoint group (PlanT 2.0's three): one phrase when all agree ("all 3" where a
    boundary is located), the one located checkpoint named with its interval when the others are flat,
    else the three numbers in order with the unit once (his form)."""
    phrases = [boundary_phrase(s, unit, quantity) for s in specs]
    located = [i for i, s in enumerate(specs) if s.get("boundary") is not None]
    if len(set(phrases)) == 1:
        return f"{phrases[0]}, all {len(specs)}" if located else phrases[0]
    if len(located) == 1:
        return f"ckpt {located[0] + 1}: {phrases[located[0]]}"
    return "ckpt " + "/".join(str(i + 1) for i in range(len(specs))) + ": " + " / ".join(_short(s) for s in specs) + f" {unit}"


def passable_counts(doc: dict, quantity: str) -> dict:
    """{k, n} of a quantity over the passable (or usable) strata, from the curve's points."""
    pts = [p for p in doc["curves"][quantity]["points"] if p.get("assignment") in ("passable", "usable")]
    return {"k": sum(p["k"] for p in pts), "n": sum(p["n"] for p in pts)}


def warehouse_lines(doc: dict, unit: str, competence_spec: dict) -> list:
    """The warehouse panel's two subtitle lines: the counts (the commitments on the passable gaps from the
    decomposition, the clean passes on them from the curve, the stops on the blocked gaps from the decision
    side; since 2026-10-12 the decision's passable side counts clean passes, so the go count is read from
    the decomposition), then the competence boundary as 'clean from'."""
    go, stop = doc["decomposition"]["p_go_passable"], doc["decision_summary"]["sides"]["blocked"]
    clean = passable_counts(doc, PAPER_QUANTITIES["competence"])
    phrase = boundary_phrase(competence_spec, unit, "competence")
    if competence_spec.get("kind") == "flat" and competence_spec.get("reason") == "all_success":
        phrase = "none, every passable width clean"
    return [f"passable: go {go['k']}/{go['n']}, clean {clean['k']}/{clean['n']}; blocked: stop {stop['k']}/{stop['n']}",
            f"clean from: {phrase}"]


def build_profiles(figure: str, *, family: str, label: str, axis: dict, panels: list, refs: dict,
                   x_range: list | None = None, columns: int = 4, warehouse: bool = False,
                   quantities: dict = PAPER_QUANTITIES, strip: dict | None = None, shade_blocked: bool = True) -> dict:
    """The figure data. `panels` is a list of {label, model, docs: [(model, doc, pool_id, doc_name)],
    campaigns, family_version} in the paper's order; a panel with no docs is drawn empty ("not run").
    A panel with several docs is a checkpoint group: the first draws solid, the rest dotted, the
    orange cells and the blocked/defect ranges follow the first. `refs` carries body_limit_m and
    policy_floor_m (CARLA corridor widths) or contact_width_m (the warehouse), or nothing."""
    unit = axis.get("unit") or "m"
    all_curves = [d["curves"][q] for p in panels for _, d, _, _ in p["docs"] for q in quantities.values()]
    x_range = x_range or fc.x_range_of(all_curves)
    if x_range is None:
        raise ValueError(f"{figure}: no numeric stratum among the documents of {family}")
    ax = axis_of(x_range)
    strata = sorted({p["x"] for c in all_curves for p in c["points"] if not isinstance(p["x"], str)})
    out_panels = []
    for p in panels:
        if not p["docs"]:
            out_panels.append({"label": p["label"], "model": p.get("model"), "status": "not run", "traces": [],
                               "gap_cells": [], "blocked": [], "defect": [], "subtitle": ["not run on this family"]})
            continue
        traces = []
        for i, (model, doc, pool_id, doc_name) in enumerate(p["docs"]):
            specs = {name: fc.render_spec(doc["curves"][q], x_range) for name, q in quantities.items()}
            traces.append({"model": model, "pool": pool_id, "document": doc_name, "style": "solid" if i == 0 else "dotted",
                           **specs})
        head = traces[0]
        head_doc = p["docs"][0][1]
        # the panel's own strata (a warehouse family's gap schedule is its own; a CARLA family's is shared):
        # the steps break only where THIS agent has no counted run, and the cells are its strata's
        panel_strata = sorted({pt["x"] for q in quantities.values() for pt in head_doc["curves"][q]["points"]
                               if not isinstance(pt["x"], str)})
        strata_cells = cells(panel_strata, ax)
        flags_blocked = [any(pt["x"] == x and pt.get("assignment") == "blocked"
                             for pt in head_doc["curves"][quantities["commitment"]]["points"]) for _, _, x in strata_cells]
        flags_defect = [any(pt["x"] == x and pt.get("assignment") == "defect"
                            for pt in head_doc["curves"][quantities["commitment"]]["points"]) for _, _, x in strata_cells]
        if warehouse:
            subtitle = warehouse_lines(head_doc, unit, head["competence"])
        elif len(traces) > 1:
            subtitle = [f"go: {group_phrase([t['commitment'] for t in traces], unit, 'commitment')}",
                        f"clean: {group_phrase([t['competence'] for t in traces], unit, 'competence')}"]
        else:
            subtitle = [f"go: {boundary_phrase(head['commitment'], unit, 'commitment')}",
                        f"clean: {boundary_phrase(head['competence'], unit, 'competence')}"]
        panel = {"label": p["label"], "model": p.get("model") or head["model"], "status": "drawn", "traces": traces,
                 "strata": panel_strata,
                 "gap_cells": gap_cells(head["commitment"], head["competence"], strata_cells),
                 "blocked": ranges(flags_blocked, strata_cells) if shade_blocked else [],
                 "defect": ranges(flags_defect, strata_cells),
                 "subtitle": subtitle, "campaigns": p.get("campaigns"), "family_version": p.get("family_version"),
                 "decision": head_doc.get("decision_summary", {}).get("sides")}
        if strip is not None:
            panel["composition"] = [{"x": c["x"], "assignment": c["assignment"], "n": c["n"], "outcomes": c["outcomes"]}
                                    for c in head_doc["outcome_composition"]]
        out_panels.append(panel)
    floor = refs.get("policy_floor_m")
    grey = [[ax[0], floor]] if floor is not None else None     # his grey runs to the certified floor where one exists
    legend = ["commitment P(go)", "competence P(clean pass)", "commits but not clean", "boundary (p = 0.5), where located"]
    if grey or any(p["blocked"] for p in out_panels):
        legend.append("widths certified blocked")
    if any(p["defect"] for p in out_panels):
        legend.append("scenario defect, excluded")
    if refs.get("body_limit_m") is not None:          # the dashed grey reference line every panel draws (2026-10-22)
        legend.append("body width, dashed grey")
    if refs.get("contact_width_m") is not None:
        legend.append("robot contact width, dashed grey")
    if any(len(p["traces"]) > 1 for p in out_panels):
        legend.append("checkpoint 1 solid, 2 and 3 dotted")
    legend += ["a break in a line: no counted runs there", "intervals: bootstrap CI, or bracket (b)"]
    return {"figure": figure, "kind": KIND, "family": family, "label": label, "axis_metric": axis.get("metric"),
            "unit": unit, "x_range": x_range, "axis": ax, "strata": strata, "quantities": dict(quantities),
            "refs": refs, "floor_range": grey, "panels": out_panels, "legend": legend,
            "layout": {"columns": columns, "warehouse": warehouse}, "strip": strip}


def rows_of(panels: list, columns: int) -> int:
    return math.ceil((len(panels) + 1) / columns)
