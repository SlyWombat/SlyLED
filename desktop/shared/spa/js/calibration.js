/** calibration.js — Camera calibration, scan mode, tracking, point cloud. Extracted from app.js Phase 3. */
// ── Scan mode + Calibration + Tracking + Point Cloud ──────────────────────
// ── Scan mode — detect stage objects via camera ───────────────────────
var _scanGhosts=[];  // [{label,confidence,x,y,z,w,h,pixelBox}]
var _scanBusy=false;

function _layScanUpdateBtn(){
  var btn=document.getElementById('btn-lay-scan');
  if(!btn)return;
  var hasCam=(_fixtures||[]).some(function(f){return f.fixtureType==='camera'&&f.positioned;});
  btn.style.display=hasCam?'flex':'none';
}

function _layScan(){
  if(_scanBusy)return;
  var cam=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera'&&f.positioned;})[0];
  if(!cam){document.getElementById('hs').textContent='No positioned camera fixture';return;}
  _scanBusy=true;
  var btn=document.getElementById('btn-lay-scan');
  if(btn){btn.style.background='#7c3aed';btn.title='Scanning...';}
  document.getElementById('hs').textContent='Scanning for objects...';
  ra('POST','/api/cameras/'+cam.id+'/scan',{threshold:0.4,resolution:320},function(r){
    _scanBusy=false;
    if(btn){btn.style.background='#4c1d95';btn.title='Scan for objects (camera)';}
    if(!r||!r.ok){
      document.getElementById('hs').textContent='Scan failed: '+(r&&r.err||'unknown');
      return;
    }
    _scanGhosts=r.detections||[];
    if(!_scanGhosts.length){
      document.getElementById('hs').textContent='No objects detected';
      drawLayout();return;
    }
    document.getElementById('hs').textContent=_scanGhosts.length+' object(s) detected — double-click to accept, right-click to dismiss';
    s3dLoadChildren();
    if(_s3d.inited)_s3dRenderGhosts();
  });
}

function _layScanAccept(idx){
  var g=_scanGhosts[idx];
  if(!g)return;
  ra('POST','/api/objects',{
    name:g.label,
    objectType:'prop',
    mobility:'static',
    color:'#22d3ee',
    opacity:20,
    transform:{pos:[g.x,0,g.z],rot:[0,0,0],scale:[g.w,g.h,100]}
  },function(r){
    if(r&&r.ok){
      _scanGhosts.splice(idx,1);
      loadObjects(function(){
        s3dLoadChildren();
        if(_s3d.inited)_s3dRenderGhosts();
      });
      document.getElementById('hs').textContent='Added "'+g.label+'" to stage';
    }
  });
}

function _layScanDismiss(idx){
  _scanGhosts.splice(idx,1);
  document.getElementById('hs').textContent=_scanGhosts.length?_scanGhosts.length+' remaining':'All dismissed';
  if(_s3d.inited)_s3dRenderGhosts();
}

function _layScanDismissAll(){
  _scanGhosts=[];
  document.getElementById('hs').textContent='Scan results cleared';
  if(_s3d.inited)_s3dRenderGhosts();
}

// ── Calibration wizard ────────────────────────────────────────────────
var _calState=null;  // {camId, step, fixtures, detected}

function _calWizardStart(camId){
  ra('POST','/api/cameras/'+camId+'/calibrate/start',{},function(r){
    if(!r||!r.ok){
      document.getElementById('hs').textContent='Calibration failed: '+(r&&r.err||'unknown');
      return;
    }
    _calState={camId:camId,step:0,fixtures:r.fixtures||[],detected:[]};
    _calWizardShow();
  });
}

function _calWizardShow(){
  var s=_calState;if(!s)return;
  var h='<div style="min-width:400px">';
  h+='<p style="color:#94a3b8;font-size:.85em;margin-bottom:.8em">Click each reference fixture on the camera image to map pixel \u2192 stage coordinates.</p>';
  // Progress
  h+='<div class="prog-bar" style="height:8px;margin-bottom:.6em"><div class="prog-fill" style="width:'+Math.round(s.detected.length/s.fixtures.length*100)+'%;transition:width .3s"></div></div>';
  h+='<div style="font-size:.82em;color:#64748b;margin-bottom:.5em">'+s.detected.length+' / '+s.fixtures.length+' reference points</div>';
  // Fixture list
  h+='<div style="max-height:200px;overflow-y:auto;margin-bottom:.6em">';
  s.fixtures.forEach(function(f,i){
    var done=s.detected.some(function(d){return d.fixtureId===f.id;});
    var isCurrent=!done&&s.detected.length===i;
    h+='<div style="padding:.3em .5em;border-radius:4px;margin-bottom:.2em;font-size:.85em;'
      +(done?'background:#065f46;color:#34d399':isCurrent?'background:#1e3a5f;color:#93c5fd;border:1px solid #3b82f6':'color:#64748b')
      +'">'
      +(done?'\u2713 ':isCurrent?'\u25b6 ':'\u2022 ')
      +f.name+' <span style="font-size:.75em;opacity:.7">('+f.x+', '+f.z+' mm)</span>'
      +'</div>';
  });
  h+='</div>';
  // Current step: show snapshot with click-to-mark
  var nextRef=null;
  for(var i=0;i<s.fixtures.length;i++){
    if(!s.detected.some(function(d){return d.fixtureId===s.fixtures[i].id;})){
      nextRef=s.fixtures[i];break;
    }
  }
  if(nextRef){
    h+='<div style="margin-bottom:.5em;font-size:.85em;color:#22d3ee">Click on <strong>'+escapeHtml(nextRef.name)+'</strong> in the image below:</div>';
    h+='<div style="position:relative;cursor:crosshair" id="cal-snap-wrap">';
    h+='<img id="cal-snap" style="width:100%;border-radius:4px;border:1px solid #334155;display:none" onclick="_calClickSnap(event,'+nextRef.id+')">';
    h+='<canvas id="cal-marks" style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none"></canvas>';
    h+='</div>';
    h+='<div id="cal-snap-msg" style="font-size:.82em;color:#64748b;margin-top:.3em">Loading snapshot...</div>';
  }
  // Buttons
  h+='<div style="display:flex;gap:.5em;margin-top:.8em;flex-wrap:wrap">';
  if(s.detected.length>=3){
    h+='<button class="btn btn-on" onclick="_calCompute()">Compute Calibration</button>';
  }
  h+='<button class="btn btn-off" onclick="_calState=null;closeModal()">Cancel</button>';
  h+='</div>';
  h+='</div>';
  document.getElementById('modal-title').textContent='Camera Calibration';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  // Load snapshot
  if(nextRef)_calLoadSnap();
}

function _calLoadSnap(){
  var s=_calState;if(!s)return;
  var img=document.getElementById('cal-snap');
  var msg=document.getElementById('cal-snap-msg');
  var x=new XMLHttpRequest();
  // Find the camera fixture to get IP
  var camFix=(_fixtures||[]).filter(function(f){return f.id===s.camId;})[0];
  if(!camFix||!camFix.cameraIp){if(msg)msg.textContent='Camera not found';return;}
  x.open('GET','/api/cameras/'+s.camId+'/snapshot');
  x.responseType='blob';
  x.onload=function(){
    if(x.status===200&&x.response){
      img.src=URL.createObjectURL(x.response);
      img.style.display='block';
      if(msg)msg.textContent='Click on the fixture location';
      _calDrawMarks();
    }else{if(msg)msg.textContent='Snapshot failed';}
  };
  x.onerror=function(){if(msg)msg.textContent='Connection failed';};
  x.send();
}

function _calClickSnap(e,fixId){
  var s=_calState;if(!s)return;
  var img=document.getElementById('cal-snap');if(!img)return;
  var rect=img.getBoundingClientRect();
  // Map click to actual pixel coords
  var scaleX=img.naturalWidth/rect.width;
  var scaleY=img.naturalHeight/rect.height;
  var px=Math.round((e.clientX-rect.left)*scaleX);
  var py=Math.round((e.clientY-rect.top)*scaleY);
  // Send to server
  ra('POST','/api/cameras/'+s.camId+'/calibrate/detect',
    {fixtureId:fixId,pixelX:px,pixelY:py},function(r){
    if(r&&r.ok){
      s.detected.push({fixtureId:fixId,px:px,py:py});
      _calWizardShow();  // Refresh wizard
    }
  });
}

function _calDrawMarks(){
  var s=_calState;if(!s)return;
  var img=document.getElementById('cal-snap');
  var cvs=document.getElementById('cal-marks');
  if(!img||!cvs)return;
  img.onload=function(){
    cvs.width=img.naturalWidth;cvs.height=img.naturalHeight;
    var ctx=cvs.getContext('2d');
    ctx.clearRect(0,0,cvs.width,cvs.height);
    s.detected.forEach(function(d){
      ctx.beginPath();ctx.arc(d.px,d.py,12,0,Math.PI*2);
      ctx.strokeStyle='#4ade80';ctx.lineWidth=3;ctx.stroke();
      ctx.beginPath();ctx.arc(d.px,d.py,3,0,Math.PI*2);
      ctx.fillStyle='#4ade80';ctx.fill();
    });
  };
  if(img.complete&&img.naturalWidth)img.onload();
}

function _calCompute(){
  var s=_calState;if(!s)return;
  ra('POST','/api/cameras/'+s.camId+'/calibrate/compute',{},function(r){
    if(!r||!r.ok){
      document.getElementById('hs').textContent='Calibration failed: '+(r&&r.err||'unknown');
      return;
    }
    var err=r.error||0;
    var h='<div style="text-align:center;padding:1em">';
    h+='<div style="font-size:2em;color:#4ade80;margin-bottom:.3em">\u2713</div>';
    h+='<div style="font-size:1.1em;color:#e2e8f0;margin-bottom:.5em">Calibration Complete</div>';
    h+='<div style="font-size:.9em;color:#94a3b8">Reprojection error: <strong style="color:'+(err<30?'#4ade80':err<100?'#fbbf24':'#f87171')+'">'+err.toFixed(1)+' mm</strong></div>';
    if(err>100)h+='<div style="font-size:.82em;color:#fbbf24;margin-top:.5em">\u26a0 High error — consider recalibrating with more reference points</div>';
    h+='<div style="margin-top:1em"><button class="btn btn-on" onclick="closeModal();_calState=null;loadLayout()">Done</button></div>';
    h+='</div>';
    document.getElementById('modal-title').textContent='Camera Calibration';
    document.getElementById('modal-body').innerHTML=h;
    // Update fixture locally
    (_fixtures||[]).forEach(function(f){if(f.id===s.camId)f.calibrated=true;});
    renderSidebar();
  });
}

// #713 B — `_rangeCalStart` family (~120 lines: rangeCalStart /
// Show / Step / Submit) deleted as dead code. No SPA callers; the
// wizard was superseded by profile-defined `panRange`/`tiltRange`.

// ── Unified mover calibration wizard ─────────────────────────────────
var _moverCalFid=null;
var _moverCalTimer=null;
var _manCal=null; // manual calibration state: {fid, markers:[], step, currentIdx, samples:[], channels:null}

// ── Printable checkerboard + camera calibration status ───────────────

function _checkCamCalWarning(){
  var warn=document.getElementById('lay-cal-warn');if(!warn)return;
  var cams=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera'&&f.cameraIp;});
  if(!cams.length){warn.style.display='none';return;}
  var uncal=0,checked=0;
  cams.forEach(function(f){
    var ip=f.cameraIp;
    var x=new XMLHttpRequest();
    x.open('GET','/api/cameras/'+f.id+'/intrinsic');
    x.timeout=3000;
    x.onload=function(){
      checked++;
      try{var r=JSON.parse(x.responseText);if(!r.calibrated)uncal++;}catch(e){uncal++;}
      if(checked>=cams.length){
        warn.style.display=uncal>0?'inline':'none';
        if(uncal>0)warn.textContent='\u26a0 '+uncal+' camera'+(uncal>1?'s':'')+' not lens-calibrated \u2192 Settings';
      }
    };
    x.onerror=function(){checked++;if(checked>=cams.length&&uncal>0){warn.style.display='inline';}};
    x.send();
  });
}

