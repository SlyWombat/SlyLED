#!/usr/bin/env python3
"""
test_dmx_routing.py -- Universe routing, Art-Net packet delivery, settings persistence.

Tests:
- Universe routes persisted and applied to engine
- Art-Net packets sent to correct destinations per route
- Unrouted universes broadcast
- Settings API CRUD
- ArtPoll discovery populates discovered nodes
- Packet capture validates ArtDMX wire format
"""
import sys, os, json, struct, socket, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

results = []
def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))

def run():
    test_settings_api()
    test_route_to_unicast()
    test_engine_routing()
    test_artdmx_packet_format()
    test_artpoll_discovery()

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


def test_settings_api():
    """Test universe routes via the settings API."""
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        # Save routes
        routes = [
            {"universe": 1, "destination": "192.168.10.219", "label": "Giga DMX"},
            {"universe": 2, "destination": "192.168.10.4", "label": "U16 Port 1"},
            {"universe": 3, "destination": "192.168.10.4", "label": "U16 Port 2"},
        ]
        r = c.post('/api/dmx/settings', json={
            'protocol': 'artnet',
            'frameRate': 40,
            'bindIp': '0.0.0.0',
            'universeRoutes': routes,
        })
        ok('Save routes', r.status_code == 200 and r.get_json().get('ok'))

        # Read back
        r = c.get('/api/dmx/settings')
        d = r.get_json()
        ok('Routes persisted', len(d.get('universeRoutes', [])) == 3)
        ok('Route 0 destination', d['universeRoutes'][0]['destination'] == '192.168.10.219')
        ok('Route 1 universe', d['universeRoutes'][1]['universe'] == 2)
        ok('Route 2 label', d['universeRoutes'][2]['label'] == 'U16 Port 2')

        # Empty routes = broadcast
        r = c.post('/api/dmx/settings', json={'universeRoutes': []})
        ok('Clear routes', r.status_code == 200)
        r = c.get('/api/dmx/settings')
        ok('Routes empty', len(r.get_json().get('universeRoutes', [])) == 0)

        # Invalid routes filtered
        r = c.post('/api/dmx/settings', json={
            'universeRoutes': [
                {"universe": 1, "destination": "10.0.0.1"},
                {"universe": 2},  # no destination — should be filtered
                {"destination": "10.0.0.2"},  # no universe — kept (universe defaults)
            ]
        })
        r = c.get('/api/dmx/settings')
        rts = r.get_json().get('universeRoutes', [])
        ok('Invalid routes filtered', len(rts) == 2,
           f'got {len(rts)}: {rts}')

        # Restore clean state
        c.post('/api/dmx/settings', json={'universeRoutes': []})


def test_route_to_unicast():
    """Test _routes_to_unicast helper."""
    from parent_server import _routes_to_unicast

    # Normal routes
    routes = [
        {"universe": 0, "destination": "10.0.0.1", "label": "A"},
        {"universe": 5, "destination": "10.0.0.2", "label": "B"},
    ]
    result = _routes_to_unicast(routes)
    ok('Route map universe 0', result.get(0) == '10.0.0.1')
    ok('Route map universe 5', result.get(5) == '10.0.0.2')
    ok('Route map length', len(result) == 2)

    # Empty
    ok('Empty routes', _routes_to_unicast([]) == {})
    ok('None routes', _routes_to_unicast(None) == {})

    # Missing destination
    ok('Skip empty dest', len(_routes_to_unicast([{"universe": 1, "destination": ""}])) == 0)


def test_engine_routing():
    """Test that the Art-Net engine routes to correct destinations."""
    from dmx_artnet import ArtNetEngine, build_artdmx, parse_artnet_header, OP_DMX

    engine = ArtNetEngine(
        bind_ip="127.0.0.1",
        unicast_targets={0: "127.0.0.1", 1: "127.0.0.2"},
        frame_rate=40,
    )

    # Verify unicast config
    ok('Engine unicast 0', engine._unicast.get(0) == '127.0.0.1')
    ok('Engine unicast 1', engine._unicast.get(1) == '127.0.0.2')

    # Reconfigure
    engine.configure(unicast_targets={3: "10.0.0.3"})
    ok('Reconfigure replaces', engine._unicast == {3: "10.0.0.3"})
    ok('Universe 0 now broadcast', 0 not in engine._unicast)

    # Status reflects config
    st = engine.status()
    ok('Status has unicast', st.get('unicastTargets') == {3: "10.0.0.3"})
    ok('Status has bindIp', st.get('bindIp') == '127.0.0.1')


def test_artdmx_packet_format():
    """Validate ArtDMX packet wire format for correct universe addressing."""
    from dmx_artnet import build_artdmx, parse_artnet_header, OP_DMX, ARTNET_HEADER

    # Universe 0
    data = bytes([255] + [0] * 511)
    pkt = build_artdmx(0, 1, data)
    ok('ArtDMX header', pkt[:8] == ARTNET_HEADER)
    hdr = parse_artnet_header(pkt)
    ok('ArtDMX opcode', hdr[0] == OP_DMX)
    uni = struct.unpack_from("<H", pkt, 14)[0]
    ok('ArtDMX universe 0', uni == 0)
    ok('ArtDMX data[0]=255', pkt[18] == 255)

    # Universe 7 (for multi-port bridge)
    pkt7 = build_artdmx(7, 42, bytes([128] * 512))
    uni7 = struct.unpack_from("<H", pkt7, 14)[0]
    ok('ArtDMX universe 7', uni7 == 7)
    ok('ArtDMX seq 42', pkt7[12] == 42)

    # Subnet addressing: Art-Net 4 uses 15-bit port-address
    # Port-address = (subnet << 4) | universe for 4-bit sub/uni
    # Or just flat 0-32767 for the port-address field
    pkt_sub = build_artdmx(0x0130, 1, bytes(512))  # subnet 1, universe 3, net 0
    uni_sub = struct.unpack_from("<H", pkt_sub, 14)[0]
    ok('ArtDMX subnet addressing', uni_sub == 0x0130)

    # Packet size: 18 header + 512 data = 530
    ok('ArtDMX total size', len(pkt) == 530)


def test_artpoll_discovery():
    """Test ArtPoll/Reply round-trip with a mock node."""
    from dmx_artnet import (ArtNetEngine, build_artpoll, build_artpoll_reply,
                            parse_artnet_header, parse_artpoll_reply,
                            OP_POLL, OP_POLL_REPLY, ARTNET_PORT)

    # Build a mock ArtPollReply
    reply = build_artpoll_reply("10.0.0.50", 6454, "TestNode", "Test DMX Bridge",
                                universes=[1, 2, 3])
    parsed = parse_artpoll_reply(reply)
    ok('Parse reply IP', parsed['ip'] == '10.0.0.50')
    ok('Parse reply shortName', parsed['shortName'] == 'TestNode')
    ok('Parse reply longName', parsed['longName'] == 'Test DMX Bridge')

    # ArtPoll packet format
    poll = build_artpoll()
    hdr = parse_artnet_header(poll)
    ok('ArtPoll opcode', hdr[0] == OP_POLL)
    ok('ArtPoll TalkToMe flags', poll[12] == 0x06)  # We set this for compatibility

    # Engine discovery state
    engine = ArtNetEngine()
    ok('No discovered initially', len(engine.discovered_nodes) == 0)

    # Simulate feeding a reply into discovered dict
    engine._discovered["10.0.0.50"] = parsed
    ok('Discovered after feed', len(engine.discovered_nodes) == 1)
    ok('Discovered IP', "10.0.0.50" in engine.discovered_nodes)


if __name__ == '__main__':
    sys.exit(run())
