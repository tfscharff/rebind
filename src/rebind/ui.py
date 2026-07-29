"""The browser UI: a thin shell over the conversion pipeline.

Rebind runs as a local web app (the OpenRefine pattern), so a librarian hands over a broken scan
and gets back a born-accessible document. The page's signature is the *review queue* -- a
conservator's condition report of what Rebind could not be certain about, because "know what you
don't know" is the product's whole thesis. The HTML/CSS/JS is inlined (no static files, no external
requests) so it works offline and needs nothing extra bundled into the frozen build.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Document

# Each reviewable condition: the flag (or synthetic key) it comes from, a librarian-facing title,
# a plain-language explanation of what to check, and a severity. `attention` means a human should
# look; `info` is a heads-up that does not imply an error.


@dataclass(frozen=True)
class _Condition:
    key: str
    title: str
    detail: str
    severity: str


_FLAG_CONDITIONS: tuple[_Condition, ...] = (
    _Condition("text-unrecoverable", "Text that could not be read",
               "The scan was too degraded to recognize here. Rebind left an honest placeholder "
               "rather than guess. Check these against the original.", "attention"),
    _Condition("table-suspected", "Possible tables",
               "These pages look like they contain a table. Rebind keeps the cell text but does "
               "not rebuild the grid yet, so cells may read in the wrong order.", "attention"),
    _Condition("multi-column-suspected", "Uncertain column order",
               "The column layout on these pages was ambiguous, so the reading order may be "
               "wrong. Worth a quick check.", "attention"),
    _Condition("ocr-source", "Text recognized from a scan",
               "These pages had no digital text, so the words were recognized from the image and "
               "may contain recognition errors.", "info"),
    _Condition("degraded-region", "Low-confidence text",
               "The recognizer was unsure about these regions. The text is included but flagged "
               "for review.", "info"),
    _Condition("heading-level-collapsed", "Deep heading levels merged",
               "The document has more heading levels than a tagged PDF allows, so the deepest "
               "were merged. The full structure is kept in the model.", "info"),
)

# Synthetic conditions not carried on nodes as a body flag.
_SCANNED = _Condition("no-text-layer", "Pages with no recoverable text",
                      "These pages had no text layer and could not be recognized -- they may be "
                      "blank, or images with no readable text.", "attention")
_ALREADY_TAGGED = _Condition("already-tagged", "Source was already tagged",
                             "The original already declares an accessibility structure. Rebind "
                             "rebuilt it anyway; you may not need to.", "info")


def _pages_for(document: Document, flag: str) -> list[int]:
    return sorted({node.page for node in document.nodes if flag in node.flags})


def build_review(document: Document, *, scanned_pages: tuple[int, ...],
                 source_was_tagged: bool) -> dict:
    """A grouped, human-facing summary of what a reviewer should check.

    Returns `{"items": [...], "clean": bool}` where each item is
    `{kind, title, detail, severity, count, pages}` sorted attention-first.
    """
    items: list[dict] = []

    for condition in _FLAG_CONDITIONS:
        pages = _pages_for(document, condition.key)
        if pages:
            items.append({
                "kind": condition.key, "title": condition.title, "detail": condition.detail,
                "severity": condition.severity, "count": len(pages), "pages": pages,
            })

    if scanned_pages:
        pages = sorted(scanned_pages)
        items.append({
            "kind": _SCANNED.key, "title": _SCANNED.title, "detail": _SCANNED.detail,
            "severity": _SCANNED.severity, "count": len(pages), "pages": pages,
        })

    if source_was_tagged:
        items.append({
            "kind": _ALREADY_TAGGED.key, "title": _ALREADY_TAGGED.title,
            "detail": _ALREADY_TAGGED.detail, "severity": _ALREADY_TAGGED.severity,
            "count": 0, "pages": [],
        })

    # Attention before info; then by descending count so the biggest issues lead.
    items.sort(key=lambda item: (0 if item["severity"] == "attention" else 1, -item["count"]))
    return {"items": items, "clean": not items}


def index_html() -> str:
    """The single-page app, fully self-contained (no external requests)."""
    return _PAGE


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rebind — rebuild a document as an accessible PDF</title>
<style>
:root{
  --paper:#fbfaf8; --panel:#ffffff; --ink:#1e2321; --muted:#6b7671;
  --cloth:#2f5d62; --cloth-deep:#234a4e; --stamp:#a6412e; --stamp-deep:#873625;
  --line:#e4e0d8; --info:#2f5d62; --attention:#a6412e;
  --radius:10px; --maxw:56rem;
  --serif:Georgia,"Iowan Old Style","Times New Roman",serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,"SFMono-Regular","Cascadia Code",Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#15191a; --panel:#1c2122; --ink:#eef0ec; --muted:#9aa39e;
    --cloth:#6bb3ba; --cloth-deep:#8fccd2; --stamp:#e08066; --stamp-deep:#eb9a83;
    --line:#2c3335; --info:#8fccd2; --attention:#e08066;
  }
}
*{box-sizing:border-box}
html{color-scheme:light dark}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
  line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 1.25rem}
header.site{border-bottom:1px solid var(--line);padding:1.4rem 0}
.brand{display:flex;align-items:baseline;gap:.7rem}
.brand h1{font-family:var(--serif);font-weight:600;font-size:1.7rem;margin:0;letter-spacing:-.01em}
.brand .tag{color:var(--muted);font-size:.92rem}
main{padding:2.4rem 0 4rem}
.lede{font-family:var(--serif);font-size:1.5rem;line-height:1.35;margin:0 0 1.9rem;max-width:34ch}
.lede em{font-style:italic;color:var(--cloth)}
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
.hint{color:var(--muted);font-size:.86rem;margin-top:1rem}
/* Status */
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:1.4rem 1.5rem;margin-top:1.5rem}
.status{display:flex;align-items:center;gap:.9rem}
.spinner{width:20px;height:20px;border:3px solid var(--line);border-top-color:var(--cloth);
  border-radius:50%;animation:spin 1s linear infinite;flex:none}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spinner{animation:none}}
.status .what{font-weight:600}
.status .elapsed{color:var(--muted);font-size:.86rem;font-family:var(--mono)}
/* Result */
.result h2{font-family:var(--serif);font-size:1.3rem;margin:0 0 .3rem}
.downloads{display:flex;flex-wrap:wrap;gap:.7rem;margin:1rem 0 .3rem}
.btn{display:inline-flex;align-items:center;gap:.5rem;font-weight:600;text-decoration:none;
  padding:.62rem 1.05rem;border-radius:8px;border:1px solid transparent;font-size:.95rem}
.btn.primary{background:var(--cloth);color:#fff}
.btn.primary:hover{background:var(--cloth-deep)}
.btn.ghost{background:transparent;color:var(--cloth);border-color:var(--line)}
.btn:focus-visible{outline:3px solid var(--stamp);outline-offset:2px}
/* Review queue — the condition report */
.queue{margin-top:1.6rem}
.queue h2{font-family:var(--serif);font-size:1.2rem;margin:0 0 .2rem}
.queue .sub{color:var(--muted);font-size:.9rem;margin:0 0 1rem}
ul.conditions{list-style:none;margin:0;padding:0;display:grid;gap:.7rem}
li.cond{border:1px solid var(--line);border-left:4px solid var(--info);border-radius:8px;
  background:var(--panel);padding:.9rem 1.05rem}
li.cond.attention{border-left-color:var(--attention)}
.cond .top{display:flex;align-items:baseline;justify-content:space-between;gap:1rem}
.cond .title{font-weight:600}
.cond .badge{font-family:var(--mono);font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;
  color:var(--muted);white-space:nowrap}
.cond .detail{color:var(--muted);font-size:.92rem;margin:.35rem 0 0}
.cond .pages{font-family:var(--mono);font-size:.8rem;color:var(--ink);margin:.5rem 0 0}
.cond .pages b{color:var(--muted);font-weight:400}
.clean{display:flex;gap:.7rem;align-items:center;color:var(--cloth);font-weight:600}
.error{border-left:4px solid var(--attention)}
.error .detail{color:var(--ink)}
.visually-hidden{position:absolute;width:1px;height:1px;clip:rect(0 0 0 0);overflow:hidden}
a.reset{display:inline-block;margin-top:1.4rem;color:var(--cloth);font-size:.9rem}
footer{border-top:1px solid var(--line);color:var(--muted);font-size:.82rem;padding:1.4rem 0;margin-top:2rem}
</style>
</head>
<body>
<header class="site"><div class="wrap brand">
  <h1>Rebind</h1><span class="tag">accessible PDF reconstruction</span>
</div></header>

<main class="wrap">
  <p class="lede">Hand over a broken scan. Get back a document that was <em>rebuilt</em> to be accessible — and an honest list of anything worth a second look.</p>

  <section id="intake" aria-labelledby="intake-h">
    <h2 id="intake-h" class="visually-hidden">Choose a PDF</h2>
    <div class="drop" id="drop">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 16V4m0 0l-4 4m4-4l4 4"/><path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg>
      <p><strong>Drop a PDF here</strong></p>
      <p>or</p>
      <label class="file-label">Choose a file
        <input type="file" id="file" accept="application/pdf,.pdf">
      </label>
      <p class="hint">Everything runs on this computer. Nothing is uploaded anywhere. Scanned documents are read page by page, which can take a few minutes.</p>
    </div>
  </section>

  <div id="live" role="status" aria-live="polite" class="visually-hidden"></div>
  <section id="work" hidden aria-labelledby="work-h"></section>
</main>

<footer class="wrap">Rebind builds a new document from the scan rather than patching it, so accessibility is true by construction. The model beside your PDF is the source of truth.</footer>

<script>
(function(){
  var drop=document.getElementById('drop');
  var file=document.getElementById('file');
  var work=document.getElementById('work');
  var live=document.getElementById('live');
  var poll=null;

  function say(msg){ live.textContent=msg; }

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

  function esc(s){return String(s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}

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
        if(s.status==='done'){ clearInterval(poll); done(id, name, s.review); }
        else if(s.status==='error'){ clearInterval(poll); showError(s.error||'Conversion failed.'); }
      }).catch(function(){});
    },1200);
  }

  function done(id, name, review){
    if(elapsedTimer) clearInterval(elapsedTimer);
    var h='<h2 class="visually-hidden">Result</h2><div class="panel result">'+
      '<h2>Your accessible PDF is ready</h2>'+
      '<p style="color:var(--muted);margin:.2rem 0 0">Rebuilt from '+esc(name)+'.</p>'+
      '<div class="downloads">'+
      '<a class="btn primary" href="/jobs/'+id+'/pdf" download>Download PDF</a>'+
      '<a class="btn ghost" href="/jobs/'+id+'/model" download>Download model (JSON)</a>'+
      '</div></div>';
    h+=renderQueue(review);
    h+='<a class="reset" href="/">Rebuild another document</a>';
    work.innerHTML=h;
    var items=(review&&review.items)||[];
    say(items.length? ('Done. '+items.length+' thing'+(items.length>1?'s':'')+' to review.') : 'Done. Nothing flagged for review.');
    // Move focus to the result region so a screen-reader/keyboard user lands on the outcome.
    work.setAttribute('tabindex','-1');
    work.focus();
  }

  function renderQueue(review){
    if(!review) return '';
    if(review.clean){
      return '<div class="panel queue"><div class="clean">'+
        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>'+
        'Nothing needed a human — the whole document converted cleanly.</div></div>';
    }
    var out='<section class="panel queue" aria-labelledby="queue-h">'+
      '<h2 id="queue-h">Worth a look</h2>'+
      '<p class="sub">Rebind surfaces only what it could not be certain about. The text is all there; these are the spots to check against the original.</p>'+
      '<ul class="conditions">';
    review.items.forEach(function(it){
      var pages = it.pages && it.pages.length ? '<p class="pages"><b>pages</b> '+it.pages.join(', ')+'</p>' : '';
      out+='<li class="cond '+esc(it.severity)+'">'+
        '<div class="top"><span class="title">'+esc(it.title)+'</span>'+
        '<span class="badge">'+(it.count?esc(it.count)+' page'+(it.count>1?'s':''):esc(it.severity))+'</span></div>'+
        '<p class="detail">'+esc(it.detail)+'</p>'+pages+'</li>';
    });
    out+='</ul></section>';
    return out;
  }

  function showError(msg){
    if(elapsedTimer) clearInterval(elapsedTimer);
    if(poll) clearInterval(poll);
    document.getElementById('intake').hidden=true;
    work.hidden=false;
    work.innerHTML='<h2 class="visually-hidden">Problem</h2>'+
      '<div class="panel error"><div class="cond attention" style="border:none;padding:0">'+
      '<div class="top"><span class="title">That didn\'t work</span></div>'+
      '<p class="detail">'+esc(msg)+'</p></div>'+
      '<a class="reset" href="/">Try another document</a></div>';
    say('Error: '+msg);
  }
})();
</script>
</body>
</html>"""
