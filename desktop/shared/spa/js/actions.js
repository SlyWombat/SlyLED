/** actions.js — Actions library: CRUD, action editor modal, type-specific fields. Extracted from app.js Phase 2. */
// ── Actions library ──────────────────────────────────────────────────────────
var _acts=[];
var _typeNames=['Blackout','Solid','Fade','Breathe','Chase','Rainbow','Fire','Comet','Twinkle','Strobe','Color Wipe','Scanner','Sparkle','Gradient','DMX Scene','Pan/Tilt Move','Gobo Select','Color Wheel','Track'];
var _dirNames=['East','North','West','South'];
var _palNames=['Classic','Ocean','Lava','Forest','Party','Heat','Cool','Pastel'];
function rgb2h(r,g,b){return'#'+('0'+r.toString(16)).slice(-2)+('0'+g.toString(16)).slice(-2)+('0'+b.toString(16)).slice(-2);}
function h2r(h){return{r:parseInt(h.slice(1,3),16),g:parseInt(h.slice(3,5),16),b:parseInt(h.slice(5,7),16)};}

// #301 — filter/sort state lives in-memory only; it's a browsing aid,
// not part of the show data.
var _actFilter={q:'',type:null,sort:'manual'};

function loadActions(){
  loadSpatialFx();
  ra('GET','/api/actions',null,function(d){
    _acts=d||[];
    _actRenderTypeChips();
    _actRefilter();
  });
}

// Build the type-filter chip row. "All" plus one chip per type currently
// present in the library (so we don't show chips for types nobody uses).
function _actRenderTypeChips(){
  var el=document.getElementById('act-type-chips');if(!el)return;
  var present={};(_acts||[]).forEach(function(a){present[a.type]=1;});
  var h=_actChip(null,'All');
  Object.keys(present).map(Number).sort(function(x,y){return x-y;}).forEach(function(t){
    h+=_actChip(t,_typeNames[t]||('Type '+t));
  });
  el.innerHTML=h;
}
function _actChip(t,label){
  var active=(_actFilter.type===t);
  var style=active
    ?'background:#14532d;color:#86efac;border:1px solid #22c55e'
    :'background:#1e293b;color:#94a3b8;border:1px solid #334155';
  return '<button class="btn" onclick="_actFilterType('+(t===null?'null':t)+')" '
    +'style="padding:.18em .55em;font-size:.72em;'+style+'">'+escapeHtml(label)+'</button>';
}
function _actFilterType(t){_actFilter.type=t;_actRenderTypeChips();_actRefilter();}

