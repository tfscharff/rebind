"""The browser UI: a thin shell over the conversion pipeline.

Rebind runs as a local web app (the OpenRefine pattern), so a librarian hands over a broken scan
and gets back a born-accessible document. The page's signature is the *condition report* -- Adobe's
own accessibility checklist, ticked off item by item against the document Rebind actually produced,
because "know what you don't know" is the product's whole thesis. What cannot be ticked moves to
the right, next to the one thing that would fix it. The document itself sits in the middle, marked
up, and every element in it is a tab stop. The HTML/CSS/JS is inlined (no static files, no external
requests) so it works offline and needs nothing extra bundled into the frozen build.
"""

from __future__ import annotations


def build_review(*, page_count: int, ocr_pages: tuple[int, ...],
                 empty_pages: tuple[int, ...]) -> dict:
    """A short, honest account of what remediation did and what -- if anything -- needs a human.

    Remediation preserves the original and only adds accessibility, so there is little to review.
    We report a one-line summary of what happened, and flag only genuine problems (pages where no
    text could be recovered at all). Returns `{summary, items, clean}` where each item is
    `{kind, title, detail, pages}`.
    """
    scanned = len(ocr_pages)
    if scanned and empty_pages:
        summary = (f"{page_count} pages. Text was recognized on {scanned} scanned "
                   f"page{'s' if scanned != 1 else ''}.")
    elif scanned:
        summary = (f"{page_count} pages. {scanned} scanned page"
                   f"{'s were' if scanned != 1 else ' was'} recognized; the rest already had text.")
    else:
        summary = f"{page_count} pages. The document already had text; nothing needed recognizing."

    items: list[dict] = []
    if empty_pages:
        items.append({
            "kind": "no-text",
            "title": "Pages with no readable text",
            "detail": ("These pages are images that no text could be recovered from -- they may be "
                       "blank, or photos/figures with no words. They are kept exactly as they were, "
                       "but they carry no readable text."),
            "pages": sorted(empty_pages),
        })
    return {"summary": summary, "items": items, "clean": not items}


def index_html() -> str:
    """The single-page app, fully self-contained (no external requests)."""
    return _PAGE


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rebind — rebuild a document as an accessible PDF</title>
<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAEv0lEQVR4nMVXTWhcVRT+zr33vTeT1qqN0qL0l2ISsdJQEWmFEbTgogu1TKHEoMSF4M6Vy2nAZUXQjUZIFLQIEX/ApZu2i4jaNiU2WqppamkjuOhPbDLz3r33yHlvJp38zGTyA57hzsy779xzvnPud+47D1hCCoWCwTpL1SYtnKdF18wAEaNUUvunph6zjraiXAawCkzGQHn+NypvuPjj8Puz6VyppNDf75cCQABY/nS/3tfHrN4G0KW01oJpMfbWhJ344mtEGJpR/sSlwcHpUqmk+qsgqD7yvT1vPaCD+CMdREe9TeCdAzP7VfquWiYipUiHIXySnPdx3Hfh5GejtUwo0SkWiwrHj5PS5U9MLn/Ulmdjb20KXZZjLQMg9o7FptK6m4z5ev+xYw/V4Ckhx/DwsNs3cbXXRPkjyd27MYjC2uK1BF+fBoDCpFKJdRjusip6T6IvFAqaJBUFQN38868xHQQdLkmyqOe8c4ZljiGt+sz0fUqgOZELJqWsS1zX2MmhCSNI7vS+0UFa7/HWouZcJCGCJw0fV8DOrSJuQi4M500Jp3QQhN775wBMZLXl+REVGOOd8ylpwKiQwRPlm3hq6jK2HjqMriOvIq7MAmoOX0Px3iOKcvh5fBwffvUt2qKoPhNMICHGDrlIAbDSdmE9WiI8msyg487f6Ni5DZ2Pd644A5FR+GD4m6VvKpXcA+A9EeZHRtUtmFUBZqxF7BxOj4wgYYZKc7S0SP7iJMGz3d0ox4mU8ZJ6zJzGbJpFIBoKmUOlFEgRlM+cNCoPuVfTl7GcmGU1MrQwRHj+wEGsRGxG6rUDICGWUPbKhJxMzXWJUse7tm2HXq8MEBEcM0YujKHiHJSihmeC6FbiGO0PbobWuiEHVgTAMyMgQu9LhwHoFlZkT68/rl9fdgsU/mcxrSgJq6X8vvzu+5a34MihFxAasz5bwMzQRDi470nIiZGltd5wXVmSsN9h08aNcDdurE8VcHWvdu/ctYxW7a8cFnrhg2jtZTg5eWVBBig999tyOWxp3zyX7vRZkGtrxTSaAhBzaVcCpEfxmXOjVQ5IPjhNfMUm2LZlC148eADeuxScABEQMloCQEotypVMBMzIe4u8Mchpjddeebm5taD26M2ysyGfb6hK0viiCsAz6/rqljuGGdeDNlzatBW3J69hevx3VCqzWfrreZU20dnZXxOJPAgi/DR+sSEJPXPq28iXYjvNntJeILNJCNlhPHc/ft39NPxv18D97zaPvklDspiMLKS+VQNAekN4MZlJ/lFaP8ze10ifbgHYgoIACIP1aMlkWjnr4D2fToMvlEr67MDADIE+NmGoWJg0hzPLhhgRXOlvq6OqPy9uZqujSLFNzozt2TGKYlELTmlMaf/41H02786YINxrK2XpjIP164oz50pryfgMnDsw+vnQhWKxqNN6KgE4Ozxw21vX4729bHI5oTMx2MnCNQ2kv96EkSGlptm6N2vO5XXgXoTVN5XO3t72nM6dYO97dBgGjXufFiOXj7USzw82id8Z++LTc/IucurUKZnEfOt1L457e/o6dGCeAbvtaR4o6+FaFg8oo+SwugWtRs4PDvwi07XImy0lUVpFsM1FmlAJcIH8ByYamJYOK3O2AAAAAElFTkSuQmCC">
<style>
:root{
  --paper:#fbfaf8; --panel:#ffffff; --ink:#1e2321; --muted:#6b7671;
  --cloth:#2f5d62; --cloth-deep:#234a4e; --stamp:#a6412e; --stamp-deep:#873625;
  --line:#e4e0d8; --info:#2f5d62; --attention:#a6412e; --pass:#3d7a4e;
  --radius:10px; --maxw:56rem;
  --serif:Georgia,"Iowan Old Style","Times New Roman",serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,"SFMono-Regular","Cascadia Code",Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#15191a; --panel:#1c2122; --ink:#eef0ec; --muted:#9aa39e;
    --cloth:#6bb3ba; --cloth-deep:#8fccd2; --stamp:#e08066; --stamp-deep:#eb9a83;
    --line:#2c3335; --info:#8fccd2; --attention:#e08066; --pass:#7fbf90;
  }
}
*{box-sizing:border-box}
html{color-scheme:light dark}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
  line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 1.25rem;transition:max-width .2s}
/* The workspace is far wider than the reading measure the upload page is set to. */
body.wide .wrap{max-width:104rem}
header.site{border-bottom:1px solid var(--line);padding:1.4rem 0}
.brand{display:flex;align-items:baseline;gap:.7rem}
.brand h1{font-family:var(--serif);font-weight:600;font-size:1.7rem;margin:0;letter-spacing:-.01em}
.brand .tag{color:var(--muted);font-size:.92rem}
/* The finished document is always one click away, from anywhere in the workspace. */
.headright{margin-left:auto;display:flex;align-items:center;gap:.8rem}
.saved{font-size:.78rem;color:var(--muted);font-family:var(--mono)}
.saved.working{color:var(--stamp)}
main{padding:2.4rem 0 4rem}
/* Drop zone */
.drop{position:relative;border:1.5px dashed var(--cloth);border-radius:var(--radius);
  background:var(--panel);padding:2.6rem 1.5rem;text-align:center;transition:border-color .15s,background .15s}