function _printAruco(){
  // Generate all 6 ArUco 4x4 markers in a single printable HTML page
  // ArUco DICT_4X4_50 — correct patterns extracted from OpenCV cv2.aruco
  var dict=[
    [1,0,1,1, 0,1,0,1, 0,0,1,1, 0,0,1,0],  // ID 0
    [0,0,0,0, 1,1,1,1, 1,0,0,1, 1,0,1,0],  // ID 1
    [0,0,1,1, 0,0,1,1, 0,0,1,0, 1,1,0,1],  // ID 2
    [1,0,0,1, 1,0,0,1, 0,1,0,0, 0,1,1,0],  // ID 3
    [0,1,0,1, 0,1,0,0, 1,0,0,1, 1,1,1,0],  // ID 4
    [0,1,1,1, 1,0,0,1, 1,1,0,0, 1,1,0,1],  // ID 5
  ];
  var sizeMm=150,sq=Math.round(sizeMm/6);
  // Build a single HTML page with all markers (one per printed page)
  var html='<!DOCTYPE html><html><head><title>SlyLED ArUco Markers</title><style>';
  html+='@page{size:letter;margin:15mm}body{font-family:sans-serif;margin:0}';
  html+='.marker{page-break-after:always;text-align:center;padding-top:15mm}';
  html+='.marker:last-child{page-break-after:auto}';
  html+='.hdr{font-size:11pt;color:#888;margin-bottom:10mm;letter-spacing:2px}';
  html+='svg{display:block;margin:0 auto}';
  html+='p{margin-top:8mm;font-size:14pt;color:#333}';
  html+='</style></head><body>';
  for(var mid=0;mid<dict.length;mid++){
    var bits=dict[mid];
    var gs=6,w=gs*sq,h=gs*sq;
    html+='<div class="marker">';
    html+='<div class="hdr">SLYLED</div>';
    html+='<svg xmlns="http://www.w3.org/2000/svg" width="'+w+'mm" height="'+h+'mm" viewBox="0 0 '+w+' '+h+'">';
    html+='<rect width="'+w+'" height="'+h+'" fill="black"/>';
    for(var r=0;r<4;r++)for(var c=0;c<4;c++){
      if(bits[r*4+c]===1){
        html+='<rect x="'+((c+1)*sq)+'" y="'+((r+1)*sq)+'" width="'+sq+'" height="'+sq+'" fill="white"/>';
      }
    }
    html+='</svg>';
    html+='<p>ArUco 4x4 &mdash; ID '+mid+' &mdash; '+sizeMm+'mm &mdash; Print at 100% scale</p>';
    html+='</div>';
  }
  html+='</body></html>';
  // Store HTML for download
  window._arucoHtml=html;
  // Render in an iframe inside a modal — no popup blocker issues
  var mh='<div style="text-align:center;margin-bottom:.8em">';
  mh+='<button class="btn btn-on" onclick="document.getElementById(\'aruco-frame\').contentWindow.print()" style="font-size:1em;padding:.5em 1.5em">Print All Markers</button>';
  mh+=' <button class="btn" onclick="_downloadAruco()" style="font-size:.85em;background:#334;color:#ccc">Download HTML</button>';
  mh+='</div>';
  mh+='<iframe id="aruco-frame" srcdoc="'+html.replace(/"/g,'&quot;')+'" style="width:100%;height:400px;border:1px solid #334;background:#fff;border-radius:4px"></iframe>';
  document.getElementById('modal-title').textContent='SlyLED ArUco Markers (6 pages)';
  document.getElementById('modal-body').innerHTML=mh;
  document.getElementById('modal').style.display='block';
  document.getElementById('hs').textContent='ArUco markers ready — click Print to send to printer';
}
function _downloadAruco(){
  if(!window._arucoHtml)return;
  var blob=new Blob([window._arucoHtml],{type:'text/html'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='slyled-aruco-markers.html';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

function _printCheckerboard(){
  // Generate a 7x10 checkerboard SVG and open in new tab for printing
  var cols=10,rows=7,sq=25; // 25mm squares
  var w=cols*sq,h=rows*sq;
  var svg='<svg xmlns="http://www.w3.org/2000/svg" width="'+w+'mm" height="'+h+'mm" viewBox="0 0 '+w+' '+h+'">';
  svg+='<rect width="'+w+'" height="'+h+'" fill="white"/>';
  for(var r=0;r<rows;r++)for(var c=0;c<cols;c++){
    if((r+c)%2===0)svg+='<rect x="'+(c*sq)+'" y="'+(r*sq)+'" width="'+sq+'" height="'+sq+'" fill="black"/>';
  }
  svg+='<text x="'+w/2+'" y="'+(h+8)+'" text-anchor="middle" font-size="3" fill="#666">SlyLED Calibration Pattern — '+cols+'x'+rows+' squares, '+sq+'mm each — Print at 100% scale</text>';
  svg+='</svg>';
  var blob=new Blob([svg],{type:'image/svg+xml'});
  var url=URL.createObjectURL(blob);
  var win=window.open(url,'_blank');
  if(win)win.print();
}

// ── Intrinsic calibration wizard (6-step) ───────────────────────────
var _calWiz={step:1,cameras:[],captures:0,captureLog:[],results:{},markersPlaced:false,stageMap:{},_refreshTimer:null};
var _calWizMinCaptures=5;

function _loadCamCalStatus(){
  // Ensure fixtures are loaded before populating camera list
  if(!_fixtures||!_fixtures.length){
    ra('GET','/api/fixtures',null,function(fx){
      if(fx&&fx.length){_fixtures=fx;_loadCamCalStatus();}
      else{_calWizRender();}
    });
    return;
  }
  var cams=_fixtures.filter(function(f){return f.fixtureType==='camera';});
  _calWiz.cameras=cams.map(function(f){
    var existing=null;
    _calWiz.cameras.forEach(function(c){if(c.id===f.id)existing=c;});
    return{id:f.id,name:f.name,ip:f.cameraIp||'',camIdx:f.cameraIdx||0,
      selected:existing?existing.selected:!!f.cameraIp,
      calibrated:existing?existing.calibrated:false,
      rmsError:existing?existing.rmsError:null};
  });
  // Check calibration status from each camera node
  _calWiz.cameras.forEach(function(cam){
    if(!cam.ip)return;
    var x=new XMLHttpRequest();
    x.open('GET','/api/cameras/'+cam.id+'/intrinsic');
    x.timeout=5000;
    x.onload=function(){
      try{
        var r=JSON.parse(x.responseText);
        cam.calibrated=!!r.calibrated;
        cam.rmsError=r.calibrated?parseFloat(r.rmsError):null;
        _calWizRender();
      }catch(e){}
    };
    x.send();
  });
  _calWizRender();
}

function _calWizStart(){
  _calWiz.step=1;_calWiz.captures=0;_calWiz.captureLog=[];
  _calWiz.results={};_calWiz.markersPlaced=false;_calWiz.stageMap={};
  showTab('settings');_setSection('cameras');
  _loadCamCalStatus();
}


// Q13-P3 — camera-health dashboard. Composes the calibration-status
// endpoint (tier / rms / marker count / timestamp) for every camera
// into an inline panel below the fixtures list. Toggles open/closed.
var _camHealthOpen=false;
function showCameraHealth(){
  var panel=document.getElementById('cam-health-panel');
  if(!panel){
    // First call — inject container below the fixtures panel.
    var parent=(document.getElementById('lay-fixtures')||document.body).parentNode;
    panel=document.createElement('div');
    panel.id='cam-health-panel';
    panel.style.cssText='margin-top:.6em;padding:.5em;border:1px solid #1e293b;border-radius:6px;background:#0f172a;display:none';
    parent.appendChild(panel);
  }
  _camHealthOpen=!_camHealthOpen;
  if(!_camHealthOpen){panel.style.display='none';return;}
  panel.style.display='block';
  var cams=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera';});
  if(!cams.length){
    panel.innerHTML='<p style="color:#94a3b8;font-size:.82em">No cameras registered yet.</p>';
    return;
  }
  var h='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.4em">'
    +'<b style="color:#cbd5e1;font-size:.82em">Camera Health</b>'
    +'<button class="btn" onclick="showCameraHealth()" style="font-size:.7em;padding:.1em .4em">Close</button></div>';
  h+='<div style="font-size:.75em;color:#94a3b8;margin-bottom:.4em">Per-camera cal tier and fit quality. Run stage-map to update.</div>';
  h+='<table class="tbl" style="width:100%;font-size:.78em"><tr>'
    +'<th>Camera</th><th>Tier</th><th>Markers</th><th>RMS (px)</th>'
    +'<th>Intrinsics</th><th>Pos</th><th>Last Cal</th><th></th></tr>';
  cams.forEach(function(cam){
    h+='<tr id="camhealth-row-'+cam.id+'"><td>'+escapeHtml(cam.name||('cam '+cam.id))+'</td>'
      +'<td colspan="7" style="color:#64748b">loading...</td></tr>';
  });
  h+='</table>';
  panel.innerHTML=h;
  // Populate each row async via /api/cameras/<fid>/calibration-status.
  cams.forEach(function(cam){
    ra('GET','/api/cameras/'+cam.id+'/calibration-status',null,function(r){
      var row=document.getElementById('camhealth-row-'+cam.id);
      if(!row)return;
      if(!r||!r.ok){
        row.innerHTML='<td>'+escapeHtml(cam.name||('cam '+cam.id))+'</td>'
          +'<td colspan="7" style="color:#f87171">'+escapeHtml((r&&r.err)||'query failed')+'</td>';
        return;
      }
      var tierBg = r.tier==='homography'?'#065f46':r.tier==='fov-projection'?'#78350f':'#7f1d1d';
      var tierFg = r.tier==='homography'?'#34d399':r.tier==='fov-projection'?'#fbbf24':'#fca5a5';
      var tierBadge = '<span style="background:'+tierBg+';color:'+tierFg+';padding:1px 6px;border-radius:3px;font-size:.75em;text-transform:uppercase">'+escapeHtml(r.tier||'?')+'</span>';
      var rms = (r.rmsError!=null?parseFloat(r.rmsError).toFixed(2):'—');
      var rmsColor = r.rmsError!=null?(r.rmsError<2?'#4ade80':r.rmsError<10?'#fbbf24':'#f87171'):'#64748b';
      var markers = (r.markersMatched!=null?r.markersMatched:'—');
      var intr = r.intrinsicSource||'—';
      var intrColor = intr==='calibrated'?'#4ade80':'#fbbf24';
      var pos = r.hasPosition?'<span style="color:#4ade80">✓</span>':'<span style="color:#f87171">✗</span>';
      var ts = r.timestamp?(new Date(r.timestamp*1000)).toLocaleString():'—';
      // #619 / #597 — clear-cal actions. Two buttons in a row:
      //   * "H" drops the stage-map homography (after rig move / marker reseating).
      //   * "I" drops the camera-node intrinsic (before re-running Advanced Scan).
      var hBtn = r.calibrated
        ? '<button class="btn" onclick="clearCameraCal('+cam.id+')" '
          +'style="font-size:.7em;padding:.1em .35em;background:#7f1d1d;color:#fca5a5" '
          +'title="#619 Drop this cameras homography (stage-map calibration). Re-run stage-map after a rig move or marker reseating.">Clear H</button>'
        : '';
      var iBtn = '<button class="btn" onclick="clearCameraIntrinsic('+cam.id+')" '
        +'style="font-size:.7em;padding:.1em .35em;background:#78350f;color:#fbbf24;margin-left:.25em" '
        +'title="#597 Reset the camera nodes saved intrinsic calibration. Forces the Advanced Scan wizard to re-capture.">Clear I</button>';
      var clearBtn = (hBtn || '—') + iBtn;
      row.innerHTML='<td>'+escapeHtml(cam.name||('cam '+cam.id))+'</td>'
        +'<td>'+tierBadge+'</td>'
        +'<td>'+markers+'</td>'
        +'<td style="color:'+rmsColor+';font-family:monospace">'+rms+'</td>'
        +'<td style="color:'+intrColor+';font-size:.8em">'+escapeHtml(intr)+'</td>'
        +'<td>'+pos+'</td>'
        +'<td style="font-size:.78em;color:#94a3b8">'+escapeHtml(ts)+'</td>'
        +'<td>'+clearBtn+'</td>';
    });
  });
}


// #619 — discard a camera's stage-map calibration so the operator can
// re-seed after a rig move without falling back to a whole-project
// factory reset. Refresh the panel afterward so the tier badge drops
// from H → FOV or RAW depending on what's left.
function clearCameraCal(fid){
  if(!confirm('Discard this cameras calibration? The operator will need to re-run stage-map before tracking is accurate again.'))return;
  ra('DELETE','/api/cameras/'+fid+'/calibration',null,function(r){
    if(r&&r.ok){
      // Reload fixtures so the H/FOV/RAW badge on the fixtures panel
      // reflects the new cal state, then re-open health to see the row.
      if(typeof loadFixtures==='function'){loadFixtures(function(){
        if(_camHealthOpen){_camHealthOpen=false;showCameraHealth();}
      });}else{
        if(_camHealthOpen){_camHealthOpen=false;showCameraHealth();}
      }
    }else{
      alert('Clear failed: '+((r&&r.err)||'unknown'));
    }
  });
}

// #597 — drop the camera nodes saved intrinsic calibration. Prompts
// before firing; the DELETE is proxied to the camera nodes
// /calibrate/intrinsic endpoint. Triggers an Advanced Scan re-capture
// next time the wizard runs.
function clearCameraIntrinsic(fid){
  if(!confirm('Reset this camera nodes intrinsic calibration? The Advanced Scan wizard will need to re-capture before stereo triangulation works again.'))return;
  ra('DELETE','/api/cameras/'+fid+'/intrinsic',null,function(r){
    if(r&&(r.ok||r.removed)){
      if(_camHealthOpen){_camHealthOpen=false;showCameraHealth();}
      alert('Intrinsic calibration cleared on camera.');
    }else{
      alert('Clear failed: '+((r&&r.err)||'unknown'));
    }
  });
}

function _calWizNext(){
  if(_calWiz.step<6)_calWiz.step++;
  if(_calWiz.step===4){
    // Reset ArUco frames on all selected cameras
    _calWiz.captures=0;_calWiz.captureLog=[];
    _calWiz.cameras.forEach(function(cam){
      if(!cam.selected||!cam.ip)return;
      ra('POST','/api/cameras/'+cam.id+'/aruco/reset',{});
    });
  }
  if(_calWiz.step===5)_calWizCompute();
  _calWizRender();
}

function _calWizBack(){
  if(_calWiz._refreshTimer){clearInterval(_calWiz._refreshTimer);_calWiz._refreshTimer=null;}
  if(_calWiz.step>1)_calWiz.step--;
  _calWizRender();
}

function _calWizReset(){
  if(_calWiz._refreshTimer){clearInterval(_calWiz._refreshTimer);_calWiz._refreshTimer=null;}
  _calWiz.step=1;_calWiz.captures=0;_calWiz.captureLog=[];
  _calWiz.results={};_calWiz.markersPlaced=false;_calWiz.stageMap={};
  _calWizRender();
}

function _calWizRender(){
  var el=document.getElementById('cam-cal-wizard');if(!el)return;
  var w=_calWiz,h='';
  // Print button — always visible at top regardless of step
  h+='<div style="display:flex;gap:.5em;align-items:center;margin-bottom:.8em;padding:.5em;background:#0c1a2a;border-radius:6px;border:1px solid #1e293b">';
  h+='<button class="btn btn-on" onclick="_printAruco()" style="font-size:.82em">Print ArUco Markers</button>';
  h+='<span style="font-size:.75em;color:#64748b">Print before starting — each marker has its ID printed below the pattern</span>';
  h+='</div>';
  // Step indicator bar
  h+='<div style="display:flex;gap:.3em;margin-bottom:1.2em;align-items:center">';
  for(var si=1;si<=6;si++){
    var active=si===w.step,done=si<w.step;
    var labels=['Select','Print','Stage Map','Capture','Compute','Done'];
    h+='<div style="flex:1;text-align:center">';
    h+='<div style="height:4px;background:'+(done?'#22d3ee':active?'#3b82f6':'#1e293b')+';border-radius:2px;margin-bottom:.3em"></div>';
    h+='<span style="font-size:.7em;color:'+(active?'#e2e8f0':done?'#22d3ee':'#475569')+';font-family:Space Grotesk;font-weight:600;letter-spacing:.06em;text-transform:uppercase">'+labels[si-1]+'</span>';
    h+='</div>';
  }
  h+='</div>';

  if(w.step===1)h+=_calWizStep1();
  else if(w.step===2)h+=_calWizStep2();
  else if(w.step===3)h+=_calWizStep3();
  else if(w.step===4)h+=_calWizStep4();
  else if(w.step===5)h+=_calWizStep5();
  else if(w.step===6)h+=_calWizStep6();
  el.innerHTML=h;

  // Start snapshot refresh timer for steps 3 (stage map) and 4 (capture)
  if(w.step===3||w.step===4){
    if(w._refreshTimer)clearInterval(w._refreshTimer);
    w._refreshTimer=setInterval(function(){_calWizRefreshPreviews();},2500);
    setTimeout(function(){_calWizRefreshPreviews();},200);
  }else{
    if(w._refreshTimer){clearInterval(w._refreshTimer);w._refreshTimer=null;}
  }
}

function _calWizStep1(){
  var w=_calWiz,h='';
  h+='<div class="card" style="max-width:640px">';
  h+='<div class="card-title">Step 1: Select Cameras</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Choose which cameras to calibrate. Calibrating the lens (intrinsics) corrects for distortion and provides accurate focal length data needed for point clouds and spatial mapping.</p>';
  if(!w.cameras.length){
    h+='<p style="color:#f59e0b;font-size:.85em">No cameras registered. Add cameras in the Setup tab first.</p>';
    h+='</div>';return h;
  }
  // Select All / Deselect All
  h+='<div style="margin-bottom:.6em;display:flex;gap:.4em">';
  h+='<button class="btn" onclick="_calWizSelectAll(true)" style="font-size:.75em;padding:.2em .6em;background:#1e3a5f;color:#93c5fd">Select All</button>';
  h+='<button class="btn" onclick="_calWizSelectAll(false)" style="font-size:.75em;padding:.2em .6em;background:#1e293b;color:#64748b">Deselect All</button>';
  h+='</div>';
  // Camera list
  w.cameras.forEach(function(cam,i){
    var checked=cam.selected?'checked':'';
    var statusHtml='';
    if(cam.calibrated&&cam.rmsError!==null){
      statusHtml='<span style="color:#4ade80;font-size:.78em">\u2713 RMS='+cam.rmsError.toFixed(2)+'px</span>';
    }else if(cam.calibrated){
      statusHtml='<span style="color:#4ade80;font-size:.78em">\u2713 Calibrated</span>';
    }else{
      statusHtml='<span style="color:#94a3b8;font-size:.78em">Lens not calibrated yet</span>';
    }
    if(!cam.ip)statusHtml='<span style="color:#555;font-size:.78em">No IP — re-register in Setup tab</span>';
    h+='<div style="display:flex;align-items:center;gap:.6em;padding:.4em .5em;border-radius:4px;margin-bottom:.3em;background:'+(cam.selected?'rgba(34,211,238,.06)':'transparent')+';border:1px solid '+(cam.selected?'rgba(34,211,238,.15)':'rgba(51,65,85,.2)')+'">';
    h+='<input type="checkbox" id="calwiz-cam-'+i+'" '+checked+' onchange="_calWizToggleCam('+i+',this.checked)" style="accent-color:#22d3ee">';
    h+='<div style="flex:1"><div style="font-size:.85em;color:#e2e8f0">'+escapeHtml(cam.name)+'</div>';
    h+='<div style="font-size:.72em;color:#64748b">'+escapeHtml(cam.ip||'no IP')+(cam.camIdx?' cam'+cam.camIdx:'')+'</div></div>';
    h+=statusHtml;
    h+='</div>';
  });
  // Navigation
  var selCount=w.cameras.filter(function(c){return c.selected&&c.ip;}).length;
  h+='<div style="display:flex;justify-content:flex-end;gap:.4em;margin-top:.8em">';
  h+='<button class="btn btn-on" onclick="_calWizNext()"'+(selCount===0?' disabled':'')+' style="'+(selCount===0?'opacity:.5;cursor:not-allowed':'')+'">Next \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizStep2(){
  var w=_calWiz,h='';
  h+='<div class="card" style="max-width:640px">';
  h+='<div class="card-title">Step 2: Prepare Markers</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Print ArUco markers and place them within view of the selected cameras. The calibration process uses these markers to compute lens parameters.</p>';
  // Print buttons
  h+='<div style="display:flex;gap:.5em;flex-wrap:wrap;margin-bottom:1em">';
  h+='<button class="btn btn-on" onclick="_printAruco()" style="font-size:.85em">Print ArUco Markers</button>';
  h+='<button class="btn" onclick="_printCheckerboard()" style="font-size:.78em;padding:.3em .6em;background:#1e293b;color:#94a3b8">Print Checkerboard (close-range)</button>';
  h+='</div>';
  // Instructions card
  h+='<div style="background:rgba(34,211,238,.04);border:1px solid rgba(34,211,238,.12);border-radius:6px;padding:.8em 1em;margin-bottom:1em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em;font-weight:600">Placement instructions:</div>';
  h+='<ul style="font-size:.82em;color:#94a3b8;margin-left:1.2em;line-height:1.8">';
  h+='<li>Print 6 markers, one per sheet (A4 or Letter)</li>';
  h+='<li>Tape to boxes, walls, or truss at <strong>different heights and angles</strong></li>';
  h+='<li>Place within camera view, at various distances (1m to 5m+)</li>';
  h+='<li>Markers should be flat, well-lit, and not crumpled</li>';
  h+='<li>Avoid placing all markers on the same plane</li>';
  h+='</ul></div>';
  // Diagram: ideal placement
  h+='<div style="background:#0a0f13;border:1px solid rgba(51,65,85,.3);border-radius:6px;padding:.8em;margin-bottom:1em;text-align:center">';
  h+='<svg width="280" height="120" viewBox="0 0 280 120" style="max-width:100%">';
  // Camera
  h+='<rect x="125" y="5" width="30" height="20" rx="3" fill="#334155" stroke="#64748b"/>';
  h+='<circle cx="140" cy="15" r="6" fill="#1e293b" stroke="#22d3ee"/>';
  h+='<text x="140" y="38" text-anchor="middle" fill="#64748b" font-size="8">Camera</text>';
  // FOV lines
  h+='<line x1="140" y1="25" x2="30" y2="110" stroke="#22d3ee" stroke-width="0.5" stroke-dasharray="3,3" opacity="0.4"/>';
  h+='<line x1="140" y1="25" x2="250" y2="110" stroke="#22d3ee" stroke-width="0.5" stroke-dasharray="3,3" opacity="0.4"/>';
  // Markers at different positions and angles
  h+='<rect x="40" y="55" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(-10,49,64)"/><text x="49" y="84" text-anchor="middle" fill="#64748b" font-size="7">Low</text>';
  h+='<rect x="95" y="45" width="18" height="18" rx="1" fill="none" stroke="#4ade80"/><text x="104" y="74" text-anchor="middle" fill="#64748b" font-size="7">Mid</text>';
  h+='<rect x="165" y="40" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(15,174,49)"/><text x="174" y="69" text-anchor="middle" fill="#64748b" font-size="7">Angled</text>';
  h+='<rect x="220" y="65" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(-5,229,74)"/><text x="229" y="94" text-anchor="middle" fill="#64748b" font-size="7">Far</text>';
  h+='<rect x="60" y="90" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(8,69,99)"/><text x="69" y="118" text-anchor="middle" fill="#64748b" font-size="7">Floor</text>';
  h+='<rect x="140" y="80" width="18" height="18" rx="1" fill="none" stroke="#4ade80" transform="rotate(-12,149,89)"/><text x="149" y="108" text-anchor="middle" fill="#64748b" font-size="7">Wall</text>';
  h+='</svg>';
  h+='<div style="font-size:.72em;color:#475569;margin-top:.3em">Place markers at varying heights, angles, and distances</div>';
  h+='</div>';
  // Confirmation checkbox
  h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.8em;padding:.5em;border-radius:4px;background:rgba(34,211,238,.04)">';
  h+='<input type="checkbox" id="calwiz-placed" onchange="_calWiz.markersPlaced=this.checked;_calWizRender()" style="accent-color:#22d3ee"'+(w.markersPlaced?' checked':'')+'>';
  h+='<label for="calwiz-placed" style="font-size:.85em;color:#e2e8f0;margin:0;cursor:pointer">I have placed the markers within camera view</label>';
  h+='</div>';
  // Navigation
  h+='<div style="display:flex;justify-content:space-between;gap:.4em;margin-top:.5em">';
  h+='<button class="btn" onclick="_calWizBack()" style="background:#1e293b;color:#94a3b8">\u2190 Back</button>';
  h+='<button class="btn btn-on" onclick="_calWizNext()"'+(w.markersPlaced?'':' disabled')+' style="'+(w.markersPlaced?'':'opacity:.5;cursor:not-allowed')+'">Next \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizStep3(){
  var w=_calWiz,h='';
  var selCams=w.cameras.filter(function(c){return c.selected&&c.ip;});
  var hasResult=Object.keys(w.stageMap).length>0;
  h+='<div class="card" style="max-width:720px">';
  h+='<div class="card-title">Step 3: Stage Mapping</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Place a printed ArUco marker (with ID visible) flat on the stage floor at a position you can measure. Enter the marker ID and its stage coordinates below.</p>';
  // Camera previews (auto-refreshing)
  h+='<div style="display:flex;flex-wrap:wrap;gap:.8em;margin-bottom:1em">';
  selCams.forEach(function(cam){
    h+='<div style="flex:1;min-width:200px;max-width:340px">';
    h+='<div style="font-size:.82em;color:#e2e8f0;margin-bottom:.3em;font-weight:600">'+escapeHtml(cam.name)+'</div>';
    h+='<div style="position:relative;background:#0a0f13;border:1px solid #334155;border-radius:4px;overflow:hidden;min-height:150px">';
    h+='<img id="calwiz-preview-'+cam.id+'" src="" style="width:100%;display:none;border-radius:4px" alt="Preview">';
    h+='<div id="calwiz-preview-msg-'+cam.id+'" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:.78em;color:#475569">Loading preview...</div>';
    h+='</div></div>';
  });
  h+='</div>';
  // Marker 1 inputs
  h+='<div style="background:rgba(34,211,238,.04);border:1px solid rgba(34,211,238,.12);border-radius:6px;padding:.8em 1em;margin-bottom:.8em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em;font-weight:600">Marker 1</div>';
  h+='<div style="display:flex;gap:.8em;flex-wrap:wrap;align-items:end">';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">ID</label>';
  h+='<input id="sm-id1" type="number" min="0" max="5" value="0" style="width:60px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">X (mm from stage right)</label>';
  h+='<input id="sm-x1" type="number" value="0" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">Y (mm from back wall)</label>';
  h+='<input id="sm-y1" type="number" value="0" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">Z (mm from floor)</label>';
  h+='<input id="sm-z1" type="number" value="0" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='</div></div>';
  // Marker 2 (optional, collapsed)
  h+='<div style="margin-bottom:.8em">';
  h+='<details id="sm-marker2-details">';
  h+='<summary style="font-size:.82em;color:#22d3ee;cursor:pointer;margin-bottom:.5em">+ Add second marker (optional)</summary>';
  h+='<div style="background:rgba(34,211,238,.04);border:1px solid rgba(34,211,238,.12);border-radius:6px;padding:.8em 1em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em;font-weight:600">Marker 2 (optional)</div>';
  h+='<div style="display:flex;gap:.8em;flex-wrap:wrap;align-items:end">';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">ID</label>';
  h+='<input id="sm-id2" type="number" min="0" max="5" value="" style="width:60px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">X (mm from stage right)</label>';
  h+='<input id="sm-x2" type="number" value="" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">Y (mm from back wall)</label>';
  h+='<input id="sm-y2" type="number" value="" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8;display:block;margin-bottom:.2em">Z (mm from floor)</label>';
  h+='<input id="sm-z2" type="number" value="" style="width:100px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:.3em .5em;border-radius:4px;font-size:.85em"></div>';
  h+='</div></div>';
  h+='</details></div>';
  // Compute button
  h+='<button class="btn btn-on" id="sm-compute-btn" onclick="_calWizComputeStageMap()" style="font-size:.9em;padding:.5em 1.5em;margin-bottom:.8em">Compute Stage Map</button>';
  // Result area
  h+='<div id="sm-result" style="margin-bottom:.8em">';
  if(hasResult){
    h+=_calWizStageMapResultHtml();
  }
  h+='</div>';
  // Navigation
  h+='<div style="display:flex;justify-content:space-between;gap:.4em;margin-top:.5em">';
  h+='<button class="btn" onclick="_calWizBack()" style="background:#1e293b;color:#94a3b8">\u2190 Back</button>';
  h+='<button class="btn btn-on" onclick="_calWizNext()" style="'+(hasResult?'':'opacity:.7')+'">Next \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizComputeStageMap(){
  var btn=document.getElementById('sm-compute-btn');
  if(btn){btn.disabled=true;btn.textContent='Computing...';}
  var markers={};
  var id1El=document.getElementById('sm-id1');
  var x1El=document.getElementById('sm-x1');
  var y1El=document.getElementById('sm-y1');
  var z1El=document.getElementById('sm-z1');
  if(id1El&&x1El&&z1El&&id1El.value!==''){
    markers[id1El.value]={x:parseInt(x1El.value)||0,y:parseInt(y1El?y1El.value:0)||0,z:parseInt(z1El.value)||0};
  }
  // Optional second marker
  var id2El=document.getElementById('sm-id2');
  var x2El=document.getElementById('sm-x2');
  var y2El=document.getElementById('sm-y2');
  var z2El=document.getElementById('sm-z2');
  if(id2El&&id2El.value!==''){
    markers[id2El.value]={x:parseInt(x2El.value)||0,y:parseInt(y2El?y2El.value:0)||0,z:parseInt(z2El.value)||0};
  }
  if(Object.keys(markers).length===0){
    if(btn){btn.disabled=false;btn.textContent='Compute Stage Map';}
    var resEl=document.getElementById('sm-result');
    if(resEl)resEl.innerHTML='<div style="color:#f87171;font-size:.85em">Enter at least one marker ID and coordinates.</div>';
    return;
  }
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  var done=0,total=selCams.length;
  _calWiz.stageMap={};
  selCams.forEach(function(cam){
    ra('POST','/api/cameras/'+cam.id+'/stage-map',
      {markers:markers,markerSize:150},
      function(r){
        done++;
        if(r&&r.ok){
          _calWiz.stageMap[cam.id]={
            cameraPositionDiagnostic:r.cameraPositionDiagnostic||null,
            rmsError:r.rmsError!=null?parseFloat(r.rmsError):null,
            distances:r.distances||null,
            // #331 — surface which intrinsics path the solver used so
            // operators can tell a bogus pose from an FOV-estimate pose.
            intrinsicSource:r.intrinsicSource||'fov-estimate',
            markersMatched:r.markersMatched||0,
            markersUsed:Object.keys(markers).length
          };
        }else{
          _calWiz.stageMap[cam.id]={error:r&&r.err||'Stage map computation failed'};
        }
        if(done>=total){
          if(btn){btn.disabled=false;btn.textContent='Compute Stage Map';}
          var resEl=document.getElementById('sm-result');
          if(resEl)resEl.innerHTML=_calWizStageMapResultHtml();
        }
      });
  });
}

function _calWizStageMapResultHtml(){
  var h='';
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  h+='<div style="background:rgba(34,211,238,.04);border:1px solid rgba(34,211,238,.12);border-radius:6px;padding:.8em 1em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;margin-bottom:.5em;font-weight:600">Stage Map Results</div>';
  h+='<div style="font-size:.72em;color:#fbbf24;margin-bottom:.3em">Diagnostic pose is from solvePnP on coplanar markers — not authoritative. Use layout position for real placement. Homography (RMS Error) is what downstream uses.</div>';
  h+='<table class="tbl" style="margin-bottom:.4em"><tr><th>Camera</th><th>PnP Diagnostic (mm)</th><th>RMS Error</th><th>Status</th></tr>';
  selCams.forEach(function(cam){
    var r=_calWiz.stageMap[cam.id];
    if(!r){
      h+='<tr><td>'+escapeHtml(cam.name)+'</td><td colspan="3" style="color:#64748b">Pending...</td></tr>';
      return;
    }
    if(r.error){
      h+='<tr><td>'+escapeHtml(cam.name)+'</td><td colspan="3" style="color:#f87171">'+escapeHtml(r.error)+'</td></tr>';
      return;
    }
    var pos=r.cameraPositionDiagnostic;
    var posStr=pos?'X:'+Math.round(pos.x)+' Y:'+Math.round(pos.y)+' Z:'+Math.round(pos.z):'N/A';
    var rmsStr=r.rmsError!=null?r.rmsError.toFixed(2)+'px':'N/A';
    var rmsColor=r.rmsError!=null?(r.rmsError<2?'#4ade80':r.rmsError<5?'#fbbf24':'#f87171'):'#64748b';
    // #331 — warn when solvePnP was run with an FOV-estimate instead of
    // the proper calibrated intrinsics; that's the #1 cause of wild Z
    // values and the operator needs to see it next to the position.
    var srcLbl=r.intrinsicSource==='calibrated'
      ?' <span style="color:#4ade80;font-size:.72em" title="Used saved intrinsics from ArUco calibration">(cal)</span>'
      :' <span style="color:#fbbf24;font-size:.72em" title="No ArUco calibration found — used FOV nameplate estimate. Run calibration for accurate pose.">(est)</span>';
    h+='<tr><td>'+escapeHtml(cam.name)+'</td>';
    h+='<td style="font-family:monospace;font-size:.82em;color:#e2e8f0">'+posStr+srcLbl+'</td>';
    h+='<td style="color:'+rmsColor+'">'+rmsStr+'</td>';
    h+='<td style="color:#4ade80">OK</td></tr>';
  });
  h+='</table>';
  if(selCams.length>1){
    // Show inter-camera distances
    var camsWithPos=selCams.filter(function(c){var r=_calWiz.stageMap[c.id];return r&&r.cameraPositionDiagnostic&&!r.error;});
    if(camsWithPos.length>1){
      h+='<div style="font-size:.78em;color:#94a3b8;margin-top:.4em">Inter-camera distances: ';
      for(var i=0;i<camsWithPos.length;i++){
        for(var j=i+1;j<camsWithPos.length;j++){
          var p1=_calWiz.stageMap[camsWithPos[i].id].cameraPositionDiagnostic;
          var p2=_calWiz.stageMap[camsWithPos[j].id].cameraPositionDiagnostic;
          var dx=p1.x-p2.x,dy=p1.y-p2.y,dz=p1.z-p2.z;
          var dist=Math.sqrt(dx*dx+dy*dy+dz*dz);
          h+=escapeHtml(camsWithPos[i].name)+' \u2194 '+escapeHtml(camsWithPos[j].name)+': '+Math.round(dist)+'mm ';
        }
      }
      h+='</div>';
    }
  }
  h+='</div>';
  return h;
}

function _calWizStep4(){
  var w=_calWiz,h='';
  var selCams=w.cameras.filter(function(c){return c.selected&&c.ip;});
  var ready=w.captures>=_calWizMinCaptures;
  h+='<div class="card" style="max-width:720px">';
  h+='<div class="card-title">Step 4: Capture Frames</div>';
  h+='<p style="font-size:.85em;color:#94a3b8;margin-bottom:.6em">Capture images from each camera with markers visible. Move some markers between captures to improve coverage. Minimum '+_calWizMinCaptures+' captures required.</p>';
  // Progress bar
  var prog=Math.min(w.captures/_calWizMinCaptures*100,100);
  h+='<div style="display:flex;align-items:center;gap:.6em;margin-bottom:.8em">';
  h+='<div class="prog-bar" style="flex:1;height:8px"><div class="prog-fill" style="width:'+prog+'%"></div></div>';
  h+='<span style="font-size:.82em;color:'+(ready?'#4ade80':'#94a3b8')+'">Capture '+w.captures+' of '+_calWizMinCaptures+(w.captures>=_calWizMinCaptures?' (minimum met)':' (minimum '+_calWizMinCaptures+' required)')+'</span>';
  h+='</div>';
  // Camera previews
  h+='<div style="display:flex;flex-wrap:wrap;gap:.8em;margin-bottom:.8em">';
  selCams.forEach(function(cam){
    h+='<div style="flex:1;min-width:200px;max-width:340px">';
    h+='<div style="font-size:.82em;color:#e2e8f0;margin-bottom:.3em;font-weight:600">'+escapeHtml(cam.name)+'</div>';
    h+='<div style="position:relative;background:#0a0f13;border:1px solid #334155;border-radius:4px;overflow:hidden;min-height:150px">';
    h+='<img id="calwiz-preview-'+cam.id+'" src="" style="width:100%;display:none;border-radius:4px" alt="Preview">';
    h+='<div id="calwiz-preview-msg-'+cam.id+'" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:.78em;color:#475569">Loading preview...</div>';
    h+='</div>';
    h+='<div id="calwiz-cam-status-'+cam.id+'" style="font-size:.75em;color:#64748b;margin-top:.2em">Ready</div>';
    h+='</div>';
  });
  h+='</div>';
  // Capture button
  h+='<div style="display:flex;gap:.5em;align-items:center;margin-bottom:.6em">';
  h+='<button class="btn btn-on" id="calwiz-capture-btn" onclick="_calWizCapture()" style="flex:0 0 auto;font-size:.9em;padding:.5em 1.5em">Capture All Cameras</button>';
  if(w.captures>0&&w.captures<_calWizMinCaptures){
    h+='<span style="font-size:.82em;color:#fbbf24">Move some markers and capture again</span>';
  }
  h+='</div>';
  // Capture log
  if(w.captureLog.length){
    h+='<div style="max-height:140px;overflow-y:auto;border:1px solid rgba(51,65,85,.2);border-radius:4px;padding:.4em .6em;margin-bottom:.6em;font-size:.78em">';
    w.captureLog.forEach(function(entry){h+=entry;});
    h+='</div>';
  }
  // Navigation
  h+='<div style="display:flex;justify-content:space-between;gap:.4em;margin-top:.5em">';
  h+='<button class="btn" onclick="_calWizBack()" style="background:#1e293b;color:#94a3b8">\u2190 Back</button>';
  h+='<button class="btn btn-on" onclick="_calWizNext()"'+(ready?'':' disabled')+' style="'+(ready?'':'opacity:.5;cursor:not-allowed')+'">Compute Calibration \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizStep5(){
  var w=_calWiz,h='';
  var selCams=w.cameras.filter(function(c){return c.selected&&c.ip;});
  var allDone=true,anyResult=false;
  selCams.forEach(function(c){var k=c.id;if(!w.results[k])allDone=false;else anyResult=true;});
  h+='<div class="card" style="max-width:720px">';
  h+='<div class="card-title">Step 5: Compute & Evaluate</div>';
  if(!allDone){
    h+='<div style="display:flex;align-items:center;gap:.6em;margin-bottom:1em;padding:.6em;background:rgba(59,130,246,.06);border-radius:4px">';
    h+='<div style="width:20px;height:20px;border:2px solid #3b82f6;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite"></div>';
    h+='<span style="font-size:.85em;color:#93c5fd">Computing calibration for all cameras...</span>';
    h+='</div>';
    h+='<style>@keyframes spin{to{transform:rotate(360deg)}}</style>';
  }
  // Results table
  if(anyResult){
    h+='<table class="tbl" style="margin-bottom:.8em"><tr><th>Camera</th><th>RMS Error</th><th>fx</th><th>fy</th><th>fx/fy</th><th>Quality</th><th>Action</th></tr>';
    selCams.forEach(function(cam){
      var r=w.results[cam.id];
      if(!r){
        h+='<tr><td>'+escapeHtml(cam.name)+'</td><td colspan="6" style="color:#64748b">Computing...</td></tr>';
        return;
      }
      if(r.error){
        h+='<tr><td>'+escapeHtml(cam.name)+'</td><td colspan="5" style="color:#f66">'+escapeHtml(r.error)+'</td>';
        h+='<td><button class="btn" onclick="_calWizRecal('+cam.id+')" style="font-size:.72em;padding:.15em .4em;background:#7f1d1d;color:#fca5a5">Redo</button></td></tr>';
        return;
      }
      var qColor={'Excellent':'#4ade80','Good':'#22d3ee','Fair':'#fbbf24','Poor':'#f87171'};
      h+='<tr><td>'+escapeHtml(cam.name)+'</td>';
      h+='<td style="color:'+(r.rms<1.5?'#4ade80':r.rms<3?'#fbbf24':'#f87171')+'">'+r.rms.toFixed(3)+'px</td>';
      h+='<td>'+r.fx.toFixed(1)+'</td>';
      h+='<td>'+r.fy.toFixed(1)+'</td>';
      h+='<td style="color:'+(r.ratio>=0.95&&r.ratio<=1.05?'#4ade80':r.ratio>=0.9&&r.ratio<=1.1?'#fbbf24':'#f87171')+'">'+r.ratio.toFixed(3)+'</td>';
      h+='<td><span class="badge" style="background:'+(r.quality==='Excellent'?'#14532d':r.quality==='Good'?'#164e63':r.quality==='Fair'?'#78350f':'#7f1d1d')+';color:'+(qColor[r.quality]||'#888')+'">'+r.quality+'</span></td>';
      h+='<td>';
      if(!r.accepted){
        h+='<button class="btn btn-on" onclick="_calWizAccept('+cam.id+')" style="font-size:.72em;padding:.15em .4em">Accept</button> ';
        h+='<button class="btn" onclick="_calWizRecal('+cam.id+')" style="font-size:.72em;padding:.15em .4em;background:#1e293b;color:#94a3b8">Redo</button>';
      }else{
        h+='<span style="color:#4ade80;font-size:.82em">\u2713 Accepted</span>';
      }
      h+='</td></tr>';
    });
    h+='</table>';
    // Bulk actions
    var acceptableCount=0,acceptedCount=0;
    selCams.forEach(function(c){var r=w.results[c.id];if(r&&!r.error){acceptableCount++;if(r.accepted)acceptedCount++;}});
    if(acceptableCount>0&&acceptedCount<acceptableCount){
      h+='<div style="display:flex;gap:.4em;margin-bottom:.6em">';
      h+='<button class="btn btn-on" onclick="_calWizAcceptAll()" style="font-size:.82em">Accept All</button>';
      h+='</div>';
    }
  }
  // Navigation
  var allAccepted=true;
  selCams.forEach(function(c){var r=w.results[c.id];if(!r||(!r.accepted&&!r.error))allAccepted=false;});
  h+='<div style="display:flex;justify-content:space-between;gap:.4em;margin-top:.5em">';
  h+='<button class="btn" onclick="_calWizBack()" style="background:#1e293b;color:#94a3b8">\u2190 Back to Capture</button>';
  h+='<button class="btn btn-on" onclick="_calWizNext()"'+(allAccepted&&allDone?'':' disabled')+' style="'+(allAccepted&&allDone?'':'opacity:.5;cursor:not-allowed')+'">Finish \u2192</button>';
  h+='</div></div>';
  return h;
}

function _calWizStep6(){
  var w=_calWiz,h='';
  var selCams=w.cameras.filter(function(c){return c.selected&&c.ip;});
  var successCount=0;
  selCams.forEach(function(c){var r=w.results[c.id];if(r&&r.accepted)successCount++;});
  h+='<div class="card" style="max-width:640px;text-align:center">';
  h+='<div style="font-size:2.5em;margin-bottom:.2em">\u2713</div>';
  h+='<div class="card-title" style="font-size:1.1em;text-align:center">Calibration Complete</div>';
  h+='<p style="font-size:.9em;color:#94a3b8;margin-bottom:1em">'+successCount+' camera'+(successCount!==1?'s':'')+' calibrated successfully.</p>';
  // Summary table
  h+='<table class="tbl" style="margin:0 auto .8em;max-width:400px"><tr><th>Camera</th><th>Quality</th><th>RMS</th></tr>';
  selCams.forEach(function(cam){
    var r=w.results[cam.id];
    if(!r||r.error){
      h+='<tr><td>'+escapeHtml(cam.name)+'</td><td style="color:#f87171">Failed</td><td>-</td></tr>';
      return;
    }
    var qColor={'Excellent':'#4ade80','Good':'#22d3ee','Fair':'#fbbf24','Poor':'#f87171'};
    h+='<tr><td>'+escapeHtml(cam.name)+'</td>';
    h+='<td style="color:'+(qColor[r.quality]||'#888')+'">'+r.quality+'</td>';
    h+='<td>'+r.rms.toFixed(3)+'px</td></tr>';
  });
  h+='</table>';
  h+='<div style="font-size:.82em;color:#64748b;margin-bottom:1em;padding:.5em;background:rgba(34,211,238,.04);border-radius:4px">Calibration is saved on each camera node and will be used automatically for all point clouds and spatial mapping.</div>';
  h+='<button class="btn btn-on" onclick="_calWizReset()" style="font-size:.9em;padding:.5em 1.5em">Done</button>';
  h+='</div>';
  return h;
}

function _calWizSelectAll(val){
  _calWiz.cameras.forEach(function(c){if(c.ip)c.selected=val;});
  _calWizRender();
}

function _calWizToggleCam(idx,val){
  if(_calWiz.cameras[idx])_calWiz.cameras[idx].selected=val;
  _calWizRender();
}

function _calWizRefreshPreviews(){
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  selCams.forEach(function(cam){
    var img=document.getElementById('calwiz-preview-'+cam.id);
    var msg=document.getElementById('calwiz-preview-msg-'+cam.id);
    if(!img)return;
    var t=Date.now();
    var newImg=new Image();
    newImg.onload=function(){
      img.src=newImg.src;img.style.display='block';
      if(msg)msg.style.display='none';
    };
    newImg.onerror=function(){
      if(msg){msg.textContent='Camera offline';msg.style.display='block';}
    };
    newImg.src='http://'+cam.ip+':5000/snapshot?cam='+(cam.camIdx||0)+'&t='+t;
  });
}

function _calWizCapture(){
  var btn=document.getElementById('calwiz-capture-btn');
  if(btn){btn.disabled=true;btn.textContent='Capturing...';}
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  var done=0,total=selCams.length;
  _calWiz.captures++;
  /* Group cameras by IP so cameras on the same hardware node are captured
     serially (avoids USB bus contention), while different nodes run in parallel. */
  var groups={};
  selCams.forEach(function(cam){
    if(!groups[cam.ip])groups[cam.ip]=[];
    groups[cam.ip].push(cam);
  });
  function captureCam(cam,cb){
    var statusEl=document.getElementById('calwiz-cam-status-'+cam.id);
    if(statusEl)statusEl.innerHTML='<span style="color:#93c5fd">Capturing...</span>';
    ra('POST','/api/cameras/'+cam.id+'/aruco/capture',{},function(r){
      var entry='';
      if(!r||!r.ok){
        entry='<div style="color:#f66">Capture '+_calWiz.captures+' - '+escapeHtml(cam.name)+': Failed</div>';
        if(statusEl)statusEl.innerHTML='<span style="color:#f66">Capture failed</span>';
      }else{
        var cams=r.cameras||[r];
        cams.forEach(function(c){
          if(c.markersFound>0){
            entry='<div style="color:#4ade80">\u2713 Capture '+_calWiz.captures+' - '+escapeHtml(cam.name)+': '+c.markersFound+' markers (IDs: '+(c.ids||[]).join(',')+'), total frames: '+c.frameCount+'</div>';
            if(statusEl)statusEl.innerHTML='<span style="color:#4ade80">\u2713 '+c.frameCount+' frames, '+c.markersFound+' markers last</span>';
          }else{
            entry='<div style="color:#f59e0b">\u2717 Capture '+_calWiz.captures+' - '+escapeHtml(cam.name)+': No markers found</div>';
            if(statusEl)statusEl.innerHTML='<span style="color:#f59e0b">No markers found</span>';
          }
        });
      }
      _calWiz.captureLog.push(entry);
      done++;
      if(done>=total){
        if(btn){btn.disabled=false;btn.textContent='Capture All Cameras';}
        _calWizRender();
      }
      cb();
    });
  }
  /* Each IP group runs serially; different IP groups run in parallel */
  Object.keys(groups).forEach(function(ip){
    var camsOnNode=groups[ip];
    (function captureNext(idx){
      if(idx>=camsOnNode.length)return;
      captureCam(camsOnNode[idx],function(){captureNext(idx+1);});
    })(0);
  });
}

function _calWizCompute(){
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  selCams.forEach(function(cam){
    ra('POST','/api/cameras/'+cam.id+'/aruco/compute',{markerSize:150},function(r){
      if(!r||!r.ok){
        _calWiz.results[cam.id]={error:r&&r.err||'Computation failed'};
      }else{
        var rms=parseFloat(r.rmsError)||0;
        var fx=parseFloat(r.fx)||0;
        var fy=parseFloat(r.fy)||0;
        var ratio=fy>0?fx/fy:0;
        var quality='Poor';
        if(rms<0.5&&ratio>=0.95&&ratio<=1.05)quality='Excellent';
        else if(rms<1.5&&ratio>=0.9&&ratio<=1.1)quality='Good';
        else if(rms<3)quality='Fair';
        _calWiz.results[cam.id]={rms:rms,fx:fx,fy:fy,cx:parseFloat(r.cx)||0,cy:parseFloat(r.cy)||0,
          ratio:ratio,quality:quality,frameCount:r.frameCount||0,accepted:false};
        // Update local fixture data
        cam.calibrated=true;cam.rmsError=rms;
        (_fixtures||[]).forEach(function(f){if(f.id===cam.id)f.intrinsicCalibrated=true;});
      }
      _calWizRender();
    });
  });
}

function _calWizAccept(camId){
  if(_calWiz.results[camId])_calWiz.results[camId].accepted=true;
  _calWizRender();
}

function _calWizAcceptAll(){
  var selCams=_calWiz.cameras.filter(function(c){return c.selected&&c.ip;});
  selCams.forEach(function(c){if(_calWiz.results[c.id]&&!_calWiz.results[c.id].error)_calWiz.results[c.id].accepted=true;});
  _calWizRender();
}

function _calWizRecal(camId){
  delete _calWiz.results[camId];
  _calWiz.step=4;
  _calWizRender();
}

// Legacy alias — called from layout panel and tests
function _intrinsicCalStart(fid){
  // Pre-select just this camera and jump to wizard
  _calWiz.step=1;_calWiz.captures=0;_calWiz.captureLog=[];
  _calWiz.results={};_calWiz.markersPlaced=false;_calWiz.stageMap={};
  var cams=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera';});
  _calWiz.cameras=cams.map(function(f){
    return{id:f.id,name:f.name,ip:f.cameraIp||'',camIdx:f.cameraIdx||0,
      selected:f.id===fid,calibrated:false,rmsError:null};
  });
  showTab('settings');_setSection('cameras');
}

function _moverCalStart(fixId){
  _moverCalFid=fixId;
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===fixId)f=fx;});
  var hasCams=(_fixtures||[]).some(function(fx){return fx.fixtureType==='camera'&&fx.cameraIp;});
  var h='<div style="min-width:400px">';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">Choose calibration method for <strong>'+escapeHtml(f?f.name:'fixture')+'</strong>:</div>';
  h+='<div style="display:flex;gap:.6em;flex-direction:column">';
  // Automatic option
  h+='<div class="card" style="padding:.8em;cursor:'+(hasCams?'pointer':'default')+';opacity:'+(hasCams?'1':'.5')+'" '+(hasCams?'onclick="_moverCalAutoStart()"':'')+' >';
  h+='<div style="font-size:.95em;color:#e2e8f0;font-weight:600">\ud83d\udcf7 Automatic (Camera)</div>';
  h+='<div style="font-size:.8em;color:#94a3b8;margin-top:.2em">Uses camera beam detection to discover and map the fixture automatically.</div>';
  if(!hasCams)h+='<div style="font-size:.78em;color:#f59e0b;margin-top:.2em">No cameras in layout \u2014 add a camera fixture first.</div>';
  h+='</div>';
  // Manual option
  h+='<div class="card" style="padding:.8em;cursor:pointer" onclick="_moverCalManualStart('+fixId+')">';
  h+='<div style="font-size:.95em;color:#e2e8f0;font-weight:600">\ud83c\udfaf Manual (Jog to Markers)</div>';
  h+='<div style="font-size:.8em;color:#94a3b8;margin-top:.2em">Place markers at known positions, then aim the beam at each one. Works without cameras.</div>';
  h+='</div></div>';
  // Verify existing calibration option
  if(f&&f.moverCalibrated){
    h+='<div class="card" style="padding:.8em;cursor:pointer" onclick="_manCalVerifyExisting('+fixId+')">';
    h+='<div style="font-size:.95em;color:#e2e8f0;font-weight:600">\u2705 Verify Existing Calibration</div>';
    h+='<div style="font-size:.8em;color:#94a3b8;margin-top:.2em">Test saved calibration positions. Send the beam to each recorded marker to confirm accuracy.</div>';
    h+='</div>';
  }
  h+='</div>';
  h+='<div style="margin-top:.6em"><button class="btn btn-off" onclick="closeModal()">Cancel</button></div>';
  h+='</div>';
  document.getElementById('modal-title').textContent='Calibrate Mover';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}
// #502 — calibration wizard modal (v2). Shows:
//   - fixture name + current fit quality badge (if already calibrated)
//   - warmup toggle, colour picker, mode toggle (legacy / v2-targets)
//   - target preview (from /api/calibration/mover/<fid>/targets)
//   - progress bar + per-target table during the run
//   - fit + verification + residual table on completion
//   - [Recalibrate fast] button for subsequent runs (warm-start, #505)
//
// Keep the old entry point name so the rest of the SPA's wiring doesn't
// need to change — fixture cards / dashboard cal buttons all invoke
// `_moverCalAutoStart` and expect it to render its own modal body.
function _moverCalAutoStart(){
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===_moverCalFid)f=fx;});
  var cal=f&&f.moverCalibrated;
  var h='<div style="min-width:480px">';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">';
  h+='Automatic calibration for <strong>'+escapeHtml(f?f.name:'fixture')+'</strong>.';
  h+='</div>';

  // Existing-fit summary block — shown when the fixture already has a v2 model.
  h+='<div id="mcal-existing" style="display:none"></div>';

  h+='<div class="card" style="padding:.6em;margin-bottom:.6em" id="mcal-options">';
  h+='<div style="font-size:.82em;color:#f59e0b;margin-bottom:.3em">\u26a0 Ensure Art-Net engine is running and fixture responds to DMX.</div>';
  h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.5em">Dim or turn off room lights for best results.</div>';
  // Stage geometry pre-check banner (#579) — populated by _moverCalGeometryCheck
  h+='<div id="mcal-geom" style="font-size:.78em;margin-bottom:.5em;padding:.35em .5em;background:#0f172a;border:1px solid #1e293b;border-radius:4px;color:#64748b">Checking stage geometry…</div>';
  h+='<div style="display:grid;grid-template-columns:120px 1fr;gap:.4em;align-items:center;font-size:.82em">';
  h+='<label style="color:#94a3b8;margin:0">Beam color:</label>';
  h+='<select id="mcal-color" style="font-size:.82em;padding:2px 4px">';
  h+='<option value="white">White</option><option value="green" selected>Green</option><option value="magenta">Magenta</option><option value="red">Red</option><option value="blue">Blue</option>';
  h+='</select>';
  h+='<label style="color:#94a3b8;margin:0">Method:</label>';
  h+='<select id="mcal-mode" style="font-size:.82em;padding:2px 4px" onchange="_moverCalModeChanged()">';
  // #713 C — `legacy` removed. The all-auto path still falls back to
  // legacy server-side; a separate PR can delete the server thread
  // body now that the SPA no longer offers `mode=legacy`.
  // #720 PR-5 — SMART promoted to default once probe + solve landed.
  // Legacy modes remain in the list for side-by-side validation; PR-7
  // deletes them.
  h+='<option value="smart" selected>SMART — automatic, camera+floor aware (#720)</option>';
  h+='<option value="all-auto">All Auto — markers first, fallback to legacy BFS</option>';
  h+='<option value="markers">Markers only — requires surveyed ArUco markers (#610)</option>';
  h+='<option value="v2">v2 target-driven (#499, requires stage-map homography)</option>';
  h+='</select>';
  h+='<label style="color:#94a3b8;margin:0">Warm-up:</label>';
  h+='<label style="color:#e2e8f0;font-size:.8em;margin:0"><input type="checkbox" id="mcal-warmup" style="margin-right:.3em">Sweep pan/tilt for 30s before sampling (thermal settle — #513)</label>';
  h+='</div>';
  // #681 — Advanced options panel. Expandable, blank = use shipped default.
  // Each row maps to a calibrationTuning key; saved against /api/settings.
  h+='<details id="mcal-adv" style="margin-top:.5em;font-size:.82em">';
  h+='<summary style="cursor:pointer;color:#94a3b8">Advanced options</summary>';
  h+='<div id="mcal-adv-body" style="margin-top:.4em;display:grid;grid-template-columns:240px 1fr;gap:.3em .6em;align-items:center">Loading…</div>';
  h+='<div style="font-size:.72em;color:#64748b;margin-top:.3em">Changes save immediately to Settings → Advanced → Calibration Timeouts (#680).</div>';
  h+='</details>';

  // Target preview (populated below when mode=v2)
  h+='<div id="mcal-targets-preview" style="display:none;margin-top:.5em;border-top:1px solid #334155;padding-top:.4em">';
  h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.3em">Auto-selected targets (<span id="mcal-targets-count">0</span>):</div>';
  h+='<div id="mcal-targets-list" style="font-size:.75em;color:#64748b;font-family:monospace;max-height:80px;overflow:auto"></div>';
  h+='</div>';
  h+='</div>';  // /mcal-options

  // Run-time status — #602: richer live view. Operators need to see
  // pan/tilt, DMX bytes, attempt counter, beam status, and recent log
  // to diagnose "why is this slow/stuck" without reading the server log.
  h+='<div id="mcal-status" style="display:none">';
  h+='<div class="prog-bar" style="height:8px;margin-bottom:.4em"><div class="prog-fill" id="mcal-prog" style="width:0%;transition:width .3s"></div></div>';
  h+='<div id="mcal-phase" style="font-size:.85em;color:#e2e8f0;margin-bottom:.3em"></div>';
  h+='<div id="mcal-detail" style="font-size:.78em;color:#64748b"></div>';
  h+='<div id="mcal-targets-progress" style="margin-top:.5em"></div>';
  // #736 — SMART per-probe progress + post-run residuals table.
  // Hidden until smartProbeGrid or result.residuals is populated by
  // the cal-status response.
  h+='<div id="mcal-smart-progress" style="margin-top:.5em;display:none"></div>';
  h+='<div id="mcal-probe" style="margin-top:.5em;font-size:.75em;color:#cbd5e1;font-family:monospace;display:none"></div>';
  h+='<div id="mcal-dmx-strip" style="margin-top:.4em;display:none"></div>';
  h+='<div id="mcal-log" style="margin-top:.5em;font-size:.72em;color:#64748b;font-family:monospace;max-height:120px;overflow-y:auto;background:#0a0f1a;border:1px solid #1e293b;border-radius:4px;padding:.3em .5em;display:none"></div>';
  h+='</div>';

  // Actions row — #602 button state machine. Start and Cancel swap
  // based on server job status (managed by _moverCalUpdateActions).
  // While status=running, Start is hidden entirely so double-clicks
  // are impossible; Cancel is the only visible action.
  h+='<div id="mcal-actions" style="display:flex;gap:.4em;margin-top:.8em">';
  h+='<button class="btn btn-on" id="mcal-go" onclick="_moverCalGo()">Start Calibration</button>';
  h+='<button class="btn btn-off" id="mcal-cancel" onclick="_moverCalCancel()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Calibrate Mover — '+escapeHtml(f?f.name:'fixture');
  document.getElementById('modal-body').innerHTML=h;

  // #602 — initial button state (Start visible, Close as secondary) and
  // Esc handler (Cancel while running, close otherwise).
  _moverCalUpdateActions(cal?'done':'pre');
  _moverCalInstallEscHandler();
  _moverCalLoadAdvancedOptions();

  // #738 — always fetch cal-status so the three-state capabilities
  // badge (no_home / angular_only / smart) renders even when no SMART
  // model has been committed yet. Render the angular-only banner when
  // applicable; the existing-cal card still appears underneath if
  // SMART data is present.
  ra('GET','/api/calibration/mover/'+_moverCalFid+'/status',null,function(st){
    if(st){
      _moverCalRenderCapabilities(st);
      if(st.status==='running'){
        document.getElementById('mcal-status').style.display='block';
        _moverCalUpdateActions('running');
        _moverCalPoll();
      }
    }
  });
  if(cal){
    ra('GET','/api/calibration/mover/'+_moverCalFid,null,function(r){
      _moverCalRenderExisting(r);
    });
  }
  // #579 — check for a point cloud before the user starts calibration so
  // they can create a lite one if nothing exists. The BFS path works
  // without geometry but benefits from it; v2 requires it.
  _moverCalGeometryCheck();
}

