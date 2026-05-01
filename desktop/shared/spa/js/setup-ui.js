/** setup-ui.js — Setup tab: fixture list, add/remove fixtures, discovery, camera nodes, SSH. Extracted from app.js Phase 2. */
function loadSetup(){
  document.getElementById('t-setup').innerHTML='<p style="color:#888">Loading fixtures...</p>';
  _renderSetup();
  // #514 — periodic poll of the mover-control claim status so the Setup
  // hardware table shows a green "ACTIVE" badge on every gyro puck that
  // currently has an active mover claim. The span is stamped async so
  // initial render stays fast.
  if(!window._gyroLockPollTimer){
    window._gyroLockPollTimer=setInterval(_gyroLockBadgeRefresh,1500);
    _gyroLockBadgeRefresh();
  }
  // #771 — UDP listener health banner. Polled at 4 s so a successful
  // restart-udp-listener click clears the banner promptly. Lazy-create
  // a top-of-tab container the first time we render.
  if(!window._udpHealthPollTimer){
    window._udpHealthPollTimer=setInterval(_udpHealthRefresh, 4000);
    _udpHealthRefresh();
  }
}

// #771 — render or hide the UDP listener offline banner on Setup tab.
function _udpHealthRefresh(){
  var tab=document.getElementById('t-setup');
  if(!tab||tab.style.display==='none')return;
  ra('GET','/api/status',null,function(r){
    var u=r&&r.udpListener||null;
    var bar=document.getElementById('udp-listener-banner');
    if(!u||u.ok){
      if(bar)bar.remove();
      return;
    }
    if(!bar){
      bar=document.createElement('div');
      bar.id='udp-listener-banner';
      bar.style.cssText='background:#7f1d1d;color:#fef2f2;border:1px solid #ef4444;border-radius:6px;padding:.7em 1em;margin-bottom:.8em;font-size:.85em;line-height:1.5;display:flex;align-items:flex-start;gap:.7em;box-shadow:0 0 12px rgba(239,68,68,.2)';
      tab.insertBefore(bar, tab.firstChild);
    }
    var port=u.port||'?';
    var err=u.lastError||(L&&L.udpListenerNoBind)||'bind failed';
    var title=(L&&L.udpListenerOffline)||'UDP listener offline';
    var hint=(L&&L.udpListenerHint)||'Discover and PONG flows will not work until UDP '+port+' is free. On Windows, Stop-Service winnat -Force usually frees an HNS-held port; then click Retry.';
    var btn=(L&&L.udpListenerRetry)||'Retry bind';
    bar.innerHTML='<div style="font-size:1.4em;line-height:1">⚠</div>'
      +'<div style="flex:1">'
      +'<div style="font-weight:600;margin-bottom:.2em">'+escapeHtml(title)+' (UDP '+escapeHtml(port)+')</div>'
      +'<div style="opacity:.85;font-size:.92em">'+escapeHtml(err)+'</div>'
      +'<div style="opacity:.7;font-size:.85em;margin-top:.4em">'+escapeHtml(hint)+'</div>'
      +'</div>'
      +'<button class="btn" onclick="_udpListenerRetry()" style="background:#fef2f2;color:#7f1d1d;font-size:.78em;flex-shrink:0">'+escapeHtml(btn)+'</button>';
  });
}

function _udpListenerRetry(){
  ra('POST','/api/diagnostics/restart-udp-listener',null,function(r){
    if(r&&r.ok){
      if(typeof toastSuccess==='function')toastSuccess((L&&L.udpListenerRestored)||'UDP listener bound — discover should work now');
    }else if(typeof toastWarn==='function'){
      var err=(r&&r.udpListener&&r.udpListener.lastError)||'';
      toastWarn(((L&&L.udpListenerStillFailing)||'UDP listener still cannot bind')+(err?': '+err:''));
    }
    _udpHealthRefresh();
  });
}

function _gyroLockBadgeRefresh(){
  if(document.getElementById('t-setup').style.display==='none')return;
  // Map device IP → child id so we can resolve claim.deviceId="gyro-<ip>".
  var ipToChild={};
  (_setupChildren||[]).forEach(function(c){
    if(c.type==='gyro'&&c.ip)ipToChild[c.ip]=c.id;
  });
  ra('GET','/api/mover-control/status',null,function(r){
    var active={};
    ((r&&r.claims)||[]).forEach(function(cl){
      var did=cl.deviceId||'';
      if(!did.startsWith('gyro-'))return;
      var ip=did.slice(5);
      var cid=ipToChild[ip];
      if(cid!=null)active[cid]=cl;
    });
    (_setupChildren||[]).forEach(function(c){
      if(c.type!=='gyro')return;
      var span=document.getElementById('gyro-lock-'+c.id);
      if(!span)return;
      var cl=active[c.id];
      if(cl&&cl.state==='streaming'){
        span.innerHTML=' <span class="badge" style="background:#065f46;color:#bbf7d0" title="Streaming orientation to mover '+cl.moverId+'">ACTIVE</span>';
      }else if(cl){
        span.innerHTML=' <span class="badge" style="background:#1e3a5f;color:#93c5fd" title="Claimed, awaiting Start">ARMED</span>';
      }else{
        span.innerHTML='';
      }
    });
  });
}

var _setupChildren=[];
var TRACK_CLASSES=[
  {id:"person",label:"Person"},{id:"cat",label:"Cat"},{id:"dog",label:"Dog"},
  {id:"horse",label:"Horse"},{id:"chair",label:"Chair"},{id:"backpack",label:"Backpack"},
  {id:"suitcase",label:"Suitcase"},{id:"sports ball",label:"Sports Ball"},
  {id:"bottle",label:"Bottle"},{id:"cup",label:"Cup"},{id:"umbrella",label:"Umbrella"},
  {id:"teddy bear",label:"Teddy Bear"},{id:"bicycle",label:"Bicycle"},
  {id:"skateboard",label:"Skateboard"},{id:"car",label:"Car"},{id:"truck",label:"Truck"}
];
function _renderSetup(){
  // Fetch both fixtures and children so we can resolve LED fixture status.
  // Pull from /api/layout (not /api/fixtures) — layout returns each
  // fixture record with x/y/z merged in from _layout.children, which is
  // what the edit modal needs to pre-populate position inputs. Using
  // /api/fixtures drops those fields and the modal ends up showing
  // (0, 0, 0).
  ra('GET','/api/layout',null,function(layout){
    var fixtures=(layout&&layout.fixtures)||[];
    _fixtures=fixtures;
    // #503 — fire a fit-quality fetch for each calibrated mover so the
    // next repaint can render the RMS badge. Cached on the fixture
    // record itself (`_mcalFit`) so subsequent renders are free.
    (_fixtures||[]).forEach(function(f){
      if(f.fixtureType==='dmx'&&f.moverCalibrated&&f._mcalFit===undefined){
        f._mcalFit=null;  // mark as "fetched"
        ra('GET','/api/calibration/mover/'+f.id,null,function(c){
          if(c&&c.fit){f._mcalFit=c.fit;}
        });
      }
    });
    ra('GET','/api/children',null,function(children){
      _setupChildren=children||[];
      var cMap={};(children||[]).forEach(function(c){cMap[c.id]=c;});
      // Separate children with fixtures from standalone hardware (DMX bridges, unlinked)
      var linkedChildIds=new Set();(fixtures||[]).forEach(function(f){
        if(f.childId!=null)linkedChildIds.add(f.childId);
        if(f.gyroChildId!=null)linkedChildIds.add(f.gyroChildId);
      });
      // Gyro children get their own dedicated row later — never treat them
      // as generic "standalone hardware" even if their fixture isn't yet
      // linked, otherwise they render twice.
      var standaloneHw=(children||[]).filter(function(c){
        return !linkedChildIds.has(c.id) && c.type!=='gyro';
      });

      var h='<div style="margin-bottom:.6em">'
        +'<button class="btn btn-on" onclick="showAddFixtureModal()" data-tip="setupAdd">+ Add</button>'
        +'<button class="btn btn-nav" onclick="discoverChildren()" id="disc-btn" style="margin-left:.5em" data-tip="setupDiscover">Discover</button>'
        +'<button class="btn btn-nav" onclick="setupRefreshAll(this)" style="margin-left:.5em" data-tip="setupRefreshAll">Refresh All</button>'
        +'</div>'
        +'<div id="disc-results" style="display:none;margin-bottom:.8em"></div>';

      // Pre-filter fixture types (needed by hardware + fixture sections)
      var ledFixtures=(fixtures||[]).filter(function(f){return (f.fixtureType||'led')==='led';});
      var dmxFixtures=(fixtures||[]).filter(function(f){return f.fixtureType==='dmx';});
      var camFixtures=(fixtures||[]).filter(function(f){return f.fixtureType==='camera';});
      var gyroFixtures=(fixtures||[]).filter(function(f){return f.fixtureType==='gyro';});

      // ── Hardware section (DMX bridges + standalone children + camera nodes)
      // Group camera fixtures by IP to show each Orange Pi as a hardware node
      var camNodeMap={};
      camFixtures.forEach(function(f){
        var ip=f.cameraIp||'';
        if(!camNodeMap[ip])camNodeMap[ip]={ip:ip,fixtures:[],name:''};
        camNodeMap[ip].fixtures.push(f);
        // Use IP as placeholder name — real hostname comes from async probe
        if(!camNodeMap[ip].name)camNodeMap[ip].name=ip||'Camera Node';
      });
      var camNodes=Object.values(camNodeMap);
      var gyroChildren=(children||[]).filter(function(c){return c.type==='gyro';});
      var hasHw=standaloneHw.length||camNodes.length||gyroChildren.length;
      if(hasHw){
        h+='<h3 style="font-size:.9em;color:#94a3b8;margin:.8em 0 .3em">Hardware</h3>'
          +'<table class="tbl"><tr><th>Device</th><th>Type</th><th>IP</th><th>Status</th><th>Firmware</th><th>Actions</th></tr>';
        standaloneHw.forEach(function(c){
          var isDmx=(c.type==='dmx'||c.boardType==='giga-dmx'||c.boardType==='DMX Bridge');
          var typeBadge=isDmx
            ?'<span class="badge" style="background:#7c3aed;color:#fff">DMX Bridge</span>'
            :'<span class="badge" style="background:#446;color:#fff">'+(c.boardType||'SlyLED')+'</span>';
          var rssi=c.rssi||0;
          var rssiHtml='';
          if(c.status===1&&rssi){
            var rssiCol=Math.abs(rssi)<=50?'#4c4':Math.abs(rssi)<=70?'#fa6':'#f66';
            rssiHtml=' <span style="color:'+rssiCol+';font-size:.75em" title="'+rssi+' dBm">'+_rssiIcon(rssi)+'</span>';
          }
          var st=c.status===1?'<span class="badge bon">Online</span>'+rssiHtml:'<span class="badge boff">Offline</span>';
          var fwVer=c.fwVersion||'—';
          var fwHtml=escapeHtml(fwVer)+'<span id="fw-ind-'+c.id+'"></span>';
          var acts='<button class="btn btn-on" onclick="refreshChild('+c.id+')">Refresh</button>';
          // #291 — when offline, expose a Find button that broadcasts a
          // hostname-targeted PING and updates the IP if the device is
          // still on the network at a new DHCP lease.
          if(c.status!==1)acts+=' <button class="btn" onclick="findChild('+c.id+',this)" style="background:#1e3a5f;color:#93c5fd">Find</button>';
          if(isDmx)acts+=' <button class="btn" onclick="window.open(\'http://'+escapeHtml(c.ip)+'/config\',\'_blank\')" style="background:#446;color:#fff">Configure</button>';
          acts+=' <button class="btn" onclick="rebootChild('+c.id+')" style="background:#654;color:#fff">Reboot</button>';
          acts+=' <button class="btn btn-off" onclick="removeChildDevice('+c.id+')">Remove</button>';
          h+='<tr><td><b>'+escapeHtml(c.name||c.hostname)+'</b></td><td>'+typeBadge+'</td><td>'+escapeHtml(c.ip)+'</td><td>'+st+'</td><td>'+fwHtml+'</td><td>'+acts+'</td></tr>';
        });
        // Gyro boards as hardware rows (#462-465: gyro devices with inline Configure panel)
        gyroChildren.forEach(function(c){
          var typeBadge='<span class="badge" style="background:#6d28d9;color:#fff">Gyro</span>';
          var rssi=c.rssi||0;
          var rssiHtml='';
          if(c.status===1&&rssi){var rssiCol=Math.abs(rssi)<=50?'#4c4':Math.abs(rssi)<=70?'#fa6':'#f66';rssiHtml=' <span style="color:'+rssiCol+';font-size:.75em" title="'+rssi+' dBm">'+_rssiIcon(rssi)+'</span>';}
          // #514 — inline lock-active indicator, populated async below.
          var lockHtml=' <span id="gyro-lock-'+c.id+'"></span>';
          var st=c.status===1?'<span class="badge bon">Online</span>'+rssiHtml+lockHtml:'<span class="badge boff">Offline</span>';
          var fwVer=c.fwVersion||'—';
          var fwHtml=escapeHtml(fwVer)+'<span id="fw-ind-'+c.id+'"></span>';
          // Name: show altName as primary if set, hostname as secondary (#464)
          var primaryName=c.altName||c.name||c.hostname||'Gyro';
          var secondaryName=(c.altName&&c.hostname&&c.altName!==c.hostname)?'<br><span style="color:#64748b;font-size:.75em">'+escapeHtml(c.hostname)+'</span>':'';
          var acts='<button class="btn btn-on" onclick="refreshChild('+c.id+')">Refresh</button>';
          if(c.status!==1)acts+=' <button class="btn" onclick="findChild('+c.id+',this)" style="background:#1e3a5f;color:#93c5fd">Find</button>';
          acts+=' <button class="btn" onclick="rebootChild('+c.id+')" style="background:#654;color:#fff">Reboot</button>'
            +' <button class="btn btn-off" onclick="removeChildDevice('+c.id+')">Remove</button>';
          // Configure button opens modal (#465)
          acts+=' <button class="btn" onclick="_gyroConfigModal('+c.id+')" style="background:#312e81;color:#a5b4fc;font-size:.8em">Configure</button>';
          h+='<tr><td><b>'+escapeHtml(primaryName)+'</b>'+secondaryName+'</td><td>'+typeBadge+'</td><td>'+escapeHtml(c.ip)+'</td><td>'+st+'</td><td>'+fwHtml+'</td><td>'+acts+'</td></tr>';
        });
        // Camera nodes as hardware rows
        camNodes.forEach(function(node){
          var typeBadge='<span class="badge" style="background:#0e7490;color:#fff">Camera Node</span>';
          var fids=node.fixtures.map(function(f){return f.id;});
          var acts='<button class="btn" onclick="window.open(\'http://'+escapeHtml(node.ip)+':5000/config\',\'_blank\')" style="background:#446;color:#fff">Configure</button>'
            +' <button class="btn btn-off" onclick="_removeCameraNode('+JSON.stringify(fids)+',\''+escapeHtml(node.name).replace(/'/g,"\\'")+'\')">Remove</button>';
          var ipKey=escapeHtml(node.ip).replace(/\./g,'-');
          h+='<tr><td><span id="cam-hw-name-'+ipKey+'"><b>'+escapeHtml(node.name)+'</b></span> <span style="color:#64748b;font-size:.75em">('+node.fixtures.length+' sensor'+(node.fixtures.length>1?'s':'')+')</span></td><td>'+typeBadge+'</td><td>'+escapeHtml(node.ip)+'</td><td id="cam-hw-st-'+ipKey+'"><span class="badge" style="background:#334;color:#888">Checking...</span></td><td id="cam-hw-fw-'+ipKey+'">—</td><td>'+acts+'</td></tr>';
        });
        h+='</table>';
      }

      // ── Fixtures section (LED + DMX fixtures)
      h+='<h3 style="font-size:.9em;color:#94a3b8;margin:.8em 0 .3em">Fixtures</h3>'
        +'<table class="tbl"><tr><th>Fixture</th><th>Type</th><th>Connection</th><th>Status</th><th>Channels / LEDs</th><th>Actions</th></tr>';
      // Async: fetch camera live status (don't block render)
      if(camFixtures.length){
        // #687 follow-up — pull the registry's camera-node version once
        // per setup refresh so the upgrade indicator can compare each
        // camera's fwVersion against the published firmware, not the
        // max-of-online-cameras (which is broken when the whole fleet
        // is uniformly stale).
        if(!window._camFirmwareLatest){
          ra('GET','/api/firmware/library',null,function(lib){
            try{
              var entry=((lib&&lib.firmware)||[]).find(function(e){return e.id==='camera-node';});
              if(entry&&entry.version)window._camFirmwareLatest=entry.version;
            }catch(e){}
          });
        }
        ra('GET','/api/cameras',null,function(cams){
        if(!cams||!cams.length)return;
        // Update per-sensor status + sync tracking state from server
        cams.forEach(function(c){
          var cell=document.getElementById('cam-st-'+c.id);
          if(cell)cell.innerHTML=c.online?'<span class="badge bon">Online</span>':'<span class="badge boff">Offline</span>';
          // Sync tracking state from server (reflects state set by Android or other clients)
          if(c.tracking)_trackingCams[c.id]=true;
          else delete _trackingCams[c.id];
          _trackBtnSync(c.id);
        });
        // Find latest camera firmware version. Old impl compared each
        // camera's fwVersion against the max across the OTHER online
        // cameras — a fleet running uniformly old firmware would never
        // flag an update. Now: prefer the registry's published version
        // (read once and cached on _camFirmwareLatest) and fall back to
        // max-of-online when the registry isn't available.
        var latestCamVer = (window._camFirmwareLatest||'0.0.0');
        cams.forEach(function(c){if(c.online&&c.fwVersion&&_cmpVer(c.fwVersion,latestCamVer)>0)latestCamVer=c.fwVersion;});
        // Update hardware node status (grouped by IP)
        var seen={};
        cams.forEach(function(c){
          var ip=c.cameraIp||'';if(!ip||seen[ip])return;seen[ip]=true;
          var key=ip.replace(/\./g,'-');
          var stCell=document.getElementById('cam-hw-st-'+key);
          var fwCell=document.getElementById('cam-hw-fw-'+key);
          var nameCell=document.getElementById('cam-hw-name-'+key);
          if(stCell){
            var stHtml=c.online?'<span class="badge bon">Online</span>':'<span class="badge boff">Offline</span>';
            if(c.online&&c.rssi){var rc=Math.abs(c.rssi)<=50?'#4c4':Math.abs(c.rssi)<=70?'#fa6':'#f66';stHtml+=' <span style="color:'+rc+';font-size:.75em" title="'+c.rssi+' dBm">'+_rssiIcon(c.rssi)+'</span>';}
            stCell.innerHTML=stHtml;
          }
          if(fwCell){
            var fw=c.fwVersion||'—';
            fwCell.innerHTML=escapeHtml(fw);
            // Show upgrade indicator if camera firmware is behind the latest
            if(c.online&&fw!=='—'&&_cmpVer(fw,latestCamVer)<0){
              fwCell.innerHTML+=' <span onclick="showTab(\'firmware\')" style="color:#f60;cursor:pointer;font-size:.9em" title="Update available: v'+latestCamVer+'">&#9650;</span>';
            }
          }
          // Update hardware name from probe hostname (node name setting)
          if(nameCell&&c.hostname&&c.hostname!==ip){
            nameCell.innerHTML='<b>'+escapeHtml(c.hostname)+'</b>';
          }
        });
      });}
      if(ledFixtures.length){
        ledFixtures.forEach(function(f){
          var ch=f.childId!=null?cMap[f.childId]:null;
          var conn='',status='',chLeds='',actions='';
          if(ch){
            var board=ch.boardType||'SlyLED';
            if(ch.type==='wled')board='WLED';
            else if(!ch.boardType){
              if(ch.sc<=1&&ch.strings&&ch.strings.length&&ch.strings[0].leds<=1)board='Giga';
              else board='ESP32';
            }
            var boardColors={'ESP32':'#2563eb','D1 Mini':'#7c3aed','Giga':'#059669','WLED':'#f59e0b'};
            conn=escapeHtml(ch.ip)+' <span class="badge" style="background:'+(boardColors[board]||'#446')+';color:#fff;font-size:.75em">'+board+'</span>';
            var rssi=ch.rssi||0;
            var rssiHtml='';
            if(ch.status===1&&rssi){
              var rssiCol=Math.abs(rssi)<=50?'#4c4':Math.abs(rssi)<=70?'#fa6':'#f66';
              rssiHtml=' <span style="color:'+rssiCol+';font-size:.75em" title="'+rssi+' dBm">'+_rssiIcon(rssi)+'</span>';
            }
            status=ch.status===1?'<span class="badge bon">Online</span>'+rssiHtml:'<span class="badge boff">Offline</span>';
            var totalLeds=0;if(ch.strings)ch.strings.forEach(function(s,i){if(i<ch.sc)totalLeds+=(s.leds||0);});
            chLeds=ch.sc+'&times;'+totalLeds;
          }else{
            conn='<span style="color:#666">No device linked</span>';
            status='<span class="badge boff">Unlinked</span>';
            chLeds='—';
          }
          actions='<button class="btn" onclick="editFixture('+f.id+')" style="background:#446;color:#fff">Edit</button>';
          if(ch)actions+=' <button class="btn" onclick="showDetails('+f.childId+')" style="background:#335;color:#fff">Test</button>'
            +' <button class="btn btn-on" onclick="refreshChild('+f.childId+')">Refresh</button>'
            +' <button class="btn" onclick="rebootChild('+f.childId+')" style="background:#654;color:#fff">Reboot</button>';
          actions+=' <button class="btn btn-off" onclick="removeFixture('+f.id+',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')">Remove</button>';
          h+='<tr><td><b>'+escapeHtml(f.name)+'</b></td><td><span class="badge" style="background:#059669;color:#fff">LED</span></td><td>'+conn+'</td><td>'+status+'</td><td>'+chLeds+'</td><td>'+actions+'</td></tr>';
        });
      }
      if(dmxFixtures.length){
        dmxFixtures.forEach(function(f){
          var conn='U'+f.dmxUniverse+' @ '+(f.dmxStartAddr||1);
          var chLeds=(f.dmxChannelCount||0)+' ch'+(f.dmxProfileId?' ('+f.dmxProfileId+')':'');
          // #503 — fit-quality badge. `f._mcalFit` is populated
          // opportunistically below by a one-shot fetch; we optimistically
          // render the stored value on each repaint.
          var calBadge='';
          if(f.moverCalibrated){
            var fit=f._mcalFit;
            if(fit&&fit.rmsErrorDeg!=null){
              var rms=fit.rmsErrorDeg;
              var bg='#065f46', fg='#bbf7d0';
              if(rms>1.5){bg='#92400e';fg='#fde68a';}
              if(rms>3.0){bg='#991b1b';fg='#fecaca';}
              calBadge=' <span title="RMS '+rms.toFixed(2)+'° / max '+fit.maxErrorDeg.toFixed(2)+'°" '
                      +'style="background:'+bg+';color:'+fg+';padding:1px 5px;border-radius:3px;font-size:.72em;margin-left:.3em;font-weight:bold">'
                      +rms.toFixed(1)+'\u00b0</span>';
            }else{
              calBadge=' <span title="calibrated" style="color:#4ade80">\u2713</span>';
            }
          }
          var actions='<button class="btn" onclick="editFixture('+f.id+')" style="background:#446;color:#fff">Edit</button>'
            +' <button class="btn" onclick="showDmxDetails('+f.id+')" style="background:#3b1f7c;color:#e9d5ff">Test</button>'
            +' <button class="btn" onclick="_moverCalStart('+f.id+')" style="background:#6b21a8;color:#d8b4fe">Calibrate'+calBadge+'</button>'
            +' <button class="btn btn-off" onclick="removeFixture('+f.id+',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')">Remove</button>';
          h+='<tr><td><b>'+escapeHtml(f.name)+'</b></td><td><span class="badge" style="background:#7c3aed;color:#fff">DMX</span></td><td>'+conn+'</td><td><span class="badge" style="background:#7c3aed;color:#fff">Configured</span></td><td>'+chLeds+'</td><td>'+actions+'</td></tr>';
        });
      }
      if(!ledFixtures.length&&!dmxFixtures.length&&!camFixtures.length){
        h+='<tr><td colspan="6" style="color:#888;text-align:center">No fixtures — click Add Fixture or Discover</td></tr>';
      }
      h+='</table>';

      // ── Cameras section — per-sensor listing grouped by node
      h+='<h3 style="font-size:.9em;color:#94a3b8;margin:.8em 0 .3em">Camera Sensors '
        +'<button class="btn" onclick="discoverCameras(this)" id="cam-disc-btn" style="font-size:.75em;padding:.15em .5em;margin-left:.5em;background:#0e7490;color:#fff">Discover</button>'
        +' <button class="btn btn-on" onclick="loadSetup()" style="font-size:.75em;padding:.15em .5em">Refresh</button>'
        +'</h3>';
      h+='<div id="cam-disc-results" style="display:none;margin-bottom:.5em"></div>';
      // Point cloud status banner (#578) — system-wide cloud built from
      // all positioned cameras. Populated below via loadPointCloudMeta().
      h+='<div id="pc-status" style="margin-bottom:.5em;padding:.4em .6em;background:#0f172a;border:1px solid #1e293b;border-radius:4px;font-size:.82em;color:#64748b">Loading point cloud status…</div>';
      if(camFixtures.length){
        h+='<table class="tbl cam-status-tbl"><tr><th>#</th><th>Sensor Name</th><th>Node</th><th>FOV</th><th>Resolution</th><th>Point cloud</th><th>Status</th><th>Actions</th></tr>';
        camFixtures.forEach(function(f){
          var ip=f.cameraIp||'—';
          var camIdx=f.cameraIdx||0;
          var fov=(f.fovDeg||60)+'\u00b0';
          var res=(f.resolutionW||'—')+'x'+(f.resolutionH||'—');
          var calBadge=f.calibrated?'<span class="badge" style="background:#065f46;color:#34d399;margin-left:4px">\u2713 Cal</span>':'';
          var acts='<button class="btn" onclick="editFixture('+f.id+')" style="background:#446;color:#fff">Edit</button>';
          if(ip!=='—'){
            acts+=' <button class="btn" onclick="_camSnap('+f.id+')" style="background:#059669;color:#fff">Snap</button>';
            // #623 — per-camera settings + auto-tune wizard.
            acts+=' <button class="btn" onclick="_camTune('+f.id+')" style="background:#1e40af;color:#dbeafe" title="Adjust V4L2 controls; auto-tune for beam / ArUco / YOLO intents">Tune</button>';
            var trkActive=_trackingCams[f.id];
            acts+=' <button class="btn" id="setup-trk-'+f.id+'" onclick="_setupTrackToggle('+f.id+')" style="background:'+(trkActive?'#9f1239':'#be185d')+';color:#fce7f3">'+(trkActive?'Stop Track':'Track')+'</button>';
          }
          acts+=' <button class="btn btn-off" onclick="removeCamera('+f.id+',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')">Remove</button>';
          h+='<tr><td style="color:#64748b">cam'+camIdx+'</td><td><b>'+escapeHtml(f.name)+'</b>'+calBadge+'</td><td>'+escapeHtml(ip)+'</td><td>'+fov+'</td><td>'+res+'</td>'
            +'<td id="cam-pc-'+f.id+'"><span class="badge" style="background:#334;color:#888;font-size:.7em">—</span></td>'
            +'<td id="cam-st-'+f.id+'"><span class="badge" style="background:#334;color:#888">...</span></td>'
            +'<td>'+acts+'</td></tr>';
          h+='<tr id="cam-snap-row-'+f.id+'" style="display:none"><td colspan="8" style="padding:.3em"><div id="cam-snap-'+f.id+'" style="text-align:center"></div></td></tr>';
        });
        h+='</table>';
      }else{
        h+='<p style="color:#555;font-size:.82em">No cameras registered. Click Discover or add via Add Fixture.</p>';
      }

      // ── ArUco marker registry (#596) ───────────────────────────
      // Surveyed tags in stage space. Both this section and the
      // Advanced Scan card render the same editor (_arucoRenderTable).
      h+='<h3 style="font-size:.9em;color:#94a3b8;margin:.8em 0 .3em">ArUco Markers'
        +' <span style="color:#64748b;font-size:.78em">· surveyed ground-truth tags in stage space</span>'
        +'</h3>';
      h+='<div id="aruco-setup-host"><p style="color:#555;font-size:.82em">Loading markers…</p></div>';

      document.getElementById('t-setup').innerHTML=h;
      _arucoLoad(function(){_arucoRenderTable('aruco-setup-host', {source:'setup'});});
      // Check firmware updates — add ▲ triangle indicator to outdated devices
      api('GET','/api/firmware/check').then(function(chk){
        if(!chk||!chk.children)return;
        chk.children.forEach(function(u){
          if(!u.needsUpdate||u.board==='wled'||u.board==='gyro'||u.type==='gyro')return;
          var el=document.getElementById('fw-ind-'+u.id);
          if(el)el.innerHTML=' <span onclick="showTab(\'firmware\')" style="color:#f60;cursor:pointer;font-size:.9em" title="Update available: v'+escapeHtml(u.latestVersion)+' (click to update)">&#9650;</span>';
        });
      }).catch(function(){});
      // Point cloud status banner + per-camera pills (#578)
      _loadPointCloudMeta(camFixtures);
    });
  });
}

