#!/usr/bin/env python3
"""Structural consistency checks for browser User-Agent profile strings.

Every entry must match exactly one known browser family, and the tokens inside
it must agree with how that browser actually formats its User-Agent:

* Chromium-based browsers use the reduced User-Agent: ``<major>.0.0.0`` versions
  and the frozen platform tokens published by the Chromium project
  (https://www.chromium.org/updates/ua-reduction/). Windows 7 is accepted only
  with Chrome 109, its final supported release.
* Firefox repeats its version in ``rv:`` (and in ``Gecko/`` on Android).
* Safari on iOS before version 26 reports a ``Version/`` equal to the OS token.
* Crawler, headless, and tooling tokens are never allowed.

The checks are maintainer tooling for curating profiles; they are not part of
the installed package. Run directly to lint profile files::

    python3 tools/profile_lint.py src/apex_ua_rotator/profiles/*.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable

REDUCED_CHROMIUM_PLATFORMS = frozenset(
    {
        "Windows NT 10.0; Win64; x64",
        "Macintosh; Intel Mac OS X 10_15_7",
        "X11; Linux x86_64",
        "X11; CrOS x86_64 14541.0.0",
        "Linux; Android 10; K",
    }
)
_ANDROID_REDUCED = "Linux; Android 10; K"
_LEGACY_WINDOWS_7 = ("Windows NT 6.1; Win64; x64", "109")
_FORBIDDEN = re.compile(r"headless|bot\b|crawl|spider|python|curl|wget|scrapy", re.IGNORECASE)

_CHROMIUM = re.compile(
    r"^Mozilla/5\.0 \((?P<platform>[^)]+)\) AppleWebKit/537\.36 \(KHTML, like Gecko\) "
    r"(?:(?P<samsung>SamsungBrowser/\d+\.\d+) )?Chrome/(?P<major>\d+)\.0\.0\.0 "
    r"(?P<mobile>Mobile )?Safari/537\.36(?: (?P<brand>Edg|EdgA|OPR)/\d+\.0\.0\.0)?$"
)
_FIREFOX_DESKTOP = re.compile(
    r"^Mozilla/5\.0 \((?:Windows NT 10\.0; Win64; x64|Macintosh; Intel Mac OS X 10\.15|"
    r"X11; Linux x86_64); rv:(?P<rv>\d+)\.0\) Gecko/20100101 Firefox/(?P<version>\d+)\.0$"
)
_FIREFOX_ANDROID = re.compile(
    r"^Mozilla/5\.0 \(Android \d+; Mobile; rv:(?P<rv>\d+)\.0\) "
    r"Gecko/(?P<gecko>\d+)\.0 Firefox/(?P<version>\d+)\.0$"
)
_SAFARI_MAC = re.compile(
    r"^Mozilla/5\.0 \(Macintosh; Intel Mac OS X 10_15_7\) AppleWebKit/605\.1\.15 "
    r"\(KHTML, like Gecko\) Version/\d+\.\d+ Safari/605\.1\.15$"
)
_IOS = re.compile(
    r"^Mozilla/5\.0 \((?:iPhone; CPU iPhone OS|iPad; CPU OS) (?P<os>\d+_\d+) like Mac OS X\) "
    r"AppleWebKit/605\.1\.15 \(KHTML, like Gecko\) "
    r"(?:Version/(?P<safari>\d+\.\d+)|CriOS/\d+\.\d+\.\d+\.\d+|FxiOS/\d+\.\d+) "
    r"Mobile/15E148 Safari/604\.1$"
)


def _chromium_problems(match: re.Match[str]) -> list[str]:
    platform = match["platform"]
    android = platform == _ANDROID_REDUCED
    problems = []
    if platform not in REDUCED_CHROMIUM_PLATFORMS and (platform, match["major"]) != _LEGACY_WINDOWS_7:
        problems.append(f"platform token {platform!r} is not a reduced Chromium value")
    if match["mobile"] and not android:
        problems.append("'Mobile' token outside Android")
    if match["samsung"] and not android:
        problems.append("SamsungBrowser outside Android")
    if match["brand"] == "EdgA" and not android:
        problems.append("EdgA outside Android")
    if match["brand"] == "Edg" and android:
        problems.append("desktop Edg token on Android")
    return problems


def lint_user_agent(user_agent: str) -> list[str]:
    """Return a list of problems; an empty list means the string is consistent."""

    if _FORBIDDEN.search(user_agent):
        return ["contains a crawler, headless, or tooling token"]
    if match := _CHROMIUM.match(user_agent):
        return _chromium_problems(match)
    if match := _FIREFOX_DESKTOP.match(user_agent):
        return [] if match["rv"] == match["version"] else ["rv: differs from Firefox version"]
    if match := _FIREFOX_ANDROID.match(user_agent):
        same = match["rv"] == match["gecko"] == match["version"]
        return [] if same else ["rv:/Gecko/ differ from Firefox version"]
    if _SAFARI_MAC.match(user_agent):
        return []
    if match := _IOS.match(user_agent):
        safari = match["safari"]
        if safari and int(safari.split(".")[0]) < 26 and match["os"].replace("_", ".") != safari:
            return [f"Safari Version/{safari} does not match iOS {match['os']}"]
        return []
    return ["does not match any known browser family format"]


def lint_entries(user_agents: Iterable[str]) -> dict[str, list[str]]:
    """Map each inconsistent User-Agent to its problems."""

    return {ua: problems for ua in user_agents if (problems := lint_user_agent(ua))}


def main(argv: list[str] | None = None) -> int:
    paths = [Path(arg) for arg in (sys.argv[1:] if argv is None else argv)]
    if not paths:
        print("usage: profile_lint.py PROFILE.json [...]", file=sys.stderr)
        return 2
    failed = False
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        problems = lint_entries(entry["user_agent"] for entry in document["entries"])
        for user_agent, issues in problems.items():
            failed = True
            print(f"{path.name}: {user_agent}\n  - " + "\n  - ".join(issues))
        if not problems:
            print(f"{path.name}: {len(document['entries'])} entries consistent")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
