// hinkspix.js — HinksPix PRO port table editor (#939)
//
// The controller keeps its own port inventory, and SlyLED mirrors it on the
// child record as `child.hinks`. This modal edits that mirror and shows the
// universe map derived from it. It never writes the controller.
//
// The table is built per expansion board and from the model's own capabilities
// (#946): a PRO V1/V2 addresses 48 outputs over 3 boards, a PRO V3 80 over 5,
// and the pixel protocols and smart-receiver options differ between them. A
// board the controller does not report as fitted still gets its rows — greyed,
// disabled, and labelled with the reason — because "16 empty outputs" and "no
// board in that slot" are different facts and only one of them is editable.
//
// Two deliberate behaviours worth knowing:
//  * Saving here only updates SlyLED's copy. Putting it on the device is a
//    separate, operator-triggered operation with its own review and revert
//    path — the push wizard in hinkspix_config.js, which also owns the
//    controller readback, the snapshots and the restore (#945). Silently
//    reconfiguring live hardware on a UI edit would be the wrong default, and
//    a push reboots the controller.
//  * Pixel geometry is stage-mm (60 px/m default), never a DMX fraction.
//    The operator corrects lengths in the fixture editor.

var _hpState = {cid: null, hinks: null, map: null, inSync: false,
                protocols: null, caps: null, plan: null, device: null, diff: null};

// What the server says this controller model can do. The fallback is the
// narrowest useful model rather than an empty table, so a page talking to an
// older server still renders a port table instead of an exception (#946).
function _hpCaps() {
  return _hpState.caps || {key: 'pro_v12', name: 'PRO V1/V2', boards: 3,
                           maxPixelPort: 48, maxPixelPortChannels: 2040,
                           pixelProtocols: ['ws2811'],
                           smartRemoteTypes: []};
}

// `nested` (#945): the push wizard opens this editor *on top of itself*, and
// expects closeModal() to hand the operator back to the wizard. A top-level
// open replaces the modal, so it clears the stack.
function hinksConfigure(cid, nested) {
  fetch('/api/hinkspix/' + cid)
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d || !d.ok) { alert('Could not load device: ' + ((d && d.err) || 'unknown')); return; }
      _hpState = {cid: cid, hinks: d.hinks || {}, map: d.map, inSync: !!d.inSync,
                  mapError: d.mapError, engineProtocol: d.engineProtocol,
                  nested: !!nested,
                  // Server-supplied so the picker can never offer a mode this
                  // controller cannot serve (e.g. DDP on PRO V1/V2 — #943 B14).
                  protocols: d.protocols,
                  // ...and so the table is built for the model it is editing:
                  // how many boards it addresses, which pixel protocols it
                  // takes, whether it has smart receivers at all (#946).
                  caps: d.caps,
                  protocolMatch: d.protocolMatch !== false};
      _hpRender();
    })
    .catch(function (e) { alert('Could not load device: ' + e); });
}

function _hpPorts() { return (_hpState.hinks && _hpState.hinks.ports) || []; }

// What the controller says is in an expansion slot, in the operator's terms.
// `null` means the probe never reported that slot at all, which is a different
// statement from "reported as empty" — but both mean nothing is plugged in.
function _hpBoardNote(kind) {
  if (kind === 'Long_Range') return 'long-range differential — smart receivers available';
  if (kind === 'Local_SPI') return 'local SPI — pixel outputs, no smart receivers';
  if (kind === 'Local_AC') return 'local AC dimming — no pixel outputs';
  if (kind === 'Not_Present') return 'not fitted — the controller reports Not_Present for this slot';
  return 'not fitted — no board reports as present in this slot';
}

function _hpRecvSelect(port, p, enabled) {
  var opts = '<option value="">—</option>';
  for (var i = 0; i < 16; i++) {
    var letter = String.fromCharCode(65 + i);
    opts += '<option value="' + letter + '"'
          + ((String(p.smartRemote || '').toUpperCase() === letter) ? ' selected' : '')
          + '>' + letter + '</option>';
  }
  return '<select class="hp-rec" data-port="' + port + '"'
       + (enabled ? ' onchange="_hpRecvChanged(' + port + ')"'
                  : ' disabled title="This board cannot carry a smart receiver."')
       + (enabled ? ' title="A-P, as printed on the receiver dial"' : '')
       + '>' + opts + '</select>';
}

function _hpRecvTypeSelect(port, p, enabled) {
  var names = {'hinkspix_4': '4-port', 'hinkspix_16': '16-port', 'hinkspix_16ac': '16AC'};
  var types = (_hpCaps().smartRemoteTypes || []);
  var opts = '<option value="">—</option>';
  types.forEach(function (t) {
    opts += '<option value="' + t + '"'
          + ((p.smartRemoteType === t) ? ' selected' : '') + '>'
          + (names[t] || t) + '</option>';
  });
  return '<select class="hp-rectype" data-port="' + port + '"'
       + ((enabled && p.smartRemote) ? '' : ' disabled') + '>' + opts + '</select>';
}

// Write a value into a cell the operator is allowed to edit. A select whose
// options do not include the value is left alone rather than being set to
// nothing: an option the model does not offer must not become a silent blank.
function _hpSet(port, cls, value) {
  var el = document.querySelector('.' + cls + '[data-port="' + port + '"]');
  if (!el || el.disabled) return;
  if (el.tagName === 'SELECT') {
    for (var i = 0; i < el.options.length; i++) {
      if (el.options[i].value === String(value)) { el.value = String(value); return; }
    }
    return;
  }
  el.value = value;
}

