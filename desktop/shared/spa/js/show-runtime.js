/** show-runtime.js — Runtime tab: playlist rendering, drag-reorder, bake, show start/stop. Extracted from app.js Phase 4d. */
var _rtTimer=null;

function loadRuntime(){
  if(_rtTimer){clearInterval(_rtTimer);_rtTimer=null;}
  // Load playlist
  ra('GET','/api/show/playlist',null,function(d){
    if(!d)return;
    _rtRenderPlaylist(d);
    var cb=document.getElementById('rt-loop-all');
    if(cb)cb.checked=!!d.loopAll;
  });
  // Start polling show status + fixture grid
  _rtRefresh();
  _rtTimer=setInterval(_rtRefresh,1000);
}

function _rtRenderPlaylist(d){
  var el=document.getElementById('rt-playlist');
  var totalEl=document.getElementById('rt-playlist-total');
  if(!el)return;
  var items=d.items||[];
  if(!items.length){
    el.innerHTML='<div style="padding:.6em;color:#888;font-size:.82em">No timelines in playlist. Go to the <a href="#" onclick="showTab(\'shows\');return false" style="color:#22d3ee">Shows tab</a> to create timelines.</div>';
    if(totalEl)totalEl.textContent='';
    return;
  }
  var h='';
  items.forEach(function(it,i){
    var baked=it.baked;
    var playing=it.playing;
    var cls=playing?' style="border-color:#22d3ee;background:#0f1a2e"':'';
    h+='<div class="li"'+cls+' draggable="true" data-tid="'+it.id+'" data-idx="'+i+'" ondragstart="_rtDragStart(event)" ondragover="_rtDragOver(event)" ondrop="_rtDrop(event)">';
    h+='<div style="display:flex;align-items:center;gap:.6em">';
    h+='<span style="color:#475569;cursor:grab;font-size:1.1em" title="Drag to reorder">&#9776;</span>';
    h+='<span style="color:#64748b;font-size:.75em;width:1.5em">'+(i+1)+'.</span>';
    h+='<b style="flex:1">'+escapeHtml(it.name)+'</b>';
    h+='<span style="color:#64748b;font-size:.82em;font-family:monospace">'+_fmtDur(it.durationS)+'</span>';
    h+='<button class="btn" onclick="_rtPlayFrom('+i+')" style="background:#1e3a5f;color:#93c5fd;padding:.2em .5em;font-size:.75em" title="Start show from here">&#9654;</button>';
    if(baked)h+='<span style="color:#4c4;font-size:.8em" title="Baked">&#x2713;</span>';
    else h+='<button class="btn" onclick="_rtBakeOne('+it.id+')" style="background:#4a2080;color:#c4b5fd;padding:.2em .5em;font-size:.75em">Bake</button>';
    if(items.length>1)h+='<span style="cursor:pointer;color:#f66;font-size:1em;padding:0 .2em" onclick="_rtRemoveItem('+i+')" title="Remove from playlist">&times;</span>';
    h+='</div></div>';
  });
  // Add timeline control
  var playlistIds=items.map(function(it){return it.id;});
  h+='<div style="padding:.4em;border-top:1px solid #1e293b;display:flex;gap:.4em;align-items:center">';
  h+='<select id="rt-add-tl" style="font-size:.78em;flex:1;padding:.2em"><option value="">+ Add timeline...</option></select>';
  h+='<button class="btn" onclick="_rtAddTimeline()" style="font-size:.72em;background:#14532d;color:#86efac;padding:.2em .5em">Add</button>';
  h+='</div>';
  el.innerHTML=h;
  // Populate add-timeline dropdown with all timelines (duplicates allowed for repeats)
  ra('GET','/api/timelines',null,function(tls){
    var sel=document.getElementById('rt-add-tl');if(!sel)return;
    (tls||[]).forEach(function(t){
      var o=document.createElement('option');o.value=t.id;
      o.textContent=t.name+' ('+t.durationS+'s)';sel.appendChild(o);
    });
    if(sel.options.length<=1)sel.options[0].textContent='No timelines available';
  });
  if(totalEl)totalEl.textContent='Total: '+_fmtDur(d.totalDurationS||0);
}

