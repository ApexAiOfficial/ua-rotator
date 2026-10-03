# Apex User-Agent Rotator

Apex User-Agent Rotator is a small, framework-agnostic Python library for weighted User-Agent selection. It includes strict profile validation, sticky identity policies, metadata-preserving JSON persistence, a dated bundled browser profile, and an optional Scrapy downloader middleware.

The core package uses only the Python standard library. Scrapy support is optional.

- **Version:** 1.2.1
- **Python:** 3.10+
- **License:** Proprietary (`LicenseRef-Proprietary`); see [Licensing](#license)
- **Default profile:** `global_2026-07-31.json`
- **Repository:** <https://github.com/ApexAiOfficial/ua-rotator>

> **Source-available, not open source.** This repository is public so the code and documentation can be reviewed. Using, copying, modifying, or redistributing the software requires a separate written agreement with ApexAI. See [`LICENSE.txt`](LICENSE.txt).

## What it does

The rotator can:

- select from weighted User-Agent profiles;
- keep one identity per request, process session, or caller-defined key;
- preserve profile metadata when loading and saving JSON documents;
- reject malformed, duplicated, unsafe, or ambiguous profile input;
- bound sticky-key memory with LRU eviction;
- integrate with Scrapy while preserving identity across redirects and retries;
- load a packaged, dated browser profile without requiring a separate data file;
- validate and sample profiles from a command line.

## What it does not do

This package changes one HTTP header. It does not automatically coordinate:

- Client Hints;
- TLS, HTTP/2, or HTTP/3 fingerprints;
- JavaScript-visible browser properties;
- viewport, fonts, locale, or timezone;
- cookies, authentication sessions, or proxy geography;
- request pacing or site-specific crawl policy.

It is not an anti-bot bypass system. Use it only for authorized automation and follow the rules that apply to the systems you access.

## Can it change a normal web browser?

Not by itself. Apex User-Agent Rotator is a Python library, not a Chrome or Firefox extension and not a system-wide proxy.

You can use it directly in:

- Python scripts;
- Scrapy projects;
- agents or plug-ins that issue HTTP requests;
- local services that need a weighted User-Agent source.

For interactive browsing in a normal browser, a browser-specific override, extension, automation framework, or local proxy must apply the selected value. Even then, changing only the User-Agent header may leave other browser signals inconsistent. See [`docs/BROWSER_USAGE.md`](docs/BROWSER_USAGE.md).

## Package layout

```text
src/apex_ua_rotator/
├── core.py                         # framework-independent selection engine
├── scrapy.py                       # optional Scrapy middleware
├── cli.py                          # command-line interface
├── smoke.py                        # localhost Scrapy integration test
├── user_agent_profiles.schema.json
└── profiles/
    ├── global_2026-07-31.json
    └── global_2026-07-31.md
```

The rest of the repository:

```text
docs/                               # profile format, Scrapy, browser, validation guides
examples/                           # script, Scrapy settings, and custom profile examples
launchers/                          # phone and computer locators for the one-file installer
tests/                              # pytest suite
tools/                              # profile validator and one-file installer builder
user_agent_profiles.schema.json     # copy of the packaged JSON Schema
```

Compatibility modules remain available for earlier integrations:

```python
import user_agent_rotator
import scrapy_user_agent_adapter
```

New integrations should use the canonical `apex_ua_rotator` package.

## Installation

The package is not published to PyPI, and prebuilt release artifacts (the wheel and the one-file installer) are not attached to this repository. Install from a source checkout, or build the artifacts yourself as shown below.

### Install from source

```bash
git clone https://github.com/ApexAiOfficial/ua-rotator.git
cd ua-rotator
python3 -m pip install .
```

### Install with Scrapy

```bash
python3 -m pip install ".[scrapy]"
```

### Build and install a wheel

```bash
python3 -m pip wheel --no-deps . -w dist
python3 -m pip install dist/apex_ua_rotator-1.2.1-py3-none-any.whl
```

### One-command installer

`apex-ua-rotator-1.2.1-one-shot.pyz` is a single file that embeds the wheel, installs it into the active Python interpreter, validates the bundled profile, and runs the localhost Scrapy smoke test when Scrapy is already installed. Build it from the wheel above:

```bash
python3 tools/build_one_shot.py dist/apex_ua_rotator-1.2.1-py3-none-any.whl \
  -o dist/apex-ua-rotator-1.2.1-one-shot.pyz
```

Then run it with any Python 3.10+ interpreter:

```bash
python3 apex-ua-rotator-1.2.1-one-shot.pyz
```

Useful options:

```bash
python3 apex-ua-rotator-1.2.1-one-shot.pyz --install-only
python3 apex-ua-rotator-1.2.1-one-shot.pyz --smoke-only
python3 apex-ua-rotator-1.2.1-one-shot.pyz --require-scrapy
python3 apex-ua-rotator-1.2.1-one-shot.pyz --report result.json
```

The one-shot tool does not download or install Scrapy. If Scrapy is absent, the core validation passes and the optional integration test is reported as skipped unless `--require-scrapy` is used.

### Locator launchers

Two small locator scripts in [`launchers/`](launchers/) find and run the one-file installer in environments where it may be buried among many files. Copy the launcher to the target device (or run it from a checkout); it forwards any additional command-line arguments to the installer.

**Phone / Android**

```bash
python3 apex-ua-rotator-phone.py --require-scrapy
```

The phone locator searches mounted shared/internal storage and removable SD-card roots, including `/storage`, `/mnt/media_rw`, `/sdcard`, Android runtime mounts, and storage paths exposed through common environment variables. The search is bounded to depth 8, does not assume a Download folder, and selects the highest-version matching installer; modification time breaks ties between copies of the same version.

**Computer**

```bash
python3 apex-ua-rotator-computer.py
```

The computer locator is cross-platform for Linux, macOS, and Windows. It searches only user-level roots: the current user's home directory plus user-owned cloud/storage paths exposed through common environment variables. The search is bounded to depth 8, never scans an entire system drive, prunes common cache/dependency/virtual-environment/trash directories, and selects the highest-version matching installer.

An explicit installer path remains the most deterministic option:

```bash
python3 /absolute/path/to/apex-ua-rotator-1.2.1-one-shot.pyz
```

## Quick start

### Use the bundled profile

```python
from apex_ua_rotator import UserAgentRotator

rotator = UserAgentRotator.from_bundled_profile()

print(len(rotator))
print(rotator.metadata["as_of"])
print(rotator.select())
```

### Use a custom in-memory list

```python
from apex_ua_rotator import UserAgentRotator

rotator = UserAgentRotator(
    [
        ("ExampleClient/1.0 desktop", 3),
        ("ExampleClient/1.0 mobile", 1),
    ]
)

print(rotator.select())
```

A single profile must be wrapped in a list:

```python
rotator = UserAgentRotator(["ExampleClient/1.0"])
```

Bare strings, bytes, mappings, and single `UserAgentEntry` objects are rejected at the top-level constructor boundary because treating them as iterables can silently create one entry per character or key.

## Selection policies

### `per_request`

Select on each call.

```python
rotator = UserAgentRotator.from_bundled_profile(policy="per_request")
print(rotator.select())
```

### `per_session`

Keep one value for the rotator instance until reset or explicitly refreshed.

```python
rotator = UserAgentRotator.from_bundled_profile(policy="per_session")
first = rotator.select()
assert first == rotator.select()
replacement = rotator.select(force_new=True)
```

### `per_key`

Keep one value per caller-supplied hashable key. The cache is bounded and uses LRU eviction.

```python
rotator = UserAgentRotator.from_bundled_profile(
    policy="per_key",
    max_key_cache=4096,
)

first = rotator.select(key="example.com")
assert first == rotator.select(key="example.com")
replacement = rotator.select(key="example.com", force_new=True)
```

Keys may represent domains, accounts, cookie jars, proxies, tenants, or any other identity scope meaningful to the caller.

## Command-line interface

After installation:

```bash
apex-ua-rotator info
apex-ua-rotator sample -n 5
apex-ua-rotator sample -n 3 --policy per_session
apex-ua-rotator validate ./profiles.json
apex-ua-rotator smoke-scrapy
```

The same interface is available without relying on the generated console script:

```bash
python3 -m apex_ua_rotator info
```

## Profile documents

A profile file uses this versioned structure:

```json
{
  "schema_version": 1,
  "metadata": {
    "purpose": "Dated profile description",
    "as_of": "2026-07-31"
  },
  "entries": [
    {
      "user_agent": "ExampleClient/1.0",
      "weight": 1.0
    }
  ]
}
```

Weights are raw relative values and do not need to total 100. A total of 100 can be useful for human-readable market models, but `1, 3, 6` and `10, 30, 60` describe the same distribution.

The loader rejects:

- unsupported top-level or entry fields;
- unsupported schema versions;
- duplicate User-Agent strings;
- empty, non-ASCII, whitespace-padded, or control-character-bearing values;
- Boolean, negative, infinite, or NaN weights;
- documents with no positive weight;
- metadata that cannot be represented as finite JSON.

Metadata is preserved by `from_file()`, `to_document()`, and `save()`.

See [`docs/PROFILE_FORMAT.md`](docs/PROFILE_FORMAT.md) and the bundled JSON Schema.

## Persistence

```python
rotator.save("profiles.json")
rotator.save("profiles.json", overwrite=True)
```

By default, saving refuses to replace an existing file. Publication uses a temporary file in the destination directory, flushes and synchronizes its contents, then publishes it atomically where supported. Temporary files are removed on failure.

## Scrapy integration

Install the optional dependency, then add the middleware:

```python
DOWNLOADER_MIDDLEWARES = {
    "apex_ua_rotator.scrapy.ScrapyUserAgentMiddleware": 90,
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
}

UA_ROTATOR_SCOPE = "domain"
UA_ROTATOR_REFRESH_ON = []
UA_ROTATOR_OVERWRITE_EXISTING = False
UA_ROTATOR_MAX_KEY_CACHE = 4096

ROBOTSTXT_USER_AGENT = "YourCrawlerName/1.0"
```

With no profile source setting, the middleware uses the default bundled profile.

Optional profile sources:

```python
# Named packaged profile
UA_ROTATOR_BUNDLED_PROFILE = "global_2026-07-31.json"

# External profile document
UA_ROTATOR_FILE = "/absolute/path/to/profiles.json"

# In-memory entries
UA_ROTATOR_ENTRIES = [("ExampleClient/1.0", 1)]
```

Configure at most one source.

Available scopes:

- `request`: one identity per request lineage;
- `spider`: one identity for the middleware instance;
- `domain`: one identity per parsed hostname;
- `cookiejar`: one identity per Scrapy `cookiejar` value;
- `proxy`: one identity per proxy value, with direct traffic grouped separately.

Retries and redirects preserve the selected identity by default. Optional refresh events are explicit:

```python
UA_ROTATOR_REFRESH_ON = ["retry"]
UA_ROTATOR_REFRESH_ON = ["redirect"]
UA_ROTATOR_REFRESH_ON = ["retry", "redirect"]
```

The adapter respects a pre-existing User-Agent header unless `UA_ROTATOR_OVERWRITE_EXISTING` is true.

See [`docs/SCRAPY.md`](docs/SCRAPY.md) for complete behavior and examples.

## Local validation

### Core test suite

Install the test dependencies first:

```bash
python3 -m pip install ".[test]"
```

Then run:

```bash
python3 -m compileall -q src tests tools examples launchers
python3 -m pytest -q
python3 tools/validate_profiles.py
```

### Coverage

```bash
coverage run -m pytest -q
coverage report -m
```

### Real Scrapy middleware smoke test

```bash
python3 -m pip install ".[scrapy]"
python3 -m apex_ua_rotator smoke-scrapy
```

The smoke test contacts only a temporary HTTP server on `127.0.0.1`. It verifies actual Scrapy middleware loading, User-Agent assignment, redirect continuity, retry continuity, request metadata, and middleware statistics.

## Integration guidance

- Use `per_request` when every independent request may use a new identity.
- Use `per_key` for domains, accounts, cookie jars, or proxies that should remain coherent.
- Do not rotate within an authenticated session unless the application explicitly requires it.
- Keep profiles external and dated so they can be reviewed and replaced independently of code.
- Log the selected scope and User-Agent during integration testing, but avoid logging credentials, cookies, or sensitive request data.
- Validate profile updates before deployment.

## Documentation

- [`docs/PROFILE_FORMAT.md`](docs/PROFILE_FORMAT.md)
- [`docs/SCRAPY.md`](docs/SCRAPY.md)
- [`docs/BROWSER_USAGE.md`](docs/BROWSER_USAGE.md)
- [`docs/VALIDATION.md`](docs/VALIDATION.md)
- [`launchers/README.md`](launchers/README.md)
- [`examples/`](examples/)
- [`CHANGELOG.md`](CHANGELOG.md)

## License

Copyright (c) 2026 ApexAI. All rights reserved.

This software is proprietary. The repository is publicly visible for review only; no permission is granted to use, copy, modify, publish, distribute, sublicense, or sell it except under a separate written agreement with ApexAI. See [`LICENSE.txt`](LICENSE.txt).

For licensing inquiries, contact ApexAI through <https://apexaiofficial.com/contact>.
