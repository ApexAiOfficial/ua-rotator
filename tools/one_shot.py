#!/usr/bin/env python3
"""Apex UA Rotator one-file installer, verifier, and optional Scrapy smoke test.

This file is packaged as ``__main__.py`` inside the release ``.pyz`` archive,
next to one wheel and a checksum manifest under ``payload/``. It needs only the
Python standard library (3.10+) and pip.

Stages:

1. verify the embedded wheel against the manifest's SHA-256;
2. install it into the running interpreter with pip (``--no-deps``);
3. verify the installation in a fresh interpreter (version, bundled profile);
4. run the localhost Scrapy smoke test in a fresh interpreter, when Scrapy is
   installed (``--require-scrapy`` turns "not installed" into a failure).

The final JSON report is printed and optionally written with ``--report``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MANIFEST_PATH = "payload/manifest.json"
WHEEL_NAME = re.compile(r"^apex_ua_rotator-(?P<version>[0-9][0-9A-Za-z.!+]*)-py3-none-any\.whl$")
PIP_HINT = (
    "pip could not install the wheel. If it reported an externally managed "
    "environment (PEP 668), run this installer with a virtual environment's "
    "Python, or pass --user where your platform allows it."
)
_VERIFY_SCRIPT = """
import json, math, sys
from pathlib import Path
import apex_ua_rotator
from apex_ua_rotator import UserAgentRotator
rotator = UserAgentRotator.from_bundled_profile()
print(json.dumps({
    "version": apex_ua_rotator.__version__,
    "module": str(Path(apex_ua_rotator.__file__).resolve()),
    "default_profile": apex_ua_rotator.DEFAULT_PROFILE_NAME,
    "profiles": len(rotator),
    "weight_total": math.fsum(entry.weight for entry in rotator.entries),
    "dataset_as_of": rotator.metadata.get("as_of"),
    "profile_revision": rotator.metadata.get("profile_revision"),
    "sample_user_agent": rotator.select(),
}))
"""


class InstallerError(RuntimeError):
    """A stage failed; the message is reported to the user."""


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _write_report(path: Path | None, payload: dict[str, Any]) -> None:
    if path is not None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _archive_path() -> Path:
    archive = Path(sys.argv[0]).resolve()
    if not archive.is_file():
        raise InstallerError(f"Cannot locate executable archive: {archive}")
    return archive


def read_payload(archive: Path) -> tuple[dict[str, Any], bytes]:
    """Return the manifest and wheel bytes after verifying the checksum."""

    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            manifest = json.loads(bundle.read(MANIFEST_PATH).decode("utf-8"))
            wheel_name = manifest["wheel"]
            expected_hash = manifest["sha256"]
            match = WHEEL_NAME.match(wheel_name) if isinstance(wheel_name, str) else None
            if match is None:
                raise InstallerError(f"Embedded wheel name is not allowed: {wheel_name!r}")
            wheel_bytes = bundle.read(f"payload/{wheel_name}")
    except (KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise InstallerError("The embedded payload manifest is missing or invalid.") from exc

    actual_hash = hashlib.sha256(wheel_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise InstallerError(
            f"Embedded wheel checksum mismatch: expected {expected_hash}, got {actual_hash}"
        )
    manifest.setdefault("version", match["version"])
    if manifest["version"] != match["version"]:
        raise InstallerError("Manifest version does not match the embedded wheel name.")
    return manifest, wheel_bytes


def _install_wheel(
    manifest: dict[str, Any], wheel_bytes: bytes, *, user: bool, force_reinstall: bool
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="apex_ua_rotator_") as temp_dir:
        wheel_file = Path(temp_dir, manifest["wheel"])
        wheel_file.write_bytes(wheel_bytes)
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-input",
            "--disable-pip-version-check",
        ]
        if force_reinstall:
            command.append("--force-reinstall")
        if user:
            command.append("--user")
        command.append(str(wheel_file))
        subprocess.run(command, check=True)
    return {"wheel": manifest["wheel"], "sha256": manifest["sha256"], "python": sys.executable}


def _verify_install(expected_version: str | None) -> dict[str, Any]:
    # A fresh interpreter sees a --user install even when the user site
    # directory did not exist when this process started.
    completed = subprocess.run(
        [sys.executable, "-c", _VERIFY_SCRIPT], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise InstallerError(
            "Installed package could not be imported:\n" + completed.stderr.strip()
        )
    verification = json.loads(completed.stdout)
    if expected_version is not None and verification["version"] != expected_version:
        raise InstallerError(
            f"Installed version {verification['version']} does not match the "
            f"embedded wheel {expected_version}; another copy may shadow it."
        )
    return verification


def _run_optional_scrapy(*, require_scrapy: bool) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "apex_ua_rotator", "smoke-scrapy"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstallerError(
            "Scrapy smoke test produced no report:\n" + completed.stderr.strip()
        ) from exc
    if completed.returncode == 2 and report.get("result") == "SKIP":
        if require_scrapy:
            raise InstallerError(report.get("reason", "Scrapy is not installed."))
        return report
    if completed.returncode != 0:
        raise InstallerError("Scrapy smoke test failed: " + json.dumps(report.get("checks", report)))
    return report


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install the embedded Apex UA Rotator wheel, validate the bundled "
            "profile, and run the localhost Scrapy smoke test when available."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install-only", action="store_true", help="Skip the Scrapy smoke test.")
    mode.add_argument(
        "--smoke-only", action="store_true", help="Skip installation; verify and smoke-test."
    )
    mode.add_argument(
        "--verify-payload",
        action="store_true",
        help="Only verify the embedded wheel checksum and print the manifest.",
    )
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result: dict[str, Any] = {"result": "PENDING"}
    try:
        expected_version: str | None = None
        if not args.smoke_only:
            manifest, wheel_bytes = read_payload(_archive_path())
            expected_version = manifest["version"]
            result["payload"] = {key: manifest[key] for key in ("wheel", "version", "sha256")}
            if args.verify_payload:
                result["result"] = "PASS"
                _print_json(result)
                _write_report(args.report, result)
                return 0
            result["install"] = _install_wheel(
                manifest,
                wheel_bytes,
                user=args.user,
                force_reinstall=not args.no_force_reinstall,
            )
        result["verification"] = _verify_install(expected_version)
        if not args.install_only:
            result["scrapy_smoke"] = _run_optional_scrapy(require_scrapy=args.require_scrapy)
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
                "hint": PIP_HINT,
            }
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report as JSON
        result.update({"result": "FAIL", "error_type": type(exc).__name__, "error": str(exc)})
    _print_json(result)
    _write_report(args.report, result)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