// #602 — Esc on the calibration modal mirrors the Cancel/Close button:
// while running, it fires _moverCalCancel (POSTs /cancel + waits for
// confirm); otherwise it just closes the modal. Installed once per
// modal open; the handler self-removes when the modal closes.
function _moverCalInstallEscHandler(){
  if(window._moverCalEscInstalled)return;
  window._moverCalEscInstalled=true;
  var onKey=function(ev){
    if(ev.key!=='Escape')return;
    var modalOpen=(document.getElementById('modal')||{}).style;
    if(!modalOpen||modalOpen.display==='none'){
      document.removeEventListener('keydown',onKey,true);
      window._moverCalEscInstalled=false;
      return;
    }
    if(_moverCalFid==null)return;
    ev.preventDefault();
    ev.stopPropagation();
    _moverCalCancel();
  };
  document.addEventListener('keydown',onKey,true);
}

function _moverCalGeometryCheck(){
  var el=document.getElementById('mcal-geom');
  if(!el)return;
  ra('GET','/api/space?meta=1',null,function(r){
    var hasCloud=r&&r.ok&&r.totalPoints>0;
    if(!hasCloud){
      el.style.borderColor='#7c2d12';
      el.innerHTML='<b style="color:#fbbf24">Stage geometry: none.</b> '
        +'<span style="color:#94a3b8">BFS will sweep the full pan/tilt range. v2 needs geometry.</span><br>'
        +'<button class="btn" onclick="_moverCalLiteSetup()" style="font-size:.72em;margin-top:.3em;background:#0e7490;color:#fff">Quick Lite Setup</button>'
        +' <button class="btn" onclick="showTab(\'calibration\')" style="font-size:.72em;margin-top:.3em;background:#1e3a5f;color:#93c5fd">Go to Calibration tab for full scan</button>';
      return;
    }
    var isLite=r.source==='lite';
    var stageW=r.stageW?(r.stageW/1000).toFixed(1)+'m':'?';
    var stageD=r.stageD?(r.stageD/1000).toFixed(1)+'m':'?';
    if(isLite){
      el.style.borderColor='#78350f';
      el.innerHTML='<b style="color:#fcd34d">Stage geometry: Lite ('+stageW+' × '+stageD+').</b> '
        +'<span style="color:#64748b">Synthesized from layout dimensions · '+r.totalPoints+' synthetic points · BFS and v2 will use this as a fallback.</span>';
    }else{
      el.style.borderColor='#065f46';
      el.innerHTML='<b style="color:#34d399">Stage geometry: '+r.totalPoints+' real points</b> '
        +'<span style="color:#64748b">from '+((r.cameras||[]).length)+' cameras · '+stageW+' × '+stageD+' stage.</span>';
    }
  });
}

