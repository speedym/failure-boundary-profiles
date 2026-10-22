"""Shared brain-declaration loader for the Isaac multi_model_eval tools.

The declarations live in models.json (see its _note). This module is the
single place cell naming and server lifecycle come from - brain identity
is an evaluation-level declaration, never a tool hardcode (the 2026-08-31
prompt-generality audit, item 1). Since F18 (2026-09-13) every brain entry
also DECLARES its identity (`source`: implementation URL + commit, weights
URL + hash) in the vocabulary scl_core.agent_identity validates - the same
reader the CARLA registry goes through - and names the conda env its
inference server runs in. Paths in an entry are relative to the brains
root (--brains-root, $SCL_BRAINS_ROOT, default ~/xmobility_brain).
Stdlib-only; runs under any env (scl_core.agent_identity is on the
stdlib-only admitted list, CLAUDE.md).
"""
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from scl_core.agent_identity import DeclarationError, validate_entry, validate_torch_builds  # noqa: E402

REGISTRY = _HERE / "models.json"


ISAAC_CONTRACT = """\
models.json Isaac keys (beside the core `source` vocabulary):
  slug     the cell-directory tag (frozen once a campaign ran with it), unique across brains
  server   {script: a file under aisle_gauntlet/, port: int, unique across brains}; the checkpoint
           is passed by the runner (agent_config), the interpreter measured from conda_env
  runtime  the env recipe (F54): python (str), torch (list of specs), torch_index (URL), then
           pins (list) and/or requirements (a file of the checkout); optional no_deps (list),
           load_check (a command run in the env from the checkout), note"""


def validate_isaac_keys(name, cfg):
    """The Isaac-side vocabulary a brain entry carries beside the core one."""
    if not isinstance(cfg.get("slug"), str) or not cfg["slug"]:
        raise DeclarationError(f"{name}: slug must be a non-empty string\n{ISAAC_CONTRACT}")
    srv = cfg.get("server")
    if srv is not None and (not isinstance(srv, dict) or not isinstance(srv.get("script"), str)
                            or not isinstance(srv.get("port"), int)):
        raise DeclarationError(f"{name}: server must declare script (str) and port (int)\n{ISAAC_CONTRACT}")
    rt = cfg.get("runtime")
    ok = (isinstance(rt, dict) and isinstance(rt.get("python"), str)
          and isinstance(rt.get("torch"), list) and rt["torch"] and isinstance(rt.get("torch_index"), str)
          and (isinstance(rt.get("pins"), list) or isinstance(rt.get("requirements"), str))
          and isinstance(rt.get("no_deps", []), list) and isinstance(rt.get("load_check", []), list))
    if not ok:
        raise DeclarationError(f"{name}: runtime must declare python, torch, torch_index and pins and/or "
                               f"requirements\n{ISAAC_CONTRACT}")


def load_brains(path=REGISTRY) -> dict:
    """{brain: entry}, every entry a legal declaration (SystemExit names the
    contract otherwise); `_`-prefixed keys are notes."""
    doc = json.loads(Path(path).read_text())
    brains = {k: v for k, v in doc.items() if not k.startswith("_") and k != "torch_builds"}
    for name, cfg in brains.items():
        try:
            validate_entry(name, cfg)
            if "python" in (cfg.get("server") or {}):
                raise DeclarationError(f"{name}: server.python is not a declaration - the interpreter is "
                                       "measured from conda_env on the box (agent_identity.env_python); a "
                                       "path here is a machine fact (principle 4)")
            validate_isaac_keys(name, cfg)
        except DeclarationError as exc:
            raise SystemExit(f"REGISTRY REFUSED ({Path(path).name}): {exc}")
    for key, label in (("slug", "slug"), ("port", "server port")):
        seen = {}
        for name, cfg in brains.items():
            value = cfg.get("slug") if key == "slug" else (cfg.get("server") or {}).get("port")
            if value is None:
                continue
            if value in seen:
                raise SystemExit(f"REGISTRY REFUSED ({Path(path).name}): {label} {value!r} is declared by both "
                                 f"{seen[value]!r} and {name!r}; it must be unique across brains")
            seen[value] = name
    return brains


BRAINS: dict = load_brains()


def load_torch_builds(path=REGISTRY) -> dict:
    """The registry-level torch_builds table (F53, the models.yaml table's twin), {} when none."""
    try:
        return validate_torch_builds(json.loads(Path(path).read_text()).get("torch_builds")) or {}
    except DeclarationError as exc:
        raise SystemExit(f"REGISTRY REFUSED ({Path(path).name}): {exc}")


TORCH_BUILDS: dict = load_torch_builds()


def spec(brain: str) -> dict:
    if brain not in BRAINS:
        raise SystemExit(
            f"ERROR: brain '{brain}' is not declared in models.json "
            f"(declared: {sorted(BRAINS)}) - declare it before evaluating")
    return BRAINS[brain]


def slug(brain: str) -> str:
    return spec(brain)["slug"]


def cell_tag(brain: str, rep) -> str:
    return "expert" if brain == "expert" else f"{slug(brain)}_r{rep}"


def cell_dir_name(brain: str, rep) -> str:
    return "expert" if brain == "expert" else f"{slug(brain)}_rep{rep}"
