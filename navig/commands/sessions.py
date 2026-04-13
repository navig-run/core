"""
NAVIG Copilot Sessions — VS Code Copilot Chat Session Manager

Browse, search, export and delete your VS Code Copilot chat sessions
stored in the local workspaceStorage directory.

Usage:
    navig copilot sessions                        # list all sessions
    navig copilot sessions stats                  # storage statistics
    navig copilot sessions list [--workspace W]   # list with optional filter
    navig copilot sessions view SESSION_ID        # inspect a session
    navig copilot sessions search QUERY           # full-text search
    navig copilot sessions export                 # export all sessions
    navig copilot sessions delete SESSION_ID      # delete a session
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlparse

import typer

from navig.console_helper import format_bytes as _human_size
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

# ── Typer app ────────────────────────────────────────────────────────────────

sessions_app = typer.Typer(
    name="sessions",
    help="Browse and manage VS Code Copilot chat sessions",
    no_args_is_help=False,  # allow bare `navig copilot sessions` to list
)


# ── Data model ───────────────────────────────────────────────────────────────


@dataclass
class ChatTurn:
    role: str  # "user" | "assistant"
    text: str
    timestamp: str | None = None

    def short(self, n: int = 120) -> str:
        t = self.text.strip().replace("\n", " ")
        return t[:n] + "…" if len(t) > n else t


@dataclass
class ChatSession:
    session_id: str
    path: Path
    workspace_hash: str
    workspace_label: str
    created_at: datetime | None = None
    turns: list[ChatTurn] = field(default_factory=list)
    file_bytes: int = 0
    parse_error: str | None = None

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def user_count(self) -> int:
        return sum(1 for t in self.turns if t.role == "user")

    @property
    def first_user_message(self) -> str:
        for t in self.turns:
            if t.role == "user":
                return t.text.strip()[:200]
        return ""

    @property
    def created_str(self) -> str:
        if self.created_at:
            return self.created_at.strftime("%Y-%m-%d %H:%M")
        return "unknown"

    def full_text(self) -> str:
        parts = [f"[{t.role.upper()}] {t.text}" for t in self.turns]
        return "\n\n".join(parts)


# ── Workspace / storage discovery ────────────────────────────────────────────


def _workspace_storage_root() -> Path:
    """Return the VS Code workspaceStorage directory."""
    from navig.platform.paths import current_os, home_dir

    home = home_dir()
    os_name = current_os()

    if os_name == "windows":
        appdata = os.environ.get("APPDATA", "").strip()
        base = Path(appdata).expanduser() if appdata else (home / "AppData" / "Roaming")
        return base / "Code" / "User" / "workspaceStorage"

    if os_name == "macos":
        return home / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage"

    xdg_config = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg_config).expanduser() if xdg_config else (home / ".config")
    return base / "Code" / "User" / "workspaceStorage"


def _workspace_label(ws_path: Path) -> str:
    """Best-effort human label for a workspace hash directory."""
    try:
        wf = ws_path / "workspace.json"
        if wf.exists():
            data = json.loads(wf.read_text(encoding="utf-8", errors="replace"))
            folder = data.get("folder", "")
            if folder:
                folder = unquote(folder)
                parsed = urlparse(folder)
                folder_path = unquote(parsed.path) if parsed.scheme else folder
                if parsed.scheme == "file" and parsed.netloc and not folder_path.startswith("//"):
                    folder_path = f"//{parsed.netloc}{folder_path}"
                if "\\" in folder_path or ":" in folder_path[:3]:
                    name = PureWindowsPath(folder_path).name
                else:
                    name = PurePosixPath(folder_path).name
                return name or folder[-40:]
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    return ws_path.name[:12]


def _discover_all_sessions(*, fast: bool = False) -> list[ChatSession]:
    """Scan all workspaceStorage hashes and return every chat session found.

    fast=True  — metadata-only scan (~100x faster, fine for list/stats).
    fast=False — full parse including message text (needed for search/view).
    """
    root = _workspace_storage_root()
    if not root.exists():
        return []

    results: list[ChatSession] = []
    for ws_dir in sorted(root.iterdir()):
        if not ws_dir.is_dir():
            continue
        chat_dir = ws_dir / "chatSessions"
        if not chat_dir.exists():
            continue
        label = _workspace_label(ws_dir)
        for jsonl in sorted(chat_dir.glob("*.jsonl")):
            s = _parse_session(jsonl, ws_dir.name, label, fast=fast)
            results.append(s)

    # Sort newest first
    results.sort(
        key=lambda s: s.created_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return results


def _parse_session(
    path: Path,
    ws_hash: str,
    ws_label: str,
    *,
    fast: bool = False,
) -> ChatSession:
    """Parse a single .jsonl file into a ChatSession.

    fast=True  — reads only the first line for metadata + counts lines for an
                 approximate turn estimate. O(file-size) but only one readline
                 + a cheap line-counter; no JSON parsing beyond the header.
                 Use for `list` and `stats`.
    fast=False — full parse including all message text.
                 Use for `view` and `search`.
    """
    session_id = path.stem
    sess = ChatSession(
        session_id=session_id,
        path=path,
        workspace_hash=ws_hash,
        workspace_label=ws_label,
        file_bytes=path.stat().st_size if path.exists() else 0,
    )

    if fast:
        # ── Fast path: read only the first (header) line ──────────────────────
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                header_line = fh.readline()
                # Count remaining non-empty lines as a proxy for turn count
                turn_estimate = sum(1 for ln in fh if ln.strip())

            header_line = header_line.strip()
            if header_line:
                try:
                    obj = json.loads(header_line)
                    if obj.get("kind") == 0:
                        v = obj.get("v", {})
                        if isinstance(v, dict):
                            sess.session_id = v.get("sessionId", session_id)
                            raw_ts = v.get("creationDate", "")
                            if raw_ts:
                                try:
                                    if isinstance(raw_ts, (int, float)):
                                        sess.created_at = datetime.fromtimestamp(
                                            raw_ts / 1000, tz=timezone.utc
                                        )
                                    else:
                                        sess.created_at = datetime.fromisoformat(
                                            str(raw_ts).rstrip("Z")
                                        ).replace(tzinfo=timezone.utc)
                                except Exception:  # noqa: BLE001
                                    pass  # best-effort; failure is non-critical
                            # Inline requests in header
                            reqs = v.get("requests", [])
                            n_inline = len(reqs) if isinstance(reqs, list) else 0
                            # Each patch line adds turns; use line count as proxy
                            # (each user turn ≈ 1 patch line)
                            approx_turns = n_inline + turn_estimate
                            # Populate a lightweight turn list just for sorting /
                            # first-message preview
                            if isinstance(reqs, list):
                                for req in reqs[:1]:  # only first for preview
                                    if not isinstance(req, dict):
                                        continue
                                    msg = req.get("message", {})
                                    if isinstance(msg, dict):
                                        text = msg.get("text", "") or ""
                                        if text:
                                            sess.turns.append(ChatTurn("user", text))
                            # Fake turn count ≈ n_inline (patch lines can add more,
                            # but we don't parse them in fast mode)
                            if approx_turns > len(sess.turns):
                                # pad with placeholder turns so turn_count is right
                                for _ in range(approx_turns - len(sess.turns)):
                                    sess.turns.append(ChatTurn("user", ""))
                except json.JSONDecodeError:
                    pass  # malformed JSON; skip line
        except Exception as exc:
            sess.parse_error = str(exc)
        return sess

    # ── Full parse (needed for `view` and `search`) ────────────────────────────
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        all_requests: list[dict] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # malformed JSON; skip line

            kind = obj.get("kind", -1)
            v = obj.get("v", {})

            if kind == 0:
                # Header line — session metadata
                if isinstance(v, dict):
                    sess.session_id = v.get("sessionId", session_id)
                    raw_ts = v.get("creationDate", "")
                    if raw_ts:
                        try:
                            if isinstance(raw_ts, (int, float)):
                                sess.created_at = datetime.fromtimestamp(
                                    raw_ts / 1000, tz=timezone.utc
                                )
                            else:
                                sess.created_at = datetime.fromisoformat(
                                    str(raw_ts).rstrip("Z")
                                ).replace(tzinfo=timezone.utc)
                        except Exception:  # noqa: BLE001
                            pass  # best-effort; failure is non-critical
                    reqs = v.get("requests", [])
                    if isinstance(reqs, list):
                        all_requests.extend(reqs)

            elif kind == 1:
                # Patch line — list of request objects
                patch_key = obj.get("k", [])
                if patch_key and isinstance(v, list):
                    all_requests.extend(v)

        # Convert requests to turns
        for req in all_requests:
            if not isinstance(req, dict):
                continue
            # User message (prompt)
            msg = req.get("message", {})
            if isinstance(msg, dict):
                text = msg.get("text", "") or ""
                if text:
                    ts = None
                    if msg.get("timestamp"):
                        ts = str(msg["timestamp"])
                    sess.turns.append(ChatTurn("user", text, ts))

            # Assistant response
            response = req.get("response", {})
            if isinstance(response, dict):
                parts = response.get("value", [])
                if isinstance(parts, list):
                    chunks: list[str] = []
                    for part in parts:
                        if isinstance(part, dict):
                            kind_p = part.get("kind", "")
                            if kind_p == "markdownContent":
                                val = part.get("value", "")
                                if isinstance(val, dict):
                                    chunks.append(val.get("value", ""))
                                elif isinstance(val, str):
                                    chunks.append(val)
                            elif kind_p == "inlineReference":
                                pass  # skip inline refs
                        elif isinstance(part, str):
                            chunks.append(part)
                    text = "\n".join(c for c in chunks if c).strip()
                    if text:
                        sess.turns.append(ChatTurn("assistant", text))
                elif isinstance(parts, str):
                    if parts.strip():
                        sess.turns.append(ChatTurn("assistant", parts))

    except Exception as exc:
        sess.parse_error = str(exc)

    return sess


# ── Helpers ───────────────────────────────────────────────────────────────────


def _all_sessions_or_die(
    workspace: str | None = None,
    *,
    fast: bool = False,
) -> list[ChatSession]:
    """Load all sessions, optionally filtered by workspace label."""
    all_s = _discover_all_sessions(fast=fast)
    if not all_s:
        ch.warning("No VS Code Copilot chat sessions found.")
        ch.dim(f"  Expected: {_workspace_storage_root()} / <hash> / chatSessions/")
        raise typer.Exit(0)
    if workspace:
        all_s = [s for s in all_s if workspace.lower() in s.workspace_label.lower()]
        if not all_s:
            ch.error(f"No sessions found for workspace containing '{workspace}'")
            raise typer.Exit(1)
    return all_s


# ── Commands ──────────────────────────────────────────────────────────────────


@sessions_app.callback(invoke_without_command=True)
def sessions_callback(ctx: typer.Context):
    """Browse and manage VS Code Copilot chat sessions."""
    if ctx.invoked_subcommand is None:
        # bare `navig copilot sessions` → list (fast scan)
        _cmd_list(workspace=None, limit=30, show_empty=False)


@sessions_app.command("list")
def _cmd_list(
    workspace: str | None = typer.Option(
        None, "--workspace", "-w", help="Filter by workspace name"
    ),
    limit: int = typer.Option(30, "--limit", "-n", help="Max sessions to show (0=all)"),
    show_empty: bool = typer.Option(False, "--empty", help="Include sessions with no turns"),
):
    """List all Copilot chat sessions."""
    from rich import box
    from rich.table import Table

    all_s = _all_sessions_or_die(workspace, fast=True)
    if not show_empty:
        all_s = [s for s in all_s if s.turn_count > 0]
    if limit and limit > 0:
        total = len(all_s)
        all_s = all_s[:limit]
    else:
        total = len(all_s)

    table = Table(
        title=f"[bold cyan]Copilot Chat Sessions[/]  [dim]({total} total)[/]",
        box=box.ROUNDED,
        border_style="bright_blue",
        show_lines=False,
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Created", style="cyan", width=16)
    table.add_column("Workspace", style="bright_blue", width=20)
    table.add_column("Turns", style="bright_cyan", width=5, justify="right")
    table.add_column("Size", style="dim", width=8, justify="right")
    table.add_column("First Message", style="white")

    for i, s in enumerate(all_s, 1):
        preview = s.first_user_message.replace("\n", " ")[:60]
        if len(s.first_user_message) > 60:
            preview += "…"
        table.add_row(
            str(i),
            s.created_str,
            s.workspace_label[:20],
            str(s.turn_count),
            _human_size(s.file_bytes),
            preview or "[dim](empty)[/]",
        )

    ch.console.print()
    ch.console.print(table)
    if total > len(all_s):
        ch.dim(f"  (showing {len(all_s)} of {total} — use --limit 0 for all)")
    ch.console.print()
    ch.dim("  Tip: navig copilot sessions view <SESSION_ID>")
    ch.dim("       navig copilot sessions search <query>")
    ch.console.print()


@sessions_app.command("stats")
def _cmd_stats(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Filter by workspace"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show storage statistics for all chat sessions."""
    from collections import defaultdict

    from rich import box
    from rich.table import Table

    all_s = _discover_all_sessions(fast=True)
    if not all_s:
        ch.warning("No sessions found.")
        return

    if workspace:
        all_s = [s for s in all_s if workspace.lower() in s.workspace_label.lower()]

    total_bytes = sum(s.file_bytes for s in all_s)
    total_turns = sum(s.turn_count for s in all_s)
    empty = sum(1 for s in all_s if s.turn_count == 0)

    ws_counts: dict = defaultdict(lambda: {"count": 0, "bytes": 0, "turns": 0})
    for s in all_s:
        ws_counts[s.workspace_label]["count"] += 1
        ws_counts[s.workspace_label]["bytes"] += s.file_bytes
        ws_counts[s.workspace_label]["turns"] += s.turn_count

    if json_output:
        import json as _json

        print(
            _json.dumps(
                {
                    "total_sessions": len(all_s),
                    "total_bytes": total_bytes,
                    "total_turns": total_turns,
                    "empty_sessions": empty,
                    "workspaces": dict(ws_counts),
                },
                indent=2,
            )
        )
        return

    ch.console.print()
    ch.console.print("[bold cyan]Copilot Session Statistics[/]")
    ch.console.print()
    ch.console.print(f"  Sessions:  [bright_cyan]{len(all_s):,}[/]  [dim]({empty} empty)[/]")
    ch.console.print(f"  Total size:[bright_cyan]{_human_size(total_bytes)}[/]")
    ch.console.print(f"  Total turns:[bright_cyan]{total_turns:,}[/]")
    ch.console.print()

    table = Table(
        title="[bold]By Workspace[/]",
        box=box.SIMPLE_HEAD,
        border_style="blue",
    )
    table.add_column("Workspace", style="bright_blue")
    table.add_column("Sessions", style="cyan", justify="right")
    table.add_column("Size", style="dim", justify="right")
    table.add_column("Turns", style="bright_cyan", justify="right")

    for ws_label, info in sorted(ws_counts.items(), key=lambda x: -x[1]["bytes"]):
        table.add_row(
            ws_label[:30],
            str(info["count"]),
            _human_size(info["bytes"]),
            str(info["turns"]),
        )
    ch.console.print(table)
    ch.console.print()


