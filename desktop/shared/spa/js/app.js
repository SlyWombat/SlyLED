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

// ── 3D Viewport (Three.js) ─────────────────────────────────────────────────
var _s3d={
  inited:false,scene:null,camera:null,perspCam:null,orthoCam:null,renderer:null,controls:null,
  tctl:null,nodes:[],lines:[],labels:[],selected:null,raycaster:null,mouse:null,
  animId:null,stageW:10,stageH:5,stageD:10
};

function s3dInit(){
  if(_s3d.inited)return;
  if(typeof THREE==='undefined'){console.warn('Three.js not loaded');return;}
  var el=document.getElementById('stage3d');if(!el)return;
  var W=el.clientWidth||900,H=el.clientHeight||500;

  _s3d.scene=new THREE.Scene();
  _s3d.scene.background=new THREE.Color(0x080810);
  _s3d.scene.fog=new THREE.Fog(0x080810,30,60);

  // Create both cameras — ortho for front/top/side, perspective for 3D
  var aspect=W/H;
  _s3d.perspCam=new THREE.PerspectiveCamera(50,aspect,0.1,100);
  _s3d.perspCam.position.set(8,6,12);
  _s3d.perspCam.lookAt(0,0,0);
  var frustumH=12;
  _s3d.orthoCam=new THREE.OrthographicCamera(-frustumH*aspect/2,frustumH*aspect/2,frustumH/2,-frustumH/2,0.01,200);
  _s3d.orthoCam.position.set(0,0,50);
  _s3d.orthoCam.lookAt(0,0,0);
  // Start with ortho camera (front view is default)
  _s3d.camera=_s3d.orthoCam;

  _s3d.renderer=new THREE.WebGLRenderer({antialias:true});
  _s3d.renderer.setSize(W,H);
  _s3d.renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
  el.appendChild(_s3d.renderer.domElement);

  // Orbit controls (attached to current camera)
  _s3d.controls=new THREE.OrbitControls(_s3d.camera,_s3d.renderer.domElement);
  _s3d.controls.enableDamping=true;_s3d.controls.dampingFactor=0.08;
  _s3d.controls.enableRotate=false; // front view: no rotation
  _s3d.controls.target.set(0,0,0);

  // Transform controls for moving nodes
  _s3d.tctl=new THREE.TransformControls(_s3d.camera,_s3d.renderer.domElement);
  _s3d.tctl.setMode('translate');_s3d.tctl.setSize(0.6);
  _s3d.tctl.addEventListener('dragging-changed',function(e){
    _s3d.controls.enabled=!e.value;
    if(e.value){
      // Drag started — save undo state for the fixture being dragged
      var grp=_s3d.tctl.object;
      if(grp&&grp.userData&&grp.userData.childId!==undefined){
        var fid=grp.userData.childId;
        var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id==fid)f=fx;});
        if(f)_laySaveUndo(f);
      }
    }
    if(!e.value){
      _layoutDirty=true;_layDirtyUpdate();
      if(_s3dDraggingAim){
        // Convert dragged aim point position to rotation and save
        var obj=_s3d.tctl.object;
        if(obj){
          var wp=new THREE.Vector3();obj.getWorldPosition(wp);
          var aim=[Math.round(wp.x*1000),Math.round(wp.y*1000),Math.round(wp.z*1000)];
          var _aimFid=_s3dDraggingAim;
          (_fixtures||[]).forEach(function(f){if(f.id===_aimFid){
            var dx=aim[0]-(f.x||0),dy=aim[1]-(f.y||0),dz=aim[2]-(f.z||0);
            var hd=Math.sqrt(dx*dx+dz*dz);
            if(hd>0.001||Math.abs(dy)>0.001){
              f.rotation=[
                Math.round(-Math.atan2(dy,hd)*180/Math.PI),
                Math.round(Math.atan2(dx,dz)*180/Math.PI),
                f.rotation?f.rotation[2]||0:0
              ];
            }
          }});
          ra('PUT','/api/fixtures/'+_aimFid+'/aim',{aimPoint:aim},function(){
            s3dLoadChildren();
          });
        }
        _s3dDraggingAim=null;
      } else {
        _s3dSyncToLd();
        // Update side panel position fields for selected fixture
        if(_s3d.selected&&_s3d.selected.userData.childId!==undefined){
          _updateSidePanel(_s3d.selected.userData.childId);
        }
      }
    }
  });
  _s3d.scene.add(_s3d.tctl);

  // Ground grid
  var grid=new THREE.GridHelper(20,20,0x1a2744,0x111828);
  _s3d.scene.add(grid);

  // Axis helper at origin (0,0,0) — stage corner
  var axes=new THREE.AxesHelper(0.5);
  _s3d.scene.add(axes);
  var originLbl=_s3dLabel('Origin (0,0,0)');
  originLbl.position.set(0,-0.08,0);
  _s3d.scene.add(originLbl);
  // Axis labels match stage coords: X=width, Y=depth(3D Z), Z=height(3D Y)
  var xLbl=_s3dLabel('X');xLbl.position.set(0.6,0.05,0);_s3d.scene.add(xLbl);
  var zLbl=_s3dLabel('Z');zLbl.position.set(0,0.6,0);_s3d.scene.add(zLbl);   // 3D Y-up = stage Z(height)
  var yLbl=_s3dLabel('Y');yLbl.position.set(0,0.05,0.6);_s3d.scene.add(yLbl); // 3D Z = stage Y(depth)

  // Ambient + directional light
  _s3d.scene.add(new THREE.AmbientLight(0x334466,0.8));
  var dl=new THREE.DirectionalLight(0xffffff,0.6);dl.position.set(5,10,7);
  _s3d.scene.add(dl);

  // Raycaster
  _s3d.raycaster=new THREE.Raycaster();
  _s3d.mouse=new THREE.Vector2();

  // Click to select
  _s3d.renderer.domElement.addEventListener('click',s3dClick);
  // Double-click to edit
  _s3d.renderer.domElement.addEventListener('dblclick',s3dDblClick);
  // Right-click to dismiss scan ghosts (replaces old cvCtx)
  _s3d.renderer.domElement.addEventListener('contextmenu',s3dCtx);
  // Drop from sidebar
  _s3d.renderer.domElement.addEventListener('dragover',function(e){e.preventDefault();});
  _s3d.renderer.domElement.addEventListener('drop',s3dDrop);

  // TransformControls objectChange — live updates during rotate drag
  _s3d.tctl.addEventListener('objectChange',function(){
    if(_layTool==='rotate'&&_s3d.selected){
      _updateRotationFromGizmo();
    }
  });

  _s3d.inited=true;
  s3dAnimate();
  // Apply default 3D view after init
  setView('3d');
}

// Right-click handler for 3D scene — dismiss scan ghosts
function s3dCtx(e){
  if(!_scanGhosts.length)return;
  e.preventDefault();
  if(!_s3d.inited)return;
  var rect=_s3d.renderer.domElement.getBoundingClientRect();
  _s3d.mouse.x=((e.clientX-rect.left)/rect.width)*2-1;
  _s3d.mouse.y=-((e.clientY-rect.top)/rect.height)*2+1;
  _s3d.raycaster.setFromCamera(_s3d.mouse,_s3d.camera);
  var ghostMeshes=[];
  _s3d.scene.children.forEach(function(c){if(c.userData&&c.userData.scanGhost)c.traverse(function(obj){if(obj.isMesh){obj._ghostIdx=c.userData.ghostIdx;ghostMeshes.push(obj);}});});
  var hits=_s3d.raycaster.intersectObjects(ghostMeshes);
  if(hits.length>0&&hits[0].object._ghostIdx!==undefined)_layScanDismiss(hits[0].object._ghostIdx);
}

// ── View presets (replaces old 2D/3D toggle) ─────────────────────────────
function _s3dSwitchCamera(cam){
  _s3d.camera=cam;
  _s3d.controls.object=cam;
  _s3d.tctl.camera=cam;
  _s3d.controls.update();
}

function setView(view){
  _layView=view;
  if(!_s3d.inited){s3dInit();if(!_s3d.inited)return;}
  var sw=_s3d.stageW||10,sh=_s3d.stageH||5,sd=_s3d.stageD||10;
  var el=document.getElementById('stage3d');
  var W=(el?el.clientWidth:900)||900,H=(el?el.clientHeight:500)||500;
  var aspect=W/H;
  var hint=document.getElementById('lay-hint');

  // Update button styles
  ['front','top','side','3d'].forEach(function(v){
    var btn=document.getElementById('btn-view-'+v);
    if(btn){btn.style.background=v===view?'#14532d':'';btn.style.color=v===view?'#86efac':'';}
  });

  if(view==='3d'){
    // Perspective camera with full orbit
    _s3dSwitchCamera(_s3d.perspCam);
    _s3d.controls.enableRotate=true;
    _s3d.scene.fog=new THREE.Fog(0x080810,30,60);
    _s3d.controls.target.set(sw/2,sh/4,sd/2);
    _s3d.camera.position.set(sw*1.2,sh*1.0,sd*1.5);
    if(hint)hint.textContent='Click to select. Drag gizmo to move. Double-click to edit. Scroll=zoom, Drag=orbit.';
  } else {
    // Orthographic camera — no rotation, pan+zoom only
    var frustumH,cx,cy,cz,tx,ty,tz;
    if(view==='front'){
      // Looking down +Z toward origin: camera sees XY plane
      // Best-fit: pick frustum so both stage width and height are visible
      frustumH=Math.max(sh,sw/aspect)*1.05;
      var oh=frustumH/2;var ow=oh*aspect;
      _s3d.orthoCam.left=-ow;_s3d.orthoCam.right=ow;_s3d.orthoCam.top=oh;_s3d.orthoCam.bottom=-oh;
      _s3d.orthoCam.near=0.01;_s3d.orthoCam.far=200;
      _s3d.orthoCam.updateProjectionMatrix();
      tx=sw/2;ty=sh/2;tz=sd/2;
      cx=sw/2;cy=sh/2;cz=sd+50;
      if(hint)hint.textContent='Front view (XZ). Drag fixtures from sidebar. Double-click to edit. Scroll=zoom, Drag=pan.';
    } else if(view==='top'){
      // Looking down -Y: camera sees XZ plane
      frustumH=Math.max(sd,sw/aspect)*1.05;
      var oh=frustumH/2;var ow=oh*aspect;
      _s3d.orthoCam.left=-ow;_s3d.orthoCam.right=ow;_s3d.orthoCam.top=oh;_s3d.orthoCam.bottom=-oh;
      _s3d.orthoCam.near=0.01;_s3d.orthoCam.far=200;
      _s3d.orthoCam.updateProjectionMatrix();
      tx=sw/2;ty=0;tz=sd/2;
      cx=sw/2;cy=Math.max(sw,sd)*1.5;cz=sd/2;
      _s3d.orthoCam.up.set(0,0,-1);
      if(hint)hint.textContent='Top view (XY, bird-eye). Scroll=zoom, Drag=pan.';
    } else if(view==='side'){
      // Looking down +X toward origin: camera sees ZY plane
      frustumH=Math.max(sh,sd/aspect)*1.05;
      var oh=frustumH/2;var ow=oh*aspect;
      _s3d.orthoCam.left=-ow;_s3d.orthoCam.right=ow;_s3d.orthoCam.top=oh;_s3d.orthoCam.bottom=-oh;
      _s3d.orthoCam.near=0.01;_s3d.orthoCam.far=200;
      _s3d.orthoCam.updateProjectionMatrix();
      tx=sw/2;ty=sh/2;tz=sd/2;
      cx=sw+50;cy=sh/2;cz=sd/2;
      if(hint)hint.textContent='Side view (YZ). Scroll=zoom, Drag=pan.';
    }
    _s3dSwitchCamera(_s3d.orthoCam);
    _s3d.controls.enableRotate=false;
    _s3d.scene.fog=null; // no fog in ortho views
    _s3d.controls.target.set(tx,ty,tz);
    _s3d.camera.position.set(cx,cy,cz);
    _s3d.camera.lookAt(tx,ty,tz);
    // Reset camera up after setting position (top view changes it)
    if(view!=='top')_s3d.orthoCam.up.set(0,1,0);
  }
  _s3d.controls.update();
}

function s3dAnimate(){
  _s3d.animId=requestAnimationFrame(s3dAnimate);
  if(_s3d.controls)_s3d.controls.update();
  // Scale fixture nodes to constant screen size regardless of zoom
  if(_s3d.camera&&_s3d.nodes){
    var baseSize=0.15; // meters — the SphereGeometry radius
    var scaleFactor;
    if(_s3d.camera.isOrthographicCamera){
      // Ortho: scale by frustum height (bigger frustum = zoom out = need bigger nodes)
      scaleFactor=(_s3d.camera.top-_s3d.camera.bottom)/8;
    }else{
      // Perspective: use average distance to stage center
      var center=new THREE.Vector3((_s3d.stageW||10)/2,(_s3d.stageH||5)/4,(_s3d.stageD||10)/2);
      scaleFactor=_s3d.camera.position.distanceTo(center)/15;
    }
    scaleFactor=Math.max(0.3,Math.min(3.0,scaleFactor));
    _s3d.nodes.forEach(function(grp){
      // Scale the node sphere (first child) but not the whole group (that would scale beams too)
      if(grp.children[0]&&grp.children[0].isMesh)grp.children[0].scale.setScalar(scaleFactor);
      // Also scale the glow ring (second child)
      if(grp.children[1]&&grp.children[1].isMesh)grp.children[1].scale.setScalar(scaleFactor);
    });
  }
  // Live beam cone update from /api/fixtures/live (#355)
  if(!_s3d._liveT)_s3d._liveT=0;
  var now=Date.now();
  if(now-_s3d._liveT>500&&ctab==='layout'){
    _s3d._liveT=now;
    ra('GET','/api/fixtures/live',null,function(liveData){
      if(!liveData||typeof liveData!=='object')return;
      _s3d.nodes.forEach(function(grp){
        var fid=grp.userData.childId;if(fid===undefined)return;
        var live=liveData[String(fid)];if(!live)return;
        var panNorm=live.pan,tiltNorm=live.tilt;
        if(panNorm===undefined||tiltNorm===undefined)return;
        var fx=null;(_fixtures||[]).forEach(function(f){if(f.id===fid)fx=f;});
        if(!fx||fx.fixtureType!=='dmx')return;
        var prof=window._profileCache&&fx.dmxProfileId?window._profileCache[fx.dmxProfileId]:null;
        var panRange=prof?prof.panRange||540:540;
        var tiltRange=prof?prof.tiltRange||270:270;
        var rot=fx.rotation||[0,0,0];
        var basePan=rot[1]||0;
        var panDeg=(panNorm-0.5)*panRange;
        var tiltDeg=(tiltNorm-0.5)*tiltRange;
        if(fx.mountedInverted)tiltDeg=-tiltDeg;
        var panRad=(basePan+panDeg)*Math.PI/180;
        var tiltRad=tiltDeg*Math.PI/180;
        var aimDir=new THREE.Vector3(Math.sin(panRad)*Math.cos(tiltRad),-Math.sin(tiltRad),Math.cos(panRad)*Math.cos(tiltRad));
        grp.children.forEach(function(child){
          if(child.userData.beamCone&&child.isMesh&&child.geometry.type==='ConeGeometry'){
            var beamLen=child.geometry.parameters.height||3;
            var mid=aimDir.clone().multiplyScalar(beamLen/2);
            child.position.copy(mid);
            child.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,-1,0),aimDir.clone().normalize()));
          }
        });
        // Update cone color from live dimmer/RGB
        if(live.dimmer!==undefined){
          var dimVal=live.dimmer/255;
          var hexCol=((live.r||0)<<16)|((live.g||0)<<8)|(live.b||0);
          grp.children.forEach(function(child){
            if(child.userData.beamCone&&child.isMesh&&child.geometry.type==='ConeGeometry'){
              if(hexCol>0)child.material.color.setHex(hexCol);
              child.material.opacity=dimVal>0.01?dimVal*0.4:0.08;
            }
          });
        }
      });
    });
  }
  if(_s3d.renderer&&_s3d.scene&&_s3d.camera)_s3d.renderer.render(_s3d.scene,_s3d.camera);
  // Info overlay
  var info=document.getElementById('s3d-info');
  if(info&&ld){
    var placed=(ld.children||[]).filter(_isPlaced).length;
    info.textContent='Stage: '+_s3d.stageW+'m x '+_s3d.stageH+'m x '+_s3d.stageD+'m | '+placed+' fixtures placed | Scroll=zoom, Drag=orbit, Right-drag=pan';
  }
}

function s3dDispose(){
  if(_s3d.animId){cancelAnimationFrame(_s3d.animId);_s3d.animId=null;}
}

function s3dLoadChildren(){
  if(!_s3d.inited||!ld)return;
  // Dispose old geometry/materials before removing nodes
  _s3d.nodes.forEach(function(grp){grp.traverse(function(obj){
    if(obj.geometry)obj.geometry.dispose();
    if(obj.material){if(obj.material.map)obj.material.map.dispose();obj.material.dispose();}
  });_s3d.scene.remove(grp);});
  _s3d.lines.forEach(function(m){_s3d.scene.remove(m);});
  _s3d.labels.forEach(function(m){_s3d.scene.remove(m);});
  _s3d.nodes=[];_s3d.lines=[];_s3d.labels=[];
  if(_s3d.tctl)_s3d.tctl.detach();_s3d.selected=null;

  // Fetch stage dimensions
  ra('GET','/api/stage',null,function(st){
    if(st){_s3d.stageW=st.w||10;_s3d.stageH=st.h||5;_s3d.stageD=st.d||10;}
    // Re-fit camera to actual stage dimensions
    setView(_layView||'front');
    // Draw stage boundary box
    _s3d.scene.children.forEach(function(c){if(c.userData.stageBox)_s3d.scene.remove(c);});
    // Stage box: Three.js (X, Y-up=height, Z=depth) = stage (W, H, D)
    var boxGeo=new THREE.BoxGeometry(_s3d.stageW,_s3d.stageH,_s3d.stageD);
    var boxEdge=new THREE.EdgesGeometry(boxGeo);
    var boxLine=new THREE.LineSegments(boxEdge,new THREE.LineBasicMaterial({color:0x1e3a5f,opacity:0.4,transparent:true}));
    boxLine.position.set(_s3d.stageW/2,_s3d.stageH/2,_s3d.stageD/2);
    boxLine.userData.stageBox=true;
    _s3d.scene.add(boxLine);

    // Stage dimension labels along edges
    _s3d.scene.children.forEach(function(c){if(c.userData&&c.userData.stageDimLabel){
      if(c.material&&c.material.map)c.material.map.dispose();if(c.material)c.material.dispose();_s3d.scene.remove(c);
    }});
    var sw=_s3d.stageW,sh=_s3d.stageH,sd=_s3d.stageD;
    var wLbl=_s3dLabel(Math.round(sw*1000)+'mm');wLbl.position.set(sw/2,-0.15,0);wLbl.userData.stageDimLabel=true;wLbl.scale.set(0.6,0.15,1);_s3d.scene.add(wLbl);
    var dLbl=_s3dLabel(Math.round(sd*1000)+'mm');dLbl.position.set(0,-0.15,sd/2);dLbl.userData.stageDimLabel=true;dLbl.scale.set(0.6,0.15,1);_s3d.scene.add(dLbl);
    var hLbl=_s3dLabel(Math.round(sh*1000)+'mm');hLbl.position.set(-0.15,sh/2,0);hLbl.userData.stageDimLabel=true;hLbl.scale.set(0.6,0.15,1);_s3d.scene.add(hLbl);

    // Camera positioning is handled by setView() — don't reset on every reload

    // Load objects THEN render them (async — must wait for data)
    loadObjects(function(){_s3dRenderObjects();});

    var placed=(_fixtures||[]).filter(_isFixturePlaced);
    placed.forEach(function(c){
      var pos=_s3dPos(c);
      var ft=c.fixtureType||'led';
      var col=ft==='dmx'?0x7c3aed:ft==='camera'?0x0e7490:(c.status===1?0x22cc66:0x555555);

      // Group — everything moves together when dragged
      var grp=new THREE.Group();
      grp.position.copy(pos);
      grp.userData.childId=c.id;

      // Sphere node (at group origin)
      var geo=new THREE.SphereGeometry(0.15,16,12);
      var mat=new THREE.MeshStandardMaterial({color:col,emissive:col,emissiveIntensity:0.4});
      var sphere=new THREE.Mesh(geo,mat);
      grp.add(sphere);

      // Invisible click helper sphere — larger hit area for raycaster (#358)
      var hitGeo=new THREE.SphereGeometry(0.35,8,8);
      var hitMat=new THREE.MeshBasicMaterial({visible:false});
      var hitSphere=new THREE.Mesh(hitGeo,hitMat);
      grp.add(hitSphere);

      // Glow ring
      var ringGeo=new THREE.RingGeometry(0.18,0.22,24);
      var ringMat=new THREE.MeshBasicMaterial({color:col,side:THREE.DoubleSide,opacity:0.3,transparent:true});
      var ring=new THREE.Mesh(ringGeo,ringMat);
      ring.rotation.x=-Math.PI/2;
      grp.add(ring);

      // DMX beam cone + aim point (for DMX fixtures with rotation)
      var _fix3d=null;
      (_fixtures||[]).forEach(function(ff){if(ff.childId===c.id||ff.id===c.id)_fix3d=ff;});
      if(_fix3d&&(_fix3d.fixtureType==='dmx'||_fix3d.fixtureType==='camera')){
        var _fRot=_fix3d.rotation||[0,0,0];
        var aim=_rotToAim(_fRot,[c.x||0,c.y||0,c.z||0],3000,_fix3d.mountedInverted);
        // Stage→Three.js: X=X, Y(depth)→Z, Z(height)→Y
        var aimLocal=new THREE.Vector3((aim[0]-(c.x||0))/1000,(aim[2]-(c.z||0))/1000,(aim[1]-(c.y||0))/1000);
        var beamLen=aimLocal.length();
        // Cone shown for DMX and camera fixtures with non-zero rotation
        var showCone=(_fRot[0]!==0||_fRot[1]!==0)||_fix3d.fixtureType==='camera';
        if(beamLen>0.01&&showCone){
          var bwDeg=_fix3d.fixtureType==='camera'?(_fix3d.fovDeg||60):15;
          // Use cached profile beamWidth (sync) — _profileCache populated by emulator/layout
          if(_fix3d.fixtureType==='dmx'&&_fix3d.dmxProfileId&&window._profileCache&&window._profileCache[_fix3d.dmxProfileId]){
            bwDeg=window._profileCache[_fix3d.dmxProfileId].beamWidth||15;
          }
          var bwRad=bwDeg*Math.PI/180;
          var topR=Math.tan(bwRad/2)*beamLen;
          var coneGeo=new THREE.ConeGeometry(topR,beamLen,16,1,true);
          var coneColor=_fix3d.fixtureType==='camera'?0x22d3ee:0xffff88;
          var coneMat=new THREE.MeshBasicMaterial({color:coneColor,opacity:0.12,transparent:true,side:THREE.DoubleSide,depthWrite:false});
          var cone=new THREE.Mesh(coneGeo,coneMat);
          // Position cone: midpoint between origin and aim, oriented toward aim
          var midPt=aimLocal.clone().multiplyScalar(0.5);
          cone.position.copy(midPt);
          // Orient: ConeGeometry apex is at +Y, base at -Y. We want apex at fixture
          // (narrow end) and base at aim (wide end), so rotate from -Y to aim direction.
          var dir=aimLocal.clone().normalize();
          var quat=new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,-1,0),dir);
          cone.quaternion.copy(quat);
          cone.userData.beamCone=true;
          cone.visible=_layShowCones;
          grp.add(cone);
          // Aim point sphere (red, draggable)
          var aimGeo=new THREE.SphereGeometry(0.08,12,12);
          var aimMat=new THREE.MeshBasicMaterial({color:0xff4444,opacity:0.8,transparent:true});
          var aimSphere=new THREE.Mesh(aimGeo,aimMat);
          aimSphere.position.copy(aimLocal);
          aimSphere.userData.isAimPoint=true;
          aimSphere.userData.fixtureId=_fix3d.id;
          aimSphere.visible=_layShowCones;
          grp.add(aimSphere);
          // Glow halo ring
          var glowGeo=new THREE.RingGeometry(0.1,0.15,24);
          var glowMat=new THREE.MeshBasicMaterial({color:0xff6666,opacity:0.25,transparent:true,side:THREE.DoubleSide,depthWrite:false});
          var glow=new THREE.Mesh(glowGeo,glowMat);
          glow.position.copy(aimLocal);
          glow.lookAt(_s3d.camera.position);
          glow.userData.beamCone=true;
          glow.visible=_layShowCones;
          grp.add(glow);

        }
      }

      // Rest vector — dashed arrow showing home direction (DMX movers + cameras)
      if(_fix3d&&(_fix3d.fixtureType==='dmx'||_fix3d.fixtureType==='camera')){
        var hasPanTilt=_fix3d.fixtureType==='camera'||
          (window._profileCache&&_fix3d.dmxProfileId&&window._profileCache[_fix3d.dmxProfileId]&&window._profileCache[_fix3d.dmxProfileId].panRange>0);
        if(hasPanTilt||_fix3d.fixtureType==='camera'){
          var rot3d=_fix3d.rotation||[0,0,0];
          var ryRad=rot3d[1]*Math.PI/180;
          var homeDir=new THREE.Vector3(Math.sin(ryRad),0,Math.cos(ryRad)).normalize();
          var vecLen=0.4;
          var homeEnd=homeDir.clone().multiplyScalar(vecLen);
          var restColor=_fix3d.calibrated?0x22c55e:(_fix3d.fixtureType==='camera'?0x22d3ee:0xf59e0b);
          var restGeo=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0),homeEnd]);
          var restMat=new THREE.LineDashedMaterial({color:restColor,dashSize:0.04,gapSize:0.02,opacity:0.7,transparent:true});
          var restLine=new THREE.Line(restGeo,restMat);
          restLine.computeLineDistances();
          restLine.userData.beamCone=true;restLine.visible=_layShowCones;
          grp.add(restLine);
          var arrowGeo=new THREE.ConeGeometry(0.02,0.06,8);
          var arrowMat=new THREE.MeshBasicMaterial({color:restColor,opacity:0.8,transparent:true});
          var arrow=new THREE.Mesh(arrowGeo,arrowMat);
          arrow.position.copy(homeEnd);
          arrow.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),homeDir));
          arrow.userData.beamCone=true;arrow.visible=_layShowCones;
          grp.add(arrow);
          var restLbl=_s3dLabel(_fix3d.fixtureType==='camera'?(_fix3d.name||'cam'+(_fix3d.cameraIdx||0)):'0,0');
          restLbl.position.copy(homeEnd.clone().add(homeDir.clone().multiplyScalar(0.05)));
          restLbl.userData.beamCone=true;restLbl.visible=_layShowCones;
          grp.add(restLbl);
        }
      }

      // LED string lines + dots (positions relative to group origin = 0,0,0)
      var _sc=c.sc||c.strings&&c.strings.length||0;
      if(c.strings&&_sc>0){
        for(var si=0;si<_sc&&si<c.strings.length;si++){
          var s=c.strings[si];if(!s||!s.leds)continue;
          var strCol=new THREE.Color(_strCol[si%_strCol.length]);
          var lenMm=s.mm||0;if(lenMm<500)lenMm=Math.max(s.leds*16,500);
          var lenM=lenMm/1000;
          var dir=_s3dDir(s.sdir||0);
          var endLocal=new THREE.Vector3(dir.x*lenM,dir.y*lenM,dir.z*lenM);
          var pts=[new THREE.Vector3(0,0,0),endLocal];
          var lineGeo=new THREE.BufferGeometry().setFromPoints(pts);
          var lineMat=new THREE.LineBasicMaterial({color:strCol,linewidth:2});
          grp.add(new THREE.Line(lineGeo,lineMat));

          // LED dots along string
          var dotCount=Math.min(s.leds,50);
          for(var di=0;di<dotCount;di++){
            var t=(di+0.5)/dotCount;
            var dp=new THREE.Vector3().lerpVectors(pts[0],endLocal,t);
            var dotGeo=new THREE.SphereGeometry(0.03,4,4);
            var dotMat=new THREE.MeshBasicMaterial({color:strCol});
            var dot=new THREE.Mesh(dotGeo,dotMat);
            dot.position.copy(dp);
            grp.add(dot);
          }
        }
      }

      // Text label (sprite, above group)
      var label=_s3dLabel(c.name||(c.hostname||'ID '+c.id));
      label.position.set(0,0.35,0);
      grp.add(label);

      _s3d.scene.add(grp);
      _s3d.nodes.push(grp);
    });
  });
}

function _s3dRenderObjects(){
  if(!_s3d.inited)return;
  // Remove old objects (dispose geometry/materials)
  var toRemove=[];
  _s3d.scene.children.forEach(function(c){if(c.userData.stageObj)toRemove.push(c);});
  toRemove.forEach(function(c){c.traverse(function(obj){
    if(obj.geometry)obj.geometry.dispose();
    if(obj.material){if(obj.material.map)obj.material.map.dispose();obj.material.dispose();}
  });_s3d.scene.remove(c);});
  // Render each object as a draggable group
  (_objects||[]).forEach(function(s){
    var t=s.transform||{pos:[0,0,0],scale:[2000,1500,1]};
    var sw=(t.scale[0]||2000)/1000,sh=(t.scale[1]||1500)/1000;
    var col=new THREE.Color(s.color||'#334155');

    var grp=new THREE.Group();
    grp.userData.stageObj=true;
    grp.userData.stageObjId=s.id;
    // Stage→Three.js: X→X, Y(depth)→Z, Z(height)→Y (#369)
    grp.position.set((t.pos[0]||0)/1000,(t.pos[2]||0)/1000,(t.pos[1]||0)/1000);

    var sd=(t.scale[2]||1)/1000;
    var useBox=((t.scale[2]||1)>100); // >10cm depth → box
    // Scale: stage [W, H, D] → Three.js [W(x), H(y), D(z)]
    // But stage H is visual height (Z axis in 3D = Y), stage D is depth (Y axis in 3D = Z)
    var geo=useBox?new THREE.BoxGeometry(sw,sh,sd):new THREE.PlaneGeometry(sw,sh);
    var mat=new THREE.MeshBasicMaterial({color:col,side:THREE.DoubleSide,opacity:(s.opacity||30)/100,transparent:true});
    var mesh=new THREE.Mesh(geo,mat);
    // Position so bottom-front corner is at the object origin
    mesh.position.set(sw/2,sh/2,useBox?(-sd/2):0);
    grp.add(mesh);

    // Border edges
    var edgeMat=new THREE.LineBasicMaterial({color:col,opacity:0.8,transparent:true});
    var edge=new THREE.LineSegments(new THREE.EdgesGeometry(geo),edgeMat);
    edge.position.copy(mesh.position);
    grp.add(edge);

    // Label
    var lbl=_s3dLabel(s.name||'Object');
    lbl.position.set(sw/2,sh+0.15,useBox?(-sd/2):0);
    grp.add(lbl);

    _s3d.scene.add(grp);
    _s3d.nodes.push(grp); // make it selectable/draggable alongside fixtures
  });
}

