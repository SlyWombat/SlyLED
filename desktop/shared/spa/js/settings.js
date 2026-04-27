/** settings.js — Settings tab: general, stage, DMX config, logging, group control, dark mode. Extracted from app.js Phase 4a. */

function applyDarkMode(dm){
  var b=document.getElementById('app');
  if(dm)b.classList.remove('light');else b.classList.add('light');
}

var _curSetSection='general';
function _setSection(s){
  _curSetSection=s;
  ['general','profiles','dmx','cameras','advanced'].forEach(function(id){
    var el=document.getElementById('ss-'+id);if(el)el.style.display=id===s?'':'none';
    var btn=document.getElementById('sn-'+id);if(btn)btn.className='tnav'+(id===s?' tact':'');
  });
  if(s==='dmx')loadDmxSettings();
  if(s==='profiles')loadDmxProfiles();
  if(s==='cameras')_loadCamCalStatus();
  if(s==='advanced'){_depthRuntimeRefresh();_ollamaRuntimeRefresh();loadCalTuning();}
}
function _stageUnitsChange(){
  var imp=parseInt(document.getElementById('s-un').value)===1;
  document.getElementById('s-stage-metric').style.display=imp?'none':'';
  document.getElementById('s-stage-imperial').style.display=imp?'':'none';
  if(imp){
    // Convert metric mm to imperial ft/in
    var w=parseFloat(document.getElementById('s-sw').value)||3000;
    var h=parseFloat(document.getElementById('s-sh').value)||2000;
    var d=parseFloat(document.getElementById('s-sd').value)||1500;
    _mmToFtIn(w,'s-sw');_mmToFtIn(h,'s-sh');_mmToFtIn(d,'s-sd');
  }else{
    // Convert imperial to metric mm
    var w=_ftInToMm('s-sw');var h=_ftInToMm('s-sh');var d=_ftInToMm('s-sd');
    document.getElementById('s-sw').value=Math.round(w);
    document.getElementById('s-sh').value=Math.round(h);
    document.getElementById('s-sd').value=Math.round(d);
  }
}
function _mmToFtIn(mm,prefix){
  var totalIn=mm/25.4;
  var ft=Math.floor(totalIn/12);
  var inn=Math.round(totalIn%12);
  document.getElementById(prefix+'-ft').value=ft;
  document.getElementById(prefix+'-in').value=inn;
}
function _ftInToMm(prefix){
  var ft=parseInt(document.getElementById(prefix+'-ft').value)||0;
  var inn=parseInt(document.getElementById(prefix+'-in').value)||0;
  return (ft*12+inn)*25.4;
}
function _getStageMm(){
  var imp=parseInt(document.getElementById('s-un').value)===1;
  if(imp){
    return{w:_ftInToMm('s-sw'),h:_ftInToMm('s-sh'),d:_ftInToMm('s-sd')};
  }
  return{w:parseFloat(document.getElementById('s-sw').value)||3000,
         h:parseFloat(document.getElementById('s-sh').value)||2000,
         d:parseFloat(document.getElementById('s-sd').value)||1500};
}
function loadSettings(){
  ra('GET','/api/settings',null,function(d){
    if(!d)return;
    document.getElementById('s-nm').value=d.name||'';
    document.getElementById('s-un').value=d.units||0;
    var cb=document.getElementById('s-dm');if(cb)cb.checked=(d.darkMode!==0);
    var asEl=document.getElementById('s-auto-show');if(asEl)asEl.checked=!!d.autoStartShow;
    applyDarkMode(d.darkMode!==0);
    var lpi=document.getElementById('s-log-path-input');
    if(lpi&&d.logPath)lpi.value=d.logPath;
    _refreshLogStatus();
  });
  // Load stage dimensions (meters → mm for display)
  ra('GET','/api/stage',null,function(st){
    if(!st)return;
    var wMm=Math.round((st.w||3)*1000);
    var hMm=Math.round((st.h||2)*1000);
    var dMm=Math.round((st.d||1.5)*1000);
    document.getElementById('s-sw').value=wMm;
    document.getElementById('s-sh').value=hMm;
    document.getElementById('s-sd').value=dMm;
    // Also populate imperial fields (rounded — display only)
    _mmToFtIn(wMm,'s-sw');_mmToFtIn(hMm,'s-sh');_mmToFtIn(dMm,'s-sd');
    // Show correct panel without converting (avoids imperial round-trip clobbering mm values)
    var imp=parseInt(document.getElementById('s-un').value)===1;
    document.getElementById('s-stage-metric').style.display=imp?'none':'';
    document.getElementById('s-stage-imperial').style.display=imp?'':'none';
    // #628 — manual override + auto-derived hint
    var manCb=document.getElementById('s-stage-manual');
    if(manCb)manCb.checked=!!st.stageBoundsManual;
    var hint=document.getElementById('s-stage-auto-hint');
    if(hint&&st.auto){
      var aw=(st.auto.w||0).toFixed(2);
      var ah=(st.auto.h||0).toFixed(2);
      var ad=(st.auto.d||0).toFixed(2);
      if(st.stageBoundsManual){
        hint.innerHTML='Auto-derived from fixtures + markers would be <b>'+aw+' × '+ah+' × '+ad+' m</b> (W × H × D). Uncheck to apply.';
      }else{
        hint.innerHTML='Auto-derived from fixtures + markers: <b>'+aw+' × '+ah+' × '+ad+' m</b> — re-runs on every layout or marker change.';
      }
    }
  });
  loadPatchView();
  _depthRuntimeRefresh();
  _ollamaRuntimeRefresh();
  _aiEnginesRefresh();
  // #615 — populate the Settings → Advanced "Version" card. Endpoint
  // already exposes the orchestrator VERSION string; the HTML
  // placeholder was never wired up to it.
  ra('GET','/status',null,function(st){
    var el=document.getElementById('s-version');
    if(!el)return;
    if(st&&st.version){
      el.textContent='SlyLED '+String(st.version)+' — running on '+String(st.hostname||'unknown host');
    }else{
      el.textContent='Version unavailable';
    }
  });
}

// ── Aggregate AI Engines card ────────────────────────────────────────
// Reads /api/ai/status — one row per engine descriptor with installed /
// installing / running / warm flags. Engines mid-download show their
// install progress; engines that are installed expose a Test button that
// runs the engine's fixed harness and prints latency next to the row.
var _aiEnginesPollTimer=null;
function _aiEnginesRefresh(){
  var box=document.getElementById('ai-engines-list');
  if(!box)return;
  if(_aiEnginesPollTimer){clearTimeout(_aiEnginesPollTimer);_aiEnginesPollTimer=null;}
  ra('GET','/api/ai/status',null,function(r){
    if(!r||!r.ok){box.innerHTML='<span style="color:#ef4444">Status unavailable</span>';return;}
    var rows=(r.engines||[]).map(_aiEngineRow);
    box.innerHTML=rows.join('')||'<span style="color:#64748b">No AI engines bundled in this build.</span>';
    // Re-poll while any engine is mid-install or pre-warm so the row
    // reflects "installing" → "installed" → "warm" without manual refresh.
    var anyInstalling=(r.engines||[]).some(function(e){return e.installing;});
    var anyColdInstalled=(r.engines||[]).some(function(e){return e.installed && !e.warm;});
    if(anyInstalling||anyColdInstalled){
      _aiEnginesPollTimer=setTimeout(_aiEnginesRefresh,3000);
    }
  });
}

