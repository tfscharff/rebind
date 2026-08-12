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
        "renderFigures", "renderWalk", "renderGoto", "renderWorking", "drawReport", "checkRow",
        "drawTodo", "drawStage", "wireTodo", "wireStage", "loadEditor", "tagLabel", "keyFor",
        "elementsOnPage", "kindOf", "structureBadge", "showError", "watch", "done", "say",
        "start", "tick", "esc", "openPalette", "closePalette", "setKind", "showType", "boxHtml",
        "focusBox", "goToPage", "turnPage", "revealChecks", "statusWord", "idleBanner",
        "actionFor", "applyEdits", "applyDescriptions", "applyFix", "stripArtifacts",
        "renderFixField", "renderFixButton", "effectiveStatus", "allPagesWalked", "walkedCount",
        "noteWalked", "readingOrderProgress"}}
    assert not ours, f"called but never defined: {sorted(ours)}"


def test_the_workspace_is_report_document_todo():
    # The three columns the result view is built from. Renaming one without renaming its CSS is a
    # silently broken layout, which no other test would catch.
    page = index_html()
    for marker in ('id="report"', 'id="stage"', 'id="todo"', 'class="workspace"'):
        assert marker in page, marker
    assert "display:grid" in page
    # The document column is the wide one -- that is the whole point of the layout.
    assert "minmax(0,2.2fr)" in page


def test_the_workspace_fits_the_window_and_the_columns_start_level():
    # No page scroll: the window is the frame, each column scrolls inside itself, and the page
    # picture is sized by the height left over so a whole page is always visible.
    page = index_html()
    assert "body.wide{height:100vh;overflow:hidden" in page
    assert ".col-report,.col-todo,.col-stage{min-height:0;height:100%" in page
    assert "body.wide .panel{margin-top:0}" in page, "columns must start on the same line"
    assert ".sheet img{display:block;height:100%;width:auto}" in page


def test_every_element_on_the_page_is_a_tab_stop():
    # The elements are tabbed through on the page itself, not in a list beside it, so each overlay
    # box has to be focusable and named for a screen reader.
    script = _script()
    assert 'tabindex="0"' in script and 'role="button"' in script
    assert "aria-label=" in script


def test_not_read_shows_as_the_element_type_but_is_sent_as_a_removal():
    # The x key looked broken because setting "Not read" deleted the type override instead of
    # storing it: the element kept showing its old type, so the keystroke appeared to do nothing.
    # It now shows as the type like any other -- and still reaches the server as a removal, since
    # there is no /Artifact structure element to tag content with.
    script = _script()
    assert "ed.tags_edit[elementId]=tag;" in script
    assert "function stripArtifacts(" in script
    assert "tags:stripArtifacts(ed.tags_edit)" in script


def test_tabbing_off_the_end_of_a_page_carries_on_to_the_next():
    script = _script()
    assert "key==='Tab' && !ev.shiftKey && index===boxes.length-1" in script
    assert "key==='Tab' && ev.shiftKey && index===0" in script


def test_clicking_a_failing_check_goes_to_it_in_the_document():
    script = _script()
    assert "data-goto=" in script
    assert "goToPage(parseInt(b.getAttribute('data-goto'),10))" in script


def test_reading_order_is_a_walk_the_person_completes():
    # No measurement can settle reading order, so the check passes when every page has actually
    # been tabbed through -- a verdict only the browser can reach.
    script = _script()
    assert "function allPagesWalked(" in script and "function noteWalked(" in script
    assert "noteWalked(ed.page)" in script
    assert "c.key==='logical-reading-order'" in script


def test_a_hotkey_sets_the_type_without_opening_anything():
    # Enter is for when you cannot remember the key. Knowing it must never cost you a menu, and
    # setting a type must move you on without a Tab, so a page is one stream of keystrokes.
    script = _script()
    assert "ed.keys.forEach(function(k){ if(k.key===key.toLowerCase()) hit=k.tag; });" in script
    assert "if(hit){ ev.preventDefault(); setKind(e.id, hit); }" in script
    assert "if(at>=0 && at+1<boxes.length){ boxes[at+1].focus(); return; }" in script


def test_the_element_chooser_sits_below_the_page_and_names_its_html_tag():
    page = index_html()
    script = _script()
    # The order in the middle column: keys, page, chooser, pager.
    assert script.index('class="sheetwrap"') < script.index('class="typebar"')
    assert '<span class="tag">' in script and "key.html" in script
    assert ".typebar .what .tag{font-family:var(--mono)" in page


def test_enter_opens_the_hotkey_palette_and_escape_leaves_it_alone():
    script = _script()
    assert "if(key==='Enter'){ ev.preventDefault(); openPalette(" in script
    assert "ev.key==='Escape'" in script
    assert "'aria-modal','true'" in script, "the palette must trap a screen reader inside it"
