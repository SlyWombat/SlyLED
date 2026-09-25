// hinkspix_guide.js — first-time HinksPix setup guide (#953)
//
// The Configure wizard (hinkspix_config.js) is a configuration-push tool for
// someone who already knows the controller. This guide walks a first-time
// operator from "controller added" to "my strings are named and lit", in plain
// words, and hands the push itself to that wizard (which owns the snapshot,
// reboot and verify safety net). The five-step wizard stays as the
// "Advanced / re-push" view.
//
//   1 Your controller   what the probe found, in plain words
//   2 Your layout       import from xLights, or set up by hand
//   3 Your strings      pixel count + name per port, validated against the model
//   4 Send              summary, then the push wizard
//   5 Check             Identify each port, colour test, light them all
//
// Identify lights a port using the layout SlyLED holds for the controller, so
// it answers for the right string once that layout has been sent (step 4).

var _hg = {cid: null, step: 1, dev: null, fixtures: [], msg: null, colour: {}};

var _HG_STEPS = ['Your controller', 'Your layout', 'Your strings', 'Send', 'Check'];
var _HG_DENSITIES = [30, 60, 144];

function hinksGuide(cid, step) {
  _modalStack = [];
  _hg.cid = cid;
  _hg.step = step || 1;
  _hg.msg = null;
  _hg.colour = {};
  _hgRender(true);
  _hgLoad().then(function () { _hgRender(); });
}

function _hgFetch(url, opts) {
  return fetch(url, opts).then(function (r) {
    return r.json().then(function (d) { return {s: r.status, d: d}; },
                        function () { return {s: r.status, d: {}}; });
  });
}

function _hgJson(method, url, body) {
  return _hgFetch(url, {method: method, headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(body || {})});
}

function _hgLoad() {
  var id = _hg.cid;
  return Promise.all([_hgFetch('/api/hinkspix/' + id), _hgFetch('/api/fixtures')])
    .then(function (out) {
      _hg.dev = out[0].s === 200 ? out[0].d : null;
      _hg.fixtures = (out[1].s === 200 && Array.isArray(out[1].d)) ? out[1].d : [];
      if (!_hg.dev) _hg.msg = {text: 'Could not read SlyLED’s copy of this controller.', good: false};
    });
}

function _hgSay(text, good) {
  _hg.msg = text ? {text: text, good: good !== false} : null;
  _hgRender();
}

function _hgGo(step) { _hg.step = step; _hg.msg = null; _hgRender(); }

// A message shown in place, without re-rendering the step — a re-render
// rebuilds the strings table from saved state and would wipe what the
// operator has typed but not yet saved.
function _hgNote(text, good) {
  _hg.msg = text ? {text: text, good: good !== false} : null;
  var root = document.getElementById('hg-root');
  if (!root) return;
  var old = root.querySelector('.hg-msg');
  if (old) old.parentNode.removeChild(old);
  if (!text) return;
  var div = document.createElement('div');
  div.innerHTML = _hgMsg();
  var strip = root.firstChild;
  root.insertBefore(div.firstChild, strip ? strip.nextSibling : null);
}

// ── Rendering ────────────────────────────────────────────────────────────────

function _hgRender(force) {
  var el = document.getElementById('modal-body');
  if (!el) return;
  // Same rule as the push wizard (#955): an async completion never paints over
  // a different dialog.
  if (!force && !document.getElementById('hg-root')) return;
  var name = (_hg.dev && _hg.dev.name) || 'HinksPix';
  document.getElementById('modal-title').textContent = 'Set up ' + name;
  el.innerHTML = '<div id="hg-root" style="font-size:.9em">' + _hgStrip()
    + _hgMsg() + (_hg.dev ? _hgBody() : '<p style="color:#9ab">Reading…</p>') + '</div>';
  document.getElementById('modal').style.display = 'block';
}

function _hgStrip() {
  var h = '<div style="display:flex;gap:.35em;margin-bottom:.6em;flex-wrap:wrap">';
  _HG_STEPS.forEach(function (n, i) {
    var cur = (i + 1 === _hg.step);
    h += '<button class="btn" onclick="_hgGo(' + (i + 1) + ')" style="background:'
      + (cur ? '#2563eb' : '#335') + ';color:#fff;' + (cur ? 'font-weight:bold' : 'opacity:.75')
      + '">' + (i + 1) + '. ' + n + '</button>';
  });
  h += '<button class="btn" onclick="hinksConfig(' + _hg.cid + ')" style="margin-left:auto;'
    + 'background:#1e293b;color:#94a3b8;font-size:.8em" title="The five-step push view: '
    + 'read, edit, review, apply, verify">Advanced…</button>';
  return h + '</div>';
}

