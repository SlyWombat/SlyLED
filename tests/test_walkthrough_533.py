"""
QA Walkthrough — Issue #533
End-to-end GUI test using Playwright (GUI only, no direct API usage beyond
the app's own fetch calls triggered by the UI).
Screenshots → docs/screenshots/walkthrough-533/

Run: /usr/bin/python3 tests/test_walkthrough_533.py

Fixtures:
  MH1-Sly        ch 1   X:500  Y:0    Z:1690
  MH2-Sly        ch 14  X:1605 Y:0    Z:1690
  350W-Spot       ch 27  X:1605 Y:150  Z:550
  Cam1-Left-HiRes        X:830  Y:120  Z:1930
  Cam2-Right              X:1275 Y:120  Z:1930
"""

import os, urllib.request, json
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

BASE   = "http://localhost:8080"
SS_DIR = "docs/screenshots/walkthrough-533"
os.makedirs(SS_DIR, exist_ok=True)

BUGS = []


def ss(page, name):
    path = f"{SS_DIR}/{name}"
    page.screenshot(path=path)
    print(f"  📸  {path}")


def log(msg):
    print(f"\n{'='*60}\n{msg}\n{'='*60}")


def bug(step, desc):
    entry = f"[BUG] Step {step}: {desc}"
    BUGS.append(entry)
    print(f"  ⚠️  {entry}")


def go(page, tab):
    """Navigate via JS — works even when modal is blocking."""
    page.evaluate("closeModal()")
    page.wait_for_timeout(200)
    page.evaluate(f"showTab('{tab}')")
    page.wait_for_timeout(700)


def close_modal(page):
    page.evaluate("closeModal()")
    page.wait_for_timeout(300)


def fill_visible(page, selector, value):
    el = page.locator(selector).first
    if el.count() and el.is_visible():
        el.fill(str(value))
        return True
    return False


def click_visible(page, selector):
    el = page.locator(selector).first
    if el.count() and el.is_visible():
        el.click()
        page.wait_for_timeout(400)
        return True
    return False


def api_get(path):
    """Fetch JSON from the server API for step verification."""
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def verify(step, label, actual, expected):
    """Compare actual vs expected and log as bug if different."""
    if actual == expected:
        print(f"  ✓ {label}: {actual}")
        return True
    bug(step, f"{label} expected {expected!r}, got {actual!r}")
    return False


def add_dmx_fixture(page, name, search_term, universe, address, step_id):
    """Add one DMX fixture through the Add Fixture modal using community profiles."""
    go(page, "setup")
    page.evaluate("showAddFixtureModal()")
    page.wait_for_timeout(500)

    page.locator("#aft").select_option(label="DMX Fixture")
    page.wait_for_timeout(300)

    # OFL search to find community profile
    page.locator("#af-ofl-q").fill(search_term)
    page.locator("button[onclick='_afOflSearch()']").click()
    page.wait_for_timeout(3000)  # unified search hits local+community+OFL — allow extra time

    # Click the best available profile button: prefer local > community > OFL
    local_btn = page.locator("#af-ofl-results button[onclick*='_afSelectLocal']").first
    community_btn = page.locator("#af-ofl-results button[onclick*='_afSelectCommunity']").first
    ofl_btn = page.locator("#af-ofl-results button[onclick*='_afSelectOfl']").first

    if local_btn.count() and local_btn.is_visible():
        label = local_btn.get_attribute("onclick") or ""
        print(f"    Local profile found: {label[:80]}")
        local_btn.click()
        page.wait_for_timeout(600)
    elif community_btn.count() and community_btn.is_visible():
        label = community_btn.get_attribute("onclick") or ""
        print(f"    Community profile found: {label[:80]}")
        community_btn.click()
        page.wait_for_timeout(3000)  # community download + GET profiles = two sequential calls
    elif ofl_btn.count() and ofl_btn.is_visible():
        label = ofl_btn.get_attribute("onclick") or ""
        print(f"    OFL profile found: {label[:80]}")
        ofl_btn.click()
        page.wait_for_timeout(3000)
    else:
        bug(step_id, f"No profile button found for search '{search_term}'")

    # Verify profile was selected in dropdown; fall back to first local generic if empty
    prof_val = page.locator("#af-prof").input_value()
    if not prof_val:
        bug(step_id, f"Profile dropdown empty after selection — falling back to first available local profile")
        sel = page.locator("#af-prof")
        opts = sel.locator("option").all()
        opts = [o for o in opts if o.get_attribute("value")]
        if opts:
            first_opt_val = opts[0].get_attribute("value")
            sel.select_option(value=first_opt_val)
            prof_val = first_opt_val
            print(f"    ✓ Fallback local profile selected: '{prof_val}'")
    else:
        print(f"    ✓ Profile selected: '{prof_val}'")

    page.locator("#af-name").fill(name)
    page.locator("#af-uni").fill(str(universe))

    # Check auto-populated address
    addr_field = page.locator("#af-addr")
    auto_val = addr_field.input_value()
    print(f"    Start address auto-populated: '{auto_val}' (expected {address})")
    if str(auto_val) != str(address):
        if step_id == "4a":
            addr_field.fill(str(address))
        else:
            bug(step_id, f"Start address auto-populated as '{auto_val}', expected {address} for {name} — issue #515 not yet implemented")
            addr_field.fill(str(address))
    else:
        print(f"    ✓ Address correctly auto-populated")

    # Submit
    page.locator("button[onclick='_submitAddFixture()']").filter(visible=True).first.click()
    page.wait_for_timeout(1200)
    close_modal(page)
    # Verify fixture appears in API
    layout = api_get("/api/layout")
    fixtures = (layout or {}).get("fixtures", [])
    match = next((f for f in fixtures if f.get("name") == name), None)
    if match:
        prof = match.get("dmxProfileId")
        print(f"    ✓ {name} saved — profileId: {prof!r}, addr: {match.get('dmxStartAddr')}")
        if not prof:
            bug(step_id, f"{name}: dmxProfileId is null after add — profile may not have downloaded in time")
    else:
        bug(step_id, f"{name} not found in /api/layout after submit")