function _s3dRenderGhosts(){
  if(!_s3d.inited)return;
  // Remove old ghosts
  var toRemove=[];
  _s3d.scene.children.forEach(function(c){if(c.userData.scanGhost)toRemove.push(c);});
  toRemove.forEach(function(c){c.traverse(function(obj){
    if(obj.geometry)obj.geometry.dispose();
    if(obj.material)obj.material.dispose();
  });_s3d.scene.remove(c);});
  // Render each ghost as a translucent cyan box
  (_scanGhosts||[]).forEach(function(g,gi){
    var grp=new THREE.Group();
    grp.userData.scanGhost=true;
    grp.userData.ghostIdx=gi;
    grp.position.set((g.x||0)/1000,0,(g.z||0)/1000);
    var sw=(g.w||200)/1000,sh=(g.h||200)/1000,sd=0.1;
    var geo=new THREE.BoxGeometry(sw,sh,sd);
    var mat=new THREE.MeshBasicMaterial({color:0x22d3ee,opacity:0.2,transparent:true,side:THREE.DoubleSide});
    var mesh=new THREE.Mesh(geo,mat);
    mesh.position.set(0,sh/2,0);
    grp.add(mesh);
    // Dashed wireframe
    var edgeGeo=new THREE.EdgesGeometry(geo);
    var edgeMat=new THREE.LineDashedMaterial({color:0x22d3ee,dashSize:0.1,gapSize:0.05,opacity:0.8,transparent:true});
    var edge=new THREE.LineSegments(edgeGeo,edgeMat);
    edge.computeLineDistances();
    edge.position.copy(mesh.position);
    grp.add(edge);
    // Label
    var lbl=_s3dLabel(g.label+' '+Math.round(g.confidence*100)+'%');
    lbl.position.set(0,sh+0.15,0);
    grp.add(lbl);
    _s3d.scene.add(grp);
  });
}

function _s3dPos(c){
  // Stage coordinate system: X=width, Y=depth, Z=height
  // Three.js uses Y-up, so we map: stage X→3D X, stage Y→3D Z (depth), stage Z→3D Y (height)
  return new THREE.Vector3((c.x||0)/1000,(c.z||0)/1000,(c.y||0)/1000);
}
function _s3dToMm(pos){
  // Reverse: 3D X→stage X, 3D Y(up)→stage Z(height), 3D Z(depth)→stage Y(depth)
  return{x:Math.round(pos.x*1000),y:Math.round(pos.z*1000),z:Math.round(pos.y*1000)};
}
function _s3dDir(sdir){
  // E=+X, N=+Z(up, 3D Y), W=-X, S=-Z(down, 3D -Y)
  // Stage Z=height maps to Three.js Y-up
  var dirs=[new THREE.Vector3(1,0,0),new THREE.Vector3(0,1,0),new THREE.Vector3(-1,0,0),new THREE.Vector3(0,-1,0)];
  return dirs[sdir]||dirs[0];
}
function _s3dSyncToLd(){
  // Write 3D group positions back into ld.children and objects
  if(!_s3d.inited||!ld)return;
  _s3d.nodes.forEach(function(grp){
    if(grp.userData.childId!==undefined){
      var cid=grp.userData.childId;
      var mm=_s3dToMm(grp.position);
      (ld.children||[]).forEach(function(c){if(c.id===cid){c.x=mm.x;c.y=mm.y;c.z=mm.z;}});
      (_fixtures||[]).forEach(function(f){if(f.id===cid){
        // Rotation stays the same — beam direction is relative to fixture
        f.x=mm.x;f.y=mm.y;f.z=mm.z;f._placed=true;
      }});
    }
    if(grp.userData.stageObj&&grp.userData.stageObjId!==undefined){
      var sid=grp.userData.stageObjId;
      var mm=_s3dToMm(grp.position);
      (_objects||[]).forEach(function(s){
        if(s.id===sid){
          if(!s.transform)s.transform={pos:[0,0,0],rot:[0,0,0],scale:[2000,1500,1]};
          s.transform.pos=[mm.x,mm.y,mm.z];
        }
      });
    }
  });
}

function _s3dLabel(text){
  var canvas=document.createElement('canvas');canvas.width=256;canvas.height=64;
  var ctx=canvas.getContext('2d');
  ctx.font='bold 24px Inter,sans-serif';ctx.fillStyle='#e2e8f0';ctx.textAlign='center';
  ctx.fillText(text,128,40);
  var tex=new THREE.CanvasTexture(canvas);
  var mat=new THREE.SpriteMaterial({map:tex,transparent:true,depthTest:false});
  var sprite=new THREE.Sprite(mat);
  sprite.scale.set(1,0.25,1);
  return sprite;
}

// ── Rotation gizmo update (TransformControls rotate mode) ─────────────
function _updateRotationFromGizmo(){
  // Called during TransformControls rotate drag — update fixture rotation and panel live
  if(!_s3d.selected)return;
  var fid=_s3d.selected.userData.childId;
  if(fid===undefined)return;
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===fid)f=fx;});
  if(!f)return;
  // Read all 3 rotation axes from the 3D group
  var euler=_s3d.selected.rotation;
  var rxDeg=Math.round(euler.x*180/Math.PI);
  var ryDeg=Math.round(euler.y*180/Math.PI);
  var rzDeg=Math.round(euler.z*180/Math.PI);
  f.rotation=[rxDeg,ryDeg,rzDeg];
  // Live update side panel pan/tilt readout
  var panEl=document.getElementById('panel-pan');
  var tiltEl=document.getElementById('panel-tilt');
  if(panEl)panEl.value=ryDeg;
  if(tiltEl)tiltEl.value=rxDeg;
  _updateSidePanel(fid);
}

function _s3dHitGroup(e){
  // Raycast into all group children, return the parent group (fixture or object)
  var rect=_s3d.renderer.domElement.getBoundingClientRect();
  _s3d.mouse.x=((e.clientX-rect.left)/rect.width)*2-1;
  _s3d.mouse.y=-((e.clientY-rect.top)/rect.height)*2+1;
  _s3d.raycaster.setFromCamera(_s3d.mouse,_s3d.camera);
  var allMeshes=[];
  _s3d.nodes.forEach(function(grp){grp.traverse(function(obj){if(obj.isMesh)allMeshes.push(obj);});});
  var hits=_s3d.raycaster.intersectObjects(allMeshes);
  if(hits.length>0){
    var obj=hits[0].object;
    while(obj.parent&&!obj.userData.childId&&!obj.userData.stageObj)obj=obj.parent;
    if(obj.userData.childId!==undefined||obj.userData.stageObj)return obj;
  }
  return null;
}

var _s3dDraggingAim=null;
function s3dClick(e){
  if(!_s3d.inited)return;
  // Check for aim point sphere first
  var rect=_s3d.renderer.domElement.getBoundingClientRect();
  _s3d.mouse.x=((e.clientX-rect.left)/rect.width)*2-1;
  _s3d.mouse.y=-((e.clientY-rect.top)/rect.height)*2+1;
  _s3d.raycaster.setFromCamera(_s3d.mouse,_s3d.camera);
  var allMeshes=[];
  _s3d.nodes.forEach(function(grp){grp.traverse(function(obj){if(obj.isMesh)allMeshes.push(obj);});});
  var hits=_s3d.raycaster.intersectObjects(allMeshes);
  if(hits.length>0&&hits[0].object.userData.isAimPoint){
    _s3d.tctl.attach(hits[0].object);
    _s3dDraggingAim=hits[0].object.userData.fixtureId;
    return;
  }
  _s3dDraggingAim=null;
  var grp=_s3dHitGroup(e);
  if(grp){
    _s3d.selected=grp;
    _s3d.tctl.attach(grp);
    if(grp.userData.childId!==undefined){
      _updateSidePanel(grp.userData.childId);
    }
    else{_updateSidePanel(null);}
  } else {
    _s3d.tctl.detach();_s3d.selected=null;
    _updateSidePanel(null);
  }
}

function s3dDblClick(e){
  if(!_s3d.inited||!ld)return;
  // Check for scan ghost hit first (accept on double-click, like old cvDbl)
  var rect=_s3d.renderer.domElement.getBoundingClientRect();
  _s3d.mouse.x=((e.clientX-rect.left)/rect.width)*2-1;
  _s3d.mouse.y=-((e.clientY-rect.top)/rect.height)*2+1;
  _s3d.raycaster.setFromCamera(_s3d.mouse,_s3d.camera);
  if(_scanGhosts.length){
    var ghostMeshes=[];
    _s3d.scene.children.forEach(function(c){if(c.userData&&c.userData.scanGhost)c.traverse(function(obj){if(obj.isMesh){obj._ghostIdx=c.userData.ghostIdx;ghostMeshes.push(obj);}});});
    var gHits=_s3d.raycaster.intersectObjects(ghostMeshes);
    if(gHits.length>0&&gHits[0].object._ghostIdx!==undefined){_layScanAccept(gHits[0].object._ghostIdx);return;}
  }
  var grp=_s3dHitGroup(e);
  if(!grp)return;
  if(grp.userData.childId!==undefined){
    // Use full-featured showNodeEdit (has Aim, Calibrate, Track buttons)
    var fx=null;(_fixtures||[]).forEach(function(f){if(f.id===grp.userData.childId)fx=f;});
    if(fx)showNodeEdit(fx);
  } else if(grp.userData.stageObj){
    var sf=null;(_objects||[]).forEach(function(s){if(s.id===grp.userData.stageObjId)sf=s;});
    if(sf)editObject(sf.id);
  }
}

function showNodeEdit3D(c){
  var dirs=['East (+X)','North (+Y)','West (-X)','South (-Y)'];
  var types=['WS2812B','WS2811','APA102'];
  var h='<div style="margin-bottom:.8em">';
  h+='<label>X (mm)</label><input id="ne-x" type="number" value="'+c.x+'" min="0" style="width:120px">';
  h+=' <label style="display:inline;margin-left:1em">Y (mm)</label><input id="ne-y" type="number" value="'+c.y+'" min="0" style="width:120px">';
  h+=' <label style="display:inline;margin-left:1em">Z (mm)</label><input id="ne-z" type="number" value="'+(c.z||0)+'" min="0" style="width:120px">';
  h+='</div>';
  if(c.sc>0&&c.strings&&c.strings.length){
    h+='<table class="tbl" style="font-size:.8em;margin-bottom:.8em"><tr><th>#</th><th>LEDs</th><th>Length</th><th>Dir</th><th>Type</th></tr>';
    for(var i=0;i<c.sc&&i<c.strings.length;i++){
      var s=c.strings[i];
      h+='<tr><td>'+(i+1)+'</td><td>'+s.leds+'</td><td>'+s.mm+'mm</td><td>'+(dirs[s.sdir]||'?')+'</td><td>'+(types[s.type]||s.type)+'</td></tr>';
    }
    h+='</table>';
  }
  h+='<div style="display:flex;gap:.5em">';
  h+='<button class="btn btn-on" onclick="applyNodePos3D('+c.id+')">Set Position</button>';
  h+='<button class="btn btn-off" onclick="removeFromCanvas('+c.id+')">Remove from Canvas</button>';
  h+='</div>';
  document.getElementById('modal-title').textContent=c.hostname+(c.name&&c.name!==c.hostname?' ('+c.name+')':'');
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function applyNodePos3D(id){
  var nx=parseInt(document.getElementById('ne-x').value)||0;
  var ny=parseInt(document.getElementById('ne-y').value)||0;
  var nz=parseInt(document.getElementById('ne-z').value)||0;
  // Update both ld.children and _fixtures
  (ld.children||[]).forEach(function(c){if(c.id===id){c.x=nx;c.y=ny;c.z=nz;c._placed=true;}});
  _fixtures.forEach(function(f){if(f.id===id){f.x=nx;f.y=ny;f.z=nz;f._placed=true;f.positioned=true;}});
  closeModal();s3dLoadChildren();renderSidebar();
  // Persist to server
  var toSave=_fixtures.filter(_isFixturePlaced).map(function(f){return{id:f.id,x:f.x,y:f.y,z:f.z||0};});
  ra('POST','/api/layout',{fixtures:toSave});
}

function s3dDrop(e){
  e.preventDefault();
  if(_layDragId===null||!_s3d.inited)return;
  // Raycast to ground plane (y=0)
  var rect=_s3d.renderer.domElement.getBoundingClientRect();
  _s3d.mouse.x=((e.clientX-rect.left)/rect.width)*2-1;
  _s3d.mouse.y=-((e.clientY-rect.top)/rect.height)*2+1;
  _s3d.raycaster.setFromCamera(_s3d.mouse,_s3d.camera);
  var plane=new THREE.Plane(new THREE.Vector3(0,1,0),0);
  var pt=new THREE.Vector3();
  _s3d.raycaster.ray.intersectPlane(plane,pt);
  if(pt){
    var mm=_s3dToMm(pt);
    mm.x=Math.max(0,mm.x);mm.y=Math.max(0,mm.y);mm.z=Math.max(0,mm.z);
    var fx=null;(_fixtures||[]).forEach(function(f){if(f.id===_layDragId)fx=f;});
    if(fx){fx.x=mm.x;fx.y=mm.y;fx.z=mm.z;fx._placed=true;
      // Also update ld.children position cache
      var found=false;(ld.children||[]).forEach(function(c){if(c.id===_layDragId){c.x=mm.x;c.y=mm.y;c.z=mm.z;found=true;}});
      if(!found)(ld.children=ld.children||[]).push({id:_layDragId,x:mm.x,y:mm.y,z:mm.z});
      s3dLoadChildren();renderSidebar();
    }
  }
  _layDragId=null;
}

// ── Layout quick-view controls (now just wrappers for setView) ────────────
function layViewReset(){setView(_layView||'front');}
function layViewTop(){setView('top');}
function layViewFront(){setView('front');}

// ── Phase 2: Fixtures ───────────────────────────────────────────────────────
var _fixtures=[];

function loadFixtures(cb){
  ra('GET','/api/fixtures',null,function(d){_fixtures=d||[];renderFixturesSidebar();if(cb)cb();});
}

function renderFixturesSidebar(){
  var el=document.getElementById('lay-fixtures');if(!el)return;
  if(!_fixtures.length){el.innerHTML='<p style="color:#555;font-size:.82em">No fixtures. Add fixtures in the Setup tab.</p>';return;}
  var h='';
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
    var ftBadge=ft==='dmx'?'<span style="font-size:.6em;background:#7c3aed;color:#fff;padding:0 3px;border-radius:2px;margin-left:2px">DMX</span>':ft==='camera'?'<span style="font-size:.6em;background:#0e7490;color:#fff;padding:0 3px;border-radius:2px;margin-left:2px">CAM</span>'+(f.calibrated?'<span style="font-size:.6em;background:#065f46;color:#34d399;padding:0 3px;border-radius:2px;margin-left:2px">\u2713 Cal</span>':''):'';
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
    h+='<label>Profile ID <span style="color:#64748b;font-size:.75em">(optional)</span></label>';
    h+='<input id="fx-prof" value="'+escapeHtml(f.dmxProfileId||'')+'" style="width:100%">';
    h+='<div style="margin-top:.8em;border-top:1px solid #1e293b;padding-top:.6em">';
    h+='<div style="display:flex;justify-content:space-between;align-items:center">';
    h+='<span style="font-weight:bold;font-size:.85em">Test Channels</span>';
    h+='<button class="btn btn-on" onclick="loadFixtureChannels('+id+')" style="font-size:.65em">Load</button>';
    h+='</div>';
    h+='<div id="fx-test-ch"></div>';
    h+='</div>';
    // Orientation calibration panel
    var orient=f.orientation||{};
    h+='<div style="margin-top:.8em;border-top:1px solid #1e293b;padding-top:.6em">';
    h+='<div style="font-weight:bold;font-size:.85em;margin-bottom:.4em">Motor Calibration '
      +(orient.verified?'<span style="color:#4ade80">\u2713 Calibrated</span>':'<span style="color:#64748b">(optional)</span>')+'</div>';
    if(orient.verified){
      h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.3em">'
        +'Pan: '+(orient.panSign>0?'normal':'\u21c4 reversed')
        +' | Tilt: '+(orient.tiltSign<0?'normal':'\u21c5 reversed')
        +'</div>';
    } else {
      h+='<div style="font-size:.78em;color:#64748b;margin-bottom:.3em">Use the upside-down checkbox above if truss-mounted. Run this test with live DMX for precise motor direction calibration.</div>';
    }
    h+='<button class="btn" onclick="_orientTest('+id+')" style="background:#4c1d95;color:#e9d5ff;font-size:.8em">Test with DMX</button>';
    h+='<button class="btn" onclick="closeModal();_moverCalStart('+id+')" style="background:#6b21a8;color:#d8b4fe;margin-left:.5em">Calibrate'+(f.moverCalibrated?' \u2713':'')+'</button>';
    h+='</div>';
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
  h+='<p style="color:#64748b;font-size:.75em;margin-top:.3em">Pan=0 faces forward (+Y depth). Pan=90 faces stage left (+X).</p>';
  if(ft==='dmx'){
    h+='<label style="display:flex;align-items:center;gap:.4em;margin-top:.5em;cursor:pointer"><input id="fx-inverted" type="checkbox"'+(f.mountedInverted?' checked':'')+' style="width:auto"> <span style="font-size:.82em">Mounted upside-down (inverted)</span></label>';
    h+='<p style="color:#64748b;font-size:.72em;margin-top:.2em">Reverses pan and tilt motor direction for truss-mounted fixtures.</p>';
  }
  h+='<div style="margin-top:.8em"><button class="btn btn-on" onclick="saveFixture('+id+',\''+ft+'\')">Save</button></div>';
  document.getElementById('modal-title').textContent='Edit Fixture: '+f.name;
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
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
    el.innerHTML=h;
  });
}

function dmxTestCh(fid,offset,value){
  ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:[{offset:offset,value:value}]},function(){});
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
function _dmxDetailColor(fid,chTypes){
  // Set color channels by type name
  ra('GET','/api/dmx/fixture/'+fid+'/channels',null,function(d){
    if(!d||!d.channels)return;
    var chs=[];
    d.channels.forEach(function(c){
      var v=chTypes[c.type]!==undefined?chTypes[c.type]:(c.type==='dimmer'?255:0);
      chs.push({offset:c.offset,value:v});
    });
    ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:chs},function(){
      // Update sliders
      var sliders=document.querySelectorAll('.dmx-detail-slider');
      sliders.forEach(function(s){
        var off=parseInt(s.dataset.offset);
        var ch=chs.find(function(c){return c.offset===off;});
        if(ch){s.value=ch.value;s.nextElementSibling.textContent=ch.value;}
      });
    });
  });
}
function _dmxDetailWhite(fid){_dmxDetailColor(fid,{red:255,green:255,blue:255,white:255,dimmer:255});}
function _dmxDetailRed(fid){_dmxDetailColor(fid,{red:255,green:0,blue:0,white:0,dimmer:255});}
function _dmxDetailGreen(fid){_dmxDetailColor(fid,{red:0,green:255,blue:0,white:0,dimmer:255});}
function _dmxDetailBlue(fid){_dmxDetailColor(fid,{red:0,green:0,blue:255,white:0,dimmer:255});}
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

// ── Profile Library UI ───────────────────────────────────────────────────────
var _profileCatColors={'par':'#059669','wash':'#2563eb','spot':'#7c3aed','moving-head':'#dc2626','strobe':'#f59e0b','fog':'#6b7280','laser':'#ec4899','other':'#446'};
// ── Universe Patch View ──────────────────────────────────────────────────────
function loadPatchView(){
  ra('GET','/api/fixtures',null,function(fixtures){
    var sel=document.getElementById('patch-uni');if(!sel)return;
    var unis=new Set();
    (fixtures||[]).forEach(function(f){if(f.fixtureType==='dmx'&&f.dmxUniverse)unis.add(f.dmxUniverse);});
    if(!unis.size)unis.add(1);
    var sorted=Array.from(unis).sort(function(a,b){return a-b;});
    sel.innerHTML='';
    sorted.forEach(function(u){var o=document.createElement('option');o.value=u;o.textContent='Universe '+u;sel.appendChild(o);});
    window._patchFixtures=fixtures||[];
    renderPatchView();
  });
}
function renderPatchView(){
  var el=document.getElementById('patch-grid');if(!el)return;
  var uni=parseInt((document.getElementById('patch-uni')||{}).value)||1;
  var fixtures=(window._patchFixtures||[]).filter(function(f){return f.fixtureType==='dmx'&&f.dmxUniverse===uni;});
  // Build channel occupancy map: channel → [fixture names]
  var occ={};
  fixtures.forEach(function(f){
    var start=f.dmxStartAddr||1;
    var count=f.dmxChannelCount||1;
    for(var ch=start;ch<start+count&&ch<=512;ch++){
      if(!occ[ch])occ[ch]=[];
      occ[ch].push(f.name||('Fixture '+f.id));
    }
  });
  // Render 32-column grid (16 rows of 32 = 512 channels)
  var h='<table style="font-size:.65em;border-collapse:collapse;width:100%">';
  h+='<tr><th style="width:30px"></th>';
  for(var c=0;c<32;c++)h+='<th style="padding:1px 2px;color:#556;text-align:center">'+(c+1)+'</th>';
  h+='</tr>';
  for(var row=0;row<16;row++){
    var base=row*32;
    h+='<tr><td style="color:#556;text-align:right;padding-right:4px;font-weight:bold">'+(base+1)+'</td>';
    for(var col=0;col<32;col++){
      var ch=base+col+1;
      var names=occ[ch];
      var conflict=names&&names.length>1;
      var used=names&&names.length>=1;
      var bg=conflict?'#7f1d1d':used?'#1e3a5f':'#0f172a';
      var border=conflict?'#ef4444':used?'#2563eb':'#1e293b';
      var tip=names?names.join(' + '):'Ch '+ch+' (free)';
      h+='<td style="background:'+bg+';border:1px solid '+border+';padding:2px 3px;text-align:center;cursor:default" title="'+escapeHtml(tip)+'">';
      if(conflict)h+='<span style="color:#fca5a5;font-weight:bold">'+ch+'</span>';
      else if(used)h+='<span style="color:#93c5fd">'+ch+'</span>';
      else h+='<span style="color:#334155">'+ch+'</span>';
      h+='</td>';
    }
    h+='</tr>';
  }
  h+='</table>';
  // Legend
  h+='<div style="font-size:.7em;color:#556;margin-top:.4em;display:flex;gap:1em">';
  h+='<span><span style="display:inline-block;width:10px;height:10px;background:#1e3a5f;border:1px solid #2563eb"></span> Used</span>';
  h+='<span><span style="display:inline-block;width:10px;height:10px;background:#7f1d1d;border:1px solid #ef4444"></span> Conflict</span>';
  h+='<span><span style="display:inline-block;width:10px;height:10px;background:#0f172a;border:1px solid #1e293b"></span> Free</span>';
  h+='</div>';
  // Fixture summary below
  if(fixtures.length){
    h+='<div style="font-size:.75em;margin-top:.5em">';
    fixtures.forEach(function(f){
      var start=f.dmxStartAddr||1;var count=f.dmxChannelCount||1;
      h+='<div style="padding:.15em 0"><span style="color:#93c5fd">'+escapeHtml(f.name)+'</span> <span style="color:#556">Ch '+start+'–'+(start+count-1)+' ('+count+'ch)</span></div>';
    });
    h+='</div>';
  }else{
    h+='<div style="font-size:.78em;color:#556;margin-top:.4em">No DMX fixtures in Universe '+uni+'</div>';
  }
  el.innerHTML=h;
}

