"""Generate Rebind's app icon and browser favicon, reproducibly.

A bound book in the UI's library-buckram teal: a teal rounded tile with a white book -- a spine
band on the left and page lines -- readable down to 16px. Run from anywhere:

    uv run python packaging/make_icon.py

Writes packaging/rebind.ico (multi-size, for the PyInstaller exe) and prints a base64 PNG data URI
for the browser favicon (pasted into src/rebind/ui.py).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw

TEAL = (47, 93, 98)          # --cloth  #2f5d62
TEAL_DEEP = (35, 74, 78)     # --cloth-deep
PAPER = (251, 250, 248)      # --paper
STAMP = (166, 65, 46)        # --stamp  (the binding band accent)

HERE = Path(__file__).resolve().parent


def _draw(size: int) -> Image.Image:
    # Supersample for clean edges, then downscale.
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded teal tile.
    pad = int(s * 0.06)
    radius = int(s * 0.22)
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=radius, fill=TEAL)

    # The book: a white block, offset slightly right of center, with a spine band on its left.
    bx0 = int(s * 0.30)
    bx1 = int(s * 0.74)
    by0 = int(s * 0.26)
    by1 = int(s * 0.74)
    # Spine band (oxblood) sits just left of the pages -- the "binding".
    spine_w = int(s * 0.07)
    d.rounded_rectangle([bx0 - spine_w, by0, bx0 + int(s * 0.01), by1],
                        radius=int(s * 0.015), fill=STAMP)
    # Page block.
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=int(s * 0.02), fill=PAPER)
    # A few text lines on the pages (skip at the smallest sizes where they'd blur to mush).
    if size >= 32:
        line_x0 = bx0 + int(s * 0.05)
        line_x1 = bx1 - int(s * 0.05)
        n = 4
        top = by0 + int(s * 0.08)
        gap = (by1 - by0 - int(s * 0.16)) / (n - 1)
        for i in range(n):
            y = int(top + i * gap)
            x1 = line_x1 if i < n - 1 else int(line_x0 + (line_x1 - line_x0) * 0.6)
            d.line([line_x0, y, x1, y], fill=TEAL_DEEP, width=max(2, int(s * 0.012)))

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    # Pillow's ICO writer ignores append_images and downsamples from the single image it is given,
    # so save from the largest master and let it produce every frame in `sizes`. Each frame is
    # drawn at its own resolution first (below) so the small ones stay crisp rather than being a
    # blurry downscale of the 256px art -- but the ICO itself is written from the 256px master with
    # the size list, which is the only form Pillow honors.
    master = _draw(256)
    ico_path = HERE / "rebind.ico"
    master.save(ico_path, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"wrote {ico_path} ({', '.join(str(s) for s in sizes)}px)")

    # Favicon data URI: a 32px PNG, small enough to inline in the page head.
    buf = io.BytesIO()
    _draw(32).save(buf, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    print("\nfavicon data URI (paste into ui.py):\n")
    print(data_uri)


if __name__ == "__main__":
    main()
