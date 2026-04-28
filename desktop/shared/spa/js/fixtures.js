/** fixtures.js — Fixture editing, orientation test, DMX channel test. Extracted from app.js Phase 3. */
// ── Fixtures ────────────────────────────────────────────────────────────────
var _fixtures=[];

function loadFixtures(cb){
  // Pull from /api/layout rather than /api/fixtures — the layout endpoint
  // merges each fixture's saved x/y/z from _layout.children into its
  // fixture record, so the edit modal can pre-populate position inputs
  // without a second round-trip. /api/fixtures returns the raw record
  // with no positions, which was causing every Edit Fixture modal to
  // open with (0, 0, 0).
  ra('GET','/api/layout',null,function(d){
    _fixtures=(d&&d.fixtures)||[];
    renderFixturesSidebar();
    if(cb)cb();
  });
}

function renderFixturesSidebar(){
  var el=document.getElementById('lay-fixtures');if(!el)return;
  if(!_fixtures.length){el.innerHTML='<p style="color:#555;font-size:.82em">No fixtures. Add fixtures in the Setup tab.</p>';return;}
  var h='';
  // Q13 — one-click Camera Health readout for all cameras.
  if(_fixtures.some(function(f){return f.fixtureType==='camera';})){
    h+='<div style="margin-bottom:.3em;display:flex;justify-content:flex-end">'
      +'<button class="btn" onclick="showCameraHealth()" style="font-size:.72em;padding:.15em .5em" title="Show tier/RMS/marker count for every registered camera">Camera Health</button>'
      +'</div>';
  }
  // Unplaced fixtures first (draggable)
  var unplaced=_fixtures.filter(function(f){return !_isFixturePlaced(f);});
  if(unplaced.length){
    h+='<div style="color:#64748b;font-size:.75em;margin-bottom:.3em">Drag to place:</div>';
    unplaced.forEach(function(f){
      var ft=f.fixtureType||'led';
      var icon=ft==='dmx'?'<span style="font-size:.6em;background:#7c3aed;color:#fff;padding:0 3px;border-radius:2px">DMX</span>':ft==='camera'?'<span style="font-size:.6em;background:#0e7490;color:#fff;padding:0 3px;border-radius:2px">CAM</span>':'';
      h+='<div class="li" draggable="true" ondragstart="layDS(event,'+f.id+')" style="cursor:grab">'
        +'<div style="color:#ccc;font-weight:bold;font-size:.85em">'+escapeHtml(f.name)+' '+icon+'</div>'
        +'<div style="color:#666;font-size:.75em">'+f.type+'</div></div>';
    });
  }
  // Placed fixtures
  var placed=_fixtures.filter(_isFixturePlaced);
  if(placed.length){
    if(unplaced.length)h+='<div style="border-top:1px solid #1e293b;margin:.4em 0"></div>';
    h+='<div style="color:#64748b;font-size:.75em;margin-bottom:.3em">On stage:</div>';
  }
  placed.forEach(function(f){
    var ft=f.fixtureType||'led';
    var icon=f.type==='point'?'\u2b24':f.type==='group'?'\u25a3':'\u2501';
    // Q5 - tiered placement-quality badge. H (green) = homography,
    // FOV (amber) = camera-pose fallback, RAW (red) = no cal + no pos.
    var ftBadge;
    if(ft==='dmx'){
      ftBadge='<span style="font-size:.6em;background:#7c3aed;color:#fff;padding:0 3px;border-radius:2px;margin-left:2px">DMX</span>';
    }else if(ft==='camera'){
      ftBadge='<span style="font-size:.6em;background:#0e7490;color:#fff;padding:0 3px;border-radius:2px;margin-left:2px">CAM</span>';
      if(f.calibrated){
        ftBadge+='<span style="font-size:.6em;background:#065f46;color:#34d399;padding:0 3px;border-radius:2px;margin-left:2px" title="Homography calibration from surveyed markers">H</span>';
      }else if(f.x!=null||f.y!=null||f.z!=null){
        ftBadge+='<span style="font-size:.6em;background:#78350f;color:#fbbf24;padding:0 3px;border-radius:2px;margin-left:2px" title="No homography - FOV projection fallback (less accurate)">FOV</span>';
      }else{
        ftBadge+='<span style="font-size:.6em;background:#7f1d1d;color:#fca5a5;padding:0 3px;border-radius:2px;margin-left:2px" title="No cal and no position - tracking holds last-good for this camera">RAW</span>';
      }
    }else{
      ftBadge='';
    }
    h+='<div style="padding:.2em 0;border-bottom:1px solid #1e293b;display:flex;align-items:center;gap:.3em">';
    h+='<span style="font-size:.8em">'+icon+'</span>';
    h+='<span style="flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;cursor:pointer;font-size:.82em" onclick="selectOnCanvas(\'fixture\','+f.id+')">'+escapeHtml(f.name)+ftBadge+'</span>';
    h+='<span style="cursor:pointer;color:#3b82f6;font-size:.75em" onclick="editFixture('+f.id+')" title="Edit">\u270e</span>';
    h+='</div>';
  });
  if(!unplaced.length&&placed.length)h+='<p style="color:#555;font-size:.75em;margin-top:.4em">All fixtures placed.</p>';
  el.innerHTML=h;
  _panelUpdateCounts();
}
function _isFixturePlaced(f){return f.positioned||f._placed;}

function autoCreateFixtures(){
  ra('POST','/api/migrate/layout',{},function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Created '+r.created+' fixtures from children';
      loadFixtures();
    }
  });
}