function showProfileBrowser(){
  _modalStack=[]; // top-level modal — clear stack
  ra('GET','/api/dmx-profiles',null,function(profiles){
    if(!profiles)return;
    var cats=['','par','wash','spot','moving-head','strobe','fog','laser','other'];
    var h='<div style="margin-bottom:.5em"><select id="prof-cat-filter" onchange="_filterProfiles()" style="font-size:.8em">';
    h+='<option value="">All Categories</option>';
    cats.forEach(function(c){if(c)h+='<option value="'+c+'">'+c+'</option>';});
    h+='</select> <input id="prof-search" placeholder="Search..." oninput="_filterProfiles()" style="font-size:.8em;width:180px"></div>';
    h+='<table class="tbl" style="font-size:.82em" id="prof-tbl"><tr><th>Name</th><th>Mfr</th><th>Cat</th><th>Ch</th><th>Source</th><th>Actions</th></tr>';
    profiles.forEach(function(p){
      var src=p.builtin?'<span style="color:#64748b;font-size:.75em">Built-in</span>':'<span style="color:#059669;font-size:.75em">Custom</span>';
      var badge='<span class="badge" style="background:'+(_profileCatColors[p.category]||'#446')+';color:#fff;font-size:.7em">'+escapeHtml(p.category||'other')+'</span>';
      var acts='<button class="btn" onclick="viewProfile(\''+escapeHtml(p.id)+'\')" style="font-size:.7em;background:#335;color:#fff">View</button>';
      if(!p.builtin)acts+=' <button class="btn" onclick="editProfile(\''+escapeHtml(p.id)+'\')" style="font-size:.7em;background:#446;color:#fff">Edit</button>'
        +' <button class="btn" onclick="_commShareProfile(\''+escapeHtml(p.id)+'\')" style="font-size:.7em;background:#7c3aed;color:#e9d5ff">Share</button>'
        +' <button class="btn btn-off" onclick="deleteProfile(\''+escapeHtml(p.id)+'\',\''+escapeHtml(p.name).replace(/'/g,"\\'")+'\')" style="font-size:.7em">Del</button>';
      else acts+=' <button class="btn" onclick="cloneProfile(\''+escapeHtml(p.id)+'\')" style="font-size:.7em;background:#446;color:#fff">Clone</button>';
      h+='<tr data-cat="'+escapeHtml(p.category||'')+'" data-name="'+escapeHtml((p.name||'')+(p.manufacturer||'')).toLowerCase()+'"><td>'+escapeHtml(p.name)+'</td><td>'+escapeHtml(p.manufacturer||'')+'</td><td>'+badge+'</td><td>'+p.channelCount+'</td><td>'+src+'</td><td>'+acts+'</td></tr>';
    });
    h+='</table>';
    document.getElementById('modal-title').textContent='Fixture Profile Library ('+profiles.length+')';
    document.getElementById('modal-body').innerHTML=h;
    document.getElementById('modal').style.display='block';
  });
}
function _filterProfiles(){
  var cat=(document.getElementById('prof-cat-filter')||{}).value||'';
  var q=((document.getElementById('prof-search')||{}).value||'').toLowerCase();
  var rows=document.querySelectorAll('#prof-tbl tr[data-cat]');
  rows.forEach(function(r){
    var show=(!cat||r.dataset.cat===cat)&&(!q||r.dataset.name.indexOf(q)>=0);
    r.style.display=show?'':'none';
  });
}
function viewProfile(id){
  _pushModal(); // save profile browser state
  ra('GET','/api/dmx-profiles/'+id,null,function(p){
    if(!p){_popModal();return;}
    var h='<p><b>'+escapeHtml(p.name)+'</b> by '+escapeHtml(p.manufacturer||'?')+'</p>';
    h+='<p style="font-size:.82em;color:#94a3b8">'+escapeHtml(p.category||'other')+' | '+p.channelCount+' ch | Color: '+escapeHtml(p.colorMode||'?')+' | Beam: '+p.beamWidth+'\u00b0';
    if(p.panRange)h+=' | Pan: '+p.panRange+'\u00b0';
    if(p.tiltRange)h+=' | Tilt: '+p.tiltRange+'\u00b0';
    h+='</p>';
    h+='<table class="tbl" style="font-size:.78em;margin-top:.5em"><tr><th>Off</th><th>Name</th><th>Type</th><th>Bits</th><th>Default</th><th>Capabilities</th></tr>';
    var topCaps=p.capabilities||{};
    (p.channels||[]).forEach(function(ch){
      var capTxt='';
      // Per-channel capabilities (builtin format) or top-level dict (custom format)
      var caps=ch.capabilities||(topCaps[String(ch.offset)]?topCaps[String(ch.offset)].map(function(c){
        return {range:[c.rangeStart!=null?c.rangeStart:c.range?c.range[0]:0,c.rangeEnd!=null?c.rangeEnd:c.range?c.range[1]:255],label:c.label,type:c.type};
      }):null);
      if(caps&&caps.length>1){
        capTxt=caps.map(function(c){var r=c.range||[0,255];return r[0]+'-'+r[1]+': '+escapeHtml(c.label||c.type||'');}).join('<br>');
      }else if(caps&&caps.length===1){
        capTxt=escapeHtml(caps[0].label||caps[0].type||'');
      }
      h+='<tr><td>'+ch.offset+'</td><td>'+escapeHtml(ch.name)+'</td><td><code>'+escapeHtml(ch.type)+'</code></td><td>'+(ch.bits||8)+'</td><td>'+(ch.default!==undefined?ch.default:0)+'</td><td style="font-size:.85em">'+capTxt+'</td></tr>';
    });
    h+='</table>';
    document.getElementById('modal-title').textContent='Profile: '+p.name;
    document.getElementById('modal-body').innerHTML=h;
    document.getElementById('modal').style.display='block';
  });
}
function deleteProfile(id,name){
  if(!confirm('Delete profile "'+name+'"?'))return;
  ra('DELETE','/api/dmx-profiles/'+id,null,function(r){
    if(r&&r.ok){document.getElementById('hs').textContent='Profile deleted';showProfileBrowser();}
    else document.getElementById('hs').textContent='Delete failed: '+(r&&r.err||'unknown');
  });
}
function cloneProfile(id){
  ra('GET','/api/dmx-profiles/'+id,null,function(p){
    if(!p)return;
    p.id=p.id+'-custom';p.name=p.name+' (Custom)';delete p.builtin;
    _openProfileEditor(p);
  });
}
function editProfile(id){
  ra('GET','/api/dmx-profiles/'+id,null,function(p){if(p)_openProfileEditor(p,true);});
}
function showProfileEditor(){_openProfileEditor({id:'',name:'',manufacturer:'',category:'par',channels:[{offset:0,name:'Dimmer',type:'dimmer',capabilities:[{range:[0,255],type:'Intensity',label:'Dimmer 0-100%'}]}],colorMode:'rgb',beamWidth:25,panRange:0,tiltRange:0});}
var _profEditChTypes=['dimmer','red','green','blue','white','amber','uv','pan','pan-fine','tilt','tilt-fine','strobe','gobo','gobo-rotation','prism','focus','zoom','frost','color-wheel','speed','macro','reset'];
var _profEditCapTypes=['ColorIntensity','Intensity','Pan','PanContinuous','Tilt','TiltContinuous','ShutterStrobe','WheelSlot','WheelRotation','Prism','Focus','Zoom','Frost','Speed','Maintenance','Effect','NoFunction','Generic'];
function _openProfileEditor(p,isEdit){
  var h='<div style="display:flex;gap:.5em;flex-wrap:wrap;margin-bottom:.5em">';
  h+='<div><label style="font-size:.78em">ID <span style="color:#64748b">(lowercase, hyphens only)</span></label><input id="pe-id" value="'+escapeHtml(p.id||'')+'" style="width:180px" oninput="this.value=this.value.toLowerCase().replace(/[^a-z0-9\\-]/g,\'-\').replace(/-+/g,\'-\').replace(/^-/,\'\')" placeholder="my-fixture-name"'+(isEdit?' disabled':'')+'></div>';
  h+='<div><label style="font-size:.78em">Name</label><input id="pe-name" value="'+escapeHtml(p.name||'')+'" style="width:200px"></div>';
  h+='<div><label style="font-size:.78em">Manufacturer</label><input id="pe-mfr" value="'+escapeHtml(p.manufacturer||'')+'" style="width:150px"></div>';
  h+='</div><div style="display:flex;gap:.5em;flex-wrap:wrap;margin-bottom:.5em">';
  var cats=['par','wash','spot','moving-head','strobe','fog','laser','other'];
  h+='<div><label style="font-size:.78em">Category</label><select id="pe-cat">';
  cats.forEach(function(c){h+='<option'+(c===p.category?' selected':'')+'>'+c+'</option>';});
  h+='</select></div>';
  var cmodes=['rgb','cmy','rgbw','rgba','single'];
  h+='<div><label style="font-size:.78em">Color Mode</label><select id="pe-cm">';
  cmodes.forEach(function(c){h+='<option'+(c===p.colorMode?' selected':'')+'>'+c+'</option>';});
  h+='</select></div>';
  h+='<div><label style="font-size:.78em">Beam \u00b0</label><input id="pe-bw" type="number" value="'+(p.beamWidth||0)+'" style="width:60px"></div>';
  h+='<div><label style="font-size:.78em">Pan \u00b0</label><input id="pe-pan" type="number" value="'+(p.panRange||0)+'" style="width:60px"></div>';
  h+='<div><label style="font-size:.78em">Tilt \u00b0</label><input id="pe-tilt" type="number" value="'+(p.tiltRange||0)+'" style="width:60px"></div>';
  h+='</div>';
  h+='<div style="font-weight:bold;font-size:.82em;margin:.5em 0">Channels</div>';
  h+='<div id="pe-chs"></div>';
  h+='<button class="btn" onclick="_peAddCh()" style="font-size:.7em;margin-top:.3em">+ Add Channel</button>';
  // Emitters (multi-emitter fixtures)
  h+='<div style="font-weight:bold;font-size:.82em;margin:.5em 0">Emitters <span style="color:#64748b;font-size:.85em">(multi-emitter/matrix — optional)</span></div>';
  h+='<div id="pe-emitters"></div>';
  h+='<button class="btn" onclick="_peAddEmitter()" style="font-size:.7em;margin-top:.3em">+ Add Emitter</button>';
  h+='<div style="margin-top:.8em"><button class="btn btn-on" onclick="_peSave('+(isEdit?'true':'false')+')">'+(isEdit?'Update':'Create')+'</button></div>';
  document.getElementById('modal-title').textContent=(isEdit?'Edit':'New')+' Fixture Profile';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  window._peChannels=JSON.parse(JSON.stringify(p.channels||[]));
  window._peEmitters=JSON.parse(JSON.stringify(p.emitters||[]));
  _peRenderChs();
  _peRenderEmitters();
}
function _peRenderChs(){
  var el=document.getElementById('pe-chs');if(!el)return;
  var h='<table class="tbl" style="font-size:.75em"><tr><th>Off</th><th>Name</th><th>Type</th><th>Bits</th><th>Default</th><th>Capabilities</th><th></th></tr>';
  (window._peChannels||[]).forEach(function(ch,i){
    var typeOpts='';_profEditChTypes.forEach(function(t){typeOpts+='<option'+(t===ch.type?' selected':'')+'>'+t+'</option>';});
    var capSummary=(ch.capabilities||[]).length+' cap'+(ch.capabilities&&ch.capabilities.length!==1?'s':'');
    h+='<tr><td>'+ch.offset+'</td><td><input value="'+escapeHtml(ch.name||'')+'" style="width:90px;font-size:.9em" onchange="window._peChannels['+i+'].name=this.value"></td>';
    h+='<td><select style="font-size:.85em" onchange="window._peChannels['+i+'].type=this.value">'+typeOpts+'</select></td>';
    h+='<td><select style="font-size:.85em" onchange="window._peChannels['+i+'].bits=parseInt(this.value);_peRecalcOffsets()"><option'+(ch.bits!==16?' selected':'')+'>8</option><option'+(ch.bits===16?' selected':'')+' value="16">16</option></select></td>';
    h+='<td><input type="number" value="'+(ch.default!==undefined?ch.default:0)+'" min="0" max="'+(ch.bits===16?65535:255)+'" style="width:50px;font-size:.9em" onchange="window._peChannels['+i+'].default=parseInt(this.value)||0"></td>';
    h+='<td><button class="btn" onclick="_peEditCaps('+i+')" style="font-size:.7em;background:#335;color:#fff">'+capSummary+'</button></td>';
    h+='<td><button class="btn btn-off" onclick="_peDelCh('+i+')" style="font-size:.7em">\u2715</button></td></tr>';
  });
  el.innerHTML=h+'</table>';
}
function _peAddCh(){
  var chs=window._peChannels||[];
  var off=0;if(chs.length)off=chs[chs.length-1].offset+(chs[chs.length-1].bits===16?2:1);
  chs.push({offset:off,name:'Ch '+(chs.length+1),type:'dimmer',capabilities:[{range:[0,255],type:'Intensity',label:'0-100%'}]});
  _peRenderChs();
}
function _peDelCh(i){window._peChannels.splice(i,1);_peRecalcOffsets();_peRenderChs();}
function _peRecalcOffsets(){
  var off=0;(window._peChannels||[]).forEach(function(ch){ch.offset=off;off+=ch.bits===16?2:1;});
  _peRenderChs();
}
function _peEditCaps(chIdx){
  _pushModal(); // save profile editor state
  var ch=window._peChannels[chIdx];
  var caps=ch.capabilities||[];
  var h='<p style="font-size:.82em;color:#94a3b8">Channel: '+escapeHtml(ch.name)+' ('+ch.type+')</p>';
  h+='<div id="pe-caps-list"></div>';
  h+='<button class="btn" onclick="_peAddCap('+chIdx+')" style="font-size:.7em;margin-top:.3em">+ Add Range</button>';
  h+='<div style="margin-top:.5em"><button class="btn btn-on" onclick="_peSaveCaps('+chIdx+')">Save Capabilities</button></div>';
  document.getElementById('modal-title').textContent='Capabilities: '+ch.name;
  document.getElementById('modal-body').innerHTML=h;
  window._peCaps=JSON.parse(JSON.stringify(caps));
  _peRenderCaps(chIdx);
}
function _peRenderCaps(chIdx){
  var el=document.getElementById('pe-caps-list');if(!el)return;
  var maxVal=(window._peChannels&&window._peChannels[chIdx]&&window._peChannels[chIdx].bits===16)?65535:255;
  // Visual range bar + table
  var h='<div style="position:relative;height:24px;background:#111;border:1px solid #334;border-radius:3px;margin-bottom:.5em">';
  var capColors={'Intensity':'#3b82f6','ColorIntensity':'#ef4444','Pan':'#22d3ee','Tilt':'#a78bfa','ShutterStrobe':'#f59e0b','WheelSlot':'#10b981','Speed':'#f97316','Generic':'#64748b','NoFunction':'#334155'};
  (window._peCaps||[]).forEach(function(c,j){
    var pct0=c.range[0]/maxVal*100,pct1=(c.range[1]+1)/maxVal*100;
    var w=pct1-pct0;
    var col=capColors[c.type]||'#64748b';
    h+='<div style="position:absolute;left:'+pct0+'%;width:'+w+'%;height:100%;background:'+col+';opacity:0.6;border-radius:2px" title="'+c.range[0]+'-'+c.range[1]+': '+(c.label||c.type)+'"></div>';
  });
  h+='</div>';
  h+='<table class="tbl" style="font-size:.75em"><tr><th>Min</th><th>Max</th><th>Type</th><th>Label</th><th>Default</th><th></th></tr>';
  (window._peCaps||[]).forEach(function(c,j){
    var tOpts='';_profEditCapTypes.forEach(function(t){tOpts+='<option'+(t===c.type?' selected':'')+'>'+t+'</option>';});
    h+='<tr><td><input type="number" value="'+c.range[0]+'" min="0" max="'+maxVal+'" style="width:55px;font-size:.9em" onchange="window._peCaps['+j+'].range[0]=parseInt(this.value);_peRenderCaps('+chIdx+')"></td>';
    h+='<td><input type="number" value="'+c.range[1]+'" min="0" max="'+maxVal+'" style="width:55px;font-size:.9em" onchange="window._peCaps['+j+'].range[1]=parseInt(this.value);_peRenderCaps('+chIdx+')"></td>';
    h+='<td><select style="font-size:.85em" onchange="window._peCaps['+j+'].type=this.value;_peRenderCaps('+chIdx+')">'+tOpts+'</select></td>';
    h+='<td><input value="'+escapeHtml(c.label||'')+'" style="width:120px;font-size:.9em" onchange="window._peCaps['+j+'].label=this.value"></td>';
    h+='<td><input type="number" value="'+(c.default!==undefined?c.default:c.range[0])+'" min="'+c.range[0]+'" max="'+c.range[1]+'" style="width:50px;font-size:.9em" onchange="window._peCaps['+j+'].default=parseInt(this.value)"></td>';
    h+='<td><button class="btn btn-off" onclick="window._peCaps.splice('+j+',1);_peRenderCaps('+chIdx+')" style="font-size:.7em">\u2715</button></td></tr>';
  });
  el.innerHTML=h+'</table>';
}
function _peAddCap(chIdx){
  var caps=window._peCaps||[];
  var startVal=caps.length?caps[caps.length-1].range[1]+1:0;
  caps.push({range:[startVal,255],type:'Intensity',label:'',default:startVal});
  _peRenderCaps(chIdx);
}
function _peSaveCaps(chIdx){
  window._peChannels[chIdx].capabilities=JSON.parse(JSON.stringify(window._peCaps||[]));
  // Pop the stack to restore profile editor (re-render channels table)
  if(_popModal()){
    _peRenderChs(); // refresh channel table with updated caps
  }else{
    _openProfileEditor(_peGatherProfile(),!!document.getElementById('pe-id').disabled);
  }
}
function _peGatherProfile(){
  return{id:(document.getElementById('pe-id')||{}).value||'',name:(document.getElementById('pe-name')||{}).value||'',
    manufacturer:(document.getElementById('pe-mfr')||{}).value||'',category:(document.getElementById('pe-cat')||{}).value||'par',
    channels:window._peChannels||[],channelCount:(window._peChannels||[]).length,
    colorMode:(document.getElementById('pe-cm')||{}).value||'rgb',
    beamWidth:parseInt((document.getElementById('pe-bw')||{}).value)||0,
    panRange:parseInt((document.getElementById('pe-pan')||{}).value)||0,
    tiltRange:parseInt((document.getElementById('pe-tilt')||{}).value)||0,
    emitters:window._peEmitters&&window._peEmitters.length?window._peEmitters:undefined};
}
function _peSave(isEdit){
  var p=_peGatherProfile();
  // Auto-generate slug from name if ID is empty
  if(!p.id&&p.name){p.id=p.name.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,128);}
  // Ensure ID is a valid slug
  p.id=(p.id||'').toLowerCase().replace(/[^a-z0-9\-]/g,'-').replace(/-+/g,'-').replace(/^-|-$/g,'');
  if(!p.id||!p.name){document.getElementById('hs').textContent='Profile needs an ID and Name';return;}
  var method=isEdit?'PUT':'POST';
  var url=isEdit?'/api/dmx-profiles/'+p.id:'/api/dmx-profiles';
  ra(method,url,p,function(r){
    if(r&&r.ok){document.getElementById('hs').textContent='Profile saved: '+p.name;closeModal();if(typeof loadDmxProfiles==='function')loadDmxProfiles();}
    else if(r&&r.err&&r.err.indexOf('built-in')>=0){
      // Built-in profile — fork as custom copy
      if(!p.id.endsWith('-custom'))p.id=p.id+'-custom';
      p.builtin=false;
      ra('POST','/api/dmx-profiles',p,function(r2){
        if(r2&&r2.ok){document.getElementById('hs').textContent='Saved as custom copy: '+p.id;closeModal();if(typeof loadDmxProfiles==='function')loadDmxProfiles();}
        else document.getElementById('hs').textContent='Save failed: '+(r2&&r2.err||'unknown');
      });
    }
    else document.getElementById('hs').textContent='Save failed: '+(r&&r.err||'unknown');
  });
}
function _peRenderEmitters(){
  var el=document.getElementById('pe-emitters');if(!el)return;
  var ems=window._peEmitters||[];
  if(!ems.length){el.innerHTML='<span style="color:#556;font-size:.78em">No emitters (single point source)</span>';return;}
  var h='<table class="tbl" style="font-size:.78em"><tr><th>#</th><th>Name</th><th>X</th><th>Y</th><th>Z</th><th></th></tr>';
  ems.forEach(function(em,i){
    var o=em.offset||[0,0,0];
    h+='<tr><td>'+(i+1)+'</td>';
    h+='<td><input value="'+escapeHtml(em.name||'')+'" style="width:80px" onchange="window._peEmitters['+i+'].name=this.value"></td>';
    h+='<td><input type="number" value="'+o[0]+'" style="width:50px" onchange="window._peEmitters['+i+'].offset[0]=parseInt(this.value)||0"></td>';
    h+='<td><input type="number" value="'+o[1]+'" style="width:50px" onchange="window._peEmitters['+i+'].offset[1]=parseInt(this.value)||0"></td>';
    h+='<td><input type="number" value="'+o[2]+'" style="width:50px" onchange="window._peEmitters['+i+'].offset[2]=parseInt(this.value)||0"></td>';
    h+='<td><span style="cursor:pointer;color:#f66" onclick="window._peEmitters.splice('+i+',1);_peRenderEmitters()">\u2716</span></td></tr>';
  });
  h+='</table>';
  el.innerHTML=h;
}
function _peAddEmitter(){
  window._peEmitters=window._peEmitters||[];
  var n=window._peEmitters.length;
  window._peEmitters.push({name:'Emitter '+(n+1),offset:[n*30,0,0]});
  _peRenderEmitters();
}
function showOflImport(){
  var h='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.5em">Paste OFL fixture JSON from <a href="https://open-fixture-library.org" target="_blank" style="color:#3b82f6">open-fixture-library.org</a></p>';
  h+='<textarea id="ofl-json" rows="12" style="width:100%;font-family:monospace;font-size:.75em" placeholder="Paste OFL fixture JSON here..."></textarea>';
  h+='<div style="margin-top:.5em"><label style="font-size:.78em">Mode index (blank = all modes)</label><input id="ofl-mode" type="number" min="0" style="width:60px"></div>';
  h+='<div style="margin-top:.5em"><button class="btn btn-on" onclick="_oflImport()">Import</button></div>';
  document.getElementById('modal-title').textContent='Import from Open Fixture Library';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}
function _oflImport(){
  var raw=document.getElementById('ofl-json').value.trim();
  if(!raw){document.getElementById('hs').textContent='Paste OFL JSON first';return;}
  var ofl;try{ofl=JSON.parse(raw);}catch(e){document.getElementById('hs').textContent='Invalid JSON: '+e.message;return;}
  var modeVal=document.getElementById('ofl-mode').value;
  var body={ofl:ofl};if(modeVal!=='')body.mode=parseInt(modeVal);
  ra('POST','/api/dmx-profiles/ofl/import-json',body,function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Imported '+r.imported+' profile(s): '+(r.profiles||[]).join(', ');
      closeModal();
    }else{
      document.getElementById('hs').textContent='Import failed: '+(r&&r.err||'unknown');
    }
  });
}
function showOflBrowse(){
  var h='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.5em">Browse the <a href="https://open-fixture-library.org" target="_blank" style="color:#3b82f6">Open Fixture Library</a></p>';
  h+='<div style="display:flex;gap:.4em;margin-bottom:.6em;flex-wrap:wrap">';
  h+='<input id="ofl-q" type="text" placeholder="Search by fixture name, manufacturer, or category..." style="flex:1;min-width:200px;padding:.4em" onkeydown="if(event.key===\'Enter\')_oflSearch()">';
  h+='<button class="btn btn-on" onclick="_oflSearch()">Search</button>';
  h+='<button class="btn" style="background:#1e293b;color:#94a3b8;font-size:.78em" onclick="_oflShowMfrs()">Manufacturers</button>';
  h+='<button class="btn" style="background:#1e293b;color:#94a3b8;font-size:.78em" onclick="_oflBrowseAll()">Browse All</button>';
  h+='</div>';
  h+='<div id="ofl-results" style="max-height:340px;overflow-y:auto"><p style="color:#555;font-size:.82em">Search by name (e.g. "par", "moving head"), manufacturer (e.g. "chauvet"), or category (e.g. "Color Changer")</p></div>';
  document.getElementById('modal-title').textContent='Open Fixture Library';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='flex';
  setTimeout(function(){document.getElementById('ofl-q').focus();},100);
}
function _oflBrowseAll(){
  var el=document.getElementById('ofl-results');if(!el)return;
  el.innerHTML='<p style="color:#888;font-size:.82em">Loading all fixtures (first load may take a few seconds)...</p>';
  ra('GET','/api/dmx-profiles/ofl/browse?limit=200',null,function(r){
    if(!r||r.err){el.innerHTML='<p style="color:#f66;font-size:.82em">'+(r&&r.err||'Failed')+'</p>';return;}
    var total=r.total||0,fixtures=r.fixtures||[];
    var h='<div style="font-size:.78em;color:#64748b;margin-bottom:.3em">'+total+' fixtures total (showing '+fixtures.length+')</div>';
    _oflRenderResults(fixtures);
  });
}
function _oflSearch(){
  var q=document.getElementById('ofl-q').value.trim();
  if(q.length<2){document.getElementById('ofl-results').innerHTML='<p style="color:#f66;font-size:.82em">Enter at least 2 characters</p>';return;}
  document.getElementById('ofl-results').innerHTML='<p style="color:#888;font-size:.82em">Searching all fixtures (first search builds index, may take a few seconds)...</p>';
  ra('GET','/api/dmx-profiles/ofl/search?q='+encodeURIComponent(q)+'&limit=100',null,function(r){
    var el=document.getElementById('ofl-results');if(!el)return;
    if(!r||r.err){el.innerHTML='<p style="color:#f66;font-size:.82em">'+(r&&r.err||'Search failed')+'</p>';return;}
    if(!r.length){el.innerHTML='<p style="color:#888;font-size:.82em">No results for "'+escapeHtml(q)+'"</p>';return;}
    _oflRenderResults(r);
  });
}
function _oflRenderResults(r){
  var el=document.getElementById('ofl-results');if(!el)return;
  var h='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.3em">';
  h+='<span style="font-size:.78em;color:#64748b">'+r.length+' fixtures</span>';
  if(r.length>1)h+='<button class="btn" style="font-size:.7em;padding:.2em .5em;background:#14532d;color:#86efac" onclick="_oflImportAll()">Import All '+r.length+'</button>';
  h+='</div>';
  h+='<table style="width:100%;font-size:.82em" id="ofl-table"><tr style="color:#888"><th style="text-align:left">Fixture</th><th style="text-align:left">Manufacturer</th><th></th></tr>';
  r.forEach(function(f){
    h+='<tr data-mfr="'+f.manufacturer+'" data-fix="'+f.fixture+'"><td style="padding:.3em .5em">'+escapeHtml(f.name)+'</td>';
    h+='<td style="padding:.3em .5em;color:#94a3b8">'+escapeHtml(f.manufacturerName)+'</td>';
    h+='<td><button class="btn" style="font-size:.75em;padding:.2em .5em;background:#14532d;color:#86efac" onclick="_oflImportById(\''+f.manufacturer+'\',\''+f.fixture+'\')">Import</button></td></tr>';
  });
  h+='</table>';
  if(r.length>=100)h+='<p style="color:#888;font-size:.78em;margin-top:.3em">Showing first 100 — refine search for more</p>';
  el.innerHTML=h;
  window._oflLastResults=r;
}
function _oflShowMfrs(){
  var el=document.getElementById('ofl-results');if(!el)return;
  el.innerHTML='<p style="color:#888;font-size:.82em">Loading manufacturers...</p>';
  ra('GET','/api/dmx-profiles/ofl/manufacturers',null,function(r){
    if(!r||r.err){el.innerHTML='<p style="color:#f66;font-size:.82em">'+(r&&r.err||'Failed')+'</p>';return;}
    var total=0;r.forEach(function(m){total+=m.fixtureCount;});
    var h='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.3em">';
    h+='<span style="font-size:.78em;color:#64748b">'+r.length+' manufacturers, '+total+' fixtures</span></div>';
    h+='<div style="display:flex;flex-wrap:wrap;gap:.3em">';
    r.forEach(function(m){
      h+='<button class="btn" style="font-size:.72em;padding:.2em .5em;background:#1e293b;color:#e2e8f0" onclick="_oflBrowseMfr(\''+m.key+'\')" title="'+m.fixtureCount+' fixtures">'+escapeHtml(m.name)+' <span style="color:#64748b">('+m.fixtureCount+')</span></button>';
    });
    h+='</div>';
    el.innerHTML=h;
  });
}
function _oflBrowseMfr(key){
  var el=document.getElementById('ofl-results');if(!el)return;
  el.innerHTML='<p style="color:#888;font-size:.82em">Loading...</p>';
  ra('GET','/api/dmx-profiles/ofl/manufacturer/'+key,null,function(r){
    if(!r||r.err){el.innerHTML='<p style="color:#f66;font-size:.82em">'+(r&&r.err||'Failed')+'</p>';return;}
    var fixtures=r.fixtures||[];
    var mapped=fixtures.map(function(f){return{manufacturer:key,manufacturerName:r.name,fixture:f.key,name:f.name};});
    var h='<div style="margin-bottom:.5em"><button class="btn" style="font-size:.72em;padding:.15em .4em;background:#334155;color:#94a3b8" onclick="_oflShowMfrs()">&larr; Back</button>';
    h+=' <b style="font-size:.85em">'+escapeHtml(r.name)+'</b> <span style="font-size:.78em;color:#64748b">('+fixtures.length+' fixtures)</span>';
    if(fixtures.length>1)h+=' <button class="btn" style="font-size:.7em;padding:.2em .5em;background:#14532d;color:#86efac;margin-left:.5em" onclick="_oflImportMfr(\''+key+'\')">Import All</button>';
    h+='</div>';
    el.innerHTML=h;
    window._oflLastResults=mapped;
    var tbl=document.createElement('div');
    var th='<table style="width:100%;font-size:.82em" id="ofl-table"><tr style="color:#888"><th style="text-align:left">Fixture</th><th></th></tr>';
    mapped.forEach(function(f){
      th+='<tr><td style="padding:.3em .5em">'+escapeHtml(f.name)+'</td>';
      th+='<td><button class="btn" style="font-size:.75em;padding:.2em .5em;background:#14532d;color:#86efac" onclick="_oflImportById(\''+f.manufacturer+'\',\''+f.fixture+'\')">Import</button></td></tr>';
    });
    th+='</table>';
    tbl.innerHTML=th;
    el.appendChild(tbl);
  });
}
function _oflImportById(mfr,fix){
  document.getElementById('hs').textContent='Importing '+fix+'...';
  ra('POST','/api/dmx-profiles/ofl/import-by-id',{manufacturer:mfr,fixture:fix},function(r){
    if(r&&r.ok){
      var names=(r.profiles||[]).map(function(p){return p.name;}).join(', ');
      document.getElementById('hs').textContent='Imported: '+names;
      if(typeof loadDmxProfiles==='function')loadDmxProfiles();
    }else{
      document.getElementById('hs').textContent='Import failed: '+(r&&r.err||'unknown');
    }
  });
}
function _oflImportMfr(mfrKey){
  document.getElementById('hs').textContent='Importing all fixtures from '+mfrKey+'...';
  ra('POST','/api/dmx-profiles/ofl/import-by-id',{manufacturer:mfrKey},function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Imported '+(r.imported||0)+' profiles from '+mfrKey;
      closeModal();if(typeof loadDmxProfiles==='function')loadDmxProfiles();
    }else{
      document.getElementById('hs').textContent='Import failed: '+(r&&r.err||'unknown');
    }
  });
}
function _oflImportAll(){
  var r=window._oflLastResults;if(!r||!r.length)return;
  if(!confirm('Import all '+r.length+' fixtures? This may take a moment.'))return;
  document.getElementById('hs').textContent='Importing '+r.length+' fixtures...';
  var done=0,total=r.length;
  r.forEach(function(f){
    ra('POST','/api/dmx-profiles/ofl/import-by-id',{manufacturer:f.manufacturer,fixture:f.fixture},function(){
      done++;
      if(done===total){
        document.getElementById('hs').textContent='Imported '+done+' fixtures';
        closeModal();if(typeof loadDmxProfiles==='function')loadDmxProfiles();
      }else if(done%10===0){
        document.getElementById('hs').textContent='Importing... '+done+'/'+total;
      }
    });
  });
}
// ── Community Profile Browser ────────────────────────────────────────────
function showCommunityBrowser(){
  _modalStack=[];
  var h='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.5em">Browse and share fixtures with the SlyLED community</p>';
  h+='<div style="display:flex;gap:.4em;margin-bottom:.6em;flex-wrap:wrap">';
  h+='<input id="comm-q" type="text" placeholder="Search community fixtures..." style="flex:1;min-width:180px;padding:.4em" onkeydown="if(event.key===\'Enter\')_commSearch()">';
  h+='<button class="btn btn-on" onclick="_commSearch()">Search</button>';
  h+='<button class="btn" style="background:#1e293b;color:#94a3b8;font-size:.78em" onclick="_commRecent()">Recent</button>';
  h+='<button class="btn" style="background:#1e293b;color:#94a3b8;font-size:.78em" onclick="_commPopular()">Popular</button>';
  h+='</div>';
  h+='<div id="comm-results" style="max-height:300px;overflow-y:auto"></div>';
  h+='<div style="border-top:1px solid #1e293b;padding-top:.5em;margin-top:.5em">';
  h+='<div id="comm-stats" style="font-size:.75em;color:#64748b"></div></div>';
  document.getElementById('modal-title').textContent='Community Fixture Library';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='flex';
  ra('GET','/api/dmx-profiles/community/stats',null,function(r){
    var el=document.getElementById('comm-stats');if(!el||!r||!r.data)return;
    el.textContent=r.data.total+' profiles shared by the community';
  });
  setTimeout(function(){document.getElementById('comm-q').focus();},100);
}
function _commSearch(){
  var q=document.getElementById('comm-q').value.trim();
  var el=document.getElementById('comm-results');if(!el)return;
  if(q.length<2){el.innerHTML='<span style="color:#f66;font-size:.82em">Enter at least 2 characters</span>';return;}
  el.innerHTML='<span style="color:#888;font-size:.82em">Searching...</span>';
  ra('GET','/api/dmx-profiles/community/search?q='+encodeURIComponent(q),null,function(r){_commRender(r);});
}
function _commRecent(){
  var el=document.getElementById('comm-results');if(el)el.innerHTML='<span style="color:#888;font-size:.82em">Loading...</span>';
  ra('GET','/api/dmx-profiles/community/recent',null,function(r){
    if(r&&r.data&&Array.isArray(r.data))_commRenderList(r.data);
    else if(r&&r.data&&r.data.profiles)_commRenderList(r.data.profiles);
    else{var el=document.getElementById('comm-results');if(el)el.innerHTML='<span style="color:#888">No results</span>';}
  });
}
function _commPopular(){
  var el=document.getElementById('comm-results');if(el)el.innerHTML='<span style="color:#888;font-size:.82em">Loading...</span>';
  ra('GET','/api/dmx-profiles/community/popular',null,function(r){
    if(r&&r.data&&Array.isArray(r.data))_commRenderList(r.data);
    else if(r&&r.data&&r.data.profiles)_commRenderList(r.data.profiles);
    else{var el=document.getElementById('comm-results');if(el)el.innerHTML='<span style="color:#888">No results</span>';}
  });
}
function _commRender(r){
  if(!r||!r.data){var el=document.getElementById('comm-results');if(el)el.innerHTML='<span style="color:#f66">Failed</span>';return;}
  var profiles=r.data.profiles||r.data||[];
  _commRenderList(profiles);
}
function _commRenderList(profiles){
  var el=document.getElementById('comm-results');if(!el)return;
  if(!profiles.length){el.innerHTML='<span style="color:#888;font-size:.82em">No profiles found</span>';return;}
  var h='<table style="width:100%;font-size:.82em"><tr style="color:#888"><th style="text-align:left">Fixture</th><th style="text-align:left">Manufacturer</th><th>Ch</th><th>DL</th><th></th></tr>';
  profiles.forEach(function(p){
    h+='<tr><td style="padding:.3em .5em">'+escapeHtml(p.name||p.slug)+'</td>';
    h+='<td style="padding:.3em .5em;color:#94a3b8">'+escapeHtml(p.manufacturer||'')+'</td>';
    h+='<td style="text-align:center">'+p.channel_count+'</td>';
    h+='<td style="text-align:center;color:#64748b">'+(p.downloads||0)+'</td>';
    h+='<td><button class="btn" style="font-size:.72em;padding:.2em .5em;background:#14532d;color:#86efac" onclick="_commDownload(\''+escapeHtml(p.slug)+'\')">Download</button></td></tr>';
  });
  el.innerHTML=h+'</table>';
}
function _commDownload(slug){
  document.getElementById('hs').textContent='Downloading '+slug+'...';
  ra('POST','/api/dmx-profiles/community/download',{slug:slug},function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Downloaded and imported: '+slug;
      if(typeof loadDmxProfiles==='function')loadDmxProfiles();
    }else{
      document.getElementById('hs').textContent='Download failed: '+(r&&r.err||'unknown');
    }
  });
}
function _commShareProfile(profileId){
  // Step 1: Check for duplicates first
  document.getElementById('hs').textContent='Checking for duplicates...';
  ra('POST','/api/dmx-profiles/community/check',{profileId:profileId},function(r){
    if(!r||!r.ok||!r.data){
      document.getElementById('hs').textContent='Check failed: '+(r&&r.error||r&&r.err||'unknown');
      return;
    }
    var data=r.data;
    if(data.duplicate){
      var msg='This profile has the same channels as "'+escapeHtml(data.duplicate_name||data.duplicate_of)+'" already in the community. Share anyway?';
      if(!confirm(msg))return;
    }
    if(!data.slug_available){
      document.getElementById('hs').textContent='A profile with this ID already exists in the community.';
      return;
    }
    // Step 2: Confirm upload
    if(!confirm('Share "'+profileId+'" to the SlyLED community? Other users will be able to download it.'))return;
    // Step 3: Upload
    document.getElementById('hs').textContent='Uploading to community...';
    ra('POST','/api/dmx-profiles/community/upload',{profileId:profileId},function(ur){
      if(ur&&ur.ok){
        document.getElementById('hs').textContent='Shared to community! Profile: '+profileId;
      }else{
        var err=ur&&(ur.error||ur.err)||'unknown';
        if(err.indexOf('already exists')>=0){
          document.getElementById('hs').textContent='This profile ID already exists in the community.';
        }else if(err.indexOf('Duplicate')>=0){
          document.getElementById('hs').textContent='Duplicate detected: '+(ur.duplicate_name||err);
        }else{
          document.getElementById('hs').textContent='Share failed: '+err;
        }
      }
    });
  });
}

function importProfileBundle(input){
  if(!input.files||!input.files[0])return;
  var reader=new FileReader();
  reader.onload=function(e){
    var data;try{data=JSON.parse(e.target.result);}catch(err){document.getElementById('hs').textContent='Invalid JSON file';return;}
    if(!Array.isArray(data)){document.getElementById('hs').textContent='File must contain a JSON array of profiles';return;}
    ra('POST','/api/dmx-profiles/import',data,function(r){
      if(r&&r.ok){
        document.getElementById('hs').textContent='Imported '+r.imported+' profile(s), skipped '+r.skipped;
      }else{
        document.getElementById('hs').textContent='Import failed: '+(r&&r.err||'unknown');
      }
    });
  };
  reader.readAsText(input.files[0]);
  input.value='';
}
function exportProfileBundle(){
  ra('GET','/api/dmx-profiles/export',null,function(d){
    if(!d||!d.length){document.getElementById('hs').textContent='No custom profiles to export';return;}
    var blob=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download='slyled-profiles-'+new Date().toISOString().slice(0,10)+'.json';
    a.click();URL.revokeObjectURL(a.href);
    document.getElementById('hs').textContent='Exported '+d.length+' profile(s)';
  });
}

