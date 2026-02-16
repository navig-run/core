# NAVIG Troubleshooting Guide

## Connection Issues

### SSH Connection Failed

**Symptoms:**
- "Connection refused" or "Connection timed out"
- "Permission denied (publickey)"

**Solutions:**

1. **Test SSH directly:**
   ```bash
   ssh -v user@hostname
   ```

2. **Check host configuration:**
   ```bash
   navig host show
   ```

3. **Verify SSH key permissions:**
   ```bash
   chmod 600 ~/.ssh/id_rsa
   chmod 644 ~/.ssh/id_rsa.pub
   ```

4. **Check if SSH agent has key:**
   ```bash
   ssh-add -l
   ssh-add ~/.ssh/id_rsa
   ```

### Password Authentication Issues

**Symptoms:**
- "Authentication failed" with password
- Special characters in password causing issues

**Solutions:**

1. **Use SSH keys instead of passwords** (recommended)

2. **If password is required**, NAVIG automatically escapes special characters. Ensure your host config has the correct password.

3. **Test with verbose mode:**
   ```bash
   navig --verbose host test
   ```

---

## Command Escaping Issues

### JSON or Special Characters Fail

**Symptoms:**
- "Unexpected token" or "parse error"
- Commands with `{`, `}`, `$`, `!`, quotes fail
- JSON payloads get mangled

**Solutions:**

1. **Use Base64 encoding (recommended):**
   ```bash
   navig run --b64 "curl -d '{\"key\":\"value\"}' api.com"
   ```

2. **Use file input:**
   ```bash
   # Save command to file
   echo "curl -d '{\"key\":\"value\"}' api.com" > cmd.sh
   navig run @cmd.sh
   ```

3. **Use stdin:**
   ```bash
   echo "curl -d '{\"key\":\"value\"}' api.com" | navig run "@-"
   ```

### Multi-line Commands Fail

**Symptoms:**
- Only first line executes
- Syntax errors on multi-line scripts

**Solutions:**

1. **Use file input:**
   ```bash
   navig run @script.sh
   ```

2. **Use interactive mode:**
   ```bash
   navig run -i
   ```

3. **Use heredoc with stdin:**
   ```bash
   cat <<'EOF' | navig run "@-"
   line1
   line2
   EOF
   ```

### Decision Tree

```
Is your command simple (no JSON, no special chars)?
├── YES → navig run "command"
└── NO → Does it contain JSON or special characters?
    ├── YES → navig run --b64 "command"
    └── NO → Is it multi-line?
        ├── YES → navig run @file.sh or navig run -i
        └── NO → navig run "command"
```

### PowerShell-Specific Escaping

PowerShell requires special handling for complex commands:

**Use here-strings to avoid escaping:**
```powershell
# Instead of escaping quotes
@'
curl -X POST -H "Content-Type: application/json" -d '{"key":"value"}' api.com
'@ | navig run "@-"
```

**Variables are preserved with `@'...'@` syntax:**
```powershell
@'
echo $HOME  # $HOME won't be expanded by PowerShell
'@ | navig run "@-"
```

### Common `--b64` Errors

**"base64: invalid input"**
- Command contains line breaks. Use `@file` method instead.
- Binary content in command. Save to file and use `navig run @file`.

**"command not found" after Base64 decode**
- The command itself contains syntax errors. Test locally first.
- Check for invisible characters (copy/paste issues).

### Interactive Mode Errors (`-i`)

**"No editor found"**
- Set `EDITOR` environment variable: `$env:EDITOR = "notepad"` (Windows)
- Set `EDITOR` environment variable: `export EDITOR=nano` (Linux/macOS)

**"Temp file not saved"**
- Editor must save the file before closing
- Don't cancel the editor (Ctrl+C); save and close normally

---

## Database Issues

### Cannot Connect to Database

**Symptoms:**
- "Access denied for user"
- "Can't connect to MySQL server"

**Solutions:**

1. **Check tunnel status:**
   ```bash
   navig tunnel status
   ```

2. **Start tunnel if needed:**
   ```bash
   navig tunnel start
   ```

3. **Verify database credentials in app config:**
   ```bash
   navig app show
   ```

4. **Test database connection:**
   ```bash
   navig db shell
   ```

### Database List Empty

**Symptoms:**
- `navig db list` returns no databases

**Solutions:**

1. **Check if MySQL/MariaDB is running:**
   ```bash
   navig run "systemctl status mysql"
   ```

2. **Verify database user has permissions:**
   ```bash
   navig db query "SHOW GRANTS"
   ```

---

## HestiaCP Issues

### "HestiaCP not found on server"

**Symptoms:**
- Error when running `navig hestia users` or `navig hestia domains`

**Solutions:**

1. **Verify HestiaCP is installed:**
   ```bash
   navig run "command -v v-list-users"
   ```

2. **Check if running as correct user:**
   HestiaCP commands often require root or admin user.

3. **Verify PATH includes HestiaCP:**
   ```bash
   navig run "echo \$PATH"
   ```

---

## Tunnel Issues

### Tunnel Won't Start

**Symptoms:**
- "Address already in use"
- Tunnel starts but immediately stops

**Solutions:**

1. **Check if port is in use:**
   ```bash
   # Windows
   netstat -ano | findstr :3306
   
   # Linux/Mac
   lsof -i :3306
   ```

2. **Kill existing process on port:**
   ```bash
   navig tunnel stop
   ```

3. **Use different local port in app config**

### Tunnel Disconnects Frequently

**Solutions:**

1. **Add SSH keepalive to host config:**
   ```yaml
   ssh_options:
     ServerAliveInterval: 60
     ServerAliveCountMax: 3
   ```

2. **Check network stability**

---

## File Operation Issues

### Upload/Download Fails

**Symptoms:**
- "Permission denied" during transfer
- Transfer hangs

**Solutions:**

1. **Check remote directory permissions:**
   ```bash
   navig run "ls -la /path/to/directory"
   ```

2. **Verify disk space:**
   ```bash
   navig monitor disk
   ```

3. **Try with verbose mode:**
   ```bash
   navig --verbose upload file.txt /remote/path/
   ```

---

## Performance Issues

### Commands Are Slow

**Solutions:**

1. **Check network latency:**
   ```bash
   ping hostname
   ```

2. **Use `--raw` for faster output:**
   ```bash
   navig --raw run "command"
   ```

3. **Check server load:**
   ```bash
   navig monitor resources
   ```

---

## Debug Mode

Enable debug logging for detailed troubleshooting:

```bash
navig --debug-log <command>
```

Debug logs are saved to `~/.navig/debug.log`

---

## Getting Help

1. **Check command help:**
   ```bash
   navig <command> --help
   ```

2. **View documentation:**
   - [Quick Start](quick-start.md)
   - [Commands Reference](commands.md)
   - [Full Handbook](HANDBOOK.md)

3. **Report issues:**
   Include debug log output when reporting issues.


