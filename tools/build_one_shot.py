#!/usr/bin/env python3
"""Build the one-file installer (``.pyz``) from a wheel, reproducibly.

The archive holds ``__main__.py`` (``tools/one_shot.py``), the wheel, and a
checksum manifest under ``payload/``. Output is byte-for-byte reproducible:
entries are written in a fixed order with fixed permissions and a timestamp
taken from ``SOURCE_DATE_EPOCH`` (or 1980-01-01 when it is unset). A
``#!/usr/bin/env python3`` line is prepended so the file can also be executed
directly on POSIX systems.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

SHEBANG = b"#!/usr/bin/env python3\n"
MANIFEST_FORMAT = 2
WHEEL_NAME = re.compile(r"^apex_ua_rotator-(?P<version>[0-9][0-9A-Za-z.!+]*)-py3-none-any\.whl$")
_ZIP_EPOCH = 315532800  # 1980-01-01T00:00:00Z, the earliest timestamp ZIP can store


def _zip_timestamp(epoch: int | None) -> tuple[int, int, int, int, int, int]:
    if epoch is None:
        raw = os.environ.get("SOURCE_DATE_EPOCH")
        epoch = int(raw) if raw else _ZIP_EPOCH
    return time.gmtime(max(epoch, _ZIP_EPOCH))[:6]


def _entry(name: str, timestamp: tuple[int, int, int, int, int, int]) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=timestamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix, so external_attr carries the mode bits
    info.external_attr = 0o100644 << 16
    return info


def build_one_shot(wheel: Path, output: Path, *, epoch: int | None = None) -> dict[str, object]:
    """Write the installer and return its manifest."""

    match = WHEEL_NAME.match(wheel.name)
    if not wheel.is_file() or match is None:
        raise ValueError(f"not an apex_ua_rotator wheel: {wheel}")
    entry = Path(__file__).with_name("one_shot.py")
    wheel_bytes = wheel.read_bytes()
    manifest: dict[str, object] = {
        "format": MANIFEST_FORMAT,
        "sha256": hashlib.sha256(wheel_bytes).hexdigest(),
        "version": match["version"],
        "wheel": wheel.name,
    }
    timestamp = _zip_timestamp(epoch)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(_entry("__main__.py", timestamp), entry.read_bytes(), compresslevel=9)
        bundle.writestr(
            _entry("payload/manifest.json", timestamp),
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            compresslevel=9,
        )
        bundle.writestr(_entry(f"payload/{wheel.name}", timestamp), wheel_bytes, compresslevel=9)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(SHEBANG + buffer.getvalue())
    output.chmod(0o755)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", "-o", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build_one_shot(args.wheel.resolve(), args.output)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