function _aiEngineRow(e){
  var badge,colour;
  if(e.installing){badge='Installing';colour='#f59e0b';}
  else if(!e.installed){badge='Not installed';colour='#64748b';}
  else if(e.warm){badge='Ready · warm';colour='#4ade80';}
  else if(e.running){badge='Running · warming up';colour='#93c5fd';}
  else{badge='Installed · cold';colour='#fbbf24';}
  var prog=e.progress||{};
  var progLine='';
  if(e.installing){
    var pct=prog.percent;
    if(pct==null&&typeof prog.progress==='number')pct=Math.round(prog.progress*100);
    progLine='<div style="font-size:.78em;color:#94a3b8;margin-top:.2em">'
      +escapeHtml(prog.message||prog.phase||'Working...')
      +(pct!=null?(' · '+pct+'%'):'')
      +'</div>';
  }
  var meta=[];
  if(e.model)meta.push('Model: '+escapeHtml(String(e.model)));
  if(e.sizeMb)meta.push(e.sizeMb+' MB');
  if(e.warmedAt){
    var ageS=Math.round(Date.now()/1000-e.warmedAt);
    meta.push('Warmed '+(ageS<60?ageS+'s':Math.round(ageS/60)+'m')+' ago');
  }
  if(e.err)meta.push('<span style="color:#f87171">'+escapeHtml(String(e.err))+'</span>');
  var canTest=!!e.installed && !e.installing;
  var testOutId='ai-test-out-'+e.id;
  return '<div style="display:flex;flex-direction:column;gap:.15em;padding:.45em 0;border-bottom:1px solid #1e293b">'
    +'<div style="display:flex;align-items:center;gap:.5em">'
      +'<span style="flex:1;font-weight:600;color:#e2e8f0">'+escapeHtml(e.name||e.id)+'</span>'
      +'<span style="font-size:.75em;padding:.1em .5em;border-radius:4px;background:'+colour+'22;color:'+colour+'">'+badge+'</span>'
      +(canTest?(' <button class="btn btn-nav" style="font-size:.75em;padding:.15em .5em" onclick="_aiEngineTest(\''+e.id+'\',this)">Test</button>'):'')
    +'</div>'
    +(meta.length?('<div style="font-size:.74em;color:#64748b">'+meta.join(' · ')+'</div>'):'')
    +progLine
    +'<div id="'+testOutId+'" style="font-size:.78em;color:#94a3b8;display:none;margin-top:.2em"></div>'
  +'</div>';
}

function _aiEngineTest(engineId,btn){
  var out=document.getElementById('ai-test-out-'+engineId);
  if(out){out.style.display='';out.innerHTML='Running test harness… 0 s';}
  if(btn){btn.disabled=true;}
  // #685 follow-up — tick the elapsed seconds so the operator sees the
  // call is progressing (cold-start qwen2.5vl:3b on CPU can take 30-90 s
  // for the first inference). Without this the row badge says "Running
  // · warming up" and the inline output sits silent — looks stuck.
  var t0 = Date.now();
  var tick = setInterval(function(){
    if(!out)return;
    var s = Math.round((Date.now()-t0)/1000);
    out.innerHTML = 'Running test harness… ' + s + ' s'
      + (s > 30 ? ' <span style="color:#94a3b8">(first call cold-loads the model — can take up to 2 min)</span>' : '');
  }, 1000);
  ra('POST','/api/ai/'+encodeURIComponent(engineId)+'/test',{},function(r){
    clearInterval(tick);
    if(btn){btn.disabled=false;}
    if(!out)return;
    var ms = Date.now() - t0;
    if(!r){out.innerHTML='<span style="color:#f87171">No response after '+Math.round(ms/1000)+' s</span>';return;}
    if(r.ok===false){
      out.innerHTML='<span style="color:#f87171">Test failed: '+escapeHtml(String(r.err||'unknown'))+'</span>';
    }else{
      var bits=[];
      if(typeof r.totalMs==='number')bits.push(r.totalMs+' ms total');
      if(typeof r.inferenceMs==='number')bits.push(r.inferenceMs+' ms inference');
      if(typeof r.ms==='number')bits.push(r.ms+' ms');
      if(r.response)bits.push('reply: '+escapeHtml(String(r.response).slice(0,80)));
      if(r.depthMeanMm!=null)bits.push('depth μ='+r.depthMeanMm+' mm');
      out.innerHTML='<span style="color:#4ade80">OK</span> · '+bits.join(' · ');
    }
    _aiEnginesRefresh();
  });
}

function _aiEnginesWarmup(){
  ra('POST','/api/ai/warmup',{},function(){_aiEnginesRefresh();});
}

// #623 — Ollama runtime status row (AI auto-tune). Same polling pattern as
// depth-runtime (#598): check /install-status first; when an install is
// running, render the progress bar and re-poll every 2s. When idle,
// query /status and render installed-vs-not state.
var _ollamaRtPollTimer=null;
function _ollamaRuntimeRefresh(){
  var box=document.getElementById('ollama-rt-status');
  if(!box)return;
  if(_ollamaRtPollTimer){clearTimeout(_ollamaRtPollTimer);_ollamaRtPollTimer=null;}
  ra('GET','/api/ollama-runtime/install-status',null,function(ins){
    var phase=ins&&ins.phase;
    var running=phase&&phase!=='done'&&phase!=='error';
    if(running){
      _ollamaRuntimeRenderRunning(ins);
      _ollamaRtPollTimer=setTimeout(_ollamaRuntimeRefresh,2000);
      return;
    }
    ra('GET','/api/ollama-runtime/status',null,function(r){
      var iBtn=document.getElementById('ollama-rt-install');
      var rBtn=document.getElementById('ollama-rt-reinstall');
      if(!r||!r.ok){
        box.innerHTML='<span style="color:#ef4444">Unavailable: '+escapeHtml((r&&r.err)||'module missing')+'</span>';
        if(iBtn)iBtn.style.display='none';
        if(rBtn)rBtn.style.display='none';
        return;
      }
      if(r.installed){
        var active=r.activeModel||r.model||'';
        box.innerHTML='<span style="color:#34d399">Installed</span> · '
          +'Ollama at <span style="color:#94a3b8;font-family:monospace">'+escapeHtml(r.url||'')+'</span>'
          +' · active model <span style="color:#94a3b8;font-family:monospace">'+escapeHtml(active)+'</span>';
        if(iBtn)iBtn.style.display='none';
        if(rBtn)rBtn.style.display='inline-block';
        // #685 follow-up — populate the model dropdown.
        _ollamaRuntimeRefreshModelList();
      }else if(!r.running){
        box.innerHTML='<span style="color:#f59e0b">Not installed</span> · '
          +'click Install for the Ollama runtime (~250 MB). No vision model is pulled — pick one from USER_MANUAL Appendix D and run `ollama pull <name>` afterwards, then select it in the dropdown below.';
        if(iBtn)iBtn.style.display='inline-block';
        if(rBtn)rBtn.style.display='none';
      }else{
        box.innerHTML='<span style="color:#f59e0b">Ollama running, model not pulled</span> · '
          +'click Install to fetch <span style="color:#94a3b8;font-family:monospace">'+escapeHtml(r.model||'')+'</span>';
        if(iBtn)iBtn.style.display='inline-block';
        if(rBtn)rBtn.style.display='none';
      }
      // Surface the last install error inline when available.
      if(ins&&ins.phase==='error'&&ins.error){
        box.innerHTML+='<div style="color:#ef4444;font-size:.78em;margin-top:.25em">Last install failed: '+escapeHtml(ins.error)+'</div>';
      }
    });
  });
}

