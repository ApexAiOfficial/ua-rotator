"""Command-line utilities for inspecting, sampling, and validating profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .core import DEFAULT_PROFILE_NAME, RotationPolicy, UserAgentRotator


def _rotator_from_args(args: argparse.Namespace, *, policy: str = "per_request") -> UserAgentRotator:
    if getattr(args, "file", None):
        return UserAgentRotator.from_file(args.file, policy=policy)
    name = getattr(args, "bundled", None) or DEFAULT_PROFILE_NAME
    return UserAgentRotator.from_bundled_profile(name, policy=policy)


def _summary(rotator: UserAgentRotator) -> dict[str, Any]:
    entries = rotator.entries
    weights = [entry.weight for entry in entries]
    return {
        "entries": len(entries),
        "weight_total": sum(weights),
        "weights_descending": weights == sorted(weights, reverse=True),
        "unique_user_agents": len({entry.user_agent for entry in entries}) == len(entries),
        "metadata": rotator.metadata,
    }


def _add_source_options(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="Load a profile JSON document.")
    source.add_argument(
        "--bundled",
        metavar="NAME",
        help=f"Load a bundled profile (default: {DEFAULT_PROFILE_NAME}).",
    )


def _command_info(args: argparse.Namespace) -> int:
    rotator = _rotator_from_args(args)
    payload = _summary(rotator)
    payload["source"] = str(args.file) if args.file else (args.bundled or DEFAULT_PROFILE_NAME)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _command_sample(args: argparse.Namespace) -> int:
    rotator = _rotator_from_args(args, policy=args.policy)
    samples: list[str] = []
    for index in range(args.count):
        key = args.key
        if args.policy == RotationPolicy.PER_KEY.value and key is None:
            key = f"sample-{index}"
        samples.append(rotator.select(key=key))
    if args.json:
        print(json.dumps({"count": len(samples), "samples": samples}, indent=2))
    else:
        print("\n".join(samples))
    return 0


def _command_validate(args: argparse.Namespace) -> int:
    rotator = UserAgentRotator.from_file(args.profile)
    payload = _summary(rotator)
    payload["source"] = str(args.profile)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _command_smoke(_: argparse.Namespace) -> int:
    from .smoke import main as smoke_main

    return smoke_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apex-ua-rotator",
        description="Inspect, sample, validate, and smoke-test Apex UA Rotator profiles.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="Show profile metadata and summary statistics.")
    _add_source_options(info)
    info.set_defaults(func=_command_info)

    sample = commands.add_parser("sample", help="Print weighted User-Agent selections.")
    _add_source_options(sample)
    sample.add_argument("-n", "--count", type=int, default=1)
    sample.add_argument(
        "--policy",
        choices=[policy.value for policy in RotationPolicy],
        default=RotationPolicy.PER_REQUEST.value,
    )
    sample.add_argument("--key", help="Sticky key used with --policy per_key.")
    sample.add_argument("--json", action="store_true", help="Emit a JSON object.")
    sample.set_defaults(func=_command_sample)

    validate = commands.add_parser("validate", help="Load and summarize a profile document.")
    validate.add_argument("profile", type=Path)
    validate.set_defaults(func=_command_validate)

    smoke = commands.add_parser(
        "smoke-scrapy", help="Run the controlled localhost Scrapy integration test."
    )
    smoke.set_defaults(func=_command_smoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "count", 1) < 1:
        parser.error("--count must be at least 1")
    try:
        return int(args.func(args))
    except (ValueError, FileNotFoundError, FileExistsError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
