// hinkspix.js — HinksPix PRO device configuration modal (#939)
//
// The controller keeps its own port inventory (up to 48 outputs), and SlyLED
// mirrors it on the child record as `child.hinks`. This modal edits that
// mirror, shows the derived universe map, and pushes the result to the device.
//
// Two deliberate behaviours worth knowing:
//  * Pushing config is ALWAYS operator-triggered. Saving here only updates
//    SlyLED's copy; the device is untouched until "Push to controller".
//    Silently reconfiguring live hardware on a UI edit would be the wrong
//    default, and a push can reboot the controller.
//  * Pixel geometry is stage-mm (60 px/m default), never a DMX fraction.
//    The operator corrects lengths in the fixture editor.

var _hpState = {cid: null, hinks: null, map: null, inSync: false};

function hinksConfigure(cid) {
  fetch('/api/hinkspix/' + cid)
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d || !d.ok) { alert('Could not load device: ' + ((d && d.err) || 'unknown')); return; }
      _hpState = {cid: cid, hinks: d.hinks || {}, map: d.map, inSync: !!d.inSync,
                  mapError: d.mapError, engineProtocol: d.engineProtocol,
                  protocolMatch: d.protocolMatch !== false};
      _hpRender();
    })
    .catch(function (e) { alert('Could not load device: ' + e); });
}

function _hpPorts() { return (_hpState.hinks && _hpState.hinks.ports) || []; }

