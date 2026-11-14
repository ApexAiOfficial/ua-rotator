"""Sticky per-host identities for a standard-library HTTP client.

Each host keeps one User-Agent; a blocked response (403/429) refreshes that
host's identity to a different value before the next attempt.

    python examples/http_client.py https://example.com/ https://example.org/
    python examples/http_client.py --fetch https://example.com/   # really fetch

Without --fetch, the script only prints the requests it would send.
"""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from apex_ua_rotator import UserAgentRotator

BLOCKED = {403, 429}

rotator = UserAgentRotator.from_bundled_profile(policy="per_key")


def build_request(url: str) -> urllib.request.Request:
    host = urlsplit(url).hostname or ""
    return urllib.request.Request(url, headers={"User-Agent": rotator.select(key=host)})


def fetch(url: str, attempts: int = 2) -> int:
    host = urlsplit(url).hostname or ""
    status = 0
    for _ in range(attempts):
        request = build_request(url)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as error:
            status = error.code
            if status not in BLOCKED:
                return status
            # Replace the identity that was blocked, never re-drawing the same one.
            rotator.refresh(key=host, previous=request.get_header("User-agent"))
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="*", default=["https://example.com/", "https://example.org/"])
    parser.add_argument("--fetch", action="store_true", help="Send the requests.")
    args = parser.parse_args()
    for url in args.urls:
        if args.fetch:
            print(url, "->", fetch(url))
        else:
            request = build_request(url)
            print(url, "->", request.get_header("User-agent"))


if __name__ == "__main__":
    main()
