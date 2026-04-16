/** dashboard.js — Dashboard tab: live grid, runner status, timeline status, gyro, 3D attach. Extracted from app.js Phase 2. */
var dashRunnerTimer=null;
function loadDash(){
  if(dashRunnerTimer)clearInterval(dashRunnerTimer);
  _liveGridBuilt=false;
  api('GET','/api/actions').then(function(a){_acts=a||[];}).catch(function(){});
  // Dashboard is fully live — fixture grid + runner status auto-refresh every 1s
  api('GET','/api/children').then(function(d){
    var startupDone=!d||!d.length||d[0].startupDone!==false;
    document.getElementById('dash-content').innerHTML=
      !startupDone?'<span style="color:#fa6;font-size:.82em">&#x23f3; Checking fixtures...</span>':'';
    if(!_s3d.inited)s3dInit();
    if(_s3d.renderer)emu3dInit();
    _dashAttach3d();
    // Status bar — count fixtures (not children) for accurate reporting
    ra('GET','/api/fixtures',null,function(fxs){
      if(!fxs)return;
      var light=fxs.filter(function(f){return f.fixtureType!=='camera';});
      var cams=fxs.filter(function(f){return f.fixtureType==='camera';});
      var onCnt=d?d.filter(function(c){return c.status===1;}).length:0;
      var parts=[];
      if(light.length)parts.push(light.length+' fixture'+(light.length>1?'s':''));
      if(cams.length)parts.push(cams.length+' camera'+(cams.length>1?'s':''));
      if(onCnt)parts.push(onCnt+' online');
      var movingObjs=(_objects||[]).filter(function(o){return o.mobility==='moving';});
      if(movingObjs.length)parts.push(movingObjs.length+' moving object'+(movingObjs.length>1?'s':''));
      document.getElementById('hs').textContent=parts.length?parts.join(' \u2022 '):'No fixtures';
    });
    refreshRunnerStatus();
    dashRunnerTimer=setInterval(refreshRunnerStatus,1000);
    if(!startupDone)_pollStartup();
    // Gyro live cards — poll if any gyro fixtures exist
    ra('GET','/api/fixtures',null,function(fxs){
      if(!fxs)return;
      var gyroFxs=fxs.filter(function(f){return f.fixtureType==='gyro';});
      if(!gyroFxs.length)return;
      ra('GET','/api/children',null,function(children){
        var childIpById={};
        (children||[]).forEach(function(c){childIpById[c.id]=c.ip;});
        _renderGyroDash(gyroFxs);
        if(!window._gyroTimer)window._gyroTimer=setInterval(function(){_refreshGyroDash(gyroFxs,childIpById);},500);
      });
    });
  }).catch(function(){
    document.getElementById('dash-content').innerHTML='<p style="color:#f66">Failed to load dashboard.</p>';
  });
}

function _renderGyroDash(gyroFxs){
  var el=document.getElementById('dash-content');if(!el)return;
  var h='<div id="gyro-dash-cards" style="margin-top:.8em">';
  h+='<div style="font-size:.85em;font-weight:bold;color:#94a3b8;margin-bottom:.4em">Gyro Controllers</div>';
  h+='<div style="display:flex;flex-wrap:wrap;gap:.6em">';
  gyroFxs.forEach(function(f){
    h+='<div id="gyro-card-'+f.id+'" style="background:#0f172a;border:1px solid #334155;border-radius:6px;padding:.5em .8em;min-width:200px">'
      +'<div style="font-weight:bold;color:#e2e8f0;font-size:.88em">'+escapeHtml(f.name)+'</div>'
      +'<div id="gyro-card-data-'+f.id+'" style="color:#64748b;font-size:.78em">Waiting for data...</div>'
      +'<div style="margin-top:.4em;display:flex;gap:.3em">'
      +'<button class="btn" onclick="_gyroEnable('+(f.gyroChildId||0)+',true)" style="font-size:.72em;padding:.15em .4em;background:#14532d;color:#86efac">Enable</button>'
      +'<button class="btn" onclick="_gyroEnable('+(f.gyroChildId||0)+',false)" style="font-size:.72em;padding:.15em .4em;background:#1e293b;color:#94a3b8">Disable</button>'
      +'<button class="btn" onclick="_gyroRecal('+(f.gyroChildId||0)+')" style="font-size:.72em;padding:.15em .4em;background:#1e3a5f;color:#93c5fd">Zero</button>'
      +'</div></div>';
  });
  h+='</div></div>';
  el.innerHTML+=h;
}

