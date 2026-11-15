# Security policy

## Supported versions

Security fixes are made on the latest release line. Upgrade to the newest release before reporting, if you can.

| Version | Supported |
|---|---|
| 1.4.x | Yes |
| < 1.4 | No |

## Reporting a vulnerability

Please report suspected vulnerabilities privately, not in public issues:

- **GitHub:** use [Report a vulnerability](https://github.com/ApexAiOfficial/ua-rotator/security/advisories/new) on this repository's Security tab.
- **Email:** support@apexaiofficial.com, with "ua-rotator security" in the subject.

Include the affected version, a description of the issue and its impact, and steps or a proof of concept to reproduce it. We aim to acknowledge reports within three business days and will keep you informed while we investigate and fix the issue.

## Scope

In scope: the `apex_ua_rotator` package, the one-file installer, the locator launchers, and the release tooling in this repository. Reports about third-party sites, Scrapy itself, or how someone uses the library against systems they do not control are out of scope.

## Verifying downloads

Every release publishes `SHA256SUMS` for its assets, and its artifacts are reproducible from the tagged source. See [Verifying a published release](docs/VALIDATION.md#verifying-a-published-release).
