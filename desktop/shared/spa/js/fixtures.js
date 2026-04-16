/** fixtures.js — Fixture editing, orientation test, DMX channel test. Extracted from app.js Phase 3. */
// ── Fixtures ────────────────────────────────────────────────────────────────
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
