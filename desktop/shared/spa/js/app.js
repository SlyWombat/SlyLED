// ── Localization string table (swap for i18n) ─────────────────────────────
function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function _rssiIcon(rssi){
  // Signal bars: ▂▄▆█ based on RSSI strength
  if(!rssi||rssi===0)return '—';
  var abs=Math.abs(rssi);
  if(abs<=50)return '▂▄▆█ '+rssi;
  if(abs<=60)return '▂▄▆░ '+rssi;
  if(abs<=70)return '▂▄░░ '+rssi;
  if(abs<=80)return '▂░░░ '+rssi;
  return '░░░░ '+rssi;
}
var L={
  // Tab navigation
  tabDash:'View live status of all connected fixtures',
  tabSetup:'Register, discover, and manage LED fixture nodes',
  tabLayout:'Arrange fixtures in physical space on canvas',
  tabActions:'Create and manage reusable animation presets',
  tabRuntime:'Design and play timelines for LED shows',
  tabSettings:'Configure app settings, dark mode, and logging',
  tabFirmware:'Flash firmware to boards and manage WiFi credentials',
  // Dashboard
  dashRefresh:'Refresh status of all fixtures via network ping',
  // Setup
  setupDiscover:'Broadcast scan for new fixtures on the network',
  setupAdd:'Register a fixture by its IP address',
  setupRefreshAll:'Ping all registered fixtures to update status',
  setupDetails:'View detailed configuration and string info',
  setupRefresh:'Ping this fixture to update its status',
  setupReboot:'Restart this fixture remotely (brief offline)',
  setupRemove:'Unregister this fixture from the orchestrator',
  // Layout
  layDetail:'Show LED string lines and LED counts on the canvas',
  layPreview:'Show live effect colors on fixtures during playback',
  laySave:'Save the current fixture positions to storage',
  layAutoArrange:'Auto-arrange DMX fixtures: evenly spaced along top of stage, aimed straight down',
  layShowStrings:'Show/hide LED string detail lines on the canvas',
  layCanvas:'Drag fixtures to position. Double-click to edit coordinates. (0,0) = bottom-left.',
  lay2d:'Switch to 2D canvas view',
  lay3d:'Switch to interactive 3D viewport (Three.js)',
  // Actions
  actNew:'Create a new action preset (effect type + parameters)',
  actEdit:'Modify this action\'s type, color, and parameters',
  actDel:'Permanently delete this action from the library',
  // Settings
  setName:'Display name for this SlyLED orchestrator instance',
  setUnits:'Choose between metric (mm) and imperial (inches)',
  setCW:'Stage width',
  setCH:'Stage height',
  setCD:'Stage depth',
  setDark:'Switch between dark and light UI themes',
  setLog:'Write debug logs to a timestamped file in the data folder',
  setSave:'Save all settings to persistent storage',
  setReset:'Delete ALL data and restore factory defaults (cannot be undone)',
  setShutdown:'Stop the SlyLED service and close the application',
  setExportConfig:'Save fixture and layout data as a JSON file',
  setLoadConfig:'Load fixtures and layout from a previously saved file',
  setExportShow:'Save all actions, runners, flights, and shows as a JSON file',
  setLoadShow:'Load show data from a file or generate a demo show',
  setQr:'Display QR code for the SlyLED Android app to scan and connect',
  // Firmware
  fwSaveWifi:'Store WiFi credentials for fixture provisioning',
  fwRefresh:'Rescan for connected boards on USB/serial ports',
  fwFlash:'Upload firmware to the selected board',
  // Table headers
  thHost:'Unique network identifier assigned by the board',
  thName:'User-assigned display name for this fixture',
  thType:'Device type: SlyLED (native) or WLED (third-party)',
  thIP:'Network address of the fixture',
  thStatus:'Current connection state: Online or Offline',
  thFW:'Firmware version currently running on the board',
  thStrings:'Number of LED strings configured on this fixture',
  thSeen:'Timestamp of last successful communication',
  thActions:'Available management actions for this fixture',
  // Step editor
  stepMove:'Reorder this step in the sequence',
  stepRm:'Remove this step from the runner',
};

// Apply tooltips from L table to all elements with data-tip attribute
function _applyTips(root){
  (root||document).querySelectorAll('[data-tip]').forEach(function(el){
    var key=el.getAttribute('data-tip');
    if(L[key])el.title=L[key];
  });
}
// Also apply to dynamic content via MutationObserver
var _tipObs=new MutationObserver(function(muts){muts.forEach(function(m){
  m.addedNodes.forEach(function(n){if(n.querySelectorAll)_applyTips(n);});
});});
document.addEventListener('DOMContentLoaded',function(){
  _applyTips();
  _tipObs.observe(document.body,{childList:true,subtree:true});
});

var ctab='dash',ld=null,phW=10000,phH=5000,drag=null,dox=0,doy=0,units=0,_cvW=900,_cvH=450,_dragStartX=0,_dragStartY=0,_dragMoved=false;
var _layTool='move'; // 'move' or 'rotate'
var _undoStack=[];   // [{fid, x, y, z, rotation}]
function _rotToAim(rot,pos,dist,inverted){
  // Convert rotation [rx,ry,rz] degrees + position to aim point [x,y,z]
  // Stage: X=width, Y=depth(forward), Z=height(up)
  dist=dist||3000;
  var rx=rot?rot[0]:0,ry=rot&&rot.length>1?rot[1]:0;
  if(inverted)rx=-rx;
  var pr=ry*Math.PI/180,tr=rx*Math.PI/180;
  return[pos[0]+Math.sin(pr)*Math.cos(tr)*dist,pos[1]+Math.cos(pr)*Math.cos(tr)*dist,pos[2]-Math.sin(tr)*dist];
}
var _layoutDirty=false;
var _panelSections={fixtures:true,objects:false}; // collapse state (#354: fixtures expanded by default)
var _panelSelectedFid=null; // currently selected fixture ID for side panel
function _clearTabTimers(){
  if(typeof dashRunnerTimer!=='undefined'&&dashRunnerTimer){clearInterval(dashRunnerTimer);dashRunnerTimer=null;}
  if(typeof _rtTimer!=='undefined'&&_rtTimer){clearInterval(_rtTimer);_rtTimer=null;}
  if(typeof _emuTimer!=='undefined'&&_emuTimer){clearInterval(_emuTimer);_emuTimer=null;}
  if(typeof _emuAnimId!=='undefined'&&_emuAnimId){cancelAnimationFrame(_emuAnimId);_emuAnimId=null;}
  if(_emu3d.animId){cancelAnimationFrame(_emu3d.animId);_emu3d.animId=null;}
  if(typeof _tlPlayTimer!=='undefined'&&_tlPlayTimer){clearInterval(_tlPlayTimer);_tlPlayTimer=null;_tlPlaying=false;}
}
function showTab(t){
  var liveTab=(t==='dash'||t==='runtime'||t==='shows');
  // Only detach if going to a non-live tab (layout, setup, etc.)
  if(!liveTab&&_emu3d.activeTab){_emu3dDetach();}
  _clearTabTimers();
  // Stop layout render loop if leaving layout
  if(ctab==='layout'&&t!=='layout'&&_s3d.animId){cancelAnimationFrame(_s3d.animId);_s3d.animId=null;}
  ctab=t;
  ['dash','setup','layout','actions','shows','runtime','settings','firmware'].forEach(function(id){
    var el=document.getElementById('t-'+id);
    if(el)el.style.display=id===t?'block':'none';
    var n=document.getElementById('n-'+id);
    if(n)n.className='tnav'+(id===t?' tact':'');
  });
  if(t==='dash'){
    if(_s3d.animId){cancelAnimationFrame(_s3d.animId);_s3d.animId=null;}
    if(!_s3d.inited)s3dInit();
    if(_s3d.renderer)emu3dInit();
    loadDash();
    if(_s3d.inited&&_s3d.renderer)_dashAttach3d();
  }
  else if(t==='setup')loadSetup();
  else if(t==='layout'){
    if(_s3d.inited&&_s3d.renderer){
      var el=document.getElementById('stage3d');
      if(el&&!el.contains(_s3d.renderer.domElement)){_emu3dDetach();}
      if(!_s3d.animId)s3dAnimate();
    }
    loadLayout();
  }
  else if(t==='actions')loadActions();
  else if(t==='shows'){
    // Attach 3D canvas to shows preview container
    if(_s3d.animId){cancelAnimationFrame(_s3d.animId);_s3d.animId=null;}
    if(!_s3d.inited)s3dInit();
    if(_s3d.renderer){
      emu3dInit();
      _emu3dAttach('shows-3d');
      if(!_emu3d.animId)emu3dAnimate();
    }
    loadShows();
  }
  else if(t==='runtime'){
    // Attach 3D canvas to runtime container
    if(_s3d.animId){cancelAnimationFrame(_s3d.animId);_s3d.animId=null;}
    if(!_s3d.inited)s3dInit();
    if(_s3d.renderer){
      emu3dInit();
      _emu3dAttach('emu-3d');
      if(!_emu3d.animId)emu3dAnimate();
    }
    loadRuntime();
  }
  else if(t==='settings'){loadSettings();loadDmxSettings();}
  else if(t==='firmware')loadFirmware();
}

function _btnSaving(btn){if(!btn)return;btn.dataset.origHtml=btn.innerHTML;btn.dataset.origBg=btn.style.background;btn.textContent='Saving...';btn.disabled=true;btn.style.background='#555';}
function _btnSaved(btn,ok){if(!btn)return;btn.textContent=ok?'Saved!':'Error';btn.style.background=ok?'#2a2':'#a22';setTimeout(function(){if(btn.dataset.origHtml){btn.innerHTML=btn.dataset.origHtml;btn.style.background=btn.dataset.origBg||'';}else{btn.textContent='Save';btn.style.background='';}btn.disabled=false;},1200);}

