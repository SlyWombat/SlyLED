/** objects-effects.js — Objects (stage objects) and Spatial Effects CRUD. Extracted from app.js Phase 2. */
// ── Objects (stage objects) ──────────────────────────────────────────
var _objects=[];

function loadObjects(cb){
  ra('GET','/api/objects',null,function(d){_objects=d||[];renderObjectsSidebar();if(cb)cb();});
}

function renderObjectsSidebar(){
  var el=document.getElementById('lay-objects');if(!el)return;
  if(!_objects.length){el.innerHTML='<p style="color:#555;font-size:.82em">No objects.</p>';return;}
  var h='';
  _objects.forEach(function(s){
    var col=s.color||'#334155';
    var icons={wall:'\u2b1b',floor:'\u2b1c',truss:'\u2501',screen:'\u25fb',prop:'\u25c6',custom:'\u25a1'};
    var icon=icons[s.objectType||'custom']||'\u25a1';
    var lockIcon=s.stageLocked?'<span title="Locked to stage" style="color:#64748b;font-size:.7em">\ud83d\udd12</span>':'';
    var mobIcon=s.mobility==='moving'?'<span title="Moving" style="color:#f59e0b;font-size:.65em">\u2194</span>':'';
    var patIcon=(s.patrol&&s.patrol.enabled)?'<span title="Patrol" style="color:#38bdf8;font-size:.65em">\u21c4</span>':'';
    h+='<div style="padding:.3em 0;border-bottom:1px solid #1e293b;display:flex;align-items:center;gap:.4em">';
    h+='<span style="color:'+col+';font-size:.9em;flex-shrink:0">'+icon+'</span>';
    h+='<span style="flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;cursor:pointer" onclick="selectOnCanvas(\'object\','+s.id+')">'+escapeHtml(s.name)+lockIcon+mobIcon+patIcon+'</span>';
    h+='<span style="cursor:pointer;color:#3b82f6;font-size:.8em" onclick="editObject('+s.id+')">\u270e</span>';
    h+='<span style="cursor:pointer;color:#f66;font-size:.8em" onclick="delObject('+s.id+')">\u2715</span>';
    h+='</div>';
  });
  el.innerHTML=h;
  _panelUpdateCounts();
}