function _hgMsg() {
  if (!_hg.msg) return '';
  return '<div class="hg-msg" style="margin:.5em 0;padding:.5em .7em;border-radius:6px;background:'
    + (_hg.msg.good ? '#123' : '#511') + '">' + escapeHtml(_hg.msg.text) + '</div>';
}

function _hgHelp(text) {
  return '<details style="margin:.4em 0;color:#94a3b8;font-size:.85em"><summary style="cursor:pointer">'
    + 'What’s this?</summary><div style="margin-top:.3em">' + text + '</div></details>';
}

function _hgBody() {
  switch (_hg.step) {
    case 1: return _hgControllerStep();
    case 2: return _hgLayoutStep();
    case 3: return _hgStringsStep();
    case 4: return _hgSendStep();
    default: return _hgCheckStep();
  }
}

// ── Step 1: the controller in plain words ───────────────────────────────────

var _HG_BOARD_WORDS = {
  Long_Range: ['Long-Range', 'ports need a receiver box on the far end of the cable'],
  Local_SPI: ['Local SPI', 'pixels plug straight in'],
  Local_AC: ['Local AC', 'no pixel ports'],
  Not_Present: ['empty', 'no board fitted']
};

function _hgBoards() {
  var h = (_hg.dev.hinks || {}), raw = h.boards || {}, out = [];
  Object.keys(raw).forEach(function (k) {
    var m = /^BD(\d+)$/i.exec(k) || /^(\d+)$/.exec(k);
    if (!m) return;
    out.push({num: parseInt(m[1], 10), type: raw[k]});
  });
  out.sort(function (a, b) { return a.num - b.num; });
  return out;
}

function _hgControllerStep() {
  var d = _hg.dev, h = d.hinks || {}, caps = d.caps || {};
  var lines = [];
  lines.push('<b>' + escapeHtml(h.model || caps.name || 'HinksPix') + '</b>'
    + (h.mcpu ? ', firmware ' + escapeHtml(String(h.mcpu)) : '') + ', at ' + escapeHtml(d.ip || ''));
  var boards = _hgBoards();
  if (boards.length) {
    boards.forEach(function (b) {
      var w = _HG_BOARD_WORDS[b.type] || [String(b.type), ''];
      var first = (b.num - 1) * 16 + 1, last = b.num * 16;
      lines.push('Board ' + b.num + ' = <b>' + escapeHtml(w[0]) + '</b>'
        + (b.type === 'Not_Present' || b.type === 'Local_AC' ? '' : ' (ports ' + first + '–' + last + ')')
        + (w[1] ? ' — ' + escapeHtml(w[1]) : ''));
    });
  } else {
    lines.push('<span style="color:#fbbf24">The controller didn’t report its boards — '
      + 'press Refresh on its Hardware row, then open this guide again.</span>');
  }
  if (h.maxU) lines.push(h.maxU + ' universes available.');
  var maxPx = Math.floor((caps.maxPixelPortChannels || 2040) / 3);
  lines.push('Each port drives up to ' + maxPx + ' RGB pixels.');
  var proto = '';
  if (d.protocolMatch === false) {
    proto = '<div style="margin:.6em 0;padding:.5em .7em;border-radius:6px;background:#3b2f10;color:#fcd34d">'
      + 'SlyLED is sending <b>' + escapeHtml(d.engineProtocol || '?') + '</b> but this controller '
      + 'is set for <b>' + escapeHtml(h.protocol || '?') + '</b>. They must match or nothing lights: '
      + 'change the DMX protocol in Settings, or the input protocol in Advanced → Edit.</div>';
  }
  return '<ul style="margin:.3em 0 .6em 1.2em;line-height:1.6">' + lines.map(function (l) {
      return '<li>' + l + '</li>'; }).join('') + '</ul>' + proto
    + _hgHelp('A HinksPix is a pixel controller: SlyLED streams colour to it over the network '
      + '(sACN or Art-Net) and it drives the light strings plugged into its ports. Ports are '
      + 'grouped 16 to a board. This guide sets up which ports have strings, how long they are '
      + 'and what they’re called, then sends that to the controller.')
    + '<div style="margin-top:1em"><button class="btn btn-on" onclick="_hgGo(2)">Next: your layout →</button></div>';
}

