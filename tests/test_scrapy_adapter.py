from __future__ import annotations

import logging
import random

import pytest

from apex_ua_rotator import UserAgentRotator
from apex_ua_rotator.scrapy import (
    META_KEY,
    ScrapyUserAgentMiddleware,
    _Identity,
)


class SequenceRng:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.index = 0

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        value = self.outputs[self.index % len(self.outputs)]
        self.index += 1
        return [value]


class FakeHeaders(dict):
    pass


class FakeRequest:
    def __init__(self, url="https://example.com/path", *, meta=None, headers=None):
        self.url = url
        self.meta = {} if meta is None else meta
        self.headers = FakeHeaders() if headers is None else FakeHeaders(headers)


class FakeStats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key):
        self.values[key] = self.values.get(key, 0) + 1


class FakeSettings:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getint(self, key, default=0):
        return int(self.values.get(key, default))

    def getbool(self, key, default=False):
        value = self.values.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.casefold() in {"1", "true", "yes", "on"}
        return bool(value)


class FakeCrawler:
    def __init__(self, values):
        self.settings = FakeSettings(values)
        self.stats = FakeStats()


def build(*, scope="request", refresh_on=(), overwrite_existing=False, stats=None):
    policy = {
        "request": "per_request",
        "spider": "per_session",
        "domain": "per_key",
        "cookiejar": "per_key",
        "proxy": "per_key",
    }[scope]
    rotator = UserAgentRotator(
        ["UA-A", "UA-B", "UA-C"],
        policy=policy,
        rng=SequenceRng(["UA-A", "UA-B", "UA-C"]),
        max_key_cache=8,
    )
    return ScrapyUserAgentMiddleware(
        rotator,
        scope=scope,
        refresh_on=refresh_on,
        overwrite_existing=overwrite_existing,
        stats=stats,
    )


def test_identity_meta_round_trip_and_rejects_malformed_values():
    identity = _Identity("UA-A", ("domain", "example.com"), 1, 2)
    assert _Identity.from_meta(identity.to_meta()) == identity
    for invalid in (
        None,
        {},
        {"user_agent": 1, "retry_times": 0, "redirect_times": 0},
        {"user_agent": "bad\n", "retry_times": 0, "redirect_times": 0},
        {"user_agent": "UA", "retry_times": True, "redirect_times": 0},
        {"user_agent": "UA", "retry_times": 0, "redirect_times": -1},
        {"user_agent": "UA", "retry_times": 0, "redirect_times": 0, "scope_key": []},
    ):
        assert _Identity.from_meta(invalid) is None


def test_request_scope_preserves_lineage_and_refreshes_once():
    middleware = build(refresh_on=["retry"])
    request = FakeRequest()
    middleware.process_request(request)
    assert request.headers[b"User-Agent"] == b"UA-A"

    retried = FakeRequest(meta=dict(request.meta), headers=dict(request.headers))
    retried.meta["retry_times"] = 1
    middleware.process_request(retried)
    assert retried.headers[b"User-Agent"] == b"UA-B"

    middleware.process_request(retried)
    assert retried.headers[b"User-Agent"] == b"UA-B"


def test_redirect_refresh_and_counter_tracking():
    middleware = build(refresh_on="redirect")
    first = FakeRequest()
    middleware.process_request(first)
    redirected = FakeRequest(meta=dict(first.meta), headers=dict(first.headers))
    redirected.meta["redirect_times"] = 1
    middleware.process_request(redirected)
    assert redirected.headers[b"User-Agent"] == b"UA-B"
    assert redirected.meta[META_KEY]["redirect_times"] == 1


def test_domain_scope_and_cross_domain_redirect():
    middleware = build(scope="domain")
    first = FakeRequest("https://EXAMPLE.com/a")
    second = FakeRequest("https://example.com/b")
    middleware.process_request(first)
    middleware.process_request(second)
    assert first.headers[b"User-Agent"] == second.headers[b"User-Agent"] == b"UA-A"

    redirected = FakeRequest("https://two.example/b", meta=dict(first.meta), headers=dict(first.headers))
    redirected.meta["redirect_times"] = 1
    middleware.process_request(redirected)
    assert redirected.headers[b"User-Agent"] == b"UA-B"
    assert redirected.meta[META_KEY]["scope_key"] == ("domain", "two.example")


