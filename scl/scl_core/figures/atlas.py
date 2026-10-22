"""The scenario family figure: one picture per family, cut from the recordings the raw bundles hold
(2026-10-21, F100).

A figure spec (`figure_atlas/v0.1`, artifacts/examples/figure_specs/scenario_atlas.json) names, per
panel, the raw bundle by campaign id and content id, the recording inside it by member path, the
second of the recording and the pixel window to cut, and the label the paper prints; and the rows the
panels form. Everything here is data: which family, which variant, which agent's camera, which second,
which window. The tool opens each bundle through scl_core.campaign_bundles (the content id must match
the spec's, the member's bytes must hash to the bundle's MANIFEST listing), decodes one frame with
ffmpeg, cuts the window, lays the rows out across the text width with one height per row, and writes
the figure through the one save path of the profile figures. The figure_data record pins the spec, the
bundles, the members, the ffmpeg build and every panel's bytes, so the paper checker's hash of the
rendered PDF is a hash of exactly those inputs.

Refusals name the contract: a bundle whose content id is not the spec's, a member the bundle does not
list, a second past the recording's end, a window outside the frame, a row set that does not cover the
panels exactly once, and a box without ffmpeg.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from scl_core import campaign_bundles as cb
from scl_core.records import SHA_CHUNK, portable_path, sha256_file

SPEC_KIND = "figure_atlas/v0.1"
FIGURE_DATA_KIND = "figure_atlas_data/v0.1"
PANEL_DIR = "atlas_panels"
LAYOUT_KEYS = ("text_width_in", "gutter_in", "label_pt", "label_height_in", "rows")
PANEL_KEYS = ("id", "label", "campaign_id", "raw_content_id_sha256", "member", "time_s", "window")
WINDOW_KEYS = ("x", "y", "w", "h")


class AtlasError(ValueError):
    """The spec, a bundle, a recording or the box is not what the figure needs."""


# --- the spec ----------------------------------------------------------------

def load_spec(path) -> dict:
    """The figure spec, shape-checked: kind, figure name, layout (text width, gutter, label size, rows
    of panel ids) and panels (each with its bundle, member, second, window and label). The rows must
    name every panel exactly once and nothing else."""
    path = Path(path)
    spec = json.loads(path.read_text())
    if spec.get("spec_kind") != SPEC_KIND:
        raise AtlasError(f"{path}: spec_kind is {spec.get('spec_kind')!r}, expected {SPEC_KIND!r}")
    if not isinstance(spec.get("figure"), str) or not spec["figure"]:
        raise AtlasError(f"{path}: `figure` must name the output (e.g. SF)")
    layout = spec.get("layout")
    if not isinstance(layout, dict) or any(k not in layout for k in LAYOUT_KEYS):
        raise AtlasError(f"{path}: `layout` must hold {list(LAYOUT_KEYS)}")
    panels = spec.get("panels")
    if not isinstance(panels, list) or not panels:
        raise AtlasError(f"{path}: `panels` must be a non-empty list")
    ids = []
    for p in panels:
        missing = [k for k in PANEL_KEYS if k not in p]
        if missing:
            raise AtlasError(f"{path}: panel {p.get('id')!r} lacks {missing}; a panel holds {list(PANEL_KEYS)}")
        if any(k not in p["window"] for k in WINDOW_KEYS):
            raise AtlasError(f"{path}: panel {p['id']!r}: `window` holds {list(WINDOW_KEYS)} in pixels")
        if p["id"] in ids:
            raise AtlasError(f"{path}: panel id {p['id']!r} is used twice")
        ids.append(p["id"])
    placed = [i for row in layout["rows"] for i in row]
    if sorted(placed) != sorted(ids) or len(placed) != len(set(placed)):
        raise AtlasError(f"{path}: layout.rows must name every panel id exactly once; rows name {placed}, "
                         f"panels are {ids}")
    return spec


def panels_by_id(spec: dict) -> dict:
    return {p["id"]: p for p in spec["panels"]}


# --- the layout (pure) --------------------------------------------------------

def layout(layout_spec: dict, sizes: dict) -> dict:
    """Rows across the text width: each row's panels share one height, the height that makes their
    widths (aspect times height) plus the gutters fill the width exactly. `sizes` maps panel id to its
    pixel (w, h). Returns rows of {ids, height_in, widths_in, y_in} and the figure's total height."""
    W, G, L = layout_spec["text_width_in"], layout_spec["gutter_in"], layout_spec["label_height_in"]
    rows, y = [], 0.0
    for ids in layout_spec["rows"]:
        aspects = [sizes[i][0] / sizes[i][1] for i in ids]
        h = (W - G * (len(ids) - 1)) / sum(aspects)
        rows.append({"ids": list(ids), "height_in": h, "widths_in": [a * h for a in aspects], "y_in": y})
        y += h + L + G
    return {"rows": rows, "total_height_in": y - G, "text_width_in": W, "gutter_in": G,
            "label_height_in": L, "label_pt": layout_spec["label_pt"]}