function _refreshGyroDash(gyroFxs,childIpById){
  ra('GET','/api/gyro/state',null,function(states){
    if(!states)return;
    var byIp={};states.forEach(function(s){byIp[s.ip]=s;});
    gyroFxs.forEach(function(f){
      var el=document.getElementById('gyro-card-data-'+f.id);
      if(!el)return;
      var ip=childIpById[f.gyroChildId];
      var st=ip?byIp[ip]:null;
      if(!st||st.stale){
        el.innerHTML='<span style="color:#64748b">STALE -- no data</span>';return;
      }
      var modeNames=['FULL','PAN','TILT','INV'];
      var liveCol=st.streaming?'#22c55e':'#94a3b8';
      el.innerHTML='<span style="color:'+liveCol+';font-size:.72em;font-weight:bold">'+(st.streaming?'LIVE':'IDLE')+'</span>'
        +' <span style="color:#94a3b8">R:'+st.roll.toFixed(1)+'\u00b0</span>'
        +' <span style="color:#94a3b8">P:'+st.pitch.toFixed(1)+'\u00b0</span>'
        +' <span style="color:#94a3b8">Y:'+st.yaw.toFixed(1)+'\u00b0</span>'
        +' <span style="color:#475569;font-size:.78em">'+st.fps+'fps</span>'
        +(st.mode!==undefined?' <span style="color:#7c3aed;font-size:.72em">'+modeNames[st.mode||0]+'</span>':'');
    });
  });
}

function _pollStartup(){
  setTimeout(function(){
    ra('GET','/api/children',null,function(d){
      if(!d||!d.length)return;
      if(d[0].startupDone===false){_pollStartup();return;}
      // Startup done — reload dashboard
      loadDash();
    });
  },2000);
}

function dashRefresh(btn){
  if(btn){btn.disabled=true;btn.textContent='Refreshing...';}
  ra('POST','/api/children/refresh-all',{},function(){
    var poll=setInterval(function(){
      ra('GET','/api/children/refresh-all/results',null,function(r){
        if(r&&r.pending)return;
        clearInterval(poll);
        if(btn){btn.disabled=false;btn.innerHTML='&#x21bb; Refresh All';}
        if(r&&r.ok){
          document.getElementById('hs').textContent=r.online+'/'+r.total+' fixtures online';
          loadDash();
        }
      });
    },500);
  });
}