function _ollamaRuntimeRenderRunning(ins){
  var box=document.getElementById('ollama-rt-status');
  if(!box)return;
  var pct=Math.round(ins.percent||0);
  var phase=escapeHtml(ins.phase||'working');
  var msg=escapeHtml(ins.message||'');
  box.innerHTML=''
    +'<div style="display:flex;align-items:center;gap:.6em;margin-bottom:.3em">'
    +  '<span style="color:#60a5fa;font-weight:600">'+phase+' — '+pct+'%</span>'
    +'</div>'
    +'<div style="background:#0f172a;border:1px solid #334155;border-radius:4px;height:10px;overflow:hidden">'
    +  '<div style="height:100%;width:'+pct+'%;background:#60a5fa;transition:width .3s"></div>'
    +'</div>'
    +(msg?'<div style="color:#94a3b8;font-size:.78em;margin-top:.25em">'+msg+'</div>':'');
  ['ollama-rt-install','ollama-rt-reinstall'].forEach(function(id){
    var b=document.getElementById(id);if(b)b.style.display='none';
  });
}

function _ollamaRuntimeInstall(force){
  var box=document.getElementById('ollama-rt-status');
  if(box)box.innerHTML='<span style="color:#94a3b8">Starting install…</span>';
  ra('POST','/api/ollama-runtime/install',{force:!!force},function(r){
    if(!r||!r.ok){
      if(box)box.innerHTML='<span style="color:#ef4444">'+escapeHtml((r&&r.message)||'Install refused')+'</span>';
      setTimeout(_ollamaRuntimeRefresh,1500);
      return;
    }
    _ollamaRuntimeRefresh();
  });
}

// #685 follow-up — model dropdown population. Called after the status
// row decides Ollama is installed; keeps the row hidden when it isn't.
function _ollamaRuntimeRefreshModelList(){
  var row=document.getElementById('ollama-rt-model-row');
  var sel=document.getElementById('ollama-rt-model-sel');
  var hint=document.getElementById('ollama-rt-model-hint');
  if(!row||!sel)return;
  ra('GET','/api/ollama-runtime/models',null,function(r){
    if(!r||!r.ok||!Array.isArray(r.models)){
      row.style.display='none';
      return;
    }
    row.style.display='block';
    var active=r.active||'';
    sel.innerHTML='';
    var visionCount=0;
    r.models.forEach(function(m){
      if(m.vision)visionCount++;
      var label=m.name+' '
        +(m.vision?'· vision':'· text-only (auto-tune may produce no delta)')
        +(m.sizeMb?' · '+m.sizeMb+' MB':'');
      var opt=document.createElement('option');
      opt.value=m.name;
      opt.textContent=label;
      if(m.name===active)opt.selected=true;
      sel.appendChild(opt);
    });
    if(hint){
      hint.textContent='— '+r.models.length+' pulled · '+visionCount+' vision-capable';
    }
  });
}

function _ollamaRuntimeModelChange(){
  var sel=document.getElementById('ollama-rt-model-sel');
  if(!sel)return;
  var v=sel.value||'';
  // Persist via /api/settings so auto-tune picks it up next run.
  ra('POST','/api/settings',{aiAutoTuneModel:v},function(r){
    if(!r||r.err){
      alert('Saving active model failed: '+((r&&r.err)||'unknown'));
      return;
    }
    // Re-poll the runtime card so the "active model" line updates.
    _ollamaRuntimeRefresh();
  });
}

// #598 — Depth-runtime status row in Settings. The Install button
// reuses the progress modal defined in setup-ui.js. When an install
// is in flight (e.g. triggered silently by the installer marker),
// the row shows inline live progress and re-polls every 2s.
var _depthRtPollTimer=null;
function _depthRuntimeRefresh(){
  var box=document.getElementById('depth-rt-status');
  if(!box)return;
  if(_depthRtPollTimer){clearTimeout(_depthRtPollTimer);_depthRtPollTimer=null;}
  // First check for an in-flight install. If one's running, we short-
  // circuit the snapshot status and show progress + auto-refresh.
  ra('GET','/api/depth-runtime/install-status',null,function(ins){
    if(ins&&ins.running){
      _depthRuntimeRenderRunning(ins);
      _depthRtPollTimer=setTimeout(_depthRuntimeRefresh,2000);
      return;
    }
    ra('GET','/api/depth-runtime/status',null,function(r){
      var iBtn=document.getElementById('depth-rt-install');
      var rBtn=document.getElementById('depth-rt-reinstall');
      var uBtn=document.getElementById('depth-rt-uninstall');
      if(!r||!r.ok){
        box.innerHTML='<span style="color:#ef4444">Unavailable: '+escapeHtml((r&&r.err)||'module missing')+'</span>';
        if(iBtn)iBtn.style.display='none';
        if(rBtn)rBtn.style.display='none';
        if(uBtn)uBtn.style.display='none';
        return;
      }
      if(r.installed){
        var running=r.runnerRunning?' <span style="color:#34d399">· runner live on port '+(r.runnerPort||'?')+'</span>':'';
        // Size: show runtime venv + weights separately so the user
        // knows Reinstall skips the 1.3 GB weight redownload.
        var sizeBits=escapeHtml(String(r.sizeMb||'?'))+' MB venv';
        if(r.weightsMb)sizeBits+=' + '+escapeHtml(String(r.weightsMb))+' MB weights (cached)';
        box.innerHTML='<span style="color:#34d399">Installed</span> · '
          +sizeBits+' · '
          +'Python '+escapeHtml(r.pythonVersion||'?')+' · '
          +escapeHtml(r.model||'')+running;
        if(iBtn)iBtn.style.display='none';
        var cBtn=document.getElementById('depth-rt-check');
        var tBtn=document.getElementById('depth-rt-test');
        if(cBtn)cBtn.style.display='inline-block';
        if(tBtn)tBtn.style.display='inline-block';
        if(rBtn)rBtn.style.display='inline-block';
        if(uBtn)uBtn.style.display='inline-block';
      }else{
        // If the previous install failed, surface the reason inline.
        if(ins&&ins.ok===false&&ins.error){
          box.innerHTML='<span style="color:#ef4444">Install failed:</span> '+escapeHtml(ins.error);
        }else{
          box.innerHTML='<span style="color:#f59e0b">Not installed</span> · click Install for ~2 GB one-time download';
        }
        if(iBtn)iBtn.style.display='inline-block';
        ['depth-rt-check','depth-rt-test'].forEach(function(id){
          var b=document.getElementById(id);if(b)b.style.display='none';
        });
        if(rBtn)rBtn.style.display='none';
        if(uBtn)uBtn.style.display='none';
      }
    });
  });
}

