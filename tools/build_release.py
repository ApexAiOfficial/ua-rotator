#!/usr/bin/env python3
"""Build, validate, and verify Apex UA Rotator release artifacts reproducibly.

Artifacts are built from a clean export of the committed tree (``git archive
HEAD``), never from the working directory, with a pinned build backend and
``SOURCE_DATE_EPOCH`` set to the commit timestamp. Two builds of the same
commit therefore produce byte-identical files, which ``--compare`` checks
against a published ``SHA256SUMS``.

Usage (from the repository root)::

    # Build into dist/release/ and write SHA256SUMS
    python3 tools/build_release.py

    # Build, then run the full validation and write RELEASE_VALIDATION.{json,md}
    python3 tools/build_release.py --validate --scrapy-python /path/to/venv/bin/python

    # Rebuild and compare against a release's published checksums
    gh release download v1.4.0 --repo ApexAiOfficial/ua-rotator --pattern SHA256SUMS --dir published
    python3 tools/build_release.py --out rebuild --compare published/SHA256SUMS

``--validate`` needs pytest, coverage, and jsonschema in the running
interpreter, and at least one ``--scrapy-python`` interpreter with Scrapy and
pytest installed. Building needs network access once, to install the pinned
build backend into a temporary virtual environment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import venv
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_one_shot import build_one_shot  # noqa: E402

# Pinned so the WHEEL "Generator" line and the build logic are identical on
# every machine. Change deliberately, and expect artifact hashes to change.
BUILD_REQUIREMENTS = ("setuptools==84.2.1",)
SUMS_NAME = "SHA256SUMS"
REPORT_JSON = "RELEASE_VALIDATION.json"
REPORT_MD = "RELEASE_VALIDATION.md"
REPORTS = frozenset({REPORT_JSON, REPORT_MD})
OUT_MARKER = ".build_release_output"
_ZIP_EPOCH = 315532800
_BUILD_HOOKS = """
import json, sys
from setuptools import build_meta
out, names = sys.argv[1], sys.argv[2]
wheel = build_meta.build_wheel(out)
sdist = build_meta.build_sdist(out)
with open(names, "w", encoding="utf-8") as handle:
    json.dump([wheel, sdist], handle)
