/** timelines.js — Timeline editor, preview, bake, sync-and-start, show execution. Extracted from app.js Phase 2. */
// ── Phase 4: Timeline Editor ────────────────────────────────────────────────
var _rtMode='timeline';
var _timelines=[], _curTl=null;
var _tlPlaying=false, _tlPlayT=0, _tlPlayTimer=null;
var _tlPxPerSec=12; // pixels per second in the timeline

function setRuntimeMode(mode){
  // Timeline is now the only mode — kept for API compatibility
  _rtMode='timeline';
  loadTimelines();
}

function loadTimelines(){
  emuLoadStage(); // always refresh stage canvas
  ra('GET','/api/timelines',null,function(d){
    _timelines=d||[];
    var sel=document.getElementById('tl-select');if(!sel)return;
    var cv=sel.value;
    sel.innerHTML='<option value="">Select timeline...</option>';
    _timelines.forEach(function(t){
      sel.innerHTML+='<option value="'+t.id+'">'+escapeHtml(t.name)+' ('+t.durationS+'s)</option>';
    });
    if(cv)sel.value=cv;
    loadSpatialFx();loadFixtures();
    // Also refresh actions so clip names resolve (not "?")
    ra('GET','/api/actions',null,function(d){_acts=d||[];});
  });
}

function newTimeline(){
  var name=prompt('Timeline name:');
  if(!name)return;
  var dur=parseInt(prompt('Duration in seconds:','60'))||60;
  ra('POST','/api/timelines',{name:name,durationS:dur},function(r){
    if(r&&r.ok){loadTimelines();setTimeout(function(){
      var sel=document.getElementById('tl-select');if(sel)sel.value=r.id;loadTimelineDetail();
    },200);}
  });
}

function deleteTimeline(){
  var sel=document.getElementById('tl-select');
  var id=sel?parseInt(sel.value):null;
  if(!id&&id!==0)return;
  if(!confirm('Delete this timeline?'))return;
  ra('DELETE','/api/timelines/'+id,null,function(){_curTl=null;document.getElementById('tl-detail').style.display='none';loadTimelines();});
}

function loadTimelineDetail(){
  var sel=document.getElementById('tl-select');
  var id=sel?parseInt(sel.value):null;
  if(isNaN(id)){document.getElementById('tl-detail').style.display='none';return;}
  ra('GET','/api/timelines/'+id,null,function(tl){
    if(!tl){document.getElementById('tl-detail').style.display='none';return;}
    _curTl=tl;
    document.getElementById('tl-detail').style.display='block';
    document.getElementById('tl-name').value=tl.name||'';
    document.getElementById('tl-dur').value=tl.durationS||60;
    document.getElementById('tl-loop').checked=!!tl.loop;
    renderTimeline();
  });
}

function saveTimeline(btn){
  if(!_curTl)return;
  _btnSaving(btn);
  _curTl.name=document.getElementById('tl-name').value;
  _curTl.durationS=parseInt(document.getElementById('tl-dur').value)||60;
  _curTl.loop=document.getElementById('tl-loop').checked;
  ra('PUT','/api/timelines/'+_curTl.id,_curTl,function(r){
    _btnSaved(btn,r&&r.ok);
    var sel=document.getElementById('tl-select');
    if(sel){var opt=sel.querySelector('option[value="'+_curTl.id+'"]');if(opt)opt.textContent=_curTl.name+' ('+_curTl.durationS+'s)';}
  });
}