function selectOnCanvas(type,id){
  if(!_s3d.inited){s3dInit();setTimeout(function(){selectOnCanvas(type,id);},500);return;}
  _s3d.nodes.forEach(function(grp){
    if(type==='fixture'&&grp.userData.childId===id){_s3d.selected=grp;_s3d.tctl.attach(grp);_updateSidePanel(id);}
    if(type==='object'&&grp.userData.stageObjId===id){_s3d.selected=grp;_s3d.tctl.attach(grp);}
  });
}



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

// ── Alignment functions (placeholder until multi-select) ────────────────
function _toggleAlignMenu(){
  var d=document.getElementById('align-dropdown');
  if(d)d.style.display=d.style.display==='none'?'block':'none';
}
function _toggleSceneMenu(){
  var d=document.getElementById('scene-dropdown');
  if(d)d.style.display=d.style.display==='none'?'block':'none';
}
// Close dropdowns when clicking elsewhere
document.addEventListener('click',function(e){
  var d=document.getElementById('align-dropdown');
  if(d&&d.style.display==='block'&&!e.target.closest('#btn-align-menu')&&!e.target.closest('#align-dropdown'))d.style.display='none';
  var s=document.getElementById('scene-dropdown');
  if(s&&s.style.display==='block'&&!e.target.closest('#btn-scene-menu')&&!e.target.closest('#scene-dropdown'))s.style.display='none';
});

function layAlign(axis,mode){
  // Requires multi-select — currently disabled.
  // Will support: 'min', 'max', 'center', 'distribute' on 'x' or 'y' axis
  document.getElementById('hs').textContent='Alignment requires 2+ selected fixtures (multi-select coming soon)';
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

// -- Stage Preview (always visible, per-pixel) ----------------------------
var _emuStage=null, _emuPreview=null, _emuTimer=null, _emuT=0, _emuRunning=false, _emuAnimId=null;

function emuLoadStage(){
  ra('GET','/api/layout',null,function(lay){
    ra('GET','/api/children',null,function(ch){
      ra('GET','/api/fixtures',null,function(fx){
        ra('GET','/api/objects',null,function(surfs){
        ra('GET','/api/spatial-effects',null,function(sfx){
          _emuStage={layout:lay,children:ch||[],fixtures:fx||[],objects:surfs||[],
            spatialFx:sfx||[],cw:(lay||{}).canvasW||10000,ch:(lay||{}).canvasH||5000};
          // Cache profile beamWidths for beam cone rendering
          if(!window._profileCache){
            window._profileCache={};
            ra('GET','/api/dmx-profiles',null,function(profs){
              (profs||[]).forEach(function(p){window._profileCache[p.id]=p;});
              _emuStageReady();
            });
          }else{_emuStageReady();}
        });
        });
      });
    });
  });
  // Start polling for show state
  if(_emuTimer)clearInterval(_emuTimer);
  _emuTimer=setInterval(function(){
    ra('GET','/api/settings',null,function(s){
      if(s&&s.runnerRunning&&s.activeTimeline>=0){
        if(!_emuRunning){
          _emuRunning=true;
          ra('GET','/api/timelines/'+s.activeTimeline+'/baked/preview',null,function(p){
            if(p&&typeof p==='object')_emuPreview=p;
            if(!_emu3d.animId&&_emu3d.activeTab)emu3dAnimate();
          });
        }
        if(s.runnerStartEpoch)_emuT=Math.max(0,Math.floor(Date.now()/1000)-s.runnerStartEpoch);
        // Sync timeline editor playhead to server elapsed time
        var elapsed=_emuT;
        var dur=_curTl?(_curTl.durationS||0):0;
        if(dur>0&&elapsed>=dur){elapsed=(_curTl&&_curTl.loop)?elapsed%dur:dur;}
        var ph=document.getElementById('tl-playhead');
        if(ph)ph.style.left=(60+elapsed*_tlPxPerSec)+'px';
        var td=document.getElementById('tl-time');
        if(td){var m=Math.floor(elapsed/60),sec=elapsed%60;td.textContent=(m<10?'0':'')+m+':'+(sec<10?'0':'')+sec.toFixed(0);}
      } else if(_emuRunning){
        _emuRunning=false;_emuPreview=null;_emuT=0;
        // Reset playhead
        var ph=document.getElementById('tl-playhead');if(ph)ph.style.left='60px';
        var td=document.getElementById('tl-time');if(td)td.textContent='00:00.0';
      }
    });
    // Refresh objects to include temporal objects; update 3D markers (#383)
    ra('GET','/api/objects',null,function(objs){
      if(_emuStage&&objs){_emuStage.objects=objs;emu3dRenderObjects();}
    });
  },1000);
}

function _emuStageReady(){
  // Initialize 3D viewport and build fixtures — used by both Dashboard and Runtime
  if(!_s3d.inited)s3dInit();
  if(!_s3d.renderer)return;
  emu3dInit();
  if(_s3d.animId){cancelAnimationFrame(_s3d.animId);_s3d.animId=null;}
  // Only re-attach if not already in the correct container
  var cid=(ctab==='dash')?'dash-3d':'emu-3d';
  if(_emu3d.activeContainer!==cid)_emu3dAttach(cid);
  emu3dBuildFixtures();
  if(_emu3d.animId){cancelAnimationFrame(_emu3d.animId);_emu3d.animId=null;}
  emu3dAnimate();
}

function _emuAnimLoop(){
  if(!_emuRunning){_emuAnimId=null;return;}
  // 3D viewport handles its own render loop via emu3dAnimate
  if(_emu3d.activeTab){_emuAnimId=null;return;}
  _emuAnimId=requestAnimationFrame(_emuAnimLoop);
}

function emuStart(tlId){
  _emuRunning=true;_emuT=0;
  ra('GET','/api/timelines/'+tlId+'/baked/preview',null,function(p){
    if(p&&typeof p==='object')_emuPreview=p;
    if(_emu3d.activeTab&&!_emu3d.animId)emu3dAnimate();
  });
}

function emuStop(){
  _emuRunning=false;_emuPreview=null;_emuT=0;
  if(_emuAnimId){cancelAnimationFrame(_emuAnimId);_emuAnimId=null;}
  emu3dUpdateColors(); // final frame shows idle state
}

// ── 3D Runtime Viewport (#273) ──────────────────────────────────────────────
var _emu3d={inited:false,camera:null,controls:null,animId:null,nodes:[],
  stageW:10,stageH:5,stageD:10,stageBox:null,activeTab:false};

function emu3dInit(){
  if(_emu3d.inited)return;
  if(typeof THREE==='undefined')return;
  // Ensure shared renderer exists
  if(!_s3d.inited)s3dInit();
  if(!_s3d.renderer)return;

  var el=document.getElementById('emu-3d');if(!el)return;
  var W=el.clientWidth||900,H=el.clientHeight||400;
  var aspect=W/H;

  // Dedicated perspective camera for runtime (elevated angle)
  _emu3d.camera=new THREE.PerspectiveCamera(50,aspect,0.1,100);
  _emu3d.camera.position.set(8,6,12);

  // Read-only orbit controls
  _emu3d.controls=new THREE.OrbitControls(_emu3d.camera,_s3d.renderer.domElement);
  _emu3d.controls.enableDamping=true;_emu3d.controls.dampingFactor=0.08;
  _emu3d.controls.enableRotate=true;
  _emu3d.controls.enabled=false; // disabled until tab is active

  _emu3d.inited=true;
}

function _emu3dAttach(containerId){
  // Move renderer canvas into a live viewport container (dashboard or runtime)
  var cid=containerId||'emu-3d';
  var el=document.getElementById(cid);if(!el||!_s3d.renderer)return;
  if(!_emu3d.camera||!_emu3d.controls)return;
  // Reparent canvas
  if(!el.contains(_s3d.renderer.domElement)){
    el.appendChild(_s3d.renderer.domElement);
  }
  var W=el.clientWidth||900,H=el.clientHeight||400;
  _s3d.renderer.setSize(W,H);
  _emu3d.camera.aspect=W/H;
  _emu3d.camera.updateProjectionMatrix();
  // Disable layout controls, enable runtime controls
  if(_s3d.controls)_s3d.controls.enabled=false;
  if(_s3d.tctl){_s3d.tctl.detach();_s3d.tctl.visible=false;}
  _emu3d.controls.enabled=true;
  _emu3d.activeTab=true;
  _emu3d.activeContainer=cid;
  // Remove layout click/dblclick listeners from canvas
  _s3d.renderer.domElement.removeEventListener('click',s3dClick);
  _s3d.renderer.domElement.removeEventListener('dblclick',s3dDblClick);
}

function _emu3dDetach(){
  // Move renderer canvas back to layout container
  var el=document.getElementById('stage3d');if(!el||!_s3d.renderer)return;
  _emu3d.activeTab=false;
  _emu3d.controls.enabled=false;
  // Stop runtime render loop
  if(_emu3d.animId){cancelAnimationFrame(_emu3d.animId);_emu3d.animId=null;}
  // Remove runtime fixture nodes from scene
  _emu3dClearNodes();
  // Move canvas back
  el.appendChild(_s3d.renderer.domElement);
  var W=el.clientWidth||900,H=el.clientHeight||500;
  _s3d.renderer.setSize(W,H);
  // Restore layout controls
  if(_s3d.controls){_s3d.controls.enabled=true;}
  if(_s3d.tctl){_s3d.tctl.visible=true;}
  // Restore layout click/dblclick listeners
  _s3d.renderer.domElement.addEventListener('click',s3dClick);
  _s3d.renderer.domElement.addEventListener('dblclick',s3dDblClick);
  // Update layout camera aspect
  if(_s3d.camera){
    if(_s3d.camera.isPerspectiveCamera){_s3d.camera.aspect=W/H;_s3d.camera.updateProjectionMatrix();}
    else if(_s3d.camera.isOrthographicCamera){
      var aspect=W/H;var oh=(_s3d.camera.top-_s3d.camera.bottom)/2;
      _s3d.camera.left=-oh*aspect;_s3d.camera.right=oh*aspect;
      _s3d.camera.updateProjectionMatrix();
    }
  }
}

function _emu3dClearNodes(){
  _emu3d.nodes.forEach(function(grp){
    grp.traverse(function(obj){
      if(obj.geometry)obj.geometry.dispose();
      if(obj.material){if(obj.material.map)obj.material.map.dispose();obj.material.dispose();}
    });
    _s3d.scene.remove(grp);
  });
  _emu3d.nodes=[];
  // Clear object nodes (tracked persons, stage objects) (#383)
  _emu3dClearObjNodes();
  // Also remove runtime stage box
  if(_emu3d.stageBox){_s3d.scene.remove(_emu3d.stageBox);_emu3d.stageBox=null;}
}

function emu3dZoomToFit(){
  if(!_emu3d.camera)return;
  var sw=_emu3d.stageW,sh=_emu3d.stageH,sd=_emu3d.stageD;
  _emu3d.controls.target.set(sw/2,sh/2,sd/2);
  // Position camera at elevated 3/4 angle, distance scales with stage size
  var maxDim=Math.max(sw,sh,sd);
  _emu3d.camera.position.set(sw/2+maxDim*0.8,sh+maxDim*0.5,sd/2+maxDim*1.0);
  _emu3d.camera.lookAt(sw/2,sh/2,sd/2);
  _emu3d.controls.update();
}

function emu3dBuildFixtures(){
  if(!_s3d.inited||!_emuStage)return;
  _emu3dClearNodes();

  var layout=_emuStage.layout;
  var layoutFixtures=(layout&&layout.fixtures)||[];

  // Use stage data from _stageData (loaded by loadLayout) or fetch sync
  var stReady=function(st){
    if(st){_emu3d.stageW=st.w||10;_emu3d.stageH=st.h||5;_emu3d.stageD=st.d||10;}
    // Stage wireframe box
    var sw=_emu3d.stageW,sh=_emu3d.stageH,sd=_emu3d.stageD;
    var boxGeo=new THREE.BoxGeometry(sw,sh,sd);
    var boxEdge=new THREE.EdgesGeometry(boxGeo);
    var boxLine=new THREE.LineSegments(boxEdge,new THREE.LineBasicMaterial({color:0x1e3a5f,opacity:0.4,transparent:true}));
    boxLine.position.set(sw/2,sh/2,sd/2);
    _s3d.scene.add(boxLine);
    _emu3d.stageBox=boxLine;

    // Build fixture meshes
    var placed=layoutFixtures.filter(function(f){return f.positioned;});
    placed.forEach(function(c){
      var pos=_s3dPos(c);
      var ft=c.fixtureType||'led';
      var col=ft==='dmx'?0x7c3aed:ft==='camera'?0x0e7490:0x22cc66;

      var grp=new THREE.Group();
      grp.position.copy(pos);
      grp.userData.emuNode=true;
      grp.userData.fixtureId=c.id;
      grp.userData.fixtureType=ft;
      grp.userData.childId=c.childId;
      // Store pan/tilt metadata for live beam animation
      if(ft==='dmx'&&c.dmxProfileId&&window._profileCache&&window._profileCache[c.dmxProfileId]){
        var prof=window._profileCache[c.dmxProfileId];
        grp.userData.panRange=prof.panRange||540;
        grp.userData.tiltRange=prof.tiltRange||270;
      }
      grp.userData.basePan=(c.rotation&&c.rotation[1])||0;
      grp.userData.mountedInverted=!!c.mountedInverted;

      // Sphere node
      var geo=new THREE.SphereGeometry(0.15,16,12);
      var mat=new THREE.MeshBasicMaterial({color:col,transparent:true,opacity:0.9});
      var sphere=new THREE.Mesh(geo,mat);
      sphere.userData.nodeSphere=true;
      grp.add(sphere);

      // Glow ring
      var ringGeo=new THREE.RingGeometry(0.18,0.22,24);
      var ringMat=new THREE.MeshBasicMaterial({color:col,side:THREE.DoubleSide,opacity:0.3,transparent:true});
      grp.add(new THREE.Mesh(ringGeo,ringMat));

      // DMX beam cone (skip cameras — not useful during runtime)
      if(ft==='dmx'){
        var _fRot=c.rotation||[0,0,0];
        var aim=_rotToAim(_fRot,[c.x||0,c.y||0,c.z||0],3000,c.mountedInverted);
        var aimLocal=new THREE.Vector3((aim[0]-(c.x||0))/1000,(aim[2]-(c.z||0))/1000,(aim[1]-(c.y||0))/1000);
        var beamLen=aimLocal.length();
        // Default beam length if rotation is zero (pointing forward)
        if(beamLen<0.01){aimLocal.set(0,-1,0);beamLen=3;}
        grp.userData.beamLen=beamLen;
        var bwDeg=15;
        if(c.dmxProfileId&&window._profileCache&&window._profileCache[c.dmxProfileId]){
          bwDeg=window._profileCache[c.dmxProfileId].beamWidth||15;
        }
        var bwRad=bwDeg*Math.PI/180;
        var topR=Math.tan(bwRad/2)*beamLen;
        var coneGeo=new THREE.ConeGeometry(topR,beamLen,16,1,true);
        var coneMat=new THREE.MeshBasicMaterial({color:0xffff88,opacity:0.1,transparent:true,side:THREE.DoubleSide,depthWrite:false});
        var cone=new THREE.Mesh(coneGeo,coneMat);
        cone.userData.beamCone=true;
        var midPt=aimLocal.clone().multiplyScalar(0.5);
        cone.position.copy(midPt);
        var dir=aimLocal.clone().normalize();
        cone.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,-1,0),dir));
        grp.add(cone);
      }

      // LED string dots (stored for color updates)
      if(c.strings&&c.strings.length){
        for(var si=0;si<c.strings.length;si++){
          var s=c.strings[si];if(!s||!s.leds)continue;
          var lenMm=s.mm||0;if(lenMm<500)lenMm=Math.max(s.leds*16,500);
          var lenM=lenMm/1000;
          var sdir=_s3dDir(s.sdir||0);
          var endLocal=new THREE.Vector3(sdir.x*lenM,sdir.y*lenM,sdir.z*lenM);
          // Line
          var lineGeo=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0),endLocal]);
          var lineMat=new THREE.LineBasicMaterial({color:0x555555});
          grp.add(new THREE.Line(lineGeo,lineMat));
          // LED dots
          var dotCount=Math.min(s.leds,50);
          for(var di=0;di<dotCount;di++){
            var t=(di+0.5)/dotCount;
            var dp=new THREE.Vector3().lerpVectors(new THREE.Vector3(0,0,0),endLocal,t);
            var dotGeo=new THREE.SphereGeometry(0.03,4,4);
            var dotMat=new THREE.MeshBasicMaterial({color:0x333340});
            var dot=new THREE.Mesh(dotGeo,dotMat);
            dot.position.copy(dp);
            dot.userData.ledDot=true;
            dot.userData.stringIdx=si;
            dot.userData.dotIdx=di;
            dot.userData.dotCount=dotCount;
            grp.add(dot);
          }
        }
      }

      // Label
      var label=_s3dLabel(c.name||('ID '+c.id));
      label.position.set(0,0.35,0);
      grp.add(label);

      _s3d.scene.add(grp);
      _emu3d.nodes.push(grp);
    });

    // Render stage objects (including tracked persons) (#383)
    emu3dRenderObjects();
    emu3dZoomToFit();
  };
  // Use cached stage data if available, otherwise fetch
  if(window._stageData){
    stReady(window._stageData);
  }else{
    ra('GET','/api/stage',null,function(st){window._stageData=st;stReady(st);});
  }
}

// ── Stage object rendering for Runtime/Dashboard 3D (#383) ─────────────
var _emu3dObjNodes=[];  // tracked separately from fixture nodes

function emu3dRenderObjects(){
  if(!_s3d.inited)return;
  var objs=(_emuStage&&_emuStage.objects)||[];
  // Build a map of current object IDs for expiry detection
  var objIds={};
  objs.forEach(function(o){objIds[o.id]=true;});

  // Remove expired objects (no longer in API response = TTL expired)
  var keep=[];
  _emu3dObjNodes.forEach(function(grp){
    if(!objIds[grp.userData.objId]){
      grp.traverse(function(obj){
        if(obj.geometry)obj.geometry.dispose();
        if(obj.material){if(obj.material.map)obj.material.map.dispose();obj.material.dispose();}
      });
      _s3d.scene.remove(grp);
    } else {
      keep.push(grp);
    }
  });
  _emu3dObjNodes=keep;

  // Build a map of existing node IDs for update-in-place
  var existing={};
  _emu3dObjNodes.forEach(function(grp){existing[grp.userData.objId]=grp;});

  objs.forEach(function(s){
    var t=s.transform||{pos:[0,0,0],scale:[2000,1500,1]};
    var isPerson=(s.objectType==='person'||s._temporal);
    // Stage→Three.js: X→X, Z(height)→Y, Y(depth)→Z
    var px=(t.pos[0]||0)/1000, py=(t.pos[2]||0)/1000, pz=(t.pos[1]||0)/1000;

    // Update existing node position (smooth lerp)
    if(existing[s.id]){
      var grp=existing[s.id];
      var tp=grp.userData._targetPos;
      if(!tp){tp={x:px,y:py,z:pz};grp.userData._targetPos=tp;}
      tp.x=px;tp.y=py;tp.z=pz;
      return; // position lerp handled in emu3dAnimate
    }

    // Create new node
    var grp=new THREE.Group();
    grp.userData.emuObj=true;
    grp.userData.objId=s.id;
    grp.userData.isPerson=isPerson;
    grp.userData._targetPos={x:px,y:py,z:pz};
    grp.position.set(px,py,pz);

    if(isPerson){
      // Person: vertical capsule (cylinder + 2 hemispheres)
      var personH=1.8; // ~1.8m tall
      var personR=0.2;  // ~0.4m wide
      var cylGeo=new THREE.CylinderGeometry(personR,personR,personH-personR*2,12);
      var cylMat=new THREE.MeshBasicMaterial({color:0xf472b6,transparent:true,opacity:0.55,depthWrite:false});
      var cyl=new THREE.Mesh(cylGeo,cylMat);
      cyl.position.set(0,(personH-personR*2)/2+personR,0);
      cyl.userData.personBody=true;
      grp.add(cyl);
      // Top hemisphere
      var topGeo=new THREE.SphereGeometry(personR,12,8,0,Math.PI*2,0,Math.PI/2);
      var topMat=new THREE.MeshBasicMaterial({color:0xf472b6,transparent:true,opacity:0.55,depthWrite:false});
      var top=new THREE.Mesh(topGeo,topMat);
      top.position.set(0,personH-personR,0);
      top.userData.personBody=true;
      grp.add(top);
      // Bottom hemisphere
      var botGeo=new THREE.SphereGeometry(personR,12,8,0,Math.PI*2,Math.PI/2,Math.PI/2);
      var botMat=new THREE.MeshBasicMaterial({color:0xf472b6,transparent:true,opacity:0.55,depthWrite:false});
      var bot=new THREE.Mesh(botGeo,botMat);
      bot.position.set(0,personR,0);
      bot.userData.personBody=true;
      grp.add(bot);
      // Glow ring at feet
      var ringGeo=new THREE.RingGeometry(0.25,0.35,24);
      var ringMat=new THREE.MeshBasicMaterial({color:0xf472b6,side:THREE.DoubleSide,opacity:0.3,transparent:true,depthWrite:false});
      var ring=new THREE.Mesh(ringGeo,ringMat);
      ring.rotation.x=-Math.PI/2;
      ring.position.set(0,0.02,0);
      grp.add(ring);
    } else {
      // Static object: box/plane (same as layout view)
      var sw=(t.scale[0]||2000)/1000,sh=(t.scale[1]||1500)/1000;
      var sd=(t.scale[2]||1)/1000;
      var col=new THREE.Color(s.color||'#334155');
      var useBox=((t.scale[2]||1)>100);
      var geo=useBox?new THREE.BoxGeometry(sw,sh,sd):new THREE.PlaneGeometry(sw,sh);
      var mat=new THREE.MeshBasicMaterial({color:col,side:THREE.DoubleSide,opacity:(s.opacity||30)/100,transparent:true,depthWrite:false});
      var mesh=new THREE.Mesh(geo,mat);
      mesh.position.set(sw/2,sh/2,useBox?(-sd/2):0);
      grp.add(mesh);
      var edgeMat=new THREE.LineBasicMaterial({color:col,opacity:0.6,transparent:true});
      grp.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo),edgeMat));
    }

    // Label above
    var lbl=_s3dLabel(s.name||(isPerson?'Person':'Object'));
    lbl.position.set(0,isPerson?2.1:((t.scale[1]||1500)/1000+0.15),0);
    lbl.userData.objLabel=true;
    grp.add(lbl);

    _s3d.scene.add(grp);
    _emu3dObjNodes.push(grp);
  });
}

function _emu3dClearObjNodes(){
  _emu3dObjNodes.forEach(function(grp){
    grp.traverse(function(obj){
      if(obj.geometry)obj.geometry.dispose();
      if(obj.material){if(obj.material.map)obj.material.map.dispose();obj.material.dispose();}
    });
    _s3d.scene.remove(grp);
  });
  _emu3dObjNodes=[];
}

function emu3dUpdateColors(){
  if(!_emu3d.nodes.length)return;
  var layoutFixtures=(_emuStage&&_emuStage.layout&&_emuStage.layout.fixtures)||[];
  _emu3d.nodes.forEach(function(grp){
    var fid=grp.userData.fixtureId;
    var ft=grp.userData.fixtureType;
    var pd=null;
    // Get preview frame for this fixture
    if(_emuPreview&&_emuRunning){
      var frames=_emuPreview[String(fid)];
      if(frames&&frames.length>0){
        var idx=Math.floor(_emuT)%frames.length;
        pd=frames[idx];
      }
    }

    if(ft==='dmx'){
      // Update sphere + cone color from preview
      var br=0x7c,bg=0x3a,bb=0xed,dimmer=0.1;
      if(pd&&typeof pd==='object'){
        if(pd.r!==undefined){br=pd.r;bg=pd.g;bb=pd.b;}
        if(pd.dimmer>0)dimmer=(pd.dimmer/255)*0.4;
        else if(br+bg+bb>10)dimmer=0.3;
      }
      var hexCol=(br<<16)|(bg<<8)|bb;
      // Update beam cone direction from pan/tilt if available
      if(pd&&pd.pan!==undefined&&pd.tilt!==undefined){
        var panRange=grp.userData.panRange||540;
        var tiltRange=grp.userData.tiltRange||270;
        var panDeg=(pd.pan-0.5)*panRange;
        var tiltDeg=(pd.tilt-0.5)*tiltRange;
        if(grp.userData.mountedInverted)tiltDeg=-tiltDeg;
        var basePan=(grp.userData.basePan||0);
        var panRad=(basePan+panDeg)*Math.PI/180;
        var tiltRad=tiltDeg*Math.PI/180;
        var aimDir=new THREE.Vector3(Math.sin(panRad)*Math.cos(tiltRad),
          -Math.sin(tiltRad),Math.cos(panRad)*Math.cos(tiltRad));
        var beamLen=grp.userData.beamLen||3;
        grp.children.forEach(function(child){
          if(child.userData.beamCone){
            var mid=aimDir.clone().multiplyScalar(beamLen/2);
            child.position.copy(mid);
            child.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(
              new THREE.Vector3(0,-1,0),aimDir.clone().normalize()));
          }
        });
      }
      grp.children.forEach(function(child){
        if(child.userData.nodeSphere){
          child.material.color.setHex(hexCol||0x7c3aed);
          child.material.opacity=pd?0.95:0.5;
        }
        if(child.userData.beamCone){
          child.material.color.setHex(hexCol||0xffff88);
          child.material.opacity=pd?dimmer:0.08;
        }
      });
    } else if(ft==='led'){
      // Update LED dots from preview
      var previewColors=null;
      if(pd&&Array.isArray(pd)){previewColors=pd;}
      // Also check by childId
      if(!previewColors&&_emuPreview&&_emuRunning&&grp.userData.childId){
        var cid=grp.userData.childId;
        // Find fixture matching this child
        (_emuStage.fixtures||[]).forEach(function(f){
          if(f.childId===cid&&_emuPreview[String(f.id)]){
            var frames=_emuPreview[String(f.id)];
            if(frames&&frames.length>0)previewColors=frames[_emuT%frames.length];
          }
        });
      }
      grp.children.forEach(function(child){
        if(!child.userData.ledDot)return;
        var si=child.userData.stringIdx,di=child.userData.dotIdx,dc=child.userData.dotCount;
        var pc=null;
        if(previewColors&&si<previewColors.length)pc=previewColors[si];
        var isAct=(pc&&typeof pc==='object'&&!Array.isArray(pc)&&pc.t!==undefined);
        var r=40,g=40,b=45;
        if(pc&&Array.isArray(pc)&&(pc[0]+pc[1]+pc[2])>3){r=pc[0];g=pc[1];b=pc[2];}
        else if(isAct){
          var eMs=(pc.e||0)*1000+(Date.now()%1000);
          var px=_emuPixel(pc,di,dc,eMs);
          if(px){r=px[0];g=px[1];b=px[2];}
        }
        child.material.color.setRGB(r/255,g/255,b/255);
        // Scale lit dots larger
        var lit=(r+g+b)>15;
        child.scale.setScalar(lit?2.0:1.0);
      });
      // Update node sphere color to average
      var nodeSphere=grp.children[0];
      if(nodeSphere&&nodeSphere.userData.nodeSphere){
        nodeSphere.material.opacity=(_emuRunning&&pd)?0.95:0.5;
      }
    }
  });
}

function emu3dAnimate(){
  if(!_emu3d.activeTab){_emu3d.animId=null;return;}
  _emu3d.animId=requestAnimationFrame(emu3dAnimate);
  if(_emu3d.controls)_emu3d.controls.update();
  emu3dUpdateColors();
  // Constant-size fixture nodes
  if(_emu3d.camera&&_emu3d.nodes.length){
    var center=new THREE.Vector3(_emu3d.stageW/2,_emu3d.stageH/4,_emu3d.stageD/2);
    var scaleFactor=Math.max(0.3,Math.min(3.0,_emu3d.camera.position.distanceTo(center)/15));
    _emu3d.nodes.forEach(function(grp){
      if(grp.children[0]&&grp.children[0].isMesh)grp.children[0].scale.setScalar(scaleFactor);
      if(grp.children[1]&&grp.children[1].isMesh)grp.children[1].scale.setScalar(scaleFactor);
    });
  }
  // Animate person markers: lerp position + pulsing opacity (#383)
  var now=Date.now()/1000;
  _emu3dObjNodes.forEach(function(grp){
    // Smooth position lerp toward target
    var tp=grp.userData._targetPos;
    if(tp){
      grp.position.x+=(tp.x-grp.position.x)*0.15;
      grp.position.y+=(tp.y-grp.position.y)*0.15;
      grp.position.z+=(tp.z-grp.position.z)*0.15;
    }
    // Pulsing opacity for person markers
    if(grp.userData.isPerson){
      var pulse=0.4+0.2*Math.sin(now*3);
      grp.traverse(function(child){
        if(child.userData.personBody&&child.material){
          child.material.opacity=pulse;
        }
      });
    }
  });
  if(_s3d.renderer&&_s3d.scene&&_emu3d.camera)_s3d.renderer.render(_s3d.scene,_emu3d.camera);
}

