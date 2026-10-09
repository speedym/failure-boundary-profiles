"""One declaration of what runs, one reader for both backends (stdlib only).

The identity of an agent is what exactly executed: the implementation
checkout and its commit, the weights and their hashes, a backbone resolved by
name, an overlay repository, and (F18 f) the environment it ran in. A backend
registry (models.yaml on CARLA, models.json on Isaac) DECLARES it per entry;
this module validates the declaration, measures the disk, refuses any
difference, and returns the portable record that campaign_meta carries once
and every run header points at by fingerprint (F18 decision 4). Installers
install TO the declaration and end each recipe with the same check.

The module never loads a registry file: callers pass the parsed mapping
(each adapter reads its own format), so this stays importable under the
fail2drive and isaacsim interpreters with no third-party package on the path
(scl_core/tests/test_stdlib_only_modules.py proves it). Portable by
construction: records carry registry-relative paths, declared URLs, measured
commits and hashes; no absolute path and no remote read from git config is
ever written (a remote can name a private repository or carry a token) - a
checkout's remote is only COMPARED with the declared URL.

History: adapters/fail2drive/scripts/multi_model_eval/model_sources.py (F27,
2026-09-12), lifted here for F18 on 2026-09-13; the adapter keeps a thin
YAML-loading shim under that name.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

WEIGHTS_KINDS = ("git_clone", "download")
_KEYS = {
    "source": {"implementation", "weights", "backbone", "overlay"},
    "implementation": {"url", "label", "commit", "modified_tracked"},
    "weights": {"url", "kind", "revision", "path_in_repo", "modified_tracked", "sha256", "files"},
    "file": {"sha256", "url"},
    "backbone": {"url", "revision", "snapshot"},
    "overlay": {"url", "commit", "path", "modified_tracked"},
}
_IGNORED_PARTS = {".git", ".cache"}   # clone metadata; a hub local_dir's download metadata

CONTRACT = """\
models.yaml `source` contract (one block per model; keys outside this vocabulary refuse):
  implementation: url (https, or null for one of OUR checkouts, then label names it and no
                  commit is declared - the fork is pinned by UPSTREAM_PIN) | commit (40 hex,
                  required with a url) | modified_tracked (optional: the recipe's own edits;
                  any other modified tracked file is a mismatch)
  weights:        required with agent_config, forbidden without. url (https) | kind: git_clone
                  | download. git_clone adds revision (40 hex, the clone's commit), path_in_repo
                  (agent_config's location inside the clone, '.' = the clone root) and may add
                  modified_tracked. Content: sha256 (64 hex) when agent_config is a FILE, or
                  files {relpath: {sha256[, url]}} when it is a DIRECTORY (its exact content;
                  download entries carry their url, git_clone entries do not)
  backbone:       optional. url (https, a huggingface.co model) | revision (40 hex) | snapshot
                  (registry-relative local copy the agent reads)
  overlay:        optional. url (https) | commit (40 hex) | path (registry-relative checkout)
                  | modified_tracked (optional)"""


class DeclarationError(ValueError):
    """models.yaml violates the declaration contract."""


class IdentityMismatch(RuntimeError):
    """What is on disk is not what models.yaml declares."""


class RequirementUnmet(RuntimeError):
    """This machine does not meet a platform requirement the entry declares."""


# --- declaration ------------------------------------------------------------

def _https(v):
    return isinstance(v, str) and v.startswith("https://")


def _hex(v, n):
    return isinstance(v, str) and len(v) == n and re.fullmatch(r"[0-9a-f]+", v) is not None


def _paths(v):
    return isinstance(v, list) and all(isinstance(p, str) and p for p in v)


def _refuse_decl(name, why):
    raise DeclarationError(f"{name}: {why}\n{CONTRACT}")


def _block(name, label, kind, value):
    if not isinstance(value, dict):
        _refuse_decl(name, f"source.{label} must be a mapping")
    extra = set(value) - _KEYS[kind]
    if extra:
        _refuse_decl(name, f"source.{label} has keys outside the vocabulary: {sorted(extra)}")
    return value


def validate_source(name, cfg):
    """The `source` block of one registry entry, or DeclarationError."""
    source = cfg.get("source")
    if not isinstance(source, dict):
        _refuse_decl(name, "no `source` block")
    _block(name, "", "source", source)

    impl = _block(name, "implementation", "implementation", source.get("implementation"))
    if "url" not in impl:
        _refuse_decl(name, "implementation.url must be present (an https URL, or null)")
    url = impl["url"]
    if url is None:
        if not impl.get("label"):
            _refuse_decl(name, "implementation.url is null (one of our checkouts) but no label names it")
        if "commit" in impl:
            _refuse_decl(name, "implementation.commit is not declared for our own checkouts (UPSTREAM_PIN pins the fork)")
    else:
        if not _https(url):
            _refuse_decl(name, "implementation.url must be an https URL or null")
        if not _hex(impl.get("commit"), 40):
            _refuse_decl(name, "implementation.commit must be the 40-hex commit of the declared url")
    if "modified_tracked" in impl and not _paths(impl["modified_tracked"]):
        _refuse_decl(name, "implementation.modified_tracked must be a list of paths")

    weights = source.get("weights")
    if "agent_config" in cfg:
        if weights is None:
            _refuse_decl(name, "agent_config is declared but source.weights is not")
        _block(name, "weights", "weights", weights)
        if not _https(weights.get("url")):
            _refuse_decl(name, "weights.url must be an https URL")
        kind = weights.get("kind")
        if kind not in WEIGHTS_KINDS:
            _refuse_decl(name, f"weights.kind must be one of {WEIGHTS_KINDS}, got {kind!r}")
        if kind == "git_clone":
            if not _hex(weights.get("revision"), 40):
                _refuse_decl(name, "git_clone weights need revision: the clone's 40-hex commit")
            if not isinstance(weights.get("path_in_repo"), str) or not weights["path_in_repo"]:
                _refuse_decl(name, "git_clone weights need path_in_repo ('.' when agent_config is the clone root)")
            if "modified_tracked" in weights and not _paths(weights["modified_tracked"]):
                _refuse_decl(name, "weights.modified_tracked must be a list of paths")
        else:
            for key in ("revision", "path_in_repo", "modified_tracked"):
                if key in weights:
                    _refuse_decl(name, f"weights.{key} belongs to git_clone weights only")
        if ("sha256" in weights) == ("files" in weights):
            _refuse_decl(name, "weights declare exactly one of sha256 (a file agent_config) or files (a directory agent_config)")
        if "sha256" in weights and not _hex(weights["sha256"], 64):
            _refuse_decl(name, "weights.sha256 must be 64 hex")
        if "files" in weights:
            files = weights["files"]
            if not isinstance(files, dict) or not files:
                _refuse_decl(name, "weights.files must be a non-empty mapping of relpath -> {sha256[, url]}")
            for rel, entry in files.items():
                if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in Path(rel).parts:
                    _refuse_decl(name, f"weights.files key {rel!r} must be a relative path inside agent_config")
                _block(name, f"weights.files[{rel}]", "file", entry)
                if not _hex(entry.get("sha256"), 64):
                    _refuse_decl(name, f"weights.files[{rel}].sha256 must be 64 hex")
                if kind == "download" and not _https(entry.get("url")):
                    _refuse_decl(name, f"weights.files[{rel}] of download weights needs its https url")
                if kind == "git_clone" and "url" in entry:
                    _refuse_decl(name, f"weights.files[{rel}] of git_clone weights carries no url (the clone's url + revision names it)")
    elif weights is not None:
        _refuse_decl(name, "source.weights declared without an agent_config")

    backbone = source.get("backbone")
    if backbone is not None:
        _block(name, "backbone", "backbone", backbone)
        if not _https(backbone.get("url")) or urllib.parse.urlsplit(backbone["url"]).hostname != "huggingface.co":
            _refuse_decl(name, "backbone.url must be an https huggingface.co model URL")
        if not _hex(backbone.get("revision"), 40):
            _refuse_decl(name, "backbone.revision must be the 40-hex model repo commit")
        if not isinstance(backbone.get("snapshot"), str) or not backbone["snapshot"]:
            _refuse_decl(name, "backbone.snapshot must name the registry-relative local copy")

    overlay = source.get("overlay")
    if overlay is not None:
        _block(name, "overlay", "overlay", overlay)
        if not _https(overlay.get("url")):
            _refuse_decl(name, "overlay.url must be an https URL")
        if not _hex(overlay.get("commit"), 40):
            _refuse_decl(name, "overlay.commit must be 40 hex")
        if not isinstance(overlay.get("path"), str) or not overlay["path"]:
            _refuse_decl(name, "overlay.path must name the registry-relative checkout")
        if "modified_tracked" in overlay and not _paths(overlay["modified_tracked"]):
            _refuse_decl(name, "overlay.modified_tracked must be a list of paths")
    return source


def parse_compute_capability(value):
    """(major, minor) from a declared or measured "M.m"; None if malformed."""
    m = re.fullmatch(r"(\d+)\.(\d+)", str(value).strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def validate_entry(name, cfg):
    """One registry entry: its declaration (validate_source) plus the
    platform requirement vocabulary - min_compute_capability: "M.m", the
    lowest CUDA compute capability the model's stack runs on (measured
    against nvidia-smi by installer and runner; absent = any CUDA GPU) -
    and accepts_torch_builds (F53): true when the registry's torch_builds
    table (one place for the versions, validate_torch_builds) may replace
    this entry's torch on a card the pinned wheel carries no kernels for
    (absent = a refusal on such a card, until the stack is tested)."""
    if "min_compute_capability" in cfg and parse_compute_capability(cfg["min_compute_capability"]) is None:
        raise DeclarationError(f"{name}: min_compute_capability must be \"M.m\" (e.g. \"8.0\"), "
                               f"got {cfg['min_compute_capability']!r}")
    ext = cfg.get("environment_extensions", [])
    if not isinstance(ext, list) or not all(isinstance(e, str) and e for e in ext):
        raise DeclarationError(f"{name}: environment_extensions must be a list of distribution names "
                               "(the compiled or pinned packages whose versions the environment record names)")
    if "torch_builds" in cfg:
        raise DeclarationError(f"{name}: torch_builds is a registry-level table (one place for the versions); "
                               "an entry accepts it with accepts_torch_builds: true")
    if "accepts_torch_builds" in cfg and not isinstance(cfg["accepts_torch_builds"], bool):
        raise DeclarationError(f"{name}: accepts_torch_builds must be true or false")
    return validate_source(name, cfg)


def validate_torch_builds(table):
    """The registry-level torch_builds table (F53): {sm_<N>: {index, packages}},
    the torch build the installer applies to an entry that accepts it
    (accepts_torch_builds) when the entry's pinned wheel carries no kernels
    for the measured card's architecture. One table per registry, so a
    version is written once; None or an empty table = no builds declared."""
    if table is None:
        return {}
    if not isinstance(table, dict):
        raise DeclarationError("registry: torch_builds must be a mapping of sm_<N> to {index, packages}")
    for tag, build in table.items():
        if not re.fullmatch(r"sm_\d+", str(tag)):
            raise DeclarationError(f"registry: torch_builds key {tag!r} must be an architecture tag "
                                   "sm_<N> (e.g. sm_120, the tag torch.cuda.get_arch_list() uses)")
        ok = (isinstance(build, dict) and isinstance(build.get("index"), str) and build["index"]
              and isinstance(build.get("packages"), list) and build["packages"]
              and all(isinstance(p, str) and "==" in p for p in build["packages"]))
        if not ok:
            raise DeclarationError(f"registry: torch_builds[{tag}] must declare index (a wheel index URL) "
                                   "and packages (a non-empty list of 'name==version' specs)")
    return table


def validate_config(config):
    """A parsed registry mapping ({"models": {name: entry}}), every entry
    validated; returns it. Loading the file is the adapter's job (YAML on
    CARLA, JSON on Isaac) - this module reads no file format."""
    if not isinstance(config, dict) or not isinstance(config.get("models"), dict):
        raise DeclarationError("registry: expected a mapping with a `models` mapping")
    for name, cfg in config["models"].items():
        if not isinstance(cfg, dict):
            raise DeclarationError(f"{name}: entry must be a mapping")
        validate_entry(name, cfg)
    validate_torch_builds(config.get("torch_builds"))
    return config


def validate_registry(config) -> dict:
    return validate_config(config)["models"]


# --- measurement -------------------------------------------------------------

from scl_core.records import sha256_file  # noqa: E402  (one streaming hash for every record, F71; its copy here retired 2026-10-08)


def _tree_files(path):
    path = Path(path)
    return {str(p.relative_to(path)): p for p in path.rglob("*")
            if p.is_file() and not (_IGNORED_PARTS & set(p.relative_to(path).parts))}


def sha256_tree(path):
    """One digest over a directory's files (sorted relative path + content),
    clone and download metadata excluded."""
    h = hashlib.sha256()
    files = _tree_files(path)
    for rel in sorted(files):
        h.update(rel.encode())
        h.update(sha256_file(files[rel]).encode())
    return h.hexdigest()


LFS_POINTER_HEAD = b"version https://git-lfs.github.com/spec/v1"


def is_lfs_pointer(path):
    """A git-lfs pointer stub (what GIT_LFS_SKIP_SMUDGE leaves for files not
    selected by --include), as opposed to materialized weights."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 1024:
        return False
    with open(path, "rb") as f:
        return f.read(len(LFS_POINTER_HEAD)) == LFS_POINTER_HEAD