function _loadPointCloudMeta(camFixtures){
  var banner=document.getElementById('pc-status');
  ra('GET','/api/space?meta=1',null,function(r){
    var hasCloud=r&&r.ok&&r.totalPoints>0;
    var isLite=hasCloud&&r.source==='lite';
    var contribIds={};
    if(hasCloud)(r.cameras||[]).forEach(function(c){contribIds[c.fixtureId]=true;});
    // Banner
    if(banner){
      var bh='';
      if(!hasCloud){
        bh='<b style="color:#94a3b8">Point cloud:</b> <span style="color:#f59e0b">none</span> '
          +'<span style="color:#64748b">— calibration will have no stage geometry.</span> '
          +'<button class="btn" onclick="_pcLiteRun()" style="font-size:.75em;margin-left:.4em;background:#0e7490;color:#fff">Quick Lite Setup</button>'
          +' <button class="btn" onclick="_pcAdvancedScan()" style="font-size:.75em;margin-left:.3em;background:#1e3a5f;color:#93c5fd">Advanced Scan\u2026</button>';
      }else if(isLite){
        bh='<b style="color:#94a3b8">Point cloud:</b> <span style="color:#f59e0b">Lite only</span> '
          +'<span style="color:#64748b">— synthesized from layout dimensions, no camera depth. Upgrade once cameras can scan.</span> '
          +'<button class="btn" onclick="_pcAdvancedScan()" style="font-size:.75em;margin-left:.4em;background:#0e7490;color:#fff">Advanced Scan\u2026</button>';
      }else{
        var ts=r.timestamp?new Date(r.timestamp*1000).toLocaleString():'unknown';
        bh='<b style="color:#94a3b8">Point cloud:</b> <span style="color:#34d399">'+r.totalPoints+' points</span> '
          +'<span style="color:#64748b">· '+((r.cameras||[]).length)+' cameras · '+escapeHtml(ts)+'</span> '
          +'<button class="btn" onclick="_pcAdvancedScan()" style="font-size:.75em;margin-left:.4em;background:#1e3a5f;color:#93c5fd">Advanced Scan\u2026</button>';
      }
      banner.innerHTML=bh;
    }
    // Per-camera pills
    (camFixtures||[]).forEach(function(f){
      var el=document.getElementById('cam-pc-'+f.id);
      if(!el)return;
      var pill;
      if(!hasCloud){
        pill='<span class="badge" title="No point cloud exists yet. Run Quick Lite Setup or Full Scan to give calibration a geometry reference." style="background:#334;color:#94a3b8;font-size:.7em">No cloud</span>';
      }else if(isLite){
        pill='<span class="badge" title="The current cloud is synthesized from layout dimensions, not real depth. Run a Full Scan once the camera is usable for depth." style="background:#78350f;color:#fcd34d;font-size:.7em">Lite</span>';
      }else if(contribIds[f.id]){
        pill='<span class="badge" title="This camera contributed depth points to the current scan." style="background:#065f46;color:#34d399;font-size:.7em">\u2713 In cloud</span>';
      }else{
        pill='<span class="badge" title="A real scan exists but this camera is not in it. Reason: camera was added after the scan, was not positioned on the layout at scan time, or returned no depth points. Click Rescan to include it." style="background:#7c2d12;color:#fed7aa;font-size:.7em">Not in cloud</span>';
      }
      el.innerHTML=pill;
    });
  });
}

function _pcLiteRun(){
  var banner=document.getElementById('pc-status');
  if(banner)banner.innerHTML='<span style="color:#64748b">Building lite cloud…</span>';
  ra('POST','/api/space/scan/lite',{},function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Lite point cloud created — '+r.totalPoints+' points';
      loadSetup();
    }else{
      document.getElementById('hs').textContent='Lite cloud failed: '+(r&&r.err||'unknown');
      loadSetup();
    }
  });
}

function _pcFullScan(){
  // Legacy one-click scan — kept for callers that didn't migrate to
  // _pcAdvancedScan. Runs monocular with default options.
  var banner=document.getElementById('pc-status');
  if(banner)banner.innerHTML='<span style="color:#64748b">Starting environment scan…</span>';
  ra('POST','/api/space/scan',{maxPointsPerCamera:5000},function(r){
    if(!r||!r.ok){
      document.getElementById('hs').textContent='Scan failed: '+(r&&r.err||'unknown');
      loadSetup();return;
    }
    document.getElementById('hs').textContent='Scan running — '+(r.cameras||0)+' cameras';
    var poll=setInterval(function(){
      ra('GET','/api/space/scan/status',null,function(st){
        if(!st)return;
        if(banner)banner.innerHTML='<span style="color:#64748b">Scanning… '+(st.progress||0)+'% · '+escapeHtml(st.message||'')+'</span>';
        if(!st.running){
          clearInterval(poll);
          document.getElementById('hs').textContent='Scan complete — '+(st.totalPoints||0)+' points';
          loadSetup();
        }
      });
    },800);
  });
}

// ── Advanced Scan modal (#588) ──────────────────────────────────────────
// Method picker, per-camera toggle, lighting, live progress, sample frames.

function _pcAdvancedScan(){
  _modalStack=[];
  _pcAdvZoeProbed=false;  // #594 — re-probe each time the modal opens
  var cams=(_fixtures||[]).filter(function(f){return f.fixtureType==='camera';});
  // Detect stereo-capable pair (same cameraIp, at least two entries)
  var byIp={};
  cams.forEach(function(c){
    if(!c.cameraIp)return;
    (byIp[c.cameraIp]=byIp[c.cameraIp]||[]).push(c);
  });
  var stereoPairs=[];
  Object.keys(byIp).forEach(function(ip){
    if(byIp[ip].length>=2)stereoPairs.push({ip:ip,cams:byIp[ip].slice(0,2)});
  });

  // #594 — highlight the recommended method for mover calibration. The
  // scan cloud feeds the mover calibration wizard's stage-geometry model,
  // so *coverage + accuracy* directly drives beam-aim quality.
  //
  // Empirically measured 2026-04-21 on the basement rig (2 ArUco-cal
  // cameras, textureless walls):
  //   ZoeDepth (host):  4976 pts / 24s   metric, dense coverage
  //   Mono DA-V2 Metric: 2622 pts / 22s   metric, ok coverage
  //   Stereo ORB:          52 pts /  2s   ArUco-cal but ORB fails on
  //                                       textureless scenes
  //
  // So even with ArUco-calibrated pairs, stereo loses ~100x in yield on
  // typical indoor stage geometry. ZoeDepth (when available on host)
  // wins on coverage and metric accuracy. Stereo stays as an advanced
  // option for scenes with rich texture but isn't the default pick.
  //
  // Recommendation order (resolved at render time + refreshed async):
  //   ZoeDepth (if host has torch+transformers) > Pi-side DA-V2 Metric > Stereo > Lite
  var stereoCalibrated=stereoPairs.length>0&&stereoPairs[0].cams.every(function(c){
    var cam=cams.filter(function(x){return x.id===c.id;})[0];
    return cam&&cam.calibrated;
  });
  // Start with mono as the deterministic baseline; the async ZoeDepth
  // probe in _pcAdvRefresh promotes ZoeDepth to Recommended when available.
  var recommend='mono';

  var h='<div style="min-width:520px">';
  h+='<div style="font-size:.85em;color:#94a3b8;margin-bottom:.4em">Build a stage-space point cloud. Pick a method, choose which cameras to include, and optionally black out DMX during capture so bright beams don\'t confuse the depth model.</div>';
  h+='<div style="font-size:.78em;color:#fcd34d;background:#1e293b;border-left:3px solid #f59e0b;padding:.35em .5em;margin-bottom:.6em;border-radius:3px">'
    +'<b>For moving-head calibration:</b> the scan becomes the stage surface the mover-cal wizard aims against. A more accurate cloud = more accurate beam targeting. The <b>Recommended</b> option below gives the best geometry your hardware allows.'
    +'</div>';

  function _recBadge(){
    return ' <span class="pcadv-rec-badge" style="background:#065f46;color:#34d399;padding:1px 6px;border-radius:8px;font-size:.7em;margin-left:.3em">\u2605 Recommended</span>';
  }

  // Method picker
  h+='<div class="card" style="padding:.6em;margin-bottom:.5em">';
  h+='<div style="font-size:.82em;font-weight:bold;color:#e2e8f0;margin-bottom:.3em">Method</div>';
  h+='<label id="pcadv-opt-mono" style="display:block;cursor:pointer;padding:.2em 0;font-size:.85em">'
    +'<input type="radio" name="pcmethod" value="mono"'+(recommend==='mono'?' checked':'')+' onchange="_pcAdvRefresh()"> Monocular (Pi-side) — DA-V2 Metric Small, fast.'
    +(recommend==='mono'?_recBadge():'')
    +'</label>';
  h+='<label id="pcadv-opt-zoe" style="display:block;cursor:pointer;padding:.2em 0;font-size:.85em">'
    +'<input type="radio" name="pcmethod" value="zoedepth" onchange="_pcAdvRefresh()"> <b>ZoeDepth (host)</b> — higher quality, slower.'
    +' <span class="pcadv-zoe-note" style="color:#64748b">Checking host availability…</span>'
    +'</label>';
  var stereoDisabled=stereoPairs.length===0;
  h+='<label id="pcadv-opt-stereo" style="display:block;cursor:pointer;padding:.2em 0;font-size:.85em'+(stereoDisabled?';opacity:.4':'')+'">'
    +'<input type="radio" name="pcmethod" value="stereo"'+(stereoDisabled?' disabled':'')+' onchange="_pcAdvRefresh()"> Stereo triangulation'
    +(stereoDisabled?' <span style="color:#f59e0b">— needs two cameras on the same node</span>'
                    :(stereoCalibrated?' <span style="color:#34d399">— ArUco-calibrated pair on '+escapeHtml(stereoPairs[0].ip)+'</span>'
                                      :' <span style="color:#fcd34d">— pair on '+escapeHtml(stereoPairs[0].ip)+' needs ArUco cal for best accuracy</span>'))
    +' <span style="color:#64748b;font-size:.9em">· ORB-based, can yield very few points on textureless scenes</span>'
    +'</label>';
  h+='<label id="pcadv-opt-lite" style="display:block;cursor:pointer;padding:.2em 0;font-size:.85em">'
    +'<input type="radio" name="pcmethod" value="lite" onchange="_pcAdvRefresh()"> Lite — synthesize from layout dimensions (no camera scan).'
    +'</label>';
  h+='</div>';

  // Camera picker
  h+='<div class="card" id="pcadv-cams" style="padding:.6em;margin-bottom:.5em">';
  h+='<div style="font-size:.82em;font-weight:bold;color:#e2e8f0;margin-bottom:.3em">Cameras to include</div>';
  if(cams.length===0){
    h+='<div style="color:#94a3b8;font-size:.8em">No camera fixtures registered.</div>';
  }else{
    cams.forEach(function(c){
      var disabled=!c.cameraIp;
      var calBadge=c.calibrated
        ?'<span title="ArUco-calibrated — stereo will be accurate" style="background:#065f46;color:#34d399;padding:1px 6px;border-radius:8px;font-size:.7em;margin-left:.3em">\u2713 cal</span>'
        :'<span title="No ArUco calibration — stereo accuracy limited by lens distortion" style="background:#78350f;color:#fcd34d;padding:1px 6px;border-radius:8px;font-size:.7em;margin-left:.3em">no cal</span>';
      h+='<div style="display:flex;align-items:center;gap:.5em;padding:.15em 0;font-size:.82em'+(disabled?';opacity:.4':'')+'">'
        +'<label style="flex:1;cursor:pointer">'
        +'<input type="checkbox" class="pcadv-cam" value="'+c.id+'"'+(disabled?' disabled':' checked')+'> '
        +escapeHtml(c.name||('cam '+c.id))+calBadge
        +' <span style="color:#64748b">· '+escapeHtml(c.cameraIp||'no IP')+' · cam'+(c.cameraIdx||0)+' · FOV '+(c.fovDeg||'?')+'°</span>'
        +'</label>'
        +(c.cameraIp?'<button class="btn" onclick="closeModal();_calWizardStart('+c.id+')" style="font-size:.72em;padding:.15em .5em;background:#7c3aed;color:#e9d5ff" title="Run ArUco calibration for this camera">Calibrate\u2026</button>':'')
        +'</div>';
    });
  }
  h+='</div>';

  // ArUco marker registry — collapsible panel (#596). Same editor as
  // the Setup tab; registered markers become ground-truth anchors for
  // stereo/multi-view scans and give the mover-cal wizard absolute
  // stage coordinates to validate against.
  // #592 — inside the same panel we surface a prescan-visibility check
  // and a minimal marker-anchored scan so operators can verify the
  // registry is actually seeing the stage BEFORE committing to a long
  // stereo/ORB run.
  h+='<details class="card" style="padding:.4em .6em;margin-bottom:.5em" id="pcadv-aruco-details" open>'
    +'<summary style="cursor:pointer;font-size:.82em;color:#e2e8f0">'
    +'ArUco markers <span style="color:#64748b;font-weight:normal">— surveyed tags that anchor stereo/multi-view scans</span>'
    +'</summary>'
    +'<div id="aruco-scan-host" style="padding-top:.4em"><p style="color:#555;font-size:.82em">Loading markers…</p></div>'
    +'<div style="padding-top:.5em;margin-top:.4em;border-top:1px solid #1e293b">'
    +  '<div style="display:flex;gap:.4em;flex-wrap:wrap">'
    +    '<button class="btn" onclick="_arucoPrescanVisibility()" style="font-size:.78em;background:#1e3a5f;color:#93c5fd">Prescan visibility</button>'
    +    '<button class="btn" id="aruco-simple-btn" onclick="_arucoScanSimple()" disabled style="font-size:.78em;background:#0e7490;color:#fff" title="Enabled once a prescan finds ≥1 registered marker visible to ≥2 cameras">Scan with visible markers</button>'
    +  '</div>'
    +  '<div id="aruco-prescan-result" style="display:none;margin-top:.4em;padding:.4em;background:#0a0f1a;border:1px solid #1e293b;border-radius:4px;font-size:.8em"></div>'
    +'</div>'
    +'</details>';

  // Lighting
  h+='<div class="card" style="padding:.6em;margin-bottom:.5em">';
  h+='<div style="font-size:.82em;font-weight:bold;color:#e2e8f0;margin-bottom:.3em">DMX lighting during capture</div>';
  h+='<select id="pcadv-light" style="width:100%;font-size:.85em;padding:.3em">';
  h+='<option value="blackout" selected>Blackout (recommended — clean depth input)</option>';
  h+='<option value="fill">Dim fill light (low ambient, no moving beams)</option>';
  h+='<option value="keep">Keep current lighting (don\'t touch DMX)</option>';
  h+='</select>';
  h+='<p style="color:#64748b;font-size:.72em;margin-top:.2em">Bright DMX beams on walls/ceiling saturate the monocular depth model. Blackout restores prior state after capture (#591).</p>';
  h+='</div>';

  // Advanced options
  h+='<details style="margin-bottom:.5em"><summary style="cursor:pointer;color:#94a3b8;font-size:.8em">Advanced options</summary>';
  h+='<div style="padding:.4em 0;font-size:.82em">';
  h+='<label>Max points per camera <input id="pcadv-maxpts" type="number" value="5000" min="500" max="30000" step="500" style="width:80px;margin-left:.4em"></label><br>';
  h+='<label style="margin-top:.3em;display:block">Stereo resolution <select id="pcadv-stereores" style="margin-left:.4em"><option value="1920x1080">1920×1080</option><option value="1280x720">1280×720</option><option value="640x480">640×480</option></select></label>';
  // #592 Phase 2 — anchor stereo with surveyed ArUco markers. Defaults
  // ON when the prescan has already reported shared markers (we don't
  // check that here; operator can uncheck if they want the legacy path).
  h+='<label style="margin-top:.3em;display:block" title="Run ArUco detection on both frames and solvePnP against the surveyed registry to correct the camera poses before stereo triangulation. Reduces reprojection error from ~350 mm (FOV-only) to <50 mm on consumer cams. Needs the registry populated and ≥1 surveyed marker visible to each camera.">'
    +'<input type="checkbox" id="pcadv-aruco-anchor" checked> <b>Anchor stereo with ArUco markers</b> '
    +'<span style="color:#64748b">— tight 50 mm reprojection filter; falls back to 500 mm if no markers are visible</span></label>';
  h+='</div></details>';

  // Status area + action buttons
  h+='<div id="pcadv-status" style="display:none;padding:.5em;background:#0f172a;border:1px solid #1e293b;border-radius:4px;margin-bottom:.5em"></div>';
  h+='<div id="pcadv-actions" style="display:flex;gap:.4em">';
  h+='<button class="btn btn-on" id="pcadv-go" onclick="_pcAdvGo()">Start Scan</button>';
  h+='<button class="btn btn-off" onclick="closeModal()">Cancel</button>';
  h+='</div>';
  h+='</div>';
  document.getElementById('modal-title').textContent='Advanced Scan';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='flex';
  _pcAdvRefresh();
  // #596 — hydrate the ArUco panel inside the scan card
  _arucoLoad(function(){_arucoRenderTable('aruco-scan-host', {source:'scan'});});
}

