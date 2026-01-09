#!/usr/bin/env python3
"""Check font sizes on the RunDetailDialog."""

from playwright.sync_api import sync_playwright

URL = "http://localhost:5173/board/df09e393-245a-433d-b4cb-622bcc19b36d"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # Visible for debugging
    page = browser.new_page()

    print(f"Navigating to {URL}...")
    page.goto(URL)
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(2000)  # Extra wait for dialog to render

    # Take a screenshot
    page.screenshot(path='/tmp/font_check.png', full_page=True)
    print("Screenshot saved to /tmp/font_check.png")

    # Find all buttons and check their computed font sizes
    print("\n=== BUTTON FONT SIZES ===")
    buttons = page.locator('button').all()
    for i, btn in enumerate(buttons[:20]):  # First 20 buttons
        try:
            text = btn.inner_text().strip()[:40]
            font_size = btn.evaluate('el => window.getComputedStyle(el).fontSize')
            print(f"Button {i}: '{text}' -> font-size: {font_size}")
        except:
            pass

    # Check specific tab buttons by looking for uppercase text
    print("\n=== TAB BUTTONS (Messages, Output, etc) ===")
    tab_buttons = page.locator('button:has-text("Messages"), button:has-text("Output"), button:has-text("Errors"), button:has-text("Commits"), button:has-text("Files"), button:has-text("Images")').all()
    for btn in tab_buttons:
        try:
            text = btn.inner_text().strip()
            font_size = btn.evaluate('el => window.getComputedStyle(el).fontSize')
            print(f"Tab '{text}' -> font-size: {font_size}")
        except:
            pass

    # Check Resume button
    print("\n=== RESUME BUTTON ===")
    resume_btn = page.locator('button:has-text("Resume")').first
    try:
        font_size = resume_btn.evaluate('el => window.getComputedStyle(el).fontSize')
        print(f"Resume button -> font-size: {font_size}")
    except Exception as e:
        print(f"Resume button not found or error: {e}")

    # Check filter buttons in StreamingLogViewer
    print("\n=== FILTER BUTTONS (All, Tools, Text, Errors) ===")
    filter_buttons = page.locator('button:has-text("All"), button:has-text("Tools"), button:has-text("Text")').all()
    for btn in filter_buttons[:6]:
        try:
            text = btn.inner_text().strip()[:20]
            font_size = btn.evaluate('el => window.getComputedStyle(el).fontSize')
            print(f"Filter '{text}' -> font-size: {font_size}")
        except:
            pass

    # Check elements with text-body class
    print("\n=== ELEMENTS WITH .text-body CLASS ===")
    text_body_els = page.locator('.text-body').all()
    print(f"Found {len(text_body_els)} elements with .text-body class")
    for i, el in enumerate(text_body_els[:10]):
        try:
            tag = el.evaluate('el => el.tagName')
            font_size = el.evaluate('el => window.getComputedStyle(el).fontSize')
            text = el.inner_text().strip()[:30]
            print(f"  {tag}: '{text}' -> font-size: {font_size}")
        except:
            pass

    print("\n=== DONE ===")
    browser.close()
