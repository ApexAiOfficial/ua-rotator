"""Optional Scrapy downloader-middleware adapter.

Scrapy-specific settings, request metadata, retry/redirect counters, and scope
mapping live here rather than in the reusable selection engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Hashable, Iterable, Mapping
from urllib.parse import urlsplit

from .core import DEFAULT_PROFILE_NAME, RotationPolicy, UserAgentRotator

VALID_SCOPES = frozenset({"request", "spider", "domain", "cookiejar", "proxy"})
VALID_REFRESH_EVENTS = frozenset({"retry", "redirect"})
META_KEY = "_apex_ua_rotator"


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


class ScrapyUserAgentMiddleware:
    """Assign and preserve User-Agent identity across request lineages.

    The adapter records the selected identity in ``Request.meta``. Scrapy retry
    and redirect requests copy metadata, allowing the adapter to preserve the
    same identity unless an explicit refresh rule applies.
    """

    def __init__(
        self,
        rotator: UserAgentRotator,
        *,
        scope: str = "request",
        refresh_on: Iterable[str] = (),
        overwrite_existing: bool = False,
        stats: Any = None,
    ) -> None:
        if not isinstance(overwrite_existing, bool):
            raise ValueError("overwrite_existing must be a boolean.")
        self.scope = self._validate_scope(scope)
        self.refresh_on = self._validate_refresh_events(refresh_on)
        self.overwrite_existing = overwrite_existing
        self.stats = stats
        self.rotator = rotator
        self._validate_policy_mapping()

    @classmethod
    def from_crawler(cls, crawler: Any) -> "ScrapyUserAgentMiddleware":
        """Build middleware from Scrapy settings or raise ``NotConfigured``."""

        settings = crawler.settings
        file_path = settings.get("UA_ROTATOR_FILE")
        entries = settings.get("UA_ROTATOR_ENTRIES")
        bundled_profile = settings.get("UA_ROTATOR_BUNDLED_PROFILE")

        has_file = file_path not in (None, "")
        has_entries = entries is not None
        has_bundled = bundled_profile not in (None, "", False)
        configured_sources = sum((has_file, has_entries, has_bundled))
        if configured_sources > 1:
            try:
                from scrapy.exceptions import NotConfigured
            except ImportError as exc:  # pragma: no cover - only outside Scrapy
                raise RuntimeError(
                    "Configure at most one of UA_ROTATOR_FILE, "
                    "UA_ROTATOR_ENTRIES, or UA_ROTATOR_BUNDLED_PROFILE."
                ) from exc
            raise NotConfigured(
                "Configure at most one of UA_ROTATOR_FILE, "
                "UA_ROTATOR_ENTRIES, or UA_ROTATOR_BUNDLED_PROFILE."
            )

        scope = settings.get("UA_ROTATOR_SCOPE", "request")
        policy = cls._policy_for_scope(scope)
        max_key_cache = settings.getint("UA_ROTATOR_MAX_KEY_CACHE", 4096)

        if has_file:
            rotator = UserAgentRotator.from_file(
                file_path,
                policy=policy,
                max_key_cache=max_key_cache,
            )
        elif has_entries:
            rotator = UserAgentRotator(
                entries,
                policy=policy,
                max_key_cache=max_key_cache,
            )
        else:
            profile_name = bundled_profile or DEFAULT_PROFILE_NAME
            if not isinstance(profile_name, str):
                raise ValueError("UA_ROTATOR_BUNDLED_PROFILE must be a file name.")
            rotator = UserAgentRotator.from_bundled_profile(
                profile_name,
                policy=policy,
                max_key_cache=max_key_cache,
            )
        refresh_on = cls._parse_refresh_setting(
            settings.get("UA_ROTATOR_REFRESH_ON", [])
        )
        overwrite = settings.getbool("UA_ROTATOR_OVERWRITE_EXISTING", False)
        return cls(
            rotator,
            scope=scope,
            refresh_on=refresh_on,
            overwrite_existing=overwrite,
            stats=getattr(crawler, "stats", None),
        )

    def process_request(self, request: Any, spider: Any = None) -> None:
        """Set or preserve the request User-Agent and lineage metadata."""

        prior = _Identity.from_meta(request.meta.get(META_KEY))
        header_user_agent = self._header_user_agent(request)
        if header_user_agent is not None and not self.overwrite_existing:
            # A header matching our lineage record was copied by Scrapy and is
            # not an external override. A mismatch is treated as explicit input.
            if prior is None or header_user_agent != prior.user_agent:
                request.meta.pop(META_KEY, None)
                self._inc("ua_rotator/skipped_existing_header")
                return None

        retry_times = self._counter(request.meta.get("retry_times", 0), "retry_times")
        redirect_times = self._counter(
            request.meta.get("redirect_times", 0), "redirect_times"
        )
        scope_key = self._scope_key(request)

        key_changed = prior is not None and prior.scope_key != scope_key
        retry_advanced = prior is not None and retry_times > prior.retry_times
        redirect_advanced = prior is not None and redirect_times > prior.redirect_times
        should_refresh = (
            ("retry" in self.refresh_on and retry_advanced)
            or ("redirect" in self.refresh_on and redirect_advanced)
        )

        if prior is None or key_changed or should_refresh:
            user_agent = self.rotator.select(
                key=scope_key if self.rotator.policy is RotationPolicy.PER_KEY else None,
                force_new=should_refresh,
            )
            self._inc("ua_rotator/selected")
            if key_changed:
                self._inc("ua_rotator/scope_changed")
            if should_refresh:
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
            if not hostname:
                raise ValueError(f"Request URL has no hostname: {request.url!r}")
            return ("domain", hostname.casefold())
        if self.scope == "cookiejar":
            return (
                "cookiejar",
                self._hashable_meta(request.meta.get("cookiejar", 0)),
            )
        if self.scope == "proxy":
            proxy = request.meta.get("proxy")
            return (
                "proxy",
                self._hashable_meta(proxy if proxy is not None else "<direct>"),
            )
        raise AssertionError(f"Unhandled scope: {self.scope}")

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
        return value

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
    def _parse_refresh_setting(value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set, frozenset)):
            return list(value)
        raise ValueError("UA_ROTATOR_REFRESH_ON must be a string or sequence.")

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
