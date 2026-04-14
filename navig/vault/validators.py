"""
NAVIG Vault Credential Validators

Provider-specific validators for testing credential validity.
Each validator makes a minimal API call to verify the credential works.
"""

from abc import ABC, abstractmethod

from .types import Credential, TestResult

# Timeouts for outbound HTTP health-check calls.
_VALIDATOR_DEFAULT_TIMEOUT: int = 10
_VALIDATOR_EXTENDED_TIMEOUT: int = 15


class CredentialValidator(ABC):
    """
    Base class for credential validators.

    Each provider should implement a validator that makes a minimal
    API call to verify credentials are valid and working.
    """

    @abstractmethod
    def validate(self, credential: Credential) -> TestResult:
        """
        Validate the credential and return result.

        Args:
            credential: Credential to validate

        Returns:
            TestResult with success status and details
        """
        pass


class OpenAIValidator(CredentialValidator):
    """Validate OpenAI API keys."""

    def validate(self, credential: Credential) -> TestResult:
        api_key = credential.data.get("api_key", "")
        if not api_key:
            return TestResult(success=False, message="API key is empty")

        try:
            import httpx

            response = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                models = response.json().get("data", [])
                return TestResult(
                    success=True,
                    message="OpenAI API key is valid",
                    details={"models_available": len(models)},
                )
            elif response.status_code == 401:
                return TestResult(success=False, message="Invalid API key")
            elif response.status_code == 429:
                return TestResult(
                    success=False,
                    message="Rate limited - key may be valid but has no quota",
                )
            else:
                error = response.json().get("error", {}).get("message", "Unknown error")
                return TestResult(
                    success=False,
                    message=f"API error: {response.status_code} - {error}",
                )
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class AnthropicValidator(CredentialValidator):
    """Validate Anthropic API keys."""

    def validate(self, credential: Credential) -> TestResult:
        api_key = credential.data.get("api_key", "")
        if not api_key:
            return TestResult(success=False, message="API key is empty")

        try:
            import httpx

            # Use a minimal message to validate
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-3-haiku-20240307",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=_VALIDATOR_EXTENDED_TIMEOUT,
            )
            if response.status_code in (200, 201):
                return TestResult(success=True, message="Anthropic API key is valid")
            elif response.status_code == 401:
                return TestResult(success=False, message="Invalid API key")
            elif response.status_code == 429:
                return TestResult(success=False, message="Rate limited - key may be valid")
            else:
                error = response.json().get("error", {}).get("message", "Unknown")
                return TestResult(
                    success=False,
                    message=f"API error: {response.status_code} - {error}",
                )
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class OpenRouterValidator(CredentialValidator):
    """Validate OpenRouter API keys."""

    def validate(self, credential: Credential) -> TestResult:
        api_key = credential.data.get("api_key", "")
        if not api_key:
            return TestResult(success=False, message="API key is empty")

        try:
            import httpx

            response = httpx.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json().get("data", {})
                return TestResult(
                    success=True,
                    message="OpenRouter API key is valid",
                    details={
                        "usage_usd": data.get("usage", 0),
                        "limit_usd": data.get("limit"),
                        "is_free_tier": data.get("is_free_tier", False),
                    },
                )
            elif response.status_code == 401:
                return TestResult(success=False, message="Invalid API key")
            else:
                return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class GroqValidator(CredentialValidator):
    """Validate Groq API keys."""

    def validate(self, credential: Credential) -> TestResult:
        api_key = credential.data.get("api_key", "")
        if not api_key:
            return TestResult(success=False, message="API key is empty")

        try:
            import httpx

            response = httpx.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                models = response.json().get("data", [])
                return TestResult(
                    success=True,
                    message="Groq API key is valid",
                    details={"models_available": len(models)},
                )
            elif response.status_code == 401:
                return TestResult(success=False, message="Invalid API key")
            else:
                return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class GitHubValidator(CredentialValidator):
    """Validate GitHub tokens."""

    def validate(self, credential: Credential) -> TestResult:
        # Support both 'api_key' and 'token' keys
        token = credential.data.get("api_key") or credential.data.get("token", "")
        if not token:
            return TestResult(success=False, message="Token is empty")

        try:
            import httpx

            response = httpx.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                user = response.json()
                return TestResult(
                    success=True,
                    message="GitHub token is valid",
                    details={
                        "login": user.get("login"),
                        "name": user.get("name"),
                        "email": user.get("email"),
                    },
                )
            elif response.status_code == 401:
                return TestResult(success=False, message="Invalid or expired token")
            elif response.status_code == 403:
                return TestResult(success=False, message="Token valid but lacks permissions")
            else:
                return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class GitLabValidator(CredentialValidator):
    """Validate GitLab tokens."""

    def validate(self, credential: Credential) -> TestResult:
        token = credential.data.get("api_key") or credential.data.get("token", "")
        base_url = credential.metadata.get("base_url", "https://gitlab.com")
        if not token:
            return TestResult(success=False, message="Token is empty")

        try:
            import httpx

            response = httpx.get(
                f"{base_url}/api/v4/user",
                headers={"PRIVATE-TOKEN": token},
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                user = response.json()
                return TestResult(
                    success=True,
                    message="GitLab token is valid",
                    details={
                        "username": user.get("username"),
                        "name": user.get("name"),
                        "email": user.get("email"),
                    },
                )
            elif response.status_code == 401:
                return TestResult(success=False, message="Invalid or expired token")
            else:
                return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class EmailValidator(CredentialValidator):
    """Validate email credentials via IMAP connection."""

    def validate(self, credential: Credential) -> TestResult:
        email = credential.metadata.get("email", "")
        password = credential.data.get("password", "")
        imap_host = credential.metadata.get("imap_host", "imap.gmail.com")
        imap_port = credential.metadata.get("imap_port", 993)

        if not email:
            return TestResult(success=False, message="Email address is empty")
        if not password:
            return TestResult(success=False, message="Password is empty")

        try:
            import imaplib

            # Connect to IMAP server
            imap = imaplib.IMAP4_SSL(imap_host, imap_port)
            imap.login(email, password)

            # List folders to verify access
            status, folders = imap.list()
            folder_count = len(folders) if folders else 0

            imap.logout()

            return TestResult(
                success=True,
                message="Email credentials are valid",
                details={
                    "imap_host": imap_host,
                    "folders_found": folder_count,
                },
            )
        except Exception as e:
            error_msg = str(e)
            if "AUTHENTICATIONFAILED" in error_msg.upper():
                return TestResult(
                    success=False,
                    message="Authentication failed - check password or enable app passwords",
                )
            elif "SSL" in error_msg.upper() or "CERTIFICATE" in error_msg.upper():
                return TestResult(success=False, message=f"SSL/TLS error: {error_msg}")
            else:
                return TestResult(success=False, message=f"IMAP error: {error_msg}")


class JiraValidator(CredentialValidator):
    """Validate Jira API tokens."""

    def validate(self, credential: Credential) -> TestResult:
        token = credential.data.get("api_key") or credential.data.get("token", "")
        email = credential.metadata.get("email", "")
        base_url = credential.metadata.get("base_url", "")

        if not token:
            return TestResult(success=False, message="API token is empty")
        if not email:
            return TestResult(success=False, message="Email is required for Jira auth")
        if not base_url:
            return TestResult(
                success=False,
                message="Jira base URL is required (e.g., https://yourorg.atlassian.net)",
            )

        try:
            import base64

            import httpx

            # Jira uses Basic auth with email:token
            auth_string = f"{email}:{token}"
            auth_bytes = base64.b64encode(auth_string.encode()).decode()

            response = httpx.get(
                f"{base_url}/rest/api/2/myself",
                headers={
                    "Authorization": f"Basic {auth_bytes}",
                    "Accept": "application/json",
                },
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                user = response.json()
                return TestResult(
                    success=True,
                    message="Jira credentials are valid",
                    details={
                        "display_name": user.get("displayName"),
                        "email": user.get("emailAddress"),
                        "account_type": user.get("accountType"),
                    },
                )
            elif response.status_code == 401:
                return TestResult(success=False, message="Invalid credentials")
            elif response.status_code == 403:
                return TestResult(success=False, message="Token valid but lacks permissions")
            else:
                return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class GenericValidator(CredentialValidator):
    """
    Fallback validator that just checks for non-empty data.

    Used when no specific validator is available for a provider.
    """

    def validate(self, credential: Credential) -> TestResult:
        if credential.data:
            # Check if there's at least one non-empty value
            has_value = any(v for v in credential.data.values() if v and str(v).strip())
            if has_value:
                return TestResult(
                    success=True,
                    message="Credential data exists (no API validation available)",
                    details={
                        "provider": credential.provider,
                        "validation_mode": "presence_only",
                    },
                )
        return TestResult(success=False, message="Credential data is empty")


class TelegramValidator(CredentialValidator):
    """Validate Telegram bot token via getMe."""

    def validate(self, credential: Credential) -> TestResult:
        token = credential.data.get("token") or credential.data.get("bot_token", "")
        if not token:
            return TestResult(success=False, message="Token is empty")

        try:
            import httpx

            response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=_VALIDATOR_DEFAULT_TIMEOUT)
            payload = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if response.status_code == 200 and payload.get("ok"):
                result = payload.get("result", {})
                return TestResult(
                    success=True,
                    message="Telegram bot token is valid",
                    details={
                        "id": result.get("id"),
                        "username": result.get("username"),
                        "validation_mode": "remote",
                    },
                )

            if payload.get("description"):
                return TestResult(success=False, message=str(payload.get("description")))
            return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class DeepgramValidator(CredentialValidator):
    """Validate Deepgram API key via projects endpoint."""

    def validate(self, credential: Credential) -> TestResult:
        api_key = credential.data.get("api_key") or credential.data.get("token", "")
        if not api_key:
            return TestResult(success=False, message="API key is empty")

        try:
            import httpx

            response = httpx.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {api_key}"},
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                projects = response.json().get("projects", [])
                return TestResult(
                    success=True,
                    message="Deepgram API key is valid",
                    details={
                        "projects": len(projects),
                        "validation_mode": "remote",
                    },
                )
            if response.status_code in (401, 403):
                return TestResult(success=False, message="Invalid API key")
            return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class ElevenLabsValidator(CredentialValidator):
    """Validate ElevenLabs API key via user endpoint."""

    def validate(self, credential: Credential) -> TestResult:
        api_key = credential.data.get("api_key") or credential.data.get("token", "")
        if not api_key:
            return TestResult(success=False, message="API key is empty")

        try:
            import httpx

            response = httpx.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": api_key},
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json()
                sub = data.get("subscription", {})
                return TestResult(
                    success=True,
                    message="ElevenLabs API key is valid",
                    details={
                        "tier": sub.get("tier", "unknown"),
                        "character_limit": sub.get("character_limit"),
                        "character_count": sub.get("character_count"),
                        "validation_mode": "remote",
                    },
                )
            if response.status_code in (401, 403):
                return TestResult(success=False, message="Invalid API key")
            return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class XAIValidator(CredentialValidator):
    """Validate xAI API key via models endpoint."""

    def validate(self, credential: Credential) -> TestResult:
        api_key = credential.data.get("api_key") or credential.data.get("token", "")
        if not api_key:
            return TestResult(success=False, message="API key is empty")

        try:
            import httpx

            response = httpx.get(
                "https://api.x.ai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                models = response.json().get("data", [])
                return TestResult(
                    success=True,
                    message="xAI API key is valid",
                    details={
                        "models_available": len(models),
                        "validation_mode": "remote",
                    },
                )
            if response.status_code in (401, 403):
                return TestResult(success=False, message="Invalid API key")
            return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


class NvidiaValidator(CredentialValidator):
    """Validate NVIDIA NIM API key via models endpoint when available."""

    def validate(self, credential: Credential) -> TestResult:
        api_key = credential.data.get("api_key") or credential.data.get("token", "")
        if not api_key:
            return TestResult(success=False, message="API key is empty")

        try:
            import httpx

            response = httpx.get(
                "https://integrate.api.nvidia.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_VALIDATOR_DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                models = response.json().get("data", [])
                return TestResult(
                    success=True,
                    message="NVIDIA API key is valid",
                    details={
                        "models_available": len(models),
                        "validation_mode": "remote",
                    },
                )
            if response.status_code in (401, 403):
                return TestResult(success=False, message="Invalid API key")

            generic_result = GenericValidator().validate(credential)
            generic_result.message = f"Credential data exists (NVIDIA remote validation unavailable: {response.status_code})"
            return generic_result
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception:
            generic_result = GenericValidator().validate(credential)
            generic_result.message = "Credential data exists (NVIDIA remote validation unavailable)"
            return generic_result


class GitHubModelsValidator(CredentialValidator):
    """Validate GitHub Models (Copilot) tokens via Azure AI inference endpoint."""

    def validate(self, credential: Credential) -> TestResult:
        token = credential.data.get("token") or credential.data.get("api_key", "")
        if not token:
            return TestResult(success=False, message="Token is empty")

        try:
            import httpx

            response = httpx.post(
                "https://models.inference.ai.azure.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
                timeout=_VALIDATOR_EXTENDED_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json()
                model = data.get("model", "unknown")
                return TestResult(
                    success=True,
                    message=f"GitHub Models token is valid (model: {model})",
                    details={"model": model},
                )
            elif response.status_code == 401:
                return TestResult(success=False, message="Invalid or expired token")
            elif response.status_code == 429:
                return TestResult(
                    success=False,
                    message="Rate limited — token may be valid but quota exhausted",
                )
            else:
                return TestResult(success=False, message=f"API error: {response.status_code}")
        except ImportError:
            return TestResult(success=False, message="httpx not available for validation")
        except Exception as e:
            return TestResult(success=False, message=f"Connection error: {e}")


# Validator registry
VALIDATORS: dict[str, type[CredentialValidator]] = {
    # AI Providers
    "openai": OpenAIValidator,
    "anthropic": AnthropicValidator,
    "openrouter": OpenRouterValidator,
    "groq": GroqValidator,
    "github_models": GitHubModelsValidator,
    "copilot": GitHubModelsValidator,
    "xai": XAIValidator,
    "nvidia": NvidiaValidator,
    "deepgram": DeepgramValidator,
    "elevenlabs": ElevenLabsValidator,
    "telegram": TelegramValidator,
    # Version Control
    "github": GitHubValidator,
    "gitlab": GitLabValidator,
    # Email
    "gmail": EmailValidator,
    "outlook": EmailValidator,
    "fastmail": EmailValidator,
    "email": EmailValidator,
    "imap": EmailValidator,
    # Project Management
    "jira": JiraValidator,
}


def get_validator(provider: str) -> CredentialValidator:
    """
    Get the appropriate validator for a provider.

    Args:
        provider: Provider name (case-insensitive)

    Returns:
        CredentialValidator instance for the provider
    """
    validator_class = VALIDATORS.get(provider.lower(), GenericValidator)
    return validator_class()


def list_supported_validators() -> list[str]:
    """Get list of providers with specific validators."""
    return sorted(VALIDATORS.keys())
