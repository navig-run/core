import logging
import os
import sys

logger = logging.getLogger(__name__)

REQUIRED_ENV_VARS = {
    # If using LLM via standard OpenAI/OpenRouter (we check if ONE of these exists)
    "LLM_KEYS": {
        "vars": ["OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
        "desc": "At least one AI provider API key must be defined to use navig intelligence features.",
        "type": "any",
    }
}


def validate_environment():
    """Validates that required environment variables are present before startup."""
    errors = []

    for key_group, config in REQUIRED_ENV_VARS.items():
        if config["type"] == "any":
            if not any(os.getenv(var) for var in config["vars"]):
                errors.append(
                    f"Missing required environment variable group {key_group}.\n"
                    f"Purpose: {config['desc']}\n"
                    f"Resolution: Please define at least one of {config['vars']} in your environment or .env file."
                )

    if errors:
        for err in errors:
            print(f"Environment Verification Failed: {err}", file=sys.stderr)
        raise RuntimeError(
            "Startup aborted due to missing REQUIRED environment variables."
        )


if __name__ == "__main__":
    validate_environment()