@sessions_app.command("view")
def _cmd_view(
    session_id: str = typer.Argument(..., help="Session ID (partial match OK)"),
    full: bool = typer.Option(False, "--full", help="Show full content"),
    max_chars: int = typer.Option(500, "--max-chars", help="Max chars per turn"),
):
    """View a chat session's content."""
    all_s = _discover_all_sessions(fast=False)
    matches = [s for s in all_s if session_id.lower() in s.session_id.lower()]

    if not matches:
        ch.error(f"No session found matching '{session_id}'")
        raise typer.Exit(1)

    sess = matches[0]
    if len(matches) > 1:
        ch.warning("Multiple matches — showing first. Use more specific ID.")

    ch.console.print()
    ch.console.print(f"[bold cyan]Session:[/] [bright_blue]{sess.session_id}[/]")
    ch.console.print(f"[dim]  Created:  {sess.created_str}[/]")
    ch.console.print(f"[dim]  Workspace:{sess.workspace_label}[/]")
    ch.console.print(f"[dim]  File:     {sess.path}[/]")
    ch.console.print(
        f"[dim]  Turns:    {sess.turn_count}  |  Size: {_human_size(sess.file_bytes)}[/]"
    )
    ch.console.print()

    if not sess.turns:
        ch.dim("  (no turns — session may be empty or format unrecognised)")
        return

    from rich.panel import Panel

    for turn in sess.turns:
        if turn.role == "user":
            color = "bright_blue"
            label = "YOU"
        else:
            color = "cyan"
            label = "COPILOT"

        text = turn.text if full else turn.text[:max_chars]
        if not full and len(turn.text) > max_chars:
            text += f"\n[dim]… ({len(turn.text) - max_chars} more chars — use --full)[/]"

        ch.console.print(
            Panel(
                text,
                title=f"[bold {color}]{label}[/]",
                border_style=color,
                expand=False,
            )
        )

    ch.console.print()


