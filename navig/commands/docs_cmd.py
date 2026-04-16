"""NAVIG docs, fetch, and search command implementations."""
from __future__ import annotations

from typing import Any

from navig.console_helper import get_console


def run_docs(
    ctx: Any,
    query: str | None,
    limit: int,
    plain: bool,
    json_output: bool,
) -> None:
    """Search NAVIG documentation — delegated from ``navig docs``."""
    import json as jsonlib
    from pathlib import Path

    import typer
    from rich.console import Console

    from navig import console_helper as ch

    console = Console(force_terminal=True)

    project_docs = Path(__file__).resolve().parent.parent / "docs"
    pkg_docs = Path(__file__).resolve().parent / "docs"

    if project_docs.exists():
        docs_dir = project_docs
    elif pkg_docs.exists():
        docs_dir = pkg_docs
    else:
        ch.error(
            "Documentation directory not found.",
            "Make sure NAVIG is installed correctly with docs/ available.",
        )
        raise typer.Exit(1)

    want_json = bool(json_output or (ctx.obj and ctx.obj.get("json")))

    if not query:
        md_files = sorted(docs_dir.glob("**/*.md"))
        topics = []
        for f in md_files:
            rel_path = f.relative_to(docs_dir)
            try:
                content = f.read_text(encoding="utf-8")
                lines = content.split("\n")
                title = None
                for line in lines:
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
                topics.append({"file": str(rel_path), "title": title or f.stem})
            except Exception:
                topics.append({"file": str(rel_path), "title": f.stem})

        if want_json:
            console.print(jsonlib.dumps({"topics": topics}, indent=2))
        else:
            console.print("[bold cyan]NAVIG Documentation[/bold cyan]")
            console.print(f"Found {len(topics)} documentation files.\n")
            for item in topics:
                title = item["title"]
                try:
                    title.encode(console.encoding or "utf-8")
                except (UnicodeEncodeError, LookupError):
                    title = "".join(c for c in title if ord(c) < 128)
                console.print(
                    f"  [cyan]*[/cyan] [yellow]{item['file']}[/yellow]: {title.strip()}"
                )
            console.print("\n[dim]Use 'navig docs <query>' to search documentation.[/dim]")
        raise typer.Exit()

    try:
        from navig.tools.web import search_docs

        results = search_docs(query=query, docs_path=docs_dir, max_results=limit)

        if want_json:
            console.print(
                jsonlib.dumps(
                    {
                        "query": query,
                        "results": [
                            {
                                "file": r.get("file"),
                                "title": r.get("title"),
                                "excerpt": r.get("excerpt"),
                                "score": r.get("score"),
                            }
                            for r in results
                        ],
                    },
                    indent=2,
                )
            )
        else:
            if not results:
                console.print(f"[yellow]No results found for '{query}'.[/yellow]")
                console.print(
                    "[dim]Try different keywords or check 'navig docs' for all topics.[/dim]"
                )
            else:
                console.print(f"[bold cyan]Search Results for '{query}'[/bold cyan]\n")
                for i, r in enumerate(results, 1):
                    console.print(f"[bold white]{i}. {r.get('title', 'Untitled')}[/bold white]")
                    console.print(f"   [dim]{r.get('file')}[/dim]")
                    if r.get("excerpt"):
                        excerpt = (
                            r["excerpt"][:300] + "..."
                            if len(r.get("excerpt", "")) > 300
                            else r.get("excerpt", "")
                        )
                        console.print(f"   {excerpt}")
                    console.print()

    except ImportError as e:
        ch.error(f"Search tools not available: {e}")
        raise typer.Exit(1) from e
    except Exception as e:
        ch.error(f"Documentation search failed: {e}")
        raise typer.Exit(1) from e


