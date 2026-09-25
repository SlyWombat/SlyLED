// schedule.js — Runtime → Schedule panel (#954 show scheduler, Phase 1)
//
// The server decides what plays (schedule_eval.py) and plays it
// (show_scheduler.py); this panel edits the document (PUT /api/schedule — the
// only write), shows what the scheduler is doing and why
// (GET /api/schedule/state), draws the week (GET /api/schedule/preview), and
// resumes after a manual override. Timelines are referenced by name; no DMX
// values anywhere. Location (for sunrise/sunset) is entered in Settings.

var _sc = {doc: null, state: null, preview: null, timer: null, dirty: false, msg: null,
           weekFrom: null, sim: null};
var _SC_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
var _SC_DAY_LBL = {mon: 'Mo', tue: 'Tu', wed: 'We', thu: 'Th', fri: 'Fr', sat: 'Sa', sun: 'Su'};
var _SC_REFS = [['clock', 'at time'], ['sunset', 'sunset'], ['sunrise', 'sunrise'],
                ['civilDusk', 'civil dusk'], ['civilDawn', 'civil dawn']];
var _SC_COLOURS = ['#2563eb', '#16a34a', '#d97706', '#db2777', '#7c3aed', '#0891b2', '#65a30d'];

function schedLoad() {
  var el = document.getElementById('sched-panel');
  if (!el) return;
  if (_sc.timer) { clearInterval(_sc.timer); _sc.timer = null; }
  _scFetchAll(true);
  _sc.timer = setInterval(function () {
    var tab = document.getElementById('t-runtime');
    if (!tab || tab.style.display === 'none') { clearInterval(_sc.timer); _sc.timer = null; return; }
    _scFetchState();
  }, 10000);
}

function _scFetch(method, url, body) {
  var o = {method: method};
  if (body !== undefined) { o.headers = {'Content-Type': 'application/json'}; o.body = JSON.stringify(body); }
  return fetch(url, o).then(function (r) {
    return r.json().then(function (d) { return {s: r.status, d: d}; },
                        function () { return {s: r.status, d: {}}; });
  });
}

function _scFetchAll(renderDoc) {
  return Promise.all([_scFetch('GET', '/api/schedule'), _scFetch('GET', '/api/schedule/state')])
    .then(function (o) {
      if (renderDoc || !_sc.dirty) _sc.doc = o[0].d;
      _sc.state = o[1].d;
      _scRender();
      _scFetchPreview();
    });
}

function _scFetchState() {
  _scFetch('GET', '/api/schedule/state').then(function (r) {
    _sc.state = r.d;
    var hd = document.getElementById('sc-head');
    if (hd) hd.innerHTML = _scHeadHtml();
  });
}

function _scFetchPreview() {
  var q = _sc.weekFrom ? ('?date=' + _sc.weekFrom + '&days=7') : '?days=7';
  _scFetch('GET', '/api/schedule/preview' + q).then(function (r) {
    _sc.preview = r.s === 200 ? r.d : null;
    var g = document.getElementById('sc-week');
    if (g) g.innerHTML = _scWeekHtml();
  });
}

// ── helpers ─────────────────────────────────────────────────────────────────

function _scTz() { return (_sc.state && _sc.state.clock && _sc.state.clock.tz) || 'America/Toronto'; }

function _scLocalTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', timeZone: _scTz()});
  } catch (e) { return iso; }
}

function _scLocalDay(iso) {
  try {
    return new Date(iso).toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric', timeZone: _scTz()});
  } catch (e) { return iso; }
}

function _scTlName(id) {
  var n = _sc.state && _sc.state.timelines && _sc.state.timelines[id];
  return n || ('timeline ' + id);
}

function _scTimelineOptions(sel) {
  var tls = (_sc.state && _sc.state.timelines) || {};
  var h = '';
  Object.keys(tls).forEach(function (id) {
    h += '<option value="' + id + '"' + (String(sel) === String(id) ? ' selected' : '') + '>'
      + escapeHtml(tls[id] || ('timeline ' + id)) + '</option>';
  });
  return h;
}

function _scPlayDesc(p) {
  if (!p) return '';
  if (p.kind === 'timeline') return _scTlName(p.timelineId);
  if (p.kind === 'playlist') return 'playlist (' + (p.order || []).map(_scTlName).join(' → ') + ')';
  if (p.kind === 'hold') return 'hold the last frame';
  return 'off (dark)';
}

function _scFmtPos(s) {
  s = Math.max(0, Math.floor(s || 0));
  var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + ':' : '') + (h && m < 10 ? '0' : '') + m + ':' + (x < 10 ? '0' : '') + x;
}

// ── rendering ───────────────────────────────────────────────────────────────