function _hpRender() {
  var h = _hpState.hinks || {}, ports = _hpPorts();
  var body = '';

  body += '<div style="display:flex;gap:1.5em;flex-wrap:wrap;margin-bottom:1em">';
  body += '<div><label style="font-size:.8em;color:#9ab">Base universe</label><br>'
        + '<input id="hp-base" type="number" min="1" max="63999" value="'
        + (h.baseUniverse || 1) + '" style="width:8em"></div>';
  body += '<div><label style="font-size:.8em;color:#9ab">Input protocol</label><br>'
        + '<select id="hp-proto">'
        + ['e131', 'artnet', 'ddp'].map(function (p) {
            return '<option value="' + p + '"' + ((h.protocol === p) ? ' selected' : '') + '>'
                 + p.toUpperCase() + '</option>';
          }).join('')
        + '</select></div>';
  var dmx = h.dmxOut || {};
  body += '<div><label style="font-size:.8em;color:#9ab">DMX out (J3)</label><br>'
        + '<label style="font-size:.85em"><input id="hp-dmx-en" type="checkbox"'
        + (dmx.enabled ? ' checked' : '') + '> universe '
        + '<input id="hp-dmx-uni" type="number" min="1" max="63999" value="'
        + (dmx.universe || '') + '" style="width:6em"></label></div>';
  body += '</div>';

  // Firmware / capability banner. The upload gate is the single most useful
  // fact about a HinksPix, because below it the device cannot be managed.
  var mcpu = h.mcpu, canUpload = h.uploadSupported;
  if (mcpu != null) {
    body += '<div style="margin-bottom:1em;padding:.6em .8em;border-radius:6px;background:'
          + (canUpload ? '#123' : '#421') + ';font-size:.85em">'
          + 'Main CPU <b>MS_' + mcpu + '</b> &middot; web ' + (h.web || '?')
          + ' &middot; max universes ' + (h.maxU || '?')
          + (canUpload ? ' &middot; <span style="color:#6d6">network upload supported</span>'
                       : ' &middot; <span style="color:#fa6">firmware too old for network'
                         + ' upload (needs MS_151+) — update via SD card first</span>')
          + '</div>';
  }

  // #940 — the controller listens on one protocol; the orchestrator streams on
  // whichever engine is configured. A mismatch means frames go nowhere.
  if (!_hpState.protocolMatch) {
    body += '<div style="margin-bottom:1em;padding:.6em .8em;border-radius:6px;'
          + 'background:#511;font-size:.85em">Protocol mismatch: the controller '
          + 'is configured for <b>' + escapeHtml(String(h.protocol || '?')).toUpperCase()
          + '</b> but SlyLED is streaming <b>' + escapeHtml(String(_hpState.engineProtocol || '?')).toUpperCase()
          + '</b>. Frames will not reach the device until these agree — change the '
          + 'input protocol here and push, or switch the engine in DMX settings.</div>';
  }
  if (_hpState.mapError) {
    body += '<div style="margin-bottom:1em;padding:.6em .8em;border-radius:6px;'
          + 'background:#511;font-size:.85em">Port layout invalid: '
          + escapeHtml(_hpState.mapError) + '</div>';
  } else if (_hpState.map) {
    var m = _hpState.map;
    body += '<div style="margin-bottom:1em;font-size:.85em;color:#9ab">Universes '
          + (m.universes.length ? (m.universes[0] + '–' + m.universes[m.universes.length - 1]
             + ' (' + m.universes.length + ')') : 'none')
          + ' &middot; ' + m.totalChannels + ' channels'
          + ' &middot; <span style="color:' + (_hpState.inSync ? '#6d6' : '#fa6') + '">'
          + (_hpState.inSync ? 'device in sync' : 'config differs from device') + '</span></div>';
  }

  body += '<table class="tbl" style="width:100%;font-size:.85em"><thead><tr>'
        + '<th>On</th><th>Port</th><th>Pixels</th><th>Length (mm)</th><th>Protocol</th>'
        + '<th>Order</th><th>Null</th><th>Bright</th><th>Gamma</th><th>Universe</th>'
        + '</tr></thead><tbody>';
  var uniByPort = {};
  if (_hpState.map) {
    _hpState.map.spans.forEach(function (s) {
      if (s.kind !== 'pixel') return;
      (uniByPort[s.port] = uniByPort[s.port] || []).push(s.universe);
    });
  }
  for (var i = 1; i <= 48; i++) {
    var p = ports.filter(function (x) { return x.port === i; })[0] || {port: i, leds: 0, enabled: false};
    var u = uniByPort[i];
    body += '<tr>'
      + '<td><input type="checkbox" class="hp-en" data-port="' + i + '"' + (p.enabled ? ' checked' : '') + '></td>'
      + '<td>' + i + '</td>'
      + '<td><input type="number" class="hp-leds" data-port="' + i + '" min="0" max="1024" value="' + (p.leds || 0) + '" style="width:5em"></td>'
      + '<td><input type="number" class="hp-mm" data-port="' + i + '" min="0" value="' + (p.mm != null ? p.mm : Math.round((p.leds || 0) * 16.67)) + '" style="width:6em"></td>'
      + '<td><select class="hp-proto" data-port="' + i + '">'
        + ['ws2811', 'ws2801', 'tls3001', 'apa102'].map(function (x) {
            return '<option' + ((p.protocol === x) ? ' selected' : '') + '>' + x + '</option>'; }).join('')
      + '</select></td>'
      + '<td><select class="hp-order" data-port="' + i + '">'
        + ['RGB', 'RBG', 'GRB', 'GBR', 'BRG', 'BGR', 'RGBW', 'WRGB'].map(function (x) {
            return '<option' + ((p.colorOrder === x) ? ' selected' : '') + '>' + x + '</option>'; }).join('')
      + '</select></td>'
      + '<td><input type="number" class="hp-null" data-port="' + i + '" min="0" max="10" value="' + (p.nullPixels || 0) + '" style="width:4em"></td>'
      + '<td><input type="number" class="hp-bri" data-port="' + i + '" min="15" max="100" step="10" value="' + (p.brightness != null ? p.brightness : 100) + '" style="width:5em"></td>'
      + '<td><input type="number" class="hp-gamma" data-port="' + i + '" min="1" max="4" value="' + (p.gamma || 1) + '" style="width:4em"></td>'
      + '<td style="color:#9ab">' + (u ? u.join(', ') : '—') + '</td>'
      + '</tr>';
  }
  body += '</tbody></table>';

  body += '<div style="margin-top:1em;display:flex;gap:.5em;flex-wrap:wrap">'
    + '<button class="btn btn-on" onclick="hinksSave()">Save</button>'
    + '<button class="btn" style="background:#dc2626;color:#fff" onclick="hinksPush()">Push to controller</button>'
    + '<button class="btn" style="background:#335;color:#fff" onclick="hinksReadback()">Read back</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksFixturesFromPorts()">Create fixtures from ports</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksProbe()">Probe</button>'
    + '<button class="btn" style="background:#059669;color:#fff" onclick="hinksStandalone(' + _hpState.cid + ')">Standalone playback →</button>'
    + '</div>'
    + '<div id="hp-result" style="margin-top:.8em;font-size:.85em"></div>';

  // Same modal mechanism the rest of the SPA uses (see showAddFixtureModal).
  _modalStack = [];
  document.getElementById('modal-title').textContent = 'HinksPix PRO — port configuration';
  document.getElementById('modal-body').innerHTML = body;
  document.getElementById('modal').style.display = 'block';
}

