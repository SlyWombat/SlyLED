#!/usr/bin/env python3
"""Test all website links with a real browser."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    print("Loading main page...")
    page.goto('https://electricrv.ca/slyled/', wait_until='networkidle', timeout=15000)

    links = page.query_selector_all('a[href]')
    print(f"Found {len(links)} links\n")

    for a in links:
        href = a.get_attribute('href') or ''
        text = (a.inner_text() or '').strip().replace('\u2192', '->').replace('\u2190', '<-')[:40]
        clickable = a.is_visible() and a.is_enabled()
        print(f"  {'OK' if clickable else 'HIDDEN'} [{text:40s}] {href}")

    # Test clicking each visible link
    print("\n--- Click Tests ---")

    # Demo link
    page.goto('https://electricrv.ca/slyled/', wait_until='networkidle', timeout=15000)
    demo = page.query_selector('a[href*="demo"]')
    if demo:
        try:
            with page.expect_navigation(timeout=10000):
                demo.click()
            print(f"  Demo click: OK -> {page.url}")
        except Exception as e:
            print(f"  Demo click: FAILED -> {e}")

    # GitHub link
    page.goto('https://electricrv.ca/slyled/', wait_until='networkidle', timeout=15000)
    gh = page.query_selector('a[href="https://github.com/SlyWombat/SlyLED"]')
    if gh:
        try:
            with page.expect_navigation(timeout=10000):
                gh.click()
            print(f"  GitHub click: OK -> {page.url}")
        except Exception as e:
            # External links may open in new context
            print(f"  GitHub click: navigation event -> {e}")

    # Download link
    page.goto('https://electricrv.ca/slyled/', wait_until='networkidle', timeout=15000)
    dl = page.query_selector('a[href*="releases/latest"]')
    if dl:
        try:
            with page.expect_navigation(timeout=10000):
                dl.click()
            print(f"  Download click: OK -> {page.url}")
        except Exception as e:
            print(f"  Download click: navigation event -> {e}")

    browser.close()
    print("\nDone.")
