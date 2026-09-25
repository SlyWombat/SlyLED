// hinkspix_config.js — the configuration push wizard (#945)
//
// hinkspix.js edits SlyLED's *mirror* of the controller's configuration. This
// file is what puts a configuration on the controller, and it is a different
// kind of operation: it overwrites what is on the device, it ends with the
// controller rebooting, and the only proof it worked is reading the device back
// afterwards. A push that reported "sent 84 requests" and a push that landed
// and rebooted into a working controller are indistinguishable from the outside
// until something is read.
//
// So it is a wizard, one job per step:
//
//   1 Read    what the controller holds now, and what it holds of ours
//   2 Edit    SlyLED's copy (the port editor in hinkspix.js) — still nothing
//             on the device
//   3 Review  the exact requests, and which findings block or need ack
//   4 Apply   the push as a job: snapshot -> write -> reboot
//   5 Verify  the device read back and diffed against what was sent
//
// Three behaviours here are deliberate and worth knowing:
//
//  * Every push snapshots the controller first, and if the snapshot cannot be
//    taken nothing is written at all. A controller that cannot be read is one
//    that cannot be put back.
//  * A failed push stops where it failed, is never resumed or retried, and
//    names the snapshot to put back — a half-written controller has no
//    description to reason from, so the way forward is a known state. When it
//    stopped *after* the controller had accepted writes, the server puts that
//    snapshot back on its own and this step reports what the recovery did; what
//    it was left holding, and by which request, is in the failure block either
//    way.
//  * "In sync" means the stored config has not changed since the last push. It
//    is not a claim about the device, and a restore clears it — restoring a
//    snapshot puts the device into a state this orchestrator does not describe.

var _hw = {cid: null, step: 1, device: null, plan: null, job: null,
           backups: [], ack: {}, busy: false, msg: null, poll: null,
           loading: null};

var _HW_STEPS = ['Read', 'Edit', 'Review', 'Apply', 'Verify'];

var _HW_PHASE = {start: 'Starting', backup: 'Snapshotting the controller',
                 upload: 'Writing', recover: 'Putting the snapshot back',
                 reboot: 'Waiting for the controller to reboot',
                 verify: 'Reading the configuration back', done: 'Done',
                 failed: 'Failed'};

// Where each phase sits on the progress bar. Any phase not listed here — and
// that is `upload` and `recover` — is interpolated by step instead.
var _HW_PHASE_PCT = {start: 3, backup: 10, reboot: 72, verify: 90, done: 100,
                     failed: 100};

function hinksConfig(cid, step) {
  // Opening the wizard replaces whatever modal is up — including the port
  // editor, whose own buttons come back here. Drop any stack entry for a panel
  // that is no longer on screen, or closeModal() would restore its stale body.
  _modalStack = [];
  _hw.cid = cid;
  _hw.step = step || 1;
  _hw.msg = null;
  _hwPollStop();
  _hwRender(true);
  _hwLoad().then(function () { _hwRender(); });
}

// #955 — moving between steps is a view change, not a device read. Reading
// the controller takes 2-6 s; re-reading on every step click stacked
// overlapping reads, and each one re-rendered the wizard when it landed —
// over the port editor if that was open by then. Reads happen on open and on
// "Read again" only.
function _hwGo(step, reread) {
  _hw.step = step;
  _hw.msg = null;
  _hwRender();
  if (reread) _hwLoad().then(function () { _hwRender(); });
}

function _hwFetch(url, opts) {
  return fetch(url, opts).then(function (r) {
    return r.json().then(function (d) { return {s: r.status, d: d}; },
                        function () { return {s: r.status, d: {}}; });
  });
}