function _hpCollect() {
  var ports = [];
  document.querySelectorAll('.hp-en').forEach(function (el) {
    var i = el.getAttribute('data-port');
    var q = function (cls) { return document.querySelector('.' + cls + '[data-port="' + i + '"]'); };
    var leds = parseInt(q('hp-leds').value, 10) || 0;
    if (!el.checked && leds === 0) return;   // keep the payload small
    ports.push({
      port: parseInt(i, 10), leds: leds,
      mm: parseInt(q('hp-mm').value, 10) || 0,
      protocol: q('hp-proto').value,
      colorOrder: q('hp-order').value,
      nullPixels: parseInt(q('hp-null').value, 10) || 0,
      brightness: parseInt(q('hp-bri').value, 10) || 100,
      gamma: parseInt(q('hp-gamma').value, 10) || 1,
      enabled: el.checked
    });
  });
  return {
    baseUniverse: parseInt(document.getElementById('hp-base').value, 10) || 1,
    protocol: document.getElementById('hp-proto').value,
    dmxOut: {enabled: document.getElementById('hp-dmx-en').checked,
             universe: parseInt(document.getElementById('hp-dmx-uni').value, 10) || null},
    ports: ports
  };
}

function _hpSay(msg, good) {
  var el = document.getElementById('hp-result');
  if (el) el.innerHTML = '<span style="color:' + (good ? '#6d6' : '#f88') + '">' + escapeHtml(msg) + '</span>';
}

function hinksSave() {
  fetch('/api/hinkspix/' + _hpState.cid, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(_hpCollect())
  }).then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
    .then(function (x) {
      if (x.s !== 200) { _hpSay(x.d.err || 'save failed', false); return; }
      _hpState.hinks = x.d.hinks; _hpState.map = x.d.map; _hpState.mapError = null;
      _hpSay('Saved to SlyLED. The controller is unchanged until you push.', true);
      _hpRender();
    }).catch(function (e) { _hpSay(String(e), false); });
}

function hinksPush() {
  if (!confirm('Push this configuration to the controller?\n\n'
             + 'This rewrites its port table and universe map.')) return;
  _hpSay('Pushing…', true);
  fetch('/api/hinkspix/' + _hpState.cid + '/push-config', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
  }).then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
    .then(function (x) {
      _hpSay(x.s === 200 ? ('Pushed: ' + (x.d.steps || []).join(', '))
                         : ('Push failed: ' + (x.d.err || '') + ' (completed: '
                            + ((x.d.completed || []).join(', ') || 'nothing') + ')'),
             x.s === 200);
      if (x.s === 200) hinksConfigure(_hpState.cid);
    }).catch(function (e) { _hpSay(String(e), false); });
}

function hinksProbe() {
  _hpSay('Probing…', true);
  fetch('/api/hinkspix/' + _hpState.cid + '/probe', {method: 'POST'})
    .then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
    .then(function (x) {
      if (x.s !== 200) { _hpSay(x.d.err || 'probe failed', false); return; }
      _hpSay('Online — MCPU ' + (x.d.info.mcpuRaw || '?') + ', web ' + (x.d.info.web || '?'), true);
      hinksConfigure(_hpState.cid);
    }).catch(function (e) { _hpSay(String(e), false); });
}