// ── Step 2: where the layout comes from ─────────────────────────────────────

function _hgLayoutStep() {
  return '<p>Where is your layout?</p>'
    + '<div style="display:flex;gap:.8em;flex-wrap:wrap;margin:.6em 0">'
    + '<button class="btn btn-on" onclick="_hgXlights()" style="padding:.7em 1em">'
    + 'Import from my xLights show folder<br><span style="font-size:.8em;opacity:.8">'
    + 'recommended if you use xLights</span></button>'
    + '<button class="btn" onclick="_hgGo(3)" style="background:#335;color:#fff;padding:.7em 1em">'
    + 'Set up by hand<br><span style="font-size:.8em;opacity:.8">enter each string’s pixel '
    + 'count</span></button></div>'
    + _hgHelp('xLights already knows which port drives which model. Choose your show folder '
      + '(the one with <code>xlights_networks.xml</code> and <code>xlights_rgbeffects.xml</code>), '
      + 'review what SlyLED read, and accept it. Nothing is sent to the controller until the '
      + 'Send step.');
}

function _hgXlights() {
  // The import dialog replaces this one; its Done returns to the guide.
  window._hgReturnAfterXlights = _hg.cid;
  hinksXlightsImport(_hg.cid);
}

// ── Step 3: strings by hand ─────────────────────────────────────────────────

function _hgPortRows() {
  var h = (_hg.dev.hinks || {});
  var byPort = {};
  (h.ports || []).forEach(function (p) { byPort[p.port] = p; });
  return byPort;
}

function _hgFixtureForPort(port) {
  for (var i = 0; i < _hg.fixtures.length; i++) {
    var f = _hg.fixtures[i];
    if (f.childId !== _hg.cid) continue;
    var ss = f.strings || [];
    for (var j = 0; j < ss.length; j++) if (ss[j].port === port) return f;
  }
  return null;
}

function _hgStringsStep() {
  var caps = _hg.dev.caps || {};
  var maxPx = Math.floor((caps.maxPixelPortChannels || 2040) / 3);
  var protos = (caps.pixelProtocols || ['ws2811']).map(function (p) { return p.toUpperCase(); });
  var byPort = _hgPortRows();
  var boards = _hgBoards().filter(function (b) {
    return b.type === 'Local_SPI' || b.type === 'Long_Range'; });
  if (!boards.length) {
    // No board report: offer the model's full range rather than nothing.
    for (var n = 1; n <= (caps.boards || 3); n++) boards.push({num: n, type: '?'});
  }
  var h = '<p>For each port with a string plugged in, enter how many pixels it has and a name. '
    + 'Leave the others empty. Pixel type: <b>' + escapeHtml(protos.join(' / ')) + '</b>'
    + (protos.length === 1 ? ' (the only type this controller drives)' : '') + '.</p>';
  boards.forEach(function (b) {
    var w = _HG_BOARD_WORDS[b.type] || [''];
    h += '<div style="margin:.8em 0 .3em;font-weight:bold;color:#94a3b8">Board ' + b.num
      + (w[0] ? ' — ' + escapeHtml(w[0]) : '') + '</div>'
      + '<table class="tbl hg-ports"><tr><th>Port</th><th>Pixels</th><th>or length</th><th>Name</th></tr>';
    for (var i = 1; i <= 16; i++) {
      var port = (b.num - 1) * 16 + i;
      var p = byPort[port] || {};
      var f = _hgFixtureForPort(port);
      var leds = (p.enabled !== false && p.leds) ? p.leds : '';
      h += '<tr><td>' + port + '</td>'
        + '<td><input type="number" min="0" max="' + maxPx + '" class="hg-leds" data-port="' + port
        + '" value="' + leds + '" style="width:5.5em"></td>'
        + '<td style="white-space:nowrap"><input type="number" min="0" step="0.1" class="hg-len" '
        + 'data-port="' + port + '" placeholder="m" style="width:4em"> m × <select class="hg-dens" '
        + 'data-port="' + port + '">' + _HG_DENSITIES.map(function (d) {
          return '<option value="' + d + '"' + (d === 60 ? ' selected' : '') + '>' + d + '/m</option>';
        }).join('') + '</select> <button class="btn" style="font-size:.75em;padding:.1em .4em" '
        + 'onclick="_hgFromLength(' + port + ')">=</button></td>'
        + '<td><input class="hg-name" data-port="' + port + '" value="'
        + escapeHtml((f && f.name) || '') + '" placeholder="e.g. Garage eaves" style="width:11em"></td></tr>';
    }
    h += '</table>';
  });
  return h + _hgHelp('Count the pixels (bulbs / LEDs) on each string, or enter its length and '
      + 'pixels-per-metre and press <b>=</b>. A port holds up to ' + maxPx + ' pixels. Don’t know '
      + 'which port a string is on? Enter your best guess, send it, then use <b>Identify</b> on '
      + 'the Check step — it lights one port at a time.')
    + '<div style="margin-top:1em"><button class="btn btn-on" onclick="_hgSaveStrings()">'
    + 'Save and continue →</button></div>';
}

