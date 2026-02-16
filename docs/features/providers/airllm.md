# AirLLM Provider

**Run 70B+ Parameter Models on 4-8GB VRAM**

AirLLM is a local inference provider that enables running large language models on hardware with limited VRAM through layer-wise inference and model sharding.

## Overview

AirLLM optimizes memory usage by loading model layers on-demand rather than keeping the entire model in VRAM. This allows:

- **70B models on 4GB VRAM** (with 4-bit compression)
- **405B models on 8GB VRAM** (with 4-bit compression)
- No quantization loss for full-precision inference
- Support for HuggingFace models and local model directories

## Installation

### 1. Install AirLLM Package

```bash
pip install airllm
```

### 2. Install PyTorch with CUDA (for GPU inference)

```bash
# For CUDA 12.1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# For CUDA 11.8
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

### 3. Optional: Install bitsandbytes for compression

```bash
pip install bitsandbytes
```

## Configuration

### Using CLI

```bash
# View current configuration
navig ai airllm --status

# Configure model path (HuggingFace ID or local path)
navig ai airllm --configure --model-path meta-llama/Llama-3.3-70B-Instruct

# Enable 4-bit compression for lower VRAM usage
navig ai airllm --configure --compression 4bit

# Set maximum VRAM
navig ai airllm --configure --max-vram 8

# Test configuration
navig ai airllm --test
```

### Using Environment Variables

```bash
export AIRLLM_MODEL_PATH="meta-llama/Llama-3.3-70B-Instruct"
export AIRLLM_MAX_VRAM_GB="8"
export AIRLLM_COMPRESSION="4bit"
export AIRLLM_DEVICE="cuda"
export HF_TOKEN="your-huggingface-token"  # For gated models
```

### Using config.yaml

```yaml
# ~/.navig/config.yaml
ai_providers:
  airllm:
    enabled: true
    model_path: "meta-llama/Llama-3.3-70B-Instruct"
    max_vram_gb: 8
    compression: "4bit"
    device: "cuda"

ai_model_preference:
  - openai:gpt-4o-mini
  - airllm:meta-llama/Llama-3.3-70B-Instruct  # Local fallback
```

## Hardware Requirements

### VRAM Recommendations

| Model Size | Min VRAM | Recommended | Compression |
|------------|----------|-------------|-------------|
| 7B         | 4GB      | 8GB         | None        |
| 13B        | 4GB      | 8GB         | None        |
| 33B        | 4GB      | 8GB         | 4bit        |
| 70B        | 4GB      | 8GB+        | 4bit        |
| 405B       | 8GB      | 16GB+       | 4bit        |

### System Requirements

- **GPU**: NVIDIA GPU with CUDA support (recommended)
- **CPU**: Also works on CPU (much slower)
- **macOS**: Apple Silicon supported via MPS
- **RAM**: Minimum 16GB system RAM recommended
- **Disk**: Model files can be 30-200GB depending on size

## Supported Models

AirLLM supports any HuggingFace-compatible model. Recommended models:

### General Purpose
- `meta-llama/Llama-3.3-70B-Instruct` - Best overall performance
- `Qwen/Qwen2.5-72B-Instruct` - Excellent for Chinese/English
- `mistralai/Mixtral-8x7B-Instruct-v0.1` - Good balance of speed/quality

### Code Generation
- `deepseek-ai/deepseek-coder-33b-instruct` - Excellent for coding
- `Qwen/Qwen2.5-Coder-32B-Instruct` - Strong coding capabilities

### Smaller Models (Faster)
- `meta-llama/Llama-3.2-8B-Instruct` - Fast, good quality
- `mistralai/Mistral-7B-Instruct-v0.2` - Very fast

## Usage

### CLI Usage

```bash
# Ask with AirLLM model
navig ai ask "Explain quantum computing" --model airllm:meta-llama/Llama-3.3-70B-Instruct

# Use in agent mode
navig agent start --model airllm:Qwen/Qwen2.5-72B-Instruct
```

### Python API

```python
from navig.providers import create_airllm_client, AirLLMConfig, CompletionRequest, Message
import asyncio