def run_fetch(
    ctx: Any,
    url: str,
    mode: str,
    max_chars: int,
    timeout: int,
    plain: bool,
    json_output: bool,
) -> None:
    """Fetch and extract content from a URL — delegated from ``navig fetch``."""
    import json as jsonlib

    import typer
    from rich.markdown import Markdown

    from navig import console_helper as ch

    console = get_console()
    want_json = bool(json_output or (ctx.obj and ctx.obj.get("json")))
    want_plain = plain or (ctx.obj and ctx.obj.get("raw"))

    try:
        from navig.tools.web import web_fetch

        if not want_json:
            console.print(f"[dim]Fetching {url}...[/dim]")

        result = web_fetch(
            url=url,
            extract_mode=mode,
            max_chars=max_chars,
            timeout_seconds=timeout,
        )

        if want_json:
            console.print(
                jsonlib.dumps(
                    {
                        "success": result.success,
                        "url": url,
                        "final_url": result.final_url,
                        "title": result.title,
                        "content": result.text[:max_chars] if result.text else None,
                        "truncated": result.truncated,
                        "error": result.error if not result.success else None,
                    },
                    indent=2,
                )
            )
        elif result.success:
            if want_plain:
                if result.title:
                    console.print(f"Title: {result.title}")
                console.print(f"URL: {result.final_url or url}\n")
                console.print(result.text)
            else:
                console.print(f"[bold cyan]{result.title or 'Untitled'}[/bold cyan]")
                console.print(f"[dim]{result.final_url or url}[/dim]\n")
                console.print(Markdown(result.text[:20000]))
                if result.truncated:
                    console.print(
                        "\n[yellow]Content truncated. Use --max-chars to increase limit.[/yellow]"
                    )
        else:
            ch.error(f"Failed to fetch URL: {result.error}")
            raise typer.Exit(1)

    except ImportError as e:
        ch.error(f"Web tools not available: {e}")
        raise typer.Exit(1) from e
    except Exception as e:
        ch.error(f"Fetch failed: {e}")
        raise typer.Exit(1) from e


def run_search(
    ctx: Any,
    query: str,
    limit: int,
    provider: str,
    plain: bool,
    json_output: bool,
) -> None:
    """Search the web — delegated from ``navig search``."""
    import json as jsonlib

    import typer

    from navig import console_helper as ch

    console = get_console()
    want_json = bool(json_output or (ctx.obj and ctx.obj.get("json")))
    want_plain = plain or (ctx.obj and ctx.obj.get("raw"))

    try:
        from navig.tools.web import web_search

        if not want_json:
            console.print(f"[dim]Searching for '{query}'...[/dim]")

        result = web_search(query=query, count=limit, provider=provider)

        if want_json:
            console.print(
                jsonlib.dumps(
                    {
                        "success": result.success,
                        "query": query,
                        "results": (
                            [
                                {"title": r.title, "url": r.url, "snippet": r.snippet}
                                for r in result.results
                            ]
                            if result.results
                            else []
                        ),
                        "error": result.error if not result.success else None,
                    },
                    indent=2,
                )
            )
        elif result.success and result.results:
            if want_plain:
                for i, r in enumerate(result.results, 1):
                    console.print(f"{i}. {r.title}")
                    console.print(f"   {r.url}")
                    if r.snippet:
                        console.print(f"   {r.snippet[:200]}")
                    console.print()
            else:
                console.print(f"[bold cyan]Search Results for '{query}'[/bold cyan]\n")
                for i, r in enumerate(result.results, 1):
                    console.print(f"[bold white]{i}. {r.title}[/bold white]")
                    console.print(f"   [blue underline]{r.url}[/blue underline]")
                    if r.snippet:
                        console.print(f"   [dim]{r.snippet[:200]}[/dim]")
                    console.print()
        elif result.success:
            console.print("[yellow]No results found.[/yellow]")
        else:
            ch.error(f"Search failed: {result.error}")
            console.print("\n[dim]Tip: Set up Brave Search API for better results:[/dim]")
            console.print("[dim]  1. Get key from https://brave.com/search/api/[/dim]")
            console.print("[dim]  2. navig config set web.search.api_key=YOUR_KEY[/dim]")
            raise typer.Exit(1)

    except ImportError as e:
        ch.error(f"Web tools not available: {e}")
        raise typer.Exit(1) from e
    except Exception as e:
        ch.error(f"Search failed: {e}")
        raise typer.Exit(1) from e
