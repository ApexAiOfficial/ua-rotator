# Locator launchers

These launchers find and run the newest Apex UA Rotator one-file installer without assuming it is stored in a Download folder. They are useful on phones and shared machines where the installer's location is unknown. When you know the path, run the installer directly; that is faster and fully deterministic.

Download `apex-ua-rotator-<version>-one-shot.pyz` and the launcher for your device from the [latest release](https://github.com/ApexAiOfficial/ua-rotator/releases/latest), or build the installer yourself (see [Building a release](../docs/VALIDATION.md#building-a-release)). Each launcher is a single file that runs from any directory with Python 3.10+.

## Phone / Android

```bash
python3 apex-ua-rotator-phone.py --require-scrapy
```

Searches mounted internal/shared storage and removable SD-card roots, including Android storage roots exposed through the runtime environment, to a depth of 8 directories. Designed for Pydroid 3, Termux, and other Android Python environments.

## Computer

```bash
python3 apex-ua-rotator-computer.py
```

Searches only user-level roots: the current user's home directory and user-owned cloud/storage paths exposed through common environment variables. It supports Linux, macOS, and Windows, never scans an entire system drive, and skips common cache, dependency, virtual-environment, and trash directories.

## How a candidate is chosen

Every file matching the installer naming pattern is read once (64 MiB at most) and checked before it can run:

- it must be a valid installer archive whose embedded wheel matches its SHA-256 manifest, so truncated downloads, corrupt archives, and tampered payloads are skipped rather than stopping the search;
- a version in the file name must equal the embedded wheel's version, so a renamed old installer cannot pose as a newer one.

Valid candidates are ranked by the **embedded** version, so a browser-renamed download such as `apex-ua-rotator-one-shot (1).pyz` still ranks correctly. Modification time breaks ties between copies of the same version. Skipped files are listed on standard error.

Before running, the launcher prints the selected path and its SHA-256. It then runs the exact bytes it checked, from a private temporary copy, so the file cannot be swapped between verification and execution.

These checks catch damaged or mislabeled files, but they cannot tell you who made a file. Anything that can write to shared storage could plant a well-formed installer with a high version number. When the installer did not come straight from the release page to your device, pin it with `--expect-sha256` and the value from `SHA256SUMS`.

## Options

| Option | Effect |
|---|---|
| `--locate-only` | Print the selected installer and its SHA-256, then exit without running it |
| `--expect-sha256 HEX` | Run only if the selected installer has exactly this SHA-256 |

All other arguments are passed to the installer:

```bash
python3 apex-ua-rotator-computer.py --locate-only
python3 apex-ua-rotator-phone.py --expect-sha256 <sha256-from-SHA256SUMS>
python3 apex-ua-rotator-computer.py --install-only
python3 apex-ua-rotator-phone.py --smoke-only --require-scrapy
python3 apex-ua-rotator-phone.py --report apex-ua-result.json
```