function _scRender() {
  var el = document.getElementById('sched-panel');
  if (!el || !_sc.doc) return;
  var d = _sc.doc;
  var h = '<div style="display:flex;align-items:center;gap:.8em;margin-bottom:.4em">'
    + '<span style="font-weight:600;color:#94a3b8;font-size:.82em;text-transform:uppercase;letter-spacing:.06em">Schedule</span>'
    + '<label style="font-size:.82em;color:#cbd5e1;display:flex;align-items:center;gap:.3em">'
    + '<input type="checkbox" id="sc-enabled" ' + (d.enabled ? 'checked' : '') + ' onchange="_scSetEnabled(this.checked)"> On</label>'
    + '<span style="flex:1"></span>'
    + '<button class="btn" style="font-size:.75em;background:#335;color:#fff" onclick="_scFetchAll(true)">Refresh</button></div>'
    + '<div id="sc-head" style="border:1px solid #334155;border-radius:4px;background:#0a0f1a;padding:.5em .7em;font-size:.84em">'
    + _scHeadHtml() + '</div>'
    + (_sc.msg ? '<div class="sc-msg" style="margin:.4em 0;padding:.4em .6em;border-radius:4px;font-size:.82em;background:'
       + (_sc.msg.good ? '#123' : '#511') + '">' + _sc.msg.html + '</div>' : '')
    + '<div id="sc-week" style="margin-top:.6em">' + _scWeekHtml() + '</div>'
    + _scSettingsHtml() + _scHinksHtml() + _scSchedulesHtml()
    + '<div style="margin-top:.6em;display:flex;gap:.5em;align-items:center;flex-wrap:wrap">'
    + '<button class="btn btn-on" onclick="_scSave()">Save schedule</button>'
    + '<button class="btn" style="background:#335;color:#fff" onclick="_scAddSchedule()">+ Schedule</button>'
    + '<span style="flex:1"></span>'
    + '<label style="font-size:.8em;color:#94a3b8">Simulate a date <input type="date" id="sc-sim-date" onchange="_scSimulate(this.value)"></label></div>'
    + '<div id="sc-sim" style="margin-top:.4em">' + _scSimHtml() + '</div>'
    + _scLogHtml();
  el.innerHTML = h;
}

