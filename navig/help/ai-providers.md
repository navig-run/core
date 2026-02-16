# AI Providers

NAVIG supports multiple AI providers with automatic fallback.

## Configured Providers

Built-in providers:
- `openai` — OpenAI (GPT-4, GPT-4o, etc.)
- `anthropic` — Anthropic Claude
- `openrouter` — OpenRouter (access to many models)
- `ollama` — Local Ollama models
- `groq` — Groq (fast inference)
- `airllm` — Local inference for 70B+ models on limited VRAM

## AirLLM (Local Large Model Inference)

AirLLM enables running 70B+ parameter models on hardware with limited VRAM (4-8GB)
through layer-wise inference and model sharding.

**Installation:**
```bash
pip install airllm
```

**Configuration:**
```bash
# View status and configuration
navig ai airllm --status

# Configure model path
navig ai airllm --configure --model-path meta-llama/Llama-3.3-70B-Instruct

# Enable 4-bit compression for lower VRAM
navig ai airllm --configure --compression 4bit --max-vram 8

# Test AirLLM
navig ai airllm --test
```

**Environment Variables:**
- `AIRLLM_MODEL_PATH` — HuggingFace model ID or local path
- `AIRLLM_MAX_VRAM_GB` — Maximum VRAM to use (default: 8)
- `AIRLLM_COMPRESSION` — '4bit', '8bit', or empty for none
- `AIRLLM_DEVICE` — 'cuda', 'cpu', or 'mps' (macOS)
- `HF_TOKEN` — HuggingFace token for gated models

**VRAM Recommendations:**
- 7B-13B models: 4GB VRAM minimum
- 33B models: 4GB with 4bit compression
- 70B models: 4GB with 4bit compression, 8GB+ recommended
- 405B models: 8GB with 4bit compression, 16GB+ recommended

## OAuth Providers

**⚠️ OAuth Currently Unavailable**

OAuth authentication requires provider-specific client registration. Currently, **no providers support OAuth** because:

- **OpenAI**: OAuth only available to enterprise partners (use API keys)
- **Anthropic**: Only supports API key authentication  
- **Others**: Would require registering NAVIG as an OAuth application

**Use API key authentication instead:**

```bash
# Add API key credential
navig cred add openai sk-... --type api-key
navig cred add anthropic sk-ant-... --type api-key

# Use the credentials
navig ai "your question" --provider openai
```

The OAuth framework is production-ready and will be enabled when we register with providers that support it.

## Commands

```bash
# List providers and status
navig ai providers

# Add API key for a provider
navig ai providers --add openai

# Remove API key
navig ai providers --remove openai

# Test provider connection
navig ai providers --test openai
```

**OAuth commands** (currently unavailable - see above):
```bash
# When OAuth becomes available:
# navig ai login <provider>
# navig ai logout <provider>
```

## Configuration

API keys and OAuth credentials are stored securely in `~/.navig/credentials/`.

You can also set keys via environment variables:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `OPENROUTER_API_KEY`

## Fallback

When a provider fails, NAVIG automatically tries the next one:

```yaml
# ~/.navig/config.yaml
ai_model_preference:
  - openai:gpt-4o-mini
  - anthropic:claude-3-haiku
  - openrouter:deepseek/deepseek-coder
```

## Examples

```bash
# Ask AI with specific model
navig ai ask "How do I optimize nginx?" --model gpt-4o

# Check which providers are configured
navig ai providers

# Add API keys for providers
navig ai providers --add openai
navig ai providers --add anthropic
```


