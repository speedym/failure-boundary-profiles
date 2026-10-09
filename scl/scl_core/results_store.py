"""The results store: where a run's interpretations and documents are read from.

Principle 12 (2026-09-19): results are store-addressed by hash, never
git-addressed. Today the store is two places that this module hides behind
one loading function: the findings directory in the repository (documents
and certification records) and an archive of derived zips (interpretations,
keyed by the content id the records pin), unpacked once into a cache keyed
by that id. Tomorrow a database sits behind the same functions.

An interpretation is everything a scoring run produced from one raw bundle
at one commit: scored rows, stats rows, findings documents, audits. A
figure or a pooling tool asks for a campaign's interpretation and gets the
folder the derived zip unpacks to, verified member by member (the zip is an
export of the store, principle 12's corollary); it never reads a zip
itself. Stdlib-only and py3.10-safe (the admitted list in CLAUDE.md).
"""
import json
import os
import shutil
from pathlib import Path

from scl_core import campaign_bundles as cb

CANON_RECORDS = "artifacts/examples/findings/certification"
CANON_FINDINGS = "artifacts/examples/findings"
CACHE_DIR = "interpretations"                             # <archive>/interpretations/<id>/: beside the zips it unpacks
MARKER = "INTERPRETATION"                                 # <cache>/<id>/INTERPRETATION names the id unpacked there


class StoreError(ValueError):
    """The store cannot answer: no record, records that disagree, an archive that holds another bundle."""


def default_archive() -> Path:
    """Where the derived zips live: SCL_CAMPAIGN_BUNDLES or ~/scl/campaigns (the tests read the same)."""
    return Path(os.environ.get("SCL_CAMPAIGN_BUNDLES", Path.home() / "scl/campaigns"))


def default_cache(archive=None) -> Path:
    """Where interpretations unpack when the caller names no cache: `<archive>/interpretations/`,
    beside the zips they come from (the archive given, else the store's). Never under a checkout:
    a cache placed relative to the code followed the code into the generated public tree (F72,
    2026-10-07). An entry is keyed by the derived zip's content id and verified at unpack, so no
    code change invalidates it: a re-stamp pins a new id in the records, and the old entry is an
    orphan `prune` removes. The launcher's verbs name a cache under their own work tree instead."""
    return (Path(archive) if archive is not None else default_archive()) / CACHE_DIR


def pinned_interpretations(root) -> dict:
    """{content id: sorted campaigns} of every derived bundle any certification record under
    CANON_RECORDS names, with no agreement check (`interpretation_id` is the strict reader):
    what the cache keeps."""
    out = {}
    base = Path(root) / CANON_RECORDS
    for path in sorted(base.glob("*/*.json")) if base.is_dir() else []:
        doc = json.loads(path.read_text())
        for families in doc.get("campaigns", {}).values():
            for entry in families.values():
                derived = entry.get("derived_bundle") if isinstance(entry, dict) else None
                if derived:
                    out.setdefault(derived["content_id_sha256"], set()).add(path.parent.name)
    return {cid: sorted(names) for cid, names in sorted(out.items())}


def prune(root, archive=None, cache=None) -> dict:
    """Remove every cache entry no certification record pins: an interpretation a re-stamp
    superseded (its zip went to the archive's superseded folder; `interpretation_by_id` unpacks it
    again on demand), an entry whose marker names another id, and a half unpack (no marker; every
    reader refuses it and says remove it). {"cache", "kept": [ids], "removed": [{"id", "reason",
    "bytes"}]}; a cache that does not exist is an empty one."""
    cache = Path(cache) if cache is not None else default_cache(archive)
    pinned = set(pinned_interpretations(root))
    report = {"cache": str(cache), "kept": [], "removed": []}
    if not cache.is_dir():
        return report
    for entry in sorted(cache.iterdir()):
        if not entry.is_dir():
            continue
        marker = entry / MARKER
        marked = marker.read_text().split()[0] if marker.is_file() else None
        if marked is None:
            reason = "half unpack (no marker)"
        elif marked != entry.name:
            reason = "marker names another interpretation"
        elif marked not in pinned:
            reason = "no certification record pins it"
        else:
            report["kept"].append(entry.name)
            continue
        size = sum(p.stat().st_size for p in entry.rglob("*") if p.is_file())
        shutil.rmtree(entry)
        report["removed"].append({"id": entry.name, "reason": reason, "bytes": size})
    return report