function _hwLoad() {
  // One round: what the device holds, and what this orchestrator thinks of it.
  // #955 — a read already in flight for this controller is reused, not
  // duplicated.
  var id = _hw.cid;
  if (_hw.loading && _hw.loading.cid === id) return _hw.loading.p;
  var p = Promise.all([_hwFetch('/api/hinkspix/' + id + '/device-config'),
                      _hwFetch('/api/hinkspix/' + id + '/apply')])
    .then(function (out) {
      var dev = out[0], job = out[1];
      if (dev.s === 200) {
        // The route answers with the device, its diff and any diffError — the
        // device box renders the first two, so they are kept apart.
        _hw.device = dev.d.device || null;
        _hw.diff = dev.d.diff || null;
        _hw.diffError = dev.d.diffError || null;
        _hw.deviceError = null;
      } else {
        _hw.device = null;
        _hw.diff = null;
        _hw.diffError = null;
        _hw.deviceError = dev.d.err || ('read failed (' + dev.s + ')');
      }
      if (job.s === 200) {
        _hw.job = job.d;
        _hw.backups = job.d.backups || [];
        if (job.d.state && job.d.state.running && !_hw.poll) _hwPollStart();
      }
      _hw.msg = null;
    });
  var done = function () { if (_hw.loading && _hw.loading.p === p) _hw.loading = null; };
  p.then(done, done);
  _hw.loading = {cid: id, p: p};
  return p;
}

function _hwSay(text, good) {
  _hw.msg = text ? {text: text, good: good !== false} : null;
  _hwRender();
}

// ── Rendering ────────────────────────────────────────────────────────────────

function _hwRender(force) {
  var el = document.getElementById('modal-body');
  if (!el) return;
  // #955 — async completions (the device read, the apply-job poll) must not
  // paint over whatever is on screen now: the port editor opened from step 2
  // shares #modal-body. Paint only while the wizard is showing, or when it is
  // being opened (force). Closing the editor repaints it (_popModal hook).
  if (!force && !document.getElementById('hpw-root')) return;
  var body = '<div id="hpw-root" style="font-size:.9em">'
    + _hwStrip() + _hwMsg() + _hwStepBody() + '</div>';
  document.getElementById('modal-title').textContent =
    'HinksPix PRO — configuration push';
  el.innerHTML = body;
  document.getElementById('modal').style.display = 'block';
}

function _hwMsg() {
  if (!_hw.msg) return '';
  return '<div style="margin:.5em 0;padding:.5em .7em;border-radius:6px;background:'
    + (_hw.msg.good ? '#123' : '#511') + '">' + escapeHtml(_hw.msg.text) + '</div>';
}

function _hwStrip() {
  var h = '<div style="display:flex;gap:.4em;margin-bottom:.6em;flex-wrap:wrap">';
  _HW_STEPS.forEach(function (name, i) {
    var n = i + 1, cur = (n === _hw.step);
    h += '<button class="btn" onclick="_hwGo(' + n + ')"'
      + ' style="background:' + (cur ? '#2563eb' : '#335') + ';color:#fff;'
      + (cur ? 'font-weight:bold' : 'opacity:.75') + '">'
      + n + '. ' + name + '</button>';
  });
  // #953 — back to the plain-words first-time guide (identify, colour test).
  if (typeof hinksGuide === 'function') {
    h += '<button class="btn" onclick="hinksGuide(' + _hw.cid + ')" style="background:#14532d;'
      + 'color:#bbf7d0;font-size:.85em" title="First-time setup: find, name and check your strings">'
      + 'Setup guide</button>';
  }
  return h + _hwSyncBadge() + '</div>';
}

function _hwSyncBadge() {
  if (!_hw.job) return '';
  var sync = _hw.job.inSync;
  var last = _hw.job.lastApply;
  var text = sync ? 'configuration in sync' : 'configuration differs from the device';
  if (last && !last.ok) {
    text = last.partial ? 'last push failed part-way' : 'last push failed';
    if (last.partial && (last.restore || {}).ok) text += ' (snapshot restored)';
  }
  return '<span style="align-self:center;margin-left:auto;padding:.2em .6em;'
    + 'border-radius:10px;background:' + (sync ? '#065f46' : '#7c2d12') + ';'
    + 'color:#fff;font-size:.85em">' + text + '</span>';
}

function _hwStepBody() {
  switch (_hw.step) {
    case 1: return _hwReadStep();
    case 2: return _hwEditStep();
    case 3: return _hwReviewStep();
    case 4: return _hwApplyStep();
    default: return _hwVerifyStep();
  }
}

// ── Step 1: what the controller holds ────────────────────────────────────────

