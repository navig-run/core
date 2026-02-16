---
name: ssh-helper
description: Secure Shell (SSH) configuration and key management.
metadata:
  navig:
    emoji: 🔑
    requires:
      bins: [ssh, ssh-keygen]
      config: [~/.ssh/config]
---

# SSH Helper Skill

Manage your SSH keys and configurations to ensure secure and seamless connectivity to all your hosts.

## Core Operations

### Key Management
```bash
# Generate a new secure key (Ed25519 is comprehensive best practice)
navig run "ssh-keygen -t ed25519 -C 'agent@navig' -f ~/.ssh/id_navig_ed25519 -N ''"

# View public key for distribution
navig file show ~/.ssh/id_navig_ed25519.pub
```

### Config Management
Optimize your SSH connection sharing and timeouts.

**Recommended Config (~/.ssh/config):**
```ssh
Host *
    ControlMaster auto
    ControlPath ~/.ssh/sockets/%r@%h-%p
    ControlPersist 600
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

**Apply Config:**
```bash
# Upload standard config
navig file add ./templates/ssh/config ~/.ssh/config --mode 600
```

### Connection Testing
```bash
# Debug a connection
navig run "ssh -v user@host 'echo Connection Successful'"
```

## Best Practices
1. **Use Ed25519**: Preferred over RSA for security and performance.
2. **Multiplexing**: Use `ControlMaster` to speed up sequential commands by reusing the TCP connection.
3. **Permissions**: Always ensure `~/.ssh` is 700 and keys are 600.



