/** file-manager.js — Project file management: export/import config and shows, File System Access API, recent projects. Extracted from app.js Phase 2. */
// ── Config / Show save-load ──────────────────────────────────────────────
function exportConfig(btn){
  _btnSaving(btn);
  ra('GET','/api/config/export',null,function(d){
    _btnSaved(btn,!!d);
    if(!d)return;
    var json=JSON.stringify(d,null,2);
    var blob=new Blob([json],{type:'application/json'});
    // Use File System Access API if available (lets user pick save location)
    if(window.showSaveFilePicker){
      window.showSaveFilePicker({suggestedName:'slyled-config.json',types:[{description:'SlyLED Config',accept:{'application/json':['.json']}}]}).then(function(handle){
        return handle.createWritable().then(function(w){return w.write(blob).then(function(){return w.close();});});
      }).catch(function(e){if(e.name!=='AbortError')console.warn('Save picker failed, using download fallback');});
    }else{
      // Fallback: browser download
      var a=document.createElement('a');
      a.href=URL.createObjectURL(blob);
      a.download='slyled-config.json';
      a.click();URL.revokeObjectURL(a.href);
    }
  });
}
function importConfig(input){
  var f=input.files[0];if(!f)return;
  var rd=new FileReader();
  rd.onload=function(e){
    try{var data=JSON.parse(e.target.result);}catch(ex){alert('Invalid JSON file');return;}
    if(data.type!=='slyled-config'){alert('Not a SlyLED config file (missing type field)');return;}
    // Schema version warning
    var sv=data.schemaVersion||data.version||1;
    if(sv>3){
      alert('This config file is version '+sv+', but this app only supports up to version 3. Please update SlyLED.');
      return;
    }
    if(!confirm('Load configuration from v'+sv+' file? This will merge fixtures and replace layout.'))return;
    ra('POST','/api/config/import',data,function(r){
      if(r&&r.ok){
        document.getElementById('hs').textContent='Config loaded (v'+sv+'): +'+r.added+' new, '+r.updated+' updated, '+r.fixturesCreated+' fixtures';
        loadSetup();loadLayout();
      }else{
        document.getElementById('hs').textContent='Config load failed: '+(r&&r.err||'unknown error');
      }
    });
  };
  rd.readAsText(f);input.value='';
}
function exportShow(btn){
  _btnSaving(btn);
  ra('GET','/api/show/export',null,function(d){
    _btnSaved(btn,!!d);
    if(!d)return;
    var blob=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
    var a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download='slyled-show.json';
    a.click();URL.revokeObjectURL(a.href);
  });
}
function openLoadShowModal(){
  document.getElementById('modal-title').textContent='Load Show';
  var h='<p style="font-size:.85em;color:#aaa;margin-bottom:1em">Choose a source:</p>';
  h+='<div style="display:flex;flex-direction:column;gap:.8em">';
  h+='<div class="card" style="margin:0;cursor:pointer;border-color:#3b82f6" onclick="document.getElementById(\'show-file-input\').click()">';
  h+='<b style="color:#93c5fd">From File</b>';
  h+='<p style="font-size:.82em;color:#94a3b8;margin-top:.3em">Load a previously saved .json show file</p>';
  h+='<input type="file" id="show-file-input" accept=".json" style="display:none" onchange="importShowFile(this)">';
  h+='</div>';
  h+='<div class="card" style="margin:0;border-color:#22c55e">';
  h+='<b style="color:#86efac">Preset Shows</b>';
  h+='<p style="font-size:.82em;color:#94a3b8;margin-top:.3em">Load a pre-built show onto the timeline</p>';
  h+='<div id="preset-list" style="margin-top:.5em"><p style="color:#555;font-size:.82em">Loading...</p></div>';
  h+='</div>';
  h+='</div>';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='flex';
  loadPresetList();
}
function importShowFile(input){
  var f=input.files[0];if(!f)return;
  var rd=new FileReader();
  rd.onload=function(e){
    try{var data=JSON.parse(e.target.result);}catch(ex){alert('Invalid JSON');return;}
    if(data.type!=='slyled-show'){alert('Not a SlyLED show file');return;}
    if(!confirm('Load show? This replaces ALL current actions, runners, flights, and shows.'))return;
    ra('POST','/api/show/import',data,function(r){
      closeModal();
      if(r&&r.ok){
        document.getElementById('hs').textContent='Show loaded: '+r.actions+' actions, '+r.runners+' runners, '+r.flights+' flights, '+r.shows+' shows';
        if(r.warning)alert(r.warning);
      }else{document.getElementById('hs').textContent='Show load failed: '+(r&&r.err||'unknown');}
    });
  };
  rd.readAsText(f);input.value='';
}