def read_jsonl(path) -> list:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def interpretation_id(root, campaign) -> dict:
    """The interpretation a campaign's certification records pin: {"content_id_sha256", "path",
    "records"}. Every entry of every record file under certification/<campaign>/ that names a
    derived bundle must name the same one; a campaign with no derived binding, or with records
    that disagree, refuses with the names."""
    base = Path(root) / CANON_RECORDS / campaign
    files = sorted(base.glob("*.json")) if base.is_dir() else []
    if not files:
        raise StoreError(f"{campaign}: no certification record under {CANON_RECORDS}/{campaign}/")
    pins = {}
    for path in files:
        doc = json.loads(path.read_text())
        for families in doc.get("campaigns", {}).values():
            for entry in families.values():
                derived = entry.get("derived_bundle")
                if derived:
                    pins.setdefault((derived["content_id_sha256"], derived["path"]), []).append(path.name)
    if not pins:
        raise StoreError(f"{campaign}: its records bind no derived bundle (a mixed historical bundle without "
                         "an interpretation; issue one with campaign_manifest.py --produce-derived)")
    if len(pins) > 1:
        raise StoreError(f"{campaign}: its records pin different interpretations: "
                         + "; ".join(f"{cid[:12]} in {sorted(set(names))}" for (cid, _), names in pins.items()))
    (content_id, path), names = next(iter(pins.items()))
    return {"content_id_sha256": content_id, "path": path, "records": sorted(set(names))}


def interpretation(root, campaign, archive=None, cache=None) -> Path:
    """The folder of a campaign's interpretation (`<cache>/<content id>/<campaign>/`), unpacked
    once from the archive's derived zip and verified member by member on the way. The zip must
    be the one the records pin (its content id); another zip under the same name refuses. A
    cache entry is trusted only when its marker names the id; a half-unpacked one refuses and
    names itself, so nothing unverified is ever read."""
    root = Path(root)
    pinned = interpretation_id(root, campaign)
    archive = Path(archive) if archive is not None else default_archive()
    cache = Path(cache) if cache is not None else default_cache(archive)
    target = cache / pinned["content_id_sha256"]
    marker = target / MARKER
    if marker.is_file():
        if marker.read_text().split()[0] == pinned["content_id_sha256"]:
            return target / campaign          # cached: the archive is not touched again
        raise StoreError(f"{target}: its marker names another interpretation; remove it")
    if target.exists():
        raise StoreError(f"{target} exists without a marker (a half unpack); remove it")
    zip_path = archive / pinned["path"]
    if not zip_path.is_file():
        raise StoreError(f"{campaign}: the archive {archive} holds no {pinned['path']} (the records pin "
                         f"interpretation {pinned['content_id_sha256'][:12]})")
    bundle = cb.read_bundle(zip_path)
    if bundle["content_id_sha256"] != pinned["content_id_sha256"]:
        raise StoreError(f"{zip_path.name} is interpretation {bundle['content_id_sha256'][:12]}, the records of "
                         f"{campaign} pin {pinned['content_id_sha256'][:12]} (a superseded or a newer zip)")
    if bundle["campaign_id"] != campaign:
        raise StoreError(f"{zip_path.name} is an interpretation of {bundle['campaign_id']}, not of {campaign}")
    return _unpack_interpretation(zip_path, bundle, cache) / campaign


def scored_dir(folder, family) -> Path:
    return Path(folder) / family / "scoring_out"


def stats_dir(folder, family) -> Path:
    return Path(folder) / family / "stats"


def findings_documents(root, campaign=None, family=None, model=None) -> list:
    """The findings documents in the store, as (path, document) pairs, narrowed by campaign,
    family and model when given; pooled documents (findings_pooled) carry no campaign and are
    returned only when no campaign is asked for."""
    out = []
    for path in sorted((Path(root) / CANON_FINDINGS).glob("**/f[sp]_*.json")):
        doc = json.loads(path.read_text())
        doc_campaign = (doc.get("campaign") or {}).get("id")
        if campaign is not None and doc_campaign != campaign:
            continue
        if family is not None and doc.get("family_id") != family:
            continue
        if model is not None and (doc.get("model") or {}).get("id") != model:
            continue
        out.append((path, doc))
    return out


