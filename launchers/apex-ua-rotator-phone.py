#!/usr/bin/env python3
"""Locate and run the newest Apex UA Rotator one-file installer on Android.

The locator searches mounted internal/shared storage and removable SD-card
mounts with bounded depth. It does not assume a Download directory and does not
scan private application storage outside the locations visible to the runtime.

Designed for Pydroid 3, Termux, and other Android Python 3.10+ environments.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

MAX_DEPTH = 8
PATTERNS = (
    "apex-ua-rotator-*-one-shot*.pyz",
    "apex-ua-rotator-one-shot*.pyz",
    "Apex_UA_Rotator*_Pydroid_OneFile*.pyz",
    "Apex_UA_Rotator*_OneFile*.pyz",
)
SKIP_DIRS = {
    ".cache",
    ".git",
    ".gradle",
    ".idea",
    ".Trash",
    "__pycache__",
    "node_modules",
    "obb",
}
_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")


def _canonical(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path.absolute()))


def _storage_roots() -> list[Path]:
    candidates: list[Path] = [
        Path("/storage"),
        Path("/mnt/media_rw"),
        Path("/sdcard"),
        Path("/storage/emulated/0"),
        Path("/mnt/runtime/default"),
        Path("/mnt/runtime/read"),
        Path("/mnt/runtime/write"),
    ]

    for name in (
        "EXTERNAL_STORAGE",
        "SECONDARY_STORAGE",
        "EMULATED_STORAGE_TARGET",
        "ANDROID_STORAGE",
        "HOME",
    ):
        raw = os.environ.get(name, "")
        for value in raw.split(os.pathsep):
            if value:
                candidates.append(Path(value).expanduser())

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


def _walk(root: Path, max_depth: int) -> Iterable[Path]:
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


def _version_key(path: Path) -> tuple[int, int, int]:
    match = _VERSION_RE.search(path.name)
    if match is None:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def _selection_key(path: Path) -> tuple[tuple[int, int, int], float, str]:
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return (_version_key(path), modified, _canonical(path))


def _find_newest(roots: Sequence[Path] | None = None) -> Path:
    search_roots = list(roots) if roots is not None else _storage_roots()
    found: dict[str, Path] = {}
    for root in search_roots:
        for path in _walk(root, MAX_DEPTH):
            found[_canonical(path)] = path

    if not found:
        searched = ", ".join(str(root) for root in search_roots) or "no readable storage roots"
        raise FileNotFoundError(
            "Apex one-file installer was not found. "
            f"Searched mounted phone storage to depth {MAX_DEPTH} under: {searched}"
        )
    return max(found.values(), key=_selection_key)


def main() -> int:
    try:
        installer = _find_newest()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Running: {installer}")
    completed = subprocess.run(
        [sys.executable, str(installer), *sys.argv[1:]],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
