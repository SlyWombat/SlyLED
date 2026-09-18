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
                  mapError: d.mapError};
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
