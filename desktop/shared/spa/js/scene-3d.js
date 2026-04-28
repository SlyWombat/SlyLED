/** scene-3d.js — 3D viewport (Three.js), view controls, alignment, arrangement. Extracted from app.js Phase 3. */
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
      // #612 — marker drag. If the transform-controls were attached to
      // an ArUco marker group, POST the new XY to /api/aruco/markers
      // and refresh the coverage overlay.
      if(_s3dDraggingMarker!=null){
        var _m=_s3d.tctl.object;
        var _mid=_s3dDraggingMarker;
        _s3dDraggingMarker=null;
        if(_m){
          var _wp=new THREE.Vector3();_m.getWorldPosition(_wp);
          // 3D→stage: X→X, Y(up)→Z(height), Z(depth)→Y(depth). Z(height)
          // stays whatever the operator surveyed (they moved XY on floor).
          var _sx=Math.round(_wp.x*1000);
          var _sy=Math.round(_wp.z*1000);
          var _existing=(window._arucoMarkersCache||[]).find(function(x){return x.id===_mid;})||{id:_mid,size:150};
          var _post={id:_mid,x:_sx,y:_sy,z:_existing.z||0,size:_existing.size||150,label:_existing.label||''};
          ra('POST','/api/aruco/markers',_post,function(){
            // Refresh overlay + coverage so the recommendation pin moves.
            _s3dLoadArucoOverlay();
          });
        }
        _s3d.tctl.detach();
        return;
      }
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
              // #600 — preserve existing roll via the axis-semantic helper
              // (roll used to live at rotation[2]; after the swap it's at
              // rotation[1]. This read goes through the helper so the
              // call site doesn't have to care.)
              var cur=rotationFromLayout(f.rotation);
              var newTilt=Math.round(-Math.atan2(dy,hd)*180/Math.PI);
              var newPan=Math.round(Math.atan2(dx,dz)*180/Math.PI);
              f.rotation=rotationToLayout(newTilt,newPan,cur.roll);
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

  // Ground grid — visibility honours persisted _layShowGrid (#638)
  var grid=new THREE.GridHelper(20,20,0x1a2744,0x111828);
  grid.userData.isGrid=true;
  grid.visible=_layShowGrid;
  _s3d.scene.add(grid);

  // Axis helper at origin (0,0,0) — stage corner
  var axes=new THREE.AxesHelper(0.5);
  axes.userData.isGrid=true;
  axes.visible=_layShowGrid;
  _s3d.scene.add(axes);
  var originLbl=_s3dLabel('Origin (0,0,0)');
  originLbl.position.set(0,-0.08,0);
  originLbl.userData.isLabel=true;
  originLbl.visible=_layShowLabels;
  _s3d.scene.add(originLbl);
  // Axis labels match stage coords: X=width, Y=depth(3D Z), Z=height(3D Y)
  var xLbl=_s3dLabel('X');xLbl.position.set(0.6,0.05,0);xLbl.userData.isLabel=true;xLbl.visible=_layShowLabels;_s3d.scene.add(xLbl);
  var zLbl=_s3dLabel('Z');zLbl.position.set(0,0.6,0);zLbl.userData.isLabel=true;zLbl.visible=_layShowLabels;_s3d.scene.add(zLbl);   // 3D Y-up = stage Z(height)
  var yLbl=_s3dLabel('Y');yLbl.position.set(0,0.05,0.6);yLbl.userData.isLabel=true;yLbl.visible=_layShowLabels;_s3d.scene.add(yLbl); // 3D Z = stage Y(depth)

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
  // Apply saved view preset (default '3d'); #638 persists across reloads.
  setView(_layView||'3d');
  // #638 — restore Point Cloud visibility if the saved pref says it was on.
  // _togglePointCloud handles the case where no scan data exists (silent
  // no-op) and re-syncs the checkbox.
  // #701 — _togglePointCloud reads cb.checked, so seed it from the pref
  // before calling. Otherwise the auto-restore was a no-op.
  if(_viewPrefs&&_viewPrefs.cloud){
    var _cbCloud=document.getElementById('vw-cloud');
    if(_cbCloud)_cbCloud.checked=true;
    try{_togglePointCloud();}catch(e){}
  }
  // Start polling remote-orientation state for debug viz (#484 phase 3)
  s3dPollRemotes();
  // Live fixture aim — keeps the beam cones honest when a mover moves.
  s3dPollFixturesLive();
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
  try{localStorage.setItem('slyled-layout-view',view);}catch(e){}
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
      // Stage objects (Pillar/Music/custom props) use real-world dimensions — do not scale
      if(grp.userData.stageObj)return;
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
        // #715 — single shared IK via _aimUnitVector. Pre-#715 this
        // path used a third inline convention that ignored rx
        // entirely and disagreed with both `_rotToAim` and the
        // live-API IK. Stage→Three.js basis swap below.
        var v=_aimUnitVector(rot, panNorm, tiltNorm, panRange, tiltRange,
                              !!fx.mountedInverted, 0.5);
        var aimDir=new THREE.Vector3(v[0], v[2], v[1]);
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
    boxLine.visible=_layShowStageBox; // #638 — persist across tab-switch rebuilds
    _s3d.scene.add(boxLine);

    // Stage dimension labels along edges
    _s3d.scene.children.forEach(function(c){if(c.userData&&c.userData.stageDimLabel){
      if(c.material&&c.material.map)c.material.map.dispose();if(c.material)c.material.dispose();_s3d.scene.remove(c);
    }});
    var sw=_s3d.stageW,sh=_s3d.stageH,sd=_s3d.stageD;
    var wLbl=_s3dLabel(Math.round(sw*1000)+'mm');wLbl.position.set(sw/2,-0.15,0);wLbl.userData.stageDimLabel=true;wLbl.scale.set(0.6,0.15,1);wLbl.visible=_layShowLabels;_s3d.scene.add(wLbl);
    var dLbl=_s3dLabel(Math.round(sd*1000)+'mm');dLbl.position.set(0,-0.15,sd/2);dLbl.userData.stageDimLabel=true;dLbl.scale.set(0.6,0.15,1);dLbl.visible=_layShowLabels;_s3d.scene.add(dLbl);
    var hLbl=_s3dLabel(Math.round(sh*1000)+'mm');hLbl.position.set(-0.15,sh/2,0);hLbl.userData.stageDimLabel=true;hLbl.scale.set(0.6,0.15,1);hLbl.visible=_layShowLabels;_s3d.scene.add(hLbl);

    // Camera positioning is handled by setView() — don't reset on every reload

    // Load objects THEN render them (async — must wait for data)
    loadObjects(function(){_s3dRenderObjects();});
    // #612 — refresh ArUco marker overlay + coverage recommendation on
    // every scene rebuild. Same cadence as _s3dRenderObjects.
    try{_s3dLoadArucoOverlay();}catch(e){}

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
          // Camera FOV cones and DMX beam cones share rendering but not visibility
          // — Camera Cones and Light Cones are separate toggles in the View menu.
          var isCam=(_fix3d.fixtureType==='camera');
          if(isCam){cone.userData.cameraCone=true;cone.visible=_layShowCamCones;}
          else{cone.userData.beamCone=true;cone.visible=_layShowCones;}
          grp.add(cone);
          // Aim point sphere (red). Draggable for DMX movers only — camera
          // aim is fixed by placement. Tag drives the correct View toggle.
          var aimGeo=new THREE.SphereGeometry(0.08,12,12);
          var aimMat=new THREE.MeshBasicMaterial({color:0xff4444,opacity:0.8,transparent:true});
          var aimSphere=new THREE.Mesh(aimGeo,aimMat);
          aimSphere.position.copy(aimLocal);
          if(isCam){aimSphere.userData.cameraCone=true;aimSphere.visible=_layShowCamCones;}
          else{
            aimSphere.userData.isAimPoint=true;
            aimSphere.userData.fixtureId=_fix3d.id;
            aimSphere.visible=_layShowCones;
          }
          grp.add(aimSphere);
          // Glow halo ring — visibility follows the cone
          var glowGeo=new THREE.RingGeometry(0.1,0.15,24);
          var glowMat=new THREE.MeshBasicMaterial({color:0xff6666,opacity:0.25,transparent:true,side:THREE.DoubleSide,depthWrite:false});
          var glow=new THREE.Mesh(glowGeo,glowMat);
          glow.position.copy(aimLocal);
          glow.lookAt(_s3d.camera.position);
          if(isCam){glow.userData.cameraCone=true;glow.visible=_layShowCamCones;}
          else{glow.userData.beamCone=true;glow.visible=_layShowCones;}
          grp.add(glow);

        }
      }

      // Rest vector — dashed arrow showing home direction (DMX movers + cameras).
      // Tagged `restArrow` (not beamCone) so the Orientation Vectors toggle
      // controls it independently of Light Cones / Camera Cones (#529).
      if(_fix3d&&(_fix3d.fixtureType==='dmx'||_fix3d.fixtureType==='camera')){
        var hasPanTilt=_fix3d.fixtureType==='camera'||
          (window._profileCache&&_fix3d.dmxProfileId&&window._profileCache[_fix3d.dmxProfileId]&&window._profileCache[_fix3d.dmxProfileId].panRange>0);
        if(hasPanTilt||_fix3d.fixtureType==='camera'){
          // #603 — the rest-direction arrow previously read only rot3d[1]
          // (yaw) and hardcoded Y=0, so a camera with rx=30° pitch-down
          // still drew its green "home direction" arrow horizontal —
          // looked like the camera was aimed at the horizon instead of
          // the stage floor. Now mirrors the live-aim math at line ~287:
          // Three.js is Y-up, positive stage pitch = tilt down = negative
          // Y in Three.js. When #600 lands (ry↔rz swap for Z-up naming)
          // the index into rot3d will need to update here too.
          var rot3d=_fix3d.rotation||[0,0,0];
          var rxRad=rot3d[0]*Math.PI/180;  // pitch (+ = down)
          var ryRad=rot3d[1]*Math.PI/180;  // yaw
          var cp=Math.cos(rxRad), sp=Math.sin(rxRad);
          var cy=Math.cos(ryRad), sy=Math.sin(ryRad);
          var homeDir=new THREE.Vector3(sy*cp,-sp,cy*cp).normalize();
          var vecLen=0.4;
          var homeEnd=homeDir.clone().multiplyScalar(vecLen);
          var restColor=_fix3d.calibrated?0x22c55e:(_fix3d.fixtureType==='camera'?0x22d3ee:0xf59e0b);
          var restGeo=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0),homeEnd]);
          var restMat=new THREE.LineDashedMaterial({color:restColor,dashSize:0.04,gapSize:0.02,opacity:0.7,transparent:true});
          var restLine=new THREE.Line(restGeo,restMat);
          restLine.computeLineDistances();
          restLine.userData.restArrow=true;restLine.visible=_layShowOrient;
          grp.add(restLine);
          var arrowGeo=new THREE.ConeGeometry(0.02,0.06,8);
          var arrowMat=new THREE.MeshBasicMaterial({color:restColor,opacity:0.8,transparent:true});
          var arrow=new THREE.Mesh(arrowGeo,arrowMat);
          arrow.position.copy(homeEnd);
          arrow.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),homeDir));
          arrow.userData.restArrow=true;arrow.visible=_layShowOrient;
          grp.add(arrow);
          var restLbl=_s3dLabel(_fix3d.fixtureType==='camera'?(_fix3d.name||'cam'+(_fix3d.cameraIdx||0)):'0,0');
          restLbl.position.copy(homeEnd.clone().add(homeDir.clone().multiplyScalar(0.05)));
          restLbl.userData.restArrow=true;restLbl.visible=_layShowOrient;
          grp.add(restLbl);
        }
      }

      // LED string lines + dots (positions relative to group origin = 0,0,0).
      // Tagged `ledString` so the LED Strings toggle can hide them without a
      // full layout redraw (#529).
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
          var strLine=new THREE.Line(lineGeo,lineMat);
          strLine.userData.ledString=true;strLine.visible=_layShowStrings;
          grp.add(strLine);

          // LED dots along string
          var dotCount=Math.min(s.leds,50);
          for(var di=0;di<dotCount;di++){
            var t=(di+0.5)/dotCount;
            var dp=new THREE.Vector3().lerpVectors(pts[0],endLocal,t);
            var dotGeo=new THREE.SphereGeometry(0.03,4,4);
            var dotMat=new THREE.MeshBasicMaterial({color:strCol});
            var dot=new THREE.Mesh(dotGeo,dotMat);
            dot.position.copy(dp);
            dot.userData.ledString=true;dot.visible=_layShowStrings;
            grp.add(dot);
          }
        }
      }

      // Text label (sprite, above group) — tagged so Labels toggle hides it.
      var label=_s3dLabel(c.name||(c.hostname||'ID '+c.id));
      label.position.set(0,0.35,0);
      label.userData.isLabel=true;label.visible=_layShowLabels;
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
    grp.visible=_layShowStageObjs; // #638 — persist across tab-switch rebuilds
    // Stage→Three.js: X→X, Y(depth)→Z, Z(height)→Y (#369)
    grp.position.set((t.pos[0]||0)/1000,(t.pos[2]||0)/1000,(t.pos[1]||0)/1000);

    var sd=(t.scale[2]||1)/1000;
    var useBox=((t.scale[2]||1)>100); // >10cm depth → box
    // Scale: stage [W, H, D] → Three.js [W(x), H(y), D(z)]
    // But stage H is visual height (Z axis in 3D = Y), stage D is depth (Y axis in 3D = Z)
    var geo=useBox?new THREE.BoxGeometry(sw,sh,sd):new THREE.PlaneGeometry(sw,sh);
    var mat=new THREE.MeshBasicMaterial({color:col,side:THREE.DoubleSide,opacity:(s.opacity||30)/100,transparent:true});
    var mesh=new THREE.Mesh(geo,mat);
    // transform.pos is the object CENTER (data format); mesh stays at group origin
    grp.add(mesh);

    // Border edges
    var edgeMat=new THREE.LineBasicMaterial({color:col,opacity:0.8,transparent:true});
    var edge=new THREE.LineSegments(new THREE.EdgesGeometry(geo),edgeMat);
    grp.add(edge);

    // Label — just above the top of the object
    var lbl=_s3dLabel(s.name||'Object');
    lbl.position.set(0,sh/2+0.15,0);
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