function hinksReadback() {
  _hpSay('Reading…', true);
  fetch('/api/hinkspix/' + _hpState.cid + '/readback')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var rb = d.readback || {};
      var el = document.getElementById('hp-result');
      if (!el) return;
      // Rendered raw on purpose: these CGIs emit undocumented comma-separated
      // text, so parsing it speculatively would invent structure.
      el.innerHTML = '<div style="font-size:.78em"><b>Device readback</b>'
        + Object.keys(rb).filter(function (k) { return k !== 'errors'; }).map(function (k) {
            return '<div style="margin-top:.4em"><span style="color:#9ab">' + k + '</span>'
                 + '<pre style="white-space:pre-wrap;word-break:break-all;margin:.2em 0;'
                 + 'background:#112;padding:.4em;border-radius:4px">'
                 + escapeHtml(String(rb[k] == null ? '(unavailable)' : rb[k]).slice(0, 1200))
                 + '</pre></div>'; }).join('')
        + '</div>';
    }).catch(function (e) { _hpSay(String(e), false); });
}

function hinksFixturesFromPorts() {
  fetch('/api/hinkspix/' + _hpState.cid + '/fixtures-from-ports', {method: 'POST'})
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) { _hpSay(d.err || 'failed', false); return; }
      _hpSay('Created ' + (d.created || []).length + ' fixture(s)'
             + ((d.skipped || []).length ? ('; ports already bound: ' + d.skipped.join(', ')) : ''),
             true);
      if (typeof loadFixtures === 'function') loadFixtures();
    }).catch(function (e) { _hpSay(String(e), false); });
}


// ── Standalone scheduled playback (#941) ─────────────────────────────────────
// The controller plays .hseq sequences from its SD card against an on-board
// day/time schedule, with SlyLED switched off entirely. There is no "play now"
// verb — the schedule IS the mechanism, so this panel edits days and times.
//
// Note the controller cannot express a window crossing midnight; the server
// rejects one and tells you how to split it.

var _hpDeploy = {config: null, progress: null, poll: null};
var _HP_DAYS = ['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY'];

function hinksStandalone(cid) {
  fetch('/api/hinkspix/' + cid + '/deploy')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d || !d.ok) { alert('Could not load: ' + ((d && d.err) || '?')); return; }
      _hpState.cid = cid;
      _hpDeploy.config = d.config || {};
      _hpDeploy.progress = d.progress || {};
      _hpRenderStandalone();
    });
}

