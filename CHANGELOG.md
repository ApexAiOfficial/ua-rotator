# Changelog

## 1.4.0 — 2026-11-14

Profile refresh. The public Python API is unchanged; only the default profile moves. Pin `global_2026-07-31-r2.json` to keep 1.3.0's output.

### Added

- New default profile `global_2026-11-14.json` (revision `2026-11-14.1`, 85 entries).
  - **Market shares:** StatCounter's October 2026 worldwide figures for platform, browser family, browser version, OS, Windows version and iOS version.
  - **Releases:** current as of 14 November 2026: Chrome 156–158, Edge 156–157, Firefox 158–160, Opera 139–140, Safari 26.6–27.2 on macOS, Chrome for iOS 157–158 and Firefox for iOS 159–160. Safari on iPhone and iPad follows the measured iOS versions, 18.6 to 27.2.
  - **Fit:** the desktop browser-by-OS table is fitted to both measured marginals. Every source and its retrieval date are listed in `global_2026-11-14.md`.
  - **Exclusions:** Samsung Internet, Opera for Android, Edge for Android and ChromeOS, because no verified current string exists for them.

### Changed

- **Frozen iOS token:** Safari on iOS and iPadOS 26 and 27 entries carry the frozen OS token `18_7` that current devices send. The July snapshot used `18_6`, Safari 26.0's value.
- **Chrome for iOS:** entries carry the device's real iOS version as `major_minor_patch` (`iPhone OS 26_6_1`), which Chrome for iOS sends instead of Safari's frozen token. They follow Safari's modeled iOS distribution, which is why the profile has 85 entries.
- **Lint:** `tools/profile_lint.py` accepts a patch release in Safari's `Version/` token on iOS (`Version/26.6.1`, `Version/27.0.1`), as observed in current traffic. It accepts a three-part OS token only for Chrome for iOS, and it requires Safari 26 and later to carry a frozen `18_x` token.
- **Docs:** README, docs and the banners now refer to the new default. `global_2026-07-31-r2.json` and `global_2026-07-31.json` stay bundled, unchanged.

## 1.3.0 — 2026-11-14

Reliability, Scrapy-integration, and release-engineering upgrade. The public Python API is backward compatible: existing code, settings, compatibility modules, and pinned profile names keep working. Behavior changes are listed under **Changed**.

### Added

- `UserAgentRotator.refresh(key=..., previous=...)` replaces a failed identity with a *different* User-Agent whenever another positively weighted entry exists. For sticky policies it converges: when the scope already moved on from `previous`, the current replacement is returned instead of drawing again.
- Corrected default profile `global_2026-07-31-r2.json` (revision `2026-07-31.2`). Its ChromeOS entry now uses the frozen reduced platform token `X11; CrOS x86_64 14541.0.0` that Chrome actually sends. Revision 1 stays bundled, unchanged, for reproducibility.
- Scrapy: `UA_ROTATOR_ENABLED = False` disables the middleware cleanly (`NotConfigured`).
- Scrapy: an INFO log line at startup records the effective source, profile count, scope, and refresh rules.
- Scrapy: a startup warning when `UA_ROTATOR_SCOPE = "proxy"` runs before `HttpProxyMiddleware`, which would hide environment proxies from the first attempt.
- Scrapy: `ua_rotator/replaced_default_header` statistic.
- CLI: `apex-ua-rotator profiles` lists bundled snapshots; `sample --seed` gives reproducible output; `info`/`validate` report `profile_age_days`.
- Launchers: `--locate-only` prints the selected installer and its SHA-256 without running it; `--expect-sha256` runs only an installer with a known checksum.
- One-file installer: `--verify-payload` checks the embedded wheel without installing.
- Release engineering: `tools/build_release.py` builds every artifact from a clean `git archive` export with a pinned build backend and `SOURCE_DATE_EPOCH`, so rebuilds are byte-identical. It also validates the build and writes `RELEASE_VALIDATION.{json,md}`. `tools/profile_lint.py` checks browser-token consistency.
- CI on GitHub Actions: Python 3.10–3.14 on Linux plus Windows and macOS, real Scrapy at the minimum (2.11) and latest versions, ruff, strict mypy, a reproducible-build job, and a release workflow that re-verifies published assets and rebuilds them from the tag.
- Examples: standard-library HTTP client with refresh on 403/429, a runnable spider, and Playwright browser contexts.
- An sdist and a reproducible offline bundle are published alongside the wheel and one-file installer.

### Changed

