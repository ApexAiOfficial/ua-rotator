# Profile document format

Apex UA Rotator profile documents are strict JSON objects with schema version `1`.

```json
{
  "schema_version": 1,
  "metadata": {},
  "entries": [
    {
      "user_agent": "ExampleClient/1.0",
      "weight": 1.0
    }
  ]
}
```

## Fields

### `schema_version`

Must equal `1`.

### `metadata`

A JSON object reserved for provenance, dates, methodology, ownership, and limitations. Metadata is not used during selection, but it is validated and preserved when a profile is loaded and saved.

### `entries`

A non-empty array. Every entry contains exactly:

- `user_agent`: a non-empty printable ASCII string with no leading or trailing whitespace;
- `weight`: a finite, non-negative number.

At least one entry must have a positive weight, and the sum of all weights must be finite. Duplicate User-Agent strings are rejected.

## Strict parsing

Files are parsed more strictly than the standard library's defaults:

- duplicate keys in any JSON object are rejected (the standard library silently keeps the last one);
- `NaN`, `Infinity`, and `-Infinity` literals are rejected (they are not JSON);
- `schema_version` must be the number `1`, not `true`.

## Relative weights

Weights are relative, not percentages. These sets are equivalent:

```text
1, 3, 6
10, 30, 60
0.1, 0.3, 0.6
```

A total of 100 is optional and is mainly useful for human-readable market models.

## Validation

```bash
apex-ua-rotator validate profiles.json
```

Or in Python:

```python
from apex_ua_rotator import UserAgentRotator

rotator = UserAgentRotator.from_file("profiles.json")
print(rotator.to_document())
```

The machine-readable JSON Schema (draft 2020-12) is packaged at:

```text
src/apex_ua_rotator/user_agent_profiles.schema.json
```

An identical copy is kept at the repository root as `user_agent_profiles.schema.json`. Its `$id` is:

```text
https://raw.githubusercontent.com/ApexAiOfficial/ua-rotator/main/user_agent_profiles.schema.json
```

The schema describes document structure. The loader additionally rejects duplicate User-Agent strings, whitespace-padded values, non-finite weights, documents with no positive weight, and the strict-parsing cases above.

## Bundled profiles

```bash
apex-ua-rotator profiles
```

| File | Revision | Notes |
|---|---|---|
| `global_2026-11-14.json` | `2026-11-14.1` | Default since 1.4.0. StatCounter October 2026 market model with November 2026 releases and the frozen iOS `18_7` token. |
| `global_2026-07-31-r2.json` | `2026-07-31.2` | Default in 1.3.0. Corrects the ChromeOS platform token. |
| `global_2026-07-31.json` | `2026-07-31.1` | Default through 1.2.1. Kept unchanged for reproducibility. |

Bundled snapshots are never edited in place. A correction ships as a numbered revision whose metadata names the file it `supersedes`, and a new market model ships as a new dated file. Pin a snapshot with `UserAgentRotator.from_bundled_profile("global_2026-07-31.json")` or `UA_ROTATOR_BUNDLED_PROFILE`.