// Re-render the table honouring the current search/type/sort filter.
// Called on every input event from the search box, chip click, or sort
// dropdown — cheap since the full action list already lives in _acts.
function _actRefilter(){
  var el=document.getElementById('act-list');if(!el)return;
  var q=(document.getElementById('act-search')||{}).value;
  var s=(document.getElementById('act-sort')||{}).value;
  _actFilter.q=(q||'').trim().toLowerCase();
  _actFilter.sort=s||'manual';
  var list=(_acts||[]).filter(function(a){
    if(_actFilter.q&&(a.name||'').toLowerCase().indexOf(_actFilter.q)<0)return false;
    if(_actFilter.type!=null&&a.type!==_actFilter.type)return false;
    return true;
  });
  if(_actFilter.sort==='name')list.sort(function(a,b){return(a.name||'').localeCompare(b.name||'');});
  else if(_actFilter.sort==='type')list.sort(function(a,b){return(a.type||0)-(b.type||0);});
  else if(_actFilter.sort==='newest')list.sort(function(a,b){return(b.id||0)-(a.id||0);});
  if(!(_acts||[]).length){
    el.innerHTML='<p style="color:#888">No actions defined yet. Click + New Action to create one.</p>';
    return;
  }
  if(!list.length){
    el.innerHTML='<p style="color:#888">No actions match — clear the search or pick a different type.</p>';
    return;
  }
  var h='<table class="tbl" style="max-width:700px"><tr><th>Name</th><th>Type</th><th>Colour</th><th>Details</th><th>Actions</th></tr>';
  list.forEach(function(a){
    var col=a.type>0?rgb2h(a.r||0,a.g||0,a.b||0):'#000';
    var scopeLbl='';
    if(a.scope==='canvas')scopeLbl='<span style="color:#6af;font-size:.8em"> [canvas]</span>';
    else if(a.scope==='performer-selected')scopeLbl='<span style="color:#fa6;font-size:.8em"> [selected]</span>';
    var det='';
    function _ms2s(v){return((v||0)/1000).toFixed(2)+'s';}
    if(a.type===2)det=_ms2s(a.speedMs)+' → '+rgb2h(a.r2||0,a.g2||0,a.b2||0);
    if(a.type===3)det='Period:'+_ms2s(a.periodMs)+' Min:'+(a.minBri||0)+'%';
    if(a.type===4)det=(_dirNames[a.direction]||'E')+' '+_ms2s(a.speedMs)+' gap:'+(a.spacing||3);
    if(a.type===5)det=(_palNames[a.paletteId]||'Classic')+' '+_ms2s(a.speedMs);
    if(a.type===6)det='Cool:'+(a.cooling||55)+' Spark:'+(a.sparking||120);
    if(a.type===7)det=(_dirNames[a.direction]||'E')+' tail:'+(a.tailLen||10)+' '+_ms2s(a.speedMs);
    if(a.type===8)det='Den:'+(a.density||3)+' Fade:'+(a.fadeSpeed||15);
    if(a.type===14)det='Dim:'+(a.dimmer||0)+' P:'+(a.pan||0).toFixed(2)+' T:'+(a.tilt||0).toFixed(2)+(a.gobo?' G:'+a.gobo:'')+(a.strobe?' S:'+a.strobe:'');
    if(a.type===15)det='P:'+((a.panStart||0).toFixed(2))+'→'+((a.panEnd||1).toFixed(2))+' T:'+((a.tiltStart||0.5).toFixed(2))+'→'+((a.tiltEnd||0.5).toFixed(2));
    if(a.type===16)det='Gobo:'+(a.gobo||0);
    if(a.type===17)det='Color:'+(a.colorWheel||0);
    h+='<tr><td><b>'+escapeHtml(a.name)+'</b>'+scopeLbl+'</td><td>'+(_typeNames[a.type]||a.type)+'</td>';
    h+='<td><span style="display:inline-block;width:24px;height:16px;border-radius:3px;background:'+col+';border:1px solid #555;vertical-align:middle"></span> '+col+'</td>';
    h+='<td style="font-size:.82em;color:#888">'+det+'</td>';
    h+='<td><button class="btn" onclick="editAction('+a.id+')" style="background:#446;color:#fff;padding:.2em .6em">Edit</button>';
    h+=' <button class="btn" onclick="duplicateAction('+a.id+')" style="background:#334155;color:#cbd5e1;padding:.2em .6em" title="Clone this action as a starting point">Dup</button>';
    h+=' <button class="btn btn-off" onclick="delAction('+a.id+')" style="padding:.2em .6em">Del</button></td></tr>';
  });
  el.innerHTML=h+'</table>';
}

// #301 — clone an action server-side so the user can tweak a copy
// without editing the original. POSTs a full clone with " (copy)"
// appended to the name; the server assigns a fresh id on insert.
function duplicateAction(id){
  var src=null;(_acts||[]).forEach(function(x){if(x.id===id)src=x;});
  if(!src)return;
  var copy={};for(var k in src)if(src.hasOwnProperty(k))copy[k]=src[k];
  delete copy.id;
  copy.name=(src.name||'Action')+' (copy)';
  ra('POST','/api/actions',copy,function(r){
    if(r&&r.ok){
      if(typeof toastSuccess==='function')toastSuccess('Duplicated: '+copy.name);
      loadActions();
    }else{
      if(typeof toastError==='function')toastError('Duplicate failed: '+(r&&r.err||'unknown'));
    }
  });
}

function newAction(){_showActModal(null);}
function editAction(id){var a=null;_acts.forEach(function(x){if(x.id===id)a=x;});if(a)_showActModal(a);}

