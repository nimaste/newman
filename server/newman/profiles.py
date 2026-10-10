"""How each person looks on maps and lists: a colour and an optional photo.

* Everyone has a colour. Someone who never picked one gets the first colour of the palette nobody
  else has yet, kept from then on (so it never changes under them, and two people don't share one
  until the palette runs out).
* A photo is any picture they upload (JPEG / PNG / WebP up to 3 MB): cropped to its centre square,
  shrunk to 256 px and kept as a JPEG under the data folder (people/<person id>.jpg). Its URL carries
  a short hash of the picture, so apps can cache it for as long as they like.
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import Any

PALETTE = ("#E5734A", "#3F8EDB", "#3FAE7A", "#9B6BD6", "#E2A93B", "#D9578A", "#2FA5A8", "#7C8CE0",
           "#B5794A", "#6E9E3C")
COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
PHOTO_MAX_BYTES = 3 * 1024 * 1024
PHOTO_SIZE = 256
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")


def clean_color(v: Any) -> str:
    """"#RRGGBB" (upper case); ValueError otherwise."""
    if not isinstance(v, str) or not COLOR.match(v.strip()):
        raise ValueError('color must look like "#3F8EDB"')
    return v.strip().upper()


def next_color(taken: list[str]) -> str:
    """The first palette colour not in `taken`; once all are used, the least used one."""
    used = [c.upper() for c in taken if c]
    for c in PALETTE:
        if c not in used:
            return c
    return min(PALETTE, key=used.count)


def ensure_colors(store: Any) -> None:
    """Give everyone without a colour their default one (kept, so it stays theirs)."""
    people = store.people()
    taken = [p["color"] for p in people if p.get("color")]
    for p in people:
        if not p.get("color"):
            c = next_color(taken)
            store.set_person(p["id"], color=c)
            taken.append(c)


def photo_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "people"


def photo_path(data_dir: Path, person_id: str) -> Path:
    return photo_dir(data_dir) / f"{hashlib.sha1(person_id.encode()).hexdigest()[:16]}.jpg"


def photo_url(p: dict[str, Any]) -> str | None:
    return f"/api/people/{p['id']}/photo?v={p['photo']}" if p.get("photo") else None


def make_photo(raw: bytes) -> bytes:
    """Any picture -> its centre square, 256 px, as a JPEG. ValueError when it isn't a picture."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValueError("That isn't a picture Newman can read (JPEG, PNG or WebP)") from e
    if img.format not in ("JPEG", "PNG", "WEBP", "MPO"):
        raise ValueError("Send a JPEG, PNG or WebP picture")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    img = ImageOps.fit(img.convert("RGB"), (PHOTO_SIZE, PHOTO_SIZE), Image.LANCZOS, centering=(0.5, 0.5))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue()


def save_photo(data_dir: Path, person_id: str, jpeg: bytes) -> str:
    """Keep it; returns the version (a hash of the picture) for its URL."""
    path = photo_path(data_dir, person_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(jpeg)
    tmp.replace(path)
    return hashlib.sha1(jpeg).hexdigest()[:12]


def remove_photo(data_dir: Path, person_id: str) -> None:
    photo_path(data_dir, person_id).unlink(missing_ok=True)
