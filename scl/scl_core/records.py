"""Content identity and portable references for records (stdlib only).

Records pin what they depend on by content hash and name in-repo files by
repo-relative path (architecture principles 4 and 5). One implementation
for every driver; the CARLA-env tools import this too.
"""
import hashlib
import json
from pathlib import Path


SHA_CHUNK = 1 << 20


def sha256_file(path) -> str:
    """The file's sha256, streamed in 1 MB chunks: memory is the chunk, never the file (until
    2026-10-08 this read the file whole, and a 13.7 GB campaign zip was 13.7 GB resident, F71)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(SHA_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def portable_path(path, root, resolve=True) -> str:
    """Repo-relative form of an in-repo file; refuses anything outside `root`.
    resolve=False keeps symlinked components (a results tree linked in)."""
    p = Path(path).resolve() if resolve else Path(path).absolute()
    try:
        return str(p.relative_to(Path(root).resolve()))
    except ValueError:
        raise ValueError(f"{path} must live inside the repository so the record stays portable") from None


def render(doc) -> str:
    """The one JSON serialization for records: indent 1, no NaN, trailing newline."""
    return json.dumps(doc, indent=1, allow_nan=False) + "\n"
