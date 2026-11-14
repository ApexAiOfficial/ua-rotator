"""Regression tests against real Scrapy components (skipped without Scrapy).

These drive ScrapyUserAgentMiddleware with Scrapy's own Request objects and the
real RetryMiddleware, RedirectMiddleware, and HttpProxyMiddleware, so lineage
behavior is checked against Scrapy's actual copy semantics rather than fakes.
No network access or reactor is needed.
"""

from __future__ import annotations

import inspect
import logging
import warnings

import pytest

scrapy = pytest.importorskip("scrapy")

from scrapy import Request, Spider  # noqa: E402
from scrapy.downloadermiddlewares.httpproxy import HttpProxyMiddleware  # noqa: E402
from scrapy.downloadermiddlewares.redirect import RedirectMiddleware  # noqa: E402
from scrapy.downloadermiddlewares.retry import get_retry_request  # noqa: E402
from scrapy.downloadermiddlewares.useragent import UserAgentMiddleware  # noqa: E402
from scrapy.exceptions import NotConfigured  # noqa: E402
from scrapy.http import Response  # noqa: E402
from scrapy.utils.test import get_crawler  # noqa: E402

from apex_ua_rotator.scrapy import META_KEY, ScrapyUserAgentMiddleware  # noqa: E402

MIDDLEWARE_PATH = "apex_ua_rotator.scrapy.ScrapyUserAgentMiddleware"
ENTRIES = [(f"UA-{index}", 1) for index in range(12)]


def make_crawler(**settings):
    values = {"UA_ROTATOR_ENTRIES": ENTRIES}
    values.update(settings)
    crawler = get_crawler(Spider, values)
    spider = Spider.from_crawler(crawler, name="apex_ua_test")
    crawler.spider = spider
    return crawler, spider


def call(method, *args, spider):
    """Call a Scrapy middleware method across Scrapy versions.

    Scrapy 2.11 requires the ``spider`` argument; newer releases deprecate it.
    """

    parameter = inspect.signature(method).parameters.get("spider")
    if parameter is not None and parameter.default is inspect.Parameter.empty:
        return method(*args, spider)
    return method(*args)


def user_agent(request):
    return request.headers.get(b"User-Agent").decode("ascii")


def test_from_crawler_reads_real_settings_and_stats():
    crawler, _ = make_crawler(
        UA_ROTATOR_SCOPE="domain",
        UA_ROTATOR_REFRESH_ON="retry,redirect",
        UA_ROTATOR_MAX_KEY_CACHE="32",
    )
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)
    assert middleware.scope == "domain"
    assert middleware.refresh_on == {"retry", "redirect"}
    assert middleware.rotator.max_key_cache == 32
    assert middleware.stats is crawler.stats
    assert crawler.settings.get("USER_AGENT") in middleware.default_user_agents


def test_disabled_setting_raises_not_configured():
    crawler, _ = make_crawler(UA_ROTATOR_ENABLED=False)
    with pytest.raises(NotConfigured):
        ScrapyUserAgentMiddleware.from_crawler(crawler)


def test_real_retry_keeps_identity_by_default():
    crawler, spider = make_crawler(UA_ROTATOR_SCOPE="request")
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)
    request = Request("https://example.com/page")
    middleware.process_request(request)
    retry = get_retry_request(request, spider=spider, reason="503 Service Unavailable")
    assert retry is not None and retry.meta["retry_times"] == 1
    middleware.process_request(retry)
    assert user_agent(retry) == user_agent(request)
    assert crawler.stats.get_value("ua_rotator/reused") == 1


def test_real_retry_refresh_picks_a_different_identity():
    crawler, spider = make_crawler(UA_ROTATOR_SCOPE="domain", UA_ROTATOR_REFRESH_ON=["retry"])
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)
    request = Request("https://example.com/page")
    middleware.process_request(request)
    retry = get_retry_request(request, spider=spider, reason="429 Too Many Requests")
    middleware.process_request(retry)
    assert user_agent(retry) != user_agent(request)
    # The domain's sticky identity moved to the replacement.
    later = Request("https://example.com/other")
    middleware.process_request(later)
    assert user_agent(later) == user_agent(retry)


def test_real_redirect_preserves_identity_across_domains_in_request_scope():
    crawler, spider = make_crawler(UA_ROTATOR_SCOPE="request")
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)
    redirect_mw = RedirectMiddleware.from_crawler(crawler)
    request = Request("https://one.example/start")
    middleware.process_request(request)
    response = Response(
        "https://one.example/start",
        status=301,
        headers={"Location": "https://two.example/final"},
        request=request,
    )
    redirected = call(redirect_mw.process_response, request, response, spider=spider)
    assert isinstance(redirected, Request)
    assert redirected.meta["redirect_times"] == 1
    middleware.process_request(redirected)
    assert user_agent(redirected) == user_agent(request)
    assert redirected.meta[META_KEY]["redirect_times"] == 1