// Q11 / #612 — ArUco marker + coverage-recommendation overlay on the 3D
// viewport. Each surveyed marker renders as a tiny yellow disc with a
// label; the coverage endpoint's recommendation pin draws as a magenta
// cone where an additional marker would most improve camera coverage.
function _s3dRenderArucoMarkers(){
  if(!_s3d.inited)return;
  // Remove prior marker meshes.
  var toRemove=[];
  _s3d.scene.children.forEach(function(c){if(c.userData.arucoMarker||c.userData.arucoRecommend)toRemove.push(c);});
  toRemove.forEach(function(c){c.traverse(function(obj){
    if(obj.geometry)obj.geometry.dispose();
    if(obj.material){if(obj.material.map)obj.material.map.dispose();obj.material.dispose();}
  });_s3d.scene.remove(c);});
  var markers=window._arucoMarkersCache||[];
  markers.forEach(function(m){
    var grp=new THREE.Group();
    grp.userData.arucoMarker=true;
    grp.userData.markerId=m.id;
    grp.visible=_layShowAruco; // #638 — honour ArUco toggle on rebuild
    // Small flat square on the floor; elevated slightly so it's not
    // z-fighting with the stage grid.
    var sz=(m.size||150)/1000;
    var geo=new THREE.BoxGeometry(sz,0.01,sz);
    var mat=new THREE.MeshBasicMaterial({color:0xfde047,opacity:0.7,transparent:true});
    var mesh=new THREE.Mesh(geo,mat);
    grp.position.set((m.x||0)/1000,((m.z||0)+5)/1000,(m.y||0)/1000);
    grp.add(mesh);
    var lbl=_s3dLabel('AR'+m.id+(m.label?' '+m.label:''));
    lbl.position.set(0,0.15,0);
    grp.add(lbl);
    _s3d.scene.add(grp);
  });
  // Recommendation pin (if coverage endpoint has run).
  var rec=window._arucoCoverageRec||null;
  if(rec&&rec.suggestedPlacement){
    var grp=new THREE.Group();
    grp.userData.arucoRecommend=true;
    grp.visible=_layShowAruco; // #638 — same toggle as ArUco markers
    var p=rec.suggestedPlacement;
    grp.position.set((p.x||0)/1000,(p.z||0)/1000,(p.y||0)/1000);
    // Magenta cone pointing up.
    var coneGeo=new THREE.ConeGeometry(0.1,0.3,12);
    var coneMat=new THREE.MeshBasicMaterial({color:0xf0abfc,opacity:0.85,transparent:true});
    var cone=new THREE.Mesh(coneGeo,coneMat);
    cone.position.set(0,0.15,0);
    grp.add(cone);
    var lbl=_s3dLabel('Suggest: add marker here');
    lbl.position.set(0,0.45,0);
    grp.add(lbl);
    _s3d.scene.add(grp);
  }
}

