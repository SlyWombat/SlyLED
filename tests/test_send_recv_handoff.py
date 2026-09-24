#!/usr/bin/env python3
"""test_send_recv_handoff.py — #948: _send_recv on macOS/BSD.

Children always reply to UDP 4210. On macOS/BSD a unicast datagram to a
shared port goes to the OLDEST socket — the 4210 listener — so
parent_server hands the reply from the listener to the waiting _send_recv
(_SEND_RECV_VIA_LISTENER). This forces that path on any OS and drives the
real _udp_listener loop with a fake socket.

Run:
    python3 tests/test_send_recv_handoff.py
"""

import os
import struct
import sys
import tempfile
import threading
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-handoff-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import net_ifaces  # noqa: E402
import parent_server as ps  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def eq(name, got, want):
    ok(name, got == want, f"got {got!r}, want {want!r}")


CHILD = "10.1.2.3"
OTHER = "10.1.2.4"


def frame(cmd, payload=b""):
    return struct.pack("<HBBI", ps.UDP_MAGIC, ps.UDP_VERSION, cmd, 0) + payload


def reply_later(ip, data, delay=0.1):
    def go():
        time.sleep(delay)
        ps._udp_waiter_deliver(ip, data)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


class _Stop(BaseException):
    """Escapes _udp_listener's `except Exception: continue`."""


class FakeListenerSock:
    def __init__(self, datagrams):
        self._q = list(datagrams)

    def recvfrom(self, n):
        if self._q:
            return self._q.pop(0)
        raise _Stop()


def run():
    eq("gate follows net_ifaces.REUSEPORT (macOS/BSD)", ps._SEND_RECV_VIA_LISTENER, net_ifaces.REUSEPORT)
    ok("no waiter -> listener dispatches normally", ps._udp_waiter_deliver(CHILD, b"x") is False)

    saved = (ps._SEND_RECV_VIA_LISTENER, ps._udp_status.get("ok"), ps._send, ps._try_bind_udp)
    sent = []
    try:
        ps._SEND_RECV_VIA_LISTENER = True
        ps._udp_status["ok"] = True
        ps._send = lambda ip, pkt: sent.append((ip, pkt))

        resp = frame(ps.CMD_STATUS_RESP, b"\x00" * 8)
        reply_later(CHILD, resp)
        got = ps._send_recv(CHILD, frame(ps.CMD_STATUS_REQ), timeout=2)
        eq("reply handed over from the listener", got, resp)
        eq("request went out via _send", [ip for ip, _ in sent], [CHILD])
        eq("_udp_status.sendRecvPort = listener", ps._udp_status.get("sendRecvPort"), "listener")
        ok("waiter table empty after success", not ps._udp_waiters, ps._udp_waiters)

        reply_later(OTHER, b"not-for-you", delay=0.05)
        t0 = time.time()
        got = ps._send_recv(CHILD, frame(ps.CMD_PING), timeout=0.4)
        eq("datagram from another IP is not taken; times out -> None", got, None)
        ok("timeout honoured", 0.35 <= time.time() - t0 < 1.5, time.time() - t0)
        ok("waiter table empty after timeout", not ps._udp_waiters, ps._udp_waiters)

        # Two concurrent requests to one child: FIFO.
        out = {}

        def ask(tag):
            out[tag] = ps._send_recv(CHILD, frame(ps.CMD_PING), timeout=2)
        t1 = threading.Thread(target=ask, args=("first",))
        t1.start()
        time.sleep(0.05)
        t2 = threading.Thread(target=ask, args=("second",))
        t2.start()
        time.sleep(0.05)
        ps._udp_waiter_deliver(CHILD, b"A")
        ps._udp_waiter_deliver(CHILD, b"B")
        t1.join(3)
        t2.join(3)
        eq("concurrent waiters served in order", (out.get("first"), out.get("second")), (b"A", b"B"))

        # The real listener loop: a waited-for reply is diverted, not dispatched;
        # a datagram from another child still reaches its handler.
        dispatched = []
        saved_entry = ps._UDP_DISPATCH.get(ps.CMD_ACTION_EVENT)
        ps._UDP_DISPATCH[ps.CMD_ACTION_EVENT] = (8, lambda ip, port, hdr, data: dispatched.append(ip))
        slot = [threading.Event(), None]
        ps._udp_waiters[CHILD] = [slot]
        ev_child = frame(ps.CMD_ACTION_EVENT, b"\x00" * 4)
        ev_other = frame(ps.CMD_ACTION_EVENT, b"\x01" * 4)
        ps._try_bind_udp = lambda port: FakeListenerSock([(ev_child, (CHILD, 4210)),
                                                          (ev_other, (OTHER, 4210))])
        try:
            ps._udp_listener()
        except _Stop:
            pass
        finally:
            if saved_entry is None:
                ps._UDP_DISPATCH.pop(ps.CMD_ACTION_EVENT, None)
            else:
                ps._UDP_DISPATCH[ps.CMD_ACTION_EVENT] = saved_entry
        eq("listener hands the waited-for datagram to the waiter", slot[1], ev_child)
        eq("listener still dispatches everything else", dispatched, [OTHER])
        ok("waiter consumed", CHILD not in ps._udp_waiters, ps._udp_waiters)
    finally:
        (ps._SEND_RECV_VIA_LISTENER, ps._udp_status["ok"], ps._send, ps._try_bind_udp) = saved


def main():
    run()
    passed = sum(1 for _, c, _ in results if c)
    failed = len(results) - passed
    for name, cond, detail in results:
        tag = "PASS" if cond else "FAIL"
        extra = f"  ({detail})" if (detail and not cond) else ""
        print(f"  [{tag}] {name}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
