from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parents[1]
PHONE = _load("phone_locator", ROOT / "launchers" / "apex-ua-rotator-phone.py")
COMPUTER = _load("computer_locator", ROOT / "launchers" / "apex-ua-rotator-computer.py")


def test_phone_walk_finds_installer_with_spaces(tmp_path: Path):
    target = tmp_path / "nested" / "folder" / "apex-ua-rotator-one-shot (1).pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"zip")
    assert target in list(PHONE._walk(tmp_path, 8))


def test_phone_walk_honors_depth(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c" / "apex-ua-rotator-one-shot.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"zip")
    assert target not in list(PHONE._walk(tmp_path, 2))


def test_computer_walk_finds_installer(tmp_path: Path):
    target = tmp_path / "Documents" / "Tools" / "apex-ua-rotator-one-shot.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"zip")
    assert target in list(COMPUTER._walk_user_root(tmp_path, 8))


def test_computer_walk_prunes_cache(tmp_path: Path):
    target = tmp_path / ".cache" / "apex-ua-rotator-one-shot.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"zip")
    assert target not in list(COMPUTER._walk_user_root(tmp_path, 8))


def test_phone_selects_highest_version_before_mtime(tmp_path: Path):
    old_version = tmp_path / "apex-ua-rotator-1.2.0-one-shot.pyz"
    new_version = tmp_path / "apex-ua-rotator-1.2.1-one-shot.pyz"
    old_version.write_bytes(b"old")
    new_version.write_bytes(b"new")
    os.utime(old_version, (2_000_000_000, 2_000_000_000))
    os.utime(new_version, (1_000_000_000, 1_000_000_000))
    assert PHONE._find_newest([tmp_path]) == new_version


def test_computer_selects_newest_copy_of_same_version(tmp_path: Path):
    first = tmp_path / "a" / "apex-ua-rotator-1.2.1-one-shot.pyz"
    second = tmp_path / "b" / "apex-ua-rotator-1.2.1-one-shot.pyz"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    os.utime(first, (1_000_000_000, 1_000_000_000))
    os.utime(second, (2_000_000_000, 2_000_000_000))
    assert COMPUTER._find_newest([tmp_path]) == second