function _showActModal(a){
  var isNew=!a;
  var t=a?a.type:1;
  var sc=a?a.scope||'performer':'performer';
  var selIds=a&&a.targetIds?a.targetIds:[];
  var h='<label>Name</label><input id="ae-nm" value="'+escapeHtml(a?a.name:'')+'" style="width:240px" placeholder="e.g. Red Wipe">';
  h+='<label>Scope</label><select id="ae-scope" onchange="_aeScopeChg()">';
  h+='<option value="performer"'+(sc==='performer'?' selected':'')+'>All Fixtures</option>';
  h+='<option value="performer-selected"'+(sc==='performer-selected'?' selected':'')+'>Selected Fixtures</option>';
  h+='<option value="canvas"'+(sc==='canvas'?' selected':'')+'>Canvas (spatial sweep)</option>';
  h+='</select>';
  // Selected fixtures picker
  h+='<div id="ae-perf-select"'+(sc==='performer-selected'?'':' style="display:none"')+'>';
  h+='<label>Select Fixtures</label><div id="ae-perf-list" style="max-height:120px;overflow-y:auto;border:1px solid #333;border-radius:4px;padding:.3em;background:#1a1a1a">';
  h+='<p style="color:#888;font-size:.8em">Loading...</p></div></div>';
  // Canvas options
  h+='<div id="ae-canvas-opts"'+(sc==='canvas'?'':' style="display:none"')+'>';
  h+='<label>Canvas Effect</label><select id="ae-ceffect"><option value="wipe" selected>Wipe</option></select>';
  h+='<label>Direction</label><select id="ae-csdir">';
  for(var d=0;d<4;d++)h+='<option value="'+d+'"'+(a&&a.direction===d?' selected':'')+'>'+_dirNames[d]+'</option>';
  h+='</select></div>';
  h+='<label>Type</label><select id="ae-tp" onchange="_aeTypeChg()">';
  for(var i=0;i<_typeNames.length;i++)h+='<option value="'+i+'"'+(t===i?' selected':'')+'>'+_typeNames[i]+'</option>';
  h+='</select>';
  // Colour picker (all types except Blackout and Rainbow)
  h+='<div id="ae-cr"'+(t===0||t===5?' style="display:none"':'')+'><label>Colour</label><input type="color" id="ae-cl" value="'+(a&&t>0?rgb2h(a.r||0,a.g||0,a.b||0):'#ff0000')+'"></div>';
  // Fade: second colour
  h+='<div id="ae-fade"'+(t===2?'':' style="display:none"')+'>';
  h+='<label>Colour 2</label><input type="color" id="ae-c2" value="'+(a?rgb2h(a.r2||0,a.g2||0,a.b2||0):'#0000ff')+'">';
  h+='<label>Speed (s)</label><input type="number" id="ae-spd" value="'+((a?a.speedMs||1000:1000)/1000)+'" min="0.1" max="30" step="0.1" style="width:80px">';
  h+='</div>';
  // Breathe: period + min brightness
  h+='<div id="ae-breathe"'+(t===3?'':' style="display:none"')+'>';
  h+='<label>Period (s)</label><input type="number" id="ae-per" value="'+((a?a.periodMs||2000:2000)/1000)+'" min="0.2" max="30" step="0.1" style="width:80px">';
  h+=' <label style="display:inline;margin-left:.5em">Min Brightness %</label><input type="number" id="ae-mbr" value="'+(a?a.minBri||0:0)+'" min="0" max="100" style="width:50px">';
  h+='</div>';
  // Chase: speed + spacing + direction
  h+='<div id="ae-chase"'+(t===4?'':' style="display:none"')+'>';
  h+='<label>Speed (s per shift)</label><input type="number" id="ae-cspd" value="'+((a?a.speedMs||100:100)/1000)+'" min="0.01" max="5" step="0.01" style="width:80px">';
  h+=' <label style="display:inline;margin-left:.5em">Spacing</label><input type="number" id="ae-cspc" value="'+(a?a.spacing||3:3)+'" min="2" max="20" style="width:50px">';
  h+='<label>Direction</label><select id="ae-cdir">';
  for(var d=0;d<4;d++)h+='<option value="'+d+'"'+(a&&a.direction===d?' selected':'')+'>'+_dirNames[d]+'</option>';
  h+='</select></div>';
  // Rainbow: speed + palette + direction
  h+='<div id="ae-rainbow"'+(t===5?'':' style="display:none"')+'>';
  h+='<label>Speed (s)</label><input type="number" id="ae-rspd" value="'+((a?a.speedMs||50:50)/1000)+'" min="0.001" max="5" step="0.01" style="width:80px">';
  h+='<label>Palette</label><select id="ae-pal">';
  for(var p=0;p<8;p++)h+='<option value="'+p+'"'+(a&&a.paletteId===p?' selected':'')+'>'+_palNames[p]+'</option>';
  h+='</select>';
  h+='<label>Direction</label><select id="ae-rdir">';
  for(var d=0;d<4;d++)h+='<option value="'+d+'"'+(a&&a.direction===d?' selected':'')+'>'+_dirNames[d]+'</option>';
  h+='</select></div>';
  // Fire: speed + cooling + sparking
  h+='<div id="ae-fire"'+(t===6?'':' style="display:none"')+'>';
  h+='<label>Speed (s)</label><input type="number" id="ae-fspd" value="'+((a?a.speedMs||15:15)/1000)+'" min="0.001" max="1" step="0.001" style="width:80px">';
  h+=' <label style="display:inline;margin-left:.5em">Cooling</label><input type="number" id="ae-cool" value="'+(a?a.cooling||55:55)+'" min="1" max="255" style="width:50px">';
  h+=' <label style="display:inline;margin-left:.5em">Sparking</label><input type="number" id="ae-spark" value="'+(a?a.sparking||120:120)+'" min="1" max="255" style="width:50px">';
  h+='</div>';
  // Comet: speed + tail + direction + decay
  h+='<div id="ae-comet"'+(t===7?'':' style="display:none"')+'>';
  h+='<label>Speed (s)</label><input type="number" id="ae-mspd" value="'+((a?a.speedMs||30:30)/1000)+'" min="0.001" max="5" step="0.01" style="width:80px">';
  h+=' <label style="display:inline;margin-left:.5em">Tail</label><input type="number" id="ae-tail" value="'+(a?a.tailLen||10:10)+'" min="1" max="50" style="width:50px">';
  h+=' <label style="display:inline;margin-left:.5em">Decay %</label><input type="number" id="ae-dec" value="'+(a?a.decay||80:80)+'" min="1" max="99" style="width:50px">';
  h+='<label>Direction</label><select id="ae-mdir">';
  for(var d=0;d<4;d++)h+='<option value="'+d+'"'+(a&&a.direction===d?' selected':'')+'>'+_dirNames[d]+'</option>';
  h+='</select></div>';
  // Twinkle: spawn + density + fade
  h+='<div id="ae-twinkle"'+(t===8?'':' style="display:none"')+'>';
  h+='<label>Spawn (s)</label><input type="number" id="ae-tspn" value="'+((a?a.spawnMs||50:50)/1000)+'" min="0.001" max="5" step="0.01" style="width:80px">';
  h+=' <label style="display:inline;margin-left:.5em">Density</label><input type="number" id="ae-tden" value="'+(a?a.density||3:3)+'" min="1" max="20" style="width:50px">';
  h+=' <label style="display:inline;margin-left:.5em">Fade Speed</label><input type="number" id="ae-tfad" value="'+(a?a.fadeSpeed||15:15)+'" min="1" max="100" style="width:50px">';
  h+='</div>';
  // DMX Scene: dimmer, pan, tilt, strobe, gobo
  var isDmx=t>=14&&t<=17;
  h+='<div id="ae-dmx"'+(isDmx?'':' style="display:none"')+'>';
  h+='<label>Dimmer (0–255)</label><input type="number" id="ae-dimmer" value="'+(a?a.dimmer||255:255)+'" min="0" max="255" style="width:80px">';
  h+='<label>Pan (0.0–1.0)</label><input type="number" id="ae-pan" value="'+(a?a.pan||0.5:0.5)+'" min="0" max="1" step="0.01" style="width:80px">';
  h+=' <label style="display:inline;margin-left:.5em">Tilt (0.0–1.0)</label><input type="number" id="ae-tilt" value="'+(a?a.tilt||0.5:0.5)+'" min="0" max="1" step="0.01" style="width:80px">';
  h+='<label>Strobe (0=off, 1–255=slow→fast)</label><input type="number" id="ae-strobe" value="'+(a?a.strobe||0:0)+'" min="0" max="255" style="width:80px">';
  h+='<label>Gobo (0=open, 1+=gobo index)</label><input type="number" id="ae-gobo" value="'+(a?a.gobo||0:0)+'" min="0" max="255" style="width:80px">';
  h+='<label>Color Wheel (0=open, 1+=color index)</label><input type="number" id="ae-colorwheel" value="'+(a?a.colorWheel||0:0)+'" min="0" max="255" style="width:80px">';
  h+='<label>Prism (0=off)</label><input type="number" id="ae-prism" value="'+(a?a.prism||0:0)+'" min="0" max="255" style="width:80px">';
  h+='</div>';
  // Pan/Tilt Move: start/end pan/tilt, speed
  h+='<div id="ae-ptmove"'+(t===15?'':' style="display:none"')+'>';
  var ptSP=a&&a.ptStartPos?a.ptStartPos:[0,0,0];
  var ptEP=a&&a.ptEndPos?a.ptEndPos:[0,0,0];
  h+='<label>Start Position (mm)</label>';
  h+='<span style="display:inline-flex;align-items:center;gap:.4em;flex-wrap:wrap">';
  h+='X <input type="number" id="ae-pt-sx" value="'+ptSP[0]+'" step="1" style="width:70px">';
  h+='Y <input type="number" id="ae-pt-sy" value="'+ptSP[1]+'" step="1" style="width:70px">';
  h+='Z <input type="number" id="ae-pt-sz" value="'+ptSP[2]+'" step="1" style="width:70px">';
  h+='</span>';
  h+='<label>End Position (mm)</label>';
  h+='<span style="display:inline-flex;align-items:center;gap:.4em;flex-wrap:wrap">';
  h+='X <input type="number" id="ae-pt-ex" value="'+ptEP[0]+'" step="1" style="width:70px">';
  h+='Y <input type="number" id="ae-pt-ey" value="'+ptEP[1]+'" step="1" style="width:70px">';
  h+='Z <input type="number" id="ae-pt-ez" value="'+ptEP[2]+'" step="1" style="width:70px">';
  h+='</span>';
  h+='<label>Speed (s)</label><input type="number" id="ae-pt-spd" value="'+((a?a.speedMs||5000:5000)/1000)+'" min="0.1" max="60" step="0.1" style="width:80px">';
  h+='</div>';
  // Track: follow moving objects
  h+='<div id="ae-track"'+(t===18?'':' style="display:none"')+'>';
  h+='<label>Target Objects</label><p style="color:#64748b;font-size:.78em;margin:0 0 .3em">Select objects to track. Leave empty to auto-track all moving objects.</p>';
  h+='<div id="ae-track-objs" style="max-height:140px;overflow-y:auto;border:1px solid #333;border-radius:4px;padding:.3em;background:#1a1a1a">';
  var trackIds=a&&a.trackObjectIds?a.trackObjectIds:[];
  (_objects||[]).forEach(function(o){
    var chk=trackIds.indexOf(o.id)>=0?' checked':'';
    var badge=o.mobility==='moving'?'<span style="color:#38bdf8;font-size:.7em"> (moving)</span>':'';
    h+='<label style="display:block;cursor:pointer;padding:.15em 0"><input type="checkbox" class="ae-track-cb" value="'+o.id+'"'+chk+'> '+escapeHtml(o.name)+badge+'</label>';
  });
  h+='<div id="ae-track-temporal"></div>';
  if(!(_objects||[]).length)h+='<p id="ae-track-empty" style="color:#555;font-size:.8em">No objects defined. Add objects on the Layout tab, or camera-tracked people appear live below.</p>';
  h+='</div>';
  h+='<label>Cycle Time (ms)</label><input type="number" id="ae-trk-cycle" value="'+(a?a.trackCycleMs||2000:2000)+'" min="100" max="60000" style="width:100px">';
  var tOff=a&&a.trackOffset?a.trackOffset:[0,0,0];
  h+='<label>Offset X (mm)</label><input type="number" id="ae-trk-ox" value="'+tOff[0]+'" style="width:80px">';
  h+=' <label style="display:inline;margin-left:.5em">Y (mm)</label><input type="number" id="ae-trk-oy" value="'+tOff[1]+'" style="width:80px">';
  h+=' <label style="display:inline;margin-left:.5em">Z (mm)</label><input type="number" id="ae-trk-oz" value="'+tOff[2]+'" style="width:80px">';
  h+='<div style="margin-top:.4em"><label style="cursor:pointer"><input type="checkbox" id="ae-trk-spread"'+(a&&a.trackAutoSpread?' checked':'')+'>  Auto-spread across targets</label></div>';
  h+='<div style="margin-top:.3em"><label style="cursor:pointer"><input type="checkbox" id="ae-trk-fixed"'+(a&&a.trackFixedAssignment?' checked':'')+'>  Fixed assignment (1:1 — extra targets ignored)</label></div>';
  h+='</div>';
  // WLED Overrides section (hidden by default, shown if WLED devices exist)
  h+='<div id="ae-wled-section" style="display:none;margin-top:1em;border:1px solid #444;border-radius:6px;padding:.6em">';
  h+='<div onclick="var el=document.getElementById(\'ae-wled-inner\');el.style.display=el.style.display===\'none\'?\'block\':\'none\'" style="cursor:pointer;font-weight:bold;color:#f60">';
  h+='&#9660; WLED Overrides <span style="font-weight:normal;font-size:.8em;color:#888">(optional — override auto-mapped effect)</span></div>';
  h+='<div id="ae-wled-inner" style="display:none;padding-top:.5em">';
  h+='<label>WLED Effect</label><select id="ae-wled-fx" style="width:260px"><option value="">Auto (mapped from type)</option></select>';
  h+='<label>WLED Palette</label><select id="ae-wled-pal" style="width:260px"><option value="">Auto</option></select>';
  h+='<label>Segment</label><select id="ae-wled-seg" style="width:260px"><option value="">All Segments</option></select>';
  h+='</div></div>';
  h+='<div style="margin-top:1em"><button class="btn btn-on" id="ae-save" onclick="_aeSubmit('+(isNew?-1:a.id)+',this)">Save Action</button></div>';
  document.getElementById('modal-title').textContent=isNew?'New Action':'Edit Action';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  _aePerfIds=selIds.slice();
  if(sc==='performer-selected')_loadPerfPicker();
  // Refresh objects + fetch temporals for Track action target list
  if(t===18){
    loadObjects(function(){
      // Re-render the target checkboxes with fresh _objects
      var el=document.getElementById('ae-track-objs');
      if(el){
        var h2='';
        (_objects||[]).forEach(function(o){
          var chk=trackIds.indexOf(o.id)>=0?' checked':'';
          var badge=o.mobility==='moving'?'<span style="color:#38bdf8;font-size:.7em"> (moving)</span>':'';
          h2+='<label style="display:block;cursor:pointer;padding:.15em 0"><input type="checkbox" class="ae-track-cb" value="'+o.id+'"'+chk+'> '+escapeHtml(o.name)+badge+'</label>';
        });
        if(!(_objects||[]).length)h2+='<p style="color:#555;font-size:.8em">No objects defined.</p>';
        h2+='<div id="ae-track-temporal"></div>';
        el.innerHTML=h2;
      }
      _aeLoadTemporalObjects(trackIds);
    });
  }
  // Detect WLED devices and populate overrides
  ra('GET','/api/children',null,function(children){
    var wc=null;
    if(children)children.forEach(function(c){if(c.type==='wled'&&!wc)wc=c;});
    if(!wc)return;
    document.getElementById('ae-wled-section').style.display='block';
    ra('GET','/api/wled/effects/'+wc.id,null,function(fx){
      if(!fx||!fx.length)return;
      var sel=document.getElementById('ae-wled-fx');
      fx.forEach(function(name,i){
        var o=document.createElement('option');o.value=i;o.textContent=i+': '+name;
        if(a&&a.wledFxOverride===i)o.selected=true;
        sel.appendChild(o);
      });
    });
    ra('GET','/api/wled/palettes/'+wc.id,null,function(pal){
      if(!pal||!pal.length)return;
      var sel=document.getElementById('ae-wled-pal');
      pal.forEach(function(name,i){
        var o=document.createElement('option');o.value=i;o.textContent=i+': '+name;
        if(a&&a.wledPalOverride===i)o.selected=true;
        sel.appendChild(o);
      });
    });
    ra('GET','/api/wled/segments/'+wc.id,null,function(segs){
      if(!segs||!segs.length||segs.length<2)return;
      var sel=document.getElementById('ae-wled-seg');
      segs.forEach(function(s){
        var o=document.createElement('option');o.value=s.id;
        o.textContent='Segment '+s.id+' (LEDs '+s.start+'\u2013'+s.stop+')';
        if(a&&a.wledSegId===s.id)o.selected=true;
        sel.appendChild(o);
      });
    });
  });
}

