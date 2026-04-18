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

// ── Moving head range calibration ─────────────────────────────────────
var _rcalState=null;

function _rangeCalStart(fixId){
  // Find a calibrated camera
  var cam=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera'&&f.calibrated&&f.positioned;})[0];
  if(!cam){
    document.getElementById('hs').textContent='No calibrated camera available — calibrate a camera first';
    return;
  }
  _rcalState={fixId:fixId,camId:cam.id,axis:'pan',step:0,steps:9,
    panSamples:[],tiltSamples:[],sweepValues:[]};
  // Generate sweep values: 0.0, 0.125, 0.25, ..., 1.0
  for(var i=0;i<=8;i++)_rcalState.sweepValues.push(i/8);
  _rcalState.steps=_rcalState.sweepValues.length;
  _rangeCalShow();
}

function _rangeCalShow(){
  var s=_rcalState;if(!s)return;
  var fix=(_fixtures||[]).filter(function(f){return f.id===s.fixId;})[0];
  var axisLabel=s.axis==='pan'?'Pan':'Tilt';
  var totalSteps=s.steps*2;  // pan + tilt
  var doneSteps=(s.axis==='pan'?0:s.steps)+s.step;
  var h='<div style="min-width:380px">';
  h+='<p style="color:#94a3b8;font-size:.85em;margin-bottom:.6em">Sweeping <strong>'+axisLabel+'</strong> on '+escapeHtml(fix?fix.name:'fixture')+'. The head will move through its range while the camera captures beam positions.</p>';
  h+='<div class="prog-bar" style="height:8px;margin-bottom:.5em"><div class="prog-fill" style="width:'+Math.round(doneSteps/totalSteps*100)+'%"></div></div>';
  h+='<div style="font-size:.82em;color:#64748b;margin-bottom:.6em">'+axisLabel+' step '+s.step+' / '+s.steps+'</div>';
  if(s.step<s.steps){
    var val=s.sweepValues[s.step];
    h+='<div style="font-size:.9em;color:#e2e8f0;margin-bottom:.4em">'+axisLabel+' = '+val.toFixed(3)+' <span style="color:#64748b">(DMX '+(s.axis==="pan"?"pan":"tilt")+': '+Math.round(val*255)+')</span></div>';
    h+='<div id="rcal-msg" style="font-size:.82em;color:#22d3ee;margin-bottom:.4em">Sending DMX + capturing...</div>';
    h+='<img id="rcal-snap" style="width:100%;border-radius:4px;border:1px solid #334155;display:none;margin-bottom:.4em">';
  }else if(s.axis==='pan'){
    h+='<div style="font-size:.9em;color:#4ade80;margin-bottom:.5em">\u2713 Pan sweep complete ('+s.panSamples.length+' samples). Starting tilt...</div>';
  }
  h+='<div style="display:flex;gap:.5em;margin-top:.6em">';
  if(s.step>=s.steps&&s.axis==='tilt'){
    h+='<button class="btn btn-on" onclick="_rangeCalSubmit()">Save Calibration</button>';
  }
  h+='<button class="btn btn-off" onclick="_rcalState=null;closeModal()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Range Calibration — '+axisLabel;
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  // Auto-advance: send DMX, capture, detect beam
  if(s.step<s.steps){
    setTimeout(function(){_rangeCalStep();},500);
  }else if(s.axis==='pan'){
    // Switch to tilt axis
    setTimeout(function(){
      s.axis='tilt';s.step=0;_rangeCalShow();
    },1000);
  }
}

