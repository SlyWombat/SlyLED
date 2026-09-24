#!/usr/bin/env python3
"""HinksPix xLights show-folder import — the routes (#947).

Two routes turn an xLights show folder into a SlyLED config:

    POST /api/hinkspix/import/xlights           read the folder or an upload,
                                                propose, store nothing
    POST /api/hinkspix/<cid>/import/xlights/accept
                                                write the port table and,
                                                optionally, a fixture per model

The load-bearing property is that the accept goes through the *same*
`_apply_config_body` the editor's own save uses. An import is a shortcut for
typing the port table, not a second way to write one, so a proposal the editor
would refuse is refused here too — and a refusal leaves the controller's stored
config exactly as it was, as does a failure halfway through the fixtures.

Nothing in this suite contacts a controller. The preview reads two files off
disk and the accept writes to the orchestrator's own store; the wire is patched
shut for the whole run so that stays a fact rather than a coincidence.

The real show folder is used verbatim (`tests/fixtures/xlights_home_eves/`) —
it is the operator's own Home Eves show, and every number asserted below was
read out of those bytes, not invented for a test.

Run: SLYLED_DATA=$(mktemp -d) python3 tests/test_hinkspix_xlights_routes.py
"""

import copy
import io
import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hx947-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from parent_server import app  # noqa: E402
import hinkspix_bridge as hb  # noqa: E402
import hinkspix_config as hc  # noqa: E402
import hinkspix_tcp as htcp  # noqa: E402
import orch_hinkspix as oh  # noqa: E402

_passed = 0
_failed = 0

SHOW = os.path.join(os.path.dirname(__file__), "fixtures", "xlights_home_eves")


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def section(title):
    print(title)


def make_child(cid=7, base=100, ports=None, ip="192.168.10.6"):
    """The operator's real unit: PRO V1/V2, MS_160, BD1+BD2 fitted."""
    return {
        "id": cid, "type": "hinkspix", "boardType": "HinksPix PRO",
        "ip": ip, "name": "Roofline", "status": 1, "seen": 0,
        "sc": 0, "strings": [],
        "hinks": {
            "model": "HinksPix PRO", "hardwareV3": False, "mcpu": 160,
            "maxU": 402, "uploadSupported": True,
            "boards": {"BD1": "Long_Range", "BD2": "Local_SPI",
                       "BD3": "Not_Present"},
            "protocol": "e131", "baseUniverse": base,
            "dmxOut": {"enabled": False, "universe": None},
            "ports": ports if ports is not None else [
                {"port": 1, "leds": 100, "enabled": True},
                {"port": 2, "leds": 300, "enabled": True},
            ],
            "configPushedAt": 0, "configHash": "",
        },
    }


def fresh_child(cid=7, **kw):
    """Install `make_child()` as the only child and return it."""
    child = make_child(cid, **kw)
    parent_server._children[:] = [child]
    parent_server._fixtures[:] = []
    parent_server._nxt_fix = 100
    return child


def wire_shut():
    """Make every controller transport raise, and count the attempts.

    The routes under test have no business on the wire: the preview reads the
    show folder, the accept writes the orchestrator's store. Patching the
    transport rather than inspecting the code means a future edit that reaches
    for the device fails a test instead of quietly probing the operator's unit.
    """
    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise AssertionError("the import path contacted the controller")

    saved = (hb.read_e131_text, htcp.HinksPixTcp)
    hb.read_e131_text = _boom
    htcp.HinksPixTcp = _boom
    return calls, saved


def _show_dir(extra=None):
    """A temp show folder, optionally with files removed/added."""
    d = tempfile.mkdtemp(prefix="hx947-show-")
    for name in ("xlights_networks.xml", "xlights_rgbeffects.xml"):
        if extra and extra.get("drop") == name:
            continue
        with open(os.path.join(SHOW, name), "r", encoding="utf-8") as fh:
            text = fh.read()
        if extra and extra.get("replace") == name:
            text = extra["text"]
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    return d


def port_rows(resp_hinks):
    return {int(p["port"]): p for p in (resp_hinks.get("ports") or [])}


