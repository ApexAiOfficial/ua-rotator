#!/usr/bin/env python3
"""Locate and run the newest Apex UA Rotator one-file installer on a computer.

The search is deliberately user-scoped and cross-platform. It searches the
current user's home directory plus user-owned cloud/storage roots exposed by
common environment variables. It never scans an entire system drive.

Every candidate is read once (at most 64 MiB) and checked before it is
trusted: it must be a valid installer archive whose embedded wheel matches its
checksum manifest, and a version in the file name must agree with the embedded
wheel. Candidates are ranked by the embedded version, so a renamed download
such as ``apex-ua-rotator-one-shot (1).pyz`` still ranks correctly. The
selected path and its SHA-256 are printed, and the exact bytes that were
checked are run from a private temporary copy, so the file cannot change
between verification and execution.

These checks catch damaged, truncated, and mislabeled files; they cannot prove
who made a file. Pin a known release with ``--expect-sha256``.

Launcher options (all other arguments are passed to the installer):

``--locate-only``
    Print the selected installer and its SHA-256, then exit without running.

``--expect-sha256 HEX``
    Run only if the selected installer has exactly this SHA-256 (from the
    release's SHA256SUMS).

The locator is intended for Linux, macOS, and Windows Python 3.10+.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Sequence

MAX_DEPTH = 8
PATTERNS = (
    "apex-ua-rotator-*-one-shot*.pyz",
    "apex-ua-rotator-one-shot*.pyz",
    "Apex_UA_Rotator*_OneFile*.pyz",
)
SKIP_DIRS = {
    ".Trash",
    ".cache",
    ".cargo",
    ".conda",
    ".git",
    ".gradle",
    ".idea",
    ".npm",
    ".pyenv",
    ".rustup",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "site-packages",
    "Trash",
    "$Recycle.Bin",
    "venv",
}
LOCATE_ONLY = "--locate-only"
EXPECT_SHA256 = "--expect-sha256"
MAX_INSTALLER_BYTES = 64 * 1024 * 1024
_VERSION_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)(?![\d.]*\d)")
_RELEASE_RE = re.compile(r"^\d+(?:\.\d+)*")
_WHEEL_RE = re.compile(r"^apex_ua_rotator-(?P<version>[0-9][0-9A-Za-z.!+]*)-py3-none-any\.whl$")


def _canonical(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path.absolute()))


def _candidate_roots() -> list[Path]:
    """Return readable, user-level roots without whole-drive scanning."""
    candidates: list[Path] = [Path.home()]
    for name in (
        "HOME",
        "USERPROFILE",
        "OneDrive",
        "OneDriveConsumer",
        "OneDriveCommercial",
        "DROPBOX",
    ):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value).expanduser())

    # HOMEDRIVE/HOMEPATH is useful on Windows when USERPROFILE is unavailable.
    drive = os.environ.get("HOMEDRIVE")
    homepath = os.environ.get("HOMEPATH")
    if drive and homepath:
        candidates.append(Path(f"{drive}{homepath}"))

    roots: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            exists = path.exists() and path.is_dir()
        except OSError:
            exists = False
        key = _canonical(path)
        if exists and key not in seen:
            seen.add(key)
            roots.append(path)
    return roots


def _matches(path: Path) -> bool:
    return any(path.match(pattern) for pattern in PATTERNS)


def _walk_user_root(root: Path, max_depth: int) -> Iterable[Path]:
    root_parts = len(root.parts)
    for current, dirs, files in os.walk(root, topdown=True, onerror=lambda _: None):
        current_path = Path(current)
        depth = len(current_path.parts) - root_parts
        dirs[:] = [
            name
            for name in dirs
            if name not in SKIP_DIRS and depth < max_depth
        ]
        for name in files:
            path = current_path / name
            if _matches(path):
                yield path


Version = tuple  # tuple[int, ...], zero-padded to at least three components


def _normalize(release: str) -> Version:
    parts = [int(part) for part in release.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def _version_tuple(text: str) -> Version | None:
    match = _VERSION_RE.search(text)
    return _normalize(match.group(1)) if match else None


def _version_key(path: Path) -> Version:
    return _version_tuple(path.name) or (0, 0, 0)


def _read_candidate(path: Path) -> bytes:
    if path.stat().st_size > MAX_INSTALLER_BYTES:
        raise ValueError("file is larger than any installer")
    with path.open("rb") as handle:
        data = handle.read(MAX_INSTALLER_BYTES + 1)
    if len(data) > MAX_INSTALLER_BYTES:
        raise ValueError("file is larger than any installer")
    return data


def _inspect(name: str, data: bytes) -> Version:
    """Return the embedded version of a valid installer or raise ValueError."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            manifest = json.loads(bundle.read("payload/manifest.json").decode("utf-8"))
            wheel = manifest["wheel"]
            match = _WHEEL_RE.match(wheel) if isinstance(wheel, str) else None
            if "__main__.py" not in bundle.namelist() or match is None:
                raise ValueError("not an Apex UA Rotator installer archive")
            info = bundle.getinfo(f"payload/{wheel}")
            if info.file_size > MAX_INSTALLER_BYTES:
                raise ValueError("embedded wheel is implausibly large")
            digest = hashlib.sha256()
            total = 0
            with bundle.open(info) as member:
                for chunk in iter(lambda: member.read(1 << 20), b""):
                    total += len(chunk)
                    if total > MAX_INSTALLER_BYTES:
                        raise ValueError("embedded wheel is implausibly large")
                    digest.update(chunk)
    except ValueError:
        raise
    except Exception as exc:  # any failure to parse means "not a valid installer"
        raise ValueError(f"unreadable or invalid installer ({type(exc).__name__}: {exc})") from exc
    if digest.hexdigest() != manifest.get("sha256"):
        raise ValueError("embedded wheel does not match its checksum")
    release = _RELEASE_RE.match(match["version"])
    if release is None:
        raise ValueError(f"unrecognized embedded version {match['version']!r}")
    embedded = _normalize(release.group(0))
    named = _version_tuple(name)
    if named is not None and named != embedded:
        raise ValueError(
            "file name version {} does not match embedded version {}".format(
                ".".join(map(str, named)), ".".join(map(str, embedded))
            )
        )
    return embedded


