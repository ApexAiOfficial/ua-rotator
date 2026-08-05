"""Controlled localhost integration test for the optional Scrapy adapter.

The smoke test never contacts the public internet. It starts a temporary local
HTTP server, exercises a redirect and a retry through Scrapy's real downloader
stack, and verifies that the middleware assigns and preserves the configured
User-Agent across the request lineage.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

DEFAULT_SMOKE_USER_AGENT = "ApexRotatorSmoke/1.0"


class ScrapySmokeUnavailable(RuntimeError):
    """Raised when Scrapy is not installed in the active Python environment."""


class ScrapySmokeFailure(RuntimeError):
    """Raised when one or more live integration checks fail."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("Scrapy integration smoke test failed")
        self.report = report


class _SmokeHandler(BaseHTTPRequestHandler):
    records: list[dict[str, Any]] = []
    retry_hits = 0

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        type(self).records.append(
            {"path": self.path, "user_agent": self.headers.get("User-Agent")}
        )
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        if self.path == "/retry":
            type(self).retry_hits += 1
            if type(self).retry_hits == 1:
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
            "python -m pip install 'apex-ua-rotator[scrapy]'"
        ) from exc

    from .scrapy import ScrapyUserAgentMiddleware

    _SmokeHandler.records = []
    _SmokeHandler.retry_hits = 0
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

        def parse(self, response: Any):
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

    process = CrawlerProcess(settings={"LOG_LEVEL": "ERROR"})
    crawler = process.create_crawler(SmokeSpider)
    deferred = process.crawl(crawler, root_url=base_url)
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

    records = list(_SmokeHandler.records)
    stats = crawler.stats.get_stats()
    paths = [record["path"] for record in records]
    final_observation = next(
        (item for item in response_observations if item["path"] == "/final"), None
    )
    retry_observation = next(
        (item for item in response_observations if item["path"] == "/retry"), None
    )

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
        "retry_endpoint_hit_twice": _SmokeHandler.retry_hits == 2,
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
    }

    report = {
        "result": "PASS" if all(checks.values()) else "FAIL",
        "scrapy_version": scrapy.__version__,
        "expected_user_agent": expected_user_agent,
        "checks": checks,
        "requests_observed": records,
        "responses_observed": response_observations,
        "stats": {
            "ua_rotator_selected": stats.get("ua_rotator/selected", 0),
            "ua_rotator_reused": stats.get("ua_rotator/reused", 0),
            "retry_count": stats.get("retry/count", 0),
            "redirect_count_if_reported": stats.get("redirect/count"),
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
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
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
