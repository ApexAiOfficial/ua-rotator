"""Framework-agnostic weighted User-Agent rotation.

The core module contains no crawler-framework imports and no hard-coded browser
matrix. Profile data is supplied explicitly, loaded from a strict versioned JSON
document, or loaded from the bundled dated profile snapshot.

Python: 3.10+
Dependencies: standard library only
"""

from __future__ import annotations

import json
import math
import os
import random
import stat
import tempfile
import threading
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any, Hashable, Iterable, Mapping, Protocol, Sequence

SCHEMA_VERSION = 1
DEFAULT_PROFILE_NAME = "global_2026-07-31.json"
_ALLOWED_DOCUMENT_FIELDS = frozenset({"schema_version", "metadata", "entries"})
_ALLOWED_ENTRY_FIELDS = frozenset({"user_agent", "weight"})


class RotationPolicy(str, Enum):
    """Selection-cache policy used by :class:`UserAgentRotator`."""

    PER_REQUEST = "per_request"
    PER_SESSION = "per_session"
    PER_KEY = "per_key"


class RandomChoices(Protocol):
    """Minimal random-source interface required by the rotator."""

    def choices(
        self,
        population: Sequence[str],
        weights: Sequence[float] | None = None,
        *,
        cum_weights: Sequence[float] | None = None,
        k: int = 1,
    ) -> list[str]: ...


@dataclass(frozen=True)
class UserAgentEntry:
    """One validated User-Agent profile and its relative selection weight."""

    user_agent: str
    weight: float = 1.0


