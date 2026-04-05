#!/usr/bin/env python3
"""Capture SPA screenshots for demo and docs using Playwright."""
from playwright.sync_api import sync_playwright

DEMO = r"D:\onedrive\My Documents\ElectricRV\Development\Projects\Lighting Arduino\server\slyled\demo"
DOCS = r"D:\onedrive\My Documents\ElectricRV\Development\Projects\Lighting Arduino\docs\screenshots"

TABS = [
    ("dash",     "01-first-launch",   "spa-dashboard"),
    ("setup",    "05-setup-fixtures", "spa-setup"),
    ("layout",   "08-layout-2d",      "spa-layout-2d"),
    ("actions",  "10-actions",        "spa-actions"),
    ("runtime",  "11-runtime",        "spa-runtime"),
    ("settings", "12-settings",       "spa-settings"),
    ("firmware", "02-firmware-tab",   "spa-firmware"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # Standard tab screenshots
    for tab, demo_name, docs_name in TABS:
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(f"http://localhost:8080/?tab={tab}", wait_until="networkidle")
        page.wait_for_timeout(3000)
        page.screenshot(path=f"{DEMO}\\{demo_name}.png")
        page.screenshot(path=f"{DOCS}\\{docs_name}.png")
        sz = len(open(f"{DEMO}\\{demo_name}.png", "rb").read()) // 1024
        print(f"  {demo_name}.png = {sz}KB")
        page.close()

    # DMX fixture wizard — step 1 with search results
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto("http://localhost:8080/?tab=setup", wait_until="networkidle")
    page.wait_for_timeout(2000)
    page.evaluate("_wizStep1()")
    page.wait_for_timeout(1000)
    # Type a search and trigger it for a populated result
    page.fill("#wiz-q", "moving head")
    page.evaluate("_wizSearch()")
    page.wait_for_timeout(5000)
    page.screenshot(path=f"{DEMO}\\07-setup-dmx.png")
    page.screenshot(path=f"{DOCS}\\spa-setup-add-dmx.png")
    print("  07-setup-dmx.png (DMX wizard with search)")
    page.close()

    # Layout 3D
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto("http://localhost:8080/?tab=layout", wait_until="networkidle")
    page.wait_for_timeout(2000)
    page.evaluate("if(typeof setLayoutMode==='function')setLayoutMode('3d')")
    page.wait_for_timeout(4000)
    page.screenshot(path=f"{DEMO}\\09-layout-3d.png")
    page.screenshot(path=f"{DOCS}\\spa-layout-3d.png")
    print("  09-layout-3d.png (3D view)")
    page.close()

    browser.close()
    print("\nDone.")
