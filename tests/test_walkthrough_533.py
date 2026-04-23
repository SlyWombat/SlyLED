"""
QA Walkthrough — Issue #533
Interactive stepped test. Opens a visible browser window; pauses after each
step so you can review and guide. Press Enter to advance, 'q' to quit, 's' to
skip the current step's assertion (mark as skipped rather than bug).

Usage:
  python3 tests/test_walkthrough_533.py           # interactive (pauses)
  python3 tests/test_walkthrough_533.py --auto    # headless, no pauses (CI/weekly)

Fixtures under test:
  MH1-Sly        ch 1   X:500  Y:0    Z:1690
  MH2-Sly        ch 14  X:1605 Y:0    Z:1690
  350W-Spot       ch 27  X:1605 Y:150  Z:550
  Cam1-Left-HiRes        X:830  Y:120  Z:1930
  Cam2-Right              X:1275 Y:120  Z:1930
"""

import os, sys, urllib.request, json, textwrap
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout, expect

BASE   = "http://localhost:8080"
SS_DIR = "docs/screenshots/walkthrough-533"
os.makedirs(SS_DIR, exist_ok=True)

AUTO   = "--auto" in sys.argv   # headless, no pauses
BUGS   = []
SKIPS  = []
PAGE   = None   # set in run() so pause() can call page.pause()


# ── Helpers ───────────────────────────────────────────────────────────────────

def ss(page, name):
    path = f"{SS_DIR}/{name}"
    page.screenshot(path=path)
    print(f"  📸  {path}")


def hr(title=""):
    w = 60
    if title:
        pad = (w - len(title) - 2) // 2
        print(f"\n{'─'*pad} {title} {'─'*(w-pad-len(title)-2)}")
    else:
        print(f"\n{'─'*w}")


def log(step, title):
    print(f"\n{'='*60}")
    print(f"  Step {step} — {title}")
    print(f"{'='*60}")


def ok(msg):
    print(f"  ✓ {msg}")


def warn(msg):
    print(f"  ⚠ {msg}")


def bug(step, desc):
    entry = f"[BUG] Step {step}: {desc}"
    BUGS.append(entry)
    print(f"  🐛  {entry}")


def pause(step, summary=""):
    """Pause for user review using Playwright inspector (click Resume to advance)."""
    if AUTO:
        return True
    if summary:
        print(f"\n  ── Step {step} complete: {summary}")
    print(f"  [PAUSED] Click ▶ Resume in the browser inspector to continue…")
    try:
        PAGE.pause()   # opens Playwright inspector with Resume button
    except Exception:
        pass
    return True


def go(page, tab):
    """Navigate via JS — works even when a modal is blocking."""
    page.evaluate("closeModal()")
    page.wait_for_timeout(200)
    page.evaluate(f"showTab('{tab}')")
    page.wait_for_timeout(700)


def close_modal(page):
    page.evaluate("closeModal()")
    page.wait_for_timeout(300)


def api_get(path):
    """Fetch JSON from the server (works from WSL via Playwright evaluate)."""
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def api_get_browser(page, path):
    """Fetch JSON from the server via the browser context (avoids WSL network)."""
    try:
        result = page.evaluate(f"""
            fetch('{path}').then(r => r.json()).catch(() => null)
        """)
        return result
    except Exception:
        return None


# ── Step implementations ───────────────────────────────────────────────────────

def step_1_launch(page):
    log(1, "Launch SlyLED SPA")
    page.goto(BASE)
    page.wait_for_timeout(3000)
    title = page.title()
    ok(f"Title: {title}")
    tab_ids = {"DASHBOARD":"n-dash","SETUP":"n-setup","LAYOUT":"n-layout",
               "ACTIONS":"n-actions","SHOWS":"n-shows","RUNTIME":"n-runtime",
               "SETTINGS":"n-settings","FIRMWARE":"n-firmware"}
    missing = [t for t, id_ in tab_ids.items() if page.locator(f"#{id_}").count() == 0]
    if missing:
        bug(1, f"Missing nav tabs: {missing}")
    else:
        ok("All 8 nav tabs present")
    ss(page, "01-launch.png")
    pause(1, "SPA loaded")


def step_2_new_project(page):
    log(2, "New project")
    page.locator("#file-menu-btn").click()
    page.wait_for_timeout(400)
    new_proj = page.locator("text=New Project").first
    if new_proj.count() and new_proj.is_visible():
        page.once("dialog", lambda d: d.accept())
        new_proj.click()
        page.wait_for_timeout(2000)
        ok("New project created — server state reset")
    else:
        bug(2, "New Project menu item not found in File menu")
    ss(page, "02-new-project.png")
    pause(2, "project reset")


