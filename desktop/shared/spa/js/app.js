// ── Localization string table (swap for i18n) ─────────────────────────────
function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

// #600 — single source of truth for the rotation array index → axis
// semantic mapping. Mirror of desktop/shared/camera_math.py
// rotation_from_layout. Layout convention is [rx pitch, ry roll, rz yaw].
function rotationFromLayout(rot){
  if(!rot||!rot.length)return{tilt:0,pan:0,roll:0};
  return{
    tilt:+(rot[0]||0),
    roll:+(rot[1]||0),
    pan:+(rot[2]||0)
  };
}
function rotationToLayout(tilt,pan,roll){
  return[+(tilt||0),+(roll||0),+(pan||0)];
}
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
// Apply tooltips to dynamic content via MutationObserver (#434)
// Scoped to modal container to avoid firing on every DOM change (DMX monitor, 3D, live grid)
var _tipObs=new MutationObserver(function(muts){muts.forEach(function(m){
  m.addedNodes.forEach(function(n){if(n.querySelectorAll)_applyTips(n);});
});});
document.addEventListener('DOMContentLoaded',function(){
  _applyTips();
  var modalEl=document.getElementById('modal');
  if(modalEl)_tipObs.observe(modalEl,{childList:true,subtree:true});
  // Also observe tab content area for tab switches
  var appEl=document.getElementById('app');
  if(appEl)_tipObs.observe(appEl,{childList:true,subtree:false});
  // Toast container — stacks in bottom-right, never blocks interaction.
  if(!document.getElementById('toast-stack')){
    var ts=document.createElement('div');
    ts.id='toast-stack';
    ts.style.cssText='position:fixed;right:16px;bottom:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:380px;pointer-events:none';
    document.body.appendChild(ts);
  }
});

// ── Toast notification system (#298) ─────────────────────────────────────
// toast(msg, level, opts) — bottom-right stacking notifications.
//   level: 'info' (cyan) | 'success' (green) | 'warn' (amber) | 'error' (red)
//   opts:  { timeout: ms, persistent: bool }  — default 5s info, 10s error.
// Replaces alert() for non-blocking feedback and the transient `#hs`
// status bar for important events the operator needs to actually see.
var _TOAST_COLORS={
  info:   {bg:'#0e7490',border:'#22d3ee',dot:'#22d3ee'},
  success:{bg:'#065f46',border:'#22c55e',dot:'#22c55e'},
  warn:   {bg:'#78350f',border:'#f59e0b',dot:'#f59e0b'},
  error:  {bg:'#7f1d1d',border:'#ef4444',dot:'#ef4444'},
};
function toast(msg,level,opts){
  level=level||'info';opts=opts||{};
  var col=_TOAST_COLORS[level]||_TOAST_COLORS.info;
  var timeout=opts.timeout;
  if(timeout==null)timeout=(level==='error'||level==='warn')?10000:5000;
  if(opts.persistent)timeout=0;
  var stack=document.getElementById('toast-stack');
  if(!stack)return;
  var t=document.createElement('div');
  t.style.cssText='background:'+col.bg+';color:#f1f5f9;border:1px solid '+col.border
    +';border-radius:6px;padding:10px 14px;font-size:.85em;box-shadow:0 6px 18px rgba(0,0,0,.4);'
    +'pointer-events:auto;display:flex;align-items:flex-start;gap:10px;'
    +'animation:toast-in .18s ease-out';
  var dot='<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:'
    +col.dot+';margin-top:7px;flex-shrink:0"></span>';
  var text='<span style="flex:1">'+String(msg).replace(/</g,'&lt;')+'</span>';
  var closeBtn='<span class="toast-x" style="opacity:.5;font-size:1.1em;line-height:1;cursor:pointer;padding:0 4px">&times;</span>';
  t.innerHTML=dot+text+closeBtn;
  var timer=null;
  function dismiss(){
    if(timer){clearTimeout(timer);timer=null;}
    t.style.transition='opacity .2s, transform .2s';
    t.style.opacity='0';t.style.transform='translateX(16px)';
    setTimeout(function(){if(t.parentNode)t.parentNode.removeChild(t);},220);
  }
  if(timeout>0)timer=setTimeout(dismiss,timeout);
  // Only the × glyph dismisses — clicking the body might be a stray click
  // on an overlapping element (Chromium fires click on the topmost), which
  // would otherwise silently discard a warning the operator hasn't read.
  var x=t.querySelector('.toast-x');
  if(x)x.addEventListener('click',function(ev){ev.stopPropagation();dismiss();});
  stack.appendChild(t);
  return dismiss;
}
// Shorthand entry points — readable at call sites and trivial to grep for.
function toastInfo(m,o){return toast(m,'info',o);}
function toastSuccess(m,o){return toast(m,'success',o);}
function toastWarn(m,o){return toast(m,'warn',o);}
function toastError(m,o){return toast(m,'error',o);}
// Keyframes for the slide-in animation — appended once.
(function(){
  if(document.getElementById('toast-style'))return;
  var s=document.createElement('style');s.id='toast-style';
  s.textContent='@keyframes toast-in{from{opacity:0;transform:translateX(16px)}to{opacity:1;transform:translateX(0)}}';
  document.head.appendChild(s);
})();