def normalize_remote(url):
    """Canonical form of a git remote FOR COMPARISON ONLY: https scheme,
    lowercase host, user-info dropped (a configured remote may carry a
    token), no trailing .git or slash. Never written into a record."""
    u = (url or "").strip()
    if not u:
        return None
    if "://" not in u:                       # scp-like: [user@]host:path
        m = re.match(r"^(?:[^@]+@)?([^:/]+):/?(.+)$", u)
        if not m:
            return None
        host, path = m.group(1), m.group(2)
    else:
        parts = urllib.parse.urlsplit(u)
        host, path = parts.hostname or "", parts.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"https://{host.lower()}/{path}"


def _git(path, *args):
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def git_toplevel(path):
    """Absolute root of the checkout containing path, or None. For
    comparisons only - never part of a record."""
    out = _git(path, "rev-parse", "--show-toplevel")
    return Path(out.strip()).resolve() if out else None


def git_facts(path):
    """{commit, dirty_tracked, remote} of the checkout containing path, or
    None outside one. `remote` is the canonical form for verification
    against the declared URL; it is never recorded. No absolute path
    leaves this function."""
    if git_toplevel(path) is None:
        return None
    # porcelain lines are "XY path" with X/Y possibly blank: never strip
    # the line before slicing off the two status columns and the space
    dirty = _git(path, "status", "--porcelain", "--untracked-files=no") or ""
    return {"commit": _git(path, "rev-parse", "HEAD").strip(),
            "dirty_tracked": [line[3:] for line in dirty.splitlines() if line.strip()],
            "remote": normalize_remote(_git(path, "config", "--get", "remote.origin.url"))}