def test_sticky_refresh_updates_scope_cache():
    middleware = build(scope="domain", refresh_on=["retry"])
    first = FakeRequest()
    middleware.process_request(first)
    retried = FakeRequest(meta=dict(first.meta), headers=dict(first.headers))
    retried.meta["retry_times"] = 1
    middleware.process_request(retried)
    assert retried.headers[b"User-Agent"] == b"UA-B"

    later = FakeRequest()
    middleware.process_request(later)
    assert later.headers[b"User-Agent"] == b"UA-B"


def test_spider_cookiejar_and_proxy_scopes():
    spider = build(scope="spider")
    one, two = FakeRequest(), FakeRequest("https://other.example")
    spider.process_request(one)
    spider.process_request(two)
    assert one.headers[b"User-Agent"] == two.headers[b"User-Agent"] == b"UA-A"

    cookiejar = build(scope="cookiejar")
    c1 = FakeRequest(meta={"cookiejar": "account-a"})
    c2 = FakeRequest(meta={"cookiejar": "account-a"})
    c3 = FakeRequest(meta={"cookiejar": "account-b"})
    cookiejar.process_request(c1)
    cookiejar.process_request(c2)
    cookiejar.process_request(c3)
    assert c1.headers[b"User-Agent"] == c2.headers[b"User-Agent"] == b"UA-A"
    assert c3.headers[b"User-Agent"] == b"UA-B"

    proxy = build(scope="proxy")
    direct1, direct2 = FakeRequest(), FakeRequest()
    proxied = FakeRequest(meta={"proxy": "http://proxy.example:8080"})
    proxy.process_request(direct1)
    proxy.process_request(direct2)
    proxy.process_request(proxied)
    assert direct1.headers[b"User-Agent"] == direct2.headers[b"User-Agent"] == b"UA-A"
    assert proxied.headers[b"User-Agent"] == b"UA-B"


def test_explicit_header_respected_and_overwrite_mode():
    middleware = build()
    request = FakeRequest(headers={b"User-Agent": b"Explicit/1.0"})
    middleware.process_request(request)
    assert request.headers[b"User-Agent"] == b"Explicit/1.0"
    assert META_KEY not in request.meta

    overwrite = build(overwrite_existing=True)
    overwrite.process_request(request)
    assert request.headers[b"User-Agent"] == b"UA-A"

    string_header = FakeRequest(headers={"User-Agent": "Explicit/2.0"})
    middleware.process_request(string_header)
    assert string_header.headers["User-Agent"] == "Explicit/2.0"


def test_explicit_override_clears_stale_metadata_and_non_ascii_header_is_preserved():
    middleware = build()
    request = FakeRequest()
    middleware.process_request(request)
    request.headers[b"User-Agent"] = "Café".encode()
    middleware.process_request(request)
    assert META_KEY not in request.meta


def test_stats_are_emitted_for_selection_reuse_refresh_scope_change_and_skip():
    stats = FakeStats()
    middleware = build(scope="domain", refresh_on=["retry"], stats=stats)
    first = FakeRequest()
    middleware.process_request(first)
    same = FakeRequest(meta=dict(first.meta), headers=dict(first.headers))
    middleware.process_request(same)
    retry = FakeRequest(meta=dict(first.meta), headers=dict(first.headers))
    retry.meta["retry_times"] = 1
    middleware.process_request(retry)
    changed = FakeRequest("https://other.example", meta=dict(retry.meta), headers=dict(retry.headers))
    changed.meta["redirect_times"] = 1
    middleware.process_request(changed)
    explicit = FakeRequest(headers={b"User-Agent": b"Explicit"})
    middleware.process_request(explicit)

    assert stats.values["ua_rotator/selected"] >= 3
    assert stats.values["ua_rotator/reused"] == 1
    assert stats.values["ua_rotator/refreshed"] == 1
    assert stats.values["ua_rotator/scope_changed"] == 1
    assert stats.values["ua_rotator/skipped_existing_header"] == 1


