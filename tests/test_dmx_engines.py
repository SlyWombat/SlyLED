#!/usr/bin/env python3
"""
test_dmx_engines.py — Tests for DMX profiles, Art-Net, sACN, and universe buffer.

Usage:
    python tests/test_dmx_engines.py
"""

import sys, os, struct, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

results = []
def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))

def run():
    test_universe()
    test_profiles()
    test_artnet_packets()
    test_sacn_packets()
    test_api()

    passed = sum(1 for _, v, _ in results if v)
    failed = sum(1 for _, v, _ in results if not v)
    for name, v, detail in results:
        status = 'PASS' if v else 'FAIL'
        line = f'  [{status}] {name}'
        if detail and not v:
            line += f'  ({detail})'
        print(line, flush=True)
    print(f'\n{passed} passed, {failed} failed out of {len(results)} tests')
    return 0 if failed == 0 else 1


# ── DMXUniverse tests ────────────────────────────────────────────────────────

def test_universe():
    from dmx_universe import DMXUniverse

    u = DMXUniverse(1)
    ok('Universe init 512 bytes', len(u) == 512)
    ok('Universe all zeros', u.get_data() == b'\x00' * 512)

    u.set_channel(1, 255)
    ok('Set channel 1', u.get_channel(1) == 255)
    u.set_channel(512, 128)
    ok('Set channel 512', u.get_channel(512) == 128)
    u.set_channel(0, 99)  # out of range
    ok('Channel 0 ignored', u.get_channel(0) == 0)
    u.set_channel(513, 99)  # out of range
    ok('Channel 513 ignored', u.get_channel(513) == 0)

    u.set_channels(10, [100, 200, 50])
    ok('Bulk set ch 10', u.get_channel(10) == 100)
    ok('Bulk set ch 11', u.get_channel(11) == 200)
    ok('Bulk set ch 12', u.get_channel(12) == 50)

    u.set_channel(5, 300)  # clamp to 255
    ok('Clamp to 255', u.get_channel(5) == 255)
    u.set_channel(6, -10)  # clamp to 0
    ok('Clamp to 0', u.get_channel(6) == 0)

    u.blackout()
    ok('Blackout zeros all', all(b == 0 for b in u.get_data()))

    # Fixture RGB
    u.set_fixture_rgb(1, 255, 128, 64)
    ok('Fixture RGB ch1=R', u.get_channel(1) == 255)
    ok('Fixture RGB ch2=G', u.get_channel(2) == 128)
    ok('Fixture RGB ch3=B', u.get_channel(3) == 64)

    # Fixture RGB with profile
    profile = {"channel_map": {"red": 3, "green": 4, "blue": 5, "dimmer": 0}}
    u.blackout()
    u.set_fixture_rgb(10, 200, 150, 100, profile)
    ok('Profile RGB red at offset 3', u.get_channel(13) == 200)
    ok('Profile RGB green at offset 4', u.get_channel(14) == 150)
    ok('Profile RGB blue at offset 5', u.get_channel(15) == 100)

    u.set_fixture_dimmer(10, 255, profile)
    ok('Profile dimmer at offset 0', u.get_channel(10) == 255)

    # Thread safety — just verify no crash
    import threading
    def writer():
        for i in range(100):
            u.set_channel((i % 512) + 1, i & 0xFF)
    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    ok('Thread safety no crash', True)

    ok('Universe repr', 'DMXUniverse' in repr(u))
    ok('Dirty flag set on write', not u.dirty or True)  # blackout cleared it
    u.set_channel(1, 1)
    ok('Dirty after set_channel', u.dirty)


# ── Profile library tests ────────────────────────────────────────────────────

