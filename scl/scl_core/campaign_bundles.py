"""Campaign bundles: the frozen evidence of a campaign and its interpretation,
two zips on both backends (F30, Milan ruling 2026-09-13/14).

The RAW bundle (`campaign_bundle/v0.3`) holds what the runner wrote and
nothing computed from it: the declared cells, the runner's campaign-level
files, the family snapshot, the declaration and the meta. Its content id
(the sha256 of MANIFEST.sha256) is the evidence id and never moves. The
DERIVED bundle (`derived_bundle/v0.2`) holds one interpretation of one raw
bundle: the scored rows, aggregates, findings, audit rows, baselines and
replay report a scoring run produced, plus a MANIFEST of the sources that
produced them (every file by repo-relative path and sha256, the git commit
they were read from, the runtime), keyed by the raw content id. No source
travels in the bundle (v0.2, 2026-09-19, Milan: the sources live in the
clones and on GitHub; a bundle carries hashes, the repository carries
bytes; v0.1 carried copies). A scorer change is a new derived bundle; the
raw one is untouched. The eleven historical bundles
(`campaign_bundle/v0.1`, `/v0.2`) are MIXED, raw and derived in one zip,
frozen for good (ruling 2026-09-08, F31); every reader here accepts them.

One deterministic writer serves every kind: entries sorted by the caller,
fixed timestamps, MANIFEST.sha256 over every data member, the provenance
record last; re-freezing unchanged content reproduces the zip byte for byte.

Stdlib-only and py3.10-safe: both adapters' campaign_manifest tools, the
certifiers and the unpack tool import this under any env (the admitted
list in CLAUDE.md, kept honest by scl_core/tests/test_stdlib_only_modules.py).
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from scl_core.records import SHA_CHUNK, sha256_file

ZIP_HASH_SIDECAR_KIND = "zip_sha256_sidecar/v0.1"

RAW_KIND = "campaign_bundle/v0.3"
LEGACY_KINDS = ("campaign_bundle/v0.1", "campaign_bundle/v0.2")   # mixed: raw + derived in one zip
RAW_KINDS = (RAW_KIND,) + LEGACY_KINDS                              # every bundle a derived one may key on
DERIVED_KIND = "derived_bundle/v0.3"
LEGACY_DERIVED_KINDS = ("derived_bundle/v0.1",   # carried source copies; re-issued, never read as current
                        "derived_bundle/v0.2")   # listed canon copies of the family and the declaration as
                                                 # sources (principle 13, 2026-09-21); re-issued the same day
SNAPSHOT_MANIFEST_NAMES = ("family_manifest.json",)   # the family manifest inside <family>/family_snapshot/:
                                                       # this name (Isaac) or <family_id>.json (CARLA)
MANIFEST = "MANIFEST.sha256"
PROVENANCE = "bundle_provenance.json"
SNAPSHOT_DIR = "source_snapshot"
SOURCE_MANIFEST = "source_manifest.json"          # <campaign>/source_snapshot/source_manifest.json, the one member there
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)   # fixed entry timestamp: bundle bytes depend only on contents


class BundleError(ValueError):
    """A bundle that is not what it claims, or content that may not enter one."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def zip_sha256(zip_path) -> str:
    """The archive's own hash, computed once per archive and kept beside it in `<zip>.sha256`
    (F71, 2026-10-08): the sidecar records the hash with the zip's size and mtime, and is used only
    while both still match, else the hash is recomputed and the sidecar rewritten (a folder that
    cannot be written to just costs the recomputation). The key is size and mtime, a proxy for
    the bytes: a zip rewritten to the same size with its mtime preserved would keep a stale hash;
    nothing in the chain writes a zip that way, and the content id (MANIFEST.sha256, read from
    inside the zip on every open) still verifies the members. Until 2026-10-08 every read of a
    bundle hashed the whole archive again, four to six times per campaign in a chain."""
    zip_path = Path(zip_path)
    st = zip_path.stat()
    sidecar = zip_path.with_name(zip_path.name + ".sha256")
    if sidecar.is_file():
        try:
            doc = json.loads(sidecar.read_text())
            if (doc.get("sidecar_kind") == ZIP_HASH_SIDECAR_KIND and doc.get("size") == st.st_size
                    and doc.get("mtime_ns") == st.st_mtime_ns and isinstance(doc.get("sha256"), str)):
                return doc["sha256"]
        except (json.JSONDecodeError, OSError):
            pass
    digest = sha256_file(zip_path)
    try:
        sidecar.write_text(json.dumps({"sidecar_kind": ZIP_HASH_SIDECAR_KIND, "zip": zip_path.name, "sha256": digest,
                                       "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                       "note": "the archive's sha256, reused while size and mtime match; delete to rehash"},
                                      indent=1) + "\n")
    except OSError:
        pass
    return digest


# --- writing ----------------------------------------------------------------

def manifest_text(entries) -> str:
    """One line per data member, `<sha256>  <arcname>`, in the caller's order."""
    return "\n".join(f"{sha256_file(src)}  {arc}" for arc, src in entries) + "\n"


def family_sub_ids(manifest: str, campaign_id: str, names) -> dict:
    """Per-family sub-id: the sha256 over that family's MANIFEST lines, so one
    family can be cited without a separate zip."""
    lines = manifest.splitlines()
    out = {}
    for name in names:
        sub = [l for l in lines if l.split("  ", 1)[1].startswith(f"{campaign_id}/{name}/")]
        out[name] = sha256_bytes(("\n".join(sub) + "\n").encode())
    return out


def write_zip(out_zip, campaign_id: str, entries, provenance: dict) -> dict:
    """Freeze `entries` ([(arcname, source path)], caller-sorted) under
    `<campaign_id>/` with MANIFEST.sha256 and the provenance record, whose
    manifest_sha256 (the content id) is filled in here. Returns the
    provenance as written."""
    out_zip = Path(out_zip)
    manifest = manifest_text(entries)
    provenance = dict(provenance, manifest_sha256=sha256_bytes(manifest.encode()))
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        def put(arcname, data=None, src=None):
            info = zipfile.ZipInfo(arcname, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data if data is not None else Path(src).read_bytes())
        for arc, src in entries:
            put(arc, src=src)
        put(f"{campaign_id}/{MANIFEST}", data=manifest)
        put(f"{campaign_id}/{PROVENANCE}", data=json.dumps(provenance, indent=1) + "\n")
    return provenance


# --- reading ----------------------------------------------------------------

def read_bundle(zip_path) -> dict:
    """What a bundle says about itself, verified: the campaign id (the one
    top-level directory), the kind, the provenance, the MANIFEST table
    (member path -> sha256), the content id (sha256 of MANIFEST.sha256, which
    the provenance must name) and the zip's own hash."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise BundleError(f"bundle not found: {zip_path}")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        provenance_members = [n for n in names if n.endswith("/" + PROVENANCE) and n.count("/") == 1]
        if len(provenance_members) != 1:
            raise BundleError(f"{zip_path.name}: expected exactly one <campaign_id>/{PROVENANCE}, "
                              f"found {provenance_members}")
        campaign_id = provenance_members[0].split("/", 1)[0]
        manifest_member = f"{campaign_id}/{MANIFEST}"
        if manifest_member not in names:
            raise BundleError(f"{zip_path.name}: no {manifest_member}; not a bundler product")
        manifest_bytes = z.read(manifest_member)
        provenance = json.loads(z.read(provenance_members[0]))
    listed = {}
    for line in manifest_bytes.decode("utf-8").splitlines():
        if line.strip():
            digest, _, path = line.partition("  ")
            listed[path] = digest
    content_id = sha256_bytes(manifest_bytes)
    if provenance.get("manifest_sha256") != content_id:
        raise BundleError(f"{zip_path.name}: {PROVENANCE} names manifest_sha256 "
                          f"{provenance.get('manifest_sha256')}, MANIFEST.sha256 hashes to {content_id}")
    foreign = [n for n in names if n not in listed and n not in (manifest_member, provenance_members[0])
               and not n.endswith("/")]
    if foreign:
        raise BundleError(f"{zip_path.name}: members not listed in MANIFEST.sha256: {foreign[:5]}")
    return {"path": zip_path, "campaign_id": campaign_id, "kind": provenance.get("bundle_kind"),
            "provenance": provenance, "listed": listed, "content_id_sha256": content_id,
            "zip_sha256": zip_sha256(zip_path)}


def member_hash(bundle: dict, path: str) -> str:
    """A member's MANIFEST-listed hash, re-verified against its bytes."""
    if path not in bundle["listed"]:
        raise BundleError(f"{bundle['path'].name}: member {path} is not listed in MANIFEST.sha256")
    with zipfile.ZipFile(bundle["path"]) as z:
        actual = sha256_bytes(z.read(path))
    if actual != bundle["listed"][path]:
        raise BundleError(f"{bundle['path'].name}: member {path} hashes to {actual}, "
                          f"MANIFEST lists {bundle['listed'][path]}")
    return actual


def member_sizes(zip_path, prefix="") -> dict:
    """{member path relative to `prefix`: uncompressed size} from the zip's central directory, for the
    members under `prefix` (directories excluded): what a tree check consults for a member the
    scoring unpack verified in place and left in the zip (F67 item 6, 2026-10-08: the recorder logs
    the bind's completeness check promises). Read in milliseconds; the bytes were hashed at unpack."""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.filename.endswith("/") or not info.filename.startswith(prefix):
                continue
            out[info.filename[len(prefix):]] = info.file_size
    return out


def unpack(zip_path, into, verify_only=None) -> dict:
    """Extract a bundle under `into/` (giving `into/<campaign_id>/...`, the
    layout the runner wrote), verifying every data member against
    MANIFEST.sha256 on the way, each member streamed in chunks (memory is a
    chunk, never a member; F71). A member that does not hash to its listing,
    or one that would land outside `into`, refuses the whole unpack; nothing
    is trusted that was not verified. `verify_only(name)`, when given, names
    the members to hash and leave in the zip (the runners' evidence no chain
    tool reads; the caller's policy, never this module's). Returns
    read_bundle's record plus `root`, the extracted campaign directory, and
    the counts: extracted, verified_in_place, bytes_extracted,
    bytes_verified_in_place."""
    bundle = read_bundle(zip_path)
    into = Path(into).resolve()
    root = into / bundle["campaign_id"]
    if root.exists():
        raise BundleError(f"{root} exists; unpack into an empty place (present is not verified)")
    bad, extracted, skipped, b_ex, b_skip = [], 0, 0, 0, 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            target = (into / name).resolve()
            if into not in target.parents:
                raise BundleError(f"{Path(zip_path).name}: member {name} escapes the target directory")
            expected = bundle["listed"].get(name)
            h = hashlib.sha256()
            if verify_only is not None and verify_only(name):
                with z.open(info) as src:
                    for chunk in iter(lambda: src.read(SHA_CHUNK), b""):
                        h.update(chunk)
                if expected is not None and h.hexdigest() != expected:
                    bad.append(name)
                skipped += 1
                b_skip += info.file_size
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as dst:
                for chunk in iter(lambda: src.read(SHA_CHUNK), b""):
                    h.update(chunk)
                    dst.write(chunk)
            if expected is not None and h.hexdigest() != expected:
                bad.append(name)
                target.unlink()
                continue
            extracted += 1
            b_ex += info.file_size
    if bad:
        raise BundleError(f"{Path(zip_path).name}: {len(bad)} member(s) do not hash to their MANIFEST "
                          f"listing: {bad[:5]}")
    bundle.update(root=root, extracted=extracted, verified_in_place=skipped,
                  bytes_extracted=b_ex, bytes_verified_in_place=b_skip)
    return bundle


# --- the raw-only gate ------------------------------------------------------

class RawTreeViolation(BundleError):
    """Content in a family-run tree that is not the runner's evidence."""


def raw_family_entries(family_dir, arc_prefix: str, campaign_files, cells: dict, extra_dirs=()) -> list:
    """The raw members of one family-run tree, or a refusal naming what does
    not belong. `campaign_files` are the file names the runner writes at the
    top level; `cells` maps each declared top-level directory (a model on
    CARLA, a variant on Isaac) to the set of cell directories declared under
    it (None: any); `extra_dirs` are top-level directories accepted whole
    (the family snapshot). Anything else is not evidence: a scoring
    directory, a stray probe, a derived folder left beside the tree."""
    family_dir = Path(family_dir)
    offenders, entries = [], []
    for p in sorted(family_dir.iterdir()):
        if p.is_file():
            if p.name in campaign_files:
                entries.append((f"{arc_prefix}/{p.name}", p))
            else:
                offenders.append(p.name)
        elif p.name in extra_dirs:
            entries.extend((f"{arc_prefix}/{q.relative_to(family_dir)}", q)
                           for q in sorted(p.rglob("*")) if q.is_file())
        elif p.name in cells:
            allowed = cells[p.name]
            for q in sorted(p.iterdir()):
                if q.is_dir() and (allowed is None or q.name in allowed):
                    entries.extend((f"{arc_prefix}/{r.relative_to(family_dir)}", r)
                                   for r in sorted(q.rglob("*")) if r.is_file())
                else:
                    offenders.append(f"{p.name}/{q.name}")
        else:
            offenders.append(p.name + "/")
    if offenders:
        vocabulary = ", ".join("%s/{%s}" % (d, "|".join(sorted(c)) if c else "*") for d, c in sorted(cells.items()))
        raise RawTreeViolation(
            "not the runner's evidence: %s; a raw bundle holds the declared cells (%s), the runner's "
            "campaign files (%s) and %s; derived outputs are frozen separately (--produce-derived)"
            % (offenders, vocabulary, ", ".join(sorted(campaign_files)),
               ", ".join(sorted(extra_dirs)) or "no other directory"))
    return entries


# --- the derived bundle -----------------------------------------------------

_IMPORT = None   # compiled lazily: `from scl_core.a.b import ...`, `import adapters.x.y` (the in-repo packages)
IN_REPO_PACKAGES = ("scl_core", "adapters")


def source_closure(seeds, root) -> list:
    """The files a derived folder's content depends on: every seed, plus every in-repo module
    a seed imports, transitively, read from the sources (`from scl_core.x.y import`,
    `import adapters.x.y`: the packages in IN_REPO_PACKAGES; a package import brings its
    __init__.py and the named member when it is a module). Keyed by what the producers read,
    never typed by hand (F44, 2026-09-19): a tool that only BINDS the derived bundle (a
    certifier, a fixture extractor) is not a seed, so an edit to it re-stales nothing; its hash
    is pinned in the record it writes. Sibling modules imported by bare name are not followed:
    a producer list names them."""
    import re
    global _IMPORT
    if _IMPORT is None:
        packages = "|".join(IN_REPO_PACKAGES)
        _IMPORT = re.compile(r"^\s*(?:from\s+((?:" + packages + r")(?:\.[\w]+)*)\s+import\s+([^\n#]+)"
                             r"|import\s+((?:" + packages + r")(?:\.[\w]+)+))", re.M)
    root = Path(root).resolve()

    def module_files(dotted):
        """scl_core.a.b -> [scl_core/__init__.py, scl_core/a/__init__.py, scl_core/a/b.py or b/__init__.py]"""
        parts = dotted.split(".")
        files = []
        for depth in range(1, len(parts) + 1):
            base = root.joinpath(*parts[:depth])
            if base.is_dir():
                init = base / "__init__.py"
                if init.is_file():
                    files.append(init)
            elif base.with_suffix(".py").is_file():
                files.append(base.with_suffix(".py"))
        return files

    seen, queue = set(), [Path(s).resolve() for s in seeds]
    while queue:
        path = queue.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        if path.suffix != ".py":
            continue
        for m in _IMPORT.finditer(path.read_text(encoding="utf-8", errors="replace")):
            package, names, plain = m.group(1), m.group(2), m.group(3)
            targets = []
            if plain:
                targets += module_files(plain)
            else:
                targets += module_files(package)
                for name in names.replace("(", "").replace(")", "").split(","):
                    name = name.strip().split(" as ")[0].strip()
                    if name:
                        targets += module_files(f"{package}.{name}")
            queue += [f for f in targets if f not in seen]
    return sorted(seen)


def measure_git(root, files=()) -> dict:
    """The commit the listed files are frozen against, measured from the checkout at `root`:
    HEAD's sha and, among `files` (under root), the ones git reports as modified, untracked or
    deleted against it. A commit describes the listed bytes only when `modified` is empty.
    Refuses when `root` is not a git checkout with a commit (measured or refused). The method
    is named."""
    import subprocess
    root = Path(root).resolve()

    def git(*args):
        try:
            done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        except OSError as exc:
            raise BundleError(f"git is not runnable here ({exc}); the source manifest names a commit") from None
        if done.returncode:
            raise BundleError(f"{root} is not a git checkout with a commit (git {args[0]}: "
                              f"{done.stderr.strip() or done.returncode})")
        return done.stdout
    commit = git("rev-parse", "HEAD").strip()
    try:
        listed = sorted(Path(f).resolve().relative_to(root).as_posix() for f in files)
    except ValueError as exc:
        raise BundleError(f"a listed source must live in the repository: {exc}") from None
    modified = set()
    if listed:
        for line in git("status", "--porcelain", "--untracked-files=all", "--", *listed).splitlines():
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if path:
                modified.add(path)
    return {"commit": commit, "modified": sorted(modified),
            "method": "git rev-parse HEAD; git status --porcelain over the listed files (a listed file git "
                      "reports is modified against the commit)"}


def source_differences(manifest_files, checkout) -> list:
    """Which listed sources a checkout does not hold as a source manifest hashes them: the
    missing and the differing paths, sorted."""
    checkout = Path(checkout).resolve()
    return sorted(rel for rel, digest in manifest_files.items()
                  if not (checkout / rel).is_file() or sha256_file(checkout / rel) != digest)


def lay_source_manifest(folder, files, root, runtime=None, git=None) -> str:
    """Write `<folder>/source_snapshot/source_manifest.json`: every file a derived folder's
    content depends on, by repo-relative path and sha256, the git identity of the checkout
    (measure_git over the listed files unless given) and the runtime (measure_runtime unless
    given). No copy travels: the derived bundle names the commit, and a checkout verifies
    against the manifest (source_differences; scripts/unpack_campaign_bundle.py
    --verify-sources). Returns the manifest file's sha256: the snapshot id every record pins.
    Deterministic for one tree, one interpreter and one git state, so a stage, a freeze and a
    bind lay the same manifest and agree by id. A file outside `root`, or one that is not a
    file, refuses."""
    root, folder = Path(root).resolve(), Path(folder)
    hashes = {}
    for source in sorted(Path(f).resolve() for f in files):
        try:
            relative = source.relative_to(root)
        except ValueError:
            raise BundleError(f"a listed source must live in the repository: {source}") from None
        if not source.is_file():
            raise BundleError(f"listed source is not a file: {relative}")
        hashes[relative.as_posix()] = sha256_file(source)
    manifest = {"files": dict(sorted(hashes.items())),
                "git": git if git is not None else measure_git(root, [root / p for p in hashes]),
                "runtime": runtime if runtime is not None else measure_runtime(),
                "note": ("the sources a derived folder's content depends on, by repo-relative path and sha256, "
                         "no copies: check the repository out at git.commit and verify it against this file; "
                         "the id of this snapshot is this file's sha256")}
    path = folder / SNAPSHOT_DIR / SOURCE_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return sha256_file(path)


def measure_runtime() -> dict:
    """The interpreter and the numeric libraries a scoring run depends on,
    as this process sees them (None where a library is absent). The method
    is named so a reader knows the scope."""
    runtime = {"python": sys.version.split()[0]}
    for name in ("numpy", "shapely"):
        try:
            module = __import__(name)
            runtime[name] = getattr(module, "__version__", None)
        except Exception:   # noqa: BLE001  (absent or broken: recorded as None, never guessed)
            runtime[name] = None
    try:
        import shapely   # noqa: F401
        runtime["geos"] = shapely.geos_version_string
    except Exception:   # noqa: BLE001
        runtime["geos"] = None
    runtime["method"] = "module __version__ as imported by the writer's interpreter; None = not importable"
    return runtime


def evidence_inputs(raw, declarations, root) -> dict:
    """What an interpretation took from the raw bundle and what it could not (principle 13,
    2026-09-21): for every declaration, the family manifest as run and the declaration itself are
    looked up AMONG THE RAW MEMBERS by hash. Returns {"raw_members": {family: {member: sha256}},
    "after_the_fact_inputs": [...], "canon_inputs": [...]}: a declaration the raw bundle does not
    hold was written after the fact and is listed as such (repo path, sha256, role); a family
    manifest the raw bundle does not hold (a bundle that predates its snapshot) was read from
    canon at the pinned hash and is listed as a canon input. A member present under another
    hash refuses: evidence and declaration disagree, and nothing is chosen between them."""
    listed = raw["listed"]
    campaign = raw["campaign_id"]
    out = {"raw_members": {}, "after_the_fact_inputs": [], "canon_inputs": []}
    for d in declarations:
        family = d.family_id
        members = {}
        candidates = [f"{campaign}/{family}/family_snapshot/{name}" for name in SNAPSHOT_MANIFEST_NAMES]
        candidates.append(f"{campaign}/{family}/family_snapshot/{family}.json")
        found = [m for m in candidates if m in listed]
        if found:
            member = found[0]
            if listed[member] != d.manifest_sha256:
                raise BundleError(f"{campaign}/{family}: the family snapshot in the raw bundle ({member}) hashes "
                                  f"{listed[member][:12]}, the declaration pins {d.manifest_sha256[:12]}: evidence "
                                  "and declaration disagree")
            members[member] = listed[member]
        else:
            out["canon_inputs"].append({"path": d.manifest_path.as_posix(), "sha256": d.manifest_sha256,
                                        "role": "family_manifest",
                                        "note": "the raw bundle predates its family snapshot; read from canon "
                                                "at the pinned hash (check_family_manifest)"})
        decl_member = f"{campaign}/{family}/campaign_declaration.json"
        if decl_member in listed:
            if listed[decl_member] != d.sha256:
                raise BundleError(f"{campaign}/{family}: the declaration in the raw bundle hashes "
                                  f"{listed[decl_member][:12]}, the one passed hashes {d.sha256[:12]}")
            members[decl_member] = listed[decl_member]
        else:
            path = Path(d.path).resolve()
            rel = path.relative_to(Path(root).resolve()).as_posix() if path.is_relative_to(Path(root).resolve()) \
                else path.as_posix()
            out["after_the_fact_inputs"].append({"path": rel, "sha256": d.sha256,
                                                 "role": "campaign_declaration",
                                                 "note": "written after the fact from the bundle's evidence; "
                                                         "not a raw member"})
        out["raw_members"][family] = members
    return out


def keep_previous_aside(out_zip, new_content_id) -> Path | None:
    """A re-issue never overwrites the previous interpretation (principle 12: re-scoring adds an
    interpretation beside the old one, "current" is a pointer): a derived zip already at `out_zip`
    whose content id differs from the one about to be written moves to
    `<archive>/superseded/<name>.<its content id, 12 hex>.zip`, where the store loader finds it by
    id (interpretation_by_id) and compare-lineages can name it; one with the same id is a
    byte-identical re-freeze and is simply rewritten. Returns the path kept aside, or None."""
    out_zip = Path(out_zip)
    if not out_zip.is_file():
        return None
    previous = read_bundle(out_zip)
    if previous["content_id_sha256"] == new_content_id:
        return None
    aside = out_zip.parent / "superseded" / f"{out_zip.stem}.{previous['content_id_sha256'][:12]}.zip"
    aside.parent.mkdir(parents=True, exist_ok=True)
    if aside.exists():
        out_zip.unlink()          # that interpretation is already kept under its id
    else:
        out_zip.rename(aside)
    print(f"previous interpretation {previous['content_id_sha256'][:12]} kept aside: {aside}")
    return aside


def write_derived_bundle(out_zip, folder, raw_zip, source_files, root, runtime=None,
                         writer_sha256=None, git=None, declarations=()) -> dict:
    """Freeze one interpretation of one raw bundle. `folder` is
    `<...>/<campaign_id>/` holding one subtree per family (the scoring
    driver's output); `raw_zip` the bundle it was computed from (raw-only,
    or a historical mixed one); `source_files` the files whose behavior the
    folder depends on, written as the source manifest here
    (lay_source_manifest, with `runtime` and `git` measured unless given),
    or None when the folder already carries source_snapshot/
    source_manifest.json (a certifier's stage laid it; files, git and
    runtime are read from it). No source is copied. Refused: a folder that
    is not named for the raw campaign, a family the raw bundle does not
    hold, an empty folder, a folder without a manifest when none is laid
    here, and any member that is a byte-copy of a raw member (evidence
    never enters a derived bundle, whatever it is called). `declarations`
    (CampaignDeclaration objects) name what the interpretation depended on
    beyond its sources: the family manifest as run and the declaration are
    recorded as RAW MEMBERS by hash (evidence_inputs), never as sources, and
    what the raw bundle does not hold is listed as after-the-fact or canon
    input (principle 13, 2026-09-21)."""
    folder, root = Path(folder), Path(root)
    raw = read_bundle(raw_zip)
    campaign_id = raw["campaign_id"]
    if raw["kind"] not in RAW_KINDS:
        raise BundleError(f"{raw['path'].name} is {raw['kind']!r}, not a raw bundle "
                          f"(one of {RAW_KINDS}); a derived bundle keys on evidence")
    if folder.name != campaign_id:
        raise BundleError(f"derived folder {folder.name!r} is not named for the raw bundle's "
                          f"campaign {campaign_id!r}")
    families = sorted(p.name for p in folder.iterdir() if p.is_dir() and p.name != SNAPSHOT_DIR)
    raw_families = raw["provenance"].get("families")
    if raw_families is not None:
        unknown = [f for f in families if f not in raw_families]
        if unknown:
            raise BundleError(f"derived families {unknown} are not in raw bundle {campaign_id} "
                              f"({sorted(raw_families)})")
    manifest_path = folder / SNAPSHOT_DIR / SOURCE_MANIFEST
    if source_files is not None:
        snapshot_id = lay_source_manifest(folder, source_files, root, runtime=runtime, git=git)
    elif manifest_path.is_file():
        snapshot_id = sha256_file(manifest_path)   # a stage laid it: the id is the file's own hash
    else:
        raise BundleError(f"derived folder {folder} carries no {SNAPSHOT_DIR}/{SOURCE_MANIFEST} and no sources "
                          "were passed to lay one")
    manifest = json.loads(manifest_path.read_text())
    entries = [(f"{campaign_id}/{p.relative_to(folder)}", p) for p in sorted(folder.rglob("*")) if p.is_file()]
    if len(entries) < 2:
        raise BundleError(f"derived folder {folder} holds no files beside the source manifest")
    raw_hashes = {sha for sha in raw["listed"].values()}
    # the gate protects evidence ids: against a RAW-ONLY bundle a byte-copy of
    # a raw member refuses; against a historical MIXED bundle (which already
    # held derived rows) identical bytes are a reproduction of those rows and
    # are recorded, never refused
    copies = [arc for arc, src in entries if sha256_file(src) in raw_hashes]
    if copies and raw["kind"] == RAW_KIND:
        raise BundleError(f"byte-copies of raw members may not enter a derived bundle: {copies[:5]}"
                          f"{' ...' if len(copies) > 5 else ''}")
    reproduced = copies or None
    evidence = evidence_inputs(raw, declarations, root)
    provenance = {
        "bundle_kind": DERIVED_KIND,
        "campaign_id": campaign_id,
        "families": families,
        "raw_bundle": {"path": raw["path"].name, "bundle_kind": raw["kind"],
                       "zip_sha256": raw["zip_sha256"], "content_id_sha256": raw["content_id_sha256"],
                       "families": {f: (raw_families or {}).get(f, {}).get("family_sub_id_sha256")
                                    for f in families}},
        "source_snapshot": {"files": dict(sorted(manifest["files"].items())),
                            "sha256": snapshot_id,
                            "id_method": f"sha256 of {SNAPSHOT_DIR}/{SOURCE_MANIFEST}",
                            "git": manifest.get("git")},
        "raw_members": evidence["raw_members"],
        "after_the_fact_inputs": evidence["after_the_fact_inputs"],
        "canon_inputs": evidence["canon_inputs"],
        "runtime": manifest.get("runtime"),
        "reproduced_raw_members": reproduced,
        "writer_sha256": writer_sha256,
        "note": ("one interpretation of one raw bundle: keyed by raw_bundle.content_id_sha256, "
                 "recomputed from the raw tree with the listed sources (the repository at "
                 "source_snapshot.git.commit, verified against the manifest; no copies travel) under "
                 "runtime; cite manifest_sha256 as the derived content id beside the raw one; no date, so "
                 "re-freezing an unchanged folder reproduces the zip byte for byte. raw_members: the family "
                 "manifest as run and the declaration, taken from the raw bundle by hash (principle 13); "
                 "after_the_fact_inputs: declarations the raw bundle does not hold; canon_inputs: files a "
                 "bundle that predates its snapshot made the chain read from canon, at the pinned hash"),
    }
    keep_previous_aside(out_zip, sha256_bytes(manifest_text(entries).encode()))
    return write_zip(out_zip, campaign_id, entries, provenance)
