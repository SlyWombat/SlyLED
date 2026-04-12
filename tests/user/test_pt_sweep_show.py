"""User test: Create 3 PT Move actions via SPA UI, build timeline, run show.

Step-by-step UI interaction with screenshots at every step.
PT Move type uses stage coordinates (mm) directly — no pan/tilt conversion needed.

Actions:
  1. MH 1 blue sweep:  (500,3000,0) → (5000,3000,0)
  2. MH 2 red sweep:   same path
  3. Both white sweep:  same path

Usage: python tests/user/test_pt_sweep_show.py [base_url] [shot_dir]
"""
import sys, os, time, json, urllib.request
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
SHOT_DIR = sys.argv[2] if len(sys.argv) > 2 else "/mnt/d/temp/live-test-session"
shot_num = 549

def shot(page, name):
    global shot_num
    shot_num += 1
    path = os.path.join(SHOT_DIR, f"{shot_num:03d}-{name}.png")
    page.screenshot(path=path)
    print(f"  [{shot_num}] {name}")
    return path

def click_tab(page, name):
    tab_map = {"Dashboard":"n-dash","Setup":"n-setup","Layout":"n-layout",
               "Actions":"n-actions","Shows":"n-shows","Runtime":"n-runtime",
               "Settings":"n-settings","Firmware":"n-firmware"}
    tid = tab_map.get(name, f"n-{name.lower()}")
    page.locator(f"#{tid}").click(timeout=5000)
    time.sleep(1.5)

def fill(page, sel, value):
    el = page.locator(sel).first
    if el.count() > 0 and el.is_visible():
        el.fill(str(value))
        return True
    return False

def create_action(page, name, color_hex, scope, fixture_names, start_xyz, end_xyz, speed_s):
    """Create a PT Move action through the SPA modal."""
    print(f"\n  Creating: {name}")

    # Click + New Action
    page.locator("button:has-text('New Action')").first.click()
    time.sleep(0.8)

    # Name
    fill(page, "#ae-nm", name)

    # Scope — select "Selected Fixtures" if specific fixtures needed
    if scope == "selected":
        page.locator("#ae-scope").select_option(value="performer-selected")
        time.sleep(0.5)
        # Check fixture checkboxes
        for fname in fixture_names:
            cb = page.locator(f"label:has-text('{fname}') input[type='checkbox']").first
            if cb.count() > 0:
                cb.check()
                print(f"    Fixture checked: {fname}")
    # else "All Fixtures" is default

    # Type — Pan/Tilt Move (15)
    page.locator("#ae-tp").select_option(value="15")
    time.sleep(0.5)

    # Color
    page.locator("#ae-cl").evaluate(f"el => el.value = '{color_hex}'")
    page.locator("#ae-cl").dispatch_event("input")

    # Dimmer
    fill(page, "#ae-dimmer", 255)

    # Start position (mm)
    fill(page, "#ae-pt-sx", start_xyz[0])
    fill(page, "#ae-pt-sy", start_xyz[1])
    fill(page, "#ae-pt-sz", start_xyz[2])

    # End position (mm)
    fill(page, "#ae-pt-ex", end_xyz[0])
    fill(page, "#ae-pt-ey", end_xyz[1])
    fill(page, "#ae-pt-ez", end_xyz[2])

    # Speed
    fill(page, "#ae-pt-spd", speed_s)

    return True

def save_action(page):
    """Scroll modal to bottom and click Save Action."""
    # Scroll modal content to reveal Save button
    page.evaluate("""() => {
        const modal = document.querySelector('.modal-body') || document.querySelector('.modal-content')
            || document.querySelector('.modal');
        if (modal) modal.scrollTop = modal.scrollHeight;
    }""")
    time.sleep(0.3)
    # Click Save Action button — use evaluate to bypass visibility check
    page.evaluate("""() => {
        const btns = document.querySelectorAll('button');
        for (const b of btns) {
            if (b.textContent.includes('Save Action')) { b.click(); return true; }
        }
        // Fallback: _fmSave function
        if (typeof _aeOk === 'function') { _aeOk(); return true; }
        return false;
    }""")
    time.sleep(1)


