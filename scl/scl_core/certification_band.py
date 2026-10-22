"""The certification constants of a declared scoring instrument over a robot record: one formula, one place.

A gap instrument certifies passages against the robot's measured contact width w (the robot record's
`contact.width_m`, a pinch sweep) with a grid resolution and an epsilon it declares under
`certification`; the dead band is [w - 2*grid_res, w + 2*eps + 2*grid_res), published planner-facing in
the capability profile after the 2026-08-30 N5 probes. Until 2026-10-08 the Isaac derivation and both
figure tools read the width, the band and the resolution from the capability profile's policy
constants, which pinned the profile, the authoring vocabulary of the environment, into every Isaac
interpretation and so into the public export. Now the instrument declares eps and the resolution
beside the rule that names the resolution, the width is read from the robot record, and the band is
computed here wherever it is drawn; the profile keeps its planner-facing copy, test-checked equal
(scl_core/tests/test_certification_band.py). Stdlib-only, py3.10-safe.
"""

ROUND = 4   # the published band's precision (the profile's own formula test rounds the same)


def certification(instrument: dict, robot_record: dict) -> dict:
    """{contact_width_m, eps_m, grid_res_m, dead_band_m, source} from an instrument's `certification`
    block and a robot record's `contact.width_m`; refuses an instrument that declares no block."""
    cert = instrument.get("certification")
    if not isinstance(cert, dict) or "eps_m" not in cert or "grid_res_m" not in cert:
        raise ValueError(f"{instrument.get('instrument_id')!r} declares no certification block (eps_m, grid_res_m)")
    width = robot_record["contact"]["width_m"]["value"]
    if width is None:
        raise ValueError(f"{robot_record.get('robot_id')!r}: contact.width_m is not measured")
    width, eps, res = float(width), float(cert["eps_m"]), float(cert["grid_res_m"])
    return {"contact_width_m": width, "eps_m": eps, "grid_res_m": res,
            "dead_band_m": [round(width - 2 * res, ROUND), round(width + 2 * eps + 2 * res, ROUND)],
            "source": "the robot record's contact.width_m and the instrument's certification eps_m and grid_res_m; "
                      "band = [w - 2*grid_res, w + 2*eps + 2*grid_res)"}
