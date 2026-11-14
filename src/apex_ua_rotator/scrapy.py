"""Optional Scrapy downloader-middleware adapter.

Scrapy-specific settings, request metadata, retry/redirect counters, and scope
mapping live here rather than in the reusable selection engine. Importing this
module does not import Scrapy; only :meth:`ScrapyUserAgentMiddleware.from_crawler`
touches Scrapy APIs, and it is only ever called by a running crawler.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Hashable, Iterable, Mapping, cast
from urllib.parse import urlsplit

from .core import DEFAULT_PROFILE_NAME, RotationPolicy, UserAgentRotator

logger = logging.getLogger(__name__)

VALID_SCOPES = frozenset({"request", "spider", "domain", "cookiejar", "proxy"})
VALID_REFRESH_EVENTS = frozenset({"retry", "redirect"})
META_KEY = "_apex_ua_rotator"
DIRECT_PROXY_KEY = "<direct>"

_SOURCE_SETTINGS = "UA_ROTATOR_FILE, UA_ROTATOR_ENTRIES, or UA_ROTATOR_BUNDLED_PROFILE"
_HTTP_PROXY_MIDDLEWARE = "scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware"
_USERINFO = re.compile(r"^((?:[A-Za-z][A-Za-z0-9+.-]*:)?//)?[^/?#]*@")


@dataclass(frozen=True)
class _Identity:
    user_agent: str
    scope_key: Hashable | None
    retry_times: int
    redirect_times: int

    def to_meta(self) -> dict[str, Any]:
        return {
            "user_agent": self.user_agent,
            "scope_key": self.scope_key,
            "retry_times": self.retry_times,
            "redirect_times": self.redirect_times,
        }

    @classmethod
    def from_meta(cls, raw: Any) -> "_Identity | None":
        if not isinstance(raw, Mapping):
            return None
        try:
            user_agent = raw["user_agent"]
            retry_times = raw["retry_times"]
            redirect_times = raw["redirect_times"]
        except KeyError:
            return None
        if not isinstance(user_agent, str) or not user_agent.isascii():
            return None
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in user_agent):
            return None
        if (
            isinstance(retry_times, bool)
            or isinstance(redirect_times, bool)
            or not isinstance(retry_times, int)
            or not isinstance(redirect_times, int)
            or retry_times < 0
            or redirect_times < 0
        ):
            return None
        scope_key = raw.get("scope_key")
        if scope_key is not None:
            try:
                hash(scope_key)
            except TypeError:
                return None
        return cls(user_agent, scope_key, retry_times, redirect_times)


def _not_configured(*args: Any) -> Exception:
    try:
        from scrapy.exceptions import NotConfigured
    except ImportError:  # pragma: no cover - from_crawler is only called by Scrapy
        return RuntimeError(*args)
    return cast(Exception, NotConfigured(*args))


class ScrapyUserAgentMiddleware:
    """Assign and preserve User-Agent identity across request lineages.

    The adapter records the selected identity in ``Request.meta``. Scrapy retry
    and redirect requests copy metadata, allowing the adapter to preserve the
    same identity unless an explicit refresh rule applies.

    A User-Agent header that was already on the request is kept, unless
    ``overwrite_existing`` is true or the header equals one of
    ``default_user_agents`` (Scrapy's ``USER_AGENT`` and
    ``DEFAULT_REQUEST_HEADERS`` values, when built with ``from_crawler``),
    which are framework defaults rather than deliberate per-request choices.
    """

    def __init__(
        self,
        rotator: UserAgentRotator,
        *,
        scope: str = "request",
        refresh_on: Iterable[str] = (),
        overwrite_existing: bool = False,
        stats: Any = None,
        default_user_agents: Iterable[str] = (),
    ) -> None:
        if not isinstance(overwrite_existing, bool):
            raise ValueError("overwrite_existing must be a boolean.")
        self.scope = self._validate_scope(scope)
        self.refresh_on = self._validate_refresh_events(refresh_on)
        self.overwrite_existing = overwrite_existing
        self.stats = stats
        self.rotator = rotator
        self.default_user_agents = self._validate_default_user_agents(
            default_user_agents
        )
        self._validate_policy_mapping()

    @classmethod
    def from_crawler(cls, crawler: Any) -> "ScrapyUserAgentMiddleware":
        """Build middleware from Scrapy settings.

        Raises ``NotConfigured`` when ``UA_ROTATOR_ENABLED`` is false, and
        ``ValueError`` for invalid or conflicting settings, so a misconfigured
        crawl fails at startup instead of running without rotation.
        """

        settings = crawler.settings
        if not settings.getbool("UA_ROTATOR_ENABLED", True):
            raise _not_configured()

        file_path = settings.get("UA_ROTATOR_FILE")
        entries = settings.get("UA_ROTATOR_ENTRIES")
        bundled_profile = settings.get("UA_ROTATOR_BUNDLED_PROFILE")

        has_file = file_path not in (None, "")
        has_entries = entries is not None
        has_bundled = bundled_profile not in (None, "", False)
        if sum((has_file, has_entries, has_bundled)) > 1:
            raise ValueError(f"Configure at most one of {_SOURCE_SETTINGS}.")

        scope = settings.get("UA_ROTATOR_SCOPE", "request")
        policy = cls._policy_for_scope(scope)
        max_key_cache = settings.getint("UA_ROTATOR_MAX_KEY_CACHE", 4096)

        if has_file:
            rotator = UserAgentRotator.from_file(
                file_path,
                policy=policy,
                max_key_cache=max_key_cache,
            )
            source = f"file {str(file_path)!r}"
        elif has_entries:
            rotator = UserAgentRotator(
                entries,
                policy=policy,
                max_key_cache=max_key_cache,
            )
            source = "UA_ROTATOR_ENTRIES"
        else:
            profile_name = bundled_profile or DEFAULT_PROFILE_NAME
            if not isinstance(profile_name, str):
                raise ValueError("UA_ROTATOR_BUNDLED_PROFILE must be a file name.")
            rotator = UserAgentRotator.from_bundled_profile(
                profile_name,
                policy=policy,
                max_key_cache=max_key_cache,
            )
            source = f"bundled profile {profile_name!r}"

        refresh_on = cls._parse_refresh_setting(
            settings.get("UA_ROTATOR_REFRESH_ON", [])
        )
        overwrite = settings.getbool("UA_ROTATOR_OVERWRITE_EXISTING", False)
        middleware = cls(
            rotator,
            scope=scope,
            refresh_on=refresh_on,
            overwrite_existing=overwrite,
            stats=getattr(crawler, "stats", None),
            default_user_agents=cls._framework_default_user_agents(settings),
        )
        if middleware.scope == "proxy":
            cls._warn_if_before_proxy_middleware(settings)
        logger.info(
            "Apex UA Rotator: %d profiles from %s; scope=%s, refresh_on=%s, "
            "overwrite_existing=%s",
            len(rotator),
            source,
            middleware.scope,
            sorted(middleware.refresh_on) or "none",
            middleware.overwrite_existing,
        )
        return middleware

    def process_request(self, request: Any, spider: Any = None) -> None:
        """Set or preserve the request User-Agent and lineage metadata."""

        prior = _Identity.from_meta(request.meta.get(META_KEY))
        header_user_agent = self._header_user_agent(request)
        if header_user_agent is not None and not self.overwrite_existing:
            # A header matching our lineage record was copied by Scrapy into a
            # retry or redirect; it is ours, not an external override.
            if prior is None or header_user_agent != prior.user_agent:
                if header_user_agent in self.default_user_agents:
                    # Set by Scrapy's own UserAgentMiddleware or
                    # DefaultHeadersMiddleware running before this one.
                    self._inc("ua_rotator/replaced_default_header")
                else:
                    request.meta.pop(META_KEY, None)
                    self._inc("ua_rotator/skipped_existing_header")
                    return None

        retry_times = self._counter(request.meta.get("retry_times", 0), "retry_times")
        redirect_times = self._counter(
            request.meta.get("redirect_times", 0), "redirect_times"
        )
        scope_key = self._scope_key(request)
        selection_key = (
            scope_key if self.rotator.policy is RotationPolicy.PER_KEY else None
        )

        if prior is None:
            user_agent = self.rotator.select(key=selection_key)
            self._inc("ua_rotator/selected")
        elif prior.scope_key != scope_key:
            # The lineage left its scope (e.g. a cross-domain redirect): adopt
            # the destination scope's identity. Refresh rules apply within a
            # scope, so the destination's identity is never re-rolled here.
            user_agent = self.rotator.select(key=selection_key)
            self._inc("ua_rotator/selected")
            self._inc("ua_rotator/scope_changed")
        elif (
            "retry" in self.refresh_on and retry_times > prior.retry_times
        ) or (
            "redirect" in self.refresh_on and redirect_times > prior.redirect_times
        ):
            # Avoid the identity that was in use when the retry or redirect
            # happened, and converge with concurrent refreshes in the scope.
            user_agent = self.rotator.refresh(key=selection_key, previous=prior.user_agent)
            self._inc("ua_rotator/selected")
            self._inc("ua_rotator/refreshed")
        else:
            user_agent = prior.user_agent
            self._inc("ua_rotator/reused")

        request.meta[META_KEY] = _Identity(
            user_agent=user_agent,
            scope_key=scope_key,
            retry_times=retry_times,
            redirect_times=redirect_times,
        ).to_meta()
        request.headers[b"User-Agent"] = user_agent.encode("ascii")
        return None

    def _validate_policy_mapping(self) -> None:
        expected = self._policy_for_scope(self.scope)
        if self.rotator.policy is not expected:
            raise ValueError(
                f"scope={self.scope!r} requires rotator policy={expected.value!r}, "
                f"got {self.rotator.policy.value!r}."
            )

    def _scope_key(self, request: Any) -> Hashable | None:
        if self.scope in {"request", "spider"}:
            return None
        if self.scope == "domain":
            try:
                hostname = urlsplit(request.url).hostname
            except ValueError as exc:
                raise ValueError(f"Invalid request URL: {request.url!r}") from exc
            # Host-less URLs (data:, file:) share one identity instead of failing.
            return ("domain", hostname.casefold() if hostname else None)
        if self.scope == "cookiejar":
            # Same default as Scrapy's CookiesMiddleware: no meta key -> jar None.
            return ("cookiejar", self._hashable_meta(request.meta.get("cookiejar")))
        if self.scope == "proxy":
            return ("proxy", self._proxy_key(request.meta.get("proxy")))
        raise AssertionError(f"Unhandled scope: {self.scope}")

    @classmethod
    def _proxy_key(cls, value: Any) -> Hashable:
        """Normalize a proxy URL into a credential-free scope key.

        Scrapy's HttpProxyMiddleware moves credentials from ``meta['proxy']``
        into a ``Proxy-Authorization`` header. Keying on scheme, host, and port
        keeps the identity stable across that rewrite and never copies
        credentials into the lineage record.
        """

        if value is None:
            return DIRECT_PROXY_KEY
        if not isinstance(value, str):
            return cls._hashable_meta(value)
        candidate = value if "//" in value else f"//{value}"
        try:
            parts = urlsplit(candidate)
            host = parts.hostname
            port = parts.port
        except ValueError:
            host = None
            port = None
        if not host:
            # Unparseable: keep a readable key, minus any userinfo. No digest of
            # the raw value, which could let someone confirm a guessed password.
            return "unparsed-proxy:" + _USERINFO.sub(r"\1", value)
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc = f"{netloc}:{port}"
        scheme = parts.scheme.lower()
        return f"{scheme}://{netloc}" if scheme else netloc

    @staticmethod
    def _header_user_agent(request: Any) -> str | None:
        value = request.headers.get(b"User-Agent")
        if value is None:
            value = request.headers.get("User-Agent")
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                return value.decode("ascii")
            except UnicodeDecodeError:
                return "<non-ascii-header>"
        return str(value)

    @staticmethod
    def _counter(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Request meta {name!r} must be a non-negative integer.")
        return value

    @staticmethod
    def _hashable_meta(value: Any) -> Hashable:
        try:
            hash(value)
        except TypeError as exc:
            raise ValueError("Selected scope metadata must be hashable.") from exc
        return cast(Hashable, value)

    @staticmethod
    def _validate_scope(scope: Any) -> str:
        if not isinstance(scope, str) or scope not in VALID_SCOPES:
            allowed = ", ".join(sorted(VALID_SCOPES))
            raise ValueError(
                f"Invalid UA rotation scope {scope!r}; expected: {allowed}."
            )
        return scope

    @staticmethod
    def _validate_refresh_events(values: Iterable[str]) -> frozenset[str]:
        if isinstance(values, str):
            values = [item.strip() for item in values.split(",") if item.strip()]
        try:
            parsed = frozenset(values)
        except TypeError as exc:
            raise ValueError("refresh_on must be an iterable of strings.") from exc
        if not all(isinstance(value, str) for value in parsed):
            raise ValueError("refresh_on values must be strings.")
        invalid = parsed - VALID_REFRESH_EVENTS
        if invalid:
            raise ValueError(f"Invalid refresh event(s): {sorted(invalid)!r}.")
        return parsed

    @staticmethod
    def _validate_default_user_agents(values: Iterable[str]) -> frozenset[str]:
        if isinstance(values, (str, bytes)):
            raise ValueError("default_user_agents must be an iterable of strings.")
        parsed = frozenset(values)
        if not all(isinstance(value, str) and value for value in parsed):
            raise ValueError("default_user_agents values must be non-empty strings.")
        return parsed

    @staticmethod
    def _parse_refresh_setting(value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set, frozenset)):
            return list(value)
        raise ValueError("UA_ROTATOR_REFRESH_ON must be a string or sequence.")

    @staticmethod
    def _framework_default_user_agents(settings: Any) -> list[str]:
        defaults: list[str] = []
        user_agent = settings.get("USER_AGENT")
        if isinstance(user_agent, str) and user_agent:
            defaults.append(user_agent)
        headers = settings.get("DEFAULT_REQUEST_HEADERS")
        if isinstance(headers, Mapping):
            for name, value in headers.items():
                if str(name).casefold() != "user-agent":
                    continue
                if isinstance(value, bytes):
                    value = value.decode("ascii", "replace")
                if isinstance(value, str) and value:
                    defaults.append(value)
        return defaults

    @classmethod
    def _warn_if_before_proxy_middleware(cls, settings: Any) -> None:
        """Warn when proxy scope runs before Scrapy assigns ``meta['proxy']``.

        HttpProxyMiddleware sets ``meta['proxy']`` from environment variables
        in its own ``process_request``. If this middleware runs first, the
        first attempt is keyed as direct traffic and its retries as the proxy,
        which changes identity mid-lineage.
        """

        # Diagnostic only: any failure to inspect settings must not break startup.
        try:
            from scrapy.utils.misc import load_object

            getter = getattr(settings, "get_component_priority_dict_with_base", None)
            if getter is None:
                getter = settings.getwithbase
            components = getter("DOWNLOADER_MIDDLEWARES")
            own_priority = proxy_priority = None
            for component, priority in components.items():
                if priority is None:
                    continue
                resolved = load_object(component) if isinstance(component, str) else component
                if not isinstance(resolved, type):
                    continue
                if issubclass(resolved, ScrapyUserAgentMiddleware):
                    own_priority = priority
                elif (
                    f"{resolved.__module__}.{resolved.__qualname__}"
                    == _HTTP_PROXY_MIDDLEWARE
                ):
                    proxy_priority = priority
        except Exception:  # noqa: BLE001
            return
        if (
            own_priority is not None
            and proxy_priority is not None
            and own_priority <= proxy_priority
        ):
            logger.warning(
                "UA_ROTATOR_SCOPE='proxy' but ScrapyUserAgentMiddleware (priority "
                "%s) runs before HttpProxyMiddleware (priority %s). Proxies taken "
                "from environment variables are not visible yet, so retries can "
                "switch identity. Give the middleware a priority above %s, for "
                "example 760.",
                own_priority,
                proxy_priority,
                proxy_priority,
            )

    @classmethod
    def _policy_for_scope(cls, scope: Any) -> RotationPolicy:
        validated = cls._validate_scope(scope)
        if validated == "request":
            return RotationPolicy.PER_REQUEST
        if validated == "spider":
            return RotationPolicy.PER_SESSION
        return RotationPolicy.PER_KEY

    def _inc(self, key: str) -> None:
        if self.stats is not None:
            self.stats.inc_value(key)