// -- Per-pixel colour helpers (mirror firmware ChildLED.cpp) ----------------
function _hsvToRgb(h,s,v){
  // FastLED-style hsv2rgb_rainbow approximation (h,s,v: 0-255)
  h=h&0xFF;s=s&0xFF;v=v&0xFF;
  var inv=255-s, r,g,b;
  var sext=Math.floor(h/43), frac=(h-sext*43)*6;
  switch(sext){
    case 0: r=v;g=(v*((255-(s*(255-frac)>>8)))>>8);b=(v*inv>>8);break;
    case 1: r=(v*((255-(s*frac>>8)))>>8);g=v;b=(v*inv>>8);break;
    case 2: r=(v*inv>>8);g=v;b=(v*((255-(s*(255-frac)>>8)))>>8);break;
    case 3: r=(v*inv>>8);g=(v*((255-(s*frac>>8)))>>8);b=v;break;
    case 4: r=(v*((255-(s*(255-frac)>>8)))>>8);g=(v*inv>>8);b=v;break;
    default:r=v;g=(v*inv>>8);b=(v*((255-(s*frac>>8)))>>8);break;
  }
  return [Math.round(r),Math.round(g),Math.round(b)];
}
function _palColor(palId,idx){
  idx=idx&0xFF;
  switch(palId){
    default:
    case 0: return _hsvToRgb(idx,255,255);
    case 1: return _hsvToRgb(((idx>>1)+120)&0xFF,200,Math.min(255,160+(idx/3|0)));
    case 2: return _hsvToRgb((idx>>2)&0xFF,255,Math.min(255,200+Math.round(Math.sin(idx*Math.PI/128)*51)));
    case 3: return _hsvToRgb(((idx/3|0)+60)&0xFF,220,Math.min(255,100+Math.round(Math.sin(idx*Math.PI/128)*128)));
    case 4: return _hsvToRgb((idx*3)&0xFF,255,255);
    case 5:{var t=idx;if(t<85)return[t*3,0,0];if(t<170)return[255,(t-85)*3,0];return[255,255,(t-170)*3];}
    case 6: return _hsvToRgb(((idx>>1)+140)&0xFF,180,Math.min(255,180+(idx>>2)));
    case 7: return _hsvToRgb(idx,100,255);
  }
}
// Compute per-pixel RGB for a procedural action at pixel position i/N
// Returns [r,g,b] for the given dot, or null if not handled
function _emuPixel(pc,di,dotCount,elapsedMs){
  var t=pc.t,p=pc.p||{};
  var e=elapsedMs;
  if(t===5){// RAINBOW
    var spd=p.speedMs||50;if(spd<1)spd=1;
    var dir=p.direction||0;
    var palId=p.paletteId||0;
    var timeOff=Math.floor(e/spd)&0xFF;
    var idx=(dir===2||dir===3)?(dotCount-1-di):di;
    var hue=((idx*255/dotCount)|0)+timeOff;
    return _palColor(palId,hue&0xFF);
  }
  if(t===4){// CHASE
    var spd=p.speedMs||100;if(spd<1)spd=1;
    var spc=p.spacing||3;if(spc<2)spc=3;
    var dir=p.direction||0;
    var off=Math.floor(e/spd)%spc;
    var idx=(dir===2||dir===3)?(dotCount-1-di):di;
    return((idx+off)%spc===0)?[p.r||100,p.g||200,p.b||255]:[0,0,0];
  }
  if(t===7){// COMET
    var spd=p.speedMs||40;if(spd<1)spd=1;
    var tail=p.tailLen||10;if(tail<1)tail=10;
    var dir=p.direction||0;
    var head=Math.floor(e/spd)%(dotCount+tail);
    var pos=(dir===2||dir===3)?(dotCount-1-head%dotCount):(head%dotCount);
    var dist=Math.abs(di-pos);
    if(head>=dotCount)return[0,0,0];
    if(dist===0)return[p.r||255,p.g||255,p.b||255];
    if(dist<=tail){var f=1-dist/tail;return[Math.round((p.r||255)*f),Math.round((p.g||255)*f),Math.round((p.b||255)*f)];}
    return[0,0,0];
  }
  if(t===10){// WIPE
    var spd=p.speedMs||30;if(spd<1)spd=1;
    var dir=p.direction||0;
    var filled=Math.floor(e/spd)%(dotCount*2);
    var filling=filled<dotCount;
    var cnt=filling?filled:(dotCount*2-filled);
    var idx=(dir===2||dir===3)?(dotCount-1-di):di;
    return(idx<cnt)?(filling?[p.r||255,p.g||128,p.b||0]:[0,0,0]):(filling?[0,0,0]:[p.r||255,p.g||128,p.b||0]);
  }
  if(t===11){// SCANNER
    var spd=p.speedMs||30;if(spd<1)spd=1;
    var bar=p.barWidth||3;if(bar<1)bar=3;
    var travel=Math.max(dotCount-bar,1);
    var cyc=travel*2;
    var pos=Math.floor(e/spd)%cyc;if(pos>=travel)pos=cyc-pos;
    if(di>=pos&&di<pos+bar)return[p.r||255,p.g||0,p.b||0];
    return[0,0,0];
  }
  if(t===2){// FADE (ping-pong)
    var spd=p.speedMs||1000;if(spd<1)spd=1;
    var cyc=spd*2;var tt=e%cyc;
    var frac=tt<spd?(tt/spd):((cyc-tt)/spd);
    return[Math.round((p.r||0)*(1-frac)+(p.r2||0)*frac),
           Math.round((p.g||0)*(1-frac)+(p.g2||0)*frac),
           Math.round((p.b||0)*(1-frac)+(p.b2||0)*frac)];
  }
  if(t===3){// BREATHE
    var per=p.periodMs||3000;if(per<1)per=3000;
    var minB=(p.minBri||0)/100;
    var phase=(e%per)/per*2*Math.PI;
    var bri=minB+(1-minB)*(0.5+0.5*Math.sin(phase));
    return[Math.round((p.r||200)*bri),Math.round((p.g||100)*bri),Math.round((p.b||255)*bri)];
  }
  if(t===9){// STROBE
    var per=p.periodMs||100;var duty=p.dutyPct||50;
    return(e%per<per*duty/100)?[p.r||255,p.g||255,p.b||255]:[0,0,0];
  }
  if(t===6){// FIRE (deterministic pseudo-random from position)
    var heat=Math.max(0,Math.min(255,128+Math.round(80*Math.sin(di*0.7+e*0.003))+Math.round(40*Math.sin(di*1.3+e*0.007))));
    if(heat<85)return[heat*3,0,0];
    if(heat<170)return[255,(heat-85)*3,0];
    return[255,255,Math.min(255,(heat-170)*3)];
  }
  if(t===8){// TWINKLE
    var seed=(di*2654435761+Math.floor(e/80))>>>0;
    var bri=((seed>>8)&0xFF);
    if(bri>180)return[Math.round((p.r||200)*bri/255),Math.round((p.g||200)*bri/255),Math.round((p.b||255)*bri/255)];
    return[0,0,0];
  }
  if(t===12){// SPARKLE
    var seed=(di*2654435761+Math.floor(e/50))>>>0;
    if(((seed>>16)&0xFF)>230)return[255,255,255];
    return[p.r||180,p.g||180,p.b||220];
  }
  if(t===13){// GRADIENT
    var frac=dotCount>1?di/(dotCount-1):0;
    return[Math.round((p.r||0)*(1-frac)+(p.r2||0)*frac),
           Math.round((p.g||0)*(1-frac)+(p.g2||0)*frac),
           Math.round((p.b||0)*(1-frac)+(p.b2||0)*frac)];
  }
  return null;
}

function emuDraw(){
  var cv=document.getElementById('emu-cv');if(!cv)return;
  var ctx=cv.getContext('2d');
  var W=cv.width,H=cv.height;
  ctx.fillStyle='#060a12';ctx.fillRect(0,0,W,H);

  if(!_emuStage)return;
  var layout=_emuStage.layout;
  var children=_emuStage.children;
  var fixtures=_emuStage.fixtures;
  var layoutFixtures=(layout&&layout.fixtures)||[];
  if(!children.length&&!layoutFixtures.length)return;
  // Debug: log fixture counts on first draw
  if(!_emuStage._logged){_emuStage._logged=true;
    var pLed=layoutFixtures.filter(function(f){return f.fixtureType==='led'&&f.positioned;}).length;
    var pDmx=layoutFixtures.filter(function(f){return f.fixtureType==='dmx'&&f.positioned;}).length;
    var pCam=layoutFixtures.filter(function(f){return f.fixtureType==='camera'&&f.positioned;}).length;
    console.log('[SlyLED EMU] '+layoutFixtures.length+' layout fixtures ('+pLed+' LED + '+pDmx+' DMX + '+pCam+' CAM positioned), '+children.length+' children, '+emuSurfs.length+' objects');
  }

  var cw=(layout&&layout.canvasW)||10000;
  var ch=(layout&&layout.canvasH)||5000;
  var layChildren=(layout&&layout.children)||[];

  // Stage border + grid
  ctx.strokeStyle='#1e3a5f';ctx.lineWidth=1;ctx.strokeRect(1,1,W-2,H-2);
  ctx.strokeStyle='#0c1222';ctx.lineWidth=0.5;
  for(var gx=1;gx<10;gx++){var x=gx*W/10;ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,H);ctx.stroke();}
  for(var gy=1;gy<5;gy++){var y=gy*H/5;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke();}

  // Objects (draw behind fixtures)
  var emuSurfs=(_emuStage&&_emuStage.objects)||[];
  emuSurfs.forEach(function(s){
    var t=s.transform||{pos:[0,0,0],scale:[2000,1500,1]};
    var sx=t.pos[0]*W/cw,sy=H-t.pos[1]*H/ch;
    var sw=t.scale[0]*W/cw,sh=t.scale[1]*H/ch;
    // Clip to canvas
    var clX=Math.max(0,sx),clY=Math.max(0,sy-sh);
    var clW=Math.min(W,sx+sw)-clX,clH=Math.min(H,sy)-clY;
    if(clW>0&&clH>0){
      ctx.globalAlpha=(s.opacity||30)/100;
      ctx.fillStyle=s.color||'#334155';ctx.fillRect(clX,clY,clW,clH);
      ctx.globalAlpha=0.5;ctx.strokeStyle=s.color||'#334155';ctx.lineWidth=1;
      if(s._temporal){ctx.setLineDash([4,3]);}
      ctx.strokeRect(clX,clY,clW,clH);
      if(s._temporal){ctx.setLineDash([]);}
      ctx.globalAlpha=1;
      ctx.fillStyle='#667';ctx.font='7px sans-serif';ctx.textAlign='left';
      var label=s.name||'';
      if(s._temporal&&s._ttl!=null){label+=' ['+s._ttl+'s]';}
      ctx.fillText(label,clX+3,clY+10);
    }
  });

  // Spatial field sweep visualization (when show is running)
  if(_emuRunning&&_emuStage&&_emuStage.spatialFx){
    var sfxMap={};_emuStage.spatialFx.forEach(function(f){sfxMap[f.id]=f;});
    // Find active timeline clips to know which effects are playing
    var activeTl=_curTl;
    if(activeTl){
      var tlDur=activeTl.durationS||30;
      var t=activeTl.loop?(_emuT%tlDur):Math.min(_emuT,tlDur);
      (activeTl.tracks||[]).forEach(function(track){
        (track.clips||[]).forEach(function(clip){
          var eid=clip.effectId;if(eid===undefined||eid===null)return;
          var fx=sfxMap[eid];if(!fx)return;
          var cs=clip.startS||0,cd=clip.durationS||tlDur;
          if(t<cs||t>cs+cd)return; // clip not active
          var motion=fx.motion||{};
          var dur=motion.durationS||1;
          var clipT=t-cs;
          var progress=Math.min(clipT/dur,1.0);
          // Easing
          var ease=motion.easing||'linear';
          if(ease==='ease-in')progress=progress*progress;
          else if(ease==='ease-out')progress=1-(1-progress)*(1-progress);
          else if(ease==='ease-in-out')progress=progress<0.5?2*progress*progress:1-2*(1-progress)*(1-progress);
          var sp=motion.startPos||[0,0,0],ep=motion.endPos||[0,0,0];
          var px=sp[0]+(ep[0]-sp[0])*progress;
          var py=sp[1]+(ep[1]-sp[1])*progress;
          var cx_=px*W/cw,cy_=H-py*H/ch;
          var size=fx.size||{};
          var r=fx.r||0,g=fx.g||0,b=fx.b||0;
          ctx.globalAlpha=0.15;
          if(fx.shape==='sphere'){
            var rad=(size.radius||1000)*W/cw;
            ctx.beginPath();ctx.arc(cx_,cy_,rad,0,2*Math.PI);
            ctx.fillStyle='rgb('+r+','+g+','+b+')';ctx.fill();
            ctx.strokeStyle='rgba('+r+','+g+','+b+',0.4)';ctx.lineWidth=1;ctx.stroke();
          }else if(fx.shape==='plane'){
            var thick=(size.thickness||200)*H/ch;
            var n=size.normal||[0,1,0];
            if(Math.abs(n[1])>0.5){
              // Horizontal plane — draw as horizontal band
              ctx.fillStyle='rgb('+r+','+g+','+b+')';
              ctx.fillRect(0,cy_-thick/2,W,thick);
            }else{
              // Vertical plane — draw as vertical band
              ctx.fillStyle='rgb('+r+','+g+','+b+')';
              ctx.fillRect(cx_-thick/2,0,thick,H);
            }
          }else if(fx.shape==='box'){
            var bw=(size.width||1000)*W/cw,bh=(size.height||1000)*H/ch;
            ctx.fillStyle='rgb('+r+','+g+','+b+')';
            ctx.fillRect(cx_-bw/2,cy_-bh/2,bw,bh);
          }
          ctx.globalAlpha=1;
          // Label
          ctx.fillStyle='rgba('+r+','+g+','+b+',0.6)';ctx.font='7px sans-serif';ctx.textAlign='center';
          ctx.fillText(fx.name||'',cx_,cy_-3);
        });
      });
    }
  }

  var dirDx=[1,0,-1,0],dirDy=[0,-1,0,1];

  // Iterate ALL children (not just fixtures with preview data)
  children.forEach(function(child){
    var lc=null;layChildren.forEach(function(c){if(c.id===child.id)lc=c;});
    if(!lc||(!lc.x&&!lc.y))return; // skip unplaced

    var cx=lc.x*W/cw,cy=H-lc.y*H/ch;
    cx=Math.max(12,Math.min(W-12,cx));cy=Math.max(12,Math.min(H-12,cy));

    var sc=child.sc||0;
    var strings=child.strings||[];

    // Find preview colors for this child's fixture
    var previewColors=null;
    if(_emuPreview&&_emuRunning){
      fixtures.forEach(function(f){
        if(f.childId===child.id&&_emuPreview[String(f.id)]){
          var frames=_emuPreview[String(f.id)];
          var dur=frames.length;
          if(dur>0){previewColors=frames[_emuT%dur];}
        }
      });
    }

    // Draw each string with individual LED pixels
    for(var si=0;si<sc&&si<strings.length;si++){
      var s=strings[si];if(!s||!s.leds)continue;
      var sdir=s.sdir||0;
      var dx=dirDx[sdir]||0,dy=dirDy[sdir]||0;
      var leds=s.leds;
      var lenMm=s.mm||0;if(lenMm<500)lenMm=Math.max(leds*16,500);
      var pxLen=dx!==0?lenMm*W/cw:lenMm*H/ch;
      pxLen=Math.max(pxLen,25);

      // Get string preview entry (may be [r,g,b] or {t,p,e} action metadata)
      var pc=null;
      if(previewColors&&si<previewColors.length) pc=previewColors[si];
      var isAction=(pc&&typeof pc==='object'&&!Array.isArray(pc)&&pc.t!==undefined);
      var solidR=40,solidG=40,solidB=45,solidLit=false;
      if(pc&&Array.isArray(pc)&&(pc[0]+pc[1]+pc[2])>3){solidR=pc[0];solidG=pc[1];solidB=pc[2];solidLit=true;}

      // Draw LED pixels along the string
      var dotCount=Math.min(leds,Math.floor(pxLen/3));
      dotCount=Math.max(dotCount,5);
      var eMs=(pc&&isAction)?((pc.e||0)*1000+(Date.now()%1000)):0;
      for(var di=0;di<dotCount;di++){
        var tt=(di+0.5)/dotCount;
        var dpx=cx+dx*pxLen*tt,dpy=cy+dy*pxLen*tt;
        var r,g,b,lit;
        if(isAction){
          var px=_emuPixel(pc,di,dotCount,eMs);
          if(px){r=px[0];g=px[1];b=px[2];lit=(r+g+b)>3;}
          else{r=40;g=40;b=45;lit=false;}
        } else {r=solidR;g=solidG;b=solidB;lit=solidLit;}
        var dotR=lit?2.5:1.5;
        ctx.beginPath();ctx.arc(dpx,dpy,dotR,0,2*Math.PI);
        if(lit){
          ctx.fillStyle='rgb('+r+','+g+','+b+')';ctx.fill();
          ctx.beginPath();ctx.arc(dpx,dpy,4,0,2*Math.PI);
          ctx.fillStyle='rgba('+r+','+g+','+b+',0.15)';ctx.fill();
        } else {
          ctx.fillStyle='rgb('+r+','+g+','+b+')';ctx.fill();
        }
      }

      // LED count label at end
      var ex=cx+dx*pxLen,ey=cy+dy*pxLen;
      ctx.fillStyle='#334';ctx.font='7px sans-serif';ctx.textAlign='center';
      ctx.fillText(leds,ex+(dx!==0?dx*8:0),ey+(dy!==0?dy*8:0));
    }

    // Node circle
    var nodeOn=child.status===1;
    ctx.beginPath();ctx.arc(cx,cy,5,0,2*Math.PI);
    ctx.fillStyle=nodeOn?'#22cc66':'#444';ctx.fill();
    ctx.strokeStyle=nodeOn?'#4c4':'#555';ctx.lineWidth=1;ctx.stroke();

    // Label
    var name=child.name||child.hostname||'';
    ctx.fillStyle='#78889a';ctx.font='8px sans-serif';ctx.textAlign='center';
    ctx.fillText(name,cx,cy+14);
  });

  // DMX fixture beams — render from layout.fixtures (positioned DMX fixtures)
  var emuCW=_emuStage.cw||10000,emuCH=_emuStage.ch||5000;
  layoutFixtures.forEach(function(fix){
    if(fix.fixtureType!=='dmx'||!fix.positioned)return;
    var fx=fix.x*W/emuCW,fy=H-fix.y*H/emuCH;
    fx=Math.max(6,Math.min(W-6,fx));fy=Math.max(6,Math.min(H-6,fy));
    // Get preview data if available
    var pd=null;
    if(_emuPreview&&_emuRunning){
      var frames=_emuPreview[String(fix.id)];
      if(frames&&frames.length>0){
        var idx=Math.floor(_emuT)%frames.length;
        pd=frames[idx];
      }
    }
    // Beam color: from preview or default purple
    var br=124,bg=58,bb=237,dimmer=0.12;
    if(pd&&typeof pd==='object'){
      if(pd.r!==undefined){br=pd.r;bg=pd.g;bb=pd.b;}
      if(pd.dimmer>0)dimmer=(pd.dimmer/255)*0.3;
      else if(br+bg+bb>10)dimmer=0.2;
    }
    // Draw beam cone from rotation — beamWidth from profile or default
    var aim=_rotToAim(fix.rotation||[0,0,0],[fix.x||0,fix.y||0,fix.z||0],3000,fix.mountedInverted);
    var ax=aim[0]*W/emuCW,ay=H-aim[1]*H/emuCH;
    var bwDeg=(fix._beamWidth)||(pd&&pd.beamWidth)||15;
    // Flood lights have wider beams (>40°), spots are narrow (<20°)
    if(!fix._beamWidth&&fix.dmxProfileId){
      // Cache beam width from profile on first access
      var cachedProf=_profileCache&&_profileCache[fix.dmxProfileId];
      if(cachedProf)fix._beamWidth=cachedProf.beamWidth||15;
    }
    var bLen=Math.sqrt((ax-fx)*(ax-fx)+(ay-fy)*(ay-fy));
    if(bLen<2)bLen=80;
    var halfW=Math.tan(bwDeg*Math.PI/360)*bLen;
    var angle=Math.atan2(ay-fy,ax-fx);
    var lx=ax+Math.cos(angle+Math.PI/2)*halfW;
    var ly=ay+Math.sin(angle+Math.PI/2)*halfW;
    var rx=ax+Math.cos(angle-Math.PI/2)*halfW;
    var ry=ay+Math.sin(angle-Math.PI/2)*halfW;
    ctx.beginPath();ctx.moveTo(fx,fy);ctx.lineTo(lx,ly);ctx.lineTo(rx,ry);ctx.closePath();
    ctx.fillStyle='rgba('+br+','+bg+','+bb+','+dimmer+')';ctx.fill();
    // Node dot
    ctx.beginPath();ctx.arc(fx,fy,4,0,2*Math.PI);
    ctx.fillStyle='rgba('+br+','+bg+','+bb+',0.8)';ctx.fill();
    // Aim dot
    ctx.beginPath();ctx.arc(ax,ay,3,0,2*Math.PI);
    ctx.fillStyle='rgba(255,68,68,0.6)';ctx.fill();
    // Label
    ctx.fillStyle='#78889a';ctx.font='8px sans-serif';ctx.textAlign='center';
    ctx.fillText(fix.name||'DMX',fx,fy+14);
  });

  // Camera FOV cones
  layoutFixtures.forEach(function(fix){
    if(fix.fixtureType!=='camera'||!fix.positioned)return;
    var fx=fix.x*W/emuCW,fy=H-fix.y*H/emuCH;
    fx=Math.max(6,Math.min(W-6,fx));fy=Math.max(6,Math.min(H-6,fy));
    var aim=_rotToAim(fix.rotation||[0,0,0],[fix.x||0,fix.y||0,fix.z||0]);
    var ax=aim[0]*W/emuCW,ay=H-aim[1]*H/emuCH;
    var bwDeg=fix.fovDeg||60;
    var bLen=Math.sqrt((ax-fx)*(ax-fx)+(ay-fy)*(ay-fy));
    if(bLen<2)bLen=80;
    var halfW=Math.tan(bwDeg*Math.PI/360)*bLen;
    var angle=Math.atan2(ay-fy,ax-fx);
    var lx=ax+Math.cos(angle+Math.PI/2)*halfW;
    var ly=ay+Math.sin(angle+Math.PI/2)*halfW;
    var rx=ax+Math.cos(angle-Math.PI/2)*halfW;
    var ry=ay+Math.sin(angle-Math.PI/2)*halfW;
    ctx.beginPath();ctx.moveTo(fx,fy);ctx.lineTo(lx,ly);ctx.lineTo(rx,ry);ctx.closePath();
    ctx.fillStyle='rgba(14,116,144,0.08)';ctx.fill();
    ctx.beginPath();ctx.arc(fx,fy,4,0,2*Math.PI);
    ctx.fillStyle='rgba(14,116,144,0.8)';ctx.fill();
    ctx.beginPath();ctx.arc(ax,ay,3,0,2*Math.PI);
    ctx.fillStyle='rgba(255,68,68,0.6)';ctx.fill();
    ctx.fillStyle='#78889a';ctx.font='8px sans-serif';ctx.textAlign='center';
    ctx.fillText(fix.name||'CAM',fx,fy+14);
  });

  // LED fixtures from layout (positioned LED fixtures with strings)
  layoutFixtures.forEach(function(fix){
    if(fix.fixtureType!=='led'||!fix.positioned)return;
    if(!fix.strings||!fix.strings.length)return;
    var fx=fix.x*W/emuCW,fy=H-fix.y*H/emuCH;
    fx=Math.max(12,Math.min(W-12,fx));fy=Math.max(12,Math.min(H-12,fy));
    // Preview colors
    var previewColors=null;
    if(_emuPreview&&_emuRunning){
      var frames=_emuPreview[String(fix.id)];
      if(frames&&frames.length>0){previewColors=frames[_emuT%frames.length];}
    }
    fix.strings.forEach(function(s,si){
      if(!s||!s.leds)return;
      var sdir=s.sdir||0,dx=dirDx[sdir]||0,dy=dirDy[sdir]||0;
      var lenMm=s.mm||0;if(lenMm<500)lenMm=Math.max(s.leds*16,500);
      var pxLen=dx!==0?lenMm*W/emuCW:lenMm*H/emuCH;pxLen=Math.max(pxLen,25);
      var pc=null;if(previewColors&&si<previewColors.length)pc=previewColors[si];
      var isAct=(pc&&typeof pc==='object'&&!Array.isArray(pc)&&pc.t!==undefined);
      var sR=40,sG=40,sB=45,sLit=false;
      if(pc&&Array.isArray(pc)&&(pc[0]+pc[1]+pc[2])>3){sR=pc[0];sG=pc[1];sB=pc[2];sLit=true;}
      var dotCount=Math.max(Math.min(s.leds,Math.floor(pxLen/3)),5);
      var eMs=isAct?((pc.e||0)*1000+(Date.now()%1000)):0;
      for(var di=0;di<dotCount;di++){
        var tt=(di+0.5)/dotCount,dpx=fx+dx*pxLen*tt,dpy=fy+dy*pxLen*tt;
        var r,g,b,lit;
        if(isAct){var px=_emuPixel(pc,di,dotCount,eMs);if(px){r=px[0];g=px[1];b=px[2];lit=(r+g+b)>3;}else{r=40;g=40;b=45;lit=false;}}
        else{r=sR;g=sG;b=sB;lit=sLit;}
        ctx.beginPath();ctx.arc(dpx,dpy,lit?2.5:1.5,0,2*Math.PI);
        ctx.fillStyle='rgb('+r+','+g+','+b+')';ctx.fill();
        if(lit){ctx.beginPath();ctx.arc(dpx,dpy,4,0,2*Math.PI);ctx.fillStyle='rgba('+r+','+g+','+b+',0.15)';ctx.fill();}
      }
    });
    // Node
    ctx.beginPath();ctx.arc(fx,fy,5,0,2*Math.PI);ctx.fillStyle='#22cc66';ctx.fill();
    ctx.fillStyle='#78889a';ctx.font='8px sans-serif';ctx.textAlign='center';
    ctx.fillText(fix.name||'LED',fx,fy+14);
  });

  // Time display
  var td=document.getElementById('emu-time');
  if(td){
    var m=Math.floor(_emuT/60),s=_emuT%60;
    td.textContent='Preview: '+(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
  }
}

// View prefs — loaded from localStorage, defaults per #255
var _viewDefaults={strings:true,lightCones:false,camCones:false,orient:true,cloud:false,grid:true,labels:true,stageBox:true};
var _viewPrefs=(function(){try{return Object.assign({},_viewDefaults,JSON.parse(localStorage.getItem('slyled-view-prefs')));}catch(e){return Object.assign({},_viewDefaults);}})();
var _layShowStrings=_viewPrefs.strings;
var _layShowCones=_viewPrefs.lightCones;
function _layConesToggle(){
  var cb=document.getElementById('vw-lightcones');
  _layShowCones=cb?cb.checked:!_layShowCones;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.children.forEach(function(grp){
      if(!grp.children)return;
      grp.children.forEach(function(c){
        if(c.userData&&c.userData.beamCone)c.visible=_layShowCones;
        if(c.userData&&c.userData.isAimPoint)c.visible=_layShowCones;
      });
    });
  }
  drawLayout();
}
function _layDetailToggle(){
  var cb=document.getElementById('vw-strings');
  _layShowStrings=cb?cb.checked:!_layShowStrings;
  _viewSave();drawLayout();
}

// ── View dropdown (#255) ──
function _viewSave(){
  _viewPrefs={strings:_layShowStrings,lightCones:_layShowCones,camCones:_layShowCamCones,orient:_layShowOrient,cloud:!!document.getElementById('vw-cloud')&&document.getElementById('vw-cloud').checked,grid:_layShowGrid,labels:_layShowLabels,stageBox:_layShowStageBox};
  localStorage.setItem('slyled-view-prefs',JSON.stringify(_viewPrefs));
}
function _viewSyncCheckboxes(){
  var m={strings:_layShowStrings,lightcones:_layShowCones,camcones:_layShowCamCones,orient:_layShowOrient,grid:_layShowGrid,labels:_layShowLabels,stagebox:_layShowStageBox};
  for(var k in m){var cb=document.getElementById('vw-'+k);if(cb)cb.checked=m[k];}
  var cl=document.getElementById('vw-cloud');if(cl)cl.checked=!!_viewPrefs.cloud;
}
function _toggleViewMenu(){
  var dd=document.getElementById('view-dropdown');
  var show=dd.style.display==='none';
  dd.style.display=show?'':'none';
  if(show)_viewSyncCheckboxes();
}
document.addEventListener('click',function(e){
  var dd=document.getElementById('view-dropdown');
  var btn=document.getElementById('btn-view-menu');
  if(dd&&btn&&!dd.contains(e.target)&&!btn.contains(e.target))dd.style.display='none';
});

var _layShowCamCones=_viewPrefs.camCones,_layShowOrient=_viewPrefs.orient,_layShowGrid=_viewPrefs.grid,_layShowLabels=_viewPrefs.labels,_layShowStageBox=_viewPrefs.stageBox;

function _layCamConesToggle(){
  var cb=document.getElementById('vw-camcones');
  _layShowCamCones=cb?cb.checked:!_layShowCamCones;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&c.userData.cameraFov)c.visible=_layShowCamCones;
    });
  }
}
function _layOrientToggle(){
  var cb=document.getElementById('vw-orient');
  _layShowOrient=cb?cb.checked:!_layShowOrient;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&(c.userData.orientArrow||c.userData.restArrow))c.visible=_layShowOrient;
    });
  }
}
function _layGridToggle(){
  var cb=document.getElementById('vw-grid');
  _layShowGrid=cb?cb.checked:!_layShowGrid;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&c.userData.isGrid)c.visible=_layShowGrid;
      if(c.type==='GridHelper')c.visible=_layShowGrid;
    });
  }
}
function _layLabelsToggle(){
  var cb=document.getElementById('vw-labels');
  _layShowLabels=cb?cb.checked:!_layShowLabels;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&c.userData.isLabel)c.visible=_layShowLabels;
      if(c.type==='Sprite')c.visible=_layShowLabels;
    });
  }
}
function _layStageBoxToggle(){
  var cb=document.getElementById('vw-stagebox');
  _layShowStageBox=cb?cb.checked:!_layShowStageBox;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&c.userData.stageBox)c.visible=_layShowStageBox;
    });
  }
}

function autoArrangeDmx(){
  // Evenly space DMX fixtures along the top of the stage, aimed straight down.
  // LED fixtures are left untouched.
  if(!_fixtures||!_fixtures.length){document.getElementById('hs').textContent='No fixtures to arrange';return;}
  var dmx=_fixtures.filter(function(f){return f.fixtureType==='dmx';});
  if(!dmx.length){document.getElementById('hs').textContent='No DMX fixtures to arrange';return;}
  // Fetch stage dimensions for depth
  ra('GET','/api/stage',null,function(st){
    var stageW=phW;                          // mm (X axis = width)
    var stageD=(st&&st.d?st.d*1000:10000);   // mm (Y axis = depth)
    var stageH=phH;                          // mm (Z axis = height)
    var topZ=Math.round(stageH*0.9);         // 90% up = near ceiling
    var backY=Math.round(stageD*0.8);        // 80% depth = 20% from back wall
    var n=dmx.length;
    var margin=stageW*0.1;                   // 10% margin each side
    var usableW=stageW-2*margin;
    var spacing=n>1?usableW/(n-1):0;
    dmx.forEach(function(f,i){
      f.x=Math.round(margin+i*spacing);
      f.y=backY;
      f.z=topZ;
      f._placed=true;
      // Point straight down: tilt -90° (looking at floor)
      f.rotation=[-90,0,0];
    });
    // Update layout positions
    var positions=[];
    _fixtures.forEach(function(f){
      if(f._placed||f.positioned){
        positions.push({id:f.id,x:f.x||0,y:f.y||0,z:f.z||0});
      }
    });
    ra('POST','/api/layout',{fixtures:positions},function(r){
      if(r&&r.ok){
        // Save rotation for each DMX fixture
        dmx.forEach(function(f){
          ra('PUT','/api/fixtures/'+f.id+'/aim',{rotation:f.rotation});
        });
        document.getElementById('hs').textContent='Arranged '+n+' DMX fixture(s) along top of stage';
        s3dLoadChildren();
        renderSidebar();
      }
    });
  });
}

// ── Scan mode — detect stage objects via camera ───────────────────────
var _scanGhosts=[];  // [{label,confidence,x,y,z,w,h,pixelBox}]
var _scanBusy=false;

function _layScanUpdateBtn(){
  var btn=document.getElementById('btn-lay-scan');
  if(!btn)return;
  var hasCam=(_fixtures||[]).some(function(f){return f.fixtureType==='camera'&&f.positioned;});
  btn.style.display=hasCam?'flex':'none';
}

function _layScan(){
  if(_scanBusy)return;
  var cam=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera'&&f.positioned;})[0];
  if(!cam){document.getElementById('hs').textContent='No positioned camera fixture';return;}
  _scanBusy=true;
  var btn=document.getElementById('btn-lay-scan');
  if(btn){btn.style.background='#7c3aed';btn.title='Scanning...';}
  document.getElementById('hs').textContent='Scanning for objects...';
  ra('POST','/api/cameras/'+cam.id+'/scan',{threshold:0.4,resolution:320},function(r){
    _scanBusy=false;
    if(btn){btn.style.background='#4c1d95';btn.title='Scan for objects (camera)';}
    if(!r||!r.ok){
      document.getElementById('hs').textContent='Scan failed: '+(r&&r.err||'unknown');
      return;
    }
    _scanGhosts=r.detections||[];
    if(!_scanGhosts.length){
      document.getElementById('hs').textContent='No objects detected';
      drawLayout();return;
    }
    document.getElementById('hs').textContent=_scanGhosts.length+' object(s) detected — double-click to accept, right-click to dismiss';
    s3dLoadChildren();
    if(_s3d.inited)_s3dRenderGhosts();
  });
}

