"""Tests for the one-file installer builder and its embedded entry point."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_one_shot  # noqa: E402
import one_shot  # noqa: E402


def fake_wheel(directory: Path, version: str = "1.3.0") -> Path:
    wheel = directory / f"apex_ua_rotator-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("apex_ua_rotator/__init__.py", f'__version__ = "{version}"\n')
    return wheel


def rewrite(archive: Path, **replacements: bytes) -> None:
    with zipfile.ZipFile(archive) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries.update(replacements)
    with zipfile.ZipFile(archive, "w") as target:
        for name, data in entries.items():
            target.writestr(name, data)


def test_build_is_reproducible_executable_and_versioned(tmp_path: Path, monkeypatch):
    wheel = fake_wheel(tmp_path)
    first, second = tmp_path / "a.pyz", tmp_path / "b" / "b.pyz"
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1785888000")
    manifest = build_one_shot.build_one_shot(wheel, first)
    wheel.touch()  # a different mtime must not change the output
    build_one_shot.build_one_shot(wheel, second)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().startswith(b"#!/usr/bin/env python3\n")
    if os.name == "posix":
        assert first.stat().st_mode & 0o111
    assert manifest == {
        "format": 2,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "version": "1.3.0",
        "wheel": wheel.name,
    }
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["__main__.py", "payload/manifest.json", f"payload/{wheel.name}"]
        assert {info.date_time for info in archive.infolist()} == {(2026, 8, 5, 0, 0, 0)}

    monkeypatch.delenv("SOURCE_DATE_EPOCH")
    build_one_shot.build_one_shot(wheel, tmp_path / "c.pyz")
    with zipfile.ZipFile(tmp_path / "c.pyz") as archive:
        assert archive.infolist()[0].date_time == (1980, 1, 1, 0, 0, 0)


def test_build_rejects_non_project_wheels(tmp_path: Path):
    other = tmp_path / "other-1.0-py3-none-any.whl"
    other.write_bytes(b"x")
    with pytest.raises(ValueError, match="not an apex_ua_rotator wheel"):
        build_one_shot.build_one_shot(other, tmp_path / "x.pyz")
    with pytest.raises(SystemExit):
        build_one_shot.main([str(other), "-o", str(tmp_path / "x.pyz")])


def test_read_payload_verifies_checksum_name_and_version(tmp_path: Path):
    archive = tmp_path / "i.pyz"
    wheel = fake_wheel(tmp_path)
    build_one_shot.build_one_shot(wheel, archive)
    manifest, wheel_bytes = one_shot.read_payload(archive)
    assert manifest["version"] == "1.3.0" and wheel_bytes == wheel.read_bytes()

    rewrite(archive, **{f"payload/{wheel.name}": wheel.read_bytes() + b"!"})
    with pytest.raises(one_shot.InstallerError, match="checksum mismatch"):
        one_shot.read_payload(archive)

    build_one_shot.build_one_shot(wheel, archive)
    traversal = {"wheel": "../../evil.whl", "sha256": "0" * 64}
    rewrite(archive, **{"payload/manifest.json": json.dumps(traversal).encode()})
    with pytest.raises(one_shot.InstallerError, match="not allowed"):
        one_shot.read_payload(archive)

    build_one_shot.build_one_shot(wheel, archive)
    lying = {"wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "version": "9.0.0"}
    rewrite(archive, **{"payload/manifest.json": json.dumps(lying).encode()})
    with pytest.raises(one_shot.InstallerError, match="does not match"):
        one_shot.read_payload(archive)

    (tmp_path / "junk.pyz").write_bytes(b"not a zip")
    with pytest.raises(one_shot.InstallerError, match="missing or invalid"):
        one_shot.read_payload(tmp_path / "junk.pyz")


def test_legacy_format_1_manifest_is_accepted(tmp_path: Path):
    archive = tmp_path / "legacy.pyz"
    wheel = fake_wheel(tmp_path, "1.2.1")
    build_one_shot.build_one_shot(wheel, archive)
    legacy = {"wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    rewrite(archive, **{"payload/manifest.json": json.dumps(legacy).encode()})
    manifest, _ = one_shot.read_payload(archive)
    assert manifest["version"] == "1.2.1"


def test_verify_payload_mode_runs_from_the_built_archive(tmp_path: Path):
    archive = tmp_path / "apex-ua-rotator-1.3.0-one-shot.pyz"
    build_one_shot.build_one_shot(fake_wheel(tmp_path), archive)
    report_path = tmp_path / "report.json"
    completed = subprocess.run(
        [sys.executable, str(archive), "--verify-payload", "--report", str(report_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["result"] == "PASS"
    assert report["payload"]["version"] == "1.3.0"
    assert "install" not in report


def test_failed_pip_install_reports_stage_and_hint(tmp_path: Path, monkeypatch, capsys):
    archive = tmp_path / "i.pyz"
    build_one_shot.build_one_shot(fake_wheel(tmp_path), archive)
    monkeypatch.setattr(one_shot.sys, "argv", [str(archive)])

    def failing_run(command, check, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(one_shot.subprocess, "run", failing_run)
    assert one_shot.main(["--install-only"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stage"] == "wheel_install"
    assert "externally managed" in report["hint"]
    assert "--disable-pip-version-check" in report["command"]


def test_installed_version_mismatch_is_a_failure(monkeypatch):
    class Completed:
        returncode = 0
        stdout = json.dumps({"version": "1.2.1"})
        stderr = ""

    monkeypatch.setattr(one_shot.subprocess, "run", lambda *a, **k: Completed())
    with pytest.raises(one_shot.InstallerError, match="shadow"):
        one_shot._verify_install("1.3.0")


@pytest.mark.parametrize(
    ("returncode", "report", "require", "outcome"),
    [
        (2, {"result": "SKIP", "reason": "Scrapy is not installed"}, False, "SKIP"),
        (2, {"result": "SKIP", "reason": "Scrapy is not installed"}, True, "error"),
        (1, {"result": "FAIL", "checks": {"x": False}}, False, "error"),
        (0, {"result": "PASS"}, True, "PASS"),
    ],
)
def test_scrapy_smoke_outcomes(monkeypatch, returncode, report, require, outcome):
    class Completed:
        stdout = json.dumps(report)
        stderr = ""

    Completed.returncode = returncode
    monkeypatch.setattr(one_shot.subprocess, "run", lambda *a, **k: Completed())
    if outcome == "error":
        with pytest.raises(one_shot.InstallerError):
            one_shot._run_optional_scrapy(require_scrapy=require)
    else:
        assert one_shot._run_optional_scrapy(require_scrapy=require)["result"] == outcome