function _moverCalLiteSetup(){
  var el=document.getElementById('mcal-geom');
  if(el)el.innerHTML='<span style="color:#94a3b8">Building lite cloud from layout…</span>';
  ra('POST','/api/space/scan/lite',{},function(r){
    if(r&&r.ok){
      _moverCalGeometryCheck();
    }else if(el){
      el.innerHTML='<span style="color:#ef4444">Lite setup failed: '+escapeHtml(r&&r.err||'unknown')+'</span>';
    }
  });
}

// #681 — Advanced options panel for the wizard, wired to the same
// calibrationTuning schema exposed at Settings → Advanced (#680). Each
// input saves on blur so the operator's tweak carries into the next
// run without a separate "Save" step; blank = use shipped default.
var _MCAL_ADV_KEYS=[
  'warmupSeconds','rejectReflection','refineAfterHit',
  'battleshipPanStepsMax','battleshipTiltStepsMax','adaptiveDensity',
  'settlePixelThresh','discoveryBattleshipS','mappingS',
  // #708 — confirm-nudge bounds (#697) + surface-aware reject (#684)
  // + auto-pose-fit drift threshold (#709) all live next to the cal
  // wizard so operators don't have to leave the modal to retune.
  'confirmContinuityCapMult','confirmRatioMin','confirmRatioMax',
  'confirmSymmetryMinPx','surfaceAwareReject','poseDriftThresholdMm'
];
var _MCAL_ADV_LABELS={
  warmupSeconds:'Warm-up duration (s)',
  rejectReflection:'Reject reflections (blink-confirm)',
  refineAfterHit:'Refine after battleship hit',
  battleshipPanStepsMax:'Battleship — max pan steps',
  battleshipTiltStepsMax:'Battleship — max tilt steps',
  adaptiveDensity:'Adaptive density (#661)',
  settlePixelThresh:'Settle threshold (px)',
  discoveryBattleshipS:'Discovery budget (s)',
  mappingS:'Mapping budget (s)',
  // #708
  confirmContinuityCapMult:'Continuity cap (× beam-width)',
  confirmRatioMin:'Proportionality min',
  confirmRatioMax:'Proportionality max',
  confirmSymmetryMinPx:'Symmetry min (px)',
  surfaceAwareReject:'Surface-aware reject',
  poseDriftThresholdMm:'Pose-drift threshold (mm)'
};
function _moverCalLoadAdvancedOptions(){
  var body=document.getElementById('mcal-adv-body');
  if(!body)return;
  ra('GET','/api/settings',null,function(d){
    if(!d){body.textContent='Failed to load';return;}
    var spec=d.calibrationTuningSpec||{};
    var cur=d.calibrationTuning||{};
    var h='';
    _MCAL_ADV_KEYS.forEach(function(k){
      var s=spec[k];if(!s)return;
      var label=_MCAL_ADV_LABELS[k]||k;
      var tip=s.tooltip||'';
      var cv=cur[k];
      var inp;
      if(s.type==='bool'){
        var checked=(cv==null?!!s.default:!!cv)?' checked':'';
        inp='<input type="checkbox" id="mcaladv-'+k+'"'+checked+
            ' onchange="_moverCalAdvSave(\''+k+'\')">';
      }else{
        var def=String(s.default);
        var val=cv==null?'':String(cv);
        inp='<input id="mcaladv-'+k+'" value="'+escapeHtml(val)+
            '" placeholder="default: '+escapeHtml(def)+'" '+
            'onblur="_moverCalAdvSave(\''+k+'\')" '+
            'style="max-width:140px">';
      }
      h+='<label style="color:#94a3b8" title="'+escapeHtml(tip)+'">'+escapeHtml(label)+'</label>'+inp;
    });
    body.innerHTML=h;
  });
}
function _moverCalAdvSave(key){
  var el=document.getElementById('mcaladv-'+key);
  if(!el)return;
  var payload={};
  if(el.type==='checkbox'){
    payload[key]=!!el.checked;
  }else{
    var v=el.value.trim();
    if(v===''){
      // Blank means "use default" — clear by sending the full current
      // set without this key. Fetch current first.
      ra('GET','/api/settings',null,function(d){
        var t=(d&&d.calibrationTuning)||{};
        delete t[key];
        var x=new XMLHttpRequest();
        x.open('POST','/api/settings',true);
        x.setRequestHeader('Content-Type','application/json');
        x.send(JSON.stringify({calibrationTuning:t}));
      });
      return;
    }
    if(key==='confirmSymmetryMinPx'||/Steps|Samples|Iterations/i.test(key)){
      // #708 — Thresh-suffixed keys are decimal mm/px and want
      // parseFloat; only explicit-integer keys go through parseInt.
      payload[key]=parseInt(v,10);
    }else{
      payload[key]=parseFloat(v);
    }
  }
  // Merge with current overrides so we don't clobber other keys.
  ra('GET','/api/settings',null,function(d){
    var t=(d&&d.calibrationTuning)||{};
    t[key]=payload[key];
    var x=new XMLHttpRequest();
    x.open('POST','/api/settings',true);
    x.setRequestHeader('Content-Type','application/json');
    x.onload=function(){
      if(x.status!==200){
        try{var r=JSON.parse(x.responseText);
          alert('Advanced option rejected: '+((r&&r.details)||[]).join('; '));
        }catch(e){alert('Advanced option rejected (see server log).');}
        _moverCalLoadAdvancedOptions();
      }
    };
    x.send(JSON.stringify({calibrationTuning:t}));
  });
}

function _moverCalModeChanged(){
  var sel=document.getElementById('mcal-mode');
  var prev=document.getElementById('mcal-targets-preview');
  if(!sel||!prev)return;
  if(sel.value==='v2'){
    prev.style.display='block';
    ra('GET','/api/calibration/mover/'+_moverCalFid+'/targets?n=6',null,function(r){
      if(!r||!r.ok)return;
      var list=document.getElementById('mcal-targets-list');
      var cnt=document.getElementById('mcal-targets-count');
      if(cnt)cnt.textContent=(r.targets||[]).length;
      if(list){
        list.innerHTML=(r.targets||[]).map(function(t,i){
          return (i+1)+'. ('+Math.round(t.x)+', '+Math.round(t.y)+', '+Math.round(t.z)+') mm';
        }).join('<br>');
      }
    });
  }else{
    prev.style.display='none';
  }
  // #720 PR-2 — show the SMART coverage preview when SMART is selected.
  if(sel.value==='smart'){
    _smartCoverageRender(_moverCalFid);
  }else{
    _smartCoveragePanelHide();
  }
}

// ── #720 PR-2 — SMART coverage preview ─────────────────────────────────
//
// When the operator picks SMART from the cal-method pulldown, fetch
// /api/fixtures/<fid>/coverage and render a flat top-down floor polygon
// preview alongside the standard cal card. The full 3D viewport (per
// the plan's `_mcal3d` Three.js singleton) is left for a follow-up —
// this panel ships the data path + a top-down 2D footprint on a canvas
// so the operator can sanity-check the cone before any probing runs.

function _smartCoveragePanelHide(){
  var p=document.getElementById('smart-coverage-panel');
  if(p)p.style.display='none';
  // #720 PR-2 — release the parallel viewport when the operator picks
  // a non-SMART mode so its render loop stops eating cycles.
  if(typeof _mcal3dDispose==='function')_mcal3dDispose();
}

// #732 — mount the SMART 3D viewport on demand. Called by the cal-
// status poller when the SMART probe loop starts from a non-SMART
// initial mode (e.g. operator picked all-auto then the server
// elevated to SMART), so the live probe overlay always has a place
// to draw. Idempotent: a second call does nothing if the panel is
// already mounted.
function _smartCoverageEnsureMounted(fid){
  var p=document.getElementById('smart-coverage-panel');
  if(p && p.style.display!=='none' && _mcal3d && _mcal3d.inited)return;
  _smartCoverageRender(fid);
}