function _layScanAccept(idx){
  var g=_scanGhosts[idx];
  if(!g)return;
  ra('POST','/api/objects',{
    name:g.label,
    objectType:'prop',
    mobility:'static',
    color:'#22d3ee',
    opacity:20,
    transform:{pos:[g.x,0,g.z],rot:[0,0,0],scale:[g.w,g.h,100]}
  },function(r){
    if(r&&r.ok){
      _scanGhosts.splice(idx,1);
      loadObjects(function(){
        s3dLoadChildren();
        if(_s3d.inited)_s3dRenderGhosts();
      });
      document.getElementById('hs').textContent='Added "'+g.label+'" to stage';
    }
  });
}

function _layScanDismiss(idx){
  _scanGhosts.splice(idx,1);
  document.getElementById('hs').textContent=_scanGhosts.length?_scanGhosts.length+' remaining':'All dismissed';
  if(_s3d.inited)_s3dRenderGhosts();
}

function _layScanDismissAll(){
  _scanGhosts=[];
  document.getElementById('hs').textContent='Scan results cleared';
  if(_s3d.inited)_s3dRenderGhosts();
}

// ── Calibration wizard ────────────────────────────────────────────────
var _calState=null;  // {camId, step, fixtures, detected}

function _calWizardStart(camId){
  ra('POST','/api/cameras/'+camId+'/calibrate/start',{},function(r){
    if(!r||!r.ok){
      document.getElementById('hs').textContent='Calibration failed: '+(r&&r.err||'unknown');
      return;
    }
    _calState={camId:camId,step:0,fixtures:r.fixtures||[],detected:[]};
    _calWizardShow();
  });
}

function _calWizardShow(){
  var s=_calState;if(!s)return;
  var h='<div style="min-width:400px">';
  h+='<p style="color:#94a3b8;font-size:.85em;margin-bottom:.8em">Click each reference fixture on the camera image to map pixel \u2192 stage coordinates.</p>';
  // Progress
  h+='<div class="prog-bar" style="height:8px;margin-bottom:.6em"><div class="prog-fill" style="width:'+Math.round(s.detected.length/s.fixtures.length*100)+'%;transition:width .3s"></div></div>';
  h+='<div style="font-size:.82em;color:#64748b;margin-bottom:.5em">'+s.detected.length+' / '+s.fixtures.length+' reference points</div>';
  // Fixture list
  h+='<div style="max-height:200px;overflow-y:auto;margin-bottom:.6em">';
  s.fixtures.forEach(function(f,i){
    var done=s.detected.some(function(d){return d.fixtureId===f.id;});
    var isCurrent=!done&&s.detected.length===i;
    h+='<div style="padding:.3em .5em;border-radius:4px;margin-bottom:.2em;font-size:.85em;'
      +(done?'background:#065f46;color:#34d399':isCurrent?'background:#1e3a5f;color:#93c5fd;border:1px solid #3b82f6':'color:#64748b')
      +'">'
      +(done?'\u2713 ':isCurrent?'\u25b6 ':'\u2022 ')
      +f.name+' <span style="font-size:.75em;opacity:.7">('+f.x+', '+f.z+' mm)</span>'
      +'</div>';
  });
  h+='</div>';
  // Current step: show snapshot with click-to-mark
  var nextRef=null;
  for(var i=0;i<s.fixtures.length;i++){
    if(!s.detected.some(function(d){return d.fixtureId===s.fixtures[i].id;})){
      nextRef=s.fixtures[i];break;
    }
  }
  if(nextRef){
    h+='<div style="margin-bottom:.5em;font-size:.85em;color:#22d3ee">Click on <strong>'+escapeHtml(nextRef.name)+'</strong> in the image below:</div>';
    h+='<div style="position:relative;cursor:crosshair" id="cal-snap-wrap">';
    h+='<img id="cal-snap" style="width:100%;border-radius:4px;border:1px solid #334155;display:none" onclick="_calClickSnap(event,'+nextRef.id+')">';
    h+='<canvas id="cal-marks" style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none"></canvas>';
    h+='</div>';
    h+='<div id="cal-snap-msg" style="font-size:.82em;color:#64748b;margin-top:.3em">Loading snapshot...</div>';
  }
  // Buttons
  h+='<div style="display:flex;gap:.5em;margin-top:.8em;flex-wrap:wrap">';
  if(s.detected.length>=3){
    h+='<button class="btn btn-on" onclick="_calCompute()">Compute Calibration</button>';
  }
  h+='<button class="btn btn-off" onclick="_calState=null;closeModal()">Cancel</button>';
  h+='</div>';
  h+='</div>';
  document.getElementById('modal-title').textContent='Camera Calibration';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  // Load snapshot
  if(nextRef)_calLoadSnap();
}

function _calLoadSnap(){
  var s=_calState;if(!s)return;
  var img=document.getElementById('cal-snap');
  var msg=document.getElementById('cal-snap-msg');
  var x=new XMLHttpRequest();
  // Find the camera fixture to get IP
  var camFix=(_fixtures||[]).filter(function(f){return f.id===s.camId;})[0];
  if(!camFix||!camFix.cameraIp){if(msg)msg.textContent='Camera not found';return;}
  x.open('GET','/api/cameras/'+s.camId+'/snapshot');
  x.responseType='blob';
  x.onload=function(){
    if(x.status===200&&x.response){
      img.src=URL.createObjectURL(x.response);
      img.style.display='block';
      if(msg)msg.textContent='Click on the fixture location';
      _calDrawMarks();
    }else{if(msg)msg.textContent='Snapshot failed';}
  };
  x.onerror=function(){if(msg)msg.textContent='Connection failed';};
  x.send();
}

function _calClickSnap(e,fixId){
  var s=_calState;if(!s)return;
  var img=document.getElementById('cal-snap');if(!img)return;
  var rect=img.getBoundingClientRect();
  // Map click to actual pixel coords
  var scaleX=img.naturalWidth/rect.width;
  var scaleY=img.naturalHeight/rect.height;
  var px=Math.round((e.clientX-rect.left)*scaleX);
  var py=Math.round((e.clientY-rect.top)*scaleY);
  // Send to server
  ra('POST','/api/cameras/'+s.camId+'/calibrate/detect',
    {fixtureId:fixId,pixelX:px,pixelY:py},function(r){
    if(r&&r.ok){
      s.detected.push({fixtureId:fixId,px:px,py:py});
      _calWizardShow();  // Refresh wizard
    }
  });
}

function _calDrawMarks(){
  var s=_calState;if(!s)return;
  var img=document.getElementById('cal-snap');
  var cvs=document.getElementById('cal-marks');
  if(!img||!cvs)return;
  img.onload=function(){
    cvs.width=img.naturalWidth;cvs.height=img.naturalHeight;
    var ctx=cvs.getContext('2d');
    ctx.clearRect(0,0,cvs.width,cvs.height);
    s.detected.forEach(function(d){
      ctx.beginPath();ctx.arc(d.px,d.py,12,0,Math.PI*2);
      ctx.strokeStyle='#4ade80';ctx.lineWidth=3;ctx.stroke();
      ctx.beginPath();ctx.arc(d.px,d.py,3,0,Math.PI*2);
      ctx.fillStyle='#4ade80';ctx.fill();
    });
  };
  if(img.complete&&img.naturalWidth)img.onload();
}

function _calCompute(){
  var s=_calState;if(!s)return;
  ra('POST','/api/cameras/'+s.camId+'/calibrate/compute',{},function(r){
    if(!r||!r.ok){
      document.getElementById('hs').textContent='Calibration failed: '+(r&&r.err||'unknown');
      return;
    }
    var err=r.error||0;
    var h='<div style="text-align:center;padding:1em">';
    h+='<div style="font-size:2em;color:#4ade80;margin-bottom:.3em">\u2713</div>';
    h+='<div style="font-size:1.1em;color:#e2e8f0;margin-bottom:.5em">Calibration Complete</div>';
    h+='<div style="font-size:.9em;color:#94a3b8">Reprojection error: <strong style="color:'+(err<30?'#4ade80':err<100?'#fbbf24':'#f87171')+'">'+err.toFixed(1)+' mm</strong></div>';
    if(err>100)h+='<div style="font-size:.82em;color:#fbbf24;margin-top:.5em">\u26a0 High error — consider recalibrating with more reference points</div>';
    h+='<div style="margin-top:1em"><button class="btn btn-on" onclick="closeModal();_calState=null;loadLayout()">Done</button></div>';
    h+='</div>';
    document.getElementById('modal-title').textContent='Camera Calibration';
    document.getElementById('modal-body').innerHTML=h;
    // Update fixture locally
    (_fixtures||[]).forEach(function(f){if(f.id===s.camId)f.calibrated=true;});
    renderSidebar();
  });
}

// ── Moving head range calibration ─────────────────────────────────────
var _rcalState=null;

function _rangeCalStart(fixId){
  // Find a calibrated camera
  var cam=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera'&&f.calibrated&&f.positioned;})[0];
  if(!cam){
    document.getElementById('hs').textContent='No calibrated camera available — calibrate a camera first';
    return;
  }
  _rcalState={fixId:fixId,camId:cam.id,axis:'pan',step:0,steps:9,
    panSamples:[],tiltSamples:[],sweepValues:[]};
  // Generate sweep values: 0.0, 0.125, 0.25, ..., 1.0
  for(var i=0;i<=8;i++)_rcalState.sweepValues.push(i/8);
  _rcalState.steps=_rcalState.sweepValues.length;
  _rangeCalShow();
}

function _rangeCalShow(){
  var s=_rcalState;if(!s)return;
  var fix=(_fixtures||[]).filter(function(f){return f.id===s.fixId;})[0];
  var axisLabel=s.axis==='pan'?'Pan':'Tilt';
  var totalSteps=s.steps*2;  // pan + tilt
  var doneSteps=(s.axis==='pan'?0:s.steps)+s.step;
  var h='<div style="min-width:380px">';
  h+='<p style="color:#94a3b8;font-size:.85em;margin-bottom:.6em">Sweeping <strong>'+axisLabel+'</strong> on '+escapeHtml(fix?fix.name:'fixture')+'. The head will move through its range while the camera captures beam positions.</p>';
  h+='<div class="prog-bar" style="height:8px;margin-bottom:.5em"><div class="prog-fill" style="width:'+Math.round(doneSteps/totalSteps*100)+'%"></div></div>';
  h+='<div style="font-size:.82em;color:#64748b;margin-bottom:.6em">'+axisLabel+' step '+s.step+' / '+s.steps+'</div>';
  if(s.step<s.steps){
    var val=s.sweepValues[s.step];
    h+='<div style="font-size:.9em;color:#e2e8f0;margin-bottom:.4em">'+axisLabel+' = '+val.toFixed(3)+' <span style="color:#64748b">(DMX '+(s.axis==="pan"?"pan":"tilt")+': '+Math.round(val*255)+')</span></div>';
    h+='<div id="rcal-msg" style="font-size:.82em;color:#22d3ee;margin-bottom:.4em">Sending DMX + capturing...</div>';
    h+='<img id="rcal-snap" style="width:100%;border-radius:4px;border:1px solid #334155;display:none;margin-bottom:.4em">';
  }else if(s.axis==='pan'){
    h+='<div style="font-size:.9em;color:#4ade80;margin-bottom:.5em">\u2713 Pan sweep complete ('+s.panSamples.length+' samples). Starting tilt...</div>';
  }
  h+='<div style="display:flex;gap:.5em;margin-top:.6em">';
  if(s.step>=s.steps&&s.axis==='tilt'){
    h+='<button class="btn btn-on" onclick="_rangeCalSubmit()">Save Calibration</button>';
  }
  h+='<button class="btn btn-off" onclick="_rcalState=null;closeModal()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Range Calibration — '+axisLabel;
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  // Auto-advance: send DMX, capture, detect beam
  if(s.step<s.steps){
    setTimeout(function(){_rangeCalStep();},500);
  }else if(s.axis==='pan'){
    // Switch to tilt axis
    setTimeout(function(){
      s.axis='tilt';s.step=0;_rangeCalShow();
    },1000);
  }
}

function _rangeCalStep(){
  var s=_rcalState;if(!s||s.step>=s.steps)return;
  var val=s.sweepValues[s.step];
  // Send DMX pan/tilt value to the fixture via action
  var panVal=s.axis==='pan'?val:0.5;
  var tiltVal=s.axis==='tilt'?val:0.5;
  // Use direct DMX write — set pan/tilt channels
  ra('POST','/api/fixtures/'+s.fixId+'/dmx-test',
    {pan:panVal,tilt:tiltVal,dimmer:1.0},function(){
    // Wait for head to move, then capture
    setTimeout(function(){
      // Capture snapshot from camera
      var x=new XMLHttpRequest();
      x.open('GET','/api/cameras/'+s.camId+'/snapshot');
      x.responseType='blob';
      x.onload=function(){
        var img=document.getElementById('rcal-snap');
        var msg=document.getElementById('rcal-msg');
        if(x.status===200&&x.response){
          if(img){img.src=URL.createObjectURL(x.response);img.style.display='block';}
          // Run scan to find beam position
          ra('POST','/api/cameras/'+s.camId+'/scan',
            {threshold:0.1,resolution:320,cam:0},function(r){
            // Use brightest/largest detection or center of image
            var px=320,py=240;  // default center
            if(r&&r.ok&&r.detections&&r.detections.length){
              // Use the detection with highest confidence
              var best=r.detections.sort(function(a,b){return b.confidence-a.confidence;})[0];
              if(best.pixelBox){px=best.pixelBox.x+best.pixelBox.w/2;py=best.pixelBox.y+best.pixelBox.h/2;}
            }
            var sample={dmxNorm:val,pixelX:px,pixelY:py};
            if(s.axis==='pan')s.panSamples.push(sample);
            else s.tiltSamples.push(sample);
            s.step++;
            if(msg)msg.textContent='Captured at pixel ('+Math.round(px)+', '+Math.round(py)+')';
            setTimeout(function(){_rangeCalShow();},300);
          });
        }else{
          if(msg)msg.textContent='Capture failed — skipping step';
          s.step++;
          setTimeout(function(){_rangeCalShow();},500);
        }
      };
      x.send();
    },800);  // Wait 800ms for head to settle
  });
}

function _rangeCalSubmit(){
  var s=_rcalState;if(!s)return;
  ra('POST','/api/fixtures/'+s.fixId+'/calibrate-range',
    {cameraId:s.camId,panSamples:s.panSamples,tiltSamples:s.tiltSamples},function(r){
    if(r&&r.ok){
      var h='<div style="text-align:center;padding:1em">';
      h+='<div style="font-size:2em;color:#4ade80;margin-bottom:.3em">\u2713</div>';
      h+='<div style="font-size:1.1em;color:#e2e8f0;margin-bottom:.5em">Range Calibration Complete</div>';
      h+='<div style="font-size:.85em;color:#94a3b8">Pan: '+s.panSamples.length+' samples, Tilt: '+s.tiltSamples.length+' samples</div>';
      h+='<div style="margin-top:1em"><button class="btn btn-on" onclick="closeModal();_rcalState=null;loadLayout()">Done</button></div>';
      h+='</div>';
      document.getElementById('modal-body').innerHTML=h;
      (_fixtures||[]).forEach(function(f){if(f.id===s.fixId)f.rangeCalibrated=true;});
      renderSidebar();
    }else{
      document.getElementById('hs').textContent='Range calibration failed: '+(r&&r.err||'unknown');
    }
  });
}

// ── Unified mover calibration wizard ─────────────────────────────────
var _moverCalFid=null;
var _moverCalTimer=null;
var _manCal=null; // manual calibration state: {fid, markers:[], step, currentIdx, samples:[], channels:null}

// ── Printable checkerboard + camera calibration status ───────────────

function _checkCamCalWarning(){
  var warn=document.getElementById('lay-cal-warn');if(!warn)return;
  var cams=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera'&&f.cameraIp;});
  if(!cams.length){warn.style.display='none';return;}
  var uncal=0,checked=0;
  cams.forEach(function(f){
    var ip=f.cameraIp;
    var x=new XMLHttpRequest();
    x.open('GET','/api/cameras/'+f.id+'/intrinsic');
    x.timeout=3000;
    x.onload=function(){
      checked++;
      try{var r=JSON.parse(x.responseText);if(!r.calibrated)uncal++;}catch(e){uncal++;}
      if(checked>=cams.length){
        warn.style.display=uncal>0?'inline':'none';
        if(uncal>0)warn.textContent='\u26a0 '+uncal+' camera'+(uncal>1?'s':'')+' not lens-calibrated \u2192 Settings';
      }
    };
    x.onerror=function(){checked++;if(checked>=cams.length&&uncal>0){warn.style.display='inline';}};
    x.send();
  });
}