class UserAgentRotator:
    """Thread-safe weighted User-Agent selector.

    Exactly one data source must be supplied: ``entries`` or ``file_path``.

    ``per_request``
        Select on every call.

    ``per_session``
        Cache one selection for this rotator instance. ``force_new=True``
        replaces that cached value.

    ``per_key``
        Cache one selection per caller-supplied hashable key. Adapters may use
        hostnames, cookie jars, accounts, proxies, or another meaningful scope.
    """

    def __init__(
        self,
        entries: Iterable[UserAgentEntry | Mapping[str, Any] | Sequence[Any] | str] | None = None,
        *,
        file_path: str | os.PathLike[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        policy: RotationPolicy | str = RotationPolicy.PER_REQUEST,
        rng: RandomChoices | None = None,
        max_key_cache: int = 4096,
    ) -> None:
        if (entries is None) == (file_path is None):
            raise ValueError("Provide exactly one of entries or file_path.")
        if file_path is not None and metadata is not None:
            raise ValueError("metadata cannot be supplied when loading file_path.")

        self._policy = self._coerce_policy(policy)
        self._rng: RandomChoices = rng if rng is not None else random.Random()
        self._max_key_cache = self._validate_cache_size(max_key_cache)
        self._lock = threading.RLock()
        self._session_ua: str | None = None
        self._key_cache: OrderedDict[Hashable, str] = OrderedDict()

        if file_path is not None:
            parsed_entries, parsed_metadata = self._read_file(Path(file_path))
        else:
            assert entries is not None
            parsed_entries = self._validate_entries(entries)
            parsed_metadata = self._validate_metadata(metadata)

        self._entries = parsed_entries
        self._metadata = parsed_metadata
        self._rebuild_vectors()

    @property
    def policy(self) -> RotationPolicy:
        """Return the active selection policy."""

        return self._policy

    @property
    def entries(self) -> tuple[UserAgentEntry, ...]:
        """Return the immutable validated profile sequence."""

        with self._lock:
            return self._entries

    @property
    def metadata(self) -> dict[str, Any]:
        """Return a detached copy of document metadata."""

        with self._lock:
            return deepcopy(self._metadata)

    @property
    def max_key_cache(self) -> int:
        """Return the maximum number of sticky ``per_key`` identities."""

        return self._max_key_cache

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __repr__(self) -> str:
        return (
            f"UserAgentRotator(entries={len(self)}, "
            f"policy={self.policy.value!r}, max_key_cache={self.max_key_cache})"
        )

    def select(
        self,
        *,
        key: Hashable | None = None,
        force_new: bool = False,
    ) -> str:
        """Select a User-Agent according to the configured policy.

        ``key`` is required for ``per_key``. Under the other policies it is
        accepted and ignored so generic adapters may call one stable interface.
        """

        with self._lock:
            if self._policy is RotationPolicy.PER_REQUEST:
                return self._choose()

            if self._policy is RotationPolicy.PER_SESSION:
                if force_new or self._session_ua is None:
                    self._session_ua = self._choose()
                return self._session_ua

            if key is None:
                raise ValueError("key is required when policy='per_key'.")
            self._ensure_hashable(key)

            if force_new or key not in self._key_cache:
                self._key_cache[key] = self._choose()
                self._key_cache.move_to_end(key)
                while len(self._key_cache) > self._max_key_cache:
                    self._key_cache.popitem(last=False)
            else:
                self._key_cache.move_to_end(key)

            return self._key_cache[key]

    def reset(self) -> None:
        """Clear all sticky selections without changing profile data."""

        with self._lock:
            self._clear_caches()

    def reset_key(self, key: Hashable) -> bool:
        """Remove one ``per_key`` cache entry; return whether it existed."""

        self._ensure_hashable(key)
        with self._lock:
            return self._key_cache.pop(key, None) is not None

    def replace(
        self,
        entries: Iterable[UserAgentEntry | Mapping[str, Any] | Sequence[Any] | str],
    ) -> None:
        """Atomically replace all profile entries after full validation.

        Existing metadata is preserved.
        """

        candidate = self._validate_entries(entries)
        with self._lock:
            self._entries = candidate
            self._rebuild_vectors()
            self._clear_caches()

    def replace_document(
        self,
        entries: Iterable[UserAgentEntry | Mapping[str, Any] | Sequence[Any] | str],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Atomically replace entries and metadata after full validation."""

        candidate_entries = self._validate_entries(entries)
        candidate_metadata = self._validate_metadata(metadata)
        with self._lock:
            self._entries = candidate_entries
            self._metadata = candidate_metadata
            self._rebuild_vectors()
            self._clear_caches()

    def set_metadata(self, metadata: Mapping[str, Any] | None) -> None:
        """Replace metadata without changing profiles or sticky selections."""

        candidate = self._validate_metadata(metadata)
        with self._lock:
            self._metadata = candidate

    def add(self, user_agent: str, weight: float = 1.0) -> None:
        """Add one unique profile, preserving raw relative weights."""

        entry = UserAgentEntry(
            self._validate_user_agent(user_agent), self._validate_weight(weight)
        )
        with self._lock:
            self.replace((*self._entries, entry))

    def remove(self, user_agent: str) -> None:
        """Remove one profile by exact validated value."""

        target = self._validate_user_agent(user_agent)
        with self._lock:
            candidate = tuple(e for e in self._entries if e.user_agent != target)
            if len(candidate) == len(self._entries):
                raise KeyError(target)
            self.replace(candidate)

    def to_document(self) -> dict[str, Any]:
        """Return the complete versioned JSON-serializable document."""

        with self._lock:
            return {
                "schema_version": SCHEMA_VERSION,
                "metadata": deepcopy(self._metadata),
                "entries": [
                    {"user_agent": entry.user_agent, "weight": entry.weight}
                    for entry in self._entries
                ],
            }

    def save(
        self,
        file_path: str | os.PathLike[str],
        *,
        overwrite: bool = False,
    ) -> None:
        """Persist the complete document using same-filesystem publication.

        Existing files are protected unless ``overwrite=True``. The temporary
        file is flushed and ``fsync``-ed before publication. On POSIX, the parent
        directory is also ``fsync``-ed on a best-effort basis.
        """

        destination = Path(file_path)
        parent = destination.parent
        if not parent.exists():
            raise FileNotFoundError(f"Parent directory does not exist: {parent}")
        if not parent.is_dir():
            raise NotADirectoryError(parent)
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)

        document = self.to_document()
        existing_mode: int | None = None
        if destination.exists():
            existing_mode = stat.S_IMODE(destination.stat().st_mode)

        file_descriptor, raw_temp = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=parent
        )
        temp_path = Path(raw_temp)
        try:
            if existing_mode is not None:
                os.chmod(temp_path, existing_mode)
            with os.fdopen(
                file_descriptor, "w", encoding="utf-8", newline="\n"
            ) as handle:
                json.dump(document, handle, indent=2, ensure_ascii=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            if overwrite:
                os.replace(temp_path, destination)
            else:
                # Hard-link publication is atomic and no-clobber when supported.
                os.link(temp_path, destination)
                temp_path.unlink()
            self._fsync_directory(parent)
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise

    @classmethod
    def from_file(
        cls,
        file_path: str | os.PathLike[str],
        *,
        policy: RotationPolicy | str = RotationPolicy.PER_REQUEST,
        rng: RandomChoices | None = None,
        max_key_cache: int = 4096,
    ) -> "UserAgentRotator":
        """Construct a rotator from a strict versioned profile document."""

        return cls(
            file_path=file_path,
            policy=policy,
            rng=rng,
            max_key_cache=max_key_cache,
        )

    @classmethod
    def from_bundled_profile(
        cls,
        name: str = DEFAULT_PROFILE_NAME,
        *,
        policy: RotationPolicy | str = RotationPolicy.PER_REQUEST,
        rng: RandomChoices | None = None,
        max_key_cache: int = 4096,
    ) -> "UserAgentRotator":
        """Construct from a JSON profile bundled with the installed package."""

        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError("Bundled profile name must be a plain file name.")
        resource = resources.files("apex_ua_rotator.profiles").joinpath(name)
        if not resource.is_file():
            raise FileNotFoundError(f"Bundled profile does not exist: {name}")
        with resource.open("r", encoding="utf-8") as handle:
            document = cls._load_json(handle, source=f"bundled profile {name!r}")
        entries, metadata = cls._parse_document(document)
        return cls(
            entries,
            metadata=metadata,
            policy=policy,
            rng=rng,
            max_key_cache=max_key_cache,
        )

    def _choose(self) -> str:
        return self._rng.choices(self._user_agents, weights=self._weights, k=1)[0]

    def _rebuild_vectors(self) -> None:
        self._user_agents = tuple(entry.user_agent for entry in self._entries)
        self._weights = tuple(entry.weight for entry in self._entries)

    def _clear_caches(self) -> None:
        self._session_ua = None
        self._key_cache.clear()

    @staticmethod
    def _coerce_policy(value: RotationPolicy | str) -> RotationPolicy:
        try:
            return value if isinstance(value, RotationPolicy) else RotationPolicy(value)
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(policy.value for policy in RotationPolicy)
            raise ValueError(
                f"Invalid policy {value!r}; expected one of: {allowed}."
            ) from exc

    @staticmethod
    def _validate_cache_size(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("max_key_cache must be a positive integer.")
        return value

    @classmethod
    def _validate_entries(
        cls,
        values: Iterable[UserAgentEntry | Mapping[str, Any] | Sequence[Any] | str],
    ) -> tuple[UserAgentEntry, ...]:
        if isinstance(values, (str, bytes, bytearray, Mapping, UserAgentEntry)):
            raise ValueError(
                "entries must be an iterable of profiles; wrap a single profile in a list."
            )

        entries: list[UserAgentEntry] = []
        seen: set[str] = set()
        try:
            iterator = iter(values)
        except TypeError as exc:
            raise ValueError("entries must be an iterable of profiles.") from exc

        for index, value in enumerate(iterator):
            user_agent, weight = cls._parse_entry(value, index=index)
            if user_agent in seen:
                raise ValueError(f"Duplicate User-Agent at index {index}: {user_agent!r}")
            seen.add(user_agent)
            entries.append(UserAgentEntry(user_agent, weight))

        if not entries:
            raise ValueError("At least one User-Agent entry is required.")
        if not any(entry.weight > 0 for entry in entries):
            raise ValueError("At least one User-Agent weight must be positive.")
        return tuple(entries)

    @classmethod
    def _parse_entry(cls, value: Any, *, index: int) -> tuple[str, float]:
        if isinstance(value, UserAgentEntry):
            return (
                cls._validate_user_agent(value.user_agent),
                cls._validate_weight(value.weight),
            )
        if isinstance(value, Mapping):
            unknown = set(value) - _ALLOWED_ENTRY_FIELDS
            if unknown or "user_agent" not in value:
                raise ValueError(f"Invalid entry at index {index}: {value!r}")
            return (
                cls._validate_user_agent(value["user_agent"]),
                cls._validate_weight(value.get("weight", 1.0)),
            )
        if (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
            and len(value) == 2
        ):
            return (
                cls._validate_user_agent(value[0]),
                cls._validate_weight(value[1]),
            )
        if isinstance(value, str):
            return cls._validate_user_agent(value), 1.0
        raise ValueError(f"Invalid entry at index {index}: {value!r}")

    @staticmethod
    def _validate_user_agent(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("User-Agent must be a string.")
        if value != value.strip():
            raise ValueError("User-Agent cannot have leading or trailing whitespace.")
        if not value:
            raise ValueError("User-Agent cannot be empty.")
        if not value.isascii():
            raise ValueError("User-Agent must contain ASCII characters only.")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise ValueError("User-Agent cannot contain ASCII control characters.")
        return value

    @staticmethod
    def _validate_weight(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("Weight cannot be boolean.")
        try:
            weight = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid weight: {value!r}") from exc
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("Weight must be finite and non-negative.")
        return weight

    @staticmethod
    def _validate_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a JSON object.")
        if not all(isinstance(key, str) for key in value):
            raise ValueError("metadata keys must be strings.")
        try:
            serialized = json.dumps(value, ensure_ascii=True, allow_nan=False)
            copied = json.loads(serialized)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain only finite JSON values.") from exc
        assert isinstance(copied, dict)
        return copied

    @staticmethod
    def _ensure_hashable(value: Hashable) -> None:
        try:
            hash(value)
        except TypeError as exc:
            raise ValueError("per_key selection key must be hashable.") from exc

    @classmethod
    def _read_file(cls, path: Path) -> tuple[tuple[UserAgentEntry, ...], dict[str, Any]]:
        with path.open("r", encoding="utf-8") as handle:
            document = cls._load_json(handle, source=str(path))
        return cls._parse_document(document)

    @staticmethod
    def _load_json(handle: Any, *, source: str) -> Any:
        try:
            return json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {source}: {exc}") from exc

    @classmethod
    def _parse_document(
        cls, document: Any
    ) -> tuple[tuple[UserAgentEntry, ...], dict[str, Any]]:
        if not isinstance(document, Mapping):
            raise ValueError("Profile file must contain a JSON object.")
        unknown = set(document) - _ALLOWED_DOCUMENT_FIELDS
        if unknown:
            raise ValueError(
                f"Profile file contains unsupported fields: {sorted(unknown)!r}."
            )
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version {document.get('schema_version')!r}; "
                f"expected {SCHEMA_VERSION}."
            )

        if "metadata" not in document:
            raise ValueError("Profile file is missing required field 'metadata'.")
        if "entries" not in document:
            raise ValueError("Profile file is missing required field 'entries'.")

        raw_entries = document["entries"]
        if not isinstance(raw_entries, list):
            raise ValueError("Profile file field 'entries' must be a JSON array.")

        parsed: list[tuple[Any, Any]] = []
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, Mapping):
                raise ValueError(f"Entry {index} must be a JSON object.")
            unknown_entry = set(raw) - _ALLOWED_ENTRY_FIELDS
            if unknown_entry:
                raise ValueError(
                    f"Entry {index} contains unsupported fields: "
                    f"{sorted(unknown_entry)!r}."
                )
            missing_entry = _ALLOWED_ENTRY_FIELDS - set(raw)
            if missing_entry:
                raise ValueError(
                    f"Entry {index} is missing required fields: "
                    f"{sorted(missing_entry)!r}."
                )
            parsed.append((raw["user_agent"], raw["weight"]))

        return (
            cls._validate_entries(parsed),
            cls._validate_metadata(document.get("metadata")),
        )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
            return
        flags = os.O_RDONLY | os.O_DIRECTORY
        try:
            directory_fd = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        finally:
            os.close(directory_fd)