var _dashChildren=null,_dashTimeline=null;
// ── Fixture Monitor Grid (#303) ─────────────────────────────────────────────
var _liveGridBuilt=false;
function refreshLiveGrid(){
  var grid=document.getElementById('dash-live-grid');
  var label=document.getElementById('dash-live-label');
  if(!grid)return;
  ra('GET','/api/fixtures/live',null,function(d){
    if(!d||!d.fixtures){
      if(!_liveGridBuilt){grid.innerHTML='<span style="color:#888;font-size:.82em">No fixtures</span>';_liveGridBuilt=true;}
      return;
    }
    var fxs=d.fixtures;
    if(!fxs.length){
      grid.innerHTML='<span style="color:#888;font-size:.82em">No fixtures registered</span>';
      _liveGridBuilt=true;return;
    }
    // Update label
    var activeCount=fxs.filter(function(f){return f.active;}).length;
    if(label){
      if(d.running)label.textContent=activeCount+'/'+fxs.length+' active';
      else label.textContent=fxs.length+' fixture'+(fxs.length>1?'s':'');
    }
    // Build or update cards
    if(!_liveGridBuilt||grid.childElementCount!==fxs.length){
      // Full rebuild
      var h='';
      fxs.forEach(function(f){
        var rgb='rgb('+f.r+','+f.g+','+f.b+')';
        var dim=f.dimmer!==undefined?Math.round(f.dimmer/2.55):0;
        var intensity=Math.max(dim,Math.round(Math.max(f.r,f.g,f.b)/2.55));
        var cls='flx-card'+(f.active?' flx-active':'')+((!f.active&&!d.running)?' flx-off':'');
        var online=f.online!==undefined?f.online:f.active;
        h+='<div class="'+cls+'" id="flx-'+f.id+'" data-fid="'+f.id+'">';
        h+='<div class="flx-badge '+(online?'flx-badge-on':'flx-badge-off')+'"></div>';
        h+='<div class="flx-name" title="'+escapeHtml(f.name)+'">'+escapeHtml(f.name)+'</div>';
        h+='<div class="flx-swatch" style="background:'+rgb+'"></div>';
        h+='<div class="flx-dim-bar"><div class="flx-dim-fill" style="width:'+intensity+'%"></div></div>';
        h+='<div class="flx-effect">'+(f.effect||'Idle')+'</div>';
        if(f.dmxAddr)h+='<div class="flx-dmx-addr">'+escapeHtml(f.dmxAddr)+'</div>';
        if(f.pan!==undefined)h+='<div class="flx-dmx-addr">P:'+f.pan+(f.panFine!==undefined?'.'+f.panFine:'')+' T:'+f.tilt+(f.tiltFine!==undefined?'.'+f.tiltFine:'')+'</div>';
        h+='</div>';
      });
      grid.innerHTML=h;
      _liveGridBuilt=true;
    }else{
      // Incremental update — just patch existing cards
      fxs.forEach(function(f){
        var card=document.getElementById('flx-'+f.id);
        if(!card)return;
        var rgb='rgb('+f.r+','+f.g+','+f.b+')';
        var dim=f.dimmer!==undefined?Math.round(f.dimmer/2.55):0;
        var intensity=Math.max(dim,Math.round(Math.max(f.r,f.g,f.b)/2.55));
        var online=f.online!==undefined?f.online:f.active;
        // Update swatch
        var sw=card.querySelector('.flx-swatch');
        if(sw)sw.style.backgroundColor=rgb;
        // Update dimmer bar
        var db=card.querySelector('.flx-dim-fill');
        if(db)db.style.width=intensity+'%';
        // Update effect label
        var eff=card.querySelector('.flx-effect');
        if(eff)eff.textContent=f.effect||'Idle';
        // Update active class
        card.className='flx-card'+(f.active?' flx-active':'')+((!f.active&&!d.running)?' flx-off':'');
        // Update badge
        var badge=card.querySelector('.flx-badge');
        if(badge)badge.className='flx-badge '+(online?'flx-badge-on':'flx-badge-off');
        // Update pan/tilt if DMX
        if(f.pan!==undefined){
          var ptEl=card.querySelectorAll('.flx-dmx-addr');
          if(ptEl.length>=2)ptEl[1].textContent='P:'+f.pan+(f.panFine!==undefined?'.'+f.panFine:'')+' T:'+f.tilt+(f.tiltFine!==undefined?'.'+f.tiltFine:'');
        }
      });
    }
  });
}