function editFixture(id){
  var f=null;_fixtures.forEach(function(fx){if(fx.id===id)f=fx;});
  if(!f)return;
  var ft=f.fixtureType||'led';
  var h='<label>Name</label><input id="fx-name" value="'+escapeHtml(f.name)+'" style="width:100%">';
  h+='<label>Geometry</label><select id="fx-type"><option value="linear"'+(f.type==='linear'?' selected':'')+'>Linear</option><option value="point"'+(f.type==='point'?' selected':'')+'>Point</option><option value="group"'+(f.type==='group'?' selected':'')+'>Group</option></select>';
  if(ft==='dmx'){
    h+='<label>Universe</label><input id="fx-uni" type="number" value="'+(f.dmxUniverse||1)+'" min="1" style="width:100%;margin-bottom:.4em">';
    h+='<label>Start Address (1–512)</label><input id="fx-addr" type="number" value="'+(f.dmxStartAddr||1)+'" min="1" max="512" style="width:100%;margin-bottom:.4em">';
    h+='<label>Channel Count</label><input id="fx-ch" type="number" value="'+(f.dmxChannelCount||3)+'" min="1" style="width:100%;margin-bottom:.4em">';
    // Unified Local + Community + OFL search — mirrors Add Fixture so
    // operators don't have to memorise a profile id by hand (was a plain
    // "Profile ID (optional)" text field before; now searches the full
    // 700-fixture library).
    h+='<div style="margin-bottom:.6em;padding:.5em;background:#0f172a;border:1px solid #1e3a5f;border-radius:4px">';
    h+='<label style="font-size:.82em;color:#93c5fd">Search All Fixtures (Local + Community + OFL)</label>';
    h+='<div style="display:flex;gap:.3em;margin:.3em 0"><input id="fx-ofl-q" placeholder="e.g. par, moving head, chauvet..." style="flex:1;padding:.3em;font-size:.85em" onkeydown="if(event.key===\'Enter\')_fxOflSearch()">';
    h+='<button class="btn" style="font-size:.75em;padding:.2em .5em;background:#1e3a5f;color:#93c5fd" onclick="_fxOflSearch()">Search</button>';
    h+='<button class="btn" style="font-size:.75em;padding:.2em .5em;background:#1e293b;color:#94a3b8" onclick="_fxBrowseAll()">Browse All</button></div>';
    h+='<div id="fx-ofl-results" style="max-height:200px;overflow-y:auto;font-size:.8em"></div>';
    h+='</div>';
    h+='<label>Profile <span style="color:#64748b;font-size:.75em">(or pick from the search above)</span></label>';
    h+='<select id="fx-prof" data-current="'+escapeHtml(f.dmxProfileId||'')+'" onchange="_editFxProfileChange()" style="width:100%">';
    h+='<option value="">-- None (generic channels) --</option>';
    if(f.dmxProfileId)h+='<option value="'+escapeHtml(f.dmxProfileId)+'" selected>'+escapeHtml(f.dmxProfileId)+' (loading\u2026)</option>';
    h+='</select>';
    h+='<div style="margin-top:.8em;border-top:1px solid #1e293b;padding-top:.6em">';
    h+='<div style="display:flex;justify-content:space-between;align-items:center">';
    h+='<span style="font-weight:bold;font-size:.85em">Test Channels</span>';
    h+='<button class="btn btn-on" onclick="loadFixtureChannels('+id+')" style="font-size:.65em">Load</button>';
    h+='</div>';
    h+='<div id="fx-test-ch"></div>';
    h+='</div>';
    // #687 \u2014 Motor Calibration / Set Home blocks render AFTER position +
    // rotation in the new ordering. Marker placeholder; the actual HTML
    // lives below the position/rotation block. Stored on a window state
    // for editFixture to splice in at the right spot.
  }else if(ft==='camera'){
    h+='<label>Camera Node IP</label><input id="fx-cam-ip" value="'+escapeHtml(f.cameraIp||'')+'" placeholder="e.g. 192.168.10.50" style="width:100%;margin-bottom:.4em">';
    h+='<label>FOV (degrees)</label><input id="fx-fov" type="number" value="'+(f.fovDeg||60)+'" min="1" max="180" style="width:100%;margin-bottom:.4em">';
    h+='<label>Stream URL <span style="color:#64748b;font-size:.75em">(optional)</span></label>';
    h+='<input id="fx-cam-url" value="'+escapeHtml(f.cameraUrl||'')+'" style="width:100%;margin-bottom:.4em">';
    h+='<div style="display:flex;gap:.5em"><div style="flex:1"><label>Width (px)</label><input id="fx-cam-rw" type="number" value="'+(f.resolutionW||1920)+'" style="width:100%"></div>';
    h+='<div style="flex:1"><label>Height (px)</label><input id="fx-cam-rh" type="number" value="'+(f.resolutionH||1080)+'" style="width:100%"></div></div>';
    // Tracking config
    h+='<div style="border-top:1px solid #1e293b;margin-top:.8em;padding-top:.6em">';
    h+='<div style="font-weight:bold;font-size:.85em;margin-bottom:.4em;color:#f472b6">Tracking Configuration</div>';
    h+='<label>Detect Classes</label>';
    h+='<div id="fx-track-classes" style="max-height:120px;overflow-y:auto;border:1px solid #333;border-radius:4px;padding:.3em;background:#1a1a1a;margin-bottom:.4em">';
    var selCls=f.trackClasses||["person"];
    TRACK_CLASSES.forEach(function(c){
      var chk=selCls.indexOf(c.id)>=0?' checked':'';
      h+='<label style="display:block;cursor:pointer;padding:.1em 0;font-size:.82em"><input type="checkbox" class="fx-trk-cls" value="'+c.id+'"'+chk+'> '+c.label+'</label>';
    });
    h+='</div>';
    h+='<div style="display:flex;gap:.5em;flex-wrap:wrap">';
    h+='<div style="flex:1;min-width:80px"><label style="font-size:.8em">FPS</label><input id="fx-trk-fps" type="number" value="'+(f.trackFps||2)+'" min="0.5" max="10" step="0.5" style="width:100%"></div>';
    h+='<div style="flex:1;min-width:80px"><label style="font-size:.8em">Threshold</label><input id="fx-trk-thr" type="number" value="'+(f.trackThreshold||0.4)+'" min="0.1" max="0.95" step="0.05" style="width:100%"></div>';
    h+='<div style="flex:1;min-width:60px"><label style="font-size:.8em">TTL (s)</label><input id="fx-trk-ttl" type="number" value="'+(f.trackTtl||5)+'" min="1" max="60" style="width:100%"></div>';
    h+='<div style="flex:1;min-width:80px"><label style="font-size:.8em">Re-ID (mm)</label><input id="fx-trk-reid" type="number" value="'+(f.trackReidMm||500)+'" min="50" max="5000" step="50" style="width:100%"></div>';
    h+='</div>';
    h+='<p style="color:#64748b;font-size:.72em;margin-top:.2em">FPS: detection rate. Threshold: min confidence. TTL: seconds before lost track expires. Re-ID: max distance to match same object.</p>';
    h+='</div>';
  }
  // Position (mm)
  h+='<label>Position (mm)</label>';
  h+='<div style="display:flex;gap:.3em;margin-bottom:.6em"><label style="font-size:.75em;color:#64748b">X</label><input id="fx-px" type="number" value="'+(f.x||0)+'" style="width:80px"> <label style="font-size:.75em;color:#64748b">Y</label><input id="fx-py" type="number" value="'+(f.y||0)+'" style="width:80px"> <label style="font-size:.75em;color:#64748b">Z</label><input id="fx-pz" type="number" value="'+(f.z||0)+'" style="width:80px"></div>';
  h+='<label>Rotation (degrees) <span style="color:#64748b;font-size:.75em">Tilt, Pan, Roll</span></label>';
  var rot=f.rotation||[0,0,0];
  h+='<div style="display:flex;gap:.3em"><label style="font-size:.75em;color:#64748b">Tilt</label><input id="fx-rx" type="number" value="'+rot[0]+'" style="width:70px"> <label style="font-size:.75em;color:#64748b">Pan</label><input id="fx-ry" type="number" value="'+rot[1]+'" style="width:70px"> <label style="font-size:.75em;color:#64748b">Roll</label><input id="fx-rz" type="number" value="'+rot[2]+'" style="width:70px"></div>';
  h+='<p style="color:#64748b;font-size:.75em;margin-top:.3em">Pan=0 faces forward (+Y depth). Pan=90 faces stage left (+X).<br>Tilt=0 is horizontal. <b>Positive Tilt aims down toward the floor</b> (Tilt=90 is straight down); negative Tilt aims above horizontal.</p>';
  if(ft==='dmx'){
    h+='<label style="display:flex;align-items:center;gap:.4em;margin-top:.5em;cursor:pointer"><input id="fx-inverted" type="checkbox"'+(f.mountedInverted?' checked':'')+' style="width:auto"> <span style="font-size:.82em">Mounted upside-down (inverted)</span></label>';
    h+='<p style="color:#64748b;font-size:.72em;margin-top:.2em">Reverses pan and tilt motor direction for truss-mounted fixtures.</p>';
    // #687 — Set Home block (between Mount and Motor Calibration)
    var hasHome = f.homePanDmx16!=null && f.homeTiltDmx16!=null;
    var setAt = f.homeSetAt ? new Date(f.homeSetAt).toLocaleString() : '';
    h+='<div style="margin-top:.8em;border-top:1px solid #1e293b;padding-top:.6em">';
    h+='<div style="font-weight:bold;font-size:.85em;margin-bottom:.4em">Set Home '
      +(hasHome?'<span style="color:#4ade80">✓ '+escapeHtml(setAt)+'</span>'
              :'<span style="color:#f59e0b">⚠ required for calibration</span>')+'</div>';
    h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.4em">Drive the fixture manually until the beam aims along the Rotation vector above, then Confirm. This anchors calibration to one operator-verified observation.</div>';
    h+='<button class="btn" onclick="_setHomeOpen('+id+')" style="background:#0e7490;color:#a5f3fc;font-size:.85em">'+(hasHome?'Re-Set Home':'Set Home')+'</button>';
    if(hasHome){
      h+=' <button class="btn" onclick="_setHomeClear('+id+')" style="background:#1e293b;color:#94a3b8;font-size:.78em;margin-left:.4em" title="Clear the saved home anchor (will require Set Home again before next cal)">Clear</button>';
    }
    h+='</div>';
    // Motor Calibration block (moved here from above)
    var orient=f.orientation||{};
    h+='<div style="margin-top:.8em;border-top:1px solid #1e293b;padding-top:.6em">';
    h+='<div style="font-weight:bold;font-size:.85em;margin-bottom:.4em">Motor Calibration '
      +(orient.verified?'<span style="color:#4ade80">✓ Calibrated</span>':'<span style="color:#64748b">(optional)</span>')+'</div>';
    if(orient.verified){
      h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.3em">'
        +'Pan: '+(orient.panSign>0?'normal':'⇄ reversed')
        +' | Tilt: '+(orient.tiltSign<0?'normal':'⇅ reversed')
        +'</div>';
    } else {
      h+='<div style="font-size:.78em;color:#64748b;margin-bottom:.3em">Use the upside-down checkbox above if truss-mounted. Run this test with live DMX for precise motor direction calibration.</div>';
    }
    h+='<button class="btn" onclick="_orientTest('+id+')" style="background:#4c1d95;color:#e9d5ff;font-size:.8em">Test with DMX</button>';
    var calDisabled = !hasHome;
    var calStyle = 'background:#6b21a8;color:#d8b4fe;margin-left:.5em' + (calDisabled?';opacity:.5;cursor:not-allowed':'');
    var calOnClick = calDisabled
      ? 'alert(\'Set Home before calibrating. Click Set Home above and drive the fixture along its rotation vector.\');return false;'
      : 'closeModal();_moverCalStart('+id+')';
    var calTitle = calDisabled ? 'Set Home first' : '';
    h+='<button class="btn" onclick="'+calOnClick+'" style="'+calStyle+'" title="'+calTitle+'">Calibrate'+(f.moverCalibrated?' ✓':'')+'</button>';
    h+='</div>';
  }
  h+='<div style="margin-top:.8em"><button class="btn btn-on" onclick="saveFixture('+id+',\''+ft+'\')">Save</button></div>';
  document.getElementById('modal-title').textContent='Edit Fixture: '+f.name;
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  if(ft==='dmx')_editFxLoadProfiles();
}