def step_3a_discover(page):
    """Discover DMX hardware. Must run before starting the engine."""
    log("3a", "Discover DMX hardware (Setup → Discover)")
    go(page, "setup")
    page.wait_for_timeout(600)

    disc_btn = page.locator("button[onclick='discoverChildren()']")
    if not (disc_btn.count() and disc_btn.is_visible()):
        bug("3a", "Discover button not found on Setup tab")
        return False

    disc_btn.click()
    ok("Discover clicked — scanning network (this takes ~5 s)…")

    # Wait up to 45 s for the button to re-enable (discover + ArtPoll ~4 s, but
    # the XHR poll itself has a 30 s timeout so we need headroom beyond that).
    try:
        expect(disc_btn).to_be_enabled(timeout=45000)
        ok("Scan complete — button re-enabled")
    except Exception:
        warn("Discover scan did not complete within 45 s — check server logs")

    page.wait_for_timeout(800)
    ss(page, "03a-discover.png")

    # Check results
    add_btns = page.locator("button[onclick*='addDiscovered']").all()
    visible_add = [b for b in add_btns if b.is_visible()]
    if visible_add:
        ok(f"{len(visible_add)} device(s) found:")
        for btn in visible_add:
            oc = btn.get_attribute("onclick") or ""
            print(f"    → {btn.inner_text().strip()!r}  {oc[:70]}")
    else:
        disc_el = page.locator("#disc-results")
        disc_text = disc_el.inner_text() if disc_el.count() else "(element missing)"
        warn(f"No devices shown.  #disc-results says: {disc_text[:120]!r}")

    # Pause so user can see results and decide
    keep_going = pause("3a", "Discover complete — check browser for results")

    # After pause: re-read buttons (user may have clicked Add manually)
    add_btns = page.locator("button[onclick*='addDiscovered']").all()
    visible_add = [b for b in add_btns if b.is_visible()]
    dmx_bridge_added = False

    if visible_add:
        # Click Add for the DMX bridge (not Configure, not Camera)
        dmx_add = [b for b in visible_add
                   if "addDiscoveredDmxBridge" not in (b.get_attribute("onclick") or "")
                   and "addDiscoveredCamera" not in (b.get_attribute("onclick") or "")]
        if dmx_add:
            dmx_add[0].click()
            page.wait_for_timeout(1500)
            dmx_bridge_added = True
            ok("DMX bridge added as child")
        else:
            bug("3a", "No suitable Add button found — only Camera/Configure buttons visible")
    else:
        bug("3a", "No DMX bridge found via Discover — DMX will broadcast. SLYC-1152 @ 192.168.10.219 expected.")

    return dmx_bridge_added


def step_3b_dmx_engine(page, dmx_bridge_added=False):
    log("3b", "Configure DMX Engine (Settings → DMX ENGINE → Start)")
    go(page, "settings")
    page.locator("#sn-dmx").click()
    page.wait_for_timeout(800)

    # Add route
    add_route = page.locator("button[onclick='dmxAddRoute()']")
    if add_route.count() and add_route.is_visible():
        add_route.click()
        page.wait_for_timeout(400)
        ok("Route added for Universe 1")
    else:
        bug("3b", "'+ Add Route' button not visible under Settings → DMX ENGINE")

    # Point route at discovered bridge
    if dmx_bridge_added:
        dest_sel = page.locator("#dmx-routes select").first
        if dest_sel.count() and dest_sel.is_visible():
            opts = dest_sel.locator("option").all()
            non_bc = [o for o in opts if (o.get_attribute("value") or "") not in ("", "broadcast")]
            if non_bc:
                dest_sel.select_option(index=len(opts) - 1)
                ok(f"Route destination → '{dest_sel.input_value()}'")
            else:
                warn("No non-broadcast option in route selector — using broadcast")

    # Save settings
    save_btn = page.locator("button[onclick='saveDmxSettings()']")
    if save_btn.count() and save_btn.is_visible():
        save_btn.click()
        page.wait_for_timeout(400)
        ok("DMX settings saved")
    else:
        bug("3b", "Save DMX Settings button not found")

    # Start engine
    start_btn = page.locator("button[onclick='dmxEngineStart()']")
    if start_btn.count() and start_btn.is_visible():
        start_btn.click()
        page.wait_for_timeout(2000)
        status_txt = (page.locator("#dmx-status").text_content() or "").strip()
        ok(f"Engine start triggered — status: '{status_txt}'")
        if "running" not in status_txt.lower():
            bug("3b", f"Engine may not have started — status: '{status_txt}'")
    else:
        bug("3b", "DMX Engine Start button not found")

    # Blink test
    blink_btn = page.locator("button[onclick='dmxBlink()']")
    if blink_btn.count() and blink_btn.is_visible():
        blink_btn.click()
        page.wait_for_timeout(3000)
        ok("Blink fired — lights should rainbow-cycle briefly")
    ss(page, "03b-dmx-engine.png")
    pause("3b", "DMX engine running — did lights blink?")


