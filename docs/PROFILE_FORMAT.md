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

At least one entry must have a positive weight. Duplicate User-Agent strings are rejected.

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

The machine-readable schema is available at:

```text
src/apex_ua_rotator/user_agent_profiles.schema.json
```
