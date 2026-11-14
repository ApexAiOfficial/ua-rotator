"""Controlled localhost integration test for the optional Scrapy adapter.

The smoke test never contacts the public internet. It starts a temporary local
HTTP server and runs two crawlers through Scrapy's real downloader stack:

* an identity crawler that exercises a redirect and a retry, verifying that the
  middleware assigns and preserves the configured User-Agent across the lineage;
* a refresh crawler with ``UA_ROTATOR_REFRESH_ON = ["retry"]``, verifying that a
  retried request is sent with a different User-Agent than the failed attempt.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.parse import urlsplit

DEFAULT_SMOKE_USER_AGENT = "ApexRotatorSmoke/1.0"
REFRESH_SMOKE_USER_AGENTS = ("ApexRotatorSmoke/refresh-a", "ApexRotatorSmoke/refresh-b")
_FAIL_ONCE_PATHS = ("/retry", "/refresh")


class ScrapySmokeUnavailable(RuntimeError):
    """Raised when Scrapy is not installed in the active Python environment."""


class ScrapySmokeFailure(RuntimeError):
    """Raised when one or more live integration checks fail."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("Scrapy integration smoke test failed")
        self.report = report


class _SmokeHandler(BaseHTTPRequestHandler):
    records: list[dict[str, Any]] = []
    hits: dict[str, int] = {}
    lock = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        with cls.lock:
            cls.records = []
            cls.hits = {path: 0 for path in _FAIL_ONCE_PATHS}

    def do_GET(self) -> None:
        cls = type(self)
        with cls.lock:
            cls.records.append(
                {"path": self.path, "user_agent": self.headers.get("User-Agent")}
            )
            first_hit = False
            if self.path in cls.hits:
                cls.hits[self.path] += 1
                first_hit = cls.hits[self.path] == 1
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        if first_hit:
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_scrapy_smoke(
    expected_user_agent: str = DEFAULT_SMOKE_USER_AGENT,
) -> dict[str, Any]:
    """Run the real Scrapy middleware smoke test and return a JSON-safe report.

    Raises:
        ScrapySmokeUnavailable: Scrapy is not installed.
        ScrapySmokeFailure: The crawl ran but one or more checks failed.
    """

    try:
        import scrapy
        from scrapy.crawler import CrawlerProcess
    except ImportError as exc:
        raise ScrapySmokeUnavailable(
            "Scrapy is not installed in the active Python environment. "
            "Install the optional dependency with: "
            "python -m pip install 'Scrapy>=2.11,<3'"
        ) from exc

    from .scrapy import ScrapyUserAgentMiddleware

    _SmokeHandler.reset()
    response_observations: list[dict[str, Any]] = []
    crawler_failures: list[Any] = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), _SmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    class SmokeSpider(scrapy.Spider):
        name = "apex_ua_rotator_smoke"
        custom_settings = {
            "LOG_LEVEL": "ERROR",
            "ROBOTSTXT_OBEY": False,
            "CONCURRENT_REQUESTS": 1,
            "RETRY_TIMES": 1,
            "RETRY_HTTP_CODES": [503],
            "DOWNLOAD_TIMEOUT": 5,
            "DOWNLOADER_MIDDLEWARES": {
                ScrapyUserAgentMiddleware: 90,
                "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
            },
            "UA_ROTATOR_ENTRIES": [(expected_user_agent, 1)],
            "UA_ROTATOR_SCOPE": "domain",
            "UA_ROTATOR_REFRESH_ON": [],
            "UA_ROTATOR_OVERWRITE_EXISTING": False,
        }

        def __init__(self, root_url: str, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.start_urls = [f"{root_url}/start", f"{root_url}/retry"]

        def parse(self, response: Any) -> Iterator[dict[str, Any]]:
            response_observations.append(
                {
                    "path": urlsplit(response.url).path,
                    "status": response.status,
                    "user_agent": response.request.headers.get(b"User-Agent").decode(
                        "ascii"
                    ),
                    "retry_times": response.meta.get("retry_times", 0),
                    "redirect_times": response.meta.get("redirect_times", 0),
                    "redirect_urls": list(response.meta.get("redirect_urls", [])),
                    "redirect_reasons": list(
                        response.meta.get("redirect_reasons", [])
                    ),
                }
            )
            yield {"url": response.url}

    class RefreshSpider(scrapy.Spider):
        name = "apex_ua_rotator_refresh_smoke"
        custom_settings = {
            "LOG_LEVEL": "ERROR",
            "ROBOTSTXT_OBEY": False,
            "CONCURRENT_REQUESTS": 1,
            "RETRY_TIMES": 1,
            "RETRY_HTTP_CODES": [503],
            "DOWNLOAD_TIMEOUT": 5,
            "DOWNLOADER_MIDDLEWARES": {
                ScrapyUserAgentMiddleware: 90,
                "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
            },
            "UA_ROTATOR_ENTRIES": [(value, 1) for value in REFRESH_SMOKE_USER_AGENTS],
            "UA_ROTATOR_SCOPE": "request",
            "UA_ROTATOR_REFRESH_ON": ["retry"],
        }

        def __init__(self, root_url: str, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.start_urls = [f"{root_url}/refresh"]

        def parse(self, response: Any) -> Iterator[dict[str, Any]]:
            yield {"url": response.url}

    process = CrawlerProcess(settings={"LOG_LEVEL": "ERROR"})
    crawler = process.create_crawler(SmokeSpider)
    refresh_crawler = process.create_crawler(RefreshSpider)
    for target in (crawler, refresh_crawler):
        deferred = process.crawl(target, root_url=base_url)
        deferred.addErrback(lambda failure: crawler_failures.append(failure))

    try:
        process.start()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if crawler_failures:
        failure = crawler_failures[0]
        traceback_text = (
            failure.getTraceback() if hasattr(failure, "getTraceback") else repr(failure)
        )
        raise RuntimeError(f"Scrapy crawler failed:\n{traceback_text}")

    all_records = list(_SmokeHandler.records)
    records = [record for record in all_records if record["path"] != "/refresh"]
    refresh_records = [record for record in all_records if record["path"] == "/refresh"]
    stats = crawler.stats.get_stats()
    refresh_stats = refresh_crawler.stats.get_stats()
    paths = [record["path"] for record in records]
    final_observation = next(
        (item for item in response_observations if item["path"] == "/final"), None
    )
    retry_observation = next(
        (item for item in response_observations if item["path"] == "/retry"), None
    )
    refresh_user_agents = [record["user_agent"] for record in refresh_records]

    redirect_meta_ok = bool(
        final_observation
        and final_observation["redirect_times"] == 1
        and final_observation["redirect_reasons"] == [302]
        and len(final_observation["redirect_urls"]) == 1
        and urlsplit(final_observation["redirect_urls"][0]).path == "/start"
    )
    retry_meta_ok = bool(
        retry_observation and retry_observation["retry_times"] == 1
    )

    checks = {
        "retry_endpoint_hit_twice": _SmokeHandler.hits["/retry"] == 2,
        "start_seen": paths.count("/start") == 1,
        "redirect_target_seen": paths.count("/final") == 1,
        "retry_seen_twice": paths.count("/retry") == 2,
        "all_user_agents_match": bool(records)
        and all(record["user_agent"] == expected_user_agent for record in records),
        "middleware_selected": stats.get("ua_rotator/selected", 0) >= 1,
        "middleware_reused": stats.get("ua_rotator/reused", 0) >= 2,
        "scrapy_retry_count": stats.get("retry/count", 0) == 1,
        "scrapy_retry_meta": retry_meta_ok,
        "scrapy_redirect_meta": redirect_meta_ok,
        "refresh_endpoint_hit_twice": _SmokeHandler.hits["/refresh"] == 2,
        "refresh_changed_identity": len(refresh_user_agents) == 2
        and refresh_user_agents[0] != refresh_user_agents[1]
        and set(refresh_user_agents) == set(REFRESH_SMOKE_USER_AGENTS),
        "refresh_counted": refresh_stats.get("ua_rotator/refreshed", 0) == 1,
    }

    report = {
        "result": "PASS" if all(checks.values()) else "FAIL",
        "scrapy_version": scrapy.__version__,
        "expected_user_agent": expected_user_agent,
        "checks": checks,
        "requests_observed": all_records,
        "responses_observed": response_observations,
        "stats": {
            "ua_rotator_selected": stats.get("ua_rotator/selected", 0),
            "ua_rotator_reused": stats.get("ua_rotator/reused", 0),
            "retry_count": stats.get("retry/count", 0),
            "redirect_count_if_reported": stats.get("redirect/count"),
            "ua_rotator_refreshed": refresh_stats.get("ua_rotator/refreshed", 0),
        },
    }
    if report["result"] != "PASS":
        raise ScrapySmokeFailure(report)
    return report


def main() -> int:
    """Command-line entry point for the localhost Scrapy smoke test."""

    try:
        report = run_scrapy_smoke()
    except ScrapySmokeUnavailable as exc:
        print(json.dumps({"result": "SKIP", "reason": str(exc)}, indent=2))
        return 2
    except ScrapySmokeFailure as exc:
        print(json.dumps(exc.report, indent=2))
        return 1
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensive CLI boundary
        print(
            json.dumps(
                {
                    "result": "FAIL",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
            )
        )
        return 1

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