function _hwReadStep() {
  var h = '<div style="color:#9ab;margin-bottom:.6em">What the controller holds '
        + 'right now, and the snapshots you can go back to.</div>';

  if (_hw.deviceError) {
    h += '<div style="padding:.6em .8em;border-radius:6px;background:#511">'
      + 'The controller could not be read: ' + escapeHtml(_hw.deviceError)
      + '<div style="color:#fa6;margin-top:.3em">Nothing can be pushed until it '
      + 'answers — a push snapshots first, and a snapshot needs a read.</div></div>';
  } else if (_hw.device) {
    var dev = _hw.device, boards = Object.keys(dev.boardPorts || {});
    var used = 0, rows = 0;
    boards.forEach(function (b) {
      (dev.boardPorts[b] || []).forEach(function (r) { rows++; if (r.used) used++; });
    });
    h += '<div style="padding:.6em .8em;border-radius:6px;background:#123">'
      + '<b>On the controller</b> &middot; input mode '
      + escapeHtml(String(dev.mode || '?'))
      + ' &middot; max universes ' + (dev.maxUniverses || '?')
      + ' &middot; ' + boards.length + ' pixel board(s)'
      + ' &middot; ' + used + ' of ' + rows + ' outputs in use'
      + '</div>';
    if (_hw.diffError) {
      h += '<div style="margin-top:.5em;color:#fa6">Difference unknown: '
        + escapeHtml(_hw.diffError) + '</div>';
    } else if (_hw.diff) {
      h += _hwDiffBox(_hw.diff, 'against SlyLED’s saved configuration');
    }
    if (boards.length) {
      h += '<details style="margin-top:.6em"><summary style="cursor:pointer">'
        + 'What the controller holds, output by output</summary>' + _hwDeviceTable(dev)
        + '</details>';
    }
  }

  var last = _hw.job && _hw.job.lastVerify;
  if (last) {
    h += '<div style="margin-top:.8em;padding:.6em .8em;border-radius:6px;background:'
      + (last.ok ? '#123' : '#421') + '"><b>Last push verified</b> ('
      + escapeHtml(_hwWhen(last.at)) + '): ' + escapeHtml(last.text || '') + '</div>';
  }

  h += '<div style="margin:.8em 0 .4em"><b>Snapshots</b> '
    + '<span style="color:#9ab">— taken automatically before every push</span>'
    + '<button class="btn" style="background:#446;color:#fff;margin-left:.5em" '
    + 'onclick="_hwSnapshot()"' + (_hw.busy ? ' disabled' : '') + '>Take one now</button></div>';
  if (!_hw.backups.length) {
    h += '<div style="color:#9ab">None yet.</div>';
  } else {
    h += '<table class="tbl" style="width:100%;font-size:.85em"><thead><tr>'
      + '<th>When</th><th>Contents</th><th></th></tr></thead><tbody>';
    _hw.backups.forEach(function (b) {
      h += '<tr><td>' + escapeHtml(_hwWhen(b.at)) + '</td>'
        + '<td>' + escapeHtml(b.summary || '') + '</td>'
        + '<td><button class="btn" style="background:#654;color:#fff" '
        + 'onclick="_hwRestore(\'' + escapeHtml(b.id) + '\')">Restore…</button> '
        + '<button class="btn" style="background:#333;color:#ccc" '
        + 'onclick="_hwDeleteSnapshot(\'' + escapeHtml(b.id) + '\')">Delete</button>'
        + '</td></tr>';
    });
    h += '</tbody></table>';
  }

  h += '<div style="margin-top:1em"><button class="btn btn-on" '
    + 'onclick="_hwGo(2)">Next: edit SlyLED’s copy →</button> '
    + '<button class="btn" style="background:#335;color:#fff" onclick="_hwLoad().then(_hwRender)">'
    + 'Read again</button></div>';
  return h;
}

function _hwDeviceTable(dev) {
  // One block per board, so board 2's ports are never shown as board 1's. A
  // readback that dumped one blob without BLK only ever saw board 0 (#943 B3).
  var h = '';
  Object.keys(dev.boardPorts || {}).sort(function (a, b) { return a - b; })
    .forEach(function (b) {
      h += '<div style="margin-top:.6em"><span style="color:#9ab">Board ' + b + '</span>'
        + '<table style="width:100%;border-collapse:collapse;margin-top:.2em">'
        + '<tr style="color:#9ab;font-size:.85em">'
        + '<th style="text-align:left">out</th><th style="text-align:left">proto</th>'
        + '<th style="text-align:left">start</th><th style="text-align:left">px</th>'
        + '<th style="text-align:left">end</th><th style="text-align:left">order</th></tr>'
        + (dev.boardPorts[b] || []).map(function (r) {
            return '<tr' + (r.used ? ' style="color:#6d6"' : ' style="color:#666"') + '>'
              + '<td>' + r.output + '</td><td>' + r.protocol + '</td><td>' + r.start
              + '</td><td>' + r.pixels + '</td><td>' + r.end + '</td>'
              + '<td>' + escapeHtml(String(r.colorOrder)) + '</td></tr>';
          }).join('')
        + '</table></div>';
    });
  if (dev.serial) {
    h += '<div style="margin-top:.6em"><span style="color:#9ab">J3 DMX-512 out</span> '
      + (dev.serial.dmxActive ? ('on &middot; universe ' + dev.serial.dmxUniverse)
                              : 'off') + '</div>';
  }
  return h;
}