function _hgFromLength(port) {
  var len = parseFloat((document.querySelector('.hg-len[data-port="' + port + '"]') || {}).value);
  var dens = parseInt((document.querySelector('.hg-dens[data-port="' + port + '"]') || {}).value, 10);
  if (!(len > 0) || !(dens > 0)) return;
  var el = document.querySelector('.hg-leds[data-port="' + port + '"]');
  if (el) el.value = Math.round(len * dens);
}

function _hgSaveStrings() {
  var caps = _hg.dev.caps || {};
  var maxPx = Math.floor((caps.maxPixelPortChannels || 2040) / 3);
  var byPort = _hgPortRows();
  var entered = {}, names = {}, bad = [];
  document.querySelectorAll('.hg-leds').forEach(function (el) {
    var port = parseInt(el.getAttribute('data-port'), 10);
    var v = el.value === '' ? 0 : parseInt(el.value, 10);
    if (isNaN(v) || v < 0) { bad.push('port ' + port + ': not a number'); return; }
    if (v > maxPx) { bad.push('port ' + port + ': ' + v + ' pixels is more than the ' + maxPx + ' a port holds'); return; }
    entered[port] = v;
  });
  document.querySelectorAll('.hg-name').forEach(function (el) {
    var n = (el.value || '').trim();
    if (n) names[parseInt(el.getAttribute('data-port'), 10)] = n;
  });
  if (bad.length) { _hgNote(bad.join('; '), false); return; }
  // Keep every field the advanced editor owns (receivers, nulls, order…);
  // only the pixel count / enabled flag is the guide's to change.
  var ports = [];
  Object.keys(byPort).forEach(function (k) {
    var p = byPort[k], port = parseInt(k, 10);
    if (port in entered) {
      var q = JSON.parse(JSON.stringify(p));
      q.leds = entered[port];
      q.enabled = entered[port] > 0 ? true : !!p.enabled;
      ports.push(q);
      delete entered[port];
    } else {
      ports.push(p);
    }
  });
  Object.keys(entered).forEach(function (k) {
    if (entered[k] > 0) ports.push({port: parseInt(k, 10), leds: entered[k], enabled: true,
                                    protocol: 'ws2811', colorOrder: 'RGB'});
  });
  _hgNote('Saving…', true);
  _hgJson('PUT', '/api/hinkspix/' + _hg.cid, {ports: ports}).then(function (r) {
    if (r.s !== 200 || !r.d.ok) { _hgNote('Not saved: ' + ((r.d && r.d.err) || r.s), false); return null; }
    return _hgJson('POST', '/api/hinkspix/' + _hg.cid + '/fixtures-from-ports', {});
  }).then(function (r) {
    if (!r) return null;
    return _hgFetch('/api/fixtures');
  }).then(function (r) {
    if (!r) return null;
    _hg.fixtures = Array.isArray(r.d) ? r.d : [];
    var renames = [];
    Object.keys(names).forEach(function (k) {
      var f = _hgFixtureForPort(parseInt(k, 10));
      if (f && f.name !== names[k]) {
        renames.push(_hgJson('PUT', '/api/fixtures/' + f.id, {name: names[k]}));
      }
    });
    return Promise.all(renames);
  }).then(function (r) {
    if (!r) return;
    return _hgLoad().then(function () {
      _hg.step = 4;
      _hgSay('Saved in SlyLED. Nothing has been sent to the controller yet.', true);
      if (typeof loadSetup === 'function') loadSetup();
    });
  });
}

