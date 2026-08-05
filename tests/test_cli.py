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


def test_version_constant():
    assert __version__ == "1.2.1"
