from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_one_shot import build_one_shot  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PHONE = _load("phone_locator", ROOT / "launchers" / "apex-ua-rotator-phone.py")
COMPUTER = _load("computer_locator", ROOT / "launchers" / "apex-ua-rotator-computer.py")
LAUNCHERS = pytest.mark.parametrize("launcher", [PHONE, COMPUTER], ids=["phone", "computer"])


def walk(launcher, root: Path, depth: int):
    return list((launcher._walk if launcher is PHONE else launcher._walk_user_root)(root, depth))


def fake_wheel(directory: Path, version: str) -> Path:
    wheel = directory / f"apex_ua_rotator-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("apex_ua_rotator/__init__.py", f'__version__ = "{version}"\n')
    return wheel


def installer(path: Path, version: str, *, mtime: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    build_one_shot(fake_wheel(path.parent, version), path)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@LAUNCHERS
def test_walk_finds_installer_with_spaces(launcher, tmp_path: Path):
    target = tmp_path / "nested" / "folder" / "apex-ua-rotator-one-shot (1).pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"zip")
    assert target in walk(launcher, tmp_path, 8)


@LAUNCHERS
def test_walk_honors_depth(launcher, tmp_path: Path):
    target = tmp_path / "a" / "b" / "c" / "apex-ua-rotator-one-shot.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"zip")
    assert target not in walk(launcher, tmp_path, 2)


def test_computer_walk_prunes_cache_and_trash(tmp_path: Path):
    for folder in (".cache", ".Trash", ".pyenv"):
        target = tmp_path / folder / "apex-ua-rotator-one-shot.pyz"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"zip")
        assert target not in walk(COMPUTER, tmp_path, 8)


@LAUNCHERS
def test_selects_highest_embedded_version_before_mtime(launcher, tmp_path: Path):
    installer(tmp_path / "apex-ua-rotator-1.2.0-one-shot.pyz", "1.2.0", mtime=2_000_000_000)
    newer = installer(tmp_path / "apex-ua-rotator-1.3.0-one-shot.pyz", "1.3.0", mtime=1_000_000_000)
    assert launcher._find_newest([tmp_path]) == newer


@LAUNCHERS
def test_renamed_download_ranks_by_embedded_version(launcher, tmp_path: Path):
    installer(tmp_path / "apex-ua-rotator-1.2.1-one-shot.pyz", "1.2.1", mtime=2_000_000_000)
    renamed = installer(tmp_path / "dl" / "apex-ua-rotator-one-shot (1).pyz", "1.3.0", mtime=1_000_000_000)
    assert launcher._find_newest([tmp_path]) == renamed


@LAUNCHERS
def test_selects_newest_copy_of_same_version(launcher, tmp_path: Path):
    installer(tmp_path / "a" / "apex-ua-rotator-1.3.0-one-shot.pyz", "1.3.0", mtime=1_000_000_000)
    second = installer(tmp_path / "b" / "apex-ua-rotator-1.3.0-one-shot.pyz", "1.3.0", mtime=2_000_000_000)
    assert launcher._find_newest([tmp_path]) == second


@LAUNCHERS
def test_corrupt_tampered_and_mislabeled_candidates_are_skipped(launcher, tmp_path: Path, capsys):
    good = installer(tmp_path / "apex-ua-rotator-1.2.1-one-shot.pyz", "1.2.1")
    (tmp_path / "apex-ua-rotator-9.9.9-one-shot.pyz").write_bytes(b"partial download")
    # A renamed old installer must not masquerade as a newer release.
    installer(tmp_path / "x" / "apex-ua-rotator-1.2.0-one-shot.pyz", "1.2.0").rename(
        tmp_path / "apex-ua-rotator-5.0.0-one-shot.pyz"
    )
    tampered = installer(tmp_path / "t" / "apex-ua-rotator-1.4.0-one-shot.pyz", "1.4.0")
    with zipfile.ZipFile(tampered) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    wheel_entry = next(name for name in entries if name.endswith(".whl"))
    entries[wheel_entry] += b"tampered"
    with zipfile.ZipFile(tampered, "w") as target:
        for name, data in entries.items():
            target.writestr(name, data)

    assert launcher._find_newest([tmp_path]) == good
    skipped = capsys.readouterr().err
    assert "9.9.9" in skipped and "invalid installer" in skipped
    assert "does not match embedded version 1.2.0" in skipped
    assert "does not match its checksum" in skipped


@LAUNCHERS
def test_missing_installer_reports_searched_roots(launcher, tmp_path: Path):
    (tmp_path / "apex-ua-rotator-1.0.0-one-shot.pyz").write_bytes(b"junk")
    with pytest.raises(FileNotFoundError, match=r"skipped .*invalid installer"):
        launcher._find_newest([tmp_path])