// Fetch markers + coverage recommendation, cache, and re-render. Called
// from panel refresh hooks (marker edit, cal run, etc.).
function _s3dLoadArucoOverlay(){
  ra('GET','/api/aruco/markers',null,function(r){
    if(r&&r.markers){window._arucoMarkersCache=r.markers;}
    ra('GET','/api/aruco/markers/coverage',null,function(r2){
      window._arucoCoverageRec=(r2&&r2.ok&&r2.recommendation)?r2.recommendation:null;
      _s3dRenderArucoMarkers();
    });
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
  // Raycast into all group children, return the parent group (fixture or object).
  //
  // #528 — beam cones, rest arrows, and aim-point halos are child meshes of a
  // fixture group but they extend up to 3 m AWAY from the fixture. A dbl-click
  // on fixture B could hit fixture A's cone first, then walk up to fixture A's
  // group — always opening whichever fixture's cone happened to be in front.
  // Exclude those presentation meshes from the hit-test list so only the node
  // sphere (and its invisible hit helper) counts as "that fixture was clicked".
  // Aim-point dragging is handled separately in s3dClick via userData.isAimPoint
  // so excluding it here doesn't break drag.
  var rect=_s3d.renderer.domElement.getBoundingClientRect();
  _s3d.mouse.x=((e.clientX-rect.left)/rect.width)*2-1;
  _s3d.mouse.y=-((e.clientY-rect.top)/rect.height)*2+1;
  _s3d.raycaster.setFromCamera(_s3d.mouse,_s3d.camera);
  var allMeshes=[];
  _s3d.nodes.forEach(function(grp){
    grp.traverse(function(obj){
      if(!obj.isMesh)return;
      // Exclude presentation meshes that project far from the fixture so a
      // click on one fixture's cone/arrow never resolves to its group (#528).
      if(obj.userData.beamCone)return;
      if(obj.userData.cameraCone)return;
      if(obj.userData.restArrow)return;
      if(obj.userData.isAimPoint)return;
      allMeshes.push(obj);
    });
  });
  var hits=_s3d.raycaster.intersectObjects(allMeshes);
  if(hits.length>0){
    var obj=hits[0].object;
    // Walk up until we hit a group marked with childId or stageObj. Use explicit
    // !== undefined so fixture id 0 (valid) isn't treated as falsy.
    while(obj.parent&&obj.userData.childId===undefined&&!obj.userData.stageObj)obj=obj.parent;
    if(obj.userData.childId!==undefined||obj.userData.stageObj)return obj;
  }
  return null;
}

var _s3dDraggingAim=null;
// #612 — marker drag state. Set to the marker id when the transform
// controls are attached to an ArUco marker group; drag-end handler
// POSTs the new XY and clears this back to null.
var _s3dDraggingMarker=null;
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
  // #612 — ArUco marker click. Ray-cast against marker meshes (they're
  // not in _s3d.nodes; we iterate _s3d.scene children tagged arucoMarker).
  var arucoMeshes=[];
  _s3d.scene.children.forEach(function(c){
    if(c.userData&&c.userData.arucoMarker){
      c.traverse(function(o){if(o.isMesh){o._arucoMarkerId=c.userData.markerId;o._arucoGroup=c;arucoMeshes.push(o);}});
    }
  });
  var arucoHits=_s3d.raycaster.intersectObjects(arucoMeshes);
  if(arucoHits.length>0&&arucoHits[0].object._arucoGroup){
    var mg=arucoHits[0].object._arucoGroup;
    _s3d.tctl.setMode('translate');
    // Constrain to the floor plane — hide the 3D-Y (up) handle so the
    // operator can only drag X and depth. Markers on an elevated pillar
    // keep their surveyed Z; no operator tries to drag a Pillar-Post
    // marker by a few cm up or down on the 3D viewport anyway.
    _s3d.tctl.showY = false;
    _s3d.tctl.attach(mg);
    _s3dDraggingMarker=mg.userData.markerId;
    return;
  } else {
    // Re-enable Y axis for subsequent non-marker attachments.
    _s3d.tctl.showY = true;
  }
  _s3dDraggingAim=null;
  _s3dDraggingMarker=null;
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


// ── selectOnCanvas (bridges sidebar ↔ 3D scene) ────────────────────────────
function selectOnCanvas(type,id){
  if(!_s3d.inited){s3dInit();setTimeout(function(){selectOnCanvas(type,id);},500);return;}
  _s3d.nodes.forEach(function(grp){
    if(type==='fixture'&&grp.userData.childId===id){_s3d.selected=grp;_s3d.tctl.attach(grp);_updateSidePanel(id);}
    if(type==='object'&&grp.userData.stageObjId===id){_s3d.selected=grp;_s3d.tctl.attach(grp);}
  });
}

// ── Alignment / Scene menus ────────────────────────────────────────────────
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

// ── View preferences + toggles ─────────────────────────────────────────────
// #255 introduced the Layout view dropdown.
// #638 added aruco + stageObjects toggles and fixed apply-on-build bugs.
// #639 extended saved prefs to Dashboard + Runtime viewports — three
// independent contexts, each with its own localStorage key and defaults
// tuned to that tab's purpose (authoring vs monitoring vs show).
var _viewDefaultsByCtx={
  layout:  {strings:true, lightCones:false,camCones:false,orient:true, cloud:false,grid:true, labels:true, stageBox:true, aruco:true, stageObjects:true},
  dash:    {strings:true, lightCones:true, camCones:false,orient:false,cloud:false,grid:false,labels:true, stageBox:false,aruco:false,stageObjects:true},
  runtime: {strings:true, lightCones:true, camCones:false,orient:false,cloud:false,grid:false,labels:false,stageBox:false,aruco:false,stageObjects:true}
};
var _viewCtxKey={layout:'slyled-view-prefs',dash:'slyled-view-prefs-dash',runtime:'slyled-view-prefs-runtime'};
function _loadCtxPrefs(ctx){
  try{return Object.assign({},_viewDefaultsByCtx[ctx],JSON.parse(localStorage.getItem(_viewCtxKey[ctx])));}
  catch(e){return Object.assign({},_viewDefaultsByCtx[ctx]);}
}
var _viewPrefsByCtx={layout:_loadCtxPrefs('layout'),dash:_loadCtxPrefs('dash'),runtime:_loadCtxPrefs('runtime')};
var _viewCtx='layout';
// _viewDefaults + _viewPrefs remain as references to the CURRENTLY ACTIVE
// context so existing scene-build and toggle sites work unchanged.
var _viewDefaults=_viewDefaultsByCtx.layout;
var _viewPrefs=_viewPrefsByCtx.layout;
var _layShowStrings=_viewPrefs.strings;
var _layShowCones=_viewPrefs.lightCones;
function _layConesToggle(){
  var cb=document.getElementById('vw-lightcones');
  _layShowCones=cb?cb.checked:!_layShowCones;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(!c.userData)return;
      if(c.userData.beamCone||c.userData.isAimPoint)c.visible=_layShowCones;
    });
  }
}
function _layDetailToggle(){
  var cb=document.getElementById('vw-strings');
  _layShowStrings=cb?cb.checked:!_layShowStrings;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&c.userData.ledString)c.visible=_layShowStrings;
    });
  }
}

