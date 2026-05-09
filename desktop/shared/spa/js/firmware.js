/** firmware.js — Firmware tab: OTA updates, USB flash, WiFi, port scanning, version compare. Extracted from app.js Phase 4b. */

// ── Semver compare: returns -1, 0, or 1 ─────────────────────────────────
function _cmpVer(a,b){
  // Compare "5.3.10" vs "5.3.9" correctly (not string compare)
  if(!a)return b?-1:0; if(!b)return 1;
  var ap=a.split('.').map(Number),bp=b.split('.').map(Number);
  for(var i=0;i<Math.max(ap.length,bp.length);i++){
    var ai=ap[i]||0,bi=bp[i]||0;
    if(ai<bi)return -1; if(ai>bi)return 1;
  }
  return 0;
}
// ── OTA update controls on Firmware tab ──────────────────────────────────
var _otaChecked=null; // cached check result
function checkOtaUpdates(){
  var el=document.getElementById('ota-children');
  // Check WiFi is configured first
  ra('GET','/api/wifi',null,function(w){
    if(!w||!w.ssid||!w.hasPassword){
      el.innerHTML='<p style="color:#f66">WiFi credentials must be configured and saved before checking for updates.</p>';
      return;
    }
    el.innerHTML='<p style="color:#888">Checking cloud for latest firmware...</p>';
    // Force-download latest firmware from GitHub before checking
    Promise.all([
      api('POST','/api/firmware/download',{board:'esp32'}).catch(function(){return null;}),
      api('POST','/api/firmware/download',{board:'d1mini'}).catch(function(){return null;})
    ]).then(function(){
      // Refresh USB section — registry/library was updated by download
      _fetchFwLibrary();
      loadFirmwarePorts();
      el.innerHTML='<p style="color:#888">Refreshing fixture status...</p>';
      return api('POST','/api/children/refresh-all').then(function(){return pollResults('/api/children/refresh-all/results');}).catch(function(){return null;});
    }).then(function(){
      return api('GET','/api/firmware/check');
    }).then(function(d){
      _otaChecked=d;
      _renderOtaTable(d);
    }).catch(function(e){
      el.innerHTML='<p style="color:#f66">'+(e&&e.message?escapeHtml(e.message):'Failed to check — is the server online?')+'</p>';
    });
  });
}
function _renderOtaTable(d){
  var el=document.getElementById('ota-children');
  if(!d||!d.children||!d.children.length){
    el.innerHTML='<p style="color:#888">No fixtures registered.</p>';
    document.getElementById('ota-update-all').style.display='none';
    return;
  }
  var outdated=d.children.filter(function(c){return c.needsUpdate&&c.board!=='wled';});
  document.getElementById('ota-update-all').style.display=outdated.length>0?'inline-block':'none';
  // #866 — per-board latest header. Boards have independent version
  // tracks (gyro v1.x, ESP32 v7.x, DMX bridge v7.5.20, etc.) so a
  // single fleet-max number is meaningless. Aggregate distinct latest
  // versions per board label and render them inline. `c.latestVersion`
  // is already populated per-row by /api/firmware/check.
  var perBoard={};
  d.children.forEach(function(c){
    if(c.board==='wled'||!c.latestVersion)return;
    var lbl=c.board==='esp32'?'ESP32':c.board==='d1mini'?'D1 Mini':c.board;
    perBoard[lbl]=c.latestVersion;
  });
  var hdr=Object.keys(perBoard).sort().map(function(k){
    return escapeHtml(k)+': v'+escapeHtml(perBoard[k]);
  }).join(' &middot; ');
  var h=hdr
    ?'<p style="font-size:.82em;color:#aaa;margin-bottom:.5em">Latest &mdash; '+hdr+'</p>'
    :'';
  h+='<table class="tbl"><tr><th>Fixture</th><th>Board</th><th>IP</th><th>Current</th><th>Status</th><th>Action</th></tr>';
  d.children.forEach(function(c){
    var boardColors={'ESP32':'#2563eb','D1 Mini':'#7c3aed','Giga':'#059669','WLED':'#f59e0b','d1mini':'#7c3aed','esp32':'#2563eb'};
    var boardLabel=c.board==='esp32'?'ESP32':c.board==='d1mini'?'D1 Mini':c.board==='wled'?'WLED':c.board;
    var bc=boardColors[c.board]||boardColors[boardLabel]||'#446';
    var tp='<span class="badge" style="background:'+bc+';color:#fff">'+escapeHtml(boardLabel)+'</span>';
    var st,act;
    var isOnline=c.status===1;
    if(c.board==='wled'){
      st='<span style="color:#888">WLED — update via device UI</span>';
      act='';
    }else if(!isOnline){
      st='<span class="badge boff">Offline</span>';
      act='<span style="color:#666">—</span>';
    }else if(c.needsUpdate){
      // #866 — per-row latest comes from c.latestVersion (board-track
      // aware) not d.latest (fleet-max across all tracks).
      st='<span class="badge" style="background:#f60;color:#fff">v'+escapeHtml(c.latestVersion||'?')+' available</span>';
      act='<button class="btn btn-on" id="ota-btn-'+c.id+'" onclick="otaSingleUpdate('+c.id+')">Update</button>';
    }else{
      st='<span class="badge bon">Up to date</span>';
      act='<span style="color:#4c4">&#x2713;</span>';
    }
    var displayName=(c.name&&c.name!==c.hostname)?escapeHtml(c.name):escapeHtml(c.hostname||'');
    var subtitle=(c.name&&c.name!==c.hostname)?'<span style="font-size:.75em;color:#888">'+escapeHtml(c.hostname||'')+'</span>':'';
    h+='<tr><td><b>'+displayName+'</b>'+(subtitle?'<br>'+subtitle:'')+'</td><td>'+tp+'</td><td>'+escapeHtml(c.ip||'')+'</td>';
    h+='<td>v'+escapeHtml(c.currentVersion)+'</td><td><span id="ota-st-'+c.id+'">'+st+'</span></td>';
    h+='<td><span id="ota-act-'+c.id+'">'+act+'</span></td></tr>';
  });
  el.innerHTML=h+'</table>';
}
function otaSingleUpdate(cid){
  if(!confirm('Update this fixture to the latest firmware? It will reboot.'))return;
  _startOtaForChild(cid);
}
function _startOtaForChild(cid){
  var stEl=document.getElementById('ota-st-'+cid);
  var actEl=document.getElementById('ota-act-'+cid);
  if(stEl)stEl.innerHTML='<span class="badge" style="background:#2563eb;color:#fff">Sending update...</span>';
  if(actEl)actEl.innerHTML='<div class="prog-bar" style="height:6px;width:100px;display:inline-block"><div class="prog-fill" id="ota-prog-'+cid+'" style="width:0%"></div></div>';
  api('POST','/api/firmware/ota/'+cid).then(function(r){
    if(stEl)stEl.innerHTML='<span class="badge" style="background:#7c3aed;color:#fff">Downloading...</span>';
    _pollOtaProgress(cid,0);
  }).catch(function(e){
    if(stEl)stEl.innerHTML='<span class="badge boff">Failed</span>';
    if(actEl)actEl.innerHTML='<button class="btn btn-on" onclick="otaSingleUpdate('+cid+')">Retry</button>';
  });
}
function _pollOtaProgress(cid,attempt){
  // Detect version change by polling child directly + server status
  var maxAttempts=30; // 30 x 3s = 90s max
  var interval=3000;
  var stEl=document.getElementById('ota-st-'+cid);
  var actEl=document.getElementById('ota-act-'+cid);
  var progEl=document.getElementById('ota-prog-'+cid);
  // Find child IP and pre-OTA version
  if(!_pollOtaProgress._ip){
    _pollOtaProgress._ip={};_pollOtaProgress._oldVer={};
    if(_otaChecked&&_otaChecked.children)_otaChecked.children.forEach(function(c){
      _pollOtaProgress._ip[c.id]=c.ip;
      _pollOtaProgress._oldVer[c.id]=c.currentVersion;
    });
  }
  var ip=_pollOtaProgress._ip[cid]||'';
  var oldVer=_pollOtaProgress._oldVer[cid]||'0';

  setTimeout(function(){
    if(progEl)progEl.style.width=Math.min(95,(attempt+1)*100/maxAttempts)+'%';
    // Phase messages
    var phase=attempt<4?'Downloading...':attempt<10?'Flashing & rebooting...':'Reconnecting...';
    if(stEl)stEl.innerHTML='<span class="badge" style="background:#2563eb;color:#fff">'+phase+'</span>';

    // Try child HTTP directly (fastest detection)
    if(ip){
      var x=new XMLHttpRequest();
      x.timeout=2000;
      x.open('GET','http://'+ip+'/status',true);
      x.onload=function(){
        try{
          var d=JSON.parse(x.responseText);
          var newVer=d.version||'';
          if(newVer&&newVer!==oldVer){
            // Version changed — success!
            if(progEl)progEl.style.width='100%';
            if(stEl)stEl.innerHTML='<span class="badge bon">Updated to v'+escapeHtml(newVer)+'</span>';
            if(actEl)actEl.innerHTML='<span style="color:#4c4">&#x2713; Done</span>';
            _pollOtaProgress._ip=null; // reset
            return;
          }
        }catch(e){}
        // Still same version or parse error — keep polling
        if(attempt<maxAttempts)_pollOtaProgress(cid,attempt+1);
        else _otaPollTimeout(cid);
      };
      x.onerror=x.ontimeout=function(){
        // Child offline (rebooting) — keep polling
        if(attempt<maxAttempts)_pollOtaProgress(cid,attempt+1);
        else _otaPollTimeout(cid);
      };
      x.send();
    }else{
      // No IP — fall back to server check
      api('GET','/api/firmware/check').then(function(d){
        var c=null;if(d&&d.children)d.children.forEach(function(ch){if(ch.id===cid)c=ch;});
        if(c&&!c.needsUpdate){
          if(progEl)progEl.style.width='100%';
          if(stEl)stEl.innerHTML='<span class="badge bon">Updated to v'+escapeHtml(c.currentVersion)+'</span>';
          if(actEl)actEl.innerHTML='<span style="color:#4c4">&#x2713; Done</span>';
        }else if(attempt<maxAttempts){_pollOtaProgress(cid,attempt+1);}
        else{_otaPollTimeout(cid);}
      }).catch(function(){
        if(attempt<maxAttempts)_pollOtaProgress(cid,attempt+1);
        else _otaPollTimeout(cid);
      });
    }
  },interval);
}
function _otaPollTimeout(cid){
  var stEl=document.getElementById('ota-st-'+cid);
  var actEl=document.getElementById('ota-act-'+cid);
  // Check one more time via server before giving up
  api('GET','/api/children').then(function(d){
    var c=null;(d||[]).forEach(function(ch){if(ch.id===cid)c=ch;});
    if(c&&c.status===1){
      if(stEl)stEl.innerHTML='<span class="badge bon">Online — v'+escapeHtml(c.fwVersion||'?')+'</span>';
      if(actEl)actEl.innerHTML='<button class="btn btn-on" onclick="checkOtaUpdates()">Recheck</button>';
    }else{
      if(stEl)stEl.innerHTML='<span class="badge boff">Timeout — check device</span>';
      if(actEl)actEl.innerHTML='<button class="btn btn-on" onclick="checkOtaUpdates()">Recheck</button>';
    }
  }).catch(function(){
    if(stEl)stEl.innerHTML='<span class="badge boff">Timeout</span>';
    if(actEl)actEl.innerHTML='<button class="btn btn-on" onclick="checkOtaUpdates()">Recheck</button>';
  });
  _pollOtaProgress._ip=null;
}
function otaUpdateAll(){
  if(!confirm('Update ALL outdated fixtures? They will reboot sequentially.'))return;
  if(!_otaChecked||!_otaChecked.children)return;
  var outdated=_otaChecked.children.filter(function(c){return c.needsUpdate&&c.board!=='wled';});
  if(!outdated.length){document.getElementById('hs').textContent='All fixtures are up to date';return;}
  document.getElementById('hs').textContent='Updating '+outdated.length+' fixture(s)...';
  // Sequential update — one at a time
  var i=0;
  function _next(){
    if(i>=outdated.length){
      document.getElementById('hs').textContent='All updates sent — monitoring progress';
      return;
    }
    _startOtaForChild(outdated[i].id);
    i++;
    setTimeout(_next,5000); // stagger by 5s
  }
  _next();
}