function _printAruco(){
  // Generate all 6 ArUco 4x4 markers in a single printable HTML page
  // ArUco DICT_4X4_50 — correct patterns extracted from OpenCV cv2.aruco
  var dict=[
    [1,0,1,1, 0,1,0,1, 0,0,1,1, 0,0,1,0],  // ID 0
    [0,0,0,0, 1,1,1,1, 1,0,0,1, 1,0,1,0],  // ID 1
    [0,0,1,1, 0,0,1,1, 0,0,1,0, 1,1,0,1],  // ID 2
    [1,0,0,1, 1,0,0,1, 0,1,0,0, 0,1,1,0],  // ID 3
    [0,1,0,1, 0,1,0,0, 1,0,0,1, 1,1,1,0],  // ID 4
    [0,1,1,1, 1,0,0,1, 1,1,0,0, 1,1,0,1],  // ID 5
  ];
  var sizeMm=150,sq=Math.round(sizeMm/6);
  // Build a single HTML page with all markers (one per printed page)
  var html='<!DOCTYPE html><html><head><title>SlyLED ArUco Markers</title><style>';
  html+='@page{size:letter;margin:15mm}body{font-family:sans-serif;margin:0}';
  html+='.marker{page-break-after:always;text-align:center;padding-top:15mm}';
  html+='.marker:last-child{page-break-after:auto}';
  html+='.hdr{font-size:11pt;color:#888;margin-bottom:10mm;letter-spacing:2px}';
  html+='svg{display:block;margin:0 auto}';
  html+='p{margin-top:8mm;font-size:14pt;color:#333}';
  html+='</style></head><body>';
  for(var mid=0;mid<dict.length;mid++){
    var bits=dict[mid];
    var gs=6,w=gs*sq,h=gs*sq;
    html+='<div class="marker">';
    html+='<div class="hdr">SLYLED</div>';
    html+='<svg xmlns="http://www.w3.org/2000/svg" width="'+w+'mm" height="'+h+'mm" viewBox="0 0 '+w+' '+h+'">';
    html+='<rect width="'+w+'" height="'+h+'" fill="black"/>';
    for(var r=0;r<4;r++)for(var c=0;c<4;c++){
      if(bits[r*4+c]===1){
        html+='<rect x="'+((c+1)*sq)+'" y="'+((r+1)*sq)+'" width="'+sq+'" height="'+sq+'" fill="white"/>';
      }
    }
    html+='</svg>';
    html+='<p>ArUco 4x4 &mdash; ID '+mid+' &mdash; '+sizeMm+'mm &mdash; Print at 100% scale</p>';
    html+='</div>';
  }
  html+='</body></html>';
  // Store HTML for download
  window._arucoHtml=html;
  // Render in an iframe inside a modal — no popup blocker issues
  var mh='<div style="text-align:center;margin-bottom:.8em">';
  mh+='<button class="btn btn-on" onclick="document.getElementById(\'aruco-frame\').contentWindow.print()" style="font-size:1em;padding:.5em 1.5em">Print All Markers</button>';
  mh+=' <button class="btn" onclick="_downloadAruco()" style="font-size:.85em;background:#334;color:#ccc">Download HTML</button>';
  mh+='</div>';
  mh+='<iframe id="aruco-frame" srcdoc="'+html.replace(/"/g,'&quot;')+'" style="width:100%;height:400px;border:1px solid #334;background:#fff;border-radius:4px"></iframe>';
  document.getElementById('modal-title').textContent='SlyLED ArUco Markers (6 pages)';
  document.getElementById('modal-body').innerHTML=mh;
  document.getElementById('modal').style.display='block';
  document.getElementById('hs').textContent='ArUco markers ready — click Print to send to printer';
}
function _downloadAruco(){
  if(!window._arucoHtml)return;
  var blob=new Blob([window._arucoHtml],{type:'text/html'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='slyled-aruco-markers.html';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

function _printCheckerboard(){
  // Generate a 7x10 checkerboard SVG and open in new tab for printing
  var cols=10,rows=7,sq=25; // 25mm squares
  var w=cols*sq,h=rows*sq;
  var svg='<svg xmlns="http://www.w3.org/2000/svg" width="'+w+'mm" height="'+h+'mm" viewBox="0 0 '+w+' '+h+'">';
  svg+='<rect width="'+w+'" height="'+h+'" fill="white"/>';
  for(var r=0;r<rows;r++)for(var c=0;c<cols;c++){
    if((r+c)%2===0)svg+='<rect x="'+(c*sq)+'" y="'+(r*sq)+'" width="'+sq+'" height="'+sq+'" fill="black"/>';
  }
  svg+='<text x="'+w/2+'" y="'+(h+8)+'" text-anchor="middle" font-size="3" fill="#666">SlyLED Calibration Pattern — '+cols+'x'+rows+' squares, '+sq+'mm each — Print at 100% scale</text>';
  svg+='</svg>';
  var blob=new Blob([svg],{type:'image/svg+xml'});
  var url=URL.createObjectURL(blob);
  var win=window.open(url,'_blank');
  if(win)win.print();
}

// ── Intrinsic calibration wizard (6-step) ───────────────────────────
var _calWiz={step:1,cameras:[],captures:0,captureLog:[],results:{},markersPlaced:false,stageMap:{},_refreshTimer:null};
var _calWizMinCaptures=5;

function _loadCamCalStatus(){
  // Ensure fixtures are loaded before populating camera list
  if(!_fixtures||!_fixtures.length){
    ra('GET','/api/fixtures',null,function(fx){
      if(fx&&fx.length){_fixtures=fx;_loadCamCalStatus();}
      else{_calWizRender();}
    });
    return;
  }
  var cams=_fixtures.filter(function(f){return f.fixtureType==='camera';});
  _calWiz.cameras=cams.map(function(f){
    var existing=null;
    _calWiz.cameras.forEach(function(c){if(c.id===f.id)existing=c;});
    return{id:f.id,name:f.name,ip:f.cameraIp||'',camIdx:f.cameraIdx||0,
      selected:existing?existing.selected:!!f.cameraIp,
      calibrated:existing?existing.calibrated:false,
      rmsError:existing?existing.rmsError:null};
  });
  // Check calibration status from each camera node
  _calWiz.cameras.forEach(function(cam){
    if(!cam.ip)return;
    var x=new XMLHttpRequest();
    x.open('GET','/api/cameras/'+cam.id+'/intrinsic');
    x.timeout=5000;
    x.onload=function(){
      try{
        var r=JSON.parse(x.responseText);
        cam.calibrated=!!r.calibrated;
        cam.rmsError=r.calibrated?parseFloat(r.rmsError):null;
        _calWizRender();
      }catch(e){}
    };
    x.send();
  });
  _calWizRender();
}

function _calWizStart(){
  _calWiz.step=1;_calWiz.captures=0;_calWiz.captureLog=[];
  _calWiz.results={};_calWiz.markersPlaced=false;_calWiz.stageMap={};
  showTab('settings');_setSection('cameras');
  _loadCamCalStatus();
}

function _calWizNext(){
  if(_calWiz.step<6)_calWiz.step++;
  if(_calWiz.step===4){
    // Reset ArUco frames on all selected cameras
    _calWiz.captures=0;_calWiz.captureLog=[];
    _calWiz.cameras.forEach(function(cam){
      if(!cam.selected||!cam.ip)return;
      ra('POST','/api/cameras/'+cam.id+'/aruco/reset',{});
    });
  }
  if(_calWiz.step===5)_calWizCompute();
  _calWizRender();
}

function _calWizBack(){
  if(_calWiz._refreshTimer){clearInterval(_calWiz._refreshTimer);_calWiz._refreshTimer=null;}
  if(_calWiz.step>1)_calWiz.step--;
  _calWizRender();
}

function _calWizReset(){
  if(_calWiz._refreshTimer){clearInterval(_calWiz._refreshTimer);_calWiz._refreshTimer=null;}
  _calWiz.step=1;_calWiz.captures=0;_calWiz.captureLog=[];
  _calWiz.results={};_calWiz.markersPlaced=false;_calWiz.stageMap={};
  _calWizRender();
}

function _calWizRender(){
  var el=document.getElementById('cam-cal-wizard');if(!el)return;
  var w=_calWiz,h='';
  // Print button — always visible at top regardless of step
  h+='<div style="display:flex;gap:.5em;align-items:center;margin-bottom:.8em;padding:.5em;background:#0c1a2a;border-radius:6px;border:1px solid #1e293b">';
  h+='<button class="btn btn-on" onclick="_printAruco()" style="font-size:.82em">Print ArUco Markers</button>';
  h+='<span style="font-size:.75em;color:#64748b">Print before starting — each marker has its ID printed below the pattern</span>';
  h+='</div>';
  // Step indicator bar
  h+='<div style="display:flex;gap:.3em;margin-bottom:1.2em;align-items:center">';
  for(var si=1;si<=6;si++){
    var active=si===w.step,done=si<w.step;
    var labels=['Select','Print','Stage Map','Capture','Compute','Done'];
    h+='<div style="flex:1;text-align:center">';
    h+='<div style="height:4px;background:'+(done?'#22d3ee':active?'#3b82f6':'#1e293b')+';border-radius:2px;margin-bottom:.3em"></div>';
    h+='<span style="font-size:.7em;color:'+(active?'#e2e8f0':done?'#22d3ee':'#475569')+';font-family:Space Grotesk;font-weight:600;letter-spacing:.06em;text-transform:uppercase">'+labels[si-1]+'</span>';
    h+='</div>';
  }
  h+='</div>';

  if(w.step===1)h+=_calWizStep1();
  else if(w.step===2)h+=_calWizStep2();
  else if(w.step===3)h+=_calWizStep3();
  else if(w.step===4)h+=_calWizStep4();
  else if(w.step===5)h+=_calWizStep5();
  else if(w.step===6)h+=_calWizStep6();
  el.innerHTML=h;

  // Start snapshot refresh timer for steps 3 (stage map) and 4 (capture)
  if(w.step===3||w.step===4){
    if(w._refreshTimer)clearInterval(w._refreshTimer);
    w._refreshTimer=setInterval(function(){_calWizRefreshPreviews();},2500);
    setTimeout(function(){_calWizRefreshPreviews();},200);
  }else{
    if(w._refreshTimer){clearInterval(w._refreshTimer);w._refreshTimer=null;}
  }
}

function _calWizStep1(){
  var w=_calWiz,h='';
  h+='<div class="card" style="max-width:640px">';
  h+='<div class="card-title">Step 1: Select Cameras</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Choose which cameras to calibrate. Calibrating the lens (intrinsics) corrects for distortion and provides accurate focal length data needed for point clouds and spatial mapping.</p>';
  if(!w.cameras.length){
    h+='<p style="color:#f59e0b;font-size:.85em">No cameras registered. Add cameras in the Setup tab first.</p>';
    h+='</div>';return h;
  }
  // Select All / Deselect All
  h+='<div style="margin-bottom:.6em;display:flex;gap:.4em">';
  h+='<button class="btn" onclick="_calWizSelectAll(true)" style="font-size:.75em;padding:.2em .6em;background:#1e3a5f;color:#93c5fd">Select All</button>';
  h+='<button class="btn" onclick="_calWizSelectAll(false)" style="font-size:.75em;padding:.2em .6em;background:#1e293b;color:#64748b">Deselect All</button>';
  h+='</div>';
  // Camera list
  w.cameras.forEach(function(cam,i){
    var checked=cam.selected?'checked':'';
    var statusHtml='';
    if(cam.calibrated&&cam.rmsError!==null){
      statusHtml='<span style="color:#4ade80;font-size:.78em">\u2713 RMS='+cam.rmsError.toFixed(2)+'px</span>';
    }else if(cam.calibrated){
      statusHtml='<span style="color:#4ade80;font-size:.78em">\u2713 Calibrated</span>';
    }else{
      statusHtml='<span style="color:#94a3b8;font-size:.78em">Lens not calibrated yet</span>';
    }
    if(!cam.ip)statusHtml='<span style="color:#555;font-size:.78em">No IP — re-register in Setup tab</span>';
    h+='<div style="display:flex;align-items:center;gap:.6em;padding:.4em .5em;border-radius:4px;margin-bottom:.3em;background:'+(cam.selected?'rgba(34,211,238,.06)':'transparent')+';border:1px solid '+(cam.selected?'rgba(34,211,238,.15)':'rgba(51,65,85,.2)')+'">';
    h+='<input type="checkbox" id="calwiz-cam-'+i+'" '+checked+' onchange="_calWizToggleCam('+i+',this.checked)" style="accent-color:#22d3ee">';
    h+='<div style="flex:1"><div style="font-size:.85em;color:#e2e8f0">'+escapeHtml(cam.name)+'</div>';
    h+='<div style="font-size:.72em;color:#64748b">'+escapeHtml(cam.ip||'no IP')+(cam.camIdx?' cam'+cam.camIdx:'')+'</div></div>';
    h+=statusHtml;
    h+='</div>';
  });
  // Navigation
  var selCount=w.cameras.filter(function(c){return c.selected&&c.ip;}).length;
  h+='<div style="display:flex;justify-content:flex-end;gap:.4em;margin-top:.8em">';
  h+='<button class="btn btn-on" onclick="_calWizNext()"'+(selCount===0?' disabled':'')+' style="'+(selCount===0?'opacity:.5;cursor:not-allowed':'')+'">Next \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizStep2(){
  var w=_calWiz,h='';
  h+='<div class="card" style="max-width:640px">';
  h+='<div class="card-title">Step 2: Prepare Markers</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Print ArUco markers and place them within view of the selected cameras. The calibration process uses these markers to compute lens parameters.</p>';
  // Print buttons
  h+='<div style="display:flex;gap:.5em;flex-wrap:wrap;margin-bottom:1em">';
  h+='<button class="btn btn-on" onclick="_printAruco()" style="font-size:.85em">Print ArUco Markers</button>';
  h+='<button class="btn" onclick="_printCheckerboard()" style="font-size:.78em;padding:.3em .6em;background:#1e293b;color:#94a3b8">Print Checkerboard (close-range)</button>';
  h+='</div>';
  // Instructions card
  h+='<div style="background:rgba(34,211,238,.04);border:1px solid rgba(34,211,238,.12);border-radius:6px;padding:.8em 1em;margin-bottom:1em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em;font-weight:600">Placement instructions:</div>';
  h+='<ul style="font-size:.82em;color:#94a3b8;margin-left:1.2em;line-height:1.8">';
  h+='<li>Print 6 markers, one per sheet (A4 or Letter)</li>';
  h+='<li>Tape to boxes, walls, or truss at <strong>different heights and angles</strong></li>';
  h+='<li>Place within camera view, at various distances (1m to 5m+)</li>';
  h+='<li>Markers should be flat, well-lit, and not crumpled</li>';
  h+='<li>Avoid placing all markers on the same plane</li>';
  h+='</ul></div>';
  // Diagram: ideal placement
  h+='<div style="background:#0a0f13;border:1px solid rgba(51,65,85,.3);border-radius:6px;padding:.8em;margin-bottom:1em;text-align:center">';
  h+='<svg width="280" height="120" viewBox="0 0 280 120" style="max-width:100%">';
  // Camera
  h+='<rect x="125" y="5" width="30" height="20" rx="3" fill="#334155" stroke="#64748b"/>';
  h+='<circle cx="140" cy="15" r="6" fill="#1e293b" stroke="#22d3ee"/>';
  h+='<text x="140" y="38" text-anchor="middle" fill="#64748b" font-size="8">Camera</text>';
  // FOV lines
  h+='<line x1="140" y1="25" x2="30" y2="110" stroke="#22d3ee" stroke-width="0.5" stroke-dasharray="3,3" opacity="0.4"/>';
  h+='<line x1="140" y1="25" x2="250" y2="110" stroke="#22d3ee" stroke-width="0.5" stroke-dasharray="3,3" opacity="0.4"/>';
  // Markers at different positions and angles
  h+='<rect x="40" y="55" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(-10,49,64)"/><text x="49" y="84" text-anchor="middle" fill="#64748b" font-size="7">Low</text>';
  h+='<rect x="95" y="45" width="18" height="18" rx="1" fill="none" stroke="#4ade80"/><text x="104" y="74" text-anchor="middle" fill="#64748b" font-size="7">Mid</text>';
  h+='<rect x="165" y="40" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(15,174,49)"/><text x="174" y="69" text-anchor="middle" fill="#64748b" font-size="7">Angled</text>';
  h+='<rect x="220" y="65" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(-5,229,74)"/><text x="229" y="94" text-anchor="middle" fill="#64748b" font-size="7">Far</text>';
  h+='<rect x="60" y="90" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(8,69,99)"/><text x="69" y="118" text-anchor="middle" fill="#64748b" font-size="7">Floor</text>';
  h+='<rect x="140" y="80" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(-12,149,89)"/><text x="149" y="108" text-anchor="middle" fill="#64748b" font-size="7">Wall</text>';
  h+='</svg>';
  h+='<div style="font-size:.72em;color:#475569;margin-top:.3em">Place markers at varying heights, angles, and distances</div>';
  h+='</div>';
  // Confirmation checkbox
  h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.8em;padding:.5em;border-radius:4px;background:rgba(34,211,238,.04)">';
  h+='<input type="checkbox" id="calwiz-placed" onchange="_calWiz.markersPlaced=this.checked;_calWizRender()" style="accent-color:#22d3ee"'+(w.markersPlaced?' checked':'')+'>';
  h+='<label for="calwiz-placed" style="font-size:.85em;color:#e2e8f0;margin:0;cursor:pointer">I have placed the markers within camera view</label>';
  h+='</div>';
  // Navigation
  h+='<div style="display:flex;justify-content:space-between;gap:.4em;margin-top:.5em">';
  h+='<button class="btn" onclick="_calWizBack()" style="background:#1e293b;color:#94a3b8">\u2190 Back</button>';
  h+='<button class="btn btn-on" onclick="_calWizNext()"'+(w.markersPlaced?'':' disabled')+' style="'+(w.markersPlaced?'':'opacity:.5;cursor:not-allowed')+'">Next \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizStep3(){
  var w=_calWiz,h='';
  var selCams=w.cameras.filter(function(c){return c.selected&&c.ip;});
  var hasResult=Object.keys(w.stageMap).length>0;
  h+='<div class="card" style="max-width:720px">';
  h+='<div class="card-title">Step 3: Stage Mapping</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Place a printed ArUco marker (with ID visible) flat on the stage floor at a position you can measure. Enter the marker ID and its stage coordinates below.</p>';
  // Camera previews (auto-refreshing)
  h+='<div style="display:flex;flex-wrap:wrap;gap:.8em;margin-bottom:1em">';
  selCams.forEach(function(cam){
    h+='<div style="flex:1;min-width:200px;max-width:340px">';
    h+='<div style="font-size:.82em;color:#e2e8f0;margin-bottom:.3em;font-weight:600">'+escapeHtml(cam.name)+'</div>';
    h+='<div style="position:relative;background:#0a0f13;border:1px solid #334155;border-radius:4px;overflow:hidden;min-height:150px">';
    h+='<img id="calwiz-preview-'+cam.id+'" src="" style="width:100%;display:none;border-radius:4px" alt="Preview">';
    h+='<div id="calwiz-preview-msg-'+cam.id+'" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:.78em;color:#475569">Loading preview...</div>';
    h+='</div></div>';
  });
  h+='</div>';
  // Marker 1 inputs
  h+='<div style="background:rgba(34,211,238,.04);border:1px solid rgba(34,211,238,.12);border-radius:6px;padding:.8em 1em;margin-bottom:.8em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em;font-weight:600">Marker 1</div>';
  h+='<div style="display:flex;gap:.8em;flex-wrap:wrap;align-items:end">';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">ID</label>';
  h+='<input id="sm-id1" type="number" min="0" max="5" value="0" style="width:60px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">X (mm from stage right)</label>';
  h+='<input id="sm-x1" type="number" value="0" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">Y (mm from back wall)</label>';
  h+='<input id="sm-y1" type="number" value="0" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">Z (mm from floor)</label>';
  h+='<input id="sm-z1" type="number" value="0" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='</div></div>';
  // Marker 2 (optional, collapsed)
  h+='<div style="margin-bottom:.8em">';
  h+='<details id="sm-marker2-details">';
  h+='<summary style="font-size:.82em;color:#22d3ee;cursor:pointer;margin-bottom:.5em">+ Add second marker (optional)</summary>';
  h+='<div style="background:rgba(34,211,238,.04);border:1px solid rgba(34,211,238,.12);border-radius:6px;padding:.8em 1em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em;font-weight:600">Marker 2 (optional)</div>';
  h+='<div style="display:flex;gap:.8em;flex-wrap:wrap;align-items:end">';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">ID</label>';
  h+='<input id="sm-id2" type="number" min="0" max="5" value="" style="width:60px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">X (mm from stage right)</label>';
  h+='<input id="sm-x2" type="number" value="" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">Y (mm from back wall)</label>';
  h+='<input id="sm-y2" type="number" value="" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">Z (mm from floor)</label>';
  h+='<input id="sm-z2" type="number" value="" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='</div></div>';
  h+='</details></div>';
  // Compute button
  h+='<button class="btn btn-on" id="sm-compute-btn" onclick="_calWizComputeStageMap()" style="font-size:.9em;padding:.5em 1.5em;margin-bottom:.8em">Compute Stage Map</button>';
  // Result area
  h+='<div id="sm-result" style="margin-bottom:.8em">';
  if(hasResult){
    h+=_calWizStageMapResultHtml();
  }
  h+='</div>';
  // Navigation
  h+='<div style="display:flex;justify-content:space-between;gap:.4em;margin-top:.5em">';
  h+='<button class="btn" onclick="_calWizBack()" style="background:#1e293b;color:#94a3b8">\u2190 Back</button>';
  h+='<button class="btn btn-on" onclick="_calWizNext()" style="'+(hasResult?'':'opacity:.7')+'">Next \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizComputeStageMap(){
  var btn=document.getElementById('sm-compute-btn');
  if(btn){btn.disabled=true;btn.textContent='Computing...';}
  var markers={};
  var id1El=document.getElementById('sm-id1');
  var x1El=document.getElementById('sm-x1');
  var y1El=document.getElementById('sm-y1');
  var z1El=document.getElementById('sm-z1');
  if(id1El&&x1El&&z1El&&id1El.value!==''){
    markers[id1El.value]={x:parseInt(x1El.value)||0,y:parseInt(y1El?y1El.value:0)||0,z:parseInt(z1El.value)||0};
  }
  // Optional second marker
  var id2El=document.getElementById('sm-id2');
  var x2El=document.getElementById('sm-x2');
  var y2El=document.getElementById('sm-y2');
  var z2El=document.getElementById('sm-z2');
  if(id2El&&id2El.value!==''){
    markers[id2El.value]={x:parseInt(x2El.value)||0,y:parseInt(y2El?y2El.value:0)||0,z:parseInt(z2El.value)||0};
  }
  if(Object.keys(markers).length===0){
    if(btn){btn.disabled=false;btn.textContent='Compute Stage Map';}
    var resEl=document.getElementById('sm-result');
    if(resEl)resEl.innerHTML='<div style="color:#f87171;font-size:.85em">Enter at least one marker ID and coordinates.</div>';
    return;
  }
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  var done=0,total=selCams.length;
  _calWiz.stageMap={};
  selCams.forEach(function(cam){
    ra('POST','/api/cameras/'+cam.id+'/stage-map',
      {markers:markers,markerSize:150},
      function(r){
        done++;
        if(r&&r.ok){
          _calWiz.stageMap[cam.id]={
            cameraPosition:r.cameraPosition||null,
            rmsError:r.rmsError!=null?parseFloat(r.rmsError):null,
            distances:r.distances||null,
            markersUsed:Object.keys(markers).length
          };
        }else{
          _calWiz.stageMap[cam.id]={error:r&&r.err||'Stage map computation failed'};
        }
        if(done>=total){
          if(btn){btn.disabled=false;btn.textContent='Compute Stage Map';}
          var resEl=document.getElementById('sm-result');
          if(resEl)resEl.innerHTML=_calWizStageMapResultHtml();
        }
      });
  });
}

function _calWizStageMapResultHtml(){
  var h='';
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  h+='<div style="background:rgba(34,211,238,.04);border:1px solid rgba(34,211,238,.12);border-radius:6px;padding:.8em 1em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em;font-weight:600">Stage Map Results</div>';
  h+='<table class="tbl" style="margin-bottom:.4em"><tr><th>Camera</th><th>Position (mm)</th><th>RMS Error</th><th>Status</th></tr>';
  selCams.forEach(function(cam){
    var r=_calWiz.stageMap[cam.id];
    if(!r){
      h+='<tr><td>'+escapeHtml(cam.name)+'</td><td colspan="3" style="color:#64748b">Pending...</td></tr>';
      return;
    }
    if(r.error){
      h+='<tr><td>'+escapeHtml(cam.name)+'</td><td colspan="3" style="color:#f87171">'+escapeHtml(r.error)+'</td></tr>';
      return;
    }
    var pos=r.cameraPosition;
    var posStr=pos?'X:'+Math.round(pos.x)+' Y:'+Math.round(pos.y)+' Z:'+Math.round(pos.z):'N/A';
    var rmsStr=r.rmsError!=null?r.rmsError.toFixed(2)+'px':'N/A';
    var rmsColor=r.rmsError!=null?(r.rmsError<2?'#4ade80':r.rmsError<5?'#fbbf24':'#f87171'):'#64748b';
    h+='<tr><td>'+escapeHtml(cam.name)+'</td>';
    h+='<td style="font-family:monospace;font-size:.82em;color:#e2e8f0">'+posStr+'</td>';
    h+='<td style="color:'+rmsColor+'">'+rmsStr+'</td>';
    h+='<td style="color:#4ade80">OK</td></tr>';
  });
  h+='</table>';
  if(selCams.length>1){
    // Show inter-camera distances
    var camsWithPos=selCams.filter(function(c){var r=_calWiz.stageMap[c.id];return r&&r.cameraPosition&&!r.error;});
    if(camsWithPos.length>1){
      h+='<div style="font-size:.78em;color:#94a3b8;margin-top:.4em">Inter-camera distances: ';
      for(var i=0;i<camsWithPos.length;i++){
        for(var j=i+1;j<camsWithPos.length;j++){
          var p1=_calWiz.stageMap[camsWithPos[i].id].cameraPosition;
          var p2=_calWiz.stageMap[camsWithPos[j].id].cameraPosition;
          var dx=p1.x-p2.x,dy=p1.y-p2.y,dz=p1.z-p2.z;
          var dist=Math.sqrt(dx*dx+dy*dy+dz*dz);
          h+=escapeHtml(camsWithPos[i].name)+' \u2194 '+escapeHtml(camsWithPos[j].name)+': '+Math.round(dist)+'mm ';
        }
      }
      h+='</div>';
    }
  }
  h+='</div>';
  return h;
}

function _calWizStep4(){
  var w=_calWiz,h='';
  var selCams=w.cameras.filter(function(c){return c.selected&&c.ip;});
  var ready=w.captures>=_calWizMinCaptures;
  h+='<div class="card" style="max-width:720px">';
  h+='<div class="card-title">Step 4: Capture Frames</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.6em">Capture images from each camera with markers visible. Move some markers between captures to improve coverage. Minimum '+_calWizMinCaptures+' captures required.</p>';
  // Progress bar
  var prog=Math.min(w.captures/_calWizMinCaptures*100,100);
  h+='<div style="display:flex;align-items:center;gap:.6em;margin-bottom:.8em">';
  h+='<div class="prog-bar" style="flex:1;height:8px"><div class="prog-fill" style="width:'+prog+'%"></div></div>';
  h+='<span style="font-size:.82em;color:'+(ready?'#4ade80':'#94a3b8')+'">Capture '+w.captures+' of '+_calWizMinCaptures+(w.captures>=_calWizMinCaptures?' (minimum met)':' (minimum '+_calWizMinCaptures+' required)')+'</span>';
  h+='</div>';
  // Camera previews
  h+='<div style="display:flex;flex-wrap:wrap;gap:.8em;margin-bottom:.8em">';
  selCams.forEach(function(cam){
    h+='<div style="flex:1;min-width:200px;max-width:340px">';
    h+='<div style="font-size:.82em;color:#e2e8f0;margin-bottom:.3em;font-weight:600">'+escapeHtml(cam.name)+'</div>';
    h+='<div style="position:relative;background:#0a0f13;border:1px solid #334155;border-radius:4px;overflow:hidden;min-height:150px">';
    h+='<img id="calwiz-preview-'+cam.id+'" src="" style="width:100%;display:none;border-radius:4px" alt="Preview">';
    h+='<div id="calwiz-preview-msg-'+cam.id+'" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:.78em;color:#475569">Loading preview...</div>';
    h+='</div>';
    h+='<div id="calwiz-cam-status-'+cam.id+'" style="font-size:.75em;color:#64748b;margin-top:.2em">Ready</div>';
    h+='</div>';
  });
  h+='</div>';
  // Capture button
  h+='<div style="display:flex;gap:.5em;align-items:center;margin-bottom:.6em">';
  h+='<button class="btn btn-on" id="calwiz-capture-btn" onclick="_calWizCapture()" style="flex:0 0 auto;font-size:.9em;padding:.5em 1.5em">Capture All Cameras</button>';
  if(w.captures>0&&w.captures<_calWizMinCaptures){
    h+='<span style="font-size:.82em;color:#fbbf24">Move some markers and capture again</span>';
  }
  h+='</div>';
  // Capture log
  if(w.captureLog.length){
    h+='<div style="max-height:140px;overflow-y:auto;border:1px solid rgba(51,65,85,.2);border-radius:4px;padding:.4em .6em;margin-bottom:.6em;font-size:.78em">';
    w.captureLog.forEach(function(entry){h+=entry;});
    h+='</div>';
  }
  // Navigation
  h+='<div style="display:flex;justify-content:space-between;gap:.4em;margin-top:.5em">';
  h+='<button class="btn" onclick="_calWizBack()" style="background:#1e293b;color:#94a3b8">\u2190 Back</button>';
  h+='<button class="btn btn-on" onclick="_calWizNext()"'+(ready?'':' disabled')+' style="'+(ready?'':'opacity:.5;cursor:not-allowed')+'">Compute Calibration \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizStep5(){
  var w=_calWiz,h='';
  var selCams=w.cameras.filter(function(c){return c.selected&&c.ip;});
  var allDone=true,anyResult=false;
  selCams.forEach(function(c){var k=c.id;if(!w.results[k])allDone=false;else anyResult=true;});
  h+='<div class="card" style="max-width:720px">';
  h+='<div class="card-title">Step 5: Compute & Evaluate</div>';
  if(!allDone){
    h+='<div style="display:flex;align-items:center;gap:.6em;margin-bottom:1em;padding:.6em;background:rgba(59,130,246,.06);border-radius:4px">';
    h+='<div style="width:20px;height:20px;border:2px solid #3b82f6;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite"></div>';
    h+='<span style="font-size:.85em;color:#93c5fd">Computing calibration for all cameras...</span>';
    h+='</div>';
    h+='<style>@keyframes spin{to{transform:rotate(360deg)}}</style>';
  }
  // Results table
  if(anyResult){
    h+='<table class="tbl" style="margin-bottom:.8em"><tr><th>Camera</th><th>RMS Error</th><th>fx</th><th>fy</th><th>fx/fy</th><th>Quality</th><th>Action</th></tr>';
    selCams.forEach(function(cam){
      var r=w.results[cam.id];
      if(!r){
        h+='<tr><td>'+escapeHtml(cam.name)+'</td><td colspan="6" style="color:#64748b">Computing...</td></tr>';
        return;
      }
      if(r.error){
        h+='<tr><td>'+escapeHtml(cam.name)+'</td><td colspan="5" style="color:#f66">'+escapeHtml(r.error)+'</td>';
        h+='<td><button class="btn" onclick="_calWizRecal('+cam.id+')" style="font-size:.72em;padding:.15em .4em;background:#7f1d1d;color:#fca5a5">Redo</button></td></tr>';
        return;
      }
      var qColor={'Excellent':'#4ade80','Good':'#22d3ee','Fair':'#fbbf24','Poor':'#f87171'};
      h+='<tr><td>'+escapeHtml(cam.name)+'</td>';
      h+='<td style="color:'+(r.rms<1.5?'#4ade80':r.rms<3?'#fbbf24':'#f87171')+'">'+r.rms.toFixed(3)+'px</td>';
      h+='<td>'+r.fx.toFixed(1)+'</td>';
      h+='<td>'+r.fy.toFixed(1)+'</td>';
      h+='<td style="color:'+(r.ratio>=0.95&&r.ratio<=1.05?'#4ade80':r.ratio>=0.9&&r.ratio<=1.1?'#fbbf24':'#f87171')+'">'+r.ratio.toFixed(3)+'</td>';
      h+='<td><span class="badge" style="background:'+(r.quality==='Excellent'?'#14532d':r.quality==='Good'?'#164e63':r.quality==='Fair'?'#78350f':'#7f1d1d')+';color:'+(qColor[r.quality]||'#888')+'">'+r.quality+'</span></td>';
      h+='<td>';
      if(!r.accepted){
        h+='<button class="btn btn-on" onclick="_calWizAccept('+cam.id+')" style="font-size:.72em;padding:.15em .4em">Accept</button> ';
        h+='<button class="btn" onclick="_calWizRecal('+cam.id+')" style="font-size:.72em;padding:.15em .4em;background:#1e293b;color:#94a3b8">Redo</button>';
      }else{
        h+='<span style="color:#4ade80;font-size:.82em">\u2713 Accepted</span>';
      }
      h+='</td></tr>';
    });
    h+='</table>';
    // Bulk actions
    var acceptableCount=0,acceptedCount=0;
    selCams.forEach(function(c){var r=w.results[c.id];if(r&&!r.error){acceptableCount++;if(r.accepted)acceptedCount++;}});
    if(acceptableCount>0&&acceptedCount<acceptableCount){
      h+='<div style="display:flex;gap:.4em;margin-bottom:.6em">';
      h+='<button class="btn btn-on" onclick="_calWizAcceptAll()" style="font-size:.82em">Accept All</button>';
      h+='</div>';
    }
  }
  // Navigation
  var allAccepted=true;
  selCams.forEach(function(c){var r=w.results[c.id];if(!r||(!r.accepted&&!r.error))allAccepted=false;});
  h+='<div style="display:flex;justify-content:space-between;gap:.4em;margin-top:.5em">';
  h+='<button class="btn" onclick="_calWizBack()" style="background:#1e293b;color:#94a3b8">\u2190 Back to Capture</button>';
  h+='<button class="btn btn-on" onclick="_calWizNext()"'+(allAccepted&&allDone?'':' disabled')+' style="'+(allAccepted&&allDone?'':'opacity:.5;cursor:not-allowed')+'">Finish \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizStep6(){
  var w=_calWiz,h='';
  var selCams=w.cameras.filter(function(c){return c.selected&&c.ip;});
  var successCount=0;
  selCams.forEach(function(c){var r=w.results[c.id];if(r&&r.accepted)successCount++;});
  h+='<div class="card" style="max-width:640px;text-align:center">';
  h+='<div style="font-size:2.5em;margin-bottom:.2em">\u2713</div>';
  h+='<div class="card-title" style="font-size:1.1em;text-align:center">Calibration Complete</div>';
  h+='<p style="font-size:.9em;color:#94a3b8;margin-bottom:1em">'+successCount+' camera'+(successCount!==1?'s':'')+' calibrated successfully.</p>';
  // Summary table
  h+='<table class="tbl" style="margin:0 auto .8em;max-width:400px"><tr><th>Camera</th><th>Quality</th><th>RMS</th></tr>';
  selCams.forEach(function(cam){
    var r=w.results[cam.id];
    if(!r||r.error){
      h+='<tr><td>'+escapeHtml(cam.name)+'</td><td style="color:#f87171">Failed</td><td>-</td></tr>';
      return;
    }
    var qColor={'Excellent':'#4ade80','Good':'#22d3ee','Fair':'#fbbf24','Poor':'#f87171'};
    h+='<tr><td>'+escapeHtml(cam.name)+'</td>';
    h+='<td style="color:'+(qColor[r.quality]||'#888')+'">'+r.quality+'</td>';
    h+='<td>'+r.rms.toFixed(3)+'px</td></tr>';
  });
  h+='</table>';
  h+='<div style="font-size:.82em;color:#64748b;margin-bottom:1em;padding:.5em;background:rgba(34,211,238,.04);border-radius:4px">Calibration is saved on each camera node and will be used automatically for all point clouds and spatial mapping.</div>';
  h+='<button class="btn btn-on" onclick="_calWizReset()" style="font-size:.9em;padding:.5em 1.5em">Done</button>';
  h+='</div>';
  return h;
}

function _calWizSelectAll(val){
  _calWiz.cameras.forEach(function(c){if(c.ip)c.selected=val;});
  _calWizRender();
}

function _calWizToggleCam(idx,val){
  if(_calWiz.cameras[idx])_calWiz.cameras[idx].selected=val;
  _calWizRender();
}

function _calWizRefreshPreviews(){
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  selCams.forEach(function(cam){
    var img=document.getElementById('calwiz-preview-'+cam.id);
    var msg=document.getElementById('calwiz-preview-msg-'+cam.id);
    if(!img)return;
    var t=Date.now();
    var newImg=new Image();
    newImg.onload=function(){
      img.src=newImg.src;img.style.display='block';
      if(msg)msg.style.display='none';
    };
    newImg.onerror=function(){
      if(msg){msg.textContent='Camera offline';msg.style.display='block';}
    };
    newImg.src='http://'+cam.ip+':5000/snapshot?cam='+(cam.camIdx||0)+'&t='+t;
  });
}

function _calWizCapture(){
  var btn=document.getElementById('calwiz-capture-btn');
  if(btn){btn.disabled=true;btn.textContent='Capturing...';}
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  var done=0,total=selCams.length;
  _calWiz.captures++;
  /* Group cameras by IP so cameras on the same hardware node are captured
     serially (avoids USB bus contention), while different nodes run in parallel. */
  var groups={};
  selCams.forEach(function(cam){
    if(!groups[cam.ip])groups[cam.ip]=[];
    groups[cam.ip].push(cam);
  });
  function captureCam(cam,cb){
    var statusEl=document.getElementById('calwiz-cam-status-'+cam.id);
    if(statusEl)statusEl.innerHTML='<span style="color:#93c5fd">Capturing...</span>';
    ra('POST','/api/cameras/'+cam.id+'/aruco/capture',{},function(r){
      var entry='';
      if(!r||!r.ok){
        entry='<div style="color:#f66">Capture '+_calWiz.captures+' - '+escapeHtml(cam.name)+': Failed</div>';
        if(statusEl)statusEl.innerHTML='<span style="color:#f66">Capture failed</span>';
      }else{
        var cams=r.cameras||[r];
        cams.forEach(function(c){
          if(c.markersFound>0){
            entry='<div style="color:#4ade80">\u2713 Capture '+_calWiz.captures+' - '+escapeHtml(cam.name)+': '+c.markersFound+' markers (IDs: '+(c.ids||[]).join(',')+'), total frames: '+c.frameCount+'</div>';
            if(statusEl)statusEl.innerHTML='<span style="color:#4ade80">\u2713 '+c.frameCount+' frames, '+c.markersFound+' markers last</span>';
          }else{
            entry='<div style="color:#f59e0b">\u2717 Capture '+_calWiz.captures+' - '+escapeHtml(cam.name)+': No markers found</div>';
            if(statusEl)statusEl.innerHTML='<span style="color:#f59e0b">No markers found</span>';
          }
        });
      }
      _calWiz.captureLog.push(entry);
      done++;
      if(done>=total){
        if(btn){btn.disabled=false;btn.textContent='Capture All Cameras';}
        _calWizRender();
      }
      cb();
    });
  }
  /* Each IP group runs serially; different IP groups run in parallel */
  Object.keys(groups).forEach(function(ip){
    var camsOnNode=groups[ip];
    (function captureNext(idx){
      if(idx>=camsOnNode.length)return;
      captureCam(camsOnNode[idx],function(){captureNext(idx+1);});
    })(0);
  });
}

function _calWizCompute(){
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  selCams.forEach(function(cam){
    ra('POST','/api/cameras/'+cam.id+'/aruco/compute',{markerSize:150},function(r){
      if(!r||!r.ok){
        _calWiz.results[cam.id]={error:r&&r.err||'Computation failed'};
      }else{
        var rms=parseFloat(r.rmsError)||0;
        var fx=parseFloat(r.fx)||0;
        var fy=parseFloat(r.fy)||0;
        var ratio=fy>0?fx/fy:0;
        var quality='Poor';
        if(rms<0.5&&ratio>=0.95&&ratio<=1.05)quality='Excellent';
        else if(rms<1.5&&ratio>=0.9&&ratio<=1.1)quality='Good';
        else if(rms<3)quality='Fair';
        _calWiz.results[cam.id]={rms:rms,fx:fx,fy:fy,cx:parseFloat(r.cx)||0,cy:parseFloat(r.cy)||0,
          ratio:ratio,quality:quality,frameCount:r.frameCount||0,accepted:false};
        // Update local fixture data
        cam.calibrated=true;cam.rmsError=rms;
        (_fixtures||[]).forEach(function(f){if(f.id===cam.id)f.intrinsicCalibrated=true;});
      }
      _calWizRender();
    });
  });
}

function _calWizAccept(camId){
  if(_calWiz.results[camId])_calWiz.results[camId].accepted=true;
  _calWizRender();
}

function _calWizAcceptAll(){
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  selCams.forEach(function(c){if(_calWiz.results[c.id]&&!_calWiz.results[c.id].error)_calWiz.results[c.id].accepted=true;});
  _calWizRender();
}

function _calWizRecal(camId){
  delete _calWiz.results[camId];
  _calWiz.step=4;
  _calWizRender();
}

// Legacy alias — called from layout panel and tests
function _intrinsicCalStart(fid){
  // Pre-select just this camera and jump to wizard
  _calWiz.step=1;_calWiz.captures=0;_calWiz.captureLog=[];
  _calWiz.results={};_calWiz.markersPlaced=false;_calWiz.stageMap={};
  var cams=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera';});
  _calWiz.cameras=cams.map(function(f){
    return{id:f.id,name:f.name,ip:f.cameraIp||'',camIdx:f.cameraIdx||0,
      selected:f.id===fid,calibrated:false,rmsError:null};
  });
  showTab('settings');_setSection('cameras');
}

function _moverCalStart(fixId){
  _moverCalFid=fixId;
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===fixId)f=fx;});
  var hasCams=(_fixtures||[]).some(function(fx){return fx.fixtureType==='camera'&&fx.cameraIp;});
  var h='<div style="min-width:400px">';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Choose calibration method for <strong>'+escapeHtml(f?f.name:'fixture')+'</strong>:</div>';
  h+='<div style="display:flex;gap:.6em;flex-direction:column">';
  // Automatic option
  h+='<div class="card" style="padding:.8em;cursor:'+(hasCams?'pointer':'default')+';opacity:'+(hasCams?'1':'.5')+'" '+(hasCams?'onclick="_moverCalAutoStart()"':'')+' >';
  h+='<div style="font-size:.95em;color:#e2e8f0;font-weight:600">\ud83d\udcf7 Automatic (Camera)</div>';
  h+='<div style="font-size:.8em;color:#94a3b8;margin-top:.2em">Uses camera beam detection to discover and map the fixture automatically.</div>';
  if(!hasCams)h+='<div style="font-size:.78em;color:#f59e0b;margin-top:.2em">No cameras in layout \u2014 add a camera fixture first.</div>';
  h+='</div>';
  // Manual option
  h+='<div class="card" style="padding:.8em;cursor:pointer" onclick="_moverCalManualStart('+fixId+')">';
  h+='<div style="font-size:.95em;color:#e2e8f0;font-weight:600">\ud83c\udfaf Manual (Jog to Markers)</div>';
  h+='<div style="font-size:.8em;color:#94a3b8;margin-top:.2em">Place markers at known positions, then aim the beam at each one. Works without cameras.</div>';
  h+='</div></div>';
  // Verify existing calibration option
  if(f&&f.moverCalibrated){
    h+='<div class="card" style="padding:.8em;cursor:pointer" onclick="_manCalVerifyExisting('+fixId+')">';
    h+='<div style="font-size:.95em;color:#e2e8f0;font-weight:600">\u2705 Verify Existing Calibration</div>';
    h+='<div style="font-size:.8em;color:#94a3b8;margin-top:.2em">Test saved calibration positions. Send the beam to each recorded marker to confirm accuracy.</div>';
    h+='</div>';
  }
  h+='</div>';
  h+='<div style="margin-top:.6em"><button class="btn btn-off" onclick="closeModal()">Cancel</button></div>';
  h+='</div>';
  document.getElementById('modal-title').textContent='Calibrate Mover';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}
function _moverCalAutoStart(){
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===_moverCalFid)f=fx;});
  var h='<div style="min-width:400px">';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Automatic calibration for <strong>'+escapeHtml(f?f.name:'fixture')+'</strong>.</div>';
  h+='<div class="card" style="padding:.6em;margin-bottom:.6em">';
  h+='<div style="font-size:.82em;color:#f59e0b;margin-bottom:.3em">\u26a0 Ensure Art-Net engine is running and fixture responds to DMX.</div>';
  h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.3em">Dim or turn off room lights for best results.</div>';
  h+='<div style="display:flex;gap:.4em;align-items:center;margin-bottom:.3em">';
  h+='<label style="font-size:.82em;color:#94a3b8;margin:0">Beam color:</label>';
  h+='<select id="mcal-color" style="font-size:.82em;padding:2px 4px">';
  h+='<option value="green">Green</option><option value="magenta">Magenta</option><option value="red">Red</option><option value="blue">Blue</option>';
  h+='</select></div></div>';
  h+='<div id="mcal-status" style="display:none">';
  h+='<div class="prog-bar" style="height:8px;margin-bottom:.4em"><div class="prog-fill" id="mcal-prog" style="width:0%;transition:width .3s"></div></div>';
  h+='<div id="mcal-phase" style="font-size:.85em;color:#e2e8f0;margin-bottom:.3em"></div>';
  h+='<div id="mcal-detail" style="font-size:.78em;color:#64748b"></div>';
  h+='</div>';
  h+='<div style="display:flex;gap:.4em;margin-top:.8em">';
  h+='<button class="btn btn-on" id="mcal-go" onclick="_moverCalGo()">Start Calibration</button>';
  h+='<button class="btn btn-off" onclick="_moverCalCancel()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Automatic Calibration';
  document.getElementById('modal-body').innerHTML=h;
}