def anchored(root, rel):
    """A registry-relative path resolved against the Fail2Drive checkout
    (absolute paths pass through)."""
    return (Path(root) / os.path.expanduser(str(rel))).resolve()


def clone_root(agent_config_path, path_in_repo):
    """Where the weights clone is rooted, from agent_config's resolved path
    and its declared location inside the clone."""
    path = Path(agent_config_path)
    if path_in_repo == ".":
        return path
    return path.parents[len(Path(path_in_repo).parts) - 1]


# --- declared vs actual --------------------------------------------------------

def check_checkout(what, path, url, commit, modified_tracked=()):
    """The checkout at path IS the declaration: remote = url, HEAD = commit,
    modified tracked files within the declared set. Returns its facts."""
    facts = git_facts(path)
    if facts is None:
        raise IdentityMismatch(f"{what}: not a git checkout")
    want = normalize_remote(url)
    if want is not None and facts["remote"] != want:
        raise IdentityMismatch(f"{what}: the checkout's remote is not the declared {url} "
                               "(the record never copies a discovered remote)")
    if facts["commit"] != commit:
        raise IdentityMismatch(f"{what}: checkout is at {facts['commit']}, the declaration pins {commit}")
    undeclared = [f for f in facts["dirty_tracked"] if f not in modified_tracked]
    if undeclared:
        raise IdentityMismatch(f"{what}: modified tracked files not declared as the recipe's own "
                               f"edits: {undeclared} (declared: {list(modified_tracked) or 'none'})")
    return facts