# --- frames -----------------------------------------------------------------

def find_ffmpeg(ffmpeg=None) -> str:
    path = ffmpeg or shutil.which("ffmpeg")
    if not path:
        raise AtlasError("ffmpeg is not on PATH; the figure decodes its frames with it (pass --ffmpeg)")
    return path


def ffmpeg_version(ffmpeg: str) -> str:
    out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=True).stdout
    return out.splitlines()[0].strip()


def extract_frame(video_path, time_s: float, ffmpeg: str):
    """The decoded frame at `time_s` seconds as a PIL image (the seek is to the exact timestamp: ffmpeg
    decodes from the preceding keyframe). Refuses when the recording ends before `time_s`."""
    from PIL import Image
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-ss", f"{time_s}", "-i", str(video_path),
           "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    run = subprocess.run(cmd, capture_output=True)
    if run.returncode != 0:
        raise AtlasError(f"ffmpeg failed on {Path(video_path).name} at {time_s} s: {run.stderr.decode(errors='replace').strip()}")
    if not run.stdout:
        raise AtlasError(f"{Path(video_path).name} has no frame at {time_s} s (the recording ends earlier)")
    return Image.open(io.BytesIO(run.stdout)).convert("RGB")


def crop(frame, window: dict):
    """The window cut from the frame; a window that leaves the frame is refused, never clamped."""
    x, y, w, h = (window[k] for k in WINDOW_KEYS)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > frame.width or y + h > frame.height:
        raise AtlasError(f"window x={x} y={y} w={w} h={h} leaves the {frame.width}x{frame.height} frame")
    return frame.crop((x, y, x + w, y + h))


def png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# --- the build ----------------------------------------------------------------