// Populate the profile dropdown on the edit modal from the local library,
// preserving the fixture's current selection.
function _editFxLoadProfiles(){
  var sel=document.getElementById('fx-prof');if(!sel)return;
  var cur=sel.getAttribute('data-current')||'';
  ra('GET','/api/dmx-profiles',null,function(profiles){
    if(!profiles||!sel)return;
    sel.innerHTML='<option value="">-- None (generic channels) --</option>';
    var matched=false;var curLc=(cur||'').toLowerCase();
    profiles.forEach(function(p){
      var o=document.createElement('option');o.value=p.id;
      o.textContent=p.name+' ('+p.channelCount+'ch)';
      if(!matched&&p.id.toLowerCase()===curLc){o.selected=true;matched=true;}
      sel.appendChild(o);
    });
    if(cur&&!matched){
      // Referenced profile no longer exists locally — preserve the id so
      // Save doesn't silently clear it. Flag it visually.
      var o=document.createElement('option');o.value=cur;o.textContent=cur+' (missing)';o.selected=true;
      sel.appendChild(o);
    }
  });
}

// When the operator picks a different profile, update the channel count
// so the address layout matches — mirrors the Add Fixture behaviour.
function _editFxProfileChange(){
  var sel=document.getElementById('fx-prof');if(!sel)return;
  var pid=sel.value;if(!pid)return;
  ra('GET','/api/dmx-profiles/'+encodeURIComponent(pid),null,function(p){
    if(!p||!p.channels)return;
    var chEl=document.getElementById('fx-ch');
    if(chEl)chEl.value=p.channels.length;
  });
}

// ── Edit Fixture unified profile search (local + community + OFL) ───
// Mirrors _afOflSearch / _afBrowseAll / _afSelect* from setup-ui.js
// but targets the fx-* element IDs used inside the edit modal.

function _fxOflSearch(){
  var q=document.getElementById('fx-ofl-q').value.trim();
  var el=document.getElementById('fx-ofl-results');if(!el)return;
  if(q.length<2){el.innerHTML='<span style="color:#f66">Enter at least 2 characters</span>';return;}
  el.innerHTML='<span style="color:#888">Searching local, community &amp; OFL...</span>';
  ra('GET','/api/dmx-profiles/unified-search?q='+encodeURIComponent(q),null,function(r){
    if(!el)return;
    if(!r||r.err){el.innerHTML='<span style="color:#f66">'+(r&&r.err||'Search failed')+'</span>';return;}
    if(!r.length){el.innerHTML='<span style="color:#888">No results for "'+escapeHtml(q)+'"</span>';return;}
    var srcColors={local:'#22c55e',community:'#7c3aed',ofl:'#3b82f6'};
    var srcLabels={local:'Local',community:'Community',ofl:'OFL'};
    var h='';
    r.forEach(function(f){
      var src=f.source||'ofl';
      var badge='<span style="font-size:.65em;padding:1px 5px;border-radius:8px;background:'+srcColors[src]+'22;color:'+srcColors[src]+'">'+srcLabels[src]+'</span>';
      var fn;
      if(src==='local')fn='_fxSelectLocal(\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\','+(f.channelCount||3)+')';
      else if(src==='community')fn='_fxSelectCommunity(\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')';
      else fn='_fxSelectOfl(\''+(f.oflMfr||'')+'\',\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')';
      h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:.3em 0;border-bottom:1px solid #1e293b">'
        +'<span>'+escapeHtml(f.name)+' <span style="color:#64748b;font-size:.82em">'+escapeHtml(f.manufacturer||'')+'</span> '+badge+'</span>'
        +'<button class="btn" style="font-size:.7em;padding:.15em .4em;background:#14532d;color:#86efac" onclick="'+fn+'">Select</button></div>';
    });
    el.innerHTML=h;
  });
}

