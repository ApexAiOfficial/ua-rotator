# Interactive browser usage

Apex UA Rotator does not directly modify Chrome, Firefox, Edge, Safari, or other interactive browsers. It supplies validated User-Agent strings to Python callers.

## Practical options

A selected value may be passed into:

- a browser automation framework;
- a browser extension that supports User-Agent overrides;
- a local HTTP proxy that rewrites headers;
- a developer-tool override for temporary testing;
- an agent or plug-in that controls its own request stack.

The exact integration depends on the browser or automation framework and is intentionally outside this package.

## Important consistency warning

Modern browsers expose more than the legacy `User-Agent` header. A manual override can disagree with Client Hints, JavaScript properties, platform behavior, TLS characteristics, viewport, or installed features. Use overrides for legitimate compatibility testing and controlled automation, not as proof that one browser has become another.

## Generate a value for another tool

```bash
apex-ua-rotator sample -n 1
```

Or:

```python
from apex_ua_rotator import UserAgentRotator

ua = UserAgentRotator.from_bundled_profile().select()
print(ua)
```
