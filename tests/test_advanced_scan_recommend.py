"""Live Playwright verification of #594 Advanced Scan recommendation.

Opens the running SlyLED v1.5.50 on port 8080, navigates to Setup,
opens the Advanced Scan card, waits for the ZoeDepth availability probe
to resolve, then screenshots the modal and verifies which option carries
the 'Recommended' badge. Reports what the UI actually showed plus the
rationale the code used to pick it.
"""
import os, sys, json
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
OUT = "/mnt/d/temp/live-test-session"

passed = failed = 0
def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1400, 'height': 950})
    page.on("pageerror", lambda e: print("  PAGE ERROR:", e))
    page.on("console", lambda m: (print("  JS:", m.text) if m.type == "error" else None))

    print("Loading SlyLED…")
    page.goto(BASE)
    page.wait_for_timeout(2500)

    # Setup tab
    page.click('#n-setup')
    page.wait_for_timeout(1200)

    # Scroll into the Point Cloud / Scan section. The Advanced Scan button
    # lives in one of three fixture cards — easiest: dispatch directly.
    print("Opening Advanced Scan modal…")
    exists = page.evaluate("() => typeof _pcAdvancedScan === 'function'")
    check("_pcAdvancedScan function exists", exists)
    if exists:
        page.evaluate("_pcAdvancedScan()")
        page.wait_for_timeout(500)

    modal_visible = page.evaluate(
        "() => document.getElementById('modal') && document.getElementById('modal').style.display !== 'none'"
    )
    check("Modal opened", modal_visible)

    # Grab pre-probe state (should show 'Checking host availability…').
    pre_note = page.evaluate(
        "() => { var n=document.querySelector('.pcadv-zoe-note'); return n?n.textContent:null; }"
    )
    print(f"  pre-probe note: {pre_note!r}")

    # Wait for the zoedepth availability probe round-trip to resolve.
    page.wait_for_function(
        "() => { var n=document.querySelector('.pcadv-zoe-note'); "
        "return n && !/Checking/.test(n.textContent); }",
        timeout=5000,
    )
    page.wait_for_timeout(200)

    post_note = page.evaluate(
        "() => { var n=document.querySelector('.pcadv-zoe-note'); return n?n.textContent:null; }"
    )
    print(f"  post-probe note: {post_note!r}")

    # Which radio is now checked?
    checked = page.evaluate(
        "() => { var r=document.querySelector('input[name=pcmethod]:checked'); return r?r.value:null; }"
    )
    print(f"  checked radio: {checked!r}")

    # Which option carries the Recommended badge?
    badges = page.evaluate("""() => {
        var out = [];
        document.querySelectorAll('.pcadv-rec-badge').forEach(function(b){
            var lbl = b.closest('label');
            out.push(lbl ? lbl.id : '(no-label)');
        });
        return out;
    }""")
    print(f"  recommend badges on: {badges}")

    # Collect per-option status so we can explain the recommendation.
    opts = page.evaluate("""() => {
        function txt(id){
            var el = document.getElementById(id);
            if(!el) return null;
            var rad = el.querySelector('input[type=radio]');
            return {
                id: id,
                checked: !!(rad && rad.checked),
                disabled: !!(rad && rad.disabled),
                recommended: !!el.querySelector('.pcadv-rec-badge'),
                text: el.textContent.replace(/\\s+/g,' ').trim()
            };
        }
        return ['pcadv-opt-mono','pcadv-opt-zoe','pcadv-opt-stereo','pcadv-opt-lite'].map(txt);
    }""")
    for o in opts:
        if o:
            print(f"  {o['id']}: checked={o['checked']} disabled={o['disabled']} "
                  f"recommended={o['recommended']}")
            print(f"    text: {o['text'][:110]}…" if len(o['text']) > 110 else f"    text: {o['text']}")

    # Shots
    os.makedirs(OUT, exist_ok=True)
    shot1 = os.path.join(OUT, "advanced-scan-recommend.png")
    page.screenshot(path=shot1, full_page=False)
    print(f"  screenshot: {shot1}")

    # Assertions
    check("Exactly one Recommended badge", len(badges) == 1, f"got {len(badges)}")
    check("The recommended option is the checked one",
          len(badges) == 1 and opts[["pcadv-opt-mono","pcadv-opt-zoe","pcadv-opt-stereo","pcadv-opt-lite"].index(badges[0])]["checked"])

    # Click each available option and screenshot.
    for opt in ("zoe", "mono", "stereo"):
        rad = page.query_selector(f'#pcadv-opt-{opt} input[type=radio]')
        if rad and rad.is_enabled():
            rad.check()
            page.wait_for_timeout(150)
            shot = os.path.join(OUT, f"advanced-scan-{opt}-selected.png")
            page.screenshot(path=shot, full_page=False)
            print(f"  screenshot {opt}: {shot}")

    browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