// API path constants — single source of truth for endpoint URLs
var API={
  children:'/api/children',fixtures:'/api/fixtures',layout:'/api/layout',
  stage:'/api/stage',settings:'/api/settings',actions:'/api/actions',
  objects:'/api/objects',timelines:'/api/timelines',wifi:'/api/wifi',
  profiles:'/api/dmx-profiles',spatialFx:'/api/spatial-effects',
  dmxSettings:'/api/dmx/settings',dmxStatus:'/api/dmx/status',
  dmxPatch:'/api/dmx/patch',dmxInterfaces:'/api/dmx/interfaces',
  showExport:'/api/show/export',showImport:'/api/show/import',
  showPresets:'/api/show/presets',showPreset:'/api/show/preset',
  configExport:'/api/config/export',configImport:'/api/config/import',
  fwRegistry:'/api/firmware/registry',fwPorts:'/api/firmware/ports',
  fwCheck:'/api/firmware/check',fwLatest:'/api/firmware/latest',
  oflSearch:'/api/dmx-profiles/ofl/search',oflImport:'/api/dmx-profiles/ofl/import-by-id',
};
// ra() — callback-style XHR (used by 240+ call sites)
// api() — Promise wrapper for async/await chains
// Both accept API constant paths (e.g. API.children) or literal strings.
function ra(method,path,body,callback){
  var x=new XMLHttpRequest();
  x.open(method,path,true);
  x.timeout=30000;
  if(body)x.setRequestHeader('Content-Type','application/json');
  x.onload=function(){try{if(callback)callback(JSON.parse(x.responseText));}catch(e){if(callback)callback(null);}};
  x.onerror=function(){if(callback)callback(null);};
  x.ontimeout=function(){console.warn('XHR timeout:',method,path);if(callback)callback(null);};
  x.send(body?JSON.stringify(body):null);
}
function api(method,path,body){
  return new Promise(function(resolve,reject){
    ra(method,path,body,function(data){
      if(data!==null)resolve(data);else reject(new Error('Request failed: '+method+' '+path));
    });
  });
}
function pollResults(url){
  return new Promise(function(resolve){
    var p=setInterval(function(){
      ra('GET',url,null,function(r){
        if(r&&r.pending)return;
        clearInterval(p);resolve(r);
      });
    },500);
  });
}
var _modalStack=[];
function _pushModal(){
  // Save current modal state before opening a sub-dialog (only if modal is visible)
  var m=document.getElementById('modal');
  if(!m||m.style.display==='none')return;
  var title=document.getElementById('modal-title').textContent;
  var body=document.getElementById('modal-body').innerHTML;
  _modalStack.push({title:title,body:body});
}
function _popModal(){
  // Restore parent modal state
  if(_modalStack.length){
    var prev=_modalStack.pop();
    document.getElementById('modal-title').textContent=prev.title;
    document.getElementById('modal-body').innerHTML=prev.body;
    return true;
  }
  return false;
}
function closeModal(){
  // If there's a parent dialog on the stack, go back to it instead of closing
  if(_popModal())return;
  document.getElementById('modal').style.display='none';
}

