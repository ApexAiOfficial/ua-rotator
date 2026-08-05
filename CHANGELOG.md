# Changelog

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