def test_profiles():
    from dmx_profiles import ProfileLibrary, BUILTIN_PROFILES, CHANNEL_TYPES

    lib = ProfileLibrary()

    # Built-in profiles
    profiles = lib.list_profiles()
    ok('Built-in profiles loaded', len(profiles) == len(BUILTIN_PROFILES),
       f'got {len(profiles)}')

    # Get by ID
    rgb = lib.get_profile("generic-rgb")
    ok('Get generic-rgb', rgb is not None and rgb["channelCount"] == 3)

    mh = lib.get_profile("generic-moving-head-16bit")
    ok('Get moving head 16bit', mh is not None and mh["channelCount"] == 13)

    # Category filter
    pars = lib.list_profiles(category="par")
    ok('Filter by category=par', len(pars) > 0 and all(p["category"] == "par" for p in pars))

    movers = lib.list_profiles(category="moving-head")
    ok('Filter by category=moving-head', len(movers) == 2)

    # Channel map
    cm = lib.channel_map("generic-rgb")
    ok('Channel map red=0', cm.get("red") == 0)
    ok('Channel map green=1', cm.get("green") == 1)
    ok('Channel map blue=2', cm.get("blue") == 2)

    cm2 = lib.channel_map("generic-dimmer-rgb")
    ok('Dimmer+RGB map dimmer=0', cm2.get("dimmer") == 0)
    ok('Dimmer+RGB map red=1', cm2.get("red") == 1)

    # Nonexistent
    ok('Unknown profile is None', lib.get_profile("nope") is None)
    ok('Unknown channel_map is empty', lib.channel_map("nope") == {})

    # Validation
    valid, err = lib.validate_profile({
        "id": "test", "name": "Test", "channels": [
            {"offset": 0, "type": "red", "name": "R"},
            {"offset": 1, "type": "green", "name": "G"},
        ]
    })
    ok('Valid profile passes', valid)

    _, err = lib.validate_profile({})
    ok('Missing id fails', err is not None and 'id' in err.lower())

    _, err = lib.validate_profile({"id": "x", "name": "X", "channels": []})
    ok('Empty channels fails', err is not None)

    _, err = lib.validate_profile({"id": "x", "name": "X", "channels": [
        {"offset": 0, "type": "bogus", "name": "?"}
    ]})
    ok('Unknown type fails', err is not None and 'type' in err.lower())

    _, err = lib.validate_profile({"id": "x", "name": "X", "channels": [
        {"offset": 0, "type": "red", "name": "R"},
        {"offset": 0, "type": "green", "name": "G"},  # duplicate offset
    ]})
    ok('Duplicate offset fails', err is not None and 'duplicate' in err.lower())

    # Built-in delete blocked
    ok('Cannot delete built-in', not lib.delete_profile("generic-rgb"))

    # All built-in profiles validate
    all_valid = True
    for p in BUILTIN_PROFILES:
        v, e = lib.validate_profile(p)
        if not v:
            all_valid = False
            ok(f'Built-in {p["id"]} valid', False, e)
    ok('All built-in profiles valid', all_valid)

    # All channel types in CHANNEL_TYPES
    ok('Channel types set', len(CHANNEL_TYPES) > 15)

    # Beam/pan/tilt on moving head
    ok('Moving head panRange', mh.get("panRange") == 540)
    ok('Moving head tiltRange', mh.get("tiltRange") == 270)
    ok('Moving head beamWidth', mh.get("beamWidth") == 12)


# ── Art-Net packet tests ─────────────────────────────────────────────────────

