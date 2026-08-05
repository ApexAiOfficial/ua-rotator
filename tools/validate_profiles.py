#!/usr/bin/env python3
"""Validate all bundled Apex User-Agent profile documents."""

from __future__ import annotations

import json
import math
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from apex_ua_rotator import UserAgentRotator  # noqa: E402

PROFILE_DIR = SRC / "apex_ua_rotator" / "profiles"


def validate(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rotator = UserAgentRotator.from_file(path)
    entries = document["entries"]
    weights = [Decimal(str(entry["weight"])) for entry in entries]
    user_agents = [entry["user_agent"] for entry in entries]

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
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise AssertionError(f"{path.name} failed: {failures}")
    return {
        "profile": path.name,
        "entry_count": len(entries),
        "weight_total": str(sum(weights)),
        "checks": checks,
    }


def main() -> int:
    profiles = sorted(PROFILE_DIR.glob("*.json"))
    if not profiles:
        raise SystemExit("No bundled JSON profiles found.")
    results = [validate(path) for path in profiles]
    print(json.dumps({"result": "PASS", "profiles": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