function _depthRuntimeRenderRunning(ins){
  var box=document.getElementById('depth-rt-status');
  if(!box)return;
  var pct=Math.round(100*(ins.progress||0));
  var phase=escapeHtml(ins.phase||'working');
  var msg=escapeHtml(ins.message||'');
  box.innerHTML=''
    +'<div style="display:flex;align-items:center;gap:.6em;margin-bottom:.3em">'
    +  '<span style="color:#60a5fa;font-weight:600">Installing '+pct+'%</span>'
    +  '<span style="color:#94a3b8;font-size:.82em">'+phase+'</span>'
    +  '<button class="btn btn-nav" style="margin-left:auto;padding:.1em .6em;font-size:.82em" onclick="_depthRuntimeOpenProgress()">Details</button>'
    +  '<button class="btn" style="padding:.1em .6em;font-size:.82em;background:#532;color:#fca5a5" onclick="_depthRuntimeCancel()">Cancel</button>'
    +'</div>'
    +'<div style="background:#0f172a;border:1px solid #334155;border-radius:4px;height:10px;overflow:hidden">'
    +  '<div style="height:100%;width:'+pct+'%;background:#60a5fa;transition:width .3s"></div>'
    +'</div>'
    +(msg?'<div style="color:#94a3b8;font-size:.78em;margin-top:.25em">'+msg+'</div>':'');
  // Hide the action buttons while running
  ['depth-rt-install','depth-rt-check','depth-rt-test','depth-rt-reinstall','depth-rt-uninstall'].forEach(function(id){
    var b=document.getElementById(id);if(b)b.style.display='none';
  });
}

function _depthRuntimeCheck(){
  var btn=document.getElementById('depth-rt-check');
  var out=document.getElementById('depth-rt-test-out');
  if(btn){btn.disabled=true;btn.textContent='Checking...';}
  if(out){out.style.display='block';out.innerHTML='<span style="color:#94a3b8">Running pip check + import probe...</span>';}
  ra('POST','/api/depth-runtime/verify',{},function(r){
    if(btn){btn.disabled=false;btn.textContent='Check Install';}
    if(!out)return;
    if(!r){out.innerHTML='<span style="color:#ef4444">✗ Check failed (no response)</span>';return;}
    if(r.ok){
      var v=r.versions||{};
      out.innerHTML='<span style="color:#34d399">✓ Install looks healthy</span> · torch '
        +escapeHtml(v.torch||'?')+' · transformers '+escapeHtml(v.transformers||'?')
        +(r.pipCheckOutput?' · <span style="color:#94a3b8">'+escapeHtml(r.pipCheckOutput.split('\n')[0])+'</span>':'');
    }else{
      var lines=[];
      if(!r.pipCheckOk)lines.push('<div><b>pip check:</b> '+escapeHtml((r.pipCheckOutput||'').split('\n')[0]||'failed')+'</div>');
      if(!r.importOk)lines.push('<div><b>import probe:</b> '+escapeHtml(r.importError||'unknown')+'</div>');
      out.innerHTML='<span style="color:#ef4444">✗ Check failed</span>'+lines.join('')
        +'<div style="margin-top:.3em"><button class="btn btn-nav" style="padding:.1em .6em;font-size:.85em" onclick="_depthRuntimeInstall(true)">Reinstall</button>'
        +' <span style="color:#94a3b8;font-size:.78em">Reinstall wipes the venv and re-pulls pinned versions. Weights are preserved.</span></div>';
    }
  });
}

function _depthRuntimeTest(){
  var btn=document.getElementById('depth-rt-test');
  var out=document.getElementById('depth-rt-test-out');
  if(btn){btn.disabled=true;btn.textContent='Testing...';}
  if(out){out.style.display='block';out.innerHTML='<span style="color:#60a5fa">Spawning runner and running a test inference (first call may take 30-60 s to load model weights)...</span>';}
  var x=new XMLHttpRequest();
  x.open('POST','/api/depth-runtime/test',true);
  x.setRequestHeader('Content-Type','application/json');
  x.timeout=180000; // 3 min: cold start + inference on CPU can genuinely take a couple minutes
  x.onload=function(){
    if(btn){btn.disabled=false;btn.textContent='Test & Warm Up';}
    var r={};try{r=JSON.parse(x.responseText||'{}');}catch(e){}
    if(!out)return;
    if(r.ok){
      out.innerHTML='<span style="color:#34d399">✓ Working</span> · inference '
        +(r.inferenceMs||'?')+' ms · depth '+(r.depthMinMm||'?')+'..'+(r.depthMaxMm||'?')+' mm (mean '
        +(r.depthMeanMm||'?')+') · runner port '+(r.runnerPort||'?');
      _depthRuntimeRefresh();
    }else{
      var err=r.err||('HTTP '+x.status);
      var hint='';
      // Dependency-level failure (wrong transformers version, missing
      // torch, etc.) is only recoverable by wiping and reinstalling.
      if(/cannot import name|model load failed|module .*not found|No module named/i.test(err)){
        hint=' <button class="btn btn-nav" style="margin-left:.4em;padding:.1em .6em;font-size:.85em" onclick="_depthRuntimeInstall(true)">Reinstall</button>'
          +'<div style="color:#94a3b8;font-size:.78em;margin-top:.25em">This looks like a dependency mismatch in the existing runtime. Reinstall will wipe '
          +'%LOCALAPPDATA%\\SlyLED\\runtimes\\depth and pull fresh pinned versions.</div>';
      }
      out.innerHTML='<span style="color:#ef4444">✗ Test failed:</span> '+escapeHtml(err)+hint;
    }
  };
  x.ontimeout=function(){
    if(btn){btn.disabled=false;btn.textContent='Test & Warm Up';}
    if(out)out.innerHTML='<span style="color:#ef4444">✗ Timed out after 3 min — check orchestrator log</span>';
  };
  x.onerror=function(){
    if(btn){btn.disabled=false;btn.textContent='Test & Warm Up';}
    if(out)out.innerHTML='<span style="color:#ef4444">✗ Network error</span>';
  };
  x.send('{}');
}