function loadFirmware(){
  // Full firmware tab load — OTA + USB + WiFi + Camera Setup + Library
  checkOtaUpdates();
  _fetchFwLibrary();  // #833 — populate USB dropdown from /api/firmware/library
  // Load WiFi creds
  ra('GET','/api/wifi',null,function(d){
    if(d){
      document.getElementById('fw-ssid').value=d.ssid||'';
      document.getElementById('fw-pass').value='';
      var st=document.getElementById('fw-pw-status');
      if(st)st.textContent=d.hasPassword?'\u2705 Password stored (encrypted)':'\u26a0 No password set';
      if(st)st.style.color=d.hasPassword?'#4c4':'#c66';
    }
  });
  loadFirmwarePorts();
  _loadCamSsh();
  _camFwRefresh();
  renderFirmwareLibrary();
}

// ── #567 Firmware Library ─────────────────────────────────────────────
// Registry-driven table with Local / Not-downloaded badges and a per-row
// Download button. Refresh-All button downloads every missing entry in
// one GitHub round-trip.
function renderFirmwareLibrary(){
  ra('GET','/api/firmware/library',null,function(r){
    var el=document.getElementById('fw-library');if(!el)return;
    var entries=(r&&r.firmware)||[];
    if(!entries.length){el.innerHTML='<p style="color:#888;font-size:.85em">Registry is empty.</p>';return;}
    var h='<table class="tbl" style="max-width:100%;font-size:.78em"><tr>'
      +'<th style="text-align:left">Board</th><th>Version</th><th>Status</th><th></th></tr>';
    entries.forEach(function(e){
      var badge;
      if(e.local){
        badge='<span class="badge" style="background:#14532d;color:#86efac">Local</span>';
      }else if(e.unreadable){
        // #821 — file is on disk but the orchestrator can't read it
        // (file lock, antivirus, OneDrive cloud-only placeholder).
        // Distinct from "Not downloaded" so the operator knows to fix
        // the file rather than waiting for a re-download.
        badge='<span class="badge" style="background:#7c2d12;color:#fdba74" '
          +'title="Binary exists on disk but the orchestrator could not read it. '
          +'Check file lock / antivirus / OneDrive cloud-only placeholder; '
          +'copy the file via Windows Explorer to ensure local availability.">'
          +'Local — unreadable</span>';
      }else if(!e.hasReleaseAsset){
        badge='<span class="badge" style="background:#475569;color:#cbd5e1" title="No releaseAsset in registry — needs local build">Not downloadable</span>';
      }else{
        badge='<span class="badge" style="background:#78350f;color:#fbbf24">Not downloaded</span>';
      }
      var action='';
      if(!e.local&&!e.unreadable&&e.hasReleaseAsset){
        action='<button class="btn" onclick="fetchFirmware(\''+escapeHtml(e.id)+'\',this)" '
          +'style="font-size:.72em;padding:.18em .5em;background:#1e3a5f;color:#93c5fd">Download</button>';
      }
      h+='<tr><td>'+escapeHtml(e.name||e.id)
        +'<div style="color:#64748b;font-size:.78em">'+escapeHtml(e.description||'')+'</div></td>'
        +'<td style="white-space:nowrap">'+escapeHtml(e.version||'?')+'</td>'
        +'<td>'+badge+'</td>'
        +'<td>'+action+'</td></tr>';
    });
    el.innerHTML=h+'</table>';
  });
}