function _moverCalGo(){
  var sel=document.getElementById('mcal-color');
  var colorMap={green:[0,255,0],magenta:[255,0,255],red:[255,0,0],blue:[0,0,255]};
  var color=colorMap[sel?sel.value:'green']||[0,255,0];
  var btn=document.getElementById('mcal-go');
  if(btn)btn.disabled=true;
  document.getElementById('mcal-status').style.display='block';
  document.getElementById('mcal-phase').textContent='Starting calibration...';
  ra('POST','/api/calibration/mover/'+_moverCalFid+'/start',{color:color},function(r){
    if(!r||!r.ok){
      document.getElementById('mcal-phase').innerHTML='<span style="color:#f66">'+(r&&r.err||'Failed to start')+'</span>';
      if(btn)btn.disabled=false;
      return;
    }
    document.getElementById('mcal-detail').textContent='Camera: '+(r.cameraName||'unknown');
    _moverCalPoll();
  });
}

function _moverCalPoll(){
  if(_moverCalTimer)clearTimeout(_moverCalTimer);
  ra('GET','/api/calibration/mover/'+_moverCalFid+'/status',null,function(r){
    if(!r)return;
    var prog=document.getElementById('mcal-prog');
    var phase=document.getElementById('mcal-phase');
    var detail=document.getElementById('mcal-detail');
    if(prog)prog.style.width=(r.progress||0)+'%';
    var phaseNames={starting:'Starting...',discovery:'Discovering beam...',mapping:'Mapping visible region...',grid:'Building interpolation grid...',complete:'Complete'};
    if(phase)phase.textContent=phaseNames[r.phase]||r.phase||'';
    if(r.status==='running'){
      _moverCalTimer=setTimeout(_moverCalPoll,1000);
    }else if(r.status==='done'){
      if(prog)prog.style.width='100%';
      var res=r.result||{};
      var h='<div style="text-align:center;padding:.5em">';
      h+='<div style="font-size:2em;color:#4ade80;margin-bottom:.3em">\u2713</div>';
      h+='<div style="font-size:1.1em;color:#e2e8f0;margin-bottom:.3em">Calibration Complete</div>';
      h+='<div style="font-size:.85em;color:#94a3b8">'+res.sampleCount+' samples, grid size '+res.gridSize+'</div>';
      h+='<div style="margin-top:.8em;display:flex;gap:.4em;justify-content:center">';
      h+='<button class="btn btn-on" onclick="closeModal();_moverCalFid=null;loadLayout()">Done</button>';
      h+='<button class="btn" style="background:#334155;color:#94a3b8" onclick="_moverCalDelete()">Recalibrate</button>';
      h+='</div></div>';
      document.getElementById('mcal-status').innerHTML=h;
      (_fixtures||[]).forEach(function(f){if(f.id===_moverCalFid)f.moverCalibrated=true;});
      renderSidebar();
    }else if(r.status==='error'){
      if(phase)phase.innerHTML='<span style="color:#f66">'+(r.error||'Unknown error')+'</span>';
      var btn=document.getElementById('mcal-go');
      if(btn){btn.disabled=false;btn.textContent='Retry';}
    }
  });
}

function _moverCalCancel(){
  if(_moverCalTimer)clearTimeout(_moverCalTimer);
  _moverCalFid=null;closeModal();
}

function _moverCalDelete(){
  if(!_moverCalFid)return;
  ra('DELETE','/api/calibration/mover/'+_moverCalFid,null,function(){
    (_fixtures||[]).forEach(function(f){if(f.id===_moverCalFid)f.moverCalibrated=false;});
    _moverCalStart(_moverCalFid);
  });
}

// ── Manual mover calibration (#368) ──────────────────────────────────

function _moverCalManualStart(fixId){
  _moverCalFid=fixId;
  // Ensure _fixtures have positions from layout
  ra('GET','/api/layout',null,function(lay){
    if(lay&&lay.fixtures)_fixtures=lay.fixtures;
    _moverCalManualStart2(fixId);
  });
}
var _calMarkers=(function(){try{var s=localStorage.getItem('slyled_cal_markers');return s?JSON.parse(s):null;}catch(e){return null;}})();
function _moverCalManualStart2(fixId){
  // Reuse persisted markers or start empty
  var markers=_calMarkers?JSON.parse(JSON.stringify(_calMarkers)):[];
  _manCal={fid:fixId,markers:markers,step:'markers',currentIdx:0,samples:[],channels:null,savedSamples:null};
  // Load existing calibration samples for this fixture (for restoring positions)
  ra('GET','/api/calibration/mover/'+fixId,null,function(cal){
    if(cal&&cal.samples)_manCal.savedSamples=cal.samples;
    _manCalRender();
  });
}

function _manCalRender(){
  if(!_manCal)return;
  var fid=_manCal.fid;
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===fid)f=fx;});
  var fname=f?f.name:'Fixture';
  if(_manCal.step==='markers')_manCalRenderMarkers(fname);
  else if(_manCal.step==='jog')_manCalRenderJog(fname);
  else if(_manCal.step==='verify')_manCalRenderVerify(fname);
  else if(_manCal.step==='done')_manCalRenderDone(fname);
}

function _manCalRenderMarkers(fname){
  var h='<div style="min-width:420px">';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.6em">Place physical markers (tape, objects) at known stage positions. You\'ll aim <strong>'+escapeHtml(fname)+'</strong> at each one.</div>';
  h+='<div style="font-size:.78em;color:#64748b;margin-bottom:.6em">Spread markers across the stage floor for best results. Minimum 2, recommended 4+.</div>';
  h+='<table style="width:100%;font-size:.82em;border-collapse:collapse">';
  h+='<tr style="color:#94a3b8"><th style="text-align:left;padding:2px 4px">#</th><th>Name</th><th>X (mm)</th><th>Y (mm)</th><th>Z (mm)</th><th></th></tr>';
  _manCal.markers.forEach(function(m,i){
    h+='<tr><td style="padding:2px 4px;color:#94a3b8">'+(i+1)+'</td>';
    h+='<td style="font-size:.78em;color:#22d3ee;max-width:80px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">'+(m.name?escapeHtml(m.name):'')+'</td>';
    h+='<td><input type="number" value="'+m.x+'" style="width:70px" onchange="_manCal.markers['+i+'].x=parseInt(this.value)||0"></td>';
    h+='<td><input type="number" value="'+m.y+'" style="width:70px" onchange="_manCal.markers['+i+'].y=parseInt(this.value)||0"></td>';
    h+='<td><input type="number" value="'+m.z+'" style="width:70px" onchange="_manCal.markers['+i+'].z=parseInt(this.value)||0"></td>';
    h+='<td><button class="btn" style="font-size:.75em;padding:1px 6px;background:#7f1d1d;color:#fca5a5" onclick="_manCal.markers.splice('+i+',1);_manCalRender()">&#x2715;</button></td></tr>';
  });
  h+='</table>';
  h+='<div style="margin-top:.4em;display:flex;gap:.5em;align-items:center;flex-wrap:wrap">';
  h+='<button class="btn" style="font-size:.8em;background:#1e3a5f;color:#93c5fd" onclick="_manCalAddMarker()">+ Add Marker</button>';
  // Dropdown — pick position from a layout object or fixture
  var pickItems=[];
  (_objects||[]).filter(function(o){return o.transform&&o.transform.pos;}).forEach(function(o){
    var p=o.transform.pos;
    pickItems.push({id:'obj:'+o.id,name:o.name,x:p[0],y:p[1],z:p[2]});
  });
  // Also include positioned fixtures (cameras, other movers — not the one being calibrated)
  // _fixtures already have x/y/z merged from layout by loadLayout()
  (_fixtures||[]).forEach(function(f){
    if(f.id===_manCal.fid)return; // skip self
    if(f.x||f.y||f.z){
      pickItems.push({id:'fix:'+f.id,name:f.name||('Fixture '+f.id),x:f.x,y:f.y,z:f.z});
    }
  });
  if(pickItems.length){
    h+='<span style="font-size:.78em;color:#64748b">or from:</span>';
    h+='<select style="font-size:.8em" onchange="_manCalAddFromPick(this)">';
    h+='<option value="">— pick —</option>';
    pickItems.forEach(function(it){
      h+='<option value="'+it.id+'">'+escapeHtml(it.name)+' ('+it.x+','+it.y+','+it.z+')</option>';
    });
    h+='</select>';
  }
  h+='</div>';
  h+='<div style="display:flex;gap:.4em;margin-top:.8em">';
  var canNext=_manCal.markers.length>=2;
  h+='<button class="btn btn-on" onclick="_manCalNextToJog()"'+(canNext?'':' disabled')+'>Next: Jog to Markers</button>';
  h+='<button class="btn btn-off" onclick="_manCal=null;closeModal()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Manual Calibration \u2014 Markers';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function _manCalAddMarker(){
  var sm=_sfStageMm();
  _manCal.markers.push({x:Math.round(sm.sw/2),y:Math.round(sm.sd/2),z:0});
  _manCalRender();
}
function _manCalAddFromPick(selEl){
  var val=selEl.value;if(!val)return;
  selEl.value='';
  var parts=val.split(':');var type=parts[0];var id=parseInt(parts[1]);
  var name='',x=0,y=0,z=0;
  if(type==='obj'){
    (_objects||[]).forEach(function(o){if(o.id===id&&o.transform){
      var p=o.transform.pos||[0,0,0];name=o.name;x=p[0];y=p[1];z=p[2];}});
  }else if(type==='fix'){
    (_fixtures||[]).forEach(function(f){if(f.id===id){name=f.name||'Fixture';x=f.x||0;y=f.y||0;z=f.z||0;}});
  }
  if(!name)return;
  _manCal.markers.push({x:x,y:y,z:z,name:name});
  _manCalRender();
}

function _manCalNextToJog(){
  if(_manCal.markers.length<2)return;
  // Persist markers for reuse across fixtures and sessions
  _calMarkers=JSON.parse(JSON.stringify(_manCal.markers));
  try{localStorage.setItem('slyled_cal_markers',JSON.stringify(_calMarkers));}catch(e){}
  _manCal.step='jog';
  _manCal.currentIdx=0;
  _manCal.samples=[];
  // Fetch channel info for pan/tilt/dimmer offsets
  ra('GET','/api/dmx/fixture/'+_manCal.fid+'/channels',null,function(d){
    if(!d||!d.channels){_manCal.channels={};_manCalRender();return;}
    var ch={};
    d.channels.forEach(function(c){
      if(c.type==='pan')ch.pan=c.offset;
      if(c.type==='tilt')ch.tilt=c.offset;
      if(c.type==='dimmer')ch.dimmer=c.offset;
      if(c.type==='red')ch.red=c.offset;
      if(c.type==='green')ch.green=c.offset;
      if(c.type==='blue')ch.blue=c.offset;
      if(c.type==='white')ch.white=c.offset;
      if(c.type==='speed')ch.speed=c.offset;
      if(c.type==='strobe')ch.strobe=c.offset;
      if(c.type==='gobo')ch.gobo=c.offset;
    });
    _manCal.channels=ch;
    _manCal.allChannels=d.channels; // full channel list for DMX display
    // Set fixture to default jog state: all zero except dimmer=255, green=255
    var fid=_manCal.fid;
    var initChs=d.channels.map(function(c){
      if(c.type==='dimmer')return{offset:c.offset,value:255};
      if(c.type==='green')return{offset:c.offset,value:255};
      return{offset:c.offset,value:0};
    });
    ra('POST','/api/dmx/fixture/'+fid+'/test',{channels:initChs},function(){
      _manCalRender();
    });
  });
}

function _manCalRenderJog(fname){
  var idx=_manCal.currentIdx;
  var total=_manCal.markers.length;
  var m=_manCal.markers[idx];
  var ch=_manCal.channels||{};
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===_manCal.fid)f=fx;});
  var addr=f?('U'+(f.dmxUniverse||1)+' @ '+(f.dmxStartAddr||1)):'';
  var h='<div style="min-width:460px">';
  // Progress
  h+='<div class="prog-bar" style="height:6px;margin-bottom:.5em"><div class="prog-fill" style="width:'+Math.round((idx/total)*100)+'%"></div></div>';
  h+='<div style="font-size:.9em;color:#e2e8f0;margin-bottom:.4em">Marker <strong>'+(idx+1)+' of '+total+'</strong>: aim beam at <span style="color:#22d3ee">X='+m.x+' Y='+m.y+' Z='+m.z+'</span> mm'+(m.name?' <span style="color:#94a3b8">('+escapeHtml(m.name)+')</span>':'')+'</div>';
  h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.6em">Adjust sliders until the beam centers on the physical marker.</div>';
  // All DMX channels
  var allCh=_manCal.allChannels||[];
  var panVal=128, tiltVal=128;
  // Try to restore from: 1) previous sample for this marker, 2) saved calibration, 3) last confirmed sample
  if(_manCal.samples[idx]){
    panVal=Math.round(_manCal.samples[idx].pan*255);
    tiltVal=Math.round(_manCal.samples[idx].tilt*255);
  }else if(_manCal.savedSamples){
    // Match saved sample by stage position
    var m=_manCal.markers[idx];
    _manCal.savedSamples.forEach(function(s){
      if(s.stageX===m.x&&s.stageY===m.y&&s.stageZ===m.z){panVal=Math.round(s.pan*255);tiltVal=Math.round(s.tilt*255);}
    });
  }else if(idx>0&&_manCal.samples.length>0){
    var ls=_manCal.samples[_manCal.samples.length-1];panVal=Math.round(ls.pan*255);tiltVal=Math.round(ls.tilt*255);
  }
  h+='<div style="max-height:280px;overflow-y:auto;border:1px solid #1e293b;border-radius:4px;padding:.4em;background:#0a0f1a;margin-bottom:.6em">';
  if(allCh.length){
    allCh.forEach(function(c){
      var isPan=(c.type==='pan'),isTilt=(c.type==='tilt');
      var isDim=(c.type==='dimmer'),isGreen=(c.type==='green'),isSpeed=(c.type==='speed');
      var defVal=isPan?panVal:isTilt?tiltVal:isDim?255:isGreen?255:isSpeed?0:(c.default||0);
      // Current value from last state or default
      var curVal=defVal;
      var highlight=isPan||isTilt?'color:#22d3ee;font-weight:600':'color:#94a3b8';
      h+='<div style="display:flex;align-items:center;gap:.4em;margin-bottom:.2em">';
      h+='<label style="width:90px;font-size:.78em;'+highlight+';text-align:right;overflow:hidden;white-space:nowrap;text-overflow:ellipsis" title="'+escapeHtml(c.name)+'">'+escapeHtml(c.name)+'</label>';
      h+='<input type="range" min="0" max="255" value="'+curVal+'" style="flex:1" id="mcj-ch-'+c.offset+'" oninput="_manCalJogCh('+c.offset+',this.value)">';
      h+='<span id="mcj-chv-'+c.offset+'" style="width:28px;font-size:.78em;color:#e2e8f0;font-family:monospace;text-align:right">'+curVal+'</span>';
      h+='</div>';
    });
  } else {
    // Fallback: just pan/tilt
    h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.3em">';
    h+='<label style="width:40px;font-size:.82em;color:#22d3ee;text-align:right">Pan</label>';
    h+='<input type="range" id="mcj-ch-'+(ch.pan||0)+'" min="0" max="255" value="'+panVal+'" style="flex:1" oninput="_manCalJogCh('+(ch.pan||0)+',this.value)">';
    h+='<span id="mcj-chv-'+(ch.pan||0)+'" style="width:28px;font-size:.78em;color:#e2e8f0;font-family:monospace">'+panVal+'</span></div>';
    h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.3em">';
    h+='<label style="width:40px;font-size:.82em;color:#22d3ee;text-align:right">Tilt</label>';
    h+='<input type="range" id="mcj-ch-'+(ch.tilt||1)+'" min="0" max="255" value="'+tiltVal+'" style="flex:1" oninput="_manCalJogCh('+(ch.tilt||1)+',this.value)">';
    h+='<span id="mcj-chv-'+(ch.tilt||1)+'" style="width:28px;font-size:.78em;color:#e2e8f0;font-family:monospace">'+tiltVal+'</span></div>';
  }
  h+='</div>';
  // Fine adjust buttons for pan/tilt
  h+='<div style="display:flex;gap:.3em;margin-bottom:.6em;justify-content:center">';
  h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'pan\',-1)">Pan \u25c0</button>';
  h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'pan\',1)">Pan \u25b6</button>';
  h+='<span style="width:10px"></span>';
  h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'tilt\',-1)">Tilt \u25b2</button>';
  h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'tilt\',1)">Tilt \u25bc</button>';
  h+='</div>';
  // Buttons
  h+='<div style="display:flex;gap:.4em">';
  h+='<button class="btn btn-on" onclick="_manCalConfirm()">Confirm Position</button>';
  if(idx>0)h+='<button class="btn" style="background:#334155;color:#94a3b8" onclick="_manCal.currentIdx--;_manCal.samples.pop();_manCalRender()">Back</button>';
  h+='<button class="btn btn-off" onclick="_manCalBlackout();_manCal=null;closeModal()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent=escapeHtml(fname)+' \u2014 '+addr+' \u2014 Jog';
  document.getElementById('modal-body').innerHTML=h;
  // Do NOT send DMX on render — only send when user moves a slider (#368)
}

function _manCalJogCh(offset,value){
  var v=parseInt(value);
  var el=document.getElementById('mcj-chv-'+offset);
  if(el)el.textContent=v;
  // Send ALL current slider values as a batch so fixture gets consistent state
  var allCh=(_manCal&&_manCal.allChannels)||[];
  var batch=[];
  allCh.forEach(function(c){
    var sl=document.getElementById('mcj-ch-'+c.offset);
    if(sl)batch.push({offset:c.offset,value:parseInt(sl.value)||0});
  });
  if(batch.length)ra('POST','/api/dmx/fixture/'+_manCal.fid+'/test',{channels:batch},function(){});
}

function _manCalJog(axis,value){
  var ch=_manCal.channels||{};
  var offset=axis==='pan'?ch.pan:ch.tilt;
  if(offset==null)return;
  var v=parseInt(value);
  _manCalJogCh(offset,v);
  var sl=document.getElementById('mcj-ch-'+offset);
  if(sl)sl.value=v;
}

function _manCalNudge(axis,dir){
  var ch=_manCal.channels||{};
  var offset=axis==='pan'?ch.pan:ch.tilt;
  if(offset==null)return;
  var sl=document.getElementById('mcj-ch-'+offset);
  if(!sl)return;
  var v=Math.max(0,Math.min(255,parseInt(sl.value)+dir));
  sl.value=v;
  _manCalJogCh(offset,v);
}

function _manCalConfirm(){
  var ch=_manCal.channels||{};
  var panEl=document.getElementById('mcj-ch-'+(ch.pan!=null?ch.pan:0));
  var tiltEl=document.getElementById('mcj-ch-'+(ch.tilt!=null?ch.tilt:1));
  var pan=(panEl?parseInt(panEl.value):128)/255;
  var tilt=(tiltEl?parseInt(tiltEl.value):128)/255;
  var m=_manCal.markers[_manCal.currentIdx];
  _manCal.samples.push({pan:pan,tilt:tilt,stageX:m.x,stageY:m.y,stageZ:m.z});
  _manCal.currentIdx++;
  if(_manCal.currentIdx>=_manCal.markers.length){
    // All markers done — save
    _manCalSave();
  }else{
    _manCalRender();
  }
}

function _manCalSave(){
  _manCal.step='saving';
  document.getElementById('modal-body').innerHTML='<div style="text-align:center;padding:1em;color:#94a3b8">Saving calibration...</div>';
  ra('POST','/api/calibration/mover/'+_manCal.fid+'/manual',{samples:_manCal.samples},function(r){
    if(r&&r.ok){
      (_fixtures||[]).forEach(function(f){if(f.id===_manCal.fid)f.moverCalibrated=true;});
      _manCal.step='verify';
      _manCalRender();
    }else{
      document.getElementById('modal-body').innerHTML='<div style="color:#f66;padding:1em">'+(r&&r.err||'Save failed')+'</div>';
    }
  });
}

function _manCalRenderVerify(fname){
  var h='<div style="min-width:400px;text-align:center;padding:.5em">';
  h+='<div style="font-size:2em;color:#4ade80;margin-bottom:.3em">\u2713</div>';
  h+='<div style="font-size:1.1em;color:#e2e8f0;margin-bottom:.3em">Manual Calibration Complete</div>';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">'+_manCal.samples.length+' positions recorded for '+escapeHtml(fname)+'</div>';
  h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.6em">Test each marker \u2014 the beam should aim at the physical position:</div>';
  h+='<div style="text-align:left;max-height:200px;overflow-y:auto">';
  _manCal.samples.forEach(function(s,i){
    h+='<div style="display:flex;align-items:center;gap:.4em;padding:.3em;border-bottom:1px solid #1e293b">';
    h+='<span style="color:#94a3b8;font-size:.82em;width:20px">'+(i+1)+'</span>';
    h+='<span style="font-size:.82em;color:#e2e8f0;flex:1">X='+s.stageX+' Y='+s.stageY+'</span>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 10px;background:#1e3a5f;color:#93c5fd" onclick="_manCalTest('+i+')">Test</button>';
    h+='</div>';
  });
  h+='</div>';
  h+='<div style="display:flex;gap:.4em;margin-top:.8em;justify-content:center">';
  h+='<button class="btn btn-on" onclick="_manCalBlackout();closeModal();_manCal=null;loadLayout()">Done</button>';
  h+='<button class="btn" style="background:#334155;color:#94a3b8" onclick="_moverCalDelete()">Recalibrate</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Calibration \u2014 Verify';
  document.getElementById('modal-body').innerHTML=h;
}

function _manCalTest(idx){
  var s=_manCal.samples[idx];
  var ch=_manCal.channels||{};
  var chs=[];
  if(ch.pan!=null)chs.push({offset:ch.pan,value:Math.round(s.pan*255)});
  if(ch.tilt!=null)chs.push({offset:ch.tilt,value:Math.round(s.tilt*255)});
  if(ch.dimmer!=null)chs.push({offset:ch.dimmer,value:255});
  if(ch.red!=null)chs.push({offset:ch.red,value:255});
  if(ch.green!=null)chs.push({offset:ch.green,value:255});
  if(ch.blue!=null)chs.push({offset:ch.blue,value:255});
  ra('POST','/api/dmx/fixture/'+_manCal.fid+'/test',{channels:chs},function(){});
}

function _manCalBlackout(){
  var ch=_manCal?_manCal.channels:{};
  if(!ch)return;
  var chs=[];
  for(var k in ch)if(ch[k]!=null)chs.push({offset:ch[k],value:0});
  if(chs.length)ra('POST','/api/dmx/fixture/'+_manCal.fid+'/test',{channels:chs},function(){});
}

function _manCalVerifyExisting(fixId){
  _moverCalFid=fixId;
  // Fetch saved calibration and channel info
  ra('GET','/api/calibration/mover/'+fixId,null,function(cal){
    if(!cal||!cal.calibrated){
      document.getElementById('modal-body').innerHTML='<div style="color:#f66;padding:1em">No calibration data found.</div>';
      return;
    }
    ra('GET','/api/dmx/fixture/'+fixId+'/channels',null,function(d){
      var ch={};
      if(d&&d.channels)d.channels.forEach(function(c){
        if(c.type==='pan')ch.pan=c.offset;
        if(c.type==='tilt')ch.tilt=c.offset;
        if(c.type==='dimmer')ch.dimmer=c.offset;
        if(c.type==='red')ch.red=c.offset;
        if(c.type==='green')ch.green=c.offset;
        if(c.type==='blue')ch.blue=c.offset;
      });
      _manCal={fid:fixId,samples:cal.samples||[],channels:ch,step:'verify'};
      _manCalRender();
    });
  });
}

// ── Tracking mode — live person markers ──────────────────────────────
var _trackingCams={};  // {camId: true}
var _trackPollTimer=null;

function _trackToggle(camId){
  if(_trackingCams[camId]){
    _trackStop(camId);
  }else{
    _trackStart(camId);
  }
}

function _setupTrackToggle(camId){
  var btn=document.getElementById('setup-trk-'+camId);
  if(btn){btn.disabled=true;btn.textContent='...';}
  _trackToggle(camId);
}

function _trackBtnSync(camId){
  var active=!!_trackingCams[camId];
  // Update Setup tab button if present
  var sb=document.getElementById('setup-trk-'+camId);
  if(sb){sb.disabled=false;sb.textContent=active?'Stop Track':'Track';sb.style.background=active?'#9f1239':'#be185d';}
  // Update modal button if present
  var mb=document.getElementById('trk-btn-'+camId);
  if(mb){mb.textContent=active?'Stop Track':'Track';mb.style.background=active?'#9f1239':'#be185d';}
}

function _trackStart(camId){
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===camId)f=fx;});
  var classes=(f&&f.trackClasses)?f.trackClasses:["person"];
  var fps=(f&&f.trackFps)?f.trackFps:2;
  var thr=(f&&f.trackThreshold)?f.trackThreshold:0.4;
  var ttl=(f&&f.trackTtl)?f.trackTtl:5;
  var reid=(f&&f.trackReidMm)?f.trackReidMm:500;
  document.getElementById('hs').textContent='Starting tracking...';
  ra('POST','/api/cameras/'+camId+'/track/start',{fps:fps,threshold:thr,ttl:ttl,classes:classes,reidMm:reid},function(r){
    if(r&&r.ok){
      _trackingCams[camId]=true;
      var lbl=classes.length===1?classes[0]:classes.length+' classes';
      document.getElementById('hs').textContent='Tracking active \u2014 watching for '+lbl;
      _trackPollStart();
    }else{
      document.getElementById('hs').textContent='Track start failed: '+(r&&r.err||'unknown');
    }
    _trackBtnSync(camId);
  });
}

function _trackStop(camId){
  ra('POST','/api/cameras/'+camId+'/track/stop',{},function(r){
    delete _trackingCams[camId];
    document.getElementById('hs').textContent='Tracking stopped';
    if(!Object.keys(_trackingCams).length)_trackPollStop();
    _trackBtnSync(camId);
  });
}

function _trackPollStart(){
  if(_trackPollTimer)return;
  _trackPollTimer=setInterval(function(){
    loadObjects(function(){
      if(_s3d.inited)_s3dRenderObjects();
    });
  },1500);
}

function _trackPollStop(){
  if(_trackPollTimer){clearInterval(_trackPollTimer);_trackPollTimer=null;}
}

// ── Environment point cloud scan + 3D preview ────────────────────────
var _pointCloudData=null;
var _pointCloudVisible=false;

function _envScan(){
  document.getElementById('modal-title').textContent='Environment Scan';
  document.getElementById('modal-body').innerHTML=
    '<p style="color:#94a3b8;font-size:.85em;margin-bottom:.6em">Scanning the environment with all positioned cameras. This builds a 3D point cloud of the physical space.</p>'
    +'<div class="prog-bar" style="height:10px;margin-bottom:.5em"><div class="prog-fill" id="env-scan-fill" style="width:0%"></div></div>'
    +'<div id="env-scan-msg" style="font-size:.82em;color:#64748b">Starting...</div>';
  document.getElementById('modal').style.display='block';
  ra('POST','/api/space/scan',{},function(r){
    if(!r||!r.ok){
      document.getElementById('env-scan-msg').innerHTML='<span style="color:#f66">'+(r&&r.err||'Failed')+'</span>';
      return;
    }
    var poll=setInterval(function(){
      ra('GET','/api/space/scan/status',null,function(s){
        var fill=document.getElementById('env-scan-fill');
        var msg=document.getElementById('env-scan-msg');
        if(!fill||!msg){clearInterval(poll);return;}
        fill.style.width=s.progress+'%';
        msg.textContent=s.message||('Progress: '+s.progress+'%');
        if(!s.running){
          clearInterval(poll);
          if(s.totalPoints>0){
            fill.style.background='#059669';
            msg.innerHTML='<span style="color:#4ade80">\u2713 '+s.totalPoints+' points captured</span>'
              +' <button class="btn btn-on" onclick="closeModal();_loadPointCloud()" style="margin-left:.5em;font-size:.8em">Show in 3D</button>';
          }else{
            msg.innerHTML='<span style="color:#fbbf24">No points captured</span>';
          }
        }
      });
    },1000);
  });
}

function _loadPointCloud(cb){
  ra('GET','/api/space',null,function(r){
    if(r&&r.ok&&r.points){
      _pointCloudData=r;
      document.getElementById('hs').textContent=r.totalPoints+' point cloud loaded';
      _pointCloudVisible=true;
      if(_s3d.inited)_renderPointCloud();
      _updateCloudBtn();
    }
    if(cb)cb();
  });
}

function _togglePointCloud(){
  if(!_pointCloudData){
    _loadPointCloud(function(){
      if(!_pointCloudData)document.getElementById('hs').textContent='No point cloud — run environment scan first';
      // _loadPointCloud already set _pointCloudVisible=true and rendered
    });
    return;
  }
  _pointCloudVisible=!_pointCloudVisible;
  if(_s3d.inited)_renderPointCloud();
  _updateCloudBtn();
}

function _updateCloudBtn(){
  var btn=document.getElementById('btn-show-cloud');
  if(btn){
    btn.style.background=_pointCloudVisible?'#1e3a5f':'';
    btn.style.color=_pointCloudVisible?'#93c5fd':'';
  }
  var cb=document.getElementById('vw-cloud');
  if(cb)cb.checked=_pointCloudVisible;
}

function _renderPointCloud(){
  if(!_s3d.inited)return;
  // Remove old cloud
  _s3d.scene.children.forEach(function(c){if(c.userData&&c.userData.pointCloud)c.visible=false;});
  var old=_s3d.scene.children.filter(function(c){return c.userData&&c.userData.pointCloud;});
  old.forEach(function(c){_s3d.scene.remove(c);if(c.geometry)c.geometry.dispose();if(c.material)c.material.dispose();});

  if(!_pointCloudVisible||!_pointCloudData||!_pointCloudData.points)return;

  var pts=_pointCloudData.points;
  var positions=new Float32Array(pts.length*3);
  var colors=new Float32Array(pts.length*3);
  for(var i=0;i<pts.length;i++){
    // Stage→Three.js: X=X, Y(depth)→Z, Z(height)→Y  (matches _s3dPos)
    positions[i*3]=pts[i][0]/1000;      // stage X → 3D X
    positions[i*3+1]=pts[i][2]/1000;    // stage Z (height) → 3D Y (up)
    positions[i*3+2]=pts[i][1]/1000;    // stage Y (depth)  → 3D Z (depth)
    colors[i*3]=pts[i][3]/255;
    colors[i*3+1]=pts[i][4]/255;
    colors[i*3+2]=pts[i][5]/255;
  }
  var geo=new THREE.BufferGeometry();
  geo.setAttribute('position',new THREE.BufferAttribute(positions,3));
  geo.setAttribute('color',new THREE.BufferAttribute(colors,3));
  var mat=new THREE.PointsMaterial({size:0.06,vertexColors:true,transparent:true,opacity:0.7,sizeAttenuation:true,depthWrite:false});
  var cloud=new THREE.Points(geo,mat);
  cloud.userData.pointCloud=true;
  _s3d.scene.add(cloud);
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
