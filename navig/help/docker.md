# docker

Operate on Docker containers on the active host.

Common commands:
- `navig docker ps`
- `navig docker logs <container>`
- `navig docker exec <container> -- <cmd>`
- `navig docker restart <container>`

Examples:
- `navig docker ps --json`
- `navig docker logs myapp --lines 200`
- `navig docker exec myapp -- sh -lc "printenv | head"`


