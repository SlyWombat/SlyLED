/** fixture-types.js — per-fixture-type descriptor registry (#899).
 *
 * Single source of truth for everything the SPA renders differently per
 * fixture type. Before this file, adding a fixture type meant editing
 * hardcoded `ft==='dmx'?...:ft==='camera'?...` chains in seven-plus places
 * (app.js side panel + edit modal, scene-3d.js layout meshes, emulation.js
 * runtime meshes, dashboard.js counts, fixtures.js sidebar + editor). Now a
 * new type — e.g. the mmWave `radar` fixture (#911) — is one entry in
 * FIXTURE_TYPES.
 *
 * LOAD ORDER: this file is the FIRST app script in index.html (before
 * dashboard.js and long before app.js / scene-3d.js). Nothing here may call
 * a helper from a later script at load time — every reference to THREE,
 * rotationFromLayout, _rotToAim, _s3dLabel, _s3dDir, _s3dStringDirFromRot,
 * _strCol, _layShow* view toggles, escapeHtml, TRACK_CLASSES, _emu* state,
 * window._profileCache etc. lives inside functions that only run after all
 * 19 classic scripts have loaded.
 *
 * Descriptor shape (every field present on every entry):
 *   key                       — registry key === fixture.fixtureType
 *   label                     — human name ('LED', 'DMX', 'Camera')
 *   badge                     — {text, bg, fg} side-panel type chip
 *   accent                    — hex colour for 3D node meshes
 *   nodeShape                 — 'sphere' (known types) | 'box' (unknown fallback)
 *   editTypeSuffix            — appended after geometry in showNodeEdit
 *   caps                      — capability flags:
 *       hasBeam            — renders an aimable light beam cone
 *       hasFov             — renders a field-of-view cone
 *       tracksPeople       — feeds the person-tracking pipeline
 *       hasOrientation     — pan/tilt inputs in the layout modals (app.js)
 *       hasInvertedMount   — "mounted upside-down" checkbox (DMX movers)
 *       hasRotationEditor  — fixture-level Tilt/Roll/Pan editor (fixtures.js)
 *       countsAsCamera     — dashboard status-bar bucket (cameras vs fixtures)
 *   layoutNodeColor(c)        — node colour on the Layout tab
 *   runtimeNodeColor(c)       — node colour on Dashboard/Runtime tabs
 *   buildLayoutMesh(c, ctx)   — type-specific Layout meshes; ctx={grp, fix}
 *   buildRuntimeMesh(c, ctx)  — type-specific Runtime meshes; ctx={grp}
 *   updateRuntimeMesh(grp,ctx)— per-frame live update or null; ctx={pd, fid}
 *   panelDetailHtml(f)        — Layout side-panel detail section
 *   quickButtonsHtml(f)       — extra buttons in showNodeEdit (app.js)
 *   sidebarBadgeHtml(f)       — chip next to unplaced fixtures (fixtures.js)
 *   sidebarPlacedBadgeHtml(f) — chip(s) next to placed fixtures (fixtures.js)
 *   renderEditFields(f, id)   — editFixture fields between Geometry and Position
 *   renderEditExtras(f, id)   — editFixture blocks after Rotation
 *   collectEditFields(body,id)— saveFixture: fold form fields into the PUT
 *                               body; return an error string to abort save
 *   afterEditOpen(id)         — hook after the edit modal renders, or null
 *
 * Unknown types (a `radar` fixture opened in a pre-#911 SPA) get a generated
 * fallback descriptor: neutral slate box in 3D, an uppercase type-name chip,
 * and a side-panel notice — presentable, position-editable, no type-specific
 * controls. See _ftUnknown().
 */

// ── Shared mesh builders ──────────────────────────────────────────────────
// Bodies moved verbatim from scene-3d.js s3dLoadChildren (Layout) and
// emulation.js emu3dBuildFixtures / emu3dUpdateColors (Runtime) under #899.
// DMX beam cones and camera FOV cones share geometry code but differ in
// colour, width source, and View-menu tag — the isCam flag carries the
// former `fixtureType==='camera'` checks.

