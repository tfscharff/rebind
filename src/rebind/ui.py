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
   The document is the point, so the middle column is twice either side of it. */
.workspace{display:grid;grid-template-columns:minmax(14rem,1fr) minmax(0,2.2fr) minmax(15rem,1fr);
  gap:1.1rem;align-items:start}
@media (max-width:80rem){ .workspace{grid-template-columns:minmax(0,1.6fr) minmax(14rem,1fr)}
  .col-report{grid-column:1 / -1} }
@media (max-width:56rem){ .workspace{grid-template-columns:minmax(0,1fr)}
  .col-report,.col-todo{grid-column:auto} }
.col-report,.col-todo{position:sticky;top:.8rem;max-height:calc(100vh - 2rem);overflow-y:auto}

/* ---- Left: the checklist, ticked off one at a time ---- */
.report .score{font-family:var(--mono);font-size:.78rem;color:var(--muted);margin:0 0 .7rem}
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

/* ---- Middle: the document ---- */
.typebar{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel);
  padding:.7rem .9rem;min-height:5.2rem;display:flex;flex-direction:column;justify-content:center}
.typebar .what{font-family:var(--serif);font-size:2rem;line-height:1.1;margin:0;
  letter-spacing:-.02em}
.typebar .what.idle{font-size:1.15rem;color:var(--muted)}
.typebar .why{margin:.25rem 0 0;color:var(--muted);font-size:.88rem}
.typebar .hint{margin:.3rem 0 0;font-size:.78rem;color:var(--muted);font-family:var(--mono)}
.typebar .altbox{margin:.45rem 0 0;display:flex;gap:.5rem;align-items:flex-start}
.typebar .altbox textarea{flex:1;font:inherit;font-size:.88rem;padding:.35rem .5rem;
  border:1px solid var(--line);border-radius:5px;background:var(--paper);color:var(--ink);
  resize:vertical}
/* The numbered overlay: boxes are positioned in percentages of the page, so the sheet just has to
   be a positioning context of the same aspect ratio as the page picture inside it. */