def test_real_cross_domain_redirect_switches_to_destination_identity_in_domain_scope():
    crawler, spider = make_crawler(UA_ROTATOR_SCOPE="domain")
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)
    redirect_mw = RedirectMiddleware.from_crawler(crawler)
    destination_first = Request("https://two.example/")
    middleware.process_request(destination_first)
    request = Request("https://one.example/start")
    middleware.process_request(request)
    response = Response(
        "https://one.example/start",
        status=302,
        headers={"Location": "https://two.example/final"},
        request=request,
    )
    redirected = call(redirect_mw.process_response, request, response, spider=spider)
    middleware.process_request(redirected)
    assert user_agent(redirected) == user_agent(destination_first)
    assert redirected.meta[META_KEY]["scope_key"] == ("domain", "two.example")


def test_builtin_user_agent_middleware_running_first_does_not_disable_rotation():
    crawler, _ = make_crawler(UA_ROTATOR_SCOPE="request")
    builtin = UserAgentMiddleware.from_crawler(crawler)
    builtin.spider_opened(crawler.spider)
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)
    request = Request("https://example.com")
    call(builtin.process_request, request, spider=crawler.spider)
    assert user_agent(request).startswith("Scrapy/")
    middleware.process_request(request)
    assert user_agent(request).startswith("UA-")
    assert crawler.stats.get_value("ua_rotator/replaced_default_header") == 1


def test_env_proxy_keeps_identity_when_ordered_after_http_proxy_middleware(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://user:secret@proxy.example:3128")
    monkeypatch.delenv("no_proxy", raising=False)
    crawler, spider = make_crawler(UA_ROTATOR_SCOPE="proxy")
    proxy_mw = HttpProxyMiddleware.from_crawler(crawler)
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)

    request = Request("http://example.com/page")
    call(proxy_mw.process_request, request, spider=spider)
    middleware.process_request(request)
    assert request.meta[META_KEY]["scope_key"] == ("proxy", "http://proxy.example:3128")
    assert "secret" not in repr(request.meta[META_KEY])

    retry = get_retry_request(request, spider=spider, reason="timeout")
    call(proxy_mw.process_request, retry, spider=spider)
    middleware.process_request(retry)
    assert user_agent(retry) == user_agent(request)
    assert crawler.stats.get_value("ua_rotator/scope_changed") is None


def test_env_proxy_ordered_before_http_proxy_middleware_changes_scope(monkeypatch):
    """Documents why proxy scope must run after HttpProxyMiddleware."""

    monkeypatch.setenv("http_proxy", "http://proxy.example:3128")
    monkeypatch.delenv("no_proxy", raising=False)
    crawler, spider = make_crawler(UA_ROTATOR_SCOPE="proxy")
    proxy_mw = HttpProxyMiddleware.from_crawler(crawler)
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)

    request = Request("http://example.com/page")
    middleware.process_request(request)  # wrong order: proxy not assigned yet
    call(proxy_mw.process_request, request, spider=spider)
    assert request.meta[META_KEY]["scope_key"] == ("proxy", "<direct>")
    retry = get_retry_request(request, spider=spider, reason="timeout")
    middleware.process_request(retry)
    assert crawler.stats.get_value("ua_rotator/scope_changed") == 1


@pytest.mark.parametrize(("priority", "warns"), [(90, True), (760, False)])
def test_proxy_scope_warns_when_ordered_before_http_proxy_middleware(caplog, priority, warns):
    crawler, _ = make_crawler(
        UA_ROTATOR_SCOPE="proxy",
        DOWNLOADER_MIDDLEWARES={
            MIDDLEWARE_PATH: priority,
            "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
        },
    )
    with caplog.at_level(logging.WARNING, logger="apex_ua_rotator.scrapy"):
        ScrapyUserAgentMiddleware.from_crawler(crawler)
    assert ("runs before HttpProxyMiddleware" in caplog.text) is warns


def test_middleware_manager_loads_adapter_without_spider_argument_deprecation():
    from scrapy.core.downloader.middleware import DownloaderMiddlewareManager

    crawler, _ = make_crawler(
        DOWNLOADER_MIDDLEWARES={
            MIDDLEWARE_PATH: 90,
            "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
        }
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manager = DownloaderMiddlewareManager.from_crawler(crawler)
    assert any(isinstance(mw, ScrapyUserAgentMiddleware) for mw in manager.middlewares)
    assert not [w for w in caught if "ScrapyUserAgentMiddleware" in str(w.message)]
