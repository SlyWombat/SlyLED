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
  });
  loadPatchView();
  _depthRuntimeRefresh();
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
        box.innerHTML='<span style="color:#34d399">Installed</span> · '
          +escapeHtml(String(r.sizeMb||'?'))+' MB · '
          +'Python '+escapeHtml(r.pythonVersion||'?')+' · '
          +escapeHtml(r.model||'')+running;
        if(iBtn)iBtn.style.display='none';
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
    +'</div>'
    +'<div style="background:#0f172a;border:1px solid #334155;border-radius:4px;height:10px;overflow:hidden">'
    +  '<div style="height:100%;width:'+pct+'%;background:#60a5fa;transition:width .3s"></div>'
    +'</div>'
    +(msg?'<div style="color:#94a3b8;font-size:.78em;margin-top:.25em">'+msg+'</div>':'');
  // Hide the action buttons while running
  ['depth-rt-install','depth-rt-reinstall','depth-rt-uninstall'].forEach(function(id){
    var b=document.getElementById(id);if(b)b.style.display='none';
  });
}

function _depthRuntimeUninstall(){
  if(!confirm('Remove the depth runtime? You can reinstall later. Any running cal jobs will fail.'))return;
  var box=document.getElementById('depth-rt-status');
  if(box)box.innerHTML='<span style="color:#94a3b8">Removing...</span>';
  var x=new XMLHttpRequest();
  x.open('DELETE','/api/depth-runtime',true);
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

function shutdownService(){
  if(!confirm('Stop the SlyLED service? The browser will lose connection.'))return;
  var x=new XMLHttpRequest();x.open('POST','/api/shutdown',true);
  x.setRequestHeader('Content-Type','application/json');
  x.setRequestHeader('X-SlyLED-Confirm','true');
  x.onload=function(){document.getElementById('hs').textContent='Service stopped. You may close this tab.';};
  x.onerror=function(){document.getElementById('hs').textContent='Service stopped.';};
  x.send('{}');
}

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