function _pcAdvRefresh(){
  // Enable/disable the camera picker based on method (stereo is fixed pair)
  var method=(document.querySelector('input[name=pcmethod]:checked')||{}).value||'mono';
  var camCard=document.getElementById('pcadv-cams');
  if(camCard)camCard.style.opacity=method==='lite'?'0.4':'1';
  document.querySelectorAll('.pcadv-cam').forEach(function(cb){
    cb.disabled=(method==='lite')||cb.hasAttribute('data-force-disabled');
  });
  // #594 — probe ZoeDepth availability once per modal open. When the
  // host has torch + transformers, promote ZoeDepth to Recommended.
  // Empirical 2026-04-21 basement rig: ZoeDepth 4976 pts vs mono 2622
  // vs stereo 52 pts — ZoeDepth dominates on coverage AND accuracy, so
  // it's the default pick whenever it's available.
  if(!_pcAdvZoeProbed){
    _pcAdvZoeProbed=true;
    // Parallel probe: availability + any in-flight install. If an install
    // is running we surface progress instead of the Install now button,
    // and keep polling until it finishes. Once it does, re-probe so the
    // option flips to Recommended automatically.
    ra('GET','/api/depth-runtime/install-status',null,function(ins){
      if(ins&&ins.running){
        _pcAdvRenderInstallingNote(ins);
        _pcAdvWatchInstall();
        return;
      }
      _pcAdvProbeZoe();
    });
    return;
  }
}

function _pcAdvProbeZoe(){
    ra('GET','/api/space/scan/zoedepth',null,function(r){
      var noteEl=document.querySelector('.pcadv-zoe-note');
      var opt=document.getElementById('pcadv-opt-zoe');
      if(!opt)return;
      if(r&&r.available){
        if(noteEl)noteEl.innerHTML='<span style="color:#34d399">installed on host · best coverage for calibration</span>';
        // Strip any existing badge (the initial render gave it to mono)
        // and move Recommended onto ZoeDepth. Auto-check if mono was
        // still the selected radio.
        var currentRec=document.querySelector('.pcadv-rec-badge');
        if(currentRec)currentRec.remove();
        opt.insertAdjacentHTML('beforeend',' <span class="pcadv-rec-badge" style="background:#065f46;color:#34d399;padding:1px 6px;border-radius:8px;font-size:.7em;margin-left:.3em">\u2605 Recommended</span>');
        var zoeRadio=opt.querySelector('input[type=radio]');
        var monoRadio=document.querySelector('#pcadv-opt-mono input[type=radio]');
        if(zoeRadio&&monoRadio&&monoRadio.checked){zoeRadio.checked=true;_pcAdvRefresh();}
      }else if(r&&r.installable){
        // #598 — offer one-click install of the optional depth runtime
        if(noteEl){
          noteEl.innerHTML='<span style="color:#f59e0b">not installed · ~2 GB one-time download</span> '
            +'<button type="button" class="btn btn-on" style="margin-left:.4em;padding:.1em .6em;font-size:.85em"'
            +' onclick="event.preventDefault();event.stopPropagation();_depthRuntimeInstall()">Install now</button>';
        }
        var zoeRadio2=opt.querySelector('input[type=radio]');
        if(zoeRadio2)zoeRadio2.disabled=true;
        opt.style.opacity='.7';
      }else{
        if(noteEl)noteEl.innerHTML='<span style="color:#ef4444">unavailable: '+escapeHtml((r&&r.reason)||'unknown')+'</span>';
        var zoeRadio3=opt.querySelector('input[type=radio]');
        if(zoeRadio3)zoeRadio3.disabled=true;
        opt.style.opacity='.45';
      }
    });
}

function _pcAdvRenderInstallingNote(ins){
  var noteEl=document.querySelector('.pcadv-zoe-note');
  var opt=document.getElementById('pcadv-opt-zoe');
  if(!opt||!noteEl)return;
  var pct=Math.round(100*(ins.progress||0));
  noteEl.innerHTML='<span style="color:#60a5fa">installing '+pct+'% — '+escapeHtml(ins.phase||'working')+'</span> '
    +'<button type="button" class="btn btn-nav" style="margin-left:.4em;padding:.1em .6em;font-size:.85em"'
    +' onclick="event.preventDefault();event.stopPropagation();_depthRuntimeOpenProgress()">Details</button>'
    +'<button type="button" class="btn" style="margin-left:.3em;padding:.1em .6em;font-size:.85em;background:#532;color:#fca5a5"'
    +' onclick="event.preventDefault();event.stopPropagation();_depthRuntimeCancel()">Cancel</button>';
  var zoeRadio=opt.querySelector('input[type=radio]');
  if(zoeRadio)zoeRadio.disabled=true;
  opt.style.opacity='.7';
}

// Poll install-status while the card is visible. Stops when the install
// finishes (success → re-probe so Recommended flips; failure → show reason).
function _pcAdvWatchInstall(){
  ra('GET','/api/depth-runtime/install-status',null,function(ins){
    if(ins&&ins.running){
      _pcAdvRenderInstallingNote(ins);
      setTimeout(_pcAdvWatchInstall,2000);
      return;
    }
    // Install finished — re-run the availability probe.
    _pcAdvProbeZoe();
  });
}
var _pcAdvZoeProbed=false;

// #598 — depth-runtime installer modal. Kicks off the background pip/HF
// download and polls /api/depth-runtime/install-status for progress.
// Two entry points: _depthRuntimeInstall() POSTs then polls;
// _depthRuntimeOpenProgress() attaches to an already-running install
// (used when the installer marker triggered it silently or the user
// clicks Install on a second surface while one is already live).
function _depthRuntimeOpenProgressModal(){
  var html='<div style="min-width:480px;max-width:560px">'
    +'<h3 style="margin:0 0 .4em 0">Install ZoeDepth runtime</h3>'
    +'<div style="color:#94a3b8;font-size:.85em;margin-bottom:.8em">'
    +'Creates a separate Python environment at %LOCALAPPDATA%\\SlyLED\\runtimes\\depth\\ '
    +'and downloads ~2 GB of dependencies + model weights. The orchestrator itself '
    +'stays lean — the depth engine runs as a subprocess only when calibration needs it.</div>'
    +'<div id="dri-phase" style="font-weight:600;margin-bottom:.3em">Starting...</div>'
    +'<div style="background:#0f172a;border:1px solid #334155;border-radius:4px;height:14px;overflow:hidden;margin-bottom:.4em">'
    +'<div id="dri-bar" style="height:100%;width:0%;background:#34d399;transition:width .3s"></div>'
    +'</div>'
    +'<div id="dri-msg" style="color:#94a3b8;font-size:.8em;min-height:1.4em;margin-bottom:.6em"></div>'
    +'<div id="dri-log" style="background:#0b1220;border:1px solid #1e293b;border-radius:4px;padding:.4em;'
    +'font-family:monospace;font-size:.72em;color:#64748b;max-height:160px;overflow:auto;margin-bottom:.6em"></div>'
    +'<div id="dri-actions" style="display:flex;gap:.4em;justify-content:flex-end">'
    +'<button id="dri-cancel" class="btn" style="background:#532;color:#fca5a5" onclick="_depthRuntimeCancel()">Cancel Install</button>'
    +'<button id="dri-close" class="btn btn-nav" onclick="closeModal();loadSettings&&loadSettings();">Close (runs in background)</button>'
    +'</div></div>';
  document.getElementById('modal-title').textContent='Depth runtime';
  document.getElementById('modal-body').innerHTML=html;
  document.getElementById('modal').style.display='flex';
}

function _depthRuntimeInstall(force){
  _depthRuntimeOpenProgressModal();
  ra('POST','/api/depth-runtime/install',{force:!!force},function(r){
    if(r&&r.ok){
      _depthRuntimePoll();
      return;
    }
    // If the server says "already running", just attach to the live job.
    if(r&&r.error&&/already running/i.test(r.error)){
      _depthRuntimePoll();
      return;
    }
    document.getElementById('dri-phase').textContent='Could not start install';
    document.getElementById('dri-msg').innerHTML='<span style="color:#ef4444">'+escapeHtml((r&&r.error)||'unknown')+'</span>';
    document.getElementById('dri-close').disabled=false;
  });
}

// Attach the progress modal to a background install already in flight
// (e.g. triggered by the installer marker on first launch).
function _depthRuntimeOpenProgress(){
  _depthRuntimeOpenProgressModal();
  _depthRuntimePoll();
}

function _depthRuntimePoll(){
  ra('GET','/api/depth-runtime/install-status',null,function(r){
    if(!r){setTimeout(_depthRuntimePoll,1500);return;}
    var bar=document.getElementById('dri-bar');
    var phase=document.getElementById('dri-phase');
    var msg=document.getElementById('dri-msg');
    var logEl=document.getElementById('dri-log');
    if(bar)bar.style.width=Math.round(100*(r.progress||0))+'%';
    if(phase)phase.textContent=r.phase||'working';
    if(msg)msg.textContent=r.message||'';
    if(logEl&&r.log){
      logEl.innerHTML=r.log.slice(-30).map(function(e){return escapeHtml(e.m);}).join('<br>');
      logEl.scrollTop=logEl.scrollHeight;
    }
    if(r.running){
      setTimeout(_depthRuntimePoll,1500);
      return;
    }
    // Done — success or failure. Hide the Cancel button; Close stays.
    var cancelBtn=document.getElementById('dri-cancel');
    if(cancelBtn)cancelBtn.style.display='none';
    var closeBtn=document.getElementById('dri-close');
    if(closeBtn)closeBtn.textContent='Close';
    if(r.ok){
      if(phase)phase.innerHTML='<span style="color:#34d399">Installed</span>';
      if(bar)bar.style.background='#34d399';
      _pcAdvZoeProbed=false;    // re-probe so the Advanced Scan card flips to available
    }else{
      if(phase)phase.innerHTML='<span style="color:#ef4444">'+(r.cancelRequested?'Cancelled':'Failed')+'</span>';
      if(bar)bar.style.background='#ef4444';
      if(msg)msg.innerHTML='<span style="color:#ef4444">'+escapeHtml(r.error||r.message||'install failed')+'</span>';
    }
  });
}

function _depthRuntimeCancel(){
  if(!confirm('Cancel the install? Any partial venv will be wiped on the next Reinstall.'))return;
  var cancelBtn=document.getElementById('dri-cancel');
  if(cancelBtn){cancelBtn.disabled=true;cancelBtn.textContent='Cancelling...';}
  ra('POST','/api/depth-runtime/install/cancel',{},function(){
    // Keep polling — the state transition to running=false happens
    // when the subprocess actually exits, which is what
    // _depthRuntimePoll already handles.
  });
}

function _pcAdvGo(){
  var method=(document.querySelector('input[name=pcmethod]:checked')||{}).value||'mono';
  var light=(document.getElementById('pcadv-light')||{}).value||'blackout';
  var maxPts=parseInt((document.getElementById('pcadv-maxpts')||{}).value||'5000');
  var selected=[];
  document.querySelectorAll('.pcadv-cam:checked').forEach(function(cb){selected.push(parseInt(cb.value));});
  var statusEl=document.getElementById('pcadv-status');
  statusEl.style.display='block';
  document.getElementById('pcadv-go').disabled=true;

  function _render(msg){statusEl.innerHTML=msg;}
  _render('<span style="color:#94a3b8">Starting…</span>');

  // Show a Done button instead of auto-closing so the operator can
  // read the result. Cancel → Done flip.
  function _finish(){
    var acts=document.getElementById('pcadv-actions');
    if(acts){
      acts.innerHTML='<button class="btn btn-on" onclick="closeModal();loadSetup()">Done</button>';
    }
  }

  function _ok(msg, extra){
    _render('<span style="color:#34d399">'+msg+'</span>'+(extra||''));
    _finish();
  }
  function _fail(err){
    _render('<span style="color:#ef4444">Failed: '+escapeHtml(err||'unknown')+'</span>');
    document.getElementById('pcadv-go').disabled=false;
  }

  if(method==='lite'){
    ra('POST','/api/space/scan/lite',{},function(r){
      if(r&&r.ok)_ok('\u2713 Lite cloud built: '+r.totalPoints+' points');
      else _fail(r&&r.err);
    });
    return;
  }
  if(method==='zoedepth'){
    if(selected.length===0){
      _render('<span style="color:#f59e0b">Select at least one camera.</span>');
      document.getElementById('pcadv-go').disabled=false;
      return;
    }
    // #696 — async kick-off + per-stage log polling. Pre-fix this used
    // a synchronous POST that exceeded the SPA's 30 s XHR timeout on
    // any rig with >2 cameras, surfacing as "Failed: unknown" while
    // the server silently finished the scan.
    _render(
      '<div style="font-size:.85em;color:#94a3b8;margin-bottom:.4em">'
      + 'Running ZoeDepth on host · ~15 s per camera on CPU. '
      + 'This window shows live per-stage progress.'
      + '</div>'
      + '<div class="prog-bar" style="height:10px;margin:.3em 0">'
      +   '<div class="prog-fill" id="zoe-fill" style="width:1%"></div>'
      + '</div>'
      + '<div id="zoe-msg" style="font-size:.82em;color:#94a3b8;margin-bottom:.3em">Starting…</div>'
      + '<div id="zoe-log" style="max-height:240px;overflow-y:auto;font-family:monospace;'
      +   'font-size:.72em;background:#0f172a;border:1px solid #334155;border-radius:4px;'
      +   'padding:.5em;color:#cbd5e1;line-height:1.45"></div>'
    );
    ra('POST','/api/space/scan/zoedepth',
       {cameras:selected,maxPoints:maxPts,lighting:light},
       function(r){
      if(!r||!r.ok){
        var err=(r&&r.err)||
                'request failed (orchestrator did not accept the scan kick-off)';
        var det=(r&&r.detail)?
                ('<div style="font-size:.8em;color:#94a3b8;margin-top:.3em">'
                 +escapeHtml(r.detail)+'</div>'):'';
        _render('<span style="color:#ef4444">Could not start ZoeDepth scan: '
                +escapeHtml(err)+'</span>'+det);
        document.getElementById('pcadv-go').disabled=false;
        return;
      }
      var seen = 0;
      var poll = setInterval(function(){
        ra('GET','/api/space/scan/zoedepth/status',null,function(s){
          if(!s){
            clearInterval(poll);
            _render('<span style="color:#ef4444">Status poll failed — orchestrator unreachable. The scan may still be running; check Setup tab for a fresh point cloud.</span>');
            document.getElementById('pcadv-go').disabled=false;
            return;
          }
          var fill=document.getElementById('zoe-fill');
          var msg=document.getElementById('zoe-msg');
          var logBox=document.getElementById('zoe-log');
          if(fill)fill.style.width=(s.progress||0)+'%';
          if(msg)msg.textContent=s.message||('Progress: '+s.progress+'%');
          if(logBox && Array.isArray(s.log) && s.log.length>seen){
            var added='';
            for(var i=seen;i<s.log.length;i++){
              var entry=s.log[i];
              var color=entry.level==='error'?'#f87171'
                       :entry.level==='warn'?'#fbbf24':'#94a3b8';
              var ts=(entry.ts||'').slice(11,19);
              added+='<div><span style="color:#475569">'+ts+'</span> '
                    +'<span style="color:'+color+'">'+escapeHtml(entry.message||'')+'</span></div>';
            }
            logBox.innerHTML+=added;
            logBox.scrollTop=logBox.scrollHeight;
            seen=s.log.length;
          }
          if(s.running)return;
          clearInterval(poll);
          if(s.error){
            _render('<span style="color:#ef4444">ZoeDepth scan failed: '
                    +escapeHtml(s.error)+'</span>'
                    +(logBox?logBox.outerHTML:''));
            document.getElementById('pcadv-go').disabled=false;
            return;
          }
          if(s.result){
            var camSummary='<div style="margin-top:.4em">'
              +(s.result.cameras||[]).map(function(c){
                return '<div style="font-size:.78em;color:#94a3b8">'
                      +escapeHtml(c.name)+' · '+c.pointCount+' pts · inference '
                      +(c.inferenceS||'?')+' s</div>';
              }).join('')+'</div>';
            var ma=s.result.markerAlignment||{};
            var alignLine='';
            if(ma.applied){
              alignLine='<div style="font-size:.78em;color:#34d399;margin-top:.2em">'
                +'✓ Z-aligned to floor markers: '+ma.zOffsetMm+' mm '
                +'('+(ma.method||'marker-median')+')</div>';
            }else if(ma.reason){
              alignLine='<div style="font-size:.78em;color:#fbbf24;margin-top:.2em">'
                +'⚠ Marker alignment skipped: '+escapeHtml(ma.reason)+'</div>';
            }
            _ok('✓ ZoeDepth scan complete: '+s.result.totalPoints
                +' points in '+s.result.elapsedS+' s',
                camSummary+alignLine);
          }else{
            _render('<span style="color:#ef4444">ZoeDepth scan ended without producing a result. Check the orchestrator log.</span>');
            document.getElementById('pcadv-go').disabled=false;
          }
        });
      }, 1000);
    });
    return;
  }
  if(method==='stereo'){
    if(selected.length!==2){
      _render('<span style="color:#f59e0b">Select exactly two cameras for a stereo pair.</span>');
      document.getElementById('pcadv-go').disabled=false;
      return;
    }
    var res=(document.getElementById('pcadv-stereores')||{}).value||'1920x1080';
    var xy=res.split('x').map(function(v){return parseInt(v);});
    var wantAnchor=(document.getElementById('pcadv-aruco-anchor')||{}).checked;
    ra('POST','/api/space/scan/stereo',{cameras:selected,resolution:xy,lighting:light,
                                          arucoMarkers:!!wantAnchor},function(r){
      if(r&&r.ok){
        // #592 Phase 2 — surface the anchor state so the operator can
        // see whether the tight 50 mm filter kicked in (and why not if
        // it didn't).
        var anchorLine='';
        if(r.arucoAnchor&&r.arucoAnchor.requested){
          if(r.arucoAnchored){
            var aA=r.arucoAnchor.a||{}, aB=r.arucoAnchor.b||{};
            anchorLine='<div style="font-size:.78em;color:#34d399;margin-top:.3em">'
              +'✓ ArUco-anchored: cam A '+(aA.cornerCount||'?')+' corners RMS '+(aA.reprojectionRmsPx||'?')+'px '
              +'· cam B '+(aB.cornerCount||'?')+' corners RMS '+(aB.reprojectionRmsPx||'?')+'px '
              +'· threshold '+(r.reprojThresholdMm||50)+'mm</div>';
          }else{
            anchorLine='<div style="font-size:.78em;color:#f59e0b;margin-top:.3em">'
              +'⚠ Anchor requested but '+escapeHtml((r.arucoAnchor.fallback||'unavailable'))
              +' — fell back to 500 mm threshold</div>';
          }
        }
        var warn=r.warning?'<div style="color:#f59e0b;font-size:.82em;margin-top:.3em">\u26a0 '+escapeHtml(r.warning)+'</div>':'';
        var details='<div style="font-size:.78em;color:#94a3b8;margin-top:.3em">'
          +'Feature matches: <b>'+r.featureMatches+'</b> · '
          +'Triangulated: <b>'+r.totalPoints+'</b> · '
          +'Capture delta: '+r.captureDeltaMs+'ms · '
          +'Tilt \u0394: '+r.tiltDelta+'°'
          +(r.panDelta!==undefined?(' · Pan \u0394: '+r.panDelta+'°'):'')
          +'</div>'+anchorLine;
        // Helper text if yield is suspiciously low.
        var calButtons=selected.map(function(id){
          return '<button class="btn" onclick="closeModal();_calWizardStart('+id+')" style="font-size:.75em;background:#7c3aed;color:#e9d5ff;margin-right:.3em">Calibrate fixture '+id+'\u2026</button>';
        }).join('');
        if(r.totalPoints===0){
          warn=(warn||'')+'<div style="color:#f59e0b;font-size:.82em;margin-top:.3em">'
            +'<b>No points triangulated.</b> Most common cause on consumer webcams is <b>uncalibrated lens distortion</b> — '
            +'FOV-derived intrinsics can\'t model the 5-15% barrel distortion in wide-angle lenses, so triangulation rays miss each other by hundreds of mm. '
            +'<br><br><b>Fix:</b> run ArUco 3D calibration on each camera, then rescan.'
            +'<div style="margin-top:.4em">'+calButtons+'</div>'
            +'<p style="font-size:.72em;color:#64748b;margin-top:.4em">Other causes: large tilt/pan \u0394, untextured scene (ORB needs features), physically misaligned poses vs layout.</p>'
            +'</div>';
        }else if(r.totalPoints<20){
          warn=(warn||'')+'<div style="color:#f59e0b;font-size:.82em;margin-top:.3em">'
            +'Low point count ('+r.totalPoints+'). ArUco camera calibration will dramatically improve stereo quality.'
            +'<div style="margin-top:.3em">'+calButtons+'</div>'
            +'</div>';
        }
        _ok('\u2713 Stereo scan complete', details+warn);
      }else{
        _fail(r&&r.err);
      }
    });
    return;
  }
  // Monocular
  if(selected.length===0){
    _render('<span style="color:#f59e0b">Select at least one camera.</span>');
    document.getElementById('pcadv-go').disabled=false;
    return;
  }
  ra('POST','/api/space/scan',{maxPointsPerCamera:maxPts,cameras:selected,lighting:light},function(r){
    if(!r||!r.ok){ _fail(r&&r.err); return; }
    _render('<span style="color:#94a3b8">Scanning '+(r.cameras||0)+' cameras · lighting='+escapeHtml(r.lighting||light)+'\u2026</span>');
    var poll=setInterval(function(){
      ra('GET','/api/space/scan/status',null,function(st){
        if(!st)return;
        var bar='<div style="background:#0a0f13;height:6px;border-radius:3px;margin:.3em 0"><div style="height:6px;background:#34d399;width:'+(st.progress||0)+'%;border-radius:3px"></div></div>';
        _render('<span style="color:#94a3b8">'+escapeHtml(st.message||'')+'</span>'+bar+'<span style="color:#64748b;font-size:.78em">'+(st.progress||0)+'%</span>');
        if(!st.running){
          clearInterval(poll);
          var cams=(st.result&&st.result.cameras)||[];
          var summary='<div style="margin-top:.4em">'+cams.map(function(c){
            var q=c.anchorQuality||'—';
            var qColor=q==='ok'?'#34d399':q==='fallback'?'#fbbf24':q==='failed'?'#ef4444':'#94a3b8';
            return '<div style="font-size:.78em;color:#94a3b8">'+escapeHtml(c.name)+' · '+c.pointCount+' pts · anchor <span style="color:'+qColor+'">'+q+'</span></div>';
          }).join('')+'</div>';
          _ok('\u2713 Scan complete: '+(st.totalPoints||0)+' points', summary);
        }
      });
    },800);
  });
}

