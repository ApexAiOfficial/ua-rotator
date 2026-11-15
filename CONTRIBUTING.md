# Contributing to UA Rotator

Thanks for helping improve UA Rotator. Bug reports, profile corrections, documentation fixes, and code contributions are all welcome.

- **Bugs and ideas:** open an [issue](https://github.com/ApexAiOfficial/ua-rotator/issues) with the version, Python version, a minimal reproduction, and what you expected.
- **Security issues:** do not open a public issue. Follow [`SECURITY.md`](SECURITY.md).

## Development setup

```bash
git clone https://github.com/ApexAiOfficial/ua-rotator.git
cd ua-rotator
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[test,scrapy]" "ruff==0.17.5" "mypy==2.4.1"
```

## Checks

Run the same checks CI runs before opening a pull request:

```bash
python -m compileall -q src tests tools examples launchers
python -m coverage run -m pytest -q && python -m coverage report   # 90% branch-coverage gate
python tools/validate_profiles.py
ruff check .
mypy
```

The real-Scrapy tests run when Scrapy is installed. CI also runs them against Scrapy 2.11, the minimum supported version.

## Guidelines

- **Keep the core dependency-free.** `apex_ua_rotator.core` uses only the standard library. Scrapy stays optional, and importing `apex_ua_rotator.scrapy` must not import Scrapy.
- **Stay backward compatible.** Public names, settings, compatibility modules (`user_agent_rotator`, `scrapy_user_agent_adapter`), and pinned profile names keep working within a major version. Describe behavior changes in `CHANGELOG.md`.
- **Fail closed.** Reject invalid input at load or startup with a clear `ValueError`, never at request time.
- **Test what you change.** Add regression tests for bug fixes. For Scrapy behavior, prefer tests in `tests/test_scrapy_real.py` that drive Scrapy's real middlewares.

### Profile changes

- Never edit a published snapshot in place. Ship a correction as a numbered revision whose metadata names the file it `supersedes`, and new market data as a new dated file.
- Cite every source and its retrieval date in the metadata `methodology` and the snapshot's notes file. Do not estimate numbers you cannot source; leave an entry out instead.
- Every entry must pass `python tools/profile_lint.py <file>`. Weights must sum to exactly 100 in descending order, and `tools/validate_profiles.py` must report PASS.

## Pull requests

1. Branch from `main`, keep the change focused, and update docs and the changelog with it.
2. Make sure the checks above pass. CI must be green before review.
3. Sign off every commit (see below).

## Developer Certificate of Origin

Contributions are accepted under the project's license, the [Apache License 2.0](LICENSE) (section 5). To certify that you have the right to submit your contribution, sign off each commit under the [Developer Certificate of Origin 1.1](https://developercertificate.org/):

```bash
git commit -s -m "Describe the change"
```

This adds a `Signed-off-by: Your Name <you@example.com>` line, which states that you agree to the DCO for that commit. The name and email can be any identity you choose to contribute under, including GitHub's noreply address.