var ctab='dash',ld=null,phW=10000,phH=5000,drag=null,dox=0,doy=0,units=0,_cvW=900,_cvH=450,_dragStartX=0,_dragStartY=0,_dragMoved=false;
var _layTool='move'; // 'move' or 'rotate'
var _undoStack=[];   // [{fid, x, y, z, rotation}] — legacy Layout-only positional undo

// #297 — global command stack for cross-tab undo/redo. Entries are
// {name, undo, redo} records; undo/redo are nullary fns that restore
// state and re-issue the server calls. Layout drag/rotate still uses
// the legacy _undoStack above — the two coexist so this change is
// scoped to action CRUD (the highest-pain regression target).
var _cmdStack=[], _cmdRedo=[], _CMD_MAX=50;
function cmdPush(name,undoFn,redoFn){
  _cmdStack.push({name:name,undo:undoFn,redo:redoFn});
  if(_cmdStack.length>_CMD_MAX)_cmdStack.shift();
  _cmdRedo.length=0;  // any new command invalidates the redo stack
}
function cmdUndo(){
  if(!_cmdStack.length)return false;
  var c=_cmdStack.pop();
  try{c.undo();}catch(e){console.error('undo failed',e);}
  _cmdRedo.push(c);
  if(typeof toastInfo==='function')toastInfo('Undo: '+c.name);
  return true;
}
function cmdRedo(){
  if(!_cmdRedo.length)return false;
  var c=_cmdRedo.pop();
  try{c.redo();}catch(e){console.error('redo failed',e);}
  _cmdStack.push(c);
  if(typeof toastInfo==='function')toastInfo('Redo: '+c.name);
  return true;
}
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
// #690-followup — warn before navigating away (tab close, refresh) when
// any dirty form is open. Currently covers the profile editor and the
// per-node SSH config modal; extend the disjunction as more dirty-tracked
// modals land. Browsers ignore the message string for security reasons
// (Chrome/Edge/Firefox all show their own generic "leave site" prompt) —
// returning a non-empty string is what triggers the dialog.
window.addEventListener('beforeunload', function(e){
  var dirty = (typeof _peDirty !== 'undefined' && _peDirty)
           || (typeof _csshDirty !== 'undefined' && _csshDirty);
  if(dirty){
    e.preventDefault();
    e.returnValue = '';   // required by Chrome
    return '';
  }
});

