#!/usr/bin/env python3
"""Generic one-file installer, profile verifier, and optional Scrapy smoke test.

This file is packaged as ``__main__.py`` inside the release ``.pyz`` archive.
The archive also contains one wheel and a checksum manifest under ``payload/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MANIFEST_PATH = "payload/manifest.json"


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _write_report(path: Path | None, payload: dict[str, Any]) -> None:
    if path is not None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_payload() -> tuple[str, bytes, str]:
    archive = Path(sys.argv[0]).resolve()
    if not archive.is_file():
        raise RuntimeError(f"Cannot locate executable archive: {archive}")

    with zipfile.ZipFile(archive, "r") as bundle:
        try:
            manifest = json.loads(bundle.read(MANIFEST_PATH).decode("utf-8"))
            wheel_name = manifest["wheel"]
            expected_hash = manifest["sha256"]
            wheel_bytes = bundle.read(f"payload/{wheel_name}")
        except (KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError("The embedded payload manifest is missing or invalid.") from exc

    actual_hash = hashlib.sha256(wheel_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise RuntimeError(
            f"Embedded wheel checksum mismatch: expected {expected_hash}, got {actual_hash}"
        )
    return wheel_name, wheel_bytes, actual_hash


def _install_wheel(*, user: bool, force_reinstall: bool) -> dict[str, Any]:
    wheel_name, wheel_bytes, wheel_hash = _read_payload()
    with tempfile.TemporaryDirectory(prefix="apex_ua_rotator_") as temp_dir:
        wheel_file = Path(temp_dir, wheel_name)
        wheel_file.write_bytes(wheel_bytes)
        command = [sys.executable, "-m", "pip", "install", "--no-deps"]
        if force_reinstall:
            command.append("--force-reinstall")
        if user:
            command.append("--user")
        command.append(str(wheel_file))
        subprocess.run(command, check=True)
    return {"wheel": wheel_name, "sha256": wheel_hash, "python": sys.executable}


def _verify_install() -> dict[str, Any]:
    import apex_ua_rotator
    from apex_ua_rotator import UserAgentRotator

    rotator = UserAgentRotator.from_bundled_profile()
    weights = [entry.weight for entry in rotator.entries]
    return {
        "version": apex_ua_rotator.__version__,
        "module": str(Path(apex_ua_rotator.__file__).resolve()),
        "profiles": len(rotator),
        "weight_total": sum(weights),
        "dataset_as_of": rotator.metadata.get("as_of"),
        "sample_user_agent": rotator.select(),
    }


def _run_optional_scrapy(*, require_scrapy: bool) -> dict[str, Any]:
    try:
        from apex_ua_rotator.smoke import ScrapySmokeUnavailable, run_scrapy_smoke
        return run_scrapy_smoke()
    except ScrapySmokeUnavailable as exc:
        if require_scrapy:
            raise RuntimeError(str(exc)) from exc
        return {"result": "SKIP", "reason": str(exc)}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install the embedded Apex UA Rotator wheel, validate the bundled "
            "profile, and run the localhost Scrapy smoke test when available."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install-only", action="store_true")
    mode.add_argument("--smoke-only", action="store_true")
    parser.add_argument(
        "--require-scrapy",
        action="store_true",
        help="Treat an unavailable Scrapy dependency as a failure.",
    )
    parser.add_argument("--user", action="store_true", help="Pass --user to pip.")
    parser.add_argument(
        "--no-force-reinstall",
        action="store_true",
        help="Do not pass --force-reinstall to pip.",
    )
    parser.add_argument("--report", type=Path, help="Write the final JSON report.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result: dict[str, Any] = {"result": "PENDING"}
    try:
        if not args.smoke_only:
            result["install"] = _install_wheel(
                user=args.user,
                force_reinstall=not args.no_force_reinstall,
            )
        result["verification"] = _verify_install()
        if not args.install_only:
            result["scrapy_smoke"] = _run_optional_scrapy(
                require_scrapy=args.require_scrapy
            )
        result["result"] = "PASS"
        _print_json(result)
        _write_report(args.report, result)
        return 0
    except subprocess.CalledProcessError as exc:
        result.update(
            {
                "result": "FAIL",
                "stage": "wheel_install",
                "return_code": exc.returncode,
                "command": exc.cmd,
            }
        )
    except Exception as exc:
        result.update(
            {
                "result": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
    _print_json(result)
    _write_report(args.report, result)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