function newObject(){
  var h='<label>Type</label><select id="sf-type" onchange="objTypeChange()">';
  h+='<option value="wall">Wall / Backdrop</option>';
  h+='<option value="floor">Floor / Platform</option>';
  h+='<option value="truss">Truss / Bar</option>';
  h+='<option value="screen">Projection Screen</option>';
  h+='<option value="prop">Prop / Person</option>';
  h+='<option value="custom">Custom Object</option>';
  h+='</select>';
  h+='<label>Mobility</label><select id="sf-mob">';
  h+='<option value="static">Static</option>';
  h+='<option value="moving">Moving</option>';
  h+='</select>';
  h+='<label>Name</label><input id="sf-name" value="" style="width:100%">';
  h+='<label>Colour</label><input id="sf-color" type="color" value="#334155">';
  h+='<div id="sf-lock-wrap" style="margin:.5em 0"><label style="cursor:pointer" title="Auto-size to match stage width/height — disables manual position and dimension editing"><input type="checkbox" id="sf-lock" onchange="objLockChange()" checked> Lock to stage dimensions</label></div>';
  h+='<div id="sf-dims">';
  h+='<label>Position X (mm)</label><input id="sf-x" type="number" value="0" style="width:100px">';
  h+=' <label style="display:inline;margin-left:.5em">Y (mm)</label><input id="sf-y" type="number" value="0" style="width:100px">';
  h+=' <label style="display:inline;margin-left:.5em">Z (mm)</label><input id="sf-z" type="number" value="0" style="width:100px">';
  h+='<label>Width (mm)</label><input id="sf-w" type="number" value="3000" min="100" style="width:100px">';
  h+=' <label style="display:inline;margin-left:.5em">Height (mm)</label><input id="sf-h" type="number" value="2000" min="100" style="width:100px">';
  h+=' <label style="display:inline;margin-left:.5em">Depth (mm)</label><input id="sf-d" type="number" value="100" min="0" style="width:100px">';
  h+='</div>';
  h+='<label>Opacity <span id="sf-op-val">30%</span></label><input id="sf-op" type="range" min="10" max="100" value="30" style="width:120px" oninput="document.getElementById(\'sf-op-val\').textContent=this.value+\'%\'">';
  h+='<div id="sf-patrol" style="margin-top:.6em;padding:.5em;border:1px solid #1e293b;border-radius:4px">';
  h+='<label style="cursor:pointer"><input type="checkbox" id="sf-pat-en" onchange="_patToggle()"> Patrol (auto back-and-forth)</label>';
  h+='<div id="sf-pat-opts" style="display:none;margin-top:.4em">';
  h+='<label>Pattern</label><select id="sf-pat-pattern" onchange="_patPatternChange()"><option value="pingpong">Ping-Pong</option><option value="circle">Circle</option><option value="figure8">Figure 8</option><option value="square">Square</option></select>';
  h+='<div id="sf-pat-axis-wrap"><label>Axis</label><select id="sf-pat-axis"><option value="x">Side to side (X)</option><option value="y">Front to back (Y)</option><option value="xy">Diagonal (X+Y)</option></select></div>';
  h+='<label>Speed</label><select id="sf-pat-speed" onchange="var c=document.getElementById(\'sf-pat-cyc\');c.style.display=this.value===\'custom\'?\'inline\':\'none\'"><option value="slow">Slow (20s)</option><option value="medium" selected>Medium (10s)</option><option value="fast">Fast (5s)</option><option value="custom">Custom</option></select>';
  h+=' <input id="sf-pat-cyc" type="number" value="10" min="1" max="120" style="width:60px;display:none"> <span id="sf-pat-cyc-lbl" style="font-size:.8em;color:#666">sec</span>';
  h+='<label>Range</label><input id="sf-pat-start" type="number" value="10" min="0" max="100" style="width:50px">% to <input id="sf-pat-end" type="number" value="90" min="0" max="100" style="width:50px">% of stage';
  h+='<div id="sf-pat-bound-wrap"><label>Bounding Object <span style="color:#64748b;font-size:.75em">(optional)</span></label><select id="sf-pat-bound"><option value="">None (use stage %)</option></select></div>';
  h+='<label>Easing</label><select id="sf-pat-ease"><option value="sine">Smooth (sine)</option><option value="linear">Linear</option></select>';
  h+='<label>Activation</label><select id="sf-pat-mode"><option value="always">Always (animate whenever DMX runs)</option><option value="on-demand">On Demand (only when linked show is playing)</option></select>';
  h+='</div></div>';
  h+='<div style="margin-top:.8em"><button class="btn btn-on" onclick="createObject()">Add to Stage</button></div>';
  document.getElementById('modal-title').textContent='New Stage Object';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  objTypeChange();
}

