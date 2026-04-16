/** profiles.js — Profile library, patch view, OFL import, community browser. Extracted from app.js Phase 3. */
// ── Gel Color Presets (common theatrical gel colors) ─────────────────────────
var _GEL_PRESETS=[
  {name:'Open / White',hex:'#ffffff'},{name:'Warm White',hex:'#fff5e6'},{name:'Cool White',hex:'#e6f0ff'},
  {name:'Primary Red',hex:'#ff0000'},{name:'Deep Red',hex:'#cc0000'},{name:'Orange',hex:'#ff6600'},
  {name:'Amber',hex:'#ffbf00'},{name:'Yellow',hex:'#ffff00'},{name:'Straw',hex:'#fce883'},
  {name:'Primary Green',hex:'#00ff00'},{name:'Dark Green',hex:'#006633'},{name:'Cyan',hex:'#00ffff'},
  {name:'Primary Blue',hex:'#0000ff'},{name:'Congo Blue',hex:'#00004d'},{name:'Lavender',hex:'#cc99ff'},
  {name:'Magenta',hex:'#ff00ff'},{name:'Pink',hex:'#ff66b2'},{name:'UV / Blacklight',hex:'#6600cc'},
  {name:'CTO (warm)',hex:'#ffad5c'},{name:'CTB (cool)',hex:'#80b3ff'},
];
// ── Profile Library + Patch View + OFL + Community ──────────────────────────
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
  var isColorWheel=window._peChannels&&window._peChannels[chIdx]&&window._peChannels[chIdx].type==='color-wheel';
  (window._peCaps||[]).forEach(function(c,j){
    var pct0=c.range[0]/maxVal*100,pct1=(c.range[1]+1)/maxVal*100;
    var w=pct1-pct0;
    var col=(c.type==='WheelSlot'&&c.color)?c.color:(capColors[c.type]||'#64748b');
    h+='<div style="position:absolute;left:'+pct0+'%;width:'+w+'%;height:100%;background:'+col+';opacity:0.6;border-radius:2px" title="'+c.range[0]+'-'+c.range[1]+': '+(c.label||c.type)+'"></div>';
  });
  h+='</div>';
  h+='<table class="tbl" style="font-size:.75em"><tr><th>Min</th><th>Max</th><th>Type</th><th>Label</th>'+(isColorWheel?'<th>Color</th>':'')+'<th>Default</th><th></th></tr>';
  (window._peCaps||[]).forEach(function(c,j){
    var tOpts='';_profEditCapTypes.forEach(function(t){tOpts+='<option'+(t===c.type?' selected':'')+'>'+t+'</option>';});
    h+='<tr><td><input type="number" value="'+c.range[0]+'" min="0" max="'+maxVal+'" style="width:55px;font-size:.9em" onchange="window._peCaps['+j+'].range[0]=parseInt(this.value);_peRenderCaps('+chIdx+')"></td>';
    h+='<td><input type="number" value="'+c.range[1]+'" min="0" max="'+maxVal+'" style="width:55px;font-size:.9em" onchange="window._peCaps['+j+'].range[1]=parseInt(this.value);_peRenderCaps('+chIdx+')"></td>';
    h+='<td><select style="font-size:.85em" onchange="window._peCaps['+j+'].type=this.value;_peRenderCaps('+chIdx+')">'+tOpts+'</select></td>';
    h+='<td><input value="'+escapeHtml(c.label||'')+'" style="width:120px;font-size:.9em" onchange="window._peCaps['+j+'].label=this.value"></td>';
    if(isColorWheel){
      var cHex=c.color||'#000000';
      h+='<td style="white-space:nowrap">';
      h+='<input type="color" value="'+cHex+'" style="width:28px;height:22px;padding:0;border:none;cursor:pointer;vertical-align:middle" onchange="window._peCaps['+j+'].color=this.value;_peRenderCaps('+chIdx+')">';
      h+=' <span style="position:relative;display:inline-block"><button class="btn" style="font-size:.65em;padding:1px 5px" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display===\'none\'?\'block\':\'none\'">Gel</button>';
      h+='<div style="display:none;position:absolute;z-index:50;background:#0f172a;border:1px solid #334;border-radius:4px;padding:4px;width:200px;top:22px;left:0;box-shadow:0 4px 12px rgba(0,0,0,.5)">';
      _GEL_PRESETS.forEach(function(g){
        h+='<span onclick="window._peCaps['+j+'].color=\''+g.hex+'\';_peRenderCaps('+chIdx+')" title="'+escapeHtml(g.name)+'" style="display:inline-block;width:22px;height:22px;background:'+g.hex+';border:1px solid #555;border-radius:2px;cursor:pointer;margin:1px"></span>';
      });
      h+='</div></span></td>';
    }
    h+='<td><input type="number" value="'+(c.default!==undefined?c.default:c.range[0])+'" min="'+c.range[0]+'" max="'+c.range[1]+'" style="width:50px;font-size:.9em" onchange="window._peCaps['+j+'].default=parseInt(this.value)"></td>';
    h+='<td><button class="btn btn-off" onclick="window._peCaps.splice('+j+',1);_peRenderCaps('+chIdx+')" style="font-size:.7em">\u2715</button></td></tr>';
  });
  el.innerHTML=h+'</table>';
}
function _peAddCap(chIdx){
  var caps=window._peCaps||[];
  var startVal=caps.length?caps[caps.length-1].range[1]+1:0;
  var isColorWheel=window._peChannels&&window._peChannels[chIdx]&&window._peChannels[chIdx].type==='color-wheel';
  var newCap=isColorWheel
    ?{range:[startVal,Math.min(startVal+4,255)],type:'WheelSlot',label:'',color:'#ffffff',default:startVal}
    :{range:[startVal,255],type:'Intensity',label:'',default:startVal};
  caps.push(newCap);
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
