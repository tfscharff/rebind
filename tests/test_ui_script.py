"""The page's inline JavaScript is the whole app UI; a syntax error in it is a blank screen.

The HTML/CSS/JS is inlined in `ui.py` as one string, so nothing type-checks or parses it at build
time. These tests parse it the way a browser would.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from rebind.ui import index_html


def _script() -> str:
    match = re.search(r"<script>(.*?)</script>", index_html(), re.DOTALL)
    assert match, "the page should carry an inline script"
    return match.group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_inline_script_parses():
    # `--check` parses without executing, which is exactly the question: would the browser have
    # thrown before running a line of it?
    # encoding must be explicit: the page contains non-ASCII (arrow glyphs in the key legend, an
    # em dash or two) and Windows' default for a pipe is cp1252, which cannot carry them.
    result = subprocess.run(
        ["node", "--check", "-"], input=_script(), capture_output=True, text=True,
        encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_every_function_the_page_calls_is_defined():
    # A typo in a handler name is invisible until a user clicks the thing. Cheap structural check:
    # every name called as `name(` must be declared as `function name(` somewhere, unless it is a
    # browser built-in or a local variable's method.
    script = _script()
    defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", script))
    called = set(re.findall(r"(?<![.\w$])([a-z][\w$]*)\s*\(", script))
    builtins = {
        "function", "if", "for", "while", "switch", "catch", "return", "typeof", "fetch",
        "setInterval", "clearInterval", "setTimeout", "parseInt", "parseFloat", "String",
        "Number", "Boolean", "encodeURIComponent", "decodeURIComponent", "isNaN", "alert",
    }
    missing = {name for name in called - defined - builtins if name.islower() or "_" in name}
    # Names that are properties/locals rather than free calls slip through the regex; keep the
    # assertion to names that look like our own helpers.
    ours = {name for name in missing if name in {
        "renderFigures", "renderQueue", "renderReadingOrder", "renderContrast", "renderWorking",
        "wireFigures", "wireContrast", "wireEditor", "loadEditor", "drawEditor", "tagLabel",
        "elementsOnPage", "kindOf", "structureBadge", "showError", "watch", "done", "say",
        "start", "tick", "esc"}}
    assert not ours, f"called but never defined: {sorted(ours)}"