function _sfStageMm(){
  // Return stage dims in mm from cached _stageData or defaults
  var s=window._stageData||{w:3,h:2,d:1.5};
  return {sw:Math.round(s.w*1000),sh:Math.round(s.h*1000),sd:Math.round(s.d*1000)};
}
function objTypeChange(){
  var t=document.getElementById('sf-type').value;
  var sm=_sfStageMm();
  var lockable=(t==='wall'||t==='floor');
  var lk=document.getElementById('sf-lock');
  var lkWrap=document.getElementById('sf-lock-wrap');
  if(lkWrap)lkWrap.style.display=lockable?'block':'none';
  if(lk){lk.checked=lockable;lk.disabled=false;}
  var presets={
    wall:  {name:'Back Wall',   color:'#334155',w:sm.sw,h:sm.sh,d:100,op:25},
    floor: {name:'Stage Floor', color:'#1a2744',w:sm.sw,h:sm.sd+1000,d:100,op:20},
    truss: {name:'Truss Bar',   color:'#555555',w:sm.sw,h:50,d:100,op:60},
    screen:{name:'Projection',  color:'#0f172a',w:2000,h:1500,d:100,op:40},
    prop:  {name:'Prop',        color:'#FF6B35',w:500,h:1800,d:500,op:40},
    custom:{name:'Object',     color:'#334155',w:2000,h:1500,d:100,op:30},
  };
  var p=presets[t]||presets.custom;
  var n=document.getElementById('sf-name');if(n&&!n.value)n.value=p.name;
  var c=document.getElementById('sf-color');if(c)c.value=p.color;
  var w=document.getElementById('sf-w');if(w)w.value=p.w;
  var h=document.getElementById('sf-h');if(h)h.value=p.h;
  var d=document.getElementById('sf-d');if(d)d.value=p.d;
  var o=document.getElementById('sf-op');if(o){o.value=p.op;var ov=document.getElementById('sf-op-val');if(ov)ov.textContent=p.op+'%';}
  // Set mobility default based on type
  var mob=document.getElementById('sf-mob');
  if(mob){
    var movingTypes={prop:true,custom:true};
    mob.value=movingTypes[t]?'moving':'static';
  }
  // Show/hide patrol section for moving types
  var patDiv=document.getElementById('sf-patrol');
  if(patDiv){
    var isMoving=mob&&mob.value==='moving';
    patDiv.style.display=isMoving?'block':'none';
  }
  objLockChange();
  _patToggle();
}
function _patToggle(){
  var en=document.getElementById('sf-pat-en');
  var opts=document.getElementById('sf-pat-opts');
  if(en&&opts){
    opts.style.display=en.checked?'block':'none';
    if(en.checked)_patPopulateBoundingObjects();
  }
}
function _patPatternChange(){
  // Show/hide axis selector — only for pingpong (circle/figure8/square use XY implicitly)
  var pat=document.getElementById('sf-pat-pattern');
  var axWrap=document.getElementById('sf-pat-axis-wrap');
  if(pat&&axWrap)axWrap.style.display=(pat.value==='pingpong')?'block':'none';
}
function _patPopulateBoundingObjects(){
  // Populate the bounding object dropdown with existing static objects
  var sel=document.getElementById('sf-pat-bound');if(!sel)return;
  var html='<option value="">None (use stage %)</option>';
  (_objects||[]).forEach(function(o){
    if(o.mobility!=='moving'&&o.name){
      html+='<option value="'+escapeHtml(o.name)+'">'+escapeHtml(o.name)+'</option>';
    }
  });
  sel.innerHTML=html;
}
function objLockChange(){
  var lk=document.getElementById('sf-lock');
  var dims=document.getElementById('sf-dims');
  if(!lk||!dims)return;
  var locked=lk.checked&&!lk.disabled;
  dims.style.opacity=locked?'0.5':'1';
  dims.style.pointerEvents=locked?'none':'auto';
}

