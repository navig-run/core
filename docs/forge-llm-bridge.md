# Forge LLM Bridge — Setup Guide

> Route Telegram messages through the NAVIG daemon on Ubuntu to VS Code
> Copilot running on your Windows machine via an SSH tunnel.

## Architecture

```
Telegram → NAVIG Daemon (Ubuntu)
              │
              └─ ForgeProvider → SSH tunnel (port 43821)
                                      │
                                      └─ VS Code LLM Server (Windows)
                                              │
                                              └─ GitHub Copilot (GPT-4o)
```

## 1. Windows: Configure Persistent LLM Secret

The VS Code LLM server generates an **ephemeral** token by default.
For the tunnel to authenticate reliably across restarts, set a fixed token.

### Generate a token

```powershell
# PowerShell — generate a URL-safe random token
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]]) -replace '[+/=]',''
```

Or use any password generator to create a 32+ character alphanumeric string.

### Set in VS Code settings.json

```jsonc
// Windows — VS Code User Settings (Ctrl+Shift+P → "Open User Settings JSON")
{
    "navig-bridge.chat.vscodeLlmPort": 43821,
    "navig-bridge.chat.vscodeLlmSecret": "YOUR_FIXED_TOKEN_HERE"
}
```

> **Do NOT** commit this token to version control.

## 2. Ubuntu: Configure Daemon

Add to `~/.navig/config.yaml` on the Ubuntu server:

```yaml
# Forge LLM Bridge settings
forge:
  url: "http://127.0.0.1:43821"
  token: "YOUR_FIXED_TOKEN_HERE"      # Same token as VS Code setting
```

The daemon reads these on startup via `ai_client._get_forge_url()` and
`ai_client._get_forge_token()`.

### Environment variable overrides (optional)

```bash
export NAVIG_FORGE_LLM_URL="http://127.0.0.1:43821"
export NAVIG_FORGE_LLM_TOKEN="YOUR_FIXED_TOKEN_HERE"
```

## 3. SSH Tunnel

### Quick test (manual)

From the **Ubuntu** machine:

```bash
ssh -L 43821:127.0.0.1:43821 user@windows-host -N
```

Then verify:

```bash
curl -s http://127.0.0.1:43821/vscode-llm/health
# Expected: {"status":"ready","model":"copilot - GPT 4o","uptime":...}
```

### Production (autossh + systemd)

1. Copy the service file:
   ```bash
   sudo cp scripts/forge-tunnel.service /etc/systemd/system/
   ```

2. Edit the service — update `User`, SSH key path, and `user@windows-host`:
   ```bash
   sudo systemctl edit forge-tunnel --force
   ```

3. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now forge-tunnel
   ```

4. Verify:
   ```bash
   systemctl status forge-tunnel
   curl -s http://127.0.0.1:43821/vscode-llm/health
   ```

### Windows: Allow inbound SSH

Make sure Windows has OpenSSH Server running and your Ubuntu key is in
`~/.ssh/authorized_keys` on the Windows user.

```powershell
# Check OpenSSH Server status
Get-Service sshd
# Start if stopped
Start-Service sshd
```

## 4. Verify End-to-End

```bash
# On Ubuntu — should return "forge"
python3 -c "
from navig.agent.ai_client import AIClient
c = AIClient()
print('Provider:', c.provider)
"

# Quick chat test
python3 -c "
import asyncio
from navig.agent.ai_client import get_ai_client
async def test():
    c = get_ai_client()
    r = await c.chat([{'role':'user','content':'Say hello in one sentence'}])
    print(r)
asyncio.run(test())
"
```

## 5. Provider Priority

The AI client auto-detects providers in this order:

| Priority | Provider   | Condition                              |
|----------|------------|----------------------------------------|
| 1        | **forge**  | Port 43821 reachable (TCP probe)       |
| 2        | openrouter | `openrouter_api_key` set               |
| 3        | airllm     | AirLLM available                       |
| 4        | local      | Ollama on port 11434                   |
| 5        | none       | Pattern-matching fallback (no LLM)     |

If Forge is unreachable (Windows off, tunnel down), the daemon
automatically falls back to the next available provider.

## 6. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `401 Unauthorized` | Token mismatch — verify VS Code setting matches `~/.navig/config.yaml` |
| `Connection refused` on 43821 | SSH tunnel is down — check `systemctl status forge-tunnel` |
| Provider detected as `openrouter` instead of `forge` | Tunnel not active — run `curl http://127.0.0.1:43821/vscode-llm/health` |
| Slow responses (>5s) | Network latency — consider colocating or using OpenRouter as primary |
| `EADDRINUSE` in VS Code output | Another process on 43821 — change port in both VS Code and config |
