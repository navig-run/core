# Security Policy

## Supported Versions

| Version | Status |
|---|---|
| 2.4.x | ✅ Supported |
| 2.3.x | Critical fixes only |
| 2.2.x and below | Unsupported |

## Reporting a Vulnerability

Do **not** report vulnerabilities through public issues.

### Preferred channel

- GitHub Security Advisories (private report)

### Email fallback

- Email: security@navig.run
- Subject: `[SECURITY] <short title>`

## Vulnerability Disclosure SLA

1. **Acknowledgement**: within 24 hours.
2. **Triage + severity classification**: within 72 hours.
3. **Remediation plan**: within 7 calendar days.
4. **Patch target windows**:
   - Critical: 1-7 days
   - High: 7-14 days
   - Medium: 30 days
   - Low: 60+ days or bundled release
5. **Coordinated disclosure**: default 90 days max, earlier when patch ships.

If active exploitation is confirmed, emergency release and disclosure are accelerated.

## Scope

In scope:

- CLI command and subprocess security boundaries
- SSH, credential, vault, and secret handling
- MCP and gateway authentication/authorization flows
- Configuration parsing and privilege-sensitive operations

Out of scope:

- Social engineering
- Physical host compromise
- Vulnerabilities in third-party services with no NAVIG defect

## Security Release Process

For a confirmed issue:

1. Reproduce and document impact.
2. Patch with tests.
3. Run security + regression checks.
4. Publish fixed version + advisory.
5. Notify users and update changelog.

## Third-Party Dependency Advisories

- `requirements.lock` is the canonical committed Python lockfile for this repo.
   Auxiliary lockfiles that duplicate the same dependency graph should not be kept
   in sync in parallel.
- If an upstream package has no published fixed release yet, keep the dependency
   surface minimal, document the constraint, and remove duplicate lockfile noise
   until a real patched version is available.
- Archived or reference-only subtrees should be excluded from active dependency
   graphs whenever possible so security tooling reflects shipped surfaces.

## Security Contact

- Primary: security@navig.run
- Advisory portal: GitHub Security Advisories

_Last updated: 2026-03-27_
