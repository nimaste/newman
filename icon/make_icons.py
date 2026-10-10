#!/usr/bin/env python3
"""Derive Newman's app / web icons from icon/newman-icon-source.png.

The source is an RGB square with a rounded-square app icon on solid black.
Run from anywhere (paths are relative to this file):

    docker run --rm -v /mnt/user/projects/Newman:/h python:3.12-slim \
        sh -c "pip install -q pillow && python /h/icon/make_icons.py"

Outputs
  icon/newman-icon-1024.png      full-bleed square (no black corners) for the app stores
  icon/newman-icon-rounded.png   1024 RGBA, rounded square with transparent corners
  web/public/favicon.ico        16/32/48 (rounded, transparent)
  web/public/favicon-32.png
  web/public/apple-touch-icon.png  180, full-bleed, opaque
  web/public/icon-192.png, icon-512.png  rounded, transparent
  web/public/icon-512-maskable.png  full-bleed artwork inside the maskable safe zone
  web/src/assets/newman-icon-128.png  rounded, for the dashboard sidebar
"""

import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, "newman-icon-source.png")
PUBLIC = os.path.join(ROOT, "web", "public")
ASSETS = os.path.join(ROOT, "web", "src", "assets")

BLACK_THRESHOLD = 40  # max(R,G,B) at or below this counts as the black surround
SS = 4  # supersampling factor for antialiased masks


def artwork_mask(img):
    """1-bit-ish mask (L) of the non-black artwork."""
    r, g, b = img.split()
    mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return mx.point(lambda v: 255 if v > BLACK_THRESHOLD else 0)


def corner_inset(mask, box, corner):
    """Distance along the 45-degree diagonal from a bbox corner to the first artwork pixel."""
    x0, y0, x1, y1 = box
    sx, sy, dx, dy = {
        "tl": (x0, y0, 1, 1),
        "tr": (x1 - 1, y0, -1, 1),
        "bl": (x0, y1 - 1, 1, -1),
        "br": (x1 - 1, y1 - 1, -1, -1),
    }[corner]
    px = mask.load()
    for d in range(min(x1 - x0, y1 - y0) // 2):
        if px[sx + dx * d, sy + dy * d]:
            return d
    raise SystemExit("could not find the rounded corner")


def measure(img):
    mask = artwork_mask(img)
    box = mask.getbbox()
    if not box:
        raise SystemExit("source is all black?")
    d = sum(corner_inset(mask, box, c) for c in ("tl", "tr", "bl", "br")) / 4.0
    # On a circular corner of radius r the diagonal gap is r * (1 - 1/sqrt 2).
    radius = d / (1 - 1 / math.sqrt(2))
    return box, radius


def rounded_mask(size, radius, inset):
    """Antialiased rounded-rect alpha mask."""
    big = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (inset * SS, inset * SS, (size - inset) * SS - 1, (size - inset) * SS - 1),
        radius=radius * SS,
        fill=255,
    )
    return big.resize((size, size), Image.LANCZOS)


def four_corner_gradient(img, size):
    """Background that continues the artwork's own blue gradient (for maskable padding)."""
    small = img.resize((8, 8), Image.LANCZOS)
    tl, tr = small.getpixel((0, 0)), small.getpixel((7, 0))
    bl, br = small.getpixel((0, 7)), small.getpixel((7, 7))
    grad = Image.new("RGB", (2, 2))
    grad.putdata([tl, tr, bl, br])
    return grad.resize((size, size), Image.BILINEAR)


def save(img, *parts):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, optimize=True)
    print(f"  {os.path.relpath(path, ROOT)}  {img.size[0]}x{img.size[1]} {img.mode}")


def main():
    src = Image.open(SRC).convert("RGB")
    box, radius = measure(src)
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0)
    print(f"artwork bbox {box}, corner radius ~{radius:.0f}px of {side}px")

    # --- Rounded, transparent corners (web) ---------------------------------
    art = src.crop((x0, y0, x0 + side, y0 + side)).resize((1024, 1024), Image.LANCZOS)
    r1024 = radius * 1024 / side
    # Inset the mask ~1.5px so the dark antialiased fringe of the source doesn't show.
    alpha = rounded_mask(1024, r1024 - 1.5, 1.5)
    rounded = art.convert("RGBA")
    rounded.putalpha(alpha)

    # --- Full bleed (app stores) ---------------------------------------------
    # Crop inwards until the square's corners are inside the rounded shape:
    # a corner at inset t is inside when t >= r * (1 - 1/sqrt 2); add a margin.
    t = math.ceil(radius * (1 - 1 / math.sqrt(2))) + 6
    full = src.crop((x0 + t, y0 + t, x0 + side - t, y0 + side - t)).resize((1024, 1024), Image.LANCZOS)

    # Sanity check: no near-black pixels left in the corners of the full-bleed icon.
    for cx, cy in ((0, 0), (1023, 0), (0, 1023), (1023, 1023)):
        if max(full.getpixel((cx, cy))) <= BLACK_THRESHOLD:
            raise SystemExit(f"full-bleed corner {cx},{cy} is still black - increase the crop margin")

    # --- Maskable: artwork at 80% inside a continuation of its gradient -------
    m = 512
    inner = int(m * 0.80)
    bg = four_corner_gradient(full, m)
    piece = full.resize((inner, inner), Image.LANCZOS)
    feather = Image.new("L", (inner, inner), 0)
    edge = int(inner * 0.06)
    ImageDraw.Draw(feather).rectangle((edge, edge, inner - edge - 1, inner - edge - 1), fill=255)
    feather = feather.filter(ImageFilter.GaussianBlur(edge / 2))
    off = (m - inner) // 2
    bg.paste(piece, (off, off), feather)
    maskable = bg

    print("writing:")
    save(full, HERE, "newman-icon-1024.png")
    save(rounded, HERE, "newman-icon-rounded.png")
    save(full.resize((180, 180), Image.LANCZOS), PUBLIC, "apple-touch-icon.png")
    for s in (192, 512):
        save(rounded.resize((s, s), Image.LANCZOS), PUBLIC, f"icon-{s}.png")
    save(maskable, PUBLIC, "icon-512-maskable.png")
    save(rounded.resize((32, 32), Image.LANCZOS), PUBLIC, "favicon-32.png")
    save(rounded.resize((128, 128), Image.LANCZOS), ASSETS, "newman-icon-128.png")
    ico = os.path.join(PUBLIC, "favicon.ico")
    rounded.resize((256, 256), Image.LANCZOS).save(ico, sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"  {os.path.relpath(ico, ROOT)}  16/32/48")


if __name__ == "__main__":
    main()