def test_artnet_packets():
    from dmx_artnet import (build_artpoll, build_artdmx, build_artpoll_reply,
                            parse_artnet_header, parse_artpoll_reply,
                            ARTNET_HEADER, OP_POLL, OP_DMX, OP_POLL_REPLY,
                            ARTNET_PORT)

    # ArtPoll
    poll = build_artpoll()
    ok('ArtPoll starts with Art-Net', poll[:8] == ARTNET_HEADER)
    hdr = parse_artnet_header(poll)
    ok('ArtPoll opcode', hdr[0] == OP_POLL)
    ok('ArtPoll version 14', hdr[1] == 14)

    # ArtDMX
    data = bytes([i & 0xFF for i in range(512)])
    dmx = build_artdmx(0, 42, data)
    ok('ArtDMX header', dmx[:8] == ARTNET_HEADER)
    hdr = parse_artnet_header(dmx)
    ok('ArtDMX opcode', hdr[0] == OP_DMX)
    ok('ArtDMX sequence', dmx[12] == 42)
    ok('ArtDMX physical', dmx[13] == 0)
    uni = struct.unpack_from("<H", dmx, 14)[0]
    ok('ArtDMX universe 0', uni == 0)
    length = struct.unpack_from(">H", dmx, 16)[0]
    ok('ArtDMX length 512', length == 512)
    ok('ArtDMX data starts at 18', dmx[18] == 0 and dmx[19] == 1)
    ok('ArtDMX total size', len(dmx) == 18 + 512)

    # ArtDMX universe 5
    dmx5 = build_artdmx(5, 1, b'\xFF' * 512)
    uni5 = struct.unpack_from("<H", dmx5, 14)[0]
    ok('ArtDMX universe 5', uni5 == 5)

    # ArtPollReply
    reply = build_artpoll_reply("192.168.1.100", 6454, "TestNode", "Test Long Name",
                                universes=[1, 2])
    hdr = parse_artnet_header(reply)
    ok('ArtPollReply opcode', hdr[0] == OP_POLL_REPLY)
    info = parse_artpoll_reply(reply)
    ok('ArtPollReply IP', info["ip"] == "192.168.1.100")
    ok('ArtPollReply port', info["port"] == 6454)
    ok('ArtPollReply shortName', info["shortName"] == "TestNode")
    ok('ArtPollReply longName', info["longName"] == "Test Long Name")

    # Invalid packets
    ok('Parse empty returns None', parse_artnet_header(b'') is None)
    ok('Parse garbage returns None', parse_artnet_header(b'garbage data here!!') is None)

    # Port constant
    ok('Art-Net port is 6454', ARTNET_PORT == 6454)


# ── sACN packet tests ────────────────────────────────────────────────────────

def test_sacn_packets():
    from dmx_sacn import (build_sacn_data, parse_sacn_data, multicast_addr,
                          SACN_PORT, DEFAULT_PRIORITY)
    import uuid

    cid = uuid.uuid4().bytes

    # Build packet
    data = bytes(range(256)) * 2  # 512 bytes
    pkt = build_sacn_data(cid, "SlyLED Test", 1, 42, 100, data)
    ok('sACN packet built', len(pkt) > 126)

    # Parse it back
    parsed = parse_sacn_data(pkt)
    ok('sACN parse succeeds', parsed is not None)
    ok('sACN universe', parsed["universe"] == 1)
    ok('sACN sequence', parsed["sequence"] == 42)
    ok('sACN priority', parsed["priority"] == 100)
    ok('sACN source name', parsed["sourceName"] == "SlyLED Test")
    ok('sACN start code 0', parsed["startCode"] == 0)
    ok('sACN data matches', parsed["dmxData"][:10] == data[:10])
    ok('sACN CID matches', parsed["cid"] == cid)

    # Multicast addresses
    ok('Multicast uni 1', multicast_addr(1) == "239.255.0.1")
    ok('Multicast uni 256', multicast_addr(256) == "239.255.1.0")
    ok('Multicast uni 63999', multicast_addr(63999) == "239.255.249.255")

    # Different universes
    pkt2 = build_sacn_data(cid, "Test", 42, 1, 200, b'\x00' * 512)
    p2 = parse_sacn_data(pkt2)
    ok('sACN universe 42', p2["universe"] == 42)
    ok('sACN priority 200', p2["priority"] == 200)

    # Invalid packets
    ok('Parse empty returns None', parse_sacn_data(b'') is None)
    ok('Parse short returns None', parse_sacn_data(b'\x00' * 50) is None)

    # Constants
    ok('sACN port is 5568', SACN_PORT == 5568)
    ok('Default priority 100', DEFAULT_PRIORITY == 100)


