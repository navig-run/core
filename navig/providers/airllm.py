"""
NAVIG AI Providers - AirLLM Client

Enables running large LLMs (70B+) on limited VRAM through layer-wise inference.
Uses the airllm library for memory-efficient model loading and generation.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .clients import (
    BaseProviderClient,
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderError,
)
from .types import ModelApi, ModelCost, ModelDefinition, ProviderConfig

# Check if AirLLM is available
try:
    from airllm import AutoModel

    AIRLLM_AVAILABLE = True
except ImportError:
    AutoModel = None
    AIRLLM_AVAILABLE = False


@dataclass
class AirLLMConfig:
    """Configuration for AirLLM provider."""

    # Model source (HuggingFace ID or local path)
    model_path: str = ""

    # VRAM management
    max_vram_gb: float = 8.0

    # Compression mode: "4bit", "8bit", or None
    compression: str | None = None

    # Layer shards saving path (optional)
    layer_shards_path: str | None = None

    # HuggingFace token for gated models
    hf_token: str | None = None

    # Enable prefetching (overlap loading and compute)
    prefetching: bool = True

    # Delete original model after sharding (save disk space)
    delete_original: bool = False

    # Generation settings
    max_length: int = 4096
    max_new_tokens: int = 2048

    # Device settings
    device: str = "cuda"  # "cuda", "cpu", "mps" (for macOS)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AirLLMConfig":
        """Create config from dictionary."""
        return cls(
            model_path=data.get("model_path", ""),
            max_vram_gb=float(data.get("max_vram_gb", 8.0)),
            compression=data.get("compression"),
            layer_shards_path=data.get("layer_shards_path"),
            hf_token=data.get("hf_token"),
            prefetching=data.get("prefetching", True),
            delete_original=data.get("delete_original", False),
            max_length=int(data.get("max_length", 4096)),
            max_new_tokens=int(data.get("max_new_tokens", 2048)),
            device=data.get("device", "cuda"),
        )

    @classmethod
    def from_env(cls) -> "AirLLMConfig":
        """Create config from environment variables."""
        return cls(
            model_path=os.environ.get("AIRLLM_MODEL_PATH", ""),
            max_vram_gb=float(os.environ.get("AIRLLM_MAX_VRAM_GB", "8")),
            compression=os.environ.get("AIRLLM_COMPRESSION"),
            layer_shards_path=os.environ.get("AIRLLM_LAYER_SHARDS_PATH"),
            hf_token=os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN"),
            prefetching=os.environ.get("AIRLLM_PREFETCHING", "true").lower() == "true",
            delete_original=os.environ.get("AIRLLM_DELETE_ORIGINAL", "false").lower()
            == "true",
            max_length=int(os.environ.get("AIRLLM_MAX_LENGTH", "4096")),
            max_new_tokens=int(os.environ.get("AIRLLM_MAX_NEW_TOKENS", "2048")),
            device=os.environ.get("AIRLLM_DEVICE", "cuda"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "model_path": self.model_path,
            "max_vram_gb": self.max_vram_gb,
            "compression": self.compression,
            "layer_shards_path": self.layer_shards_path,
            "hf_token": self.hf_token,
            "prefetching": self.prefetching,
            "delete_original": self.delete_original,
            "max_length": self.max_length,
            "max_new_tokens": self.max_new_tokens,
            "device": self.device,
        }


class AirLLMClient(BaseProviderClient):
    """
    Client for AirLLM - memory-efficient large model inference.

    Features:
    - Run 70B+ models on 4-8GB VRAM via layer sharding
    - Support for 4bit/8bit quantization
    - HuggingFace model loading
    - Compatible with Llama, Qwen, Mistral, ChatGLM, Baichuan, etc.

    Note: This is a local inference provider - no API key required.
    """

    def __init__(
        self,
        config: ProviderConfig,
        airllm_config: AirLLMConfig | None = None,
        api_key: str | None = None,  # Unused, kept for interface compatibility
        timeout: float = 300.0,  # Longer timeout for local inference
    ):
        super().__init__(config, api_key=api_key, timeout=timeout)

        # AirLLM-specific configuration
        self.airllm_config = airllm_config or AirLLMConfig.from_env()

        # Model instance (lazy loaded)
        self._model = None
        self._current_model_path: str | None = None

    def _ensure_airllm(self) -> None:
        """Ensure AirLLM is available."""
        if not AIRLLM_AVAILABLE:
            raise ProviderError(
                message="AirLLM is not installed. Install with: pip install airllm",
                provider="airllm",
                error_type="missing_dependency",
                retryable=False,
            )

    def _load_model(self, model_path: str) -> Any:
        """
        Load a model using AirLLM.

        Args:
            model_path: HuggingFace model ID or local path

        Returns:
            Loaded AirLLM model
        """
        self._ensure_airllm()

        # Check if we already have this model loaded
        if self._model is not None and self._current_model_path == model_path:
            return self._model

        # Unload previous model if any
        if self._model is not None:
            del self._model
            self._model = None
            self._current_model_path = None

            # Force garbage collection to free VRAM
            import gc

            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass  # optional dependency not installed; feature disabled

        # Build model initialization kwargs
        model_kwargs = {}

        if self.airllm_config.compression:
            model_kwargs["compression"] = self.airllm_config.compression

        if self.airllm_config.layer_shards_path:
            model_kwargs["layer_shards_saving_path"] = (
                self.airllm_config.layer_shards_path
            )

        if self.airllm_config.hf_token:
            model_kwargs["hf_token"] = self.airllm_config.hf_token

        if self.airllm_config.prefetching:
            model_kwargs["prefetching"] = self.airllm_config.prefetching

        if self.airllm_config.delete_original:
            model_kwargs["delete_original"] = self.airllm_config.delete_original

        try:
            self._model = AutoModel.from_pretrained(model_path, **model_kwargs)
            self._current_model_path = model_path
            return self._model
        except Exception as e:
            raise ProviderError(
                message=f"Failed to load model '{model_path}': {e}",
                provider="airllm",
                error_type="model_load_error",
                retryable=False,
            ) from e

    def _format_prompt(self, messages: list[Message]) -> str:
        """
        Format messages into a prompt string.

        Uses a simple chat template compatible with most models.
        """
        parts = []

        for msg in messages:
            role = msg.role.upper()
            if role == "SYSTEM":
                parts.append(f"System: {msg.content}")
            elif role == "USER":
                parts.append(f"User: {msg.content}")
            elif role == "ASSISTANT":
                parts.append(f"Assistant: {msg.content}")
            else:
                parts.append(f"{role}: {msg.content}")

        # Add prompt for assistant response
        parts.append("Assistant:")

        return "\n\n".join(parts)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """
        Execute a chat completion using AirLLM.

        Note: AirLLM is synchronous, so we run in a thread pool.
        """
        self._ensure_airllm()

        # Determine model path from request
        model_path = request.model
        if not model_path:
            model_path = self.airllm_config.model_path

        if not model_path:
            raise ProviderError(
                message="No model specified. Set model_path in config or request.",
                provider="airllm",
                error_type="invalid_request",
                retryable=False,
            )

        # Run synchronous generation in thread pool
        def generate():
            model = self._load_model(model_path)

            # Format messages into prompt
            prompt_text = self._format_prompt(request.messages)

            # Tokenize
            input_tokens = model.tokenizer(
                [prompt_text],
                return_tensors="pt",
                return_attention_mask=False,
                truncation=True,
                max_length=self.airllm_config.max_length,
                padding=False,
            )

            # Determine max_new_tokens
            max_new_tokens = min(
                request.max_tokens or self.airllm_config.max_new_tokens,
                self.airllm_config.max_new_tokens,
            )

            # Move to device
            device = self.airllm_config.device
            if device == "cuda":
                try:
                    import torch

                    if torch.cuda.is_available():
                        input_ids = input_tokens["input_ids"].cuda()
                    else:
                        input_ids = input_tokens["input_ids"]
                        device = "cpu"
                except ImportError:
                    input_ids = input_tokens["input_ids"]
                    device = "cpu"
            elif device == "mps":
                try:
                    import torch

                    if torch.backends.mps.is_available():
                        input_ids = input_tokens["input_ids"].to("mps")
                    else:
                        input_ids = input_tokens["input_ids"]
                        device = "cpu"
                except ImportError:
                    input_ids = input_tokens["input_ids"]
                    device = "cpu"
            else:
                input_ids = input_tokens["input_ids"]

            # Generate
            generation_output = model.generate(
                input_ids,
                max_new_tokens=max_new_tokens,
                use_cache=True,
                return_dict_in_generate=True,
            )

            # Decode output
            output_text = model.tokenizer.decode(
                generation_output.sequences[0],
                skip_special_tokens=True,
            )

            # Extract assistant response (after our prompt)
            # Find where the assistant response starts
            assistant_marker = "Assistant:"
            if assistant_marker in output_text:
                # Find the last occurrence (our added marker)
                parts = output_text.rsplit(assistant_marker, 1)
                if len(parts) > 1:
                    response_text = parts[1].strip()
                else:
                    response_text = output_text
            else:
                # Just return everything after the input
                response_text = output_text[len(prompt_text) :].strip()

            # Calculate token counts
            input_token_count = input_ids.shape[-1]
            output_token_count = (
                generation_output.sequences[0].shape[-1] - input_token_count
            )

            return response_text, {
                "prompt_tokens": input_token_count,
                "completion_tokens": output_token_count,
                "total_tokens": input_token_count + output_token_count,
            }

        try:
            response_text, usage = await asyncio.to_thread(generate)

            return CompletionResponse(
                content=response_text,
                tool_calls=None,  # AirLLM doesn't support tool calling natively
                finish_reason="stop",
                usage=usage,
                model=model_path,
                provider="airllm",
            )
        except ProviderError:
            raise
        except Exception as e:
            error_msg = str(e)

            # Check for common errors
            if "CUDA out of memory" in error_msg or "OutOfMemoryError" in error_msg:
                raise ProviderError(
                    message=f"Out of VRAM. Try reducing max_tokens, using compression='4bit', or a smaller model. Error: {error_msg}",
                    provider="airllm",
                    error_type="oom",
                    retryable=False,
                ) from e

            raise ProviderError(
                message=f"Generation failed: {error_msg}",
                provider="airllm",
                error_type="generation_error",
                retryable=True,
            ) from e

    async def complete_stream(
        self, request: CompletionRequest
    ) -> AsyncIterator[CompletionResponse]:
        """
        Streaming is not natively supported by AirLLM.
        Falls back to non-streaming completion.
        """
        # AirLLM doesn't support streaming natively
        # Return single response
        response = await self.complete(request)
        yield response

    def get_available_models(self) -> list[ModelDefinition]:
        """
        Get list of suggested models for AirLLM.

        These are popular models known to work well with AirLLM.
        Users can use any HuggingFace model ID or local path.
        """
        return [
            ModelDefinition(
                id="meta-llama/Llama-3.3-70B-Instruct",
                name="Llama 3.3 70B Instruct",
                context_window=128000,
                max_tokens=4096,
                cost=ModelCost(input=0, output=0),  # Free (local)
            ),
            ModelDefinition(
                id="Qwen/Qwen2.5-72B-Instruct",
                name="Qwen 2.5 72B Instruct",
                context_window=32768,
                max_tokens=4096,
                cost=ModelCost(input=0, output=0),
            ),
            ModelDefinition(
                id="mistralai/Mixtral-8x7B-Instruct-v0.1",
                name="Mixtral 8x7B Instruct",
                context_window=32768,
                max_tokens=4096,
                cost=ModelCost(input=0, output=0),
            ),
            ModelDefinition(
                id="deepseek-ai/deepseek-coder-33b-instruct",
                name="DeepSeek Coder 33B",
                context_window=16384,
                max_tokens=4096,
                cost=ModelCost(input=0, output=0),
            ),
            ModelDefinition(
                id="meta-llama/Llama-3.1-405B",
                name="Llama 3.1 405B",
                context_window=128000,
                max_tokens=4096,
                cost=ModelCost(input=0, output=0),
            ),
        ]

    async def list_local_models(self) -> list[str]:
        """
        List models in the configured layer_shards_path.

        Returns list of model directory names that have been sharded.
        """
        models = []

        shards_path = self.airllm_config.layer_shards_path
        if shards_path and os.path.isdir(shards_path):
            for item in os.listdir(shards_path):
                item_path = os.path.join(shards_path, item)
                if os.path.isdir(item_path):
                    # Check if it looks like a sharded model
                    if any(
                        f.endswith(".safetensors") or f.endswith(".bin")
                        for f in os.listdir(item_path)
                    ):
                        models.append(item)

        return models

    async def close(self):
        """Clean up model and free resources."""
        if self._model is not None:
            del self._model
            self._model = None
            self._current_model_path = None

            # Force garbage collection
            import gc

            gc.collect()

            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass  # optional dependency not installed; feature disabled


def create_airllm_client(
    airllm_config: AirLLMConfig | None = None,
    timeout: float = 300.0,
) -> AirLLMClient:
    """
    Create an AirLLM client with the given configuration.

    Args:
        airllm_config: AirLLM-specific configuration
        timeout: Request timeout in seconds (default 300s for local inference)

    Returns:
        Configured AirLLM client
    """
    from .types import BUILTIN_PROVIDERS

    # Get the airllm provider config
    provider_config = BUILTIN_PROVIDERS.get("airllm")
    if not provider_config:
        # Create a minimal provider config
        provider_config = ProviderConfig(
            name="airllm",
            base_url="local://airllm",
            api=ModelApi.OPENAI_COMPLETIONS,
            auth_header=False,
            models=[],
            priority=60,
        )

    return AirLLMClient(
        config=provider_config,
        airllm_config=airllm_config,
        timeout=timeout,
    )


def is_airllm_available() -> bool:
    """Check if AirLLM is installed and available."""
    return AIRLLM_AVAILABLE


def get_airllm_vram_recommendations() -> dict[str, str]:
    """Get VRAM recommendations for different model sizes."""
    return {
        "7B models": "4GB VRAM minimum, 8GB recommended",
        "13B models": "4GB VRAM minimum, 8GB recommended",
        "33B models": "4GB VRAM with 4bit compression, 8GB recommended",
        "70B models": "4GB VRAM with 4bit compression, 8GB+ recommended",
        "405B models": "8GB VRAM with 4bit compression, 16GB+ recommended",
    }