function _smartCoverageRender(fid){
  var card=document.getElementById('mcal-targets-preview');
  if(!card)return;
  var p=document.getElementById('smart-coverage-panel');
  if(!p){
    p=document.createElement('div');
    p.id='smart-coverage-panel';
    p.style.cssText='margin-top:.5em;border-top:1px solid #334155;padding-top:.4em';
    p.innerHTML =
      '<div style="font-size:.78em;color:#94a3b8;margin-bottom:.3em">'
      + 'SMART 3D coverage preview '
      + '<span style="color:#64748b">— drag to orbit, scroll to zoom</span></div>'
      + '<div id="mcal-3d" style="position:relative;width:100%;height:280px;'
      + 'background:#0a0e1a;border:1px solid #1e293b;border-radius:4px;'
      + 'overflow:hidden"></div>'
      + '<div id="smart-coverage-status" style="font-size:.72em;color:#64748b;margin-top:.3em"></div>';
    card.parentNode.insertBefore(p, card.nextSibling);
  }
  p.style.display='block';
  var status=document.getElementById('smart-coverage-status');
  if(status)status.textContent='Loading…';

  // Mount the 3D viewport (idempotent — does nothing if already mounted
  // on this element). Falls back to a status-only message if Three.js
  // failed to load.
  var mounted=false;
  try{mounted=(typeof _mcal3dMount==='function') && _mcal3dMount('mcal-3d');}catch(e){mounted=false;}
  if(!mounted){
    if(status)status.textContent='3D viewport unavailable (Three.js not loaded)';
    return;
  }

  // Fetch coverage + smart preview in parallel; render once both land
  // (or just coverage if preview fails — the cone alone is meaningful).
  var coverage=null, preview=null, fired=false;
  function maybeRender(){
    if(fired)return;
    if(coverage===null)return;
    // Preview is optional — render with whatever we have once coverage
    // is in.
    fired=true;
    if(coverage.err){
      if(status)status.textContent='Error: '+coverage.err;
      return;
    }
    var poly=coverage.floorPolygon||[];
    if(poly.length<3){
      if(status)status.textContent='Coverage polygon empty (no floor intersection in front of fixture)';
      _mcal3dRender(coverage, null);
      return;
    }
    _mcal3dRender(coverage, preview);
    if(status){
      var ar=_smartPolygonAreaMm2(poly);
      var floorZ=(coverage.floorZ||0).toFixed(0);
      var line='Coverage '+(ar/1e6).toFixed(2)+' m² · floor z='+floorZ+' mm';
      if(preview){
        if(preview.abortReason==='home_secondary_stale_format'){
          // #730 — pre-#730 secondary records can't bootstrap. Tell the
          // operator to re-run the wizard rather than burying it in a
          // generic abort reason.
          line+=' · re-run Home wizard (secondary format changed in #730)';
        }else if(preview.abortReason){
          line+=' · preview: '+preview.abortReason;
        }else{
          var pp=(preview.probePoints||[]).length;
          var wpArea=preview.workingPoly?_smartPolygonAreaMm2(preview.workingPoly):0;
          line+=' · working '+(wpArea/1e6).toFixed(2)+' m² · '+pp+' probes';
          if(preview.insufficient)line+=' (insufficient)';
        }
      }
      status.textContent=line;
    }
  }
  ra('GET','/api/fixtures/'+fid+'/coverage',null,function(r){
    coverage=r||{err:'no response'};
    if(!coverage.ok && !coverage.err)coverage.err=coverage.err||'unknown';
    maybeRender();
  });
  ra('GET','/api/calibration/mover/'+fid+'/smart/preview',null,function(r){
    preview=(r&&r.ok)?r:null;
    maybeRender();
  });
}

function _smartPolygonAreaMm2(poly){
  var a=0;
  for(var i=0;i<poly.length;i++){
    var j=(i+1)%poly.length;
    a+=poly[i][0]*poly[j][1]-poly[j][0]*poly[i][1];
  }
  return Math.abs(a)/2;
}

function _moverCalFitBadge(fit){
  if(!fit||fit.rmsErrorDeg==null)return'';
  var rms=fit.rmsErrorDeg;
  var col='#4ade80', lbl='GOOD';
  if(rms>1.5){col='#f59e0b';lbl='FAIR';}
  if(rms>3.0){col='#ef4444';lbl='POOR';}
  return '<span style="background:'+col+';color:#0a0e1a;padding:1px 6px;border-radius:4px;font-weight:bold;font-size:.72em;letter-spacing:.05em">'+lbl+' '+rms.toFixed(2)+'\u00b0</span>';
}

// #738 — three-state capability banner: no Home / angular only / SMART.
// Rendered above the existing-cal card. The angular-only state is the
// new one — a fixture with Home + Secondary works for gyro / aim-angles
// / manual jog / angular bake cues right now without SMART; only world-
// XYZ tracking needs SMART.
function _moverCalRenderCapabilities(r){
  var box=document.getElementById('mcal-existing');
  if(!box||!r)return;
  var caps=r.capabilities||{};
  var state=caps.state||'no_home';
  // SMART-committed path is fully covered by _moverCalRenderExisting —
  // skip the banner so we don't double-render.
  if(state==='smart')return;
  var bg, border, badge, msg;
  if(state==='angular_only'){
    bg='#1e1f00'; border='#fbbf24'; badge='ANGULAR ONLY';
    msg='Ready for cues — gyro, manual jog, aim-angles, and angular '
       +'bake cues all work exactly. <b>Run SMART to enable object '
       +'tracking and world-XYZ aim.</b>';
  } else {
    bg='#1f0a0a'; border='#ef4444'; badge='SET HOME';
    msg='Home not set — calibration and remote control unavailable. '
       +'Open the fixture editor and click "Set Home".';
  }
  var h='<div class="card" style="padding:.6em;margin-bottom:.6em;background:'+bg+';border-left:3px solid '+border+'">';
  h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.3em">';
  h+='<span style="background:'+border+';color:#0a0e1a;padding:1px 6px;border-radius:4px;font-weight:bold;font-size:.72em;letter-spacing:.05em">'+badge+'</span>';
  h+='</div>';
  h+='<div style="font-size:.78em;color:#cbd5e1">'+msg+'</div>';
  h+='</div>';
  box.innerHTML=h;
  box.style.display='block';
}

function _moverCalRenderExisting(r){
  var box=document.getElementById('mcal-existing');
  if(!box||!r||!r.calibrated)return;
  // #720 PR-7 — pre-SMART records get a yellow border and a "needs
  // SMART recalibration" banner. SMART records get the green border.
  var stale=!!r.needsSmartRecal;
  var border=stale?'#f59e0b':(r.method==='smart'?'#4ade80':'#4ade80');
  var h='<div class="card" style="padding:.6em;margin-bottom:.6em;background:#0f172a;border-left:3px solid '+border+'">';
  h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.3em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;font-weight:bold">Current calibration</div>';
  if(r.method==='smart' && r.confidence){
    var conf=r.confidence;
    var cc=conf==='high'?'#4ade80':(conf==='medium'?'#fbbf24':'#f87171');
    h+='<span style="background:'+cc+';color:#0a0e1a;padding:1px 6px;border-radius:4px;font-weight:bold;font-size:.72em;letter-spacing:.05em">SMART '+conf.toUpperCase()+'</span>';
  }
  h+=_moverCalFitBadge(r.fit);
  h+='</div>';
  if(stale){
    h+='<div style="font-size:.74em;color:#fbbf24;margin-bottom:.3em">';
    h+='Needs SMART recalibration — this fixture was last calibrated with the legacy <b>'
      +(r.legacyMethod||'unknown')+'</b> path. Re-run SMART before the legacy aim fallback is removed.';
    h+='</div>';
  }
  if(r.method==='smart' && r.residuals){
    var res=r.residuals;
    h+='<div style="font-size:.78em;color:#94a3b8">';
    h+='RMS '+(res.rmsMm||0).toFixed(1)+' mm · max '+(res.maxMm||0).toFixed(1)+' mm · ';
    h+=(res.sampleCount||0)+' probes';
    h+='</div>';
  }
  if(r.fit){
    h+='<div style="font-size:.78em;color:#94a3b8">';
    h+='RMS '+r.fit.rmsErrorDeg.toFixed(2)+'\u00b0 \u00b7 max '+r.fit.maxErrorDeg.toFixed(2)+'\u00b0 \u00b7 ';
    h+=r.fit.sampleCount+' samples';
    if(r.fit.conditionNumber!=null)h+=' \u00b7 cond '+r.fit.conditionNumber.toFixed(1);
    h+='</div>';
  }
  if(r.verification&&!r.verification.skipped&&r.verification.rmsErrorPx!=null){
    h+='<div style="font-size:.78em;color:#94a3b8;margin-top:.2em">';
    h+='Verification: '+r.verification.rmsErrorPx.toFixed(1)+'px RMS (max '+r.verification.maxErrorPx.toFixed(1)+'px)';
    h+='</div>';
  }
  // One-button fast re-calibration (#505) — uses v2 warm-start.
  h+='<div style="margin-top:.4em;display:flex;gap:.4em">';
  h+='<button class="btn btn-on" style="font-size:.78em;padding:3px 8px" onclick="_moverCalFastRecal()">Re-calibrate (fast, warm-start)</button>';
  if(r.samples){
    h+='<button class="btn" style="font-size:.78em;padding:3px 8px;background:#334155;color:#94a3b8" onclick="_moverCalShowResiduals()">View residuals ('+r.samples.length+')</button>';
  }
  h+='</div></div>';
  box.innerHTML=h;
  box.style.display='block';
  _moverCalExistingCache=r;
}
var _moverCalExistingCache=null;

// #505 — one-button fast re-cal. Uses v2 mode; warm-start comes
// automatically from _get_mover_model(fid) hitting the existing v2 cal.
function _moverCalFastRecal(){
  var opts=document.getElementById('mcal-options');
  if(opts)opts.style.display='none';
  var exist=document.getElementById('mcal-existing');
  if(exist)exist.style.display='none';
  var status=document.getElementById('mcal-status');
  if(status)status.style.display='block';
  var phase=document.getElementById('mcal-phase');
  if(phase)phase.textContent='Starting fast re-calibration (v2 warm-start)...';
  // #602 — apply the state machine here too: fast-recal is a Start path,
  // so hide Start / show Cancel while the job is live.
  _moverCalUpdateActions('running');
  ra('POST','/api/calibration/mover/'+_moverCalFid+'/start',
     {mode:'v2',warmup:false,color:[0,255,0]},function(r){
    if(!r||!r.ok){
      // 409 means a calibration is already running — attach to it.
      var isAlreadyRunning=(r&&/already running/i.test(r.err||''));
      if(isAlreadyRunning){
        if(phase)phase.textContent='Calibration already in progress — attaching';
        _moverCalPoll();
        return;
      }
      if(phase)phase.innerHTML='<span style="color:#f66">'+(r&&r.err||'Failed to start')+'</span>';
      _moverCalUpdateActions('error');
      return;
    }
    var detail=document.getElementById('mcal-detail');
    if(detail)detail.textContent='Camera: '+(r.cameraName||'unknown')+' \u00b7 mode: v2';
    _moverCalPoll();
  });
}

function _moverCalShowResiduals(){
  var r=_moverCalExistingCache;
  if(!r||!r.samples)return;
  _moverCalRenderResidualTable(r);
}

// #512 — toggle 3D residual vectors in the viewport. Closes the modal so
// the operator can see the scene; re-opening is a click on Calibrate.
function _moverCalShowIn3D(){
  var fid=_moverCalFid;
  if(!fid||typeof s3dShowResidualsForFixture!=='function')return;
  s3dShowResidualsForFixture(fid);
  closeModal();
}

function _moverCalGo(){
  var sel=document.getElementById('mcal-color');
  var colorMap={white:[255,255,255],green:[0,255,0],magenta:[255,0,255],red:[255,0,0],blue:[0,0,255]};
  var color=colorMap[sel?sel.value:'green']||[0,255,0];
  // #713 C — default to `all-auto` if the dropdown isn't found.
  // `legacy` removed from the SPA selector; the server still accepts
  // it for back-compat with external API callers until the #703
  // deletion PR lands.
  var mode=(document.getElementById('mcal-mode')||{}).value||'all-auto';
  var warmup=(document.getElementById('mcal-warmup')||{}).checked||false;
  document.getElementById('mcal-status').style.display='block';
  document.getElementById('mcal-phase').textContent='Starting calibration...';
  // #602 — hide Start immediately so a stubborn click can't re-submit.
  _moverCalUpdateActions('running');
  ra('POST','/api/calibration/mover/'+_moverCalFid+'/start',
     {color:color,mode:mode,warmup:warmup},function(r){
    if(!r||!r.ok){
      // #602 — 409 means a calibration is already running server-side.
      // Drop into the polling/state-machine path instead of showing a
      // red error; the operator was just racing a prior Start.
      var isAlreadyRunning=(r&&/already running/i.test(r.err||''));
      if(isAlreadyRunning){
        document.getElementById('mcal-phase').textContent='Calibration already in progress — attaching to running job';
        _moverCalPoll();
        return;
      }
      document.getElementById('mcal-phase').innerHTML='<span style="color:#f66">'+(r&&r.err||'Failed to start')+'</span>';
      _moverCalUpdateActions('error');
      return;
    }
    var dbits=['Camera: '+(r.cameraName||'unknown'),'mode: '+(mode||'legacy')];
    if(warmup)dbits.push('warmup: yes');
    document.getElementById('mcal-detail').textContent=dbits.join(' \u00b7 ');
    _moverCalPoll();
  });
}

// #602 — drive the Start/Cancel actions row from job state. Called
// whenever status transitions: 'running' hides Start entirely and shows
// Cancel; 'cancelling' disables Cancel while awaiting server abort;
// everything else (done/error/cancelled/pre) shows Start with contextual
// label and turns the secondary button into a plain Close.
function _moverCalUpdateActions(state){
  var go=document.getElementById('mcal-go');
  var cancel=document.getElementById('mcal-cancel');
  if(!go||!cancel)return;
  if(state==='running'){
    go.style.display='none';
    cancel.style.display='';
    cancel.disabled=false;
    cancel.textContent='Cancel Calibration';
    return;
  }
  if(state==='cancelling'){
    go.style.display='none';
    cancel.style.display='';
    cancel.disabled=true;
    cancel.textContent='Cancelling…';
    return;
  }
  go.style.display='';
  go.disabled=false;
  cancel.style.display='';
  cancel.disabled=false;
  cancel.textContent='Close';
  if(state==='error')go.textContent='Retry';
  else if(state==='cancelled')go.textContent='Start Calibration';
  else if(state==='done')go.textContent='Re-calibrate (full)';
  else go.textContent='Start Calibration';
}

// #602 — render currentProbe + dmxFrame + log tail sections from the
// server's /status payload. No-op when those fields are absent (keeps
// the panel hidden until the calibration thread actually starts probing).
//
// #736 — extend with SMART per-probe progress: probe N/16, predicted
// stage XY, last cell result (hit / miss / probing), running tally of
// hits vs misses across the grid. After SMART completes, render a
// residuals table from r.result.residuals so the operator can see
// per-probe error mm.
function _moverCalRenderProbe(r){
  var probeBox=document.getElementById('mcal-probe');
  var dmxBox=document.getElementById('mcal-dmx-strip');
  var logBox=document.getElementById('mcal-log');
  var smartBox=document.getElementById('mcal-smart-progress');
  var cp=r.currentProbe;
  if(probeBox){
    if(cp&&cp.attempt){
      var panTxt=cp.pan!=null?cp.pan.toFixed(3):'-';
      var tiltTxt=cp.tilt!=null?cp.tilt.toFixed(3):'-';
      var rgb=cp.rgb||[0,0,0];
      var swatch='<span style="display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:middle;margin:0 .3em;background:rgb('+rgb[0]+','+rgb[1]+','+rgb[2]+')"></span>';
      probeBox.innerHTML='probe #'+cp.attempt
        +'  pan='+panTxt+' ('+(cp.dmxPan!=null?cp.dmxPan:'?')+')'
        +'  tilt='+tiltTxt+' ('+(cp.dmxTilt!=null?cp.dmxTilt:'?')+')'
        +swatch+'dim='+(cp.dimmer!=null?cp.dimmer:'?');
      probeBox.style.display='';
    }else{
      probeBox.style.display='none';
    }
  }
  // #736 — SMART per-probe progress strip + post-run residuals table.
  // Renders only when smartProbeGrid is populated (probing) or when
  // result.residuals is present (done / error). Falls back to display
  // none on legacy modes so we don't pollute the existing UI.
  var grid=r.smartProbeGrid||[];
  var smartCur=r.smartCurrentProbe;
  var residuals=(r.result||{}).residuals;
  var phase=r.phase||'';
  var phaseIsSmart=phase.indexOf('smart')===0;
  if(smartBox){
    if(grid.length || residuals){
      var counts={pending:0, probing:0, hit:0, miss:0, ik_degenerate:0};
      grid.forEach(function(c){counts[c.status]=(counts[c.status]||0)+1;});
      var total=grid.length;
      var doneN=counts.hit+counts.miss+(counts.ik_degenerate||0);
      var html='';
      if(phaseIsSmart && total){
        html+='<div style="font-size:.78em;color:#cbd5e1;margin-bottom:.3em">';
        html+='SMART probing — <b>'+doneN+' / '+total+'</b> attempted';
        html+=' · <span style="color:#4ade80">'+counts.hit+' hit</span>';
        html+=' · <span style="color:#ef4444">'+counts.miss+' miss</span>';
        if(counts.ik_degenerate)html+=' · <span style="color:#9333ea">'+counts.ik_degenerate+' degenerate</span>';
        html+='</div>';
        if(smartCur && smartCur.stageXYZ){
          var sx=smartCur.stageXYZ;
          html+='<div style="font-size:.74em;color:#94a3b8;margin-bottom:.4em">'
              +'aiming probe #'+(smartCur.index+1)+' at floor ('
              +Math.round(sx[0])+', '+Math.round(sx[1])+', '+Math.round(sx[2])+') mm</div>';
        }
        // Compact dot grid showing each cell's status.
        var dots=grid.map(function(c){
          var col=({pending:'#64748b', probing:'#fbbf24', hit:'#4ade80',
                     miss:'#ef4444', ik_degenerate:'#9333ea'}[c.status])||'#64748b';
          var title=c.status+' · ('+Math.round(c.x)+', '+Math.round(c.y)+')'
                    +(c.reason?' · '+c.reason:'');
          return '<span title="'+escapeHtml(title)+'" style="display:inline-block;width:10px;height:10px;border-radius:50%;background:'+col+';margin:1px"></span>';
        }).join('');
        html+='<div style="margin-bottom:.4em">'+dots+'</div>';
      }
      if(residuals && residuals.perPoint && residuals.perPoint.length){
        // #734 follow-up — flag any measured XYZ falling outside the
        // configured stage bounds. A false-positive on diff-of-ambient
        // noise (which happens when the lamp toggle is broken) projects
        // to a random pixel and depth, often landing kilometres away
        // from the stage. An operator scanning the residuals table can
        // instantly tell "noise" from "biased fit" by the red flags.
        var sw=(_layout&&_layout.stageW)?_layout.stageW*1000:99999;
        var sd=(_layout&&_layout.stageD)?_layout.stageD*1000:99999;
        var sm=500;  // 0.5 m slop outside the nominal stage rectangle
        var nFlagged=residuals.perPoint.filter(function(p){
          return p.measured && (p.measured[0] < -sm || p.measured[0] > sw+sm
              || p.measured[1] < -sm || p.measured[1] > sd+sm);
        }).length;
        html+='<div style="font-size:.78em;color:#cbd5e1;margin-top:.3em">';
        html+='Residuals — RMS '+(residuals.rmsMm||0).toFixed(1)+' mm · max '
             +(residuals.maxMm||0).toFixed(1)+' mm · '
             +(residuals.sampleCount||residuals.perPoint.length)+' probes';
        if(nFlagged){
          html+=' · <span style="color:#f87171">'+nFlagged
              +' outside stage bounds (likely false-positive)</span>';
        }
        html+='</div>';
        html+='<table style="width:100%;font-size:.72em;color:#94a3b8;margin-top:.3em;font-family:monospace;border-collapse:collapse">';
        html+='<thead><tr style="background:#0f172a;color:#cbd5e1">'
            +'<th style="padding:2px 4px;text-align:left">#</th>'
            +'<th style="padding:2px 4px;text-align:right">measured</th>'
            +'<th style="padding:2px 4px;text-align:right">predicted</th>'
            +'<th style="padding:2px 4px;text-align:right">err mm</th>'
            +'</tr></thead><tbody>';
        residuals.perPoint.forEach(function(p, i){
          var err=p.errorMm||0;
          var col=err<50?'#4ade80':(err<100?'#fbbf24':'#ef4444');
          var oob = p.measured && (p.measured[0] < -sm || p.measured[0] > sw+sm
                    || p.measured[1] < -sm || p.measured[1] > sd+sm);
          var rowBg=oob?'background:#3f1d1d':'';
          var oobMark=oob?' <span title="outside stage bounds — likely false-positive" style="color:#f87171">⚠</span>':'';
          html+='<tr style="'+rowBg+'"><td style="padding:2px 4px">'+(i+1)+oobMark+'</td>'
              +'<td style="padding:2px 4px;text-align:right'+(oob?';color:#f87171':'')+'">('
              +Math.round(p.measured[0])+', '+Math.round(p.measured[1])+')</td>'
              +'<td style="padding:2px 4px;text-align:right">('
              +Math.round(p.predicted[0])+', '+Math.round(p.predicted[1])+')</td>'
              +'<td style="padding:2px 4px;text-align:right;color:'+col+'">'
              +err.toFixed(1)+'</td></tr>';
        });
        html+='</tbody></table>';
      }
      smartBox.innerHTML=html;
      smartBox.style.display=html?'':'none';
    } else {
      smartBox.innerHTML='';
      smartBox.style.display='none';
    }
  }
  if(dmxBox){
    var df=r.dmxFrame;
    if(df&&df.channels&&df.channels.length){
      var chs=df.channels;
      var addr=df.addr||1;
      // Show the fixture's own window (first ~16 from its start addr)
      var start=Math.max(0,addr-1);
      var slice=chs.slice(start,start+16);
      var cells=slice.map(function(v,i){
        var pct=Math.min(100,Math.round((v/255)*100));
        return '<div title="ch '+(start+i+1)+' = '+v+'" style="flex:1;min-width:14px;height:20px;background:linear-gradient(to top,#3b82f6 '+pct+'%,#1e293b '+pct+'%);border:1px solid #0f172a;font-size:.6em;color:#cbd5e1;text-align:center;line-height:20px">'+v+'</div>';
      }).join('');
      dmxBox.innerHTML='<div style="font-size:.7em;color:#64748b;margin-bottom:.2em">DMX uni '+(df.universe||1)+' ch '+addr+'–'+(addr+slice.length-1)+':</div>'
        +'<div style="display:flex;gap:1px">'+cells+'</div>';
      dmxBox.style.display='';
    }else{
      dmxBox.style.display='none';
    }
  }
  if(logBox){
    var entries=r.log||[];
    if(entries.length){
      var lines=entries.slice(-8).map(function(e){
        var col=e.level==='warning'?'#f59e0b':(e.level==='error'?'#ef4444':'#94a3b8');
        return '<div style="color:'+col+';white-space:pre-wrap">'+escapeHtml(e.msg||'')+'</div>';
      }).join('');
      logBox.innerHTML=lines;
      logBox.scrollTop=logBox.scrollHeight;
      logBox.style.display='';
    }else{
      logBox.style.display='none';
    }
  }
}