function _fmtDur(s){
  var m=Math.floor(s/60),sec=Math.floor(s%60);
  return m+':'+('0'+sec).slice(-2);
}

function _rtRefresh(){
  // Poll show status
  ra('GET','/api/show/status',null,function(d){
    if(!d)return;
    var timeEl=document.getElementById('rt-time');
    var progEl=document.getElementById('rt-prog-fill');
    var nowEl=document.getElementById('rt-now-playing');
    if(d.running){
      // #831 \u2014 show per-iteration position, not cumulative-since-start.
      // totalElapsed grows past totalDurationS on every loop pass and
      // pegs the bar at 100% forever; currentElapsed/currentDurationS
      // reset on each loop and reflect what the operator actually
      // wants to see ("how far into THIS pass are we?").
      if(timeEl)timeEl.textContent=_fmtDur(d.currentElapsed||0)+' / '+_fmtDur(d.currentDurationS||0);
      var pct=d.currentDurationS>0?Math.min(100,Math.round((d.currentElapsed/d.currentDurationS)*100)):0;
      if(progEl)progEl.style.width=pct+'%';
      if(nowEl)nowEl.textContent=d.currentName?('\u25b8 '+d.currentName):'';
    }else{
      if(timeEl)timeEl.textContent='00:00 / 00:00';
      if(progEl)progEl.style.width='0%';
      if(nowEl)nowEl.textContent='';
    }
  });
}

// ── Playlist drag-reorder ──
var _rtDragIdx=-1;
function _rtDragStart(e){_rtDragIdx=parseInt(e.currentTarget.dataset.idx);e.dataTransfer.effectAllowed='move';}
function _rtDragOver(e){e.preventDefault();e.dataTransfer.dropEffect='move';}
function _rtDrop(e){
  e.preventDefault();
  var targetIdx=parseInt(e.currentTarget.dataset.idx);
  if(_rtDragIdx<0||_rtDragIdx===targetIdx)return;
  // Reorder: remove from old position, insert at new
  ra('GET','/api/show/playlist',null,function(d){
    if(!d)return;
    var order=d.order.slice();
    var item=order.splice(_rtDragIdx,1)[0];
    order.splice(targetIdx,0,item);
    ra('POST','/api/show/playlist',{order:order},function(){
      ra('GET','/api/show/playlist',null,function(d2){if(d2)_rtRenderPlaylist(d2);});
    });
  });
}

function _rtRemoveItem(idx){
  ra('GET','/api/show/playlist',null,function(d){
    if(!d||!d.order)return;
    var order=d.order.slice();
    if(order.length<=1)return; // don't allow empty playlist
    order.splice(idx,1);
    ra('POST','/api/show/playlist',{order:order},function(){
      ra('GET','/api/show/playlist',null,function(d2){if(d2)_rtRenderPlaylist(d2);});
    });
  });
}

function _rtAddTimeline(){
  var sel=document.getElementById('rt-add-tl');
  if(!sel||!sel.value)return;
  var tid=parseInt(sel.value);
  ra('GET','/api/show/playlist',null,function(d){
    if(!d)return;
    var order=(d.order||[]).slice();
    if(order.indexOf(tid)===-1)order.push(tid);
    ra('POST','/api/show/playlist',{order:order},function(){
      ra('GET','/api/show/playlist',null,function(d2){if(d2)_rtRenderPlaylist(d2);});
    });
  });
}

function _rtPlayFrom(idx){
  _rtBakeAll(function(){
    ra('POST','/api/show/start',{startIndex:idx},function(r){
      if(!r||!r.ok){alert(r&&r.err||'Failed to start');return;}
      document.getElementById('hs').textContent='Show started from item '+(idx+1);
    });
  });
}

function _rtLoopToggle(){
  var cb=document.getElementById('rt-loop-all');
  ra('POST','/api/show/playlist',{loopAll:cb?cb.checked:false});
}

