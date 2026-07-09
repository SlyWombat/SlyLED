/** emulation.js — Stage preview, 3D runtime viewport, per-pixel rendering. Extracted from app.js Phase 3. */
// ── Emulation / Preview ─────────────────────────────────────────────────────
var _emuStage=null, _emuPreview=null, _emuTimer=null, _emuT=0, _emuRunning=false, _emuAnimId=null, _emuStageLoading=false;
// #810 — live canonical aim cache for the Dashboard/Runtime 3D viz.
// `_emuPreview` is the BAKED timeline preview; Track-action moves that
// happen live-only (`_evaluate_track_actions`) aren't in the bake, so
// cone direction post-#806/#809 must read `/api/fixtures/live`'s `aim`
// field, which the server populates from the canonical aim_stage store.
// Map: fid (string) → {aim:[vx,vy,vz], r,g,b, dimmer, source}.
var _emuLive={data:{}, pollId:null};

function emuLoadStage(){
  // Re-entry guard: boot runs `_dashAttach3d` → emuLoadStage on the default
  // dashboard tab, and a fast click to Runtime would otherwise fire a second
  // chain while the first is mid-flight, producing duplicate fixture groups
  // (`emu3dBuildFixtures` runs in each callback's `_emuStageReady`).
  if(_emuStageLoading)return;
  _emuStageLoading=true;
  ra('GET','/api/layout',null,function(lay){
    ra('GET','/api/children',null,function(ch){
      ra('GET','/api/fixtures',null,function(fx){
        ra('GET','/api/objects',null,function(surfs){
        ra('GET','/api/spatial-effects',null,function(sfx){
          _emuStage={layout:lay,children:ch||[],fixtures:fx||[],objects:surfs||[],
            spatialFx:sfx||[],cw:(lay||{}).canvasW||10000,ch:(lay||{}).canvasH||5000};
          // Cache profile beamWidths for beam cone rendering. Shared loader
          // coalesces parallel callers — see _loadProfileCache (#432).
          _loadProfileCache(function(){_emuStageLoading=false;_emuStageReady();});
        });
        });
      });
    });
  });
  // Start polling for show state
  _emuStartTimer();
}

// #810 — `/api/fixtures/live` drives the 3D viz cone direction so it
// follows Track-action sweeps (not in the bake) + any other live
// mover-control writes.
// #859 — pre-fix this maintained its own 5 Hz `setInterval` AND
// scene-3d.js maintained an identical-cadence poll for the layout
// viewport, doubling the orchestrator's per-poll cost. Now both
// register with the shared poller in app.js (`_sharedFixLiveAdd`)
// — one network round trip every 200 ms, both viewports see the
// same payload.
function _emuStartLivePoll(){
  _emuLive.pollId = "shared";  // sentinel so re-entry checks still work
  _sharedFixLiveAdd('emu', function(d){
    if(!d||!Array.isArray(d.fixtures)){_emuLive.data={};return;}
    var next={};
    d.fixtures.forEach(function(f){if(f&&f.id!==undefined)next[String(f.id)]=f;});
    _emuLive.data=next;
  });
}

function _emuStopLivePoll(){
  _emuLive.pollId = null;
  _sharedFixLiveRemove('emu');
  _emuLive.data={};
}

function _emuStartTimer(){
  // Re-entrable: every live-tab switch clears `_emuTimer` via
  // `_clearTabTimers()`, so the poll needs to be rearmed whenever we land
  // on Dashboard or Runtime again. Without this, `_emuT` / `_emuPreview`
  // stay frozen after the first tab swap and the 3D cones never animate.
  if(_emuTimer)clearInterval(_emuTimer);
  // #810 — live aim poll runs whenever the emu lifecycle is active so
  // the cone follows live writes whether or not a timeline is running.
  _emuStartLivePoll();
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
  // #770 — restore persisted height + re-attach ResizeObserver to this host
  // before sizing the canvas, so the saved height takes effect on first paint.
  if(typeof _s3dRestoreHostHeight==='function'){
    _s3dRestoreHostHeight(cid);
    _s3dAttachResizeObserver(cid);
  }
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
  // #846 — hide the layout-tab fixture nodes while the runtime tab is
  // active. Pre-fix BOTH `_s3d.nodes` (layout, updated at 5 Hz by
  // `s3dPollFixturesLive`) AND `_emu3d.nodes` (runtime, updated at
  // 60 Hz via `emu3dAnimate` → `emu3dUpdateColors`) lived in the
  // same THREE scene at the same fixture positions, with independent
  // pollers driving their beam cones. The 5/60 Hz update rate
  // mismatch — and the layout cone defaulting to home-pose at init —
  // produced the visible "blink between bake-pose and home-pose"
  // operators reported. Restored on detach.
  if(_s3d.nodes&&_s3d.nodes.length){
    _s3d.nodes.forEach(function(n){if(n)n.visible=false;});
  }
  // Remove layout click/dblclick listeners from canvas
  _s3d.renderer.domElement.removeEventListener('click',s3dClick);
  _s3d.renderer.domElement.removeEventListener('dblclick',s3dDblClick);
}

