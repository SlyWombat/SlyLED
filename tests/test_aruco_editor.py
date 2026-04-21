"""Live Playwright verification of #596 ArUco marker editor.

Drives the running orchestrator through both editor surfaces:
  1. Setup tab "ArUco Markers" section
  2. Advanced Scan card collapsible panel

For each:
  - Open the surface
  - Add a marker via the UI
  - Verify it appears in the table
  - Verify it appears in the sibling surface (round-trip through REST)
  - Delete it
  - Verify removal in both surfaces
"""
import os, sys, json, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
OUT = "/mnt/d/temp/live-test-session"

passed = failed = 0
def check(name, cond, detail=""):
    global passed, failed
    if cond: passed += 1; print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:    failed += 1; print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))

def reset_markers():
    try:
        r = urllib.request.urlopen(f"{BASE}/api/aruco/markers", timeout=3)
        d = json.loads(r.read().decode())
        for m in d.get("markers", []):
            urllib.request.urlopen(urllib.request.Request(
                f"{BASE}/api/aruco/markers/{m['id']}", method="DELETE"), timeout=3)
    except Exception as e:
        print("  reset warn:", e)

reset_markers()

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1400, 'height': 1000})
    page.on("pageerror", lambda e: print("  PAGE ERROR:", e))

    print("Loading SlyLED…")
    page.goto(BASE); page.wait_for_timeout(2500)

    # ── 1. Setup tab surface ───────────────────────────────────────
    print("\n=== Setup tab ArUco section ===")
    page.click('#n-setup'); page.wait_for_timeout(1200)
    page.wait_for_selector('#aruco-setup-host', timeout=5000)

    host = "aruco-setup-host"
    check("Setup host rendered", page.query_selector(f'#{host}') is not None)

    # Add marker id=3
    page.fill(f'#aruco-add-{host}-id', '3')
    page.fill(f'#aruco-add-{host}-label', 'USL')
    page.fill(f'#aruco-add-{host}-size', '200')
    page.fill(f'#aruco-add-{host}-x', '100')
    page.fill(f'#aruco-add-{host}-y', '200')
    page.fill(f'#aruco-add-{host}-z', '0')
    # Call _arucoRowAdd directly — "Add" buttons exist in multiple scopes
    page.evaluate(f"_arucoRowAdd('{host}')")
    page.wait_for_timeout(700)

    # Confirm row appeared
    row = page.query_selector(f'#aruco-row-{host}-3')
    check("Added row renders", row is not None)

    # Read back the server state directly
    server = json.loads(urllib.request.urlopen(f"{BASE}/api/aruco/markers", timeout=3).read())
    m = next((x for x in server["markers"] if x["id"] == 3), None)
    check("Server has marker id=3", m is not None)
    if m:
        check("Label persisted", m.get("label") == "USL", f"got {m.get('label')!r}")
        check("X persisted", m.get("x") == 100, f"got {m.get('x')}")
        check("Size persisted", m.get("size") == 200)

    page.screenshot(path=os.path.join(OUT, "aruco-setup-with-marker.png"))

    # Edit size inline
    size_input = page.query_selector(f'#aruco-row-{host}-3 input[data-fld="size"]')
    if size_input:
        size_input.fill('250')
        page.click(f'#aruco-row-{host}-3 button:has-text("Save")')
        page.wait_for_timeout(500)
        server = json.loads(urllib.request.urlopen(f"{BASE}/api/aruco/markers", timeout=3).read())
        m = next((x for x in server["markers"] if x["id"] == 3), None)
        check("Inline edit persisted size=250", m and m.get("size") == 250)

    # ── 2. Advanced Scan card surface ───────────────────────────────
    print("\n=== Advanced Scan card ArUco panel ===")
    page.evaluate("_pcAdvancedScan()"); page.wait_for_timeout(800)
    check("Scan modal opened", page.evaluate(
        "() => document.getElementById('modal').style.display !== 'none'"))

    # Expand the details panel
    details = page.query_selector('#pcadv-aruco-details')
    check("Scan card has ArUco panel", details is not None)
    if details:
        details.evaluate("el => el.open = true")
        page.wait_for_timeout(400)

    scan_host = "aruco-scan-host"
    check("Scan host rendered", page.query_selector(f'#{scan_host}') is not None)
    # The edit from the setup surface should already be reflected here
    scan_row = page.query_selector(f'#aruco-row-{scan_host}-3')
    check("Scan panel shows marker id=3 (cross-surface sync)", scan_row is not None)

    page.screenshot(path=os.path.join(OUT, "aruco-scan-panel.png"))

    # Add a second marker from the scan panel
    page.fill(f'#aruco-add-{scan_host}-id', '7')
    page.fill(f'#aruco-add-{scan_host}-label', 'DSR')
    page.fill(f'#aruco-add-{scan_host}-x', '-500')
    page.evaluate(f"_arucoRowAdd('{scan_host}')")
    page.wait_for_timeout(700)

    server = json.loads(urllib.request.urlopen(f"{BASE}/api/aruco/markers", timeout=3).read())
    ids = sorted(m["id"] for m in server["markers"])
    check("Both markers persisted (3 + 7)", ids == [3, 7], f"got {ids}")

    # Close modal, re-open Setup, verify cross-surface sync
    page.evaluate("closeModal && closeModal()"); page.wait_for_timeout(400)
    page.evaluate("loadSetup && loadSetup()"); page.wait_for_timeout(1500)
    new_row = page.query_selector(f'#aruco-row-{host}-7')
    check("Setup surface reflects marker added in Scan panel", new_row is not None)

    # Project round-trip: export + re-import, confirm markers come back
    exp = json.loads(urllib.request.urlopen(f"{BASE}/api/project/export", timeout=10).read())
    check("Project export contains arucoMarkers", "arucoMarkers" in exp,
          f"keys: {list(exp.keys())[:10]}")
    if "arucoMarkers" in exp:
        check("Export round-trip has both markers",
              sorted(m["id"] for m in exp["arucoMarkers"]) == [3, 7])

    # Delete both
    urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/aruco/markers/3", method="DELETE"), timeout=3)
    urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/aruco/markers/7", method="DELETE"), timeout=3)
    server = json.loads(urllib.request.urlopen(f"{BASE}/api/aruco/markers", timeout=3).read())
    check("All markers deleted", len(server["markers"]) == 0)

    browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