def step_4_add_fixtures(page):
    log(4, "Add DMX fixtures")
    fixtures_to_add = [
        ("MH1-Sly",   "moving head", 1,  1,  "4a"),
        ("MH2-Sly",   "moving head", 1, 14,  "4b"),
        ("350W-Spot",  "350w",        1, 27,  "5"),
    ]
    for name, search, uni, addr, step_id in fixtures_to_add:
        log(step_id, f"Add {name} (Universe {uni}, ch {addr})")
        _add_dmx_fixture(page, name, search, uni, addr, step_id)
        ss(page, f"{step_id.replace('a','').replace('b','')}-{name.lower().replace(' ','-')}.png")
        pause(step_id, f"{name} added")


def _add_dmx_fixture(page, name, search_term, universe, address, step_id):
    go(page, "setup")
    page.evaluate("showAddFixtureModal()")
    page.wait_for_timeout(500)
    page.locator("#aft").select_option(label="DMX Fixture")
    page.wait_for_timeout(300)

    for attempt in range(2):
        page.locator("#af-ofl-q").fill(search_term)
        page.locator("button[onclick='_afOflSearch()']").click()
        page.wait_for_timeout(3000)
        if page.locator("#af-ofl-results button[onclick*='_afSelect']").count():
            break
        if attempt == 0:
            warn(f"No results on attempt 1 for '{search_term}' — retrying…")

    local_btn = page.locator("#af-ofl-results button[onclick*='_afSelectLocal']").first
    community_btn = page.locator("#af-ofl-results button[onclick*='_afSelectCommunity']").first
    ofl_btn = page.locator("#af-ofl-results button[onclick*='_afSelectOfl']").first

    if local_btn.count() and local_btn.is_visible():
        print(f"    Local  : {(local_btn.get_attribute('onclick') or '')[:80]}")
        local_btn.click(); page.wait_for_timeout(600)
    elif community_btn.count() and community_btn.is_visible():
        print(f"    Community: {(community_btn.get_attribute('onclick') or '')[:80]}")
        community_btn.click(); page.wait_for_timeout(3000)
    elif ofl_btn.count() and ofl_btn.is_visible():
        print(f"    OFL    : {(ofl_btn.get_attribute('onclick') or '')[:80]}")
        ofl_btn.click(); page.wait_for_timeout(3000)
    else:
        bug(step_id, f"No profile found for '{search_term}'")

    prof_val = page.locator("#af-prof").input_value()
    if not prof_val:
        bug(step_id, "Profile dropdown empty after selection")
        opts = [o for o in page.locator("#af-prof option").all() if o.get_attribute("value")]
        if opts:
            page.locator("#af-prof").select_option(value=opts[0].get_attribute("value"))
            prof_val = page.locator("#af-prof").input_value()
    ok(f"Profile: '{prof_val}'")

    page.locator("#af-name").fill(name)
    page.locator("#af-uni").fill(str(universe))
    addr_field = page.locator("#af-addr")
    auto_val = addr_field.input_value()
    if str(auto_val) != str(address):
        warn(f"Address auto-populated as '{auto_val}', expected {address} — overriding")
        addr_field.fill(str(address))
    else:
        ok(f"Address auto-populated correctly: {address}")

    page.locator("button[onclick='_submitAddFixture()']").filter(visible=True).first.click()
    page.wait_for_timeout(1200)
    close_modal(page)

    layout = api_get("/api/layout")
    fixtures = (layout or {}).get("fixtures", [])
    match = next((f for f in fixtures if f.get("name") == name), None)
    if match:
        ok(f"{name} saved — profile:'{match.get('dmxProfileId')}' addr:{match.get('dmxStartAddr')}")
    else:
        bug(step_id, f"{name} not found in /api/layout after submit")


def step_positions(page):
    log("4/5", "Set fixture positions")
    positions = [
        (0, "MH1-Sly",    500,  0,  1690),
        (1, "MH2-Sly",   1605,  0,  1690),
        (2, "350W-Spot",  1605, 150,  550),
    ]
    for idx, name, x, y, z in positions:
        _set_position(page, idx, name, x, y, z)
    ss(page, "positions.png")
    pause("4/5", "positions set")