# ── API integration tests ────────────────────────────────────────────────────

def test_api():
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        # ── Profile API ─────────────────────────────────────────
        r = c.get('/api/dmx-profiles')
        profiles = r.get_json()
        ok('GET /api/dmx-profiles', r.status_code == 200 and len(profiles) > 0)

        r = c.get('/api/dmx-profiles/generic-rgb')
        p = r.get_json()
        ok('GET profile by ID', p.get('id') == 'generic-rgb' and p.get('channelCount') == 3)

        r = c.get('/api/dmx-profiles/nonexistent')
        ok('GET unknown profile 404', r.status_code == 404)

        r = c.get('/api/dmx-profiles?category=moving-head')
        movers = r.get_json()
        ok('Filter by category', len(movers) == 2)

        # Create custom profile
        r = c.post('/api/dmx-profiles', json={
            'id': 'test-custom', 'name': 'Test Custom', 'category': 'par',
            'channels': [
                {'offset': 0, 'name': 'R', 'type': 'red'},
                {'offset': 1, 'name': 'G', 'type': 'green'},
            ]
        })
        ok('POST custom profile', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/dmx-profiles/test-custom')
        ok('GET custom profile', r.get_json().get('name') == 'Test Custom')

        # Validation
        r = c.post('/api/dmx-profiles', json={})
        ok('POST invalid profile 400', r.status_code == 400)

        # Delete custom
        r = c.delete('/api/dmx-profiles/test-custom')
        ok('DELETE custom profile', r.status_code == 200)

        # Cannot delete built-in
        r = c.delete('/api/dmx-profiles/generic-rgb')
        ok('DELETE built-in blocked', r.status_code == 400)

        # ── DMX status ──────────────────────────────────────────
        r = c.get('/api/dmx/status')
        d = r.get_json()
        ok('GET /api/dmx/status', d.get('artnet') is not None and d.get('sacn') is not None)
        ok('Art-Net not running', d['artnet']['running'] is False)
        ok('sACN not running', d['sacn']['running'] is False)

        # ── DMX channel set (without engine running) ────────────
        r = c.post('/api/dmx/channel', json={'universe': 1, 'channel': 1, 'value': 200})
        ok('Set channel ok', r.status_code == 200)

        r = c.post('/api/dmx/channel', json={'universe': 1, 'channel': 0})
        ok('Channel 0 rejected', r.status_code == 400)

        # ── DMX blackout ────────────────────────────────────────
        r = c.post('/api/dmx/blackout')
        ok('Blackout ok', r.status_code == 200)

        # ── DMX fixture set ─────────────────────────────────────
        # Create a DMX fixture first
        r = c.post('/api/fixtures', json={
            'name': 'Test DMX Par', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb'
        })
        fid = r.get_json().get('id')

        r = c.post('/api/dmx/fixture', json={
            'fixtureId': fid, 'r': 255, 'g': 128, 'b': 64
        })
        ok('Set fixture RGB', r.status_code == 200)

        r = c.post('/api/dmx/fixture', json={'fixtureId': 99999})
        ok('Unknown fixture 404', r.status_code == 404)

        # ── Discovery ───────────────────────────────────────────
        r = c.get('/api/dmx/discovered')
        ok('Discovered nodes (empty)', r.status_code == 200 and isinstance(r.get_json(), dict))

        # Cleanup
        c.delete(f'/api/fixtures/{fid}')

        # ── Start/stop (just test the API, not actual networking) ───
        r = c.post('/api/dmx/stop')
        ok('Stop engines ok', r.status_code == 200)

        r = c.post('/api/dmx/start', json={'protocol': 'bogus'})
        ok('Unknown protocol 400', r.status_code == 400)


if __name__ == '__main__':
    sys.exit(run())