function showAddFixtureModal(){
  _modalStack=[];
  var h='<label>Fixture Type</label>'
    +'<select id="aft" onchange="_toggleAddFixFields()" style="width:100%;margin-bottom:.6em">'
    +'<option value="dmx">DMX Fixture</option>'
    +'<option value="led">SlyLED Fixture (LED)</option>'
    +'<option value="camera">Camera</option>'
    +'<option value="gyro">Gyro Controller</option>'
    +'<option value="group">Fixture Group</option></select>'
    +'<div id="af-led" style="display:none">'
    +'<label>IP Address</label><input id="af-ip" placeholder="x.x.x.x" style="width:100%">'
    +'<div style="margin-top:.8em"><button class="btn btn-on" onclick="_submitAddFixture()" style="font-size:.95em;padding:.5em 2em">Add Fixture</button></div>'
    +'</div>'
    +'<div id="af-dmx">'
    +'<div style="margin-bottom:.6em;padding:.5em;background:#0f172a;border:1px solid #1e3a5f;border-radius:4px">'
    +'<label style="font-size:.82em;color:#93c5fd">Search All Fixtures (Local + Community + OFL)</label>'
    +'<div style="display:flex;gap:.3em;margin:.3em 0"><input id="af-ofl-q" placeholder="e.g. par, moving head, chauvet..." style="flex:1;padding:.3em;font-size:.85em" onkeydown="if(event.key===\'Enter\')_afOflSearch()">'
    +'<button class="btn" style="font-size:.75em;padding:.2em .5em;background:#1e3a5f;color:#93c5fd" onclick="_afOflSearch()">Search</button>'
    +'<button class="btn" style="font-size:.75em;padding:.2em .5em;background:#1e293b;color:#94a3b8" onclick="_afBrowseAll()">Browse All</button></div>'
    +'<div id="af-ofl-results" style="max-height:200px;overflow-y:auto;font-size:.8em"></div>'
    +'</div>'
    +'<label>Name</label><input id="af-name" placeholder="Moving Head 1" style="width:100%;margin-bottom:.4em">'
    +'<div style="display:flex;gap:.5em"><div style="flex:1"><label>Universe</label><input id="af-uni" type="number" value="1" min="1" style="width:100%;margin-bottom:.4em" onchange="_afAddrAutofill()"></div>'
    +'<div style="flex:1"><label>Start Address</label><input id="af-addr" type="number" value="1" min="1" max="512" style="width:100%;margin-bottom:.4em" onchange="_afPreviewChannels()"></div>'
    +'<div style="flex:1"><label>Channels</label><input id="af-ch" type="number" value="3" min="1" max="512" style="width:100%;margin-bottom:.4em" onchange="_afPreviewChannels()"></div></div>'
    +'<label>Geometry</label><select id="af-geom" style="width:100%;margin-bottom:.4em"><option value="point">Point</option><option value="linear">Linear</option></select>'
    +'<label>Profile</label>'
    +'<select id="af-prof" style="width:100%" onchange="_afPreviewChannels()"><option value="">— None (generic channels) —</option></select>'
    +'<div id="af-ch-preview" style="max-height:120px;overflow-y:auto;font-size:.75em;margin-top:.4em"></div>'
    +'<div style="margin-top:.8em;padding-top:.5em;border-top:1px solid #334155"><button class="btn btn-on" onclick="_submitAddFixture()" style="font-size:.95em;padding:.5em 2em">Add Fixture</button></div>'
    +'</div>'
    +'<div id="af-camera" style="display:none">'
    +'<p style="color:#94a3b8;font-size:.82em;margin-bottom:.5em">Scan the network for camera nodes. Name, FOV, and resolution are read from the device.</p>'
    +'<button class="btn" id="af-cam-disc-btn" onclick="_afCamDiscover()" style="background:#0e7490;color:#fff;font-size:.9em;padding:.4em 1.2em;margin-bottom:.5em">Discover Cameras</button>'
    +' <span id="af-cam-disc-status" style="color:#64748b;font-size:.82em"></span>'
    +'<div id="af-cam-results"></div>'
    +'<div id="af-cam-manual" style="margin-top:.6em;border-top:1px solid #334155;padding-top:.5em">'
    +'<p style="color:#64748b;font-size:.78em;margin-bottom:.3em">Or enter IP manually:</p>'
    +'<div style="display:flex;gap:.3em;align-items:end"><input id="af-cam-ip" placeholder="192.168.10.x" style="flex:1">'
    +'<button class="btn" onclick="_afCamProbe()" style="background:#334155;color:#94a3b8;font-size:.82em;padding:.35em .8em">Probe</button></div>'
    +'<div id="af-cam-probe-result" style="margin-top:.3em"></div>'
    +'</div>'
    +'</div>'
    +'<div id="af-gyro" style="display:none">'
    +'<p style="color:#94a3b8;font-size:.82em;margin-bottom:.5em">Links a Waveshare ESP32-S3 gyro board to a DMX moving head fixture for real-time orientation control.</p>'
    +'<label>Name</label><input id="af-gyro-name" placeholder="Gyro Controller 1" style="width:100%;margin-bottom:.4em">'
    +'<label>Gyro Board (child)</label>'
    +'<select id="af-gyro-child" style="width:100%;margin-bottom:.4em"><option value="">— Select gyro board —</option></select>'
    +'<label>Assigned Mover Fixture (DMX)</label>'
    +'<select id="af-gyro-mover" style="width:100%;margin-bottom:.4em"><option value="">— Select DMX mover —</option></select>'
    +'<div style="margin-top:.8em"><button class="btn btn-on" onclick="_submitAddFixture()" style="font-size:.95em;padding:.5em 2em">Add Gyro Controller</button></div>'
    +'</div>'
    +'<div id="af-group" style="display:none">'
    +'<label>Group Name</label><input id="af-gname" placeholder="Front Wash" style="width:100%;margin-bottom:.4em">'
    +'<label>Select Members</label><div id="af-members" style="max-height:200px;overflow-y:auto;border:1px solid #334;padding:.3em;margin-bottom:.4em"></div>'
    +'<div style="margin-top:.8em"><button class="btn btn-on" onclick="_submitAddFixture()" style="font-size:.95em;padding:.5em 2em">Add Group</button></div>'
    +'</div>';
  document.getElementById('modal-title').textContent='Add Fixture';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
  _toggleAddFixFields(); // load DMX profile dropdown since DMX is default
}
function _toggleAddFixFields(){
  var t=document.getElementById('aft').value;
  document.getElementById('af-led').style.display=t==='led'?'':'none';
  document.getElementById('af-dmx').style.display=t==='dmx'?'':'none';
  document.getElementById('af-camera').style.display=t==='camera'?'':'none';
  document.getElementById('af-gyro').style.display=t==='gyro'?'':'none';
  document.getElementById('af-group').style.display=t==='group'?'':'none';
  if(t==='gyro'){
    // Populate gyro board + mover dropdowns
    ra('GET','/api/children',null,function(cs){
      var sel=document.getElementById('af-gyro-child');if(!sel)return;
      sel.innerHTML='<option value="">— Select gyro board —</option>';
      (cs||[]).filter(function(c){return c.type==='gyro';}).forEach(function(c){
        var o=document.createElement('option');o.value=c.id;
        o.textContent=(c.name||c.hostname)+' ('+c.ip+')';sel.appendChild(o);
      });
    });
    ra('GET','/api/fixtures',null,function(fxs){
      var sel=document.getElementById('af-gyro-mover');if(!sel)return;
      sel.innerHTML='<option value="">— Select DMX mover —</option>';
      (fxs||[]).filter(function(f){return f.fixtureType==='dmx';}).forEach(function(f){
        var o=document.createElement('option');o.value=f.id;
        o.textContent=escapeHtml(f.name)+' (U'+f.dmxUniverse+' @'+f.dmxStartAddr+')';sel.appendChild(o);
      });
    });
  }
  if(t==='group'){
    var mel=document.getElementById('af-members');
    if(mel&&!mel.innerHTML){
      ra('GET','/api/fixtures',null,function(fxs){
        var h='';(fxs||[]).forEach(function(f){
          if(f.type==='group')return;
          var badge=f.fixtureType==='dmx'?'<span style="color:#a78bfa;font-size:.75em">DMX</span>':f.fixtureType==='camera'?'<span style="color:#22d3ee;font-size:.75em">CAM</span>':'<span style="color:#4ade80;font-size:.75em">LED</span>';
          h+='<label style="display:block;padding:.15em 0;font-size:.82em"><input type="checkbox" value="'+f.id+'" class="af-member-cb"> '+escapeHtml(f.name)+' '+badge+'</label>';
        });
        mel.innerHTML=h||'<span style="color:#556">No fixtures to group</span>';
      });
    }
  }
  if(t==='dmx'){
    // Always refresh profile dropdown (custom profiles may have been added)
    var sel=document.getElementById('af-prof');
    if(sel){
      var curVal=sel.value;
      ra('GET','/api/dmx-profiles',null,function(profiles){
        if(!profiles||!sel)return;
        sel.innerHTML='<option value="">-- None (generic channels) --</option>';
        profiles.forEach(function(p){
          var o=document.createElement('option');o.value=p.id;
          o.textContent=p.name+' ('+p.channelCount+'ch)';sel.appendChild(o);
        });
        if(curVal)sel.value=curVal;
      });
    }
    // #515 — smart defaults. Universe = last-used in this session, address
    // = next free slot in that universe based on current fixtures.
    var uniEl=document.getElementById('af-uni');
    if(uniEl&&window._lastDmxUniverse)uniEl.value=window._lastDmxUniverse;
    _afAddrAutofill();
  }
}

// #515 — compute next available address in the selected universe.
function _afNextFreeAddr(uni){
  var end=0;
  (_fixtures||[]).forEach(function(f){
    if(f.fixtureType!=='dmx')return;
    if((f.dmxUniverse||1)!==uni)return;
    var a=f.dmxStartAddr||1;
    var n=f.dmxChannelCount||1;
    var last=a+n-1;
    if(last>end)end=last;
  });
  return end+1;
}

function _afAddrAutofill(){
  var uniEl=document.getElementById('af-uni');
  var addrEl=document.getElementById('af-addr');
  if(!uniEl||!addrEl)return;
  var uni=parseInt(uniEl.value)||1;
  var next=_afNextFreeAddr(uni);
  addrEl.value=next;
  _afPreviewChannels();
}
function _afOflSearch(){
  var q=document.getElementById('af-ofl-q').value.trim();
  var el=document.getElementById('af-ofl-results');if(!el)return;
  if(q.length<2){el.innerHTML='<span style="color:#f66">Enter at least 2 characters</span>';return;}
  el.innerHTML='<span style="color:#888">Searching local, community &amp; OFL...</span>';
  ra('GET','/api/dmx-profiles/unified-search?q='+encodeURIComponent(q),null,function(r){
    if(!el)return;
    if(!r||r.err){el.innerHTML='<span style="color:#f66">'+(r&&r.err||'Search failed')+'</span>';return;}
    if(!r.length){el.innerHTML='<span style="color:#888">No results for "'+escapeHtml(q)+'"</span>';return;}
    var h='';
    var srcColors={local:'#22c55e',community:'#7c3aed',ofl:'#3b82f6'};
    var srcLabels={local:'Local',community:'Community',ofl:'OFL'};
    r.forEach(function(f){
      var src=f.source||'ofl';
      var badge='<span style="font-size:.65em;padding:1px 5px;border-radius:8px;background:'+srcColors[src]+'22;color:'+srcColors[src]+'">'+srcLabels[src]+'</span>';
      var selectFn;
      if(src==='local'){
        selectFn='_afSelectLocal(\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\','+(f.channelCount||3)+')';
      }else if(src==='community'){
        selectFn='_afSelectCommunity(\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')';
      }else{
        selectFn='_afSelectOfl(\''+(f.oflMfr||'')+'\',\''+escapeHtml(f.id)+'\',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')';
      }
      h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:.3em 0;border-bottom:1px solid #1e293b">'
        +'<span>'+escapeHtml(f.name)+' <span style="color:#64748b;font-size:.82em">'+escapeHtml(f.manufacturer||'')+'</span> '+badge+'</span>'
        +'<button class="btn" style="font-size:.7em;padding:.15em .4em;background:#14532d;color:#86efac" onclick="'+selectFn+'">Select</button></div>';
    });
    el.innerHTML=h;
  });
}
// #515 — channel map preview. Adds an absolute Address column computed
// from the current start address, highlights DMX-512 overflow, and
// marks any address that clashes with an existing fixture in red.
function _afOccupiedAddrSet(){
  var uniEl=document.getElementById('af-uni');
  var uni=uniEl?(parseInt(uniEl.value)||1):1;
  var set={};
  (_fixtures||[]).forEach(function(f){
    if(f.fixtureType!=='dmx')return;
    if((f.dmxUniverse||1)!==uni)return;
    var a=f.dmxStartAddr||1;
    var n=f.dmxChannelCount||1;
    for(var i=0;i<n;i++)set[a+i]=f.name||('Fixture '+f.id);
  });
  return set;
}

