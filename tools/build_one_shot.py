#!/usr/bin/env python3
"""Build the generic one-file installer from a wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", "-o", type=Path, required=True)
    args = parser.parse_args()

    wheel = args.wheel.resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        parser.error("wheel must point to an existing .whl file")

    entry = Path(__file__).with_name("one_shot.py")
    wheel_bytes = wheel.read_bytes()
    manifest = {
        "wheel": wheel.name,
        "sha256": hashlib.sha256(wheel_bytes).hexdigest(),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("__main__.py", entry.read_text(encoding="utf-8"))
        bundle.writestr("payload/manifest.json", json.dumps(manifest, indent=2) + "\n")
        bundle.writestr(f"payload/{wheel.name}", wheel_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