// ── Project file (Save/Open/Recent) ─────────────────────────────────────────
var _projFileHandle=null; // File System Access API handle for current file
var _projName='SlyLED';

function _fmToggle(e){
  e.stopPropagation();
  var dd=document.getElementById('file-dropdown');
  dd.classList.toggle('open');
  _fmBuildRecent();
}
document.addEventListener('click',function(){document.getElementById('file-dropdown').classList.remove('open');});

function _fmBuildRecent(){
  var el=document.getElementById('fm-recent-menu');if(!el)return;
  var list=_projRecentGet();
  if(!list.length){el.innerHTML='<span class="fm-item-muted">No recent projects</span>';return;}
  var h='';
  list.forEach(function(r){
    h+='<button class="fm-item" onclick="_fmOpenRecent(\''+escapeHtml(r.filename)+'\')">'+escapeHtml(r.name)+' <span style="color:#64748b;font-size:.8em">— '+r.date+'</span></button>';
  });
  el.innerHTML=h;
}

function _projRecentGet(){
  try{return JSON.parse(localStorage.getItem('slyled-recent-projects'))||[];}catch(e){return[];}
}
function _projRecentAdd(name,filename){
  var list=_projRecentGet().filter(function(r){return r.filename!==filename;});
  list.unshift({name:name,filename:filename,date:new Date().toISOString().slice(0,10)});
  if(list.length>8)list=list.slice(0,8);
  localStorage.setItem('slyled-recent-projects',JSON.stringify(list));
}

function _projUpdateName(name){
  _projName=name||'SlyLED';
  var el=document.getElementById('proj-name-display');
  if(el)el.textContent=_projName==='SlyLED'?'':_projName;
  document.title=_projName+' — SlyLED';
}

function _fmNewProject(){
  document.getElementById('file-dropdown').classList.remove('open');
  if(!confirm('Create a new project? This will reset ALL data.'))return;
  var x=new XMLHttpRequest();
  x.open('POST','/api/reset',true);
  x.setRequestHeader('Content-Type','application/json');
  x.setRequestHeader('X-SlyLED-Confirm','true');
  x.onload=function(){
    try{var r=JSON.parse(x.responseText);}catch(e){return;}
    if(r&&r.ok){
      _projFileHandle=null;
      _projUpdateName('SlyLED');
      document.getElementById('hs').textContent='New project created';
      loadAll();
    }
  };
  x.send(null);
}

function _fmSave(){
  document.getElementById('file-dropdown').classList.remove('open');
  if(_projFileHandle&&window.showSaveFilePicker){
    // Save to existing handle
    _projDoExportToHandle(_projFileHandle);
  }else{
    _fmSaveAs();
  }
}

function _fmSaveAs(){
  document.getElementById('file-dropdown').classList.remove('open');
  ra('GET','/api/project/export',null,function(d){
    if(!d)return;
    var json=JSON.stringify(d,null,2);
    var blob=new Blob([json],{type:'application/json'});
    var fname=(d.name||'project').replace(/[^a-zA-Z0-9_\-\s]/g,'').replace(/\s+/g,'-').toLowerCase()+'.slyshow';
    if(window.showSaveFilePicker){
      window.showSaveFilePicker({suggestedName:fname,types:[{description:'SlyLED Project',accept:{'application/json':['.slyshow','.json']}}]}).then(function(handle){
        _projFileHandle=handle;
        var name=handle.name.replace(/\.(slyshow|json)$/i,'');
        _projRecentAdd(d.name||name,handle.name);
        _projUpdateName(d.name||name);
        return handle.createWritable().then(function(w){return w.write(blob).then(function(){return w.close();});});
      }).then(function(){
        document.getElementById('hs').textContent='Project saved';
      }).catch(function(e){
        if(e.name!=='AbortError'){
          // Fallback download
          var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fname;a.click();URL.revokeObjectURL(a.href);
          document.getElementById('hs').textContent='Project downloaded as '+fname;
        }
      });
    }else{
      var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fname;a.click();URL.revokeObjectURL(a.href);
      _projRecentAdd(d.name||'project',fname);
      document.getElementById('hs').textContent='Project downloaded as '+fname;
    }
  });
}

function _projDoExportToHandle(handle){
  ra('GET','/api/project/export',null,function(d){
    if(!d)return;
    var json=JSON.stringify(d,null,2);
    var blob=new Blob([json],{type:'application/json'});
    handle.createWritable().then(function(w){return w.write(blob).then(function(){return w.close();});}).then(function(){
      document.getElementById('hs').textContent='Project saved';
    }).catch(function(){
      document.getElementById('hs').textContent='Save failed';
    });
  });
}