function _hwDiffBox(diff, caption) {
  var items = diff.items || [];
  var h = '<div style="margin-top:.6em"><span style="color:'
    + (diff.changed ? '#fa6' : '#6d6') + '">'
    + (diff.changed ? (items.length + ' difference(s) ') : 'No differences ')
    + '</span><span style="color:#9ab">' + escapeHtml(caption || '') + '</span>';
  if (items.length) {
    h += '<pre style="white-space:pre-wrap;word-break:break-word;max-height:16em;'
      + 'overflow:auto;background:#112;padding:.5em;border-radius:4px;margin:.3em 0 0">'
      + escapeHtml(items.map(function (i) { return i.text; }).join('\n'))
      + '</pre>';
  }
  return h + '</div>';
}

// ── Step 2: the port editor ──────────────────────────────────────────────────

function _hwEditStep() {
  return '<div style="color:#9ab">The port table below SlyLED’s copy of the '
    + 'controller’s configuration. Editing it changes the copy only — the '
    + 'controller keeps running what it has until you push.</div>'
    + '<div style="margin:1em 0"><button class="btn btn-on" '
    + 'onclick="_hwOpenEditor()">Open the port editor…</button></div>'
    + '<div style="color:#9ab;font-size:.9em">Save in the editor, then close it to '
    + 'come back here. A port’s geometry (length in mm) is stage-mm, never a '
    + 'DMX fraction — correct lengths in the fixture editor, not here.</div>'
    + '<div style="margin-top:1em"><button class="btn" style="background:#2563eb;color:#fff" '
    + 'onclick="_hwGo(3)">Next: review the requests →</button></div>';
}

function _hwOpenEditor() {
  // The editor replaces the modal body; the wizard is kept on the modal stack
  // so closing the editor returns here (#945).
  _pushModal();
  hinksConfigure(_hw.cid, true);
}

// ── Step 3: the review ───────────────────────────────────────────────────────

function _hwReviewStep() {
  var h = '';
  if (!_hw.plan) {
    // Never render a stale plan: the stored layout may have changed since it
    // was built, and a preview that no longer matches the request it approves
    // is worse than no preview at all.
    return '<div style="color:#9ab">No plan yet. It is built from SlyLED’s '
      + 'saved configuration, so save an edit first if you made one.</div>'
      + '<div style="margin-top:1em">'
      + '<button class="btn btn-on" onclick="_hwPlan()">Build the plan</button> '
      + '<button class="btn" style="background:#335;color:#fff" '
      + 'onclick="_hwGo(2)">← Edit SlyLED’s copy</button>'
      + '</div>';
  }
  var plan = _hw.plan, reqs = plan.requests || [];
  var boots = reqs.filter(function (r) { return r.kind === 'reboot'; }).length;
  var writes = reqs.filter(function (r) { return r.kind === 'write'; }).length;
  var reads = reqs.filter(function (r) { return r.kind === 'read'; }).length;
  var blocking = _hwBlocking();

  h += '<div style="padding:.6em .8em;border-radius:6px;background:#421">'
    + '<b>' + writes + '</b> write, <b>' + reads + '</b> read, and <b>' + boots
    + '</b> reboot to <b>' + escapeHtml(String(plan.protocol)) + '</b> &middot; '
    + plan.universesUsed + ' of ' + plan.maxUniverses + ' universes &middot; boards '
    + escapeHtml((plan.boards || []).join(', ') || 'none')
    + '<div style="color:#fa6;margin-top:.3em">The controller reboots at the end: it '
    + 'drops off the network for a few seconds and the pixels go dark while it starts.'
    + '</div></div>';

  h += _hwFindings(plan.findings || []);

  h += '<details style="margin:.6em 0"><summary style="cursor:pointer">The '
    + reqs.length + ' requests, exactly as they will be sent</summary>'
    + '<pre style="white-space:pre-wrap;word-break:break-all;max-height:20em;'
    + 'overflow:auto;background:#112;padding:.5em;border-radius:4px;margin:.4em 0 0">'
    + escapeHtml(reqs.map(function (r, i) { return _hwRequestLine(r, i); }).join('\n'))
    + '</pre></details>';

  h += '<div style="margin-top:1em">'
    + '<button class="btn" style="background:' + (blocking.length ? '#444' : '#dc2626')
    + ';color:#fff" onclick="_hwUpload()"' + (blocking.length || _hw.busy ? ' disabled' : '')
    + '>Push to controller and reboot</button> '
    + '<button class="btn" style="background:#335;color:#fff" onclick="_hwPlan()">'
    + 'Rebuild the plan</button></div>';
  if (blocking.length) {
    h += '<div style="color:#f88;margin-top:.4em">Refused until the '
      + blocking.length + ' item(s) marked above are fixed or acknowledged.</div>';
  }
  return h;
}