function createObject(){
  var lk=document.getElementById('sf-lock');
  var locked=lk&&lk.checked&&!lk.disabled;
  var body={
    name:document.getElementById('sf-name').value||'Object',
    color:document.getElementById('sf-color').value,
    objectType:document.getElementById('sf-type')?document.getElementById('sf-type').value:'custom',
    transform:{
      pos:[parseInt(document.getElementById('sf-x').value)||0,parseInt(document.getElementById('sf-y').value)||0,parseInt(document.getElementById('sf-z').value)||0],
      rot:[0,0,0],
      scale:[parseInt(document.getElementById('sf-w').value)||2000,parseInt(document.getElementById('sf-h').value)||1500,parseInt(document.getElementById('sf-d').value)||100]
    },
    opacity:parseInt(document.getElementById('sf-op').value)||30,
    mobility:document.getElementById('sf-mob').value||'static',
    stageLocked:locked,
  };
  var patEn=document.getElementById('sf-pat-en');
  if(patEn&&patEn.checked){
    var sp=document.getElementById('sf-pat-speed');
    var patPattern=document.getElementById('sf-pat-pattern');
    var patBound=document.getElementById('sf-pat-bound');
    var patMode=document.getElementById('sf-pat-mode');
    body.patrol={enabled:true,
      pattern:patPattern?patPattern.value:'pingpong',
      axis:document.getElementById('sf-pat-axis').value||'x',
      speedPreset:sp?sp.value:'medium',cycleS:parseFloat(document.getElementById('sf-pat-cyc').value)||10,
      startPct:parseInt(document.getElementById('sf-pat-start').value)||10,
      endPct:parseInt(document.getElementById('sf-pat-end').value)||90,
      easing:document.getElementById('sf-pat-ease').value||'sine',
      boundingObject:(patBound&&patBound.value)||'',
      patrolMode:(patMode?patMode.value:'always')};
  }
  ra('POST','/api/objects',body,function(r){
    closeModal();if(r&&r.ok){loadObjects();s3dLoadChildren();}
  });
}