// A receiver's type only means something once its id is set, so choosing one
// unlocks the other. Wired as an inline handler on the id select.
function _hpRecvChanged(port) {
  var id = document.querySelector('.hp-rec[data-port="' + port + '"]');
  var ty = document.querySelector('.hp-rectype[data-port="' + port + '"]');
  if (!id || !ty) return;
  ty.disabled = !id.value;
  if (!id.value) ty.value = '';
}

function _hpRender() {
  var h = _hpState.hinks || {}, ports = _hpPorts();
  var body = '';

  body += '<div style="display:flex;gap:1.5em;flex-wrap:wrap;margin-bottom:1em">';
  body += '<div><label style="font-size:.8em;color:#9ab">Base universe</label><br>'
        + '<input id="hp-base" type="number" min="1" max="63999" value="'
        + (h.baseUniverse || 1) + '" style="width:8em"></div>';
  body += '<div><label style="font-size:.8em;color:#9ab">Input protocol</label><br>'
        + '<select id="hp-inproto">'
        + (_hpState.protocols || ['e131', 'sacn', 'artnet']).map(function (p) {
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
  // What a port gets when the table is filled in from the fixtures. Per-port
  // values stay editable in the table below; this is only the starting point.
  var defs = h.defaults || {};
  body += '<div title="Applied to a port when the table is filled in from the '
        + 'fixtures — not to ports already set">'
        + '<label style="font-size:.8em;color:#9ab">New-port defaults</label><br>'
        + '<label style="font-size:.85em">bright <input id="hp-def-bri" type="number" '
        + 'min="15" max="100" step="10" value="' + (defs.brightness != null ? defs.brightness : 100)
        + '" style="width:5em"> gamma <input id="hp-def-gamma" type="number" min="1" '
        + 'max="4" value="' + (defs.gamma || 1) + '" style="width:4em"></label></div>';
  body += '</div>';

  // Firmware / capability banner. The upload gate is the single most useful
  // fact about a HinksPix, because below it the device cannot be managed.
  var mcpu = h.mcpu, canUpload = h.uploadSupported;
  var caps = _hpCaps();
  if (mcpu != null) {
    body += '<div style="margin-bottom:1em;padding:.6em .8em;border-radius:6px;background:'
          + (canUpload ? '#123' : '#421') + ';font-size:.85em">'
          + 'Main CPU <b>MS_' + mcpu + '</b> &middot; web ' + (h.web || '?')
          + ' &middot; max universes ' + (h.maxU || '?')
          // The model the caps came out of, because it is what explains the
          // table below: its port count, its pixel protocols, its receivers.
          + ' &middot; <b>' + escapeHtml(caps.name) + '</b> ('
          + caps.boards + ' board' + (caps.boards === 1 ? '' : 's') + ', '
          + caps.maxPixelPort + ' ports, '
          + caps.maxPixelPortChannels + ' ch/port)'
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
        + '<th>On</th><th>Port</th>'
        + '<th title="Nodes this output drives. The controller owns this — a fixture bound here must agree.">Pixels</th>'
        + '<th title="Strip length in stage mm (SlyLED geometry, not a controller field)">Length (mm)</th>'
        + '<th title="Pixel protocol this output drives">Protocol</th>'
        + '<th title="Colour order the nodes latch">Order</th>'
        + '<th title="Pixels to skip at the start of this output — the controller begins its data this many pixels in">Skip start</th>'
        + '<th title="Per-output brightness step">Bright</th>'
        + '<th title="Per-output gamma 1-4">Gamma</th>'
        + '<th>Universe</th>'
        // The caveat is in the header rather than in a dialog on every change:
        // it is a property of the column, not of any one edit.
        + '<th title="Smart receiver on this output, A-P as printed on the receiver '
        + 'dial. The controller has no readback for receivers, so nothing here can be '
        + 'confirmed after a push — the wizard says so too. Only a Long_Range board '
        + 'can carry one.">Recv</th>'
        + '<th title="Which smart receiver is fitted — a 4-port, a 16-port, or a 16AC">Type</th>'
        + '</tr></thead>';
  var uniByPort = {};
  if (_hpState.map) {
    _hpState.map.spans.forEach(function (s) {
      if (s.kind !== 'pixel') return;
      (uniByPort[s.port] = uniByPort[s.port] || []).push(s.universe);
    });
  }

  // One section per expansion board, because that is the unit the controller
  // addresses: a port number alone cannot say whether the slot exists, and a
  // board that is not fitted has to say so rather than showing 16 editable rows
  // that go nowhere (#946).
  var fitted = h.boards || {};
  var maxPixels = Math.floor(caps.maxPixelPortChannels / 3);   // RGB nodes
  for (var b = 1; b <= caps.boards; b++) {
    var kind = fitted['BD' + b] || null;
    var isPixel = (kind === 'Local_SPI' || kind === 'Long_Range');
    var hasRecv = (kind === 'Long_Range') && (caps.smartRemoteTypes || []).length > 0;
    var note = _hpBoardNote(kind);
    body += '<tbody class="hp-board-group"><tr><th colspan="12" style="text-align:left;'
          + 'padding-top:.8em;background:#1a2430">BD' + b
          + (kind ? (' — ' + kind) : '')
          + '<span style="font-weight:normal;color:#9ab"> &middot; ' + note + '</span></th></tr>';

    for (var i = (b - 1) * 16 + 1; i <= b * 16; i++) {
      var p = ports.filter(function (x) { return x.port === i; })[0]
              || {port: i, leds: 0, enabled: false};
      var u = uniByPort[i];
      var dis = isPixel ? '' : ' disabled';
      body += '<tr' + (isPixel ? '' : ' style="opacity:.45"') + '>'
        + '<td><input type="checkbox" class="hp-en" data-port="' + i + '"' + dis
          + (p.enabled ? ' checked' : '') + '></td>'
        + '<td>' + i + '</td>'
        + '<td><input type="number" class="hp-leds" data-port="' + i + '" min="0" max="'
          + maxPixels + '" value="' + (p.leds || 0) + '" style="width:5em"' + dis + '></td>'
        + '<td><input type="number" class="hp-mm" data-port="' + i + '" min="0" value="'
          + (p.mm != null ? p.mm : Math.round((p.leds || 0) * 16.67)) + '" style="width:6em"' + dis + '></td>'
        + '<td><select class="hp-proto" data-port="' + i + '"' + dis + '>'
          + (caps.pixelProtocols || ['ws2811']).map(function (x) {
              return '<option' + ((p.protocol === x) ? ' selected' : '') + '>' + x + '</option>'; }).join('')
        + '</select></td>'
        + '<td><select class="hp-order" data-port="' + i + '"' + dis + '>'
          + ['RGB', 'RBG', 'GRB', 'GBR', 'BRG', 'BGR', 'RGBW', 'WRGB'].map(function (x) {
              return '<option' + ((p.colorOrder === x) ? ' selected' : '') + '>' + x + '</option>'; }).join('')
        + '</select></td>'
        + '<td><input type="number" class="hp-null" data-port="' + i + '" min="0" max="10" value="'
          + (p.startNulls || 0) + '" style="width:4em"' + dis + '></td>'
        + '<td><input type="number" class="hp-bri" data-port="' + i + '" min="15" max="100" step="10" value="'
          + (p.brightness != null ? p.brightness : 100) + '" style="width:5em"' + dis + '></td>'
        + '<td><input type="number" class="hp-gamma" data-port="' + i + '" min="1" max="4" value="'
          + (p.gamma || 1) + '" style="width:4em"' + dis + '></td>'
        + '<td style="color:#9ab">' + (u ? u.join(', ') : '—') + '</td>'
        // The receiver is a wiring fact, not a layout one — and only a
        // long-range differential board can carry one, so the cell says which
        // of the two it is rather than being silently absent.
        + '<td>' + (isPixel ? _hpRecvSelect(i, p, hasRecv) : '—') + '</td>'
        + '<td>' + (isPixel ? _hpRecvTypeSelect(i, p, hasRecv) : '—') + '</td>'
        + '</tr>';
    }
    body += '</tbody>';
  }
  body += '</table>';

  // Findings sit between the table and the buttons: they are what the operator
  // reads *after* pressing Save, and an error here is what a push would refuse.
  body += '<div id="hp-findings" style="margin-top:.8em;font-size:.85em"></div>';

  body += '<div style="margin-top:1em;display:flex;gap:.5em;flex-wrap:wrap">'
    + '<button class="btn btn-on" onclick="hinksSave()">Save</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksDefaultsFromFixtures()" '
    + 'title="Proposes pixels, length, colour order and receivers from the fixtures already bound to '
    + 'this controller. Nothing is saved and nothing is written to the controller.">Fill from fixtures</button>'
    // #945 — pushing is no longer a two-dialog affair in here. Everything that
    // writes the controller (snapshot, review, push, verify, restore) lives in
    // hinkspix_config.js; this modal only edits SlyLED's copy of the layout.
    + '<button class="btn" style="background:#dc2626;color:#fff" onclick="hinksConfig(' + _hpState.cid + ',3)">Review &amp; push…</button>'
    + '<button class="btn" style="background:#335;color:#fff" onclick="hinksConfig(' + _hpState.cid + ',1)">Controller state &amp; snapshots…</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksXlightsImport(' + _hpState.cid + ')" '
    + 'title="Read an xLights show folder (or its two XML files) and propose a port table from the models '
    + 'in it. Nothing is saved, and nothing is written to the controller, until you accept the proposal.">'
    + 'Import from xLights…</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksFixturesFromPorts()">Create fixtures from ports</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksProbe()">Probe</button>'
    + '<button class="btn" style="background:#059669;color:#fff" onclick="hinksStandalone(' + _hpState.cid + ')">Standalone playback →</button>'
    + '</div>'
    + '<div id="hp-result" style="margin-top:.8em;font-size:.85em"></div>';

  // Same modal mechanism the rest of the SPA uses (see showAddFixtureModal).
  // Only when this is the top-level dialog: opened from the push wizard it is a
  // sub-dialog, and clearing the stack would strand the wizard behind it.
  if (!_hpState.nested) _modalStack = [];
  document.getElementById('modal-title').textContent = 'HinksPix PRO — port configuration';
  document.getElementById('modal-body').innerHTML = body;
  document.getElementById('modal').style.display = 'block';
}

function _hpCollect() {
  var ports = [];
  document.querySelectorAll('.hp-en').forEach(function (el) {
    // A port on an unfitted board is shown so the table says why it is empty,
    // but it is not part of the config: it has no controller output to describe.
    if (el.disabled) return;
    var i = el.getAttribute('data-port');
    var q = function (cls) { return document.querySelector('.' + cls + '[data-port="' + i + '"]'); };
    var leds = parseInt(q('hp-leds').value, 10) || 0;
    if (!el.checked && leds === 0) return;   // keep the payload small
    var rec = q('hp-rec');
    ports.push({
      port: parseInt(i, 10), leds: leds,
      mm: parseInt(q('hp-mm').value, 10) || 0,
      protocol: q('hp-proto').value,
      colorOrder: q('hp-order').value,
      startNulls: parseInt(q('hp-null').value, 10) || 0,
      brightness: parseInt(q('hp-bri').value, 10) || 100,
      gamma: parseInt(q('hp-gamma').value, 10) || 1,
      enabled: el.checked,
      smartRemote: (rec && !rec.disabled) ? (rec.value || null) : null,
      smartRemoteType: (rec && !rec.disabled && rec.value)
        ? (q('hp-rectype').value || null) : null
    });
  });
  return {
    baseUniverse: parseInt(document.getElementById('hp-base').value, 10) || 1,
    protocol: document.getElementById('hp-inproto').value,
    // The starting point for a row the operator has not set; the per-port
    // values below always win (#946).
    defaults: {brightness: parseInt(document.getElementById('hp-def-bri').value, 10) || 100,
               gamma: parseInt(document.getElementById('hp-def-gamma').value, 10) || 1},
    dmxOut: {enabled: document.getElementById('hp-dmx-en').checked,
             universe: parseInt(document.getElementById('hp-dmx-uni').value, 10) || null},
    ports: ports
  };
}

function _hpSay(msg, good) {
  var el = document.getElementById('hp-result');
  if (el) el.innerHTML = '<span style="color:' + (good ? '#6d6' : '#f88') + '">' + escapeHtml(msg) + '</span>';
}

// One finding, in the operator's words. Three levels, the same three the push
// wizard draws: an error is a configuration a push would refuse, a warning is a
// decision the operator is allowed to make, and a note is something true of every
// push (the reboot) that is not a fault to fix (#945 F3). One renderer for the
// two places findings appear — the saved table and the import result — so the
// levels cannot be drawn one way in one and another way in the other.
function _hpFindingHtml(f, size) {
  var bad = f.level === 'error', info = f.level === 'info';
  return '<div style="margin:.3em 0;padding:.4em .6em;border-radius:4px;background:'
    + (bad ? '#511' : (info ? '#123' : '#421'))
    + (size ? (';font-size:' + size) : '') + '">'
    + '<b>' + (bad ? 'Cannot push' : (info ? 'Note' : 'Check')) + '</b> — '
    + escapeHtml(String(f.text))
    + (f.port ? ' <span style="color:#9ab">(port ' + f.port + ')</span>' : '')
    + '</div>';
}

// The findings the saved config raises. Shown here, at the moment the table is
// saved, rather than only when they push (#946).
function _hpFindings(list) {
  var el = document.getElementById('hp-findings');
  if (!el) return;
  list = list || [];
  if (!list.length) { el.innerHTML = ''; return; }
  var order = {error: 0, warn: 1, info: 2};
  list = list.slice().sort(function (a, b) {
    return (order[a.level] || 3) - (order[b.level] || 3);
  });
  el.innerHTML = list.map(function (f) { return _hpFindingHtml(f); }).join('');
  // Mark the rows the findings are about, so a 48-row table says which ones.
  document.querySelectorAll('.hp-row-bad').forEach(function (tr) {
    tr.classList.remove('hp-row-bad');
    tr.style.boxShadow = '';
  });
  list.filter(function (f) { return f.port; }).forEach(function (f) {
    var el2 = document.querySelector('.hp-en[data-port="' + f.port + '"]');
    var tr = el2 && el2.closest('tr');
    if (!tr) return;
    tr.classList.add('hp-row-bad');
    tr.style.boxShadow = 'inset 3px 0 0 ' + (f.level === 'error' ? '#f66' : '#fa6');
  });
}

function hinksSave() {
  fetch('/api/hinkspix/' + _hpState.cid, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(_hpCollect())
  }).then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
    .then(function (x) {
      if (x.s !== 200) { _hpSay(x.d.err || 'save failed', false); return; }
      _hpState.hinks = x.d.hinks; _hpState.map = x.d.map; _hpState.mapError = null;
      _hpState.inSync = false;
      var errs = (x.d.findings || []).filter(function (f) { return f.level === 'error'; });
      _hpSay('Saved to SlyLED. The controller is unchanged until you push.'
             + (errs.length ? (' ' + errs.length + ' thing(s) below would stop a push.')
                            : ''), !errs.length);
      _hpRender();
      _hpFindings(x.d.findings);
    }).catch(function (e) { _hpSay(String(e), false); });
}

// Fill the table in from the fixtures already bound to this controller.
//
// A proposal, and only ever a proposal: the server computes it, this writes it
// into the inputs without saving anything, and the operator reviews the result
// before Save. Only the fields a fixture can actually speak to are touched —
// pixels, length, colour order, receivers — so a value that came from the
// controller or from the operator is not overwritten by a guess.
function hinksDefaultsFromFixtures() {
  _hpSay('Reading the fixtures bound to this controller…', true);
  fetch('/api/hinkspix/' + _hpState.cid + '/defaults-from-fixtures', {method: 'POST'})
    .then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
    .then(function (x) {
      if (x.s !== 200 || !x.d.ok) { _hpSay(x.d.err || 'failed', false); return; }
      var d = x.d, n = 0;
      (d.ports || []).forEach(function (p) {
        var leds = document.querySelector('.hp-leds[data-port="' + p.port + '"]');
        if (!leds || leds.disabled) return;
        _hpSet(p.port, 'hp-leds', p.leds);
        if (p.mm != null) _hpSet(p.port, 'hp-mm', p.mm);
        _hpSet(p.port, 'hp-order', p.colorOrder);
        _hpSet(p.port, 'hp-rec', p.smartRemote || '');
        _hpSet(p.port, 'hp-rectype', p.smartRemoteType || '');
        var en = document.querySelector('.hp-en[data-port="' + p.port + '"]');
        if (en) en.checked = true;
        _hpRecvChanged(p.port);
        n++;
      });
      var msg = 'Filled ' + n + ' port(s) from the fixtures';
      if ((d.changes || []).length) msg += '. Changed: ' + d.changes.join('; ');
      if ((d.unbound || []).length) {
        msg += '. Enabled port(s) with no fixture on them: ' + d.unbound.join(', ')
             + ' — switch them off or bind a fixture';
      }
      msg += '. Nothing saved yet — review, then Save.';
      _hpSay(msg, true);
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


// ── xLights show-folder import (#947) ────────────────────────────────────────
//
// The operator's layout already exists in xLights: which controller, which
// universes, which model drives which output. This dialog reads it and offers
// it back as a *proposal* — the port table behind it is not touched until the
// operator ticks the models they want and accepts. Accepting writes SlyLED's
// copy through the same save the editor uses, so an imported port table is
// held to the same rules a hand-typed one is; the controller is not written
// either way (that is still the push wizard's job, #945).
//
// The file's controller address is usually *not* the unit's own — xLights
// keeps whatever was typed when the show was set up (the operator's Home Eves
// folder says 192.168.2.10 for a controller at 192.168.10.6) — so it is shown
// rather than reconciled. Which of the two is right is a fact about the
// network, not about either file.

var _hxi = {cid: null, folder: '', controllers: null, proposal: null,
            off: {}, createFixtures: true, busy: false, result: null};

function hinksXlightsImport(cid) {
  _pushModal();     // Cancel hands the port table back, edits and all
  _hxi = {cid: cid, folder: '', controllers: null, proposal: null,
          off: {}, createFixtures: true, busy: false, result: null};
  _hxiRender();
}

function _hxiSay(msg, good) {
  var el = document.getElementById('hxi-msg');
  if (el) {
    el.innerHTML = '<span style="color:' + (good ? '#6d6' : '#f88') + '">'
                 + escapeHtml(msg) + '</span>';
  }
}

function _hxiFetch(url, opts) {
  return fetch(url, opts).then(function (r) {
    return r.json().then(function (d) { return {s: r.status, d: d}; },
                        function () { return {s: r.status, d: {}}; });
  });
}

// Read the folder the operator pasted, or the two files they picked. The
// orchestrator does the reading, so the path is resolved on the machine the
// server runs on — a Windows path and its WSL mount both work, and a path to
// either one of the files is taken as the folder holding them.
function hinksXlightsRead(files) {
  if (_hxi.busy) return;
  var body;
  if (files && files.length) {
    var fd = new FormData();
    for (var i = 0; i < files.length; i++) fd.append('files', files[i]);
    body = {form: fd};
  } else {
    var el = document.getElementById('hxi-folder');
    var folder = el ? el.value.trim() : '';
    if (!folder) { _hxiSay('Give the folder xLights keeps this show in.', false); return; }
    var ctrl = document.getElementById('hxi-ctrl');
    body = {json: {showFolder: folder, cid: _hxi.cid,
                   controller: ctrl ? ctrl.value : undefined}};
  }
  _hxi.busy = true;
  _hxiSay('Reading…', true);
  var opts = {method: 'POST'};
  if (body.form) { opts.body = body.form; }
  else {
    opts.headers = {'Content-Type': 'application/json'};
    opts.body = JSON.stringify(body.json);
  }
  _hxiFetch('/api/hinkspix/import/xlights', opts)
    .then(function (x) {
      _hxi.busy = false;
      if (x.s !== 200 || !x.d.ok) {
        // A show with more than one controller is not an error the operator
        // can fix by retyping: the server sends the names, so this offers them.
        if ((x.d.controllers || []).length > 1) {
          _hxi.controllers = x.d.controllers;
          _hxi.proposal = null;
          _hxiRender();
          _hxiSay(x.d.err || 'choose a controller', false);
          return;
        }
        _hxiSay(x.d.err || ('read failed (' + x.s + ')'), false);
        return;
      }
      _hxi.controllers = x.d.controllers || [];
      _hxi.proposal = x.d;
      _hxi.off = {};
      // A previous apply's result is about a proposal this read just replaced:
      // leaving it up would show a stale summary with no Apply button on it.
      _hxi.result = null;
      _hxiRender();
      _hxiSay('Read ' + (x.d.models || []).length + ' model(s) from '
              + (x.d.source || 'the folder') + '.', true);
    })
    .catch(function (e) { _hxi.busy = false; _hxiSay(String(e), false); });
}

function _hxiToggle(i, on) {
  if (on) { delete _hxi.off[i]; } else { _hxi.off[i] = true; }
  _hxiRender();
}

function hinksXlightsApply() {
  if (_hxi.busy || !_hxi.proposal) return;
  // The tick state travels as `accepted` on the same proposal object the server
  // handed over, because the accept reads it there: an unticked row withdraws
  // its outputs, and a row the proposal itself rejected cannot be re-ticked
  // (its ports are ones the port table cannot hold).
  var prop = JSON.parse(JSON.stringify(_hxi.proposal));
  (prop.models || []).forEach(function (m, i) {
    if (m.accepted !== false && _hxi.off[i]) m.accepted = false;
  });
  _hxi.busy = true;
  _hxiSay('Applying…', true);
  _hxiFetch('/api/hinkspix/' + _hxi.cid + '/import/xlights/accept', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({proposal: prop,
                          createFixtures: _hxi.createFixtures})
  }).then(function (x) {
    _hxi.busy = false;
    if (x.s !== 200) {
      _hxiSay(x.d.err || ('apply failed (' + x.s + ')'), false);
      return;
    }
    _hxi.result = x.d;
    _hxiRender();
    _hxiSay('Saved to SlyLED.', true);
    if (typeof loadFixtures === 'function') loadFixtures();
  }).catch(function (e) { _hxi.busy = false; _hxiSay(String(e), false); });
}

// Leave the dialog and show the port table as it now stands: the edits the
// accept made are the server's copy, so the table is re-read rather than
// patched in place.
function hinksXlightsDone() {
  closeModal();                       // pops the port table back
  // #953 — started from the first-time setup guide: go back to it, on the
  // Send step, rather than into the advanced port table.
  if (window._hgReturnAfterXlights === _hxi.cid && typeof hinksGuide === 'function') {
    window._hgReturnAfterXlights = null;
    hinksGuide(_hxi.cid, 4);
    return;
  }
  if (typeof hinksConfigure === 'function') hinksConfigure(_hxi.cid, true);
}

function _hxiPortList(row) {
  var ps = row.ports || (row.port != null ? [row.port] : []);
  return ps.length ? ps.join(', ') : '—';
}

function _hxiModelRows(p) {
  var rows = p.models || [];
  if (!rows.length) {
    return '<div style="color:#fa6;font-size:.85em">No model in the show folder '
         + 'belongs to this controller, so there is no output to import.</div>';
  }
  var html = '<table class="tbl" style="width:100%;font-size:.85em">'
           + '<thead><tr><th title="Import this model\'s outputs">Use</th>'
           + '<th>Model</th><th>Shape</th><th>Output(s)</th><th>Nodes</th>'
           + '<th title="Where its StartChannel lands, resolved against the '
           + 'controller\'s universes">Starts at</th><th>Notes</th></tr></thead>';
  rows.forEach(function (m, i) {
    var rejected = m.accepted === false;
    var on = !rejected && !_hxi.off[i];
    html += '<tr' + (on ? '' : ' style="opacity:.5"') + '>'
      + '<td><input type="checkbox" ' + (on ? 'checked' : '')
      + (rejected ? ' disabled title="This row cannot be imported — see its notes."' : '')
      + ' onchange="_hxiToggle(' + i + ', this.checked)"></td>'
      + '<td>' + escapeHtml(String(m.name || '?')) + '</td>'
      + '<td>' + escapeHtml(String(m.displayAs || '?')) + '<br>'
      + '<span style="color:#9ab">' + escapeHtml(String(m.stringType || '?')) + '</span></td>'
      + '<td>' + escapeHtml(_hxiPortList(m)) + '</td>'
      + '<td>' + (m.nodes != null ? m.nodes : '?')
      + (m.nodesPerString != null ? (' <span style="color:#9ab">('
          + m.nodesPerString + '/string x' + (m.strings || 0) + ')</span>') : '')
      + '</td>'
      + '<td>' + (m.universe != null ? ('u' + m.universe + ' ch' + m.channel) : '—')
      + (m.startChannel ? ('<br><span style="color:#9ab;font-size:.9em">'
          + escapeHtml(String(m.startChannel)) + '</span>') : '')
      + '</td><td>'
      + (m.problems || []).map(function (pr) {
          return '<div style="color:' + (pr.level === 'error' ? '#f88' : '#fa6')
               + '">' + escapeHtml(String(pr.text)) + '</div>';
        }).join('')
      + '</td></tr>';
  });
  return html + '</table>';
}

function _hxiDiff(p) {
  var d = p.diff;
  if (!d) return '';
  var bits = [];
  if ((d.added || []).length) bits.push((d.added.length) + ' output(s) added: ' + d.added.join(', '));
  if ((d.changed || []).length) {
    bits.push((d.changed.length) + ' changed: ' + d.changed.map(function (c) {
      return c.port + ' (' + Object.keys(c.fields || {}).join(', ') + ')';
    }).join('; '));
  }
  if ((d.kept || []).length) {
    bits.push((d.kept.length) + ' kept as they are, because the show folder says '
              + 'nothing about them: ' + d.kept.join(', '));
  }
  if ((d.withdrawn || []).length) bits.push('unticked: ' + d.withdrawn.join(', '));
  var bu = (d.settings || {}).baseUniverse;
  if (bu) {
    bits.push('base universe ' + bu.from + ' → ' + bu.to
              + ' (renumbers every universe below it and the routes that publish them)');
  }
  if (!bits.length) return '';
  return '<div style="margin:.6em 0;font-size:.85em;color:#9ab">Against this '
       + 'controller now: ' + bits.map(escapeHtml).join(' · ') + '</div>';
}

function _hxiRender() {
  var p = _hxi.proposal;
  var body = '<div style="font-size:.9em;line-height:1.45">';

  body += '<div style="margin-bottom:.8em;color:#9ab">Read the show folder xLights '
        + 'keeps for this controller — <code>xlights_networks.xml</code> for the '
        + 'controller and its universes, <code>xlights_rgbeffects.xml</code> for the '
        + 'models. The orchestrator reads them, so the path must be one <i>it</i> can '
        + 'see; a Windows path and its <code>/mnt/…</code> equivalent both work, and '
        + 'the export-dialog file in the same folder is ignored.</div>';

  body += '<div style="display:flex;gap:.5em;flex-wrap:wrap;align-items:center">'
        + '<input id="hxi-folder" type="text" placeholder="C:\\Users\\…\\Xlights - Show" '
        + 'value="' + escapeHtml(_hxi.folder) + '" style="flex:1;min-width:18em" '
        + 'oninput="_hxi.folder=this.value">'
        + '<button class="btn btn-on" onclick="hinksXlightsRead()"'
        + (_hxi.busy ? ' disabled' : '') + '>Read folder</button>'
        + '</div>';

  // The same read, from a machine the orchestrator cannot see into: hand it the
  // two files instead of a path.
  body += '<div style="margin:.5em 0 .8em;font-size:.85em">'
        + '<label>…or pick the two files: '
        + '<input type="file" id="hxi-files" multiple accept=".xml" '
        + 'onchange="hinksXlightsRead(this.files)"></label> '
        + '<span style="color:#9ab">(select both — <code>xlights_networks.xml</code> '
        + 'and <code>xlights_rgbeffects.xml</code>)</span></div>';

  body += '<div id="hxi-msg" style="min-height:1.2em;font-size:.85em;margin-bottom:.6em"></div>';

  if (_hxi.controllers && _hxi.controllers.length > 1 && !p) {
    body += '<div style="margin-bottom:.8em"><label style="font-size:.85em">This show '
          + 'has ' + _hxi.controllers.length + ' controllers — which one is this? '
          + '<select id="hxi-ctrl">'
          + _hxi.controllers.map(function (n) {
              return '<option value="' + escapeHtml(String(n)) + '">'
                   + escapeHtml(String(n)) + '</option>';
            }).join('')
          + '</select></label></div>';
  }

  if (p && !_hxi.result) {
    var c = p.controller || {};
    body += '<div style="margin-bottom:.6em;font-size:.85em">'
          + '<b>' + escapeHtml(String(c.name || '?')) + '</b>'
          + (c.description ? (' — ' + escapeHtml(String(c.description))) : '')
          + ' &middot; ' + escapeHtml(String(c.vendor || '?')) + ' '
          + escapeHtml(String(c.model || '?'))
          + ' &middot; ' + escapeHtml(String(p.protocol || '?').toUpperCase())
          + ' to <b>' + escapeHtml(String(c.ip || '?')) + '</b>'
          + ' &middot; universes ' + ((p.universes || {}).base || '?')
          + (p.universes && p.universes.count
              ? ('..' + ((p.universes.base || 0) + p.universes.count - 1)
                 + ' (' + p.universes.count + ')') : '')
          + (c.fullControl ? '' : ' &middot; <span style="color:#fa6">xLights is not '
             + 'in full control — these are the universes it would use, not what the '
             + 'controller is running</span>')
          + '</div>';

    (p.notes || []).forEach(function (n) {
      body += '<div style="margin:.3em 0;padding:.4em .6em;border-radius:4px;'
            + 'background:#421;font-size:.85em">' + escapeHtml(String(n)) + '</div>';
    });
    (p.warnings || []).forEach(function (w) {
      body += '<div style="margin:.3em 0;padding:.4em .6em;border-radius:4px;'
            + 'background:#421;font-size:.85em">' + escapeHtml(String(w)) + '</div>';
    });

    body += _hxiDiff(p);
    body += _hxiModelRows(p);

    body += '<div style="margin-top:.8em;font-size:.85em">'
          + '<label><input type="checkbox" id="hxi-mkfix"' + (_hxi.createFixtures ? ' checked' : '')
          + ' onchange="_hxi.createFixtures=this.checked"> create a fixture for each '
          + 'model, named after it, bound to the output it drives</label>'
          + '<div style="color:#9ab">Fixtures already on this controller keep their '
          + 'names and their outputs; one whose name or output is taken is skipped and '
          + 'reported rather than renamed or rebound.</div></div>';

    body += '<div style="margin-top:1em;display:flex;gap:.5em">'
          + '<button class="btn btn-on" onclick="hinksXlightsApply()"'
          + (_hxi.busy ? ' disabled' : '') + '>Apply to SlyLED</button>'
          + '<button class="btn" onclick="closeModal()">Cancel</button>'
          + '</div>';
    body += '<div style="margin-top:.5em;color:#9ab;font-size:.8em">This updates '
          + "SlyLED's copy of the layout. The controller is not written — pushing "
          + 'is “Review &amp; push…” in the port table.</div>';
  }

  if (_hxi.result) {
    var r = _hxi.result;
    body += '<div style="font-size:.9em"><b>Written to SlyLED.</b> '
          + ((r.created || []).length
              ? ('Created ' + r.created.length + ' fixture(s): '
                 + r.created.map(function (f) {
                     return escapeHtml(String(f.name)) + ' (port '
                          + (f.ports || []).join(', ') + ')';
                   }).join('; ') + '.')
              : 'No fixture was created.')
          + '</div>';
    (r.skipped || []).forEach(function (s) {
      body += '<div style="margin:.3em 0;padding:.4em .6em;border-radius:4px;'
            + 'background:#421;font-size:.85em">Skipped <b>'
            + escapeHtml(String(s.name)) + '</b> — ' + escapeHtml(String(s.reason))
            + '</div>';
    });
    (r.notes || []).forEach(function (n) {
      body += '<div style="margin:.3em 0;font-size:.85em;color:#9ab">'
            + escapeHtml(String(n)) + '</div>';
    });
    (r.findings || []).forEach(function (f) {
      body += _hpFindingHtml(f, '.85em');
    });
    body += '<div style="margin-top:1em"><button class="btn btn-on" '
          + 'onclick="hinksXlightsDone()">Back to the port table</button></div>';
  }

  body += '</div>';
  document.getElementById('modal-title').textContent = 'Import from xLights — port configuration';
  document.getElementById('modal-body').innerHTML = body;
  document.getElementById('modal').style.display = 'block';
}


// ── Standalone scheduled playback (#941) ─────────────────────────────────────
// The controller plays .hseq sequences from its SD card against an on-board
// day/time schedule, with SlyLED switched off entirely. There is no "play now"
// verb — the schedule IS the mechanism, so this panel edits days and times.
//
// Note the controller cannot express a window crossing midnight; the server
// rejects one and tells you how to split it.

var _hpDeploy = {config: null, progress: null, poll: null, gate: null, eligibility: []};
var _HP_DAYS = ['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY'];

function hinksStandalone(cid) {
  fetch('/api/hinkspix/' + cid + '/deploy')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d || !d.ok) { alert('Could not load: ' + ((d && d.err) || '?')); return; }
      _hpState.cid = cid;
      _hpDeploy.config = d.config || {};
      _hpDeploy.progress = d.progress || {};
      _hpDeploy.gate = d.gate || {ok: true};
      _hpDeploy.eligibility = d.eligibility || [];
      _hpRenderStandalone();
    });
}

function _hpRenderStandalone() {
  var cfg = _hpDeploy.config || {};
  var rows = cfg.schedule || [];
  var last = cfg.lastDeploy;
  var body = '';

  // #963 — only shows that light nothing but this controller's pixels can
  // play without SlyLED; say so, and don't promise more than that.
  body += '<p style="font-size:.85em;color:#9ab">Sequences are rendered here, uploaded to '
        + 'the controller\'s SD card and played against its own clock, so the controller can '
        + 'play them by itself. Only shows that light <b>nothing but this controller\'s pixels</b> '
        + 'can be added — a show that also drives DMX fixtures, performers or another controller '
        + 'needs SlyLED running. <span style="color:#fbbf24">Not yet verified on hardware.</span></p>';

  // Everything on this screen but "Live" is a raw-TCP operation, and the
  // controller drops the connection below its firmware gate — so the server
  // refuses these with a 409. Disabling them here shows the reason up front
  // instead of letting the operator hit a bare socket error.
  var gate = _hpDeploy.gate || {ok: true};
  var blocked = gate.ok === false;
  var off = blocked ? ' disabled' : '';
  if (blocked) {
    body += '<div style="margin-bottom:1em;padding:.6em .8em;border-radius:6px;background:#421;'
          + 'font-size:.85em">' + escapeHtml(gate.err || 'Firmware is too old.') + '</div>';
  }

  body += '<div style="margin-bottom:1em"><label style="font-size:.8em;color:#9ab">Playlist name</label><br>'
        + '<input id="hp-plname" value="' + escapeHtml(cfg.playlistName || 'SHOW')
        + '" maxlength="20" style="width:14em"> '
        + '<span style="font-size:.75em;color:#789">uppercase A-Z 0-9, max 20</span></div>';

  body += '<div style="margin-bottom:1em"><label style="font-size:.8em;color:#9ab">Sequences</label>'
        + '<div id="hp-items" style="font-size:.85em">';
  var elig = {};
  (_hpDeploy.eligibility || []).forEach(function (e) { elig[e.timelineId] = e; });
  (cfg.items || []).forEach(function (it, i) {
    var e = elig[it.timelineId] || {};
    body += '<div>' + (i + 1) + '. ' + escapeHtml(e.name || ('timeline #' + it.timelineId))
          + (e.eligible === false ? ' <span class="hp-item-why" style="color:#f87171">⚠ ' + escapeHtml(e.reason || '') + '</span>' : '')
          + ' <button class="btn" style="font-size:.7em;padding:.1em .4em;background:#633;color:#fff" '
          + 'onclick="_hpRemoveItem(' + i + ')">remove</button></div>';
  });
  if (!(cfg.items || []).length) body += '<div style="color:#f88">No sequences selected — add one below.</div>';
  var opts = (_hpDeploy.eligibility || []).map(function (e) {
    return '<option value="' + e.timelineId + '"' + (e.eligible ? '' : ' disabled title="' + escapeHtml(e.reason || '') + '"') + '>'
      + escapeHtml(e.name) + (e.eligible ? '' : ' — needs SlyLED running') + '</option>';
  }).join('');
  var anyOk = (_hpDeploy.eligibility || []).some(function (e) { return e.eligible; });
  body += '</div><div style="margin-top:.4em"><select id="hp-addtid" style="min-width:14em">'
        + '<option value="">' + (anyOk ? '— choose a show —' : '— no show uses only this controller —') + '</option>' + opts + '</select> '
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
    + '<button class="btn" style="background:#dc2626;color:#fff" onclick="hinksDeploy()"' + off + '>Deploy to controller</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksSetClock()"' + off + '>Set clock</button>'
    + '<button class="btn" style="background:#335;color:#fff" onclick="hinksMode(\'standalone\')"' + off + '>Standalone</button>'
    + '<button class="btn" style="background:#335;color:#fff" onclick="hinksMode(\'live\')">Live</button>'
    + '<button class="btn" style="background:#446;color:#fff" onclick="hinksConfigure(' + _hpState.cid + ')">← Ports</button>'
    + '</div><div id="hp-result" style="margin-top:.8em;font-size:.85em"></div>';

  _modalStack = [];
  document.getElementById('modal-title').textContent = 'HinksPix PRO — standalone playback (not yet verified on hardware)';
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
          _hpDeploy.eligibility = d.eligibility || _hpDeploy.eligibility;
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