function fetchFirmware(id,btn){
  if(btn){btn.disabled=true;btn.textContent='Downloading…';}
  ra('POST','/api/firmware/fetch',{id:id},function(r){
    if(r&&r.ok){
      if(typeof toastSuccess==='function')toastSuccess('Downloaded '+id);
      renderFirmwareLibrary();
    }else{
      if(btn){btn.disabled=false;btn.textContent='Download';}
      if(typeof toastError==='function')toastError('Download failed: '+((r&&r.err)||'unknown'));
    }
  });
}

// #866 follow-up — Reload list must force-fetch firmware/registry.json
// from GitHub-main and overwrite APPDATA. Without this, a stale APPDATA
// copy on the operator's machine permanently shadows newer publishes
// (e.g. DAVEBOOK-5 still showing gyro v1.2.7 after gyro-v1.2.8 was
// released and the orchestrator was reinstalled). The endpoint busts
// the in-memory remote-registry TTL, persists the fresh registry to
// APPDATA, and returns it inline so we can re-render in one round-trip.
function reloadFirmwareRegistry(btn){
  if(btn){btn.disabled=true;var old=btn.textContent;btn.textContent='Reloading…';btn.dataset._old=old;}
  ra('POST','/api/firmware/registry/refresh',{},function(r){
    if(btn){btn.disabled=false;btn.textContent=btn.dataset._old||'↻ Reload list';}
    if(r&&r.ok){
      if(typeof toastSuccess==='function')toastSuccess('Registry refreshed from GitHub');
      // OTA table also derives from the registry; refresh both.
      checkOtaUpdates();
      renderFirmwareLibrary();
    }else{
      if(typeof toastError==='function')toastError('Reload failed: '+((r&&r.err)||'unknown'));
      // Fall back to a local re-render so the operator sees something
      // sensible even when GitHub is unreachable.
      renderFirmwareLibrary();
    }
  });
}

