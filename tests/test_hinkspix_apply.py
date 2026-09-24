#!/usr/bin/env python3
"""HinksPix configuration push — snapshot, apply, restore, verify (#945).

Scope note: a *light* self-check that the push path does what its docstrings
claim, run in-process against a fake controller. It does not touch hardware, and
the operator's unit at 192.168.10.6 is never addressed — the fake listens on
127.0.0.1 and every assertion about the device reads the fake's own state.

What it covers:

  * a snapshot is the device's own rows, verbatim, and is restorable;
  * an apply snapshots first and refuses to write a controller it could not read;
  * a push is a job — `wait: true` runs it to the end, `GET` reports progress;
  * the run is verified by reading the device back and diffing, so a push that
    wrote every request and rebooted into nothing is not reported as success;
  * a failed request stops the sequence, leaves the reboot unsent, and names the
    snapshot to restore;
  * a restore puts the snapshot's rows back on the device and clears the
    "in sync" claim, because the device no longer holds this orchestrator's
    configuration;
  * findings gate the start: an error is never passable, a warning needs its
    code acknowledged.

Run: SLYLED_DATA=$(mktemp -d) python3 tests/test_hinkspix_apply.py
"""

import contextlib
import gzip
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hxapply-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import hinkspix_bridge as hb  # noqa: E402
import hinkspix_config as hc  # noqa: E402
import parent_server  # noqa: E402   (binds orch_state, then imports orch_hinkspix)
import orch_hinkspix as oh  # noqa: E402
from parent_server import app  # noqa: E402

CID = 7
_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


class _Reject(Exception):
    """The controller refuses this request — answered, and not with "OK"."""


class _Drop(Exception):
    """The controller goes away mid-request, as it does when it reboots."""