function _rtPreviewOne(tid){
  // Preview single timeline in 3D emulator (no live output)
  var sel=document.getElementById('tl-select');
  if(sel)sel.value=tid;
  loadTimelineDetail();
  tlTogglePreview();
}

function _rtPreviewAll(){
  // For now, preview the first timeline in the playlist
  ra('GET','/api/show/playlist',null,function(d){
    if(!d||!d.order||!d.order.length){alert('Playlist is empty. Add timelines on the Shows tab.');return;}
    _rtPreviewOne(d.order[0]);
  });
}

function _rtPause(){tlPause();}
function _rtRewind(){tlRewind();}

// Push baked LED steps to children via LOAD_STEP packets. LED performers
// run autonomously from a preloaded step buffer; without this the child
// receives RUNNER_GO but has nothing to play.
function _rtSyncOne(tid,onDone){
  ra('POST','/api/timelines/'+tid+'/baked/sync',{},function(sr){
    if(!sr||!sr.ok){if(onDone)onDone(sr&&sr.err||'sync failed');return;}
    var poll=setInterval(function(){
      ra('GET','/api/timelines/'+tid+'/sync/status',null,function(sp){
        if(!sp||!sp.done)return;
        clearInterval(poll);
        if(onDone)onDone(null);
      });
    },400);
  });
}

function _rtBakeOne(tid){
  ra('POST','/api/timelines/'+tid+'/bake',{},function(r){
    if(!r)return;
    var poll=setInterval(function(){
      ra('GET','/api/timelines/'+tid+'/baked/status',null,function(s){
        if(!s||s.status==='running')return;
        clearInterval(poll);
        _rtSyncOne(tid,function(){
          ra('GET','/api/show/playlist',null,function(d){if(d)_rtRenderPlaylist(d);});
        });
      });
    },500);
  });
}

function _rtBakeAll(onDone){
  ra('GET','/api/show/playlist',null,function(d){
    if(!d||!d.order||!d.order.length){alert('Playlist is empty.');return;}
    var order=(d.order||[]).slice();
    var unbaked=d.items.filter(function(it){return !it.baked;});
    document.getElementById('hs').textContent=unbaked.length?
      'Baking '+unbaked.length+' timeline(s)...':'Syncing timelines to fixtures...';
    var idx=0;
    function bakeNext(){
      if(idx>=unbaked.length){syncAll(0);return;}
      var tid=unbaked[idx].id;
      ra('POST','/api/timelines/'+tid+'/bake',{},function(){
        var poll=setInterval(function(){
          ra('GET','/api/timelines/'+tid+'/baked/status',null,function(s){
            if(!s||s.status==='running')return;
            clearInterval(poll);idx++;bakeNext();
          });
        },500);
      });
    }
    // Sync every timeline in the playlist — even ones that were already
    // baked. A stale sync (child rebooted, orchestrator restarted) leaves
    // LED performers with empty step buffers and RUNNER_GO falls on deaf
    // ears; re-syncing is cheap and idempotent.
    function syncAll(si){
      if(si>=order.length){
        document.getElementById('hs').textContent='All timelines baked + synced';
        ra('GET','/api/show/playlist',null,function(d2){if(d2)_rtRenderPlaylist(d2);});
        if(onDone)onDone();
        return;
      }
      document.getElementById('hs').textContent='Syncing timeline '+(si+1)+'/'+order.length+'...';
      _rtSyncOne(order[si],function(err){
        if(err){document.getElementById('hs').textContent='Sync failed: '+err;return;}
        syncAll(si+1);
      });
    }
    bakeNext();
  });
}

function _rtStartShow(){
  // Auto-bake unbaked timelines, then start
  _rtBakeAll(function(){
    ra('POST','/api/show/start',{},function(r){
      if(!r||!r.ok){
        alert(r&&r.err||'Failed to start show');
        return;
      }
      document.getElementById('hs').textContent='Show started — '+r.timelines+' timeline(s)';
    });
  });
}

function _rtStopShow(){
  ra('POST','/api/show/stop',{},function(r){
    if(r&&r.ok)document.getElementById('hs').textContent='Show stopped';
  });
  tlStop();
}