function showDetails(id){
  ra('GET','/api/children',null,function(d){
    if(!d)return;
    var c=null;for(var i=0;i<d.length;i++){if(d[i].id===id){c=d[i];break;}}
    if(!c)return;
    var dirs=['E','N','W','S'],types=['WS2812B','WS2811','APA102'];
    var cleanIp=(c.ip||'').replace(/^https?:\/\//,'').replace(/\/.*$/,'');
    var h='<p style="font-size:.85em;margin-bottom:.6em">';
    h+='IP: <a href="http://'+escapeHtml(cleanIp)+'/" target="_blank" style="color:#88f">'+escapeHtml(cleanIp)+'</a>';
    if(c.desc)h+=' &mdash; '+escapeHtml(c.desc);
    h+='</p>';
    var sc=c.sc||0;
    h+='<p style="font-size:.85em;color:#aaa;margin-bottom:.4em">'+sc+' string'+(sc!==1?'s':'')+'</p>';
    if(sc>0&&c.strings&&c.strings.length){
      h+='<table class="tbl" style="font-size:.8em">';
      h+='<tr><th>#</th><th>LEDs</th><th>Len mm</th><th>Type</th><th>Dir</th><th>Folded</th></tr>';
      for(var si=0;si<sc&&si<c.strings.length;si++){
        var s=c.strings[si];
        h+='<tr><td>'+(si+1)+'</td><td>'+s.leds+'</td><td>'+s.mm+'</td>';
        h+='<td>'+(types[s.type]||s.type)+'</td><td>'+(dirs[s.sdir]||s.sdir)+'</td>';
        h+='<td>'+(s.folded?'\u2705':'—')+'</td></tr>';
      }
      h+='</table>';
    }
    document.getElementById('modal-title').textContent=c.hostname+(c.name&&c.name!==c.hostname?' ('+c.name+')':'');
    document.getElementById('modal-body').innerHTML=h;
    document.getElementById('modal').style.display='block';
  });
}

var _strCol=['#0ff','#f0f','#ff0','#0f0','#f80','#08f','#f08','#8f0'];
var _dirDx=[1,0,-1,0],_dirDy=[0,-1,0,1]; // E,N,W,S in canvas-Y-down coords
var _layDragId=null;
var _layView='3d'; // 'front', 'top', 'side', '3d'

function _isPlaced(c){return c.positioned||c._placed||(c.x>0||c.y>0||c.z>0);}

// ── Phase 7: Help Panel ─────────────────────────────────────────────────────
function toggleHelp(){
  var panel=document.getElementById('help-panel');
  if(!panel)return;
  if(panel.style.display==='block'){panel.style.display='none';return;}
  panel.style.display='block';
  // Load context-sensitive help
  var section=ctab;
  if(section==='actions')section='spatial-effects';
  if(section==='runtime')section='timeline';
  ra('GET','/api/help/'+section,null,function(d){
    var body=document.getElementById('help-body');
    var raw=d&&d.html?d.html:'<p style="color:#888">Help content not available.</p>';
    raw=raw.replace(/<script[^>]*>[\s\S]*?<\/script>/gi,'');
    if(body)body.innerHTML=raw;
  });
}

function _layCheckShowRunning(){
  ra('GET','/api/settings',null,function(s){
    var running=s&&s.runnerRunning;
    var banner=document.getElementById('lay-show-banner');
    var toolbar=document.getElementById('lay-toolbar');
    if(banner)banner.style.display=running?'block':'none';
    if(toolbar)toolbar.style.opacity=running?'0.3':'1';
    if(toolbar)toolbar.style.pointerEvents=running?'none':'auto';
    // Disable TransformControls during playback
    if(_s3d.tctl)_s3d.tctl.enabled=!running;
    // Wire stop button
    var stopBtn=document.getElementById('lay-show-stop');
    if(stopBtn&&running&&s.activeTimeline>=0){
      stopBtn.onclick=function(){ra('POST','/api/timelines/'+s.activeTimeline+'/stop',{},function(){_layCheckShowRunning();});};
    }
    if(running){
      var name=s.activeTimelineName||'Timeline';
      var el=document.getElementById('lay-show-name');
      if(el)el.textContent='Playing: '+name;
    }
  });
}
function loadLayout(){
  _layCheckShowRunning();
  ra('GET','/api/settings',null,function(s){
    if(s)units=s.units||0;
    ra('GET','/api/stage',null,function(st){
      if(st)window._stageData=st;
      // Pre-load profile cache for beam widths
      if(!window._profileCache){
        window._profileCache={};
        ra('GET','/api/dmx-profiles',null,function(profs){
          (profs||[]).forEach(function(p){window._profileCache[p.id]=p;});
          // Re-render after profiles loaded (beam cones need profile data)
          if(_s3d.inited)s3dLoadChildren();
        });
      }
      ra('GET','/api/layout',null,function(d){
        if(!d)return;ld=d;phW=d.canvasW||10000;phH=d.canvasH||5000;
        _fixtures=d.fixtures||[];
        s3dInit(); // init 3D scene on first layout load (idempotent)
        s3dLoadChildren();renderFixturesSidebar();
        loadObjects();
        // Auto-expand fixtures panel on load, show no selection
        _updateSidePanel(null);
        // Check camera calibration status — show warning if uncalibrated
        _checkCamCalWarning();
      });
    });
  });
}

function renderSidebar(){renderFixturesSidebar();_layScanUpdateBtn();_panelUpdateCounts();
  // Restore panel state: if a fixture was selected, re-apply its panel; otherwise expand fixture list
  if(_panelSelectedFid!=null){_updateSidePanel(_panelSelectedFid);}
  else{_panelSections.fixtures=true;var b=document.getElementById('panel-fixtures-body');var a=document.getElementById('panel-fixtures-arrow');if(b)b.style.display='block';if(a)a.style.transform='rotate(90deg)';}
}

// ── Side panel helpers ──────────────────────────────────────────────────
function _panelToggle(section){
  _panelSections[section]=!_panelSections[section];
  var body=document.getElementById('panel-'+section+'-body');
  var arrow=document.getElementById('panel-'+section+'-arrow');
  if(body)body.style.display=_panelSections[section]?'block':'none';
  if(arrow)arrow.style.transform=_panelSections[section]?'rotate(90deg)':'';
}
function _panelUpdateCounts(){
  var fc=document.getElementById('panel-fixtures-count');
  if(fc)fc.textContent=(_fixtures||[]).length;
  var oc=document.getElementById('panel-objects-count');
  if(oc)oc.textContent=(_objects||[]).length;
}
function _updateSidePanel(fixtureId){
  _panelSelectedFid=fixtureId||null;
  var hdr=document.getElementById('panel-header');
  var pos=document.getElementById('panel-position');
  var det=document.getElementById('panel-details');
  if(!fixtureId&&fixtureId!==0){
    // No selection — hide detail sections, expand fixtures list
    if(hdr)hdr.style.display='none';
    if(pos)pos.style.display='none';
    if(det)det.style.display='none';
    _panelSections.fixtures=true;var b=document.getElementById('panel-fixtures-body');var a=document.getElementById('panel-fixtures-arrow');if(b)b.style.display='block';if(a)a.style.transform='rotate(90deg)';
    return;
  }
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id==fixtureId)f=fx;});
  if(!f){console.warn('_updateSidePanel: no fixture with id',fixtureId,'in',(_fixtures||[]).map(function(x){return x.id;}));return;}
  var ft=f.fixtureType||'led';
  // Collapse fixtures list to make room for selected fixture details
  _panelSections.fixtures=false;
  var fb=document.getElementById('panel-fixtures-body');var fa=document.getElementById('panel-fixtures-arrow');
  if(fb)fb.style.display='none';if(fa)fa.style.transform='';
  // Scroll panel to top so header/position/details are visible
  var panel=document.getElementById('lay-panel');if(panel)panel.scrollTop=0;
  // Header
  if(hdr){
    hdr.style.display='block';
    var nm=document.getElementById('panel-fx-name');if(nm)nm.textContent=f.name||'Fixture';
    var badge=document.getElementById('panel-fx-badge');
    if(badge){
      if(ft==='dmx'){badge.textContent='DMX';badge.style.background='#7c3aed';badge.style.color='#fff';}
      else if(ft==='camera'){badge.textContent='CAM';badge.style.background='#0e7490';badge.style.color='#fff';}
      else{badge.textContent='LED';badge.style.background='#14532d';badge.style.color='#86efac';}
    }
  }
  // Position
  if(pos){
    pos.style.display='block';
    var px=document.getElementById('panel-x');if(px)px.value=f.x||0;
    var py=document.getElementById('panel-y');if(py)py.value=f.y||0;
    var pz=document.getElementById('panel-z');if(pz)pz.value=f.z||0;
    pos.dataset.fid=fixtureId;
  }
  // Type-specific details
  if(det){
    det.style.display='block';
    var h='';
    if(ft==='led'&&f.strings&&f.strings.length){
      var dirs=['E','N','W','S'];
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Strings</div>';
      f.strings.forEach(function(s,i){
        h+='<div style="padding:.2em 0;border-bottom:1px solid #1a2333;display:flex;gap:.4em;font-size:.82em">';
        h+='<span style="color:#64748b">#'+(i+1)+'</span>';
        h+='<span style="flex:1">'+(s.leds||0)+' LEDs, '+(s.mm||0)+'mm</span>';
        h+='<span style="color:#64748b">'+(dirs[s.sdir]||'?')+'</span>';
        if(s.folded)h+='<span title="Folded" style="color:#f59e0b;font-size:.7em">F</span>';
        h+='</div>';
      });
    } else if(ft==='dmx'){
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">DMX</div>';
      h+='<div style="font-size:.82em;color:#c4b5fd;margin-bottom:.2em">Profile: '+(f.dmxProfileId?'<span style="color:#e9d5ff">'+escapeHtml(f.dmxProfileId)+'</span>':'<span style="color:#64748b">None</span>')+'</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.4em">Universe: '+(f.dmxUniverse||1)+' | Addr: '+(f.dmxStartAddr||1)+'</div>';
      // Rotation / orientation
      var orient=f.orientation||{};
      var _rot=f.rotation||[0,0,0];
      var _panDeg=Math.round(_rot[1]);
      var _tiltDeg=Math.round(_rot[0]);
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Orientation</div>';
      h+='<div style="display:flex;gap:.3em;align-items:center;margin-bottom:.3em">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Pan\u00b0</label><input id="panel-pan" type="number" value="'+_panDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'pan\',this.value)">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Tilt\u00b0</label><input id="panel-tilt" type="number" value="'+_tiltDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'tilt\',this.value)">';
      if(orient.verified)h+='<span style="color:#4ade80;font-size:.75em">\u2713</span>';
      h+='</div>';
      if(f.mountedInverted)h+='<div style="font-size:.72em;color:#f59e0b;margin-bottom:.3em">\u26a0 Mounted upside-down</div>';
      h+='<div style="display:flex;gap:.3em;flex-wrap:wrap">';
      h+='<button class="btn" onclick="_orientTest('+f.id+')" style="font-size:.7em;padding:.2em .5em;background:#4c1d95;color:#e9d5ff">Test Orient</button>';
      h+='<button class="btn" onclick="closeModal();_moverCalStart('+f.id+')" style="font-size:.7em;padding:.2em .5em;background:#6b21a8;color:#d8b4fe">Calibrate'+(f.moverCalibrated?' \u2713':'')+'</button>';
      h+='</div>';
    } else if(ft==='camera'){
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Camera</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.2em">IP: '+(f.cameraIp?'<span style="color:#e2e8f0">'+escapeHtml(f.cameraIp)+'</span>':'<span style="color:#f59e0b">Not set</span>')+'</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.4em">FOV: '+(f.fovDeg||60)+'\u00b0 | '+(f.resolutionW||1920)+'\u00d7'+(f.resolutionH||1080)+'</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.3em">Homography: '+(f.calibrated?'<span style="color:#4ade80">\u2713 Done</span>':'<span style="color:#f59e0b">Not done</span>')+'</div>';
      h+='<div style="display:flex;gap:.3em;margin-bottom:.4em">';
      h+='<button class="btn" onclick="_intrinsicCalStart('+f.id+')" style="font-size:.7em;padding:.2em .5em;background:#164e63;color:#67e8f9">Calibrate Lens</button> ';
      h+='</div>';
      // Pan/Tilt editable for camera (from rotation)
      var _cRot=f.rotation||[0,0,0];
      var _cPanDeg=Math.round(_cRot[1]);
      var _cTiltDeg=Math.round(_cRot[0]);
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Orientation</div>';
      h+='<div style="display:flex;gap:.3em;align-items:center;margin-top:.3em">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Pan\u00b0</label><input id="panel-pan" type="number" value="'+_cPanDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'pan\',this.value)">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Tilt\u00b0</label><input id="panel-tilt" type="number" value="'+_cTiltDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'tilt\',this.value)">';
      h+='</div>';
    }
    det.innerHTML=h;
  }
}
function _panelPanTiltChange(fid,axis,deg){
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===fid)f=fx;});
  if(!f)return;
  var val=parseFloat(deg)||0;
  if(!f.rotation)f.rotation=[0,0,0];
  if(axis==='pan'){
    f.rotation=[f.rotation[0],val,f.rotation[2]||0];
  } else {
    f.rotation=[val,f.rotation[1],f.rotation[2]||0];
  }
  _layoutDirty=true;_layDirtyUpdate();
  ra('PUT','/api/fixtures/'+fid+'/aim',{rotation:f.rotation});
  s3dLoadChildren();
  _updateSidePanel(fid);
}
function _panelPosChange(){
  var pos=document.getElementById('panel-position');if(!pos)return;
  var fid=parseInt(pos.dataset.fid);if(isNaN(fid))return;
  var nx=parseInt(document.getElementById('panel-x').value)||0;
  var ny=parseInt(document.getElementById('panel-y').value)||0;
  var nz=parseInt(document.getElementById('panel-z').value)||0;
  (_fixtures||[]).forEach(function(f){
    if(f.id===fid){_laySaveUndo(f);f.x=nx;f.y=ny;f.z=nz;f._placed=true;f.positioned=true;}
  });
  _layoutDirty=true;_layDirtyUpdate();
  s3dLoadChildren();
}
function _panelSaveAll(){
  // Save current layout (positions + fixture changes) to server
  if(!ld)return;
  var children=(ld.children||[]).slice();
  // Sync 3D positions into layout children
  (_fixtures||[]).filter(_isFixturePlaced).forEach(function(f){
    var found=false;
    children.forEach(function(c){if(c.id===f.id){c.x=f.x;c.y=f.y;c.z=f.z;found=true;}});
    if(!found)children.push({id:f.id,x:f.x||0,y:f.y||0,z:f.z||0});
  });
  ld.children=children;
  ra('POST','/api/layout',ld,function(r){
    _layoutDirty=false;_layDirtyUpdate();
    var btn=document.querySelector('#panel-position .btn-on');
    if(btn){btn.textContent='Saved!';setTimeout(function(){btn.textContent='Save Position';},1000);}
  });
}
function _layDirtyUpdate(){
  var dot=document.getElementById('lay-dirty-dot');
  if(dot)dot.style.display=_layoutDirty?'inline':'none';
}

// ── Delete selected fixture from stage ──────────────────────────────────
function _layDeleteSelected(){
  if(!_s3d.selected)return;
  var cid=_s3d.selected.userData.childId;
  if(cid!==undefined){removeFromCanvas(cid);_updateSidePanel(null);}
}

function layDS(e,id){_layDragId=id;e.dataTransfer.setData('text/plain',String(id));e.dataTransfer.effectAllowed='move';}

// 2D canvas handlers removed — all interaction through 3D scene now

