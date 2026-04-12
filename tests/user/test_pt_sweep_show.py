"""User test: Create 3 PT Move actions via SPA UI, build timeline, run show.

Pure UI interaction — no API calls. Handles prompt() dialogs.

Usage: python tests/user/test_pt_sweep_show.py [base_url] [shot_dir]
"""
import sys, os, time
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
SHOT_DIR = sys.argv[2] if len(sys.argv) > 2 else "/mnt/d/temp/live-test-session"
PROJECT = os.path.join(os.path.dirname(__file__), "basement", "basement.slyshow")
sn = 599
issues = []

def ss(page, name):
    global sn; sn += 1
    p = os.path.join(SHOT_DIR, f"{sn:03d}-{name}.png")
    page.screenshot(path=p); print(f"  [{sn}] {name}"); return p

def tab(page, t):
    close_modal(page)
    page.locator(f"#n-{t.lower()}").click(timeout=5000); time.sleep(1.5)

def close_modal(page):
    page.evaluate("if(typeof closeModal==='function')closeModal()")
    time.sleep(0.3)

def validate(page, text, label):
    found = page.locator(f"text='{text}'").count()
    ok = found > 0
    print(f"  Validate {label}: {'✓' if ok else 'FAIL — not found'}")
    if not ok: issues.append(f"{label}: '{text}' not found")
    return ok

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()

        # Dialog handler for prompt() — provides timeline name + duration
        prompts = iter(["PT Sweep Test", "15"])
        page.on("dialog", lambda d: d.accept(next(prompts, "")))

        page.goto(BASE, timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)
        time.sleep(2)

        # ══ 1. Dashboard ══
        print("=== 1. Dashboard ===")
        ss(page, "01-dashboard")

        # ══ 2. Load project ══
        print("=== 2. Load project ===")
        # Read project file and load via SPA's internal import (same as Open Show)
        with open(PROJECT) as f:
            import json; proj_data = f.read()
        page.evaluate("""(data) => {
            var xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/project/import', false);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.send(data);
        }""", proj_data)
        page.reload(); time.sleep(2)
        ss(page, "02-project-loaded")

        # ══ 3. Actions tab ══
        print("=== 3. Actions tab ===")
        tab(page, "actions")
        ss(page, "03-actions-tab")

        # ══ 4. Create MH1 Blue Sweep ══
        print("=== 4. MH1 Blue Sweep ===")
        page.locator("button:has-text('New Action')").first.click(); time.sleep(0.8)
        page.locator("#ae-nm").fill("MH1 Blue Sweep")
        page.locator("#ae-scope").select_option(value="performer-selected"); time.sleep(0.3)
        page.locator("label:has-text('Sly MH 1') input[type='checkbox']").first.check()
        page.locator("#ae-tp").select_option(value="15"); time.sleep(0.3)
        page.locator("#ae-cl").evaluate("el => el.value = '#0000ff'")
        page.locator("#ae-cl").dispatch_event("input")
        page.locator("#ae-dimmer").fill("255")
        page.locator("#ae-pt-sx").fill("500"); page.locator("#ae-pt-sy").fill("3000"); page.locator("#ae-pt-sz").fill("0")
        page.locator("#ae-pt-ex").fill("5000"); page.locator("#ae-pt-ey").fill("3000"); page.locator("#ae-pt-ez").fill("0")
        page.locator("#ae-pt-spd").fill("5")
        ss(page, "04a-blue-filled")
        # Scroll modal and click Save Action
        page.locator("button:has-text('Save Action')").scroll_into_view_if_needed()
        page.locator("button:has-text('Save Action')").click(force=True); time.sleep(1)
        ss(page, "04b-blue-saved")
        validate(page, "MH1 Blue Sweep", "Action 1")

        # ══ 5. Create MH2 Red Sweep ══
        print("=== 5. MH2 Red Sweep ===")
        page.locator("button:has-text('New Action')").first.click(); time.sleep(0.8)
        page.locator("#ae-nm").fill("MH2 Red Sweep")
        page.locator("#ae-scope").select_option(value="performer-selected"); time.sleep(0.3)
        page.locator("label:has-text('Sly MH 2') input[type='checkbox']").first.check()
        page.locator("#ae-tp").select_option(value="15"); time.sleep(0.3)
        page.locator("#ae-cl").evaluate("el => el.value = '#ff0000'")
        page.locator("#ae-cl").dispatch_event("input")
        page.locator("#ae-dimmer").fill("255")
        page.locator("#ae-pt-sx").fill("500"); page.locator("#ae-pt-sy").fill("3000"); page.locator("#ae-pt-sz").fill("0")
        page.locator("#ae-pt-ex").fill("5000"); page.locator("#ae-pt-ey").fill("3000"); page.locator("#ae-pt-ez").fill("0")
        page.locator("#ae-pt-spd").fill("5")
        ss(page, "05a-red-filled")
        page.locator("button:has-text('Save Action')").scroll_into_view_if_needed()
        page.locator("button:has-text('Save Action')").click(force=True); time.sleep(1)
        ss(page, "05b-red-saved")
        validate(page, "MH2 Red Sweep", "Action 2")

        # ══ 6. Create Both White Sweep ══
        print("=== 6. Both White Sweep ===")
        page.locator("button:has-text('New Action')").first.click(); time.sleep(0.8)
        page.locator("#ae-nm").fill("Both White Sweep")
        # scope stays "All Fixtures" (default)
        page.locator("#ae-tp").select_option(value="15"); time.sleep(0.3)
        page.locator("#ae-cl").evaluate("el => el.value = '#ffffff'")
        page.locator("#ae-cl").dispatch_event("input")
        page.locator("#ae-dimmer").fill("255")
        page.locator("#ae-pt-sx").fill("500"); page.locator("#ae-pt-sy").fill("3000"); page.locator("#ae-pt-sz").fill("0")
        page.locator("#ae-pt-ex").fill("5000"); page.locator("#ae-pt-ey").fill("3000"); page.locator("#ae-pt-ez").fill("0")
        page.locator("#ae-pt-spd").fill("5")
        ss(page, "06a-white-filled")
        page.locator("button:has-text('Save Action')").scroll_into_view_if_needed()
        page.locator("button:has-text('Save Action')").click(force=True); time.sleep(1)
        ss(page, "06b-white-saved")
        validate(page, "Both White Sweep", "Action 3")

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)"); time.sleep(0.3)
        ss(page, "06c-all-actions")

        # ══ 7. Shows — New Timeline ══
        print("=== 7. New Timeline ===")
        tab(page, "shows")
        ss(page, "07a-shows-tab")
        # prompt() handler provides "PT Sweep Test" and "15"
        page.locator("button:has-text('New Timeline')").first.click(); time.sleep(1.5)
        ss(page, "07b-timeline-created")
        validate(page, "PT Sweep Test", "Timeline")

        # ══ 8. Add Track ══
        print("=== 8. Add Track ===")
        page.locator("button:has-text('Add Track')").first.click(); time.sleep(0.8)
        ss(page, "08a-add-track-modal")
        page.locator("#trk-fix").select_option(value="all")
        # Click the Add button inside the modal
        page.locator("#modal button:has-text('Add')").click(force=True); time.sleep(1)
        ss(page, "08b-track-added")

        # ══ 9. Add Clip 1 — Blue at 0s ══
        print("=== 9. Clip 1 — Blue 0s ===")
        page.locator("text='+ clip'").first.click(force=True); time.sleep(0.8)
        ss(page, "09a-clip-modal")
        # Select blue from dropdown
        clip_sel = page.locator("#clip-fx")
        for opt in clip_sel.locator("option").all():
            if "blue" in (opt.text_content() or "").lower():
                clip_sel.select_option(value=opt.get_attribute("value")); break
        page.locator("#clip-start").fill("0")
        page.locator("#clip-dur").fill("5")
        ss(page, "09b-clip1-filled")
        page.locator("#modal button:has-text('Add Clip')").click(force=True); time.sleep(1)
        ss(page, "09c-clip1-added")

        # ══ 10. Add Clip 2 — Red at 5s ══
        print("=== 10. Clip 2 — Red 5s ===")
        page.locator("text='+ clip'").first.click(force=True); time.sleep(0.8)
        clip_sel = page.locator("#clip-fx")
        for opt in clip_sel.locator("option").all():
            if "red" in (opt.text_content() or "").lower():
                clip_sel.select_option(value=opt.get_attribute("value")); break
        page.locator("#clip-start").fill("5")
        page.locator("#clip-dur").fill("5")
        ss(page, "10a-clip2-filled")
        page.locator("#modal button:has-text('Add Clip')").click(force=True); time.sleep(1)
        ss(page, "10b-clip2-added")

        # ══ 11. Add Clip 3 — White at 10s ══
        print("=== 11. Clip 3 — White 10s ===")
        page.locator("text='+ clip'").first.click(force=True); time.sleep(0.8)
        clip_sel = page.locator("#clip-fx")
        for opt in clip_sel.locator("option").all():
            if "white" in (opt.text_content() or "").lower():
                clip_sel.select_option(value=opt.get_attribute("value")); break
        page.locator("#clip-start").fill("10")
        page.locator("#clip-dur").fill("5")
        ss(page, "11a-clip3-filled")
        page.locator("#modal button:has-text('Add Clip')").click(force=True); time.sleep(1)
        close_modal(page)
        ss(page, "11b-timeline-with-3-clips")

        # ══ 12. Bake ══
        print("=== 12. Bake ===")
        page.locator("button:has-text('Bake')").first.click(force=True); time.sleep(3)
        close_modal(page)
        ss(page, "12-baked")

        # ══ 13. Runtime — Start Show ══
        print("=== 13. Start Show ===")
        tab(page, "runtime")
        time.sleep(1)
        ss(page, "13a-runtime")
        page.locator("button:has-text('Start Show')").first.click(force=True); time.sleep(1)
        ss(page, "13b-started")

        # ══ 14. Playback captures ══
        print("=== 14. Playback ===")
        time.sleep(2); ss(page, "14a-3s")
        time.sleep(5); ss(page, "14b-8s")
        time.sleep(5); ss(page, "14c-13s")
        time.sleep(3); ss(page, "14d-done")

        try:
            page.evaluate("if(typeof _rtStopShow==='function')_rtStopShow()")
        except: pass
        time.sleep(1)
        ss(page, "14e-stopped")

        # ══ Summary ══
        print(f"\n{'='*50}")
        print(f"Issues: {len(issues)}")
        for i, issue in enumerate(issues): print(f"  [{i+1}] {issue}")
        browser.close()

if __name__ == "__main__":
    main()