function _aeLoadTemporalObjects(selIds){
  ra('GET','/api/objects',null,function(objs){
    var el=document.getElementById('ae-track-temporal');
    if(!el||!objs)return;
    var temps=objs.filter(function(o){return o._temporal&&o.mobility==='moving';});
    if(!temps.length)return;
    // Hide the "no moving objects" placeholder if present
    var emp=document.getElementById('ae-track-empty');if(emp)emp.style.display='none';
    var h='<div style="border-top:1px solid #334155;margin-top:.3em;padding-top:.3em">';
    h+='<span style="color:#f472b6;font-size:.75em;font-weight:bold">LIVE</span>';
    temps.forEach(function(o){
      var chk=selIds.indexOf(o.id)>=0?' checked':'';
      h+='<label style="display:block;cursor:pointer;padding:.15em 0"><input type="checkbox" class="ae-track-cb" value="'+o.id+'"'+chk+'> ';
      h+='<span style="color:#f472b6">'+escapeHtml(o.name||'Person')+'</span>';
      h+=' <span style="color:#555;font-size:.75em">('+o.objectType+')</span></label>';
    });
    h+='</div>';
    el.innerHTML=h;
  });
}

function _aeScopeChg(){
  var s=document.getElementById('ae-scope').value;
  document.getElementById('ae-canvas-opts').style.display=s==='canvas'?'block':'none';
  document.getElementById('ae-perf-select').style.display=s==='performer-selected'?'block':'none';
  if(s==='performer-selected')_loadPerfPicker();
}

