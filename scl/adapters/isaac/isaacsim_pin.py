"""The admitted Isaac Sim versions, as data (adapters/isaac/ISAACSIM_PIN; F21, F45).

One line per admitted version, `key=value` tokens separated by spaces:

    version=5.1.0.0 env=isaacsim python=3.11 audit=<report path> attestations=root

  version       what importlib.metadata reports for the isaacsim package
  env           the conda env that holds it (the launcher maps a declared version to it;
                the core env guard admits `isaacsim` and `isaacsim-<suffix>`)
  python        the interpreter the env was built with (informational)
  audit         the report that admitted the version (artifacts/examples/simulator_audits/isaac/<version>/
                gpu/<gpu slug>__<driver>/report.json from audit_simulator.py; for 5.1 the audit of
                2026-09-17 on the original box, which reproduced the August M0 report)
  attestations  where a family's physics countersignatures for this version live: `root` =
                the legacy file beside the manifest (5.1, produced before versions were keyed),
                `keyed` = <family>/attestations/<version>/

A version without a line is not admitted: the runner refuses it, the launcher refuses a spec
that declares it. Stdlib only (the runner reads it under the isaacsim env, the launcher on any
python3). Bump deliberately: a simulator version is an instrument (principle 5).

EVERY audit_simulator.py report, the admitting one included, lives keyed by what its outcome
depends on, the measured GPU and driver of its header, never by hostname (ruling 2026-09-21: one
shape; the pin is data and follows a move, so no layout is frozen by pinning). The reports live in the
results store, artifacts/examples/, because a report is what a run produced (principle 12); this pin
stays with the code because the runner gates on it:

    artifacts/examples/simulator_audits/isaac/<version>/gpu/<gpu slug>__<driver>/report.json   (+ report.md)

`report_key(header)` derives the key, `keyed_report_path` and `keyed_reports` the places; the pin
line's `audit` names which report admitted the version. m0_reference.json stays at the version
level: the numbers the version was admitted with (its `source` names the box), which every further
box's report is compared against, so a report says whether that box reproduces them.
"""
import re
from pathlib import Path

PIN_PATH = Path(__file__).resolve().parent / "ISAACSIM_PIN"
AUDITS_DIR = Path(__file__).resolve().parents[2] / "artifacts/examples/simulator_audits/isaac"   # the store, not the code
DEFAULTS = {"env": "isaacsim", "python": None, "audit": None, "attestations": "root"}


def read_admitted(path=PIN_PATH) -> list:
    """[{version, env, python, audit, attestations}] in file order; refuses an empty file."""
    admitted = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line.startswith("version="):
            continue
        entry = dict(DEFAULTS)
        for token in line.split():
            key, _, value = token.partition("=")
            if not value:
                raise ValueError(f"{path}: token {token!r} is not key=value")
            entry[key] = value
        admitted.append(entry)
    if not admitted:
        raise ValueError(f"{path} admits no version (no version= line)")
    versions = [e["version"] for e in admitted]
    if len(set(versions)) != len(versions):
        raise ValueError(f"{path} lists a version twice: {versions}")
    return admitted


def admitted_versions(path=PIN_PATH) -> list:
    return [e["version"] for e in read_admitted(path)]


def entry_for(version, path=PIN_PATH) -> dict:
    """The admitted entry for a version; ValueError naming the admitted ones otherwise."""
    for entry in read_admitted(path):
        if entry["version"] == version:
            return entry
    raise ValueError(f"isaacsim {version!r} is not admitted by {path} (admitted: {admitted_versions(path)}); "
                     "an audit report admits a version (audit_simulator.py), never a run")


def env_for(version, path=PIN_PATH) -> str:
    return entry_for(version, path)["env"]


def resolve(declared=None, path=PIN_PATH) -> dict:
    """The entry a run is keyed to: the declared version when given; the only admitted one when
    nothing is declared and one is admitted; ValueError when several are admitted and none is
    declared (a campaign declares its simulator version the day two are admitted)."""
    admitted = read_admitted(path)
    if declared is not None:
        return entry_for(declared, path)
    if len(admitted) == 1:
        return admitted[0]
    raise ValueError(f"{path} admits {[e['version'] for e in admitted]}: declare the version "
                     "(the spec's simulator.version, the runner's --isaacsim-version)")


def report_key(header) -> str:
    """The key every audit report is filed under: the measured GPU name and driver of its header,
    `<gpu slug>__<driver>` (ruling 2026-09-21). Refuses a header that lacks either: the key is what
    the outcome depends on, never a hostname."""
    gpu, driver = header.get("gpu"), header.get("driver")
    if not gpu or not driver:
        raise ValueError("a report key needs the measured gpu and driver in the report header")
    slug = re.sub(r"[^a-z0-9.]+", "_", str(gpu).lower()).strip("_")
    return f"{slug}__{driver}"


def keyed_report_path(version, header, root=AUDITS_DIR) -> Path:
    """<store>/simulator_audits/isaac/<version>/gpu/<key>/report.json for this header."""
    return Path(root) / version / "gpu" / report_key(header) / "report.json"


def keyed_reports(version, root=AUDITS_DIR) -> list:
    """Every report on record for a version, the admitting one included: [(key, path)] by key."""
    d = Path(root) / version / "gpu"
    return sorted((p.parent.name, p) for p in d.glob("*/report.json")) if d.is_dir() else []
