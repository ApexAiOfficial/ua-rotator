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
DEFAULT = PROFILE_DIR / "global_2026-07-31-r2.json"
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
    entries = [entry["user_agent"] for entry in load(DEFAULT)["entries"]]
    assert profile_lint.lint_entries(entries) == {}


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
        ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/150.0.0.0 Safari/537.36", "headless"),
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "crawler"),
    ],
)
def test_lint_rejects_inconsistent_strings(user_agent, problem):
    problems = profile_lint.lint_user_agent(user_agent)
    assert problems and any(problem in item for item in problems)


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