function _emu3dDetach(){
  // Move renderer canvas back to layout container
  var el=document.getElementById('stage3d');if(!el||!_s3d.renderer)return;
  // #770 — re-attach ResizeObserver to the Layout host since we're moving
  // the canvas back there. Restore its persisted height too.
  if(typeof _s3dRestoreHostHeight==='function'){
    _s3dRestoreHostHeight('stage3d');
    _s3dAttachResizeObserver('stage3d');
  }
  _emu3d.activeTab=false;
  _emu3d.controls.enabled=false;
  // Stop runtime render loop
  if(_emu3d.animId){cancelAnimationFrame(_emu3d.animId);_emu3d.animId=null;}
  // #846 — restore layout-tab fixture node visibility for the layout
  // view; runtime nodes are about to be cleared.
  if(_s3d.nodes&&_s3d.nodes.length){
    _s3d.nodes.forEach(function(n){if(n)n.visible=true;});
  }
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
  // Guard — don't splat emulator fixture sprites into the scene when
  // we're no longer on an emulator-live tab. `_dashAttach3d` etc. kick
  // off `emuLoadStage()` which returns asynchronously; if the operator
  // has already navigated to Layout/Setup by then, the build would
  // otherwise leak emu sprites onto the Layout's live fixture labels,
  // producing apparent duplicates like two "Music" labels at the same
  // stage position.
  if(!_emu3d.activeTab)return;
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
    // #765 — tag the runtime stage box so the View-menu Stage Box toggle on
    // Dashboard / Runtime can flip its visibility. Honour the persisted pref
    // so a tab-roundtrip doesn't re-show a hidden box.
    boxLine.userData.stageBox=true;
    if(typeof _layShowStageBox!=='undefined')boxLine.visible=_layShowStageBox;
    _s3d.scene.add(boxLine);
    _emu3d.stageBox=boxLine;

    // Build fixture meshes
    var placed=layoutFixtures.filter(function(f){return f.positioned;});
    placed.forEach(function(c){
      var pos=_s3dPos(c);
      // #899 — node colour, shape, and type-specific meshes come from the
      // fixture-type registry (fixture-types.js).
      var ft=fixtureTypeKey(c);
      var ftDesc=fixtureTypeDesc(c);
      var col=ftDesc.runtimeNodeColor(c);

      var grp=new THREE.Group();
      grp.position.copy(pos);
      grp.userData.emuNode=true;
      grp.userData.fixtureId=c.id;
      grp.userData.fixtureType=ft;
      grp.userData.childId=c.childId;
      // #600 — pan now lives at rotation[2] (was rotation[1]). Route
      // through rotationFromLayout so the site reads axis-semantic.
      grp.userData.basePan=rotationFromLayout(c.rotation).pan;
      grp.userData.mountedInverted=!!c.mountedInverted;

      // Node mesh — sphere for known types; unknown types (radar before
      // #911) render as a neutral box (#899 fallback).
      var geo=ftDesc.nodeShape==='box'
        ?new THREE.BoxGeometry(0.26,0.26,0.26)
        :new THREE.SphereGeometry(0.15,16,12);
      var mat=new THREE.MeshBasicMaterial({color:col,transparent:true,opacity:0.9});
      var sphere=new THREE.Mesh(geo,mat);
      sphere.userData.nodeSphere=true;
      grp.add(sphere);

      // Glow ring
      var ringGeo=new THREE.RingGeometry(0.18,0.22,24);
      var ringMat=new THREE.MeshBasicMaterial({color:col,side:THREE.DoubleSide,opacity:0.3,transparent:true});
      grp.add(new THREE.Mesh(ringGeo,ringMat));

      // Type-specific meshes (#899): pan/tilt metadata + beam cone + rest
      // arrow for DMX movers; FOV cone + rest arrow for cameras; string
      // lines + LED dots for LED fixtures. Bodies moved verbatim into
      // fixture-types.js (_ftAimConeRuntime / _ftRestArrowRuntime /
      // _ftLedStringsRuntime).
      ftDesc.buildRuntimeMesh(c,{grp:grp});

      // Label
      var label=_s3dLabel(c.name||('ID '+c.id));
      label.position.set(0,0.35,0);
      grp.add(label);

      _s3d.scene.add(grp);
      _emu3d.nodes.push(grp);
    });

    // Render stage objects (including tracked persons) (#383)
    emu3dRenderObjects();
    // #765 — refresh ArUco overlay so the Dashboard / Runtime tabs render
    // marker quads in the shared scene. _s3dRenderArucoMarkers cleans up
    // any previous markers before re-adding, so this is idempotent across
    // tab switches and project imports.
    try{if(typeof _s3dLoadArucoOverlay==='function')_s3dLoadArucoOverlay();}catch(e){}
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
  // Same guard as emu3dBuildFixtures — the periodic object poll in
  // `emuStart` calls this every second regardless of tab. Without the
  // check, switching to Layout leaves a stream of emuObj sprites
  // (people / tracked objects) flickering on top of the Layout scene.
  if(!_emu3d.activeTab)return;
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
    // Static stage objects (Pillar, Music, props) are already drawn by
    // scene-3d.js _s3dRenderObjects. Only the animated person capsule is
    // emulation-specific; skip everything else to avoid rendering twice.
    if(!isPerson)return;
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
      // #911 — radar-tracked people (source.type stamped by the fusion
      // layer, #900) get an amber body tint (the radar accent) so
      // operators can tell radar tracks from camera tracks at a glance.
      if(s.source&&s.source.type==='radar'){grp.traverse(function(o){if(o.userData.personBody)o.material.color.setHex(0xfbbf24);});}
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

    // #899 — per-type live update moved into the fixture-type registry
    // (fixture-types.js _ftDmxRuntimeUpdate / _ftLedRuntimeUpdate). Types
    // without an updater (cameras, unknown types) render statically.
    var ftDesc=fixtureTypeDesc(ft);
    if(ftDesc.updateRuntimeMesh)ftDesc.updateRuntimeMesh(grp,{pd:pd,fid:fid});
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