function _afPreviewChannels(){
  var el=document.getElementById('af-ch-preview');if(!el)return;
  var profId=document.getElementById('af-prof').value;
  var addrEl=document.getElementById('af-addr');
  var startAddr=addrEl?(parseInt(addrEl.value)||1):1;
  var occupied=_afOccupiedAddrSet();
  var conflict=false, maxAddr=0;
  function _row(chIdx,offset,name,type,bits){
    var addr=startAddr+offset;
    if(addr>maxAddr)maxAddr=addr;
    var clash=(occupied[addr]!==undefined);
    if(clash)conflict=true;
    var over=addr>512;
    if(over)conflict=true;
    var typeCol={'red':'#f66','green':'#6f6','blue':'#66f','white':'#eee','dimmer':'#fa6','pan':'#6ef','tilt':'#c8f'};
    var col=typeCol[type]||'#94a3b8';
    var addrCell=clash
      ? '<td style="color:#f87171;font-weight:bold" title="Conflicts with '+escapeHtml(occupied[addr]||'')+'">'+addr+' ✕</td>'
      : (over?'<td style="color:#f87171" title="exceeds 512">'+addr+' ✕</td>'
             :'<td style="color:#e2e8f0">'+addr+'</td>');
    return '<tr style="border-bottom:1px solid #1e293b"><td>'+chIdx+'</td><td>+'+offset+'</td><td>'+escapeHtml(name)+'</td><td style="color:'+col+'">'+type+'</td>'+(bits!==undefined?'<td>'+bits+'</td>':'')+addrCell+'</tr>';
  }
  function _header(showBits){
    return '<table style="width:100%;border-collapse:collapse;font-size:.75em"><tr style="color:#64748b;text-align:left">'
      +'<th>#</th><th>Off</th><th>Name</th><th>Type</th>'
      +(showBits?'<th>Bits</th>':'')
      +'<th>Addr</th></tr>';
  }
  function _finalize(h){
    var chEl=document.getElementById('af-ch');
    var chCount=chEl?(parseInt(chEl.value)||0):0;
    var endAddr=startAddr+chCount-1;
    var summary='<div style="font-size:.72em;margin-top:.2em;color:'+(conflict?'#f87171':'#64748b')+'">'
      +'Range '+startAddr+'–'+endAddr+' ('+chCount+' channels)'
      +(conflict?' — conflict / overflow':'')
      +'</div>';
    el.innerHTML=h+'</table>'+summary;
  }
  if(!profId){
    var ch=parseInt(document.getElementById('af-ch').value)||3;
    var h=_header(false);
    for(var i=0;i<ch;i++)h+=_row(i+1,i,'Channel '+(i+1),'dimmer');
    _finalize(h);
    return;
  }
  ra('GET','/api/dmx-profiles/'+profId,null,function(p){
    if(!p||!p.channels){el.innerHTML='';return;}
    var h=_header(true);
    p.channels.forEach(function(c,i){
      h+=_row(i+1,c.offset||0,c.name||('Ch '+(i+1)),c.type||'dimmer',c.bits||8);
    });
    _finalize(h);
  });
}
function _afBrowseAll(){
  var el=document.getElementById('af-ofl-results');if(!el)return;
  el.innerHTML='<span style="color:#888;font-size:.82em">Loading all profiles...</span>';
  ra('GET','/api/dmx-profiles',null,function(profiles){
    if(!profiles||!profiles.length){el.innerHTML='<span style="color:#888">No profiles in library</span>';return;}
    var h='<div style="font-size:.75em;color:#64748b;margin-bottom:.3em">'+profiles.length+' profiles</div>';
    h+='<table style="width:100%;font-size:.82em;border-collapse:collapse"><tr style="color:#64748b"><th style="text-align:left">Name</th><th style="text-align:left">Manufacturer</th><th>Ch</th><th>Category</th><th></th></tr>';
    profiles.forEach(function(p){
      var src=p.builtin?'<span style="color:#64748b;font-size:.75em">built-in</span>':'<span style="color:#22c55e;font-size:.75em">custom</span>';
      h+='<tr style="border-bottom:1px solid #1e293b">'
        +'<td style="padding:.2em .3em">'+escapeHtml(p.name)+'</td>'
        +'<td style="padding:.2em .3em;color:#94a3b8">'+escapeHtml(p.manufacturer||'')+'</td>'
        +'<td style="text-align:center">'+p.channelCount+'</td>'
        +'<td style="color:#64748b;font-size:.85em">'+escapeHtml(p.category||'')+'</td>'
        +'<td><button class="btn" style="font-size:.7em;padding:.15em .4em;background:#14532d;color:#86efac" '
        +'onclick="_afSelectLocal(\''+escapeHtml(p.id)+'\',\''+escapeHtml(p.name).replace(/'/g,"\\'")+'\','+p.channelCount+')">Select</button></td></tr>';
    });
    h+='</table>';
    el.innerHTML=h;
  });
}
function _afSelectLocal(profId,displayName,chCount){
  // Local profile already exists — just select it in the dropdown
  var nameEl=document.getElementById('af-name');if(nameEl&&!nameEl.value)nameEl.value=displayName;
  var chEl=document.getElementById('af-ch');if(chEl)chEl.value=chCount;
  var sel=document.getElementById('af-prof');
  if(sel){for(var i=0;i<sel.options.length;i++){if(sel.options[i].value===profId){sel.selectedIndex=i;break;}}}
  var el=document.getElementById('af-ofl-results');
  if(el)el.innerHTML='<span style="color:#86efac">Selected local profile: '+escapeHtml(displayName)+'</span>';
  _afPreviewChannels();
}
function _afSelectCommunity(slug,displayName){
  var el=document.getElementById('af-ofl-results');
  if(el)el.innerHTML='<span style="color:#a78bfa">Downloading '+escapeHtml(displayName)+'...</span>';
  ra('POST','/api/dmx-profiles/community/download',{slug:slug},function(r){
    if(r&&r.ok){
      // Refresh profile dropdown and select
      ra('GET','/api/dmx-profiles',null,function(profiles){
        var sel=document.getElementById('af-prof');if(!sel)return;
        sel.innerHTML='<option value="">— None —</option>';
        (profiles||[]).forEach(function(p){
          var o=document.createElement('option');o.value=p.id;o.textContent=p.name+' ('+p.channelCount+'ch)';
          sel.appendChild(o);if(p.id===slug)sel.value=slug;
        });
        var nameEl=document.getElementById('af-name');if(nameEl&&!nameEl.value)nameEl.value=displayName;
        var matched=profiles.find(function(p){return p.id===slug;});
        if(matched){var chEl=document.getElementById('af-ch');if(chEl)chEl.value=matched.channelCount;}
        if(el)el.innerHTML='<span style="color:#86efac">Downloaded: '+escapeHtml(displayName)+'</span>';
        _afPreviewChannels();
      });
    }else{if(el)el.innerHTML='<span style="color:#f66">Download failed</span>';}
  });
}
function _afSelectOfl(mfr,fix,displayName){
  var el=document.getElementById('af-ofl-results');
  if(el)el.innerHTML='<span style="color:#86efac">Importing '+displayName+'...</span>';
  ra('POST','/api/dmx-profiles/ofl/import-by-id',{manufacturer:mfr,fixture:fix},function(r){
    if(r&&r.ok&&r.profiles&&r.profiles.length){
      var p=r.profiles[0];
      // Fill form with imported profile data
      var nameEl=document.getElementById('af-name');if(nameEl&&!nameEl.value)nameEl.value=p.name;
      var chEl=document.getElementById('af-ch');if(chEl)chEl.value=p.channels;
      // Add to profile dropdown and select it
      var sel=document.getElementById('af-prof');
      if(sel){
        var exists=false;
        for(var i=0;i<sel.options.length;i++){if(sel.options[i].value===p.id){sel.selectedIndex=i;exists=true;break;}}
        if(!exists){var o=document.createElement('option');o.value=p.id;o.textContent=p.name+' ('+p.channels+'ch)';sel.appendChild(o);sel.value=p.id;}
      }
      if(el)el.innerHTML='<span style="color:#86efac">Selected: '+escapeHtml(p.name)+' ('+p.channels+'ch)</span>';
      _afPreviewChannels();
    }else{
      if(el)el.innerHTML='<span style="color:#f66">Import failed: '+(r&&r.err||'unknown')+'</span>';
    }
  });
}
function _submitAddFixture(){
  var ft=document.getElementById('aft').value;
  if(ft==='led'){
    var ip=document.getElementById('af-ip').value.trim();
    if(!ip){document.getElementById('hs').textContent='Enter an IP address';return;}
    document.getElementById('hs').textContent='Adding '+ip+' (probing device...)';
    closeModal();
    ra('POST','/api/children',{ip:ip},function(r){
      if(r&&r.ok){
        // Auto-create fixture for this child
        var cid=r.id;
        var cname=r.name||r.hostname||ip;
        ra('POST','/api/fixtures',{name:cname,fixtureType:'led',type:'linear',childId:cid},function(fr){
          document.getElementById('hs').textContent=r.duplicate?'Already registered':'Added LED fixture: '+cname;
          setTimeout(loadSetup,2000);
        });
      }else{
        document.getElementById('hs').textContent='Add failed: '+(r&&r.err||'unknown');
        setTimeout(loadSetup,1000);
      }
    });
  }else if(ft==='group'){
    var gname=document.getElementById('af-gname').value.trim();
    if(!gname){document.getElementById('hs').textContent='Enter a group name';return;}
    var cbs=document.querySelectorAll('.af-member-cb:checked');
    var memberIds=[];cbs.forEach(function(cb){memberIds.push(parseInt(cb.value));});
    if(!memberIds.length){document.getElementById('hs').textContent='Select at least one member';return;}
    closeModal();
    ra('POST','/api/fixtures',{name:gname,fixtureType:'led',type:'group',childIds:memberIds},function(r){
      if(r&&r.ok)document.getElementById('hs').textContent='Created group: '+gname+' ('+memberIds.length+' members)';
      else document.getElementById('hs').textContent='Failed: '+(r&&r.err||'unknown');
      loadSetup();
    });
  }else if(ft==='camera'){
    // Cameras are added from discover results, not via form submit
    document.getElementById('hs').textContent='Use Discover or Probe to find a camera first';
    return;
  }else if(ft==='gyro'){
    var gname=document.getElementById('af-gyro-name').value.trim();
    var gcid=parseInt(document.getElementById('af-gyro-child').value)||null;
    var gmid=parseInt(document.getElementById('af-gyro-mover').value)||null;
    closeModal();
    ra('POST','/api/fixtures',{
      name:gname||'Gyro Controller',fixtureType:'gyro',type:'point',
      gyroChildId:gcid,assignedMoverId:gmid
    },function(r){
      if(r&&r.ok)document.getElementById('hs').textContent='Added gyro controller: '+(gname||'Gyro Controller');
      else document.getElementById('hs').textContent='Add failed: '+(r&&r.err||'unknown');
      loadSetup();
    });
    return;
  }else{
    var name=document.getElementById('af-name').value.trim();
    var uni=parseInt(document.getElementById('af-uni').value)||1;
    var addr=parseInt(document.getElementById('af-addr').value)||1;
    var ch=parseInt(document.getElementById('af-ch').value)||3;
    // #515 — remember last-used universe for this session.
    window._lastDmxUniverse=uni;
    // Warn if patch overflows or clashes before POST (overrideable).
    if(addr+ch-1>512){
      if(!confirm('This fixture occupies addresses '+addr+'–'+(addr+ch-1)+' which exceeds the 512-slot universe. Add anyway?'))return;
    }
    var occ=_afOccupiedAddrSet();
    var clashNames=[];
    for(var _k=0;_k<ch;_k++){
      var _name=occ[addr+_k];
      if(_name&&clashNames.indexOf(_name)<0)clashNames.push(_name);
    }
    if(clashNames.length){
      if(!confirm('Address range '+addr+'–'+(addr+ch-1)+' overlaps with: '+clashNames.join(', ')+'. Add anyway?'))return;
    }
    var geom=document.getElementById('af-geom').value;
    var prof=document.getElementById('af-prof').value;
    if(addr<1||addr>512){document.getElementById('hs').textContent='Address must be 1–512';return;}
    closeModal();
    var body={name:name||('DMX U'+uni+' @'+addr),fixtureType:'dmx',type:geom,
      dmxUniverse:uni,dmxStartAddr:addr,dmxChannelCount:ch};
    if(prof){body.dmxProfileId=prof;}
    ra('POST','/api/fixtures',body,function(r){
      if(r&&r.ok){
        document.getElementById('hs').textContent='Added DMX fixture: '+(name||('U'+uni+' @'+addr));
      }else{
        document.getElementById('hs').textContent='Add failed: '+(r&&r.err||'unknown');
      }
      loadSetup();
    });
  }
}

function discoverChildren(){
  var btn=document.getElementById('disc-btn');
  if(btn){btn.disabled=true;btn.textContent='Scanning...';}
  ra('GET','/api/children/discover',null,function(){
    // Poll for results
    var poll=setInterval(function(){
      ra('GET','/api/children/discover/results',null,function(d){
        if(d&&d.pending)return;
        clearInterval(poll);
        if(btn){btn.disabled=false;btn.textContent='Discover';}
        var el=document.getElementById('disc-results');
        if(!el)return;
        if(!d||!d.length){
          el.innerHTML='<p style="color:#888;font-size:.85em;padding:.3em 0">No new devices found on network.</p>';
          el.style.display='block';return;
        }
        var h='<p style="color:#aaa;font-size:.85em;margin-bottom:.4em">Found — click Add to register:</p>'
          +'<table class="tbl" style="max-width:800px"><tr><th>Hostname</th><th>Name</th><th>IP</th><th>Type</th><th>Strings</th><th></th></tr>';
        d.forEach(function(c){
          // Prefer the typed code (`c.type` = "gyro"/"dmx"/"camera"/"slyled")
          // and fall back to boardType for DMX/camera which use codes there.
          var bt=c.type||c.boardType||'slyled';
          var badge=bt==='dmx'?'<span class="badge" style="background:#7c3aed;color:#fff;font-size:.75em">DMX Bridge</span>'
            :bt==='camera'?'<span class="badge" style="background:#0e7490;color:#fff;font-size:.75em">Camera</span>'
            :bt==='gyro'?'<span class="badge" style="background:#a5b4fc;color:#1e1b4b;font-size:.75em">Gyro</span>'
            :'<span class="badge" style="background:#059669;color:#fff;font-size:.75em">LED</span>';
          var addBtn;
          if(bt==='camera'){
            addBtn='<button class="btn" onclick="addDiscoveredCamera(\''+c.ip+'\',\''+escapeHtml(c.hostname||c.name||'Camera').replace(/'/g,"\\'")+
              '\')" style="background:#0e7490;color:#fff">Add</button>';
          }else{
            addBtn='<button class="btn btn-on" onclick="addDiscovered(\''+c.ip+'\')">Add</button>';
          }
          var detail=bt==='camera'?(c.resolutionW+'x'+c.resolutionH)
                    :bt==='dmx'||bt==='gyro'?'\u2014'
                    :c.sc;
          h+='<tr><td>'+escapeHtml(c.hostname)+'</td><td>'+escapeHtml(c.name||'-')+'</td><td>'+escapeHtml(c.ip)+'</td><td>'+badge+'</td><td>'+detail+'</td>'
            +'<td>'+addBtn+'</td></tr>';
        });
        el.innerHTML=h+'</table>';
        el.style.display='block';
      });
    },500);
  });
}

function addDiscoveredCamera(ip,name){
  document.getElementById('hs').textContent='Adding camera '+name+' at '+ip+'...';
  ra('POST','/api/cameras',{ip:ip,name:name},function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Added camera: '+name;
      closeModal();
      setTimeout(loadSetup,1000);
    }else{
      document.getElementById('hs').textContent='Add failed: '+(r&&r.err||'unknown');
      setTimeout(loadSetup,1000);
    }
  });
}

function _afCamDiscover(){
  var btn=document.getElementById('af-cam-disc-btn');
  var st=document.getElementById('af-cam-disc-status');
  if(btn){btn.disabled=true;btn.textContent='Scanning...';}
  if(st)st.textContent='Scanning all subnets...';
  ra('GET','/api/cameras/discover',null,function(){
    var poll=setInterval(function(){
      ra('GET','/api/cameras/discover/results',null,function(d){
        if(d&&d.pending)return;
        clearInterval(poll);
        if(btn){btn.disabled=false;btn.textContent='Discover Cameras';}
        if(st)st.textContent='';
        var el=document.getElementById('af-cam-results');
        if(!el)return;
        if(!d||!d.length){
          el.innerHTML='<p style="color:#888;font-size:.85em;padding:.3em 0">No new cameras found on network.</p>';
          return;
        }
        _afCamRenderResults(el,d);
      });
    },500);
  });
}

function _afCamRenderResults(el,cams){
  var h='<table class="tbl" style="font-size:.85em;margin-top:.3em"><tr><th>Name</th><th>IP</th><th>FOV</th><th>Resolution</th><th></th></tr>';
  cams.forEach(function(c){
    var name=c.hostname||c.name||'Camera';
    h+='<tr><td><b>'+escapeHtml(name)+'</b></td>'
      +'<td>'+escapeHtml(c.ip)+'</td>'
      +'<td>'+(c.fovDeg||60)+'\u00b0</td>'
      +'<td>'+(c.resolutionW||'?')+'x'+(c.resolutionH||'?')+'</td>'
      +'<td><button class="btn" onclick="addDiscoveredCamera(\''+c.ip+'\',\''+escapeHtml(name).replace(/'/g,"\\'")+'\')" style="background:#0e7490;color:#fff;font-size:.82em;padding:.2em .6em">Add</button>'
      +'</td></tr>';
  });
  el.innerHTML=h+'</table>';
}

function _afCamProbe(){
  var ip=(document.getElementById('af-cam-ip').value||'').trim();
  if(!ip){document.getElementById('hs').textContent='Enter an IP address';return;}
  var el=document.getElementById('af-cam-probe-result');
  if(el)el.innerHTML='<span style="color:#64748b;font-size:.82em">Probing '+escapeHtml(ip)+'...</span>';
  ra('POST','/api/cameras/probe',{ip:ip},function(r){
    if(!el)return;
    if(r&&r.ok){
      _afCamRenderResults(el,[r.info]);
    }else{
      el.innerHTML='<span style="color:#ef4444;font-size:.82em">No camera found at '+escapeHtml(ip)+'</span>';
    }
  });
}

function addDiscovered(ip){
  document.getElementById('hs').textContent='Adding '+ip+' (probing device...)';
  ra('POST','/api/children',{ip:ip},function(r){
    if(!r||!r.ok){
      document.getElementById('hs').textContent='Add failed: '+(r&&r.err||'unknown');
      setTimeout(loadSetup,1000);
      return;
    }
    var cid=r.id;
    var cname=r.name||r.hostname||ip;
    var ctype=r.type||'';
    if(ctype==='dmx'){
      document.getElementById('hs').textContent='Added DMX bridge: '+cname;
      setTimeout(loadSetup,1000);
    }else if(ctype==='camera'){
      // Camera node — register via /api/cameras (probes sensors, creates
      // one fixtureType:"camera" fixture per sensor with FOV/resolution).
      var camIp=r.ip||ip;
      ra('POST','/api/cameras',{ip:camIp,name:cname},function(cr){
        if(cr&&cr.ok){
          var n=cr.count||1;
          document.getElementById('hs').textContent='Added camera node: '+cname+' ('+n+' sensor'+(n===1?'':'s')+')';
        }else{
          document.getElementById('hs').textContent='Camera register failed: '+((cr&&cr.err)||'unknown');
        }
        setTimeout(loadSetup,1500);
      });
    }else if(ctype==='gyro'){
      // Gyro puck — idempotent: if a gyro fixture already exists for this
      // child id, don't spawn a duplicate.
      var existing=(_fixtures||[]).find(function(f){return f.fixtureType==='gyro'&&f.gyroChildId===cid;});
      if(existing){
        document.getElementById('hs').textContent='Gyro already registered: '+cname;
        setTimeout(loadSetup,1000);
        return;
      }
      ra('POST','/api/fixtures',{name:cname,fixtureType:'gyro',type:'point',gyroChildId:cid,gyroEnabled:false},function(){
        document.getElementById('hs').textContent='Added gyro fixture: '+cname;
        setTimeout(loadSetup,2000);
      });
    }else{
      // LED fixture — auto-create fixture
      ra('POST','/api/fixtures',{name:cname,fixtureType:'led',type:'linear',childId:cid},function(){
        document.getElementById('hs').textContent='Added LED fixture: '+cname;
        setTimeout(loadSetup,2000);
      });
    }
  });
}

function discoverCameras(btn){
  if(btn){btn.disabled=true;btn.textContent='Scanning...';}
  ra('GET','/api/cameras/discover',null,function(){
    var poll=setInterval(function(){
      ra('GET','/api/cameras/discover/results',null,function(d){
        if(d&&d.pending)return;
        clearInterval(poll);
        if(btn){btn.disabled=false;btn.textContent='Discover';}
        var el=document.getElementById('cam-disc-results');
        if(!el)return;
        if(!d||!d.length){
          el.innerHTML='<p style="color:#888;font-size:.85em;padding:.3em 0">No new cameras found on network.</p>';
          el.style.display='block';return;
        }
        var h='<p style="color:#22d3ee;font-size:.85em;margin-bottom:.4em">Found cameras — click Add to register:</p>'
          +'<table class="tbl" style="max-width:600px"><tr><th>Name</th><th>IP</th><th>FOV</th><th></th></tr>';
        d.forEach(function(c){
          h+='<tr><td>'+escapeHtml(c.hostname||c.name||'Camera')+'</td><td>'+escapeHtml(c.ip)+'</td><td>'+(c.fovDeg||'?')+'&deg;</td>'
            +'<td><button class="btn" onclick="registerCamera(\''+c.ip+'\',\''+escapeHtml(c.hostname||'').replace(/'/g,"\\'")+'\')" style="background:#0e7490;color:#fff">Add</button></td></tr>';
        });
        el.innerHTML=h+'</table>';
        el.style.display='block';
      });
    },500);
  });
}

function _removeCameraNode(fixtureIds,name){
  if(!confirm('Remove camera node "'+name+'" and all '+fixtureIds.length+' sensor(s)?'))return;
  var remaining=fixtureIds.length;
  fixtureIds.forEach(function(fid){
    ra('DELETE','/api/cameras/'+fid,null,function(){
      remaining--;
      if(remaining<=0){loadSetup();document.getElementById('hs').textContent='Removed '+name;}
    });
  });
}

function registerCamera(ip,name){
  document.getElementById('hs').textContent='Registering camera at '+ip+'...';
  ra('POST','/api/cameras',{ip:ip,name:name||undefined},function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Registered camera: '+(name||ip);
    }else{
      document.getElementById('hs').textContent='Register failed: '+(r&&r.err||'unknown');
    }
    loadSetup();
  });
}

function _camSnap(fid){
  var row=document.getElementById('cam-snap-row-'+fid);
  var el=document.getElementById('cam-snap-'+fid);
  if(!row||!el)return;
  row.style.display='';
  el.innerHTML='<span style="color:#94a3b8;font-size:.82em">Capturing...</span>';
  var fix=(_fixtures||[]).find(function(f){return f.id===fid;});
  var camIdx=fix?fix.cameraIdx||0:0;
  var x=new XMLHttpRequest();
  x.open('GET','/api/cameras/'+fid+'/snapshot?cam='+camIdx);
  x.responseType='blob';
  x.onload=function(){
    if(x.status===200&&x.response&&x.response.size>0){
      var img=document.createElement('img');
      img.src=URL.createObjectURL(x.response);
      img.style.cssText='max-width:100%;border-radius:4px;border:1px solid #334155';
      var ts=document.createElement('div');
      ts.style.cssText='color:#64748b;font-size:.75em;margin-top:.2em';
      ts.textContent='Captured at '+new Date().toLocaleTimeString();
      el.innerHTML='';
      el.appendChild(img);
      el.appendChild(ts);
    }else{
      el.innerHTML='<span style="color:#fca5a5;font-size:.82em">Capture failed ('+x.status+')</span>';
    }
  };
  x.onerror=function(){el.innerHTML='<span style="color:#fca5a5;font-size:.82em">Connection failed</span>';};
  x.send();
}

function removeCamera(id,name){
  if(!confirm('Remove camera "'+name+'"?'))return;
  document.getElementById('hs').textContent='Removing camera...';
  ra('DELETE','/api/cameras/'+id,null,function(){
    document.getElementById('hs').textContent='Removed camera: '+name;
    loadSetup();
  });
}

// ── #623 / #683 — per-camera settings + AI auto-tune wizard ───────────
//
// Redesign scope per issue #683: three-pane modal (controls | preview |
// actions), diff-patch updates, never close-and-reload. A singleton
// `_camTuneState` holds all the per-open state; the per-pane render
// helpers update only their own contents so drag / scroll / focus
// survive each server interaction.

var _camTuneState = null;  // {fid, controls, slots, evaluator, previewPaused,
                           //  activeSlot, baselineJpeg, finalJpeg, lastScore,
                           //  previewTimer, closeObserver}

function _camTune(fid){
  // Reset state + paint the shell on first open. Later re-opens reuse
  // the same shell nodes so per-pane renderers can diff-patch.
  _camTuneStop();
  _camTuneState = {fid:fid, controls:[], slots:{}, evaluator:{},
                    previewPaused:false, activeSlot:null,
                    baselineJpeg:null, finalJpeg:null, lastScore:null,
                    tuneRunning:false, iterationScores:[]};
  document.getElementById('modal-title').textContent='Camera settings — fixture '+fid;
  document.getElementById('modal-body').innerHTML=_camTuneShellHtml();
  // #685 — widen the .modal-box for the Tune modal's three-pane layout.
  // The class is removed in _camTuneStop() so the next non-tune modal
  // (e.g. fixture editor) gets the default 540 px width back.
  var box = document.querySelector('#modal .modal-box');
  if (box) box.classList.add('modal-tune');
  document.getElementById('modal').style.display='block';
  _camTuneInstallCloseHandler();
  _camTunePreviewStart(fid);
  _camTuneFetchState(fid, /*initial*/true);
}

function _camTuneShellHtml(){
  // #685 — width / grid-template-columns / responsive collapse all live
  // in `.modal-box.modal-tune .tune-modal` (app.css). The pane min-width
  // hints below are kept so the panes still feel comfortably wide on
  // desktop, but no inline `min-width:1100px` on the outer grid so the
  // CSS clamp + media query can shrink + reflow on narrow viewports.
  return ''
    +'<div class="tune-modal">'
    +'  <div id="tune-pane-controls" style="min-width:240px;max-height:72vh;overflow:auto;padding-right:.4em;border-right:1px solid #1e293b"><div style="color:#64748b">Loading…</div></div>'
    +'  <div id="tune-pane-preview" style="min-width:300px;display:flex;flex-direction:column;gap:.35em"></div>'
    +'  <div id="tune-pane-actions" style="min-width:240px;padding-left:.4em;border-left:1px solid #1e293b"><div style="color:#64748b">…</div></div>'
    +'</div>'
    +'<div style="margin-top:.6em;display:flex;justify-content:space-between;align-items:center">'
    +'  <div id="camtune-diag" style="font-size:.72em;color:#64748b"></div>'
    +'  <button class="btn" onclick="closeModal()">Close</button>'
    +'</div>';
}

function _camTuneFetchState(fid, initial){
  ra('GET','/api/cameras/'+fid+'/settings',null,function(r){
    if(!r||!r.ok){
      var cp=document.getElementById('tune-pane-controls');
      if(cp)cp.innerHTML='<div style="color:#f87171">'+escapeHtml((r&&r.err)||'Settings query failed')+'</div>';
      return;
    }
    _camTuneState.controls = r.controls||[];
    _camTuneState.slots = r.slots||{};
    _camTuneRenderControls();
    _camTuneRenderActionsShell();
    if(initial){
      ra('GET','/api/cameras/settings/evaluator-status',null,function(st){
        _camTuneState.evaluator = st||{modes:{heuristic:{available:true},ai:{available:false}}};
        _camTuneRenderActions();
      });
    }else{
      _camTuneRenderActions();
    }
    _camTuneRenderDiag();
  });
}

// ── Controls pane (V4L2 sliders) ────────────────────────────────────────
function _camTuneRenderControls(){
  var cp=document.getElementById('tune-pane-controls');
  if(!cp||!_camTuneState)return;
  var fid=_camTuneState.fid;
  var controls=_camTuneState.controls||[];
  var h='<div style="font-weight:bold;color:#e2e8f0;margin-bottom:.3em">V4L2 controls</div>'
     +'<table style="width:100%;border-collapse:collapse"><tbody id="tune-ctrl-tbody">';
  controls.forEach(function(c){
    var name=c.name||'';
    var val=c.value;
    var lo=c.min, hi=c.max, type=c.type||'int';
    var esc=escapeHtml(name);
    var inputId='vl-'+name;
    var revertAttr='title="Reset to '+escapeHtml(String(c['default']!==undefined?c['default']:val))+'"';
    var handler='onchange="_camTuneSliderChange(\''+name+'\', this)"';
    var input='';
    if(type==='bool'||(lo===0&&hi===1)){
      input='<input id="'+inputId+'" type="checkbox" '+(val?'checked':'')+' '+handler+'>';
    }else if(typeof lo==='number'&&typeof hi==='number'){
      input='<input id="'+inputId+'" type="range" min="'+lo+'" max="'+hi+'" step="'+(c.step||1)+'" value="'+val+'" style="width:130px" '
        +'oninput="document.getElementById(\''+inputId+'v\').textContent=this.value" '+handler+'>'
        +' <span id="'+inputId+'v" style="color:#94a3b8;font-family:monospace;font-size:.78em;display:inline-block;min-width:32px;text-align:right">'+val+'</span>';
    }else{
      input='<input id="'+inputId+'" type="text" value="'+(val!==undefined?val:'')+'" style="width:110px;font-size:.82em" '+handler+'>';
    }
    var revert=(c['default']!==undefined)?
      ' <button class="btn" style="padding:0 .35em;font-size:.72em;background:#1e293b;color:#94a3b8" '+revertAttr+' onclick="_camTuneRevert(\''+name+'\')">↺</button>':'';
    h+='<tr data-ctrl="'+esc+'"><td style="padding:.18em .4em;color:#cbd5e1;font-family:monospace;font-size:.78em">'+esc+'</td>'
       +'<td style="padding:.18em .4em">'+input+revert+'</td>'
       +'<td style="padding:.18em .4em;color:#64748b;font-size:.7em">'+(lo!==undefined?('['+lo+'‥'+hi+']'):'')+'</td></tr>';
  });
  h+='</tbody></table>';
  h+='<div style="margin-top:.5em;display:flex;gap:.35em;flex-wrap:wrap">'
   +'  <button class="btn" onclick="_camTuneResetAll()" style="background:#1e293b;color:#94a3b8;font-size:.78em">Reset all defaults</button>'
   +'</div>';
  cp.innerHTML=h;
}