function _hwRequestLine(r, i) {
  var hl = r.headers || {}, bits = [];
  if (hl.BLK != null) bits.push('BLK: ' + hl.BLK);
  if (hl.ROW != null) bits.push('ROW: ' + hl.ROW);
  return (i + 1) + '. ' + String(r.kind).toUpperCase() + ' ' + r.method + ' ' + r.path
    + (bits.length ? ('  [' + bits.join('] [') + ']') : '')
    + '\n   ' + (r.note || '')
    + (hl.DATA ? ('\n   DATA: ' + hl.DATA) : '');
}

function _hwFindings(findings) {
  var h = '<div style="font-size:.95em">';
  findings.forEach(function (f) {
    // Three levels, and only two of them ask anything of the operator: an
    // error cannot be passed at all, a warning needs a tick, and an info is
    // something true of every push (the reboot) that would otherwise become a
    // box ticked without reading (#945 F3).
    var bad = f.level === 'error', info = f.level === 'info';
    h += '<div style="margin:.3em 0;padding:.4em .6em;border-radius:6px;background:'
      + (bad ? '#511' : (info ? '#123' : '#421')) + '">'
      + '<b>' + (bad ? 'Blocked' : (info ? 'Note' : 'Needs acknowledgement'))
      + '</b>'
      + (f.port != null ? (' &middot; port ' + f.port) : '')
      + ' &mdash; ' + escapeHtml(f.text);
    if (!bad && !info) {
      h += '<label style="display:block;margin-top:.3em"><input type="checkbox" '
        + (_hw.ack[f.code] ? 'checked' : '') + ' onchange="_hwAck(\''
        + escapeHtml(f.code) + '\',this.checked)"> I have read this</label>';
    }
    h += '</div>';
  });
  return h + '</div>';
}

function _hwBlocking() {
  var findings = (_hw.plan && _hw.plan.findings) || [];
  return findings.filter(function (f) {
    // Mirrors `_blocking_findings` on the server: an error is never passable
    // and a warning blocks until ticked. Anything else — info — never blocks.
    return f.level === 'error'
      || (f.level === 'warn' && !_hw.ack[f.code]);
  });
}

function _hwAck(code, on) {
  _hw.ack[code] = !!on;
  _hwRender();
}

function _hwPlan() {
  _hw.msg = null;
  _hwRender();
  return _hwFetch('/api/hinkspix/' + _hw.cid + '/plan').then(function (x) {
    if (x.s !== 200) {
      _hw.plan = null;
      _hw.ack = {};
      var why = (x.d.reasons || []).join('; ') || x.d.err || 'could not build a plan';
      _hwSay('Nothing to push: ' + why, false);
      return;
    }
    _hw.plan = x.d;
    var used = {};
    (x.d.findings || []).forEach(function (f) { used[f.code] = true; });
    Object.keys(_hw.ack).forEach(function (c) { if (!used[c]) delete _hw.ack[c]; });
    _hwRender();
  });
}

// ── Step 4: the push, as a job ───────────────────────────────────────────────