function refreshFirmwareLibrary(btn){
  if(btn){btn.disabled=true;var old=btn.textContent;btn.textContent='Refreshing…';btn.dataset._old=old;}
  ra('POST','/api/firmware/refresh-all',{},function(r){
    if(btn){btn.disabled=false;btn.textContent=btn.dataset._old||'Refresh All';}
    if(r&&r.ok){
      var n=r.downloaded||0;
      if(typeof toast==='function'){
        if(n===0)toastInfo('All firmware already local — nothing to download');
        else toastSuccess('Downloaded '+n+' new firmware binar'+(n===1?'y':'ies'));
      }
      renderFirmwareLibrary();
    }else{
      if(typeof toastError==='function')toastError('Refresh failed: '+((r&&r.err)||'unknown'));
    }
  });
}
function loadFirmwarePorts(){
  // Scan USB ports, query each for version + wifi
  ra('GET','/api/firmware/ports',null,function(ports){
    ra('GET','/api/firmware/registry',null,function(reg){
      _fwRegistry=reg&&reg.firmware?reg.firmware:[];
      var el=document.getElementById('fw-ports');
      var sel=document.getElementById('fw-port');
      if(!ports||!ports.length){el.innerHTML='<p style="color:#888">No COM ports detected.</p>';return;}
      sel.innerHTML='';
      var boardPorts=[];
      var h='<table class="tbl" id="fw-tbl"><tr><th>Port</th><th>Board</th><th>VID:PID</th><th>Firmware</th><th>WiFi</th></tr>';
      // Separate recognized boards from unknown USB devices (#317)
      var knownPorts=[];var unknownPorts=[];
      ports.forEach(function(p,idx){
        p._idx=idx;
        if(p.board||p.candidates&&p.candidates.length>0)knownPorts.push(p);
        else unknownPorts.push(p);
      });
      // Show known boards first
      knownPorts.forEach(function(p){
        var idx=p._idx;
        var fwCell='<span class="fw-q" id="fwq-'+idx+'" style="color:#888">\u23f3</span>';
        var wfCell='<span id="wfq-'+idx+'" style="color:#888">\u23f3</span>';
        h+='<tr><td>'+p.port+'</td><td>'+p.boardName+'</td><td>'+(p.vid_pid||'-')+'</td>';
        h+='<td>'+fwCell+'</td><td>'+wfCell+'</td></tr>';
        var o=document.createElement('option');o.value=p.port;o.text=p.port+' ('+p.boardName+')';sel.add(o);
        if(p.board){document.getElementById('fw-board').value=p.board;}
        boardPorts.push({port:p.port,idx:idx,board:p.board});
      });
      // Show unknown ports muted (collapsed if many)
      if(unknownPorts.length){
        unknownPorts.forEach(function(p){
          h+='<tr style="opacity:.4"><td>'+p.port+'</td><td style="color:#64748b">'+p.boardName+'</td><td style="color:#475569">'+(p.vid_pid||'-')+'</td><td>-</td><td>-</td></tr>';
          var o=document.createElement('option');o.value=p.port;o.text=p.port+' ('+p.boardName+')';sel.add(o);
        });
      }
      el.innerHTML=h+'</table>';
      updateFwImages();
      // Async query each board port for version + wifi
      boardPorts.forEach(function(bp){
        ra('POST','/api/firmware/query',{port:bp.port},function(r){
          var fwEl=document.getElementById('fwq-'+bp.idx);
          var wfEl=document.getElementById('wfq-'+bp.idx);
          if(!r||!r.ok){if(fwEl)fwEl.textContent='-';if(wfEl)wfEl.textContent='-';return;}
          var ver=r.fwVersion?'v'+r.fwVersion:'-';
          var latestVer='0.0',updateFwId='';
          var brd=r.board||bp.board;
          _fwRegistry.forEach(function(f){if(f.board===brd&&_cmpVer(f.version,latestVer)>0){latestVer=f.version;updateFwId=f.id;}});
          if(r.fwVersion&&_cmpVer(latestVer,r.fwVersion)>0)ver+=' <button class="btn btn-on" style="padding:.15em .4em;font-size:.72em" onclick="quickFlash(\''+bp.port+'\',\''+brd+'\',\''+updateFwId+'\',this)">\u2191 v'+latestVer+'</button>';
          if(fwEl)fwEl.innerHTML=ver;
          if(wfEl){
            if(r.wifiMatch===true)wfEl.innerHTML='<span style="color:#4c4">\u2705</span>';
            else if(r.wifiMatch===false)wfEl.innerHTML='<span style="color:#c44">\u26a0</span> <button class="btn btn-on" style="padding:.12em .4em;font-size:.72em" onclick="quickFlash(\''+bp.port+'\',\''+brd+'\',\''+updateFwId+'\',this)">Update WiFi</button>';
            else wfEl.textContent='-';
          }
          if(r.fwBoard){var row=fwEl&&fwEl.closest?fwEl.closest('tr'):null;if(row&&row.cells[1])row.cells[1].textContent=r.fwBoard;}
        });
      });
    });
  });
}