function _depthRuntimeUninstall(){
  // Two-step: remove venv (keeps weights) or full (wipes weights too).
  // Default is keep-weights so re-install is fast. Users have to opt
  // in to the 1.3 GB wipe via the confirm dialog.
  if(!confirm('Remove the depth runtime? The venv and runner will be deleted. '
    +'Cached model weights (~1.3 GB) are preserved for faster reinstall. '
    +'Click OK to proceed.'))return;
  var includeWeights=confirm('Also wipe the ~1.3 GB cached weights? '
    +'Click OK to free the disk space (next install re-downloads). '
    +'Click Cancel to keep the weights cached.');
  var box=document.getElementById('depth-rt-status');
  if(box)box.innerHTML='<span style="color:#94a3b8">Removing'+(includeWeights?' (including weights)':'')+'...</span>';
  var x=new XMLHttpRequest();
  var url='/api/depth-runtime'+(includeWeights?'?includeWeights=1':'');
  x.open('DELETE',url,true);
  x.setRequestHeader('Content-Type','application/json');
  x.onload=function(){
    try{var r=JSON.parse(x.responseText);}catch(e){r=null;}
    _depthRuntimeRefresh();
  };
  x.onerror=function(){_depthRuntimeRefresh();};
  x.send('{}');
}
function _refreshLogStatus(){
  ra('GET','/api/logging/status',null,function(st){
    if(!st)return;
    var statusEl=document.getElementById('s-log-status');
    var pathEl=document.getElementById('s-log-path');
    var startBtn=document.getElementById('btn-log-start');
    var stopBtn=document.getElementById('btn-log-stop');
    if(statusEl){
      statusEl.textContent=st.enabled?'\u25cf Logging Active':'\u25cb Stopped';
      statusEl.style.color=st.enabled?'#86efac':'#666';
    }
    if(pathEl)pathEl.textContent=st.path?('File: '+st.path):'';
    if(startBtn)startBtn.disabled=!!st.enabled;
    if(stopBtn)stopBtn.disabled=!st.enabled;
  });
}
function _startLogging(){
  var path=document.getElementById('s-log-path-input').value.trim();
  // Save the log path to settings so it persists across page loads
  var body=path?{path:path}:{};
  ra('POST','/api/logging/start',body,function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Logging started: '+(r.path||'default');
      // Persist path in settings
      if(path)ra('POST','/api/settings',{logPath:path},function(){});
    }else{
      document.getElementById('hs').textContent='Start failed: '+(r&&r.err||'server error');
    }
    _refreshLogStatus();
  });
}
function _stopLogging(){
  ra('POST','/api/logging/stop',null,function(r){
    if(r&&r.ok)document.getElementById('hs').textContent='Logging stopped';
    _refreshLogStatus();
  });
}

function factoryReset(){
  if(!confirm('Factory reset? This will delete ALL fixtures, runners and layout data and cannot be undone.'))return;
  var x=new XMLHttpRequest();x.open('POST','/api/reset',true);
  x.setRequestHeader('Content-Type','application/json');
  x.setRequestHeader('X-SlyLED-Confirm','true');
  x.onload=function(){try{var d=JSON.parse(x.responseText);if(d&&d.ok){loadSettings();loadDash();}}catch(e){}};
  x.send('{}');
}

// Renamed to match the onclick="shutdownServer()" attribute on the
// Advanced Settings → Shutdown button. Keep shutdownService as an
// alias so any other caller (or stale browser tab) still works.
function shutdownServer(){
  if(!confirm('Stop the SlyLED service? The browser will lose connection.'))return;
  var x=new XMLHttpRequest();x.open('POST','/api/shutdown',true);
  x.setRequestHeader('Content-Type','application/json');
  x.setRequestHeader('X-SlyLED-Confirm','true');
  x.onload=function(){document.getElementById('hs').textContent='Service stopped. You may close this tab.';};
  x.onerror=function(){document.getElementById('hs').textContent='Service stopped.';};
  x.send('{}');
}
var shutdownService = shutdownServer;

function otaTrigger(childId){
  if(!confirm('Update this fixture to the latest firmware? It will reboot during the update.'))return;
  document.getElementById('hs').textContent='Sending OTA update command...';
  api('POST','/api/firmware/ota/'+childId).then(function(r){
    document.getElementById('hs').textContent='OTA update triggered (v'+r.version+') — fixture will reboot when complete';
    setTimeout(loadDash,15000);
  }).catch(function(){
    document.getElementById('hs').textContent='OTA update failed — check server logs';
  });
}

function showQrCode(){
  var el=document.getElementById('qr-container');
  // If container not found/visible, show in modal
  if(!el||el.offsetParent===null){
    var h='<div id="qr-modal-body" style="text-align:center"><p style="color:#888">Loading QR code...</p></div>';
    document.getElementById('modal-title').textContent='Connect Android App';
    document.getElementById('modal-body').innerHTML=h;
    document.getElementById('modal').style.display='flex';
    el=document.getElementById('qr-modal-body');
  }
  el.innerHTML='<p style="color:#888;font-size:.85em">Loading...</p>';
  var img=new Image();
  img.onload=function(){
    el.innerHTML='';
    img.style.borderRadius='8px';
    img.style.maxWidth='240px';
    el.appendChild(img);
    var p=document.createElement('p');
    p.style.cssText='font-size:.75em;color:#888;margin-top:.5em';
    p.textContent='Scan with the SlyLED Android app to connect';
    el.appendChild(p);
  };
  img.onerror=function(){el.innerHTML='<p style="color:#c66;font-size:.85em">QR generation failed. Install: pip install qrcode[pil]</p>';};
  img.src='/api/qr';
}

function saveSettings(btn){
  _btnSaving(btn);
  var dm=document.getElementById('s-dm').checked?1:0;
  applyDarkMode(dm);
  var logPath=document.getElementById('s-log-path-input').value.trim();
  // Save stage dimensions (mm → meters for server)
  var stg=_getStageMm();
  var stageM={w:stg.w/1000, h:stg.h/1000, d:stg.d/1000};
  // #628 — propagate the manual-override toggle. Unchecking it causes the
  // server to recompute bounds from layout+markers (ignoring the w/h/d we
  // just sent).
  var manCb=document.getElementById('s-stage-manual');
  stageM.stageBoundsManual=!!(manCb&&manCb.checked);
  ra('POST','/api/stage',stageM,function(){});
  ra('POST','/api/settings',{
    name:document.getElementById('s-nm').value.trim(),
    units:parseInt(document.getElementById('s-un').value)||0,
    canvasW:stg.w,
    canvasH:stg.h,
    darkMode:dm,
    logPath:logPath||'',
    autoStartShow:document.getElementById('s-auto-show').checked
  },function(r){
    _btnSaved(btn,r&&r.ok);
    // Refresh layout canvas with new dimensions
    if(r&&r.ok){phW=stg.w;phH=stg.h;}
  });
}

function dmxProtoChange(){
  var proto='artnet';
  var radios=document.getElementsByName('dmx-proto');
  for(var i=0;i<radios.length;i++){if(radios[i].checked){proto=radios[i].value;break;}}
  document.getElementById('dmx-sacn-opts').style.display=(proto==='sacn')?'':'none';
  document.getElementById('dmx-artnet-opts').style.display=(proto==='artnet')?'':'none';
}

var _dmxInterfaces=[];
var _dmxDestinations=[];  // [{ip, label}] from children + discovered
var _dmxRoutes=[];        // [{universe, destination, label}]

