"""Darken text that fails WCAG contrast, without touching anything else on the page.

This is the one thing Rebind does that changes how a document *looks*, so it is opt-in per
document and never runs unless asked. Everywhere else the thesis holds: preserve the original,
add only the accessibility it is missing. Here the original's own appearance is the barrier --
publisher small print at 2.9:1 is unreadable for a large number of people no matter how well the
structure tree is built -- and no amount of tagging fixes it.

Two rules keep the change surgical:

* **Only colours used exclusively by text are rewritten.** The content stream is walked twice:
  once to record which colour operator was in effect for every painting operation, once to rewrite
  only those never used to paint a path, shading or image. A colour shared between a heading and
  the rule beneath it is left alone rather than silently restyling the artwork.
* **Hue is preserved.** The colour's luminance is scaled down in linear-light space until it meets
  the threshold; a lilac cross-reference stays lilac, just dark enough to read.

A page rebuilt from a raster render (a scan, or a page carrying marked content) is not recoloured:
its text is pixels, and repainting it would mean altering the scan itself.
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


def _darken(rgb: tuple[int, int, int], background: tuple[int, int, int],
            minimum: float) -> tuple[int, int, int]:
    """`rgb` scaled down in linear light until it meets `minimum` against `background`, hue kept."""
    target = (relative_luminance(background) + 0.05) / minimum - 0.05
    if target <= 0:
        return (0, 0, 0)
    current = relative_luminance(rgb)
    if current <= target:
        return rgb

    def linear(value: float) -> float:
        c = value / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def encode(value: float) -> int:
        value = max(0.0, min(1.0, value))
        srgb = value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
        return round(max(0.0, min(1.0, srgb)) * 255)

    channels = [linear(v) for v in rgb]
    scale = target / current if current > 0 else 0.0
    # Scaling lands close but not exactly on the threshold: luminance is a weighted sum and the
    # result is rounded back to 8 bits, so a single pass can come out at 4.49:1 -- still a failure,
    # for the sake of a rounding error. Step down until it genuinely clears, with a little headroom
    # so that anti-aliasing does not put the *rendered* result back under the bar.
    for _ in range(64):
        candidate = tuple(encode(c * scale) for c in channels)
        if contrast_ratio(candidate, background) >= minimum * HEADROOM or candidate == (0, 0, 0):
            return candidate
        scale *= 0.96
    return (0, 0, 0)


def darken_failing_text(pdf: pikepdf.Pdf, page: pikepdf.Page, *,
                        background: tuple[int, int, int] = (255, 255, 255),
                        minimum: float = 4.5) -> int:
    """Rewrite this page's text-only fill colours to meet `minimum`. Returns how many were changed.

    The page's content stream is replaced in place. Returns 0 -- leaving the page untouched -- when
    nothing needs changing, so an unaffected page keeps its original bytes.
    """
    try:
        instructions = list(pikepdf.parse_content_stream(page))
    except Exception:       # noqa: BLE001 -- an unparseable stream is simply left alone
        return 0

    # Pass one: which colour *values* paint text, and which paint anything else. Keyed on the
    # value, not on the operator instance: a document sets its accent colour afresh before every
    # run that uses it, so "this exact operator also drew a rule" would miss the case that matters
    # -- the same colour serving both a heading and the rule beneath it, where recolouring the text
    # would leave the rule behind at the old shade.
    used_by_text: set[tuple[int, int, int]] = set()
    used_by_other: set[tuple[int, int, int]] = set()
    current: int | None = None
    stack: list[int | None] = []

    def colour_at(index: int | None) -> tuple[int, int, int] | None:
        if index is None:
            return None
        operands, op = instructions[index]
        return _to_rgb(str(op), list(operands))

    for index, (_operands, op) in enumerate(instructions):
        token = str(op)
        if token in _FILL_COLOR_OPS:
            current = index
        elif token == "q":
            stack.append(current)
        elif token == "Q" and stack:
            current = stack.pop()
        else:
            rgb = colour_at(current)
            if rgb is None:
                continue
            if token in _TEXT_SHOW_OPS:
                used_by_text.add(rgb)
            elif token in _NON_TEXT_PAINT_OPS or (token == "Do" and _is_form(page, _operands)):
                used_by_other.add(rgb)

    text_only = used_by_text - used_by_other
    changed = 0
    for index, (operands, op) in enumerate(instructions):
        if str(op) not in _FILL_COLOR_OPS:
            continue
        rgb = _to_rgb(str(op), list(operands))
        if rgb is None or rgb not in text_only or contrast_ratio(rgb, background) >= minimum:
            continue
        if relative_luminance(rgb) >= relative_luminance(background) * REVERSE_VIDEO_LUMINANCE:
            # Text as light as the assumed background is reverse-video: white callout labels over a
            # photograph, a heading knocked out of a coloured banner. It fails "contrast against
            # white" only because white is the wrong thing to compare it to, and darkening it would
            # turn legible white-on-dark into dark-on-dark. Confirmed on the real sample, whose
            # figure callouts are set in pure white. Leave it to the human.
            continue
        r, g, b = _darken(rgb, background, minimum)
        instructions[index] = ([r / 255, g / 255, b / 255], pikepdf.Operator("rg"))
        changed += 1

    if changed:
        # `rg` is a device-RGB operator needing no resource entry, so nothing else has to change.
        # Any `sc` left un-rewritten still refers to whatever colour space the page declared.
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(instructions))
    return changed