var _fwRegistry=[];
// #833 — drive USB flash dropdown from /api/firmware/library, which carries
// the registry's semantic version + a sha-verified `local` flag + the
// `releaseAsset` filename per entry. The previous `_fwGithubRelease` path
// fetched a single GitHub /releases/latest and tried to map one asset name
// onto every board (esp32s3 → 'esp32s3-firmware-merged.bin' which doesn't
// exist; the gyro asset is 'esp32s3-gyro-firmware.bin'). With per-firmware
// release tags now in place (gyro-vX.Y.Z, esp32-vA.B.C …) one "latest"
// fetch is meaningless across multiple boards. Library has all the data
// already and is correctly per-board.
var _fwLibrary=[];
function updateFwImages(){
  var board=document.getElementById('fw-board').value;
  var dfuNote=document.getElementById('fw-dfu-note');
  if(dfuNote)dfuNote.style.display=board==='giga'?'block':'none';
  var sel=document.getElementById('fw-image');sel.innerHTML='';
  // Library is authoritative; fall back to registry only while the
  // library fetch is still in flight (first paint before it lands).
  var entries=_fwLibrary.length?_fwLibrary:_fwRegistry;
  entries.forEach(function(f){
    if(f.board!==board)return;
    if(f.local){
      // Cached + sha-verified — flash immediately.
      var o=document.createElement('option');
      o.value=f.id;
      o.text=f.name+' v'+f.version+' (local)';
      sel.add(o);
    } else if(f.hasReleaseAsset){
      // No cached binary, but a release asset exists — server-side
      // api_fw_flash will auto-download via resolve_binary_path on
      // submit. Pre-#833 the SPA POSTed /api/firmware/download FIRST
      // (with a stale single-release assumption), then flashed; the
      // server already does that download as part of flash, so we
      // just submit the firmwareId and let the server handle it.
      var o=document.createElement('option');
      o.value=f.id;
      o.text=f.name+' v'+f.version+' — download required';
      sel.add(o);
    }
    // entries with no local binary AND no release asset are skipped
    // (e.g. parent-giga onHold) — there is nothing to flash.
  });
  if(!sel.options.length){
    var o=document.createElement('option');o.value='';o.text='No firmware available for '+board;sel.add(o);
  }
}
// #833 — fetch /api/firmware/library on firmware tab load. Replaces
// _fetchGithubFirmware(). Library carries per-entry local + version
// already resolved by the orchestrator (which honours the APPDATA
// override + GitHub fallback per #832 pass-3), so we never have to
// guess which release tag covers which board on the SPA side.
function _fetchFwLibrary(){
  ra('GET','/api/firmware/library',null,function(r){
    _fwLibrary=(r&&r.firmware)||[];
    updateFwImages();
  });
}