function refreshRunnerStatus(){
  refreshLiveGrid();
  // Refresh objects so patrol/tracking targets update in 3D view
  ra('GET','/api/objects',null,function(objs){
    if(objs){_objects=objs;if(_s3d.inited)_s3dRenderObjects();}
  });
  // Refresh mover control status (#471)
  ra('GET','/api/mover-control/status',null,function(d){
    var el=document.getElementById('dash-mover-ctrl');
    if(!el||!d)return;
    var claims=d.claims||[];
    if(!claims.length){el.innerHTML='';return;}
    var h='<div style="margin-top:.6em;border:1px solid #312e81;border-radius:6px;padding:.5em .7em;background:#0c0a1d">';
    h+='<div style="font-size:.78em;font-weight:600;color:#a5b4fc;margin-bottom:.3em;text-transform:uppercase;letter-spacing:.06em">Mover Control</div>';
    claims.forEach(function(c){
      var col='rgb('+c.color.r+','+c.color.g+','+c.color.b+')';
      var stateCol=c.state==='streaming'?'#4ade80':c.state==='calibrating'?'#facc15':'#94a3b8';
      var mover=(_fixtures||[]).find(function(f){return f.id===c.moverId;});
      var mname=mover?escapeHtml(mover.name):'Mover #'+c.moverId;
      h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.3em;font-size:.82em">';
      h+='<span style="width:10px;height:10px;border-radius:50%;background:'+col+';flex-shrink:0"></span>';
      h+='<b style="flex:1;color:#e2e8f0">'+mname+'</b>';
      h+='<span style="color:'+stateCol+';font-size:.75em">'+c.state+(c.calibrated?' \u2713':'')+'</span>';
      h+='<span style="color:#64748b;font-size:.72em">'+escapeHtml(c.deviceName)+'</span>';
      h+='</div>';
    });
    h+='</div>';
    el.innerHTML=h;
  });
  api('GET','/api/settings').then(function(s){
    var el=document.getElementById('dash-runner');if(!el)return;
    if(!s||!s.runnerRunning){
      el.innerHTML='<p style="color:#888;font-size:.85em">No active show.</p>';
      _dashTimeline=null;return;
    }
    // Timeline-based playback
    var tid=s.activeTimeline;
    if(tid!=null&&tid>=0){
      return api('GET','/api/timelines/'+tid+'/status').then(function(st){
        if(!_dashTimeline||_dashTimeline.id!==tid)_dashChildren=null;
        _dashTimeline=st;
        var childP=_dashChildren?Promise.resolve(_dashChildren):api('GET','/api/children');
        return childP.then(function(ch){
          _dashChildren=ch||[];
          _renderTimelineDash(el,s,st);
        });
      });
    }
    // No active show
    el.innerHTML='<p style="color:#888;font-size:.85em">No active show.</p>';
  }).catch(function(){});
}

function _renderTimelineDash(el,settings,tlStatus){
  var name=tlStatus.name||'Timeline';
  var totalS=tlStatus.durationS||0;
  var elapsed=tlStatus.elapsed||0;
  var loop=tlStatus.loop;
  var children=_dashChildren||[];
  var onlineChildren=children.filter(function(c){return c.status===1;});

  var h='<div class="card" style="max-width:700px">';
  h+='<div class="card-title">&#9654; Timeline: '+escapeHtml(name)+'</div>';
  h+='<div style="font-size:.82em;color:#aaa;margin-bottom:.4em">';
  if(totalS>0){
    if(loop&&elapsed>=totalS)elapsed=elapsed%totalS;
    h+=elapsed+'s / '+totalS+'s';
    if(!loop&&elapsed>=totalS)h+=' <span style="color:#4c4">(complete)</span>';
    if(loop)h+=' <span style="color:#22d3ee">(looping)</span>';
  }else{
    h+='Running...';
  }
  h+='</div>';

  // Progress bar
  if(totalS>0){
    var pct=Math.min(100,Math.round(elapsed*100/totalS));
    h+='<div style="background:#1e293b;border-radius:4px;height:8px;max-width:500px;overflow:hidden;margin-bottom:.5em">';
    h+='<div style="background:#22d3ee;height:100%;width:'+pct+'%;transition:width .3s"></div></div>';
  }

  // Fixture count
  h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.5em">'+onlineChildren.length+' fixture'+(onlineChildren.length!==1?'s':'')+' online</div>';

  var tid=settings.activeTimeline;
  h+='<div style="margin-top:.6em"><button class="btn btn-off" onclick="tlDashStop('+tid+')">Stop Show</button></div></div>';
  el.innerHTML=h;
}

function _dashAttach3d(){
  var el=document.getElementById('dash-3d');
  if(!el)return;
  if(!_s3d.inited)s3dInit();
  if(!_s3d.renderer)return;
  emu3dInit();
  if(!_emu3d.camera||!_emu3d.controls)return;
  if(_s3d.animId){cancelAnimationFrame(_s3d.animId);_s3d.animId=null;}
  // Directly append canvas — don't go through _emu3dAttach to avoid race
  el.appendChild(_s3d.renderer.domElement);
  var W=el.clientWidth||900,H=el.clientHeight||350;
  _s3d.renderer.setSize(W,H);
  _emu3d.camera.aspect=W/H;_emu3d.camera.updateProjectionMatrix();
  if(_s3d.controls)_s3d.controls.enabled=false;
  if(_s3d.tctl){_s3d.tctl.detach();_s3d.tctl.visible=false;}
  _emu3d.controls.enabled=true;
  _emu3d.activeTab=true;
  _emu3d.activeContainer='dash-3d';
  _s3d.renderer.domElement.removeEventListener('click',s3dClick);
  _s3d.renderer.domElement.removeEventListener('dblclick',s3dDblClick);
  if(_emu3d.animId){cancelAnimationFrame(_emu3d.animId);_emu3d.animId=null;}
  emu3dAnimate();
  if(!_emuStage)emuLoadStage();
  else if(!_emu3d.nodes.length)emu3dBuildFixtures();
}

