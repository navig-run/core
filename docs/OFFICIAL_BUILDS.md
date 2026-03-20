# NAVIG Official Builds Policy

NAVIG source code is open under Apache-2.0.
Only releases that meet this policy are considered **Official NAVIG Builds**.

## Official Build Requirements

1. Built from official repository and tagged release.
2. CI checks pass (lint, tests, build, security checks).
3. Release artifacts include checksums.
4. Release artifacts include provenance attestations.
5. Release notes and changelog entries are published.

## Verification Expectations

Each official release must publish:

- `SHA256SUMS`
- signed/attested provenance metadata
- source reference (tag + commit SHA)

## Distribution Channels

Official channels:

- GitHub Releases (`navig-run/core`)
- PyPI project (`navig`) when published by official maintainers

Any fork/community build must use distinct branding and cannot claim to be official.
See `TRADEMARK.md`.

## Security Baseline for Official Builds

Required checks before release:

- static analysis (ruff + security-relevant linting)
- dependency vulnerability audit
- full test suite
- coverage gate

## Incident Response

If malicious or tampered builds are reported:

1. Triage within 24h.
2. Advisory issued when verified.
3. Revocation/notice posted via official channels.

_Last updated: 2026-03-18_