def _set_position(page, idx, name, x, y, z):
    go(page, "setup")
    page.wait_for_timeout(500)
    edit_btn = page.locator(f"button[onclick='editFixture({idx})']")
    if not (edit_btn.count() and edit_btn.is_visible()):
        bug("pos", f"Edit button for fixture {idx} ({name}) not found")
        return
    edit_btn.click()
    page.wait_for_timeout(700)
    if not page.locator("#fx-px").is_visible():
        bug("pos", f"editFixture modal did not open for {name}")
        close_modal(page); return
    for fid, val in [("fx-px", x), ("fx-py", y), ("fx-pz", z)]:
        f = page.locator(f"#{fid}")
        if f.count() and f.is_visible():
            f.fill(str(val))
    save_btn = page.locator("button[onclick*='saveFixture']").first
    if save_btn.count() and save_btn.is_visible():
        save_btn.click(); page.wait_for_timeout(1500)
    else:
        bug("pos", f"saveFixture button not found for {name}")
    close_modal(page)
    layout = api_get("/api/layout")
    saved = next((c for c in (layout or {}).get("children", []) if c.get("id") == idx), None)
    if saved and saved.get("x") == x:
        ok(f"{name} → X:{x} Y:{y} Z:{z}")
    else:
        got = f"X:{saved.get('x')} Y:{saved.get('y')} Z:{saved.get('z')}" if saved else "not found"
        bug("pos", f"{name} position mismatch — expected X:{x} Y:{y} Z:{z}, got {got}")