function _hpRenderStandalone() {
  var cfg = _hpDeploy.config || {};
  var rows = cfg.schedule || [];
  var last = cfg.lastDeploy;
  var body = '';

  body += '<p style="font-size:.85em;color:#9ab">Sequences are rendered here, uploaded to '
        + 'the controller\'s SD card, and played against its own clock — SlyLED does not '
        + 'need to be running.</p>';

  body += '<div style="margin-bottom:1em"><label style="font-size:.8em;color:#9ab">Playlist name</label><br>'
        + '<input id="hp-plname" value="' + escapeHtml(cfg.playlistName || 'SHOW')
        + '" maxlength="20" style="width:14em"> '
        + '<span style="font-size:.75em;color:#789">uppercase A-Z 0-9, max 20</span></div>';

  body += '<div style="margin-bottom:1em"><label style="font-size:.8em;color:#9ab">Sequences</label>'
        + '<div id="hp-items" style="font-size:.85em">';
  (cfg.items || []).forEach(function (it, i) {
    body += '<div>' + (i + 1) + '. timeline #' + it.timelineId
          + ' <button class="btn" style="font-size:.7em;padding:.1em .4em;background:#633;color:#fff" '
          + 'onclick="_hpRemoveItem(' + i + ')">remove</button></div>';
  });
  if (!(cfg.items || []).length) body += '<div style="color:#f88">No sequences selected — add one below.</div>';
  body += '</div><div style="margin-top:.4em"><input id="hp-addtid" type="number" min="1" '
        + 'placeholder="timeline id" style="width:9em"> '
        + '<button class="btn" style="background:#446;color:#fff" onclick="_hpAddItem()">Add</button></div></div>';

  body += '<div style="margin-bottom:1em"><label style="font-size:.8em;color:#9ab">Schedule</label>'
        + '<table class="tbl" style="width:100%;font-size:.85em"><thead><tr>'
        + '<th>Days</th><th>Start</th><th>End</th><th>Repeat</th><th>On</th><th></th>'
        + '</tr></thead><tbody id="hp-sched">';
  rows.forEach(function (r, i) {
    body += '<tr><td>' + _HP_DAYS.map(function (d) {
        var on = (r.days || []).some(function (x) { return d.indexOf(String(x).toUpperCase().slice(0, 3)) === 0; });
        return '<label style="margin-right:.3em;font-size:.75em"><input type="checkbox" class="hp-day" '
             + 'data-row="' + i + '" data-day="' + d + '"' + (on ? ' checked' : '') + '>' + d.slice(0, 3) + '</label>';
      }).join('') + '</td>'
      + '<td><input type="time" class="hp-start" data-row="' + i + '" value="' + escapeHtml(r.start || '20:00') + '"></td>'
      + '<td><input type="time" class="hp-end" data-row="' + i + '" value="' + escapeHtml(r.end || '23:00') + '"></td>'
      + '<td><input type="number" class="hp-rep" data-row="' + i + '" min="0" value="' + (r.repeat || 0) + '" style="width:4em" title="0 = repeat until the end time"></td>'
      + '<td><input type="checkbox" class="hp-on" data-row="' + i + '"' + (r.enabled !== false ? ' checked' : '') + '></td>'
      + '<td><button class="btn" style="font-size:.7em;padding:.1em .4em;background:#633;color:#fff" onclick="_hpRemoveRow(' + i + ')">remove</button></td></tr>';
  });
  body += '</tbody></table>'
        + '<button class="btn" style="margin-top:.4em;background:#446;color:#fff" onclick="_hpAddRow()">Add schedule row</button>'
        + '<div style="font-size:.75em;color:#789;margin-top:.3em">A window cannot cross midnight — '
        + 'split 8pm–1am into 20:00–23:59 and 00:00–01:00 on the next day.</div></div>';

  if (last) {
    body += '<div style="margin-bottom:1em;padding:.6em .8em;border-radius:6px;background:'
          + (last.ok ? '#123' : '#511') + ';font-size:.8em"><b>On the controller</b> — '
          + (last.ok ? 'deployed' : 'FAILED') + ' '
          + new Date((last.at || 0) * 1000).toLocaleString()
          + (last.err ? (': ' + escapeHtml(last.err)) : '')
          + '<div style="margin-top:.3em;max-height:140px;overflow:auto">'
          + (last.files || []).map(function (f) {
              return '<div>' + (f.ack ? '✓' : '✗') + ' ' + escapeHtml(f.name)
                   + ' <span style="color:#789">' + (f.bytes || 0) + ' B'
                   + (f.sha256 ? (' · ' + f.sha256) : '') + '</span></div>'; }).join('')
          + '</div></div>';
  }

  var prog = _hpDeploy.progress || {};
  body += '<div id="hp-deploy-progress" style="margin-bottom:.8em;font-size:.85em">'
        + (prog.running ? ('Deploying — ' + escapeHtml(prog.message || prog.phase || '')) : '')
        + '</div>';

  body += '<div style="display:flex;gap:.5em;flex-wrap:wrap">'
    + '<button class="btn btn-on" onclick="hinksSaveSchedule()">Save schedule</button>'
    + '<button class="btn" style="background:#dc2626;color:#fff" onclick="hinksDeploy()">Deploy to controller</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksSetClock()">Set clock</button>'
    + '<button class="btn" style="background:#335;color:#fff" onclick="hinksMode(\'standalone\')">Standalone</button>'
    + '<button class="btn" style="background:#335;color:#fff" onclick="hinksMode(\'live\')">Live</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksConfigure(' + _hpState.cid + ')">← Ports</button>'
    + '</div><div id="hp-result" style="margin-top:.8em;font-size:.85em"></div>';

  _modalStack = [];
  document.getElementById('modal-title').textContent = 'HinksPix PRO — standalone playback';
  document.getElementById('modal-body').innerHTML = body;
  document.getElementById('modal').style.display = 'block';
}