// ── Step 4: send ────────────────────────────────────────────────────────────

function _hgSummaryLines() {
  var d = _hg.dev, h = d.hinks || {}, spans = ((d.map || {}).spans) || [];
  var lines = [];
  (h.ports || []).filter(function (p) { return p.enabled !== false && p.leds > 0; })
    .forEach(function (p) {
      var f = _hgFixtureForPort(p.port);
      var us = spans.filter(function (s) { return s.port === p.port; })
        .map(function (s) { return s.universe; });
      var uni = us.length ? (us.length > 1 ? 'universes ' + us[0] + '–' + us[us.length - 1]
                                            : 'universe ' + us[0]) : '';
      lines.push('Port ' + p.port + ' → <b>' + escapeHtml((f && f.name) || 'unnamed') + '</b>, '
        + p.leds + ' pixels, ' + escapeHtml(String(p.protocol || 'ws2811').toUpperCase()) + ', '
        + escapeHtml(p.colorOrder || 'RGB') + (uni ? ', ' + uni : ''));
    });
  return lines;
}

function _hgSendStep() {
  var lines = _hgSummaryLines();
  if (!lines.length) {
    return '<p>No strings yet — go back to <b>Your strings</b> or import from xLights.</p>'
      + '<button class="btn" onclick="_hgGo(2)" style="background:#335;color:#fff">← Your layout</button>';
  }
  var inSync = _hg.dev.inSync;
  return '<p>This is what will be sent to the controller:</p><ul style="margin:.3em 0 .6em 1.2em;line-height:1.6">'
    + lines.map(function (l) { return '<li>' + l + '</li>'; }).join('') + '</ul>'
    + (inSync ? '<p style="color:#4ade80">The controller already holds this layout.</p>' : '')
    + '<p>Sending takes about a minute: SlyLED saves a snapshot of the controller first (so it can '
    + 'always be put back), writes the layout, reboots the controller and reads it back to check.</p>'
    + '<div style="display:flex;gap:.6em;flex-wrap:wrap;margin-top:.6em">'
    + '<button class="btn btn-on" onclick="hinksConfig(' + _hg.cid + ',3)">Send to the controller…</button>'
    + '<button class="btn" onclick="_hgGo(5)" style="background:#335;color:#fff">'
    + (inSync ? 'Next: check your strings →' : 'Already sent — check your strings →') + '</button></div>'
    + _hgHelp('Send opens the push view on its Review step: it lists every request, then Apply '
      + 'runs snapshot → write → reboot → verify with a progress bar. If anything fails it stops '
      + 'and offers the snapshot back. Come back here (Setup → Hardware → Set up) for the Check step.');
}

// ── Step 5: check — identify, colour test, light them all ───────────────────

function _hgCheckStep() {
  var h = _hg.dev.hinks || {};
  var ports = (h.ports || []).filter(function (p) { return p.enabled !== false && p.leds > 0; });
  if (!ports.length) return '<p>No strings yet.</p>';
  var rows = ports.map(function (p) {
    var f = _hgFixtureForPort(p.port);
    var ct = _hg.colour[p.port] || {};
    var q = '';
    if (ct.stage === 'red') {
      q = 'Lit <b style="color:#f87171">red</b> — what colour do you see? ' + _hgSeenButtons(p.port, 'red');
    } else if (ct.stage === 'green') {
      q = 'Now lit <b style="color:#4ade80">green</b> — what colour do you see? ' + _hgSeenButtons(p.port, 'green');
    } else if (ct.result) {
      q = escapeHtml(ct.result);
    }
    return '<tr data-hg-port="' + p.port + '"><td>Port ' + p.port + '</td><td>'
      + escapeHtml((f && f.name) || '') + '</td><td>' + p.leds + ' px · ' + escapeHtml(p.colorOrder || 'RGB')
      + '</td><td style="white-space:nowrap"><button class="btn btn-on" onclick="_hgIdentify(' + p.port
      + ')">Identify</button> <button class="btn" style="background:#335;color:#fff" '
      + 'onclick="_hgColourStart(' + p.port + ')">Colour test</button></td><td class="hg-q">' + q + '</td></tr>';
  }).join('');
  return '<p>Walk to your strings. <b>Identify</b> lights one port red for 8 seconds.</p>'
    + '<table class="tbl"><tr><th>Port</th><th>Name</th><th></th><th></th><th></th></tr>' + rows + '</table>'
    + '<div style="margin-top:.8em;display:flex;gap:.6em;flex-wrap:wrap">'
    + '<button class="btn btn-on" onclick="_hgLightAll()">Light them all (slow chase)</button>'
    + '<button class="btn" style="background:#335;color:#fff" onclick="_hgStopAll()">Stop</button></div>'
    + _hgHelp('<b>Nothing lit?</b> Check the layout has been sent (Send step), and that the DMX '
      + 'protocol in Settings matches the controller (step 1 warns if not). Still nothing: the '
      + 'string may be on another port — try the next one — or use the controller’s own Test page '
      + '(Hardware row → Web UI).<br><b>Colour test</b> lights the string red, then green, and asks '
      + 'what you saw; SlyLED works out the string’s colour order from your answers. '
      + '<b>Light them all</b> runs a slow chase on every string so you can check direction and '
      + 'that the whole length lights.');
}

