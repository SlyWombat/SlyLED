/** wizard.js — Fixture creation wizard: profile search, OFL/community browse, DMX address picker. Extracted from app.js Phase 2. */
// ── #142: Fixture Wizard ─────────────────────────────────────────────────
function showFixtureWizard(){
  _modalStack=[];
  window._wiz={step:1,profile:null,name:'',uni:1,addr:1,channels:3,profId:'',geom:'point'};
  _wizStep1();
}
function _wizStep1(){
  var h='<div style="display:flex;gap:.3em;margin-bottom:.8em">';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#14532d;color:#86efac;font-weight:bold">1. Choose Fixture</div>';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#1e293b;color:#64748b">2. Address</div>';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#1e293b;color:#64748b">3. Confirm</div>';
  h+='</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.5em">Search all fixtures (Local + Community + OFL) or browse your library:</p>';
  h+='<div style="display:flex;gap:.3em;margin-bottom:.5em;flex-wrap:wrap"><input id="wiz-q" placeholder="Search by name, manufacturer, or category..." style="flex:1;min-width:180px;padding:.4em" onkeydown="if(event.key===\'Enter\')_wizSearch()">';
  h+='<button class="btn btn-on" onclick="_wizSearch()">Search</button>';
  h+='<button class="btn" style="background:#1e293b;color:#94a3b8;font-size:.78em" onclick="_wizBrowseAll()">Browse All</button></div>';
  h+='<div id="wiz-results" style="max-height:250px;overflow-y:auto;margin-bottom:.5em"></div>';
  h+='<div style="border-top:1px solid #1e293b;padding-top:.5em"><button class="btn" onclick="_wizCustom()" style="background:#1e293b;color:#94a3b8;font-size:.82em">Create Custom Fixture (skip library)</button></div>';
  document.getElementById('modal-title').textContent='Add DMX Fixture';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='flex';
  setTimeout(function(){var q=document.getElementById('wiz-q');if(q)q.focus();},100);
}
function _wizBrowseAll(){
  var el=document.getElementById('wiz-results');if(!el)return;
  el.innerHTML='<span style="color:#888;font-size:.82em">Loading all profiles...</span>';
  ra('GET','/api/dmx-profiles',null,function(profiles){
    if(!profiles||!profiles.length){el.innerHTML='<span style="color:#888">No profiles</span>';return;}
    var h='<div style="font-size:.75em;color:#64748b;margin-bottom:.3em">'+profiles.length+' profiles in library</div>';
    profiles.forEach(function(p){
      var badge=p.builtin?'<span style="font-size:.6em;padding:1px 4px;border-radius:8px;background:#33415522;color:#94a3b8">Built-in</span>':'<span style="font-size:.6em;padding:1px 4px;border-radius:8px;background:#22c55e22;color:#22c55e">Custom</span>';
      h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:.3em 0;border-bottom:1px solid #1e293b">';
      h+='<div><b style="font-size:.85em">'+escapeHtml(p.name)+'</b> <span style="color:#64748b;font-size:.78em">'+escapeHtml(p.manufacturer||'')+'</span> '+badge+' <span style="color:#64748b;font-size:.72em">'+p.channelCount+'ch</span></div>';
      h+='<button class="btn" style="font-size:.72em;padding:.2em .5em;background:#14532d;color:#86efac" onclick="_wizSelectLocal(\''+escapeHtml(p.id)+'\',\''+escapeHtml(p.name).replace(/'/g,"\\'")+'\','+p.channelCount+')">Select</button></div>';
    });
    el.innerHTML=h;
  });
}
function _wizSelectLocal(profId,name,chCount){
  window._wiz.profId=profId;window._wiz.name=name;window._wiz.channels=chCount;
  _wizStep2();
}
function _wizSearch(){
  var q=document.getElementById('wiz-q').value.trim();var el=document.getElementById('wiz-results');if(!el)return;
  if(q.length<2){el.innerHTML='<span style="color:#f66;font-size:.82em">Enter at least 2 characters</span>';return;}
  el.innerHTML='<span style="color:#888;font-size:.82em">Searching local + community + OFL...</span>';
  ra('GET','/api/dmx-profiles/unified-search?q='+encodeURIComponent(q),null,function(r){
    if(!el)return;
    if(!r||r.err||!r.length){el.innerHTML='<span style="color:#888;font-size:.82em">'+(r&&r.err||'No results for "'+escapeHtml(q)+'"')+'</span>';return;}
    var srcColors={local:'#22c55e',community:'#7c3aed',ofl:'#3b82f6'};
    var srcLabels={local:'Local',community:'Community',ofl:'OFL'};
    var h='<div style="font-size:.75em;color:#64748b;margin-bottom:.3em">'+r.length+' results</div>';
    r.forEach(function(f){
      var src=f.source||'ofl';
      var badge='<span style="font-size:.6em;padding:1px 4px;border-radius:8px;background:'+srcColors[src]+'22;color:'+srcColors[src]+'">'+srcLabels[src]+'</span>';
      var selectFn;
      if(src==='local'){
        selectFn='_wizSelectLocal(\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\','+(f.channelCount||3)+')';
      }else if(src==='community'){
        selectFn='_wizSelectCommunity(\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')';
      }else{
        selectFn='_wizSelectOfl(\''+(f.oflMfr||'')+'\',\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')';
      }
      h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:.3em;border-bottom:1px solid #1e293b">';
      h+='<div><b style="font-size:.85em">'+escapeHtml(f.name)+'</b> <span style="color:#64748b;font-size:.78em">'+escapeHtml(f.manufacturer||'')+'</span> '+badge+'</div>';
      h+='<button class="btn" style="font-size:.72em;padding:.2em .5em;background:#14532d;color:#86efac" onclick="'+selectFn+'">Select</button></div>';
    });
    el.innerHTML=h;
  });
}
function _wizSelectCommunity(slug,name){
  document.getElementById('wiz-results').innerHTML='<span style="color:#a78bfa">Downloading...</span>';
  ra('POST','/api/dmx-profiles/community/download',{slug:slug},function(r){
    if(r&&r.ok){
      // Find the imported profile's channel count
      ra('GET','/api/dmx-profiles/'+slug,null,function(p){
        var ch=p?p.channelCount||3:3;
        window._wiz.profId=slug;window._wiz.name=name;window._wiz.channels=ch;
        _wizStep2();
      });
    }else{document.getElementById('wiz-results').innerHTML='<span style="color:#f66">Download failed</span>';}
  });
}
function _wizSelectOfl(mfr,fix){
  document.getElementById('wiz-results').innerHTML='<span style="color:#888">Importing...</span>';
  ra('POST','/api/dmx-profiles/ofl/import-by-id',{manufacturer:mfr,fixture:fix},function(r){
    if(r&&r.ok&&r.profiles&&r.profiles.length){
      var p=r.profiles[0];
      window._wiz.profId=p.id;window._wiz.name=p.name;window._wiz.channels=p.channels;
      _wizStep2();
    }else{document.getElementById('wiz-results').innerHTML='<span style="color:#f66">Import failed</span>';}
  });
}
function _wizCustom(){window._wiz.profId='';window._wiz.name='Custom Fixture';window._wiz.channels=3;_wizStep2();}
function _wizStep2(){
  var w=window._wiz;
  var h='<div style="display:flex;gap:.3em;margin-bottom:.8em">';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#1e293b;color:#4ade80">1. Choose</div>';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#14532d;color:#86efac;font-weight:bold">2. Address</div>';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#1e293b;color:#64748b">3. Confirm</div>';
  h+='</div>';
  h+='<p style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em">Fixture: <b>'+escapeHtml(w.name)+'</b>'+(w.profId?' ('+w.channels+' channels)':'')+'</p>';
  h+='<div style="display:flex;gap:.8em;flex-wrap:wrap;margin-bottom:.5em">';
  h+='<div><label style="font-size:.82em">Name</label><input id="wiz-name" value="'+escapeHtml(w.name)+'" style="width:200px;display:block"></div>';
  h+='<div><label style="font-size:.82em">Universe</label><input id="wiz-uni" type="number" value="'+w.uni+'" min="1" style="width:80px;display:block"></div>';
  h+='<div><label style="font-size:.82em">Start Address</label><input id="wiz-addr" type="number" value="'+w.addr+'" min="1" max="512" style="width:80px;display:block"></div>';
  if(!w.profId)h+='<div><label style="font-size:.82em">Channels</label><input id="wiz-ch" type="number" value="'+w.channels+'" min="1" style="width:60px;display:block"></div>';
  h+='</div>';
  // Address conflict check
  h+='<div id="wiz-conflict" style="font-size:.78em;margin-bottom:.5em"></div>';
  h+='<div style="display:flex;gap:.5em"><button class="btn" onclick="_wizStep1()" style="background:#334;color:#aaa">Back</button>';
  h+='<button class="btn btn-on" onclick="_wizStep3()">Next</button></div>';
  document.getElementById('modal-title').textContent='Add DMX Fixture - Address';
  document.getElementById('modal-body').innerHTML=h;
  // Check conflicts
  var uniEl=document.getElementById('wiz-uni'),addrEl=document.getElementById('wiz-addr');
  function checkConflict(){
    var uni=parseInt(uniEl.value)||1,addr=parseInt(addrEl.value)||1;
    ra('GET','/api/dmx/patch',null,function(patch){
      var el=document.getElementById('wiz-conflict');if(!el||!patch)return;
      var unis=patch.universes||{};var fx=unis[uni]||[];
      var endAddr=addr+w.channels-1;
      if(endAddr>512){
        el.innerHTML='<span style="color:#fca5a5">Error: channels extend past 512 (addr '+addr+' + '+w.channels+' ch = '+endAddr+')</span>';
        return;
      }
      var conflicts=fx.filter(function(f){return addr<=f.endAddr&&f.startAddr<=endAddr;});
      if(conflicts.length){
        el.innerHTML='<span style="color:#fca5a5">Conflict: overlaps with '+conflicts.map(function(c){return escapeHtml(c.name);}).join(', ')+'</span>';
      }else{el.innerHTML='<span style="color:#4ade80">No conflicts at U'+uni+' @'+addr+'-'+endAddr+'</span>';}
    });
  }
  if(uniEl)uniEl.addEventListener('change',checkConflict);
  if(addrEl)addrEl.addEventListener('change',checkConflict);
  checkConflict();
}
function _wizStep3(){
  var w=window._wiz;
  w.name=document.getElementById('wiz-name').value.trim()||w.name;
  w.uni=parseInt(document.getElementById('wiz-uni').value)||1;
  w.addr=parseInt(document.getElementById('wiz-addr').value)||1;
  if(document.getElementById('wiz-ch'))w.channels=parseInt(document.getElementById('wiz-ch').value)||3;
  var h='<div style="display:flex;gap:.3em;margin-bottom:.8em">';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#1e293b;color:#4ade80">1. Choose</div>';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#1e293b;color:#4ade80">2. Address</div>';
  h+='<div style="flex:1;text-align:center;padding:.3em;border-radius:4px;font-size:.78em;background:#14532d;color:#86efac;font-weight:bold">3. Confirm</div>';
  h+='</div>';
  h+='<div class="card" style="margin-bottom:.8em"><table style="font-size:.85em;width:100%">';
  h+='<tr><td style="color:#94a3b8">Name</td><td><b>'+escapeHtml(w.name)+'</b></td></tr>';
  h+='<tr><td style="color:#94a3b8">Universe</td><td>'+w.uni+'</td></tr>';
  h+='<tr><td style="color:#94a3b8">Start Address</td><td>'+w.addr+'</td></tr>';
  h+='<tr><td style="color:#94a3b8">Channels</td><td>'+w.channels+'</td></tr>';
  if(w.profId)h+='<tr><td style="color:#94a3b8">Profile</td><td>'+escapeHtml(w.profId)+'</td></tr>';
  h+='</table></div>';
  h+='<div style="display:flex;gap:.5em"><button class="btn" onclick="_wizStep2()" style="background:#334;color:#aaa">Back</button>';
  h+='<button class="btn btn-on" onclick="_wizCreate()">Create Fixture</button></div>';
  document.getElementById('modal-title').textContent='Add DMX Fixture - Confirm';
  document.getElementById('modal-body').innerHTML=h;
}
function _wizCreate(){
  var w=window._wiz;
  var body={name:w.name,fixtureType:'dmx',type:w.geom,dmxUniverse:w.uni,dmxStartAddr:w.addr,dmxChannelCount:w.channels};
  if(w.profId)body.dmxProfileId=w.profId;
  ra('POST','/api/fixtures',body,function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Created: '+w.name+' (U'+w.uni+' @'+w.addr+')';
      // #737 Issue 1 — if the new fixture's profile carries pan + tilt
      // (it's a moving head), prompt the operator to set Home now.
      // SMART, mover-control, and gyro/Android remote all hard-require
      // Home before they'll start; surfacing the prompt right at
      // creation time saves the operator from chasing the wizard
      // separately and getting stuck on a fixture_not_calibrated error
      // later.
      var fid = r.id;
      if(w.profId && fid){
        _wizMaybePromptHome(fid, w.profId, w.name);
      } else {
        closeModal();
        loadSetup();
      }
    }else{document.getElementById('hs').textContent='Failed: '+(r&&r.err||'unknown');}
  });
}

