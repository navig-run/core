---
name: startup-pack
description: Essential DevOps toolchain for modern startups.
metadata:
  navig:
    emoji: 🚀
    includes:
      - core
      - devops.docker
      - devops.github
      - devops.postgres
      - devops.discord
---

# Startup DevOps Pack

This pack bundles the essential skills needed to manage a modern startup's infrastructure.

## Included Skills

### 1. `navig-core`
The fundamental interface to your servers. Use it for:
- File transfers (`navig file add/get`)
- System monitoring (`navig host monitor`)
- Shell access (`navig run`)

### 2. `docker-ops`
Container orchestration.
- `navig docker ps`: Check running services
- `navig docker logs`: Debug application issues
- `navig docker compose up`: Deploy stacks

### 3. `github-cli`
Code repository management.
- `gh pr list`: Review pending changes
- `gh release create`: Ship new versions
- `gh run list`: Monitor CI/CD status

## Recommended Workflow
1. **Design**: Use `knowledge-md` to spec features.
2. **Code**: Push to GitHub.
3. **Deploy**: `navig docker compose up -d` on Staging.
4. **Notify**: `navig discord send` to #deployments.



