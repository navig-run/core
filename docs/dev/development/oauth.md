# OAuth Authentication

NAVIG supports OAuth PKCE authentication for subscription-based AI providers.

## Supported OAuth Providers

| Provider | Description | Command |
|----------|-------------|---------|
| `openai-codex` | ChatGPT/Codex subscription | `navig ai login openai-codex` |

## How It Works

### OAuth PKCE Flow

1. **PKCE Generation**: A code verifier (random 64-char hex) and code challenge (SHA256 hash, base64url) are generated
2. **State Generation**: A random state parameter prevents CSRF attacks
3. **Authorization URL**: Browser opens to the provider's auth endpoint with:
   - `client_id` - Public client identifier
   - `redirect_uri` - `http://127.0.0.1:1455/auth/callback`
   - `code_challenge` - PKCE challenge
   - `code_challenge_method` - `S256`
   - `state` - Random state for validation
4. **User Authentication**: User signs in with the provider
5. **Callback Capture**: 
   - Interactive: Local server on port 1455 captures the callback
   - Headless: User pastes the redirect URL manually
6. **Token Exchange**: Authorization code exchanged for access/refresh tokens
7. **Secure Storage**: Tokens saved to `~/.navig/credentials/auth-profiles.json`

### Token Refresh

When the access token expires:
1. NAVIG detects expiry (with 5-minute buffer)
2. Refresh token is used to get new tokens
3. New tokens are stored, replacing the old ones
4. If refresh fails, user is prompted to re-authenticate

## Commands

### Interactive Login

```bash
navig ai login openai-codex
```

1. Browser opens to OpenAI sign-in page
2. User completes authentication
3. Callback is captured automatically
4. Tokens are stored securely

### Headless Login (VPS/Remote)

For servers without a browser:

```bash
navig ai login openai-codex --headless
```

1. Authorization URL is displayed
2. User copies URL and opens in local browser
3. User completes authentication
4. User pastes the redirect URL back into the terminal
5. Tokens are stored securely

### Logout

```bash
navig ai logout openai-codex
```

Removes all OAuth credentials for the provider.

### Check Status

```bash
navig ai providers
```

Shows which providers have credentials configured.

## Storage

OAuth credentials are stored in:

```
~/.navig/credentials/auth-profiles.json
```

Structure:
```json
{
  "version": 1,
  "profiles": {
    "openai-codex:default": {
      "type": "oauth",
      "provider": "openai-codex",
      "accessToken": "...",
      "refreshToken": "...",
      "expiresAt": 1706745600000,
      "clientId": "DY3M3cxyoKmn8S5jSQsqN61sYYuH2n9K"
    }
  }
}
```

File permissions are set to `0600` (owner read/write only) on Unix systems.

## Troubleshooting

### "Port 1455 already in use"

Another process is using the callback port. Either:
- Close the other process
- Use `--headless` mode

### "State mismatch"

The state parameter doesn't match. This could indicate:
- CSRF attack (unlikely)
- Multiple login attempts (cancel and retry)

### "Token refresh failed"

The refresh token may be expired or revoked. Re-authenticate:

```bash
navig ai logout openai-codex
navig ai login openai-codex
```

### "Callback not received"

If the browser callback doesn't work:
1. Check that nothing is blocking localhost:1455
2. Use `--headless` mode and paste the URL manually

## Security Considerations

1. **PKCE**: Prevents authorization code interception attacks
2. **State Parameter**: Prevents CSRF attacks
3. **Local Callback**: Callback only accepts requests from localhost
4. **Secure Storage**: Credentials stored with restrictive file permissions
5. **Token Refresh**: Tokens are refreshed before expiry, minimizing exposure

## Integration with Reference Agent

This OAuth implementation is based on Reference Agent's authentication patterns:

- PKCE flow from `@mariozechner/pi-ai`
- Auth profile storage structure
- Callback server pattern
- VPS-aware headless mode

The implementation is compatible with Reference Agent's token format, allowing potential credential sharing between tools.