.drop.over{border-color:var(--stamp);background:color-mix(in srgb,var(--stamp) 6%,var(--panel))}
.drop svg{width:34px;height:34px;color:var(--cloth);margin-bottom:.6rem}
.drop p{margin:.2rem 0;color:var(--muted)}
.drop .file-label{display:inline-block;margin-top:.9rem;background:var(--cloth);color:#fff;
  font-weight:600;padding:.62rem 1.1rem;border-radius:8px;cursor:pointer}
.drop .file-label:focus-within{outline:3px solid var(--stamp);outline-offset:2px}
.drop input[type=file]{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden}
/* Status */
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:1.1rem 1.2rem;margin-top:1rem}
.status{display:flex;align-items:center;gap:.9rem}
.spinner{width:20px;height:20px;border:3px solid var(--line);border-top-color:var(--cloth);
  border-radius:50%;animation:spin 1s linear infinite;flex:none}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spinner{animation:none}}
.status .what{font-weight:600}
.status .elapsed{color:var(--muted);font-size:.86rem;font-family:var(--mono)}
.btn{display:inline-flex;align-items:center;gap:.5rem;font-weight:600;text-decoration:none;
  padding:.62rem 1.05rem;border-radius:8px;border:1px solid transparent;font-size:.95rem;
  cursor:pointer;font-family:inherit}
.btn.primary{background:var(--cloth);color:#fff}
.btn.primary:hover{background:var(--cloth-deep)}
.btn.ghost{background:transparent;color:var(--cloth);border-color:var(--line)}
.btn.small{padding:.2rem .6rem;font-size:.85rem}
.btn:disabled{opacity:.5;cursor:default}
.btn:focus-visible{outline:3px solid var(--stamp);outline-offset:2px}
h2{font-family:var(--serif);font-size:1.15rem;margin:0 0 .3rem}
.sub{color:var(--muted);font-size:.88rem;margin:0 0 .8rem}

/* ---- The workspace: report | document | what needs you --------------------------------------
   The document is the point, so the middle column is twice either side of it. The whole thing is
   sized to the window: the page never scrolls, each column scrolls inside itself if it must, and
   every column starts at the same line so the three read as one surface. */
body.wide{height:100vh;overflow:hidden;display:flex;flex-direction:column}
body.wide header.site{flex:none;padding:.7rem 0}
body.wide main{flex:1;min-height:0;padding:.7rem 0;display:flex}
body.wide .wrap{flex:1;min-height:0;display:flex}
body.wide #work{flex:1;min-height:0;display:flex}
body.wide .panel{margin-top:0}
.workspace{flex:1;min-height:0;display:grid;
  grid-template-columns:minmax(13rem,1fr) minmax(0,2.2fr) minmax(14rem,1fr);gap:1rem}
@media (max-width:80rem){ .workspace{grid-template-columns:minmax(0,1.6fr) minmax(13rem,1fr)}
  .col-report{display:none} }
.col-report,.col-todo,.col-stage{min-height:0;height:100%;display:flex;flex-direction:column}
.col-report>.panel{flex:1;min-height:0;overflow-y:auto}
/* The right column stacks three blocks: why you are walking, what you have landed on, and what
   you can press. Only the middle one grows, so the keys stay visible at the bottom. */
.col-todo{gap:.6rem}
.col-todo>.panel{flex:none}
.col-todo>.typebar{flex:1;min-height:8rem;overflow-y:auto}
.walkhead{display:flex;align-items:baseline;justify-content:space-between;gap:.6rem;
  border-bottom:1px solid var(--line);padding-bottom:.35rem;margin-bottom:.5rem}
.walkhead h2{margin:0;font-size:1rem}
.walknum{font-family:var(--mono);font-size:.74rem;color:var(--muted);white-space:nowrap}
.walknum.done{color:var(--pass);font-weight:700}
.addrem{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}
.addrem .hint{margin:0;flex:1;min-width:9rem}
.addrem b{font-size:1rem;line-height:1}
.keys h2{font-size:1rem}
.keys .sub{margin:0 0 .4rem}
.keys .sub b{color:var(--ink)}
.keylist{margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(7.5rem,1fr));
  gap:.1rem .5rem}
.keylist>div{display:flex;gap:.35rem;align-items:baseline}
.keylist dt{margin:0;flex:none}
.keylist dd{margin:0;font-size:.75rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.altlab{display:block;font-size:.78rem;font-weight:600;margin:.5rem 0 0}
.altlab .req{color:var(--attention);font-weight:400}

/* ---- Left: the checklist, ticked off one at a time ---- */
.report .score{font-family:var(--mono);font-size:.78rem;color:var(--muted);margin:0 0 .3rem}
.report .note{font-size:.78rem;color:var(--muted);margin:0 0 .7rem;
  border-left:2px solid var(--pass);padding-left:.5rem}
.group{margin:.9rem 0 0}
.group h3{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  margin:0 0 .25rem;font-weight:600;font-family:var(--sans)}
ul.checks{list-style:none;margin:0;padding:0}
li.check{display:flex;gap:.5rem;align-items:baseline;padding:.16rem 0;font-size:.86rem;
  opacity:0;transform:translateY(-2px);transition:opacity .18s ease,transform .18s ease}
li.check.shown{opacity:1;transform:none}
@media (prefers-reduced-motion:reduce){li.check{transition:none}}
li.check .mark{flex:none;width:1.1rem;text-align:center;font-weight:700}
li.check.pass .mark{color:var(--pass)}
li.check.pass .name{color:var(--muted)}
li.check.needs-you .mark,li.check.manual .mark{color:var(--attention)}
li.check.needs-you .name,li.check.manual .name{font-weight:600}
li.check.na{opacity:.45}
li.check.na.shown{opacity:.45}
li.check .name{min-width:0}
/* An item with a place in the document is a button that takes you there. */
li.check button.where{font:inherit;font-size:.86rem;background:none;border:none;padding:0;
  color:inherit;text-align:left;cursor:pointer;display:flex;gap:.4rem;align-items:baseline;
  min-width:0;font-weight:600}
li.check button.where:hover{text-decoration:underline}
li.check button.where:focus-visible{outline:2px solid var(--stamp);outline-offset:2px;
  border-radius:3px}
li.check .at{font-family:var(--mono);font-size:.7rem;color:var(--muted);flex:none;font-weight:400}

/* ---- Middle: the document, and only the document ---- */
.col-stage{gap:.55rem}
/* The page fills whatever height is left, and its width follows from that -- so the whole page is
   always visible, whatever shape it is, without the window ever scrolling. */
.sheetwrap{flex:1;min-height:0;display:flex;justify-content:center;align-items:stretch}
.sheet{position:relative;height:100%;border:1px solid var(--line);border-radius:6px;
  overflow:hidden;background:#fff}
.sheet img{display:block;height:100%;width:auto}
/* The chooser sits under the page: the element's name in large type, what it is, and its nearest
   HTML equivalent, which is the part most people recognise on sight. */
.typebar{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel);
  padding:.55rem .9rem;display:flex;flex-direction:column;justify-content:center}
.typebar .what{font-family:var(--serif);font-size:1.9rem;line-height:1.1;margin:0;
  letter-spacing:-.02em;display:flex;align-items:baseline;gap:.55rem;flex-wrap:wrap}
.typebar .what kbd.tag{font-size:.95rem;padding:.1rem .45rem;border-color:var(--stamp);
  color:var(--stamp);letter-spacing:0;font-weight:700;background:var(--paper)}
.typebar .what.idle{font-size:1.05rem;color:var(--muted);font-family:var(--sans)}
.typebar .why{margin:.2rem 0 0;color:var(--muted);font-size:.85rem}
.typebar .hint{margin:.25rem 0 0;font-size:.75rem;color:var(--muted);font-family:var(--mono)}
.typebar .altbox{margin:.35rem 0 0;display:flex;gap:.5rem;align-items:flex-start}
.typebar .altbox textarea{flex:1;font:inherit;font-size:.86rem;padding:.3rem .5rem;
  border:1px solid var(--line);border-radius:5px;background:var(--paper);color:var(--ink);
  resize:none}
.ob{position:absolute;border:1.5px solid var(--stamp);border-radius:2px;
  background:color-mix(in srgb,var(--stamp) 7%,transparent);cursor:pointer}
.ob i{position:absolute;left:-1.5px;top:-1.5px;font-style:normal;font-family:var(--mono);
  font-size:.68rem;line-height:1;padding:.15rem .28rem;background:var(--stamp);color:#fff;
  border-radius:2px 0 2px 0}
.ob.fig{border-style:dashed}
.ob.gone{border-color:var(--muted);background:repeating-linear-gradient(45deg,
  color-mix(in srgb,var(--muted) 18%,transparent) 0 4px,transparent 4px 8px)}
.ob.gone i{background:var(--muted)}
.ob:focus{outline:none;border-width:3px;background:color-mix(in srgb,var(--stamp) 18%,transparent);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--stamp) 45%,transparent)}
.pager{flex:none;display:flex;gap:.5rem;align-items:center;justify-content:center}
.pager .pageno{font-family:var(--mono);font-size:.8rem;color:var(--muted)}