def check_content(what, path, weights):
    """agent_config's content IS the declaration: the file's sha256, or the
    directory's exact set of files with their hashes. Returns the file hash
    or the tree digest."""
    path = Path(path)
    if "sha256" in weights:
        if not path.is_file():
            raise IdentityMismatch(f"{what}: declares a file sha256 but agent_config is "
                                   f"{'a directory' if path.is_dir() else 'missing'}")
        actual = sha256_file(path)
        if actual != weights["sha256"]:
            note = " (an LFS pointer stub, not weights)" if is_lfs_pointer(path) else ""
            raise IdentityMismatch(f"{what}: sha256 {actual} != declared {weights['sha256']}{note}")
        return actual
    if not path.is_dir():
        raise IdentityMismatch(f"{what}: declares a directory's files but agent_config is "
                               f"{'a file' if path.is_file() else 'missing'}")
    declared, actual = weights["files"], _tree_files(path)
    problems = [f"missing {rel}" for rel in sorted(set(declared) - set(actual))]
    problems += [f"undeclared {rel}" for rel in sorted(set(actual) - set(declared))]
    for rel in sorted(set(declared) & set(actual)):
        digest = sha256_file(actual[rel])
        if digest != declared[rel]["sha256"]:
            note = " (an LFS pointer stub, not weights)" if is_lfs_pointer(actual[rel]) else ""
            problems.append(f"{rel}: sha256 {digest} != declared {declared[rel]['sha256']}{note}")
    if problems:
        raise IdentityMismatch(f"{what}: the checkpoint directory is not the declared content:\n  "
                               + "\n  ".join(problems))
    return sha256_tree(path)


def hub_cache_dir():
    """The Hugging Face hub cache as huggingface_hub resolves it from the
    environment (HF_HUB_CACHE, else HF_HOME/hub, else ~/.cache/huggingface/hub)."""
    env = os.environ
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"]).expanduser()
    return Path(env.get("HF_HOME") or Path.home() / ".cache" / "huggingface").expanduser() / "hub"


def hub_repo_id(url):
    """org/name of a huggingface.co model URL."""
    parts = urllib.parse.urlsplit(url)
    segs = parts.path.strip("/").split("/")
    if parts.hostname != "huggingface.co" or len(segs) < 2:
        raise DeclarationError(f"not a huggingface.co model URL: {url}")
    return f"{segs[0]}/{segs[1]}"


def check_backbone(what, backbone, root):
    """A backbone the agent resolves BY NAME from the hub cache (offline at
    eval time): the cache's `main` must be the declared revision, its
    snapshot present, and the local copy the agent also reads is recorded
    by tree digest (a plain directory cannot prove its revision)."""
    repo_id = hub_repo_id(backbone["url"])
    repo_dir = hub_cache_dir() / ("models--" + repo_id.replace("/", "--"))
    ref = repo_dir / "refs" / "main"
    if not ref.is_file():
        raise IdentityMismatch(f"{what}: {repo_id} is not in the Hugging Face hub cache "
                               f"({repo_dir.name}/refs/main missing) - the agent resolves it by name offline")
    main = ref.read_text().strip()
    if main != backbone["revision"]:
        raise IdentityMismatch(f"{what}: the hub cache's main for {repo_id} is {main}, "
                               f"the declaration pins {backbone['revision']}")
    if not (repo_dir / "snapshots" / main).is_dir():
        raise IdentityMismatch(f"{what}: the hub cache names {main} as main but holds no snapshot of it")
    snapshot = anchored(root, backbone["snapshot"])
    if not snapshot.is_dir():
        raise IdentityMismatch(f"{what}: local snapshot {backbone['snapshot']} missing")
    return {"source": backbone["url"], "revision": main, "snapshot": backbone["snapshot"],
            "snapshot_sha256": sha256_tree(snapshot)}