@LAUNCHERS
def test_main_locate_only_prints_path_and_checksum(launcher, tmp_path: Path, monkeypatch, capsys):
    target = installer(tmp_path / "apex-ua-rotator-1.3.0-one-shot.pyz", "1.3.0")
    monkeypatch.setattr(launcher, "_select", lambda: (target, target.read_bytes()))
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: calls.append(a))
    assert launcher.main(["--locate-only", "--install-only"]) == 0
    out = capsys.readouterr().out
    assert str(target) in out
    assert hashlib.sha256(target.read_bytes()).hexdigest() in out
    assert calls == []


@LAUNCHERS
def test_main_runs_the_verified_bytes_and_forwards_arguments(launcher, tmp_path: Path, monkeypatch):
    target = installer(tmp_path / "apex-ua-rotator-1.3.0-one-shot.pyz", "1.3.0")
    verified = target.read_bytes()
    monkeypatch.setattr(launcher, "_select", lambda: (target, verified))
    target.write_bytes(b"replaced after verification")  # must not be what runs
    seen = {}

    class Completed:
        returncode = 7

    def fake_run(command, check):
        seen["command"] = command
        seen["ran_bytes"] = Path(command[1]).read_bytes()
        return Completed()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    assert launcher.main(["--install-only", "--report", "r.json"]) == 7
    assert seen["ran_bytes"] == verified
    assert seen["command"][2:] == ["--install-only", "--report", "r.json"]
    assert not Path(seen["command"][1]).exists()


@LAUNCHERS
def test_expect_sha256_pins_the_installer(launcher, tmp_path: Path, monkeypatch, capsys):
    target = installer(tmp_path / "apex-ua-rotator-1.3.0-one-shot.pyz", "1.3.0")
    data = target.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(launcher, "_select", lambda: (target, data))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: type("C", (), {"returncode": 0})())
    assert launcher.main(["--expect-sha256", digest.upper(), "--locate-only"]) == 0
    assert launcher.main([f"--expect-sha256={digest}"]) == 0
    assert launcher.main(["--expect-sha256", "0" * 64]) == 1
    assert "does not match" in capsys.readouterr().err
    assert launcher.main(["--expect-sha256", "nothex"]) == 1
    assert "64-character" in capsys.readouterr().err


@LAUNCHERS
def test_main_reports_errors_without_traceback(launcher, monkeypatch, capsys):
    def missing():
        raise FileNotFoundError("nothing here")

    monkeypatch.setattr(launcher, "_select", missing)
    assert launcher.main([]) == 1
    assert "ERROR: nothing here" in capsys.readouterr().err


@LAUNCHERS
def test_corrupt_payload_stream_is_skipped_not_fatal(launcher, tmp_path: Path, capsys):
    good = installer(tmp_path / "apex-ua-rotator-1.2.1-one-shot.pyz", "1.2.1")
    broken = installer(tmp_path / "b" / "apex-ua-rotator-1.3.0-one-shot.pyz", "1.3.0")
    with zipfile.ZipFile(broken) as archive:
        info = archive.getinfo(next(n for n in archive.namelist() if n.endswith(".whl")))
    raw = bytearray(broken.read_bytes())
    # Damage the compressed wheel bytes while keeping the central directory intact.
    offset = info.header_offset + len(b"#!/usr/bin/env python3\n") + 30 + len(info.filename) + 4
    for index in range(offset, offset + 8):
        raw[index] ^= 0xFF
    broken.write_bytes(bytes(raw))
    assert launcher._find_newest([tmp_path]) == good
    assert "invalid installer" in capsys.readouterr().err


@LAUNCHERS
def test_oversized_candidates_are_rejected(launcher, tmp_path: Path, monkeypatch):
    installer(tmp_path / "apex-ua-rotator-1.3.0-one-shot.pyz", "1.3.0")
    monkeypatch.setattr(launcher, "MAX_INSTALLER_BYTES", 64)
    with pytest.raises(FileNotFoundError, match="larger than any installer"):
        launcher._find_newest([tmp_path])


@LAUNCHERS
def test_two_component_versions_are_supported(launcher, tmp_path: Path):
    installer(tmp_path / "apex-ua-rotator-1.3.9-one-shot.pyz", "1.3.9")
    newer = installer(tmp_path / "n" / "apex-ua-rotator-1.4-one-shot.pyz", "1.4")
    assert launcher._find_newest([tmp_path]) == newer
    assert launcher._version_tuple("apex-ua-rotator-1.4-one-shot.pyz") == (1, 4, 0)
    assert launcher._version_tuple("apex-ua-rotator-one-shot (1).pyz") is None