function _loadPerfPicker(){
  ra('GET','/api/fixtures',null,function(d){
    var el=document.getElementById('ae-perf-list');if(!el)return;
    if(!d||!d.length){el.innerHTML='<p style="color:#888;font-size:.8em">No fixtures registered.</p>';return;}
    var h='';
    d.forEach(function(f){
      var checked=(_aePerfIds&&_aePerfIds.indexOf(f.id)>=0)?' checked':'';
      h+='<label style="display:flex;align-items:center;gap:.3em;font-size:.85em;padding:.15em 0">';
      h+='<input type="checkbox" class="ae-perf-cb" value="'+f.id+'"'+checked+'> ';
      h+=escapeHtml(f.name)+'</label>';
    });
    el.innerHTML=h;
  });
}
var _aePerfIds=[];

function _aeTypeChg(){
  var t=parseInt(document.getElementById('ae-tp').value);
  document.getElementById('ae-cr').style.display=(t===0||t===5)?'none':'block';
  document.getElementById('ae-fade').style.display=(t===2||t===13)?'block':'none';  // Fade + Gradient use 2nd color
  document.getElementById('ae-breathe').style.display=t===3?'block':'none';
  document.getElementById('ae-chase').style.display=(t===4||t===10)?'block':'none'; // Chase + Wipe use direction
  document.getElementById('ae-rainbow').style.display=t===5?'block':'none';
  document.getElementById('ae-fire').style.display=t===6?'block':'none';
  document.getElementById('ae-comet').style.display=(t===7||t===11)?'block':'none'; // Comet + Scanner
  document.getElementById('ae-twinkle').style.display=(t===8||t===12)?'block':'none'; // Twinkle + Sparkle
  document.getElementById('ae-dmx').style.display=(t>=14&&t<=17)?'block':'none';
  document.getElementById('ae-ptmove').style.display=t===15?'block':'none';
  document.getElementById('ae-track').style.display=t===18?'block':'none';
}

