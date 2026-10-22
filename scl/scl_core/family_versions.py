"""Family versions: a family id names a LINEAGE, its manifest hash names a REALIZATION (2026-10-03, Milan's
ruling after the composed-stacks episode: the stock brain's campaign and the fine-tunes' ran two realizations of
fm_isaac_004, pallet stacks and carton stacks, under one id, and every reader keyed on the id read them as one
family). The binding everywhere stays id + manifest hash, which the declarations, the records, the documents and
the raw snapshots already carry; the VERSION is a readable label over the hash, assigned here, never written as
truth into evidence.

The registry (artifacts/examples/family_versions/registry.json) is GENERATED from canon: the campaign
declarations (which hash each campaign bound) and canon's current manifests, merged with the hand overlay
(overlay.json beside it: per hash the one thing no tool can derive, what changed against the previous
version, and the stage-bytes verdict where it was measured). A hash without an overlay row refuses the
generation; the freshness test regenerates and compares, so a regenerated family or a new campaign never lands
without its row. Versions are numbered per family in the order of the campaigns that first bound each hash
(by campaign number); canon's current hash, when no campaign has bound it yet, is the last version with no
campaigns. Hashes canon once held between campaigns (edits no campaign bound) are history, not versions.

Stdlib-only and 3.10-compatible: the runners' preflight reads this under the backend envs.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

REGISTRY_KIND = "family_versions/v0.1"
# Its own directory, never solved_families/: every reader that globs that directory for manifests
# (boundary_stats.family_axes, score_runs) would read the registry as a family (caught 2026-10-03).
REGISTRY_PATH = "artifacts/examples/family_versions/registry.json"
OVERLAY_PATH = "artifacts/examples/family_versions/overlay.json"
CANON_CAMPAIGNS = "artifacts/examples/campaigns"
MANIFEST_PATHS = {"fail2drive": "artifacts/examples/solved_families/{family_id}.json",
                  "isaac": "artifacts/examples/solved_families/isaac/{family_id}/family_manifest.json"}
OVERLAY_FIELDS = ("what_changed",)                      # required per hash
OVERLAY_OPTIONAL = ("stage_bytes_vs_previous", "note", "label")   # label: the short tag that tells versions apart
                                                                   # where the number alone does not (pallets / cartons)
CANON_DECLARATION = "artifacts/examples/campaigns/{campaign_id}/{family_id}.json"
_CAMPAIGN_NUMBER = re.compile(r"_v(\d+)$")


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def campaign_number(campaign_id: str) -> int:
    m = _CAMPAIGN_NUMBER.search(campaign_id)
    return int(m.group(1)) if m else 10 ** 9


def bound_hashes(root) -> dict:
    """{(backend, family_id): {manifest_sha256: [campaign ids]}} from every declaration in canon."""
    out = {}
    for path in sorted(Path(root, CANON_CAMPAIGNS).glob("campaign_*/*.json")):
        doc = json.loads(path.read_text())
        fam = doc.get("family") or {}
        if not fam.get("family_id") or not fam.get("manifest_sha256"):
            continue
        key = (doc["backend"], fam["family_id"])
        out.setdefault(key, {}).setdefault(fam["manifest_sha256"], []).append(doc["campaign_id"])
    for key in out:
        for sha in out[key]:
            out[key][sha] = sorted(set(out[key][sha]), key=campaign_number)
    return out


def canon_manifest(root, backend, family_id):
    path = Path(root, MANIFEST_PATHS[backend].format(family_id=family_id))
    return path if path.is_file() else None


def manifest_plan(path) -> str | None:
    doc = json.loads(Path(path).read_text())
    return doc.get("plan_ref") or (doc.get("origin") or {}).get("plan_id")


def first_commit_of_content(root, rel_path, sha, git="git"):
    """The first commit in which canon's copy of rel_path had this content hash, or None (a realization that
    only ever lived in a raw bundle, like the first Isaac campaigns'). Needs git; None when unavailable."""
    try:
        log = subprocess.run([git, "-C", str(root), "log", "--format=%H %ad", "--date=short", "--follow", "--", rel_path],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in reversed([l for l in log.splitlines() if l.strip()]):
        commit, date = line.split()
        blob = subprocess.run([git, "-C", str(root), "show", f"{commit}:{rel_path}"], capture_output=True).stdout
        if blob and hashlib.sha256(blob).hexdigest() == sha:
            return {"commit": commit, "date": date}
    return None


def load_overlay(root) -> dict:
    path = Path(root, OVERLAY_PATH)
    return json.loads(path.read_text()) if path.is_file() else {}


def generate(root, git="git") -> dict:
    """The registry from canon + the overlay. Raises ValueError naming every hash without an overlay row."""
    root = Path(root)
    overlay = load_overlay(root)
    bound = bound_hashes(root)
    keys = set(bound)
    families, missing = [], []
    # canon's current families join the key set (a regenerated family awaiting its first campaign)
    for backend in MANIFEST_PATHS:
        base = Path(root, MANIFEST_PATHS[backend].format(family_id="X")).parent
        if backend == "isaac":
            for d in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
                if (d / "family_manifest.json").is_file():
                    keys.add((backend, d.name))
        else:
            for p in sorted(base.glob("fm_*.json")) if base.is_dir() else []:
                keys.add((backend, p.stem))
    for backend, family_id in sorted(keys):
        hashes = dict(bound.get((backend, family_id), {}))
        cur_path = canon_manifest(root, backend, family_id)
        cur_sha = _sha(cur_path) if cur_path else None
        if cur_sha and cur_sha not in hashes:
            hashes[cur_sha] = []
        ordered = sorted(hashes, key=lambda h: (campaign_number(hashes[h][0]) if hashes[h] else 10 ** 9, h))
        rel = MANIFEST_PATHS[backend].format(family_id=family_id)
        versions = []
        for i, sha in enumerate(ordered, 1):
            row = overlay.get(sha)
            if not row or any(not row.get(f) for f in OVERLAY_FIELDS):
                missing.append((backend, family_id, sha, hashes[sha]))
                row = row or {}
            found = first_commit_of_content(root, rel, sha, git) if git else None
            plan = manifest_plan(cur_path) if (cur_path and sha == cur_sha) else None
            versions.append({"version": i, "manifest_sha256": sha, "campaigns": hashes[sha],
                             "first_campaign": hashes[sha][0] if hashes[sha] else None,
                             "current": sha == cur_sha, "commit": (found or {}).get("commit"),
                             "canon_date": (found or {}).get("date"),
                             "plan_id": plan if plan else row.get("plan_id"),
                             "what_changed": row.get("what_changed"),
                             "stage_bytes_vs_previous": row.get("stage_bytes_vs_previous"),
                             "label": row.get("label"), "note": row.get("note")})
        families.append({"backend": backend, "family_id": family_id, "manifest_path": rel,
                         "current_version": next((v["version"] for v in versions if v["current"]), None),
                         "versions": versions})
    if missing:
        raise ValueError(f"{OVERLAY_PATH} has no 'what_changed' row for: " + "; ".join(
            f"{b} {f} {s[:12]} (campaigns {c or 'none, canon current'})" for b, f, s, c in missing))
    return {"schema_version": REGISTRY_KIND,
            "note": ("GENERATED by scripts/build_family_versions.py from the canon declarations and canon's current "
                     "manifests, merged with overlay.json beside it (hand-written: what changed per hash); "
                     "never hand-edit; a version is a label over the manifest hash the evidence binds"),
            "sources": {"declarations": sum(len(v) for b in bound.values() for v in b.values()),
                        "overlay_sha256": _sha(Path(root, OVERLAY_PATH)) if Path(root, OVERLAY_PATH).is_file() else None},
            "families": families}


def load(root) -> dict:
    return json.loads(Path(root, REGISTRY_PATH).read_text())


def index(registry: dict) -> dict:
    """{(backend, family_id, manifest_sha256): version row (with backend, family_id and the family's version
    count added)}."""
    out = {}
    for fam in registry["families"]:
        for v in fam["versions"]:
            out[(fam["backend"], fam["family_id"], v["manifest_sha256"])] = dict(
                v, backend=fam["backend"], family_id=fam["family_id"], versions_in_family=len(fam["versions"]))
    return out


def version_for(registry: dict, backend: str, family_id: str, manifest_sha256: str) -> dict:
    """The version row for a realization, or ValueError: a reader never guesses, it refuses."""
    row = index(registry).get((backend, family_id, manifest_sha256))
    if row is None:
        raise ValueError(f"{backend} {family_id} at manifest {manifest_sha256[:12]} is not in {REGISTRY_PATH}; "
                         f"add its overlay row and regenerate (scripts/build_family_versions.py --write)")
    return row


def label(row: dict) -> str:
    return f"v{row['version']}"


def display(row: dict, family_label: str) -> str:
    """The label a reader prints for a realization: the family's label, then ' vN' where the registry holds more
    than one version of the family (a number tells realizations apart; where there is only one it is noise),
    then the overlay's tag in parentheses ('Warehouse: composed stacks v2 (pallets)'). Display only: the figure
    data carries the version row beside every label whatever this prints."""
    out = family_label
    if row.get("versions_in_family", 1) > 1:
        out += f" {label(row)}"
        if row.get("label"):
            out += f" ({row['label']})"
    return out


def record(row: dict) -> dict:
    """The version leaf a figure or table writes beside a label: number, hash, tag."""
    return {"version": row["version"], "manifest_sha256": row["manifest_sha256"], "label": row.get("label")}


def declared_realization(root, campaign_id: str, family_id: str) -> tuple:
    """(backend, manifest_sha256) a canon declaration binds, or ValueError when the store holds none."""
    path = Path(root, CANON_DECLARATION.format(campaign_id=campaign_id, family_id=family_id))
    if not path.is_file():
        raise ValueError(f"{campaign_id}/{family_id}: the store holds no declaration "
                         f"({CANON_DECLARATION.format(campaign_id=campaign_id, family_id=family_id)})")
    doc = json.loads(path.read_text())
    return doc["backend"], doc["family"]["manifest_sha256"]


def document_realization(doc: dict, root=None) -> tuple:
    """(backend or None, family_id, manifest_sha256) a findings document binds. The hash is read where the
    one stamp puts it, provenance.rows_provenance.family_manifest_sha256 (every document of the one kind,
    F82, the pooled ones included), else where older emitters put it (provenance.family_manifest_sha256,
    provenance.scoring_provenance.manifest_sha256), else the declaration the document pins
    (campaign.declaration, or every campaign's under pooling.campaigns), read from root under the pinned
    hash (a declaration that changed since the document was emitted refuses). A document that names
    none is refused: a reader never guesses a realization."""
    fam = doc.get("family_id")
    backend = doc.get("backend")
    prov = doc.get("provenance") or {}
    sha = ((prov.get("rows_provenance") or {}).get("family_manifest_sha256") or prov.get("family_manifest_sha256")
           or (prov.get("scoring_provenance") or {}).get("manifest_sha256"))
    if sha:
        return backend, fam, sha
    pins = [p for p in [(doc.get("campaign") or {}).get("declaration"),
                        *[c.get("declaration") for c in (doc.get("pooling") or {}).get("campaigns") or []]] if p]
    if not pins:
        raise ValueError(f"{fam}: the document carries no family manifest hash (provenance.family_manifest_sha256) "
                         f"and pins no declaration (campaign.declaration, pooling.campaigns[].declaration); "
                         f"its realization is unknown")
    if root is None:
        raise ValueError(f"{fam}: the document pins its declaration; reading it needs the store root")
    shas, backends = set(), set()
    for pin in pins:
        path = Path(root, pin["path"])
        if not path.is_file():
            raise ValueError(f"{fam}: the pinned declaration {pin['path']} is not in the store")
        actual = _sha(path)
        if actual != pin["sha256"]:
            raise ValueError(f"{fam}: {pin['path']} hashes {actual[:12]}, the document pinned {pin['sha256'][:12]}: "
                             f"the declaration changed after the document was emitted")
        decl = json.loads(path.read_text())
        shas.add(decl["family"]["manifest_sha256"])
        backends.add(decl["backend"])
    if len(shas) != 1:
        raise ValueError(f"{fam}: the document's declarations pin {len(shas)} family manifests "
                         f"({sorted(s[:12] for s in shas)}); one document binds one realization")
    return backend or backends.pop(), fam, shas.pop()


def version_for_document(registry: dict, doc: dict, root=None) -> dict:
    """The version row of a findings document's realization (document_realization, then version_for). A
    document without a backend field (the Isaac emitter writes none) is placed by its family id when the
    registry holds that id under exactly one backend, else refused."""
    backend, fam, sha = document_realization(doc, root)
    if backend is None:
        homes = sorted({f["backend"] for f in registry["families"] if f["family_id"] == fam})
        if len(homes) != 1:
            raise ValueError(f"{fam}: the document names no backend and the registry holds the family under "
                             f"{homes or 'no backend'}")
        backend = homes[0]
    return version_for(registry, backend, fam, sha)


def declared_version(root, backend: str, family_id: str, manifest_path) -> int:
    """The launcher's preflight (both backends, 2026-10-03): the version the registry under root assigns to the
    manifest's hash. A missing registry or an unregistered hash refuses before any runner starts: no campaign
    runs on a realization the registry does not name (the registry is a deployed member of a box tree)."""
    if not Path(root, REGISTRY_PATH).is_file():
        raise ValueError(f"no family version registry at {REGISTRY_PATH} under {root}: deploy it with the tree; "
                         f"a campaign never runs on an unregistered realization")
    path = Path(manifest_path)
    if path.is_dir():                      # an Isaac family is a directory; its manifest is the member the hash binds
        path = path / "family_manifest.json"
    return version_for(load(root), backend, family_id, _sha(path))["version"]


def check_declared_version(root, doc: dict):
    """A declaration's family.version is a CLAIM checked where the declaration enters canon: when present it
    must be the registry's version for the declaration's hash (ValueError names both); absent (every declaration
    before 2026-10-03, the after-the-fact ones) nothing is claimed and the hash alone binds. Returns the
    registry's row, or None when the document claims nothing."""
    fam = doc.get("family") or {}
    claimed = fam.get("version")
    if claimed is None:
        return None
    row = version_for(load(root), doc["backend"], fam["family_id"], fam["manifest_sha256"])
    if row["version"] != claimed:
        raise ValueError(f"{doc.get('campaign_id')}/{fam['family_id']}: the declaration claims version {claimed}, the "
                         f"registry holds manifest {fam['manifest_sha256'][:12]} as v{row['version']}; the claim is "
                         f"wrong or the registry moved under it (an instrument change, never silent)")
    return row


def canon_target(root, backend: str, family_id: str, target) -> bool:
    """Is `target` the family's canon manifest path under root? (The writers refuse a canon target without a
    version note before they write anything, so a refusal never leaves canon half-written.)"""
    canon = Path(root, MANIFEST_PATHS[backend].format(family_id=family_id)).resolve()
    return Path(target).resolve() == canon


def gate_canon_manifest(root, backend: str, family_id: str, target, new_manifest_text: str, what_changed) -> dict:
    """The writers' gate as the family writers call it: when `target` is the family's canon manifest path the
    write goes through check_canon_write (a changed hash needs its note, the overlay row lands; the writer then
    regenerates the registry); a target anywhere else (artifacts/runs, a scratch tree) is not canon and passes
    untouched. Returns {"canon": bool, ...check_canon_write's leaves when canon}."""
    if not canon_target(root, backend, family_id, target):
        return {"canon": False}
    out = check_canon_write(root, backend, family_id, new_manifest_text.encode(), what_changed)
    return dict(out, canon=True)


def render_registry(reg: dict) -> str:
    """The registry's one serialization (the CLI's --check compares against it)."""
    return json.dumps(reg, indent=1, sort_keys=True) + "\n"


def regenerate(root, git="git") -> dict:
    """Write the registry from canon and the overlay (what the writers do after a canon manifest moved)."""
    reg = generate(root, git=git)
    Path(root, REGISTRY_PATH).write_text(render_registry(reg))
    return reg


def is_checkout(root, git="git") -> bool:
    """Whether `root` is a git checkout with a commit (a deployed box tree is not)."""
    try:
        return subprocess.run([git, "-C", str(root), "rev-parse", "HEAD"], capture_output=True).returncode == 0
    except OSError:
        return False


def require_note_for_canon(root, backend: str, family_id: str, target, what_changed) -> None:
    """The family writers' first line: a canon target needs its note before any work starts, so a refusal
    never leaves canon half-written (the row itself lands only when the hash moves). Raises ValueError."""
    if canon_target(root, backend, family_id, target) and not what_changed:
        raise ValueError(f"{Path(target)} is the canon family {family_id}; a canon regeneration needs --version-note "
                         f"(what changed against the previous version), written to {OVERLAY_PATH} when the manifest "
                         f"hash moves (2026-10-03)")


def write_canon_manifest(root, backend: str, family_id: str, target, manifest_text: str, what_changed, git="git") -> dict:
    """The ONE canon write every family writer makes (2026-10-11, principle 15, register I3: the sequence gate,
    write, regenerate, print had been copied into three writers and was absent from the fourth): gate the
    write (a canon target whose hash moves needs its note; the overlay row lands), write the manifest, and
    regenerate the registry when the hash moved and `root` is a git checkout. Returns the gate's dict plus
    `regenerated` and `message` (the line the writer prints). On a tree without git (a deployed box) the
    registry is left for scripts/build_family_versions.py --write on a checkout, which the freshness test
    holds the line for. A target outside canon is written untouched."""
    gate = gate_canon_manifest(root, backend, family_id, target, manifest_text, what_changed)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest_text)
    gate["regenerated"] = False
    if not gate.get("canon"):
        gate["message"] = f"{target}: not a canon target, the family version registry is untouched"
    elif not gate.get("changed"):
        gate["message"] = f"family version registry: {family_id} unchanged ({gate['new'][:12]})"
    elif git and is_checkout(root, git):
        regenerate(root, git=git)
        gate["regenerated"] = True
        gate["message"] = (f"family version registry: {family_id} moved {(gate['previous'] or 'none')[:12]} -> "
                           f"{gate['new'][:12]}; overlay row written, registry regenerated")
    else:
        gate["message"] = (f"family version registry: {family_id} moved {(gate['previous'] or 'none')[:12]} -> "
                           f"{gate['new'][:12]}; overlay row written; {root} is not a git checkout, so the registry "
                           f"is regenerated on one with scripts/build_family_versions.py --write (its freshness test "
                           f"refuses until then)")
    return gate


def check_canon_write(root, backend, family_id, new_manifest_bytes: bytes, what_changed: str | None) -> dict:
    """The writers' gate: writing a canon family whose manifest hash changes needs the overlay row (what
    changed), in the same change. Returns {"changed": bool, "previous": sha|None, "new": sha}. Raises ValueError
    when the hash changes and no note is given."""
    new_sha = hashlib.sha256(new_manifest_bytes).hexdigest()
    cur = canon_manifest(root, backend, family_id)
    prev = _sha(cur) if cur else None
    if prev == new_sha:
        return {"changed": False, "previous": prev, "new": new_sha}
    if not what_changed:
        raise ValueError(f"{backend} {family_id}: the family manifest would change ({(prev or 'none')[:12]} -> "
                         f"{new_sha[:12]}); a new version needs its 'what changed' note (--version-note) written "
                         f"to {OVERLAY_PATH} in the same change, never a silent regeneration")
    overlay = load_overlay(root)
    overlay[new_sha] = dict(overlay.get(new_sha, {}), what_changed=what_changed)
    Path(root, OVERLAY_PATH).write_text(json.dumps(overlay, indent=1, sort_keys=True) + "\n")
    return {"changed": True, "previous": prev, "new": new_sha}