"""


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_version() -> str:
    text = (ROOT / "src" / "apex_ua_rotator" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise RuntimeError("__version__ not found")
    return match.group(1)


def default_profile_name() -> str:
    text = (ROOT / "src" / "apex_ua_rotator" / "core.py").read_text(encoding="utf-8")
    match = re.search(r'^DEFAULT_PROFILE_NAME = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise RuntimeError("DEFAULT_PROFILE_NAME not found")
    return match.group(1)


def commit_epoch() -> int:
    return int(run(["git", "log", "-1", "--format=%ct", "HEAD"], cwd=ROOT).strip())


def tree_is_clean() -> bool:
    return run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT).strip() == ""


def _normalize_modes(tree: Path) -> None:
    for path in tree.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_file():
            executable = path.stat().st_mode & 0o100
            path.chmod(0o755 if executable else 0o644)
        elif path.is_dir():
            path.chmod(0o755)


def export_tree(destination: Path, *, include_uncommitted: bool) -> None:
    """Export tracked files only, so ignored or stale files never leak in."""

    destination.mkdir(parents=True)
    if include_uncommitted:
        listing = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT)
        for name in filter(None, listing.split("\0")):
            source = ROOT / name
            # Never follow symlinks: a link could point outside the repository.
            if source.is_file() and not source.is_symlink():
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    else:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, capture_output=True, check=True
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(destination, filter="data")
            else:  # pragma: no cover - Python without PEP 706 backport
                tar.extractall(destination)
    _normalize_modes(destination)


def build_python(work: Path) -> Path:
    environment = work / "build-venv"
    venv.create(environment, with_pip=True, clear=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
         "--no-input", *BUILD_REQUIREMENTS])
    return python


def normalize_sdist(path: Path, epoch: int) -> None:
    """Rewrite the sdist with sorted members, fixed owners, and fixed times."""

    with tarfile.open(path, "r:gz") as source:
        members = sorted(source.getmembers(), key=lambda member: member.name)
        payload = {
            member.name: source.extractfile(member).read() if member.isfile() else None  # type: ignore[union-attr]
            for member in members
        }
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as target:
        for member in members:
            member.mtime = epoch
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.pax_headers = {}
            if member.isdir():
                member.mode = 0o755
                target.addfile(member)
            else:
                member.mode = 0o755 if member.mode & 0o100 else 0o644
                target.addfile(member, io.BytesIO(payload[member.name] or b""))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0, compresslevel=9) as gz:
        gz.write(raw.getvalue())
    path.write_bytes(compressed.getvalue())


def deterministic_zip(output: Path, files: list[tuple[str, Path]], epoch: int) -> None:
    timestamp = time.gmtime(max(epoch, _ZIP_EPOCH))[:6]
    with zipfile.ZipFile(output, "w") as archive:
        for name, source in sorted(files):
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            executable = name.endswith((".pyz", ".py")) and source.stat().st_mode & 0o100
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)


def write_sums(directory: Path, names: list[str]) -> Path:
    lines = [f"{sha256(directory / name)}  {name}\n" for name in sorted(names)]
    target = directory / SUMS_NAME
    target.write_text("".join(lines), encoding="utf-8")
    return target


def read_sums(path: Path) -> dict[str, str]:
    sums = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.lstrip("*")] = digest
    return sums


def prepare_out_dir(out: Path) -> None:
    """Create a fresh output directory, refusing anything that is not ours to delete."""

    out = out.resolve()
    protected = {ROOT, Path.home().resolve(), Path(out.anchor)}
    if out in protected or out in ROOT.parents:
        raise ValueError(f"refusing to use {out} as the output directory")
    if out.exists():
        if not out.is_dir():
            raise ValueError(f"{out} exists and is not a directory")
        if any(out.iterdir()) and not (out / OUT_MARKER).is_file():
            raise ValueError(
                f"{out} is not empty and was not created by build_release.py; "
                "choose a new or empty directory"
            )
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / OUT_MARKER).write_text("Output of tools/build_release.py; safe to delete.\n")


def release_assets(out: Path) -> list[str]:
    return sorted(p.name for p in out.iterdir() if p.is_file() and not p.name.startswith("."))


def build(out: Path, *, include_uncommitted: bool = False, epoch: int | None = None) -> dict[str, Any]:
    """Build every release asset into ``out`` and return a build description."""

    # setuptools writes generated files (WHEEL, RECORD, copied modules) with the
    # process umask, which is recorded in the wheel; pin it so machines agree.
    previous_umask = os.umask(0o022)
    try:
        return _build(out, include_uncommitted=include_uncommitted, epoch=epoch)
    finally:
        os.umask(previous_umask)


def _build(out: Path, *, include_uncommitted: bool, epoch: int | None) -> dict[str, Any]:
    version = package_version()
    epoch = commit_epoch() if epoch is None else epoch
    prepare_out_dir(out)
    with tempfile.TemporaryDirectory(prefix="apex-ua-release-") as raw_work:
        work = Path(raw_work)
        tree = work / "src-tree"
        export_tree(tree, include_uncommitted=include_uncommitted)
        python = build_python(work)
        dist = work / "dist"
        env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch), "PYTHONHASHSEED": "0"}
        names_file = work / "built-names.json"
        run([str(python), "-c", _BUILD_HOOKS, str(dist), str(names_file)], cwd=tree, env=env)
        wheel_name, sdist_name = json.loads(names_file.read_text(encoding="utf-8"))
        normalize_sdist(dist / sdist_name, epoch)

        wheel, sdist = out / wheel_name, out / sdist_name
        shutil.copy2(dist / wheel_name, wheel)
        shutil.copy2(dist / sdist_name, sdist)
        pyz = out / f"apex-ua-rotator-{version}-one-shot.pyz"
        build_one_shot(wheel, pyz, epoch=epoch)

        profile = default_profile_name()
        profile_dir = tree / "src" / "apex_ua_rotator" / "profiles"
        copies = {
            "apex-ua-rotator-phone.py": tree / "launchers" / "apex-ua-rotator-phone.py",
            "apex-ua-rotator-computer.py": tree / "launchers" / "apex-ua-rotator-computer.py",
            profile: profile_dir / profile,
            profile.replace(".json", ".md"): profile_dir / profile.replace(".json", ".md"),
            "user_agent_profiles.schema.json": tree / "user_agent_profiles.schema.json",
        }
        for name, source in copies.items():
            shutil.copy2(source, out / name)

        top = f"Apex_UA_Rotator_{version}"
        bundle: list[tuple[str, Path]] = [
            (f"{top}/{name}", tree / name) for name in ("README.md", "CHANGELOG.md", "LICENSE.txt")
        ]
        bundle += [(f"{top}/install/{p.name}", p) for p in (wheel, sdist, pyz)]
        bundle += [(f"{top}/launchers/{p.name}", p) for p in sorted((tree / "launchers").iterdir())]
        bundle += [(f"{top}/profiles/{p.name}", p) for p in sorted(profile_dir.glob("*.json"))]
        bundle += [(f"{top}/profiles/{p.name}", p) for p in sorted(profile_dir.glob("*.md"))]
        bundle.append((f"{top}/profiles/user_agent_profiles.schema.json", copies["user_agent_profiles.schema.json"]))
        bundle += [
            (f"{top}/{p.relative_to(tree).as_posix()}", p)
            for p in sorted((tree / "docs").rglob("*"))
            if p.is_file()
        ]
        inner_sums = work / "bundle-SHA256SUMS"
        inner_sums.write_text(
            "".join(
                f"{sha256(source)}  {name.split('/', 1)[1]}\n" for name, source in sorted(bundle)
            ),
            encoding="utf-8",
        )
        bundle.append((f"{top}/SHA256SUMS", inner_sums))
        offline = out / f"apex-ua-rotator-{version}-offline.zip"
        deterministic_zip(offline, bundle, epoch)

    assets = release_assets(out)
    write_sums(out, assets)
    return {
        "version": version,
        "source_date_epoch": epoch,
        "build_requirements": list(BUILD_REQUIREMENTS),
        "assets": {name: sha256(out / name) for name in assets},
        "wheel": wheel_name,
        "sdist": sdist_name,
        "pyz": pyz.name,
        "offline": offline.name,
        "default_profile": profile,
    }


def compare(published: dict[str, str], rebuilt: Path) -> dict[str, Any]:
    """Compare published checksums with a rebuild, in both directions."""

    expected = {name: digest for name, digest in published.items() if name not in REPORTS}
    actual = {name: digest for name, digest in read_sums(rebuilt / SUMS_NAME).items()
              if name not in REPORTS}
    compared = sorted(set(actual) & set(expected))
    mismatched = [name for name in compared if expected[name] != actual[name]]
    published_only = sorted(set(expected) - set(actual))
    rebuilt_only = sorted(set(actual) - set(expected))
    return {
        "compared": compared,
        "mismatched": mismatched,
        "published_only": published_only,
        "rebuilt_only": rebuilt_only,
        "identical": bool(compared) and not (mismatched or published_only or rebuilt_only),
    }


# --------------------------------------------------------------------------- validation


def _venv_python(directory: Path) -> Path:
    venv.create(directory, with_pip=True, clear=True)
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _attempt(checks: dict[str, Any], name: str, step: Any) -> dict[str, Any]:
    """Run one validation step; a failure is recorded in the report, never raised."""

    try:
        result = step()
    except Exception as exc:  # noqa: BLE001 - every failure must end up in the report
        result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"[:4000]}
    checks[name] = result
    return result


def _completed(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return re.sub(r" in [0-9.]+s.*$", "", lines[-1].strip("= ")) if lines else ""


def validate(out: Path, info: dict[str, Any], scrapy_pythons: list[Path]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    wheel_path = out / info["wheel"]
    with tempfile.TemporaryDirectory(prefix="apex-ua-validate-") as raw_work:
        work = Path(raw_work)
        tree = work / "src-tree"
        export_tree(tree, include_uncommitted=False)

        def test_suite() -> dict[str, Any]:
            env = {**os.environ, "COVERAGE_FILE": str(work / ".coverage")}
            tests = _completed([sys.executable, "-m", "coverage", "run", "-m", "pytest", "-q",
                                "-p", "no:cacheprovider"], cwd=tree, env=env)
            report = _completed([sys.executable, "-m", "coverage", "report", "--format=total"],
                                cwd=tree, env=env)
            coverage = int(report.stdout.strip()) if report.stdout.strip().isdigit() else None
            return {
                "passed": tests.returncode == 0 and coverage is not None and coverage >= 90,
                "python": platform.python_version(),
                "summary": _last_line(tests.stdout),
                "exit_code": tests.returncode,
                "branch_coverage_percent": coverage,
            }

        def profiles() -> dict[str, Any]:
            completed = _completed([sys.executable, "tools/validate_profiles.py"], cwd=tree)
            if completed.returncode != 0:
                return {"passed": False, "error": (completed.stderr or completed.stdout)[-4000:]}
            result = json.loads(completed.stdout)
            return {"passed": result["result"] == "PASS", **result}

        def clean_wheel_install() -> dict[str, Any]:
            python = _venv_python(work / "clean-venv")
            run([str(python), "-m", "pip", "install", "--quiet", "--no-index", "--no-deps",
                 "--disable-pip-version-check", str(wheel_path)])
            installed = json.loads(run([str(python), "-m", "apex_ua_rotator", "info"]))
            sample = json.loads(run([str(python), "-m", "apex_ua_rotator", "sample", "-n", "3",
                                     "--seed", "1", "--json"]))
            version = run([str(python), "-m", "apex_ua_rotator", "--version"]).strip()
            return {
                "passed": version == info["version"] and installed["entries"] > 0
                and sample["count"] == 3,
                "cli_version": version,
                "entries": installed["entries"],
                "profile_revision": installed["metadata"].get("profile_revision"),
            }

        def one_file_installer() -> dict[str, Any]:
            python = _venv_python(work / "pyz-venv")
            report_path = work / "pyz-report.json"
            _completed([str(python), str(out / info["pyz"]), "--install-only",
                        "--report", str(report_path)])
            result = json.loads(report_path.read_text(encoding="utf-8"))
            verified = result.get("install", {}).get("sha256") == sha256(wheel_path)
            installed = result.get("verification", {}).get("version")
            return {
                "passed": result["result"] == "PASS" and verified and installed == info["version"],
                "result": result["result"],
                "embedded_checksum_verified": verified,
                "installed_version": installed,
            }

        def launchers() -> dict[str, Any]:
            home = work / "home"
            nested = home / "Documents" / "Apex Tools" / info["pyz"]
            nested.parent.mkdir(parents=True)
            shutil.copy2(out / info["pyz"], nested)
            env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
            for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "DROPBOX",
                        "EXTERNAL_STORAGE", "SECONDARY_STORAGE", "EMULATED_STORAGE_TARGET",
                        "ANDROID_STORAGE", "HOMEDRIVE", "HOMEPATH"):
                env.pop(key, None)
            found = {}
            for launcher in ("apex-ua-rotator-computer.py", "apex-ua-rotator-phone.py"):
                completed = _completed([sys.executable, str(out / launcher), "--locate-only",
                                        "--expect-sha256", sha256(nested)], env=env)
                found[launcher] = completed.returncode == 0 and str(nested) in completed.stdout
            return {"passed": all(found.values()), "found_installer": found}

        def scrapy() -> dict[str, Any]:
            if not scrapy_pythons:
                return {"passed": False, "error": "no --scrapy-python interpreter was given"}
            extracted = work / "wheel-contents"
            with zipfile.ZipFile(wheel_path) as wheel:
                wheel.extractall(extracted)
            # Both runs import the built wheel, not the source tree: PYTHONPATH
            # points at its contents and pytest's own pythonpath is cleared.
            env = {**os.environ, "PYTHONPATH": str(extracted)}
            runs = []
            for python in scrapy_pythons:
                smoke_run = _completed([str(python), "-m", "apex_ua_rotator", "smoke-scrapy"], env=env)
                try:
                    smoke = json.loads(smoke_run.stdout)
                except json.JSONDecodeError:
                    smoke = {"result": "ERROR", "error": smoke_run.stderr[-2000:]}
                tests = _completed([str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                    "-o", "pythonpath=", "tests/test_scrapy_real.py",
                                    "tests/test_scrapy_adapter.py"], cwd=tree, env=env)
                imported = _completed([str(python), "-c",
                                       "import apex_ua_rotator, scrapy, platform; "
                                       "print(apex_ua_rotator.__file__); print(scrapy.__version__); "
                                       "print(platform.python_version())"], env=env)
                origin, scrapy_version, python_version = [*imported.stdout.split(), "", "", ""][:3]
                runs.append({
                    "passed": smoke_run.returncode == 0 and smoke.get("result") == "PASS"
                    and tests.returncode == 0 and origin.startswith(str(extracted)),
                    "scrapy_version": scrapy_version,
                    "python": python_version,
                    "imports_built_wheel": origin.startswith(str(extracted)),
                    "smoke_result": smoke.get("result"),
                    "smoke_checks_passed": sum(bool(v) for v in smoke.get("checks", {}).values()),
                    "smoke_checks_total": len(smoke.get("checks", {})),
                    "adapter_tests": _last_line(tests.stdout) or tests.stderr.strip()[-300:],
                    "adapter_tests_exit_code": tests.returncode,
                })
            return {"passed": all(run["passed"] for run in runs), "runs": runs}

        _attempt(checks, "test_suite", test_suite)
        _attempt(checks, "profiles", profiles)
        _attempt(checks, "clean_wheel_install", clean_wheel_install)
        _attempt(checks, "one_file_installer", one_file_installer)
        _attempt(checks, "launchers", launchers)
        _attempt(checks, "scrapy", scrapy)

    def reproducibility() -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="apex-ua-rebuild-") as raw_rebuild:
            rebuild = Path(raw_rebuild) / "out"
            build(rebuild, epoch=info["source_date_epoch"])
            result = compare(read_sums(out / SUMS_NAME), rebuild)
        return {"passed": result["identical"], **result}

    _attempt(checks, "reproducibility", reproducibility)
    return {
        "release": info["version"],
        "date": dt.date.today().isoformat(),
        "result": "PASS" if all(check["passed"] for check in checks.values()) else "FAIL",
        "platform": {"system": platform.system(), "machine": platform.machine(),
                     "python": platform.python_version()},
        "build": {key: info[key] for key in ("source_date_epoch", "build_requirements",
                                             "default_profile")},
        "artifacts": {name: digest for name, digest in info["assets"].items() if name != SUMS_NAME},
        "checks": checks,
    }


def _cell(check: dict[str, Any], text: str) -> str:
    if "error" in check:
        return "**ERROR** " + " ".join(str(check["error"]).split())[:300]
    return ("" if check["passed"] else "**FAIL** ") + text


def render_markdown(report: dict[str, Any]) -> str:
    checks = report["checks"]
    lines = [
        f"# Apex UA Rotator {report['release']} — release validation",
        "",
        f"**Result: {report['result']}** · {report['date']} · "
        f"{report['platform']['system']} {report['platform']['machine']}, "
        f"Python {report['platform']['python']}",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    suite = checks["test_suite"]
    lines.append(f"| Test suite | {_cell(suite, str(suite.get('summary')))} |")
    lines.append(f"| Branch coverage | {_cell(suite, str(suite.get('branch_coverage_percent')) + '%')} |")
    profiles = checks["profiles"]
    for profile in profiles.get("profiles", []):
        state = "superseded, retained" if profile.get("superseded") else "all checks pass"
        default = " (default)" if profile["default"] else ""
        lines.append(f"| Profile `{profile['profile']}`{default} | {profile['entry_count']} entries, "
                     f"total {profile['weight_total']}, {state} |")
    if "error" in profiles:
        lines.append(f"| Profiles | {_cell(profiles, '')} |")
    clean = checks["clean_wheel_install"]
    clean_text = (f"version {clean.get('cli_version')}, {clean.get('entries')} profiles, "
                  f"revision {clean.get('profile_revision')}")
    lines.append(f"| Clean offline wheel install | {_cell(clean, clean_text)} |")
    pyz = checks["one_file_installer"]
    pyz_text = f"{pyz.get('result')}, embedded checksum verified: {pyz.get('embedded_checksum_verified')}"
    lines.append(f"| One-file installer (fresh environment) | {_cell(pyz, pyz_text)} |")
    found = checks["launchers"].get("found_installer", {})
    found_text = ", ".join(f"{name}: {ok}" for name, ok in found.items())
    lines.append(f"| Locators, isolated home, `--expect-sha256` | {_cell(checks['launchers'], found_text)} |")
    scrapy = checks["scrapy"]
    for item in scrapy.get("runs", []):
        text = (f"smoke {item['smoke_result']} ({item['smoke_checks_passed']}/{item['smoke_checks_total']} "
                f"checks); adapter tests {item['adapter_tests']}; imports built wheel: "
                f"{item['imports_built_wheel']}")
        lines.append(f"| Scrapy {item['scrapy_version']} (Python {item['python']}) | {_cell(item, text)} |")
    if "error" in scrapy:
        lines.append(f"| Scrapy | {_cell(scrapy, '')} |")
    repro = checks["reproducibility"]
    repro_text = f"identical ({len(repro.get('compared', []))} artifacts compared)"
    lines.append(f"| Reproducible rebuild | {_cell(repro, repro_text)} |")
    lines += [
        "",
        "## Build inputs",
        "",
        f"- `SOURCE_DATE_EPOCH`: {report['build']['source_date_epoch']} (commit timestamp)",
        f"- Build backend: {', '.join(report['build']['build_requirements'])}",
        f"- Default profile: `{report['build']['default_profile']}`",
        "",
        "## Artifacts (SHA-256)",
        "",
        "| File | SHA-256 |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | `{digest}` |" for name, digest in sorted(report["artifacts"].items())]
    lines += [
        "",
        "Reproduce: download this release's `SHA256SUMS`, check out the tag, and run "
        "`python3 tools/build_release.py --out rebuild --compare SHA256SUMS`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "release")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="Build from the working tree, including uncommitted changes.")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--scrapy-python", type=Path, action="append", default=[],
                        help="Interpreter with Scrapy and pytest installed (required with --validate).")
    parser.add_argument("--compare", type=Path, metavar="SHA256SUMS",
                        help="Compare the rebuilt artifacts against published checksums.")
    args = parser.parse_args(argv)

    if not args.allow_dirty and not tree_is_clean():
        parser.error("tracked files have uncommitted changes; commit them or pass --allow-dirty")
    if args.validate and args.allow_dirty:
        parser.error("--validate requires a clean, committed tree")
    if args.validate and not args.scrapy_python:
        parser.error("--validate needs at least one --scrapy-python interpreter")
    # Read the reference checksums before building: --out may contain them.
    published = read_sums(args.compare) if args.compare else None
    out = args.out.resolve()
    try:
        info = build(out, include_uncommitted=args.allow_dirty)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"built": info["assets"]}, indent=2))

    if published is not None:
        result = compare(published, out)
        print(json.dumps({"reproducibility": result}, indent=2))
        return 0 if result["identical"] else 1

    if args.validate:
        report = validate(out, info, args.scrapy_python)
        (out / REPORT_JSON).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (out / REPORT_MD).write_text(render_markdown(report), encoding="utf-8")
        write_sums(out, [name for name in release_assets(out) if name != SUMS_NAME])
        print(json.dumps({"validation": report["result"]}, indent=2))
        return 0 if report["result"] == "PASS" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
