from __future__ import annotations

import errno
import json
import math
import os
import random
import threading
from pathlib import Path

import pytest

from apex_ua_rotator import (
    DEFAULT_PROFILE_NAME,
    RotationPolicy,
    UserAgentEntry,
    UserAgentRotator,
)


class SequenceRng:
    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls = 0

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        assert k == 1
        assert cum_weights is None
        assert len(population) == len(weights)
        value = self.outputs[self.calls % len(self.outputs)]
        self.calls += 1
        return [value]


def document(entries=None, metadata=None, **extra):
    value = {
        "schema_version": 1,
        "metadata": {} if metadata is None else metadata,
        "entries": [{"user_agent": "UA-A", "weight": 1}] if entries is None else entries,
    }
    value.update(extra)
    return value


def write_document(tmp_path: Path, value: object, name: str = "profiles.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_constructor_requires_exactly_one_source(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        UserAgentRotator()
    with pytest.raises(ValueError, match="exactly one"):
        UserAgentRotator(["UA"], file_path=tmp_path / "x.json")
    with pytest.raises(ValueError, match="metadata cannot"):
        UserAgentRotator(file_path=tmp_path / "x.json", metadata={})


def test_constructor_rejects_ambiguous_top_level_single_values():
    for value in ("ABC", b"ABC", bytearray(b"ABC"), {"user_agent": "UA", "weight": 1}, UserAgentEntry("UA")):
        with pytest.raises(ValueError, match="wrap a single profile"):
            UserAgentRotator(value)  # type: ignore[arg-type]


def test_supported_entry_forms_and_raw_weights():
    rotator = UserAgentRotator(
        [
            "UA-A",
            ("UA-B", 2),
            ["UA-C", "3.5"],
            {"user_agent": "UA-D", "weight": 0},
            UserAgentEntry("UA-E", 4),
        ]
    )
    assert rotator.entries == (
        UserAgentEntry("UA-A", 1.0),
        UserAgentEntry("UA-B", 2.0),
        UserAgentEntry("UA-C", 3.5),
        UserAgentEntry("UA-D", 0.0),
        UserAgentEntry("UA-E", 4.0),
    )
    assert len(rotator) == 5
    assert "entries=5" in repr(rotator)


def test_invalid_entries_fail_closed():
    invalid = [
        [],
        [123],
        [["UA"]],
        [{"weight": 1}],
        [{"user_agent": "UA", "weight": 1, "extra": True}],
        ["UA", "UA"],
        [("UA", 0), ("UA-B", 0)],
    ]
    for entries in invalid:
        with pytest.raises(ValueError):
            UserAgentRotator(entries)


def test_user_agent_validation():
    for invalid in ("", " UA", "UA ", "Café", "bad\nua", "bad\tua", "bad\x7fua"):
        with pytest.raises(ValueError):
            UserAgentRotator([invalid])
    with pytest.raises(ValueError, match="string"):
        UserAgentRotator([(None, 1)])


def test_weight_validation():
    for invalid in (True, False, -1, math.inf, -math.inf, math.nan, object()):
        with pytest.raises(ValueError):
            UserAgentRotator([("UA", invalid)])
    assert UserAgentRotator([("UA", "2.5")]).entries[0].weight == 2.5


def test_policy_and_cache_size_validation():
    with pytest.raises(ValueError, match="Invalid policy"):
        UserAgentRotator(["UA"], policy="wrong")
    for invalid in (True, 0, -1, 1.5, "3"):
        with pytest.raises(ValueError, match="positive integer"):
            UserAgentRotator(["UA"], max_key_cache=invalid)  # type: ignore[arg-type]


def test_per_request_per_session_and_force_new():
    rng = SequenceRng(["UA-A", "UA-B", "UA-A"])
    request_rotator = UserAgentRotator(["UA-A", "UA-B"], rng=rng)
    assert request_rotator.select() == "UA-A"
    assert request_rotator.select() == "UA-B"

    rng = SequenceRng(["UA-A", "UA-B"])
    session = UserAgentRotator(["UA-A", "UA-B"], policy="per_session", rng=rng)
    assert session.policy is RotationPolicy.PER_SESSION
    assert session.select() == session.select() == "UA-A"
    assert session.select(force_new=True) == "UA-B"
    session.reset()
    assert session.select() == "UA-A"


def test_per_key_requires_hashable_key_and_uses_bounded_lru():
    rng = SequenceRng(["UA-A", "UA-B", "UA-C", "UA-A"])
    rotator = UserAgentRotator(
        ["UA-A", "UA-B", "UA-C"],
        policy="per_key",
        max_key_cache=2,
        rng=rng,
    )
    with pytest.raises(ValueError, match="key is required"):
        rotator.select()
    with pytest.raises(ValueError, match="hashable"):
        rotator.select(key=[])

    assert rotator.select(key="one") == "UA-A"
    assert rotator.select(key="two") == "UA-B"
    assert rotator.select(key="one") == "UA-A"  # touch one
    assert rotator.select(key="three") == "UA-C"  # evicts two
    assert rotator.reset_key("missing") is False
    assert rotator.reset_key("one") is True
    assert rotator.select(key="two") == "UA-A"  # was evicted


def test_replace_add_remove_and_metadata_behavior():
    rotator = UserAgentRotator(["UA-A"], metadata={"source": "x"})
    rotator.add("UA-B", 2)
    assert [entry.user_agent for entry in rotator.entries] == ["UA-A", "UA-B"]
    with pytest.raises(ValueError, match="Duplicate"):
        rotator.add("UA-B")

    rotator.remove("UA-A")
    assert rotator.entries == (UserAgentEntry("UA-B", 2.0),)
    with pytest.raises(KeyError):
        rotator.remove("missing")
    with pytest.raises(ValueError, match="At least one"):
        rotator.remove("UA-B")

    rotator.replace([("UA-C", 3)])
    assert rotator.metadata == {"source": "x"}
    rotator.replace_document([("UA-D", 4)], metadata={"revision": 2})
    assert rotator.entries == (UserAgentEntry("UA-D", 4.0),)
    assert rotator.metadata == {"revision": 2}
    rotator.set_metadata(None)
    assert rotator.metadata == {}


def test_metadata_is_json_safe_detached_and_preserved(tmp_path):
    metadata = {"nested": {"items": [1, 2]}, "flag": True}
    rotator = UserAgentRotator(["UA"], metadata=metadata)
    metadata["nested"]["items"].append(3)
    assert rotator.metadata["nested"]["items"] == [1, 2]

    detached = rotator.metadata
    detached["nested"]["items"].append(9)
    assert rotator.metadata["nested"]["items"] == [1, 2]

    path = tmp_path / "profiles.json"
    rotator.save(path)
    loaded = UserAgentRotator.from_file(path)
    assert loaded.metadata == rotator.metadata
    assert loaded.to_document() == rotator.to_document()

    for invalid in ([1, 2], {1: "bad"}, {"bad": math.inf}, {"bad": object()}):
        with pytest.raises(ValueError, match="metadata"):
            UserAgentRotator(["UA"], metadata=invalid)  # type: ignore[arg-type]


def test_file_document_validation(tmp_path):
    invalid_documents = [
        [],
        {"schema_version": 2, "metadata": {}, "entries": []},
        document(extra=True),
        {"schema_version": 1, "entries": [{"user_agent": "UA", "weight": 1}]},
        {"schema_version": 1, "metadata": {}},
        {"schema_version": 1, "metadata": {}, "entries": "not-list"},
        document(entries=["UA"]),
        document(entries=[{"weight": 1}]),
        document(entries=[{"user_agent": "UA"}]),
        document(entries=[{"user_agent": "UA", "weight": 1, "x": 2}]),
    ]
    for index, invalid in enumerate(invalid_documents):
        with pytest.raises(ValueError):
            UserAgentRotator.from_file(write_document(tmp_path, invalid, f"bad-{index}.json"))

    bad_json = tmp_path / "bad-json.json"
    bad_json.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        UserAgentRotator.from_file(bad_json)


def test_save_no_clobber_overwrite_permissions_and_missing_parent(tmp_path):
    rotator = UserAgentRotator(["UA"], metadata={"x": 1})
    path = tmp_path / "profiles.json"
    rotator.save(path)
    original = path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        rotator.save(path)
    assert path.read_text(encoding="utf-8") == original

    if os.name == "posix":
        path.chmod(0o640)
    rotator.replace(["UA-B"])
    rotator.save(path, overwrite=True)
    assert UserAgentRotator.from_file(path).entries[0].user_agent == "UA-B"
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o640

    with pytest.raises(FileNotFoundError):
        rotator.save(tmp_path / "missing" / "x.json")
    not_dir = tmp_path / "not-dir"
    not_dir.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        rotator.save(not_dir / "x.json")


def test_bundled_profile_and_name_validation():
    rotator = UserAgentRotator.from_bundled_profile()
    assert len(rotator) == 85
    assert rotator.metadata["profile_revision"] == "2026-11-14.1"
    assert math.isclose(sum(entry.weight for entry in rotator.entries), 100.0)
    assert DEFAULT_PROFILE_NAME == "global_2026-11-14.json"

    for invalid in ("", "../x.json", "path/x.json", 123):
        with pytest.raises(ValueError):
            UserAgentRotator.from_bundled_profile(invalid)  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        UserAgentRotator.from_bundled_profile("missing.json")


def test_concurrent_select_and_mutation_do_not_corrupt_state():
    rotator = UserAgentRotator([("UA-A", 1), ("UA-B", 1)], policy="per_key")
    errors: list[BaseException] = []

    def select_worker(worker: int):
        try:
            for index in range(200):
                assert rotator.select(key=(worker, index % 20)) in {"UA-A", "UA-B"}
        except BaseException as exc:  # pragma: no cover - failure capture
            errors.append(exc)

    threads = [threading.Thread(target=select_worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(rotator) == 2


def test_previous_profile_revision_stays_bundled_for_reproducibility():
    original = UserAgentRotator.from_bundled_profile("global_2026-07-31.json")
    revised = UserAgentRotator.from_bundled_profile("global_2026-07-31-r2.json")
    assert original.metadata["profile_revision"] == "2026-07-31.1"
    assert revised.metadata["supersedes"] == "global_2026-07-31.json"
    changed = {e.user_agent for e in original.entries} ^ {e.user_agent for e in revised.entries}
    assert changed == {
        "Mozilla/5.0 (X11; CrOS x86_64 16700.60.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    }
    assert [e.weight for e in original.entries] == [e.weight for e in revised.entries]


class WrappedRandom:
    """A non-random.Random RNG: receives raw weights, like pre-1.3 releases."""

    def __init__(self, seed):
        self.inner = random.Random(seed)
        self.calls = []

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        self.calls.append((weights is not None, cum_weights is not None))
        return self.inner.choices(population, weights=weights, cum_weights=cum_weights, k=k)


def test_cumulative_fast_path_matches_raw_weight_path_for_same_seed():
    fast = UserAgentRotator.from_bundled_profile(rng=random.Random(42))
    wrapped_rng = WrappedRandom(42)
    compatible = UserAgentRotator.from_bundled_profile(rng=wrapped_rng)
    assert [fast.select() for _ in range(500)] == [compatible.select() for _ in range(500)]
    assert set(wrapped_rng.calls) == {(True, False)}


def test_random_subclass_overriding_choices_keeps_raw_weights():
    class Custom(random.Random):
        seen = []

        def choices(self, population, weights=None, *, cum_weights=None, k=1):
            type(self).seen.append(cum_weights)
            return super().choices(population, weights=weights, cum_weights=cum_weights, k=k)

    UserAgentRotator(["UA-A", "UA-B"], rng=Custom(1)).select()
    assert Custom.seen == [None]


def test_refresh_per_request_never_repeats_previous_when_alternative_exists():
    rotator = UserAgentRotator([("UA-A", 1000), ("UA-B", 1)], rng=random.Random(3))
    assert all(rotator.refresh(previous="UA-A") == "UA-B" for _ in range(200))
    # Unknown or zero-weight "previous" values do not restrict selection.
    assert rotator.refresh(previous="not-in-profile") in {"UA-A", "UA-B"}


def test_refresh_falls_back_when_previous_is_the_only_positive_entry():
    rotator = UserAgentRotator([("UA-A", 1), ("UA-B", 0)], rng=random.Random(3))
    assert rotator.refresh(previous="UA-A") == "UA-A"


def test_refresh_per_session_replaces_and_converges():
    rotator = UserAgentRotator(["UA-A", "UA-B", "UA-C"], policy="per_session", rng=random.Random(5))
    first = rotator.select()
    replacement = rotator.refresh(previous=first)
    assert replacement != first
    assert rotator.select() == replacement
    # A second caller reporting the same stale identity adopts the replacement.
    assert rotator.refresh(previous=first) == replacement
    # Without "previous", the current sticky value is the one avoided.
    third = rotator.refresh()
    assert third != replacement
    assert rotator.select() == third


def test_refresh_per_session_before_any_selection():
    rotator = UserAgentRotator(["UA-A", "UA-B"], policy="per_session", rng=random.Random(5))
    chosen = rotator.refresh(previous="UA-A")
    assert chosen == "UA-B"
    assert rotator.select() == "UA-B"


def test_refresh_per_key_replaces_converges_and_respects_lru():
    rotator = UserAgentRotator(
        ["UA-A", "UA-B", "UA-C"], policy="per_key", max_key_cache=2, rng=random.Random(9)
    )
    with pytest.raises(ValueError, match="key is required"):
        rotator.refresh()
    with pytest.raises(ValueError, match="hashable"):
        rotator.refresh(key=[])
    with pytest.raises(ValueError, match="previous"):
        rotator.refresh(key="k", previous=1)  # type: ignore[arg-type]

    first = rotator.select(key="example.com")
    replacement = rotator.refresh(key="example.com", previous=first)
    assert replacement != first
    assert rotator.select(key="example.com") == replacement
    assert rotator.refresh(key="example.com", previous=first) == replacement
    assert rotator.refresh(key="example.com") != replacement

    rotator.select(key="two")
    rotator.refresh(key="three", previous="UA-A")
    assert len(rotator._key_cache) == 2
    assert "example.com" not in rotator._key_cache


def test_concurrent_refreshes_in_one_scope_converge_on_one_replacement():
    rotator = UserAgentRotator(
        [f"UA-{index}" for index in range(10)], policy="per_key", rng=random.Random(11)
    )
    failed = rotator.select(key="example.com")
    results: list[str] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(rotator.refresh(key="example.com", previous=failed))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1
    assert results[0] != failed


def test_weight_sum_overflow_and_huge_integers_are_rejected_at_load():
    with pytest.raises(ValueError, match="sum"):
        UserAgentRotator([("UA-A", 1e308), ("UA-B", 1e308)])
    with pytest.raises(ValueError, match="Invalid weight"):
        UserAgentRotator([("UA-A", 10**400)])


def test_strict_json_rejects_duplicate_keys_constants_and_boolean_version(tmp_path):
    duplicate = tmp_path / "dup.json"
    duplicate.write_text(
        '{"schema_version": 1, "metadata": {}, "entries": '
        '[{"user_agent": "UA-A", "user_agent": "UA-B", "weight": 1}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate object key 'user_agent'"):
        UserAgentRotator.from_file(duplicate)

    constant = tmp_path / "nan.json"
    constant.write_text(
        '{"schema_version": 1, "metadata": {}, "entries": [{"user_agent": "UA", "weight": NaN}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-finite number NaN"):
        UserAgentRotator.from_file(constant)

    with pytest.raises(ValueError, match="schema_version True"):
        UserAgentRotator.from_file(write_document(tmp_path, document(schema_version=True), "bool.json"))


def test_save_falls_back_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    def no_links(*args, **kwargs):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    from apex_ua_rotator import core

    monkeypatch.setattr(core, "_IS_WINDOWS", False)  # exercise the reservation path
    monkeypatch.setattr(os, "link", no_links)
    rotator = UserAgentRotator(["UA-A"], metadata={"x": 1})
    path = tmp_path / "profiles.json"
    rotator.save(path)
    assert UserAgentRotator.from_file(path).to_document() == rotator.to_document()
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        rotator.save(path)
    assert path.read_bytes() == original
    assert [p.name for p in tmp_path.iterdir()] == ["profiles.json"]


def test_save_falls_back_when_link_is_missing_and_cleans_up_on_failure(tmp_path, monkeypatch):
    from apex_ua_rotator import core

    monkeypatch.setattr(core, "_IS_WINDOWS", False)  # exercise the reservation path
    monkeypatch.delattr(os, "link")
    rotator = UserAgentRotator(["UA-A"])
    rotator.save(tmp_path / "a.json")
    assert UserAgentRotator.from_file(tmp_path / "a.json").entries[0].user_agent == "UA-A"

    def failing_replace(src, dst):
        raise OSError(errno.EIO, "simulated I/O error")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="simulated"):
        rotator.save(tmp_path / "b.json")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.json"]


def test_windows_fallback_uses_atomic_no_clobber_rename(tmp_path, monkeypatch):
    def no_links(*args, **kwargs):
        raise OSError(errno.EINVAL, "Incorrect function")

    renamed = []
    real_rename = os.rename

    def tracking_rename(src, dst):
        renamed.append((Path(src).name, Path(dst).name))
        if Path(dst).exists():
            raise FileExistsError(errno.EEXIST, "exists", str(dst))
        real_rename(src, dst)

    monkeypatch.setattr(os, "link", no_links)
    from apex_ua_rotator import core

    monkeypatch.setattr(core, "_IS_WINDOWS", True)
    monkeypatch.setattr(os, "rename", tracking_rename)
    rotator = UserAgentRotator(["UA-A"])
    path = tmp_path / "profiles.json"
    rotator.save(path)
    assert renamed and renamed[0][1] == "profiles.json"
    with pytest.raises(FileExistsError):
        rotator.save(path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["profiles.json"]