/* ---- Right: what is not ticked, and what would tick it ---- */
.todo .item{border:1px solid var(--line);border-left:4px solid var(--attention);border-radius:8px;
  background:var(--panel);padding:.7rem .8rem;margin-bottom:.6rem}
.todo .item.manual{border-left-color:var(--info)}
.todo .item .title{font-weight:600;font-size:.92rem}
.todo .item .detail{color:var(--muted);font-size:.85rem;margin:.25rem 0 0}
.todo .item .need{font-size:.85rem;margin:.4rem 0 0}
.todo .allclear{color:var(--pass);font-weight:600}
.pagejump{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.45rem}
.fixfield{display:flex;gap:.4rem;margin-top:.5rem}
.fixfield input{flex:1;min-width:0;font:inherit;font-size:.85rem;padding:.3rem .45rem;
  border:1px solid var(--line);border-radius:5px;background:var(--paper);color:var(--ink)}
.ratios{list-style:none;margin:.5rem 0 0;padding:0;font-size:.82rem}
.ratios li{display:flex;gap:.4rem;align-items:baseline;padding:.25rem 0;
  border-top:1px solid var(--line)}
.ratios li:first-child{border-top:none}
.ratios .swatch{font-family:var(--serif);padding:.05rem .3rem;border-radius:3px;
  border:1px solid var(--line);flex:none}
.ratios .ratio{font-family:var(--mono);font-weight:700;color:var(--attention)}
.ratios .where{font-family:var(--mono);font-size:.74rem;color:var(--muted)}
.ratios .sample{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.caveat{font-size:.78rem;color:var(--muted);display:block;margin-top:.4rem}
.actions{display:flex;flex-direction:column;gap:.5rem;margin-bottom:.8rem}

/* ---- The floating hotkey palette, opened with Enter ---- */
.palette{position:fixed;inset:0;background:color-mix(in srgb,#000 45%,transparent);
  display:flex;align-items:center;justify-content:center;z-index:50;padding:1rem}
.palette .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:1.1rem 1.3rem;max-width:44rem;width:100%;max-height:88vh;overflow-y:auto;
  box-shadow:0 18px 50px rgba(0,0,0,.3)}
.palette h2{margin:0}
.palette .keys{list-style:none;margin:.7rem 0 0;padding:0;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:.15rem .9rem}
.palette .keys li{display:flex;gap:.5rem;align-items:baseline;padding:.2rem .3rem;border-radius:5px}
.palette .keys li.current{background:color-mix(in srgb,var(--stamp) 12%,transparent)}
/* An action, not a type: set apart from the list of things an element can be. */
.palette .keys li.action{grid-column:1 / -1;margin-top:.5rem;padding-top:.5rem;
  border-top:1px solid var(--line)}
.palette .keys .lab{font-size:.88rem}
.palette .keys .what{font-size:.76rem;color:var(--muted)}
/* ---- The description prompt, opened by landing on a picture with no description ---- */
.palette .card.alt{max-width:32rem}
.palette .card.alt .sub{margin:.3rem 0 .7rem;font-size:.84rem;color:var(--muted)}
.altshot{display:block;max-width:100%;max-height:11rem;object-fit:contain;margin:0 0 .7rem;
  border:1px solid var(--line);border-radius:6px;background:var(--paper)}
#altinput{width:100%;font:inherit;font-size:.9rem;padding:.45rem .6rem;border-radius:6px;
  border:1px solid var(--line);background:var(--paper);color:var(--ink);resize:vertical}
.altact{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-top:.6rem}
.altact .hint{font-size:.76rem;color:var(--muted);margin-left:auto}
kbd{font-family:var(--mono);font-size:.72rem;background:var(--paper);border:1px solid var(--line);
  border-bottom-width:2px;border-radius:4px;padding:.05rem .32rem;color:var(--ink);flex:none;
  min-width:1.35rem;text-align:center}
.error{border-left:4px solid var(--attention)}
.error .detail{color:var(--ink)}
.visually-hidden{position:absolute;width:1px;height:1px;clip:rect(0 0 0 0);overflow:hidden}
a.reset{display:inline-block;margin-top:1rem;color:var(--cloth);font-size:.9rem}
/* The last block on the page: take the document, or start another. */
.finish{display:flex;flex-direction:column;align-items:stretch;gap:.15rem}
.finish #dl{text-align:center}
.finish a.reset{margin-top:.5rem;text-align:center}
.struct-badge{font-family:var(--mono);font-size:.74rem;text-transform:uppercase;
  letter-spacing:.03em;margin:.2rem 0 .6rem}
.struct-badge.ok{color:var(--pass)}
.struct-badge.attention{color:var(--attention);text-transform:none;letter-spacing:normal}
</style>
</head>
<body>
<header class="site"><div class="wrap brand">
  <h1>Rebind</h1><span class="tag">accessible PDF reconstruction</span>
  <div class="headright" id="headright"></div>
</div></header>

<main class="wrap">
  <section id="intake" aria-labelledby="intake-h">
    <h2 id="intake-h" class="visually-hidden">Choose a PDF</h2>
    <div class="drop" id="drop">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 16V4m0 0l-4 4m4-4l4 4"/><path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg>
      <p><strong>Drop a PDF here</strong></p>
      <p>or</p>
      <label class="file-label">Choose a file
        <input type="file" id="file" accept="application/pdf,.pdf">
      </label>
    </div>
  </section>

  <div id="live" role="status" aria-live="polite" class="visually-hidden"></div>
  <section id="work" hidden aria-labelledby="work-h"></section>
</main>