def step_6_cameras(page):
    log(6, "Add cameras via API probe (192.168.10.235)")
    CAM_IP = "192.168.10.235"
    try:
        req = urllib.request.Request(
            f"{BASE}/api/cameras",
            data=json.dumps({"ip": CAM_IP, "name": "Tracking"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        added = resp.get("added", 0)
        ok(f"{added} camera fixture(s) added from {CAM_IP}")
    except Exception as e:
        bug(6, f"POST /api/cameras failed: {e}")

    cams = api_get("/api/cameras") or []
    ok(f"{len(cams)} camera(s) registered")
    if not cams:
        bug(6, "No cameras registered after probe")
    go(page, "setup")
    page.wait_for_timeout(800)
    ss(page, "06-cameras.png")

    # Set camera positions
    cam_positions = [
        (3, "Cam1-Left-HiRes",  830, 120, 1930),
        (4, "Cam2-Right",      1275, 120, 1930),
    ]
    for idx, name, x, y, z in cam_positions:
        _set_position(page, idx, name, x, y, z)
    pause(6, "cameras added + positioned")


def step_7_calibrate_buttons(page):
    log(7, "Verify Calibrate buttons (Setup)")
    go(page, "setup")
    page.wait_for_timeout(600)
    layout = api_get("/api/layout") or {}
    dmx_fixtures = [f for f in layout.get("fixtures", []) if f.get("dmxProfileId")]
    check_pairs = [(f["id"], f["name"]) for f in dmx_fixtures[:3]]
    if not check_pairs:
        check_pairs = [(0, "MH1-Sly"), (1, "MH2-Sly"), (2, "350W-Spot")]
    for fid, name in check_pairs:
        cal_btn = page.locator(f"button[onclick*='_moverCalStart({fid})']").first
        if cal_btn.count() and cal_btn.is_visible():
            ok(f"Calibrate button present for {name} (id {fid})")
        else:
            bug(7, f"Calibrate button not found for {name} (id {fid})")
    ss(page, "07-calibrate.png")
    pause(7, "calibrate buttons verified")


def _create_stage_object(page, step_id, name, obj_type, x, y, z, w, h_mm, d):
    """Open the Objects panel Add dialog and create an object. Returns True on success."""
    go(page, "layout")
    page.wait_for_timeout(600)
    add_obj_btn = page.locator("button[onclick*='newObject()']")
    if not (add_obj_btn.count() and add_obj_btn.is_visible()):
        bug(step_id, f"newObject() button not found in layout panel")
        return False
    add_obj_btn.click()
    page.wait_for_timeout(500)

    sf_type = page.locator("#sf-type")
    if sf_type.count() and sf_type.is_visible():
        sf_type.select_option(value=obj_type)
        page.wait_for_timeout(300)

    # Unlock stage-lock if present so position/size fields are editable
    sf_lock = page.locator("#sf-lock")
    if sf_lock.count() and sf_lock.is_checked():
        sf_lock.uncheck()
        page.wait_for_timeout(200)

    for fid, val in [("sf-name", name), ("sf-x", x), ("sf-y", y), ("sf-z", z),
                     ("sf-w", w), ("sf-h", h_mm), ("sf-d", d)]:
        f = page.locator(f"#{fid}")
        if f.count() and f.is_visible():
            f.fill(str(val))

    save_btn = page.locator("button[onclick='createObject()']").first
    if save_btn.count() and save_btn.is_visible():
        save_btn.click()
        page.wait_for_timeout(600)
        return True
    else:
        bug(step_id, f"createObject() button not found for '{name}'")
        return False


def step_8_music_object(page):
    log(8, "Create 'Music' stage object at X:800 Y:2350 Z:1250 — 297×210×1 mm")
    try:
        ok_val = _create_stage_object(page, 8, "Music", "prop", 800, 2350, 1250, 297, 210, 1)
        if ok_val:
            ok("'Music' object created (297×210×1 mm)")
    except Exception as e:
        bug(8, f"Music object creation failed: {e}")
    close_modal(page)
    ss(page, "08-music-object.png")
    pause(8, "Music object on stage")


def step_9_aim_red(page):
    log(9, "Orient mode — set all fixtures to red")
    go(page, "layout")
    page.wait_for_timeout(600)
    for idx in range(3):
        try:
            details_btn = page.locator(f"button[onclick*='showFixtureDetails({idx})'], button[onclick*='fixtureDetails({idx})']").first
            if not (details_btn.count() and details_btn.is_visible()):
                # try via setup tab
                go(page, "setup")
                page.wait_for_timeout(400)
                details_btn = page.locator(f"button[onclick*='Details'][onclick*='{idx}'], button:has-text('Details')").nth(idx)
            if details_btn.count() and details_btn.is_visible():
                details_btn.click(); page.wait_for_timeout(600)
                r_field = page.locator("input[id*='red'], input[id*='-r']").first
                if r_field.count() and r_field.is_visible():
                    r_field.fill("255")
                for fid in ["g", "b", "green", "blue"]:
                    f = page.locator(f"input[id*='{fid}']").first
                    if f.count() and f.is_visible():
                        f.fill("0")
                send_btn = page.locator("button:has-text('Send'), button[onclick*='sendColor'], button[onclick*='applyColor']").first
                if send_btn.count() and send_btn.is_visible():
                    send_btn.click(); page.wait_for_timeout(300)
                ok(f"Red sent to fixture #{idx}")
                close_modal(page)
            else:
                bug(9, f"Details button not found for fixture #{idx}")
        except Exception as e:
            bug(9, f"Red set failed for fixture #{idx}: {e}")
            close_modal(page)
    ss(page, "09-aim-red.png")

    # Camera snapshot verification
    hr("Camera snapshot — verify red beams")
    layout = api_get("/api/layout") or {}
    cam_fixtures = [f for f in layout.get("fixtures", [])
                    if not f.get("dmxProfileId") or f.get("dmxProfileId") == "None"]
    if not cam_fixtures:
        cam_fixtures = [{"id": 3}, {"id": 4}]  # fallback
    for ci, cf in enumerate(cam_fixtures[:2]):
        fid = cf["id"]
        try:
            with urllib.request.urlopen(f"{BASE}/api/cameras/{fid}/snapshot?cam=0", timeout=10) as r:
                data = r.read()
            path = f"{SS_DIR}/09-beam-verify-cam{ci}.jpg"
            with open(path, "wb") as f:
                f.write(data)
            ok(f"Camera {ci} (fid:{fid}) snapshot saved ({len(data):,} bytes) → {path}")
        except Exception as e:
            warn(f"Camera {ci} (fid:{fid}) snapshot failed: {e}")

    pause(9, "red beams set — check camera snapshots above")


def step_10_blackout(page):
    log(10, "Blackout all fixtures")
    go(page, "setup")
    page.wait_for_timeout(600)
    any_ok = False
    for idx in range(3):
        try:
            details_btn = page.locator(f"button[onclick*='Details']").nth(idx)
            if details_btn.count() and details_btn.is_visible():
                details_btn.click(); page.wait_for_timeout(400)
                bo = page.locator("button:has-text('Blackout')").first
                if bo.count() and bo.is_visible():
                    bo.click(); page.wait_for_timeout(300)
                    ok(f"Blackout for fixture #{idx}")
                    any_ok = True
                close_modal(page)
        except Exception as e:
            bug(10, f"Blackout fixture #{idx}: {e}"); close_modal(page)
    if not any_ok:
        bug(10, "Blackout failed for all fixtures")
    ss(page, "10-blackout.png")
    pause(10, "all fixtures blacked out")


def step_11a_tracking(page):
    log("11a", "Camera tracking — Track buttons on camera cards")
    go(page, "setup")
    page.wait_for_timeout(600)
    track_btns = [b for b in page.locator("button:has-text('Track')").all() if b.is_visible()]
    if track_btns:
        ok(f"{len(track_btns)} Track button(s) found on camera cards")
    else:
        bug("11a", "No Track buttons found — cameras may not have been added")
    ss(page, "11a-tracking.png")
    pause("11a", "tracking UI verified")


def step_11b_track_action(page):
    log("11b", "Create 'Track People' action — target: Music object")
    go(page, "actions")
    page.wait_for_timeout(500)
    try:
        page.locator("button[onclick='newAction()']").click(); page.wait_for_timeout(600)
        page.locator("#ae-nm").fill("Track People")
        type_sel = page.locator("#ae-tp")
        opts = type_sel.locator("option").all_text_contents()
        track_opts = [o for o in opts if "track" in o.lower()]
        if track_opts:
            type_sel.select_option(label=track_opts[0])
            ok(f"Type: {track_opts[0]}")
        else:
            bug("11b", f"No Track type in dropdown: {opts}")

        # Wait for object list to load, then check the Music object
        page.wait_for_timeout(1200)
        track_objs = page.locator("#ae-track-objs")
        if track_objs.count() and track_objs.is_visible():
            music_labels = track_objs.locator("label")
            found_music = False
            for i in range(music_labels.count()):
                lbl = music_labels.nth(i)
                if "Music" in (lbl.inner_text() or ""):
                    cb = lbl.locator("input[type='checkbox']")
                    if cb.count() and not cb.is_checked():
                        cb.check()
                    ok("Music object selected as track target")
                    found_music = True
                    break
            if not found_music:
                warn("Music object not listed in Track targets — may not be created yet")
        else:
            warn("ae-track-objs not visible")

        page.locator("#ae-save").click(); page.wait_for_timeout(800)
    except Exception as e:
        bug("11b", f"Create Track action: {e}")
    close_modal(page)
    actions = api_get("/api/actions") or []
    tp = next((a for a in actions if a.get("name") == "Track People"), None)
    ok(f"'Track People' saved (id:{tp['id']}, type:{tp['type']})") if tp else bug("11b", "Not in /api/actions")
    ss(page, "11b-track-action.png")
    pause("11b", "Track People action saved")


def step_11c_floor_target(page):
    log("11c", "Create 'Floor Target' object with Figure-8 patrol")
    go(page, "layout")
    page.wait_for_timeout(600)
    try:
        add_obj_btn = page.locator("button[onclick*='newObject()']")
        if not (add_obj_btn.count() and add_obj_btn.is_visible()):
            bug("11c", "newObject() button not found in layout panel")
            ss(page, "11c-floor-target.png"); return
        add_obj_btn.click()
        page.wait_for_timeout(500)

        # Select "prop" type → mobility auto-sets to "moving", patrol section appears
        sf_type = page.locator("#sf-type")
        if sf_type.count() and sf_type.is_visible():
            sf_type.select_option(value="prop")
            page.wait_for_timeout(300)

        sf_lock = page.locator("#sf-lock")
        if sf_lock.count() and sf_lock.is_visible() and sf_lock.is_checked():
            sf_lock.uncheck(); page.wait_for_timeout(200)

        for fid, val in [("sf-name","Floor Target"),("sf-x","1050"),("sf-y","2000"),("sf-z","0"),
                         ("sf-w","500"),("sf-h","500"),("sf-d","500")]:
            f = page.locator(f"#{fid}")
            if f.count() and f.is_visible():
                f.fill(str(val))

        # Enable Figure-8 patrol
        pat_en = page.locator("#sf-pat-en")
        if pat_en.count() and pat_en.is_visible() and not pat_en.is_checked():
            pat_en.check(); page.wait_for_timeout(300)
        pat_sel = page.locator("#sf-pat-pattern")
        if pat_sel.count() and pat_sel.is_visible():
            pat_sel.select_option(value="figure8"); ok("Patrol: Figure 8")
        else:
            warn("Patrol pattern selector not visible")

        save_btn = page.locator("button[onclick='createObject()']").first
        if save_btn.count() and save_btn.is_visible():
            save_btn.click(); page.wait_for_timeout(600); ok("Floor Target created")
        else:
            bug("11c", "createObject() button not found")
    except Exception as e:
        bug("11c", f"Floor Target: {e}")
    close_modal(page)
    ss(page, "11c-floor-target.png")
    pause("11c", "Floor Target on stage")


def step_11d_figure8_action(page):
    log("11d", "Create 'Follow Figure 8' action")
    go(page, "actions")
    page.wait_for_timeout(500)
    try:
        page.locator("button[onclick='newAction()']").click(); page.wait_for_timeout(600)
        page.locator("#ae-nm").fill("Follow Figure 8")
        type_sel = page.locator("#ae-tp")
        opts = type_sel.locator("option").all_text_contents()
        track_opts = [o for o in opts if "track" in o.lower()]
        if track_opts:
            type_sel.select_option(label=track_opts[0])
        page.locator("#ae-save").click(); page.wait_for_timeout(800)
    except Exception as e:
        bug("11d", f"Follow Figure 8 action: {e}")
    close_modal(page)
    actions = api_get("/api/actions") or []
    ff = next((a for a in actions if a.get("name") == "Follow Figure 8"), None)
    ok(f"'Follow Figure 8' saved (id:{ff['id']})") if ff else bug("11d", "Not in /api/actions")
    ss(page, "11d-figure8-action.png")
    pause("11d", "Follow Figure 8 action saved")


def step_11e_timeline(page):
    log("11e", "Create timeline 'Walkthrough 533' (420 s) with Track People clips")
    go(page, "shows")
    page.wait_for_timeout(600)
    track_count = 0
    try:
        page.locator("button[onclick='newTimeline()']").click(); page.wait_for_timeout(1000)
        tl_sel = page.locator("#tl-select")
        opts = [o for o in tl_sel.locator("option").all_text_contents() if o and "Select" not in o]
        if opts:
            ok(f"Timeline auto-created: '{opts[-1]}'")
            tl_sel.select_option(index=len(opts)); page.wait_for_timeout(600)
            page.locator("#tl-name").fill("Walkthrough 533")
            page.locator("#tl-dur").fill("420")
            save_tl = page.locator("button[onclick*='saveTimeline']").first
            if save_tl.count() and save_tl.is_visible():
                save_tl.click(); page.wait_for_timeout(800); ok("Timeline saved as 'Walkthrough 533' (420 s)")
            else:
                bug("11e", "saveTimeline button not found")

            # Add one track per DMX fixture (0, 1, 2)
            for target in ["0", "1", "2"]:
                add_trk = page.locator("button[onclick='tlAddTrack()']")
                if add_trk.count() and add_trk.is_visible():
                    add_trk.click(); page.wait_for_timeout(400)
                    trk_fix = page.locator("#trk-fix")
                    if trk_fix.count() and trk_fix.is_visible():
                        trk_fix.select_option(value=target)
                    confirm = page.locator("button[onclick='tlAddTrackConfirm()']")
                    if confirm.count() and confirm.is_visible():
                        confirm.click(); page.wait_for_timeout(500)
                        ok(f"Track → fixture '{target}'")
                        track_count += 1
                    else:
                        close_modal(page)
                else:
                    bug("11e", f"+ Add Track not found (target {target})"); break

            # Add clips: Track People action, 0–420 s, on each track
            actions = api_get("/api/actions") or []
            tp = next((a for a in actions if a.get("name") == "Track People"), None)
            if tp:
                act_val = f"act:{tp['id']}"
                for ti in range(track_count):
                    clip_btn = page.locator(f"span[onclick='tlAddClipToTrack({ti})']")
                    if clip_btn.count() and clip_btn.is_visible():
                        clip_btn.click(); page.wait_for_timeout(400)
                        page.locator("#clip-fx").select_option(value=act_val)
                        page.locator("#clip-start").fill("0")
                        page.locator("#clip-dur").fill("420")
                        add_clip_btn = page.locator(f"button[onclick='tlAddClipConfirm({ti})']")
                        if add_clip_btn.count() and add_clip_btn.is_visible():
                            add_clip_btn.click(); page.wait_for_timeout(400)
                            ok(f"Clip added to track {ti}: Track People (0–420 s)")
                        else:
                            close_modal(page)
                            bug("11e", f"tlAddClipConfirm({ti}) button not found")
                    else:
                        bug("11e", f"Add clip button not found for track {ti}")
            else:
                bug("11e", "Track People action not found — no clips added")
        else:
            bug("11e", "No timelines after newTimeline()")
    except Exception as e:
        bug("11e", f"Timeline: {e}")
    close_modal(page)
    tls = api_get("/api/timelines") or []
    tl = next((t for t in tls if "Walkthrough" in (t.get("name") or "")), None)
    if tl:
        tracks = tl.get("tracks", [])
        clips_total = sum(len(t.get("clips", [])) for t in tracks)
        ok(f"Timeline '{tl['name']}' ({tl.get('durationS')}s) — {len(tracks)} track(s), {clips_total} clip(s)")
        if tl.get("durationS") != 420:
            bug("11e", f"Duration {tl.get('durationS')}s ≠ 420s")
        if clips_total == 0:
            bug("11e", "No clips in timeline tracks")
    else:
        bug("11e", "Walkthrough timeline not found in API")
    ss(page, "11e-timeline.png")
    pause("11e", "timeline created with Track People clips")


def step_11f_runtime(page):
    log("11f", "Runtime — Bake All + Start Show")
    go(page, "runtime")
    page.wait_for_timeout(600)
    # Bake button is per-playlist-row: button[onclick*='_rtBakeOne']
    bake_btns = page.locator("button[onclick*='_rtBakeOne']").all()
    bake_btns = [b for b in bake_btns if b.is_visible()]
    if bake_btns:
        ok(f"{len(bake_btns)} Bake button(s) found in playlist")
        for b in bake_btns:
            b.click(); page.wait_for_timeout(2000)
        ok("Bake triggered for all timelines")
    else:
        # Fallback: call _rtBakeAll via JS
        warn("No per-row Bake buttons visible — calling _rtBakeAll() via JS")
        page.evaluate("_rtBakeAll(function(){})")
        page.wait_for_timeout(3000)
        ok("Bake triggered via JS")
    start_btn = page.locator("button:has-text('Start Show')").first
    if start_btn.count() and start_btn.is_visible():
        ok("Start Show button present")
    else:
        bug("11f", "Start Show button not found")
    ss(page, "11f-runtime.png")
    pause("11f", "runtime ready — Start Show button visible")


def step_api_verify():
    hr("API State Verification")
    layout    = api_get("/api/layout") or {}
    actions   = api_get("/api/actions") or []
    timelines = api_get("/api/timelines") or []
    cameras   = api_get("/api/cameras") or []
    fixtures  = layout.get("fixtures", [])
    pos_map   = {c["id"]: c for c in layout.get("children", [])}
    print(f"  Fixtures  : {len(fixtures)}")
    for f in fixtures:
        p = pos_map.get(f["id"], {})
        print(f"    [{f['id']}] {f['name']}  profile:'{f.get('dmxProfileId')}'  "
              f"addr:{f.get('dmxStartAddr')}  X:{p.get('x')} Y:{p.get('y')} Z:{p.get('z')}")
    print(f"  Actions   : {len(actions)} — {[a['name'] for a in actions]}")
    print(f"  Timelines : {[(t['name'], len(t.get('tracks',[]))) for t in timelines]}")
    print(f"  Cameras   : {len(cameras)}")
    if len(fixtures) < 3:
        bug("api", f"Only {len(fixtures)} fixture(s), expected ≥3")
    for f in fixtures[:3]:
        if not f.get("dmxProfileId"):
            bug("api", f"'{f['name']}' has no dmxProfileId")
    if not actions:
        bug("api", "No actions saved")
    if not any(t.get("tracks") for t in timelines):
        bug("api", "No timeline with tracks")


def step_12_save(page):
    log(12, "Save project (File → Save As)")
    go(page, "runtime")
    page.wait_for_timeout(400)
    page.locator("#file-menu-btn").click(); page.wait_for_timeout(400)
    save_as = page.locator("text=Save As").first
    if save_as.count() and save_as.is_visible():
        save_as.click(); page.wait_for_timeout(700)
        name_f = page.locator("input[id*='proj'], input[placeholder*='name']").first
        if name_f.count() and name_f.is_visible():
            name_f.fill("Walkthrough 533")
            page.locator("button:has-text('Save')").first.click()
            page.wait_for_timeout(500); ok("Project saved via dialog")
        else:
            bug(12, "Native file-picker opened — cannot automate. Use Ctrl+S manually.")
    else:
        bug(12, "Save As not found in File menu")
    ss(page, "12-saved.png")
    pause(12, "project saved")


# ── Main runner ────────────────────────────────────────────────────────────────

def run():
    mode = "AUTO (headless)" if AUTO else "INTERACTIVE (headed)"
    print(f"\n{'='*60}")
    print(f"  SlyLED QA Walkthrough #533 — {mode}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        if AUTO:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        else:
            browser = p.chromium.launch(headless=False, slow_mo=300,
                                        args=["--no-sandbox", "--start-maximized"])
        ctx = browser.new_context(viewport=None if not AUTO else {"width": 1600, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(12000)
        global PAGE
        PAGE = page

        try:
            step_1_launch(page)
            step_2_new_project(page)
            dmx_bridge_added = step_3a_discover(page)
            step_3b_dmx_engine(page, dmx_bridge_added)
            step_4_add_fixtures(page)
            step_positions(page)
            step_6_cameras(page)
            step_7_calibrate_buttons(page)
            step_8_music_object(page)
            step_9_aim_red(page)
            step_10_blackout(page)
            step_11a_tracking(page)
            step_11b_track_action(page)
            step_11c_floor_target(page)
            step_11d_figure8_action(page)
            step_11e_timeline(page)
            step_11f_runtime(page)
            step_api_verify()
            step_12_save(page)
        except KeyboardInterrupt:
            print("\n\nInterrupted.")
        finally:
            if not AUTO:
                print("\n  Browser will stay open for 10 s for final review…")
                page.wait_for_timeout(10000)
            browser.close()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  QA WALKTHROUGH 533 — COMPLETE")
    print(f"  Screenshots → {SS_DIR}/")
    print(f"{'='*60}")
    if BUGS:
        print(f"\n  🐛 {len(BUGS)} bug(s) found:\n")
        for b in BUGS:
            print(f"    • {b}")
    else:
        print("\n  ✅ No bugs found — walkthrough clean.")
    print()


if __name__ == "__main__":
    run()