function _moverCalPoll(){
  if(_moverCalTimer)clearTimeout(_moverCalTimer);
  ra('GET','/api/calibration/mover/'+_moverCalFid+'/status',null,function(r){
    if(!r)return;
    var prog=document.getElementById('mcal-prog');
    var phase=document.getElementById('mcal-phase');
    if(prog)prog.style.width=(r.progress||0)+'%';
    var phaseNames={
      starting:'Starting...',
      warmup:'Warming up fixture (thermal settle)...',
      discovery:'Discovering beam...',
      // #610 markers-mode phases — finer-grained than the legacy BFS
      // so the operator sees exactly where in the state machine we are.
      battleship:'Searching for beam (battleship grid)...',
      confirming:'Confirming beam with pan/tilt nudge...',
      mapping:'Mapping visible region...',
      sampling:'Nudging beam to known markers...',
      fitting:'Fitting DMX → stage model...',
      verification:'Verifying fit on held-out points...',
      grid:'Building interpolation grid...',
      complete:'Complete'
    };
    if(phase){
      var phName=phaseNames[r.phase]||r.phase||'';
      var extra=r.message?(' \u2014 '+r.message):'';
      phase.textContent=phName+extra;
    }

    // Per-target progress table (v2 mode)
    var tgtBox=document.getElementById('mcal-targets-progress');
    if(tgtBox){
      if(r.targets&&r.targets.length){
        var rows=r.targets.map(function(t){
          var cols={
            pending:'#64748b',
            converging:'#f59e0b',
            converged:'#4ade80',
            skipped:'#f59e0b',
            failed:'#ef4444'
          };
          var col=cols[t.status]||'#94a3b8';
          var err=t.errorPx!=null?(' ('+t.errorPx.toFixed(0)+'px)'):'';
          return '<div style="display:flex;gap:.4em;font-size:.75em;font-family:monospace"><span style="width:16px;color:'+col+'">\u25cf</span>'
                +'<span style="flex:1;color:#94a3b8">T'+(t.idx+1)+' ('+Math.round(t.stagePos[0])+','+Math.round(t.stagePos[1])+')</span>'
                +'<span style="color:'+col+';min-width:80px">'+t.status+err+'</span>'
                +'<span style="color:#64748b">iters '+(t.iterations||0)+'</span></div>';
        }).join('');
        tgtBox.innerHTML='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.3em">Targets ('
          +(r.currentTarget!=null?(r.currentTarget+1):'-')+'/'+(r.totalTargets||r.targets.length)+'):</div>'+rows;
      }else{
        tgtBox.innerHTML='';
      }
    }

    // #602 — live probe/dmx/log view below the phase bar.
    _moverCalRenderProbe(r);

    // #732 — live SMART probe overlay on the 3D coverage viewport.
    // The viewport is mounted by _smartCoverageRender when the
    // operator selects mode=smart; if the probe loop started from a
    // different mode (operator clicked Start while still on all-auto)
    // we mount it now so the overlay has somewhere to draw. Overlay
    // renderer is null-safe when no probe data is present.
    if(r.smartProbeGrid && r.smartProbeGrid.length
        && typeof _mcal3dProbeOverlay==='function'){
      _smartCoverageEnsureMounted(_moverCalFid);
      _mcal3dProbeOverlay(r);
    } else if(typeof _mcal3dProbeOverlayClear==='function'
              && r.status!=='running' && r.status!=='validating'){
      _mcal3dProbeOverlayClear();
    }

    if(r.status==='running'){
      // #602 — keep the button state machine in sync even on the first
      // poll (covers attach-to-running after a 409).
      if(!_moverCalCancellingFid)_moverCalUpdateActions('running');
      _moverCalTimer=setTimeout(_moverCalPoll,1000);
    }else if(r.status==='validating'){
      // #720 PR-6 — SMART solver finished; render the marker
      // confirmation pass. Polling continues so the panel updates if
      // another tab confirms a marker or the cal aborts.
      if(!_moverCalCancellingFid)_moverCalUpdateActions('running');
      _smartValidateRender(_moverCalFid);
      _moverCalTimer=setTimeout(_moverCalPoll,1500);
    }else if(r.status==='error_validation_failed'){
      // #720 PR-6 — operator marked at least one marker as miss.
      if(phase)phase.innerHTML='<span style="color:#f66">SMART validation failed: '+(r.error||'')+'</span>';
      _moverCalUpdateActions('error');
      _smartValidateRenderFailed(_moverCalFid);
      _moverCalTimer=setTimeout(_moverCalPollFinal,1500);
    }else if(r.status==='done'){
      if(prog)prog.style.width='100%';
      _moverCalUpdateActions('done');
      _moverCalRenderComplete(r);
      (_fixtures||[]).forEach(function(f){if(f.id===_moverCalFid)f.moverCalibrated=true;});
      renderSidebar();
      // #682 stale-SPA — one more delayed poll so tail-end server log
      // entries (camera-lock restore, blackout confirmation, final fit
      // summary) land in the SPA before the operator closes.
      _moverCalTimer=setTimeout(_moverCalPollFinal,1500);
    }else if(r.status==='error'){
      if(phase)phase.innerHTML='<span style="color:#f66">'+(r.error||'Unknown error')+'</span>';
      _moverCalUpdateActions('error');
      _moverCalTimer=setTimeout(_moverCalPollFinal,1500);
    }else if(r.status==='cancelled'){
      // #594/#602 — Cancel hit. The fixture has already been blacked out
      // and the lock released on the server. Flip the button state and
      // stop the cancel-confirmation poller if one is still running.
      if(phase)phase.innerHTML='<span style="color:#f59e0b">Cancelled</span>';
      _moverCalUpdateActions('cancelled');
      _moverCalCancellingFid=null;
      // #682 stale-SPA — refresh log + DMX panel one last time so the
      // operator sees the cancel-time blackout entry and the final
      // camera-lock restore line instead of the pre-cancel snapshot.
      _moverCalTimer=setTimeout(_moverCalPollFinal,1500);
    }
  });
}
var _moverCalCancellingFid=null;  // #602 — set while awaiting server abort confirmation

// #682 stale-SPA — one-shot re-poll after terminal status to pick up
// tail-end log entries (camera-lock restore, final blackout). Doesn't
// re-enter the main running-state poll loop; just refreshes the probe /
// log / DMX panels against whatever the server last wrote, even if the
// modal is already displaying the terminal status banner.
function _moverCalPollFinal(){
  if(!_moverCalFid)return;
  ra('GET','/api/calibration/mover/'+_moverCalFid+'/status',null,function(r){
    if(!r)return;
    _moverCalRenderProbe(r);
  });
}

// #709 — apply the auto-pose-fit recommendation by writing the fitted
// XYZ back to the layout. The operator stays in the wizard; the next
// cal will run against the corrected pose.
function _moverCalApplyPoseFit(){
  if(!_moverCalFid)return;
  ra('GET','/api/calibration/mover/'+_moverCalFid+'/status',null,function(r){
    if(!r||!r.poseFitRecommended){alert('No pose-fit recommendation found');return;}
    var fp=r.poseFitRecommended.fittedPose;
    if(!fp||fp.length<3){alert('Fitted pose missing');return;}
    if(!confirm('Move fixture '+_moverCalFid+' to layout pose ['+
                fp.map(function(v){return v.toFixed(0);}).join(', ')+']?'))return;
    ra('GET','/api/layout',null,function(layout){
      var children=(layout&&layout.children)||[];
      var found=false;
      children.forEach(function(c){
        if(c.id===_moverCalFid){
          c.x=fp[0];c.y=fp[1];c.z=fp[2];found=true;
        }
      });
      if(!found){alert('Fixture not in layout');return;}
      ra('POST','/api/layout',{children:children},function(){
        alert('Layout updated. Re-run cal to verify.');
        closeModal();_moverCalFid=null;loadLayout();
      });
    });
  });
}
function _moverCalDismissPoseFit(){
  // Pure UX — the recommendation lives on `job` in memory. Hiding the
  // banner is enough; the underlying job status doesn't change.
  var status=document.getElementById('mcal-status');
  if(status){
    var banners=status.querySelectorAll('.card');
    banners.forEach(function(b){
      if(b.textContent.indexOf('Layout pose drift')>=0)b.style.display='none';
    });
  }
}
function _moverCalRenderComplete(r){
  var status=document.getElementById('mcal-status');
  if(!status)return;
  var res=r.result||{};
  var fit=r.fit;
  var ver=r.verification;
  var h='<div style="padding:.5em">';
  h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.4em">';
  h+='<div style="font-size:1.5em;color:#4ade80">\u2713</div>';
  h+='<div style="flex:1">';
  h+='<div style="font-size:1em;color:#e2e8f0;font-weight:bold">Calibration Complete</div>';
  h+='<div style="font-size:.8em;color:#94a3b8">'+(res.sampleCount||r.sampleCount||0)+' samples</div>';
  h+='</div>';
  h+=_moverCalFitBadge(fit);
  h+='</div>';
  if(fit){
    h+='<div class="card" style="padding:.5em;margin-bottom:.5em;background:#0f172a">';
    h+='<div style="font-size:.78em;color:#94a3b8">Fit quality</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:.2em;font-size:.78em;color:#e2e8f0;margin-top:.2em">';
    h+='<div>RMS: '+fit.rmsErrorDeg.toFixed(2)+'\u00b0</div>';
    h+='<div>Max: '+fit.maxErrorDeg.toFixed(2)+'\u00b0</div>';
    h+='<div>Samples: '+fit.sampleCount+'</div>';
    h+='<div>Cond #: '+(fit.conditionNumber!=null?fit.conditionNumber.toFixed(1):'-')+'</div>';
    h+='</div></div>';
  }
  if(ver){
    if(ver.skipped){
      h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.4em">Verification skipped ('+escapeHtml(ver.reason||'')+')</div>';
    }else if(ver.rmsErrorPx!=null){
      var col=ver.maxErrorPx<40?'#4ade80':(ver.maxErrorPx<100?'#f59e0b':'#ef4444');
      h+='<div class="card" style="padding:.5em;margin-bottom:.5em;background:#0f172a;border-left:3px solid '+col+'">';
      h+='<div style="font-size:.78em;color:#94a3b8">Verification on held-out points</div>';
      h+='<div style="font-size:.78em;color:#e2e8f0;margin-top:.2em">';
      h+='RMS '+ver.rmsErrorPx.toFixed(1)+'px \u00b7 max '+ver.maxErrorPx.toFixed(1)+'px \u00b7 '+(ver.points||[]).length+' points';
      h+='</div></div>';
    }
  }
  // #709 \u2014 auto-pose-fit recommendation banner. When the cal-end
  // solver finds the layout pose differs from the fitted pose by
  // more than `poseDriftThresholdMm`, surface the diff with a
  // pointer at Verify Pose so the operator can correct without
  // re-running the cal.
  if(r.poseFitRecommended){
    var pf=r.poseFitRecommended;
    var cur=pf.currentPose||[0,0,0];
    var fit2=pf.fittedPose||[0,0,0];
    h+='<div class="card" style="padding:.6em;margin-bottom:.5em;background:#451a03;border-left:3px solid #f59e0b">';
    h+='<div style="font-size:.85em;color:#fef3c7;font-weight:bold">\u26a0 Layout pose drift detected</div>';
    h+='<div style="font-size:.78em;color:#fde68a;margin-top:.3em">';
    h+='Fitted pose differs from layout by '+(pf.deltaXyzMm||0).toFixed(0)+'mm '+
       '(residual RMS '+(pf.residualRmsMm||0).toFixed(1)+'mm, '+
       (pf.sampleCount||0)+' markers)';
    h+='</div>';
    h+='<div style="font-size:.74em;color:#94a3b8;font-family:monospace;margin-top:.3em">';
    h+='current  ['+cur.map(function(v){return v.toFixed(0);}).join(', ')+']<br>';
    h+='fitted   ['+fit2.map(function(v){return v.toFixed(0);}).join(', ')+']';
    h+='</div>';
    h+='<div style="margin-top:.4em;display:flex;gap:.4em">';
    h+='<button class="btn btn-on" onclick="_moverCalApplyPoseFit()">Apply fitted pose</button>';
    h+='<button class="btn" style="background:#0f172a;color:#fef3c7;border:1px solid #f59e0b" onclick="_moverCalDismissPoseFit()">Dismiss</button>';
    h+='</div></div>';
  }else if(r.poseFitConfirmed){
    var pfc=r.poseFitConfirmed;
    h+='<div style="font-size:.74em;color:#4ade80;margin-bottom:.4em">'+
       '\u2713 Pose-fit confirms layout (drift '+(pfc.deltaXyzMm||0).toFixed(0)+
       'mm, residual RMS '+(pfc.residualRmsMm||0).toFixed(1)+'mm)</div>';
  }
  // Residual table with exclude buttons (#504)
  h+='<div id="mcal-residual-table"></div>';
  h+='<div style="margin-top:.6em;display:flex;gap:.4em;justify-content:center;flex-wrap:wrap">';
  h+='<button class="btn btn-on" onclick="closeModal();_moverCalFid=null;loadLayout()">Done</button>';
  h+='<button class="btn" style="background:#0f172a;color:#e2e8f0;border:1px solid #334155" onclick="_moverCalShowIn3D()">Show residuals in 3D</button>';
  h+='<button class="btn" style="background:#334155;color:#94a3b8" onclick="_moverCalDelete()">Re-calibrate (full)</button>';
  h+='</div></div>';
  status.innerHTML=h;
  // Pull the persisted samples + per-sample errors from the GET endpoint.
  ra('GET','/api/calibration/mover/'+_moverCalFid,null,function(full){
    _moverCalExistingCache=full;
    _moverCalRenderResidualTable(full);
  });
}

// #504 — residual table lists each sample's per-sample error and lets
// the operator exclude an outlier, which triggers a server-side re-fit.
function _moverCalRenderResidualTable(r){
  var box=document.getElementById('mcal-residual-table');
  if(!box)return;
  if(!r||!r.samples){box.innerHTML='';return;}
  var errs=(r.fit&&r.fit.perSampleDeg)||[];
  // perSampleDeg may not be in the persisted fit; fall back to empty.
  var n=r.samples.length;
  var h='<div class="card" style="padding:.5em;margin-bottom:.5em;background:#0f172a">';
  h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.3em">Samples (click \u2715 to exclude and re-fit):</div>';
  for(var i=0;i<n;i++){
    var s=r.samples[i];
    var pan=s.pan!=null?s.pan:(Array.isArray(s)?s[0]:'-');
    var tilt=s.tilt!=null?s.tilt:(Array.isArray(s)?s[1]:'-');
    var err=errs[i];
    var col='#64748b';
    if(err!=null){
      if(err>3)col='#ef4444';
      else if(err>1)col='#f59e0b';
      else col='#4ade80';
    }
    h+='<div style="display:flex;gap:.4em;font-size:.74em;font-family:monospace;padding:2px 0;border-bottom:1px solid #1e293b">';
    h+='<span style="width:24px;color:#64748b">'+(i+1)+'.</span>';
    h+='<span style="flex:1;color:#94a3b8">pan='+(typeof pan==='number'?pan.toFixed(3):pan)
         +' tilt='+(typeof tilt==='number'?tilt.toFixed(3):tilt)+'</span>';
    h+='<span style="min-width:60px;color:'+col+';text-align:right">'+(err!=null?err.toFixed(2)+'\u00b0':'-')+'</span>';
    h+='<button class="btn" style="background:transparent;color:#64748b;padding:0 6px;font-size:.9em" onclick="_moverCalExcludeSample('+i+')">\u2715</button>';
    h+='</div>';
  }
  h+='</div>';
  box.innerHTML=h;
}

function _moverCalExcludeSample(idx){
  if(!confirm('Exclude sample '+(idx+1)+' and re-fit?'))return;
  ra('POST','/api/calibration/mover/'+_moverCalFid+'/exclude-sample',{index:idx},function(r){
    if(!r||!r.ok){alert((r&&r.err)||'Re-fit failed');return;}
    // Refresh the existing-fit summary + residual table.
    ra('GET','/api/calibration/mover/'+_moverCalFid,null,function(full){
      _moverCalExistingCache=full;
      var res=document.getElementById('mcal-residual-table');
      if(res)_moverCalRenderResidualTable(full);
      var exist=document.getElementById('mcal-existing');
      if(exist){
        // Also refresh the fit badge if we're still on the start view.
        _moverCalRenderExisting(full);
      }
    });
  });
}

function _moverCalCancel(){
  // #594 — if a calibration job is running on the server, signal the
  // background thread to abort (fixture blackout + lock release happen on
  // the server side) before closing the modal. Without this POST the
  // thread would keep sweeping the fixture after the UI was dismissed.
  //
  // #602 — after POSTing /cancel, keep polling /status until it flips to
  // 'cancelled' (or an error / terminal state) before closing the modal.
  // This means the operator can trust that closing the wizard actually
  // means the fixture has stopped moving — no more "Cancel is a lie".
  var fid=_moverCalFid;
  var close=function(){
    if(_moverCalTimer)clearTimeout(_moverCalTimer);
    _moverCalCancellingFid=null;
    _moverCalFid=null;closeModal();
  };
  if(!fid){close();return;}
  ra('GET','/api/calibration/mover/'+fid+'/status',null,function(st){
    if(!st||st.status!=='running'){
      // Nothing running (or done/error/cancelled) — the Cancel button has
      // effectively become Close in those states, so just close.
      close();
      return;
    }
    var phaseEl=document.getElementById('mcal-phase');
    if(phaseEl)phaseEl.textContent='Cancelling… (waiting for current probe to finish)';
    _moverCalCancellingFid=fid;
    _moverCalUpdateActions('cancelling');
    ra('POST','/api/calibration/mover/'+fid+'/cancel',{},function(){
      // Poll until the thread acknowledges cancellation (status flips
      // from 'running' to 'cancelled' | 'error' | 'done'). Cap at ~10s
      // of polling so a wedged thread can't block the operator forever.
      var startedAt=Date.now(),MAX_MS=10000;
      var tick=function(){
        if(_moverCalCancellingFid!==fid)return;  // superseded
        ra('GET','/api/calibration/mover/'+fid+'/status',null,function(cs){
          if(_moverCalCancellingFid!==fid)return;
          if(!cs||cs.status!=='running'){
            _moverCalCancellingFid=null;
            close();
            return;
          }
          if(Date.now()-startedAt>MAX_MS){
            // Abort the wait but stay on-screen so the operator can see
            // the thread is genuinely stuck.
            if(phaseEl)phaseEl.innerHTML='<span style="color:#f59e0b">Cancel POSTed but calibration thread has not acknowledged after 10s — inspect server log.</span>';
            _moverCalCancellingFid=null;
            return;
          }
          _moverCalRenderProbe(cs);
          setTimeout(tick,500);
        });
      };
      setTimeout(tick,500);
    });
  });
}

function _moverCalDelete(){
  if(!_moverCalFid)return;
  ra('DELETE','/api/calibration/mover/'+_moverCalFid,null,function(){
    (_fixtures||[]).forEach(function(f){if(f.id===_moverCalFid)f.moverCalibrated=false;});
    _moverCalStart(_moverCalFid);
  });
}

// ── Manual mover calibration (#368) ──────────────────────────────────

function _moverCalManualStart(fixId){
  _moverCalFid=fixId;
  // Ensure _fixtures have positions from layout
  ra('GET','/api/layout',null,function(lay){
    if(lay&&lay.fixtures)_fixtures=lay.fixtures;
    // #614 — pull the surveyed ArUco marker registry so the "or from:"
    // dropdown in _manCalRenderMarkers can offer the already-measured
    // floor positions. Non-fatal on failure; the dropdown simply omits
    // ArUco entries.
    if(typeof _arucoLoad==='function'){
      _arucoLoad(function(){_moverCalManualStart2(fixId);});
    }else{
      _moverCalManualStart2(fixId);
    }
  });
}
var _calMarkers=(function(){try{var s=localStorage.getItem('slyled_cal_markers');return s?JSON.parse(s):null;}catch(e){return null;}})();
function _moverCalManualStart2(fixId){
  // Reuse persisted markers or start empty
  var markers=_calMarkers?JSON.parse(JSON.stringify(_calMarkers)):[];
  _manCal={fid:fixId,markers:markers,step:'markers',currentIdx:0,samples:[],channels:null,savedSamples:null};
  // Load existing calibration samples for this fixture (for restoring positions)
  ra('GET','/api/calibration/mover/'+fixId,null,function(cal){
    if(cal&&cal.samples)_manCal.savedSamples=cal.samples;
    _manCalRender();
  });
}