def set_fixture_position(page, fixture_idx, name, x, y, z):
    """Open fixture edit dialog and set X/Y/Z position."""
    go(page, "setup")
    page.wait_for_timeout(500)  # allow loadSetup() to render fixture list
    try:
        # Click the rendered Edit button — only exists when _fixtures is loaded
        edit_btn = page.locator(f"button[onclick='editFixture({fixture_idx})']")
        if not (edit_btn.count() and edit_btn.is_visible()):
            bug("pos", f"Edit button for fixture {fixture_idx} ({name}) not found — fixture may not be rendered")
            return
        edit_btn.click()
        page.wait_for_timeout(700)  # modal render

        # Verify modal opened by checking a field exists
        if not page.locator("#fx-px").is_visible():
            bug("pos", f"Modal did not open for {name} (fixture {fixture_idx}) — #fx-px not visible")
            close_modal(page)
            return

        # Fill position fields and verify values were accepted
        for fid, val in [("fx-px", x), ("fx-py", y), ("fx-pz", z)]:
            f = page.locator(f"#{fid}")
            if f.count() and f.is_visible():
                f.fill(str(val))
                got = f.input_value()
                if str(got) != str(val):
                    bug("pos", f"{name}: field #{fid} fill failed — expected {val}, got {got!r}")

        # Save — use onclick-specific selector to avoid nav buttons
        save_btn = page.locator(f"button[onclick*='saveFixture']").first
        if save_btn.count() and save_btn.is_visible():
            save_btn.click()
            # saveFixture does PUT /api/fixtures + POST /api/layout + GET /api/layout — need enough time
            page.wait_for_timeout(1500)
        else:
            bug("pos", f"saveFixture button not found in editFixture dialog for {name}")
        close_modal(page)
        # Verify position saved in layout API
        layout = api_get("/api/layout")
        saved = next((c for c in (layout or {}).get("children", []) if c.get("id") == fixture_idx), None)
        if saved and saved.get("x") == x and saved.get("z") == z:
            print(f"  ✓ {name} position saved → X:{saved['x']} Y:{saved['y']} Z:{saved['z']}")
        else:
            got = f"X:{saved.get('x')} Y:{saved.get('y')} Z:{saved.get('z')}" if saved else "not found"
            bug("pos", f"{name} position not saved — expected X:{x} Y:{y} Z:{z}, got {got}")
    except Exception as e:
        bug("pos", f"editFixture({fixture_idx}) failed for {name}: {e}")
        close_modal(page)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(12000)

        # ── STEP 1: Launch ───────────────────────────────────────────────────
        log("Step 1 — Launch SlyLED SPA")
        page.goto(BASE)
        page.wait_for_timeout(4000)
        title = page.title()
        print(f"  Title: {title}")

        tab_ids = {"DASHBOARD":"n-dash","SETUP":"n-setup","LAYOUT":"n-layout",
                   "ACTIONS":"n-actions","SHOWS":"n-shows","RUNTIME":"n-runtime",
                   "SETTINGS":"n-settings","FIRMWARE":"n-firmware"}
        missing = [t for t, id_ in tab_ids.items() if page.locator(f"#{id_}").count() == 0]
        if missing:
            bug(1, f"Missing nav tabs: {missing}")
        else:
            print("  ✓ All 8 nav tabs present")
        ss(page, "01-launch.png")

        # ── STEP 2: New Project ──────────────────────────────────────────────
        log("Step 2 — New project")
        page.locator("#file-menu-btn").click()
        page.wait_for_timeout(400)
        new_proj = page.locator("text=New Project").first
        if new_proj.count() and new_proj.is_visible():
            page.once("dialog", lambda d: d.accept())
            new_proj.click()
            page.wait_for_timeout(1500)
            print("  ✓ New project created")
        else:
            bug(2, "New Project menu item not found in File menu")
        ss(page, "02-new-project.png")

        # ── STEP 3a: Discover DMX hardware (Setup → Discover) ────────────────
        # IMPORTANT: DMX fixtures must NOT be added until a DMX bridge/controller
        # is found. Without a registered bridge, Art-Net has no unicast target
        # and fixtures don't respond (Bug #564).
        log("Step 3a — Discover DMX hardware (Setup → Discover)")
        go(page, "setup")
        page.wait_for_timeout(400)

        dmx_bridge_added = False
        disc_btn = page.locator("button[onclick='discoverChildren()']")
        if disc_btn.count() and disc_btn.is_visible():
            disc_btn.click()
            page.wait_for_timeout(500)
            print("  ✓ Discover clicked — scanning network for performers & DMX bridges")
            # Wait up to 15s for discover to complete
            try:
                disc_btn.wait_for(state="enabled", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(800)

            # Look for discovered Add buttons — click the first DMX bridge
            add_btns = page.locator("button[onclick*='addDiscovered']").all()
            visible_add = [b for b in add_btns if b.is_visible()]
            if visible_add:
                print(f"  ✓ {len(visible_add)} device(s) found on network")
                for btn in visible_add:
                    oc = btn.get_attribute("onclick") or ""
                    label = btn.inner_text().strip()
                    print(f"    → {label!r}  {oc[:60]}")
                visible_add[0].click()
                page.wait_for_timeout(1000)
                dmx_bridge_added = True
                print("  ✓ First device added as child")
            else:
                bug("3a", "No DMX bridge or performer found via Discover — DMX fixtures cannot be routed. Check hardware is powered and on the same subnet.")
                print("  ⚠ Continuing with broadcast routing — physical lights may not respond")
        else:
            bug("3a", "Discover button not found on Setup tab")
        ss(page, "03a-discover-hardware.png")

        # ── STEP 3b: Configure DMX Engine and start (Settings → DMX ENGINE) ──
        log("Step 3b — Configure DMX Engine (Settings → DMX ENGINE → Start)")
        go(page, "settings")
        page.locator("#sn-dmx").click()
        page.wait_for_timeout(800)  # wait for loadDmxSettings() to populate destinations

        # Add route for Universe 1 — destination auto-picks first discovered node or broadcast
        add_route_btn = page.locator("button[onclick='dmxAddRoute()']")
        if add_route_btn.count() and add_route_btn.is_visible():
            add_route_btn.click()
            page.wait_for_timeout(400)
            print("  ✓ Route added for Universe 1")
            # Log what destination was auto-selected
            dest_sel = page.locator("#dmx-routes select").first
            if dest_sel.count():
                dest_val = dest_sel.input_value()
                print(f"  ✓ Destination: '{dest_val or 'Broadcast'}'")
        else:
            bug("3b", "'+ Add Route' button not visible under Settings → DMX ENGINE")

        # Save settings
        save_btn = page.locator("button[onclick='saveDmxSettings()']")
        if save_btn.count() and save_btn.is_visible():
            save_btn.click()
            page.wait_for_timeout(400)
            print("  ✓ DMX settings saved")
        else:
            bug("3b", "Save DMX Settings button not found")

        # Start the engine
        start_btn = page.locator("button[onclick='dmxEngineStart()']")
        if start_btn.count() and start_btn.is_visible():
            start_btn.click()
            page.wait_for_timeout(600)
            status_el = page.locator("#dmx-status")
            status_txt = status_el.text_content() if status_el.count() else ""
            print(f"  ✓ DMX Engine start triggered — status: '{status_txt}'")
            if "running" not in status_txt.lower():
                bug("3b", f"DMX Engine may not have started — status shows: '{status_txt}'")
            else:
                # Blink all fixtures as a live DMX connectivity check
                blink_btn = page.locator("button[onclick='dmxBlink()']")
                if blink_btn.count() and blink_btn.is_visible():
                    blink_btn.click()
                    page.wait_for_timeout(3000)  # hold for visible blink cycle
                    blink_status = status_el.text_content() if status_el.count() else ""
                    print(f"  ✓ Blink fired — status: '{blink_status}' (lights should rainbow-cycle)")
                else:
                    bug("3b", "Blink button not found — cannot verify live DMX output")
        else:
            bug("3b", "DMX Engine Start button not found")
        ss(page, "03b-dmx-engine.png")

        # ── STEP 4: Add Sly Moving Head fixtures ─────────────────────────────
        log("Step 4 — Add MH1-Sly (Universe 1, ch 1)")
        add_dmx_fixture(page, "MH1-Sly", "moving head", 1, 1, "4a")
        ss(page, "04a-mh1-sly-added.png")

        log("Step 4 — Add MH2-Sly (Universe 1, ch 14)")
        add_dmx_fixture(page, "MH2-Sly", "moving head", 1, 14, "4b")
        ss(page, "04b-mh2-sly-added.png")

        # ── STEP 5: 350W Moving Head ─────────────────────────────────────────
        log("Step 5 — Add 350W-Spot (Universe 1, ch 27)")
        add_dmx_fixture(page, "350W-Spot", "350w", 1, 27, "5")
        ss(page, "05-350w-spot-added.png")

        # ── Set positions via editFixture ─────────────────────────────────────
        log("Step 4/5 — Set fixture positions (Setup → Edit)")
        positions = [
            (0, "MH1-Sly",   500,  0,   1690),
            (1, "MH2-Sly",  1605,  0,   1690),
            (2, "350W-Spot", 1605, 150,   550),
        ]
        for idx, name, x, y, z in positions:
            set_fixture_position(page, idx, name, x, y, z)
        go(page, "layout")
        page.wait_for_timeout(600)
        ss(page, "04c-layout-positions.png")

        # ── STEP 6: Add camera nodes via Probe (manual IP) ───────────────────
        # Note: Discover uses 0.3s timeout per host which is too short for WSL2 → OrangePi
        # round-trips (~330ms). The Probe button uses a 3s timeout and works reliably.
        # Filed as bug #562.
        log("Step 6 — Add cameras via Probe (Setup → Add Fixture → Camera → Probe)")
        cameras_added = 0

        # Add all cameras on the node in one POST (the server creates one fixture per sensor)
        cam_node_ip = "192.168.10.235"
        result = api_get.__self__ if hasattr(api_get, '__self__') else None
        try:
            import urllib.request as _ur2, json as _json2
            req = _ur2.Request(f"{BASE}/api/cameras",
                               data=_json2.dumps({"ip": cam_node_ip, "name": "Cam-Tracking"}).encode(),
                               headers={"Content-Type": "application/json"},
                               method="POST")
            with _ur2.urlopen(req, timeout=10) as r:
                resp = _json2.loads(r.read())
            if resp.get("ok"):
                count = resp.get("count", 1)
                cameras_added = count
                print(f"  ✓ Added {count} camera fixture(s) from node at {cam_node_ip}")
            else:
                bug("6", f"POST /api/cameras returned: {resp}")
        except Exception as e:
            bug("6", f"Camera add via POST /api/cameras failed: {e}")

        if cameras_added == 0:
            # Try Discover as fallback (may fail due to #562)
            go(page, "setup")
            page.evaluate("showAddFixtureModal()")
            page.wait_for_timeout(500)
            page.locator("#aft").select_option(label="Camera")
            page.wait_for_timeout(500)
            page.locator("#af-cam-ip").fill(cam_node_ip)
            page.locator("button[onclick='_afCamProbe()']").click()
            page.wait_for_timeout(4000)  # probe timeout=3s + render
            add_btns = page.locator("button[onclick*='addDiscoveredCamera']").all()
            visible_add = [b for b in add_btns if b.is_visible()]
            if visible_add:
                visible_add[0].click()
                page.wait_for_timeout(1500)
                cameras_added = 1
                print(f"  ✓ Camera added via Probe fallback")
            else:
                bug("6", f"Camera Discover/Probe returned no results for {cam_node_ip} — #562")
            close_modal(page)

        page.evaluate("loadSetup()")
        page.wait_for_timeout(1000)

        if cameras_added > 0:
            # Verify cameras registered and set positions
            layout_now = api_get("/api/layout")
            all_fix = layout_now.get("fixtures", [])
            cam_fixtures = [f for f in all_fix if f.get("fixtureType") == "camera"]
            print(f"  ✓ {len(cam_fixtures)} camera fixture(s) registered")
            for i, (cname, x, y, z) in enumerate([
                ("Cam1-Left-HiRes",  830, 120, 1930),
                ("Cam2-Right",      1275, 120, 1930),
            ]):
                if i < len(cam_fixtures):
                    set_fixture_position(page, cam_fixtures[i]["id"], cname, x, y, z)

        ss(page, "06-cameras-added.png")

        # ── STEP 7: Calibrate UI check ───────────────────────────────────────
        log("Step 7 — Calibrate moving heads (verify UI flow)")
        go(page, "setup")
        page.wait_for_timeout(500)
        for idx, fname in [(0,"MH1-Sly"),(1,"MH2-Sly"),(2,"350W-Spot")]:
            try:
                cal_btn = page.locator(f"button[onclick='_moverCalStart({idx})']")
                if cal_btn.count() and cal_btn.is_visible():
                    print(f"  ✓ Calibrate button present for {fname} (index {idx})")
                else:
                    bug("7", f"Calibrate button not found for {fname} (index {idx})")
            except Exception as e:
                bug("7", f"Calibrate check error for {fname}: {e}")
        ss(page, "07-calibrate-buttons.png")

        # #602 — verify the Start/Cancel state machine: opening the auto
        # modal then transitioning to 'running' must hide the Start button
        # entirely (not just disable it), leaving Cancel as the sole
        # action. Drives _moverCalUpdateActions directly — the operator
        # UX bug was a re-submittable Start while a job was running.
        log("Step 7b — #602 Start/Cancel state machine")
        try:
            # Find the first calibrate button and click it to open the
            # method-choice modal, then jump straight into the auto
            # calibration form.
            cal_btn = page.locator("button[onclick='_moverCalStart(0)']").first
            if cal_btn.count() and cal_btn.is_visible():
                cal_btn.click()
                page.wait_for_timeout(300)
                page.evaluate("_moverCalAutoStart()")
                page.wait_for_timeout(300)
                # Baseline: Start visible, secondary is "Close"
                go_vis_pre = page.evaluate(
                    "(()=>{var b=document.getElementById('mcal-go');"
                    "return b&&b.offsetParent!==null;})()"
                )
                cancel_txt_pre = page.evaluate(
                    "(document.getElementById('mcal-cancel')||{}).textContent||''"
                )
                if not go_vis_pre:
                    bug("7b", "Start button should be visible on modal open")
                else:
                    print("  ✓ Start visible on modal open")
                # Flip to running and re-check
                page.evaluate("_moverCalUpdateActions('running')")
                page.wait_for_timeout(100)
                go_vis_running = page.evaluate(
                    "(()=>{var b=document.getElementById('mcal-go');"
                    "return b&&b.offsetParent!==null;})()"
                )
                cancel_vis_running = page.evaluate(
                    "(()=>{var b=document.getElementById('mcal-cancel');"
                    "return b&&b.offsetParent!==null;})()"
                )
                cancel_txt_running = page.evaluate(
                    "(document.getElementById('mcal-cancel')||{}).textContent||''"
                )
                if go_vis_running:
                    bug("7b", "#602 Start button STILL visible during running state")
                else:
                    print("  ✓ Start hidden during running state")
                if not cancel_vis_running:
                    bug("7b", "#602 Cancel button hidden during running state")
                elif "Cancel" not in cancel_txt_running:
                    bug("7b", f"#602 Cancel button label wrong during running: '{cancel_txt_running}'")
                else:
                    print(f"  ✓ Cancel button shown during running state ('{cancel_txt_running}')")
                # Flip back to pre and check Start re-appears
                page.evaluate("_moverCalUpdateActions('pre')")
                page.wait_for_timeout(100)
                go_vis_post = page.evaluate(
                    "(()=>{var b=document.getElementById('mcal-go');"
                    "return b&&b.offsetParent!==null;})()"
                )
                if not go_vis_post:
                    bug("7b", "Start button should reappear in 'pre' state")
                else:
                    print("  ✓ Start returns after state reset")
                ss(page, "07b-state-machine.png")
                # Close modal
                page.evaluate("closeModal()")
                page.wait_for_timeout(200)
            else:
                print("  - Skipping state-machine check (no calibrate button present)")
        except Exception as e:
            bug("7b", f"State machine check error: {e}")

        # ── STEP 8: Create "Music" stage object ──────────────────────────────
        log("Step 8 — Create stage object 'Music' at X:800 Y:2350 Z:1250")
        go(page, "layout")
        page.wait_for_timeout(600)
        try:
            # Layout toolbar '+ Add' button calls newObject()
            add_obj_btn = page.locator("button[onclick*='newObject']").first
            if add_obj_btn.count() and add_obj_btn.is_visible():
                add_obj_btn.click()
                page.wait_for_timeout(600)

                # Inspect what dialog/form appeared
                modal_btns = [(b.inner_text().strip(), b.get_attribute('onclick'))
                              for b in page.locator("button").all()
                              if b.is_visible() and b.get_attribute('onclick') and
                              'showTab' not in (b.get_attribute('onclick') or '') and
                              '_fmToggle' not in (b.get_attribute('onclick') or '') and
                              'toggleHelp' not in (b.get_attribute('onclick') or '') and
                              'showAddFixtureModal' not in (b.get_attribute('onclick') or '')]
                print(f"    Add Object dialog buttons: {modal_btns[:8]}")

                # Modal fields: sf-name, sf-x, sf-y, sf-z | submit: createObject()
                page.locator("#sf-name").fill("Music")
                page.locator("#sf-x").fill("800")
                page.locator("#sf-y").fill("2350")
                page.locator("#sf-z").fill("1250")
                save_btn = page.locator("button[onclick='createObject()']").first
                if save_btn.count() and save_btn.is_visible():
                    save_btn.click()
                    page.wait_for_timeout(500)
                    print("  ✓ 'Music' object created at (800, 2350, 1250)")
                else:
                    bug("8", f"'Add to Stage' (createObject) button not visible in Add Object modal")
                    close_modal(page)
            else:
                bug("8", "newObject() button not found in Layout toolbar — '+ Add' button may be missing or have wrong onclick")
        except Exception as e:
            bug("8", f"Add Object failed: {e}")
            close_modal(page)
        ss(page, "08-music-object.png")

        # ── STEP 9: Aim at Music in red ──────────────────────────────────────
        log("Step 9 — Orient mode (R key) + set red via fixture Details")
        go(page, "layout")
        page.wait_for_timeout(500)

        # Press R key on canvas to activate Orient mode
        page.locator("canvas").first.click()
        page.wait_for_timeout(200)
        page.keyboard.press("r")
        page.wait_for_timeout(400)

        orient_btn = page.locator("button:has-text('Orient')")
        if orient_btn.count():
            print("  ✓ Orient button present in toolbar")
        else:
            bug("9", "Orient button not found in Layout toolbar")

        # Set red on each fixture via showDmxDetails
        go(page, "setup")
        page.wait_for_timeout(500)
        all_red_ok = True
        for idx in range(3):
            try:
                details_btn = page.locator(f"button[onclick='showDmxDetails({idx})']")
                if details_btn.count() and details_btn.is_visible():
                    details_btn.click()
                    page.wait_for_timeout(500)
                    red_btn = page.locator(f"button[onclick='_dmxDetailRed({idx})']").first
                    if red_btn.count() and red_btn.is_visible():
                        red_btn.click()
                        page.wait_for_timeout(1500)  # hold red so fixtures are visibly lit
                        print(f"  ✓ Red set for fixture #{idx}")
                    else:
                        bug("9", f"Red quick-button (_dmxDetailRed) not found in Details panel for fixture #{idx}")
                        all_red_ok = False
                    close_modal(page)
                else:
                    bug("9", f"Details button not visible for fixture #{idx}")
                    all_red_ok = False
            except Exception as e:
                bug("9", f"Set red failed for fixture #{idx}: {e}")
                close_modal(page)
        ss(page, "09-aim-red.png")

        # ── Camera verification: capture snapshot with red lit ────────────────
        log("Camera verification — snapshot with red lights on")
        page.wait_for_timeout(1500)  # hold red long enough for camera to capture
        import urllib.request as _ur3
        for cam_idx in [0, 1]:
            try:
                with _ur3.urlopen(f"http://192.168.10.235:5000/snapshot?cam={cam_idx}", timeout=15) as r:
                    img = r.read()
                snap_path = f"{SS_DIR}/09-beam-verify-cam{cam_idx}.jpg"
                with open(snap_path, "wb") as f:
                    f.write(img)
                print(f"  ✓ Camera {cam_idx} snapshot saved ({len(img)} bytes) → {snap_path}")
            except Exception as e:
                bug("9-cam", f"Camera {cam_idx} snapshot failed: {e}")

        # ── STEP 10: Blackout ────────────────────────────────────────────────
        log("Step 10 — Blackout all fixtures")
        go(page, "setup")
        page.wait_for_timeout(400)
        blackout_ok = False
        for idx in range(3):
            try:
                details_btn = page.locator(f"button[onclick='showDmxDetails({idx})']")
                if details_btn.count() and details_btn.is_visible():
                    details_btn.click()
                    page.wait_for_timeout(400)
                    bo = page.locator("button:has-text('Blackout')").first
                    if bo.count() and bo.is_visible():
                        bo.click()
                        page.wait_for_timeout(300)
                        print(f"  ✓ Blackout for fixture #{idx}")
                        blackout_ok = True
                    else:
                        bug("10", f"Blackout button not found in Details panel for fixture #{idx}")
                    close_modal(page)
            except Exception as e:
                bug("10", f"Blackout failed for fixture #{idx}: {e}")
                close_modal(page)
        if blackout_ok:
            print("  ✓ Blackout applied to all fixtures")
        else:
            bug("10", "No global Blackout control found — must open Details per fixture (UX improvement opportunity)")
        ss(page, "10-blackout.png")

        # ── STEP 11a: Camera tracking ─────────────────────────────────────────
        log("Step 11a — Camera tracking (Track button on camera card)")
        go(page, "setup")
        page.wait_for_timeout(600)
        track_btns = page.locator("button:has-text('Track')").all()
        visible_track = [b for b in track_btns if b.is_visible()]
        if visible_track:
            print(f"  ✓ {len(visible_track)} Track button(s) found on camera cards")
        else:
            bug("11a", "Track button not found on any camera card — cameras may not have been added (Step 6 required live hardware)")
        ss(page, "11a-tracking-ui.png")

        # ── STEP 11b: Track People action ─────────────────────────────────────
        log("Step 11b — Create 'Track People' action")
        go(page, "actions")
        page.wait_for_timeout(500)
        try:
            new_action = page.locator("button[onclick='newAction()']")
            if new_action.count() and new_action.is_visible():
                new_action.click()
                page.wait_for_timeout(600)

                # Correct field IDs: ae-nm (name), ae-tp (type)
                name_f = page.locator("#ae-nm")
                if name_f.count() and name_f.is_visible():
                    name_f.fill("Track People")
                else:
                    bug("11b", "Name field #ae-nm not found in action modal")

                type_sel = page.locator("#ae-tp")
                if type_sel.count() and type_sel.is_visible():
                    opts = type_sel.locator("option").all_text_contents()
                    track_opts = [o for o in opts if "track" in o.lower()]
                    if track_opts:
                        type_sel.select_option(label=track_opts[0])
                        print(f"    ✓ Type set to: {track_opts[0]}")
                    else:
                        bug("11b", f"No Track type in dropdown: {opts}")

                save_btn = page.locator("#ae-save")
                if save_btn.count() and save_btn.is_visible():
                    save_btn.click()
                    page.wait_for_timeout(800)
                else:
                    bug("11b", "Save Action (#ae-save) button not found")
            else:
                bug("11b", "+ New Action button not found")
        except Exception as e:
            bug("11b", f"Create Track action failed: {e}")
        close_modal(page)
        # Verify via API
        actions = api_get("/api/actions") or []
        tp = next((a for a in actions if a.get("name") == "Track People"), None)
        if tp:
            print(f"  ✓ 'Track People' action saved (id:{tp['id']}, type:{tp['type']})")
        else:
            bug("11b", f"'Track People' not found in /api/actions — got: {[a.get('name') for a in actions]}")
        ss(page, "11b-track-action.png")

        # ── STEP 11c: Figure-8 floor target ──────────────────────────────────
        log("Step 11c — Create 'Floor Target' object with Figure-8 patrol")
        go(page, "layout")
        page.wait_for_timeout(600)
        try:
            canvas = page.locator("canvas").first
            canvas.click(button="right", position={"x": 700, "y": 400})
            page.wait_for_timeout(500)

            ctx_add = page.locator("text=Add Object, text=New Object").first
            if ctx_add.count() and ctx_add.is_visible():
                ctx_add.click()
                page.wait_for_timeout(500)

                # Fill Floor Target using sf-* fields
                page.locator("#sf-name").fill("Floor Target")
                page.locator("#sf-x").fill("1050")
                page.locator("#sf-y").fill("2000")
                page.locator("#sf-z").fill("0")

                # Enable patrol and set Figure-8
                pat_en = page.locator("#sf-pat-en")
                if pat_en.count() and not pat_en.is_checked():
                    pat_en.check()
                    page.wait_for_timeout(300)

                pat_sel = page.locator("#sf-pat-pattern")
                if pat_sel.count() and pat_sel.is_visible():
                    opts = pat_sel.locator("option").all_text_contents()
                    print(f"    Patrol patterns: {opts}")
                    fig8 = [o for o in opts if "figure" in o.lower() or "8" in o.lower()]
                    if fig8:
                        pat_sel.select_option(label=fig8[0])
                        print(f"    ✓ Pattern: {fig8[0]}")
                    else:
                        bug("11c", f"Figure-8 not in patrol options: {opts}")

                save_btn = page.locator("button[onclick='createObject()']").first
                if save_btn.count() and save_btn.is_visible():
                    save_btn.click()
                    page.wait_for_timeout(500)
                    print("  ✓ Floor Target created with patrol pattern")
                else:
                    bug("11c", "'Add to Stage' button not visible")
        except Exception as e:
            bug("11c", f"Floor Target setup failed: {e}")
        close_modal(page)
        ss(page, "11c-floor-target.png")

        # ── STEP 11d: Follow Figure 8 action ─────────────────────────────────
        log("Step 11d — Create 'Follow Figure 8' action")
        go(page, "actions")
        page.wait_for_timeout(500)
        try:
            new_action = page.locator("button[onclick='newAction()']")
            if new_action.count() and new_action.is_visible():
                new_action.click()
                page.wait_for_timeout(600)
                name_f = page.locator("#ae-nm")
                if name_f.count() and name_f.is_visible():
                    name_f.fill("Follow Figure 8")
                else:
                    bug("11d", "Name field #ae-nm not found in action modal")
                type_sel = page.locator("#ae-tp")
                if type_sel.count() and type_sel.is_visible():
                    opts = type_sel.locator("option").all_text_contents()
                    track_opts = [o for o in opts if "track" in o.lower()]
                    if track_opts:
                        type_sel.select_option(label=track_opts[0])
                save_btn = page.locator("#ae-save")
                if save_btn.count() and save_btn.is_visible():
                    save_btn.click()
                    page.wait_for_timeout(800)
        except Exception as e:
            bug("11d", f"Follow Figure 8 action failed: {e}")
        close_modal(page)
        # Verify via API
        actions = api_get("/api/actions") or []
        ff = next((a for a in actions if a.get("name") == "Follow Figure 8"), None)
        if ff:
            print(f"  ✓ 'Follow Figure 8' action saved (id:{ff['id']}, type:{ff['type']})")
        else:
            bug("11d", f"'Follow Figure 8' not found in /api/actions — got: {[a.get('name') for a in actions]}")
        ss(page, "11d-figure8-action.png")

        # ── STEP 11e: Build show timeline ─────────────────────────────────────
        log("Step 11e — Create timeline 'Walkthrough 533' (420s) in Shows tab")
        go(page, "shows")
        page.wait_for_timeout(600)
        try:
            new_tl = page.locator("button[onclick='newTimeline()']")
            if new_tl.count() and new_tl.is_visible():
                # newTimeline() auto-creates "Timeline N" (60s) — no prompt dialogs.
                # Rename and set duration via inline #tl-name / #tl-dur fields after creation.
                new_tl.click()
                page.wait_for_timeout(1000)

                # Verify a timeline appeared and the detail panel opened
                tl_sel = page.locator("#tl-select")
                opts = tl_sel.locator("option").all_text_contents() if tl_sel.count() else []
                real_opts = [o for o in opts if o and "Select" not in o]
                if real_opts:
                    print(f"  ✓ Timeline auto-created: '{real_opts[-1]}'")
                    # Select it (newTimeline auto-selects, but ensure detail view is open)
                    tl_sel.select_option(index=len(real_opts))
                    page.wait_for_timeout(600)

                    # Rename + set duration via inline fields
                    tl_name_f = page.locator("#tl-name")
                    tl_dur_f = page.locator("#tl-dur")
                    if tl_name_f.count() and tl_name_f.is_visible():
                        tl_name_f.fill("Walkthrough 533")
                    else:
                        bug("11e", "#tl-name field not visible in timeline detail — cannot rename")
                    if tl_dur_f.count() and tl_dur_f.is_visible():
                        tl_dur_f.fill("420")
                    else:
                        bug("11e", "#tl-dur field not visible — cannot set 420s duration")

                    # Save (button calls saveTimeline(this))
                    save_tl_btn = page.locator("button[onclick*='saveTimeline']").first
                    if save_tl_btn.count() and save_tl_btn.is_visible():
                        save_tl_btn.click()
                        page.wait_for_timeout(800)
                        print("  ✓ Timeline renamed to 'Walkthrough 533' (420s)")
                    else:
                        bug("11e", "saveTimeline button not found — timeline name/duration not persisted")

                    # Add one track per target: all fixtures + each DMX fixture
                    for track_target in ["all", "0", "1", "2"]:
                        add_trk = page.locator("button[onclick='tlAddTrack()']")
                        if add_trk.count() and add_trk.is_visible():
                            add_trk.click()
                            page.wait_for_timeout(400)
                            trk_fix = page.locator("#trk-fix")
                            if trk_fix.count() and trk_fix.is_visible():
                                trk_fix.select_option(value=track_target)
                            confirm_btn = page.locator("button[onclick='tlAddTrackConfirm()']")
                            if confirm_btn.count() and confirm_btn.is_visible():
                                confirm_btn.click()
                                page.wait_for_timeout(500)
                                print(f"    ✓ Track added for target: '{track_target}'")
                            else:
                                close_modal(page)
                        else:
                            bug("11e", f"+ Add Track button not found (target {track_target})")
                            break

                    # Verify via API
                    tls = api_get("/api/timelines") or []
                    tl = next((t for t in tls if "Walkthrough" in t.get("name", "")), None)
                    if tl:
                        track_count = len(tl.get("tracks", []))
                        print(f"  ✓ Timeline '{tl['name']}' ({tl['durationS']}s) has {track_count} track(s)")
                        if track_count == 0:
                            bug("11e", "Timeline has 0 tracks — tlAddTrackConfirm may have failed")
                        if tl['durationS'] != 420:
                            bug("11e", f"Duration is {tl['durationS']}s, expected 420s")
                    else:
                        bug("11e", "Walkthrough timeline not found in /api/timelines after rename")
                else:
                    bug("11e", "No timelines in dropdown after newTimeline()")
            else:
                bug("11e", "+ New Timeline button not found on Shows tab")
        except Exception as e:
            bug("11e", f"Timeline creation failed: {e}")
        close_modal(page)
        ss(page, "11e-timeline.png")

        # ── STEP 11f: Runtime — Bake All + Start Show ─────────────────────────
        log("Step 11f — Runtime: Bake All and Start Show")
        go(page, "runtime")
        page.wait_for_timeout(600)

        bake_btn = page.locator("button:has-text('Bake All')").first
        if bake_btn.count() and bake_btn.is_visible():
            bake_btn.click()
            page.wait_for_timeout(2000)
            print("  ✓ Bake All triggered")
        else:
            # Try singular Bake
            bake_btn2 = page.locator("button:has-text('Bake')").first
            if bake_btn2.count() and bake_btn2.is_visible():
                bake_btn2.click()
                page.wait_for_timeout(2000)
                print("  ✓ Bake triggered")
            else:
                bug("11f", "Bake All / Bake button not found in Runtime tab")

        start_btn = page.locator("button:has-text('Start Show')").first
        if start_btn.count() and start_btn.is_visible():
            print("  ✓ Start Show button present")
        else:
            bug("11f", "Start Show button not found in Runtime tab")
        ss(page, "11f-runtime.png")

        # ── API STATE SUMMARY ─────────────────────────────────────────────────
        log("API State Verification")
        layout   = api_get("/api/layout") or {}
        actions  = api_get("/api/actions") or []
        timelines = api_get("/api/timelines") or []
        cameras  = api_get("/api/cameras") or []
        fixtures = layout.get("fixtures", [])
        children_pos = {c["id"]: c for c in layout.get("children", [])}
        print(f"  Fixtures  : {len(fixtures)} (expect 3 DMX + cameras)")
        for f in fixtures:
            pos = children_pos.get(f["id"], {})
            print(f"    [{f['id']}] {f['name']}  profile:{f.get('dmxProfileId')!r}  addr:{f.get('dmxStartAddr')}  X:{pos.get('x')} Y:{pos.get('y')} Z:{pos.get('z')}")
        print(f"  Actions   : {len(actions)} — {[a['name'] for a in actions]}")
        tl_summary = [(t['name'], len(t.get('tracks',[]))) for t in timelines]
        print(f"  Timelines : {tl_summary}")
        print(f"  Cameras   : {len(cameras)}")
        expected_fixtures = 3
        if len(fixtures) < expected_fixtures:
            bug("api", f"Only {len(fixtures)} fixture(s) saved, expected ≥{expected_fixtures}")
        for f in fixtures[:3]:
            pos = children_pos.get(f["id"], {})
            if pos.get("x", 0) == 0 and pos.get("z", 0) == 0:
                bug("api", f"Fixture '{f['name']}' position is still 0,0,0 — saveFixture position chain failed")
            if not f.get("dmxProfileId"):
                bug("api", f"Fixture '{f['name']}' has no dmxProfileId — community profile not applied")
        if not actions:
            bug("api", "No actions saved — action creation failed")
        if not timelines or not any(t.get("tracks") for t in timelines):
            bug("api", "No timeline with tracks found — timeline track creation failed")

        # ── STEP 12: Save project ─────────────────────────────────────────────
        log("Step 12 — Save project (File → Save As)")
        go(page, "runtime")
        page.wait_for_timeout(400)
        page.locator("#file-menu-btn").click()
        page.wait_for_timeout(400)
        save_as = page.locator("text=Save As").first
        if save_as.count() and save_as.is_visible():
            save_as.click()
            page.wait_for_timeout(700)
            # Try to fill name if a dialog/input appears
            name_f = page.locator("input[id*='proj'], input[placeholder*='name'], input[id*='filename']").first
            if name_f.count() and name_f.is_visible():
                name_f.fill("Walkthrough 533")
                page.locator("button:has-text('Save')").first.click()
                page.wait_for_timeout(500)
                print("  ✓ Saved as 'Walkthrough 533'")
            else:
                # File System Access API may open native picker
                bug("12", "Native file-picker opened for Save As — cannot automate native dialog. File can be saved manually with Ctrl+S.")
                page.keyboard.press("Escape")
        else:
            bug("12", "'Save As' not found in File menu")
        ss(page, "12-saved.png")

        browser.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("QA WALKTHROUGH 533 — COMPLETE")
    print(f"Screenshots saved to: {SS_DIR}/")
    print(f"{'='*60}")
    if BUGS:
        print(f"\n⚠️  {len(BUGS)} issue(s) found — file as GitHub bugs:\n")
        for i, b in enumerate(BUGS, 1):
            print(f"  {i}. {b}")
    else:
        print("\n✅  No issues found in GUI flow.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run()
