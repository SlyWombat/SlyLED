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
  // Fetch both fixtures and children so we can resolve LED fixture status
  ra('GET','/api/fixtures',null,function(fixtures){
    _fixtures=fixtures||[];
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
          var acts='<button class="btn btn-on" onclick="refreshChild('+c.id+')">Refresh</button>'
            +' <button class="btn" onclick="rebootChild('+c.id+')" style="background:#654;color:#fff">Reboot</button>'
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
      if(camFixtures.length){ra('GET','/api/cameras',null,function(cams){
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
        // Find latest camera firmware version across all online cameras
        var latestCamVer='0.0.0';
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
          if(ch)actions+=' <button class="btn" onclick="showDetails('+f.childId+')" style="background:#335;color:#fff">Details</button>'
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
            +' <button class="btn" onclick="showDmxDetails('+f.id+')" style="background:#3b1f7c;color:#e9d5ff">Details</button>'
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
      if(camFixtures.length){
        h+='<table class="tbl cam-status-tbl"><tr><th>#</th><th>Sensor Name</th><th>Node</th><th>FOV</th><th>Resolution</th><th>Status</th><th>Actions</th></tr>';
        camFixtures.forEach(function(f){
          var ip=f.cameraIp||'—';
          var camIdx=f.cameraIdx||0;
          var fov=(f.fovDeg||60)+'\u00b0';
          var res=(f.resolutionW||'—')+'x'+(f.resolutionH||'—');
          var calBadge=f.calibrated?'<span class="badge" style="background:#065f46;color:#34d399;margin-left:4px">\u2713 Cal</span>':'';
          var acts='<button class="btn" onclick="editFixture('+f.id+')" style="background:#446;color:#fff">Edit</button>';
          if(ip!=='—'){
            acts+=' <button class="btn" onclick="_camSnap('+f.id+')" style="background:#059669;color:#fff">Snap</button>';
            var trkActive=_trackingCams[f.id];
            acts+=' <button class="btn" id="setup-trk-'+f.id+'" onclick="_setupTrackToggle('+f.id+')" style="background:'+(trkActive?'#9f1239':'#be185d')+';color:#fce7f3">'+(trkActive?'Stop Track':'Track')+'</button>';
          }
          acts+=' <button class="btn btn-off" onclick="removeCamera('+f.id+',\''+escapeHtml(f.name).replace(/'/g,"\\'")+'\')">Remove</button>';
          h+='<tr><td style="color:#64748b">cam'+camIdx+'</td><td><b>'+escapeHtml(f.name)+'</b>'+calBadge+'</td><td>'+escapeHtml(ip)+'</td><td>'+fov+'</td><td>'+res+'</td><td id="cam-st-'+f.id+'"><span class="badge" style="background:#334;color:#888">...</span></td><td>'+acts+'</td></tr>';
          h+='<tr id="cam-snap-row-'+f.id+'" style="display:none"><td colspan="7" style="padding:.3em"><div id="cam-snap-'+f.id+'" style="text-align:center"></div></td></tr>';
        });
        h+='</table>';
      }else{
        h+='<p style="color:#555;font-size:.82em">No cameras registered. Click Discover or add via Add Fixture.</p>';
      }

      document.getElementById('t-setup').innerHTML=h;
      // Check firmware updates — add ▲ triangle indicator to outdated devices
      api('GET','/api/firmware/check').then(function(chk){
        if(!chk||!chk.children)return;
        chk.children.forEach(function(u){
          if(!u.needsUpdate||u.board==='wled'||u.board==='gyro'||u.type==='gyro')return;
          var el=document.getElementById('fw-ind-'+u.id);
          if(el)el.innerHTML=' <span onclick="showTab(\'firmware\')" style="color:#f60;cursor:pointer;font-size:.9em" title="Update available: v'+escapeHtml(u.latestVersion)+' (click to update)">&#9650;</span>';
        });
      }).catch(function(){});
    });
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
              '\')" style="background:#0e7490;color:#fff">Add Camera</button>'
              +' <button class="btn" onclick="window.open(\'http://'+escapeHtml(c.ip)+':5000/config\',\'_blank\')" style="background:#475569;color:#fff">Config</button>';
          }else{
            addBtn='<button class="btn btn-on" onclick="addDiscovered(\''+c.ip+'\')">Add</button>';
            if(bt==='dmx')addBtn+=' <button class="btn" onclick="addDiscoveredDmxBridge(\''+c.ip+'\')" style="background:#7c3aed;color:#fff">Configure</button>';
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

function addDiscoveredDmxBridge(ip){
  document.getElementById('hs').textContent='Opening DMX bridge config at '+ip+'...';
  window.open('http://'+ip+'/config','_blank');
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
      +' <button class="btn" onclick="window.open(\'http://'+escapeHtml(c.ip)+':5000/config\',\'_blank\')" style="background:#475569;color:#fff;font-size:.82em;padding:.2em .6em">Config</button>'
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
        var h='<p style="color:#22d3ee;font-size:.85em;margin-bottom:.4em">Found cameras — click Register:</p>'
          +'<table class="tbl" style="max-width:600px"><tr><th>Name</th><th>IP</th><th>FOV</th><th></th></tr>';
        d.forEach(function(c){
          h+='<tr><td>'+escapeHtml(c.hostname||c.name||'Camera')+'</td><td>'+escapeHtml(c.ip)+'</td><td>'+(c.fovDeg||'?')+'&deg;</td>'
            +'<td><button class="btn" onclick="registerCamera(\''+c.ip+'\',\''+escapeHtml(c.hostname||'').replace(/'/g,"\\'")+'\')" style="background:#0e7490;color:#fff">Register</button></td></tr>';
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
