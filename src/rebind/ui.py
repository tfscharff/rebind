"""The browser UI: a thin shell over the conversion pipeline.

Rebind runs as a local web app (the OpenRefine pattern), so a librarian hands over a broken scan
and gets back a born-accessible document. The page's signature is the *review queue* -- a
conservator's condition report of what Rebind could not be certain about, because "know what you
don't know" is the product's whole thesis. The HTML/CSS/JS is inlined (no static files, no external
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
.struct-badge{font-family:var(--mono);font-size:.78rem;text-transform:uppercase;letter-spacing:.03em;
  margin:.3rem 0 0}
.struct-badge.ok{color:var(--cloth)}
.struct-badge.attention{color:var(--attention);text-transform:none;letter-spacing:normal}
/* Figure description editor */
.figures h2{font-family:var(--serif);font-size:1.2rem;margin:0 0 .2rem}
.figrow{display:flex;gap:1rem;align-items:flex-start;padding:.9rem 0;border-top:1px solid var(--line)}
.figrow:first-of-type{border-top:none}
.figthumb{width:110px;height:auto;max-height:110px;object-fit:contain;border:1px solid var(--line);border-radius:6px;background:#fff;flex:none}
.figfield{flex:1}
.figfield label{font-weight:600;font-size:.92rem;display:block}
.figpage{font-family:var(--mono);font-size:.75rem;color:var(--muted);font-weight:400;margin-left:.4rem}
.figfield textarea{width:100%;margin-top:.4rem;font:inherit;font-size:.95rem;padding:.5rem .6rem;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);resize:vertical}
.figfield textarea:focus-visible{outline:3px solid var(--stamp);outline-offset:1px}
#applyalts{margin-top:1rem}
/* The two checks Adobe always leaves to a human: reading order and colour contrast */
.check h2{font-family:var(--serif);font-size:1.2rem;margin:0 0 .5rem}
.verdict{margin:0 0 .4rem;font-weight:600}
.verdict.ok{color:var(--cloth)}
.verdict.attention{color:var(--attention)}
.pageorder{margin:1.1rem 0 0}
.pageorder figcaption{font-family:var(--mono);font-size:.78rem;color:var(--muted);margin-bottom:.35rem}
/* The numbered overlay: boxes are positioned in percentages of the page, so the sheet just has to
   be a positioning context of the same aspect ratio as the thumbnail inside it. */
.sheet{position:relative;display:inline-block;max-width:100%;border:1px solid var(--line);
  border-radius:4px;overflow:hidden;background:#fff}
.sheet img{display:block;max-width:100%;height:auto}
.ob{position:absolute;border:1.5px solid var(--stamp);border-radius:2px;
  background:color-mix(in srgb,var(--stamp) 7%,transparent)}
.ob i{position:absolute;left:-1.5px;top:-1.5px;font-style:normal;font-family:var(--mono);
  font-size:.68rem;line-height:1;padding:.15rem .28rem;background:var(--stamp);color:#fff;
  border-radius:2px 0 2px 0}
.ratios{list-style:none;margin:.7rem 0 0;padding:0;font-size:.9rem}
.ratios li{display:flex;gap:.6rem;align-items:baseline;padding:.35rem 0;border-top:1px solid var(--line)}
.ratios li:first-child{border-top:none}
.ratios .swatch{font-family:var(--serif);padding:.1rem .35rem;border-radius:3px;
  border:1px solid var(--line);flex:none}
.ratios .ratio{font-family:var(--mono);font-weight:700;color:var(--attention)}
.ratios .need,.ratios .where{font-family:var(--mono);font-size:.78rem;color:var(--muted)}
.ratios .sample{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ratios .more{color:var(--muted);font-style:italic}
.fixrow{display:flex;gap:.8rem;align-items:center;flex-wrap:wrap;margin-top:.9rem}
.fixrow .caveat{font-size:.82rem;color:var(--muted);flex:1;min-width:16rem}
.error{border-left:4px solid var(--attention)}
.error .detail{color:var(--ink)}
.visually-hidden{position:absolute;width:1px;height:1px;clip:rect(0 0 0 0);overflow:hidden}
a.reset{display:inline-block;margin-top:1.4rem;color:var(--cloth);font-size:.9rem}
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
        if(s.status==='done'){ clearInterval(poll); done(id, name, s); }
        else if(s.status==='error'){ clearInterval(poll); showError(s.error||'Conversion failed.'); }
      }).catch(function(){});
    },1200);
  }

  function done(id, name, s){
    if(elapsedTimer) clearInterval(elapsedTimer);
    var review=s.review, figures=s.figures||[];
    var h='<h2 class="visually-hidden">Result</h2><div class="panel result">'+
      '<h2>Your accessible PDF is ready</h2>'+
      structureBadge(s.structure_ok, s.structure_issues||[])+
      '<div class="downloads">'+
      '<a class="btn primary" href="/jobs/'+id+'/pdf" download>Download PDF</a>'+
      '</div></div>';
    h+=renderFigures(id, name, figures);
    h+=renderQueue(review);
    h+=renderReadingOrder(s.reading_order);
    h+=renderContrast(s.contrast);
    h+='<a class="reset" href="/">Do another document</a>';
    work.innerHTML=h;
    if(figures.length){ wireFigures(id, name); }
    wireContrast(id, name);
    say(figures.length? ('Done. '+figures.length+' image'+(figures.length>1?'s':'')+' can be described for a screen reader.') : 'Done. Your accessible PDF is ready to download.');
    work.setAttribute('tabindex','-1');
    work.focus();
  }

  function structureBadge(ok, issues){
    if(ok) return '<p class="struct-badge ok">PDF/UA-2 tagged</p>';
    var list=issues.map(function(i){return esc(i);}).join(', ');
    return '<p class="struct-badge attention">Structure check found an issue: '+list+'</p>';
  }

  function renderFigures(id, name, figures){
    if(!figures.length) return '';
    var out='<section class="panel figures" aria-labelledby="fig-h"><h2 id="fig-h">Describe the images</h2>'+
      '<p class="sub">A screen reader can\'t see these images. Describe what each one shows, and Rebind will add it to the PDF. Leave one blank to keep it as decoration.</p>'+
      '<div id="figlist">';
    figures.forEach(function(f){
      out+='<div class="figrow">'+
        '<img class="figthumb" src="'+f.thumb+'" alt="Preview of an image on page '+esc(f.page)+'">'+
        '<div class="figfield"><label>Description <span class="figpage">page '+esc(f.page)+'</span>'+
        '<textarea data-fid="'+esc(f.id)+'" rows="2" placeholder="e.g. A bar chart of sales rising each quarter."></textarea></label></div>'+
        '</div>';
    });
    out+='</div><button type="button" class="btn primary" id="applyalts">Add descriptions</button></section>';
    return out;
  }

  function wireFigures(id, name){
    var btn=document.getElementById('applyalts');
    if(!btn) return;
    btn.addEventListener('click', function(){
      var alts={};
      document.querySelectorAll('#figlist textarea').forEach(function(t){
        if(t.value.trim()) alts[t.getAttribute('data-fid')]=t.value.trim();
      });
      if(!Object.keys(alts).length){ say('Type a description first, or download the PDF as is.'); return; }
      btn.disabled=true; btn.textContent='Adding…';
      renderWorking(name, Date.now()); say('Adding your descriptions…');
      fetch('/jobs/'+id+'/describe',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({alts:alts})})
        .then(function(r){return r.json();}).then(function(j){
          if(j.error){ showError(j.error); return; }
          watch(id, name, Date.now());
        }).catch(function(){ showError('Could not apply descriptions.'); });
    });
  }

  // The two checks Adobe's checker always hands back to a human. Rebind can't make them pass --
  // nothing can -- so it shows its work instead: the order it chose, and a real measurement.
  function renderReadingOrder(ro){
    if(!ro || !ro.checked) return '';
    var out='<section class="panel check" aria-labelledby="ro-h"><h2 id="ro-h">Reading order</h2>';
    if(!ro.pages.length){
      out+='<p class="verdict ok">All '+ro.checked+' page'+(ro.checked>1?'s':'')+' read straight down a single column — there is no ordering decision to second-guess.</p>';
      return out+'</section>';
    }
    out+='<p class="verdict ok">'+ro.clear+' of '+ro.checked+' pages read straight down — nothing to check.</p>'+
      '<p class="sub">These '+ro.pages.length+' had a real choice in them. The numbers show the order a screen reader will read each block. If a number is in the wrong place, the order is wrong.</p>';
    ro.pages.forEach(function(p){
      out+='<figure class="pageorder"><figcaption>Page '+esc(p.page)+' — '+esc(p.reason)+'</figcaption>'+
        '<div class="sheet">'+
        (p.thumb? '<img src="'+p.thumb+'" alt="Page '+esc(p.page)+'">' : '')+
        p.blocks.map(function(b){
          return '<span class="ob" style="left:'+b.left+'%;top:'+b.top+'%;width:'+b.width+'%;height:'+b.height+'%" title="'+esc(b.text)+'"><i>'+b.n+'</i></span>';
        }).join('')+
        '</div></figure>';
    });
    return out+'</section>';
  }

  function renderContrast(c){
    if(!c || !c.measured) return '';
    var out='<section class="panel check" aria-labelledby="ct-h"><h2 id="ct-h">Colour contrast</h2>';
    var lowest=c.lowest? (' Lowest measured: '+c.lowest.ratio+':1.') : '';
    if(c.ok){
      var how=c.darkened? ' '+c.darkened+' text colour'+(c.darkened>1?'s were':' was')+' darkened to get there — nothing else about the page changed.'
                        : '';
      return out+'<p class="verdict ok">All '+c.measured+' lines of text meet WCAG AA against what is actually behind them.'+esc(lowest)+esc(how)+'</p></section>';
    }
    out+='<p class="verdict attention">'+c.failures.length+' of '+c.measured+' lines fall below WCAG AA, on page'+(c.pages.length>1?'s':'')+' '+c.pages.join(', ')+'.</p>'+
      '<p class="sub">Measured from the rendered page, so this is the contrast a reader actually sees. This is how the original was published — Rebind changes nothing unless you ask it to.</p>'+
      '<ul class="ratios">';
    c.failures.slice(0,12).forEach(function(f){
      out+='<li><span class="swatch" style="background:'+esc(f.paper)+';color:'+esc(f.ink)+'">Aa</span>'+
        '<span class="ratio">'+f.ratio+':1</span> <span class="need">needs '+f.required+':1</span>'+
        '<span class="where">p. '+esc(f.page)+'</span>'+
        '<span class="sample">'+esc(f.text)+'</span></li>';
    });
    if(c.failures.length>12){ out+='<li class="more">…and '+(c.failures.length-12)+' more.</li>'; }
    out+='</ul>'+
      '<div class="fixrow"><button type="button" class="btn" id="darkenbtn">Darken this text to meet AA</button>'+
      '<span class="caveat">Changes how the document looks. Only the text colours above are altered — never a colour the artwork also uses — and each keeps its hue.</span></div>';
    return out+'</section>';
  }

  function wireContrast(id, name){
    var btn=document.getElementById('darkenbtn');
    if(!btn) return;
    btn.addEventListener('click', function(){
      btn.disabled=true;
      renderWorking(name, Date.now()); say('Darkening the faint text…');
      fetch('/jobs/'+id+'/contrast',{method:'POST'})
        .then(function(r){return r.json();}).then(function(j){
          if(j.error){ showError(j.error); return; }
          watch(id, name, Date.now());
        }).catch(function(){ showError('Could not adjust the contrast.'); });
    });
  }

  function renderQueue(review){
    if(!review) return '';
    var out='<div class="panel queue">';
    if(review.summary){ out+='<p class="sub" style="margin:0">'+esc(review.summary)+'</p>'; }
    if(review.clean){
      out+='</div>';
      return out;
    }
    out+='<h2 id="queue-h" style="margin-top:.9rem">Needs a look</h2>'+
      '<ul class="conditions">';
    review.items.forEach(function(it){
      var pages = it.pages && it.pages.length ? '<p class="pages"><b>pages</b> '+it.pages.join(', ')+'</p>' : '';
      out+='<li class="cond attention">'+
        '<div class="top"><span class="title">'+esc(it.title)+'</span></div>'+
        '<p class="detail">'+esc(it.detail)+'</p>'+pages+'</li>';
    });
    out+='</ul></div>';
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
