"""Correct text that fails WCAG contrast, without touching anything else on the page.

Contrast is the one barrier a structure tree cannot fix: publisher small print at 2.9:1 is
unreadable for a large number of people no matter how well the document is tagged. It is also the
one thing that can be settled outright rather than handed back as homework -- a human eye cannot
compute a luminance ratio, so asking a librarian to "check the colour contrast" is asking for a
judgement they have no way to make. So Rebind measures it and fixes it.

The correction is driven by the measurement (`contrast.measure`), never guessed: each failing
colour is corrected against the paper actually sampled behind it, so text on a dark panel is
*lightened* and text on white is darkened, and both come out passing.

Two rules keep the change surgical:

* **Only text is repainted.** Every colour change is made inside a text object (`BT`..`ET`) and
  the original colour is restored at its end, so a colour shared between a heading and the rule
  beneath it corrects the heading and leaves the rule exactly as it was. Nothing that paints a
  path, shading or image is ever reached.
* **Hue is preserved.** The colour's luminance is moved in linear-light space until it meets the
  threshold; a lilac cross-reference stays lilac, just dark enough to read.

A page rebuilt from a raster render (a scan) is not recoloured: its text is pixels, and repainting
it would mean altering the scan itself. Such text declares no colour, so it is not measured either.
"""

from __future__ import annotations

import pikepdf
from pikepdf import Name

from .contrast import contrast_ratio, relative_luminance

# Operators that set the NON-stroking colour (the one text is painted with by default).
_FILL_COLOR_OPS = {"g": 1, "rg": 3, "k": 4, "sc": None, "scn": None}
# Operators that paint something that is not text: paths and shadings. `Do` is handled separately:
# it only counts when it draws a *Form* XObject, whose content inherits the fill colour and may
# paint with it. An image XObject ignores the fill colour altogether, so counting `Do` wholesale
# needlessly protected colours no image ever used -- on the real sample it left four callout
# labels beside a photograph uncorrected, purely because the colour happened to be in effect when
# the photograph was drawn.
_NON_TEXT_PAINT_OPS = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "sh"}
_TEXT_SHOW_OPS = {"Tj", "TJ", "'", '"'}
# Aim slightly past the threshold. A colour that lands exactly on 4.5:1 renders, once anti-aliased
# into thin glyph strokes, as very slightly lighter than it is -- enough to measure back at 4.48
# and still read as a failure. 5% of headroom costs nothing visually and settles it.
HEADROOM = 1.05
# Text at or above this fraction of the background's luminance is reverse-video, not faint text.
REVERSE_VIDEO_LUMINANCE = 0.9


def _to_rgb(op: str, operands: list) -> tuple[int, int, int] | None:
    """The operator's colour as 8-bit sRGB, or None if it is not a plain device colour."""
    try:
        values = [float(v) for v in operands]
    except (TypeError, ValueError):
        return None      # a pattern name, or anything else non-numeric
    if op == "g" and len(values) == 1:
        grey = values[0]
        return tuple(round(max(0.0, min(1.0, grey)) * 255) for _ in range(3))
    if op in ("rg", "sc", "scn") and len(values) == 3:
        return tuple(round(max(0.0, min(1.0, v)) * 255) for v in values)
    if op in ("k", "sc", "scn") and len(values) == 4:
        c, m, y, k = (max(0.0, min(1.0, v)) for v in values)
        return tuple(round(255 * (1 - min(1.0, channel + k))) for channel in (c, m, y))
    if op in ("sc", "scn") and len(values) == 1:
        grey = max(0.0, min(1.0, values[0]))
        return tuple(round(grey * 255) for _ in range(3))
    return None


def _is_form(page: pikepdf.Page, operands) -> bool:
    """Whether a `Do` draws a Form XObject (which inherits and may paint with the fill colour)
    rather than an image (which ignores it). Unknown names are treated as forms -- the cautious
    reading, since a form is the case where leaving the colour alone matters."""
    try:
        xobjects = page.obj.get("/Resources", {}).get("/XObject", {})
        target = xobjects.get(str(operands[0]))
    except Exception:      # noqa: BLE001 -- a malformed resource dict is simply treated as a form
        return True
    if target is None:
        return True
    return target.get("/Subtype") != Name.Image


def _linear(value: float) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _encode(value: float) -> int:
    value = max(0.0, min(1.0, value))
    srgb = value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
    return round(max(0.0, min(1.0, srgb)) * 255)


def _darken(rgb: tuple[int, int, int], background: tuple[int, int, int],
            minimum: float) -> tuple[int, int, int]:
    """`rgb` scaled down in linear light until it meets `minimum` against `background`, hue kept."""
    target = (relative_luminance(background) + 0.05) / minimum - 0.05
    if target <= 0:
        return (0, 0, 0)
    current = relative_luminance(rgb)
    if current <= target:
        return rgb
    channels = [_linear(v) for v in rgb]
    scale = target / current if current > 0 else 0.0
    # Scaling lands close but not exactly on the threshold: luminance is a weighted sum and the
    # result is rounded back to 8 bits, so a single pass can come out at 4.49:1 -- still a failure,
    # for the sake of a rounding error. Step down until it genuinely clears, with a little headroom
    # so that anti-aliasing does not put the *rendered* result back under the bar.
    for _ in range(64):
        candidate = tuple(_encode(c * scale) for c in channels)
        if contrast_ratio(candidate, background) >= minimum * HEADROOM or candidate == (0, 0, 0):
            return candidate
        scale *= 0.96
    return (0, 0, 0)