function loadDmxSettings(){
  // Load network interfaces, destinations, and settings in parallel
  ra('GET','/api/dmx/interfaces',null,function(ifaces){
    _dmxInterfaces=ifaces||[];
    var sel=document.getElementById('dmx-bind');
    if(sel){
      sel.innerHTML='';
      _dmxInterfaces.forEach(function(ifc){
        var o=document.createElement('option');o.value=ifc.ip;
        o.textContent=ifc.ip+(ifc.name&&ifc.name!=='All Interfaces'?' ('+ifc.name+')':'');
        sel.appendChild(o);
      });
    }
    // Build destination list from children + discovered nodes
    ra('GET','/api/children',null,function(children){
      _dmxDestinations=[];
      (children||[]).forEach(function(c){
        if(c.type==='dmx'||c.boardType==='DMX Bridge')
          _dmxDestinations.push({ip:c.ip,label:c.name||c.hostname||c.ip});
      });
      ra('GET','/api/dmx/discovered',null,function(nodes){
        for(var ip in (nodes||{})){
          if(!_dmxDestinations.some(function(d){return d.ip===ip;}))
            _dmxDestinations.push({ip:ip,label:nodes[ip].shortName||ip});
        }
        // Now load settings
        ra('GET','/api/dmx/settings',null,function(d){
          if(!d)return;
          var proto=d.protocol||'artnet';
          var radios=document.getElementsByName('dmx-proto');
          for(var i=0;i<radios.length;i++){radios[i].checked=(radios[i].value===proto);}
          document.getElementById('dmx-fps').value=d.frameRate||40;
          if(sel)sel.value=d.bindIp||'0.0.0.0';
          document.getElementById('dmx-pri').value=d.sacnPriority||100;
          document.getElementById('dmx-pri-val').textContent=d.sacnPriority||100;
          document.getElementById('dmx-src').value=d.sacnSourceName||'SlyLED';
          _dmxRoutes=d.universeRoutes||[];
          var asEl=document.getElementById('dmx-auto-start');
          if(asEl)asEl.checked=d.autoStartEngine!==false;
          var bbEl=document.getElementById('dmx-boot-blink');
          if(bbEl)bbEl.checked=d.bootBlinkFixtures!==false;
          dmxRenderRoutes();
          dmxProtoChange();
          ra('GET','/api/dmx/status',null,function(st){
            if(!st)return;
            var a=st.artnet||{},s=st.sacn||{};
            var running=a.running||s.running;
            var el=document.getElementById('dmx-status');
            if(el)el.textContent=running?'Engine: running ('+(a.running?'Art-Net':'sACN')+')':'Engine: stopped';
          });
        });
      });
    });
  });
}

function dmxRenderRoutes(){
  var el=document.getElementById('dmx-routes');if(!el)return;
  if(!_dmxRoutes.length){
    el.innerHTML='<div style="color:#555;font-size:.82em">No routes. All universes will broadcast.</div>';
    return;
  }
  var h='<table style="width:100%;font-size:.82em;border-collapse:collapse">'
    +'<tr style="color:#64748b"><th style="text-align:left;padding:.2em .3em">Universe</th>'
    +'<th style="text-align:left;padding:.2em .3em">Destination</th>'
    +'<th style="text-align:left;padding:.2em .3em">Label</th><th></th></tr>';
  _dmxRoutes.forEach(function(r,idx){
    h+='<tr style="border-top:1px solid #1e293b">';
    h+='<td style="padding:.2em .3em"><input type="number" min="1" max="32767" value="'+(r.universe||1)+'" style="width:50px" onchange="dmxRouteChg('+idx+',\'universe\',parseInt(this.value))"></td>';
    h+='<td style="padding:.2em .3em"><select style="width:150px" onchange="dmxRouteChg('+idx+',\'destination\',this.value)">';
    h+='<option value="">Broadcast</option>';
    _dmxDestinations.forEach(function(d){
      h+='<option value="'+escapeHtml(d.ip)+'"'+(r.destination===d.ip?' selected':'')+'>'+escapeHtml(d.ip)+' ('+escapeHtml(d.label)+')</option>';
    });
    // If saved destination isn't in the list, add it
    if(r.destination&&!_dmxDestinations.some(function(d){return d.ip===r.destination;}))
      h+='<option value="'+escapeHtml(r.destination)+'" selected>'+escapeHtml(r.destination)+'</option>';
    h+='</select></td>';
    h+='<td style="padding:.2em .3em"><input value="'+escapeHtml(r.label||'')+'" style="width:100px" onchange="dmxRouteChg('+idx+',\'label\',this.value)"></td>';
    h+='<td style="padding:.2em .3em"><span style="cursor:pointer;color:#f66" onclick="dmxDelRoute('+idx+')">&times;</span></td></tr>';
  });
  el.innerHTML=h+'</table>';
}

function dmxRouteChg(idx,field,val){if(_dmxRoutes[idx])_dmxRoutes[idx][field]=val;}

function dmxAddRoute(){
  var nextUni=1;
  if(_dmxRoutes.length)nextUni=Math.max.apply(null,_dmxRoutes.map(function(r){return r.universe||1;}))+1;
  var defaultDest=_dmxDestinations.length?_dmxDestinations[0].ip:'';
  _dmxRoutes.push({universe:nextUni,destination:defaultDest,label:''});
  dmxRenderRoutes();
}

function dmxDelRoute(idx){_dmxRoutes.splice(idx,1);dmxRenderRoutes();}

function saveDmxSettings(){
  var proto='artnet';
  var radios=document.getElementsByName('dmx-proto');
  for(var i=0;i<radios.length;i++){if(radios[i].checked){proto=radios[i].value;break;}}
  var body={
    protocol:proto,
    frameRate:parseInt(document.getElementById('dmx-fps').value)||40,
    bindIp:document.getElementById('dmx-bind').value||'0.0.0.0',
    sacnPriority:parseInt(document.getElementById('dmx-pri').value)||100,
    sacnSourceName:document.getElementById('dmx-src').value.trim()||'SlyLED',
    universeRoutes:_dmxRoutes.filter(function(r){return r.destination;})
  };
  ra('POST','/api/dmx/settings',body,function(r){
    document.getElementById('dmx-status').textContent=r&&r.ok?'Settings saved':'Save failed';
  });
}

function saveDmxBootSettings(){
  var body={
    autoStartEngine:document.getElementById('dmx-auto-start').checked,
    bootBlinkFixtures:document.getElementById('dmx-boot-blink').checked
  };
  ra('POST','/api/dmx/settings',body,function(r){
    var el=document.getElementById('dmx-status');
    if(el)el.textContent=r&&r.ok?'Boot settings saved':'Save failed';
  });
}

function dmxEngineStart(){
  var proto=document.querySelector('input[name=dmx-proto]:checked');
  var p=proto?proto.value:'artnet';
  ra('POST','/api/dmx/start',{protocol:p},function(r){
    document.getElementById('dmx-status').textContent=r&&r.ok?'Engine: running ('+p+')':'Start failed';
  });
}

function dmxEngineStop(){
  ra('POST','/api/dmx/stop',{},function(r){
    document.getElementById('dmx-status').textContent=r&&r.ok?'Engine: stopped':'Stop failed';
  });
}

function dmxBlink(){
  ra('POST','/api/dmx/blink',{},function(r){
    var msg;
    if(r&&r.ok)msg='Blinking '+(r.fixtures||'')+' fixture(s)...';
    else msg='Blink failed — '+(r&&r.err||'engine not running');
    document.getElementById('dmx-status').textContent=msg;
  });
}