function _dashEmuDraw(){
  var cv=document.getElementById('dash-emu-cv');if(!cv)return;
  // Ensure emulator stage data is loaded
  if(!_emuStage){
    emuLoadStage();
    setTimeout(_dashEmuDraw,500);return;
  }
  // Reuse the main emuDraw logic by temporarily swapping the canvas ID
  var origCv=document.getElementById('emu-cv');
  // Just call emuDraw with the dashboard canvas
  var ctx=cv.getContext('2d');
  var W=cv.width,H=cv.height;
  ctx.fillStyle='#060a12';ctx.fillRect(0,0,W,H);
  if(!_emuStage)return;
  var layout=_emuStage.layout;
  var cw=(layout&&layout.canvasW)||10000;
  var ch=(layout&&layout.canvasH)||5000;
  var layoutFixtures=(layout&&layout.fixtures)||[];
  if(!layoutFixtures.length)return;
  // Stage border
  ctx.strokeStyle='#1e3a5f';ctx.lineWidth=1;ctx.strokeRect(1,1,W-2,H-2);
  // Objects
  (_emuStage.objects||[]).forEach(function(s){
    var t=s.transform||{pos:[0,0,0],scale:[2000,1500,1]};
    var sx=t.pos[0]*W/cw,sy=H-t.pos[1]*H/ch;
    var sw=t.scale[0]*W/cw,sh=t.scale[1]*H/ch;
    ctx.globalAlpha=(s.opacity||30)/100;ctx.fillStyle=s.color||'#334155';
    ctx.fillRect(Math.max(0,sx),Math.max(0,sy-sh),Math.min(W,sx+sw)-Math.max(0,sx),Math.min(H,sy)-Math.max(0,sy-sh));
    ctx.globalAlpha=1;
  });
  // Dashboard 2D front view: X=horizontal, Z(height)=vertical (Y-up)
  // Stage dimensions in mm: stageW(X), stageH(Z height), stageD(Y depth)
  var stgW=(layout&&layout.stageW)||(cw);  // mm
  var stgH=(layout&&layout.stageH)||(ch);  // mm (height)
  // Use stage dimensions from _emuStage if available
  if(window._stageData){stgW=(_stageData.w||10)*1000;stgH=(_stageData.h||5)*1000;}
  // DMX fixtures — front view: X→horizontal, Z→vertical
  layoutFixtures.forEach(function(fix){
    if(fix.fixtureType!=='dmx'||!fix.positioned)return;
    var fx=(fix.x||0)*W/stgW,fy=H-(fix.z||0)*H/stgH;
    fx=Math.max(4,Math.min(W-4,fx));fy=Math.max(4,Math.min(H-4,fy));
    var aim=_rotToAim(fix.rotation||[0,0,0],[fix.x||0,fix.y||0,fix.z||0],3000,fix.mountedInverted);
    var ax=(aim[0]||0)*W/stgW,ay=H-(aim[2]||0)*H/stgH;
    var bLen=Math.sqrt((ax-fx)*(ax-fx)+(ay-fy)*(ay-fy));if(bLen<2)bLen=60;
    var bwDeg=(window._profileCache&&fix.dmxProfileId&&window._profileCache[fix.dmxProfileId])?window._profileCache[fix.dmxProfileId].beamWidth||15:15;
    var halfW=Math.tan(bwDeg*Math.PI/360)*bLen;
    var angle=Math.atan2(ay-fy,ax-fx);
    ctx.beginPath();ctx.moveTo(fx,fy);
    ctx.lineTo(ax+Math.cos(angle+Math.PI/2)*halfW,ay+Math.sin(angle+Math.PI/2)*halfW);
    ctx.lineTo(ax+Math.cos(angle-Math.PI/2)*halfW,ay+Math.sin(angle-Math.PI/2)*halfW);
    ctx.closePath();ctx.fillStyle='rgba(124,58,237,0.12)';ctx.fill();
    ctx.beginPath();ctx.arc(fx,fy,3,0,2*Math.PI);ctx.fillStyle='rgba(124,58,237,0.8)';ctx.fill();
    ctx.fillStyle='#78889a';ctx.font='7px sans-serif';ctx.textAlign='center';ctx.fillText(fix.name||'',fx,fy+12);
  });
  // Camera FOV cones
  layoutFixtures.forEach(function(fix){
    if(fix.fixtureType!=='camera'||!fix.positioned)return;
    var fx=(fix.x||0)*W/stgW,fy=H-(fix.z||0)*H/stgH;
    fx=Math.max(4,Math.min(W-4,fx));fy=Math.max(4,Math.min(H-4,fy));
    var aim=_rotToAim(fix.rotation||[0,0,0],[fix.x||0,fix.y||0,fix.z||0]);
    var ax=(aim[0]||0)*W/stgW,ay=H-(aim[2]||0)*H/stgH;
    var bLen=Math.sqrt((ax-fx)*(ax-fx)+(ay-fy)*(ay-fy));if(bLen<2)bLen=60;
    var halfW=Math.tan((fix.fovDeg||60)*Math.PI/360)*bLen;
    var angle=Math.atan2(ay-fy,ax-fx);
    ctx.beginPath();ctx.moveTo(fx,fy);
    ctx.lineTo(ax+Math.cos(angle+Math.PI/2)*halfW,ay+Math.sin(angle+Math.PI/2)*halfW);
    ctx.lineTo(ax+Math.cos(angle-Math.PI/2)*halfW,ay+Math.sin(angle-Math.PI/2)*halfW);
    ctx.closePath();ctx.fillStyle='rgba(14,116,144,0.08)';ctx.fill();
    ctx.beginPath();ctx.arc(fx,fy,3,0,2*Math.PI);ctx.fillStyle='rgba(14,116,144,0.8)';ctx.fill();
    ctx.fillStyle='#78889a';ctx.font='7px sans-serif';ctx.textAlign='center';ctx.fillText(fix.name||'',fx,fy+12);
  });
  // LED fixtures — front view
  layoutFixtures.forEach(function(fix){
    if(fix.fixtureType!=='led'||!fix.positioned||!fix.strings||!fix.strings.length)return;
    var fx=(fix.x||0)*W/stgW,fy=H-(fix.z||0)*H/stgH;
    fx=Math.max(4,Math.min(W-4,fx));fy=Math.max(4,Math.min(H-4,fy));
    var dirDx=[1,0,-1,0],dirDy=[0,-1,0,1];
    fix.strings.forEach(function(s){
      if(!s||!s.leds)return;
      var dx=dirDx[s.sdir||0]||0,dy=dirDy[s.sdir||0]||0;
      var lenMm=s.mm||500;var pxLen=dx?lenMm*W/stgW:lenMm*H/stgH;pxLen=Math.max(pxLen,15);
      for(var di=0;di<Math.min(s.leds,30);di++){
        var t=(di+0.5)/Math.min(s.leds,30);
        ctx.beginPath();ctx.arc(fx+dx*pxLen*t,fy+dy*pxLen*t,1.5,0,2*Math.PI);ctx.fillStyle='#334';ctx.fill();
      }
    });
    ctx.beginPath();ctx.arc(fx,fy,3,0,2*Math.PI);ctx.fillStyle='#22cc66';ctx.fill();
    ctx.fillStyle='#78889a';ctx.font='7px sans-serif';ctx.textAlign='center';ctx.fillText(fix.name||'',fx,fy+12);
  });
  // Axis labels
  ctx.fillStyle='#334';ctx.font='8px sans-serif';
  ctx.textAlign='left';ctx.fillText('X \u2192',4,H-4);
  ctx.save();ctx.translate(8,H-10);ctx.rotate(-Math.PI/2);ctx.fillText('Z \u2191 (height)',0,0);ctx.restore();
}

function tlDashStop(tid){
  ra('POST','/api/timelines/'+tid+'/stop',{},function(r){
    if(r&&r.ok)document.getElementById('hs').textContent='Show stopped';
  });
}
