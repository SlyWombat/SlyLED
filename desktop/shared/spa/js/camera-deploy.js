/** camera-deploy.js — Camera node SSH config, firmware check, deploy, scan. Extracted from app.js Phase 4c. */

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
// #690-followup \u2014 track unsaved edits so closeModal can warn before
// discarding, and so _csshTest can pass live form values to the server
// (bypassing the save-before-test trap).
var _csshDirty = false;
function _csshMarkDirty(){
  _csshDirty = true;
  var hint = document.getElementById('cssh-dirty-hint');
  if(hint)hint.style.display = '';
}
function _camSshModal(ip,fid){
  var _csshIp=ip;
  _csshDirty = false;
  document.getElementById('modal-title').textContent='SSH Configuration \u2014 '+ip;
  var h='<div style="min-width:420px">';
  h+='<div style="margin-bottom:.6em"><label style="font-size:.82em;color:#94a3b8">Authentication Type</label><br>';
  h+='<label style="font-size:.85em;margin-right:1em"><input type="radio" name="cssh-auth" value="password" checked onchange="_csshToggle();_csshMarkDirty()"> Password</label>';
  h+='<label style="font-size:.85em"><input type="radio" name="cssh-auth" value="key" onchange="_csshToggle();_csshMarkDirty()"> SSH Key</label>';
  h+='</div>';
  h+='<div id="cssh-pw-section">';
  h+='<label style="font-size:.82em">Username</label>';
  h+='<input id="cssh-user" value="root" oninput="_csshMarkDirty()" style="width:100%;margin-bottom:.3em">';
  h+='<label style="font-size:.82em">Password</label>';
  h+='<div style="display:flex;gap:.3em;margin-bottom:.3em"><input id="cssh-pass" type="password" oninput="_csshMarkDirty()" placeholder="Enter password" style="flex:1">';
  h+='<button type="button" class="btn" style="padding:.2em .4em;background:#333;font-size:.75em" onmousedown="document.getElementById(\'cssh-pass\').type=\'text\'" onmouseup="document.getElementById(\'cssh-pass\').type=\'password\'" onmouseleave="document.getElementById(\'cssh-pass\').type=\'password\'">&#128065;</button></div>';
  h+='</div>';
  h+='<div id="cssh-key-section" style="display:none">';
  h+='<label style="font-size:.82em">Username</label>';
  h+='<input id="cssh-key-user" value="root" oninput="_csshMarkDirty()" style="width:100%;margin-bottom:.3em">';
  h+='<label style="font-size:.82em">Key File Path</label>';
  h+='<input id="cssh-keypath" oninput="_csshMarkDirty()" placeholder="~/.ssh/id_ed25519" style="width:100%;margin-bottom:.3em">';
  h+='<label style="font-size:.82em">Or paste key content</label>';
  h+='<textarea id="cssh-keycontent" rows="4" oninput="_csshMarkDirty()" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" style="width:100%;font-size:.75em;font-family:monospace;margin-bottom:.3em"></textarea>';
  h+='</div>';
  h+='<div style="display:flex;gap:.5em;margin-top:.6em">';
  h+='<button class="btn btn-on" onclick="_csshSave(\''+ip+'\')">Save</button>';
  h+='<button class="btn" onclick="_csshTest(\''+ip+'\')" style="background:#1e3a5f;color:#93c5fd">Test Connection</button>';
  h+='</div>';
  h+='<div id="cssh-dirty-hint" style="display:none;margin-top:.4em;font-size:.78em;color:#fbbf24">● Unsaved changes — click Save to persist (Test will use the form values either way).</div>';
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
    if(r&&r.ok){
      el.innerHTML='<span style="color:#4c4">&#x2713; Saved for '+escapeHtml(ip)+'</span>';
      _csshDirty=false;
      var hint=document.getElementById('cssh-dirty-hint');
      if(hint)hint.style.display='none';
    }
    else{el.innerHTML='<span style="color:#f66">Save failed: '+(r&&r.err||'unknown')+'</span>';}
  });
}
function _csshTest(ip){
  var el=document.getElementById('cssh-result');
  el.innerHTML='<span style="color:#94a3b8">Testing connection to '+escapeHtml(ip)+'...</span>';
  // #690-followup — pass the form's live values so an operator can
  // verify credentials before deciding whether to commit them via Save.
  // The server endpoint accepts these in the body and falls back to the
  // saved per-node config for any field omitted.
  var isKey=document.querySelector('input[name="cssh-auth"][value="key"]').checked;
  var testBody;
  if(isKey){
    testBody={
      user:document.getElementById('cssh-key-user').value||'root',
      keyPath:document.getElementById('cssh-keypath').value||'',
      password:''
    };
  }else{
    testBody={
      user:document.getElementById('cssh-user').value||'root',
      password:document.getElementById('cssh-pass').value||'',
      keyPath:''
    };
  }
  ra('POST','/api/cameras/node/'+encodeURIComponent(ip)+'/ssh/test',testBody,function(r){
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
