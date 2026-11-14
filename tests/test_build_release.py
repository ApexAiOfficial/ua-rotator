"""Unit tests for the release tool's deterministic and safety-critical helpers.

Full builds need network access for the pinned build backend; they run in the
CI "Reproducible release build" job instead.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_release  # noqa: E402


def write_sums(directory: Path, sums: dict[str, str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sums.items()), encoding="utf-8"
    )


def test_read_sums_accepts_binary_marker(tmp_path: Path):
    path = tmp_path / "SHA256SUMS"
    path.write_text("aa  one.whl\nbb *two.pyz\n\n", encoding="utf-8")
    assert build_release.read_sums(path) == {"one.whl": "aa", "two.pyz": "bb"}


def test_compare_is_symmetric_and_ignores_reports(tmp_path: Path):
    base = {"a.whl": "1", "b.pyz": "2"}
    write_sums(tmp_path / "same", {**base, "RELEASE_VALIDATION.md": "x"})
    assert build_release.compare({**base, "RELEASE_VALIDATION.json": "y"}, tmp_path / "same")["identical"]

    result = build_release.compare({**base, "planted.zip": "3"}, tmp_path / "same")
    assert result["published_only"] == ["planted.zip"] and not result["identical"]

    write_sums(tmp_path / "extra", {**base, "c.zip": "4"})
    result = build_release.compare(base, tmp_path / "extra")
    assert result["rebuilt_only"] == ["c.zip"] and not result["identical"]

    result = build_release.compare({"a.whl": "1", "b.pyz": "9"}, tmp_path / "same")
    assert result["mismatched"] == ["b.pyz"] and not result["identical"]

    write_sums(tmp_path / "empty", {})
    assert not build_release.compare({}, tmp_path / "empty")["identical"]


def test_prepare_out_dir_refuses_dangerous_targets(tmp_path: Path, monkeypatch):
    for target in (build_release.ROOT, build_release.ROOT.parent, Path.home(), Path("/")):
        with pytest.raises(ValueError, match="refusing"):
            build_release.prepare_out_dir(target)

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("precious", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        build_release.prepare_out_dir(foreign)
    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "precious"

    not_dir = tmp_path / "file"
    not_dir.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        build_release.prepare_out_dir(not_dir)


def test_prepare_out_dir_reuses_only_its_own_output(tmp_path: Path):
    out = tmp_path / "release"
    build_release.prepare_out_dir(out)
    assert (out / build_release.OUT_MARKER).is_file()
    (out / "old.whl").write_text("stale", encoding="utf-8")
    build_release.prepare_out_dir(out)
    assert build_release.release_assets(out) == []

    empty = tmp_path / "empty"
    empty.mkdir()
    build_release.prepare_out_dir(empty)
    assert (empty / build_release.OUT_MARKER).is_file()


def _tarball(path: Path, *, mtime: int, uid: int) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data, mode in (("pkg/b.py", b"b", 0o664), ("pkg/a.py", b"a", 0o775)):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.uid, info.uname, info.mode = len(data), mtime, uid, "someone", mode
            tar.addfile(info, io.BytesIO(data))


def test_normalize_sdist_is_deterministic(tmp_path: Path):
    first, second = tmp_path / "1.tar.gz", tmp_path / "2.tar.gz"
    _tarball(first, mtime=1_700_000_000, uid=1000)
    _tarball(second, mtime=1_800_000_000, uid=501)
    for path in (first, second):
        build_release.normalize_sdist(path, 1_785_888_000)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as tar:
        members = tar.getmembers()
    assert [m.name for m in members] == ["pkg/a.py", "pkg/b.py"]
    assert {(m.uid, m.uname, m.mtime) for m in members} == {(0, "", 1_785_888_000)}
    assert [oct(m.mode) for m in members] == ["0o755", "0o644"]


def test_deterministic_zip_ignores_file_times(tmp_path: Path):
    source = tmp_path / "launcher.py"
    source.write_text("print('x')\n", encoding="utf-8")
    source.chmod(0o755)
    build_release.deterministic_zip(tmp_path / "1.zip", [("top/launcher.py", source)], 1_785_888_000)
    os.utime(source, (1_000_000_000, 1_000_000_000))
    build_release.deterministic_zip(tmp_path / "2.zip", [("top/launcher.py", source)], 1_785_888_000)
    assert (tmp_path / "1.zip").read_bytes() == (tmp_path / "2.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "1.zip") as archive:
        info = archive.getinfo("top/launcher.py")
    expected = 0o100755 if os.name == "posix" else 0o100644
    assert info.external_attr >> 16 == expected


@pytest.mark.skipif(os.name != "posix", reason="symlinks and modes are POSIX-specific")
def test_normalize_modes_never_touches_symlink_targets(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    outside.chmod(0o600)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "link").symlink_to(outside)
    (tree / "file.py").write_text("x", encoding="utf-8")
    build_release._normalize_modes(tree)
    assert outside.stat().st_mode & 0o777 == 0o600
    assert (tree / "file.py").stat().st_mode & 0o777 == 0o644


def test_render_markdown_reports_errors_instead_of_crashing():
    failed = {"passed": False, "error": "RuntimeError: boom"}
    report = {
        "release": "9.9.9",
        "date": "2026-11-14",
        "result": "FAIL",
        "platform": {"system": "Linux", "machine": "x86_64", "python": "3.12.0"},
        "build": {"source_date_epoch": 1, "build_requirements": ["setuptools==1"], "default_profile": "p.json"},
        "artifacts": {"a.whl": "00"},
        "checks": {name: dict(failed) for name in (
            "test_suite", "profiles", "clean_wheel_install", "one_file_installer",
            "launchers", "scrapy", "reproducibility")},
    }
    text = build_release.render_markdown(report)
    assert "**Result: FAIL**" in text
    assert text.count("**ERROR** RuntimeError: boom") >= 7


def test_attempt_records_exceptions():
    checks: dict = {}
    build_release._attempt(checks, "boom", lambda: 1 / 0)
    assert checks["boom"]["passed"] is False
    assert "ZeroDivisionError" in checks["boom"]["error"]


def test_validate_requires_a_scrapy_interpreter(monkeypatch, capsys):
    monkeypatch.setattr(build_release, "tree_is_clean", lambda: True)
    with pytest.raises(SystemExit) as raised:
        build_release.main(["--validate"])
    assert raised.value.code == 2
    assert "--scrapy-python" in capsys.readouterr().err