function _fxBrowseAll(){
  var el=document.getElementById('fx-ofl-results');if(!el)return;
  el.innerHTML='<span style="color:#888;font-size:.82em">Loading all profiles...</span>';
  ra('GET','/api/dmx-profiles',null,function(profiles){
    if(!profiles||!profiles.length){el.innerHTML='<span style="color:#888">No profiles in library</span>';return;}
    var h='<div style="font-size:.75em;color:#64748b;margin-bottom:.3em">'+profiles.length+' profiles</div>';
    h+='<table style="width:100%;font-size:.82em;border-collapse:collapse"><tr style="color:#64748b"><th style="text-align:left">Name</th><th style="text-align:left">Manufacturer</th><th>Ch</th><th>Category</th><th></th></tr>';
    profiles.forEach(function(p){
      h+='<tr style="border-bottom:1px solid #1e293b">'
        +'<td style="padding:.2em .3em">'+escapeHtml(p.name)+'</td>'
        +'<td style="padding:.2em .3em;color:#94a3b8">'+escapeHtml(p.manufacturer||'')+'</td>'
        +'<td style="text-align:center">'+p.channelCount+'</td>'
        +'<td style="color:#64748b;font-size:.85em">'+escapeHtml(p.category||'')+'</td>'
        +'<td><button class="btn" style="font-size:.7em;padding:.15em .4em;background:#14532d;color:#86efac" '
        +'onclick="_fxSelectLocal(\''+escapeHtml(p.id)+'\',\''+escapeHtml(p.name).replace(/'/g,"\\'")+'\','+p.channelCount+')">Select</button></td></tr>';
    });
    h+='</table>';
    el.innerHTML=h;
  });
}

function _fxSelectLocal(profId,displayName,chCount){
  var chEl=document.getElementById('fx-ch');if(chEl)chEl.value=chCount;
  var sel=document.getElementById('fx-prof');
  if(sel){
    // Ensure the option exists before selecting — the dropdown is loaded
    // async, so a fresh-from-search pick may race with _editFxLoadProfiles.
    var found=false;
    for(var i=0;i<sel.options.length;i++){
      if(sel.options[i].value===profId){sel.selectedIndex=i;found=true;break;}
    }
    if(!found){
      var o=document.createElement('option');o.value=profId;o.textContent=displayName+' ('+chCount+'ch)';o.selected=true;
      sel.appendChild(o);
    }
  }
  var el=document.getElementById('fx-ofl-results');
  if(el)el.innerHTML='<span style="color:#86efac">Selected: '+escapeHtml(displayName)+'</span>';
}

function _fxSelectCommunity(slug,displayName){
  var el=document.getElementById('fx-ofl-results');
  if(el)el.innerHTML='<span style="color:#a78bfa">Downloading '+escapeHtml(displayName)+'...</span>';
  ra('POST','/api/dmx-profiles/community/download',{slug:slug},function(r){
    if(r&&r.ok){
      // Refresh local profile list then select the freshly-downloaded one.
      ra('GET','/api/dmx-profiles',null,function(profiles){
        var sel=document.getElementById('fx-prof');if(!sel)return;
        sel.innerHTML='<option value="">-- None (generic channels) --</option>';
        (profiles||[]).forEach(function(p){
          var o=document.createElement('option');o.value=p.id;o.textContent=p.name+' ('+p.channelCount+'ch)';
          sel.appendChild(o);if(p.id===slug)sel.value=slug;
        });
        var matched=(profiles||[]).find(function(p){return p.id===slug;});
        if(matched){var chEl=document.getElementById('fx-ch');if(chEl)chEl.value=matched.channelCount;}
        if(el)el.innerHTML='<span style="color:#86efac">Downloaded: '+escapeHtml(displayName)+'</span>';
      });
    }else{
      if(el)el.innerHTML='<span style="color:#f66">Download failed'+(r&&r.err?': '+escapeHtml(r.err):'')+'</span>';
    }
  });
}

function _fxSelectOfl(mfr,fix,displayName){
  var el=document.getElementById('fx-ofl-results');
  if(el)el.innerHTML='<span style="color:#86efac">Importing '+escapeHtml(displayName)+'...</span>';
  ra('POST','/api/dmx-profiles/ofl/import-by-id',{manufacturer:mfr,fixture:fix},function(r){
    if(r&&r.ok&&r.profiles&&r.profiles.length){
      var p=r.profiles[0];
      ra('GET','/api/dmx-profiles',null,function(profiles){
        var sel=document.getElementById('fx-prof');if(!sel)return;
        sel.innerHTML='<option value="">-- None (generic channels) --</option>';
        (profiles||[]).forEach(function(pp){
          var o=document.createElement('option');o.value=pp.id;o.textContent=pp.name+' ('+pp.channelCount+'ch)';
          sel.appendChild(o);if(pp.id===p.id)sel.value=p.id;
        });
        var chEl=document.getElementById('fx-ch');if(chEl&&p.channelCount)chEl.value=p.channelCount;
        if(el)el.innerHTML='<span style="color:#86efac">Imported: '+escapeHtml(displayName)+'</span>';
      });
    }else{
      if(el)el.innerHTML='<span style="color:#f66">Import failed'+(r&&r.err?': '+escapeHtml(r.err):'')+'</span>';
    }
  });
}

// ── Fixture orientation test ──────────────────────────────────────────
var _orientFid=null;
var _orientStep=0;

function _orientTest(fid){
  _orientFid=fid;
  _orientStep=0;
  window._orientData={};
  var f=null;_fixtures.forEach(function(fx){if(fx.id===fid)f=fx;});
  var fname=f?f.name:'Fixture';
  var h='<div style="min-width:380px">';
  h+='<p style="color:#94a3b8;font-size:.85em;margin-bottom:.8em">Determine how <strong>'+escapeHtml(fname)+'</strong> moves by watching the beam as pan and tilt values change.</p>';
  h+='<div class="card" style="margin-bottom:.6em;padding:.6em">';
  h+='<div style="font-size:.82em;color:#f59e0b;margin-bottom:.5em">\u26a0 Make sure the DMX Art-Net engine is running and the fixture responds to DMX commands.</div>';
  h+='<button class="btn btn-on" id="orient-start" onclick="_orientStart()" style="width:100%">Start Orientation Test</button>';
  h+='</div>';
  h+='<div id="orient-step" style="display:none">';
  h+='<div class="prog-bar" style="height:6px;margin-bottom:.5em"><div class="prog-fill" id="orient-prog" style="width:0%"></div></div>';
  h+='<div id="orient-status" style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em"></div>';
  h+='<div id="orient-buttons" style="display:flex;flex-wrap:wrap;gap:.3em"></div>';
  h+='</div>';
  h+='<div style="margin-top:.8em;display:flex;gap:.4em">';
  h+='<button class="btn btn-off" onclick="closeModal();_orientCleanup()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Orientation Test';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function _orientStart(){
  var btn=document.getElementById('orient-start');
  if(btn)btn.style.display='none';
  document.getElementById('orient-step').style.display='block';
  // Pre-flight: check DMX works by sending a test command
  var st=document.getElementById('orient-status');
  st.textContent='Checking DMX connection...';
  ra('POST','/api/fixtures/'+_orientFid+'/dmx-test',{pan:0.5,tilt:0.5,dimmer:1.0},function(r){
    if(!r||!r.ok){
      st.innerHTML='<span style="color:#f66">DMX test failed — is the Art-Net engine running?</span>'
        +'<br><button class="btn btn-on" onclick="_orientStart()" style="margin-top:.4em;font-size:.82em">Retry</button>';
      return;
    }
    st.textContent='Light ON at center position. Look at where it points.';
    document.getElementById('orient-prog').style.width='10%';
    setTimeout(function(){_orientStep=1;_orientShowStep();},2000);
  });
}

function _orientShowStep(){
  var el=document.getElementById('orient-buttons');
  var st=document.getElementById('orient-status');
  var prog=document.getElementById('orient-prog');
  if(!el||!st)return;

  if(_orientStep===1){
    prog.style.width='33%';
    st.innerHTML='<strong>Pan Test:</strong> Moving pan from 0.5 \u2192 0.6. Watch the beam...';
    el.innerHTML='';
    ra('POST','/api/fixtures/'+_orientFid+'/dmx-test',{pan:0.6,tilt:0.5,dimmer:1.0},function(r){
      if(!r||!r.ok){st.innerHTML='<span style="color:#f66">DMX send failed</span>';return;}
      setTimeout(function(){
        st.innerHTML='<strong>Pan Test:</strong> Pan value increased. Which way did the beam move?';
        el.innerHTML=
          '<button class="btn" onclick="_orientAnswer(\'pan\',1)" style="background:#1e3a5f;color:#93c5fd;padding:.5em 1em">\u2192 Right</button>'
          +'<button class="btn" onclick="_orientAnswer(\'pan\',-1)" style="background:#1e3a5f;color:#93c5fd;padding:.5em 1em">\u2190 Left</button>'
          +'<button class="btn" onclick="_orientAnswer(\'pan\',2)" style="background:#1e3a5f;color:#93c5fd;padding:.5em 1em">\u2191 Up</button>'
          +'<button class="btn" onclick="_orientAnswer(\'pan\',-2)" style="background:#1e3a5f;color:#93c5fd;padding:.5em 1em">\u2193 Down</button>'
          +'<button class="btn" onclick="_orientRetry(1)" style="background:#475569;color:#94a3b8;padding:.5em 1em">Retry</button>'
          +'<button class="btn" onclick="_orientAnswer(\'pan\',0)" style="background:#334155;color:#64748b;padding:.5em 1em">Skip</button>';
      },1500);
    });

  }else if(_orientStep===2){
    prog.style.width='66%';
    // Reset to center first
    ra('POST','/api/fixtures/'+_orientFid+'/dmx-test',{pan:0.5,tilt:0.5,dimmer:1.0},function(){
      st.innerHTML='<strong>Tilt Test:</strong> Resetting to center... then moving tilt.';
      el.innerHTML='';
      setTimeout(function(){
        ra('POST','/api/fixtures/'+_orientFid+'/dmx-test',{pan:0.5,tilt:0.6,dimmer:1.0},function(r){
          if(!r||!r.ok){st.innerHTML='<span style="color:#f66">DMX send failed</span>';return;}
          setTimeout(function(){
            st.innerHTML='<strong>Tilt Test:</strong> Tilt value increased. Which way did the beam move?';
            el.innerHTML=
              '<button class="btn" onclick="_orientAnswer(\'tilt\',-1)" style="background:#1e3a5f;color:#93c5fd;padding:.5em 1em">\u2193 Down</button>'
              +'<button class="btn" onclick="_orientAnswer(\'tilt\',1)" style="background:#1e3a5f;color:#93c5fd;padding:.5em 1em">\u2191 Up</button>'
              +'<button class="btn" onclick="_orientAnswer(\'tilt\',2)" style="background:#1e3a5f;color:#93c5fd;padding:.5em 1em">\u2192 Right</button>'
              +'<button class="btn" onclick="_orientAnswer(\'tilt\',-2)" style="background:#1e3a5f;color:#93c5fd;padding:.5em 1em">\u2190 Left</button>'
              +'<button class="btn" onclick="_orientRetry(2)" style="background:#475569;color:#94a3b8;padding:.5em 1em">Retry</button>'
              +'<button class="btn" onclick="_orientAnswer(\'tilt\',0)" style="background:#334155;color:#64748b;padding:.5em 1em">Skip</button>';
          },1500);
        });
      },1500);
    });

  }else if(_orientStep===3){
    prog.style.width='100%';
    // Save
    var orient=window._orientData||{};
    orient.homePan=0.5;orient.homeTilt=0.5;orient.verified=true;
    ra('PUT','/api/fixtures/'+_orientFid,{orientation:orient},function(r){
      if(r&&r.ok){
        (_fixtures||[]).forEach(function(f){if(f.id===_orientFid)f.orientation=orient;});
        var panDesc=orient.panSign>0?'Right (+X)':orient.panSign<0?'Left (-X)':'Not determined';
        var tiltDesc=orient.tiltSign>0?'Down (-Y)':orient.tiltSign<0?'Up (+Y)':'Not determined';
        st.innerHTML='<span style="color:#4ade80;font-size:1.1em">\u2713 Orientation Saved</span>';
        el.innerHTML=
          '<div style="width:100%;margin:.5em 0;font-size:.85em">'
          +'<div style="margin-bottom:.2em">Pan increase \u2192 <strong style="color:#93c5fd">'+panDesc+'</strong></div>'
          +'<div>Tilt increase \u2192 <strong style="color:#93c5fd">'+tiltDesc+'</strong></div>'
          +'</div>'
          +'<button class="btn btn-on" onclick="closeModal();_orientCleanup();editFixture('+_orientFid+')">Done</button>'
          +' <button class="btn" onclick="_orientStep=0;_orientStart()" style="background:#475569;color:#94a3b8">Redo Test</button>';
      }else{
        st.innerHTML='<span style="color:#f66">Save failed</span>';
      }
    });
    _orientCleanup();
  }
}

function _orientRetry(step){
  // Reset to center and redo current step
  ra('POST','/api/fixtures/'+_orientFid+'/dmx-test',{pan:0.5,tilt:0.5,dimmer:1.0},function(){
    _orientStep=step;
    setTimeout(function(){_orientShowStep();},1000);
  });
}

function _orientAnswer(axis,sign){
  if(!window._orientData)window._orientData={};
  if(axis==='pan'){
    window._orientData.panSign=sign||1;
    _orientStep=2;
  }else{
    window._orientData.tiltSign=sign||-1;
    _orientStep=3;
  }
  _orientShowStep();
}

function _orientCleanup(){
  if(_orientFid)ra('POST','/api/fixtures/'+_orientFid+'/dmx-test',{pan:0.5,tilt:0.5,dimmer:0});
}

function saveFixture(id,ft){
  var body={
    name:document.getElementById('fx-name').value,
    type:document.getElementById('fx-type').value,
    rotation:[parseFloat(document.getElementById('fx-rx').value)||0,parseFloat(document.getElementById('fx-ry').value)||0,parseFloat(document.getElementById('fx-rz').value)||0]
  };
  if(ft==='dmx'){
    body.dmxUniverse=parseInt(document.getElementById('fx-uni').value)||1;
    body.dmxStartAddr=parseInt(document.getElementById('fx-addr').value)||1;
    body.dmxChannelCount=parseInt(document.getElementById('fx-ch').value)||3;
    body.dmxProfileId=document.getElementById('fx-prof').value.trim()||null;
    var invEl=document.getElementById('fx-inverted');
    if(invEl)body.mountedInverted=invEl.checked;
  }else if(ft==='camera'){
    body.cameraIp=document.getElementById('fx-cam-ip').value.trim();
    body.fovDeg=parseInt(document.getElementById('fx-fov').value)||60;
    body.cameraUrl=document.getElementById('fx-cam-url').value.trim();
    body.resolutionW=parseInt(document.getElementById('fx-cam-rw').value)||1920;
    body.resolutionH=parseInt(document.getElementById('fx-cam-rh').value)||1080;
    var clsCbs=document.querySelectorAll('.fx-trk-cls:checked');
    var classes=[];clsCbs.forEach(function(cb){classes.push(cb.value);});
    if(!classes.length)classes=["person"];
    body.trackClasses=classes;
    body.trackFps=parseFloat(document.getElementById('fx-trk-fps').value)||2;
    body.trackThreshold=parseFloat(document.getElementById('fx-trk-thr').value)||0.4;
    body.trackTtl=parseInt(document.getElementById('fx-trk-ttl').value)||5;
    body.trackReidMm=parseInt(document.getElementById('fx-trk-reid').value)||500;
  }
  // Save position from edit dialog
  var nx=parseInt(document.getElementById('fx-px').value)||0;
  var ny=parseInt(document.getElementById('fx-py').value)||0;
  var nz=parseInt(document.getElementById('fx-pz').value)||0;
  ra('PUT','/api/fixtures/'+id,body,function(r){
    if(!r||!r.ok){closeModal();return;}
    // Update layout position
    var lay=ld||{};var children=lay.children||[];
    var found=false;
    children.forEach(function(c){if(c.id===id){c.x=nx;c.y=ny;c.z=nz;found=true;}});
    if(!found)children.push({id:id,x:nx,y:ny,z:nz});
    lay.children=children;
    ra('POST','/api/layout',lay,function(){
      closeModal();
      ra('GET','/api/layout',null,function(d){
        if(d){ld=d;_fixtures=d.fixtures||[];}
        if(_s3d.inited)s3dLoadChildren();renderFixturesSidebar();
      });
      loadSetup();
    });
  });
}

function delFixture(id){
  if(!confirm('Delete this fixture?'))return;
  ra('DELETE','/api/fixtures/'+id,null,function(){loadFixtures();});
}

function _capLabel(caps,val){
  if(!caps||!caps.length)return'';
  for(var i=0;i<caps.length;i++){
    var r=caps[i].range;
    if(r&&val>=r[0]&&val<=r[1])return caps[i].label||'';
  }
  return'';
}
function loadFixtureChannels(fid){
  ra('GET','/api/dmx/fixture/'+fid+'/channels',null,function(d){
    if(!d||!d.channels)return;
    var el=document.getElementById('fx-test-ch');if(!el)return;
    var h='<div style="font-size:.75em;color:#64748b;margin:.4em 0">Universe '+d.universe+' @ addr '+d.startAddr+'</div>';
    d.channels.forEach(function(ch,ci){
      var caps=ch.capabilities||[];
      var capLbl=_capLabel(caps,ch.value);
      h+='<div style="display:flex;align-items:center;gap:.5em;margin:.2em 0">';
      h+='<span style="width:80px;font-size:.75em;color:#94a3b8">'+escapeHtml(ch.name)+'</span>';
      h+='<input type="range" min="0" max="255" value="'+ch.value+'" style="flex:1" data-caps=\''+JSON.stringify(caps).replace(/'/g,"&#39;")+'\' '
        +'oninput="this.nextElementSibling.textContent=this.value;var cl=this.parentNode.querySelector(\'.cap-lbl\');if(cl)cl.textContent=_capLabel(JSON.parse(this.dataset.caps),parseInt(this.value));dmxTestCh('+fid+','+ch.offset+',parseInt(this.value))">';
      h+='<span style="width:28px;font-size:.75em;text-align:right">'+ch.value+'</span>';
      h+='</div>';
      if(caps.length>1){h+='<div class="cap-lbl" style="font-size:.65em;color:#7c3aed;margin-left:85px;margin-bottom:.2em">'+escapeHtml(capLbl)+'</div>';}
    });
    h+='<button class="btn btn-off" onclick="dmxTestBlackout('+fid+')" style="font-size:.7em;margin-top:.4em">Blackout</button>';
    h+=' <button class="btn" onclick="dmxTestSendAll('+fid+')" style="font-size:.7em;margin-top:.4em;background:#1e3a5f;color:#93c5fd">Send All</button>';
    el.innerHTML=h;
    // Send all channel defaults on load so fixture matches what sliders show
    var initChs=d.channels.filter(function(ch){return ch.value>0||ch.default>0;}).map(function(ch){
      return{offset:ch.offset,value:ch.value>0?ch.value:(ch.default||0)};
    });
    if(initChs.length)ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:initChs},function(){});
  });
}

function dmxTestCh(fid,offset,value){
  ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:[{offset:offset,value:value}]},function(){});
}