function renderTimeline(){
  if(!_curTl)return;
  var dur=_curTl.durationS||60;
  var trackW=60+dur*_tlPxPerSec;
  var ruler=document.getElementById('tl-ruler');
  var rows=document.getElementById('tl-track-rows');
  if(!ruler||!rows)return;

  // Ruler
  var rh='';
  for(var s=0;s<=dur;s+=5){
    var x=60+s*_tlPxPerSec;
    rh+='<span style="position:absolute;left:'+x+'px;top:4px;font-size:.65em;color:#475569">'+s+'s</span>';
    rh+='<span style="position:absolute;left:'+x+'px;top:16px;width:1px;height:8px;background:#1e293b"></span>';
  }
  ruler.style.width=trackW+'px';ruler.innerHTML=rh;

  // Tracks
  var tracks=_curTl.tracks||[];
  var th='';
  tracks.forEach(function(trk,ti){
    var fixName=trk.allPerformers?'\u2605 Stage (All)':'Track '+(ti+1);
    if(!trk.allPerformers){var fix=null;_fixtures.forEach(function(f){if(f.id===trk.fixtureId)fix=f;});if(fix)fixName=fix.name||fixName;}
    th+='<div style="display:flex;height:40px;border-bottom:1px solid #1e293b">';
    th+='<div style="width:60px;flex-shrink:0;padding:2px 4px;font-size:.65em;color:#94a3b8;overflow:hidden;border-right:1px solid #1e293b;display:flex;flex-direction:column;justify-content:center;gap:1px">'
      +'<div style="display:flex;align-items:center;gap:1px"><span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;max-width:40px" title="'+escapeHtml(fixName)+'">'+escapeHtml(fixName)+'</span>'
      +'<span style="display:flex;flex-direction:column;line-height:1">'
      +(ti>0?'<span style="cursor:pointer;color:#64748b;font-size:8px" onclick="tlMoveTrack('+ti+',-1)" title="Move up">\u25b2</span>':'')
      +(ti<tracks.length-1?'<span style="cursor:pointer;color:#64748b;font-size:8px" onclick="tlMoveTrack('+ti+',1)" title="Move down">\u25bc</span>':'')
      +'</span></div>'
      +'<span style="display:flex;gap:2px;align-items:center"><span style="cursor:pointer;color:#3b82f6;font-size:.9em" onclick="tlAddClipToTrack('+ti+')" title="Add clip">+</span><span style="cursor:pointer;color:#f66;font-size:.9em" onclick="tlDeleteTrack('+ti+')" title="Delete track">&times;</span></span></div>';
    th+='<div style="flex:1;position:relative;min-width:'+(trackW-60)+'px" id="tl-tr-'+ti+'">';
    // Render clips
    (trk.clips||[]).forEach(function(clip,ci){
      var lx=clip.startS*_tlPxPerSec;
      var w=clip.durationS*_tlPxPerSec;
      var fxn=clip.name||'',fxcol='#3b82f6';
      if(clip.actionId!=null){
        _acts.forEach(function(a){if(a.id===clip.actionId){fxn='\u25b6 '+a.name;fxcol=rgb2h(a.r||100,a.g||100,a.b||255);}});
      } else if(clip.effectId!=null){
        _spatialFx.forEach(function(f){if(f.id===clip.effectId){fxn=f.name;fxcol=rgb2h(f.r||100,f.g||100,f.b||255);}});
      }
      th+='<div style="position:absolute;left:'+lx+'px;top:4px;width:'+Math.max(w,8)+'px;height:28px;background:'+fxcol+'33;border:1px solid '+fxcol+';border-radius:3px;cursor:pointer;overflow:hidden;font-size:.65em;color:#e2e8f0;padding:2px 4px;white-space:nowrap" ';
      th+='onclick="editClip('+ti+','+ci+')" title="'+escapeHtml(fxn)+' ('+clip.durationS+'s)">';
      th+=escapeHtml(fxn||'?')+'</div>';
    });
    th+='</div></div>';
  });
  if(!tracks.length)th='<p style="color:#475569;font-size:.82em;padding:.6em">No tracks. Click + Add Track to begin.</p>';
  rows.style.width=trackW+'px';rows.innerHTML=th;
}

function tlMoveTrack(idx,dir){
  if(!_curTl||!_curTl.tracks)return;
  var t=_curTl.tracks;
  var to=idx+dir;
  if(to<0||to>=t.length)return;
  var tmp=t[idx];t[idx]=t[to];t[to]=tmp;
  ra('PUT','/api/timelines/'+_curTl.id,_curTl,function(){renderTimeline();});
}

function tlDeleteTrack(idx){
  if(!_curTl||!_curTl.tracks||idx<0||idx>=_curTl.tracks.length)return;
  var trk=_curTl.tracks[idx];
  var name=trk.allPerformers?'Stage (All)':'Track '+(idx+1);
  if(!confirm('Delete track "'+name+'"?'))return;
  _curTl.tracks.splice(idx,1);
  ra('PUT','/api/timelines/'+_curTl.id,_curTl,function(){renderTimeline();});
}