function _manCalRender(){
  if(!_manCal)return;
  var fid=_manCal.fid;
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===fid)f=fx;});
  var fname=f?f.name:'Fixture';
  if(_manCal.step==='markers')_manCalRenderMarkers(fname);
  else if(_manCal.step==='jog')_manCalRenderJog(fname);
  else if(_manCal.step==='verify')_manCalRenderVerify(fname);
  else if(_manCal.step==='done')_manCalRenderDone(fname);
}

function _manCalRenderMarkers(fname){
  var h='<div style="min-width:420px">';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.6em">Place physical markers (tape, objects) at known stage positions. You\'ll aim <strong>'+escapeHtml(fname)+'</strong> at each one.</div>';
  h+='<div style="font-size:.78em;color:#64748b;margin-bottom:.6em">Spread markers across the stage floor for best results. Minimum 2, recommended 4+.</div>';
  h+='<table style="width:100%;font-size:.82em;border-collapse:collapse">';
  h+='<tr style="color:#94a3b8"><th style="text-align:left;padding:2px 4px">#</th><th>Name</th><th>X (mm)</th><th>Y (mm)</th><th>Z (mm)</th><th></th></tr>';
  _manCal.markers.forEach(function(m,i){
    h+='<tr><td style="padding:2px 4px;color:#94a3b8">'+(i+1)+'</td>';
    h+='<td style="font-size:.78em;color:#22d3ee;max-width:80px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">'+(m.name?escapeHtml(m.name):'')+'</td>';
    h+='<td><input type="number" value="'+m.x+'" style="width:70px" onchange="_manCal.markers['+i+'].x=parseInt(this.value)||0"></td>';
    h+='<td><input type="number" value="'+m.y+'" style="width:70px" onchange="_manCal.markers['+i+'].y=parseInt(this.value)||0"></td>';
    h+='<td><input type="number" value="'+m.z+'" style="width:70px" onchange="_manCal.markers['+i+'].z=parseInt(this.value)||0"></td>';
    h+='<td><button class="btn" style="font-size:.75em;padding:1px 6px;background:#7f1d1d;color:#fca5a5" onclick="_manCal.markers.splice('+i+',1);_manCalRender()">&#x2715;</button></td></tr>';
  });
  h+='</table>';
  h+='<div style="margin-top:.4em;display:flex;gap:.5em;align-items:center;flex-wrap:wrap">';
  h+='<button class="btn" style="font-size:.8em;background:#1e3a5f;color:#93c5fd" onclick="_manCalAddMarker()">+ Add Marker</button>';
  // Dropdown — pick position from a layout object or fixture
  var pickItems=[];
  (_objects||[]).filter(function(o){return o.transform&&o.transform.pos;}).forEach(function(o){
    var p=o.transform.pos;
    pickItems.push({id:'obj:'+o.id,name:o.name,x:p[0],y:p[1],z:p[2]});
  });
  // Also include positioned fixtures (cameras, other movers — not the one being calibrated)
  // _fixtures already have x/y/z merged from layout by loadLayout()
  (_fixtures||[]).forEach(function(f){
    if(f.id===_manCal.fid)return; // skip self
    if(f.x||f.y||f.z){
      pickItems.push({id:'fix:'+f.id,name:f.name||('Fixture '+f.id),x:f.x,y:f.y,z:f.z});
    }
  });
  // #614 — surveyed ArUco markers from the #596 registry. Physical
  // floor-placed tags are the most reliable set of "known stage
  // positions" in the room; offering them here means the operator
  // doesn't re-type coordinates already entered for camera cal.
  var aruco=(typeof _aruco_cache!=='undefined'&&_aruco_cache&&_aruco_cache.markers)||[];
  aruco.forEach(function(m){
    var lbl='ArUco '+m.id+(m.note?' — '+m.note:'');
    pickItems.push({id:'aruco:'+m.id,name:lbl,x:m.x|0,y:m.y|0,z:m.z|0});
  });
  if(pickItems.length){
    h+='<span style="font-size:.78em;color:#64748b">or from:</span>';
    h+='<select style="font-size:.8em" onchange="_manCalAddFromPick(this)">';
    h+='<option value="">— pick —</option>';
    pickItems.forEach(function(it){
      h+='<option value="'+it.id+'">'+escapeHtml(it.name)+' ('+it.x+','+it.y+','+it.z+')</option>';
    });
    h+='</select>';
  }
  h+='</div>';
  h+='<div style="display:flex;gap:.4em;margin-top:.8em">';
  var canNext=_manCal.markers.length>=2;
  h+='<button class="btn btn-on" onclick="_manCalNextToJog()"'+(canNext?'':' disabled')+'>Next: Jog to Markers</button>';
  h+='<button class="btn btn-off" onclick="_manCal=null;closeModal()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Manual Calibration \u2014 Markers';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function _manCalAddMarker(){
  var sm=_sfStageMm();
  _manCal.markers.push({x:Math.round(sm.sw/2),y:Math.round(sm.sd/2),z:0});
  _manCalRender();
}
function _manCalAddFromPick(selEl){
  var val=selEl.value;if(!val)return;
  selEl.value='';
  var parts=val.split(':');var type=parts[0];var id=parseInt(parts[1]);
  var name='',x=0,y=0,z=0;
  if(type==='obj'){
    (_objects||[]).forEach(function(o){if(o.id===id&&o.transform){
      var p=o.transform.pos||[0,0,0];name=o.name;x=p[0];y=p[1];z=p[2];}});
  }else if(type==='fix'){
    (_fixtures||[]).forEach(function(f){if(f.id===id){name=f.name||'Fixture';x=f.x||0;y=f.y||0;z=f.z||0;}});
  }else if(type==='aruco'){
    // #614 — surveyed ArUco marker from the #596 registry
    var aruco=(typeof _aruco_cache!=='undefined'&&_aruco_cache&&_aruco_cache.markers)||[];
    aruco.forEach(function(m){
      if(m.id===id){
        name='ArUco '+m.id+(m.note?' — '+m.note:'');
        x=m.x|0;y=m.y|0;z=m.z|0;
      }
    });
  }
  if(!name)return;
  _manCal.markers.push({x:x,y:y,z:z,name:name});
  _manCalRender();
}

function _manCalNextToJog(){
  if(_manCal.markers.length<2)return;
  // Persist markers for reuse across fixtures and sessions
  _calMarkers=JSON.parse(JSON.stringify(_manCal.markers));
  try{localStorage.setItem('slyled_cal_markers',JSON.stringify(_calMarkers));}catch(e){}
  _manCal.step='jog';
  _manCal.currentIdx=0;
  _manCal.samples=[];
  // Fetch channel info for pan/tilt/dimmer offsets
  ra('GET','/api/dmx/fixture/'+_manCal.fid+'/channels',null,function(d){
    if(!d||!d.channels){_manCal.channels={};_manCalRender();return;}
    var ch={};
    d.channels.forEach(function(c){
      if(c.type==='pan')ch.pan=c.offset;
      if(c.type==='tilt')ch.tilt=c.offset;
      // #714 — fine pan/tilt offsets for 16-bit drive.
      if(c.type==='pan-fine')ch.panFine=c.offset;
      if(c.type==='tilt-fine')ch.tiltFine=c.offset;
      if(c.type==='dimmer')ch.dimmer=c.offset;
      if(c.type==='red')ch.red=c.offset;
      if(c.type==='green')ch.green=c.offset;
      if(c.type==='blue')ch.blue=c.offset;
      if(c.type==='white')ch.white=c.offset;
      if(c.type==='speed')ch.speed=c.offset;
      if(c.type==='strobe')ch.strobe=c.offset;
      if(c.type==='gobo')ch.gobo=c.offset;
    });
    _manCal.channels=ch;
    _manCal.allChannels=d.channels; // full channel list for DMX display
    // Render handles DMX send — it restores saved pan/tilt per marker + defaults
    _manCalRender();
  });
}

function _manCalRenderJog(fname){
  var idx=_manCal.currentIdx;
  var total=_manCal.markers.length;
  var m=_manCal.markers[idx];
  var ch=_manCal.channels||{};
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===_manCal.fid)f=fx;});
  var addr=f?('U'+(f.dmxUniverse||1)+' @ '+(f.dmxStartAddr||1)):'';
  // #714 \u2014 16-bit drive when the profile exposes pan-fine + tilt-fine.
  // Without the fine channels the operator gets ~2.1 deg per slider step
  // on a 540 deg pan, too coarse to land the beam on a 150 mm marker.
  var has16 = (ch.panFine != null && ch.tiltFine != null);
  var h='<div style="min-width:460px">';
  // Progress
  h+='<div class="prog-bar" style="height:6px;margin-bottom:.5em"><div class="prog-fill" style="width:'+Math.round((idx/total)*100)+'%"></div></div>';
  h+='<div style="font-size:.9em;color:#e2e8f0;margin-bottom:.4em">Marker <strong>'+(idx+1)+' of '+total+'</strong>: aim beam at <span style="color:#22d3ee">X='+m.x+' Y='+m.y+' Z='+m.z+'</span> mm'+(m.name?' <span style="color:#94a3b8">('+escapeHtml(m.name)+')</span>':'')+'</div>';
  h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.6em">Adjust sliders until the beam centers on the physical marker.'+(has16?' <span style="color:#4ade80">(16-bit drive)</span>':'')+'</div>';
  // All DMX channels
  var allCh=_manCal.allChannels||[];
  // Restore pan/tilt as a 16-bit value (or 8-bit equivalent on profiles
  // without fine channels). Saved samples carry pan/tilt as 0..1 floats.
  var pan16 = 32768, tilt16 = 32768;
  if(_manCal.samples[idx]){
    pan16 = Math.round(_manCal.samples[idx].pan * 65535);
    tilt16 = Math.round(_manCal.samples[idx].tilt * 65535);
  }else if(_manCal.savedSamples){
    var mm = _manCal.markers[idx];
    _manCal.savedSamples.forEach(function(s){
      if(s.stageX===mm.x && s.stageY===mm.y && s.stageZ===mm.z){
        pan16 = Math.round(s.pan * 65535);
        tilt16 = Math.round(s.tilt * 65535);
      }
    });
  }else if(idx>0 && _manCal.samples.length>0){
    var ls = _manCal.samples[_manCal.samples.length-1];
    pan16 = Math.round(ls.pan * 65535);
    tilt16 = Math.round(ls.tilt * 65535);
  }
  // 8-bit slider values derived from 16-bit (used by per-channel rows
  // for pan/tilt + the no-fine fallback path).
  var panVal = (pan16 >> 8) & 0xFF;
  var tiltVal = (tilt16 >> 8) & 0xFF;
  // Stash the 16-bit state so step buttons + confirm can read it back
  // without depending on slider rounding. Initialised below.
  _manCal._pan16 = pan16; _manCal._tilt16 = tilt16;
  if(has16){
    h+='<div style="display:grid;grid-template-columns:60px 1fr 60px;gap:.4em .5em;align-items:center;margin-bottom:.6em">';
    h+='<label style="font-size:.82em;color:#22d3ee;text-align:right">Pan</label>';
    h+='<input type="range" id="mcj-pan16" min="0" max="65535" step="1" value="'+pan16+'" oninput="_manCalJog16(\'pan\',this.value)">';
    h+='<span id="mcj-pan16v" style="font-size:.78em;color:#e2e8f0;font-family:monospace;text-align:right">'+pan16+'</span>';
    h+='<label style="font-size:.82em;color:#22d3ee;text-align:right">Tilt</label>';
    h+='<input type="range" id="mcj-tilt16" min="0" max="65535" step="1" value="'+tilt16+'" oninput="_manCalJog16(\'tilt\',this.value)">';
    h+='<span id="mcj-tilt16v" style="font-size:.78em;color:#e2e8f0;font-family:monospace;text-align:right">'+tilt16+'</span>';
    h+='</div>';
  }
  h+='<div style="max-height:280px;overflow-y:auto;border:1px solid #1e293b;border-radius:4px;padding:.4em;background:#0a0f1a;margin-bottom:.6em">';
  if(allCh.length){
    allCh.forEach(function(c){
      var isPan=(c.type==='pan'),isTilt=(c.type==='tilt');
      var isPanFine=(c.type==='pan-fine'),isTiltFine=(c.type==='tilt-fine');
      var isDim=(c.type==='dimmer'),isGreen=(c.type==='green'),isSpeed=(c.type==='speed');
      // #714 \u2014 when the 16-bit driver is active, pan/tilt coarse + fine
      // rows are read-only mirrors so operators can see the wire bytes
      // but can't desync them by dragging the per-channel slider.
      var defVal;
      if(isPan)      defVal = panVal;
      else if(isTilt) defVal = tiltVal;
      else if(isPanFine)  defVal = pan16 & 0xFF;
      else if(isTiltFine) defVal = tilt16 & 0xFF;
      else if(isDim)  defVal = 255;
      else if(isGreen)defVal = 255;
      else if(isSpeed)defVal = 0;
      else            defVal = c.default || 0;
      var curVal=defVal;
      var driven = has16 && (isPan||isTilt||isPanFine||isTiltFine);
      var highlight = (isPan||isTilt||isPanFine||isTiltFine)
                       ? 'color:#22d3ee;font-weight:600'
                       : 'color:#94a3b8';
      h+='<div style="display:flex;align-items:center;gap:.4em;margin-bottom:.2em">';
      h+='<label style="width:90px;font-size:.78em;'+highlight+';text-align:right;overflow:hidden;white-space:nowrap;text-overflow:ellipsis" title="'+escapeHtml(c.name)+'">'+escapeHtml(c.name)+'</label>';
      h+='<input type="range" min="0" max="255" value="'+curVal+'" style="flex:1'+(driven?';opacity:.55;cursor:not-allowed':'')+
         '" id="mcj-ch-'+c.offset+'"'+(driven?' disabled':' oninput="_manCalJogCh('+c.offset+',this.value)"')+'>';
      h+='<span id="mcj-chv-'+c.offset+'" style="width:28px;font-size:.78em;color:#e2e8f0;font-family:monospace;text-align:right">'+curVal+'</span>';
      h+='</div>';
    });
  } else {
    h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.3em">';
    h+='<label style="width:40px;font-size:.82em;color:#22d3ee;text-align:right">Pan</label>';
    h+='<input type="range" id="mcj-ch-'+(ch.pan||0)+'" min="0" max="255" value="'+panVal+'" style="flex:1" oninput="_manCalJogCh('+(ch.pan||0)+',this.value)">';
    h+='<span id="mcj-chv-'+(ch.pan||0)+'" style="width:28px;font-size:.78em;color:#e2e8f0;font-family:monospace">'+panVal+'</span></div>';
    h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.3em">';
    h+='<label style="width:40px;font-size:.82em;color:#22d3ee;text-align:right">Tilt</label>';
    h+='<input type="range" id="mcj-ch-'+(ch.tilt||1)+'" min="0" max="255" value="'+tiltVal+'" style="flex:1" oninput="_manCalJogCh('+(ch.tilt||1)+',this.value)">';
    h+='<span id="mcj-chv-'+(ch.tilt||1)+'" style="width:28px;font-size:.78em;color:#e2e8f0;font-family:monospace">'+tiltVal+'</span></div>';
  }
  h+='</div>';
  // Fine adjust buttons. With 16-bit drive, fine = +/-1 and coarse =
  // +/-256 (= 1 unit of the upper byte). Without fine channels, both
  // buttons step the 8-bit slider by 1.
  h+='<div style="display:flex;gap:.3em;margin-bottom:.6em;justify-content:center;flex-wrap:wrap">';
  if(has16){
    h+='<button class="btn" style="font-size:.75em;padding:2px 6px" onclick="_manCalNudge16(\'pan\',-256)">Pan \u226a</button>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge16(\'pan\',-1)">Pan \u2212</button>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge16(\'pan\',1)">Pan +</button>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 6px" onclick="_manCalNudge16(\'pan\',256)">Pan \u226b</button>';
    h+='<span style="width:14px"></span>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 6px" onclick="_manCalNudge16(\'tilt\',-256)">Tilt \u226a</button>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge16(\'tilt\',-1)">Tilt \u2212</button>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge16(\'tilt\',1)">Tilt +</button>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 6px" onclick="_manCalNudge16(\'tilt\',256)">Tilt \u226b</button>';
  }else{
    h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'pan\',-1)">Pan \u2212</button>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'pan\',1)">Pan +</button>';
    h+='<span style="width:10px"></span>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'tilt\',-1)">Tilt \u2212</button>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'tilt\',1)">Tilt +</button>';
  }
  h+='</div>';
  // Buttons
  h+='<div style="display:flex;gap:.4em">';
  h+='<button class="btn btn-on" onclick="_manCalConfirm()">Confirm Position</button>';
  if(idx>0)h+='<button class="btn" style="background:#334155;color:#94a3b8" onclick="_manCal.currentIdx--;_manCal.samples.pop();_manCalRender()">Back</button>';
  h+='<button class="btn btn-off" onclick="_manCalBlackout();_manCal=null;closeModal()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent=escapeHtml(fname)+' \u2014 '+addr+' \u2014 Jog';
  document.getElementById('modal-body').innerHTML=h;
  // Send pan/tilt + defaults to DMX so fixture aims at saved/restored position
  var ch=_manCal.channels||{};
  var sendChs=[];
  if(ch.pan!=null)sendChs.push({offset:ch.pan,value:panVal});
  if(ch.tilt!=null)sendChs.push({offset:ch.tilt,value:tiltVal});
  // #714 — write 16-bit LSB if the profile has fine channels.
  if(has16){
    sendChs.push({offset:ch.panFine, value: pan16 & 0xFF});
    sendChs.push({offset:ch.tiltFine, value: tilt16 & 0xFF});
  }
  if(ch.dimmer!=null)sendChs.push({offset:ch.dimmer,value:255});
  if(ch.red!=null)sendChs.push({offset:ch.red,value:255});
  if(ch.green!=null)sendChs.push({offset:ch.green,value:255});
  if(ch.blue!=null)sendChs.push({offset:ch.blue,value:255});
  (_manCal.allChannels||[]).forEach(function(c){
    if(c.default>0&&c.type!=='pan'&&c.type!=='tilt'&&c.type!=='dimmer'
       &&c.type!=='red'&&c.type!=='green'&&c.type!=='blue'
       &&c.type!=='pan-fine'&&c.type!=='tilt-fine')
      sendChs.push({offset:c.offset,value:c.default});
  });
  if(sendChs.length)ra('POST','/api/dmx/fixture/'+_manCal.fid+'/test',{channels:sendChs},function(){});
}

// #714 — 16-bit drive helper. Splits a 0..65535 value into coarse +
// fine bytes, sends both together, and updates the per-channel mirror
// sliders + readouts so the all-channels display stays in sync.
function _manCalJog16(axis, value){
  if(!_manCal)return;
  var v16 = Math.max(0, Math.min(65535, parseInt(value)||0));
  var ch = _manCal.channels || {};
  var coarseOff = (axis === 'pan') ? ch.pan : ch.tilt;
  var fineOff = (axis === 'pan') ? ch.panFine : ch.tiltFine;
  if(coarseOff == null || fineOff == null) return;
  var coarse = (v16 >> 8) & 0xFF;
  var fine = v16 & 0xFF;
  if(axis === 'pan') _manCal._pan16 = v16; else _manCal._tilt16 = v16;
  // Update the 16-bit slider + readout (in case the caller adjusted via nudge).
  var sl = document.getElementById('mcj-' + axis + '16');
  if(sl) sl.value = v16;
  var rd = document.getElementById('mcj-' + axis + '16v');
  if(rd) rd.textContent = v16;
  // Mirror to the per-channel readouts so the all-channels list is honest.
  var cr = document.getElementById('mcj-ch-' + coarseOff);
  var fr = document.getElementById('mcj-ch-' + fineOff);
  if(cr) cr.value = coarse;
  if(fr) fr.value = fine;
  var crv = document.getElementById('mcj-chv-' + coarseOff);
  var frv = document.getElementById('mcj-chv-' + fineOff);
  if(crv) crv.textContent = coarse;
  if(frv) frv.textContent = fine;
  // Send full slider state so other channels (dimmer, color, etc.)
  // stay at their current values.
  var allCh = (_manCal.allChannels) || [];
  var batch = [];
  allCh.forEach(function(c){
    if(c.offset === coarseOff) batch.push({offset: c.offset, value: coarse});
    else if(c.offset === fineOff) batch.push({offset: c.offset, value: fine});
    else {
      var s = document.getElementById('mcj-ch-' + c.offset);
      if(s) batch.push({offset: c.offset, value: parseInt(s.value) || 0});
    }
  });
  if(batch.length) ra('POST', '/api/dmx/fixture/' + _manCal.fid + '/test',
                       {channels: batch}, function(){});
}

function _manCalNudge16(axis, delta){
  if(!_manCal) return;
  var cur = (axis === 'pan') ? _manCal._pan16 : _manCal._tilt16;
  if(cur == null) cur = 32768;
  _manCalJog16(axis, Math.max(0, Math.min(65535, cur + delta)));
}

