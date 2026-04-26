#!/usr/bin/env python3
"""test_pan_tilt_resolution.py — #689.

Pan/tilt motion writes must drive at fixture-native resolution and route
the 16-bit LSB to the profile's explicit ``pan-fine`` / ``tilt-fine``
offset, not blindly to ``coarse_offset + 1``.

Covers:
- compute_pan_tilt_writes() pure logic (8-bit, 16-bit contiguous,
  16-bit non-contiguous, missing channels, clamping)
- DMXUniverse.set_fixture_pan_tilt() routes through the helper
- write_pan_tilt_to_buffer() lands bytes at the right offsets
- _set_mover_dmx() honours bits=16 and respects non-contiguous fine
- OFL importer emits explicit pan-fine / tilt-fine entries
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0
_errors = []


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _errors.append(name)
        print(f'  [FAIL] {name}')


def section(s):
    print(f'\n── {s} ──')


# ── Pure helper ─────────────────────────────────────────────────────────

from dmx_universe import (compute_pan_tilt_writes, write_pan_tilt_to_buffer,
                          DMXUniverse)

section('compute_pan_tilt_writes — 8-bit')

profile_8 = {
    'channel_map': {'pan': 0, 'tilt': 1},
    'channels': [
        {'offset': 0, 'type': 'pan'},   # bits unspecified → 8-bit
        {'offset': 1, 'type': 'tilt'},
    ],
}
w = compute_pan_tilt_writes(0.5, 0.5, profile_8)
# int(0.5 * 255) = 127 — int() truncates toward zero, matches project convention.
ok(w == [(0, 127), (1, 127)], f'8-bit center (got {w})')

w = compute_pan_tilt_writes(0.0, 1.0, profile_8)
ok(w == [(0, 0), (1, 255)], f'8-bit endpoints (got {w})')

w = compute_pan_tilt_writes(-0.5, 1.5, profile_8)
ok(w == [(0, 0), (1, 255)], f'8-bit clamp out-of-range (got {w})')

section('compute_pan_tilt_writes — 16-bit contiguous')

profile_16_contig = {
    'channel_map': {'pan': 0, 'pan-fine': 1, 'tilt': 2, 'tilt-fine': 3},
    'channels': [
        {'offset': 0, 'type': 'pan',       'bits': 16},
        {'offset': 1, 'type': 'pan-fine'},
        {'offset': 2, 'type': 'tilt',      'bits': 16},
        {'offset': 3, 'type': 'tilt-fine'},
    ],
}
w = compute_pan_tilt_writes(0.5, 0.5, profile_16_contig)
# int(0.5 * 65535) = 32767 = 0x7FFF → MSB=127, LSB=255
ok(w == [(0, 127), (1, 255), (2, 127), (3, 255)],
   f'16-bit contiguous center (got {w})')

w = compute_pan_tilt_writes(1.0, 0.0, profile_16_contig)
ok(w == [(0, 255), (1, 255), (2, 0), (3, 0)],
   f'16-bit contiguous endpoints (got {w})')

# 0.501 should distinguish from 0.5 — only possible at 16-bit precision.
# 0.501 * 65535 = 32833.0 → MSB=128 (0x80), LSB=65 (0x41).
w = compute_pan_tilt_writes(0.501, 0.5, profile_16_contig)
ok(w[0] == (0, 128), f'16-bit 0.501 MSB jumps to 128 (got {w[0]})')
ok(w[1][0] == 1 and w[1][1] != 255,
   f'16-bit 0.501 LSB differs from 0.5 case (got {w[1]})')

section('compute_pan_tilt_writes — 16-bit NON-contiguous (#689 core)')

# Pan coarse at slot 0, but pan-fine at slot 5 (some channel between).
# Tilt coarse at slot 6, tilt-fine at slot 9.
profile_non_contig = {
    'channel_map': {'pan': 0, 'pan-fine': 5, 'tilt': 6, 'tilt-fine': 9},
    'channels': [
        {'offset': 0, 'type': 'pan',       'bits': 16},
        {'offset': 1, 'type': 'dimmer'},
        {'offset': 2, 'type': 'red'},
        {'offset': 3, 'type': 'green'},
        {'offset': 4, 'type': 'blue'},
        {'offset': 5, 'type': 'pan-fine'},  # the trap: NOT at pan + 1
        {'offset': 6, 'type': 'tilt',      'bits': 16},
        {'offset': 7, 'type': 'strobe'},
        {'offset': 8, 'type': 'gobo'},
        {'offset': 9, 'type': 'tilt-fine'},
    ],
}
w = compute_pan_tilt_writes(0.5, 1.0, profile_non_contig)
# Expected: pan 0.5 → 32767 = 0x7FFF → MSB=127, LSB=255 (int truncation)
#           tilt 1.0 → 65535 = 0xFFFF → MSB=255, LSB=255
ok((0, 127) in w, f'pan MSB at offset 0 (got {w})')
ok((5, 255) in w, f'pan LSB at offset 5, NOT 1 (got {w})')
ok((6, 255) in w, f'tilt MSB at offset 6 (got {w})')
ok((9, 255) in w, f'tilt LSB at offset 9, NOT 7 (got {w})')

# Critical regression check — old code would write LSB to `coarse + 1`
# which on this profile is the dimmer (offset 1). Make sure we're NOT
# writing to offsets 1 (dimmer) or 7 (strobe).
ok(not any(off == 1 for off, _ in w),
   f'Did NOT write to dimmer (off+1 trap)')
ok(not any(off == 7 for off, _ in w),
   f'Did NOT write to strobe (off+1 trap)')

section('compute_pan_tilt_writes — split-channel profile (no bits, has pan-fine) — #691')

# The built-in movinghead-150w-12ch + beamlight-350w-16ch model pan/tilt
# as two separate channel entries with NO `bits` annotation. Before #691
# the helper saw bits=8 and silently dropped the LSB.
profile_split = {
    'channel_map': {'pan': 0, 'pan-fine': 1, 'tilt': 2, 'tilt-fine': 3},
    'channels': [
        {'offset': 0, 'type': 'pan'},        # NO bits — relies on pan-fine sibling
        {'offset': 1, 'type': 'pan-fine'},
        {'offset': 2, 'type': 'tilt'},       # NO bits
        {'offset': 3, 'type': 'tilt-fine'},
    ],
}
w = compute_pan_tilt_writes(0.6770, 0.0, profile_split)
# 0.6770 × 65535 = 44367.195 → int(trunc) = 44367 = 0xAD4F → MSB=173 LSB=79
ok((0, 173) in w, f'split profile pan MSB=173 (got {w})')
ok((1, 79)  in w, f'split profile pan LSB=79 — written, not zero (got {w})')
ok((2, 0)   in w, f'split profile tilt MSB=0 (got {w})')
ok((3, 0)   in w, f'split profile tilt LSB=0 (got {w})')

# Verify against the actual built-in 150W profile (regression target).
import dmx_profiles as _dp
prof = _dp.ProfileLibrary().channel_info('movinghead-150w-12ch')
ok(prof is not None, 'built-in movinghead-150w-12ch loads')
if prof:
    real_profile = {'channel_map': prof['channel_map'], 'channels': prof['channels']}
    w = compute_pan_tilt_writes(0.6770, 0.0, real_profile)
    pan_msb_off = real_profile['channel_map']['pan']
    pan_fine_off = real_profile['channel_map']['pan-fine']
    msb_pair = next((p for p in w if p[0] == pan_msb_off), None)
    lsb_pair = next((p for p in w if p[0] == pan_fine_off), None)
    ok(msb_pair == (pan_msb_off, 173),
       f'150W real profile pan MSB=173 at off {pan_msb_off} (got {msb_pair})')
    ok(lsb_pair == (pan_fine_off, 79),
       f'150W real profile pan LSB=79 at off {pan_fine_off} — #691 fix (got {lsb_pair})')

section('compute_pan_tilt_writes — fallback: 16-bit but no pan-fine entry')

# Legacy slymovehead-style: bits=16 declared on coarse channel but no
# explicit pan-fine in the channel list. Helper falls back to off+1.
profile_legacy = {
    'channel_map': {'pan': 0, 'tilt': 2},
    'channels': [
        {'offset': 0, 'type': 'pan',  'bits': 16},
        {'offset': 2, 'type': 'tilt', 'bits': 16},
    ],
}
w = compute_pan_tilt_writes(0.5, 0.5, profile_legacy)
ok(w == [(0, 127), (1, 255), (2, 127), (3, 255)],
   f'legacy fallback writes LSB at off+1 (got {w})')

section('compute_pan_tilt_writes — missing channels')

profile_pan_only = {
    'channel_map': {'pan': 0},
    'channels': [{'offset': 0, 'type': 'pan'}],
}
w = compute_pan_tilt_writes(0.5, 0.5, profile_pan_only)
ok(w == [(0, 127)], f'pan-only profile skips tilt (got {w})')

w = compute_pan_tilt_writes(0.5, 0.5, None)
ok(w == [], f'None profile returns empty')

w = compute_pan_tilt_writes(0.5, 0.5, {})
ok(w == [], f'Empty profile returns empty')

# ── DMXUniverse delegation ──────────────────────────────────────────────

section('DMXUniverse.set_fixture_pan_tilt')

uni = DMXUniverse(1)
uni.set_fixture_pan_tilt(1, 0.5, 0.5, profile_non_contig)
# 0.5 → 32767 = 0x7FFF → MSB=127, LSB=255
ok(uni.get_channel(1) == 127, f'pan MSB on ch 1 = 127 (got {uni.get_channel(1)})')
ok(uni.get_channel(2) == 0,   f'dimmer untouched on ch 2 = 0 (got {uni.get_channel(2)})')
ok(uni.get_channel(6) == 255, f'pan LSB on ch 6 = 255 (got {uni.get_channel(6)})')
ok(uni.get_channel(7) == 127, f'tilt MSB on ch 7 = 127 (got {uni.get_channel(7)})')
ok(uni.get_channel(8) == 0,   f'strobe untouched on ch 8 = 0 (got {uni.get_channel(8)})')
ok(uni.get_channel(10) == 255, f'tilt LSB on ch 10 = 255 (got {uni.get_channel(10)})')

# ── write_pan_tilt_to_buffer ────────────────────────────────────────────

section('write_pan_tilt_to_buffer (mover_calibrator)')

buf = bytearray(512)
write_pan_tilt_to_buffer(buf, 1, 0.5, 1.0, profile_non_contig)
# offsets: pan@0 → buf[0]=127, pan-fine@5 → buf[5]=255 (0x7FFF)
#          tilt@6 → buf[6]=255, tilt-fine@9 → buf[9]=255 (0xFFFF)
ok(buf[0] == 127, f'buf[0] (pan MSB) = 127 (got {buf[0]})')
ok(buf[1] == 0,   f'buf[1] (dimmer slot, untouched) = 0 (got {buf[1]})')
ok(buf[5] == 255, f'buf[5] (pan LSB) = 255 (got {buf[5]})')
ok(buf[6] == 255, f'buf[6] (tilt MSB) = 255 (got {buf[6]})')
ok(buf[7] == 0,   f'buf[7] (strobe slot, untouched) = 0 (got {buf[7]})')
ok(buf[9] == 255, f'buf[9] (tilt LSB) = 255 (got {buf[9]})')

# Start-addr offset check
buf2 = bytearray(512)
write_pan_tilt_to_buffer(buf2, 17, 0.5, 0.0, profile_16_contig)
ok(buf2[16] == 127 and buf2[17] == 255, f'start_addr=17 lands pan at index 16 (got {buf2[16]}, {buf2[17]})')

# ── _set_mover_dmx integration (mover_calibrator) ──────────────────────

section('_set_mover_dmx routes pan/tilt through helper')

import mover_calibrator as mc
buf = bytearray(512)
mc._set_mover_dmx(buf, 1, 0.5, 1.0, 0, 0, 0, dimmer=0, profile=profile_non_contig)
ok(buf[0] == 127, f'mover_calibrator pan MSB = 127 (got {buf[0]})')
ok(buf[5] == 255, f'mover_calibrator pan LSB at non-contig offset 5 (got {buf[5]})')
ok(buf[9] == 255, f'mover_calibrator tilt LSB at non-contig offset 9 (got {buf[9]})')

# ── OFL importer emits pan-fine / tilt-fine ─────────────────────────────

section('OFL importer emits pan-fine / tilt-fine entries')

import json
fake_ofl = {
    'name': 'NonContig Mover',
    'categories': ['Moving Head'],
    'physical': {'lens': {'degreesMinMax': [10, 10]}},
    'availableChannels': {
        'Pan':       {'capabilities': [{'type': 'Pan', 'angleStart': '0deg', 'angleEnd': '540deg'}],
                      'fineChannelAliases': ['Pan fine']},
        'Pan fine':  {'capabilities': [{'type': 'Pan'}]},
        'Dimmer':    {'capabilities': [{'type': 'Intensity'}]},
        'Red':       {'capabilities': [{'type': 'ColorIntensity', 'color': 'Red'}]},
        'Green':     {'capabilities': [{'type': 'ColorIntensity', 'color': 'Green'}]},
        'Blue':      {'capabilities': [{'type': 'ColorIntensity', 'color': 'Blue'}]},
        'Tilt':      {'capabilities': [{'type': 'Tilt', 'angleStart': '0deg', 'angleEnd': '270deg'}],
                      'fineChannelAliases': ['Tilt fine']},
        'Strobe':    {'capabilities': [{'type': 'ShutterStrobe', 'shutterEffect': 'Open'}]},
        'Gobo':      {'capabilities': [{'type': 'WheelSlot'}]},
        'Tilt fine': {'capabilities': [{'type': 'Tilt'}]},
    },
    'modes': [{
        'name': '10-channel',
        'channels': ['Pan', 'Dimmer', 'Red', 'Green', 'Blue',
                     'Pan fine', 'Tilt', 'Strobe', 'Gobo', 'Tilt fine'],
    }],
}

from ofl_importer import ofl_to_slyled
# ofl_to_slyled returns a list of profiles (one per mode).
fake_ofl_payload = dict(fake_ofl)
fake_ofl_payload['name'] = 'NonContig Mover'
profiles = ofl_to_slyled(fake_ofl_payload)
ok(len(profiles) == 1, f'OFL importer produced 1 profile (got {len(profiles)})')

p = profiles[0]
ch_types = [(c['type'], c['offset']) for c in p['channels']]
ok(('pan', 0) in ch_types,        f'pan at wire offset 0 (got {ch_types})')
ok(('pan-fine', 5) in ch_types,   f'pan-fine at wire offset 5, not 1 (got {ch_types})')
ok(('tilt', 6) in ch_types,       f'tilt at wire offset 6 (got {ch_types})')
ok(('tilt-fine', 9) in ch_types,  f'tilt-fine at wire offset 9, not 7 (got {ch_types})')

# Round-trip: build channel_map and use the helper.
cm = {c['type']: c['offset'] for c in p['channels']}
profile = {'channel_map': cm, 'channels': p['channels']}
w = compute_pan_tilt_writes(0.5, 1.0, profile)
ok((0, 127) in w and (5, 255) in w,
   f'helper round-trips OFL profile pan (got {w})')
ok((6, 255) in w and (9, 255) in w,
   f'helper round-trips OFL profile tilt (got {w})')

# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