function _rangeCalStep(){
  var s=_rcalState;if(!s||s.step>=s.steps)return;
  var val=s.sweepValues[s.step];
  // Send DMX pan/tilt value to the fixture via action
  var panVal=s.axis==='pan'?val:0.5;
  var tiltVal=s.axis==='tilt'?val:0.5;
  // Use direct DMX write — set pan/tilt channels
  ra('POST','/api/fixtures/'+s.fixId+'/dmx-test',
    {pan:panVal,tilt:tiltVal,dimmer:1.0},function(){
    // Wait for head to move, then capture
    setTimeout(function(){
      // Capture snapshot from camera
      var x=new XMLHttpRequest();
      x.open('GET','/api/cameras/'+s.camId+'/snapshot');
      x.responseType='blob';
      x.onload=function(){
        var img=document.getElementById('rcal-snap');
        var msg=document.getElementById('rcal-msg');
        if(x.status===200&&x.response){
          if(img){img.src=URL.createObjectURL(x.response);img.style.display='block';}
          // Run scan to find beam position
          ra('POST','/api/cameras/'+s.camId+'/scan',
            {threshold:0.1,resolution:320,cam:0},function(r){
            // Use brightest/largest detection or center of image
            var px=320,py=240;  // default center
            if(r&&r.ok&&r.detections&&r.detections.length){
              // Use the detection with highest confidence
              var best=r.detections.sort(function(a,b){return b.confidence-a.confidence;})[0];
              if(best.pixelBox){px=best.pixelBox.x+best.pixelBox.w/2;py=best.pixelBox.y+best.pixelBox.h/2;}
            }
            var sample={dmxNorm:val,pixelX:px,pixelY:py};
            if(s.axis==='pan')s.panSamples.push(sample);
            else s.tiltSamples.push(sample);
            s.step++;
            if(msg)msg.textContent='Captured at pixel ('+Math.round(px)+', '+Math.round(py)+')';
            setTimeout(function(){_rangeCalShow();},300);
          });
        }else{
          if(msg)msg.textContent='Capture failed — skipping step';
          s.step++;
          setTimeout(function(){_rangeCalShow();},500);
        }
      };
      x.send();
    },800);  // Wait 800ms for head to settle
  });
}