function showTab(t){
  var liveTab=(t==='dash'||t==='runtime'||t==='shows');
  // Only detach if going to a non-live tab (layout, setup, etc.)
  if(!liveTab&&_emu3d.activeTab){_emu3dDetach();}
  _clearTabTimers();
  // Stop layout render loop if leaving layout
  if(ctab==='layout'&&t!=='layout'&&_s3d.animId){cancelAnimationFrame(_s3d.animId);_s3d.animId=null;}
  ctab=t;
  // #639 — switch the 3D view context so each tab shows its own saved
  // visibility prefs. Layout authoring, Dashboard monitoring, and
  // Runtime playback want different overlays on the shared scene.
  if(typeof _setViewCtx==='function'){
    if(t==='layout')_setViewCtx('layout');
    else if(t==='dash')_setViewCtx('dash');
    else if(t==='runtime'||t==='shows')_setViewCtx('runtime');
  }
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
      if(!_emuStage)emuLoadStage();
      else{_emuStartTimer();if(!_emu3d.nodes.length)emu3dBuildFixtures();}
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
  // Guard: unsaved profile-editor changes prompt before closing.
  if(typeof _peDirty !== 'undefined' && _peDirty){
    // Only prompt when the top-level modal (not a pushed sub-dialog) would close.
    if(!_modalStack.length){
      if(!confirm('You have unsaved changes to this profile. Close and discard?'))return;
      _peDirty=false;
    }
  }
  // #690-followup — same guard for the per-node SSH config modal: closing
  // without Save means an operator's edits never reach _camera_ssh, and
  // the deploy/test endpoints fall back to saved (or empty) credentials.
  if(typeof _csshDirty !== 'undefined' && _csshDirty){
    if(!_modalStack.length){
      if(!confirm('You have unsaved SSH credentials for this camera node. Close and discard?'))return;
      _csshDirty=false;
    }
  }
  // If there's a parent dialog on the stack, go back to it instead of closing
  if(_popModal())return;
  // Clear any active polling intervals (#430, gyro live status)
  if(window._bakePoll){clearInterval(window._bakePoll);window._bakePoll=null;}
  if(window._gcfgPoll){clearInterval(window._gcfgPoll);window._gcfgPoll=null;}
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
var _layView=(function(){try{var v=localStorage.getItem('slyled-layout-view');return (v==='front'||v==='top'||v==='side'||v==='3d')?v:'3d';}catch(e){return '3d';}})(); // 'front', 'top', 'side', '3d' — persisted per #638

function _isPlaced(c){return c.positioned||c._placed||(c.x>0||c.y>0||c.z>0);}

// ── Phase 7: Help Panel ─────────────────────────────────────────────────────
// The "?" button opens an in-app side panel with contextual help for the
// current tab. The panel has a "More info" link at the bottom that
// opens the full local user manual (`/help`) in a separate window for
// operators who want the full document. When the panel is open, the
// body gets a `help-open` class so the header (File menu etc.) shifts
// left and isn't obscured by the panel.
var _HELP_DEEP_LINKS = {
  dash: 'getting-started',
  setup: 'fixture-setup',
  layout: 'layout',
  actions: 'spatial-effects',
  shows: 'shows',
  runtime: 'timeline',
  settings: 'dmx-profiles',
  firmware: 'firmware',
  cameras: 'cameras'
};

function _helpSectionForTab(){
  var s = ctab;
  if (s === 'actions') s = 'spatial-effects';
  if (s === 'runtime') s = 'timeline';
  return s;
}

function toggleHelp(){
  var panel = document.getElementById('help-panel');
  if (!panel) return;
  if (panel.style.display === 'block'){
    panel.style.display = 'none';
    document.body.classList.remove('help-open');
    return;
  }
  panel.style.display = 'block';
  document.body.classList.add('help-open');
  ra('GET', '/api/help/' + _helpSectionForTab(), null, function(d){
    var body = document.getElementById('help-body');
    var raw = d && d.html ? d.html : '<p style="color:#888">Help content not available.</p>';
    raw = raw.replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '');
    if (body) body.innerHTML = raw;
    // #670 — after help fragment renders, wire glossary hover cards.
    _glossaryHoverWire(body);
  });
}