function _hpCollectSchedule() {
  var cfg = _hpDeploy.config || {};
  var rows = (cfg.schedule || []).map(function (_, i) {
    var days = [];
    document.querySelectorAll('.hp-day[data-row="' + i + '"]').forEach(function (el) {
      if (el.checked) days.push(el.getAttribute('data-day'));
    });
    var q = function (cls) { return document.querySelector('.' + cls + '[data-row="' + i + '"]'); };
    return {days: days, start: q('hp-start').value, end: q('hp-end').value,
            repeat: parseInt(q('hp-rep').value, 10) || 0, enabled: q('hp-on').checked};
  });
  return {playlistName: document.getElementById('hp-plname').value,
          items: cfg.items || [], schedule: rows};
}

function _hpAddRow() {
  var cfg = _hpDeploy.config || (_hpDeploy.config = {});
  cfg.schedule = _hpCollectSchedule().schedule;
  cfg.schedule.push({days: _HP_DAYS.slice(), start: '20:00', end: '23:00',
                     repeat: 0, enabled: true});
  _hpRenderStandalone();
}
function _hpRemoveRow(i) {
  var cfg = _hpDeploy.config;
  cfg.schedule = _hpCollectSchedule().schedule;
  cfg.schedule.splice(i, 1);
  _hpRenderStandalone();
}
function _hpAddItem() {
  var tid = parseInt(document.getElementById('hp-addtid').value, 10);
  if (!tid) return;
  var cfg = _hpDeploy.config || (_hpDeploy.config = {});
  cfg.schedule = _hpCollectSchedule().schedule;
  (cfg.items = cfg.items || []).push({timelineId: tid});
  _hpRenderStandalone();
}
function _hpRemoveItem(i) {
  var cfg = _hpDeploy.config;
  cfg.schedule = _hpCollectSchedule().schedule;
  cfg.items.splice(i, 1);
  _hpRenderStandalone();
}

function hinksSaveSchedule(silent) {
  var payload = _hpCollectSchedule();
  return fetch('/api/hinkspix/' + _hpState.cid + '/deploy', {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  }).then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
    .then(function (x) {
      if (x.s !== 200) { _hpSay(x.d.err || 'save failed', false); return false; }
      _hpDeploy.config = Object.assign(_hpDeploy.config || {}, x.d.config);
      if (!silent) _hpSay('Schedule saved.', true);
      return true;
    });
}

function hinksDeploy() {
  hinksSaveSchedule(true).then(function (okSaved) {
    if (!okSaved) return;
    if (!confirm('Render and upload to the controller, then switch it to standalone?\n\n'
               + 'Large sequences upload in 580-byte chunks and can take several minutes.')) return;
    fetch('/api/hinkspix/' + _hpState.cid + '/deploy', {method: 'POST'})
      .then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
      .then(function (x) {
        if (x.s !== 200) { _hpSay(x.d.err || 'deploy refused', false); return; }
        _hpPollDeploy();
      });
  });
}

function _hpPollDeploy() {
  if (_hpDeploy.poll) clearInterval(_hpDeploy.poll);
  _hpDeploy.poll = setInterval(function () {
    fetch('/api/hinkspix/' + _hpState.cid + '/deploy')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var p = (d && d.progress) || {};
        var el = document.getElementById('hp-deploy-progress');
        if (el) el.textContent = p.running
          ? ('Deploying — ' + (p.message || p.phase || ''))
          : (p.ok === false ? ('Deploy failed: ' + (p.err || '')) : (p.message || ''));
        if (!p.running) {
          clearInterval(_hpDeploy.poll); _hpDeploy.poll = null;
          _hpDeploy.config = d.config || _hpDeploy.config;
          _hpDeploy.progress = p;
          _hpRenderStandalone();
        }
      });
  }, 1000);
}

function hinksSetClock() {
  fetch('/api/hinkspix/' + _hpState.cid + '/set-clock', {method: 'POST'})
    .then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
    .then(function (x) {
      _hpSay(x.s === 200 ? 'Controller clock set.' : ('Failed: ' + (x.d.err || '')), x.s === 200);
    });
}

function hinksMode(mode) {
  if (!confirm('Switch the controller to ' + mode + ' mode?')) return;
  fetch('/api/hinkspix/' + _hpState.cid + '/mode', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode: mode})
  }).then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
    .then(function (x) {
      _hpSay(x.s === 200 ? ('Controller switched to ' + mode + '.')
                         : ('Failed: ' + (x.d.err || '')), x.s === 200);
    });
}