def test_configuration_validation():
    with pytest.raises(ValueError, match="boolean"):
        ScrapyUserAgentMiddleware(UserAgentRotator(["UA"]), overwrite_existing="false")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Invalid UA rotation scope"):
        ScrapyUserAgentMiddleware(UserAgentRotator(["UA"]), scope="unknown")
    with pytest.raises(ValueError, match="Invalid refresh"):
        build(refresh_on=["rety"])
    with pytest.raises(ValueError, match="strings"):
        build(refresh_on=[1])
    with pytest.raises(ValueError, match="iterable"):
        build(refresh_on=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires rotator policy"):
        ScrapyUserAgentMiddleware(UserAgentRotator(["UA"], policy="per_request"), scope="domain")


def test_request_validation():
    with pytest.raises(ValueError, match="non-negative"):
        build().process_request(FakeRequest(meta={"retry_times": -1}))
    with pytest.raises(ValueError, match="non-negative"):
        build().process_request(FakeRequest(meta={"redirect_times": True}))
    with pytest.raises(ValueError, match="Invalid request URL"):
        build(scope="domain").process_request(FakeRequest("http://[bad"))
    with pytest.raises(ValueError, match="hashable"):
        build(scope="cookiejar").process_request(FakeRequest(meta={"cookiejar": []}))


def test_from_crawler_with_entries_and_file(tmp_path):
    crawler = FakeCrawler({
        "UA_ROTATOR_ENTRIES": [("UA-A", 1), ("UA-B", 1)],
        "UA_ROTATOR_SCOPE": "domain",
        "UA_ROTATOR_REFRESH_ON": "retry, redirect",
        "UA_ROTATOR_OVERWRITE_EXISTING": "true",
        "UA_ROTATOR_MAX_KEY_CACHE": 17,
    })
    middleware = ScrapyUserAgentMiddleware.from_crawler(crawler)
    assert middleware.scope == "domain"
    assert middleware.refresh_on == {"retry", "redirect"}
    assert middleware.overwrite_existing is True
    assert middleware.rotator.max_key_cache == 17
    assert middleware.stats is crawler.stats

    path = tmp_path / "profiles.json"
    UserAgentRotator(["UA-X"]).save(path)
    file_crawler = FakeCrawler({"UA_ROTATOR_FILE": str(path)})
    from_file = ScrapyUserAgentMiddleware.from_crawler(file_crawler)
    request = FakeRequest()
    from_file.process_request(request)
    assert request.headers[b"User-Agent"] == b"UA-X"


def test_from_crawler_defaults_to_bundled_profile_and_accepts_named_bundle():
    default = ScrapyUserAgentMiddleware.from_crawler(FakeCrawler({}))
    assert len(default.rotator) == 85

    named = ScrapyUserAgentMiddleware.from_crawler(
        FakeCrawler({"UA_ROTATOR_BUNDLED_PROFILE": "global_2026-07-31.json"})
    )
    assert len(named.rotator) == 63


def test_from_crawler_conflicting_sources_fail_fast():
    for values in (
        {"UA_ROTATOR_FILE": "x", "UA_ROTATOR_ENTRIES": ["UA"]},
        {"UA_ROTATOR_FILE": "x", "UA_ROTATOR_BUNDLED_PROFILE": "global_2026-07-31.json"},
        {"UA_ROTATOR_ENTRIES": ["UA"], "UA_ROTATOR_BUNDLED_PROFILE": "global_2026-07-31.json"},
    ):
        with pytest.raises(ValueError, match="at most one"):
            ScrapyUserAgentMiddleware.from_crawler(FakeCrawler(values))


def test_from_crawler_can_be_disabled_explicitly():
    try:
        from scrapy.exceptions import NotConfigured as expected
    except ImportError:  # pragma: no cover - depends on the environment
        expected = RuntimeError
    with pytest.raises(expected):
        ScrapyUserAgentMiddleware.from_crawler(FakeCrawler({"UA_ROTATOR_ENABLED": False}))
    enabled = ScrapyUserAgentMiddleware.from_crawler(FakeCrawler({"UA_ROTATOR_ENABLED": "1"}))
    assert len(enabled.rotator) == 85


def test_from_crawler_rejects_invalid_bundled_profile_setting():
    with pytest.raises(ValueError, match="file name"):
        ScrapyUserAgentMiddleware.from_crawler(
            FakeCrawler({"UA_ROTATOR_BUNDLED_PROFILE": True})
        )


def test_real_scrapy_request_when_available():
    scrapy_http = pytest.importorskip("scrapy.http")
    request = scrapy_http.Request("https://example.com")
    middleware = build(scope="domain")
    middleware.process_request(request)
    assert request.headers[b"User-Agent"] == b"UA-A"
    assert request.meta[META_KEY]["scope_key"] == ("domain", "example.com")


def seeded(scope, entries, *, refresh_on=(), seed=0, **kwargs):
    policy = {"request": "per_request", "spider": "per_session"}.get(scope, "per_key")
    rotator = UserAgentRotator(entries, policy=policy, rng=random.Random(seed))
    return ScrapyUserAgentMiddleware(rotator, scope=scope, refresh_on=refresh_on, **kwargs)


def lineage_copy(request, url=None, **meta):
    copied = FakeRequest(url or request.url, meta=dict(request.meta), headers=dict(request.headers))
    copied.meta.update(meta)
    return copied


@pytest.mark.parametrize("scope", ["request", "spider", "domain", "cookiejar", "proxy"])
def test_refresh_on_retry_always_changes_identity_in_every_scope(scope):
    # A dominant weight makes a repeat almost certain with select(force_new=True).
    middleware = seeded(scope, [("UA-HOT", 10_000), ("UA-COLD", 1)], refresh_on=["retry"])
    for attempt in range(50):
        first = FakeRequest(f"https://site{attempt}.example/")
        middleware.process_request(first)
        retried = lineage_copy(first, retry_times=1)
        middleware.process_request(retried)
        assert retried.headers[b"User-Agent"] != first.headers[b"User-Agent"]
        middleware.rotator.reset()


def test_concurrent_failures_in_one_domain_converge_instead_of_churning():
    stats = FakeStats()
    middleware = seeded(
        "domain", [f"UA-{i}" for i in range(20)], refresh_on=["retry"], stats=stats
    )
    in_flight = [FakeRequest(f"https://example.com/{i}") for i in range(5)]
    for request in in_flight:
        middleware.process_request(request)
    original = {request.headers[b"User-Agent"] for request in in_flight}
    assert len(original) == 1

    retries = [lineage_copy(request, retry_times=1) for request in in_flight]
    for request in retries:
        middleware.process_request(request)
    replacements = {request.headers[b"User-Agent"] for request in retries}
    assert len(replacements) == 1
    assert replacements != original
    later = FakeRequest("https://example.com/later")
    middleware.process_request(later)
    assert {later.headers[b"User-Agent"]} == replacements
    assert stats.values["ua_rotator/refreshed"] == 5


def test_cross_domain_redirect_with_refresh_adopts_destination_identity():
    middleware = build(scope="domain", refresh_on=["redirect"])
    destination = FakeRequest("https://two.example/")
    middleware.process_request(destination)
    start = FakeRequest("https://one.example/")
    middleware.process_request(start)
    assert destination.headers[b"User-Agent"] == b"UA-A"
    assert start.headers[b"User-Agent"] == b"UA-B"
    redirected = lineage_copy(start, "https://two.example/landing", redirect_times=1)
    middleware.process_request(redirected)
    assert redirected.headers[b"User-Agent"] == destination.headers[b"User-Agent"]


def test_framework_default_user_agent_is_replaced_not_treated_as_explicit():
    stats = FakeStats()
    middleware = ScrapyUserAgentMiddleware.from_crawler(
        FakeCrawler(
            {
                "UA_ROTATOR_ENTRIES": ["UA-A"],
                "USER_AGENT": "Scrapy/2.20 (+https://scrapy.org)",
                "DEFAULT_REQUEST_HEADERS": {"user-agent": b"Project-Default/1.0"},
            }
        )
    )
    middleware.stats = stats
    assert middleware.default_user_agents == {
        "Scrapy/2.20 (+https://scrapy.org)",
        "Project-Default/1.0",
    }
    for header in (b"Scrapy/2.20 (+https://scrapy.org)", b"Project-Default/1.0"):
        request = FakeRequest(headers={b"User-Agent": header})
        middleware.process_request(request)
        assert request.headers[b"User-Agent"] == b"UA-A"
    explicit = FakeRequest(headers={b"User-Agent": b"Explicit/1.0"})
    middleware.process_request(explicit)
    assert explicit.headers[b"User-Agent"] == b"Explicit/1.0"
    assert stats.values == {
        "ua_rotator/replaced_default_header": 2,
        "ua_rotator/selected": 2,
        "ua_rotator/skipped_existing_header": 1,
    }


def test_default_user_agents_validation():
    rotator = UserAgentRotator(["UA"])
    with pytest.raises(ValueError, match="iterable"):
        ScrapyUserAgentMiddleware(rotator, default_user_agents="Scrapy/2.0")
    with pytest.raises(ValueError, match="non-empty"):
        ScrapyUserAgentMiddleware(rotator, default_user_agents=[""])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "<direct>"),
        ("http://proxy.example:8080", "http://proxy.example:8080"),
        ("HTTP://user:secret@Proxy.Example:8080", "http://proxy.example:8080"),
        ("http://user:secret@proxy.example", "http://proxy.example"),
        ("proxy.example:3128", "proxy.example:3128"),
        ("//proxy.example:3128", "proxy.example:3128"),
        ("http://[2001:db8::1]:8080", "http://[2001:db8::1]:8080"),
    ],
)
def test_proxy_scope_key_is_normalized_and_credential_free(value, expected):
    assert ScrapyUserAgentMiddleware._proxy_key(value) == expected