def model_identity(name, cfg, root):
    """What actually runs for one registry entry, verified against its
    declaration and returned in portable form (recorded into
    campaign_meta.model_identity): DeclarationError if the entry is not a
    legal declaration, IdentityMismatch if the disk disagrees with it."""
    source = validate_source(name, cfg)
    impl = source["implementation"]
    cwd = cfg.get("cwd", ".")
    if impl["url"] is not None:
        facts = check_checkout(f"model {name!r} implementation ({cwd})", anchored(root, cwd),
                               impl["url"], impl["commit"], impl.get("modified_tracked", ()))
    else:
        facts = git_facts(anchored(root, cwd))
    ident = {"agent": cfg["agent"],
             "implementation": {"source": impl["url"], "label": impl.get("label"),
                                "commit": facts["commit"] if facts else None,
                                "dirty_tracked": facts["dirty_tracked"] if facts else []}}
    if "agent_config" in cfg:
        weights = source["weights"]
        path = anchored(root, cfg["agent_config"])
        what = f"model {name!r} weights ({cfg['agent_config']})"
        if not path.exists():
            raise IdentityMismatch(f"{what}: missing")
        record = {"source": weights["url"], "kind": weights["kind"], "path": cfg["agent_config"]}
        if weights["kind"] == "git_clone":
            clone = clone_root(path, weights["path_in_repo"])
            top = git_toplevel(clone)
            if top is None or top != clone:
                raise IdentityMismatch(f"{what}: path_in_repo {weights['path_in_repo']!r} does not "
                                       "lead from agent_config to a git clone root")
            wf = check_checkout(what, clone, weights["url"], weights["revision"],
                                weights.get("modified_tracked", ()))
            record["commit"], record["dirty_tracked"] = wf["commit"], wf["dirty_tracked"]
        record["sha256"] = check_content(what, path, weights)
        ident["weights"] = record
    if source.get("backbone"):
        ident["backbone"] = check_backbone(f"model {name!r} backbone", source["backbone"], root)
    if source.get("overlay"):
        overlay = source["overlay"]
        of = check_checkout(f"model {name!r} overlay ({overlay['path']})", anchored(root, overlay["path"]),
                            overlay["url"], overlay["commit"], overlay.get("modified_tracked", ()))
        ident["overlay"] = {"source": overlay["url"], "path": overlay["path"],
                            "commit": of["commit"], "dirty_tracked": of["dirty_tracked"]}
    return ident


# --- platform requirements ---------------------------------------------------------

def _nvidia_smi_query():
    """Raw `nvidia-smi --query-gpu=index,name,compute_cap` csv, or None when
    nvidia-smi is absent or fails (tests substitute this)."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=index,name,compute_cap", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def gpus():
    """[(index, name, (major, minor))] as nvidia-smi enumerates them, or None
    without a usable nvidia-smi."""
    text = _nvidia_smi_query()
    if text is None:
        return None
    found = []
    for line in text.splitlines():
        parts = [s.strip() for s in line.split(",")]
        cap = parse_compute_capability(parts[2]) if len(parts) >= 3 else None
        if cap is not None and parts[0].isdigit():
            found.append((int(parts[0]), parts[1], cap))
    return found


def selected_gpu(gpu=None, env=None):
    """The GPU index a model process will use: an explicit choice, else the
    first of CUDA_VISIBLE_DEVICES, else 0."""
    if gpu is not None:
        return int(gpu)
    first = (env if env is not None else os.environ).get("CUDA_VISIBLE_DEVICES", "").split(",")[0].strip()
    return int(first) if first.isdigit() else 0


def sm_tag(capability):
    """"sm_120" for (12, 0), "sm_89" for (8, 9): the architecture tag torch's arch list uses."""
    return "sm_%d%d" % (capability[0], capability[1])


def kernels_cover(arch_list, capability):
    """Whether a torch build compiled for arch_list (torch.cuda.get_arch_list())
    executes on a card of this compute capability: a cubin (sm_) of the same
    major architecture and a lower or equal minor runs on the card; PTX
    (compute_) of a lower or equal capability is JIT-compiled forward by the
    driver; an architecture-specific target (sm_90a) fits its own capability
    only. None when the list is unknown (no torch, a probe without it)."""
    if arch_list is None:
        return None
    major, minor = capability
    for tag in arch_list:
        m = re.fullmatch(r"(sm|compute)_(\d+)([a-z]*)", str(tag))
        if not m:
            continue
        kind, t_major, t_minor = m.group(1), *divmod(int(m.group(2)), 10)
        if kind == "sm" and t_major == major and (t_minor == minor or (t_minor < minor and not m.group(3))):
            return True
        if kind == "compute" and (t_major, t_minor) <= (major, minor):
            return True
    return False


