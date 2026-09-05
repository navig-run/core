---
name: "infra-ops"
description: "How to operate live infrastructure safely — servers, SSH, deployments, databases, containers, incidents and outages. Use when the operator asks to deploy, restart, migrate, debug a production problem, run something on a remote host, or touch a database."
activation_keywords:
  - deploy
  - deployment
  - production
  - prod
  - outage
  - incident
  - rollback
  - migration
  - migrate
  - ssh
  - server
  - restart
  - docker
  - container
  - nginx
  - database
  - backup
  - restore
  - downtime
priority: 0
version: "1.0.0"
category: "playbook"
---

# Infra-ops playbook

Domain guidance for touching live systems. Reliability over cleverness — this is the
part of the job where being wrong is expensive.

## Before you touch anything

1. **Read before you write.** Inspect state first: what is running, what version, what
   changed last, what the logs say. A fix applied to a misdiagnosed system is a second
   incident.
2. **Name the target explicitly.** Host, environment, space, database, branch. Never
   infer production from context — if the target is ambiguous, ask. A command that
   would be right on staging can be unrecoverable on prod.
3. **Know the rollback before you roll forward.** If there isn't one, say so, and make
   that the decision the operator gets to take.
4. **Prefer the reversible form.** Dry run, `--check`, a read-only query, a copy, a new
   file beside the old one. Take a backup before a destructive migration.
5. **One change at a time during an incident.** Parallel fixes destroy the ability to
   tell which one worked.

## During an incident

Stabilise first, diagnose second, fix third, write it up last. Restoring service is
allowed to be inelegant. Say what you're doing as you do it — a silent operator during
an outage is worse than a slow one.

Capture the evidence *before* it rotates away: the failing log lines, the timestamps,
the last deploy. Post-incident, that is the whole value.

## Never do these without explicit consent

- `DROP`, `TRUNCATE`, mass `UPDATE`/`DELETE` without a `WHERE` you have read aloud
- force-push, history rewrite, branch deletion on a shared repo
- restarting or scaling a production service
- rotating a credential that other systems hold
- anything that sends mail, messages or notifications to real people

State the risk, offer the safer variant, and wait for a real answer.

## What not to do

- Never invent a path, hostname, container name, or flag. Verify it exists or say you
  can't find it — a plausible wrong path is more dangerous than "I don't know".
- Don't paste a secret into a log, a report, an issue, or a commit message.
- Don't silence a failing check to make output green.