def _stream_member(zip_path, arcname: str, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z, z.open(arcname) as src, target.open("wb") as dst:
        for chunk in iter(lambda: src.read(SHA_CHUNK), b""):
            dst.write(chunk)


def cut_panels(spec: dict, bundles_dir, work_dir, ffmpeg: str) -> tuple:
    """Every panel's picture from its bundle: the bundle opened and checked (campaign id, raw kind,
    content id as the spec pins it), the member's bytes verified against the MANIFEST, the frame
    decoded and the window cut. Returns ({id: image}, [panel records]) in spec order."""
    bundles_dir, work_dir = Path(bundles_dir), Path(work_dir)
    opened, images, records = {}, {}, []
    for p in spec["panels"]:
        cid = p["campaign_id"]
        if cid not in opened:
            zip_path = bundles_dir / f"{cid}.zip"
            if not zip_path.is_file():
                raise AtlasError(f"panel {p['id']!r}: no raw bundle {zip_path}")
            bundle = cb.open_bundle(zip_path, campaign_id=cid, kinds=cb.RAW_KINDS, refuse=AtlasError)
            if bundle["content_id_sha256"] != p["raw_content_id_sha256"]:
                raise AtlasError(f"panel {p['id']!r}: {zip_path.name} has content id {bundle['content_id_sha256']}, "
                                 f"the spec pins {p['raw_content_id_sha256']}")
            opened[cid] = bundle
        bundle = opened[cid]
        if bundle["content_id_sha256"] != p["raw_content_id_sha256"]:
            raise AtlasError(f"panel {p['id']!r}: the spec pins two content ids for {cid}")
        arcname = f"{cid}/{p['member']}"
        member_sha = bundle["member_hash"](arcname)
        local = work_dir / cid / p["member"]
        _stream_member(bundle["path"], arcname, local)
        frame = extract_frame(local, float(p["time_s"]), ffmpeg)
        image = crop(frame, p["window"])
        images[p["id"]] = image
        records.append({"id": p["id"], "label": p["label"], "family_id": p.get("family_id"),
                        "variant": p.get("variant"), "agent": p.get("agent"), "view": p.get("view"),
                        "campaign_id": cid, "raw_content_id_sha256": bundle["content_id_sha256"],
                        "member": p["member"], "member_sha256": member_sha, "time_s": float(p["time_s"]),
                        "frame_size": [frame.width, frame.height], "window": dict(p["window"]),
                        "panel_size": [image.width, image.height], "panel_sha256": cb.sha256_bytes(png_bytes(image))})
    return images, records


def spec_pin(spec_path, root) -> dict:
    """The spec by portable path and hash. A spec outside the repository (a one-off figure, a test's
    synthetic spec) is pinned by hash and name with `outside_repository` set, never by a machine path."""
    spec_path = Path(spec_path)
    try:
        return {"path": portable_path(spec_path, root), "sha256": sha256_file(spec_path)}
    except ValueError:
        return {"path": None, "name": spec_path.name, "sha256": sha256_file(spec_path), "outside_repository": True}


def build(spec_path, bundles_dir, out_dir, work_dir, root, builder_path, ffmpeg=None) -> dict:
    """The whole figure_data record for the spec: the panels cut (written as PNGs under
    `<out>/atlas_panels/`), the layout computed from their sizes, and the lineage (spec, builder, ffmpeg,
    bundles). Written to `<out>/figure_data/<figure>.json`; returned with the images under `_images`
    for the renderer."""
    spec_path, out_dir, root = Path(spec_path), Path(out_dir), Path(root)
    spec = load_spec(spec_path)
    ffmpeg = find_ffmpeg(ffmpeg)
    images, records = cut_panels(spec, bundles_dir, work_dir, ffmpeg)
    panel_dir = out_dir / PANEL_DIR
    panel_dir.mkdir(parents=True, exist_ok=True)
    for pid, image in images.items():
        (panel_dir / f"{pid}.png").write_bytes(png_bytes(image))
    lay = layout(spec["layout"], {pid: (im.width, im.height) for pid, im in images.items()})
    bundles = {}
    for r in records:
        bundles.setdefault(r["campaign_id"], r["raw_content_id_sha256"])
    data = {"kind": FIGURE_DATA_KIND, "figure": spec["figure"], "panels": records, "layout": lay,
            "lineage": {"spec": spec_pin(spec_path, root),
                        "builder": {"path": portable_path(builder_path, root), "sha256": sha256_file(builder_path)},
                        "module": {"path": portable_path(__file__, root), "sha256": sha256_file(__file__)},
                        "ffmpeg": ffmpeg_version(ffmpeg),
                        "inputs": {"raw_bundles": bundles}}}
    fd = out_dir / "figure_data"
    fd.mkdir(parents=True, exist_ok=True)
    (fd / f"{spec['figure']}.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    data["_images"] = images
    return data


def render(data: dict, out_dir) -> list:
    """The figure: one axes per panel at its laid-out place, the picture embedded unsampled, the label
    beneath in the paper's serif at the spec's size; pdf, png and svg through the profile figures' save."""
    from scl_core.figures import draw_profiles as dp
    import numpy as np
    plt = dp.mpl()
    lay, images = data["layout"], data["_images"]
    labels = {p["id"]: p["label"] for p in data["panels"]}
    W, H = lay["text_width_in"], lay["total_height_in"]
    fig = plt.figure(figsize=(W, H))
    for row in lay["rows"]:
        x = 0.0
        for pid, w in zip(row["ids"], row["widths_in"]):
            h, y = row["height_in"], row["y_in"]
            ax = fig.add_axes([x / W, 1 - (y + h) / H, w / W, h / H])
            ax.imshow(np.asarray(images[pid]), interpolation="none", aspect="auto")
            ax.set_axis_off()
            fig.text(x / W, 1 - (y + h + 0.03) / H, f"({pid}) {labels[pid]}", fontsize=lay["label_pt"],
                     ha="left", va="top")
            x += w + lay["gutter_in"]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dp._save(fig, out_dir, data["figure"])
    return [out_dir / f"{data['figure']}.{ext}" for ext in ("pdf", "png", "svg")]
