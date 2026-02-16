# GitLab Runner Addon for NAVIG

## Overview
GitLab Runner is an open-source application that works with GitLab CI/CD to run jobs in a pipeline. It can run on various platforms and supports multiple executors including Docker, Kubernetes, and shell.

## Features
- Multiple executor support (Docker, Shell, Kubernetes, SSH)
- Autoscaling with Docker Machine or Kubernetes
- Parallel job execution
- Job artifacts and caching
- Secure variable handling
- Cross-platform (Linux, macOS, Windows)

## Usage

### Enable the Addon
```bash
navig server-template init gitlab-runner --server <server-name>
navig server-template enable gitlab-runner --server <server-name>
```

### Common Operations
```bash
# Check runner status
navig run "gitlab-runner status" --server <server-name>

# List registered runners
navig run "gitlab-runner list" --server <server-name>

# Register a new runner
navig run "gitlab-runner register" --server <server-name>

# Verify runners are connected
navig run "gitlab-runner verify" --server <server-name>

# View runner logs
navig run "journalctl -u gitlab-runner -f" --server <server-name>
```

## Configuration

### Registration
```bash
gitlab-runner register \
  --url https://gitlab.com/ \
  --registration-token YOUR_TOKEN \
  --executor docker \
  --docker-image alpine:latest \
  --description "My Runner" \
  --tag-list "docker,linux"
```

### Key Settings in `/etc/gitlab-runner/config.toml`:
- `concurrent` - Number of concurrent jobs
- `check_interval` - Job check interval
- `[[runners]]` - Individual runner configurations
- `executor` - Job executor type
- `[runners.docker]` - Docker-specific settings

## Default Paths
| Path | Description |
|------|-------------|
| `/etc/gitlab-runner` | Configuration directory |
| `/etc/gitlab-runner/config.toml` | Main configuration file |
| `/home/gitlab-runner/builds` | Build directory |
| `/home/gitlab-runner/cache` | Cache directory |

## Executors
| Executor | Use Case |
|----------|----------|
| `shell` | Run jobs directly on the host |
| `docker` | Run jobs in Docker containers |
| `docker+machine` | Autoscaling Docker runners |
| `kubernetes` | Run jobs in Kubernetes pods |
| `ssh` | Run jobs on remote servers |

## References
- Official Documentation: https://docs.gitlab.com/runner/
- Installation Guide: https://docs.gitlab.com/runner/install/
- Configuration Reference: https://docs.gitlab.com/runner/configuration/
- GitHub: https://gitlab.com/gitlab-org/gitlab-runner