function showNodeEdit(f){
  var h='<div style="margin-bottom:.8em">';
  h+='<label>X (mm)</label><input id="ne-x" type="number" value="'+f.x+'" min="0" style="width:120px">';
  h+=' <label style="display:inline;margin-left:1em">Y (mm)</label><input id="ne-y" type="number" value="'+f.y+'" min="0" style="width:120px">';
  h+=' <label style="display:inline;margin-left:1em">Z (mm)</label><input id="ne-z" type="number" value="'+(f.z||0)+'" min="0" style="width:120px">';
  h+='</div>';
  var ft=f.fixtureType||'led';
  h+='<div style="color:#64748b;font-size:.8em;margin-bottom:.5em">Type: '+f.type+(ft==='dmx'?' (DMX)':ft==='camera'?' (Camera)':'')+' | ID: '+f.id+'</div>';
  if(f.strings&&f.strings.length){
    var dirs=['East (+X)','North (+Y)','West (-X)','South (-Y)'];
    h+='<table class="tbl" style="font-size:.8em;margin-bottom:.8em"><tr><th>#</th><th>LEDs</th><th>Length</th><th>Dir</th></tr>';
    for(var i=0;i<f.strings.length;i++){
      var s=f.strings[i];
      h+='<tr><td>'+(i+1)+'</td><td>'+(s.leds||0)+'</td><td>'+(s.mm||0)+'mm</td><td>'+(dirs[s.sdir]||'?')+'</td></tr>';
    }
    h+='</table>';
  }
  // Rotation fields for DMX and Camera fixtures
  if(ft==='dmx'||ft==='camera'){
    var _fRot=f.rotation||[0,0,0];
    h+='<div style="margin-bottom:.6em">';
    h+='<label style="font-size:.82em;color:#94a3b8">Orientation (degrees)</label>';
    h+='<div style="display:flex;gap:.4em;align-items:center;margin-top:.2em">';
    h+='<label style="font-size:.75em;color:#64748b;margin:0">Tilt</label><input id="ne-tilt" type="number" value="'+Math.round(_fRot[0])+'" style="width:65px;font-size:.85em;padding:2px 4px">';
    h+='<label style="font-size:.75em;color:#64748b;margin:0">Pan</label><input id="ne-pan" type="number" value="'+Math.round(_fRot[1])+'" style="width:65px;font-size:.85em;padding:2px 4px">';
    h+='<label style="font-size:.75em;color:#64748b;margin:0">Roll</label><input id="ne-roll" type="number" value="'+Math.round(_fRot[2]||0)+'" style="width:65px;font-size:.85em;padding:2px 4px">';
    h+='</div>';
    h+='<div style="font-size:.72em;color:#64748b;margin-top:.2em">Pan=0 faces forward (+Y). Tilt negative = down.</div>';
    if(ft==='dmx'){
      h+='<label style="display:flex;align-items:center;gap:.4em;margin-top:.3em;cursor:pointer"><input id="ne-inverted" type="checkbox"'+(f.mountedInverted?' checked':'')+' style="width:auto"> <span style="font-size:.8em">Mounted upside-down</span></label>';
    }
    h+='</div>';
  }
  h+='<div style="display:flex;gap:.5em;flex-wrap:wrap">';
  h+='<button class="btn btn-on" onclick="applyNodePos('+f.id+')">Save</button>';
  if(ft==='dmx'){
    h+='<button class="btn" style="background:#1e3a5f;color:#93c5fd" onclick="startAimMode('+f.id+')">Click to Aim</button>';
    h+='<button class="btn" style="background:#6b21a8;color:#d8b4fe" onclick="closeModal();_moverCalStart('+f.id+')">Calibrate'+(f.moverCalibrated?' \u2713':'')+'</button>';
  }
  if(ft==='camera'){
    h+='<button class="btn" style="background:#7c3aed;color:#e9d5ff" onclick="closeModal();_calWizardStart('+f.id+')">Calibrate'+(f.calibrated?' \u2713':'')+'</button>';
    var _ta=_trackingCams[f.id];
    h+='<button class="btn" id="trk-btn-'+f.id+'" style="background:'+(_ta?'#9f1239':'#be185d')+';color:#fce7f3" onclick="closeModal();_trackToggle('+f.id+')">'+(_ta?'Stop Track':'Track')+'</button>';
  }
  h+='<button class="btn btn-off" onclick="removeFromCanvas('+f.id+')">Remove from Stage</button>';
  h+='</div>';
  document.getElementById('modal-title').textContent=f.name;
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function applyNodePos(id){
  var nx=parseInt(document.getElementById('ne-x').value)||0;
  var ny=parseInt(document.getElementById('ne-y').value)||0;
  var nz=parseInt(document.getElementById('ne-z').value)||0;
  nx=Math.max(0,nx);ny=Math.max(0,ny);nz=Math.max(0,nz);
  // Save rotation if fields present (DMX/camera)
  var tiltEl=document.getElementById('ne-tilt');
  var panEl=document.getElementById('ne-pan');
  var rollEl=document.getElementById('ne-roll');
  var invEl=document.getElementById('ne-inverted');
  var rotChanged=false;
  if(tiltEl&&panEl){
    var newRot=[parseFloat(tiltEl.value)||0,parseFloat(panEl.value)||0,parseFloat(rollEl?rollEl.value:0)||0];
    _fixtures.forEach(function(f){if(f.id===id){f.rotation=newRot;if(invEl)f.mountedInverted=invEl.checked;}});
    rotChanged=true;
  }
  _fixtures.forEach(function(f){if(f.id===id){f.x=nx;f.y=ny;f.z=nz;f._placed=true;f.positioned=true;}});
  closeModal();
  // Save rotation to server first, then layout position
  var afterRot=function(){
    var lay=ld||{};var children=(lay.children||[]).slice();
    var found=false;
    children.forEach(function(c){if(c.id===id){c.x=nx;c.y=ny;c.z=nz;found=true;}});
    if(!found)children.push({id:id,x:nx,y:ny,z:nz});
    lay.children=children;
    ra('POST','/api/layout',lay,function(){
      // Reload layout (merges fixtures+positions) then rebuild 3D
      ra('GET','/api/layout',null,function(d){
        if(d){ld=d;_fixtures=d.fixtures||[];}
        s3dLoadChildren();renderFixturesSidebar();_updateSidePanel(id);
      });
    });
  };
  if(rotChanged){
    var f=null;_fixtures.forEach(function(fx){if(fx.id===id)f=fx;});
    var aimBody={rotation:f?f.rotation:[0,0,0]};
    // Also save mountedInverted via PUT fixture
    if(f&&f.mountedInverted!==undefined){
      ra('PUT','/api/fixtures/'+id,{mountedInverted:!!f.mountedInverted},function(){
        ra('PUT','/api/fixtures/'+id+'/aim',aimBody,afterRot);
      });
    }else{
      ra('PUT','/api/fixtures/'+id+'/aim',aimBody,afterRot);
    }
  }else{
    afterRot();
  }
}

function removeFromCanvas(id){
  _fixtures.forEach(function(f){if(f.id===id){f.x=0;f.y=0;f.z=0;f._placed=false;f.positioned=false;}});
  closeModal();s3dLoadChildren();
  renderFixturesSidebar();
}

function fmtCoord(mm){
  if(units===1)return(mm/25.4).toFixed(1)+'"';return mm+'mm';
}

// drawLayout() — now a thin wrapper that delegates to 3D scene
function drawLayout(){
  if(_s3d.inited)s3dLoadChildren();
}

function saveLayout(btn){
  if(!_fixtures)return;
  _btnSaving(btn);
  if(_s3d.inited)_s3dSyncToLd();
  _layoutDirty=false;_layDirtyUpdate();
  var toSave=_fixtures.filter(_isFixturePlaced);
  ra('POST','/api/layout',{fixtures:toSave.map(function(f){return{id:f.id,x:f.x,y:f.y,z:f.z||0};})},
    function(r){
      if(!r||!r.ok){_btnSaved(btn,false);return;}
      toSave.forEach(function(f){f.positioned=true;f._placed=false;});
      // Also save any moved objects
      var pending=0;
      (_objects||[]).forEach(function(s){
        if(s.transform){
          pending++;
          ra('DELETE','/api/objects/'+s.id,null,function(){
            ra('POST','/api/objects',s,function(){pending--;if(!pending)_btnSaved(btn,true);});
          });
        }
      });
      if(!pending)_btnSaved(btn,true);
      renderSidebar();
    });
}

// 2D canvas helpers (_nodePos, _hitNode, _cvMouse, cvDown) removed — 3D scene handles all interaction
// ── Layout tool mode (Move / Rotate) + Undo ─────────────────────────
function _layToolToggle(){
  _layTool=_layTool==='move'?'rotate':'move';
  // Update dual mode buttons
  var btnM=document.getElementById('btn-lay-tool');
  var btnR=document.getElementById('btn-lay-orient');
  if(btnM){
    btnM.style.background=_layTool==='move'?'#14532d':'';
    btnM.style.color=_layTool==='move'?'#86efac':'';
  }
  if(btnR){
    btnR.style.background=_layTool==='rotate'?'#7c3aed':'';
    btnR.style.color=_layTool==='rotate'?'#e9d5ff':'';
  }
  // Switch 3D TransformControls mode
  if(_s3d.inited&&_s3d.tctl){
    _s3d.tctl.setMode(_layTool==='rotate'?'rotate':'translate');
  }
  document.getElementById('hs').textContent=_layTool==='move'?'Move mode — drag gizmo to reposition':'Rotate mode — use rotation gizmo to orient fixture';
}
function _laySaveUndo(f){
  _undoStack.push({fid:f.id,x:f.x,y:f.y,z:f.z||0,
    rotation:f.rotation?f.rotation.slice():null});
  if(_undoStack.length>20)_undoStack.shift();
}
function _layUndo(){
  if(!_undoStack.length){document.getElementById('hs').textContent='Nothing to undo';return;}
  var u=_undoStack.pop();
  _fixtures.forEach(function(f){
    if(f.id===u.fid){
      f.x=u.x;f.y=u.y;f.z=u.z;
      if(u.rotation)f.rotation=u.rotation;
    }
  });
  s3dLoadChildren();
  // Save to server
  var toSave=_fixtures.filter(_isFixturePlaced).map(function(f){return{id:f.id,x:f.x,y:f.y,z:f.z||0};});
  ra('POST','/api/layout',{fixtures:toSave});
  document.getElementById('hs').textContent='Undone';
}
// Keyboard shortcuts: R=rotate, M=move, Ctrl+Z=undo
document.addEventListener('keydown',function(e){
  // Global shortcuts (work from any tab, any focus)
  if((e.ctrlKey||e.metaKey)&&(e.key==='s'||e.key==='S')){e.preventDefault();_fmSave();return;}
  if((e.ctrlKey||e.metaKey)&&(e.key==='o'||e.key==='O')){e.preventDefault();_fmOpen();return;}
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT')return;
  if(ctab!=='layout')return;
  if((e.key==='r'||e.key==='R')&&_layTool!=='rotate'){_layToolToggle();}
  else if((e.key==='m'||e.key==='g'||e.key==='M'||e.key==='G')&&_layTool!=='move'){_layToolToggle();}
  else if((e.ctrlKey||e.metaKey)&&e.key==='z'){e.preventDefault();_layUndo();}
  else if(e.key==='Delete'||e.key==='Backspace'){_layDeleteSelected();}
});

// 2D compass gizmo, startAimMode, cvMove, cvUp removed — use 3D gizmo for aiming

function startAimMode(fixtureId){
  // In unified 3D view: select the aim point sphere and drag it with the gizmo
  document.getElementById('hs').textContent='Click the red aim point sphere in the 3D view, then drag the gizmo';
  closeModal();
  // Auto-enable cones so aim point is visible
  if(!_layShowCones)_layConesToggle();
}


// ── Runners ─────────────────────────────────────────────────────────────────
function setBrightness(val){
  val=parseInt(val)||255;
  var bv=document.getElementById('rt-bri-val');if(bv)bv.textContent=Math.round(val*100/255)+'%';
  ra('POST','/api/settings',{globalBrightness:val},null);
}

function saveLoopSetting(){
  var lp=document.getElementById('rt-loop');
  ra('POST','/api/settings',{runnerLoop:lp&&lp.checked?true:false},null);
}

// ── Shows tab ────────────────────────────────────────────────────────────────
function loadShows(){
  loadTimelines();
  // Restore last-opened timeline
  setTimeout(function(){
    var last=localStorage.getItem('slyled-last-timeline');
    if(last){
      var sel=document.getElementById('tl-select');
      if(sel&&!sel.value){sel.value=last;loadTimelineDetail();}
    }
  },300);
}
// Save last-opened timeline when user selects one
var _origLoadTimelineDetail=typeof loadTimelineDetail==='function'?null:null;
function _wrapTlSelect(){
  var sel=document.getElementById('tl-select');
  if(sel&&!sel.dataset.wrapped){
    sel.dataset.wrapped='1';
    sel.addEventListener('change',function(){
      if(sel.value)localStorage.setItem('slyled-last-timeline',sel.value);
    });
  }
}
// Hook into loadTimelines to wrap the select
var _origLoadTimelines=loadTimelines;
loadTimelines=function(){_origLoadTimelines();setTimeout(_wrapTlSelect,400);};

// ── Runtime tab (playlist + playback) ────────────────────────────────────────
var _rtTimer=null;

function loadRuntime(){
  if(_rtTimer){clearInterval(_rtTimer);_rtTimer=null;}
  // Load playlist
  ra('GET','/api/show/playlist',null,function(d){
    if(!d)return;
    _rtRenderPlaylist(d);
    var cb=document.getElementById('rt-loop-all');
    if(cb)cb.checked=!!d.loopAll;
  });
  // Start polling show status + fixture grid
  _rtRefresh();
  _rtTimer=setInterval(_rtRefresh,1000);
}

function _rtRenderPlaylist(d){
  var el=document.getElementById('rt-playlist');
  var totalEl=document.getElementById('rt-playlist-total');
  if(!el)return;
  var items=d.items||[];
  if(!items.length){
    el.innerHTML='<div style="padding:.6em;color:#888;font-size:.82em">No timelines in playlist. Go to the <a href="#" onclick="showTab(\'shows\');return false" style="color:#22d3ee">Shows tab</a> to create timelines.</div>';
    if(totalEl)totalEl.textContent='';
    return;
  }
  var h='';
  items.forEach(function(it,i){
    var baked=it.baked;
    var playing=it.playing;
    var cls=playing?' style="border-color:#22d3ee;background:#0f1a2e"':'';
    h+='<div class="li"'+cls+' draggable="true" data-tid="'+it.id+'" data-idx="'+i+'" ondragstart="_rtDragStart(event)" ondragover="_rtDragOver(event)" ondrop="_rtDrop(event)">';
    h+='<div style="display:flex;align-items:center;gap:.6em">';
    h+='<span style="color:#475569;cursor:grab;font-size:1.1em" title="Drag to reorder">&#9776;</span>';
    h+='<span style="color:#64748b;font-size:.75em;width:1.5em">'+(i+1)+'.</span>';
    h+='<b style="flex:1">'+escapeHtml(it.name)+'</b>';
    h+='<span style="color:#64748b;font-size:.82em;font-family:monospace">'+_fmtDur(it.durationS)+'</span>';
    h+='<button class="btn" onclick="_rtPlayFrom('+i+')" style="background:#1e3a5f;color:#93c5fd;padding:.2em .5em;font-size:.75em" title="Start show from here">&#9654;</button>';
    if(baked)h+='<span style="color:#4c4;font-size:.8em" title="Baked">&#x2713;</span>';
    else h+='<button class="btn" onclick="_rtBakeOne('+it.id+')" style="background:#4a2080;color:#c4b5fd;padding:.2em .5em;font-size:.75em">Bake</button>';
    if(items.length>1)h+='<span style="cursor:pointer;color:#f66;font-size:1em;padding:0 .2em" onclick="_rtRemoveItem('+i+')" title="Remove from playlist">&times;</span>';
    h+='</div></div>';
  });
  // Add timeline control
  var playlistIds=items.map(function(it){return it.id;});
  h+='<div style="padding:.4em;border-top:1px solid #1e293b;display:flex;gap:.4em;align-items:center">';
  h+='<select id="rt-add-tl" style="font-size:.78em;flex:1;padding:.2em"><option value="">+ Add timeline...</option></select>';
  h+='<button class="btn" onclick="_rtAddTimeline()" style="font-size:.72em;background:#14532d;color:#86efac;padding:.2em .5em">Add</button>';
  h+='</div>';
  el.innerHTML=h;
  // Populate add-timeline dropdown with all timelines (duplicates allowed for repeats)
  ra('GET','/api/timelines',null,function(tls){
    var sel=document.getElementById('rt-add-tl');if(!sel)return;
    (tls||[]).forEach(function(t){
      var o=document.createElement('option');o.value=t.id;
      o.textContent=t.name+' ('+t.durationS+'s)';sel.appendChild(o);
    });
    if(sel.options.length<=1)sel.options[0].textContent='No timelines available';
  });
  if(totalEl)totalEl.textContent='Total: '+_fmtDur(d.totalDurationS||0);
}

function _fmtDur(s){
  var m=Math.floor(s/60),sec=Math.floor(s%60);
  return m+':'+('0'+sec).slice(-2);
}

function _rtRefresh(){
  // Poll show status
  ra('GET','/api/show/status',null,function(d){
    if(!d)return;
    var timeEl=document.getElementById('rt-time');
    var progEl=document.getElementById('rt-prog-fill');
    var nowEl=document.getElementById('rt-now-playing');
    if(d.running){
      if(timeEl)timeEl.textContent=_fmtDur(d.totalElapsed||0)+' / '+_fmtDur(d.totalDurationS||0);
      var pct=d.totalDurationS>0?Math.min(100,Math.round((d.totalElapsed/d.totalDurationS)*100)):0;
      if(progEl)progEl.style.width=pct+'%';
      if(nowEl)nowEl.textContent=d.currentName?('\u25b8 '+d.currentName):'';
    }else{
      if(timeEl)timeEl.textContent='00:00 / 00:00';
      if(progEl)progEl.style.width='0%';
      if(nowEl)nowEl.textContent='';
    }
  });
}

// ── Playlist drag-reorder ──
var _rtDragIdx=-1;
function _rtDragStart(e){_rtDragIdx=parseInt(e.currentTarget.dataset.idx);e.dataTransfer.effectAllowed='move';}
function _rtDragOver(e){e.preventDefault();e.dataTransfer.dropEffect='move';}
function _rtDrop(e){
  e.preventDefault();
  var targetIdx=parseInt(e.currentTarget.dataset.idx);
  if(_rtDragIdx<0||_rtDragIdx===targetIdx)return;
  // Reorder: remove from old position, insert at new
  ra('GET','/api/show/playlist',null,function(d){
    if(!d)return;
    var order=d.order.slice();
    var item=order.splice(_rtDragIdx,1)[0];
    order.splice(targetIdx,0,item);
    ra('POST','/api/show/playlist',{order:order},function(){
      ra('GET','/api/show/playlist',null,function(d2){if(d2)_rtRenderPlaylist(d2);});
    });
  });
}

function _rtRemoveItem(idx){
  ra('GET','/api/show/playlist',null,function(d){
    if(!d||!d.order)return;
    var order=d.order.slice();
    if(order.length<=1)return; // don't allow empty playlist
    order.splice(idx,1);
    ra('POST','/api/show/playlist',{order:order},function(){
      ra('GET','/api/show/playlist',null,function(d2){if(d2)_rtRenderPlaylist(d2);});
    });
  });
}

function _rtAddTimeline(){
  var sel=document.getElementById('rt-add-tl');
  if(!sel||!sel.value)return;
  var tid=parseInt(sel.value);
  ra('GET','/api/show/playlist',null,function(d){
    if(!d)return;
    var order=(d.order||[]).slice();
    if(order.indexOf(tid)===-1)order.push(tid);
    ra('POST','/api/show/playlist',{order:order},function(){
      ra('GET','/api/show/playlist',null,function(d2){if(d2)_rtRenderPlaylist(d2);});
    });
  });
}

function _rtPlayFrom(idx){
  _rtBakeAll(function(){
    ra('POST','/api/show/start',{startIndex:idx},function(r){
      if(!r||!r.ok){alert(r&&r.err||'Failed to start');return;}
      document.getElementById('hs').textContent='Show started from item '+(idx+1);
    });
  });
}

function _rtLoopToggle(){
  var cb=document.getElementById('rt-loop-all');
  ra('POST','/api/show/playlist',{loopAll:cb?cb.checked:false});
}

function _rtPreviewOne(tid){
  // Preview single timeline in 3D emulator (no live output)
  var sel=document.getElementById('tl-select');
  if(sel)sel.value=tid;
  loadTimelineDetail();
  tlTogglePreview();
}

function _rtPreviewAll(){
  // For now, preview the first timeline in the playlist
  ra('GET','/api/show/playlist',null,function(d){
    if(!d||!d.order||!d.order.length){alert('Playlist is empty. Add timelines on the Shows tab.');return;}
    _rtPreviewOne(d.order[0]);
  });
}

function _rtPause(){tlPause();}
function _rtRewind(){tlRewind();}

function _rtBakeOne(tid){
  ra('POST','/api/timelines/'+tid+'/bake',{},function(r){
    if(!r)return;
    // Poll bake status
    var poll=setInterval(function(){
      ra('GET','/api/timelines/'+tid+'/baked/status',null,function(s){
        if(!s||s.status==='running')return;
        clearInterval(poll);
        // Refresh playlist to update bake indicator
        ra('GET','/api/show/playlist',null,function(d){if(d)_rtRenderPlaylist(d);});
      });
    },500);
  });
}

function _rtBakeAll(onDone){
  ra('GET','/api/show/playlist',null,function(d){
    if(!d||!d.order||!d.order.length){alert('Playlist is empty.');return;}
    var unbaked=d.items.filter(function(it){return !it.baked;});
    if(!unbaked.length){
      document.getElementById('hs').textContent='All timelines already baked';
      if(onDone)onDone();
      return;
    }
    document.getElementById('hs').textContent='Baking '+unbaked.length+' timeline(s)...';
    var idx=0;
    function bakeNext(){
      if(idx>=unbaked.length){
        document.getElementById('hs').textContent='All timelines baked';
        ra('GET','/api/show/playlist',null,function(d2){if(d2)_rtRenderPlaylist(d2);});
        if(onDone)onDone();
        return;
      }
      var tid=unbaked[idx].id;
      ra('POST','/api/timelines/'+tid+'/bake',{},function(){
        var poll=setInterval(function(){
          ra('GET','/api/timelines/'+tid+'/baked/status',null,function(s){
            if(!s||s.status==='running')return;
            clearInterval(poll);idx++;bakeNext();
          });
        },500);
      });
    }
    bakeNext();
  });
}

function _rtStartShow(){
  // Auto-bake unbaked timelines, then start
  _rtBakeAll(function(){
    ra('POST','/api/show/start',{},function(r){
      if(!r||!r.ok){
        alert(r&&r.err||'Failed to start show');
        return;
      }
      document.getElementById('hs').textContent='Show started — '+r.timelines+' timeline(s)';
    });
  });
}

function _rtStopShow(){
  ra('POST','/api/show/stop',{},function(r){
    if(r&&r.ok)document.getElementById('hs').textContent='Show stopped';
  });
  tlStop();
}

// ── Firmware management ──────────────────────────────────────────────────────
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
      // Refresh USB section — registry was updated by download
      _fetchGithubFirmware();
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
  var h='<p style="font-size:.82em;color:#aaa;margin-bottom:.5em">Latest: v'+escapeHtml(d.latest)+'</p>';
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
      st='<span class="badge" style="background:#f60;color:#fff">v'+escapeHtml(d.latest)+' available</span>';
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
  // Full firmware tab load — OTA + USB + WiFi + Camera Setup
  checkOtaUpdates();
  _fetchGithubFirmware();
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
var _fwGithubRelease=null;
function updateFwImages(){
  var board=document.getElementById('fw-board').value;
  var dfuNote=document.getElementById('fw-dfu-note');
  if(dfuNote)dfuNote.style.display=board==='giga'?'block':'none';
  var sel=document.getElementById('fw-image');sel.innerHTML='';
  // Add local firmware from registry
  _fwRegistry.forEach(function(f){
    if(f.board===board){
      var o=document.createElement('option');o.value=f.id;o.text=f.name+' v'+f.version+' (local)';sel.add(o);
    }
  });
  // Add latest from GitHub if available and newer
  if(_fwGithubRelease){
    var assetMap={'esp32':'esp32-firmware-merged.bin','d1mini':'d1mini-firmware.bin','esp32s3':'esp32s3-firmware-merged.bin'};
    var assetName=assetMap[board];
    if(assetName){
      var found=false;
      _fwGithubRelease.assets.forEach(function(a){if(a.name===assetName)found=true;});
      if(found){
        var o=document.createElement('option');
        o.value='github:'+board;
        o.text='Latest v'+_fwGithubRelease.version+' (download from cloud)';
        sel.add(o);
        // Select GitHub option if it's newer than local
        var localVer='0.0';
        _fwRegistry.forEach(function(f){if(f.board===board)localVer=f.version;});
        if(_cmpVer(_fwGithubRelease.version,localVer)>0)sel.value='github:'+board;
      }
    }
  }
  if(!sel.options.length){
    var o=document.createElement('option');o.value='';o.text='No firmware available for '+board;sel.add(o);
  }
}
// Fetch GitHub release info on firmware tab load
function _fetchGithubFirmware(){
  api('GET','/api/firmware/latest').then(function(d){
    _fwGithubRelease=d;
    updateFwImages();
  }).catch(function(){});
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

// ── Camera Setup (Firmware tab) ──────────────────────────────────────────
function _loadCamSsh(){
  ra('GET','/api/cameras/ssh',null,function(d){
    if(!d)return;
    var u=document.getElementById('cam-ssh-user');
    if(u)u.value=d.sshUser||'root';
    var pw=document.getElementById('cam-ssh-pass');
    if(pw)pw.placeholder=d.hasPassword?'(saved)':'(not set)';
    var pst=document.getElementById('cam-ssh-pw-status');
    if(pst){
      pst.textContent=d.hasPassword?'\u2705 Password saved':'';
      pst.style.color='#4c4';
    }
    var k=document.getElementById('cam-ssh-key');
    if(k)k.value=d.sshKeyPath||'';
    var kst=document.getElementById('cam-ssh-key-status');
    if(kst){
      if(d.hasKey){kst.textContent='\u2705 Key file found';kst.style.color='#4c4';}
      else if(d.sshKeyPath){kst.textContent='\u26a0 Key file not found';kst.style.color='#c66';}
      else{kst.textContent='';}
    }
  });
}
function _saveCamSsh(){
  var body={sshUser:document.getElementById('cam-ssh-user').value.trim()};
  var pw=document.getElementById('cam-ssh-pass').value;
  if(pw)body.sshPassword=pw;
  var key=document.getElementById('cam-ssh-key').value.trim();
  body.sshKeyPath=key;
  var keyContent=document.getElementById('cam-ssh-key-content').value.trim();
  if(keyContent)body.sshKeyContent=keyContent;
  ra('POST','/api/cameras/ssh',body,function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='SSH credentials saved';
      document.getElementById('cam-ssh-pass').value='';
      document.getElementById('cam-ssh-key-content').value='';
      _loadCamSsh();
    }else{
      document.getElementById('hs').textContent='Save failed: '+(r&&r.err||'unknown');
    }
  });
}
function _camGenKey(){
  ra('POST','/api/cameras/ssh/generate-key',{},function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='SSH key pair generated';
      var el=document.getElementById('cam-ssh-pubkey');
      var txt=document.getElementById('cam-ssh-pubkey-text');
      if(el&&txt){el.style.display='block';txt.value=r.publicKey||'';}
      _loadCamSsh();
    }else{
      document.getElementById('hs').textContent='Key generation failed: '+(r&&r.err||'unknown');
    }
  });
}
function _copyPubKey(){
  var txt=document.getElementById('cam-ssh-pubkey-text');
  if(txt){txt.select();document.execCommand('copy');document.getElementById('hs').textContent='Public key copied to clipboard';}
}
function _camFwRefresh(){
  ra('GET','/api/cameras',null,function(cams){
    var el=document.getElementById('cam-fw-list');
    if(!el)return;
    if(!cams||!cams.length){
      el.innerHTML='<p style="color:#888;font-size:.82em">No cameras registered. Add cameras from the Setup tab.</p>';
      return;
    }
    // Group by IP (hardware node), not per sensor
    var nodes={};
    cams.forEach(function(c){
      var ip=c.cameraIp||'';
      if(!nodes[ip])nodes[ip]={ip:ip,name:ip,fid:c.id,ver:'',online:false,sensors:0};
      nodes[ip].sensors++;
      if(c.fwVersion)nodes[ip].ver=c.fwVersion;
      if(c.online)nodes[ip].online=true;
      if(c.hostname&&c.hostname!==ip)nodes[ip].name=c.hostname;
    });
    var h='<table class="tbl" style="font-size:.85em"><tr><th>Node</th><th>IP</th><th>Sensors</th><th>Version</th><th>Status</th><th></th></tr>';
    Object.values(nodes).forEach(function(n){
      var st=n.online?'<span class="badge bon">Online</span>':'<span class="badge boff">Offline</span>';
      var sshBtn='<button class="btn" onclick="_camSshModal(\''+n.ip+'\','+n.fid+')" style="background:#446;color:#fff;font-size:.78em;padding:.2em .5em">SSH</button>';
      var acts=sshBtn;
      if(n.online&&n.ip){
        acts+=' <button class="btn" onclick="_camDeploy(\''+n.ip+'\')" id="cam-upg-'+n.ip.replace(/\./g,'-')+'" style="background:#475569;color:#fff;font-size:.78em;padding:.2em .5em">Upgrade</button>'
          +' <button class="btn" onclick="_camDeploy(\''+n.ip+'\',true)" style="background:#334155;color:#94a3b8;font-size:.78em;padding:.2em .5em">Force</button>';
      }
      h+='<tr><td><b>'+escapeHtml(n.name)+'</b></td><td>'+escapeHtml(n.ip)+'</td><td>'+n.sensors+'</td><td>'+(n.ver||'\u2014')+'</td><td>'+st+'</td><td>'+acts+'</td></tr>';
    });
    el.innerHTML=h+'</table>';
  });
}
function _camGhCheck(){
  var btn=document.getElementById('cam-gh-btn');
  var el=document.getElementById('cam-gh-status');
  if(btn){btn.disabled=true;btn.textContent='Checking...';}
  ra('GET','/api/firmware/camera/check',null,function(d){
    if(btn){btn.disabled=false;btn.textContent='\u2601 Check GitHub';}
    if(!el)return;
    if(!d||d.err){el.innerHTML='<p style="color:#e44;font-size:.82em">'+(d&&d.err||'Check failed')+'</p>';return;}
    var loc=d.localVersion||'?';
    var dl=d.downloadedVersion;
    var lat=d.latestVersion||'?';
    var h='<div style="font-size:.82em;padding:.4em;background:#1e1b4b;border:1px solid #4c1d95;border-radius:4px;margin:.4em 0">';
    h+='<span style="color:#a78bfa">Bundled:</span> v'+escapeHtml(loc);
    if(dl)h+=' &nbsp; <span style="color:#34d399">Downloaded:</span> v'+escapeHtml(dl);
    h+=' &nbsp; <span style="color:#fbbf24">GitHub:</span> v'+escapeHtml(lat);
    if(d.updateAvailable){
      h+=' &nbsp; <button class="btn" onclick="_camGhDownload()" id="cam-gh-dl" style="padding:.1em .5em;background:#059669;color:#fff;font-size:.9em">Download v'+escapeHtml(lat)+'</button>';
    }else{
      h+=' &nbsp; <span style="color:#4ade80">Up to date</span>';
    }
    el.innerHTML=h+'</div>';
  });
}
function _camGhDownload(){
  var btn=document.getElementById('cam-gh-dl');
  if(btn){btn.disabled=true;btn.textContent='Downloading...';}
  ra('POST','/api/firmware/camera/download',{},function(d){
    var el=document.getElementById('cam-gh-status');
    if(!d||!d.ok){
      if(el)el.innerHTML='<p style="color:#e44;font-size:.82em">Download failed</p>';
      return;
    }
    var h='<div style="font-size:.82em;padding:.4em;background:#052e16;border:1px solid #059669;border-radius:4px;margin:.4em 0">';
    h+='<span style="color:#4ade80">Downloaded v'+(d.version||'?')+' &mdash; '+d.files.length+' files</span>';
    if(d.warnings&&d.warnings.length)h+='<br><span style="color:#fbbf24">Warnings: '+d.warnings.join(', ')+'</span>';
    h+='</div>';
    if(el)el.innerHTML=h;
    _camFwRefresh();
  });
}
function _camScanBoards(){
  var btn=document.getElementById('cam-scan-btn');
  if(btn){btn.disabled=true;btn.textContent='Scanning...';}
  ra('GET','/api/cameras/scan-network',null,function(){
    var poll=setInterval(function(){
      ra('GET','/api/cameras/scan-network/results',null,function(d){
        if(d&&d.pending)return;
        clearInterval(poll);
        if(btn){btn.disabled=false;btn.textContent='Scan for SBC Boards';}
        var el=document.getElementById('cam-scan-results');
        if(!el)return;
        if(!d||!d.length){
          el.innerHTML='<p style="color:#888;font-size:.82em;padding:.3em 0">No SSH-accessible boards found.</p>';
          return;
        }
        var h='<table class="tbl" style="font-size:.85em"><tr><th>IP</th><th>Hostname</th><th>Status</th><th></th></tr>';
        d.forEach(function(dev){
          var st,act,depId='cam-dep-'+dev.ip.replace(/\./g,'-');
          if(dev.hasCamera){
            st='<span class="badge bon">Camera v'+(dev.fwVersion||'?')+'</span>';
            act='<button class="btn" onclick="_camDeploy(\''+dev.ip+'\')" id="'+depId+'" style="background:#475569;color:#fff;font-size:.78em;padding:.2em .5em">Upgrade</button>'
              +' <button class="btn" onclick="addDiscoveredCamera(\''+dev.ip+'\',\''+escapeHtml(dev.hostname||'Camera').replace(/'/g,"\\'")+'\')" style="background:#0e7490;color:#fff;font-size:.78em;padding:.2em .5em">Register</button>';
          }else{
            st='<span class="badge boff">No camera software</span>';
            act='<button class="btn" onclick="_camDeploy(\''+dev.ip+'\')" id="'+depId+'" style="background:#059669;color:#fff;font-size:.78em;padding:.2em .5em">Install</button>';
          }
          h+='<tr><td>'+escapeHtml(dev.ip)+'</td><td>'+escapeHtml(dev.hostname||'\u2014')+'</td><td>'+st+'</td><td>'+act+'</td></tr>';
        });
        el.innerHTML=h+'</table>';
      });
    },500);
  });
}
function _camSshModal(ip,fid){
  var _csshIp=ip;
  document.getElementById('modal-title').textContent='SSH Configuration \u2014 '+ip;
  var h='<div style="min-width:420px">';
  h+='<div style="margin-bottom:.6em"><label style="font-size:.82em;color:#94a3b8">Authentication Type</label><br>';
  h+='<label style="font-size:.85em;margin-right:1em"><input type="radio" name="cssh-auth" value="password" checked onchange="_csshToggle()"> Password</label>';
  h+='<label style="font-size:.85em"><input type="radio" name="cssh-auth" value="key" onchange="_csshToggle()"> SSH Key</label>';
  h+='</div>';
  h+='<div id="cssh-pw-section">';
  h+='<label style="font-size:.82em">Username</label>';
  h+='<input id="cssh-user" value="root" style="width:100%;margin-bottom:.3em">';
  h+='<label style="font-size:.82em">Password</label>';
  h+='<div style="display:flex;gap:.3em;margin-bottom:.3em"><input id="cssh-pass" type="password" placeholder="Enter password" style="flex:1">';
  h+='<button type="button" class="btn" style="padding:.2em .4em;background:#333;font-size:.75em" onmousedown="document.getElementById(\'cssh-pass\').type=\'text\'" onmouseup="document.getElementById(\'cssh-pass\').type=\'password\'" onmouseleave="document.getElementById(\'cssh-pass\').type=\'password\'">&#128065;</button></div>';
  h+='</div>';
  h+='<div id="cssh-key-section" style="display:none">';
  h+='<label style="font-size:.82em">Username</label>';
  h+='<input id="cssh-key-user" value="root" style="width:100%;margin-bottom:.3em">';
  h+='<label style="font-size:.82em">Key File Path</label>';
  h+='<input id="cssh-keypath" placeholder="~/.ssh/id_ed25519" style="width:100%;margin-bottom:.3em">';
  h+='<label style="font-size:.82em">Or paste key content</label>';
  h+='<textarea id="cssh-keycontent" rows="4" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" style="width:100%;font-size:.75em;font-family:monospace;margin-bottom:.3em"></textarea>';
  h+='</div>';
  h+='<div style="display:flex;gap:.5em;margin-top:.6em">';
  h+='<button class="btn btn-on" onclick="_csshSave(\''+ip+'\')">Save</button>';
  h+='<button class="btn" onclick="_csshTest(\''+ip+'\')" style="background:#1e3a5f;color:#93c5fd">Test Connection</button>';
  h+='</div>';
  h+='<div id="cssh-result" style="margin-top:.5em;font-size:.85em"></div>';
  h+='</div>';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='flex';
  // Load existing config by IP
  ra('GET','/api/cameras/node/'+encodeURIComponent(ip)+'/ssh',null,function(d){
    if(!d||!d.configured)return;
    if(d.authType==='key'){
      document.querySelector('input[name="cssh-auth"][value="key"]').checked=true;
      _csshToggle();
      var ku=document.getElementById('cssh-key-user');if(ku)ku.value=d.user||'root';
      var kp=document.getElementById('cssh-keypath');if(kp)kp.value=d.keyPath||'';
    }else{
      var u=document.getElementById('cssh-user');if(u)u.value=d.user||'root';
    }
  });
}
function _csshToggle(){
  var isKey=document.querySelector('input[name="cssh-auth"][value="key"]').checked;
  document.getElementById('cssh-pw-section').style.display=isKey?'none':'';
  document.getElementById('cssh-key-section').style.display=isKey?'':'none';
}
function _csshSave(ip){
  var isKey=document.querySelector('input[name="cssh-auth"][value="key"]').checked;
  var body={authType:isKey?'key':'password'};
  if(isKey){
    body.user=document.getElementById('cssh-key-user').value||'root';
    body.keyPath=document.getElementById('cssh-keypath').value;
    var kc=document.getElementById('cssh-keycontent').value;
    if(kc)body.keyContent=kc;
  }else{
    body.user=document.getElementById('cssh-user').value||'root';
    body.password=document.getElementById('cssh-pass').value;
  }
  ra('POST','/api/cameras/node/'+encodeURIComponent(ip)+'/ssh',body,function(r){
    var el=document.getElementById('cssh-result');
    if(r&&r.ok){el.innerHTML='<span style="color:#4c4">&#x2713; Saved for '+escapeHtml(ip)+'</span>';}
    else{el.innerHTML='<span style="color:#f66">Save failed: '+(r&&r.err||'unknown')+'</span>';}
  });
}
function _csshTest(ip){
  var el=document.getElementById('cssh-result');
  el.innerHTML='<span style="color:#94a3b8">Testing connection to '+escapeHtml(ip)+'...</span>';
  ra('POST','/api/cameras/node/'+encodeURIComponent(ip)+'/ssh/test',{},function(r){
    if(!r){el.innerHTML='<span style="color:#f66">Request failed</span>';return;}
    if(r.ok){
      el.innerHTML='<span style="color:#4c4">&#x2713; '+escapeHtml(r.msg)+'</span>';
    }else{
      el.innerHTML='<span style="color:#f66">&#x2717; '+escapeHtml(r.err)+'</span>'
        +(r.guidance?'<br><span style="color:#94a3b8;font-size:.85em">'+escapeHtml(r.guidance)+'</span>':'');
    }
  });
}

function _camDeploy(ip,force){
  document.getElementById('modal-title').textContent=(force?'Force reinstall':'Deploy')+' \u2014 '+ip;
  document.getElementById('modal-body').innerHTML=
    '<div style="min-width:380px">'
    +'<div id="cam-deploy-ver" style="font-size:.82em;color:#94a3b8;margin-bottom:.5em"></div>'
    +'<div class="prog-bar" style="height:12px;margin-bottom:.5em"><div class="prog-fill" id="cam-deploy-fill" style="width:0%;transition:width .3s"></div></div>'
    +'<div id="cam-deploy-step" style="font-size:.82em;color:#64748b;margin-bottom:.2em"></div>'
    +'<div id="cam-deploy-msg" style="font-size:.85em;color:#94a3b8;margin-bottom:.4em">Connecting...</div>'
    +'<div id="cam-deploy-log" style="max-height:200px;overflow-y:auto;font-family:monospace;font-size:.75em;background:#0f172a;border:1px solid #334155;border-radius:4px;padding:.4em;color:#64748b"></div>'
    +'<div id="cam-deploy-actions" style="margin-top:.6em;display:none"></div>'
    +'</div>';
  document.getElementById('modal').style.display='block';

  // Map progress ranges to human-readable step names
  var steps=[
    [0,4,'Checking version'],[5,9,'SSH connect'],[10,24,'Pre-flight checks'],
    [25,29,'Creating directories'],[30,39,'Uploading firmware'],
    [40,49,'Installing system packages'],[50,59,'Installing Python dependencies'],
    [60,69,'Setting up detection model'],[70,79,'Configuring service'],
    [80,89,'Starting server'],[90,99,'Verifying'],[100,100,'Done']
  ];
  function stepLabel(pct){
    for(var i=0;i<steps.length;i++){if(pct>=steps[i][0]&&pct<=steps[i][1])return steps[i][2];}
    return '';
  }

  ra('POST','/api/cameras/deploy',{ip:ip,force:!!force},function(r){
    if(r&&r.err){
      document.getElementById('cam-deploy-msg').innerHTML='<span style="color:#f66">'+escapeHtml(r.err)+'</span>';
      _camDeployLog('Error: '+r.err);
      _camDeployShowActions(ip,true);
      return;
    }
    _camDeployLog('Deploy started for '+ip+(force?' (forced)':'')+'...');
    var lastMsg='',lastStep='';
    var poll=setInterval(function(){
      ra('GET','/api/cameras/deploy/status',null,function(s){
        var fill=document.getElementById('cam-deploy-fill');
        var msg=document.getElementById('cam-deploy-msg');
        var stepEl=document.getElementById('cam-deploy-step');
        var verEl=document.getElementById('cam-deploy-ver');
        if(!fill||!msg){clearInterval(poll);return;}
        fill.style.width=s.progress+'%';

        // Show version comparison when available
        if(verEl&&(s.localVersion||s.remoteVersion)){
          var vt='';
          if(s.remoteVersion&&s.localVersion&&s.remoteVersion!==s.localVersion)
            vt='Upgrade: v'+s.remoteVersion+' \u2192 v'+s.localVersion;
          else if(s.remoteVersion&&s.localVersion&&s.remoteVersion===s.localVersion)
            vt='Reinstall: v'+s.localVersion;
          else if(s.localVersion&&!s.remoteVersion)
            vt='Fresh install: v'+s.localVersion;
          if(vt)verEl.textContent=vt;
        }

        // Show current step
        var sl=stepLabel(s.progress);
        if(stepEl&&sl&&sl!==lastStep){
          lastStep=sl;
          stepEl.textContent='Step: '+sl;
        }

        if(s.error){
          msg.innerHTML='<span style="color:#ef4444">\u2718 '+escapeHtml(s.error)+'</span>';
          fill.style.background='#ef4444';
          if(stepEl)stepEl.style.color='#ef4444';
          _camDeployLog('ERROR: '+s.error);
          _camDeployShowActions(ip,true);
          clearInterval(poll);return;
        }
        if(s.message!==lastMsg){
          lastMsg=s.message;
          msg.textContent=s.message;
          _camDeployLog('['+s.progress+'%] '+s.message);
        }
        if(!s.running){
          clearInterval(poll);
          if(s.progress>=95){
            msg.innerHTML='<span style="color:#4ade80">\u2713 '+escapeHtml(s.message)+'</span>';
            fill.style.background='#059669';
            if(stepEl)stepEl.textContent='';
            _camDeployLog('Complete!');
            _camDeployShowActions(ip,false);
            setTimeout(function(){_camFwRefresh();},2000);
          }
        }
      });
    },800);
  });
}
function _camDeployShowActions(ip,isError){
  var el=document.getElementById('cam-deploy-actions');
  if(!el)return;
  el.style.display='flex';
  el.style.gap='.4em';
  el.style.flexWrap='wrap';
  if(isError){
    el.innerHTML=
      '<button class="btn" onclick="_camDeploy(\''+ip+'\',false)" style="background:#0e7490;color:#fff;font-size:.82em;padding:.3em .8em">Retry</button>'
      +'<button class="btn" onclick="_camDeploy(\''+ip+'\',true)" style="background:#475569;color:#e2e8f0;font-size:.82em;padding:.3em .8em">Force Reinstall</button>'
      +'<button class="btn" onclick="closeModal()" style="background:#334155;color:#94a3b8;font-size:.82em;padding:.3em .8em">Close</button>';
  }else{
    el.innerHTML=
      '<button class="btn" onclick="_camDeploy(\''+ip+'\',true)" style="background:#475569;color:#e2e8f0;font-size:.82em;padding:.3em .8em">Force Reinstall</button>'
      +'<button class="btn" onclick="closeModal()" style="background:#334155;color:#94a3b8;font-size:.82em;padding:.3em .8em">Close</button>';
  }
}
function _camDeployLog(text){
  var el=document.getElementById('cam-deploy-log');
  if(!el)return;
  el.textContent+=(el.textContent?'\n':'')+text;
  el.scrollTop=el.scrollHeight;
}

var _flashPoll=null;
function doFlash(btn){
  var port=document.getElementById('fw-port').value;
  var board=document.getElementById('fw-board').value;
  var fwId=document.getElementById('fw-image').value;
  if(!port||!fwId){alert('Select a port and firmware');return;}
  // WiFi must be configured before flashing
  var ssid=document.getElementById('fw-ssid').value;
  if(!ssid){alert('WiFi credentials must be configured before flashing.\nEnter SSID and password above, then click Save WiFi.');return;}
  _btnSaving(btn);
  document.getElementById('fw-progress').style.display='block';
  // If GitHub firmware selected, download first then flash
  if(fwId.indexOf('github:')===0){
    document.getElementById('fw-prog-msg').textContent='Downloading latest from cloud...';
    document.getElementById('fw-prog-fill').style.width='10%';
    api('POST','/api/firmware/download',{board:board}).then(function(r){
      document.getElementById('fw-prog-msg').textContent='Downloaded v'+r.version+' — flashing...';
      document.getElementById('fw-prog-fill').style.width='20%';
      // Find the local registry ID for this board
      var localId='';
      _fwRegistry.forEach(function(f){if(f.board===board)localId=f.id;});
      if(!localId){_btnSaved(btn,false);document.getElementById('fw-prog-msg').textContent='No local firmware ID found';return;}
      // Reload registry then flash
      ra('GET','/api/firmware/registry',null,function(reg){
        _fwRegistry=reg&&reg.firmware?reg.firmware:_fwRegistry;
        _doFlashLocal(btn,port,board,localId);
      });
    }).catch(function(e){
      _btnSaved(btn,false);
      document.getElementById('fw-prog-msg').textContent='Download failed: '+e.message;
    });
    return;
  }
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
    document.getElementById('dmx-status').textContent=r&&r.ok?'Blinking...':'Blink failed — '+(r&&r.err||'engine not running');
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


// ── #143: Visual Capability Editor ──────────────────────────────────────
// Enhanced _peRenderCaps with visual range bars
// (Original renders a text table — this adds colored bars)


// Init
ra('GET','/status',null,function(d){
  if(d&&d.version){
    var fv=document.getElementById('fv');
    if(fv)fv.textContent='SlyLED v'+d.version+' \u2014 The Orchestrator';
  }
});
var _qTab=new URLSearchParams(location.search).get('tab')||location.hash.replace('#','')||'dash';
showTab(_qTab);
ra('GET','/api/settings',null,function(d){
  applyDarkMode(!d||d.darkMode!==0);
});
ra('GET','/api/project/name',null,function(d){if(d&&d.name)_projUpdateName(d.name);});
