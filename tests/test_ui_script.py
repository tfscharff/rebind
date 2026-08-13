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
    assert "goToPage(parseInt(page,10))" in script


def test_the_run_from_the_report_to_the_document_is_only_what_is_wrong():
    # The tab order is the product here. Everything that used to sit between the report and
    # element 1 -- the download in the header, the fix fields under "Needs you" -- was a stop the
    # person paid on every pass through the document, to reach a control they wanted once.
    script = _script()
    # Every failing check is reachable, whether or not it names a page; passes are not stops.
    assert "var open=(status!=='pass'&&status!=='n/a');" in script
    # ...and nothing else in the report is.
    assert ".item input, .item button, .item a, .item textarea" in script
    assert "el.tabIndex=-1;" in script
    # The header holds the save state and nothing you can tab to.
    header = script[script.index("function drawHeader("):script.index("function setSaveState(")]
    assert "id=\"dl\"" not in header and "reset" not in header


def test_the_document_and_a_fresh_start_are_the_last_two_stops():
    script = _script()
    todo = script[script.index("function drawTodo("):script.index("function actionFor(")]
    finish = todo[todo.index('class="panel finish"'):]
    assert finish.index('id="dl"') < finish.index('class="reset"'), \
        "the download must come before the link that throws the document away"


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
    assert "ed.allKeys.forEach(function(k){ if(k.key===key.toLowerCase()) hit=k.tag; });" in script
    assert "if(hit){ ev.preventDefault(); setKind(e.id, hit); }" in script
    assert "if(at>=0 && at+1<boxes.length){ boxes[at+1].focus(); return; }" in script


def test_the_middle_column_holds_the_document_and_nothing_else():
    # The keys and the element chooser moved to the right column, beside the reading-order block
    # they belong with. The middle is the page and its pager.
    script = _script()
    stage = script[script.index("function drawStage("):script.index("function idleBanner(")]
    assert 'class="sheetwrap"' in stage and 'class="pager"' in stage
    assert 'class="typebar"' not in stage and 'class="stagetop"' not in stage
    # The key beside the name, so it is learned by meeting it rather than by reading a legend.
    assert '<kbd class="tag">' in script and "key.key" in script
    assert ".typebar .what kbd.tag{" in index_html()


def test_the_right_column_is_the_walk_the_element_and_the_keys():
    script = _script()
    todo = script[script.index("function drawTodo("):script.index("function actionFor(")]
    assert "Reading order" in todo
    assert 'id="typebar"' in todo, "the element chooser lives here now"
    assert 'class="keylist"' in todo, "and so do the keys"


def test_adding_and_removing_an_element_are_an_obvious_pair():
    # Retagging answers "what is this?". Adding and removing do not, so they get their own pair of
    # controls and the obvious pair of keys rather than hiding among the twenty types.
    script = _script()
    assert "key==='+'||key==='='" in script
    assert "key==='-'||key==='_'" in script
    assert "function addElement(" in script
    assert 'id="addel"' in script and 'id="delel"' in script


def test_reading_order_and_the_element_share_one_panel():
    script = _script()
    todo = script[script.index("function drawTodo("):script.index("function actionFor(")]
    assert 'class="walkhead"' in todo and 'id="typebody"' in todo
    # The explanatory paragraph and the progress bar are gone; the count stays, because the check
    # cannot tick without it.
    assert "walkbar" not in script
    assert 'id="roprogress"' in todo


def test_nothing_has_to_be_saved_by_hand():
    # No Apply button anywhere: an edit goes to the server on its own and the header says where
    # the rebuild has got to. State that exists only in the tab is state that can be lost.
    script = _script()
    assert "edapply" not in script, "the Apply button should be gone"
    assert "function applyEdits(" in script and "setTimeout(sendEdits" in script
    assert "applyEdits();" in script
    # The rebuild must not throw the workspace away -- that would lose the user's place mid-edit.
    assert "function awaitRebuild(" in script
    assert "renderWorking" not in script[script.index("function sendEdits("):
                                         script.index("function refreshElements(")]


def test_descriptions_are_asked_for_in_the_walk_and_nowhere_else():
    # Two places to type the same description is one place too many, and the report is the wrong
    # one: a column of thumbnails stripped of the page they came from, asking for work the walk is
    # already about to ask for on the picture itself.
    script = _script()
    assert "renderFigures" not in script
    assert "applyDescriptions" not in script
    assert "figlist" not in script and "figthumb" not in script
    assert "'describe'" not in script, "the report must not offer a describe control"


def test_landing_on_an_undescribed_picture_asks_for_a_description():
    # A picture is the one thing the machine cannot finish, so the walk stops for it rather than
    # leaving a list of homework behind. It asks where the person is already looking -- on the
    # element, as they tab onto it -- with Rebind's own guess already in the box.
    script = _script()
    assert "function openAltPrompt(" in script
    assert "if(needsAlt(e) && !ed.altAsked[e.id]) openAltPrompt(e.id);" in script
    prompt = script[script.index("function openAltPrompt("):script.index("function closeAltPrompt(")]
    assert "altGuess(e)" in prompt, "the guess must be pre-filled, not an empty box"
    assert "'aria-modal','true'" in prompt, "the prompt must trap a screen reader inside it"
    # Accepting is one key, and it carries on with the walk; skipping leaves the document alone.
    assert "ev.key==='Enter' && !ev.shiftKey" in prompt
    assert "function focusNext(" in script
    assert 'id="altskip"' in prompt


def test_a_description_is_asked_for_once_not_every_time():
    # Closing the prompt hands focus back to the box whose focus opened it. Without a record of
    # having asked, that is an unbreakable loop -- the prompt reopens forever and the page is
    # unusable. Space is what asks again.
    script = _script()
    assert "ed.altAsked[elementId]=true;" in script
    assert "openAltPrompt(e.id);" in script[script.index("if(key===' '&&kindOf(e)==='Figure'"):
                                            script.index("if(key==='['||key===']')")]


def test_enter_opens_the_hotkey_palette_and_escape_leaves_it_alone():
    script = _script()
    assert "if(key==='Enter'){ ev.preventDefault(); openPalette(" in script
    assert "ev.key==='Escape'" in script
    assert "'aria-modal','true'" in script, "the palette must trap a screen reader inside it"
