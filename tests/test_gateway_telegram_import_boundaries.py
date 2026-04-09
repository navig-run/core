from __future__ import annotations

import ast
from pathlib import Path


def test_gateway_telegram_imports_stay_within_boundary() -> None:
    """Prevent new hard coupling: gateway files outside Telegram modules must not import Telegram directly."""

    repo_root = Path(__file__).resolve().parents[1]
    gateway_dir = repo_root / "navig" / "gateway"
    channels_dir = gateway_dir / "channels"

    allowed_files = {
        gateway_dir / "routes" / "__init__.py",
        gateway_dir / "routes" / "telegram_webhook.py",
    }

    violations: list[str] = []

    for py_file in gateway_dir.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue

        if py_file.is_relative_to(channels_dir):
            continue
        if py_file in allowed_files:
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    segments = alias.name.split(".")
                    if segments and segments[0] in {"telegram", "telegram_bot"}:
                        rel = py_file.relative_to(repo_root).as_posix()
                        violations.append(f"{rel}:{node.lineno} import {alias.name}")

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                module_segments = module.split(".") if module else []
                is_internal_telegram_module = module.startswith("navig.gateway.channels.telegram")
                if (
                    module_segments
                    and module_segments[0] in {"telegram", "telegram_bot"}
                    and not is_internal_telegram_module
                ):
                    rel = py_file.relative_to(repo_root).as_posix()
                    violations.append(f"{rel}:{node.lineno} from {module}")
                    continue

                for alias in node.names:
                    is_gateway_scope = module.startswith(
                        "navig.gateway.routes"
                    ) or module.startswith("navig.gateway.channels")
                    if is_gateway_scope and (
                        alias.name == "telegram" or alias.name.startswith("telegram_")
                    ):
                        rel = py_file.relative_to(repo_root).as_posix()
                        scope = module or "."
                        violations.append(f"{rel}:{node.lineno} from {scope} import {alias.name}")

    assert not violations, (
        "Gateway Telegram import boundary violated (imports must stay in "
        "navig/gateway/channels/* or gateway Telegram route modules):\n"
        + "\n".join(sorted(violations))
    )