def main():
    issues = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        js_errors = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))

        page.goto(BASE, timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)
        time.sleep(2)

        # ── STEP 1: Actions tab ──
        print("=== STEP 1: Open Actions tab ===")
        click_tab(page, "Actions")
        shot(page, "01-actions-tab-initial")

        # ── STEP 2: Create Action 1 — MH1 Blue Sweep ──
        print("=== STEP 2: Action 1 — MH1 Blue Sweep ===")
        create_action(page,
            name="MH1 Blue Sweep",
            color_hex="#0000ff",
            scope="selected",
            fixture_names=["Sly MH 1"],
            start_xyz=[500, 3000, 0],
            end_xyz=[5000, 3000, 0],
            speed_s=5)
        shot(page, "02a-action1-filled")

        # Save
        save_action(page)
        shot(page, "02b-action1-saved")

        # ── STEP 3: Create Action 2 — MH2 Red Sweep ──
        print("=== STEP 3: Action 2 — MH2 Red Sweep ===")
        create_action(page,
            name="MH2 Red Sweep",
            color_hex="#ff0000",
            scope="selected",
            fixture_names=["Sly MH 2"],
            start_xyz=[500, 3000, 0],
            end_xyz=[5000, 3000, 0],
            speed_s=5)
        shot(page, "03a-action2-filled")

        save_action(page)
        shot(page, "03b-action2-saved")

        # ── STEP 4: Create Action 3 — Both White Sweep ──
        print("=== STEP 4: Action 3 — Both White Sweep ===")
        create_action(page,
            name="Both White Sweep",
            color_hex="#ffffff",
            scope="all",
            fixture_names=[],
            start_xyz=[500, 3000, 0],
            end_xyz=[5000, 3000, 0],
            speed_s=5)
        shot(page, "04a-action3-filled")

        save_action(page)
        shot(page, "04b-action3-saved")

        # Scroll to see all actions
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
        shot(page, "04c-all-actions")

        # ── STEP 5: Shows tab — Create timeline ──
        print("=== STEP 5: Shows tab — Create timeline ===")
        click_tab(page, "Shows")
        shot(page, "05a-shows-tab")

        # Click + New Timeline
        page.locator("button:has-text('New Timeline')").first.click()
        time.sleep(1)
        shot(page, "05b-new-timeline")

        # Select it from the dropdown
        tl_select = page.locator("select").first
        if tl_select.count() > 0:
            opts = tl_select.locator("option").all_text_contents()
            print(f"  Timeline options: {opts}")
            # Pick the last one (newest)
            if len(opts) > 1:
                tl_select.select_option(index=len(opts)-1)
                time.sleep(1)
        shot(page, "05c-timeline-selected")

        # ── STEP 6: Add clips to timeline via API ──
        print("=== STEP 6: Add clips to timeline (API) ===")
        try:
            with urllib.request.urlopen(f"{BASE}/api/timelines", timeout=5) as r:
                timelines = json.loads(r.read())
            tl_id = timelines[-1]["id"] if timelines else None
            print(f"  Timeline ID: {tl_id}")

            with urllib.request.urlopen(f"{BASE}/api/actions", timeout=5) as r:
                actions = json.loads(r.read())
            a_ids = {}
            for a in actions:
                nm = a.get("name", "").lower()
                if "blue" in nm: a_ids["blue"] = a["id"]
                elif "red" in nm: a_ids["red"] = a["id"]
                elif "white" in nm: a_ids["white"] = a["id"]
            print(f"  Action IDs: {a_ids}")

            if tl_id and len(a_ids) == 3:
                with urllib.request.urlopen(f"{BASE}/api/timelines/{tl_id}", timeout=5) as r:
                    tl_data = json.loads(r.read())
                if not tl_data.get("tracks"):
                    tl_data["tracks"] = [{"id": 0, "name": "Track 1", "clips": []}]
                tl_data["name"] = "PT Sweep Test"
                tl_data["tracks"][0]["clips"] = [
                    {"actionId": a_ids["blue"],  "startMs": 0,     "durationMs": 5000, "fixtureIds": [2]},
                    {"actionId": a_ids["red"],   "startMs": 5000,  "durationMs": 5000, "fixtureIds": [7]},
                    {"actionId": a_ids["white"], "startMs": 10000, "durationMs": 5000, "fixtureIds": [2, 7]},
                ]
                tl_data["durationMs"] = 15000
                body = json.dumps(tl_data).encode()
                req = urllib.request.Request(f"{BASE}/api/timelines/{tl_id}", data=body, method="PUT",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    print(f"  Updated: {json.loads(r.read()).get('ok')}")
            else:
                print(f"  ISSUE: missing timeline or actions")
                issues.append("Missing timeline/actions for clip setup")
        except Exception as e:
            print(f"  ISSUE: {e}")
            issues.append(f"Timeline setup: {e}")

        page.reload()
        time.sleep(2)
        click_tab(page, "Shows")
        tl_select = page.locator("select").first
        if tl_select.count() > 0:
            opts = tl_select.locator("option").all_text_contents()
            for opt in opts:
                if "sweep" in opt.lower() or "pt" in opt.lower():
                    tl_select.select_option(label=opt)
                    break
            time.sleep(1)
        shot(page, "06-timeline-with-clips")

        # ── STEP 7: Runtime — Bake ──
        print("=== STEP 7: Runtime — Bake ===")
        click_tab(page, "Runtime")
        time.sleep(1)
        shot(page, "07a-runtime-playlist")

        # Bake each timeline
        try:
            with urllib.request.urlopen(f"{BASE}/api/timelines", timeout=5) as r:
                timelines = json.loads(r.read())
            for tl in timelines:
                req = urllib.request.Request(f"{BASE}/api/timelines/{tl['id']}/bake",
                    data=b'{}', headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    print(f"  Bake {tl.get('name','?')}: {json.loads(r.read()).get('ok')}")
        except Exception as e:
            print(f"  Bake issue: {e}")
            issues.append(f"Bake: {e}")
        time.sleep(2)
        shot(page, "07b-runtime-baked")

        # ── STEP 8: Start Show ──
        print("=== STEP 8: Start Show ===")
        try:
            req = urllib.request.Request(f"{BASE}/api/show/start",
                data=b'{}', headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                start_result = json.loads(r.read())
            print(f"  Start: {start_result}")
        except Exception as e:
            print(f"  Start issue: {e}")
            issues.append(f"Show start: {e}")

        # ── STEP 9: Capture show at key moments ──
        print("=== STEP 9: Show playback screenshots ===")
        time.sleep(2)
        shot(page, "08a-show-2s-blue-sweep")

        time.sleep(4)
        shot(page, "08b-show-6s-red-sweep-start")

        time.sleep(3)
        shot(page, "08c-show-9s-red-sweep-mid")

        time.sleep(3)
        shot(page, "08d-show-12s-white-sweep")

        time.sleep(3)
        shot(page, "08e-show-15s-end")

        # Stop
        try:
            req = urllib.request.Request(f"{BASE}/api/show/stop",
                data=b'{}', headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                print(f"  Stop: {json.loads(r.read())}")
        except: pass
        time.sleep(1)
        shot(page, "08f-show-stopped")

        # ── JS errors ──
        if js_errors:
            print(f"\n=== JS ERRORS ({len(js_errors)}) ===")
            for e in js_errors[:5]:
                print(f"  {e[:200]}")
                issues.append(f"JS: {e[:150]}")

        # ── Summary ──
        print(f"\n{'='*50}")
        print(f"Issues: {len(issues)}")
        for i, issue in enumerate(issues):
            print(f"  [{i+1}] {issue}")

        browser.close()

if __name__ == "__main__":
    main()