// Blackout: zero every universe and force blackout frames out to the wire,
// even if the engine was stopped — covers the case where a bridge is still
// latched on a previous cue after an orchestrator crash. (#601)
function dmxBlackoutAll(){
  var el=document.getElementById('dmx-status');
  if(el)el.textContent='Blacking out...';
  ra('POST','/api/dmx/blackout',{},function(r){
    if(!el)return;
    if(r&&r.ok){
      el.textContent='Blackout sent'+(r.flushed?' ('+r.flushed+' frame(s) flushed)':'');
    } else {
      el.textContent='Blackout failed'+(r&&r.err?' — '+r.err:'');
    }
  });
}

// ── #144: Live DMX Monitor ───────────────────────────────────────────────
var _dmxMonTimer=null;
function showDmxMonitor(){
  _modalStack=[];
  var h='<div style="display:flex;gap:.5em;align-items:center;margin-bottom:.5em">';
  h+='<label style="font-size:.82em">Universe:</label><select id="mon-uni" onchange="_dmxMonRefresh()" style="font-size:.85em">';
  for(var i=1;i<=4;i++)h+='<option value="'+i+'">Universe '+i+'</option>';
  h+='</select>';
  h+='<label style="font-size:.75em;color:#64748b;margin-left:1em"><input type="checkbox" id="mon-auto" checked> Auto-refresh</label>';
  h+='</div>';
  h+='<div id="mon-grid" style="font-family:monospace;font-size:9px;line-height:1.4;max-height:400px;overflow-y:auto"></div>';
  document.getElementById('modal-title').textContent='DMX Monitor';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='flex';
  _dmxMonRefresh();
  if(_dmxMonTimer)clearInterval(_dmxMonTimer);
  _dmxMonTimer=setInterval(function(){
    if(document.getElementById('mon-auto')&&document.getElementById('mon-auto').checked)_dmxMonRefresh();
  },250);
}
function _dmxMonRefresh(){
  var uni=parseInt(document.getElementById('mon-uni').value)||1;
  ra('GET','/api/dmx/monitor/'+uni,null,function(d){
    if(!d||!d.channels)return;
    var el=document.getElementById('mon-grid');if(!el)return;
    var ch=d.channels;
    var h='<table style="border-collapse:collapse;width:100%">';
    // 32 columns x 16 rows = 512
    h+='<tr><td></td>';
    for(var c=0;c<32;c++)h+='<td style="color:#556;text-align:center;padding:0 1px;font-size:7px">'+(c+1)+'</td>';
    h+='</tr>';
    for(var row=0;row<16;row++){
      var addr=row*32+1;
      h+='<tr><td style="color:#556;padding-right:3px;font-size:7px;text-align:right">'+addr+'</td>';
      for(var c=0;c<32;c++){
        var idx=row*32+c;
        var v=ch[idx]||0;
        var bg=v>0?'hsl('+Math.round(210-v*0.8)+',60%,'+Math.round(15+v*0.2)+'%)':'#111';
        var fg=v>128?'#000':'#888';
        h+='<td style="background:'+bg+';color:'+fg+';text-align:center;padding:1px 2px;border:1px solid #1e293b;cursor:pointer;min-width:18px" '
          +'onclick="_dmxMonSet('+uni+','+(idx+1)+',this)" title="Ch '+(idx+1)+'">'+v+'</td>';
      }
      h+='</tr>';
    }
    el.innerHTML=h+'</table>';
  });
}
function _dmxMonSet(uni,addr,cell){
  var v=prompt('Channel '+addr+' value (0-255):',cell.textContent);
  if(v===null)return;
  ra('POST','/api/dmx/monitor/'+uni+'/set',{channels:[{addr:parseInt(addr),value:parseInt(v)||0}]},function(){_dmxMonRefresh();});
}

// ── #145: Fixture Group Control ──────────────────────────────────────────
function showGroupControl(){
  _modalStack=[];
  ra('GET','/api/fixtures',null,function(fixtures){
    var groups=(fixtures||[]).filter(function(f){return f.type==='group';});
    var h='';
    if(!groups.length){
      h='<p style="color:#888;font-size:.85em">No fixture groups defined. Create a group from Setup > Add Fixture > Fixture Group.</p>';
    }else{
      groups.forEach(function(g){
        var members=g.childIds||[];
        h+='<div class="card" style="margin-bottom:.5em">';
        h+='<b>'+escapeHtml(g.name)+'</b> <span style="color:#64748b;font-size:.8em">('+members.length+' members)</span>';
        h+='<div style="display:flex;gap:.8em;align-items:center;margin-top:.4em;flex-wrap:wrap">';
        h+='<label style="font-size:.78em">Dimmer</label><input type="range" min="0" max="255" value="0" style="width:120px" oninput="_grpCtl('+g.id+',{dimmer:parseInt(this.value)})">';
        h+='<label style="font-size:.78em">R</label><input type="range" min="0" max="255" value="0" class="grp-r" style="width:80px" oninput="_grpColor('+g.id+')">';
        h+='<label style="font-size:.78em">G</label><input type="range" min="0" max="255" value="0" class="grp-g" style="width:80px" oninput="_grpColor('+g.id+')">';
        h+='<label style="font-size:.78em">B</label><input type="range" min="0" max="255" value="0" class="grp-b" style="width:80px" oninput="_grpColor('+g.id+')">';
        h+='</div>';
        h+='<div style="display:flex;gap:.3em;margin-top:.4em">';
        h+='<button class="btn" style="font-size:.7em;background:#554;color:#fed" onclick="_grpPreset('+g.id+',255,200,100,255)">Warm</button>';
        h+='<button class="btn" style="font-size:.7em;background:#225;color:#88f" onclick="_grpPreset('+g.id+',0,100,255,255)">Cool</button>';
        h+='<button class="btn" style="font-size:.7em;background:#522;color:#f88" onclick="_grpPreset('+g.id+',255,0,0,255)">Red</button>';
        h+='<button class="btn btn-off" style="font-size:.7em" onclick="_grpPreset('+g.id+',0,0,0,0)">Off</button>';
        h+='</div></div>';
      });
    }
    document.getElementById('modal-title').textContent='Fixture Group Control';
    document.getElementById('modal-body').innerHTML=h;
    document.getElementById('modal').style.display='flex';
  });
}
function _grpCtl(gid,body){ra('POST','/api/fixtures/group/'+gid+'/control',body,function(){});}
function _grpColor(gid){
  var card=event.target.closest('.card');if(!card)return;
  var r=parseInt(card.querySelector('.grp-r').value)||0;
  var g=parseInt(card.querySelector('.grp-g').value)||0;
  var b=parseInt(card.querySelector('.grp-b').value)||0;
  _grpCtl(gid,{r:r,g:g,b:b,dimmer:255});
}
function _grpPreset(gid,r,g,b,dim){_grpCtl(gid,{r:r,g:g,b:b,dimmer:dim});}