def runs_weights(cfg):
    """An entry that declares weights runs them on the GPU; one without (a
    privileged expert) runs no kernels, so the kernel check does not apply."""
    return bool((cfg.get("source") or {}).get("weights"))


def declared_torch_build(cfg, capability, torch_builds=None):
    """The registry's build for this card's architecture when the entry
    accepts the table (accepts_torch_builds), else None."""
    if not cfg.get("accepts_torch_builds"):
        return None
    return (torch_builds or {}).get(sm_tag(capability))


def build_package_names(torch_builds):
    """The distribution names the registry's table may install, for the
    environment probe's extensions (so the installer learns which of them an
    env already holds)."""
    names = []
    for build in (torch_builds or {}).values():
        for spec in build.get("packages", []):
            n = spec.split("==")[0].strip().lower()
            if n not in names:
                names.append(n)
    return names


def packages_to_install(build, environment):
    """Of a build's packages, those the environment already holds (the probe's
    extensions name them): the swap replaces what is there and adds nothing
    (torchaudio is not put into an env that never had it)."""
    ext = (environment or {}).get("extensions") or {}
    return [spec for spec in build["packages"] if ext.get(spec.split("==")[0].strip().lower()) is not None]


def gpu_kernel_fit(name, cfg, environment, gpu=None, env=None, torch_builds=None):
    """F53: does the entry's measured environment execute on the selected GPU?
    Returns {"status", "gpu", "build", "message"}: status "not_applicable" (no
    weights, no torch, no arch list measured, no usable nvidia-smi),
    "covered", "build" (not covered, the entry accepts the registry's
    torch_builds and the table has one for the card) or "refused" (not
    covered, nothing applies); gpu = (index, name, capability) when a card
    was checked; build = the table's block or None.
    RequirementUnmet when the selected index is not among nvidia-smi's."""
    none = {"status": "not_applicable", "gpu": None, "build": None, "message": ""}
    if not runs_weights(cfg) or not environment or environment.get("torch") is None:
        return none
    arch_list = environment.get("torch_arch_list")
    found = gpus()
    if arch_list is None or found is None:
        return none
    index = selected_gpu(gpu, env)
    match = [g for g in found if g[0] == index]
    if not match:
        raise RequirementUnmet(f"model {name!r}: GPU index {index} is not among nvidia-smi's "
                               f"{[g[0] for g in found]}")
    idx, gname, cap = match[0]
    if kernels_cover(arch_list, cap):
        return {"status": "covered", "gpu": match[0], "build": None,
                "message": f"model {name!r}: torch {environment['torch']} kernels cover GPU {idx} "
                           f"({gname}, {sm_tag(cap)})"}
    build = declared_torch_build(cfg, cap, torch_builds)
    finding = (f"model {name!r}: its environment's torch {environment['torch']} carries kernels for "
               f"{sorted(arch_list)} and GPU {idx} ({gname}) is {sm_tag(cap)} (compute capability "
               f"{cap[0]}.{cap[1]}); ")
    if build:
        return {"status": "build", "gpu": match[0], "build": build,
                "message": finding + f"the registry's torch_builds table has a build for {sm_tag(cap)} "
                                     f"({', '.join(build['packages'])} from {build['index']}), which "
                                     f"install_models.py applies"}
    if not cfg.get("accepts_torch_builds"):
        why = ("the entry does not accept the registry's torch_builds (accepts_torch_builds); run on a "
               "covered GPU, or declare acceptance once the stack has been tested under the table's build")
    else:
        why = (f"the registry's torch_builds table declares no build for {sm_tag(cap)} "
               "(torch_builds: {sm_<N>: {index, packages}}); run on a covered GPU or declare one")
    return {"status": "refused", "gpu": match[0], "build": None, "message": finding + why}


def check_gpu_kernels(name, cfg, environment, gpu=None, env=None, torch_builds=None):
    """The runners' preflight (F53): RequirementUnmet when the entry's
    environment cannot execute on the selected GPU, before any simulator
    starts (the alternative is the agent's setup failing inside the
    leaderboard or Kit, per route, with retries). Returns the checked
    (index, name, capability), or None when the check does not apply."""
    fit = gpu_kernel_fit(name, cfg, environment, gpu=gpu, env=env, torch_builds=torch_builds)
    if fit["status"] in ("build", "refused"):
        raise RequirementUnmet(fit["message"])
    return fit["gpu"]


def _nvidia_smi_cuda_version():
    """The CUDA version the DRIVER reports in nvidia-smi's banner ("CUDA Version: 12.8"), or
    None; distinct from the CUDA build each model's torch reports (tests substitute this)."""
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"CUDA Version:\s*([0-9.]+)", r.stdout or "") if r.returncode == 0 else None
    return m.group(1) if m else None