def main():
    calls, saved = wire_shut()
    c = app.test_client()

    # ── Path spellings ───────────────────────────────────────────────────────
    section("Path spellings — the operator's path, in this machine's dialect")
    ok("a Windows path is also tried as its WSL mount",
       oh._win_to_wsl("C:\\Users\\Sly\\OneDrive\\Show") == "/mnt/c/Users/Sly/OneDrive/Show")
    ok("backslash or slash both work in a Windows path",
       oh._win_to_wsl("D:/Projects/Show") == "/mnt/d/Projects/Show")
    ok("a WSL path is also tried as its Windows spelling",
       oh._wsl_to_win("/mnt/d/Projects/Show") == "D:\\Projects\\Show")
    ok("a path that is neither shape rewrites to nothing",
       oh._win_to_wsl("/home/sly/show") is None
       and oh._wsl_to_win("C:\\x") is None)
    ok("candidates are tried in order, the literal spelling first",
       oh._show_folder_candidates("C:\\a\\b")
       == ["C:\\a\\b", "/mnt/c/a/b"])
    ok("a path to one of the files is taken as the folder holding it",
       oh._show_folder_candidates("C:\\a\\b\\xlights_networks.xml")
       == ["C:\\a\\b", "/mnt/c/a/b"],
       oh._show_folder_candidates("C:\\a\\b\\xlights_networks.xml"))
    ok("a POSIX path to one of the files likewise",
       oh._show_folder_candidates("/a/b/xlights_rgbeffects.xml") == ["/a/b"])
    ok("a bare filename is not turned into the working directory",
       oh._show_folder_candidates("xlights_networks.xml")
       == ["xlights_networks.xml"])
    ok("quotes around a pasted path are dropped",
       oh._show_folder_candidates('  "C:\\a\\b"  ') == ["C:\\a\\b", "/mnt/c/a/b"])
    ok("an empty path has no candidates",
       oh._show_folder_candidates("") == [] and oh._show_folder_candidates(None) == [])

    # ── Preview, real show folder ────────────────────────────────────────────
    section("Preview — the operator's Home Eves show folder")
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": SHOW})
    ok("the real show folder reads", r.status_code == 200, r.get_json())
    p = r.get_json()
    ok("it reports the folder it read", p.get("source") == SHOW)
    ok("it reports the controller it found",
       p.get("controllers") == ["Ethernet_"]
       and p["controller"]["name"] == "Ethernet_")
    ok("the controller keeps xLights' stale address, not the unit's",
       p["controller"]["ip"] == "192.168.2.10")
    ok("the universe block is the one the file declares",
       p["universes"] == {"base": 1, "count": 2, "channels": 510})
    ok("one model row, accepted", len(p["models"]) == 1
       and p["models"][0]["name"] == "Single Line"
       and p["models"][0]["accepted"] is True)
    ok("the row resolves its start channel to universe 1 channel 1",
       (p["models"][0]["universe"], p["models"][0]["channel"]) == (1, 1))
    ok("one proposed port, on the model's output",
       [(x["port"], x["leds"]) for x in p["hinks"]["ports"]]
       == [(17, 200)], p["hinks"]["ports"])
    ok("the port is proposed as the pixel protocol the file names",
       p["hinks"]["ports"][0]["protocol"] == "ws2811")
    ok("the port carries stage-mm geometry, not a channel count",
       p["hinks"]["ports"][0]["mm"] == 3334)
    ok("a clean file proposes without warnings", p["warnings"] == [],
       p["warnings"])
    ok("an untargeted preview says which limits it was checked against",
       any("no target controller" in n for n in p["notes"]), p["notes"])
    ok("there is no diff without a target controller", p.get("diff") is None)

    # ── Preview, path refusals ───────────────────────────────────────────────
    section("Preview — refusing what it cannot read")
    r = c.post("/api/hinkspix/import/xlights", json={})
    ok("no showFolder at all is a 400",
       r.status_code == 400 and "showFolder" in r.get_json()["err"])
    r = c.post("/api/hinkspix/import/xlights",
               json={"showFolder": "/nope/definitely/not/here"})
    ok("a folder that does not exist says so",
       r.status_code == 400 and "no such folder" in r.get_json()["err"])
    r = c.post("/api/hinkspix/import/xlights",
               json={"showFolder": os.path.join(SHOW, "xlights_networks.xml")})
    ok("a path to one of the files is accepted as the folder",
       r.status_code == 200 and r.get_json()["hinks"]["ports"][0]["port"] == 17)
    half = _show_dir({"drop": "xlights_rgbeffects.xml"})
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": half})
    ok("half a show folder names the file that is missing",
       r.status_code == 400
       and "xlights_rgbeffects.xml" in r.get_json()["err"], r.get_json())
    bad = _show_dir({"replace": "xlights_networks.xml", "text": "not xml at all"})
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": bad})
    ok("a file that is not XML is a 400, not a traceback",
       r.status_code == 400 and "XML" in r.get_json()["err"], r.get_json())
    empty = _show_dir({"replace": "xlights_networks.xml",
                       "text": "<Networks></Networks>"})
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": empty})
    ok("a file with no controllers is a 400 naming what it expected",
       r.status_code == 400
       and "no <Controller> found" in r.get_json()["err"], r.get_json())
    ok("and the controller list is empty rather than absent",
       r.get_json()["controllers"] == [])
    r = c.post("/api/hinkspix/import/xlights",
               json={"showFolder": "xlights_networks.xml"})
    ok("a bare filename is refused, not read from the orchestrator's cwd",
       r.status_code == 400 and "no such folder" in r.get_json()["err"],
       r.get_json())

    # ── Preview, upload ──────────────────────────────────────────────────────
    section("Preview — the two files posted instead of a folder")
    with open(os.path.join(SHOW, "xlights_networks.xml"), "rb") as fh:
        net = fh.read()
    with open(os.path.join(SHOW, "xlights_rgbeffects.xml"), "rb") as fh:
        rgb = fh.read()
    r = c.post("/api/hinkspix/import/xlights",
               data={"networks": (io.BytesIO(net), "xlights_networks.xml"),
                     "rgbeffects": (io.BytesIO(rgb), "xlights_rgbeffects.xml")},
               content_type="multipart/form-data")
    ok("an upload proposes what the folder does",
       r.status_code == 200
       and r.get_json()["hinks"]["ports"][0]["port"] == 17,
       r.get_json())
    ok("an upload says where it read from",
       r.get_json()["source"] == "(uploaded)")
    r = c.post("/api/hinkspix/import/xlights",
               data={"files": (io.BytesIO(net), "xlights_networks.xml")},
               content_type="multipart/form-data")
    ok("half an upload names the missing file",
       r.status_code == 400 and "rgbeffects" in r.get_json()["err"],
       r.get_json())

    # ── Preview, controller choice ───────────────────────────────────────────
    section("Preview — a show with more than one controller")
    two = _show_dir({"replace": "xlights_networks.xml", "text":
                     "<Networks>"
                     "<Controller Id='1' Name='Garage' Vendor='HinksPix' "
                     "Model='PRO V1/V2' IP='192.168.10.6' "
                     "Protocol='E131' FullxLightsControl='TRUE'>"
                     "<network ComPort='192.168.10.6' BaudRate='1' "
                     "NetworkType='E131' MaxChannels='510'/></Controller>"
                     "<Controller Id='2' Name='Eaves' Vendor='HinksPix' "
                     "Model='PRO V1/V2' IP='192.168.10.7' "
                     "Protocol='E131' FullxLightsControl='TRUE'>"
                     "<network ComPort='192.168.10.7' BaudRate='201' "
                     "NetworkType='E131' MaxChannels='510'/></Controller>"
                     "</Networks>"})
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": two})
    ok("two controllers are refused rather than coin-flipped",
       r.status_code == 400 and r.get_json()["controllers"] == ["Garage", "Eaves"],
       r.get_json())
    r = c.post("/api/hinkspix/import/xlights",
               json={"showFolder": two, "controller": "Eaves"})
    ok("naming one imports it", r.status_code == 200
       and r.get_json()["controller"]["name"] == "Eaves")
    ok("and its universe block is its own",
       r.get_json()["universes"]["base"] == 201, r.get_json()["universes"])
    ok("the models belong to the other controller, so none are imported",
       r.get_json()["models"] == []
       and any("no models" in w for w in r.get_json()["warnings"]))
    r = c.post("/api/hinkspix/import/xlights",
               json={"showFolder": two, "controller": "Nope"})
    ok("an unknown controller name is a 400",
       r.status_code == 400 and "no controller named" in r.get_json()["err"])

    # ── Preview against a unit ───────────────────────────────────────────────
    section("Preview — against the controller it would be applied to")
    child0 = fresh_child()
    before_preview = copy.deepcopy(child0["hinks"])
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": SHOW, "cid": 7})
    ok("a targeted preview reads", r.status_code == 200, r.get_json())
    ok("a preview stores nothing", child0["hinks"] == before_preview)
    p = r.get_json()
    ok("the diff adds the model's output", p["diff"]["added"] == [17],
       p["diff"])
    ok("the diff keeps the outputs the folder says nothing about",
       p["diff"]["kept"] == [1, 2], p["diff"])
    ok("the diff reports nothing withdrawn", p["diff"]["withdrawn"] == [])
    ok("the diff reports the base universe moving",
       p["diff"]["settings"]["baseUniverse"] == {"from": 100, "to": 1},
       p["diff"]["settings"])
    ok("the move is called out in words too",
       any("base universe would move" in n for n in p["notes"]), p["notes"])
    ok("the stale xLights address is called out",
       any("192.168.2.10" in n and "192.168.10.6" in n for n in p["notes"]),
       p["notes"])
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": SHOW, "cid": 99})
    ok("an unknown cid is a 404", r.status_code == 404)
    r = c.post("/api/hinkspix/import/xlights",
               json={"showFolder": SHOW, "cid": "seven"})
    ok("a non-numeric cid is a 400", r.status_code == 400)

    # ── Accept ───────────────────────────────────────────────────────────────
    section("Accept — the port table and the fixtures")
    child = fresh_child()
    preview = c.post("/api/hinkspix/import/xlights",
                     json={"showFolder": SHOW, "cid": 7}).get_json()
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": preview, "createFixtures": True})
    ok("a clean proposal is accepted", r.status_code == 200, r.get_json())
    a = r.get_json()
    rows = port_rows(a["hinks"])
    ok("the model's output is written", 17 in rows and rows[17]["leds"] == 200,
       rows.get(17))
    ok("it carries the file's pixel protocol and colour order",
       rows[17]["protocol"] == "ws2811" and rows[17]["colorOrder"] == "RGB")
    ok("the outputs the folder is silent about survive the import",
       sorted(rows) == [1, 2, 17] and rows[1]["leds"] == 100
       and rows[2]["leds"] == 300, sorted(rows))
    ok("the kept outputs are reported, not silently kept",
       any("2 output(s)" in n for n in a["notes"]), a["notes"])
    ok("the base universe follows the show folder",
       a["hinks"]["baseUniverse"] == 1)
    ok("one fixture is created for the single model",
       [f["name"] for f in a["created"]] == ["Single Line"], a["created"])
    ok("nothing was skipped", a["skipped"] == [], a["skipped"])
    fix = next(f for f in parent_server._fixtures if f["name"] == "Single Line")
    ok("the fixture is bound to the model's output",
       fix["childId"] == 7 and fix["strings"] == [{"port": 17, "leds": 200,
                                                   "mm": 3334}], fix)
    ok("the fixture takes its pixel count from the port table, not the file",
       fix["strings"][0]["leds"] == rows[17]["leds"])
    ok("the accept returns the map it stored",
       a["map"]["universes"][0] == 1 and a["configHash"] == oh._config_hash(a["hinks"]))

    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": preview, "createFixtures": True})
    ok("re-accepting the same proposal is not an error", r.status_code == 200)
    ok("and does not duplicate the fixture",
       len([f for f in parent_server._fixtures
            if f["name"] == "Single Line"]) == 1)
    ok("the second accept skips the fixture it already made",
       [s["name"] for s in r.get_json()["skipped"]] == ["Single Line"]
       and "already on this controller" in r.get_json()["skipped"][0]["reason"],
       r.get_json()["skipped"])

    section("Accept — a port somebody already hung a fixture on")
    fresh_child()
    parent_server._fixtures[:] = [{"id": 41, "name": "Eaves strip",
                                   "fixtureType": "led", "type": "linear",
                                   "childId": 7,
                                   "strings": [{"port": 17, "leds": 200}]}]
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": preview, "createFixtures": True})
    ok("the config still imports", r.status_code == 200, r.get_json())
    ok("the fixture is skipped, with the reason",
       r.get_json()["created"] == []
       and "already bound" in r.get_json()["skipped"][0]["reason"],
       r.get_json()["skipped"])

    section("Accept — the rows the operator unticked")
    fresh_child()
    unticked = copy.deepcopy(preview)
    unticked["models"][0]["accepted"] = False
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": unticked, "createFixtures": True})
    ok("an unticked row is accepted as a request, not an error",
       r.status_code == 200, r.get_json())
    ok("its output is not written",
       17 not in port_rows(r.get_json()["hinks"]),
       port_rows(r.get_json()["hinks"]))
    ok("the outputs the controller already had are untouched",
       sorted(port_rows(r.get_json()["hinks"])) == [1, 2])
    ok("the unticked output is reported",
       any("unticked" in n and "17" in n for n in r.get_json()["notes"]),
       r.get_json()["notes"])
    ok("no fixture is created for an unticked row",
       r.get_json()["created"] == [] and parent_server._fixtures == [])

    section("Accept — createFixtures off means config only")
    fresh_child()
    r = c.post("/api/hinkspix/7/import/xlights/accept", json={"proposal": preview})
    ok("the config is written", r.status_code == 200
       and 17 in port_rows(r.get_json()["hinks"]))
    ok("no fixture is created", r.get_json()["created"] == []
       and parent_server._fixtures == [])

    # ── Accept, held to the PUT's own rules ──────────────────────────────────
    section("Accept — refusing what the editor's own save would refuse")
    child = fresh_child()
    before = copy.deepcopy(child["hinks"])
    bad = copy.deepcopy(preview)
    bad["hinks"]["ports"][0]["leds"] = -5
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": bad, "createFixtures": True})
    ok("a negative pixel count is refused", r.status_code == 400, r.get_json())
    bad = copy.deepcopy(preview)
    bad["hinks"]["ports"][0]["port"] = 99
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": bad, "createFixtures": True})
    ok("an output past the model's ceiling is refused",
       r.status_code == 400 and "out of range" in r.get_json()["err"],
       r.get_json())
    bad = copy.deepcopy(preview)
    bad["hinks"]["ports"].append(dict(bad["hinks"]["ports"][0]))
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": bad, "createFixtures": True})
    ok("a body naming one output twice is refused, not resolved by keeping "
       "the last",
       r.status_code == 400 and "more than once" in r.get_json()["err"],
       r.get_json())
    bad = copy.deepcopy(preview)
    bad["hinks"]["protocol"] = "telepathy"
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": bad, "createFixtures": True})
    ok("a protocol this hardware does not offer is refused",
       r.status_code == 400, r.get_json())
    bad = copy.deepcopy(preview)
    bad["hinks"]["baseUniverse"] = 0
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": bad, "createFixtures": True})
    ok("baseUniverse 0 is refused", r.status_code == 400)
    ok("after five refusals the stored config is byte-identical",
       child["hinks"] == before, child["hinks"])
    ok("and no fixture was created by a refused accept",
       parent_server._fixtures == [])

    section("Accept — bodies that are not proposals")
    r = c.post("/api/hinkspix/7/import/xlights/accept", json={})
    ok("no proposal at all is a 400",
       r.status_code == 400
       and "proposal" in r.get_json()["err"]
       and "verbatim" in r.get_json()["err"],
       r.get_json())
    r = c.post("/api/hinkspix/7/import/xlights/accept", json={"proposal": {}})
    ok("a proposal with no hinks block is a 400", r.status_code == 400)
    # The preview response is self-describing, so posting it back unwrapped is
    # a caller doing the obvious thing rather than a caller making a mistake.
    # Two nesting levels and one right answer is a trap with no purpose (#947 QA).
    fresh_child()
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json=dict(preview, createFixtures=True))
    ok("the preview response itself is an acceptable body",
       r.status_code == 200 and 17 in port_rows(r.get_json()["hinks"]),
       r.get_json())
    ok("...and it is written exactly as the wrapped form writes it",
       r.get_json()["created"] and len(parent_server._fixtures) == 1,
       str(r.get_json().get("created")))
    was = copy.deepcopy(parent_server._children[0]["hinks"])
    r = c.post("/api/hinkspix/7/import/xlights/accept",
               json={"proposal": {"hinks": {"ports": []}}})
    ok("a proposal that names no outputs is a body the port table can hold",
       r.status_code == 200, r.get_json())
    # Carried-over rows go through the same save a hand-typed edit does, so they
    # come back in the port table's own shape — same pixels, same enabled flag,
    # with the defaults the editor would have filled in made explicit. What must
    # not change is which outputs exist and what they drive.
    after = port_rows(r.get_json()["hinks"])
    ok("and every output the controller had survives it, unchanged in what it "
       "drives",
       sorted(after) == sorted(port_rows(was))
       and [(after[n]["leds"], after[n]["enabled"]) for n in sorted(after)]
       == [(port_rows(was)[n]["leds"], port_rows(was)[n].get("enabled", True))
           for n in sorted(after)],
       after)
    ok("with the port table's own defaults filled in, as a save would",
       all(after[n].get("mm") and after[n].get("protocol") for n in after))
    r = c.post("/api/hinkspix/99/import/xlights/accept", json={"proposal": preview})
    ok("an unknown controller is a 404", r.status_code == 404)

    # ── Accept, all or nothing ───────────────────────────────────────────────
    section("Accept — a failure halfway through is rolled back")
    child = fresh_child()
    before_hinks = copy.deepcopy(child["hinks"])

    def _boom(*a, **k):
        raise RuntimeError("boom")

    saved_validator = parent_server._validate_fixture_ports
    parent_server._validate_fixture_ports = _boom
    try:
        r = c.post("/api/hinkspix/7/import/xlights/accept",
                   json={"proposal": preview, "createFixtures": True})
    finally:
        parent_server._validate_fixture_ports = saved_validator
    ok("the failure is reported, not swallowed",
       r.status_code == 500 and "nothing was changed" in r.get_json()["err"],
       r.get_json())
    ok("the port table is put back", child["hinks"] == before_hinks,
       child["hinks"])
    ok("no half-created fixture survives", parent_server._fixtures == [],
       parent_server._fixtures)
    ok("the fixture counter is put back too", parent_server._nxt_fix == 100,
       parent_server._nxt_fix)
    ok("the proposed ports were not left behind either",
       {int(p["port"]) for p in child["hinks"]["ports"]} == {1, 2},
       child["hinks"]["ports"])
    mine = [x for x in parent_server._dmx_settings.get("universeRoutes") or []
            if x.get("label") == "hinkspix:7"]
    # Routes are derived from the config rather than stored beside it, so what
    # "rolled back" means for them is that they describe the config that
    # survived: base 100 with a 100px port 1 and a 300px port 2.
    ok("and the universes published are the ones the surviving config owns",
       sorted(x["universe"] for x in mine) == [100, 101, 102],
       mine)

    # ── The promise the suite is built on ────────────────────────────────────
    section("The whole run stayed off the wire")
    ok("no transport call was attempted in any test above", calls == [], calls)
    hb.read_e131_text, htcp.HinksPixTcp = saved

    print()
    print(f"{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