def test_proxy_scope_identity_survives_credential_stripping_and_never_stores_secrets():
    middleware = seeded("proxy", [f"UA-{i}" for i in range(10)])
    first = FakeRequest(meta={"proxy": "http://user:secret@proxy.example:8080"})
    middleware.process_request(first)
    assert "secret" not in repr(first.meta[META_KEY])
    # Scrapy's HttpProxyMiddleware rewrites meta["proxy"] without credentials.
    retried = lineage_copy(first, proxy="http://proxy.example:8080", retry_times=1)
    middleware.process_request(retried)
    assert retried.headers[b"User-Agent"] == first.headers[b"User-Agent"]
    unparseable = FakeRequest(meta={"proxy": "http://user:secret@proxy.example:notaport"})
    middleware.process_request(unparseable)
    assert "secret" not in repr(unparseable.meta[META_KEY])
    assert unparseable.meta[META_KEY]["scope_key"] == (
        "proxy",
        "unparsed-proxy:http://proxy.example:notaport",
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://alice:hunter2@:8080",
        "alice:hunter2@proxy.example:bad",
        "//alice:hunter2@[::1:8080",
        "socks5h://alice:hunter2@host:99999",
    ],
)
def test_unparseable_proxy_keys_never_carry_credentials(value):
    key = ScrapyUserAgentMiddleware._proxy_key(value)
    assert key.startswith("unparsed-proxy:")
    assert "hunter2" not in key and "alice" not in key


