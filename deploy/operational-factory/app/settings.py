import os


def get_env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


DATABASE_URL = get_env("DATABASE_URL", "postgresql+psycopg2://navig:navig@postgres:5432/navig_factory")
REDIS_URL = get_env("REDIS_URL", "redis://:navig@redis:6379/0")
OLLAMA_BASE_URL = get_env("OLLAMA_BASE_URL", "http://ollama:11434")
TOOL_GATEWAY_URL = get_env("TOOL_GATEWAY_URL", "http://tool-gateway:8090")
RUNTIME_URL = get_env("RUNTIME_URL", "http://navig-runtime:8091")
SANDBOX_URL = get_env("SANDBOX_URL", "http://sandbox-runner:8092")

DEFAULT_TENANT = get_env("DEFAULT_TENANT", "solo-company")
DEFAULT_USER_EMAIL = get_env("DEFAULT_USER_EMAIL", "owner@example.com")
APPROVER_EMAIL = get_env("APPROVER_EMAIL", DEFAULT_USER_EMAIL)

SAFE_RATE_LIMIT = int(get_env("GATEWAY_SAFE_RATE_LIMIT", "30"))
RESTRICTED_RATE_LIMIT = int(get_env("GATEWAY_RESTRICTED_RATE_LIMIT", "5"))
RATE_WINDOW_SECONDS = int(get_env("GATEWAY_RATE_WINDOW_SECONDS", "60"))

REPO_PATH = get_env("REPO_PATH", "/workspace/repo")
PRIMARY_MODEL = get_env("PRIMARY_MODEL", "qwen2.5:7b-instruct")
CODE_MODEL = get_env("CODE_MODEL", "deepseek-coder:6.7b")