// ── #680 Calibration Timeouts ───────────────────────────────────────────
// Operator-tunable mover-calibration knobs. Grouped by phase so the
// common "battleship too short" tune is right at the top.
var _CAL_TUNE_GROUPS=[
  {title:'Phase time budgets (s)',keys:[
    'discoveryBattleshipS','discoveryColourFallbackS','mappingS','fitS','verificationS'
  ]},
  {title:'Warmup',keys:['warmupSeconds']},
  {title:'Battleship grid clamps',keys:[
    'battleshipPanStepsMin','battleshipPanStepsMax',
    'battleshipTiltStepsMin','battleshipTiltStepsMax'
  ]},
  // #708 — DD plausibility-gate bounds (#697) + surface-aware reject
  // toggle (#684) + auto-pose-fit drift threshold (#709). The "Lab vs
  // Stage" preset on the panel writes these in bulk.
  {title:'Confirm-nudge plausibility (#697)',keys:[
    'confirmContinuityCapMult','confirmRatioMin','confirmRatioMax','confirmSymmetryMinPx'
  ]},
  {title:'Depth + pose (#684 / #709)',keys:[
    'surfaceAwareReject','poseDriftThresholdMm'
  ]},
  {title:'Settle timing',keys:[
    'settleS','settleBaseS','settleEscalateS','settleVerifyGapS','settlePixelThresh'
  ]},
  {title:'BFS + convergence',keys:['bfsMaxSamples','convergeMaxIterations']},
  {title:'Mover-control (non-calibration)',keys:['moverClaimTtlS']}
];
var _CAL_TUNE_LABELS={
  discoveryBattleshipS:'Battleship discovery budget',
  discoveryColourFallbackS:'Legacy discovery budget',
  mappingS:'Mapping / BFS budget',
  fitS:'Model-fit budget',
  verificationS:'Verification budget',
  warmupSeconds:'Warmup duration',
  battleshipPanStepsMax:'Max pan probes',
  battleshipTiltStepsMax:'Max tilt probes',
  battleshipPanStepsMin:'Min pan probes',
  battleshipTiltStepsMin:'Min tilt probes',
  settleS:'Legacy settle time (s)',
  settleBaseS:'Adaptive settle — base (s)',
  settleEscalateS:'Adaptive settle — escalation (s list)',
  settleVerifyGapS:'Settle verify gap (s)',
  settlePixelThresh:'Settle drift threshold (px)',
  bfsMaxSamples:'BFS sample cap',
  convergeMaxIterations:'Convergence iteration cap',
  moverClaimTtlS:'Gyro / phone claim TTL (s)',
  // #708 — labels for #697 / #684 / #709 keys.
  confirmContinuityCapMult:'Continuity cap (× beam-width)',
  confirmRatioMin:'Proportionality min (obs/exp)',
  confirmRatioMax:'Proportionality max (obs/exp)',
  confirmSymmetryMinPx:'Symmetry min (px)',
  surfaceAwareReject:'Surface-aware reject',
  poseDriftThresholdMm:'Pose-drift threshold (mm)'
};
function _calTuneInputId(k){return 'ct-'+k;}
function loadCalTuning(){
  ra('GET','/api/settings',null,function(d){
    if(!d)return;
    var spec=d.calibrationTuningSpec||{};
    var cur=d.calibrationTuning||{};
    var host=document.getElementById('cal-tuning-body');
    if(!host)return;
    var html='';
    _CAL_TUNE_GROUPS.forEach(function(g){
      html+='<div style="margin:.6em 0"><div style="font-weight:600;color:#e2e8f0">'+
            escapeHtml(g.title)+'</div>';
      g.keys.forEach(function(k){
        var s=spec[k];if(!s)return;
        var label=_CAL_TUNE_LABELS[k]||k;
        var tip=s.tooltip||'';
        var defVal=Array.isArray(s.default)?s.default.join(', '):String(s.default);
        var cv=cur[k];
        var curStr=cv==null?'':(Array.isArray(cv)?cv.join(', '):String(cv));
        var clampHint='['+s.min+' – '+s.max+(s.type==='int'?', int':'')+']';
        if(s.type==='bool')clampHint='[on / off]';
        html+='<div style="display:flex;gap:.6em;align-items:center;margin:.25em 0">';
        html+='<label style="flex:0 0 240px" title="'+escapeHtml(tip)+'">'+escapeHtml(label)+
              ' <span style="color:#64748b;font-size:.82em">'+escapeHtml(clampHint)+'</span></label>';
        if(s.type==='bool'){
          // #708 — bool keys render as a checkbox so operators can flip
          // surfaceAwareReject without typing 'true'/'false'.
          var checked=(cv==null)?(!!s.default):!!cv;
          html+='<input type="checkbox" id="'+_calTuneInputId(k)+'"'+
                (checked?' checked':'')+' style="margin:0">';
        }else{
          html+='<input id="'+_calTuneInputId(k)+'" value="'+escapeHtml(curStr)+
                '" placeholder="default: '+escapeHtml(defVal)+'" style="flex:1;max-width:180px">';
        }
        html+='<span style="color:#64748b;font-size:.78em">default '+escapeHtml(defVal)+'</span>';
        html+='</div>';
      });
      html+='</div>';
    });
    host.innerHTML=html;
    var st=document.getElementById('cal-tuning-status');if(st)st.textContent='';
  });
}
function _collectCalTuning(){
  var spec={};
  // Rebuild from a cached GET; faster than round-tripping every save.
  // For correctness we re-parse the inputs against their known keys.
  var out={};
  _CAL_TUNE_GROUPS.forEach(function(g){
    g.keys.forEach(function(k){
      var el=document.getElementById(_calTuneInputId(k));
      if(!el)return;
      // #708 — bool keys collected from .checked, not .value.
      if(el.type==='checkbox'){
        out[k]=!!el.checked;
        return;
      }
      var v=el.value.trim();
      if(v==='')return;  // blank = use default
      if(k==='settleEscalateS'){
        out[k]=v.split(',').map(function(x){return parseFloat(x.trim());}).filter(function(x){return !isNaN(x);});
      }else if(k==='confirmSymmetryMinPx' || /Steps|Samples|Iterations|Thresh/i.test(k)){
        out[k]=parseInt(v,10);
      }else{
        out[k]=parseFloat(v);
      }
    });
  });
  return out;
}
function saveCalTuning(btn){
  var payload={calibrationTuning:_collectCalTuning()};
  var st=document.getElementById('cal-tuning-status');
  if(st)st.textContent='Saving…';
  var x=new XMLHttpRequest();
  x.open('POST','/api/settings',true);
  x.setRequestHeader('Content-Type','application/json');
  x.onload=function(){
    try{var d=JSON.parse(x.responseText);}catch(e){d={};}
    if(x.status===200&&d.ok){
      if(st)st.textContent='Saved';
      loadCalTuning();
    }else{
      var msg='Save failed';
      if(d&&d.details&&d.details.length)msg='Save failed: '+d.details.join('; ');
      else if(d&&d.err)msg='Save failed: '+d.err;
      if(st){st.textContent=msg;st.style.color='#fca5a5';}
    }
  };
  x.send(JSON.stringify(payload));
}
function resetCalTuning(btn){
  if(!confirm('Clear all calibration-timeout overrides and use defaults?'))return;
  var x=new XMLHttpRequest();
  x.open('POST','/api/settings',true);
  x.setRequestHeader('Content-Type','application/json');
  x.onload=function(){
    if(x.status===200){loadCalTuning();}
  };
  x.send(JSON.stringify({calibrationTuning:{}}));
}
