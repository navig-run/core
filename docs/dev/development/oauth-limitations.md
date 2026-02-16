# OAuth Limitations for AI Providers

## Current Status

The NAVIG OAuth implementation is **fully functional** and follows industry-standard PKCE flow. However, **OAuth authentication requires provider-specific client registration**, which has the following limitations:

## OpenAI / ChatGPT

❌ **Not Available**

- OpenAI's OAuth is **only available to enterprise partners and approved applications**
- Public API access uses API keys, not OAuth
- The client ID from Reference Agent cannot be reused (it's registered specifically for Reference Agent)
- **Solution**: Use API key authentication instead:
  ```bash
  navig cred add openai my-api-key --type api-key
  ```

## Anthropic Claude

❌ **Not Available**

- Anthropic currently only supports API key authentication
- No OAuth endpoints available
- **Solution**: Use API key authentication:
  ```bash
  navig cred add anthropic my-api-key --type api-key
  ```

## Google AI / Gemini

✅ **Potentially Available**

- Google supports OAuth 2.0 for AI services
- Requires registering NAVIG as a Google Cloud application
- Would need Google Cloud project, OAuth consent screen, and client credentials

## Microsoft Azure OpenAI

✅ **Potentially Available**

- Azure supports OAuth 2.0 via Azure Active Directory
- Requires Azure AD app registration
- Would need tenant ID, client ID, and client secret

## How OAuth Works

When you see a **white page** at an OAuth authorization URL, it means:

1. ❌ The client ID is invalid or not registered
2. ❌ The client ID is registered for a different application
3. ❌ The provider doesn't support public OAuth
4. ❌ Additional configuration is required (consent screen, etc.)

## When Will OAuth Be Available?

OAuth authentication in NAVIG will work when:

1. **Provider Supports OAuth**: The AI provider offers OAuth 2.0 authentication
2. **NAVIG Registration**: We register NAVIG as an application with the provider
3. **Client Credentials**: We obtain valid client_id (and possibly client_secret)
4. **Configuration**: We add the provider to `OAUTH_PROVIDERS` dict with valid credentials

## Alternative: API Key Authentication

For now, all AI providers support **API key authentication**, which is simpler and works immediately:

```bash
# Add API key credential
navig cred add <provider> <api-key> --type api-key

# List providers and their requirements
navig ai providers

# Use AI functionality
navig ai "your question" --provider <provider>
```

## Future Plans

The OAuth framework is **production-ready** and will be enabled for providers as we:

1. Register NAVIG with provider OAuth systems
2. Obtain valid client credentials
3. Add provider configurations to `oauth.py`

The implementation follows RFC 7636 (PKCE) and supports:
- ✅ Interactive browser flow
- ✅ Headless/VPS flow with manual URL paste
- ✅ Secure token storage
- ✅ Automatic token refresh
- ✅ Multiple account profiles

The framework is ready - we just need valid OAuth registrations with providers.



