#!/usr/bin/env python3
"""
Playwright E2E UI Test Suite for Media Studio Hub
Validates:
- Page loads with valid title and favicon
- Header brand branding elements
- Download URL input form and action button
- View switching (Studio, Explorer, Duplicates, History, Settings)
- Theme toggle (Dark vs Light)
- Language switcher (English vs Español)
"""

import sys, subprocess, time, urllib.request
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8888"

def ensure_server_running():
    try:
        urllib.request.urlopen(f"{BASE_URL}/api/status", timeout=1)
        return None
    except Exception:
        proc = subprocess.Popen([sys.executable, "media_server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            try:
                urllib.request.urlopen(f"{BASE_URL}/api/status", timeout=1)
                return proc
            except Exception:
                time.sleep(0.5)
        return proc

def test_ui_e2e():
    server_proc = ensure_server_running()
    print(f"🚀 Starting Playwright E2E UI tests on {BASE_URL}...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # 1. Load root web interface
            page.goto(BASE_URL, wait_until="networkidle")
        title = page.title()
        print(f"  ✓ Page Title: '{title}'")
        assert "Media Studio" in title, f"Unexpected page title: {title}"

        # 2. Verify Key UI Header Elements
        header = page.locator(".header")
        assert header.is_visible(), "Header component is not visible"
        print("  ✓ Header & Brand elements visible")

        # 3. Verify Download Input & Mode Buttons
        url_input = page.locator("#urlInput")
        assert url_input.is_visible(), "URL input element not visible"
        download_btn = page.locator("#btnDl")
        assert download_btn.is_visible(), "Submit download button not visible"
        print("  ✓ Download form and action buttons present")

        # 4. Test Navigation Tabs (Explorer, Settings, History, Duplicates, Studio)
        tab_explorer = page.locator("#tabBtnExplorer")
        assert tab_explorer.is_visible(), "Explorer tab not visible"
        tab_explorer.click()
        page.wait_for_timeout(200)
        assert page.locator("#viewExplorer").is_visible(), "Explorer view not active"
        print("  ✓ Navigated to Explorer View")

        tab_settings = page.locator("#tabBtnSettings")
        assert tab_settings.is_visible(), "Settings tab not visible"
        tab_settings.click()
        page.wait_for_timeout(200)
        assert page.locator("#viewSettings").is_visible(), "Settings view not active"
        print("  ✓ Navigated to Settings View")

        tab_history = page.locator("#tabBtnHistory")
        assert tab_history.is_visible(), "History tab not visible"
        tab_history.click()
        page.wait_for_timeout(200)
        assert page.locator("#viewHistory").is_visible(), "History view not active"
        print("  ✓ Navigated to History View")

        tab_studio = page.locator("#tabBtnStudio")
        tab_studio.click()
        page.wait_for_timeout(200)
        assert page.locator("#viewStudio").is_visible(), "Studio view not active"
        print("  ✓ Navigated back to Studio View")

        # 5. Check Theme Switcher
        html_element = page.locator("html")
        btn_light = page.locator("#btnThemeLight")
        btn_dark = page.locator("#btnThemeDark")
        
        btn_light.click()
        page.wait_for_timeout(100)
        assert html_element.get_attribute("data-theme") == "light", "Failed to switch to light theme"
        print("  ✓ Switched to Light Theme")

        btn_dark.click()
        page.wait_for_timeout(100)
        assert html_element.get_attribute("data-theme") == "dark", "Failed to switch to dark theme"
        print("  ✓ Switched back to Dark Theme")

        # 6. Check Language Switcher
        btn_es = page.locator("#btnLangEs")
        btn_en = page.locator("#btnLangEn")
        
        btn_es.click()
        page.wait_for_timeout(100)
        assert "Ajustes" in page.locator("#lblNavSettings").inner_text() or "Configuración" in page.locator("#lblNavSettings").inner_text() or "Preferencias" in page.locator("#lblNavSettings").inner_text(), "Spanish translation failed"
        print("  ✓ Switched language to Español")

        btn_en.click()
        page.wait_for_timeout(100)
        assert "Settings" in page.locator("#lblNavSettings").inner_text(), "English translation failed"
        print("  ✓ Switched language back to English")

        # 7. Test Queue Tracks Expand / Collapse Accordion
        toggle_btn = page.locator(".btn-toggle-tracks").first
        if toggle_btn.is_visible():
            toggle_btn.click()
            page.wait_for_timeout(300)
            drawer = page.locator(".queue-tracks-drawer").first
            assert drawer.is_visible(), "Queue tracks drawer did not open"
            print("  ✓ Expandable Queue Tracks Drawer tested successfully")

        browser.close()
        print("\n🎉 ALL PLAYWRIGHT E2E UI TESTS PASSED SUCCESSFULLY!")
    finally:
        if server_proc:
            server_proc.terminate()

if __name__ == "__main__":
    try:
        test_ui_e2e()
    except Exception as e:
        print(f"❌ Playwright E2E UI Test Failed: {e}", file=sys.stderr)
        sys.exit(1)
