# Scrapy adapter

`ScrapyUserAgentMiddleware` maps Scrapy request lifecycle state onto the generic selection engine.

## Minimal configuration

```python
DOWNLOADER_MIDDLEWARES = {
    "apex_ua_rotator.scrapy.ScrapyUserAgentMiddleware": 90,
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
}

UA_ROTATOR_SCOPE = "domain"
ROBOTSTXT_USER_AGENT = "YourCrawlerName/1.0"
```

The default bundled profile is used when no source is configured.

## Profile sources

Configure at most one:

```python
UA_ROTATOR_BUNDLED_PROFILE = "global_2026-07-31.json"
UA_ROTATOR_FILE = "/absolute/path/to/profiles.json"
UA_ROTATOR_ENTRIES = [("ExampleClient/1.0", 1)]
```

## Scope behavior

| Scope | Core policy | Identity boundary |
|---|---|---|
| `request` | `per_request` | request lineage |
| `spider` | `per_session` | middleware instance |
| `domain` | `per_key` | parsed hostname |
| `cookiejar` | `per_key` | Scrapy cookie jar key |
| `proxy` | `per_key` | proxy setting or direct traffic |

The middleware stores its lineage record in `Request.meta` under `_apex_ua_rotator`. Scrapy copies metadata into retry and redirect requests, allowing the same identity to continue across the lineage.

## Refresh rules

By default, retries and redirects keep the existing identity.

```python
UA_ROTATOR_REFRESH_ON = []
```

A refresh can be requested when the retry or redirect counter advances:

```python
UA_ROTATOR_REFRESH_ON = ["retry"]
UA_ROTATOR_REFRESH_ON = ["redirect"]
UA_ROTATOR_REFRESH_ON = ["retry", "redirect"]
```

For sticky scopes, a refresh updates the underlying scope cache so later requests in that scope receive the replacement identity.

## Existing headers

The adapter preserves an explicitly supplied User-Agent by default:

```python
UA_ROTATOR_OVERWRITE_EXISTING = False
```

Set the value to true only when the middleware should replace every existing User-Agent header.

## Cache size

`domain`, `cookiejar`, and `proxy` scopes use the core LRU key cache:

```python
UA_ROTATOR_MAX_KEY_CACHE = 4096
```

## Statistics

When a Scrapy stats collector is available, the adapter records:

- `ua_rotator/selected`
- `ua_rotator/reused`
- `ua_rotator/refreshed`
- `ua_rotator/scope_changed`
- `ua_rotator/skipped_existing_header`

## Local smoke test

```bash
apex-ua-rotator smoke-scrapy
```

The test uses only `127.0.0.1` and verifies a real redirect and retry through Scrapy's downloader middleware stack.