function _manCalJogCh(offset,value){
  var v=parseInt(value);
  var el=document.getElementById('mcj-chv-'+offset);
  if(el)el.textContent=v;
  // Send ALL current slider values as a batch so fixture gets consistent state
  var allCh=(_manCal&&_manCal.allChannels)||[];
  var batch=[];
  allCh.forEach(function(c){
    var sl=document.getElementById('mcj-ch-'+c.offset);
    if(sl)batch.push({offset:c.offset,value:parseInt(sl.value)||0});
  });
  if(batch.length)ra('POST','/api/dmx/fixture/'+_manCal.fid+'/test',{channels:batch},function(){});
}

function _manCalJog(axis,value){
  var ch=_manCal.channels||{};
  var offset=axis==='pan'?ch.pan:ch.tilt;
  if(offset==null)return;
  var v=parseInt(value);
  _manCalJogCh(offset,v);
  var sl=document.getElementById('mcj-ch-'+offset);
  if(sl)sl.value=v;
}

function _manCalNudge(axis,dir){
  var ch=_manCal.channels||{};
  var offset=axis==='pan'?ch.pan:ch.tilt;
  if(offset==null)return;
  var sl=document.getElementById('mcj-ch-'+offset);
  if(!sl)return;
  var v=Math.max(0,Math.min(255,parseInt(sl.value)+dir));
  sl.value=v;
  _manCalJogCh(offset,v);
}

function _manCalConfirm(){
  var ch=_manCal.channels||{};
  // #714 — read 16-bit pan/tilt when fine channels exist; saved
  // sample's pan / tilt fields stay 0..1 floats but now carry the
  // full 16-bit precision, matching what the cal pipeline writes
  // since #689.
  var pan, tilt;
  if(ch.panFine != null && ch.tiltFine != null
       && _manCal._pan16 != null && _manCal._tilt16 != null){
    pan = _manCal._pan16 / 65535;
    tilt = _manCal._tilt16 / 65535;
  } else {
    var panEl=document.getElementById('mcj-ch-'+(ch.pan!=null?ch.pan:0));
    var tiltEl=document.getElementById('mcj-ch-'+(ch.tilt!=null?ch.tilt:1));
    pan=(panEl?parseInt(panEl.value):128)/255;
    tilt=(tiltEl?parseInt(tiltEl.value):128)/255;
  }
  var m=_manCal.markers[_manCal.currentIdx];
  _manCal.samples.push({pan:pan,tilt:tilt,stageX:m.x,stageY:m.y,stageZ:m.z});
  _manCal.currentIdx++;
  if(_manCal.currentIdx>=_manCal.markers.length){
    // All markers done — save
    _manCalSave();
  }else{
    _manCalRender();
  }
}

function _manCalSave(){
  _manCal.step='saving';
  document.getElementById('modal-body').innerHTML='<div style="text-align:center;padding:1em;color:#94a3b8">Saving calibration...</div>';
  ra('POST','/api/calibration/mover/'+_manCal.fid+'/manual',{samples:_manCal.samples},function(r){
    if(r&&r.ok){
      (_fixtures||[]).forEach(function(f){if(f.id===_manCal.fid)f.moverCalibrated=true;});
      _manCal.step='verify';
      _manCalRender();
    }else{
      document.getElementById('modal-body').innerHTML='<div style="color:#f66;padding:1em">'+(r&&r.err||'Save failed')+'</div>';
    }
  });
}

function _manCalRenderVerify(fname){
  var h='<div style="min-width:400px;text-align:center;padding:.5em">';
  h+='<div style="font-size:2em;color:#4ade80;margin-bottom:.3em">\u2713</div>';
  h+='<div style="font-size:1.1em;color:#e2e8f0;margin-bottom:.3em">Manual Calibration Complete</div>';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.8em">'+_manCal.samples.length+' positions recorded for '+escapeHtml(fname)+'</div>';
  h+='<div style="font-size:.82em;color:#94a3b8;margin-bottom:.6em">Test each marker \u2014 the beam should aim at the physical position:</div>';
  h+='<div style="text-align:left;max-height:200px;overflow-y:auto">';
  _manCal.samples.forEach(function(s,i){
    h+='<div style="display:flex;align-items:center;gap:.4em;padding:.3em;border-bottom:1px solid #1e293b">';
    h+='<span style="color:#94a3b8;font-size:.82em;width:20px">'+(i+1)+'</span>';
    h+='<span style="font-size:.82em;color:#e2e8f0;flex:1">X='+s.stageX+' Y='+s.stageY+'</span>';
    h+='<button class="btn" style="font-size:.75em;padding:2px 10px;background:#1e3a5f;color:#93c5fd" onclick="_manCalTest('+i+')">Test</button>';
    h+='</div>';
  });
  h+='</div>';
  h+='<div style="display:flex;gap:.4em;margin-top:.8em;justify-content:center">';
  h+='<button class="btn btn-on" onclick="_manCalBlackout();closeModal();_manCal=null;loadLayout()">Done</button>';
  h+='<button class="btn" style="background:#334155;color:#94a3b8" onclick="_moverCalDelete()">Recalibrate</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Calibration \u2014 Verify';
  document.getElementById('modal-body').innerHTML=h;
}

function _manCalTest(idx){
  var s=_manCal.samples[idx];
  var ch=_manCal.channels||{};
  var chs=[];
  if(ch.pan!=null)chs.push({offset:ch.pan,value:Math.round(s.pan*255)});
  if(ch.tilt!=null)chs.push({offset:ch.tilt,value:Math.round(s.tilt*255)});
  if(ch.dimmer!=null)chs.push({offset:ch.dimmer,value:255});
  if(ch.red!=null)chs.push({offset:ch.red,value:255});
  if(ch.green!=null)chs.push({offset:ch.green,value:255});
  if(ch.blue!=null)chs.push({offset:ch.blue,value:255});
  // Also send channel defaults (strobe open, color wheel white, etc.)
  (_manCal.allChannels||[]).forEach(function(c){
    if(c.default>0&&c.type!=='pan'&&c.type!=='tilt'&&c.type!=='dimmer'
       &&c.type!=='red'&&c.type!=='green'&&c.type!=='blue'){
      chs.push({offset:c.offset,value:c.default});
    }
  });
  ra('POST','/api/dmx/fixture/'+_manCal.fid+'/test',{channels:chs},function(){});
}

function _manCalBlackout(){
  var ch=_manCal?_manCal.channels:{};
  if(!ch)return;
  var chs=[];
  for(var k in ch)if(ch[k]!=null)chs.push({offset:ch[k],value:0});
  if(chs.length)ra('POST','/api/dmx/fixture/'+_manCal.fid+'/test',{channels:chs},function(){});
}

function _manCalVerifyExisting(fixId){
  _moverCalFid=fixId;
  // Fetch saved calibration and channel info
  ra('GET','/api/calibration/mover/'+fixId,null,function(cal){
    if(!cal||!cal.calibrated){
      document.getElementById('modal-body').innerHTML='<div style="color:#f66;padding:1em">No calibration data found.</div>';
      return;
    }
    ra('GET','/api/dmx/fixture/'+fixId+'/channels',null,function(d){
      var ch={};
      if(d&&d.channels)d.channels.forEach(function(c){
        if(c.type==='pan')ch.pan=c.offset;
        if(c.type==='tilt')ch.tilt=c.offset;
        if(c.type==='dimmer')ch.dimmer=c.offset;
        if(c.type==='red')ch.red=c.offset;
        if(c.type==='green')ch.green=c.offset;
        if(c.type==='blue')ch.blue=c.offset;
      });
      _manCal={fid:fixId,samples:cal.samples||[],channels:ch,step:'verify',
               allChannels:d&&d.channels?d.channels:[]};
      _manCalRender();
    });
  });
}

// ── Tracking mode — live person markers ──────────────────────────────
var _trackingCams={};  // {camId: true}
var _trackPollTimer=null;

function _trackToggle(camId){
  if(_trackingCams[camId]){
    _trackStop(camId);
  }else{
    _trackStart(camId);
  }
}

function _setupTrackToggle(camId){
  var btn=document.getElementById('setup-trk-'+camId);
  if(btn){btn.disabled=true;btn.textContent='...';}
  _trackToggle(camId);
}

function _trackBtnSync(camId){
  var active=!!_trackingCams[camId];
  // Update Setup tab button if present
  var sb=document.getElementById('setup-trk-'+camId);
  if(sb){sb.disabled=false;sb.textContent=active?'Stop Track':'Track';sb.style.background=active?'#9f1239':'#be185d';}
  // Update modal button if present
  var mb=document.getElementById('trk-btn-'+camId);
  if(mb){mb.textContent=active?'Stop Track':'Track';mb.style.background=active?'#9f1239':'#be185d';}
}

function _trackStart(camId){
  var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===camId)f=fx;});
  var classes=(f&&f.trackClasses)?f.trackClasses:["person"];
  var fps=(f&&f.trackFps)?f.trackFps:2;
  var thr=(f&&f.trackThreshold)?f.trackThreshold:0.4;
  var ttl=(f&&f.trackTtl)?f.trackTtl:5;
  var reid=(f&&f.trackReidMm)?f.trackReidMm:500;
  document.getElementById('hs').textContent='Starting tracking...';
  ra('POST','/api/cameras/'+camId+'/track/start',{fps:fps,threshold:thr,ttl:ttl,classes:classes,reidMm:reid},function(r){
    if(r&&r.ok){
      _trackingCams[camId]=true;
      var lbl=classes.length===1?classes[0]:classes.length+' classes';
      document.getElementById('hs').textContent='Tracking active \u2014 watching for '+lbl;
      _trackPollStart();
    }else{
      document.getElementById('hs').textContent='Track start failed: '+(r&&r.err||'unknown');
    }
    _trackBtnSync(camId);
  });
}

function _trackStop(camId){
  ra('POST','/api/cameras/'+camId+'/track/stop',{},function(r){
    delete _trackingCams[camId];
    document.getElementById('hs').textContent='Tracking stopped';
    if(!Object.keys(_trackingCams).length)_trackPollStop();
    _trackBtnSync(camId);
  });
}

function _trackPollStart(){
  if(_trackPollTimer)return;
  _trackPollTimer=setInterval(function(){
    loadObjects(function(){
      if(_s3d.inited)_s3dRenderObjects();
    });
  },1500);
}

function _trackPollStop(){
  if(_trackPollTimer){clearInterval(_trackPollTimer);_trackPollTimer=null;}
}

// ── Environment point cloud scan + 3D preview ────────────────────────
var _pointCloudData=null;
var _pointCloudVisible=false;

function _envScan(){
  document.getElementById('modal-title').textContent='Environment Scan';
  document.getElementById('modal-body').innerHTML=
    '<p style="color:#94a3b8;font-size:.85em;margin-bottom:.6em">Scanning the environment with all positioned cameras. This builds a 3D point cloud of the physical space.</p>'
    +'<div class="prog-bar" style="height:10px;margin-bottom:.5em"><div class="prog-fill" id="env-scan-fill" style="width:0%"></div></div>'
    +'<div id="env-scan-msg" style="font-size:.82em;color:#64748b">Starting...</div>';
  document.getElementById('modal').style.display='block';
  ra('POST','/api/space/scan',{},function(r){
    if(!r||!r.ok){
      document.getElementById('env-scan-msg').innerHTML='<span style="color:#f66">'+(r&&r.err||'Failed')+'</span>';
      return;
    }
    var poll=setInterval(function(){
      ra('GET','/api/space/scan/status',null,function(s){
        var fill=document.getElementById('env-scan-fill');
        var msg=document.getElementById('env-scan-msg');
        if(!fill||!msg){clearInterval(poll);return;}
        fill.style.width=s.progress+'%';
        msg.textContent=s.message||('Progress: '+s.progress+'%');
        if(!s.running){
          clearInterval(poll);
          if(s.totalPoints>0){
            fill.style.background='#059669';
            msg.innerHTML='<span style="color:#4ade80">\u2713 '+s.totalPoints+' points captured</span>'
              +' <button class="btn btn-on" onclick="closeModal();_loadPointCloud()" style="margin-left:.5em;font-size:.8em">Show in 3D</button>';
          }else{
            msg.innerHTML='<span style="color:#fbbf24">No points captured</span>';
          }
        }
      });
    },1000);
  });
}

function _loadPointCloud(cb){
  // #701 — fetch cloud data only. The caller decides whether to make it
  // visible / render / persist, based on the operator's intent at the
  // moment the load completes (which may differ from when it started).
  ra('GET','/api/space',null,function(r){
    var ok=!!(r&&r.ok&&r.points);
    if(ok){
      _pointCloudData=r;
      document.getElementById('hs').textContent=r.totalPoints+' point cloud loaded';
    }
    if(cb)cb(ok);
  });
}

function _togglePointCloud(){
  // When invoked from the View-menu checkbox, trust the checkbox state so it
  // can't desync from _pointCloudVisible. Fall back to a plain flip for the
  // legacy button-driven path (#529).
  // #701 — async-load race fix: if a click triggers a load, re-check the
  // checkbox at the moment the load completes and honour THAT intent. The
  // load no longer auto-renders; only this function calls _renderPointCloud.
  var cb=document.getElementById('vw-cloud');
  var desired=cb?cb.checked:!_pointCloudVisible;
  if(!_pointCloudData){
    if(!desired){_pointCloudVisible=false;_updateCloudBtn();_persistCloudPref(false);return;}
    _loadPointCloud(function(ok){
      // Re-read the checkbox NOW — operator may have toggled while loading.
      var stillWanted=cb?cb.checked:_pointCloudVisible;
      if(!ok){
        document.getElementById('hs').textContent='No point cloud — run environment scan first';
        _pointCloudVisible=false;
        if(cb)cb.checked=false;
        _updateCloudBtn();
        _persistCloudPref(false);
        return;
      }
      _pointCloudVisible=stillWanted;
      if(_s3d.inited)_renderPointCloud();
      _updateCloudBtn();
      _persistCloudPref(stillWanted);
    });
    return;
  }
  _pointCloudVisible=desired;
  if(_s3d.inited)_renderPointCloud();
  _updateCloudBtn();
  _persistCloudPref(desired);
}

// #638 — persist point-cloud visibility directly (Bug 2: previously only
// written when another toggle triggered _viewSave; could be lost on reload).
function _persistCloudPref(visible){
  try{
    var raw=localStorage.getItem('slyled-view-prefs');
    var p=raw?JSON.parse(raw):{};
    p.cloud=!!visible;
    localStorage.setItem('slyled-view-prefs',JSON.stringify(p));
  }catch(e){}
}

function _updateCloudBtn(){
  var btn=document.getElementById('btn-show-cloud');
  if(btn){
    btn.style.background=_pointCloudVisible?'#1e3a5f':'';
    btn.style.color=_pointCloudVisible?'#93c5fd':'';
  }
  var cb=document.getElementById('vw-cloud');
  if(cb)cb.checked=_pointCloudVisible;
}

function _renderPointCloud(){
  if(!_s3d.inited)return;
  // Remove old cloud
  _s3d.scene.children.forEach(function(c){if(c.userData&&c.userData.pointCloud)c.visible=false;});
  var old=_s3d.scene.children.filter(function(c){return c.userData&&c.userData.pointCloud;});
  old.forEach(function(c){_s3d.scene.remove(c);if(c.geometry)c.geometry.dispose();if(c.material)c.material.dispose();});

  if(!_pointCloudVisible||!_pointCloudData||!_pointCloudData.points)return;

  var pts=_pointCloudData.points;
  var positions=new Float32Array(pts.length*3);
  var colors=new Float32Array(pts.length*3);
  for(var i=0;i<pts.length;i++){
    // Stage→Three.js: X=X, Y(depth)→Z, Z(height)→Y  (matches _s3dPos)
    positions[i*3]=pts[i][0]/1000;      // stage X → 3D X
    positions[i*3+1]=pts[i][2]/1000;    // stage Z (height) → 3D Y (up)
    positions[i*3+2]=pts[i][1]/1000;    // stage Y (depth)  → 3D Z (depth)
    colors[i*3]=pts[i][3]/255;
    colors[i*3+1]=pts[i][4]/255;
    colors[i*3+2]=pts[i][5]/255;
  }
  var geo=new THREE.BufferGeometry();
  geo.setAttribute('position',new THREE.BufferAttribute(positions,3));
  geo.setAttribute('color',new THREE.BufferAttribute(colors,3));
  var mat=new THREE.PointsMaterial({size:0.06,vertexColors:true,transparent:true,opacity:0.7,sizeAttenuation:true,depthWrite:false});
  var cloud=new THREE.Points(geo,mat);
  cloud.userData.pointCloud=true;
  _s3d.scene.add(cloud);
}


// ── #720 PR-6 — SMART validation pass UI ───────────────────────────────
//
// After the SMART solver finishes the cal status flips to "validating"
// with a list of surveyed ArUco markers inside the working area. The
// operator slews to each marker via a button, eyeballs the beam against
// the physical sticker, and answers Hit / Miss. Final commit happens
// only after every marker is confirmed Hit. Any Miss aborts.

function _smartValidateRender(fid){
  // Mounted into the cal status box just below the phase line.
  var status=document.getElementById('mcal-status');
  if(!status)return;
  var panel=document.getElementById('smart-validate-panel');
  if(!panel){
    panel=document.createElement('div');
    panel.id='smart-validate-panel';
    panel.style.cssText='margin-top:.6em;padding:.5em;background:#0f172a;border:1px solid #1e3a5f;border-radius:4px';
    status.appendChild(panel);
  }
  ra('GET','/api/calibration/mover/'+fid+'/smart/validate/state',null,function(r){
    if(!r||!r.ok){
      panel.innerHTML='<div style="font-size:.78em;color:#f87171">Validation state unavailable</div>';
      return;
    }
    var markers=r.markers||[];
    if(!markers.length){
      panel.innerHTML='<div style="font-size:.78em;color:#94a3b8">No surveyed markers in working area — calibration committing automatically.</div>';
      return;
    }
    var h='<div style="font-size:.85em;color:#e2e8f0;font-weight:bold;margin-bottom:.4em">Confirm SMART calibration ('+markers.length+' markers)</div>';
    h+='<div style="font-size:.74em;color:#94a3b8;margin-bottom:.5em">For each marker: Slew → check beam lands on the physical sticker → Hit (green) or Miss (red).</div>';
    h+='<div style="display:flex;flex-direction:column;gap:.35em">';
    for(var i=0;i<markers.length;i++){
      var m=markers[i];
      var col='#64748b'; var lbl='pending';
      if(m.confirmed===true){col='#4ade80';lbl='Hit';}
      else if(m.confirmed===false){col='#ef4444';lbl='Miss';}
      var dis=(m.confirmed===null)?'':'disabled';
      h+='<div style="display:flex;align-items:center;gap:.4em;padding:.3em;background:#0a0e1a;border-left:3px solid '+col+';border-radius:3px">';
      h+='<span style="flex:1;font-size:.78em;color:#cbd5e1">'+m.name+' <span style="color:#64748b">('+Math.round(m.x)+', '+Math.round(m.y)+', '+Math.round(m.z)+') mm</span></span>';
      h+='<span style="font-size:.74em;color:'+col+';min-width:50px">'+lbl+'</span>';
      h+='<button class="btn" '+dis+' onclick="_smartValidateAim('+fid+','+m.id+')" style="font-size:.74em;padding:2px 8px;background:#1e3a5f;color:#93c5fd">Slew</button>';
      h+='<button class="btn" '+dis+' onclick="_smartValidateConfirm('+fid+','+m.id+',true)" style="font-size:.74em;padding:2px 8px;background:#064e3b;color:#86efac">Hit</button>';
      h+='<button class="btn" '+dis+' onclick="_smartValidateConfirm('+fid+','+m.id+',false)" style="font-size:.74em;padding:2px 8px;background:#7f1d1d;color:#fca5a5">Miss</button>';
      h+='</div>';
    }
    h+='</div>';
    panel.innerHTML=h;
  });
}

function _smartValidateAim(fid,markerId){
  ra('POST','/api/calibration/mover/'+fid+'/smart/validate/aim',
     {markerId:markerId},function(r){
    if(!r||!r.ok){
      alert('Slew failed: '+((r&&r.err)||'unknown'));
      return;
    }
  });
}

function _smartValidateConfirm(fid,markerId,hit){
  ra('POST','/api/calibration/mover/'+fid+'/smart/validate/confirm',
     {markerId:markerId,hit:hit},function(r){
    if(!r||!r.ok){
      alert('Confirm failed: '+((r&&r.err)||'unknown'));
      return;
    }
    // Server has updated job state; re-render the panel.
    _smartValidateRender(fid);
    // If commit landed, refresh the cal card so it shows the green
    // "Current calibration" block.
    if(r.committed){
      (_fixtures||[]).forEach(function(f){if(f.id===fid)f.moverCalibrated=true;});
      renderSidebar();
    }
  });
}

function _smartValidateRenderFailed(fid){
  var status=document.getElementById('mcal-status');
  if(!status)return;
  var panel=document.getElementById('smart-validate-panel');
  if(!panel){
    panel=document.createElement('div');
    panel.id='smart-validate-panel';
    panel.style.cssText='margin-top:.6em;padding:.5em;background:#3f1d1d;border:1px solid #7f1d1d;border-radius:4px';
    status.appendChild(panel);
  }
  panel.innerHTML='<div style="font-size:.85em;color:#fca5a5;font-weight:bold">SMART validation failed</div>'
    +'<div style="font-size:.78em;color:#f87171;margin-top:.3em">At least one marker did not hit. Calibration was discarded; the prior calibration record (if any) is preserved. Re-run SMART after checking the home anchor and the working area.</div>';
}