function saveWifi(btn){
  _btnSaving(btn);
  var pw=document.getElementById('fw-pass').value;
  ra('POST','/api/wifi',{ssid:document.getElementById('fw-ssid').value,password:pw},
    function(r){
      _btnSaved(btn,r&&r.ok);
      if(r&&r.ok){
        // Refresh status indicator
        var st=document.getElementById('fw-pw-status');
        if(st){
          st.textContent=pw?'\u2705 Password stored (encrypted)':'\u26a0 No password set';
          st.style.color=pw?'#4c4':'#c66';
        }
        document.getElementById('fw-pass').value='';
        document.getElementById('fw-pass').type='password';
      }
    });
}

var _flashPoll=null;
function doFlash(btn){
  var port=document.getElementById('fw-port').value;
  var board=document.getElementById('fw-board').value;
  var fwId=document.getElementById('fw-image').value;
  if(!port||!fwId){toastWarn('Select a port and firmware');return;}
  // WiFi must be configured before flashing
  var ssid=document.getElementById('fw-ssid').value;
  if(!ssid){toastWarn('WiFi credentials required. Enter SSID + password above and click Save WiFi before flashing.',{timeout:10000});return;}
  _btnSaving(btn);
  document.getElementById('fw-progress').style.display='block';
  // #833 — single flash path. Server-side api_fw_flash already
  // auto-downloads via resolve_binary_path(auto_download=True) when
  // the binary isn't cached locally, so we don't need a separate
  // SPA-side cloud-download step. The previous `github:` value path
  // was tied to a stale single-release assumption that broke once
  // per-firmware release tags landed.
  _doFlashLocal(btn,port,board,fwId);
}
function _doFlashLocal(btn,port,board,fwId){
  document.getElementById('fw-progress').style.display='block';
  document.getElementById('fw-prog-fill').style.width='2%';
  document.getElementById('fw-prog-msg').textContent='Initiating flash on '+port+'...';
  ra('POST','/api/firmware/flash',{port:port,board:board,firmwareId:fwId},function(r){
    if(!r||!r.ok){
      _btnSaved(btn,false);
      document.getElementById('fw-prog-msg').textContent='Flash failed: '+(r&&r.err||'server rejected request');
      document.getElementById('fw-prog-fill').style.width='0%';
      return;
    }
    _flashPoll=setInterval(function(){
      ra('GET','/api/firmware/flash/status',null,function(s){
        if(!s)return;
        document.getElementById('fw-prog-fill').style.width=s.progress+'%';
        document.getElementById('fw-prog-msg').textContent=s.message||'';
        if(!s.running){
          clearInterval(_flashPoll);_flashPoll=null;
          _btnSaved(btn,!s.error);
          if(s.error){
            document.getElementById('fw-prog-msg').textContent='Error: '+s.error;
            document.getElementById('fw-prog-fill').style.background='#a22';
          }else{
            document.getElementById('fw-prog-msg').textContent='Flash complete — waiting for board to come online...';
            _waitForBoardOnline(port);
          }
        }
      });
    },500);
  });
}

