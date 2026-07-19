---
name: "docker-restart-safe"
description: "Safely restart Docker containers with mandatory confirmation and state checks."
user-invocable: true
version: "1.1.0"
author: "NAVIG Core Team"
category: "devops"
risk-level: "destructive"
requires:
  - "pack:docker-health"
confirmation-required: true

navig-commands:
  - name: "restart"
    syntax: "navig docker restart <container_name>"
    description: "Restart a container gracefully."
    risk: "destructive"
    confirmation_msg: "⚠️  WARNING: You are about to restart container '{container_name}'. This may cause downtime."

  - name: "down"
    syntax: "navig docker down <compose_file>"
    description: "Stop and remove resources defined in a compose file."
    risk: "destructive"
    confirmation_msg: "⚠️  DESTRUCTIVE: This will stop all services in '{compose_file}' and remove networks."

examples:
  - user: "Restart the web server"
    thought: "User wants to restart a container. I need to identify the container name first."
    command: "navig docker restart web-server-1"

  - user: "Take down the stack"
    thought: "User wants to run docker-compose down. This is a high-risk action."
    command: "navig docker down ./docker-compose.yml"

---

# Docker Restart Safe

This skill wraps standard Docker commands with a safety layer. Unlike raw Docker commands, these verify the container exists and force a user confirmation step before execution.

## Safety Features

1.  **Pre-Flight Check**: Verifies the container is actually running before attempting restart.
2.  **Confirmation Modal**: Intercepts the command and presents a [Y/n] prompt to the user with a summary of impact.
3.  **Health Verification**: Automatically runs the `docker-health` pack after restart to confirm successful recovery.

## Dependencies

-   Requires `docker` executable in PATH.
-   Integrates with `docker-health` pack for post-operation verification.
