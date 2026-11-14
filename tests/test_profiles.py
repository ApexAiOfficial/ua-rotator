"""Bundled-profile accuracy and consistency checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import profile_lint  # noqa: E402
import validate_profiles  # noqa: E402

PROFILE_DIR = ROOT / "src" / "apex_ua_rotator" / "profiles"
DEFAULT = PROFILE_DIR / "global_2026-11-14.json"
PREVIOUS_DEFAULT = PROFILE_DIR / "global_2026-07-31-r2.json"
REVISION_1 = PROFILE_DIR / "global_2026-07-31.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_validator_passes_for_every_bundled_profile(capsys):
    assert validate_profiles.main() == 0
    report = json.loads(capsys.readouterr().out)
    by_name = {item["profile"]: item for item in report["profiles"]}
    assert by_name[DEFAULT.name]["default"] is True
    assert "superseded" not in by_name[DEFAULT.name]
    assert by_name[REVISION_1.name]["superseded"] is True
    assert len(by_name[REVISION_1.name]["known_issues"]) == 1


def test_default_profile_tokens_are_consistent():
    for path in (DEFAULT, PREVIOUS_DEFAULT):
        entries = [entry["user_agent"] for entry in load(path)["entries"]]
        assert profile_lint.lint_entries(entries) == {}, path.name


def test_lint_catches_the_revision_1_chromeos_token():
    problems = profile_lint.lint_entries(entry["user_agent"] for entry in load(REVISION_1)["entries"])
    assert list(problems) == [
        "Mozilla/5.0 (X11; CrOS x86_64 16700.60.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ]


@pytest.mark.parametrize(
    ("user_agent", "problem"),
    [
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.113 Safari/537.36", "known browser family"),
        ("Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36", "reduced Chromium"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36", "'Mobile' token"),
        ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36 Edg/150.0.0.0", "desktop Edg"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 EdgA/150.0.0.0", "EdgA outside"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/29.0 Chrome/146.0.0.0 Safari/537.36", "SamsungBrowser outside"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/152.0", "rv: differs"),
        ("Mozilla/5.0 (Android 16; Mobile; rv:152.0) Gecko/151.0 Firefox/152.0", "Gecko/ differ"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1", "does not match iOS"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0.1 Mobile/15E148 Safari/604.1", "frozen 18_x"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7_12 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Mobile/15E148 Safari/604.1", "only for Chrome for iOS"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/160.0 Mobile/15E148 Safari/604.1", "only for Chrome for iOS"),
        ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/150.0.0.0 Safari/537.36", "headless"),
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "crawler"),
    ],
)
def test_lint_rejects_inconsistent_strings(user_agent, problem):
    problems = profile_lint.lint_user_agent(user_agent)
    assert problems and any(problem in item for item in problems)


@pytest.mark.parametrize(
    "user_agent",
    [
        # Observed in November 2026 traffic: Safari 26+ freezes the OS token and reports a patch release.
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0.1 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPad; CPU OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.2 Mobile/15E148 Safari/604.1",
    ],
)
def test_lint_accepts_safari_patch_releases(user_agent):
    assert profile_lint.lint_user_agent(user_agent) == []


@pytest.mark.parametrize(
    "user_agent",
    [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26 Mobile/15E148 Safari/604.1",
    ],
)
def test_lint_rejects_malformed_safari_versions(user_agent):
    assert profile_lint.lint_user_agent(user_agent)


@pytest.mark.parametrize(
    "user_agent",
    [
        # Chrome for iOS sends the device's real iOS version as major_minor_patch (Chromium ios/web/common/user_agent.mm).
        "Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/158.0.8127.35 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPad; CPU OS 18_7_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/157.0.8103.37 Mobile/15E148 Safari/604.1",
    ],
)
def test_lint_accepts_chrome_for_ios_real_os_versions(user_agent):
    assert profile_lint.lint_user_agent(user_agent) == []


def test_default_profile_gives_chrome_for_ios_the_real_three_part_os_version():
    """Chrome for iOS never borrows Safari's frozen token: each entry carries major_minor_patch."""
    import re

    crios = [entry["user_agent"] for entry in load(DEFAULT)["entries"] if "CriOS/" in entry["user_agent"]]
    assert crios
    for user_agent in crios:
        assert re.search(r"OS \d+_\d+_\d+ like Mac OS X\)", user_agent), user_agent


def test_default_profile_freezes_the_ios_token_for_safari_26_and_later():
    """Every Safari 26+ entry on iPhone or iPad carries the frozen ``18_7`` token current devices send."""
    import re

    for entry in load(DEFAULT)["entries"]:
        match = re.search(r"OS (\d+_\d+) like Mac OS X\).* Version/(\d+)\.", entry["user_agent"])
        if match and int(match.group(2)) >= 26:
            assert match.group(1) == "18_7", entry["user_agent"]


def test_lint_accepts_windows_7_only_with_chrome_109():
    ua = "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
    assert profile_lint.lint_user_agent(ua) == []


def test_lint_cli(capsys):
    assert profile_lint.main([str(DEFAULT)]) == 0
    assert profile_lint.main([str(REVISION_1)]) == 1
    assert profile_lint.main([]) == 2
    assert "not a reduced Chromium value" in capsys.readouterr().out


def test_schema_copies_are_identical():
    packaged = ROOT / "src" / "apex_ua_rotator" / "user_agent_profiles.schema.json"
    assert packaged.read_bytes() == (ROOT / "user_agent_profiles.schema.json").read_bytes()


def test_bundled_and_example_documents_satisfy_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = load(ROOT / "user_agent_profiles.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    for path in [*PROFILE_DIR.glob("*.json"), ROOT / "examples" / "custom_profiles.json"]:
        assert list(validator.iter_errors(load(path))) == [], path.name
