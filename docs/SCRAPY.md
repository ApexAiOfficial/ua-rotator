# Scrapy adapter

`ScrapyUserAgentMiddleware` maps Scrapy's request lifecycle onto the selection engine. It picks an identity, records it in `Request.meta`, keeps it across retries and redirects, and replaces it only when a rule you configured says so.

Supported: Scrapy 2.11 through the latest 2.x release, tested in CI at both ends. Scrapy stays an optional dependency, and importing `apex_ua_rotator.scrapy` does not import Scrapy.

```bash
python -m pip install "Scrapy>=2.11,<3"
```

## Minimal configuration

```python
DOWNLOADER_MIDDLEWARES = {
    "apex_ua_rotator.scrapy.ScrapyUserAgentMiddleware": 90,
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
}

UA_ROTATOR_SCOPE = "domain"
ROBOTSTXT_USER_AGENT = "YourCrawlerName/1.0"
```

With no source setting, the default bundled profile is used. `ROBOTSTXT_USER_AGENT` controls which `robots.txt` rules apply to your crawler, independently of the rotated header.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `UA_ROTATOR_ENABLED` | `True` | `False` disables the middleware (`NotConfigured`). |
| `UA_ROTATOR_SCOPE` | `"request"` | Identity boundary; see [Scopes](#scopes). |
| `UA_ROTATOR_REFRESH_ON` | `[]` | `"retry"`, `"redirect"`, both, or a comma-separated string. |
| `UA_ROTATOR_OVERWRITE_EXISTING` | `False` | Replace User-Agent headers that requests already carry. |
| `UA_ROTATOR_MAX_KEY_CACHE` | `4096` | LRU bound for sticky scope keys. |
| `UA_ROTATOR_BUNDLED_PROFILE` | default profile | Named packaged profile, e.g. `"global_2026-07-31-r2.json"`. |
| `UA_ROTATOR_FILE` | — | Path to an external profile document. |
| `UA_ROTATOR_ENTRIES` | — | In-memory entries, e.g. `[("ExampleClient/1.0", 1)]`. |

Configure at most one of the three source settings. Conflicting sources, unknown scopes, and invalid refresh events raise `ValueError` when the crawler starts, so a misconfigured crawl fails immediately instead of running without rotation.

At startup the middleware logs one INFO line with the effective source, profile count, scope, and refresh rules.

## Scopes

| Scope | Core policy | One identity per | Scope key |
|---|---|---|---|
| `request` | `per_request` | request lineage | — |
| `spider` | `per_session` | middleware instance (crawl) | — |
| `domain` | `per_key` | hostname, case-folded | `("domain", "example.com")` |
| `cookiejar` | `per_key` | Scrapy cookie jar | `("cookiejar", meta["cookiejar"])` |
| `proxy` | `per_key` | proxy endpoint, or direct traffic | `("proxy", "http://host:port")` |

A **lineage** is a request together with the retries and redirects Scrapy derives from it. The middleware stores its record under `Request.meta["_apex_ua_rotator"]`, and Scrapy copies `meta` into those derived requests, so the identity follows the lineage.

Details that matter in production:

- **`domain`**: subdomains are separate hosts. URLs without a hostname (`data:`, `file:`) share one identity.
- **`cookiejar`**: requests without a `cookiejar` key use the key `None`, the same default jar as Scrapy's `CookiesMiddleware`. An explicit `cookiejar=0` is a different jar and gets a different identity.
- **`proxy`**: the key is normalized to scheme, host, and port. Credentials in `meta["proxy"]` are never copied into the lineage record, not even as a hash, and the identity stays the same after `HttpProxyMiddleware` moves them into a `Proxy-Authorization` header. Requests without a proxy share the key `"<direct>"`.

### Ordering for `proxy` scope

`HttpProxyMiddleware` (priority 750) fills in `meta["proxy"]` from the `http_proxy`/`https_proxy` environment variables. At priority 90 the rotator runs before it, so the first attempt looks like direct traffic and its retries look like proxy traffic, which changes identity mid-lineage. Proxy-rotation middlewares that set `meta["proxy"]` have the same effect. For `proxy` scope, run the rotator after them:

```python
DOWNLOADER_MIDDLEWARES = {
    "apex_ua_rotator.scrapy.ScrapyUserAgentMiddleware": 760,
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
}
UA_ROTATOR_SCOPE = "proxy"
```

The middleware logs a warning at startup when `proxy` scope is ordered before `HttpProxyMiddleware`.

## Refresh rules

By default, retries and redirects keep the identity of the original request:

```python
UA_ROTATOR_REFRESH_ON = []
```

To change identity when Scrapy retries or follows a redirect:

```python
UA_ROTATOR_REFRESH_ON = ["retry"]              # e.g. after 429/503 or a timeout
UA_ROTATOR_REFRESH_ON = ["redirect"]
UA_ROTATOR_REFRESH_ON = ["retry", "redirect"]
```

A refresh triggers when the request's `retry_times` or `redirect_times` counter has advanced past the value in the lineage record. It guarantees two things:

- **A different identity.** The User-Agent in use when the retry or redirect happened is excluded from the draw, as long as another positively weighted entry exists. (Re-drawing from the full distribution would often return the same string: the most common entry in the bundled profile carries about 25% of the weight.)
- **Convergence in sticky scopes.** For `spider`, `domain`, `cookiejar`, and `proxy`, the replacement becomes the scope's identity for later requests. When several in-flight requests in one scope fail together, the first refresh picks the replacement and the others adopt it instead of each drawing again.

A redirect that also changes the scope key, such as a cross-domain redirect in `domain` scope, adopts the destination scope's identity. Refresh rules apply within a scope, so the destination's identity is never replaced because the lineage arrived from somewhere else.

Retries issued from spider callbacks through `scrapy.downloadermiddlewares.retry.get_retry_request()` increase `retry_times` as well, so `"retry"` also covers soft bans you detect yourself.

## Existing headers

A User-Agent that a request already carries is respected by default:

```python
yield scrapy.Request(url, headers={"User-Agent": "Partner-Integration/2.0"})
```

The middleware leaves that header alone and removes its lineage record. Two cases are not treated as explicit:

- a header equal to the `USER_AGENT` setting or to `DEFAULT_REQUEST_HEADERS["User-Agent"]`, which Scrapy's own middlewares set when they run first; it is replaced and counted as `ua_rotator/replaced_default_header`;
- a header that Scrapy copied from the lineage record into a retry or redirect.

`UA_ROTATOR_OVERWRITE_EXISTING = True` replaces every existing header.

## Statistics

When a stats collector is available, the middleware records:

| Key | Incremented when |
|---|---|
| `ua_rotator/selected` | a new identity is drawn (first request, scope change, or refresh) |
| `ua_rotator/reused` | a retry or redirect keeps its lineage identity |
| `ua_rotator/refreshed` | a refresh rule replaced the identity |
| `ua_rotator/scope_changed` | the scope key changed within a lineage |
| `ua_rotator/skipped_existing_header` | an explicit header was respected |
| `ua_rotator/replaced_default_header` | a framework-default header was replaced |

## Local smoke test

```bash
apex-ua-rotator smoke-scrapy
```

The smoke test starts a temporary HTTP server on `127.0.0.1` and runs two crawlers through Scrapy's real downloader stack. One checks redirect and retry continuity with request metadata and statistics. The other checks that `UA_ROTATOR_REFRESH_ON = ["retry"]` sends the retry with a different User-Agent. It prints a JSON report and exits with 0 (PASS), 1 (FAIL), or 2 (SKIP, Scrapy not installed).