def _lighten(rgb: tuple[int, int, int], background: tuple[int, int, int],
             minimum: float) -> tuple[int, int, int]:
    """The mirror of `_darken`, for text that sits on something darker than itself.

    Reverse video is a real design -- a heading knocked out of a coloured banner, callout labels
    over a dark micrograph -- and darkening it would turn legible light-on-dark into dark-on-dark.
    The fix is to push it further from the background, which here means lighter.
    """
    channels = [_linear(v) for v in rgb]
    headroom = 1.0 - max(channels) if max(channels) < 1.0 else 0.0
    for step in range(1, 65):
        # Move toward white along the ray from the colour, so the hue survives the lift.
        lift = headroom * (step / 64.0)
        candidate = tuple(_encode(c + lift) for c in channels)
        if contrast_ratio(candidate, background) >= minimum * HEADROOM:
            return candidate
    return (255, 255, 255)


def correction_for(ink: tuple[int, int, int], paper: tuple[int, int, int],
                   minimum: float) -> tuple[int, int, int]:
    """The colour `ink` should become to clear `minimum` against `paper`, keeping its hue.

    Which direction to move is decided by the paper, not assumed: text lighter than what is behind
    it is lifted away from it, text darker than it is pushed down.
    """
    if relative_luminance(ink) >= relative_luminance(paper):
        return _lighten(ink, paper, minimum)
    return _darken(ink, paper, minimum)


def corrections_for(report) -> dict[tuple[int, int, int], tuple[int, int, int]]:
    """Every failing ink colour in a `ContrastReport`, mapped to what it should become.

    One ink can fail against more than one paper (the same grey over white and over a shaded row).
    The correction kept is the one that clears the hardest of them, so applying a single colour
    change per ink settles every line that used it.
    """
    out: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for line in report.failures:
        candidate = correction_for(line.ink, line.paper, line.required)
        best = out.get(line.ink)
        if best is None:
            out[line.ink] = candidate
            continue
        # "Hardest" means furthest from where it started, in either direction.
        start = relative_luminance(line.ink)
        if abs(relative_luminance(candidate) - start) > abs(relative_luminance(best) - start):
            out[line.ink] = candidate
    return {ink: fixed for ink, fixed in out.items() if fixed != ink}


def _rg(rgb: tuple[int, int, int]) -> pikepdf.ContentStreamInstruction:
    """A device-RGB fill-colour instruction. `rg` needs no resource entry, so substituting it for
    a `k` or an `sc` requires nothing else on the page to change."""
    return pikepdf.ContentStreamInstruction(
        [value / 255 for value in rgb], pikepdf.Operator("rg"))


def apply_corrections(pdf: pikepdf.Pdf, page: pikepdf.Page,
                      corrections: dict[tuple[int, int, int], tuple[int, int, int]]) -> int:
    """Repaint this page's failing text in its corrected colours. Returns how many runs changed.

    Every change is confined to a text object: a corrected colour is set just after `BT`, or in
    place of a colour operator that occurs inside one, and the original is restored at `ET`. That
    is what makes it safe to correct a colour the artwork also uses -- the heading is darkened, the
    rule beneath it is not touched, and the graphics state after the text object is exactly what it
    was. A page with nothing to correct keeps its original bytes.
    """
    if not corrections:
        return 0
    try:
        instructions = list(pikepdf.parse_content_stream(page))
    except Exception:       # noqa: BLE001 -- an unparseable stream is simply left alone
        return 0

    out: list = []
    changed = 0
    in_text = False
    current: pikepdf.ContentStreamInstruction | None = None   # the live fill-colour instruction
    current_rgb: tuple[int, int, int] | None = None
    stack: list[tuple] = []

    for ins in instructions:
        token = str(getattr(ins, "operator", ""))
        if token in _FILL_COLOR_OPS:
            rgb = _to_rgb(token, list(ins.operands))
            current, current_rgb = ins, rgb
            if in_text and rgb is not None and rgb in corrections:
                out.append(_rg(corrections[rgb]))
                changed += 1
                continue
            out.append(ins)
            continue
        if token == "q":
            stack.append((current, current_rgb))
        elif token == "Q" and stack:
            current, current_rgb = stack.pop()
        elif token == "BT":
            in_text = True
            out.append(ins)
            if current_rgb is not None and current_rgb in corrections:
                out.append(_rg(corrections[current_rgb]))
                changed += 1
            continue
        elif token == "ET":
            in_text = False
            out.append(ins)
            # Put back exactly the instruction the page had, not a device-RGB approximation of it:
            # re-encoding a CMYK or separation colour would shift whatever is painted next.
            if current_rgb is not None and current_rgb in corrections and current is not None:
                out.append(current)
            continue
        out.append(ins)

    if changed:
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))
    return changed
