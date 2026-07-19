---
id: security-audit
spec_version: 1
name: Security Audit
version: 1.0.0
category: security
license: MIT
description: A guided server-hardening audit checklist — updates, SSH config, ports, services, logins, firewall, certs, permissions.
author: navig
tags: [security, audit, hardening, checklist, runbook]
target: local

inputs: []

steps:
  - id: system-updates
    kind: instruction
    safety: safe
    text: |
      Check for pending system updates:
      `navig run "apt list --upgradable 2>/dev/null || yum check-update"`
  - id: ssh-config
    kind: instruction
    safety: safe
    text: |
      Review SSH hardening — expect PasswordAuthentication no, PermitRootLogin
      prohibit-password:  `navig run "grep -E '(PasswordAuth|PermitRoot|Port)' /etc/ssh/sshd_config"`
  - id: open-ports
    kind: instruction
    safety: safe
    text: |
      Review listening ports:  `navig run "ss -tulpn"`
  - id: running-services
    kind: instruction
    safety: safe
    text: |
      Review running services:  `navig run "systemctl list-units --type=service --state=running"`
  - id: failed-logins
    kind: instruction
    safety: safe
    text: |
      Check for brute-force attempts:  `navig run "grep 'Failed password' /var/log/auth.log | tail -20"`
  - id: sudo-users
    kind: instruction
    safety: safe
    text: |
      Review privileged accounts:  `navig run "getent group sudo wheel"`
  - id: firewall
    kind: instruction
    safety: safe
    text: |
      Confirm the firewall is active:  `navig run "ufw status || firewall-cmd --state"`
  - id: ssl-certs
    kind: instruction
    safety: safe
    text: |
      Check certificate expiry dates under /etc/ssl.
  - id: file-permissions
    kind: instruction
    safety: safe
    text: |
      Verify permissions on sensitive files:
      `navig run "ls -la /etc/passwd /etc/shadow /etc/ssh/sshd_config"`

verify:
  kind: none

receipt:
  redact: []
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