// ── Glossary hover cards (#670) ────────────────────────────────────────
//
// Any <abbr> (or element with data-term) inside the help panel gets a
// floating card on hover / focus. Terms are matched case-insensitively
// against the structured glossary at /api/glossary. Extends to any DOM
// element the caller passes in — used by both the help panel and the
// full-manual HTML when it's rendered in an iframe.
var _glossaryCache = null;
function _glossaryLoad(cb){
  if (_glossaryCache !== null){ cb(_glossaryCache); return; }
  ra('GET', '/api/glossary', null, function(r){
    _glossaryCache = (r && r.ok && r.entries) ? r.entries : [];
    cb(_glossaryCache);
  });
}
function _glossaryLookup(entries, term){
  if (!term) return null;
  var key = term.toLowerCase();
  for (var i=0;i<entries.length;i++){
    if ((entries[i].term||'').toLowerCase() === key) return entries[i];
  }
  return null;
}
function _glossaryHoverWire(root){
  if (!root) return;
  // Find all glossary terms we can hover: <abbr>, [data-term], or bold
  // occurrences of known glossary keys inside body text.
  _glossaryLoad(function(entries){
    // 1) <abbr> + [data-term] — these already opt-in.
    var targets = root.querySelectorAll('abbr, [data-term]');
    targets.forEach(function(el){
      var term = el.getAttribute('data-term') || el.getAttribute('title') || el.textContent;
      _glossaryAttach(el, term, entries);
    });
    // 2) Auto-decorate <code> and <strong> text that matches a known
    //    glossary term — this way Appendix references light up without
    //    the author needing to wrap each one in <abbr>.
    var lang = (document.documentElement.lang || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en';
    var vocab = {};
    entries.forEach(function(e){ if(e && e.term) vocab[e.term.toLowerCase()] = e; });
    root.querySelectorAll('code, strong').forEach(function(el){
      var txt = (el.textContent||'').trim();
      if (txt && vocab[txt.toLowerCase()] && !el.hasAttribute('data-term')){
        el.setAttribute('data-term', txt);
        _glossaryAttach(el, txt, entries);
      }
    });
  });
}
function _glossaryAttach(el, term, entries){
  var entry = _glossaryLookup(entries, term);
  if (!entry) return;
  el.classList.add('glossary-term');
  el.style.cursor = 'help';
  el.style.borderBottom = '1px dotted #a855f7';
  el.addEventListener('mouseenter', function(){ _glossaryShow(el, entry); });
  el.addEventListener('focus',      function(){ _glossaryShow(el, entry); });
  el.addEventListener('mouseleave', _glossaryHide);
  el.addEventListener('blur',       _glossaryHide);
}
function _glossaryShow(anchor, entry){
  _glossaryHide();
  var card = document.createElement('div');
  card.id = 'glossary-card';
  var lang = (document.documentElement.lang || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en';
  var l = entry[lang] && (entry[lang].long || entry[lang].short) ? entry[lang] : entry.en;
  var acronym = entry.acronym && l && l.short ? ('<div style="font-size:.75em;color:#94a3b8;margin-bottom:.2em">'+escapeHtml(l.short)+'</div>') : '';
  var body = l && l.long ? escapeHtml(l.long) : '';
  var see = (entry.see_also||[]).map(function(s){return escapeHtml(s);}).join(' · ');
  card.innerHTML =
    '<div style="font-weight:600;color:#e2e8f0;margin-bottom:.25em">'+escapeHtml(entry.term)+'</div>'
    + acronym
    + '<div style="color:#cbd5e1">'+body+'</div>'
    + (see ? '<div style="color:#64748b;font-size:.75em;margin-top:.4em">See also: '+see+'</div>' : '');
  card.style.cssText = 'position:fixed;z-index:300;max-width:320px;padding:.6em .8em;'
    + 'background:#0b1020;border:1px solid #334155;border-left:3px solid #a855f7;'
    + 'border-radius:6px;box-shadow:0 8px 24px rgba(0,0,0,.5);'
    + 'font-size:.82em;line-height:1.45;pointer-events:none';
  document.body.appendChild(card);
  var r = anchor.getBoundingClientRect();
  var top = r.bottom + 6, left = r.left;
  // Flip vertically when near bottom of viewport.
  if (top + 120 > window.innerHeight) top = r.top - card.offsetHeight - 6;
  // Clamp horizontally.
  if (left + 320 > window.innerWidth) left = window.innerWidth - 332;
  card.style.top = top + 'px';
  card.style.left = left + 'px';
}
function _glossaryHide(){
  var c = document.getElementById('glossary-card');
  if (c) c.remove();
}

function _openFullManual(){
  var anchor = _HELP_DEEP_LINKS[ctab] || '';
  var url = '/help' + (anchor ? '#' + anchor : '');
  window.open(url, '_blank', 'noopener');
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
// Shared profile-cache loader — emuLoadStage() and loadLayout() both fire
// on page load and previously double-fetched /api/dmx-profiles (#432).
// The pending-callback queue coalesces concurrent callers into one request.
window._profileCachePending=null;
function _loadProfileCache(cb){
  if(window._profileCache){if(cb)cb();return;}
  if(window._profileCachePending){if(cb)window._profileCachePending.push(cb);return;}
  var queue=window._profileCachePending=cb?[cb]:[];
  ra('GET','/api/dmx-profiles',null,function(profs){
    window._profileCache={};
    (profs||[]).forEach(function(p){window._profileCache[p.id]=p;});
    window._profileCachePending=null;
    queue.forEach(function(fn){try{fn();}catch(e){}});
  });
}

function loadLayout(){
  _layCheckShowRunning();
  ra('GET','/api/settings',null,function(s){
    if(s)units=s.units||0;
    ra('GET','/api/stage',null,function(st){
      if(st)window._stageData=st;
      // Pre-load profile cache for beam widths
      _loadProfileCache(function(){
        // Re-render after profiles loaded (beam cones need profile data)
        if(_s3d.inited)s3dLoadChildren();
      });
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
  // #600 — rotation layout is [rx pitch, ry roll, rz yaw]. Update via the
  // axis-semantic helper so the index layout only ever reads from one
  // place.
  var cur=rotationFromLayout(f.rotation);
  if(axis==='pan'){
    f.rotation=rotationToLayout(cur.tilt,val,cur.roll);
  } else {
    f.rotation=rotationToLayout(val,cur.pan,cur.roll);
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
// Keyboard shortcuts: R=rotate, M=move, Ctrl+Z=undo (Layout only), plus
// Space/Esc/Home/End/Arrow keys for timeline playback (#299). All global
// shortcuts are suppressed while the user is typing in a form element so
// Space in a text field still inserts a space.
document.addEventListener('keydown',function(e){
  // Global shortcuts (work from any tab, any focus)
  if((e.ctrlKey||e.metaKey)&&(e.key==='s'||e.key==='S')){e.preventDefault();_fmSave();return;}
  if((e.ctrlKey||e.metaKey)&&(e.key==='o'||e.key==='O')){e.preventDefault();_fmOpen();return;}
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT')return;
  if(e.target.isContentEditable)return;

  // #297 — Ctrl+Shift+Z redo and Ctrl+Z undo, active on every tab. The
  // global command stack holds cross-tab mutations (action delete for
  // now); the layout-positional _undoStack is consulted as a fallback so
  // existing move/rotate undo keeps working.
  if((e.ctrlKey||e.metaKey)&&e.shiftKey&&(e.key==='z'||e.key==='Z')){
    e.preventDefault();cmdRedo();return;
  }
  if((e.ctrlKey||e.metaKey)&&(e.key==='z'||e.key==='Z')){
    if(cmdUndo()){e.preventDefault();return;}
    // Else: fall through so Layout-tab positional undo can still fire.
  }

  // Timeline playback shortcuts — active on the Shows/Runtime tabs where a
  // timeline is open. Fall through if no timeline is selected so the key
  // can still be handled by the layout block below.
  var hasTl=(typeof _curTl!=='undefined'&&_curTl)
            &&(ctab==='shows'||ctab==='runtime');
  if(hasTl){
    if(e.key===' '||e.code==='Space'){
      e.preventDefault();
      if(typeof tlTogglePreview==='function')tlTogglePreview();
      return;
    }
    if(e.key==='Escape'){
      e.preventDefault();
      if(typeof tlStop==='function')tlStop();
      return;
    }
    if(e.key==='Home'){
      e.preventDefault();
      if(typeof tlRewind==='function')tlRewind();
      return;
    }
    if(e.key==='End'){
      e.preventDefault();
      if(typeof _tlJumpToEnd==='function')_tlJumpToEnd();
      return;
    }
    if(e.key==='ArrowLeft'||e.key==='ArrowRight'){
      e.preventDefault();
      var dir=(e.key==='ArrowRight'?1:-1);
      var step=(e.shiftKey?5:1);
      if(typeof _tlNudge==='function')_tlNudge(dir*step);
      return;
    }
  }
  // 'B' anywhere (outside inputs) → blackout all fixtures. Second press
  // does nothing extra since blackout is idempotent — operators use Esc
  // or another command to resume output.
  if((e.key==='b'||e.key==='B')&&ctab!=='layout'){
    e.preventDefault();
    if(typeof _dmxBlackoutAll==='function')_dmxBlackoutAll();
    else ra('POST','/api/dmx/blackout',{},function(){
      var hs=document.getElementById('hs');if(hs)hs.textContent='Blackout';
    });
    return;
  }

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

// #598 — if the Windows installer dropped the depth.install-requested
// marker, the orchestrator kicks off the install silently at boot.
// Pop the progress modal once per session so the user actually sees
// the 2 GB download happening instead of a silent 5-minute wait.
var _depthRuntimeBootShown=false;
ra('GET','/api/depth-runtime/install-status',null,function(ins){
  if(ins&&ins.running&&!_depthRuntimeBootShown&&typeof _depthRuntimeOpenProgress==='function'){
    _depthRuntimeBootShown=true;
    _depthRuntimeOpenProgress();
  }
});