@sessions_app.command("search")
def _cmd_search(
    query: str = typer.Argument(..., help="Search query (case-insensitive)"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Limit to workspace"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
    context_chars: int = typer.Option(150, "--context", help="Characters of context to show"),
):
    """Search across all chat sessions for a query string."""

    all_s = _all_sessions_or_die(workspace, fast=False)
    q = query.lower()
    results: list[tuple] = []  # (session, turn, snippet)

    for sess in all_s:
        for turn in sess.turns:
            if q in turn.text.lower():
                idx = turn.text.lower().find(q)
                start = max(0, idx - 60)
                end = min(len(turn.text), idx + context_chars)
                snippet = turn.text[start:end].replace("\n", " ")
                # Highlight match
                ql = len(query)
                rel_idx = turn.text.lower()[start:end].lower().find(q)
                if rel_idx >= 0:
                    snippet = (
                        snippet[:rel_idx]
                        + f"[bold bright_yellow]{snippet[rel_idx : rel_idx + ql]}[/bold bright_yellow]"
                        + snippet[rel_idx + ql :]
                    )
                results.append((sess, turn, snippet))
                if len(results) >= limit:
                    break
        if len(results) >= limit:
            break

    if not results:
        ch.warning(f"No matches for '[bold]{query}[/]'")
        return

    ch.console.print()
    ch.console.print(
        f"[bold cyan]Search: '{query}'[/]  [dim]({len(results)} match{'es' if len(results) != 1 else ''})[/]"
    )
    ch.console.print()

    for sess, turn, snippet in results:
        role_color = "bright_blue" if turn.role == "user" else "cyan"
        ch.console.print(
            f"  [dim]{sess.created_str}[/]  "
            f"[{role_color}]{turn.role.upper():<9}[/]  "
            f"[bright_blue]{sess.workspace_label[:16]:<16}[/]  "
            f"{snippet}"
        )
        ch.console.print(f"  [dim]  → Session: {sess.session_id}[/]")
        ch.console.print()

    ch.dim("Tip: navig copilot sessions view <SESSION_ID>")
    ch.console.print()


@sessions_app.command("export")
def _cmd_export(
    output: Path | None = typer.Option(None, "--output", "-o", help="Output file path"),
    fmt: str = typer.Option("json", "--format", "-f", help="Format: json, md, csv"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Filter by workspace"),
    session_id: str | None = typer.Option(None, "--session", "-s", help="Export single session"),
    empty: bool = typer.Option(False, "--empty", help="Include empty sessions"),
):
    """Export sessions to JSON, Markdown, or CSV."""
    all_s = _all_sessions_or_die(workspace, fast=False)

    if session_id:
        all_s = [s for s in all_s if session_id.lower() in s.session_id.lower()]
        if not all_s:
            ch.error(f"No session matching '{session_id}'")
            raise typer.Exit(1)

    if not empty:
        all_s = [s for s in all_s if s.turn_count > 0]

    fmt = fmt.lower()
    if fmt not in ("json", "md", "csv"):
        ch.error("Format must be one of: json, md, csv")
        raise typer.Exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output is None:
        suffix = {"json": ".json", "md": ".md", "csv": ".csv"}[fmt]
        output = Path(f"copilot_sessions_{timestamp}{suffix}")

    if fmt == "json":
        data = []
        for s in all_s:
            data.append(
                {
                    "session_id": s.session_id,
                    "created": s.created_str,
                    "workspace": s.workspace_label,
                    "workspace_hash": s.workspace_hash,
                    "file_bytes": s.file_bytes,
                    "turns": [{"role": t.role, "text": t.text} for t in s.turns],
                }
            )
        _tmp_path: Path | None = None
        try:
            _fd, _tmp = tempfile.mkstemp(dir=output.parent, suffix=".tmp")
            _tmp_path = Path(_tmp)
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write(json.dumps(data, indent=2, ensure_ascii=False))
            os.replace(_tmp_path, output)
            _tmp_path = None
        finally:
            if _tmp_path is not None:
                _tmp_path.unlink(missing_ok=True)

    elif fmt == "md":
        lines = []
        for s in all_s:
            lines.append(f"# Session: {s.session_id}")
            lines.append(f"- Created: {s.created_str}")
            lines.append(f"- Workspace: {s.workspace_label}")
            lines.append("")
            for t in s.turns:
                lines.append(f"**{t.role.upper()}**")
                lines.append(t.text)
                lines.append("")
            lines.append("---")
            lines.append("")
        _tmp_path: Path | None = None
        try:
            _fd, _tmp = tempfile.mkstemp(dir=output.parent, suffix=".tmp")
            _tmp_path = Path(_tmp)
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write("\n".join(lines))
            os.replace(_tmp_path, output)
            _tmp_path = None
        finally:
            if _tmp_path is not None:
                _tmp_path.unlink(missing_ok=True)

    elif fmt == "csv":
        import csv

        with open(output, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["session_id", "created", "workspace", "role", "text"])
            for s in all_s:
                for t in s.turns:
                    w.writerow([s.session_id, s.created_str, s.workspace_label, t.role, t.text])

    size = output.stat().st_size
    ch.success(f"Exported {len(all_s)} sessions → {output}  ({_human_size(size)})")


@sessions_app.command("delete")
def _cmd_delete(
    session_id: str | None = typer.Argument(None, help="Session ID to delete (partial match OK)"),
    workspace: str | None = typer.Option(
        None, "--workspace", "-w", help="Delete all sessions from workspace"
    ),
    keep: int = typer.Option(0, "--keep", "-k", help="Keep N newest sessions (with --workspace)"),
    all_sessions: bool = typer.Option(False, "--all", help="Delete ALL sessions (requires --yes)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete one or more chat sessions.

    Examples:
        navig copilot sessions delete abc123
        navig copilot sessions delete --workspace myproject --keep 10
        navig copilot sessions delete --all --yes
    """
    all_s = _discover_all_sessions(fast=True)
    targets: list[ChatSession] = []

    if all_sessions:
        targets = list(all_s)
    elif workspace:
        ws_sessions = [s for s in all_s if workspace.lower() in s.workspace_label.lower()]
        if keep > 0:
            targets = ws_sessions[keep:]  # already newest-first
        else:
            targets = ws_sessions
    elif session_id:
        targets = [s for s in all_s if session_id.lower() in s.session_id.lower()]
    else:
        ch.error("Specify SESSION_ID, --workspace, or --all")
        raise typer.Exit(1)

    if not targets:
        ch.warning("No matching sessions found.")
        return

    total_bytes = sum(t.file_bytes for t in targets)
    ch.console.print()
    ch.console.print(
        f"[bold red]About to delete {len(targets)} session(s)  ({_human_size(total_bytes)})[/]"
    )
    for s in targets[:5]:
        ch.console.print(f"  [dim]• {s.created_str}  {s.workspace_label}  {s.session_id[:20]}[/]")
    if len(targets) > 5:
        ch.dim(f"  … and {len(targets) - 5} more")
    ch.console.print()

    if not yes:
        confirmed = typer.confirm("Proceed with deletion?", default=False)
        if not confirmed:
            ch.info("Cancelled.")
            return

    deleted = 0
    failed = 0
    for s in targets:
        try:
            s.path.unlink(missing_ok=True)
            deleted += 1
        except OSError as exc:
            ch.warning(f"Could not delete {s.path.name}: {exc}")
            failed += 1

    ch.success(f"Deleted {deleted} session(s)  (freed ~{_human_size(total_bytes)})")
    if failed:
        ch.warning(f"{failed} file(s) could not be deleted")
