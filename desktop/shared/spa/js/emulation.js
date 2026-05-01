/** emulation.js — Stage preview, 3D runtime viewport, per-pixel rendering. Extracted from app.js Phase 3. */
// ── Emulation / Preview ─────────────────────────────────────────────────────
var _emuStage=null, _emuPreview=null, _emuTimer=null, _emuT=0, _emuRunning=false, _emuAnimId=null, _emuStageLoading=false;

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

function _emuStartTimer(){
  // Re-entrable: every live-tab switch clears `_emuTimer` via
  // `_clearTabTimers()`, so the poll needs to be rearmed whenever we land
  // on Dashboard or Runtime again. Without this, `_emuT` / `_emuPreview`
  // stay frozen after the first tab swap and the 3D cones never animate.
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
      // #600 — pan now lives at rotation[2] (was rotation[1]). Route
      // through rotationFromLayout so the site reads axis-semantic.
      grp.userData.basePan=rotationFromLayout(c.rotation).pan;
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

      // DMX beam cone + camera FOV cone. Camera cones used to be skipped
      // here so the View-menu "Camera Cones" toggle on Dashboard had nothing
      // to find (#765). Build them with the correct userData tag so the
      // shared toggle traversal in scene-3d.js picks them up.
      if(ft==='dmx'||ft==='camera'){
        var _fRot=c.rotation||[0,0,0];
        var aim=_rotToAim(_fRot,[c.x||0,c.y||0,c.z||0],3000,c.mountedInverted);
        var aimLocal=new THREE.Vector3((aim[0]-(c.x||0))/1000,(aim[2]-(c.z||0))/1000,(aim[1]-(c.y||0))/1000);
        var beamLen=aimLocal.length();
        // Default beam length if rotation is zero (pointing forward)
        if(beamLen<0.01){aimLocal.set(0,-1,0);beamLen=3;}
        grp.userData.beamLen=beamLen;
        var bwDeg=15;
        if(ft==='camera'){
          bwDeg=c.effectiveFovDeg||c.fovDeg||60;
        }else if(c.dmxProfileId&&window._profileCache&&window._profileCache[c.dmxProfileId]){
          bwDeg=window._profileCache[c.dmxProfileId].beamWidth||15;
        }
        var bwRad=bwDeg*Math.PI/180;
        var topR=Math.tan(bwRad/2)*beamLen;
        var coneGeo=new THREE.ConeGeometry(topR,beamLen,16,1,true);
        var coneCol=ft==='camera'?0x22d3ee:0xffff88;
        var coneMat=new THREE.MeshBasicMaterial({color:coneCol,opacity:ft==='camera'?0.12:0.1,transparent:true,side:THREE.DoubleSide,depthWrite:false});
        var cone=new THREE.Mesh(coneGeo,coneMat);
        // #765 — tag matches the toggle the operator expects to control it.
        if(ft==='camera'){
          cone.userData.cameraCone=true;
          if(typeof _layShowCamCones!=='undefined')cone.visible=_layShowCamCones;
        }else{
          cone.userData.beamCone=true;
          if(typeof _layShowCones!=='undefined')cone.visible=_layShowCones;
        }
        var midPt=aimLocal.clone().multiplyScalar(0.5);
        cone.position.copy(midPt);
        var dir=aimLocal.clone().normalize();
        cone.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,-1,0),dir));
        grp.add(cone);
      }

      // #765 — Orientation Vectors: dashed home-direction arrow + label for
      // DMX movers and cameras. Tagged `restArrow` so the shared toggle in
      // scene-3d.js controls visibility on Dashboard / Runtime as well as
      // Layout. Mirrors the math in scene-3d.js:518–544 — keep in sync if
      // either side changes.
      if(ft==='dmx'||ft==='camera'){
        var hasPanTilt=ft==='camera'||
          (window._profileCache&&c.dmxProfileId&&window._profileCache[c.dmxProfileId]&&window._profileCache[c.dmxProfileId].panRange>0);
        if(hasPanTilt){
          var _ro=c.rotation||[0,0,0];
          var rxR=_ro[0]*Math.PI/180;
          var ryR=_ro[1]*Math.PI/180;
          var cp=Math.cos(rxR), sp=Math.sin(rxR);
          var cy=Math.cos(ryR), sy=Math.sin(ryR);
          var homeDir=new THREE.Vector3(sy*cp,-sp,cy*cp).normalize();
          var vecLen=0.4;
          var homeEnd=homeDir.clone().multiplyScalar(vecLen);
          var restCol=c.calibrated?0x22c55e:(ft==='camera'?0x22d3ee:0xf59e0b);
          var restGeo=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0),homeEnd]);
          var restMat=new THREE.LineDashedMaterial({color:restCol,dashSize:0.04,gapSize:0.02,opacity:0.7,transparent:true});
          var restLine=new THREE.Line(restGeo,restMat);
          restLine.computeLineDistances();
          restLine.userData.restArrow=true;
          if(typeof _layShowOrient!=='undefined')restLine.visible=_layShowOrient;
          grp.add(restLine);
          var arrowGeo=new THREE.ConeGeometry(0.02,0.06,8);
          var arrowMat=new THREE.MeshBasicMaterial({color:restCol,opacity:0.8,transparent:true});
          var arrow=new THREE.Mesh(arrowGeo,arrowMat);
          arrow.position.copy(homeEnd);
          arrow.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),homeDir));
          arrow.userData.restArrow=true;
          if(typeof _layShowOrient!=='undefined')arrow.visible=_layShowOrient;
          grp.add(arrow);
        }
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
    var bwDeg=fix.effectiveFovDeg||fix.fovDeg||60;
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