function _wizMaybePromptHome(fid, profileId, fixtureName){
  // Fetch the profile to determine if it's a mover (has pan + tilt).
  // Falls through to closeModal() on any error or non-mover profile so
  // the LED-bar / par-can / camera flows aren't slowed by a 404 prompt.
  ra('GET','/api/dmx-profiles/'+encodeURIComponent(profileId),null,function(prof){
    var channels = (prof && prof.channels) || [];
    var hasPan = channels.some(function(c){return c.type==='pan';});
    var hasTilt = channels.some(function(c){return c.type==='tilt';});
    if(!hasPan || !hasTilt){
      closeModal();
      loadSetup();
      return;
    }
    _wizShowHomePrompt(fid, fixtureName);
  });
}

function _wizShowHomePrompt(fid, fixtureName){
  // The wizard modal is still open — replace its body with the Home
  // prompt and let the operator decide. Skipping leaves the fixture
  // in fixtures.json with no homePanDmx16 / homeTiltDmx16; the
  // calibration card will surface "Home not set" until they run the
  // wizard.
  document.getElementById('modal-title').textContent='Set Home for '+fixtureName+'?';
  var s = '<div style="font-size:.85em;color:#cbd5e1;margin-bottom:.6em">'
        + '<b>'+escapeHtml(fixtureName)+'</b> is a moving head. SMART '
        + 'calibration, gyro / Android remote control, and aim-by-XYZ '
        + 'all require a <b>Home position</b> — the DMX values where '
        + 'the beam aims along the fixture\'s saved rotation vector.'
        + '</div>'
        + '<div style="font-size:.78em;color:#94a3b8;margin-bottom:.8em">'
        + 'Set it now (≈30 seconds — drive the beam to its forward '
        + 'reference, then walk through the new direction-only Home '
        + 'Secondary capture), or skip and you\'ll get prompted again '
        + 'when you try to calibrate or remote-control this fixture.'
        + '</div>'
        + '<div style="display:flex;gap:.5em;justify-content:flex-end">'
        + '<button class="btn" onclick="_wizSkipHome()" '
        + 'style="background:#1e293b;color:#cbd5e1">Skip — set later</button>'
        + '<button class="btn btn-on" onclick="_wizStartHome('+fid+')" '
        + 'style="background:#0e7490;color:#a5f3fc">Set Home now</button>'
        + '</div>';
  document.getElementById('modal-body').innerHTML = s;
}

function _wizSkipHome(){
  closeModal();
  loadSetup();
}

function _wizStartHome(fid){
  // Close the wizard modal and chain into the existing Set Home
  // wizard for the new fixture. _setHomeOpen lives in fixtures.js
  // and walks Primary capture → direction-only Secondary (#730).
  closeModal();
  loadSetup();
  if(typeof _setHomeOpen === 'function'){
    setTimeout(function(){_setHomeOpen(fid);}, 150);
  }
}