def test_cross_domain_redirect_with_refresh_never_rerolls_destination_identity():
    # Both domains hold the same identity; leaving one.example must not
    # replace two.example's sticky identity.
    rotator = UserAgentRotator(
        ["UA-A", "UA-B", "UA-C"], policy="per_key", rng=SequenceRng(["UA-A", "UA-A", "UA-B"])
    )
    stats = FakeStats()
    middleware = ScrapyUserAgentMiddleware(
        rotator, scope="domain", refresh_on=["redirect"], stats=stats
    )
    destination = FakeRequest("https://two.example/")
    middleware.process_request(destination)
    start = FakeRequest("https://one.example/")
    middleware.process_request(start)
    assert destination.headers[b"User-Agent"] == start.headers[b"User-Agent"] == b"UA-A"
    redirected = lineage_copy(start, "https://two.example/landing", redirect_times=1)
    middleware.process_request(redirected)
    assert redirected.headers[b"User-Agent"] == b"UA-A"
    assert rotator.select(key=("domain", "two.example")) == "UA-A"
    assert stats.values.get("ua_rotator/refreshed") is None
    assert stats.values["ua_rotator/scope_changed"] == 1


def test_cookiejar_default_matches_scrapy_cookies_middleware():
    middleware = seeded("cookiejar", [f"UA-{i}" for i in range(10)])
    implicit = FakeRequest()
    middleware.process_request(implicit)
    assert implicit.meta[META_KEY]["scope_key"] == ("cookiejar", None)
    explicit_zero = FakeRequest(meta={"cookiejar": 0})
    middleware.process_request(explicit_zero)
    assert explicit_zero.meta[META_KEY]["scope_key"] == ("cookiejar", 0)


def test_hostless_urls_share_one_domain_identity():
    middleware = seeded("domain", ["UA-A", "UA-B"])
    data = FakeRequest("data:,hello")
    file_request = FakeRequest("file:///tmp/page.html")
    middleware.process_request(data)
    middleware.process_request(file_request)
    assert data.meta[META_KEY]["scope_key"] == ("domain", None)
    assert data.headers[b"User-Agent"] == file_request.headers[b"User-Agent"]


def test_from_crawler_logs_effective_configuration(caplog):
    with caplog.at_level(logging.INFO, logger="apex_ua_rotator.scrapy"):
        ScrapyUserAgentMiddleware.from_crawler(
            FakeCrawler({"UA_ROTATOR_SCOPE": "domain", "UA_ROTATOR_REFRESH_ON": "retry"})
        )
    assert "85 profiles from bundled profile 'global_2026-11-14.json'" in caplog.text
    assert "scope=domain" in caplog.text
    assert "refresh_on=['retry']" in caplog.text


def test_compatibility_module_reexports_adapter():
    import scrapy_user_agent_adapter
    import user_agent_rotator

    assert scrapy_user_agent_adapter.ScrapyUserAgentMiddleware is ScrapyUserAgentMiddleware
    assert user_agent_rotator.UserAgentRotator is UserAgentRotator
