"""Pictures a household uploads for a wall dashboard's Christmas characters (dashboard.christmas.pictures).

Transparent PNG or WebP up to 2 MB, at most 3 per dashboard (dashboards.MAX_PICTURES). Each is shrunk to at
most 600 px on its longer side, kept as a PNG (transparency intact) under the data folder at
wall-pictures/<dashboard id>/<picture id>.png; the id is a hash of the picture, so its URL can be cached.
"""
from __future__ import annotations

import hashlib
import io
import re
import shutil
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
MAX_SIDE = 600
TYPES = ("image/png", "image/webp")
FOLDER = "wall-pictures"
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def folder(data_dir: Path, dashboard_id: str) -> Path:
    if not _SAFE.match(dashboard_id):
        raise ValueError("bad dashboard id")
    return Path(data_dir) / FOLDER / dashboard_id


def path(data_dir: Path, dashboard_id: str, picture_id: str) -> Path:
    return folder(data_dir, dashboard_id) / f"{picture_id}.png"


def url(dashboard_id: str, picture_id: str) -> str:
    return f"/api/dashboards/{dashboard_id}/pictures/{picture_id}"


def make(raw: bytes) -> bytes:
    """A PNG or WebP -> a PNG of at most MAX_SIDE px (transparency kept). ValueError when it isn't one."""
    from PIL import Image, UnidentifiedImageError
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValueError("That isn't a picture Newman can read (PNG or WebP)") from e
    if img.format not in ("PNG", "WEBP"):
        raise ValueError("Send a PNG or WebP picture (with a see-through background)")
    img = img.convert("RGBA")
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def save(data_dir: Path, dashboard_id: str, png: bytes) -> str:
    pid = hashlib.sha1(png).hexdigest()[:16]
    p = path(data_dir, dashboard_id, pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(p)
    return pid


def remove(data_dir: Path, dashboard_id: str, picture_id: str) -> None:
    path(data_dir, dashboard_id, picture_id).unlink(missing_ok=True)


def copy_all(data_dir: Path, from_id: str, to_id: str, picture_ids: list[str]) -> list[str]:
    """A duplicated dashboard gets its own copies of the pictures; returns the ids that were there to copy."""
    out = []
    for pid in picture_ids:
        src = path(data_dir, from_id, pid)
        if src.is_file():
            dst = path(data_dir, to_id, pid)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            out.append(pid)
    return out


def remove_all(data_dir: Path, dashboard_id: str) -> None:
    shutil.rmtree(folder(data_dir, dashboard_id), ignore_errors=True)