function dmxTestSendAll(fid){
  var sliders=document.querySelectorAll('#fx-test-ch input[type=range]');
  var chs=[];
  sliders.forEach(function(s){
    var offset=parseInt(s.getAttribute('oninput').match(/dmxTestCh\(\d+,(\d+)/)[1]);
    chs.push({offset:offset,value:parseInt(s.value)});
  });
  if(chs.length)ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:chs},function(){});
}

function dmxTestBlackout(fid){
  var sliders=document.querySelectorAll('#fx-test-ch input[type=range]');
  sliders.forEach(function(s){s.value=0;s.nextElementSibling.textContent='0';});
  ra('GET','/api/dmx/fixture/'+fid+'/channels',null,function(d){
    if(!d)return;
    var chs=d.channels.map(function(ch){return{offset:ch.offset,value:0};});
    ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:chs},function(){});
  });
}

function showDmxDetails(fid){
  var fix=null;(_fixtures||[]).forEach(function(f){if(f.id===fid)fix=f;});
  if(!fix)return;
  var h='<div style="margin-bottom:.5em;font-size:.82em;color:#94a3b8">'
    +'Universe '+fix.dmxUniverse+' | Address '+(fix.dmxStartAddr||1)+' | '+fix.dmxChannelCount+' channels'
    +(fix.dmxProfileId?' | Profile: <b>'+escapeHtml(fix.dmxProfileId)+'</b>':'')
    +'</div>';
  h+='<div id="dmx-detail-ch" style="max-height:350px;overflow-y:auto"><p style="color:#888;font-size:.82em">Loading channels...</p></div>';
  h+='<div style="margin-top:.6em;display:flex;gap:.5em;flex-wrap:wrap">'
    +'<button class="btn btn-on" onclick="_dmxDetailAllOn('+fid+')" style="font-size:.78em">All On (255)</button>'
    +'<button class="btn btn-off" onclick="_dmxDetailBlackout('+fid+')" style="font-size:.78em">Blackout</button>'
    +'<button class="btn" onclick="_dmxDetailWhite('+fid+')" style="font-size:.78em;background:#554;color:#fed">White</button>'
    +'<button class="btn" onclick="_dmxDetailRed('+fid+')" style="font-size:.78em;background:#522;color:#f88">Red</button>'
    +'<button class="btn" onclick="_dmxDetailGreen('+fid+')" style="font-size:.78em;background:#252;color:#8f8">Green</button>'
    +'<button class="btn" onclick="_dmxDetailBlue('+fid+')" style="font-size:.78em;background:#225;color:#88f">Blue</button>'
    +'<button class="btn" onclick="_dmxDetailDefaults('+fid+')" style="font-size:.78em;background:#1e3a5f;color:#93c5fd">Defaults</button>'
    +'</div>';
  document.getElementById('modal-title').textContent=fix.name+' — Channel Test';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='flex';
  // Load channels
  ra('GET','/api/dmx/fixture/'+fid+'/channels',null,function(d){
    var el=document.getElementById('dmx-detail-ch');if(!el||!d||!d.channels)return;
    var ch='';
    d.channels.forEach(function(c,ci){
      var caps=c.capabilities||[];
      var capLbl=_capLabel(caps,c.value);
      ch+='<div style="display:flex;align-items:center;gap:.4em;margin:.3em 0">'
        +'<span style="width:100px;font-size:.78em;color:#94a3b8;white-space:nowrap;overflow:hidden">'+escapeHtml(c.name)+'</span>'
        +'<input type="range" min="0" max="255" value="'+c.value+'" class="dmx-detail-slider" data-offset="'+c.offset+'" style="flex:1" '
        +'data-caps=\''+JSON.stringify(caps).replace(/'/g,"&#39;")+'\' '
        +'oninput="this.nextElementSibling.textContent=this.value;var cl=this.parentNode.querySelector(\'.cap-lbl\');if(cl)cl.textContent=_capLabel(JSON.parse(this.dataset.caps),parseInt(this.value));dmxTestCh('+fid+','+c.offset+',parseInt(this.value))">'
        +'<span style="width:30px;font-size:.78em;text-align:right">'+c.value+'</span>'
        +'</div>';
      if(caps.length>1){ch+='<div class="cap-lbl" style="font-size:.65em;color:#7c3aed;margin-left:105px;margin-bottom:.1em">'+escapeHtml(capLbl)+'</div>';}
    });
    el.innerHTML=ch;
  });
}
function _dmxDetailSetAll(fid,val){
  var sliders=document.querySelectorAll('.dmx-detail-slider');
  var chs=[];
  sliders.forEach(function(s){s.value=val;s.nextElementSibling.textContent=val;chs.push({offset:parseInt(s.dataset.offset),value:val});});
  ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:chs},function(){});
}
function _dmxDetailAllOn(fid){_dmxDetailSetAll(fid,255);}
function _dmxDetailBlackout(fid){_dmxDetailSetAll(fid,0);}
function _dmxDetailColor(fid,r,g,b){
  // #609 — profile-aware color. Server routes through _set_fixture_color
  // so RGB fixtures get RGB channels and color-wheel fixtures get the
  // nearest wheel slot. After the write, re-read channels to resync
  // sliders so the UI reflects what actually hit the universe buffer.
  ra('POST','/api/dmx/fixture/'+fid+'/test',{color:{r:r,g:g,b:b,dimmer:255}},function(){
    ra('GET','/api/dmx/fixture/'+fid+'/channels',null,function(d){
      if(!d||!d.channels)return;
      var sliders=document.querySelectorAll('.dmx-detail-slider');
      sliders.forEach(function(s){
        var off=parseInt(s.dataset.offset);
        var ch=d.channels.find(function(c){return c.offset===off;});
        if(ch){s.value=ch.value;s.nextElementSibling.textContent=ch.value;}
      });
    });
  });
}
function _dmxDetailWhite(fid){_dmxDetailColor(fid,255,255,255);}
function _dmxDetailRed(fid){_dmxDetailColor(fid,255,0,0);}
function _dmxDetailGreen(fid){_dmxDetailColor(fid,0,255,0);}
function _dmxDetailBlue(fid){_dmxDetailColor(fid,0,0,255);}
function _dmxDetailDefaults(fid){
  // Load profile defaults for all channels
  ra('GET','/api/dmx/fixture/'+fid+'/channels',null,function(d){
    if(!d||!d.channels)return;
    var chs=[];
    d.channels.forEach(function(c){
      var v=c.default||0;
      chs.push({offset:c.offset,value:v});
    });
    ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:chs},function(){
      var sliders=document.querySelectorAll('.dmx-detail-slider');
      sliders.forEach(function(s){
        var off=parseInt(s.dataset.offset);
        var ch=d.channels.find(function(c){return c.offset===off;});
        if(ch){var v=ch.default||0;s.value=v;s.nextElementSibling.textContent=v;}
      });
    });
  });
}