function tlAddTrack(){
  if(!_curTl)return;
  if(!_curTl.tracks)_curTl.tracks=[];
  var opts='<option value="all">All Fixtures (Stage)</option>';
  _fixtures.forEach(function(f){opts+='<option value="'+f.id+'">'+escapeHtml(f.name||('Fixture '+f.id))+'</option>';});
  var h='<label>Target</label><select id="trk-fix">'+opts+'</select>';
  h+='<div style="margin-top:.8em"><button class="btn btn-on" onclick="tlAddTrackConfirm()">Add</button></div>';
  document.getElementById('modal-title').textContent='Add Track';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function tlAddTrackConfirm(){
  var val=document.getElementById('trk-fix').value;
  var track={clips:[]};
  if(val==='all'){track.allPerformers=true;}
  else{track.fixtureId=parseInt(val);}
  _curTl.tracks.push(track);
  closeModal();
  ra('PUT','/api/timelines/'+_curTl.id,_curTl,function(){renderTimeline();});
}

function _clipPickerOpts(selectedVal){
  // Combined dropdown: spatial effects + classic actions
  // Values: "sfx:ID" for spatial, "act:ID" for classic action
  var h='<optgroup label="Spatial Effects">';
  _spatialFx.forEach(function(f){h+='<option value="sfx:'+f.id+'"'+(selectedVal==='sfx:'+f.id?' selected':'')+'>'+escapeHtml(f.name)+'</option>';});
  h+='</optgroup><optgroup label="Classic Actions">';
  var aNames=['Blackout','Solid','Fade','Breathe','Chase','Rainbow','Fire','Comet','Twinkle','Strobe','Wipe','Scanner','Sparkle','Gradient','DMX Scene','Pan/Tilt Move','Gobo Select','Color Wheel','Track'];
  _acts.forEach(function(a){
    var tn=aNames[a.type]||'Type '+a.type;
    h+='<option value="act:'+a.id+'"'+(selectedVal==='act:'+a.id?' selected':'')+'>'+escapeHtml(a.name)+' ('+tn+')</option>';
  });
  h+='</optgroup>';
  return h;
}
function _clipPickerVal(clip){
  if(clip.actionId!=null)return 'act:'+clip.actionId;
  return 'sfx:'+(clip.effectId||0);
}
function _clipParseVal(val){
  // Returns {effectId:N} or {actionId:N}
  if(!val)return{effectId:0};
  var parts=val.split(':');
  if(parts[0]==='act')return{actionId:parseInt(parts[1])};
  return{effectId:parseInt(parts[1])};
}

function editClip(ti,ci){
  if(!_curTl||!_curTl.tracks||!_curTl.tracks[ti])return;
  var clip=_curTl.tracks[ti].clips[ci];
  var h='<label>Effect / Action</label><select id="clip-fx">'+_clipPickerOpts(_clipPickerVal(clip))+'</select>';
  h+='<label>Start (s)</label><input id="clip-start" type="number" min="0" step="0.1" value="'+clip.startS+'" style="width:100px">';
  h+='<label>Duration (s)</label><input id="clip-dur" type="number" min="0.1" step="0.1" value="'+clip.durationS+'" style="width:100px">';
  h+='<div style="margin-top:.8em;display:flex;gap:.5em">';
  h+='<button class="btn btn-on" onclick="saveClip('+ti+','+ci+')">Save</button>';
  h+='<button class="btn btn-off" onclick="removeClip('+ti+','+ci+')">Remove</button>';
  h+='</div>';
  document.getElementById('modal-title').textContent='Edit Clip';
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function saveClip(ti,ci){
  var clip=_curTl.tracks[ti].clips[ci];
  var parsed=_clipParseVal(document.getElementById('clip-fx').value);
  delete clip.effectId;delete clip.actionId;
  if(parsed.actionId!=null)clip.actionId=parsed.actionId;
  else clip.effectId=parsed.effectId;
  clip.startS=parseFloat(document.getElementById('clip-start').value)||0;
  clip.durationS=parseFloat(document.getElementById('clip-dur').value)||1;
  closeModal();
  ra('PUT','/api/timelines/'+_curTl.id,_curTl,function(){renderTimeline();});
}

function removeClip(ti,ci){
  _curTl.tracks[ti].clips.splice(ci,1);
  closeModal();
  ra('PUT','/api/timelines/'+_curTl.id,_curTl,function(){renderTimeline();});
}

// Add clip via double-click on track row (future enhancement stub)
function tlAddClipToTrack(ti){
  if(!_curTl||(!_spatialFx.length&&!_acts.length)){alert('Create effects or actions first.');return;}
  var h='<label>Effect / Action</label><select id="clip-fx">'+_clipPickerOpts('')+'</select>';
  h+='<label>Start (s)</label><input id="clip-start" type="number" min="0" step="0.1" value="0" style="width:100px">';
  h+='<label>Duration (s)</label><input id="clip-dur" type="number" min="0.1" step="0.1" value="5" style="width:100px">';
  h+='<div style="margin-top:.8em"><button class="btn btn-on" onclick="tlAddClipConfirm('+ti+')">Add Clip</button></div>';
  document.getElementById('modal-title').textContent='Add Clip to Track '+(ti+1);
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';
}

function tlAddClipConfirm(ti){
  var parsed=_clipParseVal(document.getElementById('clip-fx').value);
  var clip={
    startS:parseFloat(document.getElementById('clip-start').value)||0,
    durationS:parseFloat(document.getElementById('clip-dur').value)||5
  };
  if(parsed.actionId!=null)clip.actionId=parsed.actionId;
  else clip.effectId=parsed.effectId;
  _curTl.tracks[ti].clips.push(clip);
  closeModal();
  ra('PUT','/api/timelines/'+_curTl.id,_curTl,function(){renderTimeline();});
}

// Timeline playback preview
function tlTogglePreview(){
  if(_tlPlaying){tlPause();return;}
  if(!_curTl)return;
  _tlPlaying=true;
  // Resume from current position (don't reset to 0 unless at end)
  var dur=_curTl.durationS||60;
  if(_tlPlayT>=dur)_tlPlayT=0;
  document.getElementById('tl-play-btn').textContent='\u23f8 Pause';
  var _tlLastFrame=performance.now();
  _tlPlayTimer=setInterval(function(){
    var now=performance.now();
    _tlPlayT+=(now-_tlLastFrame)/1000;
    _tlLastFrame=now;
    var dur=_curTl.durationS||60;
    if(_tlPlayT>=dur){
      if(_curTl.loop){_tlPlayT=0;}
      else{tlStopPreview();return;}
    }
    // Update playhead
    var ph=document.getElementById('tl-playhead');
    if(ph)ph.style.left=(60+_tlPlayT*_tlPxPerSec)+'px';
    // Update time display
    var td=document.getElementById('tl-time');
    if(td){var m=Math.floor(_tlPlayT/60),s=(_tlPlayT%60).toFixed(1);td.textContent=(m<10?'0':'')+m+':'+(s<10?'0':'')+s;}
  },100);
}

function tlPause(){
  // Pause preview at current position (don't reset)
  if(!_tlPlaying)return;
  _tlPlaying=false;
  if(_tlPlayTimer){clearInterval(_tlPlayTimer);_tlPlayTimer=null;}
  var btn=document.getElementById('tl-play-btn');if(btn)btn.textContent='\u25b6 Resume';
}

function tlRewind(){
  // Reset playhead to start
  _tlPlayT=0;
  var ph=document.getElementById('tl-playhead');if(ph)ph.style.left='60px';
  var td=document.getElementById('tl-time');if(td)td.textContent='00:00.0';
  if(_tlPlaying){tlPause();}
  var btn=document.getElementById('tl-play-btn');if(btn)btn.textContent='\u25b6 Preview';
}

function tlStop(){
  // Smart stop: stops live show if running, otherwise stops preview
  if(_emuRunning||_curTl){
    // Stop live show on hardware + DMX
    emuStop();
    if(_curTl){
      ra('POST','/api/timelines/'+_curTl.id+'/stop',{},function(r){
        if(r&&r.ok)document.getElementById('hs').textContent='Show stopped';
        else document.getElementById('hs').textContent='Show stop sent';
      });
    }
  }
  // Also stop local preview
  _tlPlaying=false;_tlPlayT=0;
  if(_tlPlayTimer){clearInterval(_tlPlayTimer);_tlPlayTimer=null;}
  var btn=document.getElementById('tl-play-btn');if(btn)btn.textContent='\u25b6 Preview';
  var ph=document.getElementById('tl-playhead');if(ph)ph.style.left='60px';
  var td=document.getElementById('tl-time');if(td)td.textContent='00:00.0';
}

// Keep backward compat name for any internal references
function tlStopPreview(){tlRewind();}
function tlStopShow(){tlStop();}

// ── Phase 5: Baking ─────────────────────────────────────────────────────────
function tlBake(){
  if(!_curTl){alert('No timeline selected');return;}
  // Show progress modal
  var h='<div id="bake-status" style="font-size:.85em;color:#94a3b8">Starting bake...</div>';
  h+='<div class="prog-bar" style="margin:.8em 0"><div class="prog-fill" id="bake-prog" style="width:0%"></div></div>';
  h+='<div id="bake-detail" style="font-size:.8em;color:#64748b"></div>';
  document.getElementById('modal-title').textContent='Baking: '+_curTl.name;
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';

  ra('POST','/api/timelines/'+_curTl.id+'/bake',{},function(r){
    if(!r||!r.ok){document.getElementById('bake-status').textContent='Error: '+(r&&r.err||'failed');return;}
    // Poll progress
    var poll=setInterval(function(){
      ra('GET','/api/timelines/'+_curTl.id+'/baked/status',null,function(s){
        if(!s)return;
        var bar=document.getElementById('bake-prog');
        var stat=document.getElementById('bake-status');
        var det=document.getElementById('bake-detail');
        if(bar)bar.style.width=s.progress+'%';
        if(stat)stat.textContent=s.status+' — Frame '+s.frame+' / '+s.totalFrames;
        if(det){
          var segs='';for(var k in s.segments||{}){segs+='Fixture '+k+': '+s.segments[k]+' segments. ';}
          det.textContent=segs;
        }
        if(s.done||s.error){
          clearInterval(poll);
          if(s.error){if(stat)stat.textContent='Error: '+s.error;}
          else{
            if(stat)stat.textContent='Bake complete! Syncing to fixtures...';
            if(det)det.textContent=segs;
            // Auto-sync to fixtures after bake
            ra('POST','/api/timelines/'+_curTl.id+'/baked/sync',null,function(sr){
              if(sr&&sr.ok){
                if(stat)stat.textContent='Synced to '+sr.synced+' fixture(s). Ready to start.';
              } else {
                if(stat)stat.textContent='Bake done but sync failed: '+(sr&&sr.err||sr&&sr.warn||'?');
              }
            });
          }
        }
      });
    },500);
  });
}

// ── Phase 6: Show Execution ─────────────────────────────────────────────────
function tlSyncAndStart(){
  if(!_curTl){alert('No timeline selected');return;}

  // Show sync progress modal
  var h='<div id="sync-phase" style="font-size:.9em;color:#22d3ee;font-weight:bold;margin-bottom:.6em">Phase 1: Baking...</div>';
  h+='<div class="prog-bar" style="margin-bottom:.8em"><div class="prog-fill" id="sync-prog" style="width:0%"></div></div>';
  h+='<div id="sync-performers" style="font-size:.82em"></div>';
  h+='<div id="sync-actions" style="margin-top:1em;display:none">';
  h+='<button class="btn btn-on" id="sync-go-btn" onclick="tlGoAfterSync()" disabled>Start Show</button>';
  h+=' <button class="btn btn-off" onclick="closeModal()">Cancel</button>';
  h+='</div>';
  document.getElementById('modal-title').textContent='Sync & Start: '+_curTl.name;
  document.getElementById('modal-body').innerHTML=h;
  document.getElementById('modal').style.display='block';

  var tlId=_curTl.id;

  // Phase 1: Check if bake is fresh, skip if so
  ra('GET','/api/timelines/'+tlId+'/baked',null,function(baked){
    if(baked&&baked.bakedAt&&!baked.err){
      // Bake exists — skip to sync
      _syncPhase('Bake up to date — syncing to fixtures...');_syncProg(100);
      _doSyncPhase(tlId);
      return;
    }
    // No bake or stale — bake first
    _syncPhase('Phase 1: Baking...');
    ra('POST','/api/timelines/'+tlId+'/bake',{},function(br){
    if(!br||!br.ok){_syncPhase('Bake failed: '+(br&&br.err||'?'));return;}
    var bakePoll=setInterval(function(){
      ra('GET','/api/timelines/'+tlId+'/baked/status',null,function(s){
        if(!s)return;
        _syncProg(s.progress||0);
        if(s.done){
          clearInterval(bakePoll);
          if(s.error){_syncPhase('Bake error: '+s.error);return;}
          _doSyncPhase(tlId);
        }
      });
    },500);
  });
  });
}

function _doSyncPhase(tlId){
  _syncPhase('Syncing to fixtures...');_syncProg(0);
  ra('POST','/api/timelines/'+tlId+'/baked/sync',{},function(sr){
    if(!sr||!sr.ok){_syncPhase('Sync failed');return;}
    var syncPoll=setInterval(function(){
      ra('GET','/api/timelines/'+tlId+'/sync/status',null,function(sp){
        if(!sp)return;
        _renderSyncPerformers(sp);
        var total=sp.totalPerformers||1;
        var ready=sp.readyCount||0;
        _syncProg(Math.round(ready/total*100));
        if(sp.done){
          clearInterval(syncPoll);
          if(sp.allReady){
            _syncPhase('All fixtures ready!');_syncProg(100);
          } else {
            _syncPhase('Warning: Not all fixtures verified');
            document.getElementById('sync-go-btn').textContent='Start Anyway';
          }
          document.getElementById('sync-actions').style.display='block';
          document.getElementById('sync-go-btn').disabled=false;
        }
      });
    },400);
  });
}

function _syncPhase(text){var el=document.getElementById('sync-phase');if(el)el.textContent=text;}
function _syncProg(pct){var el=document.getElementById('sync-prog');if(el)el.style.width=pct+'%';}

function _renderSyncPerformers(sp){
  var el=document.getElementById('sync-performers');if(!el)return;
  var perfs=sp.performers||{};
  var h='';
  for(var cid in perfs){
    var p=perfs[cid];
    var icon={pending:'\u23f3',syncing:'\u25b6',verifying:'\ud83d\udd0d',ready:'\u2705',unverified:'\u26a0',failed:'\u274c'}[p.status]||'\u2022';
    var detail='';
    if(p.status==='syncing')detail=' Step '+p.stepsLoaded+'/'+p.totalSteps;
    if(p.status==='verifying')detail=' Verifying...';
    if(p.retries>0)detail+=' (retry '+p.retries+')';
    if(p.error)detail=' '+p.error;
    var col={ready:'#86efac',failed:'#fca5a5',unverified:'#fbbf24'}[p.status]||'#94a3b8';
    h+='<div style="padding:.25em 0;color:'+col+'">'+icon+' <b>'+escapeHtml(p.name)+'</b> <span style="color:#64748b">'+p.ip+'</span>'+detail+'</div>';
  }
  el.innerHTML=h;
}

function tlGoAfterSync(){
  if(!_curTl)return;
  var tlId=_curTl.id;
  closeModal();
  ra('POST','/api/timelines/'+tlId+'/start',{},function(r){
    if(r&&r.ok){
      document.getElementById('hs').textContent='Show started — '+r.started+' fixtures, go in 5s';
      emuStart(tlId);
    } else {
      document.getElementById('hs').textContent='Start failed: '+(r&&r.err||'?');
    }
  });
}

function tlStopShow(){
  if(!_curTl)return;
  emuStop();
  ra('POST','/api/timelines/'+_curTl.id+'/stop',{},function(r){
    if(r&&r.ok)document.getElementById('hs').textContent='Show stopped — '+r.stopped+' fixtures';
  });
}