function _aeSubmit(id,btn){
  var nm=document.getElementById('ae-nm').value.trim();if(!nm){alert('Name required');return;}
  var t=parseInt(document.getElementById('ae-tp').value)||0;
  var col=t>0&&t!==5?h2r(document.getElementById('ae-cl').value):{r:0,g:0,b:0};
  var scope=document.getElementById('ae-scope').value;
  var body={name:nm,type:t,r:col.r,g:col.g,b:col.b,scope:scope};
  if(scope==='performer-selected'){
    var cbs=document.querySelectorAll('.ae-perf-cb:checked');
    var ids=[];cbs.forEach(function(cb){ids.push(parseInt(cb.value));});
    body.targetIds=ids;
  }
  if(scope==='canvas'){
    body.canvasEffect=document.getElementById('ae-ceffect').value;
    body.direction=parseInt(document.getElementById('ae-csdir').value)||0;
  }
  // Convert seconds input → ms for internal storage
  function _s2ms(id,def){var v=parseFloat(document.getElementById(id).value);return isNaN(v)?def:Math.round(v*1000);}
  if(t===2){var c2=h2r(document.getElementById('ae-c2').value);body.r2=c2.r;body.g2=c2.g;body.b2=c2.b;body.speedMs=_s2ms('ae-spd',1000);}
  if(t===3){body.periodMs=_s2ms('ae-per',2000);body.minBri=parseInt(document.getElementById('ae-mbr').value)||0;}
  if(t===4){body.speedMs=_s2ms('ae-cspd',100);body.spacing=parseInt(document.getElementById('ae-cspc').value)||3;body.direction=parseInt(document.getElementById('ae-cdir').value)||0;}
  if(t===5){body.speedMs=_s2ms('ae-rspd',50);body.paletteId=parseInt(document.getElementById('ae-pal').value)||0;body.direction=parseInt(document.getElementById('ae-rdir').value)||0;}
  if(t===6){body.speedMs=_s2ms('ae-fspd',15);body.cooling=parseInt(document.getElementById('ae-cool').value)||55;body.sparking=parseInt(document.getElementById('ae-spark').value)||120;}
  if(t===7){body.speedMs=_s2ms('ae-mspd',30);body.tailLen=parseInt(document.getElementById('ae-tail').value)||10;body.decay=parseInt(document.getElementById('ae-dec').value)||80;body.direction=parseInt(document.getElementById('ae-mdir').value)||0;}
  if(t===8){body.spawnMs=_s2ms('ae-tspn',50);body.density=parseInt(document.getElementById('ae-tden').value)||3;body.fadeSpeed=parseInt(document.getElementById('ae-tfad').value)||15;}
  // DMX action types
  if(t>=14&&t<=17){
    body.dimmer=parseInt(document.getElementById('ae-dimmer').value)||0;
    body.pan=parseFloat(document.getElementById('ae-pan').value)||0;
    body.tilt=parseFloat(document.getElementById('ae-tilt').value)||0;
    body.strobe=parseInt(document.getElementById('ae-strobe').value)||0;
    body.gobo=parseInt(document.getElementById('ae-gobo').value)||0;
    body.colorWheel=parseInt(document.getElementById('ae-colorwheel').value)||0;
    body.prism=parseInt(document.getElementById('ae-prism').value)||0;
  }
  if(t===15){body.ptStartPos=[parseFloat(document.getElementById('ae-pt-sx').value)||0,parseFloat(document.getElementById('ae-pt-sy').value)||0,parseFloat(document.getElementById('ae-pt-sz').value)||0];body.ptEndPos=[parseFloat(document.getElementById('ae-pt-ex').value)||0,parseFloat(document.getElementById('ae-pt-ey').value)||0,parseFloat(document.getElementById('ae-pt-ez').value)||0];body.speedMs=_s2ms('ae-pt-spd',5000);}
  if(t===16){body.gobo=parseInt(document.getElementById('ae-gobo').value)||0;}
  if(t===17){body.colorWheel=parseInt(document.getElementById('ae-colorwheel').value)||0;}
  if(t===18){
    var tcbs=document.querySelectorAll('.ae-track-cb:checked');
    var tids=[];tcbs.forEach(function(cb){tids.push(parseInt(cb.value));});
    body.trackObjectIds=tids;
    body.trackCycleMs=parseInt(document.getElementById('ae-trk-cycle').value)||2000;
    body.trackOffset=[parseInt(document.getElementById('ae-trk-ox').value)||0,parseInt(document.getElementById('ae-trk-oy').value)||0,parseInt(document.getElementById('ae-trk-oz').value)||0];
    body.trackAutoSpread=!!document.getElementById('ae-trk-spread').checked;
    body.trackFixedAssignment=!!document.getElementById('ae-trk-fixed').checked;
  }
  // WLED overrides (optional)
  var wfx=document.getElementById('ae-wled-fx');
  if(wfx&&wfx.value!=='')body.wledFxOverride=parseInt(wfx.value);
  var wpal=document.getElementById('ae-wled-pal');
  if(wpal&&wpal.value!=='')body.wledPalOverride=parseInt(wpal.value);
  var wseg=document.getElementById('ae-wled-seg');
  if(wseg&&wseg.value!=='')body.wledSegId=parseInt(wseg.value);
  _btnSaving(btn);
  var method=id<0?'POST':'PUT';
  var url=id<0?'/api/actions':'/api/actions/'+id;
  ra(method,url,body,function(r){
    _btnSaved(btn,r&&r.ok);
    if(r&&r.ok){setTimeout(function(){closeModal();loadActions();},800);}
  });
}