// ── #687 — Set Home modal ──────────────────────────────────────────────
//
// Live pan/tilt sliders write DMX in real time via /api/fixtures/<fid>/dmx-test
// (the existing endpoint we use for individual-channel manual control). On
// Confirm, we POST the current 16-bit pan/tilt values to /api/fixtures/<fid>/home;
// on Cancel we restore whatever the engine had before the modal opened.
//
// State is held on a window global because the modal innerHTML is rebuilt
// per open and the close handler has to reach back out to read it.

var _setHomeState = null;

function _setHomeOpen(fid){
  // Snapshot current fixture state so Cancel can restore. Use simple
  // 0.5/0.5 + dimmer 0 as the safe default if nothing better is known.
  var f = null;
  _fixtures.forEach(function(fx){if(fx.id===fid)f=fx;});
  if(!f){alert('Fixture not found');return;}
  _setHomeState = {
    fid: fid,
    pan: (f.homePanDmx16!=null) ? (f.homePanDmx16/65535) : 0.5,
    tilt: (f.homeTiltDmx16!=null) ? (f.homeTiltDmx16/65535) : 0.5,
    dimmer: 0,
    color: [0, 255, 0],
    saved: false
  };
  document.getElementById('modal-title').textContent='Set Home — '+(f.name||'fixture '+fid);
  document.getElementById('modal-body').innerHTML = _setHomeShellHtml(f);
  document.getElementById('modal').style.display='block';
  // Push initial DMX so the operator sees something on stage.
  _setHomeWriteDmx();
}

function _setHomeShellHtml(f){
  var rot = f.rotation || [0, 0, 0];
  var s = '';
  s += '<div style="font-size:.85em;color:#94a3b8;margin-bottom:.6em">';
  s += 'Drive the fixture until the beam aims along its <b>rotation vector</b> ';
  s += '<span style="color:#cbd5e1">[tilt='+rot[0]+'°, pan='+rot[2]+'°, roll='+rot[1]+'°]</span>, ';
  s += 'then Confirm. The saved DMX value becomes the cal-kickoff anchor (#687).';
  s += '</div>';
  // Pan slider
  s += '<label style="font-size:.82em;color:#cbd5e1">Pan <span id="sh-pan-val" style="color:#64748b">DMX16='+Math.round(_setHomeState.pan*65535)+'</span></label>';
  s += '<div style="display:flex;align-items:center;gap:.4em;margin-bottom:.5em">';
  s += '<button class="btn" onclick="_setHomeNudge(\'pan\',-256)" style="font-size:.78em;padding:.1em .35em" title="-256 (1 coarse step)">«</button>';
  s += '<button class="btn" onclick="_setHomeNudge(\'pan\',-32)" style="font-size:.78em;padding:.1em .35em">‹</button>';
  s += '<input type="range" id="sh-pan" min="0" max="65535" step="1" value="'+Math.round(_setHomeState.pan*65535)+'" oninput="_setHomeSlide(\'pan\',this.value)" style="flex:1">';
  s += '<button class="btn" onclick="_setHomeNudge(\'pan\',32)" style="font-size:.78em;padding:.1em .35em">›</button>';
  s += '<button class="btn" onclick="_setHomeNudge(\'pan\',256)" style="font-size:.78em;padding:.1em .35em">»</button>';
  s += '</div>';
  // Tilt slider
  s += '<label style="font-size:.82em;color:#cbd5e1">Tilt <span id="sh-tilt-val" style="color:#64748b">DMX16='+Math.round(_setHomeState.tilt*65535)+'</span></label>';
  s += '<div style="display:flex;align-items:center;gap:.4em;margin-bottom:.5em">';
  s += '<button class="btn" onclick="_setHomeNudge(\'tilt\',-256)" style="font-size:.78em;padding:.1em .35em">«</button>';
  s += '<button class="btn" onclick="_setHomeNudge(\'tilt\',-32)" style="font-size:.78em;padding:.1em .35em">‹</button>';
  s += '<input type="range" id="sh-tilt" min="0" max="65535" step="1" value="'+Math.round(_setHomeState.tilt*65535)+'" oninput="_setHomeSlide(\'tilt\',this.value)" style="flex:1">';
  s += '<button class="btn" onclick="_setHomeNudge(\'tilt\',32)" style="font-size:.78em;padding:.1em .35em">›</button>';
  s += '<button class="btn" onclick="_setHomeNudge(\'tilt\',256)" style="font-size:.78em;padding:.1em .35em">»</button>';
  s += '</div>';
  s += '<div style="display:flex;gap:.4em;margin:.6em 0">';
  s += '<button class="btn" id="sh-beam-btn" onclick="_setHomeToggleBeam()" style="background:#1e3a5f;color:#93c5fd">Beam ON</button>';
  s += '<button class="btn" onclick="_setHomeRecentre()" style="background:#1e293b;color:#cbd5e1">Recentre</button>';
  s += '<div style="flex:1"></div>';
  s += '<span id="sh-status" style="font-size:.75em;color:#64748b;align-self:center"></span>';
  s += '</div>';
  s += '<div style="margin-top:.8em;display:flex;gap:.4em;justify-content:flex-end">';
  s += '<button class="btn" onclick="_setHomeCancel()" style="background:#1e293b;color:#cbd5e1">Cancel</button>';
  s += '<button class="btn btn-on" onclick="_setHomeConfirm()" style="background:#0e7490;color:#a5f3fc">Confirm Home</button>';
  s += '</div>';
  return s;
}

function _setHomeWriteDmx(){
  if(!_setHomeState)return;
  var fid = _setHomeState.fid;
  var body = {
    pan: _setHomeState.pan,
    tilt: _setHomeState.tilt,
    dimmer: _setHomeState.dimmer,
    red: _setHomeState.color[0]/255,
    green: _setHomeState.color[1]/255,
    blue: _setHomeState.color[2]/255
  };
  var x = new XMLHttpRequest();
  x.open('POST', '/api/fixtures/'+fid+'/dmx-test', true);
  x.setRequestHeader('Content-Type','application/json');
  x.onload = function(){
    var st = document.getElementById('sh-status');
    if(st)st.textContent = (x.status>=200 && x.status<300) ? 'live' : ('HTTP '+x.status);
  };
  x.onerror = function(){
    var st = document.getElementById('sh-status');
    if(st)st.textContent = 'DMX write failed';
  };
  x.send(JSON.stringify(body));
}