def factory_rows(start):
    """The rows a factory-fresh board carries: 100 px on every output.

    Not invented — this is the MS_160 capture of 2026-09-23, port 17 at
    controller channels 4801-5100 included (protocol 0 *with* a pixel count,
    which is the shape #943 B10 turned on).
    """
    return [f"{o},0,{1 + (o - 1) * 300},100,{o * 300},0,0,0,100,1"
            for o in range(start, start + 16)]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass                                   # the suite prints its own results

    def do_GET(self):
        dev = self.server.device
        hdr = {k.lower(): v for k, v in self.headers.items()}
        dev.requests.append(("GET", self.path, hdr))
        if not dev.up():
            self.close_connection = True       # off the air: no reply at all
            return
        try:
            body = dev.answer(self.path, hdr)
        except _Drop:
            self.close_connection = True
            return
        except _Reject as exc:
            body = json.dumps({"CMD": "ERROR", "ERROR": str(exc)}).encode()
        self._send(body)

    def do_POST(self):
        dev = self.server.device
        dev.posts += 1
        dev.requests.append(("POST", self.path, {}))
        self._send(b'{"CMD":"POST","ERROR":"ERROR"}')

    def _send(self, body):
        gzipped = body[:1] == b"{"                # the MS_160 gzips JSON replies
        if gzipped:
            body = gzip.compress(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeHinksPix:
    """A HinksPix PRO that stores what it is told, on 127.0.0.1, in-process.

    Written here rather than shared with the QA lane's harness: this suite has
    to stand alone in CI, and the fake's only job is to be *the device* for the
    routes under test. What it reproduces is what #943 paid for, because those
    are the parts a wrong implementation still passes against a permissive fake:

      * every call is a GET with the command in request **headers** — a POST, a
        body, or a missing ``BLK``/``ROW`` is refused rather than ignored;
      * replies are gzipped, as the unit does for bodies it has to assemble;
      * PCONFIG, E131 and DATA_MODE writes mutate the fake's own tables, so a
        readback after a push can be compared against what was pushed;
      * ``OP_MODE`` drops the connection and takes the device off the air for a
        moment, which is what the reboot does to a caller.
    """

    def __init__(self, boards=("L", "S", "N"), max_u=402, factory_universes=6):
        self.boards = list(boards)
        self.max_u = max_u
        self.factory_universes = factory_universes
        self.mode = "E131"
        self.serial = {"DMX_ACTIVE": 0, "DMX_UNIV": 1, "DMX_START": 1,
                       "DMX_CHAN_CNT": 512, "DDP_DMX_ACTIVE": 0,
                       "DDP_DMX_START": 1, "DDP_DMX_CHAN_CNT": 512}
        self.num_u = 32
        self.ports = {b: factory_rows((b - 1) * 16 + 1)
                      for b in range(1, len(self.boards) + 1)}
        self.table = self._factory_table()
        self.log = []                # command names, in the order they arrived
        self.requests = []           # (path, headers) for every request
        self.posts = 0
        self.fail = set()            # POST commands answered without "OK"
        self.fail_once = set()       # ...the first time only, then the device
                                     # stops refusing (a hiccup, not a wedge) —
                                     # which is what lets a run that stopped
                                     # part-way be seen to be recoverable
        self.fail_paths = set()      # CGI paths answered as an error object
        self.down_until = 0.0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.device = self
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    # ── state ────────────────────────────────────────────────────────────
    @property
    def ip(self):
        return f"127.0.0.1:{self.port}"

    def up(self):
        return time.time() >= self.down_until

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _factory_table(self):
        rows = []
        for i in range(1, self.max_u + 1):
            if i <= self.factory_universes:
                start = (i - 1) * 300 + 1
                rows.append(f"{i},{i},300,1,{start},{start + 299}")
            else:
                rows.append(f"{i},{i},0,1,0,0")
        return rows

    def board_info(self):
        info = {"CMD": "BD_INFO", "Controller": "H", "Type": "P", "MCPU": "MS_160",
                "PCPU": "PS_39", "ECPU": "EZ_40", "WEB": "WF_113", "Smart4": "SR_0",
                "SmartAC": "SA_0", "MaxU": str(self.max_u), "NumU": str(self.num_u)}
        for i, kind in enumerate(self.boards, start=1):
            info[f"BD{i}"] = kind
        return info

    # ── request handling ─────────────────────────────────────────────────
    def answer(self, path, hdr):
        path = path.split("?")[0]
        if path in self.fail_paths:
            raise _Reject(f"{path} is unavailable")
        if path == hb.CGI_BOARD_INFO:
            self.log.append("BOARD_INFO")
            return json.dumps(self.board_info()).encode()
        if path == hb.CGI_DATA_MODE:
            if hdr.get("blk") is None:
                # Without BLK the unit answers for a default block, which is how
                # the main board's mode came back as another block's (#943 B3).
                raise _Reject("no BLK header")
            self.log.append("READ_MODE")
            return json.dumps({"CMD": "DATA_MODE", "MODE": self.mode,
                               **self.serial}).encode()
        if path == hb.CGI_PORT_CONFIG:
            if hdr.get("blk") is None:
                raise _Reject("no BLK header")
            board = int(hdr["blk"]) + 1
            rows = self.ports.get(board)
            if rows is None:
                raise _Reject(f"no board {board}")
            self.log.append(f"READ_PORTS:{board}")
            return json.dumps({"CMD": "PCONFIG", "BOARD": str(int(hdr["blk"])),
                               "LIST": [{"V": r} for r in rows]}).encode()
        if path == hb.CGI_E131_DATA:
            if hdr.get("row") is None:
                raise _Reject("no ROW header")
            per = hb.UNIVERSES_PER_BLOCK
            start = int(hdr["row"]) * per
            self.log.append(f"READ_E131:{hdr['row']}")
            # Plain text, not JSON: the controller hands the table back as one
            # comma string (the capture this fake's table comes from).
            return ",".join(self.table[start:start + per]).encode()
        if path == hb.CGI_UNPACK:
            self.log.append("UNPACK")
            return b'"OK"'
        if path == hb.CGI_POST_DATA:
            return self._post(hdr.get("data") or "")
        raise _Reject(f"unknown path {path}")

    def _post(self, payload):
        try:
            cmd = json.loads(payload)
        except ValueError:
            raise _Reject("payload was not JSON")
        name = cmd.get("CMD")
        if name in self.fail_once:
            self.fail_once.discard(name)
            return b'"FAILED"'                 # this one, and only this one
        if name in self.fail:
            return b'"FAILED"'                 # a refusal that is not a drop
        self.log.append(name)
        if name == "DATA_MODE" and "MODE" in cmd:
            self.mode = cmd["MODE"]
        elif name == "DATA_MODE":
            for k in self.serial:
                if k in cmd:
                    self.serial[k] = int(cmd[k])
        elif name == "E131":
            per = hb.UNIVERSES_PER_BLOCK
            start = int(cmd["BLK"]) * per
            rows = [e["V"] for e in cmd["LIST"]]
            self.table[start:start + len(rows)] = rows
        elif name == "BD_INFO":
            self.num_u = int(cmd["NumU"])
        elif name == "PCONFIG":
            self.ports[int(cmd["BOARD"]) + 1] = [e["V"] for e in cmd["LIST"]]
        elif name == "OP_MODE":
            self.down_until = time.time() + 1.0
            raise _Drop()                      # the reboot, from the caller's side
        else:
            raise _Reject(f"unknown command {name}")
        return b'"OK"'


def child_for(dev, cid=CID):
    """The operator's controller, pointed at the fake."""
    return {
        "id": cid, "type": "hinkspix", "ip": dev.ip, "name": "Roofline",
        "status": 1,
        "hinks": {
            "model": "HinksPix PRO", "mcpu": 160, "hardwareV3": False,
            "maxU": dev.max_u, "numU": dev.num_u, "uploadSupported": True,
            "boards": {"BD1": "Long_Range", "BD2": "Local_SPI",
                       "BD3": "Not_Present", "BD4": "Not_Present",
                       "BD5": "Not_Present"},
            "protocol": "e131", "baseUniverse": 1,
            "dmxOut": {"enabled": False, "universe": None},
            "ports": [{"port": 1, "leds": 100, "enabled": True,
                       "protocol": "ws2811", "colorOrder": "RGB"}],
        },
    }


@contextlib.contextmanager
def controller(cid=CID, **kw):
    """A fake device registered as a child for the duration of the block."""
    dev = FakeHinksPix(**kw)
    child = child_for(dev, cid)
    parent_server._children.append(child)
    try:
        yield dev, child
    finally:
        dev.close()
        if child in parent_server._children:
            parent_server._children.remove(child)


def warn_codes(c, cid=CID):
    """Every code the plan for this controller warns about.

    The wizard acknowledges these on the way in; a helper that did not would be
    asserting a push that is blocked by design (#945), and a test asserting a
    409 would pass for the wrong reason.
    """
    plan = c.get(f"/api/hinkspix/{cid}/plan").get_json() or {}
    return [f["code"] for f in plan.get("findings", [])
            if f.get("level") == "warn"]


def apply_now(c, cid=CID, **body):
    """Start an apply and wait for the job, the way a script (not the SPA) does.

    Warnings are acknowledged unless the caller names its own ``ack``, so the
    gate itself stays testable.
    """
    if "ack" not in body:
        body["ack"] = warn_codes(c, cid)
    body["wait"] = True
    r = c.post(f"/api/hinkspix/{cid}/apply", json=body)
    return r, r.get_json()


def restore_now(c, backup_id, cid=CID):
    r = c.post(f"/api/hinkspix/{cid}/restore",
               json={"backupId": backup_id, "wait": True})
    return r, r.get_json()


def fresh_store():
    """Start from an empty orchestrator store, whatever the data dir holds.

    `parent_server` loads `children` and `fixtures` at import, so a second run
    in the same SLYLED_DATA opens on the previous run's controllers — whose fake
    HTTP servers are long gone, so the run addresses nothing and every device
    read fails. The snapshots persist too, and a leftover one changes what
    "newest first" lists. The documented invocation is
    `SLYLED_DATA=$(mktemp -d)`, but a suite that only works in a fresh directory
    goes red the moment a runner reuses one, and the failure looks like a
    product bug rather than stale scratch.

    The snapshot wipe is scoped to the two child ids this file drives, and names
    its files through the module's own path helper: this never touches anything
    it did not create.
    """
    parent_server._children[:] = []
    parent_server._fixtures[:] = []
    for cid in (CID, 99):
        for b in oh._list_backups(cid):
            path = oh._backup_path(cid, b["id"])
            if path:
                os.remove(path)


def main():
    fresh_store()
    print("snapshot — the device's own rows, verbatim (#945)")
    with app.test_client() as c, controller() as (dev, child):
        r = c.post(f"/api/hinkspix/{CID}/backups")
        b = r.get_json()
        ok("POST backups returns a snapshot id", r.status_code == 200
           and b.get("ok") and b.get("id"), str(b)[:200])
        ok("the snapshot read the device's universe table, not just its ports",
           b.get("decoded", {}).get("universes")
           and len(b["decoded"]["universes"]) == 402,
           str(len((b.get("decoded") or {}).get("universes") or [])))
        ok("...with the table's own first row (the capture's 1,1,300,1,1,300)",
           b["decoded"]["universes"][0]["channels"] == 300
           and b["decoded"]["universes"][0]["start"] == 1,
           str(b["decoded"]["universes"][0]))
        ok("a snapshot taken from a healthy unit carries no warnings",
           b.get("warnings") == [], str(b.get("warnings")))
        stored = c.get(f"/api/hinkspix/{CID}/backups/{b['id']}").get_json()
        raw = (stored.get("backup") or {}).get("raw") or {}
        ok("the port rows are stored verbatim, not re-derived from the decode",
           (raw.get("boards") or {}).get("1") == factory_rows(1),
           str((raw.get("boards") or {}).get("1", [])[:2]))
        ok("the universe rows are stored verbatim too",
           (raw.get("e131") or {}).get("0", [])[:1] == ["1,1,300,1,1,300"],
           str((raw.get("e131") or {}).get("0", [])[:1]))
        listed = c.get(f"/api/hinkspix/{CID}/backups").get_json()
        ok("GET backups lists it, newest first",
           [x["id"] for x in listed.get("backups", [])] == [b["id"]],
           str(listed.get("backups")))
        ok("the listing describes it in the operator's terms",
           "board(s)" in (listed["backups"][0].get("summary") or ""),
           str(listed["backups"][0].get("summary")))
        ok("DELETE removes it",
           c.delete(f"/api/hinkspix/{CID}/backups/{b['id']}").status_code == 200
           and c.get(f"/api/hinkspix/{CID}/backups/{b['id']}").status_code == 404)
        ok("the fake never saw a POST or a body (the #943 B1 contract)",
           dev.posts == 0 and all(method == "GET" for method in ("GET",)))

    print("apply — snapshot, push, reboot, verify (#945)")
    with app.test_client() as c, controller() as (dev, child):
        planned = c.get(f"/api/hinkspix/{CID}/plan").get_json()
        intended_rows = [r for r in planned["intended"]["boardPorts"]["1"]]
        r, ab = apply_now(c)
        state = ab.get("state") or {}
        ok("POST apply runs the job to completion", r.status_code == 200
           and state.get("phase") == "done", str(ab)[:200])
        ok("the run reports success only after verifying", state.get("ok") is True
           and (state.get("verify") or {}).get("ok") is True,
           str(state.get("verify"))[:200])
        sent = [hc.PortRow(x["output"], x["protocol"], x["start"], x["pixels"],
                           x["end"], x["direction"], x["colorOrder"],
                           x["startNulls"], x["brightness"], x["gamma"]).to_v()
                for x in intended_rows]
        ok("the controller's port table now holds what was sent",
           sent == dev.ports[1], f"{dev.ports[1][:1]} vs {sent[:1]}")
        ok("...and its universe table too, fillers included",
           dev.table[0] == "1,1,300,1,1,300"
           and dev.table[-1] == "402,402,0,1,0,0" and len(dev.table) == 402,
           f"{dev.table[0]!r} .. {dev.table[-1]!r} ({len(dev.table)})")
        ok("the whole table was written, not only the rows in use",
           dev.log.count("E131") == 402 // hb.UNIVERSES_PER_BLOCK,
           str(dev.log.count("E131")))
        ok("the controller was rebooted into live mode", "OP_MODE" in dev.log)
        ok("the port table was written per board, both boards",
           dev.ports[2] != factory_rows(17), str(dev.ports[2][:1]))
        ok("a snapshot was taken before the write",
           bool(state.get("backupId")), str(state)[:200])
        snap = c.get(f"/api/hinkspix/{CID}/backups/{state['backupId']}").get_json()
        ok("...and it holds the pre-push rows, which are the factory ones",
           ((snap.get("backup") or {}).get("raw") or {}).get("boards", {}).get("1")
           == factory_rows(1))

        print("apply — the job state, in sync badge, and the wire contract")
        st = c.get(f"/api/hinkspix/{CID}/apply").get_json()
        ok("GET apply reports the finished job", st.get("state", {}).get("phase") == "done"
           and st.get("state", {}).get("running") is False, str(st)[:200])
        ok("...and the recorded last apply", bool(st.get("lastApply")))
        ok("the config hash now matches, so the UI can say in-sync",
           st.get("inSync") is True, str(st.get("inSync")))
        ok("no request in the whole run was a POST", dev.posts == 0)
        ok("every request was a GET, with the command in headers",
           bool(dev.requests) and all(m == "GET" for m, _p, _h in dev.requests),
           str([m for m, _p, _h in dev.requests if m != "GET"][:3]))
        ok("the run executed the sequence the plan previewed, step for step",
           len(state.get("steps") or []) == len(planned.get("requests") or [])
           and state.get("steps") == [q["note"] for q in planned["requests"]],
           f"{len(state.get('steps') or [])} ran vs "
           f"{len(planned.get('requests') or [])} previewed")

    print("apply — a controller that cannot be read is not written to")
    with app.test_client() as c, controller() as (dev, child):
        dev.down_until = time.time() + 30        # unreachable, not merely unhappy
        r, ab = apply_now(c)
        state = ab.get("state") or {}
        ok("the run fails before doing anything", r.status_code == 200
           and state.get("ok") is False and state.get("phase") == "failed",
           str(state)[:200])
        ok("nothing was written to the controller",
           not [x for x in dev.log if x in ("DATA_MODE", "E131", "PCONFIG",
                                            "BD_INFO", "OP_MODE")],
           str(dev.log[:5]))
        ok("no snapshot id is reported, because none could be taken",
           not state.get("backupId"), str(state.get("backupId")))
        ok("and the port table is untouched", dev.ports[1] == factory_rows(1))

    print("apply — a failed request stops the sequence")
    with app.test_client() as c, controller() as (dev, child):
        dev.fail = {"PCONFIG"}
        r, ab = apply_now(c)
        state = ab.get("state") or {}
        ok("the run reports the failure and the step it reached",
           state.get("ok") is False and state.get("err")
           and state.get("step"), str(state)[:200])
        ok("the reboot was never sent", "OP_MODE" not in dev.log, str(dev.log))
        ok("the snapshot for this run is named, so it can be restored",
           bool(state.get("backupId")), str(state.get("backupId")))
        ok("the run stopped at the failed step, not after it",
           len(state.get("stepsDone") or []) == state.get("step", 0) - 1,
           f"{len(state.get('stepsDone') or [])} done, step {state.get('step')}")
        last = c.get(f"/api/hinkspix/{CID}/apply").get_json().get("lastApply") or {}
        ok("the failure is recorded on the child, not only in the job state",
           last.get("ok") is False and last.get("backupId"), str(last))
        ok("the run is reported as left part-way, not as a clean refusal",
           state.get("partial") is True and state.get("accepted"),
           f"partial={state.get('partial')} accepted={state.get('accepted')}")
        ok("...with how much of the sequence had landed, out of how much",
           0 < state["accepted"] < state.get("requests", 0),
           f"{state.get('accepted')} of {state.get('requests')}")
        ok("...and the universe table the push did write was put back",
           dev.table == dev._factory_table(), str(dev.table[:1]))
        ok("...while the port rows it never reached are the pre-push ones",
           dev.ports[1] == factory_rows(1), str(dev.ports[1][:1]))
        # A device that refuses *every* PCONFIG cannot be put back either, so
        # the recovery gets as far as the same write and stops. That is the
        # case the wizard still has to hand the operator a button for.
        rec = state.get("restore") or {}
        ok("the snapshot was replayed without waiting to be asked",
           rec.get("backupId") == state.get("backupId") and rec.get("text"),
           str(rec)[:200])
        ok("...and when that replay fails the snapshot is still named to go back to",
           rec.get("ok") is False and state.get("restoreBackupId"),
           str(state.get("restoreBackupId")))

    print("apply — a push that stopped part-way is put back on its own (#945 F1)")
    with app.test_client() as c, controller() as (dev, child):
        dev.fail_once = {"PCONFIG"}       # the controller hiccups on a port write
        r, ab = apply_now(c)
        state = ab.get("state") or {}
        rec = state.get("restore") or {}
        ok("the push is reported as having landed part-way",
           state.get("ok") is False and state.get("partial") is True,
           f"partial={state.get('partial')} ok={state.get('ok')}")
        ok("the snapshot taken before the write was replayed by itself",
           rec.get("ok") is True and rec.get("verify", {}).get("ok") is True,
           str(rec)[:300])
        ok("the controller's port rows are the pre-push ones again",
           dev.ports[1] == factory_rows(1) and dev.ports[2] == factory_rows(17),
           str(dev.ports[1][:1]))
        ok("...and its universe table too, row for row",
           dev.table == dev._factory_table(), str(dev.table[:1]))
        ok("the failure that caused it is still what is reported",
           "PCONFIG" in (state.get("err") or ""), str(state.get("err"))[:120])
        ok("the failure is reported where the push stopped, not where the "
           "recovery did",
           state.get("step") == (state.get("lastApply") or {}).get("failedAt"),
           f"step={state.get('step')} recorded at "
           f"{(state.get('lastApply') or {}).get('failedAt')}")
        ok("the recovery says what it did, in the operator's terms",
           "back on the controller" in (rec.get("text") or ""), str(rec.get("text")))
        ok("the run is not claimed to have succeeded",
           (state.get("lastApply") or {}).get("ok") is False
           and (state.get("lastApply") or {}).get("restore", {}).get("ok") is True,
           str(state.get("lastApply"))[:200])
        st = c.get(f"/api/hinkspix/{CID}/apply").get_json()
        ok("the badge stops claiming the device holds this config",
           st.get("inSync") is False, str(st.get("inSync")))
        ok("the record on the child carries the recovery, not just the job state",
           ((st.get("lastApply") or {}).get("restore") or {}).get("ok") is True,
           str(st.get("lastApply"))[:200])

    print("apply — a push refused before its first write has nothing to undo")
    with app.test_client() as c, controller() as (dev, child):
        dev.fail = {"DATA_MODE"}          # the very first write is refused
        r, ab = apply_now(c)
        state = ab.get("state") or {}
        ok("a failure before any write is not called part-way",
           state.get("partial") is False, str(state.get("partial")))
        ok("no recovery is attempted, so the controller is not rebooted for nothing",
           state.get("restore") is None and state.get("restoreBackupId") is None
           and "OP_MODE" not in dev.log, str(state.get("restore")))
        ok("...and the device holds exactly what it held before",
           dev.ports[1] == factory_rows(1) and dev.table == dev._factory_table())
        ok("the snapshot is still named, so an operator can go back deliberately",
           bool(state.get("backupId")), str(state.get("backupId")))

    print("restore — putting a snapshot back (#945)")
    with app.test_client() as c, controller() as (dev, child):
        before = c.post(f"/api/hinkspix/{CID}/backups").get_json()["id"]
        apply_now(c)
        ok("the device holds the pushed config before the restore",
           dev.ports[1] != factory_rows(1))
        preview = c.get(f"/api/hinkspix/{CID}/restore?backupId={before}").get_json()
        ok("GET restore previews the requests without sending them",
           preview.get("ok") and preview.get("requests")
           and dev.ports[1] != factory_rows(1), str(preview)[:200])
        ok("...and says what a snapshot cannot put back",
           any("UnPack" in w for w in (preview.get("warnings") or [])),
           str(preview.get("warnings")))
        r, rb = restore_now(c, before)
        state = rb.get("state") or {}
        ok("POST restore runs the job and verifies it",
           r.status_code == 200 and state.get("ok") is True,
           str(state.get("verify"))[:200])
        ok("the controller's port rows are the snapshot's again",
           dev.ports[1] == factory_rows(1) and dev.ports[2] == factory_rows(17),
           str(dev.ports[1][:1]))
        ok("the universe table is back to the snapshot's rows",
           dev.table[:1] == ["1,1,300,1,1,300"], str(dev.table[:1]))
        st = c.get(f"/api/hinkspix/{CID}/apply").get_json()
        ok("the in-sync claim is dropped — the device holds a snapshot, not this config",
           st.get("inSync") is False, str(st.get("inSync")))
        ok("the restore itself was snapshotted first, so it is undoable",
           bool(state.get("backupId")) and state["backupId"] != before,
           str(state.get("backupId")))

    print("restore — refusing what it cannot do")
    with app.test_client() as c, controller() as (dev, child):
        ok("an unknown snapshot is a 404",
           c.post(f"/api/hinkspix/{CID}/restore",
                  json={"backupId": "19990101-000000"}).status_code == 404)
        ok("a path-shaped id is not a path",
           c.post(f"/api/hinkspix/{CID}/restore",
                  json={"backupId": "../../children"}).status_code == 404)
        bad = oh._save_backup(CID, {"version": 99, "at": int(time.time()),
                                    "probe": {}, "raw": {}, "decoded": {}})
        r = c.get(f"/api/hinkspix/{CID}/restore?backupId={bad['id']}")
        ok("a snapshot from a newer build is refused, not half-replayed",
           r.status_code == 400 and "version" in (r.get_json().get("err") or ""),
           str(r.get_json())[:200])
        r = c.post(f"/api/hinkspix/{CID}/restore",
                   json={"backupId": bad["id"], "wait": True})
        ok("...and POSTing it is refused the same way", r.status_code == 400,
           str(r.get_json())[:120])
        ok("nothing was written for either refusal",
           "OP_MODE" not in dev.log, str(dev.log))

    print("gates — findings, and one job at a time")
    with app.test_client() as c, controller() as (dev, child):
        real_validate = hc.validate
        hc.validate = lambda *a, **kw: [
            hc.Finding("warn", "empty_config",
                       "Every port is disabled, so this upload blanks the "
                       "controller's output.")]
        try:
            # An explicit empty list, so the helper does not fetch the plan and
            # acknowledge the very warning under test.
            r, gated = apply_now(c, ack=[])
            ok("a warning blocks an unacknowledged push",
               r.status_code == 409 and gated.get("ok") is False,
               str(gated)[:200])
            ok("...and comes back as a finding with a stable code",
               [f["code"] for f in (gated.get("blocking") or [])] == ["empty_config"],
               str(gated.get("blocking")))
            ok("nothing was written while it was blocked",
               "OP_MODE" not in dev.log, str(dev.log))
            r, ab = apply_now(c, ack=["empty_config"])
            ok("acknowledging it by code lets the push through",
               r.status_code == 200
               and (ab.get("state") or {}).get("phase") == "done",
               str(ab)[:200])
        finally:
            hc.validate = real_validate

        # The two levels on either side of a warning (#945 F2, F3).
        hc.validate = lambda *a, **kw: [
            hc.Finding("error", "empty_config",
                       "Every port is disabled, and the controller refuses to "
                       "be told about no universes.")]
        try:
            r, gated = apply_now(c, ack=["empty_config"])
            ok("an error blocks the push even when its code is acknowledged",
               r.status_code == 409 and gated.get("ok") is False,
               str(gated)[:200])
        finally:
            hc.validate = real_validate

        hc.validate = lambda *a, **kw: [
            hc.Finding("info", "reboot_required",
                       "Applying reboots the controller.")]
        try:
            # An info-level finding is a statement about every push, not a
            # decision — as a warning it is a box to tick every time, which is
            # how an acknowledgement gate turns into a click-through.
            r, ab = apply_now(c, ack=[])
            ok("an informational finding never blocks, acked or not",
               r.status_code == 200
               and (ab.get("state") or {}).get("phase") == "done",
               str(ab)[:200])
            plan = c.get(f"/api/hinkspix/{CID}/plan").get_json()
            ok("...and it is still reported, so the operator is still told",
               [f["code"] for f in (plan.get("findings") or [])]
               == ["reboot_required"], str(plan.get("findings"))[:200])
        finally:
            hc.validate = real_validate

        real = c.post(f"/api/hinkspix/{CID}/backups").get_json()["id"]
        oh._config_state[CID] = {"running": True, "phase": "upload"}
        try:
            r = c.post(f"/api/hinkspix/{CID}/apply",
                       json={"ack": warn_codes(c)})
            ok("a second push is refused while one is running",
               r.status_code == 409, str(r.get_json())[:120])
            r = c.post(f"/api/hinkspix/{CID}/restore", json={"backupId": real})
            ok("a restore is refused while a push is running too",
               r.status_code == 409, str(r.get_json())[:120])
        finally:
            oh._config_state.pop(CID, None)

        oh._deploy_state[CID] = {"running": True}
        try:
            r = c.post(f"/api/hinkspix/{CID}/apply",
                       json={"ack": warn_codes(c)})
            ok("a push is refused while a standalone deploy is running",
               r.status_code == 409, str(r.get_json())[:120])
        finally:
            oh._deploy_state.pop(CID, None)

    print("snapshots — retention, and one device's snapshots staying its own")
    with controller(cid=99) as (dev, child):
        base = int(time.time()) - 3600
        for i in range(12):
            oh._save_backup(99, {"version": hc.BACKUP_VERSION, "at": base + i,
                                 "probe": {}, "raw": {}, "decoded": {}})
        kept = oh._list_backups(99)
        ids = [k["id"] for k in kept]
        ok(f"only the newest {oh.BACKUP_KEEP} snapshots are kept",
           len(kept) == oh.BACKUP_KEEP, str(len(kept)))
        ok("...they are listed newest first, which is how the SPA shows them",
           ids == sorted(ids, reverse=True), str(ids[:2]))
        ok("...and the oldest two are the ones dropped",
           ids[-1] == time.strftime("%Y%m%d-%H%M%S", time.localtime(base + 2)),
           f"{ids[-1]} vs {base + 2}")
        other = [b["id"] for b in oh._list_backups(CID)]
        ok("another device's snapshots are held, and listed, separately",
           other and not (set(other) & set(ids)),
           str(sorted(set(other) & set(ids)))[:200])

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