<script>
(function(){
  var drop=document.getElementById('drop');
  var file=document.getElementById('file');
  var work=document.getElementById('work');
  var live=document.getElementById('live');
  var poll=null;

  function say(msg){ live.textContent=msg; }

  // Rebind has no window of its own, so closing this tab is the only way a user can quit it.
  // The heartbeat is what the server actually watches: stop sending it and the process exits,
  // which is what keeps an invisible instance from lingering (and from blocking the next
  // install). The beacon on the way out is a courtesy that makes quitting immediate; it is not
  // relied upon, because it is never sent by a browser that was killed rather than closed.
  fetch('/heartbeat').catch(function(){});
  setInterval(function(){ fetch('/heartbeat').catch(function(){}); }, 5000);
  window.addEventListener('pagehide', function(){
    if(navigator.sendBeacon) navigator.sendBeacon('/shutdown', '');
  });

  ['dragenter','dragover'].forEach(function(e){
    drop.addEventListener(e,function(ev){ev.preventDefault();drop.classList.add('over');});
  });
  ['dragleave','drop'].forEach(function(e){
    drop.addEventListener(e,function(ev){ev.preventDefault();drop.classList.remove('over');});
  });
  drop.addEventListener('drop',function(ev){
    var f=ev.dataTransfer.files[0]; if(f) start(f);
  });
  file.addEventListener('change',function(){ if(file.files[0]) start(file.files[0]); });

  function esc(s){return String(s).replace(/[&<>"]/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}

  function start(f){
    if(f.type && f.type.indexOf('pdf')===-1 && !/\.pdf$/i.test(f.name)){
      showError("That doesn't look like a PDF. Choose a .pdf file.");
      return;
    }
    document.getElementById('intake').hidden=true;
    var started=Date.now();
    renderWorking(f.name, started);
    say('Working on '+f.name);
    fetch('/convert?filename='+encodeURIComponent(f.name),{
      method:'POST',headers:{'content-type':'application/pdf'},body:f
    }).then(function(r){return r.json();}).then(function(j){
      if(j.error){showError(j.error);return;}
      watch(j.job_id, f.name, started);
    }).catch(function(){ showError('Could not reach the converter. Is Rebind still running?'); });
  }

  function renderWorking(name, started){
    document.body.classList.remove('wide');
    work.hidden=false;
    work.innerHTML='<h2 id="work-h" class="visually-hidden">Progress</h2>'+
      '<div class="panel"><div class="status">'+
      '<div class="spinner" aria-hidden="true"></div>'+
      '<div><div class="what" id="what">Reading '+esc(name)+'…</div>'+
      '<div class="elapsed" id="elapsed">0s</div></div></div></div>';
    tick(started);
  }
  var elapsedTimer=null;
  function tick(started){
    if(elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer=setInterval(function(){
      var el=document.getElementById('elapsed');
      if(!el){clearInterval(elapsedTimer);return;}
      el.textContent=Math.round((Date.now()-started)/1000)+'s';
    },1000);
  }

  function watch(id, name, started){
    poll=setInterval(function(){
      fetch('/jobs/'+id).then(function(r){return r.json();}).then(function(s){
        var what=document.getElementById('what');
        if(what && s.stage) what.textContent=s.stage;
        if(s.status==='done'){ clearInterval(poll); done(id, name, s); }
        else if(s.status==='error'){ clearInterval(poll); showError(s.error||'Conversion failed.'); }
      }).catch(function(){});
    },1200);
  }

  // Checks whose fix is asked for in the walk itself. They keep their row in the report -- the
  // rule did not pass and the report says so -- but they get no second block underneath asking
  // for the same work in a worse place.
  var ASKED_IN_THE_WALK={'figures-alternate-text':true};

  // ---- State ---------------------------------------------------------------------------------
  var ed={id:null,name:null,elements:[],pages:{},tags:[],keys:[],page:1,pageList:[],
          tags_edit:{},removed:{},alts:{},focused:null,figures:[],checks:[],status:null,
          palette:false,walked:{},artifact:null,allKeys:[],altAsked:{}};

  function done(id, name, s){
    if(elapsedTimer) clearInterval(elapsedTimer);
    ed.id=id; ed.name=name; ed.status=s;
    ed.figures=s.figures||[]; ed.checks=s.checklist||[];
    document.body.classList.add('wide');
    work.innerHTML='<h2 id="work-h" class="visually-hidden">Your accessible PDF</h2>'+
      '<div class="workspace">'+
      '<aside class="col-report" id="report" aria-labelledby="rep-h"></aside>'+
      '<div class="col-stage" id="stage"></div>'+
      '<aside class="col-todo" id="todo" aria-labelledby="todo-h"></aside>'+
      '</div>';
    drawHeader();
    drawReport();
    // The right column is drawn by loadEditor, once the page list is known: the reading-order
    // block counts pages, and it cannot count them before they have arrived.
    loadEditor(id, name);
    say('Done. Your accessible PDF is ready.');
  }

  // Only the save state lives in the header. The download used to sit here too, which put a tab
  // stop in front of every one of the document's elements -- the header is the first thing in the
  // page, and getting to element 1 meant tabbing past it every time.
  function drawHeader(){
    var host=document.getElementById('headright');
    if(!host) return;
    host.innerHTML='<span class="saved" id="savestate">All changes saved</span>';
  }

  function setSaveState(text, working){
    var el=document.getElementById('savestate');
    if(!el) return;
    el.textContent=text;
    el.className='saved'+(working?' working':'');
  }

  // ---- Left column: Adobe's checklist, ticked off as it goes ----------------------------------
  function drawReport(){
    var host=document.getElementById('report');
    if(!host) return;
    var passed=0, total=0;
    ed.checks.forEach(function(c){ if(c.status!=='n/a'){ total++; if(c.status==='pass') passed++; } });
    var groups=[], byGroup={};
    ed.checks.forEach(function(c){
      if(!byGroup[c.group]){ byGroup[c.group]=[]; groups.push(c.group); }
      byGroup[c.group].push(c);
    });
    var h='<section class="panel report"><h2 id="rep-h">Accessibility report</h2>'+
      '<p class="score">'+passed+' of '+total+' checks pass</p>';
    groups.forEach(function(g){
      h+='<div class="group"><h3>'+esc(g)+'</h3><ul class="checks">';
      byGroup[g].forEach(function(c){ h+=checkRow(c); });
      h+='</ul></div>';
    });
    // Whatever did not pass, with the thing that would fix it, directly under the list it came
    // from. Two are deliberately not repeated here: reading order, which has the whole right
    // column, and the pictures needing a description, which are asked for one at a time in the
    // walk. Both already have a place; a second block asking for the same work is clutter with a
    // tab stop attached.
    var open=ed.checks.filter(function(c){
      return effectiveStatus(c)==='needs-you'
        && c.action!=='reading-order' && !ASKED_IN_THE_WALK[c.key]; });
    if(open.length){
      h+='<div class="group"><h3>Needs you</h3>';
      open.forEach(function(c){
        h+='<div class="item" id="item-'+esc(c.key)+'">'+
          '<div class="title">'+esc(c.title)+'</div>'+
          '<p class="detail">'+esc(c.detail)+'</p>'+
          (c.need? '<p class="need">'+esc(c.need)+'</p>':'')+
          actionFor(c)+'</div>';
      });
      h+='</div>';
    }
    h+=structureBadge(ed.status.structure_ok, ed.status.structure_issues||[])+'</section>';
    host.innerHTML=h;
    wireActions();
    // The report's tab stops are its failing checks and nothing else, so that the run from the
    // first "!" to element 1 is exactly as long as the list of things wrong. The controls under
    // "Needs you" are still reachable -- the "!" row is what takes you to them -- but they are out
    // of the natural order, because otherwise every one of them stands between the report and the
    // document, and the document is where the work is.
    Array.prototype.slice.call(
      host.querySelectorAll('.item input, .item button, .item a, .item textarea'))
      .forEach(function(el){ el.tabIndex=-1; });
    // Activating a failing check takes you to whatever would fix it: the page it is on, with the
    // middle column turned to it, or the field that sets it.
    Array.prototype.slice.call(host.querySelectorAll('.check button')).forEach(function(b){
      b.addEventListener('click',function(){
        var page=b.getAttribute('data-goto');
        if(page){ goToPage(parseInt(page,10)); return; }
        var item=document.getElementById('item-'+b.getAttribute('data-item'));
        if(!item){ say('Nothing to do for that one here.'); return; }
        item.scrollIntoView({block:'nearest'});
        var field=item.querySelector('input, textarea, button');
        if(field) field.focus();
      });
    });
    revealChecks(host);
  }

  function checkRow(c){
    var status=effectiveStatus(c);
    var cls=status==='n/a'?'na':status;
    var mark=status==='pass'?'✓':(status==='n/a'?'–':'!');
    var where=(c.locations&&c.locations.length)? c.locations[0].page : null;
    var label=esc(c.title)+'<span class="visually-hidden">: '+esc(statusWord(status))+'</span>';
    // Every check that did not pass is a tab stop, whether or not it names a page: the walk
    // through the report is the walk through what is wrong, and a failing check you cannot reach
    // by keyboard is one you cannot act on. Passing and not-applicable rows are read, not visited.
    var open=(status!=='pass'&&status!=='n/a');
    var body=open
      ? '<button type="button" class="where"'+
        (where? ' data-goto="'+where+'"' : ' data-item="'+esc(c.key)+'"')+'>'+label+
        (where? '<span class="at">p. '+where+'</span>' : '')+'</button>'
      : '<span class="name">'+label+'</span>';
    return '<li class="check '+cls+'" data-check="'+esc(c.key)+'">'+
      '<span class="mark" aria-hidden="true">'+mark+'</span>'+body+'</li>';
  }

  // Reading order is the one check whose verdict lives in the browser: it passes when the person
  // has actually walked every page, which is a thing only the page can know.
  function effectiveStatus(c){
    if(c.key==='logical-reading-order') return allPagesWalked()? 'pass' : 'manual';
    return c.status;
  }

  function allPagesWalked(){
    return ed.pageList.length>0 && ed.pageList.every(function(p){ return ed.walked[p]; });
  }

  function walkedCount(){
    return ed.pageList.filter(function(p){ return ed.walked[p]; }).length;
  }

  // Called whenever a page is tabbed into. Only the two places that show reading order are
  // repainted -- redrawing the whole report would replay its tick-through animation on every page
  // turn, and redrawing the right column would discard half-typed descriptions.
  function noteWalked(page){
    if(ed.walked[page]) return;
    ed.walked[page]=true;
    var check=null;
    ed.checks.forEach(function(c){ if(c.key==='logical-reading-order') check=c; });
    if(!check) return;
    var row=document.querySelector('.check[data-check="logical-reading-order"]');
    if(row){
      var done=allPagesWalked();
      row.className='check '+(done?'pass':'manual')+' shown';
      var mark=row.querySelector('.mark');
      if(mark) mark.textContent=done?'✓':'!';
    }
    var progress=document.getElementById('roprogress');
    if(progress) progress.textContent=readingOrderProgress();
    if(allPagesWalked()) say('Every page walked — reading order checked off.');
  }

  function readingOrderProgress(){
    return walkedCount()+' of '+ed.pageList.length+' pages walked.';
  }

  function statusWord(status){
    if(status==='pass') return 'passes';
    if(status==='n/a') return 'not applicable';
    if(status==='manual') return 'needs your eye';
    return 'needs you';
  }

  // Ticked off one at a time, so the report reads as something being worked through rather than a
  // wall that was always there. Capped in total length, and skipped outright for anyone who has
  // asked the system for less motion.
  function revealChecks(host){
    var rows=Array.prototype.slice.call(host.querySelectorAll('li.check'));
    var still=window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if(still){ rows.forEach(function(r){ r.classList.add('shown'); }); return; }
    var step=Math.max(12, Math.min(45, 1400/(rows.length||1)));
    rows.forEach(function(row,i){
      setTimeout(function(){ row.classList.add('shown'); }, i*step);
    });
  }

  // ---- Right column: everything not ticked, next to what would tick it ------------------------
  // The right column: the one check that is still yours, then the element you are standing on,
  // then the keys. Everything here is about the page in the middle, in the order you need it --
  // why you are walking, what you have landed on, and what you can press.
  function drawTodo(){
    var host=document.getElementById('todo');
    if(!host) return;
    var done=allPagesWalked();
    // Reading order and the element you are standing on are the same act -- you learn the order by
    // walking it, and the walk is what the element list is for -- so they share one panel.
    var h='<section class="panel typebar" id="typebar" role="status" aria-live="polite">'+
      '<div class="walkhead"><h2 id="todo-h">Reading order</h2>'+
      '<span class="walknum'+(done?' done':'')+'" id="roprogress">'+
      esc(readingOrderProgress())+'</span></div>'+
      '<div id="typebody">'+idleBanner()+'</div>'+
      '</section>'+
      '<section class="panel keys"><h2>Keys</h2>'+
      '<p class="sub"><b>Tab</b> next element · <b>Shift + Tab</b> previous · '+
      '<b>+</b> add · <b>−</b> remove · <b>[</b> <b>]</b> turn the page · '+
      '<b>Enter</b> lists every type</p>'+
      '<dl class="keylist">'+ed.allKeys.map(function(k){
        return '<div><dt><kbd>'+esc(k.key)+'</kbd></dt><dd>'+esc(k.label)+'</dd></div>';
      }).join('')+'</dl></section>'+
      // Last on the page, and last in the tab order, because they are what you do when the work is
      // finished. Taking the document is the second-to-last stop and starting over is the last:
      // nobody should reach "do another document" without passing the download first.
      '<section class="panel finish"><h2 class="visually-hidden">When you are done</h2>'+
      '<a class="btn primary" id="dl" href="/jobs/'+ed.id+'/pdf" download>Download PDF</a>'+
      '<a class="reset" href="/">Do another document</a></section>';
    host.innerHTML=h;
  }

  // Every open item gets a control. A fix Rebind can perform is a button; a fault that can only
  // be corrected by retagging gets the buttons that take you to it. Nothing is reported without
  // a way to act on it -- an issue with no route to a fix is just a complaint.
  function actionFor(c){
    // Describing a picture is not done here. It is asked for in the walk, on the picture itself,
    // at the moment you reach it -- so what the report offers is the way into the walk at the page
    // that needs it, not a second place to type the same descriptions from a list of thumbnails.
    if(c.action==='set-title') return renderFixField(c, 'A short, meaningful title');
    if(c.action==='set-language') return renderFixField(c, 'e.g. en, en-GB, fr');
    if(c.action==='strip-scripts') return renderFixButton(c, 'Remove the scripts');
    return renderGoto(c);
  }

  function renderGoto(c){
    if(!c.locations || !c.locations.length) return '';
    var out='<div class="pagejump">';
    c.locations.slice(0,24).forEach(function(loc){
      out+='<button type="button" class="btn ghost small jump" data-page="'+esc(loc.page)+
        '">Show me · p. '+esc(loc.page)+'</button>';
    });
    if(c.locations.length>24){ out+='<span class="caveat">…and '+(c.locations.length-24)+
      ' more pages.</span>'; }
    return out+'</div>';
  }

  function renderFixField(c, placeholder){
    return '<div class="fixfield"><input type="text" id="fix-'+esc(c.key)+'" '+
      'aria-label="'+esc(c.title)+'" placeholder="'+esc(placeholder)+'">'+
      '<button type="button" class="btn small dofix" data-fix="'+esc(c.action)+'" '+
      'data-field="fix-'+esc(c.key)+'">Set</button></div>';
  }

  function renderFixButton(c, label){
    return '<button type="button" class="btn small dofix" data-fix="'+esc(c.action)+'" '+
      'style="margin-top:.5rem">'+esc(label)+'</button>';
  }

  function applyFix(fix, value){
    renderWorking(ed.name, Date.now()); say('Applying the fix…');
    fetch('/jobs/'+ed.id+'/fix',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({fix:fix, value:value||''})})
      .then(function(r){return r.json();}).then(function(j){
        if(j.error){ showError(j.error); return; }
        watch(ed.id, ed.name, Date.now());
      }).catch(function(){ showError('Could not apply that fix.'); });
  }

  function structureBadge(ok, issues){
    if(ok) return '<p class="struct-badge ok">PDF/UA-2 tagged</p>';
    return '<p class="struct-badge attention">Structure check: '+
      issues.map(function(i){return esc(i);}).join(', ')+'</p>';
  }

  function wireActions(){
    Array.prototype.slice.call(document.querySelectorAll('.dofix')).forEach(function(b){
      b.addEventListener('click', function(){
        var field=b.getAttribute('data-field');
        var input=field? document.getElementById(field) : null;
        if(input && !input.value.trim()){ say('Type something first.'); input.focus(); return; }
        applyFix(b.getAttribute('data-fix'), input? input.value.trim() : '');
      });
    });
    Array.prototype.slice.call(document.querySelectorAll('.jump')).forEach(function(b){
      b.addEventListener('click', function(){
        goToPage(parseInt(b.getAttribute('data-page'),10));
      });
    });
  }

  // /Artifact is not a structure type, so it never goes to the server as a tag: the same element
  // is in `removed`, which is what actually keeps it out of the reading order.
  function stripArtifacts(tags){
    var out={};
    Object.keys(tags).forEach(function(k){ if(tags[k]!=='Artifact') out[k]=tags[k]; });
    return out;
  }

  // Nothing is ever "unsaved". An edit goes to the server on its own, the document is rebuilt in
  // the background, and the header says where that has got to -- there is no button to forget to
  // press and no state that exists only in this tab. Edits are coalesced so that a run of
  // keystrokes is one rebuild rather than one per press.
  var saveTimer=null, saving=false, pending=false;

  function applyEdits(){
    if(saveTimer) clearTimeout(saveTimer);
    setSaveState('Saving…', true);
    saveTimer=setTimeout(sendEdits, 900);
  }

  function sendEdits(){
    if(saving){ pending=true; return; }
    saving=true; pending=false;
    setSaveState('Saving…', true);
    var removed=Object.keys(ed.removed).filter(function(k){ return ed.removed[k]; });
    fetch('/jobs/'+ed.id+'/edits',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({tags:stripArtifacts(ed.tags_edit), removed:removed, alts:ed.alts})})
      .then(function(r){return r.json();}).then(function(j){
        if(j.error){ saving=false; setSaveState('Could not save', true); return; }
        awaitRebuild();
      }).catch(function(){ saving=false; setSaveState('Could not save', true); });
  }

  // The rebuild is watched quietly: the workspace stays exactly where it is, keeping the page you
  // are on and the element you are standing on, and only the report and the element list are
  // refreshed when it lands. Throwing the view away mid-edit would lose your place.
  function awaitRebuild(){
    var tick=setInterval(function(){
      fetch('/jobs/'+ed.id).then(function(r){return r.json();}).then(function(s){
        if(s.status==='running') return;
        clearInterval(tick);
        saving=false;
        if(s.status==='error'){ setSaveState('Could not save', true); return; }
        ed.status=s; ed.figures=s.figures||[]; ed.checks=s.checklist||[];
        refreshElements();
      }).catch(function(){});
    }, 900);
  }

  function refreshElements(){
    fetch('/jobs/'+ed.id+'/elements').then(function(r){return r.json();}).then(function(d){
      if(!d.error){
        ed.elements=d.elements||[]; ed.pages=d.pages||{};
        ed.pageList=Object.keys(ed.pages).map(Number).sort(function(a,b){return a-b;});
        if(ed.pageList.indexOf(ed.page)<0) ed.page=ed.pageList[0]||1;
      }
      drawReport(); drawStage(); drawTodo();
      setSaveState('All changes saved', false);
      if(pending) sendEdits();
    }).catch(function(){ setSaveState('All changes saved', false); });
  }

  // ---- Middle column: the document, every element a tab stop ----------------------------------
  function loadEditor(id, name){
    var host=document.getElementById('stage');
    if(!host) return;
    host.innerHTML='<div class="panel"><p class="sub" style="margin:0">Laying out the page…</p></div>';
    fetch('/jobs/'+id+'/elements').then(function(r){return r.json();}).then(function(d){
      if(d.error){ host.innerHTML=''; return; }
      ed.elements=d.elements||[]; ed.pages=d.pages||{}; ed.tags=d.tags||[]; ed.keys=d.keys||[];
      // "Not read" is an action, not a type, so it arrives separately -- but it answers to a key
      // exactly like the types do, so the editor holds them in one list for lookup.
      ed.artifact=d.artifact||null;
      ed.allKeys=ed.artifact? ed.keys.concat([ed.artifact]) : ed.keys;
      ed.tags_edit=(d.edits&&d.edits.tags)||{};
      ed.removed={}; ((d.edits&&d.edits.removed)||[]).forEach(function(k){ ed.removed[k]=true; });
      ed.alts=(d.edits&&d.edits.alts)||{};
      ed.pageList=Object.keys(ed.pages).map(Number).sort(function(a,b){return a-b;});
      if(ed.pageList.indexOf(ed.page)<0) ed.page=ed.pageList[0]||1;
      drawStage();
      drawTodo();
    }).catch(function(){ host.innerHTML=''; });
  }

  function elementsOnPage(){
    return ed.elements.filter(function(e){ return e.page===ed.page; });
  }

  function kindOf(e){ return ed.tags_edit[e.id]||e.kind; }

  function keyFor(tag){
    var found=null;
    ed.allKeys.forEach(function(k){ if(k.tag===tag) found=k; });
    return found;
  }

  function tagLabel(t){
    var k=keyFor(t);
    return k? k.label : (t==='Artifact'? 'Not read' : t);
  }

  function drawStage(){
    var host=document.getElementById('stage');
    if(!host) return;
    var items=elementsOnPage();
    var pos=ed.pageList.indexOf(ed.page);
    // The middle column is the document and nothing else: the page, as large as the window will
    // allow, with the pager under it. Everything that talks *about* the page moved right.
    var h='<div class="sheetwrap"><div class="sheet" id="sheet">'+
      (ed.pages[ed.page]? '<img src="'+ed.pages[ed.page]+'" alt="Page '+ed.page+'">':'')+
      items.map(function(e,i){ return boxHtml(e,i); }).join('')+'</div></div>'+
      '<div class="pager">'+
      '<button type="button" class="btn ghost small" id="edprev"'+(pos<=0?' disabled':'')+
      '>‹ Previous</button>'+
      '<span class="pageno">Page '+ed.page+' of '+(ed.pageList.length||1)+'</span>'+
      '<button type="button" class="btn ghost small" id="ednext"'+
      (pos>=ed.pageList.length-1?' disabled':'')+'>Next ›</button></div>';
    host.innerHTML=h;
    wireStage();
  }

  function idleBanner(){
    return '<p class="what idle">Tab into the page to walk its elements</p>'+
      '<p class="why">Every block Rebind tagged is a stop, and the order you meet them in is the '+
      'order a screen reader reads them in. Tab past the last one and the next page opens.</p>';
  }

  function boxHtml(e,i){
    var k=kindOf(e);
    var untagged=(k==='Artifact');
    var gone=!!ed.removed[e.id]||untagged;
    var cls='ob'+(gone?' gone':'')+(k==='Figure'?' fig':'');
    return '<span class="'+cls+'" tabindex="0" role="button" data-box="'+esc(e.id)+'"'+
      ' aria-label="'+esc((untagged?'Not read':tagLabel(k))+', item '+(i+1)+': '+
        (e.text||e.alt||'picture').slice(0,80))+'"'+
      ' style="left:'+e.left+'%;top:'+e.top+'%;width:'+e.width+'%;height:'+e.height+'%">'+
      '<i aria-hidden="true">'+(untagged?'—':(i+1))+'</i></span>';
  }

  // What the banner says while an element has focus: the type in big letters, what that type
  // means, and -- for a figure -- the description box, editable where it stands.
  function showType(e){
    // Only the body is replaced: the reading-order header above it is shared with the walk and
    // must survive every element change.
    var bar=document.getElementById('typebody');
    if(!bar) return;
    var k=kindOf(e), key=keyFor(k);
    var alt=ed.alts[e.id]!==undefined? ed.alts[e.id] : (e.alt||'');
    // The key beside the name, so the way to change it is learned by meeting it rather than by
    // reading a legend: every element you land on tells you which press would have set it.
    var h='<p class="what">'+esc(key? key.label : k)+
      (key&&key.key? '<kbd class="tag">'+esc(key.key)+'</kbd>' : '')+'</p>'+
      '<p class="why">'+esc(key&&key.what? key.what : 'A structure element.')+'</p>';
    // Adding and removing are the two edits that are not "what is this?", so they get their own
    // pair of controls rather than hiding among the types. Whichever one does nothing here is
    // disabled instead of absent, so the pair stays in the same place on every element.
    var out=(k==='Artifact');
    h+='<div class="addrem">'+
      '<button type="button" class="btn ghost small" id="addel"'+(out?'':' disabled')+
      ' title="Add this to the reading order"><b>+</b> Add</button>'+
      '<button type="button" class="btn ghost small" id="delel"'+(out?' disabled':'')+
      ' title="Take this out of the reading order"><b>−</b> Remove</button>'+
      '<span class="hint">'+(out? 'Not read. + puts it into the reading order.'
                                : 'In the reading order. − takes it out.')+'</span></div>';
    if(k==='Figure'){
      // A figure is the one thing a machine cannot finish. The box is here the moment you land on
      // one, pre-filled with the best guess Rebind has -- the document's own caption where there
      // is one, otherwise the figure's own text -- and yours to rewrite.
      h+='<label class="altlab" for="altnow">Description <span class="req">needed</span></label>'+
        '<div class="altbox"><textarea id="altnow" rows="3" data-id="'+esc(e.id)+'" '+
        'placeholder="What does this picture show?">'+esc(alt||altGuess(e))+'</textarea></div>'+
        '<p class="hint">Edit it here, or press Space on the page to be asked again</p>';
    }
    bar.innerHTML=h;
    var add=document.getElementById('addel');
    if(add) add.addEventListener('click', function(){ addElement(e.id); });
    var del=document.getElementById('delel');
    if(del) del.addEventListener('click', function(){ setKind(e.id, 'Artifact'); });
    var box=document.getElementById('altnow');
    if(box) box.addEventListener('input',function(){
      ed.alts[box.getAttribute('data-id')]=box.value;
      applyEdits();
    });
    if(box) box.addEventListener('keydown',function(ev){
      if(ev.key==='Escape'){ ev.preventDefault(); focusBox(e.id); }
    });
  }

  // The best opening line Rebind has for a figure nobody has described yet: the caption it found
  // beside the picture, or -- for a figure made out of text -- that text. Never invented, and
  // never written into the document unless it is still there when the edit is saved.
  // Adding is giving a region Rebind left out a type, and a paragraph is the safe one to give it:
  // it puts the words into the reading order without claiming they are a heading or a table. The
  // type can then be changed with a keystroke like any other element's.
  function addElement(elementId){
    var known=null;
    ed.elements.forEach(function(e){ if(e.id===elementId) known=e; });
    if(!known || kindOf(known)!=='Artifact'){
      say('That is already in the reading order.');
      return;
    }
    setKind(elementId, 'P');
  }

  function altGuess(e){
    var guess='';
    ed.figures.forEach(function(f){ if(f.id===e.id && f.alt_guess) guess=f.alt_guess; });
    return guess || e.text || '';
  }

  function focusBox(elementId){
    var box=document.querySelector('[data-box="'+elementId+'"]');
    if(box) box.focus();
  }

  function goToPage(page, landOnLast){
    if(ed.pageList.indexOf(page)<0) return;
    ed.page=page; ed.focused=null; drawStage();
    var boxes=document.querySelectorAll('.ob');
    var target=landOnLast? boxes[boxes.length-1] : boxes[0];
    if(target) target.focus();
    else noteWalked(page);   // a page with nothing on it is still a page you have been to
  }

  function turnPage(step, landOnLast){
    var at=ed.pageList.indexOf(ed.page);
    var next=ed.pageList[Math.min(Math.max(at+step,0),ed.pageList.length-1)];
    if(next===ed.page) return false;
    goToPage(next, landOnLast);
    return true;
  }

  function wireStage(){
    var prev=document.getElementById('edprev'), next=document.getElementById('ednext');
    if(prev) prev.addEventListener('click',function(){ turnPage(-1); });
    if(next) next.addEventListener('click',function(){ turnPage(1); });

    var items=elementsOnPage();
    var boxes=Array.prototype.slice.call(document.querySelectorAll('.ob'));
    boxes.forEach(function(box,index){
      var e=items[index];
      box.addEventListener('focus',function(){
        ed.focused=e.id; showType(e); noteWalked(ed.page);
        // Tabbing onto an undescribed picture is the moment to ask, and the only moment the
        // person is looking straight at it. Asked once; Space asks again.
        if(needsAlt(e) && !ed.altAsked[e.id]) openAltPrompt(e.id);
      });
      box.addEventListener('click',function(){ box.focus(); });
      box.addEventListener('keydown',function(ev){
        if(ev.ctrlKey||ev.metaKey||ev.altKey) return;
        var key=(ev.key||'');
        // Tabbing off the end of a page carries on to the next one, so checking a whole document
        // is one unbroken run of Tab rather than a page turn every dozen presses.
        if(key==='Tab' && !ev.shiftKey && index===boxes.length-1){
          if(turnPage(1)){ ev.preventDefault(); }
          return;
        }
        if(key==='Tab' && ev.shiftKey && index===0){
          if(turnPage(-1, true)){ ev.preventDefault(); }
          return;
        }
        if(key==='Enter'){ ev.preventDefault(); openPalette(e.id); return; }
        if(key===' '&&kindOf(e)==='Figure'){
          ev.preventDefault();
          openAltPrompt(e.id);
          return;
        }
        if(key==='['||key===']'){ ev.preventDefault(); turnPage(key===']'?1:-1); return; }
        // The two edits that are not "what is this?", on the obvious pair of keys.
        if(key==='+'||key==='='){ ev.preventDefault(); addElement(e.id); return; }
        if(key==='-'||key==='_'){ ev.preventDefault(); setKind(e.id, 'Artifact'); return; }
        if(key==='ArrowDown'||key==='ArrowUp'){
          var to=boxes[index+(key==='ArrowDown'?1:-1)];
          if(to){ ev.preventDefault(); to.focus(); }
          return;
        }
        // The key sets the type straight away. Enter is only for when you cannot remember which
        // key you want; knowing it should never cost you a menu.
        var hit=null;
        ed.allKeys.forEach(function(k){ if(k.key===key.toLowerCase()) hit=k.tag; });
        if(hit){ ev.preventDefault(); setKind(e.id, hit); }
      });
    });
    if(ed.focused){
      var again=document.querySelector('[data-box="'+ed.focused+'"]');
      if(again) again.focus();
    }
  }

  // ---- The floating hotkey palette ------------------------------------------------------------
  function openPalette(elementId){
    closePalette();
    var e=null;
    ed.elements.forEach(function(x){ if(x.id===elementId) e=x; });
    if(!e) return;
    var current=kindOf(e);
    var host=document.createElement('div');
    host.className='palette';
    host.id='palette';
    host.setAttribute('role','dialog');
    host.setAttribute('aria-modal','true');
    host.setAttribute('aria-label','Change what this element is');
    host.innerHTML='<div class="card"><h2>What is this?</h2>'+
      '<p class="sub">Press a key. Esc leaves it as it is.</p><ul class="keys">'+
      ed.allKeys.map(function(k){
        return '<li'+(k.tag===current?' class="current"':'')+
          (k.tag==='Artifact'?' class="action"':'')+'><kbd>'+esc(k.key)+'</kbd>'+
          '<span><span class="lab">'+esc(k.label)+'</span><br>'+
          '<span class="what">'+esc(k.what||'')+'</span></span></li>';
      }).join('')+'</ul></div>';
    document.body.appendChild(host);
    ed.palette=true;
    host.tabIndex=-1;
    host.focus();
    host.addEventListener('keydown',function(ev){
      if(ev.ctrlKey||ev.metaKey||ev.altKey) return;
      if(ev.key==='Escape'){ ev.preventDefault(); closePalette(); focusBox(elementId); return; }
      var pressed=(ev.key||'').toLowerCase();
      var hit=null;
      ed.allKeys.forEach(function(k){ if(k.key===pressed) hit=k.tag; });
      if(hit){ ev.preventDefault(); closePalette(); setKind(elementId, hit); }
    });
    host.addEventListener('click',function(ev){
      if(ev.target===host){ closePalette(); focusBox(elementId); }
    });
  }

  function closePalette(){
    var host=document.getElementById('palette');
    if(host && host.parentNode) host.parentNode.removeChild(host);
    ed.palette=false;
  }

  // ---- The description prompt ------------------------------------------------------------------
  // A picture is the one thing in the document a machine cannot finish, so it is the one thing the
  // walk stops for. Landing on a figure that has no description yet opens this with Rebind's own
  // guess already in the box: the work is to read one line and press Enter, not to compose from
  // nothing. Nothing here is ever written into the document unless the person leaves it there.
  function needsAlt(e){
    if(kindOf(e)!=='Figure') return false;
    var have=ed.alts[e.id]!==undefined? ed.alts[e.id] : (e.alt||'');
    return !String(have).trim();
  }

  function figThumb(elementId){
    var src='';
    ed.figures.forEach(function(f){ if(f.id===elementId && f.thumb) src=f.thumb; });
    return src;
  }

  function openAltPrompt(elementId){
    closeAltPrompt();
    closePalette();
    var e=null;
    ed.elements.forEach(function(x){ if(x.id===elementId) e=x; });
    if(!e) return;
    // Asked once per element. Without this the prompt would reopen the instant it closes, because
    // closing hands focus back to the very box whose focus opened it.
    ed.altAsked[elementId]=true;
    var guess=(ed.alts[e.id]!==undefined? ed.alts[e.id] : (e.alt||'')) || altGuess(e);
    var thumb=figThumb(elementId);
    var host=document.createElement('div');
    host.className='palette';
    host.id='altprompt';
    host.setAttribute('role','dialog');
    host.setAttribute('aria-modal','true');
    host.setAttribute('aria-label','Describe this picture');
    host.innerHTML='<div class="card alt"><h2>What does this picture show?</h2>'+
      '<p class="sub">'+(guess? 'Rebind guessed from the page. Accept it or change it.'
                              : 'Rebind found no caption to guess from.')+'</p>'+
      (thumb? '<img class="altshot" src="'+esc(thumb)+'" alt="The picture being described">':'')+
      '<textarea id="altinput" rows="3" placeholder="A short description of the picture">'+
      esc(guess)+'</textarea>'+
      '<div class="altact"><button type="button" class="btn" id="altok">Use this</button>'+
      '<button type="button" class="btn ghost" id="altskip">Skip for now</button>'+
      '<span class="hint">Enter accepts · Shift+Enter for a new line · Esc skips</span></div></div>';
    document.body.appendChild(host);
    ed.palette=true;

    var input=document.getElementById('altinput');
    input.focus();
    // Cursor at the end, not a selection: the guess is a starting point to edit, and selecting it
    // all would mean the first key typed silently destroys it.
    input.setSelectionRange(input.value.length, input.value.length);

    function accept(){
      var text=input.value.trim();
      closeAltPrompt();
      if(!text){ focusBox(elementId); return; }
      ed.alts[elementId]=text;
      applyEdits();
      say('Description saved.');
      focusNext(elementId);
    }
    function skip(){ closeAltPrompt(); focusBox(elementId); }

    document.getElementById('altok').addEventListener('click', accept);
    document.getElementById('altskip').addEventListener('click', skip);
    input.addEventListener('keydown',function(ev){
      if(ev.key==='Escape'){ ev.preventDefault(); skip(); return; }
      if(ev.key==='Enter' && !ev.shiftKey){ ev.preventDefault(); accept(); }
    });
    host.addEventListener('click',function(ev){ if(ev.target===host) skip(); });
  }

  function closeAltPrompt(){
    var host=document.getElementById('altprompt');
    if(host && host.parentNode) host.parentNode.removeChild(host);
    ed.palette=false;
  }

  // The next element in the walk, carrying on to the next page at the end of this one -- what Tab
  // would have done, so accepting a description never costs a keystroke to get moving again.
  function focusNext(elementId){
    var items=elementsOnPage();
    var at=-1;
    items.forEach(function(e,i){ if(e.id===elementId) at=i; });
    var boxes=document.querySelectorAll('.ob');
    if(at>=0 && at+1<boxes.length){ boxes[at+1].focus(); return; }
    if(!turnPage(1) && boxes[at]) boxes[at].focus();
  }

  // Setting a type moves on to the next element by itself. Correcting a page is then a single
  // stream of keystrokes with no Tab between them -- and where the type was already right, Tab
  // steps past it. Running off the end of the page carries on to the next one, as Tab does.
  function setKind(elementId, tag){
    var items=elementsOnPage();
    var at=-1;
    items.forEach(function(e,i){ if(e.id===elementId) at=i; });
    // "Not read" is a type like any other as far as the editor is concerned -- it has to show as
    // the element's type, or pressing x looks like it did nothing. It reaches the server as a
    // removal rather than as a tag (there is no /Artifact structure element; content that should
    // not be read is drawn as an artifact instead), which stripArtifacts() below takes care of.
    ed.tags_edit[elementId]=tag;
    if(tag==='Artifact') ed.removed[elementId]=true; else delete ed.removed[elementId];
    ed.focused=null;
    drawStage();
    applyEdits();
    say(tagLabel(tag)+' set.');
    var boxes=document.querySelectorAll('.ob');
    if(at>=0 && at+1<boxes.length){ boxes[at+1].focus(); return; }
    // A figure just made needs its description before anything else, so stay on it rather than
    // walking past the one thing that still needs a person.
    if(tag==='Figure' && boxes[at]){ boxes[at].focus(); return; }
    if(!turnPage(1) && boxes[at]) boxes[at].focus();
  }

  function showError(msg){
    if(elapsedTimer) clearInterval(elapsedTimer);
    if(poll) clearInterval(poll);
    document.body.classList.remove('wide');
    document.getElementById('intake').hidden=true;
    work.hidden=false;
    work.innerHTML='<h2 class="visually-hidden">Problem</h2>'+
      '<div class="panel error">'+
      '<div class="title" style="font-weight:600">That didn\'t work</div>'+
      '<p class="detail">'+esc(msg)+'</p>'+
      '<a class="reset" href="/">Try another document</a></div>';
    say('Error: '+msg);
  }
})();
</script>
</body>
</html>"""
