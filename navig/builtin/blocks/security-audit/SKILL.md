---
name: Security Audit
description: A guided server-hardening audit checklist — updates, SSH config, ports, services, logins, firewall, certs, permissions.
version: 1.0.0
generated-by: navig-block
block-id: security-audit
---

# Server hardening audit checklist

A guided runbook for a basic server security audit: patch level, SSH config, open
ports, running services, failed logins, sudo users, firewall, SSL certs, and
sensitive-file permissions.

## Guided (instruction) block

An audit checklist a human reads and acts on — there is no single machine-verifiable
outcome, so it ships as an **instruction** block (`verification: none`). For
production, pair it with automated vulnerability scanners and rootkit checks
(rkhunter/chkrootkit).

## Apply

```
navig apply security-audit
```

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply security-audit
```

### Inputs

- (none)