function _hwUpload() {
  _hw.busy = true;
  _hw.msg = null;
  _hwRender();
  _hwFetch('/api/hinkspix/' + _hw.cid + '/apply', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ack: Object.keys(_hw.ack).filter(function (c) { return _hw.ack[c]; })})
  }).then(function (x) {
    _hw.busy = false;
    if (x.s === 409) {
      // The server gates on the same findings; show them where they were read.
      _hw.plan = {requests: (_hw.plan && _hw.plan.requests) || [], protocol: '',
                  universesUsed: 0, maxUniverses: 0, boards: [],
                  findings: x.d.findings || []};
      _hwSay(x.d.err || 'the push was refused', false);
      return;
    }
    if (x.s !== 200 && x.s !== 202) {
      _hwSay('The push could not be started: ' + (x.d.err || x.s), false);
      return;
    }
    _hw.step = 4;
    _hwPollStart();
    _hwRender();
  });
}

function _hwPollStart() {
  _hwPollStop();
  _hw.poll = setInterval(_hwPollTick, 1000);
  window._hwPoll = _hw.poll;
  return _hwPollTick();
}

function _hwPollStop() {
  if (_hw.poll) clearInterval(_hw.poll);
  _hw.poll = null;
  window._hwPoll = null;
}

function _hwOpen() {
  // The wizard is still the operator's panel. Closing the modal only hides it —
  // `closeModal` does not clear the body — so the test is visibility *and*
  // presence, or a poll would keep running behind a closed dialog forever.
  var m = document.getElementById('modal');
  return !!(m && m.style.display !== 'none'
            && document.getElementById('hpw-root'));
}

function _hwPollTick() {
  // Stop if the wizard is no longer on screen: a poll that outlives its panel
  // is a request loop nobody can see or switch off.
  if (!_hwOpen()) { _hwPollStop(); return; }
  return _hwFetch('/api/hinkspix/' + _hw.cid + '/apply').then(function (x) {
    if (x.s !== 200) return;
    _hw.job = x.d;
    _hw.backups = x.d.backups || _hw.backups;
    var st = x.d.state || {};
    if (st.kind) _hw.kind = st.kind;
    if (!st.running) {
      _hwPollStop();
      _hw.step = st.ok ? 5 : 4;
    }
    _hwRender();
  });
}

