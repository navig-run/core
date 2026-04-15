"""navig memory — conversations, knowledge-base, memory-bank & key-facts.

Extracted from navig/cli/__init__.py (P1-14 CLI decomposition).
"""

from __future__ import annotations

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

memory_app = typer.Typer(
    help="Manage conversation memory and knowledge base",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_config():
    """Return the global ConfigManager singleton."""
    from navig.config import get_config_manager

    return get_config_manager()


# ============================================================================
# Session / conversation commands
# ============================================================================


@memory_app.command("sessions")
def memory_sessions(
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum sessions to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List conversation sessions."""
    from pathlib import Path

    try:
        from navig.memory import ConversationStore

        config = _get_config()
        db_path = Path(config.global_config_dir) / "memory" / "memory.db"

        if not db_path.exists():
            if plain:
                print("No sessions")
            else:
                ch.info("No conversation history yet")
            return

        store = ConversationStore(db_path)
        sessions = store.list_sessions(limit=limit)

        if not sessions:
            if plain:
                print("No sessions")
            else:
                ch.info("No conversation sessions found")
            return

        if plain:
            for s in sessions:
                print(
                    f"{s.session_key}\t{s.message_count}\t{s.total_tokens}\t{s.updated_at.isoformat()}"
                )
        else:
            from rich.table import Table

            table = Table(title="Conversation Sessions")
            table.add_column("Session", style="cyan")
            table.add_column("Messages", justify="right")
            table.add_column("Tokens", justify="right")
            table.add_column("Last Updated", style="dim")

            for s in sessions:
                table.add_row(
                    s.session_key,
                    str(s.message_count),
                    str(s.total_tokens),
                    s.updated_at.strftime("%Y-%m-%d %H:%M"),
                )

            ch.console.print(table)

        store.close()

    except ImportError as e:
        ch.error(f"Memory module not available: {e}")
    except Exception as e:
        ch.error(f"Error listing sessions: {e}")


@memory_app.command("history")
def memory_history(
    session: str = typer.Argument(..., help="Session key to show"),
    limit: int = typer.Option(50, "--limit", "-l", help="Maximum messages"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """Show conversation history for a session."""
    from pathlib import Path

    try:
        from navig.memory import ConversationStore

        config = _get_config()
        db_path = Path(config.global_config_dir) / "memory" / "memory.db"

        if not db_path.exists():
            ch.error("No conversation history")
            return

        store = ConversationStore(db_path)
        messages = store.get_history(session, limit=limit)

        if not messages:
            ch.info(f"No messages in session '{session}'")
            store.close()
            return

        if plain:
            for m in messages:
                print(f"{m.role}\t{m.timestamp.isoformat()}\t{m.content[:100]}")
        else:
            ch.info(f"Session: {session} ({len(messages)} messages)")
            ch.console.print()

            for m in messages:
                role_style = "bold cyan" if m.role == "user" else "bold green"
                ch.console.print(
                    f"[{role_style}]{m.role.upper()}[/] ({m.timestamp.strftime('%H:%M')})"
                )
                ch.console.print(m.content[:500] + ("..." if len(m.content) > 500 else ""))
                ch.console.print()

        store.close()

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("clear")
def memory_clear(
    session: str = typer.Option(None, "--session", "-s", help="Clear specific session"),
    all_sessions: bool = typer.Option(False, "--all", help="Clear all sessions"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear conversation memory."""
    from pathlib import Path

    if not session and not all_sessions:
        ch.error("Specify --session or --all")
        raise typer.Exit(1)

    try:
        from navig.memory import ConversationStore

        config = _get_config()
        db_path = Path(config.global_config_dir) / "memory" / "memory.db"

        if not db_path.exists():
            ch.info("No memory to clear")
            return

        if not force:
            target = "all sessions" if all_sessions else f"session '{session}'"
            if not typer.confirm(f"Clear {target}?"):
                raise typer.Abort()

        store = ConversationStore(db_path)

        if all_sessions:
            sessions = store.list_sessions(limit=1000)
            count = 0
            for s in sessions:
                if store.delete_session(s.session_key):
                    count += 1
            ch.success(f"Cleared {count} sessions")
        else:
            if store.delete_session(session):
                ch.success(f"Cleared session '{session}'")
            else:
                ch.warning(f"Session '{session}' not found")

        store.close()

    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("compact")
def memory_compact(
    session: str | None = typer.Argument(
        None, help="Session key to compact (default: most recent)"
    ),
    instructions: str | None = typer.Option(
        None,
        "--instructions",
        "-i",
        help="Custom summarization instructions appended to the condensing prompt",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """Compress a session's history into a single AI-generated summary.

    Replaces the full message history with a compact summary so the
    session context remains meaningful without occupying your entire
    context window on future queries.

    Examples:
        navig memory compact
        navig memory compact telegram:user:12345
        navig memory compact --instructions "Focus on action items"
        navig memory compact --yes --plain
    """
    from pathlib import Path

    try:
        from navig.llm_generate import run_llm
        from navig.memory import ConversationStore

        config = _get_config()
        db_path = Path(config.global_config_dir) / "memory" / "memory.db"

        if not db_path.exists():
            ch.info("No conversation history found")
            return

        store = ConversationStore(db_path)

        # Resolve target session
        if session:
            target_key = session
            session_info = store.get_session(target_key)
            if session_info is None:
                ch.error(f"Session '{target_key}' not found")
                store.close()
                raise typer.Exit(1)
        else:
            sessions = store.list_sessions(limit=1)
            if not sessions:
                ch.info("No conversation sessions found")
                store.close()
                return
            target_key = sessions[0].session_key
            session_info = sessions[0]

        msg_count = session_info.message_count
        threshold = int(config.get("memory.compact_threshold_messages", 20) or 20)

        if msg_count < threshold:
            if plain:
                print(f"skip: {msg_count} messages (threshold={threshold})")
            else:
                ch.info(
                    f"Nothing to compact — session has only {msg_count} message"
                    f"{'s' if msg_count != 1 else ''} (threshold: {threshold})"
                )
            store.close()
            return

        if not plain:
            ch.info(f"Session: {target_key}")
            ch.info(f"Messages: {msg_count} → will compress to 1 summary")

        if not yes:
            if not typer.confirm(f"Compact {msg_count} messages into a summary?"):
                raise typer.Abort()

        # Fetch full history for summarisation
        messages = store.get_history(target_key, limit=msg_count)
        if not messages:
            ch.info("Session is already empty")
            store.close()
            return

        # Build summarisation prompt
        effort_level = str(config.get("memory.compact_summary_effort", "low") or "low")
        system_instruction = (
            "You are a concise summariser.  Read the conversation below and produce a "
            "clear, specific 3–6 sentence summary that captures: what was discussed, "
            "key decisions made, and the most important next step.  Be concrete — name "
            "specific technologies, commands, or files mentioned."
        )
        if instructions:
            system_instruction += f"\n\nAdditional focus: {instructions}"

        history_text = "\n".join(
            f"{m.role.upper()}: {m.content[:500]}"
            for m in messages[-100:]  # cap at last 100 for the LLM call
        )
        llm_messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": history_text},
        ]

        if not plain:
            ch.dim("Generating summary...")

        result = run_llm(llm_messages, mode="summary", effort=effort_level)
        summary = (result.content or "").strip()

        if not summary:
            ch.error("LLM returned an empty summary — session unchanged")
            store.close()
            raise typer.Exit(1)

        # Atomically replace history with the summary
        deleted = store.compact_session(target_key, summary)
        store.close()

        if plain:
            print(f"compacted: {deleted} messages")
            print(summary)
        else:
            from rich.panel import Panel

            ch.console.print()
            ch.console.print(
                Panel(
                    summary,
                    title=f"[bold green]Session summary[/bold green]  "
                    f"[dim]({deleted} messages → 1)[/dim]",
                    border_style="green",
                    padding=(1, 2),
                )
            )
            ch.success(f"Compacted {deleted} messages into 1 summary")

    except typer.Abort:
        ch.info("Cancelled")
    except typer.Exit:
        raise
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("knowledge")
def memory_knowledge(
    action: str = typer.Argument("list", help="list, add, search, clear"),
    key: str = typer.Option(None, "--key", "-k", help="Knowledge key"),
    content: str = typer.Option(None, "--content", "-c", help="Knowledge content"),
    query: str = typer.Option(None, "--query", "-q", help="Search query"),
    tags: str = typer.Option(None, "--tags", "-t", help="Comma-separated tags"),
    limit: int = typer.Option(20, "--limit", "-l", help="Result limit"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """Manage knowledge base entries."""
    from pathlib import Path

    try:
        from navig.memory import KnowledgeBase, KnowledgeEntry

        config = _get_config()
        db_path = Path(config.global_config_dir) / "memory" / "knowledge.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        kb = KnowledgeBase(db_path, embedding_provider=None)

        if action == "list":
            # List all entries
            entries = kb.export_entries()[:limit]

            if not entries:
                ch.info("Knowledge base is empty")
                return

            if plain:
                for e in entries:
                    print(f"{e['key']}\t{e['source']}\t{e['content'][:80]}")
            else:
                from rich.table import Table

                table = Table(title="Knowledge Base")
                table.add_column("Key", style="cyan")
                table.add_column("Source", style="dim")
                table.add_column("Content", max_width=50)
                table.add_column("Tags")

                for e in entries:
                    import json

                    tags_list = json.loads(e.get("tags", "[]"))
                    table.add_row(
                        e["key"],
                        e.get("source", ""),
                        (e["content"][:50] + "..." if len(e["content"]) > 50 else e["content"]),
                        ", ".join(tags_list),
                    )

                ch.console.print(table)

        elif action == "add":
            if not key or not content:
                ch.error("--key and --content required for add")
                raise typer.Exit(1)

            tag_list = [t.strip() for t in tags.split(",")] if tags else []

            entry = KnowledgeEntry(
                key=key,
                content=content,
                tags=tag_list,
                source="cli",
            )
            kb.upsert(entry, compute_embedding=False)
            ch.success(f"Added knowledge: {key}")

        elif action == "search":
            if not query:
                ch.error("--query required for search")
                raise typer.Exit(1)

            tag_list = [t.strip() for t in tags.split(",")] if tags else None
            results = kb.text_search(query, limit=limit, tags=tag_list)

            if not results:
                ch.info("No matching entries")
                return

            if plain:
                for e in results:
                    print(f"{e.key}\t{e.content[:80]}")
            else:
                for e in results:
                    ch.console.print(f"[cyan]{e.key}[/]")
                    ch.console.print(f"  {e.content[:200]}")
                    if e.tags:
                        ch.console.print(f"  Tags: {', '.join(e.tags)}")
                    ch.console.print()

        elif action == "clear":
            if not typer.confirm("Clear entire knowledge base?"):
                raise typer.Abort()
            count = kb.clear()
            ch.success(f"Cleared {count} entries")

        else:
            ch.error(f"Unknown action: {action}")
            ch.info("Valid actions: list, add, search, clear")

        kb.close()

    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("stats")
def memory_stats():
    """Show memory usage statistics."""
    from pathlib import Path

    try:
        from navig.memory import ConversationStore, KnowledgeBase

        config = _get_config()

        # Conversation stats
        conv_db = Path(config.global_config_dir) / "memory" / "memory.db"
        if conv_db.exists():
            store = ConversationStore(conv_db)
            sessions = store.list_sessions(limit=1000)
            total_messages = sum(s.message_count for s in sessions)
            total_tokens = sum(s.total_tokens for s in sessions)
            store.close()

            ch.info("Conversation Memory:")
            ch.console.print(f"  Sessions: {len(sessions)}")
            ch.console.print(f"  Messages: {total_messages}")
            ch.console.print(f"  Tokens: {total_tokens:,}")
            ch.console.print(f"  Size: {conv_db.stat().st_size / 1024:.1f} KB")
        else:
            ch.info("Conversation Memory: empty")

        ch.console.print()

        # Knowledge stats
        kb_db = Path(config.global_config_dir) / "memory" / "knowledge.db"
        if kb_db.exists():
            kb = KnowledgeBase(kb_db, embedding_provider=None)
            count = kb.count()
            kb.close()

            ch.info("Knowledge Base:")
            ch.console.print(f"  Entries: {count}")
            ch.console.print(f"  Size: {kb_db.stat().st_size / 1024:.1f} KB")
        else:
            ch.info("Knowledge Base: empty")

    except Exception as e:
        ch.error(f"Error: {e}")


# ============================================================================
# Memory Bank Commands (file-based knowledge with vector search)
# ============================================================================


@memory_app.command("bank")
def memory_bank_status(
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """Show memory bank status and statistics.

    The memory bank is a file-based knowledge store at ~/.navig/memory/
    that supports hybrid search (vector + keyword).

    Examples:
        navig memory bank
        navig memory bank --plain
    """
    try:
        from navig.memory import get_memory_manager

        manager = get_memory_manager(use_embeddings=False)  # Don't load embeddings for status
        status = manager.get_status()

        if plain:
            print(f"directory={status['memory_directory']}")
            print(f"files={status['indexed_files']}")
            print(f"chunks={status['total_chunks']}")
            print(f"tokens={status['total_tokens']}")
            print(f"embedded={status['embedded_chunks']}")
            print(f"size_mb={status['database_size_mb']}")
            print(f"embeddings={status['embeddings_enabled']}")
        else:
            ch.info("Memory Bank Status")
            ch.console.print(f"  Directory: {status['memory_directory']}")
            ch.console.print(f"  Indexed files: {status['indexed_files']}")
            ch.console.print(f"  Total chunks: {status['total_chunks']}")
            ch.console.print(f"  Total tokens: {status['total_tokens']:,}")
            ch.console.print(f"  Embedded chunks: {status['embedded_chunks']}")
            ch.console.print(f"  Database size: {status['database_size_mb']} MB")

            if status["embeddings_enabled"]:
                ch.console.print(f"  Embedding model: {status['embedding_model']}")
            else:
                ch.console.print("  Embeddings: [dim]disabled[/dim]")

        manager.close()

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("index")
def memory_bank_index(
    force: bool = typer.Option(False, "--force", "-f", help="Re-index even unchanged files"),
    no_embed: bool = typer.Option(False, "--no-embed", help="Skip embedding generation"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show file-by-file progress"),
):
    """Index files in the memory bank.

    Scans ~/.navig/memory/ for .md/.txt files and creates
    searchable chunks with vector embeddings.

    Examples:
        navig memory index
        navig memory index --force
        navig memory index --no-embed
    """
    try:
        from navig.memory import get_memory_manager

        def progress(file_path: str, status: str):
            if verbose:
                icon = "✓" if status == "indexed" else "→" if status == "skipped" else "✗"
                ch.console.print(f"  {icon} {file_path}")

        ch.info("Indexing memory bank...")

        manager = get_memory_manager(use_embeddings=not no_embed)
        result = manager.index(
            force=force,
            embed=not no_embed,
            progress_callback=progress if verbose else None,
        )

        ch.success(f"Indexed {result.files_processed} files ({result.files_skipped} skipped)")
        ch.console.print(f"  Created {result.chunks_created} chunks")
        ch.console.print(f"  Total tokens: {result.total_tokens:,}")
        ch.console.print(f"  Embedded: {result.chunks_embedded} chunks")
        ch.console.print(f"  Duration: {result.duration_seconds:.2f}s")

        if result.errors:
            ch.warning(f"Errors ({len(result.errors)}):")
            for err in result.errors[:5]:
                ch.console.print(f"  • {err}")

        manager.close()

    except ImportError as e:
        ch.error(f"Missing dependency: {e}")
        ch.info("For embeddings, install: pip install sentence-transformers")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("search")
def memory_bank_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-l", help="Maximum results"),
    file: str = typer.Option(None, "--file", "-f", help="Filter by file pattern"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    keyword_only: bool = typer.Option(
        False, "--keyword", "-k", help="Keyword-only search (no embeddings)"
    ),
):
    """Search the memory bank with hybrid search.

    Uses 70% vector similarity + 30% BM25 keyword matching.
    Falls back to keyword-only if embeddings unavailable.

    Examples:
        navig memory search "docker networking"
        navig memory search "nginx config" --limit 10
        navig memory search "deploy" --file "*.md"
        navig memory search "docker" --keyword
    """
    import json as json_module

    try:
        from navig.memory import get_memory_manager

        # Try with embeddings first, fall back to keyword-only
        use_embeddings = not keyword_only
        manager = None

        try:
            manager = get_memory_manager(use_embeddings=use_embeddings)
            response = manager.search(query, limit=limit, file_filter=file)
        except ImportError:
            # Embeddings not available, fall back to keyword-only
            if not keyword_only and not plain:
                ch.warning("Embeddings unavailable, using keyword-only search")
                ch.info("For semantic search: pip install sentence-transformers numpy")
            manager = get_memory_manager(use_embeddings=False)
            # Use keyword-only search via search engine (proper normalization)
            response = manager.search_engine.search(
                query, limit=limit, file_filter=file, keyword_only=True
            )

        if json_output:
            print(json_module.dumps(response.to_dict(), indent=2))
            if manager:
                manager.close()
            return

        if not response.results:
            if plain:
                print("No results")
            else:
                ch.info("No matching results found")
            if manager:
                manager.close()
            return

        if plain:
            for r in response.results:
                print(f"{r.combined_score:.3f}\t{r.file_path}:{r.line_start}\t{r.snippet[:80]}")
        else:
            ch.info(f"Found {len(response.results)} results ({response.search_time_ms:.1f}ms)")
            ch.console.print()

            for i, r in enumerate(response.results, 1):
                score_bar = "█" * int(r.combined_score * 10)
                ch.console.print(f"[bold cyan]{i}.[/bold cyan] [dim]{r.citation()}[/dim]")
                ch.console.print(f"   Score: [green]{score_bar}[/green] {r.combined_score:.3f}")
                ch.console.print(f"   {r.snippet}")
                ch.console.print()

        if manager:
            manager.close()

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("files")
def memory_bank_files(
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """List indexed files in the memory bank."""
    try:
        from navig.memory import get_memory_manager

        manager = get_memory_manager(use_embeddings=False)
        files = manager.list_files()

        if not files:
            if plain:
                print("No files")
            else:
                ch.info("No files indexed yet")
                ch.info(f"Add .md files to: {manager.memory_dir}")
            manager.close()
            return

        if plain:
            for f in files:
                print(f"{f['file_path']}\t{f['chunk_count']}\t{f['total_tokens']}")
        else:
            from rich.table import Table

            table = Table(title="Indexed Memory Files")
            table.add_column("File", style="cyan")
            table.add_column("Chunks", justify="right")
            table.add_column("Tokens", justify="right")
            table.add_column("Indexed", style="dim")

            for f in files:
                indexed_at = f["indexed_at"][:10] if f.get("indexed_at") else "-"
                table.add_row(
                    f["file_path"],
                    str(f["chunk_count"]),
                    str(f["total_tokens"]),
                    indexed_at,
                )

            ch.console.print(table)

        manager.close()

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("clear-bank")
def memory_bank_clear(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear the memory bank index (keeps original files).

    This clears the search index but preserves the original
    .md files in ~/.navig/memory/

    Examples:
        navig memory clear-bank
        navig memory clear-bank --force
    """
    try:
        from navig.memory import get_memory_manager

        if not force:
            if not typer.confirm("Clear memory bank index? (files are preserved)"):
                raise typer.Abort()

        manager = get_memory_manager(use_embeddings=False)
        result = manager.clear(confirm=True)

        ch.success("Memory bank index cleared")
        ch.console.print(f"  Files removed: {result.get('files_deleted', 0)}")
        ch.console.print(f"  Chunks removed: {result.get('chunks_deleted', 0)}")
        ch.console.print(f"  Cache cleared: {result.get('cache_cleared', 0)}")

        manager.close()

    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


# ============================================================================
# Key Facts (Conversational Memory) Commands
# ============================================================================


@memory_app.command("facts")
def memory_facts_list(
    category: str = typer.Option(None, "--category", "-c", help="Filter by category"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max facts to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List stored key facts (what NAVIG remembers about you).

    Shows persistent memories extracted from conversations:
    preferences, decisions, identity, technical context.

    Examples:
        navig memory facts
        navig memory facts --category preference
        navig memory facts --json
    """
    import json as json_module

    try:
        from navig.memory.key_facts import get_key_fact_store

        store = get_key_fact_store()
        facts = store.get_active(limit=limit, category=category)

        if not facts:
            if plain:
                print("No facts stored")
            else:
                ch.info("No key facts stored yet.")
                ch.info("Facts are automatically extracted from conversations.")
            return

        if json_output:
            print(json_module.dumps([f.to_dict() for f in facts], indent=2, default=str))
            return

        if plain:
            for f in facts:
                tags = ",".join(f.tags[:3]) if f.tags else ""
                print(f"{f.id[:8]}\t{f.category}\t{f.confidence:.2f}\t{tags}\t{f.content[:80]}")
        else:
            from rich.table import Table

            table = Table(title=f"Key Facts ({len(facts)} active)")
            table.add_column("ID", style="dim", max_width=8)
            table.add_column("Category", style="cyan")
            table.add_column("Confidence", justify="right")
            table.add_column("Content", max_width=60)
            table.add_column("Tags", style="dim", max_width=20)

            for f in facts:
                conf_color = (
                    "green" if f.confidence >= 0.8 else "yellow" if f.confidence >= 0.6 else "red"
                )
                table.add_row(
                    f.id[:8],
                    f.category,
                    f"[{conf_color}]{f.confidence:.2f}[/{conf_color}]",
                    f.content[:60],
                    ", ".join(f.tags[:3]),
                )
            ch.console.print(table)

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("remember")
def memory_remember(
    content: str = typer.Argument(..., help="Fact to remember"),
    category: str = typer.Option(
        "context",
        "--category",
        "-c",
        help="preference|decision|identity|technical|context",
    ),
    tags: str = typer.Option("", "--tags", "-t", help="Comma-separated tags"),
):
    """Manually add a key fact to memory.

    Examples:
        navig memory remember "User prefers dark mode" --category preference
        navig memory remember "Deploy target is AWS eu-west-1" --category technical --tags aws,deploy
    """
    try:
        from navig.memory.key_facts import VALID_CATEGORIES, KeyFact, get_key_fact_store

        cat = category.lower().strip()
        if cat not in VALID_CATEGORIES:
            ch.warning(f"Unknown category '{cat}', using 'context'")
            cat = "context"

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        fact = KeyFact(
            content=content,
            category=cat,
            tags=tag_list,
            confidence=1.0,  # Manually added = full confidence
            source_platform="cli",
        )

        store = get_key_fact_store()
        result = store.upsert(fact)
        ch.success(f"Remembered: {result.content}")
        ch.console.print(
            f"  ID: [dim]{result.id[:8]}[/dim]  Category: [cyan]{result.category}[/cyan]"
        )

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("forget")
def memory_forget(
    fact_id: str = typer.Argument(None, help="Fact ID (prefix) to forget"),
    query: str = typer.Option(None, "--query", "-q", help="Search and forget matching facts"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a key fact from memory (soft-delete).

    Examples:
        navig memory forget abc12345
        navig memory forget --query "dark mode"
    """
    try:
        from navig.memory.key_facts import get_key_fact_store

        store = get_key_fact_store()

        if query:
            results = store.search_keyword(query, limit=10)
            if not results:
                ch.info("No matching facts found")
                return

            for fact, _ in results:
                ch.console.print(f"  [{fact.id[:8]}] {fact.content}")

            if not force and not typer.confirm(f"Forget {len(results)} fact(s)?"):
                ch.info("Cancelled")
                return

            for fact, _ in results:
                store.soft_delete(fact.id)
            ch.success(f"Forgot {len(results)} fact(s)")

        elif fact_id:
            # Match by prefix
            facts = store.get_active(limit=500)
            matches = [f for f in facts if f.id.startswith(fact_id)]

            if not matches:
                ch.error(f"No fact found matching '{fact_id}'")
                return

            for f in matches:
                if not force:
                    ch.console.print(f"  {f.content}")
                    if not typer.confirm("Forget this fact?"):
                        continue
                store.soft_delete(f.id)
                ch.success(f"Forgot: {f.content[:60]}")
        else:
            ch.error("Provide a fact ID or --query")

    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("fact-stats")
def memory_fact_stats(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show key facts memory statistics.

    Examples:
        navig memory fact-stats
        navig memory fact-stats --json
    """
    import json as json_module

    try:
        from navig.memory.key_facts import get_key_fact_store

        store = get_key_fact_store()
        stats = store.get_stats()

        if json_output:
            print(json_module.dumps(stats, indent=2, default=str))
            return

        ch.info("Key Facts Memory Statistics:")
        ch.console.print(f"  Total facts:      {stats['total']}")
        ch.console.print(f"  Active:           [green]{stats['active']}[/green]")
        ch.console.print(f"  Deleted:          [red]{stats['deleted']}[/red]")
        ch.console.print(f"  Superseded:       [yellow]{stats['superseded']}[/yellow]")
        ch.console.print(f"  DB path:          [dim]{stats['db_path']}[/dim]")

        if stats.get("by_category"):
            ch.console.print("\n  By category:")
            for cat, count in stats["by_category"].items():
                ch.console.print(f"    {cat}: {count}")

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("sync")
def memory_sync(
    ctx: typer.Context,
    from_url: str = typer.Option(
        ..., "--from", help="Source gateway URL (e.g. http://10.0.0.5:7422)."
    ),
    formation: str = typer.Option("", "--formation", "-f", help="Formation ID filter."),
    limit: int = typer.Option(500, "--limit", "-n", min=1, max=5000, help="Max chunks to pull."),
    token: str = typer.Option("", "--token", "-t", help="Bearer token for remote gateway."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be imported without writing."
    ),
) -> None:
    """Pull memory chunks from a remote NAVIG formation.

    Connects to a remote NAVIG gateway, exports its memory chunks,
    and imports them into the local memory store.

    Uses HTTP transport — not mesh (mesh explicitly excludes memory sync).

    Examples:
        navig memory sync --from http://10.0.0.5:7422
        navig memory sync --from http://remote:7422 --formation myproject
        navig memory sync --from http://remote:7422 --token mytoken --dry-run
    """
    ch.info(f"Connecting to {from_url} …")

    try:
        import json as _json
        from urllib.request import Request, urlopen

        from navig.memory.sync import import_chunks

        params = f"limit={limit}"
        if formation:
            params += f"&formation_id={formation}"

        url = f"{from_url.rstrip('/')}/memory/sync/export?{params}"
        headers: dict = {"User-Agent": "navig-sync/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:  # noqa: S310
                payload = _json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            ch.error(f"Failed to connect to {from_url}: {exc}")
            raise typer.Exit(1) from exc

        chunks = payload.get("chunks", payload) if isinstance(payload, dict) else payload
        if not isinstance(chunks, list):
            ch.error("Remote returned unexpected format.")
            raise typer.Exit(1)

        ch.info(f"Received {len(chunks)} chunk(s) from remote.")

        if dry_run:
            ch.dim("  (dry-run: nothing written)")
            raise typer.Exit(0)

        from navig.config import get_config_manager

        cfg = get_config_manager()
        db_path = cfg.storage_dir / "memory" / "chunks.db"

        imported, skipped = import_chunks(db_path, chunks, formation)
        ch.success(f"Sync complete: {imported} imported, {skipped} skipped.")

    except (SystemExit, typer.Exit):
        raise
    except Exception as exc:
        ch.error(f"Sync failed: {exc}")
        raise typer.Exit(1) from exc