- Scrapy: refreshing on retry or redirect never re-sends the identity that was in use, and concurrent failures in one sticky scope converge on one replacement instead of cycling through several.
- Scrapy: conflicting profile sources (`UA_ROTATOR_FILE`, `UA_ROTATOR_ENTRIES`, `UA_ROTATOR_BUNDLED_PROFILE`) now raise `ValueError` at startup. They previously raised `NotConfigured`, which silently disabled rotation for the whole crawl.
- Scrapy: a User-Agent equal to the `USER_AGENT` setting or `DEFAULT_REQUEST_HEADERS` is treated as a framework default and replaced, so enabling Scrapy's built-in middleware, or ordering it first, no longer disables rotation.
- Scrapy: `proxy` scope keys are normalized to scheme, host, and port. Credentials in `meta["proxy"]` are never copied into the lineage record, not even as a hash, and identity stays stable when `HttpProxyMiddleware` strips them.
- Scrapy: a redirect that leaves its scope adopts the destination scope's identity; refresh rules apply only within a scope.
- Scrapy: `cookiejar` scope uses `None` for requests without a `cookiejar` key, matching `CookiesMiddleware`. Previously that case shared a key with an explicit `cookiejar=0`.
- Scrapy: requests without a hostname (`data:`, `file:`) share one `domain`-scope identity instead of failing with `ValueError`.
- Core: profile documents reject duplicate JSON keys, `NaN`/`Infinity` literals, and a boolean `schema_version`; weights whose sum overflows are rejected at load time instead of failing inside `random.choices`.
- Core: selection with `random.Random` instances uses precomputed cumulative weights. A per-request `select()` on the bundled 63-entry profile measured 1.8 µs against 5.0 µs before (CPython 3.12), with identical results for the same seed; the gap widens as profiles grow. Custom RNG objects still receive raw weights.
- Core: `save()` keeps its no-clobber guarantee on filesystems without hard links (FAT/exFAT, Android shared storage). Windows uses an atomic no-clobber rename; other platforms reserve the name exclusively before an atomic replace. It previously failed there.
- One-file installer: the build is reproducible, carries a shebang (it can run directly on POSIX), and records the version in its manifest. Installation is verified in a fresh interpreter, so `--user` installs are detected correctly and a shadowing older copy is reported. pip failures include a PEP 668 hint.
- Launchers: candidates are read once, with a size bound, and checked before they run. Corrupt or truncated files, tampered payloads, and installers whose file-name version disagrees with the embedded wheel are skipped instead of aborting the search. Ranking uses the embedded version (any number of components), so renamed downloads rank correctly. The exact bytes that were checked run from a private temporary copy, closing the window between verification and execution.
- Packaging: the version is single-sourced from `apex_ua_rotator.__version__`.

### Repository

- Replaced the internal `apexai.local` JSON Schema `$id` with the public repository URL.
- Added project URLs and an explicit `license-files` entry to the package metadata.
- Documented that the code is source-available under the proprietary license and that use requires a separate written agreement.
- Release artifacts are now published on GitHub Releases; the README documents both those downloads and installing from source.
- The Scrapy smoke test's missing-dependency message suggests installing Scrapy directly.
- Expanded `.gitignore` to cover environment files, credentials and keys, virtual environments, tool caches, and build artifacts.

## 1.2.1 — 2026-08-01

### Added

- Separate phone and computer locator launchers for finding and running the one-file installer without relying on a specific download folder.
- Phone locator searches mounted shared/internal storage and removable SD-card roots with bounded depth, without assuming a Download directory.
- Computer locator searches cross-platform user-level roots on Linux, macOS, and Windows with bounded depth and noise-directory pruning; it never scans an entire system drive.

### Changed

- Removed folder-specific launcher assumptions from release instructions and versioned the one-file installer name.
- Locator selection now prefers the highest semantic version and uses modification time only to choose between copies of the same version.
- Documented direct execution, locator execution, and explicit-path execution as separate installation paths.

## 1.2.0 — 2026-08-01

### Added

- Command-line interface for profile inspection, sampling, validation, and the localhost Scrapy smoke test.
- `python -m apex_ua_rotator` support.
- Packaged, reusable Scrapy smoke-test implementation with structured PASS, FAIL, and SKIP results.
- Default bundled-profile loading for the Scrapy middleware; no external JSON path is required for a basic setup.
- Optional `UA_ROTATOR_BUNDLED_PROFILE` setting for selecting a named packaged profile.
- Generic one-file installer and validator in the release bundle.
- Expanded documentation and reusable examples for scripts, Scrapy, agents, and interactive-browser boundaries.

### Changed

- Reorganized release packaging so install artifacts, profiles, checksums, and the source repository are clearly separated inside one archive.
- Rewrote the README and profile notes as durable product documentation rather than review-session records.
- Replaced situational validation language in the bundled profile metadata with a stable curation policy.
- Corrected the live Scrapy smoke test to verify redirect metadata and observed requests instead of relying on an optional redirect statistic.

## 1.1.0 — 2026-08-01

### Fixed

- Rejected ambiguous top-level constructor inputs instead of iterating strings or mappings into invalid profiles.
- Preserved document metadata across load, mutation, serialization, and save operations.
- Added strict profile-document validation, bounded sticky caches, compatibility import shims, and an installable `src/` package layout.
- Added a formal JSON Schema and a conservative 63-entry bundled profile snapshot.
