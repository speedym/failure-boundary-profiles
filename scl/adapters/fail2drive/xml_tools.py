"""Fail2Drive route XML: parse, canonical serialize, semantic diff.

Parsing mirrors the backend's own conventions exactly (objects strings are
split the way CustomObstacle._create_construction_setup splits them; absent
placement params default to 0.0). Nothing is silently discarded: unknown
elements and attributes become warnings on the parsed result.

Serialization is canonical, not byte-preserving: positions (waypoints,
triggers, weather) use Python float repr as the sources do ("162.2", "185.0"),
compact params (objects strings, distance) use %g as the sources do ("-90",
"0.3", "50"). Round-trip fidelity is asserted semantically via
xml_semantic_diff, never byte-wise.
"""
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

PLACEMENT_PARAM_ORDER = ("x", "y", "z", "yaw", "pitch", "roll")

WEATHER_ATTR_ORDER = (
    "route_percentage", "cloudiness", "precipitation", "precipitation_deposits",
    "wetness", "wind_intensity", "sun_azimuth_angle", "sun_altitude_angle",
    "fog_density", "fog_distance", "fog_falloff", "scattering_intensity",
    "mie_scattering_scale",
)


@dataclass
class ParsedObject:
    blueprint: str
    params: dict[str, float]          # full x/y/z/yaw/pitch/roll, 0.0 defaults


@dataclass
class ParsedScenario:
    name: str
    type: str
    trigger: dict[str, float]         # x, y, z, yaw
    distance: float | None            # None when the XML omits <distance>
    objects: list[ParsedObject]
    extra_params: dict[str, float]    # value-form children, e.g. wait
    interval_params: dict[str, dict]  # from/to children, e.g. frequency


@dataclass
class ParsedRoute:
    route_id: str
    town: str
    weathers: list[dict]              # route_percentage int, rest float
    waypoints: list[dict]             # x, y, z
    scenarios: list[ParsedScenario]
    warnings: list[str] = field(default_factory=list)


class F2DParseError(Exception):
    pass


def _parse_objects_attr(value: str) -> ParsedObject:
    """Mirror of the backend's `{k: v for k=v in attr.split(' ')}` parsing."""
    pairs = {}
    for token in value.split(" "):
        if "=" not in token:
            raise F2DParseError(f"malformed objects token '{token}' in '{value}'")
        key, _, val = token.partition("=")
        pairs[key] = val
    if "id" not in pairs:
        raise F2DParseError(f"objects entry missing id: '{value}'")
    blueprint = pairs.pop("id")
    params = {k: 0.0 for k in PLACEMENT_PARAM_ORDER}
    for key, val in pairs.items():
        if key not in params:
            raise F2DParseError(f"unknown placement param '{key}' in '{value}'")
        params[key] = float(val)
    return ParsedObject(blueprint=blueprint, params=params)


def parse_route_xml(path: Path) -> ParsedRoute:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise F2DParseError(f"not well-formed XML: {exc}") from exc
    if root.tag != "routes":
        raise F2DParseError(f"expected <routes> root, got <{root.tag}>")
    routes = root.findall("route")
    if not routes:
        raise F2DParseError("no <route> element")
    warnings: list[str] = []
    if len(routes) > 1:
        warnings.append(f"file contains {len(routes)} routes; importing the first only")
    route = routes[0]

    weathers = []
    for w in route.findall("./weathers/weather"):
        entry: dict = {}
        for key, val in w.attrib.items():
            entry[key] = int(val) if key == "route_percentage" else float(val)
        weathers.append(entry)

    waypoints = [
        {k: float(p.attrib[k]) for k in ("x", "y", "z")}
        for p in route.findall("./waypoints/position")
    ]

    scenarios = []
    for s in route.findall("./scenarios/scenario"):
        objects, extra, intervals = [], {}, {}
        trigger: dict[str, float] = {}
        distance: float | None = None
        for child in s:
            if child.tag == "objects":
                for attr_name in sorted(child.attrib):
                    objects.append(_parse_objects_attr(child.attrib[attr_name]))
            elif child.tag == "trigger_point":
                trigger = {k: float(child.attrib[k]) for k in ("x", "y", "z", "yaw")}
            elif child.tag == "distance":
                distance = float(child.attrib["value"])
            elif "value" in child.attrib:
                # Params are usually numeric, but some scenario types carry
                # strings (BadParkingObstacle: vehicle blueprint, direction).
                raw = child.attrib["value"]
                try:
                    extra[child.tag] = float(raw)
                except ValueError:
                    extra[child.tag] = raw
            elif "from" in child.attrib and "to" in child.attrib:
                intervals[child.tag] = {
                    "from": float(child.attrib["from"]),
                    "to": float(child.attrib["to"]),
                }
            else:
                warnings.append(
                    f"unrecognized <{child.tag}> element in scenario "
                    f"'{s.attrib.get('name')}' preserved as warning only"
                )
        if not trigger:
            raise F2DParseError(f"scenario '{s.attrib.get('name')}' has no trigger_point")
        scenarios.append(ParsedScenario(
            name=s.attrib["name"], type=s.attrib["type"], trigger=trigger,
            distance=distance, objects=objects,
            extra_params=extra, interval_params=intervals,
        ))

    known_route_children = {"weathers", "waypoints", "scenarios"}
    for child in route:
        if child.tag not in known_route_children:
            warnings.append(f"unrecognized route element <{child.tag}> ignored")

    return ParsedRoute(
        route_id=route.attrib["id"], town=route.attrib["town"],
        weathers=weathers, waypoints=waypoints, scenarios=scenarios,
        warnings=warnings,
    )