def raw_member(archive, raw_zip_name, member, sha256) -> bytes:
    """One member of a raw bundle in the archive, read without unpacking the zip and verified
    against the hash the interpretation recorded for it (bundle_provenance.raw_members, principle
    13): the family manifest as run, for a store-side tool that has an interpretation folder and
    no results tree. Refuses a missing zip, a missing member and a hash that does not match."""
    import hashlib
    import zipfile
    path = Path(archive) / raw_zip_name
    if not path.is_file():
        raise StoreError(f"the archive {archive} holds no {raw_zip_name}")
    with zipfile.ZipFile(path) as z:
        try:
            data = z.read(member)
        except KeyError:
            raise StoreError(f"{raw_zip_name} holds no member {member}") from None
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise StoreError(f"{raw_zip_name}:{member} hashes {digest[:12]}, the interpretation recorded {sha256[:12]}")
    return data


def interpretation_by_id(root, campaign, content_id, archive=None, cache=None) -> Path:
    """The folder of ONE interpretation of a campaign named by its content id (a prefix of at
    least 12 hex digits is enough), whether or not it is the one the records pin: the cache
    first, else every derived zip in the archive and its `superseded*` folders whose provenance
    carries that id, unpacked and verified like `interpretation`. Refuses an id nothing holds,
    naming the places searched."""
    archive = Path(archive) if archive is not None else default_archive()
    cache = Path(cache) if cache is not None else default_cache(archive)
    if cache.is_dir():
        for entry in sorted(cache.iterdir()):
            marker = entry / MARKER
            if entry.name.startswith(content_id) and marker.is_file() and (entry / campaign).is_dir():
                return entry / campaign
    searched = []
    folders = [archive] + sorted(p for p in archive.glob("superseded*") if p.is_dir())
    for folder in folders:
        for zip_path in sorted(folder.glob(f"{campaign}_derived*.zip")):
            searched.append(zip_path)
            bundle = cb.read_bundle(zip_path)
            if bundle["campaign_id"] == campaign and bundle["content_id_sha256"].startswith(content_id):
                return _unpack_interpretation(zip_path, bundle, cache) / campaign
    raise StoreError(f"{campaign}@{content_id[:12]}: no interpretation with that id under {cache} or in "
                     f"{[str(p) for p in searched] or [str(archive)]}")


def interpretation_from_zip(zip_path, cache=None) -> Path:
    """The folder of the interpretation a derived zip holds, unpacked once into the cache (the
    store's, beside its archive, unless named) under the zip's own content id and verified member
    by member; the zip is admitted as an input only through this unpack, so the comparison and
    every store-side tool read folders, never zips."""
    zip_path = Path(zip_path)
    cache = Path(cache) if cache is not None else default_cache()
    bundle = cb.read_bundle(zip_path)
    if bundle["kind"] not in (cb.DERIVED_KIND, *cb.LEGACY_DERIVED_KINDS):
        raise StoreError(f"{zip_path.name} is {bundle['kind']!r}, not a derived bundle: an interpretation is what a "
                         "derived zip holds; a raw zip is evidence and is scored, never compared as an interpretation")
    return _unpack_interpretation(zip_path, bundle, cache) / bundle["campaign_id"]


def _unpack_interpretation(zip_path, bundle, cache) -> Path:
    target = Path(cache) / bundle["content_id_sha256"]
    marker = target / MARKER
    if marker.is_file():
        if marker.read_text().split()[0] == bundle["content_id_sha256"]:
            return target
        raise StoreError(f"{target}: its marker names another interpretation; remove it")
    if target.exists():
        raise StoreError(f"{target} exists without a marker (a half unpack); remove it")
    try:
        cb.unpack(zip_path, target)
        marker.write_text(f"{bundle['content_id_sha256']} {Path(zip_path).name}\n")
    except OSError as exc:
        raise StoreError(f"the interpretation cache {cache} cannot be written ({exc}); interpretations unpack "
                         f"beside the archive unless the caller names a cache (--cache)") from None
    return target