function _setHomeSlide(axis, val){
  if(!_setHomeState)return;
  var v = parseInt(val,10) / 65535;
  if(axis==='pan')_setHomeState.pan = v;
  else _setHomeState.tilt = v;
  var lab = document.getElementById('sh-'+axis+'-val');
  if(lab)lab.textContent='DMX16='+Math.round(_setHomeState[axis]*65535);
  _setHomeWriteDmx();
}

function _setHomeNudge(axis, delta){
  if(!_setHomeState)return;
  var cur = Math.round(_setHomeState[axis]*65535) + delta;
  cur = Math.max(0, Math.min(65535, cur));
  _setHomeState[axis] = cur/65535;
  var slider = document.getElementById('sh-'+axis);
  if(slider)slider.value = cur;
  var lab = document.getElementById('sh-'+axis+'-val');
  if(lab)lab.textContent='DMX16='+cur;
  _setHomeWriteDmx();
}

function _setHomeRecentre(){
  if(!_setHomeState)return;
  _setHomeState.pan = 0.5;
  _setHomeState.tilt = 0.5;
  var ps = document.getElementById('sh-pan'); if(ps)ps.value=Math.round(_setHomeState.pan*65535);
  var ts = document.getElementById('sh-tilt'); if(ts)ts.value=Math.round(_setHomeState.tilt*65535);
  var pl = document.getElementById('sh-pan-val'); if(pl)pl.textContent='DMX16='+Math.round(_setHomeState.pan*65535);
  var tl = document.getElementById('sh-tilt-val'); if(tl)tl.textContent='DMX16='+Math.round(_setHomeState.tilt*65535);
  _setHomeWriteDmx();
}

function _setHomeToggleBeam(){
  if(!_setHomeState)return;
  _setHomeState.dimmer = _setHomeState.dimmer > 0 ? 0 : 1;
  var btn = document.getElementById('sh-beam-btn');
  if(btn)btn.textContent = _setHomeState.dimmer>0 ? 'Beam OFF' : 'Beam ON';
  _setHomeWriteDmx();
}

function _setHomeBlackout(){
  // Drop dimmer to 0 so the beam isn't left on after Confirm/Cancel.
  // #720 PR-1 + #730 — pan/tilt may not be set during the secondary
  // step. Look up the fixture's home anchor as a fallback so the
  // blackout doesn't snap pan/tilt to 0 (which would slew the head
  // across the room).
  if(!_setHomeState)return;
  var fid = _setHomeState.fid;
  var f = null;
  (_fixtures||[]).forEach(function(fx){if(fx.id===fid)f=fx;});
  var pan = (_setHomeState.pan!=null) ? _setHomeState.pan
            : (f && f.homePanDmx16!=null ? f.homePanDmx16/65535 : 0.5);
  var tilt = (_setHomeState.tilt!=null) ? _setHomeState.tilt
            : (f && f.homeTiltDmx16!=null ? f.homeTiltDmx16/65535 : 0.5);
  var x = new XMLHttpRequest();
  x.open('POST', '/api/fixtures/'+fid+'/dmx-test', true);
  x.setRequestHeader('Content-Type','application/json');
  x.send(JSON.stringify({pan:pan, tilt:tilt, dimmer:0}));
}

function _setHomeCancel(){
  if(!_setHomeState)return;
  _setHomeBlackout();
  _setHomeState = null;
  closeModal();
}

function _setHomeConfirm(){
  if(!_setHomeState)return;
  var fid = _setHomeState.fid;
  var pan16 = Math.round(_setHomeState.pan * 65535);
  var tilt16 = Math.round(_setHomeState.tilt * 65535);
  var x = new XMLHttpRequest();
  x.open('POST', '/api/fixtures/'+fid+'/home', true);
  x.setRequestHeader('Content-Type','application/json');
  x.onload = function(){
    var r = null; try{r=JSON.parse(x.responseText);}catch(e){}
    if(x.status>=200 && x.status<300 && r && r.ok){
      // Reflect on the local fixture so the next editFixture render
      // shows the green check + new timestamp without a server round-trip.
      _fixtures.forEach(function(fx){
        if(fx.id===fid){
          fx.homePanDmx16 = pan16;
          fx.homeTiltDmx16 = tilt16;
          fx.homeSetAt = (r && r.homeSetAt) || new Date().toISOString();
        }
      });
      // #720 PR-1 — transition to the Home Secondary wizard step.
      // The legacy single-step Confirm Home is still functional if the
      // operator backs out: the secondary block is purely additive.
      _setHomeOpenSecondary(fid);
    } else {
      alert('Set Home failed: ' + ((r && r.err) || ('HTTP '+x.status)));
    }
  };
  x.onerror = function(){alert('Set Home: network error');};
  x.send(JSON.stringify({panDmx16: pan16, tiltDmx16: tilt16}));
}

// ── #720 PR-1 + #730 — Home Secondary wizard step (direction-only) ─────
//
// After the operator confirms Home (primary), we slew the fixture along
// ONE AXIS AT A TIME and ask the operator a binary question: did the
// beam move LEFT or RIGHT (pan), then DOWN or UP (tilt). The signed
// offset comes from the operator's two binary clicks; the magnitude
// comes from the profile envelope. Wizard accepts unlimited "Show me
// again" retries — operators get distracted, abort-and-restart of the
// whole flow over a 2-second slew is unacceptable. See #730.

function _setHomeOpenSecondary(fid){
  var f = null;
  _fixtures.forEach(function(fx){if(fx.id===fid)f=fx;});
  if(!f){closeModal();return;}
  _setHomeState = _setHomeState || {};
  _setHomeState.fid = fid;
  _setHomeState.secStep = 'pan';
  _setHomeState.panOffsetDmx16 = null;
  _setHomeState.tiltOffsetDmx16 = null;
  _setHomeState.panDir = null;
  _setHomeState.tiltDir = null;
  document.getElementById('modal-title').textContent='Home Secondary — '+(f.name||'fixture '+fid);
  document.getElementById('modal').style.display='block';
  _setHomeSecondaryRender();
  _setHomeSecondarySlew('pan');
}

function _setHomeSecondaryRender(){
  if(!_setHomeState)return;
  var step = _setHomeState.secStep;
  var s = '';

  // #732 — confirmation step before commit. Operator reviews both
  // direction calls; can go back and re-do either axis or commit.
  if(step==='confirm'){
    s += '<div style="font-size:.85em;color:#94a3b8;margin-bottom:.6em">'
       + 'Review your answers before saving Home Secondary. The fixture '
       + 'is now back at home; nothing has been written yet.</div>';
    s += '<div style="background:#0f172a;border:1px solid #1e3a5f;'
       + 'border-radius:4px;padding:.5em .7em;margin-bottom:.7em">';
    s += '<div style="display:flex;justify-content:space-between;'
       + 'font-size:.84em;color:#cbd5e1;margin-bottom:.3em">'
       + '<span>Pan slew →</span>'
       + '<span style="font-weight:bold">'+(_setHomeState.panDir||'?').toUpperCase()+'</span></div>';
    s += '<div style="display:flex;justify-content:space-between;'
       + 'font-size:.84em;color:#cbd5e1">'
       + '<span>Tilt slew →</span>'
       + '<span style="font-weight:bold">'+(_setHomeState.tiltDir||'?').toUpperCase()+'</span></div>';
    s += '</div>';
    s += '<div style="display:flex;gap:.4em;margin-bottom:.5em">';
    s += '<button class="btn" onclick="_setHomeSecondaryGoBackTo(\'pan\')" '
       + 'style="flex:1;background:#334155;color:#cbd5e1">↻ Re-do pan</button>';
    s += '<button class="btn" onclick="_setHomeSecondaryGoBackTo(\'tilt\')" '
       + 'style="flex:1;background:#334155;color:#cbd5e1">↻ Re-do tilt</button>';
    s += '</div>';
    s += '<div style="display:flex;gap:.4em;justify-content:space-between">';
    s += '<button class="btn" onclick="_setHomeSecondarySkip()" '
       + 'style="background:#1e293b;color:#94a3b8">Cancel</button>';
    s += '<button class="btn btn-on" onclick="_setHomeSecondaryCommit()" '
       + 'style="background:#0e7490;color:#a5f3fc">Confirm & save</button>';
    s += '</div>';
    document.getElementById('modal-body').innerHTML = s;
    return;
  }

  s = '<div style="font-size:.85em;color:#94a3b8;margin-bottom:.6em">';
  if(step==='pan'){
    s += 'Step 1 of 2 — pan slew. The fixture will return to home, '
       + 'pause for 2 seconds, then sweep the head 90° in pan. '
       + 'Watch the beam: did it move <b>left</b> or <b>right</b> '
       + 'across the room?';
  } else if(step==='tilt'){
    s += 'Step 2 of 2 — tilt slew. The fixture will return to home, '
       + 'pause for 2 seconds, then tilt the head 90°. Did the beam '
       + 'aim <b>down</b> (closer to the floor) or <b>up</b> (toward '
       + 'the ceiling)?';
  }
  s += '</div>';
  s += '<div id="sh2-status" style="font-size:.78em;color:#64748b;margin-bottom:.6em">Returning to home…</div>';
  s += '<div id="sh2-buttons" style="display:none">';
  if(step==='pan'){
    s += '<div style="display:flex;gap:.5em;margin-bottom:.6em">';
    s += '<button class="btn" onclick="_setHomeSecondaryAnswer(\'left\')" '
       + 'style="flex:1;background:#1e3a5f;color:#93c5fd;padding:.5em">← Beam moved LEFT</button>';
    s += '<button class="btn" onclick="_setHomeSecondaryAnswer(\'right\')" '
       + 'style="flex:1;background:#1e3a5f;color:#93c5fd;padding:.5em">Beam moved RIGHT →</button>';
    s += '</div>';
  } else if(step==='tilt'){
    s += '<div style="display:flex;gap:.5em;margin-bottom:.6em">';
    s += '<button class="btn" onclick="_setHomeSecondaryAnswer(\'down\')" '
       + 'style="flex:1;background:#1e3a5f;color:#93c5fd;padding:.5em">↓ Beam moved DOWN</button>';
    s += '<button class="btn" onclick="_setHomeSecondaryAnswer(\'up\')" '
       + 'style="flex:1;background:#1e3a5f;color:#93c5fd;padding:.5em">Beam moved UP ↑</button>';
    s += '</div>';
  }
  s += '<div style="display:flex;gap:.4em;justify-content:space-between">';
  // #732 — Show me again now uses the /retry endpoint, which executes
  // the same return-home → 2 s pause → 90 ° slew sequence so the
  // operator can re-watch the motion from a known starting pose.
  s += '<button class="btn" onclick="_setHomeSecondaryRetry(\''+step+'\')" '
     + 'style="background:#334155;color:#cbd5e1">↻ Show me again</button>';
  s += '<button class="btn" onclick="_setHomeSecondarySkip()" '
     + 'style="background:#1e293b;color:#94a3b8" '
     + 'title="Skip — Home primary is still saved; SMART will need probes to bootstrap">Skip</button>';
  s += '</div></div>';
  document.getElementById('modal-body').innerHTML = s;
}

