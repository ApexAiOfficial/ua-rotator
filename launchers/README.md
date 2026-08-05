# Locator launchers

These launchers locate and run the newest Apex UA Rotator one-file installer without assuming that it is stored in a Download folder.

## Phone / Android

```bash
python3 apex-ua-rotator-phone.py --require-scrapy
```

The phone launcher searches mounted internal/shared storage and removable SD-card roots, including Android storage roots exposed through the runtime environment. Search depth is bounded to 8 directory levels. It chooses the highest semantic version found; modification time breaks ties between copies of the same version.

Designed for Pydroid 3, Termux, and other Android Python environments.

## Computer

```bash
python3 apex-ua-rotator-computer.py
```

The computer launcher searches only user-level roots: the current user's home directory and user-owned cloud/storage paths exposed through common environment variables. It supports Linux, macOS, and Windows, never scans an entire system drive, and prunes common cache, dependency, virtual-environment, and trash directories.

## Forwarded options

All arguments after the launcher name are passed to the one-file installer, for example:

```bash
python3 apex-ua-rotator-computer.py --install-only
python3 apex-ua-rotator-phone.py --smoke-only --require-scrapy
python3 apex-ua-rotator-phone.py --report apex-ua-result.json
```

An explicit path to the one-file installer remains the fastest and most deterministic execution method when the location is already known.