// ── View dropdown (#255, #638, #639) ──
// _viewSave writes to the ACTIVE context's localStorage key so Dashboard,
// Runtime and Layout can have independent prefs (#639).
function _viewSave(){
  var p={strings:_layShowStrings,lightCones:_layShowCones,camCones:_layShowCamCones,orient:_layShowOrient,cloud:!!document.getElementById('vw-cloud')&&document.getElementById('vw-cloud').checked,grid:_layShowGrid,labels:_layShowLabels,stageBox:_layShowStageBox,aruco:_layShowAruco,stageObjects:_layShowStageObjs};
  _viewPrefs=p;
  _viewPrefsByCtx[_viewCtx]=p;
  try{localStorage.setItem(_viewCtxKey[_viewCtx],JSON.stringify(p));}catch(e){}
}
// #639 — activate a view context. Copies that context's saved prefs into
// the module-level _layShow* scalars, traverses the shared 3D scene once
// to re-apply all visibility flags, and syncs the dropdown checkboxes.
function _setViewCtx(ctx){
  if(ctx!=='layout'&&ctx!=='dash'&&ctx!=='runtime')return;
  _viewCtx=ctx;
  var p=_viewPrefsByCtx[ctx];
  _viewPrefs=p;
  _layShowStrings=p.strings;
  _layShowCones=p.lightCones;
  _layShowCamCones=p.camCones;
  _layShowOrient=p.orient;
  _layShowGrid=p.grid;
  _layShowLabels=p.labels;
  _layShowStageBox=p.stageBox;
  _layShowAruco=p.aruco;
  _layShowStageObjs=p.stageObjects;
  _applyAllVisibility();
  _viewSyncCheckboxes();
}
// #639 — single scene traversal that applies every ctx-controlled
// visibility flag. Called when switching view contexts (tab change) so
// the shared Three.js scene reflects the new context's saved prefs.
function _applyAllVisibility(){
  if(!_s3d||!_s3d.inited)return;
  _s3d.scene.traverse(function(c){
    if(!c.userData)return;
    if(c.userData.ledString)c.visible=_layShowStrings;
    if(c.userData.beamCone||c.userData.isAimPoint)c.visible=_layShowCones;
    if(c.userData.cameraCone)c.visible=_layShowCamCones;
    if(c.userData.orientArrow||c.userData.restArrow)c.visible=_layShowOrient;
    if(c.type==='GridHelper'||c.userData.isGrid)c.visible=_layShowGrid;
    if(c.userData.isLabel||c.userData.stageDimLabel)c.visible=_layShowLabels;
    if(c.userData.stageBox)c.visible=_layShowStageBox;
    if(c.userData.arucoMarker||c.userData.arucoRecommend)c.visible=_layShowAruco;
    if(c.userData.stageObj)c.visible=_layShowStageObjs;
  });
}
function _viewSyncCheckboxes(){
  var m={strings:_layShowStrings,lightcones:_layShowCones,camcones:_layShowCamCones,orient:_layShowOrient,grid:_layShowGrid,labels:_layShowLabels,stagebox:_layShowStageBox,aruco:_layShowAruco,stageobjs:_layShowStageObjs};
  for(var k in m){var cb=document.getElementById('vw-'+k);if(cb)cb.checked=m[k];}
  var cl=document.getElementById('vw-cloud');if(cl)cl.checked=!!_viewPrefs.cloud;
}
// #639 — the view dropdown floats above whichever tab is active. It
// started inside Layout's toolbar but is re-parented to <body> on first
// open so Dashboard/Runtime can also show it (their tab content hides
// its siblings via display:none). All openers use fixed-position
// placement against the clicked anchor button's bounding rect.
function _positionViewDropdown(anchorBtn){
  var dd=document.getElementById('view-dropdown');
  if(!dd)return;
  if(dd.parentNode!==document.body)document.body.appendChild(dd);
  dd.style.position='fixed';
  if(anchorBtn){
    var r=anchorBtn.getBoundingClientRect();
    dd.style.top=(r.bottom+4)+'px';
    dd.style.left=Math.max(8,r.right-200)+'px';
  }
}
function _toggleViewMenu(){
  var dd=document.getElementById('view-dropdown');
  if(!dd)return;
  var show=dd.style.display==='none'||dd.style.display==='';
  if(show){
    _setViewCtx('layout');
    _positionViewDropdown(document.getElementById('btn-view-menu'));
    dd.style.display='block';
    _viewSyncCheckboxes();
  }else{
    dd.style.display='none';
  }
}
function _openViewMenuAt(ev,ctx){
  if(ev&&ev.stopPropagation)ev.stopPropagation();
  _setViewCtx(ctx);
  var btn=(ev&&ev.currentTarget)||null;
  _positionViewDropdown(btn);
  var dd=document.getElementById('view-dropdown');
  if(dd)dd.style.display='block';
  _viewSyncCheckboxes();
}
document.addEventListener('click',function(e){
  var dd=document.getElementById('view-dropdown');
  if(!dd)return;
  var btn=document.getElementById('btn-view-menu');
  // #639 — also treat the overlay eye-icons on Dashboard/Runtime as
  // toggle surfaces so clicking them doesn't dismiss the dropdown.
  var isOverlay=e.target.closest&&e.target.closest('.view-overlay-btn');
  if(!dd.contains(e.target)&&(!btn||!btn.contains(e.target))&&!isOverlay){
    dd.style.display='none';
  }
});

var _layShowCamCones=_viewPrefs.camCones,_layShowOrient=_viewPrefs.orient,_layShowGrid=_viewPrefs.grid,_layShowLabels=_viewPrefs.labels,_layShowStageBox=_viewPrefs.stageBox,_layShowAruco=_viewPrefs.aruco,_layShowStageObjs=_viewPrefs.stageObjects;