def _select(roots: Sequence[Path] | None = None) -> tuple[Path, bytes]:
    """Return the best valid installer and the exact bytes that were verified."""
    search_roots = list(roots) if roots is not None else _candidate_roots()
    found: dict[str, Path] = {}
    for root in search_roots:
        for path in _walk_user_root(root, MAX_DEPTH):
            found[_canonical(path)] = path

    ranked: list[tuple[Version, float, str, Path, bytes]] = []
    rejected: list[str] = []
    for key, path in found.items():
        try:
            modified = path.stat().st_mtime
            data = _read_candidate(path)
            version = _inspect(path.name, data)
        except (OSError, ValueError) as exc:
            rejected.append(f"{path}: {exc}")
            continue
        ranked.append((version, modified, key, path, data))

    if not ranked:
        searched = ", ".join(str(root) for root in search_roots) or "no readable user roots"
        detail = "".join(f"\n  skipped {line}" for line in rejected)
        raise FileNotFoundError(
            "Apex one-file installer was not found within the current user's "
            f"storage scope. Searched to depth {MAX_DEPTH} under: {searched}{detail}"
        )
    for line in rejected:
        print(f"Skipped {line}", file=sys.stderr)
    best = max(ranked, key=lambda item: item[:3])
    return best[3], best[4]


def _find_newest(roots: Sequence[Path] | None = None) -> Path:
    return _select(roots)[0]


def _parse_launcher_options(arguments: list[str]) -> tuple[bool, str | None, list[str]]:
    locate_only, expected, forwarded = False, None, []
    iterator = iter(arguments)
    for argument in iterator:
        if argument == LOCATE_ONLY:
            locate_only = True
        elif argument == EXPECT_SHA256:
            expected = next(iterator, "")
        elif argument.startswith(EXPECT_SHA256 + "="):
            expected = argument.split("=", 1)[1]
        else:
            forwarded.append(argument)
    if expected is not None:
        expected = expected.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"{EXPECT_SHA256} needs a 64-character hexadecimal SHA-256")
    return locate_only, expected, forwarded


def _run_verified_copy(path: Path, data: bytes, arguments: list[str]) -> int:
    descriptor, raw = tempfile.mkstemp(prefix=f"{path.stem}-verified-", suffix=".pyz")
    copy = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        return subprocess.run([sys.executable, str(copy), *arguments], check=False).returncode
    finally:
        try:
            copy.unlink()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    try:
        locate_only, expected, arguments = _parse_launcher_options(
            list(sys.argv[1:] if argv is None else argv)
        )
        installer, data = _select()
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, never traceback
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    checksum = hashlib.sha256(data).hexdigest()
    print(f"Installer: {installer}")
    print(f"SHA-256:   {checksum}")
    if expected is not None and checksum != expected:
        print(f"ERROR: SHA-256 does not match {EXPECT_SHA256}; not running it.", file=sys.stderr)
        return 1
    if locate_only:
        return 0
    try:
        return _run_verified_copy(installer, data, arguments)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