def gpu_record(gpu=None, env=None):
    """F18 (g): the GPU a run uses, for its run header and the campaign meta: index (nvidia-smi
    order), name, compute capability, driver, and since 2026-09-27 (F52) the card's memory in
    MiB and the driver's CUDA version. None when nvidia-smi is not usable or the index is
    absent - recorded as null, never guessed; a field nvidia-smi does not give is null."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=index,name,compute_cap,driver_version,memory.total",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    index = selected_gpu(gpu, env)
    for line in r.stdout.splitlines():
        parts = [s.strip() for s in line.split(",")]
        if len(parts) >= 4 and parts[0].isdigit() and int(parts[0]) == index:
            mem = re.match(r"(\d+)", parts[4]) if len(parts) >= 5 else None
            return {"index": index, "name": parts[1], "compute_capability": parts[2], "driver": parts[3],
                    "vram_mib": int(mem.group(1)) if mem else None,
                    "cuda_driver": _nvidia_smi_cuda_version()}
    return None


def require_gpu_record(gpu, selected=None):
    """F58 (2026-09-29): a runner does not start without a measured GPU. `gpu` is gpu_record's
    result; None means nvidia-smi failed or lists no device at the selected index (a userspace
    driver upgraded under the running kernel module looks exactly like this: the day it was found,
    nine cells ran to their timeouts on a box whose driver had refused to initialize). The GPU
    record is part of the campaign's instrument vintage and every cell needs the device, so the
    contract is: measured GPU or no run. Returns the record."""
    if gpu is None:
        where = f"index {selected}" if selected is not None else "the selected index"
        raise RequirementUnmet(
            f"no measured GPU: nvidia-smi failed or lists no device at {where}; the runner records the box's "
            "GPU, driver and CUDA driver in the campaign meta (the instrument vintage) and every cell needs "
            "the device, so it refuses before the simulator starts. What nvidia-smi and "
            "/proc/driver/nvidia/version say on this box is the fact to read (a driver/library version "
            "mismatch after an unattended driver upgrade is the known cause).")
    return gpu


def _read_text_or_none(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def host_record(root="/"):
    """F52 (2026-09-27): the machine a campaign runs on, measured at campaign start for the meta of
    both backends: hostname, OS name and version (os-release), kernel, architecture, CPU model
    and core count (the simulator's physics is CPU work), total memory. Every leaf None when
    its source is unreadable - measured or null, never guessed. `root` lets a test point the
    readers at a fake tree."""
    import platform
    root = Path(root)
    os_name = os_version = None
    text = _read_text_or_none(root / "etc/os-release")
    if text:
        kv = {}
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip().strip('"')
        os_name, os_version = kv.get("PRETTY_NAME") or kv.get("NAME"), kv.get("VERSION_ID")
    cpu_model = None
    text = _read_text_or_none(root / "proc/cpuinfo")
    if text:
        m = re.search(r"^model name\s*:\s*(.+)$", text, re.M)
        cpu_model = m.group(1).strip() if m else None
    ram_gib = None
    text = _read_text_or_none(root / "proc/meminfo")
    if text:
        m = re.search(r"^MemTotal:\s*(\d+)\s*kB", text, re.M)
        ram_gib = round(int(m.group(1)) / 1048576, 1) if m else None
    return {"hostname": platform.node(),
            "os": {"name": os_name, "version": os_version},
            "kernel": platform.release(), "arch": platform.machine(),
            "cpu": {"model": cpu_model, "cores": os.cpu_count()},
            "ram_gib": ram_gib,
            "method": "os-release, uname, /proc/cpuinfo model name, /proc/meminfo MemTotal, os.cpu_count"}


def simulator_build(version=None, binary=None):
    """F52 (2026-09-27): what simulator BYTES ran, beside the declared pin, one shape on both
    backends: a version string as the installation states it (a VERSION file, a package's
    metadata) and, when the backend has one, the executable's name, size and sha256 (hashed
    once per campaign start). Absent inputs are null, never guessed."""
    record = {"version": version, "binary": None}
    if binary is not None:
        b = Path(binary)
        if b.is_file():
            record["binary"] = {"name": b.name, "size": b.stat().st_size, "sha256": sha256_file(b)}
    return record


def check_compute_capability(name, cfg, gpu=None, env=None):
    """RequirementUnmet when the selected GPU is below the entry's declared
    min_compute_capability. Returns the (index, name, capability) that was
    checked, or None when the entry declares no minimum. Indices are
    nvidia-smi's (PCI order); CUDA agrees under CUDA_DEVICE_ORDER=PCI_BUS_ID."""
    declared = cfg.get("min_compute_capability")
    if declared is None:
        return None
    want = parse_compute_capability(declared)
    found = gpus()
    if found is None:
        raise RequirementUnmet(f"model {name!r} declares min_compute_capability {declared} but "
                               "nvidia-smi is not usable here, so no GPU can be verified")
    index = selected_gpu(gpu, env)
    match = [g for g in found if g[0] == index]
    if not match:
        raise RequirementUnmet(f"model {name!r}: GPU index {index} is not among nvidia-smi's "
                               f"{[g[0] for g in found]}")
    idx, gname, cap = match[0]
    if cap < want:
        capable = [f"{i} ({n}, {c[0]}.{c[1]})" for i, n, c in found if c >= want]
        raise RequirementUnmet(
            f"model {name!r} needs compute capability >= {declared} (models.yaml "
            f"min_compute_capability); GPU {idx} is {gname}, compute capability {cap[0]}.{cap[1]}. "
            + (f"Capable GPUs on this machine: {', '.join(capable)}." if capable
               else "No GPU on this machine meets it.")
            + " (Indices are nvidia-smi's; CUDA agrees under CUDA_DEVICE_ORDER=PCI_BUS_ID.)")
    return match[0]


