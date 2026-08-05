# Validation workflow

## Fast checks

```bash
python3 -m compileall -q src tests tools examples
python3 -m pytest -q
python3 tools/validate_profiles.py
```

## Coverage

```bash
coverage run -m pytest -q
coverage report -m
```

## Build and clean installation

```bash
python3 -m pip wheel --no-deps --no-build-isolation . -w dist
python3 -m venv .validation-venv
.validation-venv/bin/python -m pip install --no-index --find-links dist apex-ua-rotator
.validation-venv/bin/apex-ua-rotator info
```

On Windows, use the Python executable under `.validation-venv\\Scripts`.

## Scrapy integration

```bash
python3 -m pip install ".[scrapy]"
apex-ua-rotator smoke-scrapy
```

The smoke test is local-only. A separate authorized crawl should still be used to evaluate behavior under the target project's actual middleware, cookies, redirects, proxies, and request concurrency.
