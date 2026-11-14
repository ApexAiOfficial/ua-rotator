"""One browser context per identity with Playwright.

    python -m pip install playwright && python -m playwright install chromium
    python examples/playwright_context.py

A browser context carries its own User-Agent, cookies, and storage, so each
context behaves as one coherent identity. Pick profiles that match the engine
you launch (here: Chromium-family strings for Chromium), because the browser's
other signals still come from the real engine.
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from apex_ua_rotator import UserAgentRotator

bundled = UserAgentRotator.from_bundled_profile()
chromium_only = UserAgentRotator(
    [
        (entry.user_agent, entry.weight)
        for entry in bundled.entries
        if "Chrome/" in entry.user_agent and "Mobile" not in entry.user_agent
    ]
)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    for _ in range(2):
        context = browser.new_context(user_agent=chromium_only.select())
        page = context.new_page()
        page.goto("https://example.com/")
        print(page.evaluate("navigator.userAgent"))
        context.close()
    browser.close()