function delAction(id){
  if(!confirm('Delete this action?'))return;
  // Snapshot the action so Ctrl+Z can POST it back. The server assigns a
  // fresh id on re-create, so redo has to re-snapshot after the restore
  // to stay in sync (#297).
  var snap=null;(_acts||[]).forEach(function(x){if(x.id===id)snap=JSON.parse(JSON.stringify(x));});
  var newId=null;
  function doDelete(targetId,cb){
    var x=new XMLHttpRequest();x.open('DELETE','/api/actions/'+targetId,true);
    x.onload=function(){loadActions();if(cb)cb();};
    x.onerror=function(){if(typeof toastError==='function')toastError('Delete failed');};
    x.send();
  }
  doDelete(id,function(){
    if(typeof toastSuccess==='function')toastSuccess('Action deleted — Ctrl+Z to restore');
    if(!snap||typeof cmdPush!=='function')return;
    cmdPush('Delete action "'+(snap.name||'?')+'"',
      function undo(){
        var body={};for(var k in snap)if(k!=='id')body[k]=snap[k];
        ra('POST','/api/actions',body,function(r){
          if(r&&r.ok){newId=r.id;loadActions();}
        });
      },
      function redo(){
        if(newId!=null)doDelete(newId);else if(snap&&snap.id!=null)doDelete(snap.id);
      });
  });
}
