"""
NAVIG AI Client - Wrapper for AI API calls

Provides a unified interface for AI/LLM calls with:
- Hybrid 3-tier routing (small / big / coder_big)
- Pluggable providers per model slot (Ollama, OpenRouter, OpenAI, llama.cpp)
- Automatic fallback (small → big/coder_big) with quality check
- Telemetry logging for every routed request
- User tier overrides (/big, /small, /coder)
- Backward-compatible: single-provider mode when routing disabled
"""

import logging
import os
import threading
import time
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Lazy import for aiohttp
aiohttp = None


@dataclass
class AIMessage:
    role: str  # 'system', 'user', 'assistant'
    content: str
    
    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


class AIClient:
    """
    AI client for NAVIG that uses the existing providers system.
    
    Uses settings from ~/.navig/config.yaml:
    - openrouter_api_key
    - ai_model_preference[]
    - airllm_model_path (for local inference)
    - airllm_compression
    
    Prioritizes:
    1. OpenRouter if API key is set
    2. AirLLM for local inference if configured
    3. Falls back to pattern matching
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider: str = "auto",  # 'auto', 'openrouter', 'openai', 'airllm', 'local'
        base_url: Optional[str] = None,
    ):
        # Load from NAVIG config first
        self._load_navig_config()
        
        # Override with explicit params if provided
        self.api_key = api_key or self._navig_api_key
        self.model = model or self._navig_model
        self.provider = provider
        self.base_url = base_url
        
        # Determine best provider if auto
        if provider == "auto":
            self.provider = self._detect_best_provider()
            
        self._session = None
        self._fallback_manager = None

        # Model router (hybrid 3-tier routing)
        self._model_router = None
        self._init_model_router()

    def _init_model_router(self):
        """Initialize hybrid model router from config."""
        try:
            from navig.agent.model_router import HybridRouter
            from navig.config import get_config_manager
            config = get_config_manager()
            self._model_router = HybridRouter.from_config(
                config.global_config, default_model=self.model
            )
            if self._model_router.is_active:
                summary = self._model_router.status_summary()
                models = summary.get("models", {})
                logger.info(
                    "Hybrid router active: mode=%s | small=%s/%s | big=%s/%s | coder=%s/%s",
                    summary.get("mode", "?"),
                    models.get("small", {}).get("provider", "?"),
                    models.get("small", {}).get("model", "?"),
                    models.get("big", {}).get("provider", "?"),
                    models.get("big", {}).get("model", "?"),
                    models.get("coder_big", {}).get("provider", "?"),
                    models.get("coder_big", {}).get("model", "?"),
                )
        except Exception as e:
            logger.warning("Model router not initialized: %s", e)
            self._model_router = None

    @property
    def model_router(self):
        """Access the model router (may be None if not configured)."""
        return self._model_router

    def _load_navig_config(self):
        """Load API keys and model preferences from NAVIG config."""
        self._navig_api_key = None
        self._navig_model = "google/gemini-2.5-flash"
        self._airllm_config = None
        
        try:
            from navig.config import get_config_manager
            config = get_config_manager()
            
            # Try vault first (updates last_used_at for auditing)
            api_key = None
            try:
                from navig.vault import get_vault
                vault = get_vault()
                secret = vault.get_secret("openrouter", "api_key", caller="ai_client")
                if secret:
                    api_key = secret.reveal().strip()
            except Exception:
                pass
            
            # Fall back to config
            if not api_key:
                api_key = config.global_config.get('openrouter_api_key', '')
            if api_key and api_key.strip():
                self._navig_api_key = api_key.strip()
            
            # Get model preference
            models = config.global_config.get('ai_model_preference', [])
            
            # Handle JSON string format
            if isinstance(models, str):
                try:
                    import json
                    models = json.loads(models)
                except Exception:
                    models = [models]  # Treat as single model string
            
            if models and isinstance(models, list) and len(models) > 0:
                self._navig_model = models[0]
                
            # Get AirLLM config
            airllm_model = config.global_config.get('airllm_model_path')
            airllm_compression = config.global_config.get('airllm_compression')
            if airllm_model:
                self._airllm_config = {
                    'model_path': airllm_model,
                    'compression': airllm_compression,
                }
                
        except Exception:
            # Fall back to env vars
            self._navig_api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
            
    def _detect_best_provider(self) -> str:
        """Detect the best available provider.
        
        Priority: mcp_forge → github_models → openrouter → airllm → local → none
        """
        # ⓪ MCP Forge — VS Code Copilot via MCP WebSocket (preferred)
        mcp_forge_url = self._get_forge_mcp_url()
        if mcp_forge_url:
            try:
                import socket
                from urllib.parse import urlparse
                parsed = urlparse(mcp_forge_url.replace("ws://", "http://").replace("wss://", "https://"))
                host = parsed.hostname or "127.0.0.1"
                port = parsed.port or 42070
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                result = sock.connect_ex((host, port))
                sock.close()
                if result == 0:
                    return "mcp_forge"
            except Exception:
                pass

        # ① GitHub Models — free tier, needs GITHUB_TOKEN
        gh_token = self._get_github_models_token()
        if gh_token:
            return "github_models"

        # ③ OpenRouter if API key is set
        if self._navig_api_key:
            return "openrouter"
            
        # Check for AirLLM
        if self._airllm_config:
            try:
                from navig.providers import is_airllm_available
                if is_airllm_available():
                    return "airllm"
            except ImportError:
                pass
                
        # Check for Ollama/local
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('localhost', 11434))
            sock.close()
            if result == 0:
                return "local"
        except Exception:
            pass
            
        # Default to pattern matching (no LLM available)
        return "none"

    def _get_forge_mcp_url(self) -> str:
        """Read Forge MCP WebSocket URL from bridge-grid.json, config, env, or default.

        Priority:
        1. bridge-grid.json live llm_port  (written by navig-bridge heartbeat)
        2. NAVIG_FORGE_MCP_URL env var
        3. forge.mcp_url in ~/.navig/config.yaml
        4. Hardcoded default ws://127.0.0.1:42070
        """
        # ① Live port from navig-bridge heartbeat file (most reliable)
        try:
            from navig.providers.bridge_grid_reader import get_llm_port
            live_port = get_llm_port()
            if live_port:
                return f"ws://127.0.0.1:{live_port}"
        except Exception:
            pass

        # ② Explicit env override
        env_url = os.getenv("NAVIG_FORGE_MCP_URL")
        if env_url:
            return env_url

        # ③ Config file
        try:
            from navig.config import get_config_manager
            cfg = get_config_manager().global_config or {}
            forge_cfg = cfg.get("forge", {})
            url = forge_cfg.get("mcp_url")
            if url:
                return url
        except Exception:
            pass

        return "ws://127.0.0.1:42070"

    def _get_forge_token(self) -> str:
        """Read Forge LLM bearer token from config or env."""
        env_tok = os.getenv("NAVIG_FORGE_LLM_TOKEN")
        if env_tok:
            return env_tok
        try:
            from navig.config import get_config_manager
            cfg = get_config_manager().global_config or {}
            return cfg.get("forge", {}).get("token", "")
        except Exception:
            return ""

    def _get_github_models_token(self) -> str:
        """Read GitHub Models token from vault, config, or env."""
        # ① Environment variable
        token = os.getenv("GITHUB_TOKEN", "")
        if token:
            return token
        # ② Vault
        try:
            from navig.vault import get_vault
            vault = get_vault()
            secret = vault.get_secret("github_models", "token", caller="ai_client")
            if secret:
                val = secret.reveal().strip() if hasattr(secret, "reveal") else str(secret).strip()
                if val:
                    return val
        except Exception:
            pass
        # ③ Config file
        try:
            from navig.config import get_config_manager
            cfg = get_config_manager().global_config or {}
            return cfg.get("github_models", {}).get("token", "")
        except Exception:
            return ""
        
    def _get_fallback_manager(self):
        """Lazy-load the fallback manager for multi-provider support."""
        if self._fallback_manager is None:
            try:
                from navig.providers import FallbackManager
                self._fallback_manager = FallbackManager()
            except ImportError:
                pass
        return self._fallback_manager
        
    async def _get_session(self):
        global aiohttp
        if aiohttp is None:
            import aiohttp as _aiohttp
            aiohttp = _aiohttp
            
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session
        
    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
            
    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        model: Optional[str] = None,
    ) -> str:
        """
        Send chat completion request.

        Args:
            messages: Chat messages in OpenAI format.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            model: Optional model override (used by router).
        """
        
        # Try using the full provider system first
        if self.provider in ("openrouter", "openai", "airllm", "mcp_forge", "github_models"):
            fallback_mgr = self._get_fallback_manager()
            if fallback_mgr:
                try:
                    return await self._chat_with_providers(
                        fallback_mgr, messages, temperature, max_tokens
                    )
                except Exception:
                    if self.provider == "airllm":
                        # AirLLM error - try direct
                        return await self._chat_airllm(messages, temperature, max_tokens)
                        
        # Direct API call for OpenRouter/OpenAI
        if self.provider in ("openrouter", "openai") and self.api_key:
            return await self._chat_api(messages, temperature, max_tokens)
            
        # AirLLM local inference
        if self.provider == "airllm":
            return await self._chat_airllm(messages, temperature, max_tokens)
            
        # Ollama/local
        if self.provider == "local":
            return await self._chat_local(
                messages, temperature, max_tokens, model=model
            )

        # MCP Forge — VS Code Copilot via MCP WebSocket (preferred)
        if self.provider == "mcp_forge":
            return await self._chat_mcp_forge(messages, temperature, max_tokens, model=model)

        # GitHub Models — free tier via GitHub PAT
        if self.provider == "github_models":
            return await self._chat_github_models(messages, temperature, max_tokens, model=model)
            
        # No provider available
        raise RuntimeError(
            "No AI provider available. Set openrouter_api_key in ~/.navig/config.yaml, "
            "GITHUB_TOKEN for GitHub Models, or configure AirLLM for local inference."
        )

    async def chat_routed(
        self,
        messages: List[Dict[str, str]],
        user_message: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tier_override: str = "",
    ) -> str:
        """
        Chat with automatic hybrid model routing.

        Routes to the best tier (small / big / coder_big) based on message
        content, then dispatches to the correct provider (local Ollama,
        remote OpenRouter, etc.)

        Falls back to standard ``chat()`` if routing is not configured.

        Args:
            tier_override: "small", "big", "coder_big" — skip rules.
        """
        from navig.agent.model_router import RoutingTelemetry, FALLBACK_NOTE

        router = self._model_router
        msg_text = user_message or self._extract_user_message(messages)
        telemetry = RoutingTelemetry(user_override=tier_override)

        # No router or not active → plain chat
        if not router or not router.is_active:
            return await self.chat(messages, temperature, max_tokens)

        # ── Routing decision ──
        t0 = time.monotonic()
        if router.cfg.mode == "router_llm_json":
            decision = await router.route_async(
                msg_text,
                tier_override=tier_override,
                chat_fn=self._chat_local_with_model,
            )
        else:
            decision = router.route(msg_text, tier_override=tier_override)

        telemetry.selected_tier = decision.tier
        telemetry.routing_reason = decision.reason
        telemetry.provider = decision.provider
        telemetry.model = decision.model
        telemetry.max_tokens_used = decision.max_tokens

        logger.info(
            "Route: tier=%s provider=%s model=%s reason=%s",
            decision.tier, decision.provider, decision.model, decision.reason,
        )

        # ── Execute via provider ──
        try:
            response_text = await self._execute_routed(
                messages, decision, temperature
            )
        except Exception as exc:
            logger.error("Routed call failed (%s), falling back to default chat", exc)
            return await self.chat(messages, temperature, max_tokens)

        # ── Fallback ──
        if router.should_fallback(response_text, decision):
            fb_decision = router.fallback_decision(decision, msg_text)
            logger.info(
                "Fallback: %s → %s (%s/%s)",
                decision.tier, fb_decision.tier,
                fb_decision.provider, fb_decision.model,
            )
            telemetry.fallback_occurred = True
            telemetry.fallback_reason = f"{decision.tier}_low_quality"

            # Prepend retry note to system message
            fb_messages = list(messages)
            if fb_messages and fb_messages[0].get("role") == "system":
                fb_messages[0] = {
                    "role": "system",
                    "content": fb_messages[0]["content"] + "\n\n" + FALLBACK_NOTE,
                }
            else:
                fb_messages.insert(0, {"role": "system", "content": FALLBACK_NOTE})

            try:
                response_text = await self._execute_routed(
                    fb_messages, fb_decision, temperature
                )
                telemetry.selected_tier = fb_decision.tier
                telemetry.provider = fb_decision.provider
                telemetry.model = fb_decision.model
            except Exception as exc:
                logger.error("Fallback call failed (%s)", exc)

        telemetry.latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "Telemetry: tier=%s provider=%s model=%s latency=%dms fallback=%s",
            telemetry.selected_tier, telemetry.provider, telemetry.model,
            telemetry.latency_ms, telemetry.fallback_occurred,
        )

        return response_text

    async def _execute_routed(
        self,
        messages: List[Dict[str, str]],
        decision,
        temperature: float,
    ) -> str:
        """
        Execute a routed LLM call.

        If the router has a provider pool (HybridRouter.call), use it.
        Otherwise fall back to the appropriate provider via self.chat().
        """
        router = self._model_router

        # Try HybridRouter.call() (uses llm_providers)
        try:
            resp = await router.call(messages, decision)
            return resp.content
        except Exception as e:
            logger.debug("HybridRouter.call failed (%s), trying legacy path", e)

        # Legacy fallback: route to appropriate provider-specific method
        provider = decision.provider or self.provider
        if provider == "github_models":
            return await self._chat_github_models(
                messages,
                temperature=decision.temperature,
                max_tokens=decision.max_tokens,
                model=decision.model,
            )
        elif provider in ("openrouter", "openai"):
            return await self._chat_api(
                messages,
                temperature=decision.temperature,
                max_tokens=decision.max_tokens,
            )
        elif provider == "mcp_forge":
            # Map routing tier to purpose hint for navig-bridge model selection
            tier = getattr(decision, 'tier', '')
            purpose_map = {'small': 'small_talk', 'big': 'big_tasks', 'coder_big': 'coding'}
            purpose = purpose_map.get(tier, '')
            return await self._chat_mcp_forge(
                messages,
                temperature=decision.temperature,
                max_tokens=decision.max_tokens,
                model=decision.model,
                purpose=purpose or None,
            )
        # Default: try local (Ollama)
        return await self._chat_local(
            messages,
            temperature=decision.temperature,
            max_tokens=decision.max_tokens,
            model=decision.model,
        )

    async def _chat_local_with_model(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 200,
        model: Optional[str] = None,
    ) -> str:
        """Convenience wrapper used by the LLM-based router."""
        return await self._chat_local(
            messages, temperature, max_tokens, model=model
        )

    @staticmethod
    def _extract_user_message(messages: List[Dict[str, str]]) -> str:
        """Extract the last user message from the messages list."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""
            
    async def _chat_with_providers(
        self,
        fallback_mgr,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Use the full provider system with fallback."""
        from navig.providers import Message, CompletionRequest
        
        # Convert messages
        provider_messages = [
            Message(role=m["role"], content=m["content"]) 
            for m in messages
        ]
        
        request = CompletionRequest(
            messages=provider_messages,
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        
        # Run with fallback
        result = await fallback_mgr.run_with_fallback(request)
        return result.response.content
        
    async def _chat_api(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Direct API call to OpenRouter/OpenAI."""
        session = await self._get_session()
        
        if self.provider == "openrouter":
            base_url = self.base_url or "https://openrouter.ai/api/v1"
        else:
            base_url = self.base_url or "https://api.openai.com/v1"
            
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://navig.run"
            headers["X-Title"] = "NAVIG Autonomous Agent"
            
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        try:
            async with session.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120, connect=10),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(f"AI API error ({response.status}): {error_text}")
                    
                data = await response.json()
                return data["choices"][0]["message"]["content"]
                
        except Exception as e:
            raise RuntimeError(f"AI request failed: {e}")
            
    async def _chat_airllm(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Use AirLLM for local inference."""
        try:
            from navig.providers.airllm import AirLLMClient, AirLLMConfig
            from navig.providers import Message, CompletionRequest
            
            # Create config from NAVIG settings
            config = AirLLMConfig(
                model_path=self._airllm_config.get('model_path'),
                compression=self._airllm_config.get('compression'),
            )
            
            client = AirLLMClient(airllm_config=config)
            
            # Convert messages
            provider_messages = [
                Message(role=m["role"], content=m["content"]) 
                for m in messages
            ]
            
            request = CompletionRequest(
                messages=provider_messages,
                model=config.model_path,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            result = await client.complete(request)
            return result.content
            
        except Exception as e:
            raise RuntimeError(f"AirLLM inference failed: {e}")
            
    async def _chat_mcp_forge(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> str:
        """Route through the MCP Forge bridge (VS Code Copilot via MCP WebSocket).

        Args:
            purpose: Task purpose hint (coding/small_talk/big_tasks/summarize/research).
                     When set, navig-bridge picks the optimal Copilot model automatically.
        """
        from navig.agent.llm_providers import McpForgeProvider

        mcp_url = self._get_forge_mcp_url()
        mcp_token = self._get_forge_token()

        provider = McpForgeProvider(base_url=mcp_url, api_key=mcp_token)
        try:
            resp = await provider.chat(
                model=model or "",
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                purpose=purpose,
            )
            return resp.content
        finally:
            await provider.close()

    async def _chat_github_models(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
    ) -> str:
        """Route through GitHub Models (Azure AI Inference)."""
        from navig.agent.llm_providers import GitHubModelsProvider

        token = self._get_github_models_token()
        provider = GitHubModelsProvider(api_key=token)
        try:
            resp = await provider.chat(
                model=model or "gpt-4o",
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.content
        finally:
            await provider.close()

    async def _chat_local(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
    ) -> str:
        """Use local Ollama or LM Studio."""
        session = await self._get_session()
        
        base_url = self.base_url or "http://localhost:11434/v1"
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        try:
            async with session.post(
                f"{base_url}/chat/completions",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120, connect=10),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(f"Local AI error ({response.status}): {error_text}")
                    
                data = await response.json()
                return data["choices"][0]["message"]["content"]
                
        except Exception as e:
            raise RuntimeError(f"Local AI request failed: {e}")
            
    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> str:
        """Simple completion with optional system prompt."""
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            
        messages.append({"role": "user", "content": prompt})
        
        return await self.chat(messages, **kwargs)
        
    def is_available(self) -> bool:
        """Check if any AI provider is available."""
        return self.provider != "none"

    def re_detect_provider(self) -> str:
        """
        Re-run provider detection.  Called when network conditions change
        (e.g. reverse SSH tunnel established, VS Code opened).
        Returns the newly selected provider name.
        """
        old = self.provider
        self.provider = self._detect_best_provider()
        if self.provider != old:
            logger.info("Provider re-detected: %s → %s", old, self.provider)
        return self.provider


# Singleton instance
_default_client: Optional[AIClient] = None
_default_client_lock = threading.Lock()


def get_ai_client() -> AIClient:
    """Get or create default AI client (thread-safe)."""
    global _default_client
    if _default_client is None:
        with _default_client_lock:
            if _default_client is None:
                _default_client = AIClient()
    return _default_client


async def quick_chat(message: str, system: Optional[str] = None) -> str:
    """Quick one-shot chat."""
    client = get_ai_client()
    return await client.complete(message, system_prompt=system)