function _hwApplyStep() {
  var st = (_hw.job && _hw.job.state) || {};
  var running = !!st.running;
  if (!running && !st.phase) {
    return '<div style="color:#9ab">No push has run in this session. Review a '
      + 'configuration and push it from step 3.</div>'
      + '<div style="margin-top:1em"><button class="btn" style="background:#335;color:#fff" '
      + 'onclick="_hwGo(3)">Review the requests →</button></div>';
  }

  var steps = st.steps || [], done = st.stepsDone || [];
  var pct = _HW_PHASE_PCT[st.phase];
  if (pct == null) {
    pct = steps.length ? Math.round(10 + 55 * (done.length / steps.length)) : 12;
  }

  var h = '<div style="padding:.6em .8em;border-radius:6px;background:'
    + (st.phase === 'failed' ? '#511' : (running ? '#123' : '#123')) + '">'
    + '<b>' + escapeHtml(_HW_PHASE[st.phase] || st.phase || 'Working') + '</b>'
    + ' <span style="color:#9ab">(' + escapeHtml(String(st.kind || 'apply')) + ')</span>'
    + '<div style="margin-top:.5em;background:#224;border-radius:4px;height:.7em">'
    + '<div style="width:' + Math.max(2, Math.min(100, pct)) + '%;height:100%;'
    + 'background:' + (st.phase === 'failed' ? '#b91c1c' : '#2563eb')
    + ';border-radius:4px"></div></div>'
    + '<div style="margin-top:.4em;color:#9ab">'
    + escapeHtml(st.message || '') + '</div></div>';

  if (st.backupId) {
    h += '<div style="margin-top:.5em;font-size:.9em;color:#9ab">Snapshot taken '
      + 'before writing: <code>' + escapeHtml(st.backupId) + '</code>'
      + '</div>';
  }
  (st.backupWarnings || []).forEach(function (w) {
    h += '<div style="margin-top:.4em;color:#fa6">The snapshot is incomplete: '
      + escapeHtml(w) + '</div>';
  });
  if (steps.length) {
    h += '<div style="margin-top:.6em;max-height:14em;overflow:auto;font-size:.85em">'
      + steps.map(function (note, i) {
          var mark = i < done.length ? '✓' : (i === done.length && running ? '▶' : '·');
          var colour = i < done.length ? '#6d6' : '#9ab';
          return '<div style="color:' + colour + '">' + mark + ' '
            + escapeHtml(note) + '</div>';
        }).join('') + '</div>';
  }

  if (st.phase === 'failed') {
    // What the controller was left holding, in the two cases that could not be
    // more different: a push that stopped before its first write left the
    // device exactly as it was, and one that stopped at step 40 of 68 left it
    // holding half of each config (#945 F1).
    var accepted = st.accepted || 0, total = st.requests || 0;
    var recover = st.restore;
    // `partial` is the server's own verdict on whether a *write* landed — a
    // request that was accepted before the failure may have been a read, and
    // saying "left part-way" about one of those would be a false alarm.
    var partial = st.partial === undefined ? accepted > 0 : !!st.partial;
    h += '<div style="margin-top:.8em;padding:.6em .8em;border-radius:6px;background:#511">'
      + 'The push stopped: ' + escapeHtml(st.err || 'unknown error')
      + (st.step ? (' (step ' + st.step + ')') : '')
      + (partial
          ? ('<div style="color:#fa6;margin-top:.3em">' + accepted + ' of '
             + total + ' request(s) had been accepted, so the controller was '
             + 'left part-way.</div>')
          : ('<div style="color:#9ab;margin-top:.3em">Nothing had been written '
             + 'yet — the controller still holds what it held before.</div>'));
    if (recover) {
      h += '<div style="margin-top:.3em;color:' + (recover.ok ? '#6d6' : '#fa6') + '">'
        + escapeHtml(String(recover.text || ''))
        + (recover.ok ? '' : ' — the first step lists the snapshots to go back to.')
        + '</div>';
    } else if (partial && st.backupId) {
      h += '<div style="color:#fa6;margin-top:.3em">The snapshot was not put '
        + 'back automatically.</div>';
    }
    // Only offered where there is something to undo. A push refused before its
    // first write needs no repair, and a Restore button beside "nothing had
    // been written" reads as though it does — the snapshot list below is there
    // for the operator who wants to put it back anyway.
    if ((partial || recover) && (!recover || !recover.ok) && st.backupId) {
      h += '<button class="btn" style="background:#654;color:#fff;margin-top:.4em" '
        + 'onclick="_hwRestore(\'' + escapeHtml(st.backupId) + '\')">Restore '
        + escapeHtml(st.backupId) + '</button>';
    }
    h += '</div>';
  }

  h += '<div style="margin-top:1em"><button class="btn" style="background:#335;color:#fff" '
    + 'onclick="_hwGo(5)">Verification →</button> '
    + '<button class="btn" style="background:#335;color:#fff" onclick="_hwPollStart()">'
    + 'Refresh progress</button></div>';
  return h;
}

// ── Step 5: what the controller ended up with ────────────────────────────────