function _camTuneSliderChange(name, el){
  if(!_camTuneState)return;
  var fid=_camTuneState.fid;
  var c=(_camTuneState.controls||[]).filter(function(x){return x.name===name;})[0];
  if(!c)return;
  var v=(c.type==='bool'||(c.min===0&&c.max===1))
        ?(el.checked?1:0)
        :parseInt(el.value, 10);
  if(isNaN(v))return;
  var body={controls:{}};
  body.controls[name]=v;
  _camTuneSetDiag('Writing '+name+'='+v+'…');
  ra('POST','/api/cameras/'+fid+'/settings',body,function(r){
    if(!r||!r.ok){
      _camTuneSetDiag('Write failed: '+((r&&r.err)||'unknown'), true);
      return;
    }
    // Reflect committed value in local state + diff-patch the single row.
    c.value=v;
    _camTuneSetDiag('Wrote '+name+'='+v);
    // Refresh score after a brief settle so preview reflects the change.
    setTimeout(function(){ _camTunePreviewTick(fid); }, 500);
  });
}

function _camTuneRevert(name){
  var c=(_camTuneState&&_camTuneState.controls||[]).filter(function(x){return x.name===name;})[0];
  if(!c||c['default']===undefined)return;
  var el=document.getElementById('vl-'+name);
  if(!el)return;
  var valEl=document.getElementById('vl-'+name+'v');
  if(c.type==='bool'||(c.min===0&&c.max===1)){
    el.checked=!!c['default'];
  }else{
    el.value=c['default'];
    if(valEl)valEl.textContent=c['default'];
  }
  _camTuneSliderChange(name, el);
}

function _camTuneResetAll(){
  if(!_camTuneState)return;
  if(!confirm('Reset every V4L2 control to its default value?'))return;
  var fid=_camTuneState.fid;
  var body={controls:{}};
  (_camTuneState.controls||[]).forEach(function(c){
    if(c['default']!==undefined)body.controls[c.name]=c['default'];
  });
  if(!Object.keys(body.controls).length)return;
  ra('POST','/api/cameras/'+fid+'/settings',body,function(r){
    if(!r||!r.ok){alert('Reset failed: '+((r&&r.err)||'unknown'));return;}
    _camTuneFetchState(fid, false);
    setTimeout(function(){ _camTunePreviewTick(fid); }, 500);
  });
}

// ── Preview pane (live canvas) ──────────────────────────────────────────
function _camTuneRenderPreviewShell(){
  var pp=document.getElementById('tune-pane-preview');
  if(!pp)return;
  pp.innerHTML=''
    +'<div style="font-weight:bold;color:#e2e8f0">Live preview <span id="camtune-preview-badge" style="font-size:.7em;color:#64748b;margin-left:.3em">polling 0.33 Hz</span></div>'
    +'<canvas id="camtune-canvas" width="640" height="360" style="width:100%;background:#000;border:1px solid #1e293b;border-radius:4px;display:block"></canvas>'
    +'<div style="display:flex;gap:.3em;align-items:center;font-size:.78em">'
    +'  <button class="btn" onclick="_camTunePreviewToggle()" id="camtune-pause-btn" style="padding:0 .4em;font-size:.78em;background:#1e293b;color:#cbd5e1">Pause</button>'
    +'  <button class="btn" onclick="_camTunePreviewSnap()" style="padding:0 .4em;font-size:.78em;background:#1e293b;color:#cbd5e1">Snap now</button>'
    +'  <span id="camtune-score" style="margin-left:auto;color:#94a3b8"></span>'
    +'</div>'
    +'<div id="camtune-compare" style="display:none;margin-top:.2em">'
    +'  <div style="font-size:.78em;color:#94a3b8;margin-bottom:.2em">Before / after</div>'
    +'  <div style="display:flex;gap:.3em">'
    +'    <div style="flex:1"><div style="font-size:.7em;color:#64748b">baseline <span id="camtune-compare-before-score"></span></div><img id="camtune-compare-before" style="width:100%;border:1px solid #1e293b;background:#000"></div>'
    +'    <div style="flex:1"><div style="font-size:.7em;color:#64748b">current <span id="camtune-compare-after-score"></span></div><img id="camtune-compare-after" style="width:100%;border:1px solid #1e293b;background:#000"></div>'
    +'  </div>'
    +'</div>';
}

function _camTunePreviewStart(fid){
  _camTuneRenderPreviewShell();
  _camTunePreviewTick(fid);
  // #685 follow-up — the preview poll competes with auto-tune's V4L2
  // capture; even with the per-camera lock the operator was hitting
  // capture-timeout toasts when the loop ran longer than 2 s per
  // iteration. Slow the cadence to 3 s and pause entirely while a tune
  // run is in flight; the loop already paints fresh before/after
  // thumbnails into the compare strip when it finishes so live preview
  // is just a "is the camera alive?" indicator during a run.
  _camTuneState.previewTimer=setInterval(function(){
    if(!_camTuneState)return;
    if(_camTuneState.previewPaused)return;
    if(_camTuneState.tuneRunning)return;
    _camTunePreviewTick(fid);
  }, 3000);
}

function _camTunePreviewStop(){
  if(_camTuneState&&_camTuneState.previewTimer){
    clearInterval(_camTuneState.previewTimer);
    _camTuneState.previewTimer=null;
  }
}

function _camTunePreviewToggle(){
  if(!_camTuneState)return;
  _camTuneState.previewPaused=!_camTuneState.previewPaused;
  var btn=document.getElementById('camtune-pause-btn');
  if(btn)btn.textContent=_camTuneState.previewPaused?'Resume':'Pause';
  var badge=document.getElementById('camtune-preview-badge');
  if(badge)badge.textContent=_camTuneState.previewPaused?'paused':'polling 0.33 Hz';
}

function _camTunePreviewSnap(){
  if(_camTuneState)_camTunePreviewTick(_camTuneState.fid);
}

// #685 — fetch() instead of <img>.src so a 503 with a JSON body can be
// read; the SPA then surfaces the typed errType (capture-busy /
// camera-unreachable / capture-timeout / capture-failed) instead of the
// generic "camera offline?" toast. capture-busy specifically is the
// expected mid-iteration race with auto-tune — show "device busy"
// rather than alarming the operator about an offline camera.
var _CAM_FETCH_HINTS = {
  'capture-busy':       'Device busy (auto-tune capturing) — preview will catch up',
  'capture-timeout':    'Capture timed out — camera node is slow or stuck',
  'camera-unreachable': 'Camera node unreachable on the network',
  'capture-failed':     'Camera capture failed',
  'not-found':          'Camera fixture missing',
  'not-configured':     'Camera fixture has no IP'
};

function _camTunePreviewTick(fid){
  var canvas=document.getElementById('camtune-canvas');
  if(!canvas)return;
  var url='/api/cameras/'+fid+'/snapshot?t='+Date.now();
  fetch(url, {cache:'no-store'}).then(function(r){
    if(!r.ok){
      // Server returned a typed JSON error envelope (#685).
      return r.json().then(function(j){
        var hint = _CAM_FETCH_HINTS[j&&j.errType] || (j&&j.err) || ('Snapshot HTTP '+r.status);
        // capture-busy is transient — log without screaming red.
        _camTuneSetDiag(hint, j&&j.errType!=='capture-busy');
      }, function(){
        _camTuneSetDiag('Snapshot HTTP '+r.status, true);
      });
    }
    return r.blob().then(function(blob){
      var src = URL.createObjectURL(blob);
      var img=new Image();
      img.onload=function(){
        var ctx=canvas.getContext('2d');
        var cw=canvas.clientWidth||canvas.width;
        var ch=cw * (img.height/img.width);
        if(canvas.width!==cw||canvas.height!==ch){
          canvas.width=cw; canvas.height=ch;
        }
        ctx.drawImage(img, 0, 0, cw, ch);
        if(_camTuneState){
          _camTuneState.lastJpegSrc=src;
          if(!_camTuneState.baselineJpeg)_camTuneState.baselineJpeg=src;
        }
        _camTuneRefreshScore(fid);
        // Free the previous tick's blob URL once the new one paints.
        // (No-op on first tick when prev is undefined.)
        if(_camTuneState && _camTuneState._prevBlobSrc){
          try{URL.revokeObjectURL(_camTuneState._prevBlobSrc);}catch(e){}
        }
        if(_camTuneState)_camTuneState._prevBlobSrc = src;
      };
      img.src=src;
    });
  }).catch(function(e){
    // Network-level fail (no response) — distinct from a typed 503.
    _camTuneSetDiag('Snapshot request failed (network)', true);
  });
}

// Compute a quick-and-dirty heuristic score client-side by asking the
// orchestrator to re-run its evaluator on the current frame. The server
// proxies this through `/api/cameras/<fid>/settings/evaluator-status?
// snapshot=1` if we add that; for now we show the last auto-tune run
// score (if any) and camera framing hints.
function _camTuneRefreshScore(fid){
  var el=document.getElementById('camtune-score');
  if(!el||!_camTuneState)return;
  var parts=[];
  if(_camTuneState.lastScore!=null)parts.push('last score '+_camTuneState.lastScore);
  parts.push('res '+(canvas()?canvas().width+'×'+canvas().height:'?'));
  el.textContent=parts.join(' · ');
  function canvas(){return document.getElementById('camtune-canvas');}
}

// ── Actions pane (intent + evaluator + slots + auto-tune) ───────────────
function _camTuneRenderActionsShell(){
  // Nothing heavy — _camTuneRenderActions builds the full contents.
}

