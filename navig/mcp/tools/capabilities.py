"""MCP bundle: what this install can do, and running one installed command.

Four read-only originals back the ``navig_capabilities`` router and one action backs
``navig_run``. ``navig_run`` executes an *installed* CLI command and nothing else: the
path must equal a command in the install's manifest, arguments are a list (never a shell
string), and its approval level comes from :func:`navig.capabilities.safety.classify` for
that exact path, via ``gate_target`` in :mod:`navig.mcp.routing`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

#: Output budget per stream; the agent gets the head and the tail, not 10 MB of log.
MAX_OUTPUT = 64_000
DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 1800
#: Strings that would make an argument act as a second command if it were ever passed to
#: a shell. ``navig_run`` never uses a shell; this is a second wall, not the first.
_MAX_ARGS = 64
_MAX_ARG_LEN = 4096

_TOOLS: dict[str, dict[str, Any]] = {
    "navig_capabilities_overview": {
        "name": "navig_capabilities_overview",
        "description": (
            "List the installed NAVIG plugins with their CLI verbs and command counts, and "
            "the accounts, keys and devices each can use now (social networks, mailboxes, "
            "media provider keys, phones). Presence only, never a secret. Returns plugins "
            "with accounts and states; use navig_capabilities_find for exact commands."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "description": "Re-run the account probes instead of the 60 s cache.",
                    "default": False,
                }
            },
        },
    },
    "navig_capabilities_find": {
        "name": "navig_capabilities_find",
        "description": (
            "Find the installed NAVIG commands that match a goal in plain words, e.g. "
            "'upload a clip to youtube' or 'remove an image background'. Read-only: it only "
            "searches the installed command list. Returns command paths with summary, "
            "owning plugin and approval level; pass one to navig_capabilities_command_help, "
            "then use navig_run_cli to execute it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The goal, in plain words."},
                "limit": {
                    "type": "integer",
                    "description": "Maximum commands to return (1-25).",
                    "default": 8,
                },
            },
            "required": ["query"],
        },
    },
    "navig_capabilities_command_help": {
        "name": "navig_capabilities_command_help",
        "description": (
            "Show the usage of one installed NAVIG command: arguments, options, whether it "
            "accepts --json and its approval level. Returns the usage line and option "
            "flags; the path must be exact, as returned by navig_capabilities_find."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Exact command path, e.g. 'navig social upload'.",
                }
            },
            "required": ["path"],
        },
    },
    "navig_capabilities_suggest": {
        "name": "navig_capabilities_suggest",
        "description": (
            "Suggest next steps for a file the agent produced or received (image, video, "
            "audio, fan-out brief), limited to installed commands and connected accounts: "
            "edit, re-score audio, review on Telegram, post per account. Returns ready "
            "steps with arguments and the steps a missing connection would unlock; run one "
            "with navig_run."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Path of the file to act on."}
            },
            "required": ["file"],
        },
    },
    "navig_run_cli": {
        "name": "navig_run_cli",
        "description": (
            "Run one installed NAVIG CLI command with a list of arguments and return its "
            "exit code, stdout and stderr (JSON parsed when the command printed JSON). The "
            "path must be an exact installed command from navig_capabilities_find; no "
            "shell is used. Publishing, sending and deleting commands require approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Exact installed command path, e.g. 'navig audio loudness'.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Arguments and options after the path, one per item.",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Seconds before the command is stopped (1-1800).",
                    "default": DEFAULT_TIMEOUT_S,
                },
            },
            "required": ["path"],
        },
    },
}


def register(server: Any) -> None:
    server.tools.update({name: dict(spec) for name, spec in _TOOLS.items()})
    server._tool_handlers.update(
        {
            "navig_capabilities_overview": _overview,
            "navig_capabilities_find": _find,
            "navig_capabilities_command_help": _command_help,
            "navig_capabilities_suggest": _suggest,
            "navig_run_cli": _run,
        }
    )
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update(
        {
            "navig_capabilities_overview": "safe",
            "navig_capabilities_find": "safe",
            "navig_capabilities_command_help": "safe",
            "navig_capabilities_suggest": "safe",
            # The static level is the worst case; gate_target resolves the real level of
            # the exact command per call (a status read is not gated, a post is).
            "navig_run_cli": "dangerous",
        }
    )
    # A command may run for minutes; keep the stdio reader free for cancellation.
    if hasattr(server, "_concurrent_tools"):
        server._concurrent_tools.add("navig_run_cli")


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"error": message, "isError": True, **extra}


def _overview(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.capabilities import overview
    from navig.capabilities.probes import clear_cache

    if args.get("refresh"):
        clear_cache()
    return overview()


def _find(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.capabilities import find

    query = str(args.get("query") or "").strip()
    if not query:
        return _error("query is required")
    try:
        limit = int(args.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    results = find(query, limit=limit)
    return {
        "query": query,
        "commands": results,
        "next": "navig_capabilities action=command_help path=<path>, then navig_run.",
    }


def _command_help(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.capabilities import command_help

    path = str(args.get("path") or "")
    found = command_help(path)
    if found is None:
        return _error(
            f"{path!r} is not an installed command",
            hint="Use navig_capabilities action=find to get exact paths.",
        )
    return found


def _suggest(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.capabilities import suggest

    file = str(args.get("file") or "").strip()
    if not file:
        return _error("file is required")
    return suggest(file)


def resolve_run_target(path: Any) -> tuple[str | None, str | None]:
    """(exact installed path, error) — the closed check ``navig_run`` and the gate share."""
    from navig.capabilities.registry import PATH_RE, get

    if not isinstance(path, str):
        return None, "path must be a string"
    normalised = " ".join(path.split())
    if not PATH_RE.match(normalised):
        return None, "path must look like 'navig <group> <command>' (lowercase words only)"
    if get(normalised) is None:
        return None, f"{normalised!r} is not an installed command; use navig_capabilities find"
    return normalised, None


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n… [{len(text) - MAX_OUTPUT} chars omitted] …\n{text[-half:]}"


def _run(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    path, problem = resolve_run_target(args.get("path"))
    if path is None:
        return _error(problem or "invalid path")
    raw_args = args.get("args") or []
    if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
        return _error("args must be a list of strings")
    if len(raw_args) > _MAX_ARGS or any(len(a) > _MAX_ARG_LEN or "\x00" in a for a in raw_args):
        return _error(f"at most {_MAX_ARGS} arguments of {_MAX_ARG_LEN} characters each")
    try:
        timeout = int(args.get("timeout_s") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    timeout = max(1, min(timeout, MAX_TIMEOUT_S))

    # `python -m navig` in THIS interpreter: the same install the manifest describes,
    # whatever is (or is not) on PATH.
    argv = [sys.executable, "-m", "navig", *path.split()[1:], *raw_args]
    from navig.platform.paths import invocation_cwd

    env = {
        **os.environ,
        "NO_COLOR": "1",
        "NAVIG_NONINTERACTIVE": "1",  # a confirmation prompt fails instead of hanging
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "NAVIG_INVOCATION_CWD": str(invocation_cwd()),
    }
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv list, closed path, no shell
            argv,
            cwd=str(invocation_cwd()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return _error(f"could not start {path}: {exc}")
    on_cancel = getattr(server, "on_cancel", None)
    if callable(on_cancel):
        on_cancel(proc.kill)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return _error(
            f"{path} did not finish within {timeout}s and was stopped",
            stdout=_clip(stdout or ""),
            stderr=_clip(stderr or ""),
        )
    result: dict[str, Any] = {
        "command": path,
        "args": raw_args,
        "exit_code": proc.returncode,
        "stdout": _clip(stdout or ""),
        "stderr": _clip(stderr or ""),
    }
    parsed = _maybe_json(stdout or "")
    if parsed is not None:
        result["json"] = parsed
        result.pop("stdout")
    if proc.returncode != 0:
        result["isError"] = True
        result["error"] = f"{path} exited with code {proc.returncode}"
    return result


def _maybe_json(text: str) -> Any:
    import json

    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None