.sheet{position:relative;display:block;border:1px solid var(--line);border-radius:6px;
  overflow:hidden;background:#fff;margin:.8rem 0}
.sheet img{display:block;width:100%;height:auto}
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
.pager{display:flex;gap:.5rem;align-items:center;justify-content:center}
.pager .pageno{font-family:var(--mono);font-size:.8rem;color:var(--muted)}

/* ---- Right: what is not ticked, and what would tick it ---- */
.todo .item{border:1px solid var(--line);border-left:4px solid var(--attention);border-radius:8px;
  background:var(--panel);padding:.7rem .8rem;margin-bottom:.6rem}
.todo .item.manual{border-left-color:var(--info)}
.todo .item .title{font-weight:600;font-size:.92rem}
.todo .item .detail{color:var(--muted);font-size:.85rem;margin:.25rem 0 0}
.todo .item .need{font-size:.85rem;margin:.4rem 0 0}
.todo .allclear{color:var(--pass);font-weight:600}
.figrow{display:flex;gap:.6rem;align-items:flex-start;margin-top:.6rem}
.figthumb{width:64px;height:auto;max-height:64px;object-fit:contain;border:1px solid var(--line);
  border-radius:4px;background:#fff;flex:none}
.figrow textarea{flex:1;min-width:0;font:inherit;font-size:.85rem;padding:.35rem .45rem;
  border:1px solid var(--line);border-radius:5px;background:var(--paper);color:var(--ink);
  resize:vertical}
.pagejump{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.45rem}
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
.palette .keys .lab{font-size:.88rem}
.palette .keys .what{font-size:.76rem;color:var(--muted)}
kbd{font-family:var(--mono);font-size:.72rem;background:var(--paper);border:1px solid var(--line);
  border-bottom-width:2px;border-radius:4px;padding:.05rem .32rem;color:var(--ink);flex:none;
  min-width:1.35rem;text-align:center}
.error{border-left:4px solid var(--attention)}
.error .detail{color:var(--ink)}
.visually-hidden{position:absolute;width:1px;height:1px;clip:rect(0 0 0 0);overflow:hidden}
a.reset{display:inline-block;margin-top:1rem;color:var(--cloth);font-size:.9rem}
.struct-badge{font-family:var(--mono);font-size:.74rem;text-transform:uppercase;
  letter-spacing:.03em;margin:.2rem 0 .6rem}
.struct-badge.ok{color:var(--pass)}
.struct-badge.attention{color:var(--attention);text-transform:none;letter-spacing:normal}
</style>
</head>
<body>
<header class="site"><div class="wrap brand">
  <h1>Rebind</h1><span class="tag">accessible PDF reconstruction</span>
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

  // ---- State ---------------------------------------------------------------------------------
  var ed={id:null,name:null,elements:[],pages:{},tags:[],keys:[],page:1,pageList:[],
          tags_edit:{},removed:{},alts:{},focused:null,figures:[],checks:[],status:null,
          palette:false};

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
    drawReport();
    drawTodo();
    loadEditor(id, name);
    say('Done. Your accessible PDF is ready.');
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
    var n=0;
    groups.forEach(function(g){
      h+='<div class="group"><h3>'+esc(g)+'</h3><ul class="checks">';
      byGroup[g].forEach(function(c){
        var cls=c.status==='n/a'?'na':c.status;
        var mark=c.status==='pass'?'✓':(c.status==='n/a'?'–':'!');
        h+='<li class="check '+cls+'" data-step="'+(n++)+'">'+
          '<span class="mark" aria-hidden="true">'+mark+'</span>'+
          '<span class="name">'+esc(c.title)+
          '<span class="visually-hidden">: '+esc(statusWord(c.status))+'</span></span></li>';
      });
      h+='</ul></div>';
    });
    h+='</section>';
    host.innerHTML=h;
    revealChecks(host);
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
  function drawTodo(){
    var host=document.getElementById('todo');
    if(!host) return;
    var open=ed.checks.filter(function(c){
      return c.status==='needs-you'||c.status==='manual'; });
    var dirty=Object.keys(ed.tags_edit).length||Object.keys(ed.removed).length||
              Object.keys(ed.alts).length;
    var h='<section class="panel todo"><h2 id="todo-h">What needs you</h2>'+
      '<div class="actions">'+
      '<a class="btn primary" href="/jobs/'+ed.id+'/pdf" download>Download PDF</a>'+
      '<button type="button" class="btn ghost" id="edapply"'+(dirty?'':' disabled')+
      '>Apply my changes</button></div>'+
      structureBadge(ed.status.structure_ok, ed.status.structure_issues||[]);
    if(!open.length){
      h+='<p class="allclear">Everything Rebind can check passes.</p>';
    }
    open.forEach(function(c){
      h+='<div class="item'+(c.status==='manual'?' manual':'')+'">'+
        '<div class="title">'+esc(c.title)+'</div>'+
        '<p class="detail">'+esc(c.detail)+'</p>'+
        (c.need? '<p class="need">'+esc(c.need)+'</p>':'')+
        actionFor(c)+'</div>';
    });
    h+='<a class="reset" href="/">Do another document</a></section>';
    host.innerHTML=h;
    wireTodo();
  }

  function actionFor(c){
    if(c.action==='describe') return renderFigures();
    if(c.action==='contrast') return renderContrast(ed.status.contrast);
    if(c.action==='reading-order') return renderReadingOrder(ed.status.reading_order);
    return '';
  }

  function structureBadge(ok, issues){
    if(ok) return '<p class="struct-badge ok">PDF/UA-2 tagged</p>';
    return '<p class="struct-badge attention">Structure check: '+
      issues.map(function(i){return esc(i);}).join(', ')+'</p>';
  }

  function renderFigures(){
    if(!ed.figures.length) return '';
    var out='<div id="figlist">';
    ed.figures.forEach(function(f){
      out+='<div class="figrow">'+
        '<img class="figthumb" src="'+esc(f.thumb)+'" alt="Preview of an image on page '+
        esc(f.page)+'">'+
        '<textarea data-fid="'+esc(f.id)+'" rows="2" aria-label="Description for the image on '+
        'page '+esc(f.page)+'" placeholder="What does this picture show?"></textarea></div>';
    });
    return out+'</div><button type="button" class="btn small" id="applyalts" '+
      'style="margin-top:.6rem">Add descriptions</button>';
  }

  // Rebind cannot pass this one -- nothing can -- so it points at the pages where the order was a
  // real decision, and the button puts that page in the middle column to be tabbed through.
  function renderReadingOrder(ro){
    if(!ro || !ro.pages || !ro.pages.length) return '';
    var out='<div class="pagejump">';
    ro.pages.forEach(function(p){
      out+='<button type="button" class="btn ghost small jump" data-page="'+esc(p.page)+'" '+
        'title="'+esc(p.reason)+'">p. '+esc(p.page)+'</button>';
    });
    return out+'</div><span class="caveat">Tab through the page to hear the order Rebind '+
      'chose.</span>';
  }

  function renderContrast(c){
    if(!c || !c.measured || c.ok) return '';
    var out='<ul class="ratios">';
    c.failures.slice(0,6).forEach(function(f){
      out+='<li><span class="swatch" style="background:'+esc(f.paper)+';color:'+esc(f.ink)+
        '">Aa</span><span class="ratio">'+esc(f.ratio)+':1</span>'+
        '<span class="where">p. '+esc(f.page)+'</span>'+
        '<span class="sample">'+esc(f.text)+'</span></li>';
    });
    if(c.failures.length>6){ out+='<li class="sample">…and '+(c.failures.length-6)+' more.</li>'; }
    out+='</ul><button type="button" class="btn small" id="darkenbtn" '+
      'style="margin-top:.5rem">Darken this text to meet AA</button>'+
      '<span class="caveat">The only thing Rebind will change about how the document looks. '+
      'Each colour keeps its hue, and no colour the artwork also uses is touched.</span>';
    return out;
  }

  function wireTodo(){
    var apply=document.getElementById('edapply');
    if(apply) apply.addEventListener('click', applyEdits);
    var alts=document.getElementById('applyalts');
    if(alts) alts.addEventListener('click', applyDescriptions);
    var darken=document.getElementById('darkenbtn');
    if(darken) darken.addEventListener('click', function(){
      darken.disabled=true;
      renderWorking(ed.name, Date.now()); say('Darkening the faint text…');
      fetch('/jobs/'+ed.id+'/contrast',{method:'POST'})
        .then(function(r){return r.json();}).then(function(j){
          if(j.error){ showError(j.error); return; }
          watch(ed.id, ed.name, Date.now());
        }).catch(function(){ showError('Could not adjust the contrast.'); });
    });
    Array.prototype.slice.call(document.querySelectorAll('.jump')).forEach(function(b){
      b.addEventListener('click', function(){
        goToPage(parseInt(b.getAttribute('data-page'),10));
      });
    });
  }

  function applyDescriptions(){
    var alts={};
    Array.prototype.slice.call(document.querySelectorAll('#figlist textarea')).forEach(function(t){
      if(t.value.trim()) alts[t.getAttribute('data-fid')]=t.value.trim();
    });
    if(!Object.keys(alts).length){ say('Type a description first.'); return; }
    renderWorking(ed.name, Date.now()); say('Adding your descriptions…');
    fetch('/jobs/'+ed.id+'/describe',{method:'POST',
      headers:{'content-type':'application/json'},body:JSON.stringify({alts:alts})})
      .then(function(r){return r.json();}).then(function(j){
        if(j.error){ showError(j.error); return; }
        watch(ed.id, ed.name, Date.now());
      }).catch(function(){ showError('Could not apply descriptions.'); });
  }

  function applyEdits(){
    var removed=Object.keys(ed.removed).filter(function(k){ return ed.removed[k]; });
    renderWorking(ed.name, Date.now()); say('Applying your changes…');
    fetch('/jobs/'+ed.id+'/edits',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({tags:ed.tags_edit, removed:removed, alts:ed.alts})})
      .then(function(r){return r.json();}).then(function(j){
        if(j.error){ showError(j.error); return; }
        watch(ed.id, ed.name, Date.now());
      }).catch(function(){ showError('Could not apply your changes.'); });
  }

  // ---- Middle column: the document, every element a tab stop ----------------------------------
  function loadEditor(id, name){
    var host=document.getElementById('stage');
    if(!host) return;
    host.innerHTML='<div class="panel"><p class="sub" style="margin:0">Laying out the page…</p></div>';
    fetch('/jobs/'+id+'/elements').then(function(r){return r.json();}).then(function(d){
      if(d.error){ host.innerHTML=''; return; }
      ed.elements=d.elements||[]; ed.pages=d.pages||{}; ed.tags=d.tags||[]; ed.keys=d.keys||[];
      ed.tags_edit=(d.edits&&d.edits.tags)||{};
      ed.removed={}; ((d.edits&&d.edits.removed)||[]).forEach(function(k){ ed.removed[k]=true; });
      ed.alts=(d.edits&&d.edits.alts)||{};
      ed.pageList=Object.keys(ed.pages).map(Number).sort(function(a,b){return a-b;});
      if(ed.pageList.indexOf(ed.page)<0) ed.page=ed.pageList[0]||1;
      drawStage();
    }).catch(function(){ host.innerHTML=''; });
  }

  function elementsOnPage(){
    return ed.elements.filter(function(e){ return e.page===ed.page; });
  }

  function kindOf(e){ return ed.tags_edit[e.id]||e.kind; }

  function keyFor(tag){
    var found=null;
    ed.keys.forEach(function(k){ if(k.tag===tag) found=k; });
    return found;
  }

  function tagLabel(t){
    var k=keyFor(t);
    return k? k.label : t;
  }

  function drawStage(){
    var host=document.getElementById('stage');
    if(!host) return;
    var items=elementsOnPage();
    var pos=ed.pageList.indexOf(ed.page);
    // The type banner sits above the page, so the element's name is in the same glance as the
    // element itself. It is a live region: a screen reader hears the type on arrival too.
    var h='<div class="typebar" id="typebar" role="status" aria-live="polite">'+idleBanner()+'</div>'+
      '<div class="sheet" id="sheet">'+
      (ed.pages[ed.page]? '<img src="'+ed.pages[ed.page]+'" alt="Page '+ed.page+'">':'')+
      items.map(function(e,i){ return boxHtml(e,i); }).join('')+'</div>'+
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
      '<p class="why">Every block Rebind tagged is a stop. The order you tab through them is the '+
      'order a screen reader reads them in.</p>'+
      '<p class="hint">Enter changes what an element is · [ ] turn the page</p>';
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
    var bar=document.getElementById('typebar');
    if(!bar) return;
    var k=kindOf(e), key=keyFor(k);
    var alt=ed.alts[e.id]!==undefined? ed.alts[e.id] : (e.alt||'');
    var h='<p class="what">'+esc(key? key.label : k)+'</p>'+
      '<p class="why">'+esc(key&&key.what? key.what : 'A structure element.')+'</p>';
    if(k==='Figure'){
      h+='<div class="altbox"><textarea id="altnow" rows="2" data-id="'+esc(e.id)+'" '+
        'aria-label="Description of this figure" placeholder="What does this picture show?">'+
        esc(alt)+'</textarea></div>'+
        '<p class="hint">Space to type a description · Enter to change the type</p>';
    } else {
      h+='<p class="hint">Enter to change what this is · [ ] turn the page</p>';
    }
    bar.innerHTML=h;
    var box=document.getElementById('altnow');
    if(box) box.addEventListener('input',function(){
      ed.alts[box.getAttribute('data-id')]=box.value;
      var apply=document.getElementById('edapply'); if(apply) apply.disabled=false;
    });
    if(box) box.addEventListener('keydown',function(ev){
      if(ev.key==='Escape'){ ev.preventDefault(); focusBox(e.id); }
    });
  }

  function focusBox(elementId){
    var box=document.querySelector('[data-box="'+elementId+'"]');
    if(box) box.focus();
  }

  function goToPage(page){
    if(ed.pageList.indexOf(page)<0) return;
    ed.page=page; ed.focused=null; drawStage();
    var first=document.querySelector('.ob');
    if(first) first.focus();
  }

  function turnPage(step){
    var at=ed.pageList.indexOf(ed.page);
    goToPage(ed.pageList[Math.min(Math.max(at+step,0),ed.pageList.length-1)]);
  }

  function wireStage(){
    var prev=document.getElementById('edprev'), next=document.getElementById('ednext');
    if(prev) prev.addEventListener('click',function(){ turnPage(-1); });
    if(next) next.addEventListener('click',function(){ turnPage(1); });

    var items=elementsOnPage();
    var boxes=Array.prototype.slice.call(document.querySelectorAll('.ob'));
    boxes.forEach(function(box,index){
      var e=items[index];
      box.addEventListener('focus',function(){ ed.focused=e.id; showType(e); });
      box.addEventListener('click',function(){ box.focus(); });
      box.addEventListener('keydown',function(ev){
        if(ev.ctrlKey||ev.metaKey||ev.altKey) return;
        var key=(ev.key||'');
        if(key==='Enter'){ ev.preventDefault(); openPalette(e.id); return; }
        if(key===' '&&kindOf(e)==='Figure'){
          ev.preventDefault();
          var box2=document.getElementById('altnow');
          if(box2) box2.focus();
          return;
        }
        if(key==='['||key===']'){ ev.preventDefault(); turnPage(key===']'?1:-1); return; }
        if(key==='ArrowDown'||key==='ArrowUp'){
          var to=boxes[index+(key==='ArrowDown'?1:-1)];
          if(to){ ev.preventDefault(); to.focus(); }
        }
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
      ed.keys.map(function(k){
        return '<li'+(k.tag===current?' class="current"':'')+'><kbd>'+esc(k.key)+'</kbd>'+
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
      ed.keys.forEach(function(k){ if(k.key===pressed) hit=k.tag; });
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

  // Setting a type redraws the page and puts focus back on the same element, so a run of
  // corrections is one uninterrupted pass: Tab, Enter, key, Tab, Enter, key.
  function setKind(elementId, tag){
    if(tag==='Artifact') delete ed.tags_edit[elementId]; else ed.tags_edit[elementId]=tag;
    // "Not read" on something Rebind did tag means removing it; on furniture it means leave it.
    var known=null;
    ed.elements.forEach(function(e){ if(e.id===elementId) known=e; });
    if(tag==='Artifact' && known && known.kind!=='Artifact') ed.removed[elementId]=true;
    else delete ed.removed[elementId];
    ed.focused=elementId;
    drawStage();
    // Deliberately NOT redrawing the right column: it holds description boxes the user may be
    // half way through typing into, and rebuilding it would throw that text away.
    var apply=document.getElementById('edapply');
    if(apply) apply.disabled=false;
    say(tagLabel(tag)+' set.');
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