function editObject(id){
  var s=null;_objects.forEach(function(sf){if(sf.id===id)s=sf;});
  if(!s)return;
  var t=s.transform||{pos:[0,0,0],rot:[0,0,0],scale:[2000,1500,1]};
  var lockable=(s.objectType==='wall'||s.objectType==='floor');
  var locked=!!s.stageLocked;
  var h='<label>Name</label><input id="sf-name" value="'+escapeHtml(s.name)+'" style="width:100%">';
  h+='<label>Colour</label><input id="sf-color" type="color" value="'+(s.color||'#334155')+'">';
  if(lockable){
    h+='<div style="margin:.5em 0"><label style="cursor:pointer"><input type="checkbox" id="sf-lock" onchange="objLockChange()"'+(locked?' checked':'')+'>  Lock to stage dimensions</label></div>';
  }
  h+='<div id="sf-dims"'+(locked?' style="opacity:0.5;pointer-events:none"':'')+'>';
  h+='<label>Position X (mm)</label><input id="sf-x" type="number" value="'+(t.pos[0]||0)+'" style="width:100px">';
  h+=' <label style="display:inline;margin-left:.5em">Y</label><input id="sf-y" type="number" value="'+(t.pos[1]||0)+'" style="width:100px">';
  h+=' <label style="display:inline;margin-left:.5em">Z</label><input id="sf-z" type="number" value="'+(t.pos[2]||0)+'" style="width:100px">';
  h+='<label>Width (mm)</label><input id="sf-w" type="number" value="'+(t.scale[0]||2000)+'" min="100" style="width:100px">';
  h+=' <label style="display:inline;margin-left:.5em">Height</label><input id="sf-h" type="number" value="'+(t.scale[1]||1500)+'" min="100" style="width:100px">';
  h+=' <label style="display:inline;margin-left:.5em">Depth</label><input id="sf-d" type="number" value="'+(t.scale[2]||100)+'" min="0" style="width:100px">';
  h+='</div>';
  h+='<label>Opacity</label><input id="sf-op" type="range" min="10" max="100" value="'+(s.opacity||30)+'" style="width:120px">';
  if(!locked){
    h+='<label>Mobility</label><select id="sf-mob">';
    h+='<option value="static"'+((s.mobility||'static')==='static'?' selected':'')+'>Static</option>';
    h+='<option value="moving"'+((s.mobility||'static')==='moving'?' selected':'')+'>Moving</option>';
    h+='</select>';
  }
  var isMoving=(s.mobility==='moving');
  var pat=s.patrol||{};
  if(isMoving){
    h+='<div id="sf-patrol" style="margin-top:.6em;padding:.5em;border:1px solid #1e293b;border-radius:4px">';
    h+='<label style="cursor:pointer"><input type="checkbox" id="sf-pat-en" onchange="_patToggle()"'+(pat.enabled?' checked':'')+'>  Patrol (auto back-and-forth)</label>';
    h+='<div id="sf-pat-opts" style="'+(pat.enabled?'':'display:none;')+'margin-top:.4em">';
    h+='<label>Pattern</label><select id="sf-pat-pattern" onchange="_patPatternChange()"><option value="pingpong"'+((pat.pattern||'pingpong')==='pingpong'?' selected':'')+'>Ping-Pong</option><option value="circle"'+((pat.pattern)==='circle'?' selected':'')+'>Circle</option><option value="figure8"'+((pat.pattern)==='figure8'?' selected':'')+'>Figure 8</option><option value="square"'+((pat.pattern)==='square'?' selected':'')+'>Square</option></select>';
    h+='<div id="sf-pat-axis-wrap" style="'+((pat.pattern&&pat.pattern!=='pingpong')?'display:none':'')+'"><label>Axis</label><select id="sf-pat-axis"><option value="x"'+((pat.axis||'x')==='x'?' selected':'')+'>Side to side (X)</option><option value="y"'+((pat.axis)==='y'?' selected':'')+'>Front to back (Y)</option><option value="xy"'+((pat.axis)==='xy'?' selected':'')+'>Diagonal (X+Y)</option></select></div>';
    h+='<label>Speed</label><select id="sf-pat-speed" onchange="var c=document.getElementById(\'sf-pat-cyc\');c.style.display=this.value===\'custom\'?\'inline\':\'none\'"><option value="slow"'+((pat.speedPreset)==='slow'?' selected':'')+'>Slow (20s)</option><option value="medium"'+((pat.speedPreset||'medium')==='medium'?' selected':'')+'>Medium (10s)</option><option value="fast"'+((pat.speedPreset)==='fast'?' selected':'')+'>Fast (5s)</option><option value="custom"'+((pat.speedPreset)==='custom'?' selected':'')+'>Custom</option></select>';
    h+=' <input id="sf-pat-cyc" type="number" value="'+(pat.cycleS||10)+'" min="1" max="120" style="width:60px;'+(pat.speedPreset==='custom'?'':'display:none')+'">';
    h+='<label>Range</label><input id="sf-pat-start" type="number" value="'+(pat.startPct||10)+'" min="0" max="100" style="width:50px">% to <input id="sf-pat-end" type="number" value="'+(pat.endPct||90)+'" min="0" max="100" style="width:50px">% of stage';
    h+='<div id="sf-pat-bound-wrap"><label>Bounding Object <span style="color:#64748b;font-size:.75em">(optional)</span></label><select id="sf-pat-bound"><option value="">None (use stage %)</option>';
    (_objects||[]).forEach(function(o){if(o.mobility!=='moving'&&o.name)h+='<option value="'+escapeHtml(o.name)+'"'+((pat.boundingObject===o.name)?' selected':'')+'>'+escapeHtml(o.name)+'</option>';});
    h+='</select></div>';
    h+='<label>Easing</label><select id="sf-pat-ease"><option value="sine"'+((pat.easing||'sine')==='sine'?' selected':'')+'>Smooth (sine)</option><option value="linear"'+((pat.easing)==='linear'?' selected':'')+'>Linear</option></select>';
    var pm=pat.patrolMode||'always';
    h+='<label>Activation</label><select id="sf-pat-mode"><option value="always"'+(pm==='always'?' selected':'')+'>Always</option><option value="on-demand"'+(pm==='on-demand'?' selected':'')+'>On Demand (only when show playing)</option></select>';
    h+='</div></div>';
  }
  h+='<div style="margin-top:.8em;display:flex;gap:.5em">';
  h+='<button class="btn btn-on" onclick="updateObject('+id+')">Save</button>';
  h+='<button class="btn btn-off" onclick="delObject('+id+')">Delete</button>';
  h+='</div>';
  document.getElementById('modal-title').textContent='Edit Object: '+s.name;
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function updateObject(id){
  var lk=document.getElementById('sf-lock');
  var locked=lk&&lk.checked;
  // Preserve objectType from original object
  var origType='custom';_objects.forEach(function(sf){if(sf.id===id)origType=sf.objectType||'custom';});
  var body={
    name:document.getElementById('sf-name').value,
    color:document.getElementById('sf-color').value,
    objectType:origType,
    transform:{
      pos:[parseInt(document.getElementById('sf-x').value)||0,parseInt(document.getElementById('sf-y').value)||0,parseInt(document.getElementById('sf-z').value)||0],
      rot:[0,0,0],
      scale:[parseInt(document.getElementById('sf-w').value)||2000,parseInt(document.getElementById('sf-h').value)||1500,parseInt(document.getElementById('sf-d').value)||100]
    },
    opacity:parseInt(document.getElementById('sf-op').value)||30,
    mobility:(document.getElementById('sf-mob')?document.getElementById('sf-mob').value:null)||'static',
    stageLocked:!!locked,
  };
  var patEn=document.getElementById('sf-pat-en');
  if(patEn&&patEn.checked){
    var sp=document.getElementById('sf-pat-speed');
    var patPattern=document.getElementById('sf-pat-pattern');
    var patBound=document.getElementById('sf-pat-bound');
    body.patrol={enabled:true,
      pattern:patPattern?patPattern.value:'pingpong',
      axis:document.getElementById('sf-pat-axis').value||'x',
      speedPreset:sp?sp.value:'medium',cycleS:parseFloat(document.getElementById('sf-pat-cyc').value)||10,
      startPct:parseInt(document.getElementById('sf-pat-start').value)||10,
      endPct:parseInt(document.getElementById('sf-pat-end').value)||90,
      easing:document.getElementById('sf-pat-ease').value||'sine',
      boundingObject:(patBound&&patBound.value)||'',
      patrolMode:(document.getElementById('sf-pat-mode')?document.getElementById('sf-pat-mode').value:'always')};
  }
  // Delete and recreate (simple approach since objects API doesn't have PUT)
  ra('DELETE','/api/objects/'+id,null,function(){
    ra('POST','/api/objects',body,function(r){
      closeModal();loadObjects();s3dLoadChildren();
    });
  });
}

function delObject(id){
  if(!confirm('Delete this object?'))return;
  ra('DELETE','/api/objects/'+id,null,function(){
    closeModal();loadObjects();s3dLoadChildren();
  });
}

// ── Phase 3: Spatial Effects ────────────────────────────────────────────────
var _spatialFx=[];

function loadSpatialFx(cb){
  ra('GET','/api/spatial-effects',null,function(d){_spatialFx=d||[];renderSfxList();if(cb)cb();});
}

function renderSfxList(){
  var el=document.getElementById('sfx-list');if(!el)return;
  if(!_spatialFx.length){el.innerHTML='<p style="color:#555;font-size:.85em">No spatial effects yet.</p>';return;}
  var h='<table class="tbl" style="max-width:700px"><tr><th>Name</th><th>Category</th><th>Shape</th><th>Colour</th><th>Actions</th></tr>';
  _spatialFx.forEach(function(fx){
    var col=rgb2h(fx.r||0,fx.g||0,fx.b||0);
    var shape=fx.category==='fixture-local'?('Action #'+(fx.actionType||0)):((fx.shape||'sphere'));
    h+='<tr><td>'+escapeHtml(fx.name)+'</td>';
    h+='<td><span class="badge '+(fx.category==='spatial-field'?'bon':'boff')+'">'+fx.category+'</span></td>';
    h+='<td>'+shape+'</td>';
    h+='<td><span style="display:inline-block;width:16px;height:16px;border-radius:3px;background:'+col+';vertical-align:middle"></span> '+col+'</td>';
    h+='<td><button class="btn" onclick="editSpatialFx('+fx.id+')" style="background:#446;color:#fff;font-size:.75em">Edit</button> ';
    h+='<button class="btn btn-off" onclick="delSpatialFx('+fx.id+')" style="font-size:.75em">Del</button></td></tr>';
  });
  h+='</table>';el.innerHTML=h;
}

function newSpatialFx(){
  var h='<label>Name</label><input id="sfx-name" style="width:100%">';
  h+='<label>Category</label><select id="sfx-cat" onchange="sfxCatChange()" style="width:100%"><option value="spatial-field">Spatial Field</option><option value="fixture-local">Fixture-Local</option></select>';
  h+='<div id="sfx-field-opts">';
  h+='<label>Shape</label><select id="sfx-shape"><option>sphere</option><option>plane</option><option>box</option></select>';
  h+='<label>Colour</label><div style="display:flex;gap:.3em"><input id="sfx-r" type="number" min="0" max="255" value="255" style="width:60px" placeholder="R"><input id="sfx-g" type="number" min="0" max="255" value="0" style="width:60px" placeholder="G"><input id="sfx-b" type="number" min="0" max="255" value="0" style="width:60px" placeholder="B"></div>';
  h+='<label>Radius / Size (mm)</label><input id="sfx-radius" type="number" value="1000" style="width:120px">';
  h+='<label>Motion Start (x,y,z mm)</label><div style="display:flex;gap:.3em"><input id="sfx-sx" type="number" value="0" style="width:80px"><input id="sfx-sy" type="number" value="0" style="width:80px"><input id="sfx-sz" type="number" value="0" style="width:80px"></div>';
  h+='<label>Motion End (x,y,z mm)</label><div style="display:flex;gap:.3em"><input id="sfx-ex" type="number" value="5000" style="width:80px"><input id="sfx-ey" type="number" value="0" style="width:80px"><input id="sfx-ez" type="number" value="0" style="width:80px"></div>';
  h+='<label>Duration (s)</label><input id="sfx-dur" type="number" value="5" min="0.1" step="0.1" style="width:80px">';
  h+='<label>Easing</label><select id="sfx-ease"><option>linear</option><option>ease-in</option><option>ease-out</option><option>ease-in-out</option></select>';
  h+='<label>Blend</label><select id="sfx-blend"><option>replace</option><option>add</option><option>multiply</option><option>screen</option></select>';
  h+='</div>';
  h+='<div id="sfx-local-opts" style="display:none"><label>Action Type</label><select id="sfx-atype">';
  var aNames=['Blackout','Solid','Fade','Breathe','Chase','Rainbow','Fire','Comet','Twinkle','Strobe','Wipe','Scanner','Sparkle','Gradient','DMX Scene','Pan/Tilt Move','Gobo Select','Colour Wheel','Track'];
  aNames.forEach(function(n,i){h+='<option value="'+i+'">'+n+'</option>';});
  h+='</select></div>';
  h+='<div style="margin-top:1em"><button class="btn btn-on" onclick="saveSpatialFx()">Create</button></div>';
  document.getElementById('modal-title').textContent='New Spatial Effect';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function sfxCatChange(){
  var cat=document.getElementById('sfx-cat').value;
  document.getElementById('sfx-field-opts').style.display=cat==='spatial-field'?'block':'none';
  document.getElementById('sfx-local-opts').style.display=cat==='fixture-local'?'block':'none';
}

function saveSpatialFx(){
  var body={
    name:document.getElementById('sfx-name').value,
    category:document.getElementById('sfx-cat').value,
  };
  if(body.category==='spatial-field'){
    body.shape=document.getElementById('sfx-shape').value;
    body.r=parseInt(document.getElementById('sfx-r').value)||0;
    body.g=parseInt(document.getElementById('sfx-g').value)||0;
    body.b=parseInt(document.getElementById('sfx-b').value)||0;
    body.size={radius:parseInt(document.getElementById('sfx-radius').value)||1000};
    body.motion={
      startPos:[parseInt(document.getElementById('sfx-sx').value)||0,parseInt(document.getElementById('sfx-sy').value)||0,parseInt(document.getElementById('sfx-sz').value)||0],
      endPos:[parseInt(document.getElementById('sfx-ex').value)||0,parseInt(document.getElementById('sfx-ey').value)||0,parseInt(document.getElementById('sfx-ez').value)||0],
      durationS:parseFloat(document.getElementById('sfx-dur').value)||5,
      easing:document.getElementById('sfx-ease').value
    };
    body.blend=document.getElementById('sfx-blend').value;
  } else {
    body.actionType=parseInt(document.getElementById('sfx-atype').value)||0;
  }
  ra('POST','/api/spatial-effects',body,function(r){
    closeModal();if(r&&r.ok)loadSpatialFx();
  });
}

function editSpatialFx(id){
  var fx=null;_spatialFx.forEach(function(f){if(f.id===id)fx=f;});
  if(!fx)return;
  // Re-use create modal with pre-filled values
  newSpatialFx();
  setTimeout(function(){
    document.getElementById('modal-title').textContent='Edit: '+fx.name;
    document.getElementById('sfx-name').value=fx.name||'';
    document.getElementById('sfx-cat').value=fx.category||'spatial-field';
    sfxCatChange();
    if(fx.category==='spatial-field'){
      document.getElementById('sfx-shape').value=fx.shape||'sphere';
      document.getElementById('sfx-r').value=fx.r||0;
      document.getElementById('sfx-g').value=fx.g||0;
      document.getElementById('sfx-b').value=fx.b||0;
      document.getElementById('sfx-radius').value=(fx.size||{}).radius||1000;
      var m=fx.motion||{};var sp=m.startPos||[0,0,0];var ep=m.endPos||[0,0,0];
      document.getElementById('sfx-sx').value=sp[0];document.getElementById('sfx-sy').value=sp[1];document.getElementById('sfx-sz').value=sp[2];
      document.getElementById('sfx-ex').value=ep[0];document.getElementById('sfx-ey').value=ep[1];document.getElementById('sfx-ez').value=ep[2];
      document.getElementById('sfx-dur').value=m.durationS||5;
      document.getElementById('sfx-ease').value=m.easing||'linear';
      document.getElementById('sfx-blend').value=fx.blend||'replace';
    } else {
      document.getElementById('sfx-atype').value=fx.actionType||0;
    }
    // Override save to PUT
    var saveBtn=document.querySelector('#modal .btn-on');
    if(saveBtn){saveBtn.textContent='Update';saveBtn.onclick=function(){
      var body={name:document.getElementById('sfx-name').value,category:document.getElementById('sfx-cat').value};
      if(body.category==='spatial-field'){
        body.shape=document.getElementById('sfx-shape').value;
        body.r=parseInt(document.getElementById('sfx-r').value)||0;body.g=parseInt(document.getElementById('sfx-g').value)||0;body.b=parseInt(document.getElementById('sfx-b').value)||0;
        body.size={radius:parseInt(document.getElementById('sfx-radius').value)||1000};
        body.motion={startPos:[parseInt(document.getElementById('sfx-sx').value)||0,parseInt(document.getElementById('sfx-sy').value)||0,parseInt(document.getElementById('sfx-sz').value)||0],
          endPos:[parseInt(document.getElementById('sfx-ex').value)||0,parseInt(document.getElementById('sfx-ey').value)||0,parseInt(document.getElementById('sfx-ez').value)||0],
          durationS:parseFloat(document.getElementById('sfx-dur').value)||5,easing:document.getElementById('sfx-ease').value};
        body.blend=document.getElementById('sfx-blend').value;
      } else {body.actionType=parseInt(document.getElementById('sfx-atype').value)||0;}
      ra('PUT','/api/spatial-effects/'+id,body,function(r){closeModal();if(r&&r.ok)loadSpatialFx();});
    };}
  },50);
}

function delSpatialFx(id){
  if(!confirm('Delete this spatial effect?'))return;
  ra('DELETE','/api/spatial-effects/'+id,null,function(){loadSpatialFx();});
}