function _fmOpen(){
  document.getElementById('file-dropdown').classList.remove('open');
  if(window.showOpenFilePicker){
    window.showOpenFilePicker({types:[{description:'SlyLED Project',accept:{'application/json':['.slyshow','.json']}}],multiple:false}).then(function(handles){
      _projFileHandle=handles[0];
      return handles[0].getFile();
    }).then(function(file){
      return file.text().then(function(txt){_fmImportJson(txt,_projFileHandle?_projFileHandle.name:'');});
    }).catch(function(e){if(e.name!=='AbortError')console.warn('Open picker failed');});
  }else{
    document.getElementById('proj-file-input').click();
  }
}

function _fmOpenFile(input){
  var f=input.files[0];if(!f)return;
  var rd=new FileReader();
  rd.onload=function(e){_fmImportJson(e.target.result,f.name);};
  rd.readAsText(f);input.value='';
}

function _fmImportJson(txt,filename){
  try{var data=JSON.parse(txt);}catch(ex){alert('Invalid JSON file');return;}
  if(data.type!=='slyled-project'){alert('Not a SlyLED project file.\n\nExpected type "slyled-project" but got "'+(data.type||'undefined')+'".\n\nUse Import Config for config files or Load Show for show files.');return;}
  var sv=data.schemaVersion||1;
  var summary='Project: '+(data.name||'Untitled')+'\nSaved: '+(data.savedAt||'unknown')+'\nApp version: '+(data.appVersion||'unknown')+'\n\nThis will replace ALL current data.';
  if(!confirm('Load project?\n\n'+summary))return;
  document.getElementById('hs').textContent='Loading project...';
  ra('POST','/api/project/import',data,function(r){
    if(r&&r.ok){
      _projRecentAdd(r.name||data.name||'Untitled',filename);
      _projUpdateName(r.name||data.name);
      document.getElementById('hs').textContent='Project "'+r.name+'" loaded — '+r.children+' children, '+r.fixtures+' fixtures, '+r.actions+' actions';
      // Guide user to re-enter SSH credentials for camera nodes
      if(r.sshNeeded&&r.sshNeeded.length){
        var msg='The following camera nodes need SSH credentials re-entered (passwords are not portable between computers):\n\n';
        r.sshNeeded.forEach(function(n){
          msg+='\u2022 '+n.ip+' ('+n.authType+', user: '+n.user+')'+(n.keyPath?' — key: '+n.keyPath:'')+'\n';
        });
        msg+='\nGo to Firmware tab > Camera Setup to configure SSH for each node.';
        setTimeout(function(){alert(msg);},500);
      }
      // Guide user about WiFi credentials (#315)
      ra('GET','/api/wifi',null,function(w){
        if(!w||(!w.ssid&&!w.hasPassword)){
          setTimeout(function(){
            alert('WiFi credentials are not included in project files for security.\n\nIf you need OTA firmware updates, enter your network SSID and password on the Firmware tab.');
          },1200);
        }
      });
      loadAll();
    }else{
      document.getElementById('hs').textContent='Project load failed: '+(r&&r.err||'unknown error');
    }
  });
}

function _fmOpenRecent(filename){
  document.getElementById('file-dropdown').classList.remove('open');
  // Recent files are just names — user must re-open since we can't persist FS handles across sessions
  alert('To open "'+filename+'", use File > Open and select the file.\n\nRecent files show your history for reference.');
}

function loadAll(){
  // Reload all tabs with fresh data
  loadDash();loadSetup();loadLayout();loadActions();loadRuntime();loadSettings();
  ra('GET','/api/project/name',null,function(d){if(d&&d.name)_projUpdateName(d.name);});
}

function loadPresetList(){
  ra('GET','/api/show/presets',null,function(presets){
    var el=document.getElementById('preset-list');if(!el)return;
    if(!presets||!presets.length){el.innerHTML='<p style="color:#555">No presets available</p>';return;}
    var h='<div style="display:flex;flex-wrap:wrap;gap:.4em">';
    presets.forEach(function(p){
      h+='<button class="btn" onclick="loadPreset(\''+p.id+'\')" style="background:#14532d;color:#86efac;font-size:.75em;padding:.3em .6em" title="'+escapeHtml(p.desc)+'">'+escapeHtml(p.name)+'</button>';
    });
    h+='</div>';el.innerHTML=h;
  });
}
function loadPreset(id){
  ra('POST','/api/show/preset',{id:id},function(r){
    closeModal();
    if(r&&r.ok){
      var msg='Loaded "'+r.name+'" — '+r.actions+' actions, '+r.effects+' effects.';
      if(r.warnings&&r.warnings.length){
        msg+=' Warning: '+r.warnings.join('; ');
      } else {
        msg+=' Go to Runtime tab to play.';
      }
      document.getElementById('hs').textContent=msg;
      showTab('runtime');
    }else{
      document.getElementById('hs').textContent='Load failed: '+(r&&r.err||'?');
    }
  });
}
