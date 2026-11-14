#!/usr/bin/env python3
"""Validate all bundled Apex User-Agent profile documents.

Checks every ``src/apex_ua_rotator/profiles/*.json`` document with the real
loader, the bundled-snapshot invariants (descending weights totaling 100,
consistent metadata), the JSON Schema (when ``jsonschema`` is installed), the
browser-token lint in ``profile_lint.py``, and repository consistency (both
schema copies identical, one notes file per snapshot).

A profile named by another profile's ``metadata.supersedes`` is retained
unchanged for reproducibility; its lint findings are reported as known issues
instead of failures.
"""

from __future__ import annotations

import json
import math
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (SRC, ROOT / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from profile_lint import lint_entries  # noqa: E402

from apex_ua_rotator import DEFAULT_PROFILE_NAME, UserAgentRotator  # noqa: E402

PROFILE_DIR = SRC / "apex_ua_rotator" / "profiles"
PACKAGED_SCHEMA = SRC / "apex_ua_rotator" / "user_agent_profiles.schema.json"
ROOT_SCHEMA = ROOT / "user_agent_profiles.schema.json"


def _schema_errors(document: object) -> list[str] | None:
    try:
        import jsonschema
    except ImportError:
        return None
    schema = json.loads(PACKAGED_SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(document)]


def validate(path: Path, *, superseded: bool = False) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rotator = UserAgentRotator.from_file(path)
    entries = document["entries"]
    weights = [Decimal(str(entry["weight"])) for entry in entries]
    user_agents = [entry["user_agent"] for entry in entries]
    schema_errors = _schema_errors(document)
    lint = lint_entries(user_agents)

    checks = {
        "actual_loader_accepts": len(rotator) == len(entries),
        "metadata_preserved": rotator.to_document()["metadata"] == document["metadata"],
        "entries_non_empty": bool(entries),
        "entry_fields_exact": all(set(entry) == {"user_agent", "weight"} for entry in entries),
        "weights_finite_positive": all(math.isfinite(float(weight)) and weight > 0 for weight in weights),
        "weights_total_100": sum(weights) == Decimal("100.000000"),
        "weights_descending": all(weights[index] >= weights[index + 1] for index in range(len(weights) - 1)),
        "user_agents_unique": len(user_agents) == len(set(user_agents)),
        "metadata_count_matches": document["metadata"].get("profile_count") == len(entries),
        "metadata_total_matches": Decimal(str(document["metadata"].get("weight_total"))) == Decimal("100.0"),
        "notes_file_present": path.with_suffix(".md").is_file(),
        "schema_valid": schema_errors == [] if schema_errors is not None else None,
        "browser_tokens_consistent": not lint or superseded,
    }
    failures = [name for name, passed in checks.items() if passed is False]
    if failures:
        detail = {"failures": failures, "schema_errors": schema_errors, "lint": lint}
        raise AssertionError(f"{path.name} failed: {json.dumps(detail, indent=2)}")
    result: dict[str, object] = {
        "profile": path.name,
        "default": path.name == DEFAULT_PROFILE_NAME,
        "profile_revision": document["metadata"].get("profile_revision"),
        "entry_count": len(entries),
        "weight_total": str(sum(weights)),
        "checks": checks,
    }
    if superseded:
        result["superseded"] = True
        result["known_issues"] = lint
    return result


def main() -> int:
    profiles = sorted(PROFILE_DIR.glob("*.json"))
    if not profiles:
        raise SystemExit("No bundled JSON profiles found.")
    if PACKAGED_SCHEMA.read_bytes() != ROOT_SCHEMA.read_bytes():
        raise SystemExit("The packaged and repository-root JSON Schemas differ.")
    if not (PROFILE_DIR / DEFAULT_PROFILE_NAME).is_file():
        raise SystemExit(f"Default profile {DEFAULT_PROFILE_NAME} is not bundled.")
    superseded = {
        json.loads(path.read_text(encoding="utf-8"))["metadata"].get("supersedes")
        for path in profiles
    }
    results = [validate(path, superseded=path.name in superseded) for path in profiles]
    print(
        json.dumps(
            {
                "result": "PASS",
                "schema_copies_identical": True,
                "jsonschema_checked": _schema_errors({}) is not None,
                "profiles": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