function _camTuneRenderActions(){
  var ap=document.getElementById('tune-pane-actions');
  if(!ap||!_camTuneState)return;
  var fid=_camTuneState.fid;
  var slots=_camTuneState.slots||{};
  var ai=(_camTuneState.evaluator&&_camTuneState.evaluator.modes&&_camTuneState.evaluator.modes.ai)||{};
  var aiOk=!!ai.available;
  var aiLabel='Local VLM ('+escapeHtml(ai.model||'qwen2.5vl:3b')+')';
  var aiOptDisabled=aiOk?'':'disabled';
  var aiHint=aiOk
    ?'<span style="color:#86efac">✓ Available</span>'
    :'<span style="color:#fbbf24">Unavailable:</span> <span style="color:#94a3b8">'+escapeHtml(ai.err||'install via Settings → AI Runtime')+'</span>';
  var activeSlot=_camTuneState.activeSlot;
  var slotRows='';
  var slotNames=Object.keys(slots);
  if(!slotNames.length){
    slotRows='<div style="color:#64748b;font-size:.8em;padding:.3em 0">No slots saved. Run Auto-Tune with "Save as slot" or click "Save current as slot" below.</div>';
  }else{
    slotNames.forEach(function(name){
      var s=slots[name];
      var active=(name===activeSlot);
      slotRows+='<div style="display:flex;align-items:center;gap:.3em;padding:.2em .3em;border-radius:3px;'
             +(active?'background:#0f172a;border:1px solid #334155':'')
             +'">'
             +'  <span style="flex:1">'+(active?'● ':'○ ')+'<b>'+escapeHtml(name)+'</b>'
             +'    <span style="color:#64748b;font-size:.72em;margin-left:.4em">'+escapeHtml(s.intent||'general')+(s.score!=null?(' · '+s.score):'')+'</span>'
             +'  </span>'
             +'  <button class="btn" style="padding:0 .35em;font-size:.72em;background:'+(active?'#1e3a5f':'#0e7490')+';color:'+(active?'#94a3b8':'#fff')+'" onclick="_camTuneActivateSlot('+fid+',\''+name.replace(/\'/g,"\\'")+'\')">'+(active?'Active':'Activate')+'</button>'
             +'  <button class="btn" style="padding:0 .35em;font-size:.72em;background:#431d1d;color:#fca5a5" title="Delete slot" onclick="_camTuneDeleteSlot('+fid+',\''+name.replace(/\'/g,"\\'")+'\')">×</button>'
             +'</div>';
    });
  }

  ap.innerHTML=''
    +'<div style="font-weight:bold;color:#e2e8f0;margin-bottom:.3em">Auto-tune</div>'
    +'<label style="font-size:.82em;color:#94a3b8">Intent'
    +'  <select id="camtune-intent" style="width:100%;font-size:.82em;margin-top:.15em">'
    +'    <option value="general">General</option>'
    +'    <option value="beam">Beam detection</option>'
    +'    <option value="aruco">ArUco markers</option>'
    +'    <option value="yolo">YOLO objects</option>'
    +'  </select></label>'
    +'<label style="font-size:.82em;color:#94a3b8;display:block;margin-top:.4em">Evaluator'
    +'  <select id="camtune-eval" style="width:100%;font-size:.82em;margin-top:.15em">'
    +'    <option value="analyzer" selected>Analyzer (CV — deterministic, default)</option>'
    +'    <option value="heuristic">Heuristic (histogram only — gating)</option>'
    +'    <option value="ai" '+aiOptDisabled+'>'+aiLabel+'</option>'
    +'    <option value="auto">Auto (analyzer → AI if configured)</option>'
    +'  </select></label>'
    +'<div style="font-size:.72em;margin-top:.2em">'+aiHint+'</div>'
    +'<label style="font-size:.82em;color:#94a3b8;display:block;margin-top:.4em">Max iterations'
    +'  <input id="camtune-iter" type="number" min="1" max="12" value="6" style="width:60px;font-size:.82em;margin-left:.4em"></label>'
    // #685 follow-up — VLM input resolution preset. Smaller = faster
    // inference (qwen2.5vl:3b on CPU is ~3 s @ 320 px vs ~30 s @ 4 K),
    // larger = more detail for ArUco / fine-focus intents. Resolves
    // to absolute pixel max-side; the orchestrator clamps server-side
    // so a malformed request can't ask the VLM to ingest a 4 K frame.
    +'<label style="font-size:.82em;color:#94a3b8;display:block;margin-top:.4em">VLM input size <span id="camtune-resize-hint" style="color:#64748b;font-size:.78em"></span>'
    +'  <select id="camtune-resize" onchange="_camTuneResizeHintUpdate('+fid+')" style="width:100%;font-size:.82em;margin-top:.15em">'
    +'    <option value="320">Tiny (320 px) — fastest, ~3 s/iter</option>'
    +'    <option value="640" selected>Standard (640 px) — balanced (default)</option>'
    +'    <option value="960">Detailed (960 px) — slower, more precision</option>'
    +'  </select></label>'
    +'<label style="font-size:.82em;color:#94a3b8;display:block;margin-top:.4em">Save result as slot'
    +'  <input id="camtune-autosave" type="text" placeholder="optional" style="width:100%;font-size:.82em;margin-top:.15em"></label>'
    +'<div id="camtune-run-row" style="display:flex;gap:.4em;margin-top:.6em">'
    +'  <button id="camtune-run-btn" class="btn btn-on" onclick="_camTuneRun('+fid+')" style="flex:1;background:#6b21a8;color:#d8b4fe">Run Auto-Tune</button>'
    +'  <button id="camtune-cancel-btn" class="btn" onclick="_camTuneCancel('+fid+')" style="display:none;background:#7f1d1d;color:#fecaca">Cancel</button>'
    +'</div>'
    +'<div id="camtune-progress" style="margin-top:.4em;font-size:.78em;color:#64748b;min-height:1em"></div>'
    // #685 follow-up — scrollable log pane mirroring the cal wizard.
    // Polled from /api/cameras/<fid>/settings/auto-tune/status while a
    // run is in flight (see _camTuneLogPoll). Auto-scrolls to bottom on
    // new entries; pauses auto-scroll when the operator scrolls up.
    +'<div id="camtune-log" onscroll="_camTuneLogTrackScroll()" '
    +'  style="margin-top:.4em;max-height:200px;overflow-y:auto;'
    +'  background:#020617;border:1px solid #1e293b;border-radius:4px;'
    +'  padding:.4em;font-family:monospace;font-size:.72em;color:#cbd5e1;'
    +'  line-height:1.4em"></div>'
    +'<hr style="border:none;border-top:1px solid #1e293b;margin:.6em 0">'
    +'<div style="font-weight:bold;color:#e2e8f0;margin-bottom:.25em">Saved slots</div>'
    +'<div id="camtune-slots">'+slotRows+'</div>'
    +'<div style="margin-top:.4em;display:flex;gap:.25em">'
    +'  <input id="camtune-save-name" type="text" placeholder="slot name…" style="flex:1;font-size:.78em;padding:.15em .3em">'
    +'  <button class="btn" onclick="_camTuneSaveCurrent('+fid+')" style="background:#1e3a5f;color:#93c5fd;font-size:.78em">Save current</button>'
    +'</div>';
  // #685 follow-up — paint the resize-hint after the select renders so
  // the operator sees the ratio relative to the camera's native res.
  setTimeout(function(){_camTuneResizeHintUpdate(_camTuneState.fid);}, 0);
}

function _camTuneResizeHintUpdate(fid){
  var hint = document.getElementById('camtune-resize-hint');
  var sel = document.getElementById('camtune-resize');
  if(!hint || !sel)return;
  var px = parseInt(sel.value, 10) || 640;
  // Walk _fixtures for native resolution.
  var native = null;
  if(typeof _fixtures !== 'undefined'){
    _fixtures.forEach(function(f){
      if(f.id===fid && f.resolutionW && f.resolutionH){
        native = {w:f.resolutionW, h:f.resolutionH};
      }
    });
  }
  if(!native){hint.textContent = '(native res unknown)';return;}
  var longSide = Math.max(native.w, native.h);
  var ratio = px / longSide;
  hint.textContent = '(native '+native.w+'×'+native.h+'; '
    + Math.round(ratio*100) + '% of native long side)';
}

// ── Slot operations (diff-patched — never replace modal body) ──────────
function _camTuneActivateSlot(fid, name){
  _camTuneSetDiag('Activating slot '+name+'…');
  ra('POST','/api/cameras/'+fid+'/settings/slots/'+encodeURIComponent(name)+'/activate',{},function(r){
    if(!r||!r.ok){_camTuneSetDiag('Activate failed: '+((r&&r.err)||'unknown'), true);return;}
    if(_camTuneState)_camTuneState.activeSlot=name;
    _camTuneFetchState(fid, false);
    setTimeout(function(){ _camTunePreviewTick(fid); }, 400);
  });
}

function _camTuneDeleteSlot(fid, name){
  if(!confirm('Delete slot "'+name+'"?'))return;
  ra('DELETE','/api/cameras/'+fid+'/settings/slots/'+encodeURIComponent(name),null,function(r){
    if(!r||!r.ok){alert('Delete failed: '+((r&&r.err)||'unknown'));return;}
    if(_camTuneState){
      delete _camTuneState.slots[name];
      if(_camTuneState.activeSlot===name)_camTuneState.activeSlot=null;
    }
    _camTuneRenderActions();
  });
}

function _camTuneSaveCurrent(fid){
  var el=document.getElementById('camtune-save-name');
  var name=(el&&el.value||'').trim();
  if(!name){alert('Enter a slot name.');return;}
  // Reuse /settings POST with slot=name but no controls → server sets
  // only the slot metadata. Fall through to auto-tune save path if
  // that endpoint doesn't accept empty controls.
  var body={controls:{}, slot:name};
  // Gather current values as the "slot" contents.
  (_camTuneState.controls||[]).forEach(function(c){
    body.controls[c.name]=c.value;
  });
  ra('POST','/api/cameras/'+fid+'/settings',body,function(r){
    if(!r||!r.ok){alert('Save slot failed: '+((r&&r.err)||'unknown'));return;}
    if(el)el.value='';
    _camTuneFetchState(fid, false);
  });
}

// ── Auto-tune run (baseline → execute → compare) ───────────────────────

// #685 follow-up — disable the Run button while a tune is running and
// expose a Cancel sibling. Centralised so both onload + onerror +
// ontimeout + cancel paths can call it without duplicating selectors.
function _camTuneSetRunUiBusy(busy, statusText){
  var run=document.getElementById('camtune-run-btn');
  var cancel=document.getElementById('camtune-cancel-btn');
  if(run){
    run.disabled=!!busy;
    run.style.opacity=busy?'0.55':'1';
    run.style.cursor=busy?'not-allowed':'pointer';
  }
  if(cancel){
    cancel.style.display=busy?'inline-block':'none';
  }
  if(statusText!=null){
    var prog=document.getElementById('camtune-progress');
    if(prog)prog.textContent=statusText;
  }
}

function _camTuneRun(fid){
  if(!_camTuneState)return;
  if(_camTuneState.tuneRunning)return;  // #685 — guard against double-click.
  var intent=(document.getElementById('camtune-intent')||{}).value||'general';
  var evaluator=(document.getElementById('camtune-eval')||{}).value||'analyzer';
  var maxIt=parseInt((document.getElementById('camtune-iter')||{}).value, 10)||6;
  // #685 follow-up — VLM resize preset (Tiny / Standard / Detailed).
  // Server clamps to [160, 1280] so a malformed value can't pass through.
  var resizeLongSide=parseInt((document.getElementById('camtune-resize')||{}).value, 10)||640;
  var slot=((document.getElementById('camtune-autosave')||{}).value||'').trim();
  var prog=document.getElementById('camtune-progress');
  // Capture baseline snapshot for the compare strip.
  _camTuneState.baselineJpeg='/api/cameras/'+fid+'/snapshot?t=base-'+Date.now();
  var baselineImg=document.getElementById('camtune-compare-before');
  if(baselineImg)baselineImg.src=_camTuneState.baselineJpeg;
  var compare=document.getElementById('camtune-compare');
  if(compare)compare.style.display='block';
  _camTuneState.tuneRunning=true;
  _camTuneSetRunUiBusy(true);

  function _kickRun(){
    _camTuneSetRunUiBusy(true, 'Running auto-tune ('+evaluator+' → '+intent+')… this may take 10–90 s.');
    var body={intent:intent, evaluator:evaluator, maxIterations:maxIt,
              resizeLongSide:resizeLongSide};
    if(slot)body.saveSlot=slot;
    var x=new XMLHttpRequest();
    x.open('POST','/api/cameras/'+fid+'/settings/auto-tune', true);
    _camTuneState.tuneXhr=x;  // #685 — cancel hook abandons this XHR.
    // #685 follow-up — start the log-pane poller. Stops itself once
    // the XHR resolves (see _camTuneRunXhr below).
    _camTuneLogStart(fid);
    _camTuneRunXhr(x, fid, prog, body);
  }
  function _runWithFreshStatus(){
    ra('GET','/api/cameras/settings/evaluator-status',null,function(st){
      _camTuneState.evaluator = st || _camTuneState.evaluator || {modes:{}};
      // #685 follow-up — DON'T re-render the actions pane mid-run; that
      // wipes our Cancel button + progress strip. The evaluator-status
      // refresh is enough to catch a stale "AI cold" state; the next
      // open of the modal will paint the full pane.
      _kickRun();
    });
  }
  if(evaluator==='ai' || evaluator==='auto'){
    // #685 follow-up — skip the warmup call when /api/ollama-runtime
    // already reports `warm: true`. Operators who tested AI on the
    // Settings tab moments ago shouldn't sit through another 30 s
    // warmup just to start auto-tune.
    ra('GET','/api/ollama-runtime/status',null,function(st){
      if(st && st.warm && st.installed){
        _runWithFreshStatus();
        return;
      }
      _camTuneSetRunUiBusy(true, 'Warming up the vision model… (cold-start can take 30 s)');
      var w=new XMLHttpRequest();
      w.open('POST','/api/ollama-runtime/warmup', true);
      w.timeout=60*1000;
      w.onload=function(){_runWithFreshStatus();};
      w.onerror=function(){_runWithFreshStatus();};
      w.ontimeout=function(){_runWithFreshStatus();};
      _camTuneState.tuneXhr=w;  // cancel-able during warmup window
      w.send();
    });
    return;
  }
  // Heuristic evaluator path — no warmup needed.
  _kickRun();
}

// #685 follow-up — best-effort cancel. The server's auto-tune route is
// synchronous in the request thread so the underlying loop keeps going
// until the next iteration check; we POST a cancel hint AND abandon the
// XHR client-side so the SPA returns to the run-able state immediately.
function _camTuneCancel(fid){
  if(!_camTuneState||!_camTuneState.tuneRunning)return;
  if(_camTuneState.tuneXhr){
    try{_camTuneState.tuneXhr.abort();}catch(e){}
    _camTuneState.tuneXhr=null;
  }
  // Tell the server too — the iteration loop checks for cancel between
  // iterations so it can exit cleanly and unlock the camera device.
  var x=new XMLHttpRequest();
  x.open('POST','/api/cameras/'+fid+'/settings/auto-tune/cancel', true);
  x.send();
  _camTuneState.tuneRunning=false;
  _camTuneSetRunUiBusy(false, 'Cancelled.');
}

function _camTuneRunXhr(x, fid, prog, body){
  x.timeout=5*60*1000;
  x.setRequestHeader('Content-Type','application/json');
  x.onload=function(){
    _camTuneState.tuneRunning=false;
    _camTuneState.tuneXhr=null;
    _camTuneSetRunUiBusy(false);
    var r=null; try{r=JSON.parse(x.responseText);}catch(e){}
    if(!r||r.err){
      if(prog)prog.innerHTML='<span style="color:#f87171">'+escapeHtml((r&&r.err)||'Auto-tune failed (HTTP '+x.status+')')+'</span>';
      return;
    }
    var before=(r.before||{}).score;
    var after=(r.after||{}).score;
    var iters=(r.history||[]).length-1;
    _camTuneState.lastScore=after;
    if(prog)prog.innerHTML='<span style="color:#86efac">✓ '+escapeHtml('Before '+before+' → after '+after+' ('+iters+' iters, '+(r.evaluator||'heuristic')+')')+'</span>';
    var beforeScoreEl=document.getElementById('camtune-compare-before-score');
    if(beforeScoreEl)beforeScoreEl.textContent=before!=null?('score '+before):'';
    var afterImg=document.getElementById('camtune-compare-after');
    if(afterImg)afterImg.src='/api/cameras/'+fid+'/snapshot?t=final-'+Date.now();
    var afterScoreEl=document.getElementById('camtune-compare-after-score');
    if(afterScoreEl)afterScoreEl.textContent=after!=null?('score '+after):'';
    // Diff-patch the controls pane so the post-tune V4L2 values show up
    // in the sliders — no modal close/reload.
    _camTuneFetchState(fid, false);
  };
  x.onerror=function(){
    _camTuneState.tuneRunning=false;
    _camTuneState.tuneXhr=null;
    _camTuneSetRunUiBusy(false);
    if(prog)prog.innerHTML='<span style="color:#f87171">Auto-tune network error</span>';
  };
  x.ontimeout=function(){
    _camTuneState.tuneRunning=false;
    _camTuneState.tuneXhr=null;
    _camTuneSetRunUiBusy(false);
    if(prog)prog.innerHTML='<span style="color:#f87171">Auto-tune timed out (5 min cap)</span>';
  };
  x.onabort=function(){
    // Cancel button path; UI already cleaned up.
  };
  x.send(JSON.stringify(body));
}

// ── Close lifecycle (stop preview poll) ─────────────────────────────────
function _camTuneInstallCloseHandler(){
  var modal=document.getElementById('modal');
  if(!modal||!_camTuneState)return;
  if(_camTuneState.closeObserver)_camTuneState.closeObserver.disconnect();
  _camTuneState.closeObserver=new MutationObserver(function(){
    if(modal.style.display==='none'||!modal.style.display){
      _camTuneStop();
    }
  });
  _camTuneState.closeObserver.observe(modal, {attributes:true, attributeFilter:['style']});
}

function _camTuneStop(){
  if(!_camTuneState)return;
  _camTunePreviewStop();
  if(_camTuneState.closeObserver){
    try{_camTuneState.closeObserver.disconnect();}catch(e){}
  }
  // #685 follow-up — also stop the log poller so the modal close
  // doesn't leak a setTimeout chain.
  _camTuneLogStop();
  // #685 — restore default modal-box width for the next modal opener.
  var box = document.querySelector('#modal .modal-box');
  if (box) box.classList.remove('modal-tune');
  _camTuneState=null;
}

// ── #685 follow-up — auto-tune log pane ────────────────────────────────
// Polls /api/cameras/<fid>/settings/auto-tune/status at 1 Hz while a tune
// is running. Sends `since=<count>` so each poll only ships new entries.
// Auto-scrolls to bottom unless the operator scrolled up to read older
// entries (tracked via _camTuneLogTrackScroll).

var _camTuneLogState = null;

function _camTuneLogStart(fid){
  _camTuneLogStop();
  var el = document.getElementById('camtune-log');
  if(el)el.innerHTML = '';
  _camTuneLogState = {
    fid: fid,
    since: 0,
    timer: null,
    autoScroll: true,
    lastDoneSeen: 0
  };
  _camTuneLogTick();
}

function _camTuneLogStop(){
  if(_camTuneLogState && _camTuneLogState.timer){
    clearTimeout(_camTuneLogState.timer);
    _camTuneLogState.timer = null;
  }
  _camTuneLogState = null;
}

function _camTuneLogTick(){
  if(!_camTuneLogState)return;
  var fid = _camTuneLogState.fid;
  var since = _camTuneLogState.since;
  var url = '/api/cameras/'+fid+'/settings/auto-tune/status?since='+since;
  fetch(url, {cache:'no-store'}).then(function(r){return r.json();}).then(function(j){
    if(!_camTuneLogState)return;
    var entries = (j && j.log) || [];
    if(entries.length){
      _camTuneLogAppend(entries);
      _camTuneLogState.since = j.total || (since + entries.length);
    }
    // Stop polling once status moves out of `running`. Give one extra
    // tick so the trailing log entries (final-score / completed) land.
    if(j && j.status && j.status !== 'running'){
      _camTuneLogState.lastDoneSeen += 1;
      if(_camTuneLogState.lastDoneSeen > 1){
        _camTuneLogStop();
        return;
      }
    }
    _camTuneLogState.timer = setTimeout(_camTuneLogTick, 1000);
  }).catch(function(){
    if(!_camTuneLogState)return;
    _camTuneLogState.timer = setTimeout(_camTuneLogTick, 2000);
  });
}

function _camTuneLogAppend(entries){
  var el = document.getElementById('camtune-log');
  if(!el)return;
  var levelColor = {info:'#cbd5e1', warn:'#fbbf24', err:'#f87171'};
  var html = '';
  entries.forEach(function(e){
    var col = levelColor[e.level] || '#cbd5e1';
    html += '<div style="white-space:pre-wrap;color:'+col+'">'
         +  '<span style="color:#475569">'+ (e.ts||'') +'</span> '
         +  escapeHtml(String(e.msg||''))
         +  '</div>';
  });
  el.insertAdjacentHTML('beforeend', html);
  if(_camTuneLogState && _camTuneLogState.autoScroll){
    el.scrollTop = el.scrollHeight;
  }
}

function _camTuneLogTrackScroll(){
  // If the operator scrolls up more than ~30 px from the bottom, stop
  // auto-scrolling so they can read older entries. They get
  // auto-scroll back the moment they scroll back to the bottom.
  if(!_camTuneLogState)return;
  var el = document.getElementById('camtune-log');
  if(!el)return;
  var atBottom = (el.scrollHeight - el.clientHeight - el.scrollTop) < 30;
  _camTuneLogState.autoScroll = atBottom;
}

function _camTuneSetDiag(msg, isErr){
  var el=document.getElementById('camtune-diag');
  if(!el)return;
  el.textContent=msg||'';
  el.style.color=isErr?'#f87171':'#64748b';
}

function _camTuneRenderDiag(){
  _camTuneSetDiag('camera fid '+_camTuneState.fid+' · '+(_camTuneState.controls||[]).length+' controls · '+(Object.keys(_camTuneState.slots||{}).length)+' saved slot(s)');
}


function scanNetwork(btn){
  if(btn){btn.disabled=true;btn.textContent='Scanning...';}
  ra('GET','/api/cameras/scan-network',null,function(){
    var poll=setInterval(function(){
      ra('GET','/api/cameras/scan-network/results',null,function(d){
        if(d&&d.pending)return;
        clearInterval(poll);
        if(btn){btn.disabled=false;btn.textContent='Scan Network';}
        var el=document.getElementById('net-scan-results');
        if(!el)return;
        if(!d||!d.length){
          el.innerHTML='<p style="color:#888;font-size:.85em;padding:.3em 0">No SSH-accessible devices found on network.</p>';
          el.style.display='block';return;
        }
        var h='<p style="color:#94a3b8;font-size:.85em;margin-bottom:.4em">SSH devices found \u2014 deploy camera software:</p>'
          +'<table class="tbl" style="max-width:500px"><tr><th>IP</th><th>Status</th><th></th></tr>';
        d.forEach(function(dev){
          var st=dev.hasCamera?'<span class="badge bon">Camera running</span>':'<span class="badge boff">No camera software</span>';
          var act=dev.hasCamera
            ?'<button class="btn" onclick="registerCamera(\''+dev.ip+'\',\''+escapeHtml(dev.hostname||'').replace(/'/g,"\\'")+'\')" style="background:#0e7490;color:#fff;font-size:.8em">Register</button>'
            :'<button class="btn" onclick="deployCameraServer(\''+dev.ip+'\')" id="deploy-btn-'+dev.ip.replace(/\\./g,'-')+'" style="background:#475569;color:#fff;font-size:.8em">Deploy</button>';
          h+='<tr><td>'+escapeHtml(dev.ip)+'</td><td>'+st+'</td><td>'+act+'</td></tr>';
        });
        el.innerHTML=h+'</table>';
        el.style.display='block';
      });
    },500);
  });
}

function deployCameraServer(ip){
  _camDeploy(ip);
}

function showSshSettings(){
  ra('GET','/api/cameras/ssh',null,function(d){
    var h='<label>SSH Username</label><input id="ssh-user" value="'+escapeHtml((d&&d.sshUser)||'root')+'" style="width:100%;margin-bottom:.4em">'
      +'<label>SSH Password</label><input id="ssh-pass" type="password" placeholder="'+(d&&d.hasPassword?'(saved)':'Enter password')+'" style="width:100%;margin-bottom:.4em">'
      +'<label>SSH Key Path <span style="color:#64748b;font-size:.75em">(optional, overrides password)</span></label>'
      +'<input id="ssh-key" value="'+escapeHtml((d&&d.sshKeyPath)||'')+'" placeholder="~/.ssh/id_ed25519" style="width:100%;margin-bottom:.6em">'
      +'<button class="btn btn-on" onclick="saveSshSettings()">Save</button>';
    document.getElementById('modal-title').textContent='Camera SSH Settings';
    document.getElementById('modal-body').innerHTML=h;
    document.getElementById('modal').style.display='block';
  });
}

function saveSshSettings(){
  var body={sshUser:document.getElementById('ssh-user').value.trim()};
  var pw=document.getElementById('ssh-pass').value;
  if(pw)body.sshPassword=pw;
  var key=document.getElementById('ssh-key').value.trim();
  body.sshKeyPath=key;
  ra('POST','/api/cameras/ssh',body,function(r){
    if(r&&r.ok){closeModal();document.getElementById('hs').textContent='SSH settings saved';}
    else{document.getElementById('hs').textContent='Save failed: '+(r&&r.err||'unknown');}
  });
}

function removeFixture(id,name){
  if(!confirm('Remove fixture "'+name+'"?'))return;
  document.getElementById('hs').textContent='Removing '+name+'...';
  // For LED fixtures, also find and remove the linked child
  var fix=null;(_fixtures||[]).forEach(function(f){if(f.id===id)fix=f;});
  ra('DELETE','/api/fixtures/'+id,null,function(r){
    if(fix&&(fix.fixtureType||'led')==='led'&&fix.childId!=null){
      ra('DELETE','/api/children/'+fix.childId,null,function(){
        document.getElementById('hs').textContent=(r&&r.ok)?'Fixture removed':'Remove failed';
        loadFixtures();loadSetup();
      });
    }else{
      document.getElementById('hs').textContent=(r&&r.ok)?'Fixture removed':'Remove failed';
      loadFixtures();loadSetup();
    }
  });
}

function removeChildDevice(id){
  if(!confirm('Remove this device?'))return;
  ra('DELETE','/api/children/'+id,null,function(r){
    document.getElementById('hs').textContent=(r&&r.ok)?'Device removed':'Remove failed';
    loadSetup();
  });
}

function refreshChild(id){
  ra('POST','/api/children/'+id+'/refresh',{},function(){setTimeout(_renderSetup,700);});
}

// #291 — broadcast-search a single offline performer by hostname. Updates
// the stored IP when DHCP rotated the lease. Disables the button while
// the request runs so the operator can't fire it twice.
function findChild(id,btn){
  if(btn){btn.disabled=true;btn.textContent='Searching...';}
  document.getElementById('hs').textContent='Searching for device...';
  ra('POST','/api/children/'+id+'/find',{},function(r){
    if(r&&r.found){
      var msg=r.oldIp&&r.oldIp!==r.ip
        ? 'Found at new IP '+r.ip+' (was '+r.oldIp+')'
        : 'Reconnected at '+r.ip;
      document.getElementById('hs').textContent=msg;
    }else{
      document.getElementById('hs').textContent=(r&&r.err)||'Device not found on network';
    }
    if(btn){btn.disabled=false;btn.textContent='Find';}
    setTimeout(_renderSetup,400);
  });
}

function rebootChild(id){
  if(!confirm('Reboot this fixture? It will be offline for a few seconds.'))return;
  document.getElementById('hs').textContent='Rebooting fixture...';
  ra('POST','/api/children/'+id+'/reboot',{},function(r){
    if(r&&r.ok)document.getElementById('hs').textContent='Reboot sent. Waiting for reconnect...';
    else document.getElementById('hs').textContent='Reboot failed: '+(r&&r.err||'unreachable');
    setTimeout(_renderSetup,8000);
  });
}

function _gyroEnable(childId, enable){
  var endpoint='/api/gyro/'+childId+(enable?'/enable':'/disable');
  ra('POST',endpoint,enable?{fps:20}:{},function(r){
    if(r&&r.ok)document.getElementById('hs').textContent=enable?'Gyro streaming enabled':'Gyro streaming disabled';
    else document.getElementById('hs').textContent='Gyro command failed: '+(r&&r.err||'unknown');
  });
}


function _gyroConfigModal(childId){
  // Fetch fresh children data (not available as a global after module extraction)
  ra('GET','/api/children',null,function(children){
    if(!children)return;
    var c=null;children.forEach(function(ch){if(ch.id===childId)c=ch;});
    if(!c)return;
    _gyroConfigModalRender(childId, c);
  });
}

function _gyroConfigModalRender(childId, c){
  var gyroFixtures=(_fixtures||[]).filter(function(f){return f.fixtureType==='gyro';});
  var dmxFixtures=(_fixtures||[]).filter(function(f){return f.fixtureType==='dmx';});
  var gf=gyroFixtures.find(function(gfx){return gfx.gyroChildId===childId;});

  var h='<div style="margin-bottom:1em">';
  // Device info
  h+='<div style="display:flex;gap:1em;flex-wrap:wrap;margin-bottom:.8em">';
  h+='<div><label style="font-size:.78em;color:#94a3b8">Hostname</label><div style="font-size:.9em;color:#e2e8f0">'+escapeHtml(c.hostname||c.ip)+'</div></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8">IP</label><div style="font-size:.9em;color:#e2e8f0">'+escapeHtml(c.ip)+'</div></div>';
  h+='<div><label style="font-size:.78em;color:#94a3b8">Firmware</label><div style="font-size:.9em;color:#e2e8f0">'+(c.fwVersion||'\u2014')+'</div></div>';
  h+='</div>';

  // Name (editable)
  h+='<label>Device Name</label>';
  h+='<input id="gcfg-name" value="'+escapeHtml(c.altName||c.name||c.hostname||'')+'" style="width:100%;margin-bottom:.6em" placeholder="e.g. Stage Left Gyro">';

  // Mover assignment
  var moverOpts='<option value="">No mover assigned</option>';
  dmxFixtures.forEach(function(m){
    moverOpts+='<option value="'+m.id+'"'+(gf&&m.id===gf.assignedMoverId?' selected':'')+'>'+escapeHtml(m.name)+'</option>';
  });
  h+='<label>Assigned Mover</label>';
  h+='<select id="gcfg-mover" style="width:100%;margin-bottom:.6em">'+moverOpts+'</select>';

  // Tuning (only if fixture exists)
  if(gf){
    var f=gf;
    // ── Send Lock + Live Status ──────────────────────────────────
    h+='<div style="border-top:1px solid #1e293b;padding-top:.6em;margin-top:.4em">';
    h+='<div style="display:flex;gap:.5em;align-items:center;margin-bottom:.5em">';
    h+='<button id="gcfg-lock-btn" class="btn btn-on" onclick="_gyroSendLock('+childId+','+f.id+')" style="font-size:.85em">Send Lock</button>';
    h+='</div>';
    h+='<div id="gcfg-live" style="padding:.4em .6em;border:1px solid #1e293b;border-radius:4px;font-size:.82em;color:#94a3b8;margin-bottom:.6em">';
    h+='\u25cb Not connected</div>';
    h+='<div style="font-size:.72em;color:#64748b;margin-bottom:.8em">'
      +'Send Lock tells the gyro controller which moving head to control. '
      +'Once locked, press START on the gyro device to begin streaming orientation data. '
      +'The status above updates live while this card is open.</div>';

    // ── Smoothing (the only operator-facing preference in the
    //    stage-space architecture; pan/tilt mapping comes from the
    //    calibration grid, not tunable multipliers) ────────────────
    h+='<div style="border-top:1px solid #1e293b;padding-top:.6em;margin-top:.4em">';
    var curSmooth=f.smoothing!=null?f.smoothing:0.15;
    h+='<div style="margin-bottom:.4em">';
    h+='<label style="font-size:.78em;color:#94a3b8;display:block">Smoothing</label>';
    h+='<div style="display:flex;align-items:center;gap:.5em">';
    h+='<span style="font-size:.7em;color:#64748b">Smooth</span>';
    h+='<input id="gcfg-sm" type="range" min="0.05" max="1" step="0.05" value="'+curSmooth+'" style="flex:1">';
    h+='<span style="font-size:.7em;color:#64748b">Instant</span>';
    h+='<span id="gcfg-sm-val" style="font-size:.78em;color:#e2e8f0;min-width:2em;text-align:right">'+curSmooth.toFixed(2)+'</span>';
    h+='</div>';
    h+='<div style="font-size:.68em;color:#64748b;margin-top:.15em">Low = dampened, smooth movement. High = instant, direct response</div>';
    h+='</div>';
    h+='</div>';
  }
  h+='</div>';

  // Action buttons
  h+='<div style="display:flex;gap:.4em;margin-top:.8em">';
  h+='<button class="btn btn-on" onclick="_gyroConfigSave('+childId+')">Save</button>';
  if(gf)h+='<button class="btn btn-off" onclick="_gyroUnassign('+gf.id+',\''+escapeHtml(gf.name).replace(/'/g,"\\'")+'\')">Unassign Mover</button>';
  h+='</div>';

  document.getElementById('modal-title').textContent='Configure: '+(c.altName||c.name||c.hostname);
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';

  // Wire up smoothing slider live-value display
  var smEl=document.getElementById('gcfg-sm');
  if(smEl){
    smEl.oninput=function(){document.getElementById('gcfg-sm-val').textContent=parseFloat(this.value).toFixed(2);};
  }

  // Start live status polling (every 2s while modal open)
  if(gf&&gf.assignedMoverId!=null){
    _gyroLivePoll(childId,gf);
    window._gcfgPoll=setInterval(function(){_gyroLivePoll(childId,gf);},2000);
  }
}

function _gyroSendLock(childId,fixtureId){
  // Send CMD_GYRO_CTRL + auto-claim mover
  var gf=(_fixtures||[]).find(function(f){return f.id===fixtureId;});
  if(!gf)return;
  _gyroToggleEnabled(fixtureId,true);
  // Brief visual feedback then revert
  var btn=document.getElementById('gcfg-lock-btn');
  if(btn){
    btn.textContent='Sent \u2713';btn.style.background='#059669';
    setTimeout(function(){
      var b=document.getElementById('gcfg-lock-btn');
      if(b){b.textContent='Send Lock';b.style.background='';}
    },2000);
  }
}

function _gyroLivePoll(childId,gf){
  var el=document.getElementById('gcfg-live');
  if(!el)return;  // modal closed
  // Poll both the claim state (mover-control) and the primitive's stream
  // state (remote-orientation). Show the fullest picture — the puck can be
  // streaming orient data before any lock/claim exists.
  ra('GET','/api/mover-control/status',null,function(ds){
    ra('GET','/api/remotes/live',null,function(dr){
      if(!document.getElementById('gcfg-live'))return;
      var claim=(ds&&ds.claims||[]).find(function(c){return c.moverId===gf.assignedMoverId;});
      // Match the remote by device IP (gyroChildId → child IP → deviceId "gyro-<ip>").
      var childIp=(_children||[]).reduce(function(a,c){return c.id===gf.gyroChildId?c.ip:a;},null);
      var remote=childIp?(dr&&dr.remotes||[]).find(function(r){return r.deviceId==='gyro-'+childIp;}):null;

      var parts=[];
      var border='#1e293b';
      if(remote){
        var age=remote.lastDataAge!=null?remote.lastDataAge:99;
        // #476 — three-tier state: hard (grey, latched), soft (amber, transient), live (green).
        var dot, stateLabel;
        if(remote.hardStale){
          dot='<span style="color:#64748b">\u25cb</span>';
          stateLabel='Lost ('+remote.staleReason+')';
          border='#475569';
        }else if(remote.softStale){
          dot='<span style="color:#f59e0b">\u25cf</span>';
          stateLabel='Reconnecting...';
          border='#92400e';
        }else if(remote.calibrated){
          dot='<span style="color:#22c55e">\u25cf</span>';
          stateLabel='Calibrated';
          border='#059669';
        }else{
          dot='<span style="color:#22c55e">\u25cf</span>';
          stateLabel='Streaming (uncal)';
          border='#059669';
        }
        parts.push(dot+' <b>'+escapeHtml(remote.name||('remote '+remote.id))+'</b> \u2014 '+stateLabel
          +' <span style="color:#64748b">('+age.toFixed(1)+'s ago)</span>');
      }
      if(claim){
        parts.push('<b>Lock:</b> '+escapeHtml(claim.deviceName)+' ('+claim.state+')'
          +' <button class="btn btn-off" onclick="ra(\'POST\',\'/api/mover-control/release\',{moverId:'
          +gf.assignedMoverId+'},function(){_gyroConfigModal('+childId+')})" style="font-size:.72em;margin-left:.4em">Release</button>');
      }else if(remote){
        parts.push('<span style="color:#64748b">No lock \u2014 press <b>Send Lock</b> to control a mover.</span>');
      }
      if(!parts.length){
        el.innerHTML='\u25cb Not connected';
        el.style.borderColor='#1e293b';
        return;
      }
      el.innerHTML=parts.join('<br>');
      el.style.borderColor=border;
    });
  });
}

function _gyroConfigSave(childId){
  var name=(document.getElementById('gcfg-name')||{}).value||'';
  var moverVal=document.getElementById('gcfg-mover').value;
  var moverId=moverVal?parseInt(moverVal,10):null;

  // Save device name
  if(name)ra('PUT','/api/children/'+childId,{altName:name},function(){});

  // Find existing gyro fixture for this child
  var gf=(_fixtures||[]).find(function(f){return f.fixtureType==='gyro'&&f.gyroChildId===childId;});

  if(!gf&&moverId!=null){
    // Create new gyro fixture with mover assignment
    var mover=(_fixtures||[]).find(function(f){return f.id===moverId;});
    ra('POST','/api/fixtures',{name:(name||'Gyro')+' \u2192 '+(mover?mover.name:'Mover'),fixtureType:'gyro',type:'point',gyroChildId:childId,assignedMoverId:moverId,gyroEnabled:false},function(r){
      if(r&&r.ok){document.getElementById('hs').textContent='Gyro configured';closeModal();loadSetup();}
      else document.getElementById('hs').textContent='Failed: '+(r&&r.err||'unknown');
    });
  }else if(gf){
    // Update existing fixture — mover assignment + smoothing
    var body={};
    if(moverId!==undefined)body.assignedMoverId=moverId;
    var sm=document.getElementById('gcfg-sm');if(sm)body.smoothing=parseFloat(sm.value);
    ra('PUT','/api/fixtures/'+gf.id,body,function(r){
      if(r&&r.ok){document.getElementById('hs').textContent='Gyro configuration saved';closeModal();loadSetup();}
      else document.getElementById('hs').textContent='Save failed: '+(r&&r.err||'unknown');
    });
  }else{
    // No mover selected and no existing fixture — just save name
    document.getElementById('hs').textContent='Device name saved';
    closeModal();loadSetup();
  }
}

function _gyroToggleEnabled(fixtureId,enable){
  ra('PUT','/api/fixtures/'+fixtureId,{gyroEnabled:enable},function(r){
    if(r&&r.ok){
      var f=null;(_fixtures||[]).forEach(function(fx){if(fx.id===fixtureId)f=fx;});
      if(f&&f.gyroChildId!=null)_gyroEnable(f.gyroChildId,enable);
      document.getElementById('hs').textContent=enable?'Gyro enabled':'Gyro disabled';
      loadSetup();
    }else{
      document.getElementById('hs').textContent='Toggle failed: '+(r&&r.err||'unknown');
    }
  });
}

function _gyroAssignMover(childId,selId){
  var sel=document.getElementById(selId);
  if(!sel||!sel.value){document.getElementById('hs').textContent='Select a mover first';return;}
  var moverId=parseInt(sel.value,10);
  var mover=null;(_fixtures||[]).forEach(function(f){if(f.id===moverId)mover=f;});
  var name=(mover?mover.name+' ':'')+'Gyro';
  ra('POST','/api/fixtures',{name:name,fixtureType:'gyro',type:'point',gyroChildId:childId,assignedMoverId:moverId,gyroEnabled:false},function(r){
    if(r&&r.ok){document.getElementById('hs').textContent='Gyro fixture created';loadSetup();}
    else document.getElementById('hs').textContent='Create failed: '+(r&&r.err||'unknown');
  });
}

function _gyroReassign(fixtureId,selId){
  var sel=document.getElementById(selId);
  if(!sel)return;
  var mid=sel.value?parseInt(sel.value,10):null;
  ra('PUT','/api/fixtures/'+fixtureId,{assignedMoverId:mid},function(r){
    if(r&&r.ok){document.getElementById('hs').textContent=mid?'Mover reassigned':'Mover cleared';loadSetup();}
    else document.getElementById('hs').textContent='Reassign failed: '+(r&&r.err||'unknown');
  });
}

function _gyroUnassign(fixtureId,name){
  if(!confirm('Remove gyro fixture "'+name+'"? This will unlink the gyro board from its mover.'))return;
  ra('DELETE','/api/fixtures/'+fixtureId,null,function(r){
    if(r&&r.ok){document.getElementById('hs').textContent='Gyro fixture removed';loadSetup();}
    else document.getElementById('hs').textContent='Remove failed: '+(r&&r.err||'unknown');
  });
}

function setupRefreshAll(btn){
  if(btn){btn.disabled=true;btn.textContent='Refreshing...';}
  ra('POST','/api/children/refresh-all',{},function(){
    var poll=setInterval(function(){
      ra('GET','/api/children/refresh-all/results',null,function(r){
        if(r&&r.pending)return;
        clearInterval(poll);
        if(btn){btn.disabled=false;btn.textContent='Refresh All';}
        _renderSetup();
      });
    },500);
  });
}

// ── ArUco marker registry (#596) ────────────────────────────────────
// Shared editor rendered into both the Setup tab and a collapsible panel
// inside the Advanced Scan card. Both views edit /api/aruco/markers so
// changes round-trip immediately.
var _aruco_cache={markers:[], dictId:50};

function _arucoLoad(then){
  ra('GET','/api/aruco/markers',null,function(r){
    if(r&&r.ok){
      _aruco_cache.markers=r.markers||[];
      _aruco_cache.dictId=r.dictId||50;
    }
    if(typeof then==='function')then();
  });
}

function _arucoRenderTable(hostId, opts){
  opts=opts||{};
  var host=document.getElementById(hostId);
  if(!host)return;
  var markers=_aruco_cache.markers||[];
  var dictMax=(_aruco_cache.dictId||50)-1;
  var h='';
  if(!markers.length){
    h+='<p style="color:#64748b;font-size:.82em;margin:.3em 0">No markers surveyed yet. Place ArUco tags at known stage positions (DICT_4X4_'
      +(dictMax+1)+', IDs 0..'+dictMax+') and add them below so cameras can use them as ground truth.</p>';
  }else{
    h+='<div style="overflow-x:auto">'
      +'<table class="tbl" style="font-size:.8em">'
      +'<tr><th>ID</th><th>Label</th><th>Size (mm)</th><th>X</th><th>Y</th><th>Z</th>'
      +'<th>Rx°</th><th>Ry°</th><th>Rz°</th><th></th></tr>';
    markers.forEach(function(m){
      h+='<tr id="aruco-row-'+hostId+'-'+m.id+'">'
        +'<td><b>'+m.id+'</b></td>'
        +'<td><input data-fld="label" value="'+escapeHtml(m.label||'')+'" style="width:80px;font-size:.9em" placeholder="—"></td>'
        +'<td><input data-fld="size" type="number" step="1" min="1" value="'+m.size+'" style="width:60px;font-size:.9em"></td>'
        +'<td><input data-fld="x" type="number" step="1" value="'+m.x+'" style="width:70px;font-size:.9em"></td>'
        +'<td><input data-fld="y" type="number" step="1" value="'+m.y+'" style="width:70px;font-size:.9em"></td>'
        +'<td><input data-fld="z" type="number" step="1" value="'+m.z+'" style="width:70px;font-size:.9em"></td>'
        +'<td><input data-fld="rx" type="number" step="1" value="'+m.rx+'" style="width:50px;font-size:.9em"></td>'
        +'<td><input data-fld="ry" type="number" step="1" value="'+m.ry+'" style="width:50px;font-size:.9em"></td>'
        +'<td><input data-fld="rz" type="number" step="1" value="'+m.rz+'" style="width:50px;font-size:.9em"></td>'
        +'<td>'
          +'<button class="btn" onclick="_arucoRowSave(\''+hostId+'\','+m.id+')" style="font-size:.72em;padding:.15em .5em;background:#059669;color:#fff">Save</button> '
          +'<button class="btn btn-off" onclick="_arucoRowDelete(\''+hostId+'\','+m.id+')" style="font-size:.72em;padding:.15em .5em">Delete</button>'
        +'</td></tr>';
    });
    h+='</table></div>';
  }
  // Add row
  h+='<div style="margin-top:.5em;padding:.4em;background:#0f172a;border:1px dashed #334155;border-radius:4px">'
    +'<div style="font-size:.78em;color:#94a3b8;margin-bottom:.25em">Add marker</div>'
    +'<div style="display:flex;flex-wrap:wrap;gap:.3em;align-items:center">'
    +'<label style="font-size:.78em">ID <input id="aruco-add-'+hostId+'-id" type="number" step="1" min="0" max="'+dictMax+'" style="width:55px"></label>'
    +'<label style="font-size:.78em">Label <input id="aruco-add-'+hostId+'-label" style="width:80px" placeholder="e.g. USR"></label>'
    +'<label style="font-size:.78em">Size <input id="aruco-add-'+hostId+'-size" type="number" step="1" min="1" value="150" style="width:60px"> mm</label>'
    +'<label style="font-size:.78em">X <input id="aruco-add-'+hostId+'-x" type="number" step="1" value="0" style="width:65px"></label>'
    +'<label style="font-size:.78em">Y <input id="aruco-add-'+hostId+'-y" type="number" step="1" value="0" style="width:65px"></label>'
    +'<label style="font-size:.78em">Z <input id="aruco-add-'+hostId+'-z" type="number" step="1" value="0" style="width:65px"></label>'
    +'<button class="btn btn-on" onclick="_arucoRowAdd(\''+hostId+'\')" style="font-size:.78em;padding:.2em .6em">Add</button>'
    +'</div></div>';
  // Status line for validation + save feedback
  h+='<div id="aruco-status-'+hostId+'" style="font-size:.78em;color:#64748b;margin-top:.3em">Dictionary: DICT_4X4_'+(dictMax+1)+' · IDs 0..'+dictMax+'</div>';
  host.innerHTML=h;
}

function _arucoStatus(hostId, msg, ok){
  var el=document.getElementById('aruco-status-'+hostId);
  if(el)el.innerHTML='<span style="color:'+(ok?'#34d399':'#f59e0b')+'">'+escapeHtml(msg)+'</span>';
}

function _arucoRowSave(hostId, mid){
  var row=document.getElementById('aruco-row-'+hostId+'-'+mid);
  if(!row)return;
  var rec={id:mid};
  row.querySelectorAll('input[data-fld]').forEach(function(inp){
    var k=inp.getAttribute('data-fld');
    rec[k]=(k==='label')?inp.value:parseFloat(inp.value||0);
  });
  ra('POST','/api/aruco/markers',rec,function(r){
    if(r&&r.ok){
      _aruco_cache.markers=r.markers||[];
      _arucoStatus(hostId,'Marker '+mid+' saved',true);
      _arucoRefreshOther(hostId);
    }else{
      _arucoStatus(hostId,(r&&r.err)||'Save failed',false);
    }
  });
}

function _arucoRowDelete(hostId, mid){
  if(!confirm('Delete marker '+mid+'?'))return;
  ra('DELETE','/api/aruco/markers/'+mid,null,function(r){
    if(r&&r.ok){
      _aruco_cache.markers=r.markers||[];
      _arucoRenderTable(hostId, {source:hostId.indexOf('pcadv')===0?'scan':'setup'});
      _arucoStatus(hostId,'Marker '+mid+' removed',true);
      _arucoRefreshOther(hostId);
    }
  });
}

function _arucoRowAdd(hostId){
  var id=parseInt((document.getElementById('aruco-add-'+hostId+'-id')||{}).value);
  if(isNaN(id)||id<0){_arucoStatus(hostId,'ID is required',false);return;}
  var dictMax=(_aruco_cache.dictId||50)-1;
  if(id>dictMax){_arucoStatus(hostId,'ID '+id+' outside dictionary range 0..'+dictMax,false);return;}
  if(_aruco_cache.markers.some(function(m){return m.id===id;})){
    _arucoStatus(hostId,'Marker ID '+id+' already registered — edit the existing row',false);return;
  }
  var rec={
    id:id,
    label:(document.getElementById('aruco-add-'+hostId+'-label')||{}).value||'',
    size:parseFloat((document.getElementById('aruco-add-'+hostId+'-size')||{}).value||150),
    x:parseFloat((document.getElementById('aruco-add-'+hostId+'-x')||{}).value||0),
    y:parseFloat((document.getElementById('aruco-add-'+hostId+'-y')||{}).value||0),
    z:parseFloat((document.getElementById('aruco-add-'+hostId+'-z')||{}).value||0),
    rx:0,ry:0,rz:0
  };
  ra('POST','/api/aruco/markers',rec,function(r){
    if(r&&r.ok){
      _aruco_cache.markers=r.markers||[];
      _arucoRenderTable(hostId, {source:hostId.indexOf('pcadv')===0?'scan':'setup'});
      _arucoStatus(hostId,'Marker '+id+' added',true);
      _arucoRefreshOther(hostId);
    }else{
      _arucoStatus(hostId,(r&&r.err)||'Add failed',false);
    }
  });
}

function _arucoRefreshOther(hostId){
  // If Setup + Scan panels are both on the page, keep them in sync.
  var other=(hostId==='aruco-setup-host')?'aruco-scan-host':'aruco-setup-host';
  if(document.getElementById(other))_arucoRenderTable(other, {source:other==='aruco-scan-host'?'scan':'setup'});
}

// ── #592 ArUco prescan + marker-anchored simple scan ──────────────────

// Prescan the cameras for marker visibility. Hits every registered
// camera fixture, snapshots it, runs ArUco detection on the
// orchestrator, and renders a per-camera banner showing which marker
// IDs were seen and how many are both visible-to-≥2-cameras AND in the
// surveyed registry. The "Scan with visible markers" button is enabled
// once the shared count is ≥1.
function _arucoPrescanVisibility(){
  var box=document.getElementById('aruco-prescan-result');
  if(box){box.style.display='block';box.innerHTML='<span style="color:#64748b">Snapshotting cameras…</span>';}
  var simpleBtn=document.getElementById('aruco-simple-btn');
  if(simpleBtn)simpleBtn.disabled=true;
  // Honour the operator's per-camera checkbox selection in the scan card.
  var cameraIds=Array.prototype.slice.call(document.querySelectorAll('.pcadv-cam:checked:not(:disabled)'))
    .map(function(cb){return parseInt(cb.value);});
  var body=cameraIds.length?{cameras:cameraIds}:{};
  ra('POST','/api/space/scan/aruco-preview',body,function(r){
    if(!r||!r.ok){
      if(box)box.innerHTML='<span style="color:#ef4444">Prescan failed: '+escapeHtml((r&&r.err)||'unknown')+'</span>';
      return;
    }
    var shared=r.shared||r.sharedIds||[];
    var corresp=r.correspondences||0;
    var lines=[];
    (r.cameras||[]).forEach(function(c){
      if(c.err){
        lines.push('<div style="color:#ef4444">• '+escapeHtml(c.name||'Camera '+c.id)+' — '+escapeHtml(c.err)+'</div>');
        return;
      }
      var ids=(c.markers||[]).map(function(m){return m.id;});
      var col=ids.length?'#34d399':'#f59e0b';
      var idTxt=ids.length?ids.join(', '):'(none)';
      lines.push('<div style="color:'+col+'">• '+escapeHtml(c.name||'Camera '+c.id)
                +' <span style="color:#94a3b8">sees markers</span> ['+idTxt+']</div>');
    });
    var bannerCol=(shared.length>=1)?'#34d399':'#f59e0b';
    var bannerTxt;
    if(shared.length===0){
      bannerTxt='<b style="color:#f59e0b">No surveyed markers visible to ≥2 cameras.</b> '
              +'<span style="color:#94a3b8">Either no markers overlap across cameras, or the visible ones are not in the registry yet.</span>';
    }else{
      bannerTxt='<b style="color:'+bannerCol+'">Shared markers: ['+shared.join(', ')+']</b>'
              +' <span style="color:#94a3b8">— '+corresp+' correspondence'+(corresp===1?'':'s')+' available for stereo anchoring.</span>';
    }
    if(box){
      box.innerHTML=bannerTxt+'<div style="margin-top:.3em">'+lines.join('')+'</div>';
    }
    if(simpleBtn)simpleBtn.disabled=(shared.length<1);
  });
}

// Kick the marker-anchored simple scan. Produces a tiny cloud (4
// corners × shared markers) but the points are triangulated from
// surveyed correspondences, so the per-marker delta vs surveyed
// position is an immediate quality number.
function _arucoScanSimple(){
  var box=document.getElementById('aruco-prescan-result');
  var btn=document.getElementById('aruco-simple-btn');
  if(btn)btn.disabled=true;
  if(box){
    box.style.display='block';
    box.innerHTML='<span style="color:#64748b">Triangulating ArUco corners from registered cameras…</span>';
  }
  var cameraIds=Array.prototype.slice.call(document.querySelectorAll('.pcadv-cam:checked:not(:disabled)'))
    .map(function(cb){return parseInt(cb.value);});
  var body=cameraIds.length?{cameras:cameraIds}:{};
  ra('POST','/api/space/scan/aruco-simple',body,function(r){
    if(btn)btn.disabled=false;
    if(!r||!r.ok){
      if(box)box.innerHTML='<span style="color:#ef4444">Scan failed: '+escapeHtml((r&&r.err)||'unknown')+'</span>';
      return;
    }
    var rows=(r.triangulated||[]).map(function(t){
      var dCol=t.deltaMm<50?'#34d399':(t.deltaMm<200?'#f59e0b':'#ef4444');
      return '<tr>'
        +'<td style="padding:.15em .4em">'+t.id+'</td>'
        +'<td style="padding:.15em .4em;color:#94a3b8">['+t.surveyed.map(function(v){return Math.round(v);}).join(', ')+']</td>'
        +'<td style="padding:.15em .4em;color:#cbd5e1">['+t.triangulatedCenter.map(function(v){return Math.round(v);}).join(', ')+']</td>'
        +'<td style="padding:.15em .4em;color:'+dCol+';font-weight:bold">'+t.deltaMm.toFixed(0)+' mm</td>'
        +'</tr>';
    }).join('');
    if(box){
      box.innerHTML='<div style="color:#34d399"><b>Scan complete:</b> '+r.totalPoints+' points from '+(r.triangulated||[]).length+' surveyed marker(s) in '+r.elapsedS+'s</div>'
        +'<table style="margin-top:.4em;width:100%;font-size:.78em;border-collapse:collapse">'
        +'<tr style="color:#64748b;border-bottom:1px solid #1e293b"><th style="text-align:left;padding:.15em .4em">ID</th><th style="text-align:left;padding:.15em .4em">Surveyed (mm)</th><th style="text-align:left;padding:.15em .4em">Triangulated (mm)</th><th style="text-align:left;padding:.15em .4em">Δ</th></tr>'
        +rows
        +'</table>'
        +'<div style="color:#64748b;margin-top:.3em;font-size:.72em">Point cloud saved as <code>source: aruco-markers</code>. Open the Layout tab to see the anchored points; use this delta to verify each camera\'s position/rotation before running a full stereo scan.</div>';
    }
    // Refresh the 3D viewport if it's loaded so the new cloud shows up.
    if(typeof s3dReloadPointCloud==='function')s3dReloadPointCloud();
  });
}