function _hwVerifyStep() {
  var job = _hw.job || {}, verify = job.lastVerify || (job.state || {}).verify;
  var h = '';
  if (!verify) {
    h += '<div style="color:#9ab">No verification yet. Every push ends by reading '
      + 'the controller back and diffing it against what was sent.</div>';
  } else {
    h += '<div style="padding:.6em .8em;border-radius:6px;background:'
      + (verify.ok ? '#123' : '#421') + '"><b>'
      + (verify.ok ? 'The controller holds what was sent'
                   : 'The controller differs from what was sent') + '</b>'
      + ' <span style="color:#9ab">(' + escapeHtml(_hwWhen(verify.at)) + ')</span>'
      + '<div style="color:#9ab;margin-top:.3em">' + escapeHtml(verify.text || '') + '</div>'
      + '</div>';
    var counts = verify.counts || {};
    if (verify.ok === false && verify.text) {
      h += '<div style="margin-top:.5em;color:#9ab">'
        + (counts.ports || 0) + ' port row(s), ' + (counts.universes || 0)
        + ' universe row(s) differ.</div>';
    }
    if (verify.items && verify.items.length) {
      h += '<pre style="white-space:pre-wrap;word-break:break-word;max-height:20em;'
        + 'overflow:auto;background:#112;padding:.5em;border-radius:4px;margin-top:.4em">'
        + escapeHtml(verify.items.map(function (i) { return i.text; }).join('\n'))
        + '</pre>';
    }
    if (verify.warnings && verify.warnings.length) {
      h += '<div style="margin-top:.4em;color:#fa6">'
        + escapeHtml(verify.warnings.join('; ')) + '</div>';
    }
  }

  var last = job.lastApply;
  if (last) {
    h += '<div style="margin-top:.8em;color:#9ab;font-size:.9em">Last run: '
      + escapeHtml(String(last.kind || 'apply')) + ' at '
      + escapeHtml(_hwWhen(last.at)) + ' &middot; '
      + (last.ok ? 'reported success' : 'reported failure')
      + (last.requests ? (' &middot; ' + last.requests + ' requests') : '')
      // A run that stopped part-way is not the same end state as one that was
      // refused before writing, and what happened to the snapshot is the part
      // of it an operator reading this screen needs. A run that failed clean
      // says nothing extra here.
      + (last.ok || !last.partial ? ''
          : (last.restore
              ? (last.restore.ok
                  ? ' &middot; ' + (last.accepted || 0) + ' accepted, then the '
                    + 'snapshot was put back'
                  : ' &middot; ' + (last.accepted || 0) + ' accepted, and the '
                    + 'snapshot could <b>not</b> be put back')
              : ' &middot; ' + (last.accepted || 0) + ' accepted, left part-way'))
      + (last.backupId ? (' &middot; snapshot ' + escapeHtml(last.backupId)) : '')
      + '</div>';
  }

  h += '<div style="margin-top:1em"><button class="btn" style="background:#335;color:#fff" '
    + 'onclick="_hwGo(1, true)">Read the controller again →</button></div>';
  return h;
}

// ── Snapshots: take, restore, delete ─────────────────────────────────────────

function _hwSnapshot() {
  _hw.busy = true;
  _hw.msg = null;
  _hwRender();
  _hwFetch('/api/hinkspix/' + _hw.cid + '/backups', {method: 'POST'})
    .then(function (x) {
      _hw.busy = false;
      if (x.s !== 200) { _hwSay(x.d.err || 'the snapshot could not be taken', false); return; }
      _hwSay('Snapshot ' + x.d.id + ' taken — ' + (x.d.summary || ''), true);
      return _hwLoad().then(_hwRender);
    });
}

function _hwRestore(id) {
  // Preview first: a restore cannot put back the UnPack remap table or the
  // smart-receiver settings, and that is worth reading before, not after.
  _hwFetch('/api/hinkspix/' + _hw.cid + '/restore?backupId=' + encodeURIComponent(id))
    .then(function (x) {
      if (x.s !== 200) { _hwSay(x.d.err || 'no such snapshot', false); return; }
      var d = x.d;
      var warn = (d.warnings || []).map(function (w) { return '\n  - ' + w; }).join('');
      var msg = 'Put snapshot ' + id + ' back on the controller?\n\n'
        + (d.summary || '') + '\n\n'
        + 'This sends ' + (d.requests || []).length + ' requests and reboots the '
        + 'controller. It also snapshots the current state first, so it is itself '
        + 'undoable.'
        + (warn ? ('\n\nA snapshot cannot restore:' + warn) : '');
      if (!confirm(msg)) return;
      _hwBusy(true);
      _hwFetch('/api/hinkspix/' + _hw.cid + '/restore', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({backupId: id})
      }).then(function (y) {
        _hwBusy(false);
        if (y.s !== 200) { _hwSay(y.d.err || 'the restore could not be started', false); return; }
        _hw.step = 4;
        _hwPollStart();
        _hwRender();
      });
    });
}

function _hwDeleteSnapshot(id) {
  if (!confirm('Delete snapshot ' + id + '? This cannot be undone — the '
             + 'controller can still be read again, but not restored to that '
             + 'state.')) return;
  _hwFetch('/api/hinkspix/' + _hw.cid + '/backups/' + encodeURIComponent(id),
           {method: 'DELETE'}).then(function (x) {
    if (x.s !== 200) { _hwSay(x.d.err || 'could not delete it', false); return; }
    _hwSay('Snapshot ' + id + ' deleted', true);
    return _hwLoad().then(_hwRender);
  });
}

function _hwBusy(on) {
  _hw.busy = !!on;
  _hwRender();
}

function _hwWhen(at) {
  if (!at) return 'unknown time';
  try { return new Date(at * 1000).toLocaleString(); }
  catch (e) { return String(at); }
}