# --- fingerprint, environment guard, environment measurement (F18) --------------

def identity_fingerprint(block) -> str:
    """sha256 of the canonical JSON of one agent's identity block. A run
    header carries this, never the block (F18 decision 4): every cell is
    bound to the identity it ran under, the block lives once in
    campaign_meta, and a later invocation appending another model to the
    meta leaves earlier fingerprints valid."""
    return hashlib.sha256(json.dumps(block, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def require_conda_env(expected):
    """Refuse to run outside the named conda env (SCL_ALLOW_FOREIGN_ENV=1
    overrides deliberately). Not just package hygiene: the env's persistent
    config vars (WORK_DIR on fail2drive) are configuration, not only deps."""
    prefix = Path(sys.prefix)
    if prefix.parent.name == "envs":
        env_name = prefix.name           # named env: .../envs/<name>
    elif (prefix / "conda-meta").is_dir():
        env_name = "base"                # conda root itself
    else:
        env_name = "system python"
    if env_name == expected or os.environ.get("SCL_ALLOW_FOREIGN_ENV") == "1":
        return
    sys.exit(
        f"ERROR: wrong Python environment '{env_name}':\n"
        f"  {sys.executable}\n"
        f"Run under the '{expected}' conda env:\n"
        f"  conda activate {expected}\n"
        f"(deliberate foreign-env runs: set SCL_ALLOW_FOREIGN_ENV=1)")


# Runs INSIDE the model's own interpreter (py3.8-compatible: SimLingo and
# HiP-AD envs), prints one JSON line. importlib.metadata is stdlib there.
_ENVIRONMENT_PROBE = r"""
import hashlib, json, sys
import importlib.metadata as m
names = json.loads(sys.argv[1])
dists = sorted({"%s==%s" % (d.metadata["Name"].lower(), d.version) for d in m.distributions()
                if d.metadata["Name"]})
out = {"python": sys.version.split()[0],
       "packages_sha256": hashlib.sha256("\n".join(dists).encode()).hexdigest(),
       "packages_count": len(dists),
       "packages_method": "sha256 over sorted 'name==version' lines of importlib.metadata.distributions()"}
try:
    import torch
    out["torch"], out["cuda"] = torch.__version__, torch.version.cuda
    try:
        out["torch_arch_list"] = list(torch.cuda.get_arch_list())   # F53: the compiled kernel targets
    except Exception:
        out["torch_arch_list"] = None
except Exception:
    out["torch"], out["cuda"], out["torch_arch_list"] = None, None, None
ext = {}
for name in names:
    try:
        ext[name] = m.version(name)
    except m.PackageNotFoundError:
        ext[name] = None
out["extensions"] = ext
print(json.dumps(out))
"""


def env_python(conda_exe, env_name, timeout=120):
    """The interpreter of a conda env, as the env itself reports it
    (sys.executable under `conda run -n <env>`): measured on the box, never
    declared, since an interpreter path is a machine fact (the F53 Isaac
    smoke on TANKGIRL: models.json carried the cloud box's
    ~/miniconda3/envs/... path). RequirementUnmet when the env is missing."""
    cmd = [conda_exe, "run", "-n", env_name, "python", "-c", "import sys; print(sys.executable)"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RequirementUnmet(f"environment {env_name!r}: interpreter probe could not run ({exc})")
    path = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ""
    if not path:
        raise RequirementUnmet(f"environment {env_name!r}: no interpreter reported (conda run failed: "
                               f"{(r.stderr or '').strip()[-300:]})")
    return path


def measure_environment(conda_exe, env_name, extensions=(), timeout=300):
    """F18 (f): the environment an agent runs in, as its OWN interpreter
    reports it: python, torch and its CUDA build (None when absent), a hash
    of the installed distributions with its method stated (principle 4), and
    the version of each named extension (None when absent). Measured at run
    time, never copied from an install manifest. RequirementUnmet when the
    env is missing or the probe fails."""
    cmd = [conda_exe, "run", "-n", env_name, "python", "-c", _ENVIRONMENT_PROBE,
           json.dumps(list(extensions))]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RequirementUnmet(f"environment {env_name!r}: probe could not run ({exc})")
    if r.returncode != 0:
        raise RequirementUnmet(f"environment {env_name!r}: probe failed\n{r.stderr.strip()[-800:]}")
    line = [l for l in r.stdout.splitlines() if l.startswith("{")]
    if not line:
        raise RequirementUnmet(f"environment {env_name!r}: probe printed no record")
    record = json.loads(line[-1])
    record["name"] = env_name
    return record