function _layCamConesToggle(){
  var cb=document.getElementById('vw-camcones');
  _layShowCamCones=cb?cb.checked:!_layShowCamCones;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&c.userData.cameraCone)c.visible=_layShowCamCones;
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
    // Only toggle sprites explicitly tagged as labels — rest-vector labels
    // are Sprites too but belong to the Orientation Vectors toggle (#529).
    // #638 — stage dimension labels (stageDimLabel) also follow Labels.
    _s3d.scene.traverse(function(c){
      if(c.userData&&(c.userData.isLabel||c.userData.stageDimLabel))c.visible=_layShowLabels;
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
// #638 — ArUco marker overlay toggle
function _layArucoToggle(){
  var cb=document.getElementById('vw-aruco');
  _layShowAruco=cb?cb.checked:!_layShowAruco;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&(c.userData.arucoMarker||c.userData.arucoRecommend))c.visible=_layShowAruco;
    });
  }
}
// #638 — Stage Objects toggle (music stands, static props, patrol targets)
function _layStageObjsToggle(){
  var cb=document.getElementById('vw-stageobjs');
  _layShowStageObjs=cb?cb.checked:!_layShowStageObjs;
  _viewSave();
  if(_s3d.inited){
    _s3d.scene.traverse(function(c){
      if(c.userData&&c.userData.stageObj)c.visible=_layShowStageObjs;
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

// ── Remote-orientation debug visualisation (#484 phase 3) ─────────────────
// Renders each remote (gyro puck / phone) as an icon + aim ray in the
// viewport. Consumes GET /api/remotes/live — no coupling to mover-follow.

var _s3dRemotes={group:null,byId:{},pollId:null};

function _s3dRemoteColor(rec){
  // #476 — hard-stale latches grey until cleared; soft-stale pulses amber.
  if(rec.hardStale||rec.staleReason)return 0x6b7280;  // grey — lost/stale
  if(rec.softStale)return 0xf59e0b;                   // amber — reconnecting
  if(rec.connectionState==='armed')return 0x3b82f6;   // blue
  if(rec.connectionState==='streaming'){
    var age=rec.lastDataAge;
    if(age==null||age<2)return 0x22c55e;              // green — fresh
    if(age<5)return 0xf59e0b;                         // amber — slow stream
    return 0x6b7280;                                  // grey — dead stream
  }
  return 0x64748b;                                    // idle — slate
}

// Stage (mm, X=width Y=depth Z=height) → scene (meters, Y-up).
// 3D X = stage X / 1000, 3D Y = stage Z / 1000, 3D Z = stage Y / 1000.
function _s3dRemotePos(pos_mm){
  return new THREE.Vector3(
    (pos_mm[0]||0)/1000,
    (pos_mm[2]||0)/1000,
    (pos_mm[1]||0)/1000
  );
}
function _s3dRemoteAim(aim){
  // aim is unit vector in stage coords — remap axes for scene.
  return new THREE.Vector3(aim[0]||0, aim[2]||0, aim[1]||0).normalize();
}

function _s3dBuildRemoteGroup(rec){
  var grp=new THREE.Group();
  grp.userData.remoteId=rec.id;
  var col=_s3dRemoteColor(rec);

  // Icon: small cube for phone, cylinder for puck.
  var iconGeo=rec.kind==='phone'
    ? new THREE.BoxGeometry(0.16,0.26,0.02)
    : new THREE.CylinderGeometry(0.09,0.09,0.03,24);
  var iconMat=new THREE.MeshStandardMaterial({color:col,roughness:0.4,metalness:0.2});
  var icon=new THREE.Mesh(iconGeo,iconMat);
  grp.add(icon);

  // Aim ray: only drawn once we have a calibrated aim vector.  Uncalibrated
  // remotes render as the icon alone, so nothing misleads the operator.
  if(rec.aim){
    var dir=_s3dRemoteAim(rec.aim);
    var rayLen=3.0; // 3 m — visible termination
    var arrow=new THREE.ArrowHelper(dir,new THREE.Vector3(0,0,0),rayLen,col,0.25,0.12);
    arrow.userData.isRay=true;
    grp.add(arrow);
  }

  // Label
  var lbl=_s3dLabel(rec.name||('Remote '+rec.id));
  lbl.position.set(0,0.2,0);lbl.scale.set(0.6,0.15,1);
  grp.add(lbl);

  return grp;
}

function _s3dUpdateRemoteGroup(grp,rec){
  var col=_s3dRemoteColor(rec);
  var ray=null;
  grp.traverse(function(o){if(o.userData.isRay)ray=o;});
  if(rec.aim){
    if(!ray){
      // Calibration just completed — add a ray for this remote.
      ray=new THREE.ArrowHelper(_s3dRemoteAim(rec.aim),new THREE.Vector3(0,0,0),3.0,col,0.25,0.12);
      ray.userData.isRay=true;
      grp.add(ray);
    }else{
      ray.setDirection(_s3dRemoteAim(rec.aim));
      ray.setColor(new THREE.Color(col));
    }
  }else if(ray){
    // Calibration was cleared — drop the ray.
    grp.remove(ray);
    if(ray.line&&ray.line.geometry)ray.line.geometry.dispose();
    if(ray.cone&&ray.cone.geometry)ray.cone.geometry.dispose();
  }
  grp.traverse(function(o){
    if(!o.userData.isRay&&o.isMesh&&o.material){
      o.material.color=new THREE.Color(col);
    }
  });
}

function s3dRenderRemotes(list){
  if(!_s3d.inited||typeof THREE==='undefined')return;
  if(!_s3dRemotes.group){
    _s3dRemotes.group=new THREE.Group();
    _s3dRemotes.group.userData.remotesRoot=true;
    _s3d.scene.add(_s3dRemotes.group);
  }
  var seen={};
  (list||[]).forEach(function(rec){
    seen[rec.id]=true;
    var grp=_s3dRemotes.byId[rec.id];
    if(!grp){
      grp=_s3dBuildRemoteGroup(rec);
      _s3dRemotes.group.add(grp);
      _s3dRemotes.byId[rec.id]=grp;
    }
    grp.position.copy(_s3dRemotePos(rec.pos||[0,0,1600]));
    _s3dUpdateRemoteGroup(grp,rec);
  });
  // Remove remotes no longer in the list
  Object.keys(_s3dRemotes.byId).forEach(function(idStr){
    if(!seen[idStr]){
      var grp=_s3dRemotes.byId[idStr];
      grp.traverse(function(obj){
        if(obj.geometry)obj.geometry.dispose();
        if(obj.material){if(obj.material.map)obj.material.map.dispose();obj.material.dispose();}
      });
      _s3dRemotes.group.remove(grp);
      delete _s3dRemotes.byId[idStr];
    }
  });
}

function s3dPollRemotes(){
  if(_s3dRemotes.pollId)return;
  var fetchOne=function(){
    if(!_s3d.inited)return;
    ra('GET','/api/remotes/live',null,function(d){
      if(d&&d.remotes)s3dRenderRemotes(d.remotes);
    });
  };
  fetchOne();
  _s3dRemotes.pollId=setInterval(fetchOne,1000);
}

function s3dStopPollRemotes(){
  if(_s3dRemotes.pollId){clearInterval(_s3dRemotes.pollId);_s3dRemotes.pollId=null;}
}

// ── Live fixture aim (DMX movers) ─────────────────────────────────────────
// Polls /api/fixtures/live and updates the beam cone + aim sphere of each
// DMX mover to its current pan/tilt-driven aim vector. This is what keeps
// the 3D view honest when a gyro puck, timeline, or any other source moves
// the fixture.

var _s3dFixLive={pollId:null};

function _s3dAimStageToLocal(aim){
  // Stage (X, Y, Z) -> Three.js Y-up (X, Z_stage, Y_stage).
  return new THREE.Vector3(aim[0]||0, aim[2]||0, aim[1]||0).normalize();
}

function _s3dUpdateFixtureAim(fid, aim){
  if(!aim||!_s3d.nodes)return;
  // Find the scene group for this fixture/child.
  var grp=null;
  _s3d.nodes.forEach(function(g){if(g.userData&&g.userData.childId===fid)grp=g;});
  if(!grp)return;
  var dir=_s3dAimStageToLocal(aim);
  // Distance is whatever the existing aim sphere already sits at — keep
  // the beam length stable, just redirect.
  var beamLen=3.0;
  grp.traverse(function(o){
    if(o.userData&&o.userData.isAimPoint){
      beamLen=o.position.length()||beamLen;
    }
  });
  var aimLocal=dir.clone().multiplyScalar(beamLen);
  grp.traverse(function(o){
    if(!o.userData)return;
    if(o.userData.beamCone&&o.geometry&&o.geometry.type==='ConeGeometry'){
      // Re-centre + re-orient the cone.
      o.position.copy(aimLocal.clone().multiplyScalar(0.5));
      var q=new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,-1,0),dir);
      o.quaternion.copy(q);
    }else if(o.userData.isAimPoint){
      o.position.copy(aimLocal);
    }else if(o.userData.beamCone&&o.geometry&&o.geometry.type==='RingGeometry'){
      // Glow halo around the aim point.
      o.position.copy(aimLocal);
      if(_s3d.camera)o.lookAt(_s3d.camera.position);
    }
  });
}

function s3dRenderFixturesLive(list){
  if(!_s3d.inited||!list)return;
  list.forEach(function(f){
    if(f.aim)_s3dUpdateFixtureAim(f.id, f.aim);
  });
}

function s3dPollFixturesLive(){
  if(_s3dFixLive.pollId)return;
  var fetchOne=function(){
    if(!_s3d.inited)return;
    ra('GET','/api/fixtures/live',null,function(d){
      if(d&&d.fixtures)s3dRenderFixturesLive(d.fixtures);
    });
  };
  fetchOne();
  _s3dFixLive.pollId=setInterval(fetchOne,500);  // 2 Hz — enough for a viewport
}

function s3dStopPollFixturesLive(){
  if(_s3dFixLive.pollId){clearInterval(_s3dFixLive.pollId);_s3dFixLive.pollId=null;}
}

// ── Residual error vectors (#512) ────────────────────────────────────────
// For each calibration sample, draw a short line from the target point
// (where the operator intended the beam to hit) to the predicted point
// (where the fitted model says the beam actually lands). Colour
// gradient by error magnitude: green <50mm, amber <200mm, red ≥200mm.

var _s3dResiduals={group:null,fid:null};

function _s3dStageToLocal(xyz){
  return new THREE.Vector3((xyz[0]||0)/1000, (xyz[2]||0)/1000, (xyz[1]||0)/1000);
}

function s3dClearResiduals(){
  if(!_s3dResiduals.group)return;
  _s3dResiduals.group.traverse(function(o){
    if(o.geometry)o.geometry.dispose();
    if(o.material)o.material.dispose();
  });
  _s3d.scene.remove(_s3dResiduals.group);
  _s3dResiduals.group=null;
  _s3dResiduals.fid=null;
}

function s3dShowResidualsForFixture(fid){
  if(typeof THREE==='undefined'||!_s3d.inited)return;
  s3dClearResiduals();
  ra('GET','/api/calibration/mover/'+fid+'/residuals',null,function(r){
    if(!r||!r.ok||!r.samples||!r.samples.length)return;
    var grp=new THREE.Group();
    grp.userData.residualsFor=fid;
    r.samples.forEach(function(s){
      var a=_s3dStageToLocal(s.actual);
      var p=_s3dStageToLocal(s.predicted);
      var err=s.errorMm||0;
      var col=err<50?0x4ade80:(err<200?0xf59e0b:0xef4444);
      var geo=new THREE.BufferGeometry().setFromPoints([a,p]);
      var mat=new THREE.LineBasicMaterial({color:col,linewidth:2,
        transparent:true,opacity:0.85});
      grp.add(new THREE.Line(geo,mat));
      // Small sphere at the actual point (operator's intended target)
      var sph=new THREE.Mesh(
        new THREE.SphereGeometry(0.035,8,8),
        new THREE.MeshBasicMaterial({color:col}));
      sph.position.copy(a);
      grp.add(sph);
    });
    _s3d.scene.add(grp);
    _s3dResiduals.group=grp;
    _s3dResiduals.fid=fid;
  });
}

function s3dToggleResidualsForFixture(fid){
  if(_s3dResiduals.fid===fid){s3dClearResiduals();return false;}
  s3dShowResidualsForFixture(fid);
  return true;
}


// ── #720 PR-2 — _mcal3d: read-only 3D viewport for the calibration card ─
//
// A parallel singleton to `_s3d`: own renderer, own scene, own canvas.
// Read-only — no drag, no transform, no fixture editing. Renders the
// SMART coverage cone (apex at fixture, lateral faces extruded down to
// the floor footprint), the working area polygon, and the probe-point
// spheres. The Dashboard `_s3d` viewport is untouched.
//
// Stage → 3D coord convention matches `_s3d`:
//   stage X (width)  → 3D X
//   stage Y (depth)  → 3D Z
//   stage Z (height) → 3D Y (up)
// Units: mm → m (divide by 1000) so OrbitControls feel right.

var _mcal3d={
  inited:false,scene:null,camera:null,renderer:null,controls:null,
  mountId:null,animId:null,
  // Layer groups, recreated on each render so we can swap data.
  coneMesh:null, floorPolyMesh:null, workingPolyMesh:null,
  probesGroup:null, fixtureMarker:null,
  data:null
};

function _mcal3dMount(mountId){
  if(typeof THREE==='undefined'){
    console.warn('Three.js not loaded; SMART 3D viewport unavailable');
    return false;
  }
  var el=document.getElementById(mountId);
  if(!el)return false;

  // Already mounted on this element — just resize and redraw.
  if(_mcal3d.inited && _mcal3d.mountId===mountId && _mcal3d.renderer
      && _mcal3d.renderer.domElement.parentNode===el){
    _mcal3dResize();
    return true;
  }

  // Mounting somewhere new — tear down whatever's there.
  _mcal3dDispose();
  _mcal3d.mountId=mountId;

  var W=el.clientWidth||320;
  var H=el.clientHeight||280;

  _mcal3d.scene=new THREE.Scene();
  _mcal3d.scene.background=new THREE.Color(0x0a0e1a);

  var aspect=W/H;
  _mcal3d.camera=new THREE.PerspectiveCamera(50,aspect,0.05,200);
  _mcal3d.camera.position.set(6,5,7);
  _mcal3d.camera.lookAt(0,0,0);

  _mcal3d.renderer=new THREE.WebGLRenderer({antialias:true});
  _mcal3d.renderer.setSize(W,H);
  _mcal3d.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
  // Clear any prior child (e.g. the legacy 2D canvas).
  while(el.firstChild)el.removeChild(el.firstChild);
  el.appendChild(_mcal3d.renderer.domElement);

  if(typeof THREE.OrbitControls!=='undefined'){
    _mcal3d.controls=new THREE.OrbitControls(_mcal3d.camera,_mcal3d.renderer.domElement);
    _mcal3d.controls.enableDamping=true;
    _mcal3d.controls.dampingFactor=0.08;
    _mcal3d.controls.target.set(0,0,0);
    _mcal3d.controls.update();
  }

  // Lights — mirror _s3d defaults.
  _mcal3d.scene.add(new THREE.AmbientLight(0x334466,0.85));
  var dl=new THREE.DirectionalLight(0xffffff,0.55);
  dl.position.set(5,10,7);
  _mcal3d.scene.add(dl);

  // Floor grid + axes (CCW: x stage-width red, y stage-up green, z stage-depth blue)
  var grid=new THREE.GridHelper(20,20,0x1a2744,0x111828);
  _mcal3d.scene.add(grid);
  var axes=new THREE.AxesHelper(0.8);
  _mcal3d.scene.add(axes);

  _mcal3d.inited=true;

  // Animation loop — local to this viewport so the Dashboard's anim
  // loop is unchanged.
  function tick(){
    if(!_mcal3d.inited)return;
    _mcal3d.animId=requestAnimationFrame(tick);
    if(_mcal3d.controls)_mcal3d.controls.update();
    _mcal3d.renderer.render(_mcal3d.scene,_mcal3d.camera);
  }
  tick();

  // Resize observer to keep the canvas snug as the cal panel resizes.
  if(typeof ResizeObserver!=='undefined' && !_mcal3d._resizeObs){
    _mcal3d._resizeObs=new ResizeObserver(function(){_mcal3dResize();});
    _mcal3d._resizeObs.observe(el);
  }

  return true;
}

function _mcal3dResize(){
  if(!_mcal3d.inited || !_mcal3d.renderer)return;
  var el=document.getElementById(_mcal3d.mountId);
  if(!el)return;
  var W=el.clientWidth||320, H=el.clientHeight||280;
  _mcal3d.renderer.setSize(W,H);
  _mcal3d.camera.aspect=W/H;
  _mcal3d.camera.updateProjectionMatrix();
}

function _mcal3dDispose(){
  if(_mcal3d.animId){cancelAnimationFrame(_mcal3d.animId);_mcal3d.animId=null;}
  if(_mcal3d._resizeObs){
    try{_mcal3d._resizeObs.disconnect();}catch(e){}
    _mcal3d._resizeObs=null;
  }
  if(_mcal3d.renderer){
    try{
      if(_mcal3d.renderer.domElement && _mcal3d.renderer.domElement.parentNode){
        _mcal3d.renderer.domElement.parentNode.removeChild(_mcal3d.renderer.domElement);
      }
      _mcal3d.renderer.dispose();
    }catch(e){}
  }
  _mcal3d.renderer=null;
  _mcal3d.scene=null;
  _mcal3d.camera=null;
  _mcal3d.controls=null;
  _mcal3d.inited=false;
  _mcal3d.coneMesh=null;
  _mcal3d.floorPolyMesh=null;
  _mcal3d.workingPolyMesh=null;
  _mcal3d.probesGroup=null;
  _mcal3d.fixtureMarker=null;
}

// stage mm → 3D scene: (x,y,z) mm → (x/1000, z/1000, y/1000)
function _mcal3dPos(stage_xyz){
  return new THREE.Vector3(
    (stage_xyz[0]||0)/1000,
    (stage_xyz[2]||0)/1000,
    (stage_xyz[1]||0)/1000
  );
}

function _mcal3dClearLayers(){
  ['coneMesh','floorPolyMesh','workingPolyMesh','probesGroup','fixtureMarker'].forEach(function(k){
    var obj=_mcal3d[k];
    if(obj && obj.parent)obj.parent.remove(obj);
    if(obj && obj.geometry)try{obj.geometry.dispose();}catch(e){}
    if(obj && obj.material){
      try{(Array.isArray(obj.material)?obj.material:[obj.material]).forEach(function(m){m.dispose();});}catch(e){}
    }
    _mcal3d[k]=null;
  });
}

function _mcal3dRender(coverage, preview){
  // coverage: {cone:{apex,axis,...}, floorPolygon:[[x,y],...], floorZ}
  // preview (optional): {workingPoly, probePoints} from /smart/preview
  if(!_mcal3d.inited)return;
  _mcal3dClearLayers();
  _mcal3d.data={coverage:coverage, preview:preview||null};

  if(!coverage || !coverage.cone)return;
  var apexStage=coverage.cone.apex || [0,0,0];
  var floorZ=(coverage.floorZ!=null)?coverage.floorZ:0;

  // Fixture marker: small yellow sphere at apex.
  var fixGeo=new THREE.SphereGeometry(0.06,16,12);
  var fixMat=new THREE.MeshBasicMaterial({color:0xfbbf24});
  _mcal3d.fixtureMarker=new THREE.Mesh(fixGeo,fixMat);
  _mcal3d.fixtureMarker.position.copy(_mcal3dPos(apexStage));
  _mcal3d.scene.add(_mcal3d.fixtureMarker);

  // Coverage cone — translucent volume from apex to each floor polygon
  // edge vertex. Built as a TRIANGLE_FAN (apex + every floor vertex,
  // closing back to first).
  var floorPoly=coverage.floorPolygon || [];
  if(floorPoly.length>=3){
    var apex=_mcal3dPos(apexStage);
    var floorVerts=floorPoly.map(function(p){return _mcal3dPos([p[0],p[1],floorZ]);});

    // Lateral faces: one triangle per polygon edge.
    var sideGeo=new THREE.BufferGeometry();
    var sidePositions=[];
    for(var i=0;i<floorVerts.length;i++){
      var a=floorVerts[i];
      var b=floorVerts[(i+1)%floorVerts.length];
      sidePositions.push(apex.x,apex.y,apex.z);
      sidePositions.push(b.x,b.y,b.z);
      sidePositions.push(a.x,a.y,a.z);
    }
    sideGeo.setAttribute('position',new THREE.Float32BufferAttribute(sidePositions,3));
    sideGeo.computeVertexNormals();
    var sideMat=new THREE.MeshBasicMaterial({
      color:0x38bdf8, transparent:true, opacity:0.18,
      side:THREE.DoubleSide, depthWrite:false
    });
    _mcal3d.coneMesh=new THREE.Mesh(sideGeo,sideMat);
    _mcal3d.scene.add(_mcal3d.coneMesh);

    // Floor footprint — convex polygon as a fan from vertex 0.
    var floorGeo=new THREE.BufferGeometry();
    var floorPositions=[];
    for(var j=1;j<floorVerts.length-1;j++){
      var v0=floorVerts[0], v1=floorVerts[j], v2=floorVerts[j+1];
      floorPositions.push(v0.x,v0.y,v0.z);
      floorPositions.push(v1.x,v1.y,v1.z);
      floorPositions.push(v2.x,v2.y,v2.z);
    }
    floorGeo.setAttribute('position',new THREE.Float32BufferAttribute(floorPositions,3));
    floorGeo.computeVertexNormals();
    var floorMat=new THREE.MeshBasicMaterial({
      color:0x38bdf8, transparent:true, opacity:0.32,
      side:THREE.DoubleSide, depthWrite:false
    });
    _mcal3d.floorPolyMesh=new THREE.Mesh(floorGeo,floorMat);
    _mcal3d.scene.add(_mcal3d.floorPolyMesh);

    // Floor outline edges for clarity.
    var outlineGeo=new THREE.BufferGeometry();
    var outlinePos=[];
    for(var k=0;k<floorVerts.length;k++){
      var p1=floorVerts[k], p2=floorVerts[(k+1)%floorVerts.length];
      outlinePos.push(p1.x,p1.y,p1.z, p2.x,p2.y,p2.z);
    }
    outlineGeo.setAttribute('position',new THREE.Float32BufferAttribute(outlinePos,3));
    var outlineMat=new THREE.LineBasicMaterial({color:0x38bdf8});
    var outline=new THREE.LineSegments(outlineGeo,outlineMat);
    _mcal3d.scene.add(outline);
    // Reuse coneMesh.userData to track the outline for cleanup.
    _mcal3d.coneMesh.userData.outline=outline;
  }

  // Frame the camera on the data: target = midpoint of fixture XY +
  // floor centroid; offset = enough distance to fit the polygon.
  if(_mcal3d.controls && floorPoly.length>=3){
    var cx=floorPoly.reduce(function(a,p){return a+p[0];},0)/floorPoly.length;
    var cy=floorPoly.reduce(function(a,p){return a+p[1];},0)/floorPoly.length;
    var tgt=_mcal3dPos([(cx+apexStage[0])/2,(cy+apexStage[1])/2,
                          (apexStage[2]+floorZ)/2]);
    _mcal3d.controls.target.copy(tgt);
    // Heuristic distance: bounding sphere of polygon + apex.
    var maxR=0;
    floorPoly.forEach(function(p){
      var d=Math.hypot(p[0]-apexStage[0],p[1]-apexStage[1]);
      if(d>maxR)maxR=d;
    });
    var dist=Math.max(2.0,(maxR/1000)*1.6+1.0);
    var dir=new THREE.Vector3(1,0.6,1).normalize().multiplyScalar(dist);
    _mcal3d.camera.position.copy(tgt.clone().add(dir));
    _mcal3d.camera.lookAt(tgt);
    _mcal3d.controls.update();
  }

  // PR-3 overlays: working area + probe points.
  if(preview){
    if(preview.workingPoly && preview.workingPoly.length>=3){
      var wpVerts=preview.workingPoly.map(function(p){return _mcal3dPos([p[0],p[1],floorZ+5]);});
      var wpGeo=new THREE.BufferGeometry();
      var wpPos=[];
      for(var w=1;w<wpVerts.length-1;w++){
        wpPos.push(wpVerts[0].x,wpVerts[0].y,wpVerts[0].z,
                    wpVerts[w].x,wpVerts[w].y,wpVerts[w].z,
                    wpVerts[w+1].x,wpVerts[w+1].y,wpVerts[w+1].z);
      }
      wpGeo.setAttribute('position',new THREE.Float32BufferAttribute(wpPos,3));
      var wpMat=new THREE.MeshBasicMaterial({
        color:0x4ade80, transparent:true, opacity:0.45,
        side:THREE.DoubleSide, depthWrite:false
      });
      _mcal3d.workingPolyMesh=new THREE.Mesh(wpGeo,wpMat);
      _mcal3d.scene.add(_mcal3d.workingPolyMesh);
    }
    if(preview.probePoints && preview.probePoints.length){
      var grp=new THREE.Group();
      var probeGeo=new THREE.SphereGeometry(0.04,12,10);
      var probeMat=new THREE.MeshBasicMaterial({color:0xfbbf24});
      preview.probePoints.forEach(function(p){
        var s=new THREE.Mesh(probeGeo,probeMat);
        s.position.copy(_mcal3dPos([p[0],p[1],floorZ+10]));
        grp.add(s);
      });
      _mcal3d.probesGroup=grp;
      _mcal3d.scene.add(grp);
    }
  }
}


// ── #732 — live SMART probe overlay during cal-status polling ──────────
//
// While the SMART probe loop runs, parent_server's cal-status response
// carries `smartProbeGrid` (per-cell status: pending/probing/hit/miss),
// `smartCurrentProbe` (the live target), and the working/coverage
// polygons. Render them as a live overlay on top of the static
// coverage cone — the SPA polls cal-status at ~1 Hz so the head's
// motion mirrors visibly in the viewport.

function _mcal3dProbeOverlayClear(){
  if(!_mcal3d.inited)return;
  ['liveProbesGroup','currentBeamGroup'].forEach(function(k){
    var obj=_mcal3d[k];
    if(obj && obj.parent)obj.parent.remove(obj);
    if(obj){
      obj.traverse(function(child){
        if(child.geometry)try{child.geometry.dispose();}catch(e){}
        if(child.material){
          var mats=Array.isArray(child.material)?child.material:[child.material];
          mats.forEach(function(m){try{m.dispose();}catch(e){}});
        }
      });
    }
    _mcal3d[k]=null;
  });
}

function _mcal3dProbeOverlay(status){
  // status is the /api/calibration/mover/<fid>/status response.
  if(!_mcal3d.inited)return;
  if(!status || !status.smartProbeGrid || !status.smartProbeGrid.length){
    _mcal3dProbeOverlayClear();
    return;
  }
  _mcal3dProbeOverlayClear();
  var floorZ=(status.smartFloorZ!=null)?status.smartFloorZ:0;
  var COLOURS={
    pending:0x64748b,  // grey
    probing:0xfbbf24,  // yellow
    hit:    0x4ade80,  // green
    miss:   0xef4444,  // red
    ik_degenerate: 0x9333ea,  // purple
  };
  var grp=new THREE.Group();
  var probeGeo=new THREE.SphereGeometry(0.05,14,12);
  status.smartProbeGrid.forEach(function(p){
    var c=COLOURS[p.status] || COLOURS.pending;
    var mat=new THREE.MeshBasicMaterial({color:c});
    var s=new THREE.Mesh(probeGeo,mat);
    s.position.copy(_mcal3dPos([p.x, p.y, floorZ+15]));
    grp.add(s);
  });
  _mcal3d.liveProbesGroup=grp;
  _mcal3d.scene.add(grp);

  // Current target → bright pulsing marker + line from fixture apex.
  if(status.smartCurrentProbe){
    var cur=status.smartCurrentProbe.stageXYZ;
    var apex=_mcal3d.data && _mcal3d.data.coverage
              && _mcal3d.data.coverage.cone
              && _mcal3d.data.coverage.cone.apex;
    var bgrp=new THREE.Group();
    // Big yellow ring on the floor at the current target.
    var ringGeo=new THREE.RingGeometry(0.10, 0.18, 24);
    var ringMat=new THREE.MeshBasicMaterial({
      color:0xfde047, side:THREE.DoubleSide, transparent:true, opacity:0.85
    });
    var ring=new THREE.Mesh(ringGeo, ringMat);
    var p=_mcal3dPos([cur[0], cur[1], floorZ+25]);
    ring.position.copy(p);
    ring.rotation.x = -Math.PI/2;  // lay flat on the floor
    bgrp.add(ring);
    // Optical-path line from fixture apex to target.
    if(apex){
      var lineGeo=new THREE.BufferGeometry();
      var apexPos=_mcal3dPos(apex);
      lineGeo.setAttribute('position', new THREE.Float32BufferAttribute(
        [apexPos.x, apexPos.y, apexPos.z, p.x, p.y, p.z], 3));
      var lineMat=new THREE.LineBasicMaterial({color:0xfde047});
      bgrp.add(new THREE.Line(lineGeo, lineMat));
    }
    _mcal3d.currentBeamGroup=bgrp;
    _mcal3d.scene.add(bgrp);
  }
}