function _rangeCalSubmit(){
  var s=_rcalState;if(!s)return;
  ra('POST','/api/fixtures/'+s.fixId+'/calibrate-range',
    {cameraId:s.camId,panSamples:s.panSamples,tiltSamples:s.tiltSamples},function(r){
    if(r&&r.ok){
      var h='<div style="text-align:center;padding:1em">';
      h+='<div style="font-size:2em;color:#4ade80;margin-bottom:.3em">\u2713</div>';
      h+='<div style="font-size:1.1em;color:#e2e8f0;margin-bottom:.5em">Range Calibration Complete</div>';
      h+='<div style="font-size:.85em;color:#94a3b8">Pan: '+s.panSamples.length+' samples, Tilt: '+s.tiltSamples.length+' samples</div>';
      h+='<div style="margin-top:1em"><button class="btn btn-on" onclick="closeModal();_rcalState=null;loadLayout()">Done</button></div>';
      h+='</div>';
      document.getElementById('modal-body').innerHTML=h;
      (_fixtures||[]).forEach(function(f){if(f.id===s.fixId)f.rangeCalibrated=true;});
      renderSidebar();
    }else{
      document.getElementById('hs').textContent='Range calibration failed: '+(r&&r.err||'unknown');
    }
  });
}

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
            cameraPosition:r.cameraPosition||null,
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
  h+='<table class="tbl" style="margin-bottom:.4em"><tr><th>Camera</th><th>Position (mm)</th><th>RMS Error</th><th>Status</th></tr>';
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
    var pos=r.cameraPosition;
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
    var camsWithPos=selCams.filter(function(c){var r=_calWiz.stageMap[c.id];return r&&r.cameraPosition&&!r.error;});
    if(camsWithPos.length>1){
      h+='<div style="font-size:.78em;color:#94a3b8;margin-top:.4em">Inter-camera distances: ';
      for(var i=0;i<camsWithPos.length;i++){
        for(var j=i+1;j<camsWithPos.length;j++){
          var p1=_calWiz.stageMap[camsWithPos[i].id].cameraPosition;
          var p2=_calWiz.stageMap[camsWithPos[j].id].cameraPosition;
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
  h+='<div style="display:grid;grid-template-columns:120px 1fr;gap:.4em;align-items:center;font-size:.82em">';
  h+='<label style="color:#94a3b8;margin:0">Beam color:</label>';
  h+='<select id="mcal-color" style="font-size:.82em;padding:2px 4px">';
  h+='<option value="white">White</option><option value="green" selected>Green</option><option value="magenta">Magenta</option><option value="red">Red</option><option value="blue">Blue</option>';
  h+='</select>';
  h+='<label style="color:#94a3b8;margin:0">Method:</label>';
  h+='<select id="mcal-mode" style="font-size:.82em;padding:2px 4px" onchange="_moverCalModeChanged()">';
  h+='<option value="legacy" selected>Legacy BFS (broad sampling)</option>';
  h+='<option value="v2">v2 target-driven (#499, requires camera ArUco cal)</option>';
  h+='</select>';
  h+='<label style="color:#94a3b8;margin:0">Warm-up:</label>';
  h+='<label style="color:#e2e8f0;font-size:.8em;margin:0"><input type="checkbox" id="mcal-warmup" style="margin-right:.3em">Sweep pan/tilt for 30s before sampling (thermal settle — #513)</label>';
  h+='</div>';

  // Target preview (populated below when mode=v2)
  h+='<div id="mcal-targets-preview" style="display:none;margin-top:.5em;border-top:1px solid #334155;padding-top:.4em">';
  h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.3em">Auto-selected targets (<span id="mcal-targets-count">0</span>):</div>';
  h+='<div id="mcal-targets-list" style="font-size:.75em;color:#64748b;font-family:monospace;max-height:80px;overflow:auto"></div>';
  h+='</div>';
  h+='</div>';  // /mcal-options

  // Run-time status
  h+='<div id="mcal-status" style="display:none">';
  h+='<div class="prog-bar" style="height:8px;margin-bottom:.4em"><div class="prog-fill" id="mcal-prog" style="width:0%;transition:width .3s"></div></div>';
  h+='<div id="mcal-phase" style="font-size:.85em;color:#e2e8f0;margin-bottom:.3em"></div>';
  h+='<div id="mcal-detail" style="font-size:.78em;color:#64748b"></div>';
  h+='<div id="mcal-targets-progress" style="margin-top:.5em"></div>';
  h+='</div>';

  h+='<div id="mcal-actions" style="display:flex;gap:.4em;margin-top:.8em">';
  h+='<button class="btn btn-on" id="mcal-go" onclick="_moverCalGo()">Start Calibration</button>';
  h+='<button class="btn btn-off" onclick="_moverCalCancel()">Cancel</button>';
  h+='</div></div>';
  document.getElementById('modal-title').textContent='Calibrate Mover — '+escapeHtml(f?f.name:'fixture');
  document.getElementById('modal-body').innerHTML=h;

  // If already calibrated, fetch the stored fit + render the existing-fit summary.
  if(cal){
    ra('GET','/api/calibration/mover/'+_moverCalFid,null,function(r){
      _moverCalRenderExisting(r);
    });
  }
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
}

function _moverCalFitBadge(fit){
  if(!fit||fit.rmsErrorDeg==null)return'';
  var rms=fit.rmsErrorDeg;
  var col='#4ade80', lbl='GOOD';
  if(rms>1.5){col='#f59e0b';lbl='FAIR';}
  if(rms>3.0){col='#ef4444';lbl='POOR';}
  return '<span style="background:'+col+';color:#0a0e1a;padding:1px 6px;border-radius:4px;font-weight:bold;font-size:.72em;letter-spacing:.05em">'+lbl+' '+rms.toFixed(2)+'\u00b0</span>';
}

function _moverCalRenderExisting(r){
  var box=document.getElementById('mcal-existing');
  if(!box||!r||!r.calibrated)return;
  var h='<div class="card" style="padding:.6em;margin-bottom:.6em;background:#0f172a;border-left:3px solid #4ade80">';
  h+='<div style="display:flex;align-items:center;gap:.5em;margin-bottom:.3em">';
  h+='<div style="font-size:.85em;color:#e2e8f0;font-weight:bold">Current calibration</div>';
  h+=_moverCalFitBadge(r.fit);
  h+='</div>';
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
  ra('POST','/api/calibration/mover/'+_moverCalFid+'/start',
     {mode:'v2',warmup:false,color:[0,255,0]},function(r){
    if(!r||!r.ok){
      if(phase)phase.innerHTML='<span style="color:#f66">'+(r&&r.err||'Failed to start')+'</span>';
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
  var mode=(document.getElementById('mcal-mode')||{}).value||'legacy';
  var warmup=(document.getElementById('mcal-warmup')||{}).checked||false;
  var btn=document.getElementById('mcal-go');
  if(btn)btn.disabled=true;
  document.getElementById('mcal-status').style.display='block';
  document.getElementById('mcal-phase').textContent='Starting calibration...';
  ra('POST','/api/calibration/mover/'+_moverCalFid+'/start',
     {color:color,mode:mode,warmup:warmup},function(r){
    if(!r||!r.ok){
      document.getElementById('mcal-phase').innerHTML='<span style="color:#f66">'+(r&&r.err||'Failed to start')+'</span>';
      if(btn)btn.disabled=false;
      return;
    }
    var dbits=['Camera: '+(r.cameraName||'unknown'),'mode: '+(mode||'legacy')];
    if(warmup)dbits.push('warmup: yes');
    document.getElementById('mcal-detail').textContent=dbits.join(' \u00b7 ');
    _moverCalPoll();
  });
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
      mapping:'Mapping visible region...',
      sampling:'Sampling target points...',
      fitting:'Running Levenberg-Marquardt fit...',
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

    if(r.status==='running'){
      _moverCalTimer=setTimeout(_moverCalPoll,1000);
    }else if(r.status==='done'){
      if(prog)prog.style.width='100%';
      _moverCalRenderComplete(r);
      (_fixtures||[]).forEach(function(f){if(f.id===_moverCalFid)f.moverCalibrated=true;});
      renderSidebar();
    }else if(r.status==='error'){
      if(phase)phase.innerHTML='<span style="color:#f66">'+(r.error||'Unknown error')+'</span>';
      var btn=document.getElementById('mcal-go');
      if(btn){btn.disabled=false;btn.textContent='Retry';}
    }
  });
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
  if(_moverCalTimer)clearTimeout(_moverCalTimer);
  _moverCalFid=null;closeModal();
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
    _moverCalManualStart2(fixId);
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
  var h='<div style="min-width:460px">';
  // Progress
  h+='<div class="prog-bar" style="height:6px;margin-bottom:.5em"><div class="prog-fill" style="width:'+Math.round((idx/total)*100)+'%"></div></div>';
  h+='<div style="font-size:.9em;color:#e2e8f0;margin-bottom:.4em">Marker <strong>'+(idx+1)+' of '+total+'</strong>: aim beam at <span style="color:#22d3ee">X='+m.x+' Y='+m.y+' Z='+m.z+'</span> mm'+(m.name?' <span style="color:#94a3b8">('+escapeHtml(m.name)+')</span>':'')+'</div>';
  h+='<div style="font-size:.78em;color:#94a3b8;margin-bottom:.6em">Adjust sliders until the beam centers on the physical marker.</div>';
  // All DMX channels
  var allCh=_manCal.allChannels||[];
  var panVal=128, tiltVal=128;
  // Try to restore from: 1) previous sample for this marker, 2) saved calibration, 3) last confirmed sample
  if(_manCal.samples[idx]){
    panVal=Math.round(_manCal.samples[idx].pan*255);
    tiltVal=Math.round(_manCal.samples[idx].tilt*255);
  }else if(_manCal.savedSamples){
    // Match saved sample by stage position
    var m=_manCal.markers[idx];
    _manCal.savedSamples.forEach(function(s){
      if(s.stageX===m.x&&s.stageY===m.y&&s.stageZ===m.z){panVal=Math.round(s.pan*255);tiltVal=Math.round(s.tilt*255);}
    });
  }else if(idx>0&&_manCal.samples.length>0){
    var ls=_manCal.samples[_manCal.samples.length-1];panVal=Math.round(ls.pan*255);tiltVal=Math.round(ls.tilt*255);
  }
  h+='<div style="max-height:280px;overflow-y:auto;border:1px solid #1e293b;border-radius:4px;padding:.4em;background:#0a0f1a;margin-bottom:.6em">';
  if(allCh.length){
    allCh.forEach(function(c){
      var isPan=(c.type==='pan'),isTilt=(c.type==='tilt');
      var isDim=(c.type==='dimmer'),isGreen=(c.type==='green'),isSpeed=(c.type==='speed');
      var defVal=isPan?panVal:isTilt?tiltVal:isDim?255:isGreen?255:isSpeed?0:(c.default||0);
      // Current value from last state or default
      var curVal=defVal;
      var highlight=isPan||isTilt?'color:#22d3ee;font-weight:600':'color:#94a3b8';
      h+='<div style="display:flex;align-items:center;gap:.4em;margin-bottom:.2em">';
      h+='<label style="width:90px;font-size:.78em;'+highlight+';text-align:right;overflow:hidden;white-space:nowrap;text-overflow:ellipsis" title="'+escapeHtml(c.name)+'">'+escapeHtml(c.name)+'</label>';
      h+='<input type="range" min="0" max="255" value="'+curVal+'" style="flex:1" id="mcj-ch-'+c.offset+'" oninput="_manCalJogCh('+c.offset+',this.value)">';
      h+='<span id="mcj-chv-'+c.offset+'" style="width:28px;font-size:.78em;color:#e2e8f0;font-family:monospace;text-align:right">'+curVal+'</span>';
      h+='</div>';
    });
  } else {
    // Fallback: just pan/tilt
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
  // Fine adjust buttons for pan/tilt
  h+='<div style="display:flex;gap:.3em;margin-bottom:.6em;justify-content:center">';
  h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'pan\',-1)">Pan \u2212</button>';
  h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'pan\',1)">Pan +</button>';
  h+='<span style="width:10px"></span>';
  h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'tilt\',-1)">Tilt \u2212</button>';
  h+='<button class="btn" style="font-size:.75em;padding:2px 8px" onclick="_manCalNudge(\'tilt\',1)">Tilt +</button>';
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
  if(ch.dimmer!=null)sendChs.push({offset:ch.dimmer,value:255});
  if(ch.red!=null)sendChs.push({offset:ch.red,value:255});
  if(ch.green!=null)sendChs.push({offset:ch.green,value:255});
  if(ch.blue!=null)sendChs.push({offset:ch.blue,value:255});
  (_manCal.allChannels||[]).forEach(function(c){
    if(c.default>0&&c.type!=='pan'&&c.type!=='tilt'&&c.type!=='dimmer'
       &&c.type!=='red'&&c.type!=='green'&&c.type!=='blue')
      sendChs.push({offset:c.offset,value:c.default});
  });
  if(sendChs.length)ra('POST','/api/dmx/fixture/'+_manCal.fid+'/test',{channels:sendChs},function(){});
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
  var panEl=document.getElementById('mcj-ch-'+(ch.pan!=null?ch.pan:0));
  var tiltEl=document.getElementById('mcj-ch-'+(ch.tilt!=null?ch.tilt:1));
  var pan=(panEl?parseInt(panEl.value):128)/255;
  var tilt=(tiltEl?parseInt(tiltEl.value):128)/255;
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
  ra('GET','/api/space',null,function(r){
    if(r&&r.ok&&r.points){
      _pointCloudData=r;
      document.getElementById('hs').textContent=r.totalPoints+' point cloud loaded';
      _pointCloudVisible=true;
      if(_s3d.inited)_renderPointCloud();
      _updateCloudBtn();
    }
    if(cb)cb();
  });
}

function _togglePointCloud(){
  // When invoked from the View-menu checkbox, trust the checkbox state so it
  // can't desync from _pointCloudVisible. Fall back to a plain flip for the
  // legacy button-driven path (#529).
  var cb=document.getElementById('vw-cloud');
  var desired=cb?cb.checked:!_pointCloudVisible;
  if(!_pointCloudData){
    if(!desired){_pointCloudVisible=false;_updateCloudBtn();return;}
    _loadPointCloud(function(){
      if(!_pointCloudData){
        document.getElementById('hs').textContent='No point cloud — run environment scan first';
        if(cb)cb.checked=false;
      }
      // _loadPointCloud already set _pointCloudVisible=true and rendered
    });
    return;
  }
  _pointCloudVisible=desired;
  if(_s3d.inited)_renderPointCloud();
  _updateCloudBtn();
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