// Layout: beam/FOV cone + red aim sphere + glow halo (scene-3d.js).
function _ftAimConeLayout(c, fix, grp, isCam){
  var _fRot=fix.rotation||[0,0,0];
  var aim=_rotToAim(_fRot,[c.x||0,c.y||0,c.z||0],3000,fix.mountedInverted);
  // Stage→Three.js: X=X, Y(depth)→Z, Z(height)→Y
  var aimLocal=new THREE.Vector3((aim[0]-(c.x||0))/1000,(aim[2]-(c.z||0))/1000,(aim[1]-(c.y||0))/1000);
  var beamLen=aimLocal.length();
  // Cone shown for DMX and camera fixtures with non-zero rotation.
  // #892 — all three axes count: a mover with only pan set (rz,
  // index 2 per #600) used to get no cone.
  var showCone=(_fRot[0]!==0||_fRot[1]!==0||_fRot[2]!==0)||isCam;
  if(beamLen>0.01&&showCone){
    var bwDeg=isCam?(fix.effectiveFovDeg||fix.fovDeg||60):15;
    // Use cached profile beamWidth (sync) — _profileCache populated by emulator/layout
    if(!isCam&&fix.dmxProfileId&&window._profileCache&&window._profileCache[fix.dmxProfileId]){
      bwDeg=window._profileCache[fix.dmxProfileId].beamWidth||15;
    }
    var bwRad=bwDeg*Math.PI/180;
    var topR=Math.tan(bwRad/2)*beamLen;
    var coneGeo=new THREE.ConeGeometry(topR,beamLen,16,1,true);
    var coneColor=isCam?0x22d3ee:0xffff88;
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
      aimSphere.userData.fixtureId=fix.id;
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

// Layout: dashed rest-vector arrow + label (scene-3d.js). Tagged `restArrow`
// (not beamCone) so the Orientation Vectors toggle controls it independently
// of Light Cones / Camera Cones (#529).
function _ftRestArrowLayout(fix, grp, isCam){
  var hasPanTilt=isCam||
    (window._profileCache&&fix.dmxProfileId&&window._profileCache[fix.dmxProfileId]&&window._profileCache[fix.dmxProfileId].panRange>0);
  if(hasPanTilt||isCam){
    // #603 — the rest-direction arrow previously read only the yaw
    // slot and hardcoded Y=0, so a camera with rx=30° pitch-down
    // still drew its green "home direction" arrow horizontal —
    // looked like the camera was aimed at the horizon instead of
    // the stage floor. Mirrors the live-aim math in _aimUnitVector:
    // Three.js is Y-up, positive stage pitch = tilt down = negative
    // Y in Three.js. #892 — axis reads route through
    // rotationFromLayout (#600 schema v2: yaw/pan lives at rz).
    var _restA=rotationFromLayout(fix.rotation);
    var rxRad=_restA.tilt*Math.PI/180;  // pitch (+ = down)
    var ryRad=_restA.pan*Math.PI/180;   // yaw
    var cp=Math.cos(rxRad), sp=Math.sin(rxRad);
    var cy=Math.cos(ryRad), sy=Math.sin(ryRad);
    var homeDir=new THREE.Vector3(sy*cp,-sp,cy*cp).normalize();
    var vecLen=0.4;
    var homeEnd=homeDir.clone().multiplyScalar(vecLen);
    var restColor=fix.calibrated?0x22c55e:(isCam?0x22d3ee:0xf59e0b);
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
    var restLbl=_s3dLabel(isCam?(fix.name||'cam'+(fix.cameraIdx||0)):'0,0');
    restLbl.position.copy(homeEnd.clone().add(homeDir.clone().multiplyScalar(0.05)));
    restLbl.userData.restArrow=true;restLbl.visible=_layShowOrient;
    grp.add(restLbl);
  }
}

// Layout: LED string lines + dots (scene-3d.js). Positions relative to group
// origin. Tagged `ledString` so the LED Strings toggle can hide them without
// a full layout redraw (#529).
function _ftLedStringsLayout(c, grp){
  var _sc=c.sc||c.strings&&c.strings.length||0;
  if(c.strings&&_sc>0){
    for(var si=0;si<_sc&&si<c.strings.length;si++){
      var s=c.strings[si];if(!s||!s.leds)continue;
      var strCol=new THREE.Color(_strCol[si%_strCol.length]);
      var lenMm=s.mm||0;if(lenMm<500)lenMm=Math.max(s.leds*16,500);
      var lenM=lenMm/1000;
      // #864 — per-string position offset. Stage→Three.js: X→X,
      // Y(depth)→Z, Z(height)→Y. A string without x/y/z starts at
      // the fixture group origin.
      var startLocal=new THREE.Vector3(0,0,0);
      if(typeof s.x==='number'&&typeof s.y==='number'&&typeof s.z==='number'){
        startLocal.set((s.x-(c.x||0))/1000,(s.z-(c.z||0))/1000,(s.y-(c.y||0))/1000);
      }
      // #866 — direction comes from the per-string `rotation`
      // ([rx, ry, rz] degrees, same convention as cameras and DMX
      // fixtures, #586/#600). Default forward is stage +Y; rotation
      // re-aims that. Falls back to the legacy `sdir` token when
      // the string has no rotation. Stage→Three.js axis swap on the
      // resulting direction: stage X→three X, stage Y→three Z,
      // stage Z→three Y.
      var dxL,dyL,dzL;
      var sRot=Array.isArray(s.rotation)&&s.rotation.length===3
               ?s.rotation:null;
      if(sRot){
        var sd=_s3dStringDirFromRot(sRot);
        dxL=sd[0]; dyL=sd[2]; dzL=sd[1];
      }else{
        var dir=_s3dDir(s.sdir||0);
        dxL=dir.x; dyL=dir.y; dzL=dir.z;
      }
      var endLocal=startLocal.clone().add(new THREE.Vector3(dxL*lenM,dyL*lenM,dzL*lenM));
      var pts=[startLocal,endLocal];
      var lineGeo=new THREE.BufferGeometry().setFromPoints(pts);
      var lineMat=new THREE.LineBasicMaterial({color:strCol,linewidth:2});
      var strLine=new THREE.Line(lineGeo,lineMat);
      strLine.userData.ledString=true;strLine.visible=_layShowStrings;
      grp.add(strLine);

      // LED dots along string
      var dotCount=Math.min(s.leds,50);
      for(var di=0;di<dotCount;di++){
        var t=(di+0.5)/dotCount;
        var dp=new THREE.Vector3().lerpVectors(startLocal,endLocal,t);
        var dotGeo=new THREE.SphereGeometry(0.03,4,4);
        var dotMat=new THREE.MeshBasicMaterial({color:strCol});
        var dot=new THREE.Mesh(dotGeo,dotMat);
        dot.position.copy(dp);
        dot.userData.ledString=true;dot.visible=_layShowStrings;
        grp.add(dot);
      }
    }
  }
}

// Runtime: beam/FOV cone (emulation.js). Camera cones used to be skipped
// here so the View-menu "Camera Cones" toggle on Dashboard had nothing
// to find (#765). Build them with the correct userData tag so the
// shared toggle traversal in scene-3d.js picks them up.
function _ftAimConeRuntime(c, grp, isCam){
  var _fRot=c.rotation||[0,0,0];
  var aim=_rotToAim(_fRot,[c.x||0,c.y||0,c.z||0],3000,c.mountedInverted);
  var aimLocal=new THREE.Vector3((aim[0]-(c.x||0))/1000,(aim[2]-(c.z||0))/1000,(aim[1]-(c.y||0))/1000);
  var beamLen=aimLocal.length();
  // Default beam length if rotation is zero (pointing forward)
  if(beamLen<0.01){aimLocal.set(0,-1,0);beamLen=3;}
  grp.userData.beamLen=beamLen;
  var bwDeg=15;
  if(isCam){
    bwDeg=c.effectiveFovDeg||c.fovDeg||60;
  }else if(c.dmxProfileId&&window._profileCache&&window._profileCache[c.dmxProfileId]){
    bwDeg=window._profileCache[c.dmxProfileId].beamWidth||15;
  }
  var bwRad=bwDeg*Math.PI/180;
  var topR=Math.tan(bwRad/2)*beamLen;
  var coneGeo=new THREE.ConeGeometry(topR,beamLen,16,1,true);
  var coneCol=isCam?0x22d3ee:0xffff88;
  var coneMat=new THREE.MeshBasicMaterial({color:coneCol,opacity:isCam?0.12:0.1,transparent:true,side:THREE.DoubleSide,depthWrite:false});
  var cone=new THREE.Mesh(coneGeo,coneMat);
  // #765 — tag matches the toggle the operator expects to control it.
  if(isCam){
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

// Runtime: dashed home-direction arrow (emulation.js). #765 — tagged
// `restArrow` so the shared toggle in scene-3d.js controls visibility on
// Dashboard / Runtime as well as Layout. Mirrors _ftRestArrowLayout — keep
// in sync if either side changes.
function _ftRestArrowRuntime(c, grp, isCam){
  var hasPanTilt=isCam||
    (window._profileCache&&c.dmxProfileId&&window._profileCache[c.dmxProfileId]&&window._profileCache[c.dmxProfileId].panRange>0);
  if(hasPanTilt){
    // #892 — axis reads route through rotationFromLayout (#600
    // schema v2: yaw/pan lives at rz, not the old ry slot).
    var _roA=rotationFromLayout(c.rotation);
    var rxR=_roA.tilt*Math.PI/180;
    var ryR=_roA.pan*Math.PI/180;
    var cp=Math.cos(rxR), sp=Math.sin(rxR);
    var cy=Math.cos(ryR), sy=Math.sin(ryR);
    var homeDir=new THREE.Vector3(sy*cp,-sp,cy*cp).normalize();
    var vecLen=0.4;
    var homeEnd=homeDir.clone().multiplyScalar(vecLen);
    var restCol=c.calibrated?0x22c55e:(isCam?0x22d3ee:0xf59e0b);
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

// Runtime: LED string lines + dots stored for color updates (emulation.js).
// #864 / #866 — per-string position offset + per-string rotation. Same
// convention as scene-3d.js's Layout-tab path: start = (s.x, s.y, s.z)
// stage-mm if all three set, else fixture group origin; direction from
// s.rotation via the shared `_s3dStringDirFromRot` helper (mirror of
// camera_math build_camera_to_stage); legacy `sdir` only when no rotation.
// Stage→Three.js axis swap: stage X→three X, stage Y→three Z, stage Z→three Y.
function _ftLedStringsRuntime(c, grp){
  if(c.strings&&c.strings.length){
    for(var si=0;si<c.strings.length;si++){
      var s=c.strings[si];if(!s||!s.leds)continue;
      var lenMm=s.mm||0;if(lenMm<500)lenMm=Math.max(s.leds*16,500);
      var lenM=lenMm/1000;

      var startLocal=new THREE.Vector3(0,0,0);
      if(typeof s.x==='number'&&typeof s.y==='number'&&typeof s.z==='number'){
        startLocal.set((s.x-(c.x||0))/1000,(s.z-(c.z||0))/1000,(s.y-(c.y||0))/1000);
      }

      var dxL,dyL,dzL;
      var sRot=Array.isArray(s.rotation)&&s.rotation.length===3
               ?s.rotation:null;
      if(sRot&&typeof _s3dStringDirFromRot==='function'){
        var sd=_s3dStringDirFromRot(sRot);
        dxL=sd[0]; dyL=sd[2]; dzL=sd[1];
      }else{
        var sdir=_s3dDir(s.sdir||0);
        dxL=sdir.x; dyL=sdir.y; dzL=sdir.z;
      }
      var endLocal=startLocal.clone().add(new THREE.Vector3(dxL*lenM,dyL*lenM,dzL*lenM));

      // Line
      var lineGeo=new THREE.BufferGeometry().setFromPoints([startLocal,endLocal]);
      var lineMat=new THREE.LineBasicMaterial({color:0x555555});
      grp.add(new THREE.Line(lineGeo,lineMat));
      // LED dots — one InstancedMesh per string (B4). Each dot used to be
      // its own Mesh+SphereGeometry+material (~1 draw call per LED; cap
      // 50/string × 8 strings × N fixtures ≈ thousands of calls on a big
      // rig). Instancing folds a whole string into one draw call. The
      // material color stays white so per-instance instanceColor is the
      // final tint (shader multiplies diffuse × instanceColor); the
      // lit-dot 2× scale lives in the per-instance matrix. Base dot
      // geometry/size/positions/idle color are unchanged from the
      // per-mesh version.
      var dotCount=Math.min(s.leds,50);
      var dotGeo=new THREE.SphereGeometry(0.03,4,4);
      var dotMat=new THREE.MeshBasicMaterial({color:0xffffff});
      var dots=new THREE.InstancedMesh(dotGeo,dotMat,dotCount);
      var idleCol=new THREE.Color(0x333340);
      var im=new THREE.Matrix4();
      var dotPos=new Array(dotCount);
      for(var di=0;di<dotCount;di++){
        var t=(di+0.5)/dotCount;
        var dp=new THREE.Vector3().lerpVectors(startLocal,endLocal,t);
        dotPos[di]=dp;
        im.makeTranslation(dp.x,dp.y,dp.z);
        dots.setMatrixAt(di,im);
        dots.setColorAt(di,idleCol);   // also allocates instanceColor
      }
      dots.instanceMatrix.needsUpdate=true;
      if(dots.instanceColor)dots.instanceColor.needsUpdate=true;
      // The base geometry's bounding sphere doesn't cover the instance
      // positions in r137 — disable culling so dots never pop out when
      // the camera moves close (individual meshes were culled per-dot).
      dots.frustumCulled=false;
      dots.userData.ledDots=true;
      dots.userData.stringIdx=si;
      dots.userData.dotCount=dotCount;
      dots.userData.dotPos=dotPos;
      grp.add(dots);
    }
  }
}

// Runtime per-frame updater — DMX mover (emulation.js emu3dUpdateColors).
function _ftDmxRuntimeUpdate(grp, ctx){
  var pd=ctx.pd, fid=ctx.fid;
  // #810 — prefer the live canonical aim from /api/fixtures/live so
  // Track-action sweeps (live-only, not in the bake) animate the
  // cone. Live colour also wins when present so the cone tint
  // matches the wire while a claim/track is running. Fall back to
  // the baked preview when no live entry exists yet.
  var liveEntry=_emuLive&&_emuLive.data?_emuLive.data[String(fid)]:null;
  if(liveEntry&&typeof liveEntry!=='object')liveEntry=null;
  // #920 — only let the live entry shadow the baked preview when it is
  // actually driving output (claim held, active wire output, or a
  // canonical live aim). /api/fixtures/live returns an entry for EVERY
  // fixture; with no Art-Net/sACN engine writing it is all zeros and
  // would otherwise mask the baked-preview tint (idle cone forever, #810).
  var liveDriving=!!(liveEntry&&(liveEntry.active||liveEntry.source==='claim'||Array.isArray(liveEntry.aim)));
  var src=null;
  if(liveDriving)src=liveEntry;
  else if(pd&&typeof pd==='object')src=pd;

  // Update sphere + cone color
  var br=0x7c,bg=0x3a,bb=0xed,dimmer=0.1;
  if(src){
    if(src.r!==undefined){br=src.r;bg=src.g;bb=src.b;}
    if(src.dimmer>0)dimmer=(src.dimmer/255)*0.4;
    else if(br+bg+bb>10)dimmer=0.3;
  }
  var hexCol=(br<<16)|(bg<<8)|bb;

  // Update beam cone direction. Order: live.aim (canonical, post-#806/#809)
  // → live pan/tilt forward IK → baked-preview pan/tilt forward IK.
  var aimDir=null;
  if(liveEntry&&Array.isArray(liveEntry.aim)&&liveEntry.aim.length>=3){
    // Stage (X, Y, Z) → Three.js Y-up (X, Z_stage, Y_stage)
    var a=liveEntry.aim;
    aimDir=new THREE.Vector3(a[0]||0, a[2]||0, a[1]||0);
    if(aimDir.lengthSq()>1e-6)aimDir.normalize();
    else aimDir=null;
  }
  if(!aimDir){
    var ptSrc=(liveEntry&&liveEntry.pan!==undefined&&liveEntry.tilt!==undefined)
      ? liveEntry : (pd&&pd.pan!==undefined&&pd.tilt!==undefined ? pd : null);
    if(ptSrc){
      var panRange=grp.userData.panRange||540;
      var tiltRange=grp.userData.tiltRange||270;
      var panDeg=(ptSrc.pan-0.5)*panRange;
      var tiltDeg=(ptSrc.tilt-0.5)*tiltRange;
      if(grp.userData.mountedInverted)tiltDeg=-tiltDeg;
      var basePan=(grp.userData.basePan||0);
      var panRad=(basePan+panDeg)*Math.PI/180;
      var tiltRad=tiltDeg*Math.PI/180;
      aimDir=new THREE.Vector3(Math.sin(panRad)*Math.cos(tiltRad),
        -Math.sin(tiltRad),Math.cos(panRad)*Math.cos(tiltRad));
    }
  }
  if(aimDir){
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
      child.material.opacity=src?0.95:0.5;
    }
    if(child.userData.beamCone){
      child.material.color.setHex(hexCol||0xffff88);
      child.material.opacity=src?dimmer:0.08;
    }
  });
}

// Runtime per-frame updater — LED strings (emulation.js emu3dUpdateColors).
// B4 — dots are InstancedMesh per string; per-dot colors go through
// setColorAt/instanceColor and the lit 2× scale through setMatrixAt,
// instead of per-mesh material.color / scale. Scratch objects avoid
// per-frame allocations in this 60 Hz path.
var _ftLedScratch={col:null,mat:null};
function _ftLedRuntimeUpdate(grp, ctx){
  var pd=ctx.pd;
  if(!_ftLedScratch.col){_ftLedScratch.col=new THREE.Color();_ftLedScratch.mat=new THREE.Matrix4();}
  var sCol=_ftLedScratch.col,sMat=_ftLedScratch.mat;
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
    if(!child.userData.ledDots)return;
    var si=child.userData.stringIdx,dc=child.userData.dotCount;
    var dotPos=child.userData.dotPos;
    var pc=null;
    if(previewColors&&si<previewColors.length)pc=previewColors[si];
    var isAct=(pc&&typeof pc==='object'&&!Array.isArray(pc)&&pc.t!==undefined);
    var isArr=(pc&&Array.isArray(pc)&&(pc[0]+pc[1]+pc[2])>3);
    for(var di=0;di<dc;di++){
      var r=40,g=40,b=45;
      if(isArr){r=pc[0];g=pc[1];b=pc[2];}
      else if(isAct){
        var eMs=(pc.e||0)*1000+(Date.now()%1000);
        var px=_emuPixel(pc,di,dc,eMs);
        if(px){r=px[0];g=px[1];b=px[2];}
      }
      sCol.setRGB(r/255,g/255,b/255);
      child.setColorAt(di,sCol);
      // Scale lit dots larger — same 2×/1× rule as the per-mesh version;
      // makeScale().setPosition() reproduces position+uniform-scale.
      var lit=(r+g+b)>15;
      var sc=lit?2.0:1.0;
      var dp=dotPos[di];
      sMat.makeScale(sc,sc,sc).setPosition(dp.x,dp.y,dp.z);
      child.setMatrixAt(di,sMat);
    }
    if(child.instanceColor)child.instanceColor.needsUpdate=true;
    child.instanceMatrix.needsUpdate=true;
  });
  // Update node sphere color to average
  var nodeSphere=grp.children[0];
  if(nodeSphere&&nodeSphere.userData.nodeSphere){
    nodeSphere.material.opacity=(_emuRunning&&pd)?0.95:0.5;
  }
}

// ── Radar coverage sector (#911) ─────────────────────────────────────────
// The Rd-03D mmWave module senses a planar wedge: ~8 m range, ±60° azimuth
// (docs/design/mmwave_tracking.md §2.2). Rendered as a translucent fan of
// radius `fixture.rangeMm` lying in the sensor's plane, oriented by the
// fixture rotation.

// Stage-frame unit direction of a sensor-plane ray at azimuth `delta`
// radians off boresight (+delta toward +X when unrotated), for a fixture
// rotation read through rotationFromLayout. Applies the same
// Ry(roll) → Rx(-tilt) → Rz(-pan) matrix sequence as _s3dStringDirFromRot
// (the JS mirror of camera_math.build_camera_to_stage, #586/#600) but to
// an arbitrary in-plane ray instead of the fixed forward vector — at
// delta=0 this reproduces _rotToAim's boresight exactly, so the sector
// opens the same way a camera FOV cone would aim. Returns [sx, sy, sz]
// in stage frame (NOT three.js).
function _ftRadarStageDir(rotA, delta){
  // Sensor-local ray: forward is stage +Y, the wedge spreads toward ±X,
  // the sensing plane is z=0 before the fixture rotation is applied.
  var v=[Math.sin(delta),Math.cos(delta),0];
  var roll=rotA.roll*Math.PI/180, tilt=rotA.tilt*Math.PI/180, pan=rotA.pan*Math.PI/180;
  var cr=Math.cos(roll),sr=Math.sin(roll);
  v=[cr*v[0]+sr*v[2], v[1], -sr*v[0]+cr*v[2]];
  var ct=Math.cos(tilt),st=Math.sin(tilt);
  v=[v[0], ct*v[1]+st*v[2], -st*v[1]+ct*v[2]];
  var cp=Math.cos(pan),sp=Math.sin(pan);
  v=[cp*v[0]+sp*v[1], -sp*v[0]+cp*v[1], v[2]];
  return v;
}

// Shared Layout/Runtime builder: translucent fan fill + brighter outline,
// group-local (the fixture group carries the world position). Tagged
// `cameraCone` so the View-menu "Camera Cones" toggle governs it — the
// sector is the radar's FOV analog and follows the camera-cone
// show/hide convention on every tab (default hidden, like camera FOV
// cones; `radarSector` tag identifies it for tests/tools). Runtime passes
// a lower fillOpacity, mirroring how the camera cone renders statically
// there (no per-frame updater).
function _ftRadarSector(fix, grp, fillOpacity){
  var rangeM=((typeof fix.rangeMm==='number'&&fix.rangeMm>0)?fix.rangeMm:8000)/1000;
  var fovDeg=(typeof fix.fovDeg==='number'&&fix.fovDeg>0)?fix.fovDeg:120;
  var half=(fovDeg/2)*Math.PI/180;
  var rotA=rotationFromLayout(fix.rotation);
  var segs=24;
  // Arc points, stage→three.js swap (X→X, Y depth→Z, Z height→Y).
  var arc=[];
  for(var i=0;i<=segs;i++){
    var d=-half+(i/segs)*2*half;
    var sd=_ftRadarStageDir(rotA,d);
    arc.push(new THREE.Vector3(sd[0]*rangeM,sd[2]*rangeM,sd[1]*rangeM));
  }
  // Fan fill: origin + arc as a triangle fan.
  var pos=new Float32Array(segs*9);
  for(var ti=0;ti<segs;ti++){
    var a=arc[ti],b=arc[ti+1],o=ti*9;
    pos[o]=0;pos[o+1]=0;pos[o+2]=0;
    pos[o+3]=a.x;pos[o+4]=a.y;pos[o+5]=a.z;
    pos[o+6]=b.x;pos[o+7]=b.y;pos[o+8]=b.z;
  }
  var fanGeo=new THREE.BufferGeometry();
  fanGeo.setAttribute('position',new THREE.BufferAttribute(pos,3));
  var fanMat=new THREE.MeshBasicMaterial({color:0xf59e0b,opacity:fillOpacity,transparent:true,side:THREE.DoubleSide,depthWrite:false});
  var fan=new THREE.Mesh(fanGeo,fanMat);
  fan.userData.cameraCone=true;fan.userData.radarSector=true;
  if(typeof _layShowCamCones!=='undefined')fan.visible=_layShowCamCones;
  grp.add(fan);
  // Brighter outline: origin → arc → origin.
  var outlinePts=[new THREE.Vector3(0,0,0)].concat(arc,[new THREE.Vector3(0,0,0)]);
  var outGeo=new THREE.BufferGeometry().setFromPoints(outlinePts);
  var outMat=new THREE.LineBasicMaterial({color:0xfbbf24,opacity:0.6,transparent:true});
  var outline=new THREE.Line(outGeo,outMat);
  outline.userData.cameraCone=true;outline.userData.radarSector=true;
  if(typeof _layShowCamCones!=='undefined')outline.visible=_layShowCamCones;
  grp.add(outline);
}

// ── Registry ──────────────────────────────────────────────────────────────
var FIXTURE_TYPES={
  led:{
    key:'led',
    label:'LED',
    badge:{text:'LED',bg:'#14532d',fg:'#86efac'},
    accent:0x22cc66,
    nodeShape:'sphere',
    editTypeSuffix:'',
    caps:{hasBeam:false,hasFov:false,tracksPeople:false,hasOrientation:false,
          hasInvertedMount:false,hasRotationEditor:false,countsAsCamera:false},
    layoutNodeColor:function(c){return c.status===1?0x22cc66:0x555555;},
    runtimeNodeColor:function(c){return 0x22cc66;},
    buildLayoutMesh:function(c,ctx){_ftLedStringsLayout(c,ctx.grp);},
    buildRuntimeMesh:function(c,ctx){_ftLedStringsRuntime(c,ctx.grp);},
    updateRuntimeMesh:_ftLedRuntimeUpdate,
    panelDetailHtml:function(f){
      if(!(f.strings&&f.strings.length))return'';
      var h='';
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
      return h;
    },
    quickButtonsHtml:function(f){return'';},
    sidebarBadgeHtml:function(f){return'';},
    sidebarPlacedBadgeHtml:function(f){return'';},
    renderEditFields:function(f,id){return'';},
    // #864 / #866 — per-string position + rotation editor for multi-string
    // LED fixtures. The fixture's `strings` array always carries 8 slots
    // (PONG protocol constant) but only the slots with leds>0 are real
    // strings reported by the firmware — show inputs for those only.
    // Each row captures: stage-mm start position (x,y,z) and a rotation
    // [rx, ry, rz] in degrees using the same convention as cameras and
    // DMX fixtures (#586/#600). Default forward = stage +Y; rotation
    // re-aims that. Length is `mm` from firmware (read-only). The
    // rotation replaces the legacy NESW `sdir` token for rendering
    // purposes — sdir stays on the record for back-compat.
    renderEditExtras:function(f,id){
      var h='';
      var activeStrings=[];
      if(f.strings){
        for(var aiS=0;aiS<f.strings.length;aiS++){
          if(f.strings[aiS]&&(f.strings[aiS].leds||0)>0)activeStrings.push(aiS);
        }
      }
      if(activeStrings.length>0){
        h+='<div style="margin-top:.8em;border-top:1px solid #1e293b;padding-top:.6em">';
        h+='<div style="font-weight:bold;font-size:.85em;margin-bottom:.4em">Per-String Position &amp; Rotation <span style="color:#64748b;font-size:.75em">('+activeStrings.length+' active string'+(activeStrings.length===1?'':'s')+' reported)</span></div>';
        for(var aIdx=0;aIdx<activeStrings.length;aIdx++){
          var ss=activeStrings[aIdx];
          var sObj=f.strings[ss]||{};
          var sx=(sObj.x!=null)?sObj.x:'';
          var sy=(sObj.y!=null)?sObj.y:'';
          var sz=(sObj.z!=null)?sObj.z:'';
          var srot=Array.isArray(sObj.rotation)?sObj.rotation:[];
          // #866 — Tilt is shown / typed in the operator-facing convention
          // (Tilt+ = above horizon, #783) to match the fixture-level
          // rotation editor. Internal storage keeps rx>0 = pitch DOWN
          // (#586/#600), so we negate on display and on save. Stored value
          // in `srot[0]` is internal-frame; UI shows `-srot[0]`.
          var tiltDisplay=(typeof srot[0]==='number')?(-srot[0]):'';
          var ryVal=(typeof srot[1]==='number')?srot[1]:'';
          var rzVal=(typeof srot[2]==='number')?srot[2]:'';
          var sLeds=sObj.leds||0;
          var sMm=sObj.mm||0;
          h+='<div style="margin-bottom:.55em;padding:.4em .5em;background:#0f172a;border-radius:4px">';
          h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.25em"><b>String '+(ss+1)+'</b> <span style="color:#64748b">'+sLeds+' LEDs · '+sMm+' mm</span></div>';
          h+='<div style="display:flex;align-items:center;gap:.3em;margin-bottom:.25em">';
          h+='<span style="font-size:.7em;color:#64748b;min-width:60px">Start</span>';
          h+='<label style="font-size:.7em;color:#64748b">X</label><input id="fx-str-'+ss+'-x" type="number" value="'+sx+'" placeholder="inherit" style="width:70px">';
          h+='<label style="font-size:.7em;color:#64748b">Y</label><input id="fx-str-'+ss+'-y" type="number" value="'+sy+'" placeholder="inherit" style="width:70px">';
          h+='<label style="font-size:.7em;color:#64748b">Z</label><input id="fx-str-'+ss+'-z" type="number" value="'+sz+'" placeholder="inherit" style="width:70px">';
          h+='</div>';
          h+='<div style="display:flex;align-items:center;gap:.3em;flex-wrap:wrap">';
          h+='<span style="font-size:.7em;color:#64748b;min-width:60px">Rotation</span>';
          h+='<label style="font-size:.7em;color:#64748b" title="Tilt: + above horizon, - below">Tilt</label><input id="fx-str-'+ss+'-rx" type="number" step="any" value="'+tiltDisplay+'" placeholder="0" style="width:70px">';
          h+='<label style="font-size:.7em;color:#64748b" title="Roll about strip axis">Roll</label><input id="fx-str-'+ss+'-ry" type="number" step="any" value="'+ryVal+'" placeholder="0" style="width:70px">';
          h+='<label style="font-size:.7em;color:#64748b" title="Pan: + toward stage-left">Pan</label><input id="fx-str-'+ss+'-rz" type="number" step="any" value="'+rzVal+'" placeholder="0" style="width:70px">';
          h+=' <span style="font-size:.65em;color:#64748b;margin-left:.4em">presets:</span>';
          // Preset values are in the operator-facing convention (Tilt+ =
          // up). _fxSetStrRot negates rx on save same as the manual input.
          h+=' <button class="btn" type="button" style="font-size:.65em;padding:.1em .4em" onclick="_fxSetStrRot('+ss+',90,0,0)" title="Vertical up: Tilt +90">↑ up</button>';
          h+=' <button class="btn" type="button" style="font-size:.65em;padding:.1em .4em" onclick="_fxSetStrRot('+ss+',-90,0,0)" title="Vertical down: Tilt -90">↓ down</button>';
          h+=' <button class="btn" type="button" style="font-size:.65em;padding:.1em .4em" onclick="_fxSetStrRot('+ss+',0,0,90)" title="Stage-left: Pan +90">+X</button>';
          h+=' <button class="btn" type="button" style="font-size:.65em;padding:.1em .4em" onclick="_fxSetStrRot('+ss+',0,0,-90)" title="Stage-right: Pan -90">-X</button>';
          h+=' <button class="btn" type="button" style="font-size:.65em;padding:.1em .4em" onclick="_fxSetStrRot('+ss+',0,0,0)" title="Default forward (+Y)">+Y</button>';
          h+=' <button class="btn" type="button" style="font-size:.65em;padding:.1em .4em" onclick="_fxSetStrRot('+ss+',0,0,180)" title="Toward audience (-Y)">-Y</button>';
          h+='</div>';
          h+='</div>';
        }
        h+='<p style="color:#64748b;font-size:.72em;margin-top:.2em">Start = stage-mm anchor of the first LED (leave blank to inherit fixture position). Rotation in degrees: <b>Tilt+ aims above horizon</b> (Tilt=+90 is straight up), Roll about strip axis, Pan+ aims stage-left. Same convention as cameras and DMX fixtures. Strip length is firmware-reported mm.</p>';
        h+='</div>';
      }
      return h;
    },
    // #864 / #866 — fold per-string position and rotation into the
    // strings payload. Editor only renders rows for active strings
    // (leds>0); inactive slots (leds=0) round-trip unchanged. Position
    // (x,y,z) and rotation (rx,ry,rz) are independently optional but
    // each group is all-or-nothing — partial start XYZ or partial
    // rotation is rejected to avoid silent half-set inheritance bugs.
    collectEditFields:function(body,id){
      var origFx=null;_fixtures.forEach(function(fx){if(fx.id===id)origFx=fx;});
      if(origFx&&origFx.strings&&document.querySelector('[id^="fx-str-"][id$="-x"]')){
        var perStrErr=null;
        var newStrings=origFx.strings.map(function(s,ss){
          var copy={};for(var k in s){if(k!=='x'&&k!=='y'&&k!=='z'&&k!=='rotation'&&k!=='aim')copy[k]=s[k];}
          var ex=document.getElementById('fx-str-'+ss+'-x');
          if(!ex)return copy; // inactive string slot — keep as-is
          var ey=document.getElementById('fx-str-'+ss+'-y');
          var ez=document.getElementById('fx-str-'+ss+'-z');
          var vx=ex.value.trim(),vy=ey?ey.value.trim():'',vz=ez?ez.value.trim():'';
          var filled=(vx!=='')+(vy!=='')+(vz!=='');
          if(filled===3){copy.x=parseFloat(vx);copy.y=parseFloat(vy);copy.z=parseFloat(vz);}
          else if(filled!==0){perStrErr='String '+(ss+1)+' Start: fill all of X / Y / Z, or leave them all blank.';return copy;}
          var erx=document.getElementById('fx-str-'+ss+'-rx');
          var ery=document.getElementById('fx-str-'+ss+'-ry');
          var erz=document.getElementById('fx-str-'+ss+'-rz');
          var vrx=erx?erx.value.trim():'',vry=ery?ery.value.trim():'',vrz=erz?erz.value.trim():'';
          var rFilled=(vrx!=='')+(vry!=='')+(vrz!=='');
          if(rFilled===3){
            // #866 — operator typed Tilt in the +up convention; negate
            // back to internal rx>0=down so spatial_engine /
            // build_camera_to_stage see the canonical convention. Same
            // negation the fixture-level Rotation editor performs.
            copy.rotation=[-parseFloat(vrx),parseFloat(vry),parseFloat(vrz)];
          }else if(rFilled!==0){
            perStrErr='String '+(ss+1)+' Rotation: fill all of rx / ry / rz, or leave them all blank.';return copy;
          }
          return copy;
        });
        if(perStrErr)return perStrErr;
        body.strings=newStrings;
      }
      return null;
    },
    afterEditOpen:null,
  },

  dmx:{
    key:'dmx',
    label:'DMX',
    badge:{text:'DMX',bg:'#7c3aed',fg:'#fff'},
    accent:0x7c3aed,
    nodeShape:'sphere',
    editTypeSuffix:' (DMX)',
    caps:{hasBeam:true,hasFov:false,tracksPeople:false,hasOrientation:true,
          hasInvertedMount:true,hasRotationEditor:true,countsAsCamera:false},
    layoutNodeColor:function(c){return 0x7c3aed;},
    runtimeNodeColor:function(c){return 0x7c3aed;},
    buildLayoutMesh:function(c,ctx){
      if(!ctx.fix)return;
      _ftAimConeLayout(c,ctx.fix,ctx.grp,false);
      _ftRestArrowLayout(ctx.fix,ctx.grp,false);
    },
    buildRuntimeMesh:function(c,ctx){
      // Store pan/tilt metadata for live beam animation
      if(c.dmxProfileId&&window._profileCache&&window._profileCache[c.dmxProfileId]){
        var prof=window._profileCache[c.dmxProfileId];
        ctx.grp.userData.panRange=prof.panRange||540;
        ctx.grp.userData.tiltRange=prof.tiltRange||270;
      }
      _ftAimConeRuntime(c,ctx.grp,false);
      _ftRestArrowRuntime(c,ctx.grp,false);
    },
    updateRuntimeMesh:_ftDmxRuntimeUpdate,
    panelDetailHtml:function(f){
      var h='';
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">DMX</div>';
      h+='<div style="font-size:.82em;color:#c4b5fd;margin-bottom:.2em">Profile: '+(f.dmxProfileId?'<span style="color:#e9d5ff">'+escapeHtml(f.dmxProfileId)+'</span>':'<span style="color:#64748b">None</span>')+'</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.4em">Universe: '+(f.dmxUniverse||1)+' | Addr: '+(f.dmxStartAddr||1)+'</div>';
      // Rotation / orientation
      var orient=f.orientation||{};
      // #892 — read through rotationFromLayout (#600: pan lives at rz,
      // not rot[1] which is roll). Tilt displays negated to match the
      // fixture editor in fixtures.js (#783: operator tilt+ = above
      // horizon; internal rx>0 = down). _panelPanTiltChange negates back
      // on save so typed values round-trip.
      var _rotA=rotationFromLayout(f.rotation);
      var _panDeg=Math.round(_rotA.pan);
      var _tiltDeg=Math.round(-_rotA.tilt);
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Orientation</div>';
      h+='<div style="display:flex;gap:.3em;align-items:center;margin-bottom:.3em">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Pan°</label><input id="panel-pan" type="number" value="'+_panDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'pan\',this.value)">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Tilt°</label><input id="panel-tilt" type="number" value="'+_tiltDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'tilt\',this.value)">';
      if(orient.verified)h+='<span style="color:#4ade80;font-size:.75em">✓</span>';
      h+='</div>';
      if(f.mountedInverted)h+='<div style="font-size:.72em;color:#f59e0b;margin-bottom:.3em">⚠ Mounted upside-down</div>';
      h+='<div style="display:flex;gap:.3em;flex-wrap:wrap">';
      h+='<button class="btn" onclick="_orientTest('+f.id+')" style="font-size:.7em;padding:.2em .5em;background:#4c1d95;color:#e9d5ff">Test Orient</button>';
      h+='<button class="btn" onclick="closeModal();_moverCalStart('+f.id+')" style="font-size:.7em;padding:.2em .5em;background:#6b21a8;color:#d8b4fe">Calibrate'+(f.moverCalibrated?' ✓':'')+'</button>';
      h+='</div>';
      return h;
    },
    quickButtonsHtml:function(f){
      var h='';
      h+='<button class="btn" style="background:#1e3a5f;color:#93c5fd" onclick="startAimMode('+f.id+')">Click to Aim</button>';
      h+='<button class="btn" style="background:#6b21a8;color:#d8b4fe" onclick="closeModal();_moverCalStart('+f.id+')">Calibrate'+(f.moverCalibrated?' ✓':'')+'</button>';
      return h;
    },
    sidebarBadgeHtml:function(f){
      return'<span style="font-size:.6em;background:#7c3aed;color:#fff;padding:0 3px;border-radius:2px">DMX</span>';
    },
    sidebarPlacedBadgeHtml:function(f){
      return'<span style="font-size:.6em;background:#7c3aed;color:#fff;padding:0 3px;border-radius:2px;margin-left:2px">DMX</span>';
    },
    renderEditFields:function(f,id){
      var h='';
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
      if(f.dmxProfileId)h+='<option value="'+escapeHtml(f.dmxProfileId)+'" selected>'+escapeHtml(f.dmxProfileId)+' (loading…)</option>';
      h+='</select>';
      h+='<div style="margin-top:.8em;border-top:1px solid #1e293b;padding-top:.6em">';
      h+='<div style="display:flex;justify-content:space-between;align-items:center">';
      h+='<span style="font-weight:bold;font-size:.85em">Test Channels</span>';
      h+='<button class="btn btn-on" onclick="loadFixtureChannels('+id+')" style="font-size:.65em">Load</button>';
      h+='</div>';
      h+='<div id="fx-test-ch"></div>';
      h+='</div>';
      // #687 — Motor Calibration / Set Home blocks render AFTER position +
      // rotation in the new ordering (see renderEditExtras).
      return h;
    },
    renderEditExtras:function(f,id){
      var h='';
      h+='<label style="display:flex;align-items:center;gap:.4em;margin-top:.5em;cursor:pointer"><input id="fx-inverted" type="checkbox"'+(f.mountedInverted?' checked':'')+' style="width:auto"> <span style="font-size:.82em">Mounted upside-down (inverted)</span></label>';
      h+='<p style="color:#64748b;font-size:.72em;margin-top:.2em">Reverses pan and tilt motor direction for truss-mounted fixtures.</p>';
      // #687 + #744 — Set Home block (between Mount and Motor Calibration).
      // The badge reflects the THREE-state capability gate from #738 — a
      // primary-only fixture (homeSetAt truthy but homeSecondary null) is
      // not Calibrate-ready. Showing "✓ Home set" for that state misled
      // operators into thinking they were ready when Calibrate would
      // refuse with state:no_home.
      var hasPrimary = f.homePanDmx16!=null && f.homeTiltDmx16!=null;
      var hasSecondary = !!f.homeSecondary;
      var setAt = f.homeSetAt ? new Date(f.homeSetAt).toLocaleString() : '';
      h+='<div style="margin-top:.8em;border-top:1px solid #1e293b;padding-top:.6em">';
      var badge;
      if(hasPrimary && hasSecondary){
        badge='<span style="color:#4ade80">✓ '+escapeHtml(setAt)+'</span>';
      } else if(hasPrimary){
        badge='<span style="color:#fbbf24">◐ Primary set, secondary missing — re-run Set Home</span>';
      } else {
        badge='<span style="color:#f59e0b">⚠ required for calibration</span>';
      }
      h+='<div style="font-weight:bold;font-size:.85em;margin-bottom:.4em">Set Home '+badge+'</div>';
      h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.4em">Drive the fixture manually until the beam aims along the Rotation vector above, then Confirm. This anchors calibration to one operator-verified observation.</div>';
      h+='<button class="btn" onclick="_setHomeOpen('+id+')" style="background:#0e7490;color:#a5f3fc;font-size:.85em">'+(hasPrimary?'Re-Set Home':'Set Home')+'</button>';
      if(hasPrimary){
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
      // #738 capability gate: Calibrate-ready needs primary Home + Secondary
      // direction calls. Pre-#788 this read an undefined `hasHome` which
      // raised ReferenceError mid-render and blanked the modal for every
      // DMX fixture (LED fixtures skipped this block entirely so the bug
      // looked like "Edit only works for LEDs").
      var calDisabled = !(hasPrimary && hasSecondary);
      var calStyle = 'background:#6b21a8;color:#d8b4fe;margin-left:.5em' + (calDisabled?';opacity:.5;cursor:not-allowed':'');
      var calOnClick = calDisabled
        ? 'alert(\'Set Home + Movement Direction before calibrating. Click Set Home above and drive the fixture along its rotation vector.\');return false;'
        : 'closeModal();_moverCalStart('+id+')';
      var calTitle = calDisabled ? 'Set Home first' : '';
      h+='<button class="btn" onclick="'+calOnClick+'" style="'+calStyle+'" title="'+calTitle+'">Calibrate'+(f.moverCalibrated?' ✓':'')+'</button>';
      h+='</div>';
      return h;
    },
    collectEditFields:function(body,id){
      body.dmxUniverse=parseInt(document.getElementById('fx-uni').value)||1;
      body.dmxStartAddr=parseInt(document.getElementById('fx-addr').value)||1;
      body.dmxChannelCount=parseInt(document.getElementById('fx-ch').value)||3;
      body.dmxProfileId=document.getElementById('fx-prof').value.trim()||null;
      var invEl=document.getElementById('fx-inverted');
      if(invEl)body.mountedInverted=invEl.checked;
      return null;
    },
    afterEditOpen:function(id){_editFxLoadProfiles();},
  },

  camera:{
    key:'camera',
    label:'Camera',
    badge:{text:'CAM',bg:'#0e7490',fg:'#fff'},
    accent:0x0e7490,
    nodeShape:'sphere',
    editTypeSuffix:' (Camera)',
    caps:{hasBeam:false,hasFov:true,tracksPeople:true,hasOrientation:true,
          hasInvertedMount:false,hasRotationEditor:true,countsAsCamera:true},
    layoutNodeColor:function(c){return 0x0e7490;},
    runtimeNodeColor:function(c){return 0x0e7490;},
    buildLayoutMesh:function(c,ctx){
      if(!ctx.fix)return;
      _ftAimConeLayout(c,ctx.fix,ctx.grp,true);
      _ftRestArrowLayout(ctx.fix,ctx.grp,true);
    },
    buildRuntimeMesh:function(c,ctx){
      _ftAimConeRuntime(c,ctx.grp,true);
      _ftRestArrowRuntime(c,ctx.grp,true);
    },
    updateRuntimeMesh:null,
    panelDetailHtml:function(f){
      var h='';
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Camera</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.2em">IP: '+(f.cameraIp?'<span style="color:#e2e8f0">'+escapeHtml(f.cameraIp)+'</span>':'<span style="color:#f59e0b">Not set</span>')+'</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.4em">FOV: '+(f.fovDeg||60)+'° | '+(f.resolutionW||1920)+'×'+(f.resolutionH||1080)+'</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.3em">Homography: '+(f.calibrated?'<span style="color:#4ade80">✓ Done</span>':'<span style="color:#f59e0b">Not done</span>')+'</div>';
      h+='<div style="display:flex;gap:.3em;margin-bottom:.4em">';
      h+='<button class="btn" onclick="_intrinsicCalStart('+f.id+')" style="font-size:.7em;padding:.2em .5em;background:#164e63;color:#67e8f9">Calibrate Lens</button> ';
      h+='</div>';
      // Pan/Tilt editable for camera (from rotation)
      // #892 — same rotationFromLayout read + #783 tilt negation as the
      // DMX block above.
      var _cRotA=rotationFromLayout(f.rotation);
      var _cPanDeg=Math.round(_cRotA.pan);
      var _cTiltDeg=Math.round(-_cRotA.tilt);
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Orientation</div>';
      h+='<div style="display:flex;gap:.3em;align-items:center;margin-top:.3em">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Pan°</label><input id="panel-pan" type="number" value="'+_cPanDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'pan\',this.value)">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Tilt°</label><input id="panel-tilt" type="number" value="'+_cTiltDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'tilt\',this.value)">';
      h+='</div>';
      return h;
    },
    quickButtonsHtml:function(f){
      var h='';
      h+='<button class="btn" style="background:#7c3aed;color:#e9d5ff" onclick="closeModal();_calWizardStart('+f.id+')">Calibrate'+(f.calibrated?' ✓':'')+'</button>';
      var _ta=_trackingCams[f.id];
      h+='<button class="btn" id="trk-btn-'+f.id+'" style="background:'+(_ta?'#9f1239':'#be185d')+';color:#fce7f3" onclick="closeModal();_trackToggle('+f.id+')">'+(_ta?'Stop Track':'Track')+'</button>';
      return h;
    },
    sidebarBadgeHtml:function(f){
      return'<span style="font-size:.6em;background:#0e7490;color:#fff;padding:0 3px;border-radius:2px">CAM</span>';
    },
    // Q5 - tiered placement-quality badge. H (green) = homography,
    // FOV (amber) = camera-pose fallback, RAW (red) = no cal + no pos.
    sidebarPlacedBadgeHtml:function(f){
      var ftBadge='<span style="font-size:.6em;background:#0e7490;color:#fff;padding:0 3px;border-radius:2px;margin-left:2px">CAM</span>';
      if(f.calibrated){
        ftBadge+='<span style="font-size:.6em;background:#065f46;color:#34d399;padding:0 3px;border-radius:2px;margin-left:2px" title="Homography calibration from surveyed markers">H</span>';
      }else if(f.x!=null||f.y!=null||f.z!=null){
        ftBadge+='<span style="font-size:.6em;background:#78350f;color:#fbbf24;padding:0 3px;border-radius:2px;margin-left:2px" title="No homography - FOV projection fallback (less accurate)">FOV</span>';
      }else{
        ftBadge+='<span style="font-size:.6em;background:#7f1d1d;color:#fca5a5;padding:0 3px;border-radius:2px;margin-left:2px" title="No cal and no position - tracking holds last-good for this camera">RAW</span>';
      }
      return ftBadge;
    },
    renderEditFields:function(f,id){
      var h='';
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
      return h;
    },
    renderEditExtras:function(f,id){return'';},
    collectEditFields:function(body,id){
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
      return null;
    },
    afterEditOpen:null,
  },

  // mmWave radar node (#911) — ESP32-C61 + Ai-Thinker Rd-03D, a planar
  // people-tracking sensor (docs/design/mmwave_tracking.md). Amber accent
  // (led=green, dmx=violet, camera=cyan). Small flat module → box node.
  // Fields: radarNode (hostname/IP of the mmWave child), rangeMm
  // (detection radius, Rd-03D ≈ 8000), fovDeg (full azimuth width,
  // Rd-03D = 120 i.e. ±60°), radarEnabled (feeds person tracking;
  // undefined = enabled).
  radar:{
    key:'radar',
    label:'Radar',
    badge:{text:'RDR',bg:'#78350f',fg:'#fbbf24'},
    accent:0xf59e0b,
    nodeShape:'box',
    editTypeSuffix:' (Radar)',
    caps:{hasBeam:false,hasFov:true,tracksPeople:true,hasOrientation:true,
          hasInvertedMount:false,hasRotationEditor:true,countsAsCamera:false},
    layoutNodeColor:function(c){return 0xf59e0b;},
    runtimeNodeColor:function(c){return 0xf59e0b;},
    buildLayoutMesh:function(c,ctx){_ftRadarSector(ctx.fix||c,ctx.grp,0.10);},
    // Runtime: same sector at lower opacity — like the camera FOV cone it
    // renders statically (no per-frame updater; tracking output shows up
    // as person objects, not on the fixture mesh).
    buildRuntimeMesh:function(c,ctx){_ftRadarSector(c,ctx.grp,0.06);},
    updateRuntimeMesh:null,
    panelDetailHtml:function(f){
      var rngMm=(typeof f.rangeMm==='number'&&f.rangeMm>0)?f.rangeMm:8000;
      var fov=(typeof f.fovDeg==='number'&&f.fovDeg>0)?f.fovDeg:120;
      var enabled=(f.radarEnabled!==false);
      var h='';
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Radar</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.2em">Node: '+(f.radarNode?'<span style="color:#e2e8f0">'+escapeHtml(f.radarNode)+'</span>':'<span style="color:#f59e0b">Not set</span>')+'</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.2em">Range: '+(Math.round(rngMm/100)/10)+' m | FoV: ±'+(fov/2)+'°</div>';
      h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.2em">'+(enabled?'<span style="color:#4ade80">● Enabled</span>':'<span style="color:#f59e0b">○ Disabled</span>')+'</div>';
      h+='<div style="font-size:.75em;color:#f472b6;margin-bottom:.4em">Feeds person tracking</div>';
      // Orientation Pan/Tilt — same rotationFromLayout read + #783 tilt
      // negation as the camera block above (mounting tilt per
      // mmwave_tracking.md §7 is captured by rx).
      var _rRotA=rotationFromLayout(f.rotation);
      var _rPanDeg=Math.round(_rRotA.pan);
      var _rTiltDeg=Math.round(-_rRotA.tilt);
      h+='<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Orientation</div>';
      h+='<div style="display:flex;gap:.3em;align-items:center;margin-top:.3em">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Pan°</label><input id="panel-pan" type="number" value="'+_rPanDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'pan\',this.value)">';
      h+='<label style="font-size:.72em;color:#64748b;margin:0">Tilt°</label><input id="panel-tilt" type="number" value="'+_rTiltDeg+'" style="width:58px;font-size:.82em;padding:2px 3px" onchange="_panelPanTiltChange('+f.id+',\'tilt\',this.value)">';
      h+='</div>';
      return h;
    },
    quickButtonsHtml:function(f){return'';},
    sidebarBadgeHtml:function(f){
      return'<span style="font-size:.6em;background:#78350f;color:#fbbf24;padding:0 3px;border-radius:2px">RDR</span>';
    },
    sidebarPlacedBadgeHtml:function(f){
      return'<span style="font-size:.6em;background:#78350f;color:#fbbf24;padding:0 3px;border-radius:2px;margin-left:2px">RDR</span>';
    },
    renderEditFields:function(f,id){
      var rngMm=(typeof f.rangeMm==='number'&&f.rangeMm>0)?f.rangeMm:8000;
      var fov=(typeof f.fovDeg==='number'&&f.fovDeg>0)?f.fovDeg:120;
      var h='';
      h+='<label>Radar Node <span style="color:#64748b;font-size:.75em">(hostname or IP of the mmWave node)</span></label>';
      h+='<input id="fx-radar-node" value="'+escapeHtml(f.radarNode||'')+'" placeholder="e.g. mmwave-01.local or 192.168.10.60" style="width:100%;margin-bottom:.4em">';
      h+='<label>Range (mm) <span style="color:#64748b;font-size:.75em">(Rd-03D ≈ 8000)</span></label>';
      h+='<input id="fx-radar-range" type="number" value="'+rngMm+'" min="100" step="100" style="width:100%;margin-bottom:.4em">';
      h+='<label>FoV (degrees, full width) <span style="color:#64748b;font-size:.75em">(Rd-03D: 120 = ±60°)</span></label>';
      h+='<input id="fx-radar-fov" type="number" value="'+fov+'" min="1" max="180" style="width:100%;margin-bottom:.4em">';
      h+='<label style="display:flex;align-items:center;gap:.4em;margin-top:.2em;cursor:pointer"><input id="fx-radar-enabled" type="checkbox"'+(f.radarEnabled!==false?' checked':'')+' style="width:auto"> <span style="font-size:.82em">Enabled (feeds person tracking)</span></label>';
      // #913 — position/rotation here are the layer-1 manual survey
      // (mmwave_tracking.md §6). The calibration walk refines them.
      h+='<div style="font-size:.75em;color:#64748b;margin-top:.4em">Tip: after placing this radar roughly, refine its pose with the <b>Radar Calibration</b> walk on the Setup tab (one person walks a loop through the radar overlap zones).</div>';
      return h;
    },
    renderEditExtras:function(f,id){return'';},
    collectEditFields:function(body,id){
      var rngV=parseInt(document.getElementById('fx-radar-range').value);
      if(!(rngV>0))return'Range: enter a positive number of millimetres (Rd-03D is about 8000).';
      var fovV=parseFloat(document.getElementById('fx-radar-fov').value);
      if(!(fovV>0&&fovV<=180))return'FoV: enter a full width between 1 and 180 degrees (Rd-03D is 120).';
      body.radarNode=document.getElementById('fx-radar-node').value.trim();
      body.rangeMm=rngV;
      body.fovDeg=fovV;
      body.radarEnabled=document.getElementById('fx-radar-enabled').checked;
      return null;
    },
    afterEditOpen:null,
  },
};

// `gyro` fixtures (handheld gyro remotes, #772/#825) predate this registry
// and were never special-cased in the rendering chains — every converted
// call site treated them as the implicit `led` else-branch (green/grey node,
// LED side-panel badge, no sidebar chip, no runtime animation) EXCEPT the
// fixtures.js editor, whose `ft!=='led'` gate showed them the fixture-level
// rotation editor. This entry pins that exact historical behaviour so the
// unknown-type fallback below stays reserved for genuinely unknown types
// (e.g. `radar` before #911).
FIXTURE_TYPES.gyro={
  key:'gyro',
  label:'Gyro',
  badge:FIXTURE_TYPES.led.badge,
  accent:FIXTURE_TYPES.led.accent,
  nodeShape:'sphere',
  editTypeSuffix:'',
  caps:{hasBeam:false,hasFov:false,tracksPeople:false,hasOrientation:false,
        hasInvertedMount:false,hasRotationEditor:true,countsAsCamera:false},
  layoutNodeColor:FIXTURE_TYPES.led.layoutNodeColor,
  runtimeNodeColor:FIXTURE_TYPES.led.runtimeNodeColor,
  buildLayoutMesh:FIXTURE_TYPES.led.buildLayoutMesh,
  buildRuntimeMesh:FIXTURE_TYPES.led.buildRuntimeMesh,
  updateRuntimeMesh:null,
  panelDetailHtml:FIXTURE_TYPES.led.panelDetailHtml,
  quickButtonsHtml:function(f){return'';},
  sidebarBadgeHtml:function(f){return'';},
  sidebarPlacedBadgeHtml:function(f){return'';},
  renderEditFields:function(f,id){return'';},
  renderEditExtras:function(f,id){return'';},
  collectEditFields:function(body,id){return null;},
  afterEditOpen:null,
};

// ── Unknown-type fallback ─────────────────────────────────────────────────
// What an un-updated SPA shows for a fixture type it doesn't know (e.g. a
// `radar` fixture from #911 opened in an older orchestrator): a neutral
// slate box in the 3D scenes with the usual name label, an uppercase
// type-name chip in the sidebar/side panel, and position editing — no
// type-specific controls invented.
var _ftUnknownCache={};
function _ftUnknown(key){
  if(_ftUnknownCache[key])return _ftUnknownCache[key];
  var label=key?key.charAt(0).toUpperCase()+key.slice(1):'Unknown';
  var chipText=String(key||'?').toUpperCase();
  var chip='<span style="font-size:.6em;background:#334155;color:#cbd5e1;padding:0 3px;border-radius:2px">'+escapeHtml(chipText)+'</span>';
  var desc={
    key:key,
    label:label,
    badge:{text:chipText,bg:'#334155',fg:'#cbd5e1'},
    accent:0x64748b,
    nodeShape:'box',
    editTypeSuffix:' ('+label+')',
    caps:{hasBeam:false,hasFov:false,tracksPeople:false,hasOrientation:false,
          hasInvertedMount:false,hasRotationEditor:true,countsAsCamera:false},
    layoutNodeColor:function(c){return 0x64748b;},
    runtimeNodeColor:function(c){return 0x64748b;},
    buildLayoutMesh:function(c,ctx){},
    buildRuntimeMesh:function(c,ctx){},
    updateRuntimeMesh:null,
    panelDetailHtml:function(f){
      return'<div style="font-weight:600;color:#94a3b8;font-size:.78em;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">'+escapeHtml(label)+'</div>'
        +'<div style="font-size:.82em;color:#94a3b8">This orchestrator build does not know the “'+escapeHtml(key)+'” fixture type yet. Position editing works; update the orchestrator for full support.</div>';
    },
    quickButtonsHtml:function(f){return'';},
    sidebarBadgeHtml:function(f){return chip;},
    sidebarPlacedBadgeHtml:function(f){
      return'<span style="font-size:.6em;background:#334155;color:#cbd5e1;padding:0 3px;border-radius:2px;margin-left:2px">'+escapeHtml(chipText)+'</span>';
    },
    renderEditFields:function(f,id){return'';},
    renderEditExtras:function(f,id){return'';},
    collectEditFields:function(body,id){return null;},
    afterEditOpen:null,
  };
  _ftUnknownCache[key]=desc;
  return desc;
}

// ── Lookup helpers ────────────────────────────────────────────────────────
// fixtureTypeKey(f)  — canonical type key for a fixture record ('led' when
//                      the field is absent, matching the legacy `||'led'`
//                      defaulting at every converted call site).
// fixtureTypeDesc(x) — descriptor for a fixture record OR a bare key string;
//                      unknown keys get the generated fallback above.
function fixtureTypeKey(f){
  return (f&&f.fixtureType)||'led';
}
function fixtureTypeDesc(f){
  var key=(typeof f==='string')?(f||'led'):fixtureTypeKey(f);
  return FIXTURE_TYPES[key]||_ftUnknown(key);
}
