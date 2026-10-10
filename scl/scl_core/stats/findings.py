"""Read backend FindingsSummary v0.2 documents through a neutral view.

This is a translation, not a scorer: counts, exclusions, and source provenance
survive. Fits use natural coordinates, including a signed logistic slope.
Pinned backend documents need not change when a consumer adopts this reader.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

CURVE_IDS = ("p_go", "p_clean", "passage")
VIEW_KIND = "findings_view/v0.1"
# the axis kinds a document may declare (F35, 2026-09-14): numeric is the
# default every pinned document has; categorical names its levels and its
# points carry a level and no coordinate, fits are absent (nothing is
# monotone in an identity) and readers take ladder_series, not curve_series
AXIS_KINDS = ("numeric", "categorical")
AXES = {
    "measured_gap_m": ("m", "measured"),
    "minimum_free_corridor_width": ("m", "measured"),
    "maximum_route_intrusion": ("m", "measured"),
    "crossing_time_gap_s": ("s", "nominal"),
}


def _assignment(value):
    return "blocked" if value == "blockage" else value


def normalize_findings(document: dict) -> dict:
    """Return a detached, validated view of Isaac, static CARLA, or flow data.

    Null coordinates stay null. Bootstrap and fit scope are explicit. Legacy
    CARLA slopes were fitted on orientation*x; convert their slope to natural x.
    Backend-specific diagnostics remain in decomposition/provenance unchanged.
    """
    doc = deepcopy(document)
    if doc.get("findings_kind") == VIEW_KIND:
        validate_view(doc)
        return doc
    if doc.get("findings_kind") != "findings_summary/v0.2":
        raise ValueError("findings reader requires findings_summary/v0.2")
    isaac = "brain" in doc and "backend" not in doc
    metric = doc.get("axis_metric", "measured_gap_m" if isaac else None)
    axis = doc.get("axis")
    if axis is None:
        if metric not in AXES:
            raise ValueError(f"axis metadata required for metric {metric!r}")
        unit, basis = AXES[metric]
        axis = {"metric": metric, "unit": unit, "basis": basis}
    axis = dict(axis)
    if axis.get("kind", "numeric") not in AXIS_KINDS:
        raise ValueError(f"axis kind must be one of {AXIS_KINDS}")
    categorical = axis.get("kind", "numeric") == "categorical"   # pinned numeric documents keep their axis as is
    curves = {}
    for cid in CURVE_IDS:
        source = doc["curves"][cid]
        points = []
        for point in source["points"]:
            p = dict(point)
            p["x"] = p.pop("width_m") if "width_m" in p else p.get("x")
            p["assignment"] = _assignment(p.get("assignment"))
            points.append(p)
        if categorical:
            order = {level: i for i, level in enumerate(axis["levels"])}
            points.sort(key=lambda p: order.get(p.get("level"), len(order)))
        fit = source.get("fit", source.get("fit_passable"))
        boot = source.get("bootstrap")
        if fit is not None:
            fit = dict(fit)
            nested_boot = fit.pop("bootstrap", None)
            if boot is None:
                boot = nested_boot
            orientation = fit.get("orientation", 1)
            if orientation not in (-1, 1):
                raise ValueError("fit orientation must be +1 or -1")
            if not isaac and "fit_passable" in source and fit.get("slope") is not None:
                fit["slope"] *= orientation
            fit["orientation"] = orientation
            fit["slope_axis"] = "natural"
        curves[cid] = {
            "points": points, "fit": fit, "bootstrap": boot,
            "fit_scope": source.get("fit_scope", "none" if categorical else "passable"),
            "meaning": source.get("meaning"),
            "fit_note": source.get("fit_note", (fit or {}).get("fit_note")),
        }
    composition = doc["outcome_composition"]
    if isinstance(composition, dict) and categorical:
        assignments = {}
        for curve in curves.values():
            for p in curve["points"]:
                assignments.setdefault(p.get("level"), set()).add(p["assignment"])
        order = {level: i for i, level in enumerate(axis["levels"])}
        composition = [
            {"level": level, "x": None, "n": sum(counts.values()), "outcomes": counts,
             "assignment": (next(iter(assignments[level]))
                            if len(assignments.get(level, ())) == 1 else None)}
            for level, counts in sorted(composition.items(), key=lambda item: order.get(item[0], len(order)))
        ]
    elif isinstance(composition, dict):
        assignments = {}
        for curve in curves.values():
            for p in curve["points"]:
                assignments.setdefault(p["x"], set()).add(p["assignment"])
        composition = [
            {"x": float(x), "n": sum(counts.values()), "outcomes": counts,
             "assignment": (next(iter(assignments[float(x)]))
                            if len(assignments.get(float(x), ())) == 1 else None)}
            for x, counts in sorted(composition.items(), key=lambda item: float(item[0]))
        ]
    else:
        for c in composition:
            if "width_m" in c:
                c["x"] = c.pop("width_m")
            c["assignment"] = _assignment(c.get("assignment"))
    view = {
        "findings_kind": VIEW_KIND,
        "family_id": doc["family_id"],
        "campaign": doc.get("campaign"),
        "backend": doc.get("backend", "isaac" if isaac else None),
        "agent": doc.get("brain", doc.get("model")),
        "axis": axis, "curves": curves,
        "outcome_composition": composition,
        "decomposition": doc.get("decomposition", {}),
        "instrument": doc.get("instrument"),
        "provenance": doc.get("provenance", {}),
    }
    validate_view(view)
    return view


def validate_view(view: dict) -> None:
    """Check the common consumer contract without inferring missing evidence."""
    if view.get("findings_kind") != VIEW_KIND:
        raise ValueError(f"expected {VIEW_KIND}")
    axis = view["axis"]
    if not all(axis.get(k) for k in ("metric", "unit", "basis")):
        raise ValueError("axis requires metric, unit, and basis")
    kind = axis.get("kind", "numeric")
    if kind not in AXIS_KINDS:
        raise ValueError(f"axis kind must be one of {AXIS_KINDS}")
    categorical = kind == "categorical"
    if categorical and not (isinstance(axis.get("levels"), list) and axis["levels"]):
        raise ValueError("a categorical axis names its levels, in order")
    if not (view.get("agent") or {}).get("id"):
        raise ValueError("findings require an agent id")
    if set(view["curves"]) != set(CURVE_IDS):
        raise ValueError(f"findings require curves {CURVE_IDS}")
    for curve in view["curves"].values():
        if categorical:
            if curve["fit"] is not None:
                raise ValueError("a categorical axis carries no fit (nothing is monotone in an identity)")
            for p in curve["points"]:
                if p.get("x") is not None or p.get("level") not in axis["levels"]:
                    raise ValueError("categorical points carry a declared level and no coordinate")
        for p in curve["points"]:
            n, k = p["n"], p["k"]
            if not isinstance(n, int) or not isinstance(k, int) or not 0 <= k <= n:
                raise ValueError("point counts require integers 0 <= k <= n")
            if p["x"] is not None and not math.isfinite(p["x"]):
                raise ValueError("point x must be finite or null")
            if (n == 0 and p["rate"] is not None) or (
                    n and (p["rate"] is None or abs(p["rate"] - k / n) > 0.00051)):
                raise ValueError("point rate must agree with k/n (or be null for n=0)")
            lo, hi = p["wilson_95"]
            if not 0 <= lo <= hi <= 1:
                raise ValueError("point Wilson interval must lie in [0, 1]")
    for c in view["outcome_composition"]:
        if c["n"] != sum(c["outcomes"].values()):
            raise ValueError("outcome composition must account for every run")


def read_findings(path: str | Path) -> dict:
    """Load and normalize a document, retaining its exact source content hash."""
    path = Path(path)
    raw = path.read_bytes()
    view = normalize_findings(json.loads(raw))
    view["source"] = {"name": path.name, "sha256": hashlib.sha256(raw).hexdigest()}
    return view


def curve_series(view: dict, quantity: str) -> dict:
    """Backend-independent plotting/analysis input; null coordinates are omitted.
    A categorical axis has no coordinates: take ladder_series."""
    validate_view(view)
    if view["axis"].get("kind", "numeric") == "categorical":
        raise ValueError(f"axis {view['axis']['metric']!r} is categorical: use ladder_series, not curve_series")
    curve = view["curves"][quantity]
    points = sorted((p for p in curve["points"]
                     if p["x"] is not None and p["rate"] is not None), key=lambda p: p["x"])
    return {"axis": view["axis"], "points": points, "fit": curve["fit"],
            "bootstrap": curve["bootstrap"], "fit_scope": curve["fit_scope"]}


def ladder_series(view: dict, quantity: str) -> dict:
    """The categorical analog of curve_series: one point per declared level in
    the axis's own order (levels without runs are reported with n = 0), no
    fit, no orientation."""
    validate_view(view)
    axis = view["axis"]
    if axis.get("kind", "numeric") != "categorical":
        raise ValueError(f"axis {axis['metric']!r} is numeric: use curve_series, not ladder_series")
    by_level = {p["level"]: p for p in view["curves"][quantity]["points"]}
    points = [by_level.get(level, {"level": level, "x": None, "assignment": None, "n": 0, "k": 0,
                                   "rate": None, "wilson_95": [0.0, 1.0]}) for level in axis["levels"]]
    return {"axis": axis, "levels": list(axis["levels"]), "points": points,
            "fit": None, "bootstrap": None, "fit_scope": "none"}
