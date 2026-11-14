from __future__ import annotations

import json

from apex_ua_rotator import __version__
from apex_ua_rotator.cli import main


def test_info_uses_bundled_profile(capsys):
    assert main(["info"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entries"] == 63
    assert payload["weight_total"] == 100.0
    assert payload["unique_user_agents"] is True


def test_sample_plain_and_json(capsys):
    assert main(["sample", "-n", "2"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2

    assert main(["sample", "-n", "2", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert len(payload["samples"]) == 2


def test_validate_profile(tmp_path, capsys):
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metadata": {"name": "test"},
                "entries": [{"user_agent": "Example/1.0", "weight": 1}],
            }
        ),
        encoding="utf-8",
    )
    assert main(["validate", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entries"] == 1
    assert payload["metadata"] == {"name": "test"}


def test_invalid_count_is_parser_error():
    try:
        main(["sample", "--count", "0"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected argparse to exit")


def test_sample_seed_is_reproducible(capsys):
    assert main(["sample", "-n", "5", "--seed", "11", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)["samples"]
    assert main(["sample", "-n", "5", "--seed", "11", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["samples"] == first


def test_sample_per_key_policy_generates_keys(capsys):
    assert main(["sample", "-n", "3", "--policy", "per_key", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 3


def test_profiles_lists_bundled_snapshots_and_default(capsys):
    assert main(["profiles"]) == 0
    profiles = json.loads(capsys.readouterr().out)["profiles"]
    names = {profile["name"]: profile for profile in profiles}
    assert names["global_2026-07-31-r2.json"]["default"] is True
    assert names["global_2026-07-31.json"]["default"] is False
    assert all(profile["entries"] == 63 for profile in profiles)


def test_info_reports_profile_age_and_source(capsys):
    assert main(["info", "--bundled", "global_2026-07-31.json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "global_2026-07-31.json"
    assert isinstance(payload["profile_age_days"], int)


def test_age_days_handles_missing_and_invalid_dates():
    import datetime as dt

    from apex_ua_rotator.cli import _age_days

    assert _age_days("2026-07-31", dt.date(2026, 8, 10)) == 10
    assert _age_days("not-a-date") is None
    assert _age_days(None) is None


def test_errors_exit_with_status_two(tmp_path, capsys):
    assert main(["validate", str(tmp_path / "missing.json")]) == 2
    assert "error:" in capsys.readouterr().err
    assert main(["validate", str(tmp_path)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["info", "--file", str(bad)]) == 2


def test_version_constant_matches_package_metadata_and_changelog():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert __version__ == "1.3.0"
    assert f"## {__version__} " in (root / "CHANGELOG.md").read_text(encoding="utf-8")


def test_offline_examples_run(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    for script, expected in (
        ("basic.py", "example.com =>"),
        ("http_client.py", "https://example.com/ -> Mozilla/5.0"),
    ):
        completed = subprocess.run(
            [sys.executable, str(root / "examples" / script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert expected in completed.stdout
