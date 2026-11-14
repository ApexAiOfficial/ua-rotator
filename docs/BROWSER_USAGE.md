# Interactive browser usage

Apex UA Rotator does not modify Chrome, Firefox, Edge, Safari, or other interactive browsers. It supplies validated User-Agent strings to Python callers, and something else has to apply them.

## Practical options

A selected value can be passed to:

- a browser automation framework (Playwright, Selenium);
- a browser extension that supports User-Agent overrides;
- a local HTTP proxy that rewrites headers;
- a developer-tools override for temporary testing;
- an agent or plug-in that controls its own request stack.

## Playwright: one identity per browser context

A Playwright browser context has its own User-Agent, cookies, and storage, so one context per identity keeps everything that belongs together in one place. A runnable version is in [`examples/playwright_context.py`](../examples/playwright_context.py).

```python
from playwright.sync_api import sync_playwright
from apex_ua_rotator import UserAgentRotator

bundled = UserAgentRotator.from_bundled_profile()
chromium_desktop = UserAgentRotator([
    (e.user_agent, e.weight) for e in bundled.entries
    if "Chrome/" in e.user_agent and "Mobile" not in e.user_agent
])

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(user_agent=chromium_desktop.select())
    page = context.new_page()
    page.goto("https://example.com/")
```

## Selenium (Chrome)

```python
from selenium import webdriver
from apex_ua_rotator import UserAgentRotator

options = webdriver.ChromeOptions()
options.add_argument(f"--user-agent={UserAgentRotator.from_bundled_profile().select()}")
driver = webdriver.Chrome(options=options)
```

## Important consistency warning

Modern browsers expose much more than the `User-Agent` header. An override can disagree with Client Hints (`Sec-CH-UA*`), `navigator.userAgentData`, platform behavior, TLS and HTTP/2 characteristics, viewport, fonts, and installed features. Two habits limit the damage:

- **Match the engine.** Use Chromium-family strings with Chromium and Firefox strings with Firefox. Filter the profile, as above, rather than sending a Safari string from Chromium.
- **Match the form factor.** Desktop strings belong in desktop-sized viewports.

Use overrides for legitimate compatibility testing and controlled automation, not as proof that one browser has become another.

## Generate a value for another tool

```bash
apex-ua-rotator sample -n 1
apex-ua-rotator sample -n 5 --seed 42   # reproducible
```