function _scHeadHtml() {
  var st = _sc.state || {};
  if (!st.enabled) return '<span style="color:#94a3b8">Schedule is off — nothing plays unless you start it.</span>';
  if (st.error) return '<span style="color:#f87171">' + escapeHtml(st.error) + '</span>';
  var h = '';
  var ov = st.override;
  if (ov && ov.active) {
    var since = ov.since ? new Date(ov.since * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';
    h += '<div style="color:#fbbf24"><b>Manual — schedule paused</b>' + (since ? ' since ' + since : '')
      + (ov.by ? ' by ' + escapeHtml(ov.by) : '')
      + (ov.resumeAt ? ' · resumes at ' + new Date(ov.resumeAt * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '')
      + ' <button class="btn btn-on" style="font-size:.8em;padding:.1em .6em" onclick="_scResume()">Resume</button></div>';
  }
  var now = st.now || {};
  if (now.play) {
    var src = now.source === 'entry' ? 'Now' : (now.source === 'quiet' ? 'Quiet hours' : 'Idle');
    var what = now.entry ? (now.entry.name + (now.entry.scheduleName ? ' (' + now.entry.scheduleName + ')' : '')) : _scPlayDesc(now.play);
    h += '<div><b>' + src + ':</b> ' + escapeHtml(what)
      + (now.entry ? ' — ' + escapeHtml(_scPlayDesc(now.play)) : '')
      + (now.window ? ' · until ' + _scLocalTime(now.window.endUtc) + ' · pos ' + _scFmtPos(now.positionS) : '')
      + '</div><div style="color:#94a3b8;font-size:.9em">' + escapeHtml(now.reason || '') + '</div>';
  }
  if (st.next) {
    h += '<div style="margin-top:.2em"><b>Next:</b> ' + escapeHtml(st.next.what) + ' at '
      + _scLocalTime(st.next.atUtc) + ' <span style="color:#94a3b8">(' + _scLocalDay(st.next.atUtc) + ')</span></div>';
  }
  if (st.sun && st.sun.sunset) {
    h += '<div style="color:#94a3b8;font-size:.9em">Sunrise ' + _scLocalTime(st.sun.sunrise) + ' · sunset '
      + _scLocalTime(st.sun.sunset) + ' · ' + escapeHtml(_scTz()) + (st.clock && st.clock.isDst ? ' (DST)' : '') + '</div>';
  }
  var loc = (_sc.doc || {}).location || {};
  if (loc.lat == null || loc.lon == null) {
    h += '<div style="color:#fbbf24;font-size:.9em">No location set — sunrise/sunset entries need it '
      + '(<a href="#" onclick="showTab(\'settings\');return false" style="color:#22d3ee">Settings → Location</a>).</div>';
  }
  return h;
}

function _scSettingsHtml() {
  var d = _sc.doc, idle = d.idle || {}, q = d.quietHours || {}, ov = d.overridePolicy || {};
  var idleSel = idle.kind || 'timeline';
  return '<details style="margin-top:.6em" open><summary style="cursor:pointer;color:#94a3b8;font-size:.82em">Between entries &amp; manual control</summary>'
    + '<div style="display:flex;flex-wrap:wrap;gap:1em;align-items:center;font-size:.82em;margin-top:.4em">'
    + '<label>Idle <select id="sc-idle-kind" onchange="_scDirty()">'
    + '<option value="timeline"' + (idleSel === 'timeline' ? ' selected' : '') + '>play the wash</option>'
    + '<option value="hold"' + (idleSel === 'hold' ? ' selected' : '') + '>hold the last frame</option>'
    + '<option value="off"' + (idleSel === 'off' ? ' selected' : '') + '>off (dark)</option></select></label>'
    + '<label>Wash <select id="sc-idle-tl" onchange="_scDirty()"><option value="">— choose —</option>'
    + _scTimelineOptions(idle.timelineId) + '</select></label>'
    + '<label>Quiet hours <input type="time" id="sc-q-start" value="' + (q.start || '') + '" onchange="_scDirty()"> – '
    + '<input type="time" id="sc-q-end" value="' + (q.end || '') + '" onchange="_scDirty()"></label>'
    + '<label title="A manual Start/Stop/Next pauses the schedule. With this on it picks up again at the next window edge; off = until you press Resume.">'
    + '<input type="checkbox" id="sc-autoresume" ' + (ov.autoResumeAtBoundary !== false ? 'checked' : '') + ' onchange="_scDirty()"> After a manual stop, resume at the next window edge</label>'
    + '</div>'
    + (idleSel === 'off' ? '<div style="color:#fbbf24;font-size:.8em;margin-top:.3em">Idle is off: the stage goes dark between entries.</div>' : '')
    + '</details>';
}

// ── Phase 2: HinksPix offline hand-off ──────────────────────────────────────

function _scHinksHtml() {
  var ctls = (_sc.state && _sc.state.hinkspixControllers) || [];
  if (!ctls.length) return '';
  var hp = (_sc.doc.hinkspix || {});
  var sel = hp.controllers || [];
  var pol = hp.handoff || 'manual';
  var compiled = (_sc.state && _sc.state.hinkspix) || {};
  var h = '<details style="margin-top:.6em"' + (sel.length ? ' open' : '') + '><summary style="cursor:pointer;color:#94a3b8;font-size:.82em">'
    + 'HinksPix — keep playing when SlyLED is off</summary><div style="font-size:.82em;margin-top:.4em">'
    + '<div style="color:#94a3b8;margin-bottom:.3em">Entries ticked <b>Offline</b> (and the wash) are compiled into the controller\'s own weekday schedule '
    + 'and copied to its SD card. The controller has no calendar, so one compile covers the coming week; SlyLED recompiles nightly while it runs.</div>';
  ctls.forEach(function (c) {
    var info = compiled[String(c.id)] || {};
    h += '<div style="display:flex;gap:.6em;align-items:center;flex-wrap:wrap;margin:.2em 0">'
      + '<label><input type="checkbox" class="sc-hp-ctl" value="' + c.id + '"' + (sel.indexOf(c.id) >= 0 ? ' checked' : '') + ' onchange="_scDirty()"> '
      + escapeHtml(c.name || c.ip) + '</label>'
      + '<button class="btn" style="font-size:.75em;background:#335;color:#fff" onclick="_scCompile(' + c.id + ',false)">Preview compile</button>'
      + '<button class="btn btn-on" style="font-size:.75em" onclick="_scCompile(' + c.id + ',true)">Compile &amp; send</button>'
      + '<span style="color:#94a3b8">' + (info.compiledAt ? 'compiled ' + _scLocalDay(info.compiledAt) + ' for ' + escapeHtml(info.from || '') + '–' + escapeHtml(info.to || '')
        + (info.deploy ? ' · ' + escapeHtml(info.deploy) : '') : 'not compiled yet') + '</span></div>';
  });
  h += '<div style="display:flex;gap:1em;flex-wrap:wrap;margin-top:.3em">'
    + '<label>Hand-off <select id="sc-hp-policy" onchange="_scDirty()">'
    + '<option value="manual"' + (pol === 'manual' ? ' selected' : '') + '>copy files only (switch modes yourself)</option>'
    + '<option value="shutdown"' + (pol === 'shutdown' ? ' selected' : '') + '>standalone when SlyLED exits, live when it starts</option>'
    + '<option value="always"' + (pol === 'always' ? ' selected' : '') + '>standalone after every send</option></select></label>'
    + '<label><input type="checkbox" id="sc-hp-idle" ' + (hp.compileIdle === false ? '' : 'checked') + ' onchange="_scDirty()"> include the wash</label>'
    + '<label><input type="checkbox" id="sc-hp-nightly" ' + (hp.nightly ? 'checked' : '') + ' onchange="_scDirty()"> recompile + send nightly (03:30)</label></div>'
    + '<div id="sc-hp-out" style="margin-top:.3em"></div></div></details>';
  return h;
}

function _scCompile(cid, deploy) {
  if (_sc.dirty) { _scSave(); }
  var out = document.getElementById('sc-hp-out');
  if (out) out.innerHTML = '<span style="color:#94a3b8">' + (deploy ? 'Compiling and sending…' : 'Compiling…') + '</span>';
  _scFetch('POST', '/api/schedule/compile/hinkspix/' + cid, {deploy: !!deploy, horizonDays: 7}).then(function (r) {
    if (!out) return;
    if (r.s !== 200 || !r.d.ok) {
      out.innerHTML = '<span style="color:#f87171">' + escapeHtml((r.d && (r.d.err || (r.d.errors || []).join('; '))) || String(r.s)) + '</span>';
      return;
    }
    var d = r.d.compile || {};
    var rows = '';
    Object.keys(d.days || {}).forEach(function (day) {
      (d.days[day] || []).forEach(function (x) {
        rows += '<tr><td>' + day.slice(0, 3) + '</td><td>' + x.start + '–' + x.end + '</td><td>' + escapeHtml(x.playlist) + '</td></tr>';
      });
    });
    out.innerHTML = '<div style="color:#94a3b8">' + escapeHtml(d.from + ' – ' + d.to) + ' · playlists: '
      + Object.keys(d.playlists || {}).map(function (k) { return escapeHtml(k) + ' (' + d.playlists[k].map(_scTlName).join(', ') + ')'; }).join('; ')
      + (r.d.deploy ? ' · <b>' + escapeHtml(r.d.deploy) + '</b>' : '') + '</div>'
      + (rows ? '<table class="tbl" style="font-size:.78em">' + rows + '</table>' : '<div>No offline rows this week.</div>')
      + (d.warnings || []).map(function (w) { return '<div style="color:#fbbf24">' + escapeHtml(w) + '</div>'; }).join('');
    _scFetchState();
  });
}

function _scRefHtml(prefix, ref, allowDuration) {
  ref = ref || {ref: 'clock', time: '18:00'};
  var kind = ('durationMin' in ref) ? 'duration' : (ref.ref || 'clock');
  var opts = _SC_REFS.slice();
  if (allowDuration) opts.push(['duration', 'for (minutes)']);
  var h = '<select class="' + prefix + '-kind" onchange="_scDirty();_scRefKind(this)">';
  opts.forEach(function (o) { h += '<option value="' + o[0] + '"' + (o[0] === kind ? ' selected' : '') + '>' + o[1] + '</option>'; });
  h += '</select> <input type="time" class="' + prefix + '-time" value="' + (ref.time || '18:00') + '" style="'
    + (kind === 'clock' ? '' : 'display:none') + '" onchange="_scDirty()">'
    + '<input type="number" class="' + prefix + '-off" value="' + (kind === 'duration' ? (ref.durationMin || 60) : (ref.offsetMin || 0))
    + '" style="width:4.5em;' + (kind === 'clock' ? 'display:none' : '') + '" title="' + (kind === 'duration' ? 'minutes' : '± minutes') + '" onchange="_scDirty()">';
  return h;
}

function _scRefKind(sel) {
  var row = sel.parentNode;
  var cls = sel.className.replace('-kind', '');
  var t = row.querySelector('.' + cls + '-time'), o = row.querySelector('.' + cls + '-off');
  if (t) t.style.display = sel.value === 'clock' ? '' : 'none';
  if (o) o.style.display = sel.value === 'clock' ? 'none' : '';
}

function _scPlayHtml(play) {
  play = play || {kind: 'timeline'};
  var k = play.kind || 'timeline';
  return '<select class="sc-play-kind" onchange="_scDirty()">'
    + '<option value="timeline"' + (k === 'timeline' ? ' selected' : '') + '>timeline</option>'
    + '<option value="playlist"' + (k === 'playlist' ? ' selected' : '') + '>show playlist</option>'
    + '<option value="off"' + (k === 'off' ? ' selected' : '') + '>off (dark)</option></select> '
    + '<select class="sc-play-tl" onchange="_scDirty()">' + _scTimelineOptions(play.timelineId) + '</select>';
}

function _scSchedulesHtml() {
  var h = '';
  (_sc.doc.schedules || []).forEach(function (s, si) {
    var season = s.season || {};
    h += '<div class="sc-sched" data-si="' + si + '" style="margin-top:.7em;border:1px solid #334155;border-radius:4px;padding:.5em;background:#0b1220">'
      + '<div style="display:flex;flex-wrap:wrap;gap:.6em;align-items:center;font-size:.82em">'
      + '<input class="sc-s-name" value="' + escapeHtml(s.name || '') + '" placeholder="Schedule name" style="font-weight:bold;width:12em" onchange="_scDirty()">'
      + '<label>Priority <input type="number" class="sc-s-prio" value="' + (s.priority || 0) + '" style="width:4em" onchange="_scDirty()"></label>'
      + '<label>Season <input class="sc-s-from" value="' + escapeHtml(season.from || '') + '" placeholder="MM-DD" style="width:6em" onchange="_scDirty()"> to '
      + '<input class="sc-s-to" value="' + escapeHtml(season.to || '') + '" placeholder="MM-DD" style="width:6em" onchange="_scDirty()"></label>'
      + '<label><input type="checkbox" class="sc-s-en" ' + (s.enabled === false ? '' : 'checked') + ' onchange="_scDirty()"> on</label>'
      + '<span style="flex:1"></span>'
      + '<button class="btn" style="font-size:.75em;background:#335;color:#fff" onclick="_scAddEntry(' + si + ')">+ Entry</button>'
      + '<button class="btn btn-off" style="font-size:.75em" onclick="_scDelSchedule(' + si + ')">Delete</button></div>'
      + '<table class="tbl" style="margin-top:.4em;font-size:.8em"><tr><th>Entry</th><th>Days</th><th>Start</th><th>End</th><th>Play</th><th title="Fade in from dark / fade out to dark, seconds">Fade in/out</th><th>Join late</th><th title="Also play on the HinksPix from its SD card when SlyLED is off (compiled nightly)">Offline</th><th></th></tr>';
    (s.entries || []).forEach(function (e, ei) {
      var days = e.days || [];
      h += '<tr class="sc-entry" data-ei="' + ei + '"><td><input class="sc-e-name" value="' + escapeHtml(e.name || '') + '" style="width:9em" onchange="_scDirty()"></td><td style="white-space:nowrap">';
      _SC_DAYS.forEach(function (dd) {
        var on = days.indexOf(dd) >= 0;
        h += '<label style="margin-right:.15em;cursor:pointer"><input type="checkbox" class="sc-e-day" value="' + dd + '"' + (on ? ' checked' : '') + ' onchange="_scDirty()" style="display:none">'
          + '<span class="sc-chip" onclick="this.previousSibling.checked=!this.previousSibling.checked;this.style.background=this.previousSibling.checked?\'#2563eb\':\'#1e293b\';_scDirty();return false" style="padding:.05em .3em;border-radius:3px;background:' + (on ? '#2563eb' : '#1e293b') + '">' + _SC_DAY_LBL[dd] + '</span></label>';
      });
      h += '</td><td class="sc-e-start" style="white-space:nowrap">' + _scRefHtml('sc-st', e.start, false) + '</td>'
        + '<td class="sc-e-end" style="white-space:nowrap">' + _scRefHtml('sc-en', e.end, true) + '</td>'
        + '<td style="white-space:nowrap">' + _scPlayHtml(e.play) + '</td>'
        + '<td style="white-space:nowrap"><input type="number" min="0" max="60" class="sc-e-fin" value="' + ((e.transition || {}).fadeInS || 0) + '" style="width:3.5em" onchange="_scDirty()"> / '
        + '<input type="number" min="0" max="60" class="sc-e-fout" value="' + ((e.transition || {}).fadeOutS || 0) + '" style="width:3.5em" onchange="_scDirty()"></td>'
        + '<td title="Restarting SlyLED mid-window joins the show where it would be by now"><input type="checkbox" class="sc-e-resume" ' + (e.resume === 'start' ? '' : 'checked') + ' onchange="_scDirty()"></td>'
        + '<td><input type="checkbox" class="sc-e-offline" ' + ((e.hinkspix || {}).compile ? 'checked' : '') + ' onchange="_scDirty()"></td>'
        + '<td><button class="btn btn-off" style="font-size:.75em;padding:.05em .4em" onclick="_scDelEntry(' + si + ',' + ei + ')">✕</button></td></tr>';
    });
    h += '</table></div>';
  });
  if (!(_sc.doc.schedules || []).length) {
    h += '<p style="color:#94a3b8;font-size:.82em;margin-top:.6em">No schedules yet. <b>+ Schedule</b>, then add entries such as '
      + '“every night, sunset −15 min to 23:00, play the eaves show”.</p>';
  }
  return h;
}

function _scWeekHtml() {
  var p = _sc.preview;
  if (!p || !p.segments) return '<div style="color:#64748b;font-size:.8em">Week view loads…</div>';
  var tz = _scTz();
  var dayMs = 86400000;
  var colour = {}, ci = 0;
  var cols = '';
  var start0 = new Date(p.segments[0].startUtc).getTime();
  for (var d = 0; d < (p.days || 7); d++) {
    var dStart = start0 + d * dayMs, dEnd = dStart + dayMs;
    var blocks = '';
    p.segments.forEach(function (sg) {
      var a = Math.max(new Date(sg.startUtc).getTime(), dStart), b = Math.min(new Date(sg.endUtc).getTime(), dEnd);
      if (b <= a) return;
      var col;
      if (sg.play && sg.play.kind === 'off') col = '#111';
      else if (sg.what === 'idle' || sg.what === 'quiet hours') col = sg.play && sg.play.kind === 'timeline' ? '#1e3a2f' : '#1f2937';
      else { if (!colour[sg.what]) colour[sg.what] = _SC_COLOURS[ci++ % _SC_COLOURS.length]; col = colour[sg.what]; }
      var top = (a - dStart) / dayMs * 100, ht = (b - a) / dayMs * 100;
      var tip = sg.what + ' — ' + _scPlayDesc(sg.play) + '\n' + _scLocalTime(sg.startUtc) + '–' + _scLocalTime(sg.endUtc)
        + '\n' + (sg.reason || '') + (sg.losers && sg.losers.length ? '\nwins over: ' + sg.losers.join(', ') : '');
      blocks += '<div title="' + escapeHtml(tip) + '" style="position:absolute;left:0;right:0;top:' + top + '%;height:' + ht
        + '%;background:' + col + ';border-bottom:1px solid #0a0f1a' + (sg.losers && sg.losers.length ? ';background-image:repeating-linear-gradient(45deg,transparent 0 4px,rgba(255,255,255,.12) 4px 6px)' : '') + '"></div>';
    });
    (p.sun || []).forEach(function (s) {
      if (!s.sunset) return;
      var t = new Date(s.sunset).getTime();
      if (t >= dStart && t < dEnd) blocks += '<div title="sunset ' + _scLocalTime(s.sunset) + '" style="position:absolute;left:0;right:0;top:' + ((t - dStart) / dayMs * 100) + '%;border-top:2px dashed #f59e0b"></div>';
      var r = new Date(s.sunrise).getTime();
      if (r >= dStart && r < dEnd) blocks += '<div title="sunrise ' + _scLocalTime(s.sunrise) + '" style="position:absolute;left:0;right:0;top:' + ((r - dStart) / dayMs * 100) + '%;border-top:2px dashed #fde68a"></div>';
    });
    cols += '<div style="flex:1;min-width:0"><div style="font-size:.7em;color:#94a3b8;text-align:center">' + _scLocalDay(new Date(dStart + 3600000).toISOString())
      + '</div><div class="sc-day" style="position:relative;height:180px;border:1px solid #1e293b;background:#0a0f1a">' + blocks + '</div></div>';
  }
  var legend = Object.keys(colour).map(function (k) {
    return '<span style="margin-right:.8em"><span style="display:inline-block;width:.8em;height:.8em;background:' + colour[k] + ';vertical-align:middle"></span> ' + escapeHtml(k) + '</span>';
  }).join('') + '<span style="margin-right:.8em"><span style="display:inline-block;width:.8em;height:.8em;background:#1e3a2f;vertical-align:middle"></span> idle / wash</span>'
    + '<span style="color:#f59e0b">- - sunset</span>';
  var warn = (p.warnings || []).map(function (w) { return '<div style="color:#fbbf24">' + escapeHtml(w) + '</div>'; }).join('');
  return '<div style="display:flex;align-items:center;gap:.5em;font-size:.78em;color:#94a3b8;margin-bottom:.2em">Week '
    + '<button class="btn" style="font-size:.8em;padding:0 .4em;background:#1e293b;color:#cbd5e1" onclick="_scWeekShift(-7)">◀</button>'
    + '<button class="btn" style="font-size:.8em;padding:0 .4em;background:#1e293b;color:#cbd5e1" onclick="_scWeekShift(7)">▶</button>'
    + '<span>00:00 at top · 24:00 at bottom · hover a block for why</span></div>'
    + '<div style="display:flex;gap:2px">' + cols + '</div><div style="font-size:.72em;color:#94a3b8;margin-top:.2em">' + legend + '</div>'
    + '<div style="font-size:.78em;margin-top:.2em">' + warn + '</div>';
}

function _scWeekShift(days) {
  var base = _sc.weekFrom ? new Date(_sc.weekFrom + 'T12:00:00') : new Date();
  base.setDate(base.getDate() + days);
  _sc.weekFrom = base.toISOString().slice(0, 10);
  _scFetchPreview();
}

function _scSimulate(date) {
  if (!date) return;
  _scFetch('GET', '/api/schedule/preview?date=' + date + '&days=1').then(function (r) {
    _sc.sim = r.s === 200 ? r.d : {err: (r.d && r.d.err) || r.s};
    var el = document.getElementById('sc-sim');
    if (el) el.innerHTML = _scSimHtml();
  });
}

function _scSimHtml() {
  var s = _sc.sim;
  if (!s) return '';
  if (s.err) return '<div style="color:#f87171;font-size:.8em">' + escapeHtml(String(s.err)) + '</div>';
  var rows = (s.segments || []).map(function (sg) {
    return '<tr><td>' + _scLocalTime(sg.startUtc) + '–' + _scLocalTime(sg.endUtc) + '</td><td><b>' + escapeHtml(sg.what)
      + '</b></td><td>' + escapeHtml(_scPlayDesc(sg.play)) + '</td><td style="color:#94a3b8">' + escapeHtml(sg.reason || '') + '</td></tr>';
  }).join('');
  var sun = (s.sun && s.sun[0]) ? 'Sunrise ' + _scLocalTime(s.sun[0].sunrise) + ' · sunset ' + _scLocalTime(s.sun[0].sunset) : '';
  return '<div style="font-size:.8em;color:#94a3b8">' + escapeHtml(s.from) + ' · ' + sun + '</div>'
    + '<table class="tbl" style="font-size:.8em">' + rows + '</table>'
    + (s.warnings || []).concat(s.notes || []).map(function (w) { return '<div style="color:#fbbf24;font-size:.78em">' + escapeHtml(w) + '</div>'; }).join('');
}

function _scLogHtml() {
  var lg = (_sc.state && _sc.state.log) || [];
  if (!lg.length) return '';
  return '<details style="margin-top:.6em"><summary style="cursor:pointer;color:#94a3b8;font-size:.82em">Recent scheduler activity</summary>'
    + '<div style="font-size:.78em;font-family:monospace;color:#cbd5e1;max-height:12em;overflow:auto">'
    + lg.slice().reverse().map(function (e) { return '<div>' + _scLocalTime(e.at) + ' ' + escapeHtml(e.msg) + '</div>'; }).join('')
    + '</div></details>';
}

// ── editing ─────────────────────────────────────────────────────────────────

function _scDirty() { _sc.dirty = true; }

function _scReadRef(cell, prefix) {
  var kind = cell.querySelector('.' + prefix + '-kind').value;
  var t = cell.querySelector('.' + prefix + '-time').value;
  var o = parseFloat(cell.querySelector('.' + prefix + '-off').value || '0');
  if (kind === 'clock') return {ref: 'clock', time: t || '00:00'};
  if (kind === 'duration') return {durationMin: o > 0 ? o : 60};
  return {ref: kind, offsetMin: isNaN(o) ? 0 : o};
}

function _scCollect() {
  var d = JSON.parse(JSON.stringify(_sc.doc));
  var kind = document.getElementById('sc-idle-kind');
  if (kind) {
    var tl = document.getElementById('sc-idle-tl').value;
    d.idle = {kind: kind.value};
    if (kind.value === 'timeline') d.idle.timelineId = tl ? parseInt(tl, 10) : null;
    var qs = document.getElementById('sc-q-start').value, qe = document.getElementById('sc-q-end').value;
    d.quietHours = (qs && qe) ? {start: qs, end: qe} : null;
    d.overridePolicy = {autoResumeAtBoundary: document.getElementById('sc-autoresume').checked};
  }
  var hp = document.getElementById('sc-hp-policy');
  if (hp) {
    var ctl = [];
    document.querySelectorAll('.sc-hp-ctl').forEach(function (cb) { if (cb.checked) ctl.push(parseInt(cb.value, 10)); });
    d.hinkspix = {handoff: hp.value, compileIdle: document.getElementById('sc-hp-idle').checked,
                  nightly: document.getElementById('sc-hp-nightly').checked, controllers: ctl};
  }
  var nextS = 1;
  (d.schedules || []).forEach(function (s) { if (typeof s.id === 'number' && s.id >= nextS) nextS = s.id + 1; });
  document.querySelectorAll('.sc-sched').forEach(function (box) {
    var si = parseInt(box.getAttribute('data-si'), 10);
    var s = d.schedules[si];
    s.name = box.querySelector('.sc-s-name').value.trim() || ('Schedule ' + s.id);
    s.priority = parseInt(box.querySelector('.sc-s-prio').value || '0', 10) || 0;
    var f = box.querySelector('.sc-s-from').value.trim(), t = box.querySelector('.sc-s-to').value.trim();
    s.season = (f && t) ? {from: f, to: t} : null;
    s.enabled = box.querySelector('.sc-s-en').checked;
    box.querySelectorAll('.sc-entry').forEach(function (row) {
      var e = s.entries[parseInt(row.getAttribute('data-ei'), 10)];
      e.name = row.querySelector('.sc-e-name').value.trim() || ('Entry ' + e.id);
      e.days = [];
      row.querySelectorAll('.sc-e-day').forEach(function (cb) { if (cb.checked) e.days.push(cb.value); });
      e.start = _scReadRef(row.querySelector('.sc-e-start'), 'sc-st');
      e.end = _scReadRef(row.querySelector('.sc-e-end'), 'sc-en');
      var pk = row.querySelector('.sc-play-kind').value, ptl = row.querySelector('.sc-play-tl').value;
      if (pk === 'timeline') e.play = {kind: 'timeline', timelineId: ptl ? parseInt(ptl, 10) : null, loop: true};
      else if (pk === 'playlist') e.play = {kind: 'playlist', order: (e.play && e.play.order) || _scPlaylistOrder(), loop: true};
      else e.play = {kind: 'off'};
      e.resume = row.querySelector('.sc-e-resume').checked ? 'position' : 'start';
      var fi = parseFloat(row.querySelector('.sc-e-fin').value || '0') || 0;
      var fo = parseFloat(row.querySelector('.sc-e-fout').value || '0') || 0;
      e.transition = (fi || fo) ? {fadeInS: fi, fadeOutS: fo} : null;
      e.hinkspix = {compile: row.querySelector('.sc-e-offline').checked};
    });
  });
  return d;
}

function _scPlaylistOrder() { return (window._rtPlaylistOrder || []).slice(); }

function _scSave() {
  var d = _scCollect();
  _scFetch('PUT', '/api/schedule', d).then(function (r) {
    if (r.s === 200 && r.d.ok) {
      _sc.dirty = false;
      var w = r.d.warnings || [];
      _sc.msg = {good: true, html: 'Saved.' + (w.length ? '<br>' + w.map(escapeHtml).join('<br>') : '')};
      _scFetchAll(true);
    } else {
      _sc.doc = d;
      _sc.msg = {good: false, html: 'Not saved:<br>' + ((r.d && r.d.errors) || [r.d && r.d.err || r.s]).map(function (x) { return escapeHtml(String(x)); }).join('<br>')};
      _scRender();
    }
  });
}

function _scAddSchedule() {
  _sc.doc = _scCollect();
  var id = 1;
  (_sc.doc.schedules || []).forEach(function (s) { if (s.id >= id) id = s.id + 1; });
  _sc.doc.schedules = (_sc.doc.schedules || []).concat([{id: id, name: 'Schedule ' + id, priority: 10,
    enabled: true, season: null, entries: [], exceptions: []}]);
  _sc.dirty = true;
  _scAddEntry(_sc.doc.schedules.length - 1, true);
}

function _scAddEntry(si, fresh) {
  if (!fresh) _sc.doc = _scCollect();
  var s = _sc.doc.schedules[si];
  var id = 1;
  (s.entries || []).forEach(function (e) { if (e.id >= id) id = e.id + 1; });
  var firstTl = Object.keys((_sc.state && _sc.state.timelines) || {})[0];
  s.entries = (s.entries || []).concat([{id: id, name: 'Evening', days: _SC_DAYS.slice(),
    start: {ref: 'sunset', offsetMin: -15}, end: {ref: 'clock', time: '23:00'},
    play: {kind: 'timeline', timelineId: firstTl ? parseInt(firstTl, 10) : null, loop: true},
    resume: 'position'}]);
  _sc.dirty = true;
  _scRender();
}

function _scDelEntry(si, ei) {
  _sc.doc = _scCollect();
  _sc.doc.schedules[si].entries.splice(ei, 1);
  _sc.dirty = true;
  _scRender();
}

function _scDelSchedule(si) {
  var s = _sc.doc.schedules[si];
  if (!confirm('Delete schedule "' + (s.name || s.id) + '"?')) return;
  _sc.doc = _scCollect();
  _sc.doc.schedules.splice(si, 1);
  _sc.dirty = true;
  _scRender();
}

function _scSetEnabled(on) {
  _scFetch('POST', '/api/schedule/enabled', {enabled: !!on}).then(function () { _scFetchAll(!_sc.dirty); });
}

function _scResume() {
  _scFetch('POST', '/api/schedule/resume', {}).then(function () { setTimeout(function () { _scFetchAll(!_sc.dirty); }, 800); });
}

// ── Settings → Location card ────────────────────────────────────────────────

function schedLocLoad() {
  var el = document.getElementById('sched-loc');
  if (!el) return;
  _scFetch('GET', '/api/schedule').then(function (r) {
    var loc = (r.d && r.d.location) || {};
    el.innerHTML = '<div class="card-title">Location (for sunset / sunrise schedules)</div>'
      + '<div style="display:flex;gap:.8em;flex-wrap:wrap;align-items:flex-end;font-size:.85em">'
      + '<label>Latitude<br><input id="sl-lat" type="number" step="0.0001" min="-90" max="90" value="' + (loc.lat == null ? '' : loc.lat) + '" placeholder="e.g. 43.6532" style="width:8em"></label>'
      + '<label>Longitude<br><input id="sl-lon" type="number" step="0.0001" min="-180" max="180" value="' + (loc.lon == null ? '' : loc.lon) + '" placeholder="e.g. -79.3832" style="width:8em"></label>'
      + '<label>Time zone<br><input id="sl-tz" value="' + escapeHtml(loc.tz || 'America/Toronto') + '" style="width:12em"></label>'
      + '<button class="btn btn-on" onclick="schedLocSave(this)">Save location</button></div>'
      + '<div style="font-size:.75em;color:#64748b;margin-top:.3em">West longitudes are negative. Entered here only — SlyLED never looks up your location.</div>'
      + '<div id="sl-msg" style="font-size:.8em;margin-top:.2em"></div>';
  });
}

function schedLocSave(btn) {
  var body = {lat: document.getElementById('sl-lat').value, lon: document.getElementById('sl-lon').value,
              tz: document.getElementById('sl-tz').value.trim()};
  _scFetch('POST', '/api/schedule/location', body).then(function (r) {
    var m = document.getElementById('sl-msg');
    if (m) m.innerHTML = (r.s === 200 && r.d.ok) ? '<span style="color:#4ade80">Saved.</span>'
      : '<span style="color:#f87171">' + escapeHtml((r.d && r.d.err) || String(r.s)) + '</span>';
  });
}