# Create client
config = AirLLMConfig(
    model_path="meta-llama/Llama-3.3-70B-Instruct",
    max_vram_gb=8,
    compression="4bit",
)
client = create_airllm_client(config)

# Make request
async def main():
    request = CompletionRequest(
        messages=[
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="What is the capital of France?"),
        ],
        model="meta-llama/Llama-3.3-70B-Instruct",
        max_tokens=500,
    )
    
    response = await client.complete(request)
    print(response.content)
    await client.close()

asyncio.run(main())
```

## Fallback Configuration

Add AirLLM as a fallback when cloud providers are unavailable:

```yaml
# ~/.navig/config.yaml
ai_model_preference:
  - openai:gpt-4o-mini          # Primary (fast, cloud)
  - anthropic:claude-3-haiku    # Secondary (cloud)
  - airllm:deepseek-ai/deepseek-coder-33b-instruct  # Local fallback
```

## Performance Tips

### 1. Use Compression

4-bit compression provides ~3x speedup with minimal quality loss:

```bash
navig ai airllm --configure --compression 4bit
```

### 2. Pre-shard Models

First-time model loading takes longer as AirLLM shards the model. Subsequent loads are faster.

### 3. Use Prefetching

Prefetching overlaps model loading with compute (~10% speedup):

```bash
export AIRLLM_PREFETCHING=true
```

### 4. Optimize Max Tokens

Shorter outputs are faster:

```bash
# In config or request
max_tokens: 500  # Instead of 4096
```

## Troubleshooting

### Out of Memory (OOM) Errors

```
RuntimeError: CUDA out of memory
```

**Solutions:**
1. Enable compression: `--compression 4bit`
2. Reduce `max_vram_gb` setting
3. Close other GPU applications
4. Use a smaller model

### Slow First-Time Loading

The first run takes longer because AirLLM:
1. Downloads the model from HuggingFace (if not cached)
2. Shards the model into layers

Subsequent runs use cached shards and are much faster.

### Model Not Found

```
Error: Could not find model 'xxx'
```

**Solutions:**
1. Check the model ID is correct on HuggingFace
2. For gated models, set `HF_TOKEN` environment variable
3. Use a local path if you have the model downloaded

### GPU Not Detected

```
Warning: CUDA not available, falling back to CPU
```

**Solutions:**
1. Install PyTorch with CUDA: `pip install torch --index-url https://download.pytorch.org/whl/cu121`
2. Verify CUDA installation: `nvidia-smi`
3. Check PyTorch CUDA: `python -c "import torch; print(torch.cuda.is_available())"`

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `AIRLLM_MODEL_PATH` | HuggingFace model ID or local path | (required) |
| `AIRLLM_MAX_VRAM_GB` | Maximum VRAM to use in GB | 8 |
| `AIRLLM_COMPRESSION` | Compression mode: `4bit`, `8bit`, or empty | none |
| `AIRLLM_DEVICE` | Device: `cuda`, `cpu`, or `mps` | cuda |
| `AIRLLM_PREFETCHING` | Enable prefetching | true |
| `AIRLLM_LAYER_SHARDS_PATH` | Custom path for model shards | (default cache) |
| `AIRLLM_MAX_LENGTH` | Maximum input length | 4096 |
| `AIRLLM_MAX_NEW_TOKENS` | Maximum output tokens | 2048 |
| `AIRLLM_DELETE_ORIGINAL` | Delete original model after sharding | false |
| `HF_TOKEN` | HuggingFace token for gated models | (optional) |

## Comparison with Other Providers

| Feature | AirLLM | Ollama | Cloud (OpenAI/Anthropic) |
|---------|--------|--------|--------------------------|
| Cost | Free | Free | Pay per token |
| Privacy | 100% local | 100% local | Data sent to cloud |
| Model size | 70B-405B | 7B-70B | Any |
| VRAM needed | 4-8GB | 8-48GB | None |
| Speed | Slower | Fast | Fast |
| Offline | ✓ | ✓ | ✗ |

## See Also

- [AI Providers Overview](../HANDBOOK.md#ai-providers)
- [Fallback Configuration](../HANDBOOK.md#provider-fallback)
- [AirLLM GitHub](https://github.com/lyogavin/airllm)
- [HuggingFace Models](https://huggingface.co/models)