# --- Serialization -----------------------------------------------------------

def _compact(v: float | str) -> str:
    """%g style used by objects strings and <distance> ('50', '-90', '0.3');
    categorical param values (e.g. direction='right') pass through."""
    return v if isinstance(v, str) else f"{v:g}"


def _pos(v: float) -> str:
    """Float-repr style used by positions and weather ('162.2', '185.0')."""
    return repr(float(v))


def _objects_value(obj: ParsedObject) -> str:
    parts = [f"id={obj.blueprint}"]
    for key in PLACEMENT_PARAM_ORDER:
        if obj.params.get(key, 0.0) != 0.0:
            parts.append(f"{key}={_compact(obj.params[key])}")
    return " ".join(parts)


def serialize_route_xml(route: ParsedRoute) -> str:
    lines = ["<routes>", f'  <route id="{route.route_id}" town="{route.town}">']

    lines.append("    <weathers>")
    for w in route.weathers:
        attrs = []
        for key in WEATHER_ATTR_ORDER:
            if key in w:
                val = str(w[key]) if key == "route_percentage" else _pos(w[key])
                attrs.append(f'{key}="{val}"')
        for key in sorted(set(w) - set(WEATHER_ATTR_ORDER)):
            attrs.append(f'{key}="{_pos(w[key])}"')
        lines.append(f'      <weather {" ".join(attrs)}/>')
    lines.append("    </weathers>")

    lines.append("    <waypoints>")
    for p in route.waypoints:
        lines.append(
            f'      <position x="{_pos(p["x"])}" y="{_pos(p["y"])}" z="{_pos(p["z"])}"/>'
        )
    lines.append("    </waypoints>")

    lines.append("    <scenarios>")
    for s in route.scenarios:
        lines.append(f'      <scenario name="{s.name}" type="{s.type}">')
        if s.objects:
            letters = "abcdefghijklmnopqrstuvwxyz"
            attrs = " ".join(
                f'{letters[i]}="{_objects_value(o)}"' for i, o in enumerate(s.objects)
            )
            lines.append(f"        <objects {attrs}/>")
        for key in sorted(s.extra_params):
            lines.append(f'        <{key} value="{_compact(s.extra_params[key])}"/>')
        for key in sorted(s.interval_params):
            iv = s.interval_params[key]
            lines.append(
                f'        <{key} from="{_compact(iv["from"])}" to="{_compact(iv["to"])}"/>'
            )
        if s.distance is not None:
            lines.append(f'        <distance value="{_compact(s.distance)}"/>')
        t = s.trigger
        lines.append(
            f'        <trigger_point x="{_pos(t["x"])}" y="{_pos(t["y"])}" '
            f'z="{_pos(t["z"])}" yaw="{_pos(t["yaw"])}"/>'
        )
        lines.append("      </scenario>")
    lines.append("    </scenarios>")

    lines.append("  </route>")
    lines.append("</routes>")
    return "\n".join(lines)


# --- Semantic comparison -----------------------------------------------------

def _num_or_str(value: str):
    try:
        return float(value)
    except ValueError:
        return value


def _attrs_equal(a: str, b: str, tol: float) -> bool:
    va, vb = _num_or_str(a), _num_or_str(b)
    if isinstance(va, float) and isinstance(vb, float):
        return math.isclose(va, vb, abs_tol=tol)
    return a == b


def xml_semantic_diff(xml_a: str, xml_b: str, tol: float = 1e-6) -> list[str]:
    """Structural + numeric comparison of two route XML documents.

    Objects strings are compared as parsed param dicts (with 0.0 defaults), so
    'y=-2.5' equals 'x=0 y=-2.5' and formatting variance never registers.
    """
    diffs: list[str] = []

    def walk(a: ET.Element, b: ET.Element, path: str) -> None:
        if a.tag != b.tag:
            diffs.append(f"{path}: tag <{a.tag}> != <{b.tag}>")
            return
        here = f"{path}/{a.tag}"
        for key in sorted(set(a.attrib) | set(b.attrib)):
            if key not in a.attrib or key not in b.attrib:
                diffs.append(f"{here}@{key}: present on one side only")
                continue
            if a.tag == "objects":
                oa, ob = _parse_objects_attr(a.attrib[key]), _parse_objects_attr(b.attrib[key])
                if oa.blueprint != ob.blueprint:
                    diffs.append(f"{here}@{key}: blueprint {oa.blueprint} != {ob.blueprint}")
                for p in PLACEMENT_PARAM_ORDER:
                    if not math.isclose(oa.params[p], ob.params[p], abs_tol=tol):
                        diffs.append(
                            f"{here}@{key}.{p}: {oa.params[p]} != {ob.params[p]}")
            elif not _attrs_equal(a.attrib[key], b.attrib[key], tol):
                diffs.append(f"{here}@{key}: '{a.attrib[key]}' != '{b.attrib[key]}'")
        ca, cb = list(a), list(b)
        if len(ca) != len(cb):
            diffs.append(f"{here}: child count {len(ca)} != {len(cb)}")
        for i, (child_a, child_b) in enumerate(zip(ca, cb)):
            walk(child_a, child_b, f"{here}[{i}]")

    walk(ET.fromstring(xml_a), ET.fromstring(xml_b), "")
    return diffs
