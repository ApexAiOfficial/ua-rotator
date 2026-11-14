# Validation and release process

All commands run from the repository root.

## Development checks

```bash
python3 -m pip install ".[test]"
python3 -m compileall -q src tests tools examples launchers
python3 -m coverage run -m pytest -q
python3 -m coverage report            # fails below 90% branch coverage
python3 tools/validate_profiles.py
```

Lint and type checks (versions pinned to match CI):

```bash
python3 -m pip install "ruff==0.17.5" "mypy==2.4.1"
ruff check .
mypy                                   # strict mode, configured in pyproject.toml
```

The real-Scrapy tests (`tests/test_scrapy_real.py`) and the Scrapy smoke test run when Scrapy is installed and are skipped otherwise:

```bash
python3 -m pip install ".[test,scrapy]"
python3 -m pytest -q tests/test_scrapy_real.py tests/test_scrapy_adapter.py
apex-ua-rotator smoke-scrapy
```

`tools/validate_profiles.py` checks every bundled profile with the real loader, the snapshot invariants (descending weights totaling exactly 100, matching metadata), the JSON Schema, the browser-token lint in `tools/profile_lint.py`, and repository consistency (identical schema copies, a notes file per snapshot).

## Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push to `main` and every pull request:

| Job | What it checks |
|---|---|
| Tests | Python 3.10–3.14 on Linux; Python 3.12 on Windows and macOS; branch-coverage gate; profile validation |
| Scrapy | real Scrapy components and the localhost smoke test against Scrapy 2.11 (minimum supported) and the latest 2.x |
| Lint and type check | `ruff check`, strict `mypy` |
| Reproducible release build | builds all release artifacts twice and requires byte-identical output |

Actions are pinned to full commit SHAs and kept current by Dependabot.

## Building a release

```bash
python3 tools/build_release.py --validate --scrapy-python /path/to/venv-with-scrapy/bin/python
```

The tool:

1. exports the committed tree with `git archive HEAD`, so ignored and uncommitted files cannot leak into artifacts (it refuses to run with uncommitted changes to tracked files);
2. installs the pinned build backend (`setuptools==84.2.1`) in a temporary virtual environment and builds the wheel and sdist with `SOURCE_DATE_EPOCH` set to the commit timestamp and a fixed umask;
3. builds the one-file installer, copies the launchers, default profile, and schema, and assembles a deterministic offline bundle;
4. writes `SHA256SUMS`;
5. with `--validate`: runs the test suite with coverage, validates profiles, installs the wheel offline into a clean environment, installs through the one-file installer into another, runs both locators in an isolated home directory, runs the Scrapy smoke test and adapter tests with each `--scrapy-python` against the built wheel (the report records that the wheel, not the source tree, was imported), and rebuilds everything to prove byte-identical output. Every check gates on exit codes. A failing or crashing step is recorded as FAIL or ERROR in `RELEASE_VALIDATION.json` and `RELEASE_VALIDATION.md` rather than aborting the run.

`--validate` requires at least one `--scrapy-python` interpreter with both Scrapy and pytest installed. Artifacts land in `dist/release/`. The tool only deletes an output directory that is empty or that it created itself; it refuses the repository, its parents, and your home directory.

To build only the one-file installer from a wheel:

```bash
python3 tools/build_one_shot.py dist/release/apex_ua_rotator-1.4.0-py3-none-any.whl -o apex-ua-rotator-1.4.0-one-shot.pyz
```

## Verifying a published release

Anyone can check a release independently:

```bash
gh release download v1.4.0 --repo ApexAiOfficial/ua-rotator --dir published
(cd published && sha256sum --check SHA256SUMS)       # integrity
git checkout v1.4.0
python3 tools/build_release.py --out rebuild --compare published/SHA256SUMS   # reproducibility
```

[`.github/workflows/release-verify.yml`](../.github/workflows/release-verify.yml) runs the same two checks on GitHub's infrastructure whenever a release is published.

The comparison works in both directions: an asset that is published but not rebuilt, or rebuilt but not published, fails it. The validation reports are the only assets excluded, because they record the date and environment of the validation run.

## One-file installer

```bash
python3 apex-ua-rotator-1.4.0-one-shot.pyz --verify-payload   # checksum only, no install
python3 apex-ua-rotator-1.4.0-one-shot.pyz --report result.json
```

The installer verifies the embedded wheel's SHA-256 before installing it, then checks the installation from a fresh interpreter.