function _hgSeenButtons(port, which) {
  var c = {R: '#dc2626', G: '#16a34a', B: '#2563eb'};
  return ['R', 'G', 'B'].map(function (l) {
    return '<button class="btn" style="background:' + c[l] + ';color:#fff;padding:.1em .6em" '
      + 'onclick="_hgSeen(' + port + ',\'' + which + '\',\'' + l + '\')">'
      + {R: 'Red', G: 'Green', B: 'Blue'}[l] + '</button>';
  }).join(' ');
}

function _hgIdentifyReq(body) {
  return _hgJson('POST', '/api/hinkspix/' + _hg.cid + '/identify', body).then(function (r) {
    if (r.s !== 200 || !r.d.ok) { _hgSay('Couldn’t light it: ' + ((r.d && r.d.err) || r.s), false); return null; }
    if (r.d.protocolMatch === false) {
      _hgSay('Sending, but SlyLED’s DMX protocol doesn’t match the controller’s input — it may '
        + 'stay dark. See step 1.', false);
    } else if (r.d.inSync === false) {
      _hgSay('Lit using SlyLED’s layout, which hasn’t been sent to the controller yet — the '
        + 'wrong string (or none) may light until you Send.', false);
    }
    return r.d;
  });
}

function _hgIdentify(port) {
  _hgIdentifyReq({port: port, color: 'red', seconds: 8}).then(function (d) {
    if (d && !_hg.msg) _hgSay('Port ' + port + ' is lit red for 8 seconds.', true);
  });
}

function _hgColourStart(port) {
  _hg.colour[port] = {stage: 'red'};
  _hgIdentifyReq({port: port, color: 'red', seconds: 30}).then(function () { _hgRender(); });
}

function _hgSeen(port, which, letter) {
  var ct = _hg.colour[port] || {};
  if (which === 'red') {
    ct.seenRed = letter;
    ct.stage = 'green';
    _hg.colour[port] = ct;
    _hgIdentifyReq({port: port, color: 'green', seconds: 30}).then(function () { _hgRender(); });
    return;
  }
  ct.seenGreen = letter;
  _hgJson('POST', '/api/hinkspix/' + _hg.cid + '/color-order',
          {port: port, seenRed: ct.seenRed, seenGreen: ct.seenGreen}).then(function (r) {
    _hgIdentifyReq({stop: true});
    if (r.s !== 200 || !r.d.ok) {
      _hg.colour[port] = {result: 'Test unclear: ' + ((r.d && r.d.err) || r.s)};
      _hgRender();
      return;
    }
    _hg.colour[port] = {result: r.d.changed
      ? 'Colour order is ' + r.d.colorOrder + ' (was ' + r.d.was + ') — saved; Send again to apply.'
      : 'Colour order ' + r.d.colorOrder + ' is right.'};
    _hgLoad().then(function () { _hgRender(); });
  });
}

function _hgLightAll() {
  _hgIdentifyReq({all: true, pattern: 'chase', color: 'white', seconds: 20}).then(function (d) {
    if (d && !_hg.msg) _hgSay('Every string is running a slow chase for 20 seconds — it should '
      + 'move away from the controller end along each string.', true);
  });
}

function _hgStopAll() {
  _hgIdentifyReq({stop: true}).then(function () { _hgSay('Stopped.', true); });
}
