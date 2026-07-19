---
id: docker-health
spec_version: 1
name: Docker Health Check
version: 1.0.0
category: ops
license: MIT
description: A guided checklist to verify Docker containers are healthy and resources are adequate.
author: navig
tags: [docker, health, monitoring, checklist, runbook]
target: local

inputs: []

steps:
  - id: daemon-status
    kind: instruction
    safety: safe
    text: |
      Check the Docker daemon is up:  `navig docker info`
  - id: list-containers
    kind: instruction
    safety: safe
    text: |
      List running containers:  `navig docker ps`
  - id: resource-usage
    kind: instruction
    safety: safe
    text: |
      Check per-container resource usage:  `navig docker stats --no-stream`
  - id: review-logs
    kind: instruction
    safety: safe
    text: |
      Scan recent container logs for ERROR / FATAL / exceptions:
      `navig docker logs --tail 50`
  - id: disk-space
    kind: instruction
    safety: safe
    text: |
      Confirm at least ~20% free disk:  `navig run "df -h"`
  - id: stopped-containers
    kind: instruction
    safety: safe
    text: |
      Check for unexpectedly exited containers:
      `navig docker ps -a --filter status=exited`

verify:
  kind: none

receipt:
  redact: []
---
# Docker health checklist

A guided runbook for verifying Docker infrastructure health: daemon, running
containers, resource usage, logs, disk headroom, and stopped containers.

## Guided (instruction) block

A diagnostic checklist, not a single verifiable outcome (`verification: none`).
For a runnable full-host sweep, apply the **server-health** block. You can also
automate a periodic reminder with `navig trigger add`.

## Apply

```
navig apply docker-health
```
