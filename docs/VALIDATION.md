# Validation workflow

All commands run from the repository root.

## Test dependencies

```bash
python3 -m pip install ".[test]"
```

## Fast checks

```bash
python3 -m compileall -q src tests tools examples launchers
python3 -m pytest -q
python3 tools/validate_profiles.py
```

## Coverage

```bash
coverage run -m pytest -q
coverage report -m
```

The coverage configuration in `pyproject.toml` fails the report below 90% branch coverage.

## Build and clean installation

`--no-build-isolation` uses the current environment's build backend, so it needs `setuptools>=77` installed. Omit the flag to let pip fetch the build backend instead.

```bash
python3 -m pip wheel --no-deps --no-build-isolation . -w dist
python3 -m venv .validation-venv
.validation-venv/bin/python -m pip install --no-index --find-links dist apex-ua-rotator
.validation-venv/bin/apex-ua-rotator info
```

On Windows, use the executables under `.validation-venv\Scripts`.

## One-file installer

```bash
python3 tools/build_one_shot.py dist/apex_ua_rotator-1.2.1-py3-none-any.whl \
  -o dist/apex-ua-rotator-1.2.1-one-shot.pyz
python3 dist/apex-ua-rotator-1.2.1-one-shot.pyz --report result.json
```

The installer verifies the embedded wheel's SHA-256 checksum before installing it.

## Scrapy integration

```bash
python3 -m pip install ".[scrapy]"
apex-ua-rotator smoke-scrapy
```

The smoke test is local-only. A separate authorized crawl should still be used to evaluate behavior under the target project's actual middleware, cookies, redirects, proxies, and request concurrency.