function _waitForBoardOnline(port){
  // Poll port list until the board reappears, then rescan
  var attempts=0;
  var maxAttempts=15; // 15 x 2s = 30s max
  var poller=setInterval(function(){
    attempts++;
    ra('GET','/api/firmware/ports',null,function(d){
      if(!d)return;
      var found=false;
      d.forEach(function(p){if(p.port===port)found=true;});
      var msg=document.getElementById('fw-prog-msg');
      if(found){
        clearInterval(poller);
        if(msg)msg.textContent='Board back online on '+port;
        // Rescan ports and refresh OTA status
        setTimeout(function(){
          loadFirmware();
          document.getElementById('fw-progress').style.display='none';
        },2000);
      }else if(attempts>=maxAttempts){
        clearInterval(poller);
        if(msg)msg.textContent='Board did not reappear on '+port+' — check connection';
        setTimeout(function(){
          loadFirmware();
          document.getElementById('fw-progress').style.display='none';
        },3000);
      }else{
        if(msg)msg.textContent='Waiting for board on '+port+'... ('+attempts+'/'+maxAttempts+')';
      }
    });
  },2000);
}

function quickFlash(port,board,fwId,btn){
  var ssid=document.getElementById('fw-ssid').value;
  if(!ssid){alert('WiFi credentials must be configured before flashing.\nEnter SSID and password above, then click Save WiFi.');return;}
  if(!confirm('Flash firmware '+fwId+' to '+port+'?'))return;
  _btnSaving(btn);
  document.getElementById('fw-progress').style.display='block';
  ra('POST','/api/firmware/flash',{port:port,board:board,firmwareId:fwId},function(r){
    if(!r||!r.ok){_btnSaved(btn,false);return;}
    _flashPoll=setInterval(function(){
      ra('GET','/api/firmware/flash/status',null,function(s){
        if(!s)return;
        document.getElementById('fw-prog-fill').style.width=s.progress+'%';
        document.getElementById('fw-prog-msg').textContent=s.message||'';
        if(!s.running){
          clearInterval(_flashPoll);_flashPoll=null;
          _btnSaved(btn,!s.error);
          if(s.error)document.getElementById('fw-prog-msg').textContent='Error: '+s.error;
          else setTimeout(loadFirmware,2000);
        }
      });
    },500);
  });
}