function _setHomeSecondarySlew(axis){
  return _setHomeSecondaryDriveServer(axis,
    '/api/fixtures/'+_setHomeState.fid+'/home/secondary/prepare');
}

function _setHomeSecondaryRetry(axis){
  return _setHomeSecondaryDriveServer(axis,
    '/api/fixtures/'+_setHomeState.fid+'/home/secondary/retry');
}

function _setHomeSecondaryDriveServer(axis, endpoint){
  if(!_setHomeState)return;
  // Hide the answer buttons until the slew settles.
  var btns = document.getElementById('sh2-buttons');
  if(btns)btns.style.display='none';
  var st = document.getElementById('sh2-status');
  if(st){
    st.style.color='#64748b';
    st.textContent='Returning to home, pausing 2 s, then slewing '+axis+' 90°…';
  }
  var x = new XMLHttpRequest();
  x.open('POST',endpoint,true);
  x.setRequestHeader('Content-Type','application/json');
  x.onload = function(){
    var r = null; try{r=JSON.parse(x.responseText);}catch(e){}
    if(x.status>=200 && x.status<300 && r && r.ok){
      if(axis==='pan')_setHomeState.panOffsetDmx16 = r.panOffsetDmx16;
      else _setHomeState.tiltOffsetDmx16 = r.tiltOffsetDmx16;
      var st2 = document.getElementById('sh2-status');
      if(st2){
        var off = (axis==='pan'?r.panOffsetDmx16:r.tiltOffsetDmx16);
        var deg = r.slewDeg!=null ? r.slewDeg : 90;
        st2.textContent = 'Slewed '+axis+' '+(off>=0?'+':'-')+deg.toFixed(0)+'° '
                          +'('+(off>=0?'+':'')+off+' DMX). Did the beam move?';
      }
      if(btns)btns.style.display='';
    } else {
      var st3 = document.getElementById('sh2-status');
      if(st3){
        st3.style.color = '#f87171';
        st3.textContent = 'Slew failed: '+((r&&r.err)||('HTTP '+x.status));
      }
    }
  };
  x.onerror = function(){
    var st4 = document.getElementById('sh2-status');
    if(st4){st4.style.color='#f87171';st4.textContent='Network error during slew';}
  };
  x.send(JSON.stringify({axis: axis, settleMs: 1200}));
}

function _setHomeSecondaryAnswer(direction){
  if(!_setHomeState)return;
  if(_setHomeState.secStep==='pan'){
    _setHomeState.panDir = direction;
    _setHomeState.secStep = 'tilt';
    _setHomeSecondaryRender();
    _setHomeSecondarySlew('tilt');
    return;
  }
  if(_setHomeState.secStep==='tilt'){
    _setHomeState.tiltDir = direction;
    // #732 — advance to confirmation step instead of committing
    // directly. Operator reviews both calls before save.
    _setHomeState.secStep = 'confirm';
    _setHomeSecondaryReturnToHome();
    _setHomeSecondaryRender();
  }
}

function _setHomeSecondaryReturnToHome(){
  // #732 — fire-and-forget drive-to-home so the head is at the
  // reference pose while the operator reviews the confirmation panel.
  // Uses /dmx-test instead of the secondary endpoints so we don't
  // re-trigger the home-pause + slew cycle.
  if(!_setHomeState)return;
  var fid = _setHomeState.fid;
  var f = null;
  (_fixtures||[]).forEach(function(fx){if(fx.id===fid)f=fx;});
  if(!f || f.homePanDmx16==null || f.homeTiltDmx16==null)return;
  var x = new XMLHttpRequest();
  x.open('POST','/api/fixtures/'+fid+'/dmx-test', true);
  x.setRequestHeader('Content-Type','application/json');
  x.send(JSON.stringify({
    pan: f.homePanDmx16/65535,
    tilt: f.homeTiltDmx16/65535,
    dimmer: 0,
  }));
}

function _setHomeSecondaryGoBackTo(axis){
  // #732 — operator clicked "Re-do pan" or "Re-do tilt" on the
  // confirmation panel. Re-enter the chosen axis step and replay
  // its slew so the operator can re-answer.
  if(!_setHomeState)return;
  if(axis==='pan'){
    _setHomeState.panDir = null;
    _setHomeState.tiltDir = null;       // pan re-do invalidates tilt
    _setHomeState.secStep = 'pan';
  } else {
    _setHomeState.tiltDir = null;
    _setHomeState.secStep = 'tilt';
  }
  _setHomeSecondaryRender();
  _setHomeSecondarySlew(axis);
}

function _setHomeSecondaryCommit(){
  if(!_setHomeState)return;
  var fid = _setHomeState.fid;
  var body = {
    panOffsetDmx16: _setHomeState.panOffsetDmx16,
    tiltOffsetDmx16: _setHomeState.tiltOffsetDmx16,
    panMovedDirection: _setHomeState.panDir,
    tiltMovedDirection: _setHomeState.tiltDir
  };
  var x = new XMLHttpRequest();
  x.open('POST','/api/fixtures/'+fid+'/home/secondary', true);
  x.setRequestHeader('Content-Type','application/json');
  x.onload = function(){
    var r = null; try{r=JSON.parse(x.responseText);}catch(e){}
    if(x.status>=200 && x.status<300 && r && r.ok){
      _fixtures.forEach(function(fx){
        if(fx.id===fid)fx.homeSecondary = r.homeSecondary;
      });
      _setHomeBlackout();
      _setHomeState = null;
      closeModal();
      editFixture(fid);
    } else {
      alert('Save Secondary failed: '+((r&&r.err)||('HTTP '+x.status)));
    }
  };
  x.onerror = function(){alert('Save Secondary: network error');};
  x.send(JSON.stringify(body));
}

function _setHomeSecondarySkip(){
  // Operator opted out — Home primary is already saved on the server.
  if(!_setHomeState)return;
  var fid = _setHomeState.fid;
  _setHomeBlackout();
  _setHomeState = null;
  closeModal();
  if(fid!=null)editFixture(fid);
}

function _setHomeClear(fid){
  if(!confirm('Clear saved Home anchor for this fixture? Calibrate will be disabled until Set Home runs again.'))return;
  var x = new XMLHttpRequest();
  x.open('DELETE', '/api/fixtures/'+fid+'/home', true);
  x.onload = function(){
    if(x.status>=200 && x.status<300){
      _fixtures.forEach(function(fx){
        if(fx.id===fid){
          delete fx.homePanDmx16;
          delete fx.homeTiltDmx16;
          delete fx.homeSetAt;
        }
      });
      closeModal();
      editFixture(fid);
    } else {
      alert('Clear Home failed: HTTP '+x.status);
    }
  };
  x.send();
}
