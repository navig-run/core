"""
Wiki Commands - Knowledge Base Management System

Manages project wiki with AI-powered inbox processing.
Structure:
    .navig/wiki/
    ├── inbox/           # Staging area for unprocessed content
    ├── .meta/           # Wiki configuration & indexes
    ├── knowledge/       # Encyclopedia & knowledge base (public/private)
    ├── technical/       # Technical documentation
    ├── hub/             # Project command center (PIM)
    ├── external/        # External-facing content
    └── archive/         # Archived content
"""

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
import yaml

from navig import console_helper as ch
from navig.config import ConfigManager

# Wiki folder structure
WIKI_STRUCTURE = {
    "inbox": {},
    ".meta": {"_files": ["config.yaml", "index.md", "glossary.md"]},
    "knowledge": {
        "concepts": {},
        "domain": {},
        "guides": {},
        "resources": {},
        "_files": [".visibility"],
    },
    "technical": {
        "architecture": {},
        "api": {},
        "database": {},
        "decisions": {},
        "troubleshooting": {},
    },
    "hub": {
        "roadmap": {},
        "planning": {},
        "tasks": {},
        "changelog": {},
        "retrospectives": {},
    },
    "external": {"business": {}, "marketing": {}, "press": {}},
    "archive": {},
}

# Default wiki configuration
DEFAULT_CONFIG = """# Wiki Configuration
wiki:
  version: "1.0"
  default_language: en

  # Link style: wiki (use [[wiki-links]]) or markdown (use [text](path))
  link_style: wiki

  # AI processing settings
  ai:
    auto_process_inbox: true
    auto_translate: true
    auto_categorize: true
    auto_move: false  # Require confirmation before moving files
    rewrite_style: preserve  # preserve | standardize | summarize

  # Cleanup settings
  cleanup:
    archive_after_days: 365

  # Publishing settings
  publish:
    default_visibility: private  # public | private
    exclude_patterns:
      - "*.draft.md"
      - "_*"
      - ".meta/*"
"""

DEFAULT_INDEX = """# Wiki Index

> Auto-generated index of all wiki pages.

## Quick Links

- [Knowledge Base](knowledge/)
- [Technical Docs](technical/)
- [Project Hub](hub/)
- [External Materials](external/)

## Recent Updates

*No pages yet. Use `navig wiki add` to add content.*

---
*Last updated: {date}*
"""

DEFAULT_GLOSSARY = """# Glossary

> Project terminology and definitions.

## Terms

*No terms defined yet. Add terms as you build your knowledge base.*

---
*Use `navig wiki edit glossary` to add terms.*
"""

DEFAULT_VISIBILITY = """# Visibility: public
# Options: public | private
# Public content can be published to GitHub wiki or docs site
visibility: private
"""


def get_wiki_path(config: ConfigManager) -> Path:
    """Get the wiki directory path for current project."""
    if config.app_config_dir:
        return config.app_config_dir / "wiki"
    return config.global_config_dir / "wiki"


def get_global_wiki_path() -> Path:
    """Get the global wiki directory path."""
    return Path.home() / ".navig" / "wiki"


def ensure_wiki_initialized(wiki_path: Path) -> bool:
    """Check if wiki is initialized."""
    return (wiki_path / ".meta" / "config.yaml").exists()


def create_folder_structure(base_path: Path, structure: dict, indent: int = 0) -> list[str]:
    """
    Recursively create folder structure.

    Returns list of created paths for display.
    """
    created = []

    for name, contents in structure.items():
        if name == "_files":
            continue

        folder_path = base_path / name
        folder_path.mkdir(parents=True, exist_ok=True)
        created.append(("folder", str(folder_path.relative_to(base_path.parent))))

        # Create subfolders
        if isinstance(contents, dict) and contents:
            sub_created = create_folder_structure(folder_path, contents, indent + 1)
            created.extend(sub_created)

        # Create default files if specified
        if "_files" in structure.get(name, {}):
            for filename in structure[name]["_files"]:
                file_path = folder_path / filename
                if not file_path.exists():
                    file_path.touch()
                    created.append(("file", str(file_path.relative_to(base_path.parent))))

    # Handle _files at current level
    if "_files" in structure:
        for filename in structure["_files"]:
            file_path = base_path / filename
            if not file_path.exists():
                file_path.touch()
                created.append(("file", str(file_path.relative_to(base_path.parent))))

    return created


def init_wiki(wiki_path: Path, force: bool = False) -> bool:
    """
    Initialize wiki structure with default files.

    Returns True if successful.
    """
    if ensure_wiki_initialized(wiki_path) and not force:
        return False

    # Create folder structure (but not the placeholder files)
    wiki_path.mkdir(parents=True, exist_ok=True)

    # Create folders only, skip _files entries
    for name, contents in WIKI_STRUCTURE.items():
        if name == "_files":
            continue
        folder_path = wiki_path / name
        folder_path.mkdir(parents=True, exist_ok=True)

        if isinstance(contents, dict):
            for subname, subcontents in contents.items():
                if subname == "_files":
                    continue
                subfolder = folder_path / subname
                subfolder.mkdir(parents=True, exist_ok=True)

    # Ensure .meta folder exists
    meta_path = wiki_path / ".meta"
    meta_path.mkdir(parents=True, exist_ok=True)

    # config.yaml
    config_file = meta_path / "config.yaml"
    if not config_file.exists() or force:
        config_file.write_text(DEFAULT_CONFIG, encoding="utf-8")

    # index.md
    index_file = meta_path / "index.md"
    if not index_file.exists() or force:
        content = DEFAULT_INDEX.format(date=datetime.now().strftime("%Y-%m-%d %H:%M"))
        index_file.write_text(content, encoding="utf-8")

    # glossary.md
    glossary_file = meta_path / "glossary.md"
    if not glossary_file.exists() or force:
        glossary_file.write_text(DEFAULT_GLOSSARY, encoding="utf-8")

    # knowledge/.visibility
    visibility_file = wiki_path / "knowledge" / ".visibility"
    if not visibility_file.exists() or force:
        visibility_file.write_text(DEFAULT_VISIBILITY, encoding="utf-8")

    return True


def get_wiki_config(wiki_path: Path) -> dict[str, Any]:
    """Load wiki configuration."""
    config_file = wiki_path / ".meta" / "config.yaml"
    if config_file.exists():
        with open(config_file, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def list_wiki_pages(
    wiki_path: Path, folder: str | None = None, recursive: bool = True
) -> list[dict[str, Any]]:
    """
    List all wiki pages.

    Returns list of dicts with page info.
    """
    pages = []
    search_path = wiki_path / folder if folder else wiki_path

    if not search_path.exists():
        return pages

    pattern = "**/*.md" if recursive else "*.md"

    for md_file in search_path.glob(pattern):
        # Skip hidden folders and meta
        rel_path = md_file.relative_to(wiki_path)
        if any(part.startswith(".") for part in rel_path.parts):
            continue

        # Get file info
        stat = md_file.stat()

        # Extract title from first heading
        title = md_file.stem
        try:
            content = md_file.read_text(encoding="utf-8")
            for line in content.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # Normalize path to forward slashes for cross-platform consistency
        rel_path_str = str(rel_path).replace("\\", "/")
        folder_str = str(rel_path.parent).replace("\\", "/")

        pages.append(
            {
                "path": rel_path_str,
                "name": md_file.stem,
                "title": title,
                "folder": folder_str,
                "modified": datetime.fromtimestamp(stat.st_mtime),
                "size": stat.st_size,
            }
        )

    return sorted(pages, key=lambda x: x["path"])


def search_wiki(wiki_path: Path, query: str, case_sensitive: bool = False) -> list[dict[str, Any]]:
    """
    Full-text search across wiki pages.

    Returns list of matches with context.
    """
    results = []
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)

    for md_file in wiki_path.glob("**/*.md"):
        rel_path = md_file.relative_to(wiki_path)
        if any(part.startswith(".") for part in rel_path.parts):
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
            matches = list(pattern.finditer(content))

            if matches:
                # Get context around first match
                first_match = matches[0]
                start = max(0, first_match.start() - 50)
                end = min(len(content), first_match.end() + 50)
                context = content[start:end].replace("\n", " ").strip()
                if start > 0:
                    context = "..." + context
                if end < len(content):
                    context = context + "..."

                # Normalize path to forward slashes
                rel_path_str = str(rel_path).replace("\\", "/")

                results.append({"path": rel_path_str, "matches": len(matches), "context": context})
        except Exception:
            continue

    return sorted(results, key=lambda x: x["matches"], reverse=True)


def resolve_wiki_link(wiki_path: Path, link: str) -> Path | None:
    """
    Resolve a [[wiki-link]] to actual file path.

    Supports:
    - [[page-name]] - fuzzy search
    - [[folder/page-name]] - exact path
    - [[global:page-name]] - global wiki
    """
    # Handle global wiki prefix
    if link.startswith("global:"):
        link = link[7:]
        wiki_path = get_global_wiki_path()

    # Remove display text if present [[path|Display]]
    if "|" in link:
        link = link.split("|")[0]

    # Try exact path first
    exact_path = wiki_path / f"{link}.md"
    if exact_path.exists():
        return exact_path

    # Fuzzy search - find by name
    link_name = Path(link).stem
    for md_file in wiki_path.glob(f"**/{link_name}.md"):
        rel_path = md_file.relative_to(wiki_path)
        if not any(part.startswith(".") for part in rel_path.parts):
            return md_file

    return None


def find_broken_links(wiki_path: Path) -> list[dict[str, Any]]:
    """Find all broken wiki links."""
    broken = []
    link_pattern = re.compile(r"\[\[([^\]]+)\]\]")

    for md_file in wiki_path.glob("**/*.md"):
        rel_path = md_file.relative_to(wiki_path)
        if any(part.startswith(".") for part in rel_path.parts):
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
            for match in link_pattern.finditer(content):
                link = match.group(1).split("|")[0]  # Remove display text
                if not resolve_wiki_link(wiki_path, link):
                    # Normalize path to forward slashes
                    rel_path_str = str(rel_path).replace("\\", "/")
                    broken.append(
                        {
                            "file": rel_path_str,
                            "link": link,
                            "line": content[: match.start()].count("\n") + 1,
                        }
                    )
        except Exception:
            continue

    return broken


def categorize_content(content: str, filename: str) -> str:
    """
    AI-assisted categorization of content.

    Returns suggested folder path.
    """
    filename_lower = filename.lower()
    content_lower = content.lower()

    # Technical indicators
    tech_keywords = [
        "api",
        "database",
        "schema",
        "architecture",
        "code",
        "function",
        "class",
        "method",
        "endpoint",
        "migration",
        "bug",
        "error",
        "debug",
    ]

    # Business/external indicators
    business_keywords = [
        "investor",
        "pitch",
        "market",
        "revenue",
        "roi",
        "strategy",
        "campaign",
        "marketing",
        "press",
        "announcement",
        "stakeholder",
    ]

    # Hub/planning indicators
    hub_keywords = [
        "roadmap",
        "milestone",
        "task",
        "sprint",
        "backlog",
        "todo",
        "planning",
        "release",
        "version",
        "changelog",
        "retrospective",
    ]

    # Knowledge indicators
    knowledge_keywords = [
        "concept",
        "definition",
        "overview",
        "guide",
        "tutorial",
        "explanation",
        "introduction",
        "what is",
        "how to",
    ]

    # Count matches
    tech_score = sum(1 for kw in tech_keywords if kw in content_lower or kw in filename_lower)
    business_score = sum(
        1 for kw in business_keywords if kw in content_lower or kw in filename_lower
    )
    hub_score = sum(1 for kw in hub_keywords if kw in content_lower or kw in filename_lower)
    knowledge_score = sum(
        1 for kw in knowledge_keywords if kw in content_lower or kw in filename_lower
    )

    # Determine category
    scores = {
        "technical": tech_score,
        "external/business": business_score,
        "hub": hub_score,
        "knowledge": knowledge_score,
    }

    best_category = max(scores, key=scores.get)

    if scores[best_category] == 0:
        return "knowledge/concepts"  # Default

    # Refine subcategory based on content
    if best_category == "technical":
        if "api" in content_lower:
            return "technical/api"
        elif "database" in content_lower or "schema" in content_lower:
            return "technical/database"
        elif "architecture" in content_lower or "design" in content_lower:
            return "technical/architecture"
        elif "decision" in content_lower or "adr" in content_lower:
            return "technical/decisions"
        else:
            return "technical/troubleshooting"

    elif best_category == "hub":
        if "roadmap" in content_lower or "milestone" in content_lower:
            return "hub/roadmap"
        elif "task" in content_lower or "todo" in content_lower:
            return "hub/tasks"
        elif "changelog" in content_lower or "release" in content_lower:
            return "hub/changelog"
        elif "retrospective" in content_lower or "lesson" in content_lower:
            return "hub/retrospectives"
        else:
            return "hub/planning"

    elif best_category == "external/business":
        if "marketing" in content_lower or "campaign" in content_lower:
            return "external/marketing"
        elif "press" in content_lower or "media" in content_lower:
            return "external/press"
        else:
            return "external/business"

    else:  # knowledge
        if "guide" in content_lower or "tutorial" in content_lower or "how to" in content_lower:
            return "knowledge/guides"
        elif "concept" in content_lower or "definition" in content_lower:
            return "knowledge/concepts"
        elif "resource" in content_lower or "link" in content_lower:
            return "knowledge/resources"
        else:
            return "knowledge/domain"


def process_inbox_item(wiki_path: Path, filename: str, auto_move: bool = False) -> dict[str, Any]:
    """
    Process a single inbox item.

    Returns processing result with suggested action.
    """
    inbox_path = wiki_path / "inbox"
    file_path = inbox_path / filename

    if not file_path.exists():
        return {"error": f"File not found: {filename}"}

    # Read content
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": f"Cannot read file: {e}"}

    # Categorize
    suggested_folder = categorize_content(content, filename)

    # Extract title
    title = file_path.stem
    for line in content.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break

    result = {
        "file": filename,
        "title": title,
        "suggested_folder": suggested_folder,
        "content_preview": content[:200] + "..." if len(content) > 200 else content,
    }

    if auto_move:
        # Move file to suggested location
        dest_folder = wiki_path / suggested_folder
        dest_folder.mkdir(parents=True, exist_ok=True)
        dest_path = dest_folder / filename

        # Avoid overwriting
        if dest_path.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = dest_folder / f"{stem}-{counter}{suffix}"
                counter += 1

        shutil.move(str(file_path), str(dest_path))
        result["moved_to"] = str(dest_path.relative_to(wiki_path))

    return result


def update_index(wiki_path: Path):
    """Update the wiki index with all pages."""
    pages = list_wiki_pages(wiki_path)

    # Group by folder
    by_folder = {}
    for page in pages:
        folder = page["folder"]
        if folder not in by_folder:
            by_folder[folder] = []
        by_folder[folder].append(page)

    # Generate index content
    lines = [
        "# Wiki Index",
        "",
        "> Auto-generated index of all wiki pages.",
        "",
        f"**Total Pages:** {len(pages)}",
        "",
    ]

    # Add sections by top-level folder
    top_folders = ["knowledge", "technical", "hub", "external", "archive"]

    for top_folder in top_folders:
        folder_pages = [p for p in pages if p["folder"].startswith(top_folder)]
        if folder_pages:
            lines.append(f"## {top_folder.title()}")
            lines.append("")

            for page in folder_pages:
                lines.append(f"- [[{page['path'][:-3]}|{page['title']}]]")

            lines.append("")

    lines.extend(["---", f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*"])

    # Write index
    index_path = wiki_path / ".meta" / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")


# ============================================================================
# CLI Commands
# ============================================================================

wiki_app = typer.Typer(
    name="wiki", help="📚 Wiki & Knowledge Base Management", no_args_is_help=True
)


@wiki_app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", "-f", help="Reinitialize even if wiki exists"),
    global_wiki: bool = typer.Option(
        False, "--global", "-g", help="Initialize global wiki (~/.navig/wiki)"
    ),
):
    """Initialize wiki structure for current project."""
    config = ConfigManager()

    if global_wiki:
        wiki_path = get_global_wiki_path()
        location = "global"
    else:
        wiki_path = get_wiki_path(config)
        location = "project"

    if ensure_wiki_initialized(wiki_path) and not force:
        ch.warning(f"Wiki already initialized at {wiki_path}")
        ch.info("Use --force to reinitialize")
        return

    ch.info(f"Initializing {location} wiki at {wiki_path}...")

    if init_wiki(wiki_path, force):
        ch.success("✓ Wiki initialized successfully!")
        ch.dim("")
        ch.dim("Structure created:")
        ch.dim("  inbox/           - Drop files here for processing")
        ch.dim("  .meta/           - Wiki configuration")
        ch.dim("  knowledge/       - Encyclopedia & knowledge base")
        ch.dim("  technical/       - Technical documentation")
        ch.dim("  hub/             - Project command center")
        ch.dim("  external/        - External-facing content")
        ch.dim("  archive/         - Archived content")
        ch.dim("")
        ch.info("Next steps:")
        ch.dim("  navig wiki add <file>     - Add content to wiki")
        ch.dim("  navig wiki list           - View all pages")
    else:
        ch.error("Failed to initialize wiki")


@wiki_app.command("list")
def cmd_list(
    folder: str | None = typer.Argument(
        None, help="Folder to list (e.g., 'knowledge', 'technical')"
    ),
    all_pages: bool = typer.Option(
        False, "--all", "-a", help="Show all pages including subfolders"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List wiki pages."""
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    pages = list_wiki_pages(wiki_path, folder, recursive=all_pages or folder is not None)

    if not pages:
        if folder:
            ch.warning(f"No pages found in '{folder}'")
        else:
            ch.warning("No pages found. Use 'navig wiki add' to create content.")
        return

    if plain:
        for page in pages:
            print(page["path"])
        return

    from rich.table import Table

    table = Table(title="📚 Wiki Pages" + (f" - {folder}" if folder else ""))
    table.add_column("Page", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Folder", style="dim")
    table.add_column("Modified", style="dim")

    for page in pages:
        table.add_row(
            page["name"],
            page["title"][:40] + "..." if len(page["title"]) > 40 else page["title"],
            page["folder"],
            page["modified"].strftime("%Y-%m-%d"),
        )

    ch.console.print(table)
    ch.dim(f"\nTotal: {len(pages)} pages")


@wiki_app.command("show")
def cmd_show(
    page: str = typer.Argument(
        ..., help="Page name or path (e.g., 'concepts/overview' or 'overview')"
    ),
    raw: bool = typer.Option(False, "--raw", "-r", help="Show raw markdown without rendering"),
):
    """View a wiki page."""
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    # Resolve page path
    page_path = resolve_wiki_link(wiki_path, page)

    if not page_path:
        ch.error(f"Page not found: {page}")
        ch.dim("Use 'navig wiki list' to see available pages")
        raise typer.Exit(1)

    content = page_path.read_text(encoding="utf-8")

    if raw:
        print(content)
    else:
        from rich.markdown import Markdown
        from rich.panel import Panel

        rel_path = page_path.relative_to(wiki_path)
        ch.console.print(Panel(Markdown(content), title=f"📄 {rel_path}", border_style="blue"))


@wiki_app.command("add")
def cmd_add(
    file: Path = typer.Argument(..., help="File to add to wiki"),
    folder: str | None = typer.Option(
        None, "--folder", "-f", help="Destination folder (e.g., 'knowledge/concepts')"
    ),
    inbox: bool = typer.Option(False, "--inbox", "-i", help="Add to inbox for AI processing"),
):
    """Add a file to the wiki."""
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    if not file.exists():
        ch.error(f"File not found: {file}")
        raise typer.Exit(1)

    # Determine destination
    if inbox or (not folder):
        dest_folder = wiki_path / "inbox"
    else:
        dest_folder = wiki_path / folder
        dest_folder.mkdir(parents=True, exist_ok=True)

    # Copy file
    dest_path = dest_folder / file.name

    if dest_path.exists():
        if not typer.confirm(f"File exists at {dest_path}. Overwrite?"):
            raise typer.Exit(0)

    shutil.copy2(str(file), str(dest_path))

    rel_dest = dest_path.relative_to(wiki_path)
    ch.success(f"✓ Added to wiki: {rel_dest}")

    if inbox or (not folder):
        ch.info("File added to inbox. Run 'navig wiki inbox process' to categorize.")


@wiki_app.command("edit")
def cmd_edit(
    page: str = typer.Argument(..., help="Page name or path to edit"),
    editor: str | None = typer.Option(
        None, "--editor", "-e", help="Editor to use (default: $EDITOR)"
    ),
):
    """Open a wiki page in editor."""
    import os
    import subprocess

    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    # Resolve page path
    page_path = resolve_wiki_link(wiki_path, page)

    if not page_path:
        # Create new page?
        if typer.confirm(f"Page '{page}' doesn't exist. Create it?"):
            # Determine folder
            if "/" in page:
                folder = Path(page).parent
                page_path = wiki_path / folder / f"{Path(page).stem}.md"
            else:
                page_path = wiki_path / "knowledge" / "concepts" / f"{page}.md"

            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_text(
                f"# {Path(page).stem.replace('-', ' ').title()}\n\n", encoding="utf-8"
            )
            ch.success(f"✓ Created: {page_path.relative_to(wiki_path)}")
        else:
            raise typer.Exit(0)

    # Open in editor
    editor_cmd = editor or os.environ.get("EDITOR", "code")

    try:
        subprocess.run([editor_cmd, str(page_path)], check=True)
    except FileNotFoundError:
        ch.error(f"Editor not found: {editor_cmd}")
        ch.dim(f"File location: {page_path}")
    except subprocess.CalledProcessError:
        pass  # Editor exited with error, but that's ok


@wiki_app.command("remove")
def cmd_remove(
    page: str = typer.Argument(..., help="Page name or path to remove"),
    archive: bool = typer.Option(
        True, "--archive/--delete", help="Archive instead of delete (default: archive)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove (archive) a wiki page."""
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    page_path = resolve_wiki_link(wiki_path, page)

    if not page_path:
        ch.error(f"Page not found: {page}")
        raise typer.Exit(1)

    rel_path = page_path.relative_to(wiki_path)

    if not force:
        action = "archive" if archive else "permanently delete"
        if not typer.confirm(f"Are you sure you want to {action} '{rel_path}'?"):
            raise typer.Exit(0)

    if archive:
        # Move to archive with year folder
        year = datetime.now().strftime("%Y")
        archive_folder = wiki_path / "archive" / year
        archive_folder.mkdir(parents=True, exist_ok=True)

        dest_path = archive_folder / page_path.name
        shutil.move(str(page_path), str(dest_path))
        ch.success(f"✓ Archived: {rel_path} → archive/{year}/{page_path.name}")
    else:
        page_path.unlink()
        ch.success(f"✓ Deleted: {rel_path}")


@wiki_app.command("search")
def cmd_search(
    query: str = typer.Argument(..., help="Search query"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """Full-text search across wiki pages."""
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    results = search_wiki(wiki_path, query)

    if not results:
        ch.warning(f"No results found for: {query}")
        return

    if plain:
        for r in results:
            print(f"{r['path']}:{r['matches']}")
        return

    ch.success(f"Found {len(results)} pages matching '{query}':")
    ch.dim("")

    for r in results:
        ch.info(f"  📄 {r['path']} ({r['matches']} matches)")
        ch.dim(f"     {r['context']}")
        ch.dim("")


# Inbox subcommand group
inbox_app = typer.Typer(help="Inbox processing commands")
wiki_app.add_typer(inbox_app, name="inbox")


@inbox_app.callback(invoke_without_command=True)
def inbox_list(ctx: typer.Context):
    """List inbox items."""
    if ctx.invoked_subcommand is not None:
        return

    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    inbox_path = wiki_path / "inbox"
    items = list(inbox_path.glob("*"))
    items = [i for i in items if i.is_file()]

    if not items:
        ch.success("✓ Inbox is empty")
        return

    ch.info(f"📥 Inbox: {len(items)} items pending")
    ch.dim("")

    for item in items:
        stat = item.stat()
        ch.dim(f"  • {item.name} ({stat.st_size} bytes)")

    ch.dim("")
    ch.info("Run 'navig wiki inbox process' to categorize items")


@inbox_app.command("process")
def inbox_process(
    filename: str | None = typer.Argument(None, help="Specific file to process"),
    auto_move: bool = typer.Option(
        False, "--auto", "-a", help="Automatically move files to suggested folders"
    ),
):
    """Process inbox items with AI categorization."""
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    inbox_path = wiki_path / "inbox"

    if filename:
        items = [inbox_path / filename] if (inbox_path / filename).exists() else []
    else:
        items = [i for i in inbox_path.glob("*") if i.is_file()]

    if not items:
        ch.success("✓ No items to process")
        return

    ch.info(f"Processing {len(items)} item(s)...")
    ch.dim("")

    for item in items:
        result = process_inbox_item(wiki_path, item.name, auto_move)

        if "error" in result:
            ch.error(f"  ✗ {item.name}: {result['error']}")
            continue

        ch.info(f"  📄 {result['file']}")
        ch.dim(f"     Title: {result['title']}")
        ch.success(f"     Suggested: {result['suggested_folder']}")

        if "moved_to" in result:
            ch.success(f"     ✓ Moved to: {result['moved_to']}")
        elif not auto_move:
            ch.dim("     Use --auto to move automatically")

        ch.dim("")

    # Update index after processing
    if auto_move:
        update_index(wiki_path)
        ch.info("✓ Wiki index updated")


# Links subcommand group
links_app = typer.Typer(help="Wiki link management")
wiki_app.add_typer(links_app, name="links")


@links_app.callback(invoke_without_command=True)
def links_list(ctx: typer.Context):
    """Show wiki link statistics."""
    if ctx.invoked_subcommand is not None:
        return

    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    # Count all links
    link_pattern = re.compile(r"\[\[([^\]]+)\]\]")
    total_links = 0
    pages_with_links = 0

    for md_file in wiki_path.glob("**/*.md"):
        rel_path = md_file.relative_to(wiki_path)
        if any(part.startswith(".") for part in rel_path.parts):
            continue

        content = md_file.read_text(encoding="utf-8")
        matches = link_pattern.findall(content)
        if matches:
            total_links += len(matches)
            pages_with_links += 1

    broken = find_broken_links(wiki_path)

    ch.info("🔗 Wiki Links Statistics")
    ch.dim("")
    ch.dim(f"  Total links: {total_links}")
    ch.dim(f"  Pages with links: {pages_with_links}")
    ch.dim(f"  Broken links: {len(broken)}")

    if broken:
        ch.dim("")
        ch.warning("Run 'navig wiki links broken' to see broken links")


@links_app.command("broken")
def links_broken():
    """Find broken wiki links."""
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    broken = find_broken_links(wiki_path)

    if not broken:
        ch.success("✓ No broken links found")
        return

    ch.warning(f"Found {len(broken)} broken links:")
    ch.dim("")

    for b in broken:
        ch.error(f"  {b['file']}:{b['line']} → [[{b['link']}]]")


@wiki_app.command("publish")
def cmd_publish(
    preview: bool = typer.Option(False, "--preview", "-p", help="Preview what would be published"),
    include_private: bool = typer.Option(False, "--all", "-a", help="Include private content"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output directory"),
):
    """Publish public wiki content."""
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    # Check visibility settings
    visibility_file = wiki_path / "knowledge" / ".visibility"
    visibility = "private"
    if visibility_file.exists():
        content = visibility_file.read_text(encoding="utf-8")
        if "visibility: public" in content:
            visibility = "public"

    # Collect publishable pages
    publishable = []

    for md_file in wiki_path.glob("**/*.md"):
        rel_path = md_file.relative_to(wiki_path)

        # Skip hidden folders
        if any(part.startswith(".") for part in rel_path.parts):
            continue

        # Skip archive
        if str(rel_path).startswith("archive"):
            continue

        # Check folder visibility
        top_folder = rel_path.parts[0] if rel_path.parts else ""

        if top_folder == "knowledge":
            if visibility == "public" or include_private:
                publishable.append(rel_path)
        elif top_folder == "external":
            publishable.append(rel_path)  # Always public
        elif include_private:
            publishable.append(rel_path)

    if not publishable:
        ch.warning("No content to publish")
        ch.dim("Set 'visibility: public' in knowledge/.visibility to enable publishing")
        return

    if preview:
        ch.info(f"📤 Would publish {len(publishable)} pages:")
        ch.dim("")
        for p in publishable:
            ch.dim(f"  • {p}")
        return

    # Actual publishing
    output_dir = output or (wiki_path.parent / "wiki-export")
    output_dir.mkdir(parents=True, exist_ok=True)

    for rel_path in publishable:
        src = wiki_path / rel_path
        dest = output_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))

    ch.success(f"✓ Published {len(publishable)} pages to {output_dir}")


@wiki_app.command("sync")
def cmd_sync():
    """Sync with global wiki."""
    config = ConfigManager()
    project_wiki = get_wiki_path(config)
    global_wiki = get_global_wiki_path()

    if not ensure_wiki_initialized(project_wiki):
        ch.error("Project wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    if not ensure_wiki_initialized(global_wiki):
        ch.info("Global wiki not initialized. Initializing...")
        init_wiki(global_wiki)

    ch.success("✓ Wiki sync complete")
    ch.dim(f"  Project: {project_wiki}")
    ch.dim(f"  Global:  {global_wiki}")
    ch.dim("")
    ch.dim("Use [[global:page]] syntax to reference global wiki pages")


# RAG (Retrieval-Augmented Generation) subcommand group
rag_app = typer.Typer(help="RAG knowledge base for AI assistants")
wiki_app.add_typer(rag_app, name="rag")


@rag_app.callback(invoke_without_command=True)
def rag_status(ctx: typer.Context):
    """Show RAG index status."""
    if ctx.invoked_subcommand is not None:
        return

    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    from navig.wiki_rag import get_wiki_rag

    rag = get_wiki_rag(wiki_path)
    stats = rag.get_stats()

    ch.header("🤖 Wiki RAG Status")
    ch.dim("")
    ch.info(f"📄 Documents: {stats['total_documents']}")
    ch.info(f"📝 Chunks: {stats['total_chunks']}")
    ch.info(f"📊 Words: {stats['total_words']}")
    ch.info(f"🔤 Unique terms: {stats['unique_terms']}")
    ch.info(f"📏 Avg doc length: {stats['avg_doc_length']} words")
    ch.dim("")
    ch.dim("Use 'navig wiki rag query <question>' to test semantic search")


@rag_app.command("query")
def rag_query(
    query: str = typer.Argument(..., help="Natural language query"),
    limit: int = typer.Option(5, "--limit", "-l", help="Max results"),
    context: bool = typer.Option(False, "--context", "-c", help="Show full context for AI"),
):
    """Query the wiki knowledge base.

    Uses BM25 semantic search to find relevant content.

    Examples:
        navig wiki rag query "how to deploy docker"
        navig wiki rag query "nginx configuration" --context
    """
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    from navig.wiki_rag import get_wiki_rag

    rag = get_wiki_rag(wiki_path)

    if context:
        # Return formatted context for AI
        ctx_text = rag.get_context(query)
        ch.console.print(ctx_text)
        return

    results = rag.search(query, top_k=limit)

    if not results:
        ch.warning(f"No results found for: {query}")
        return

    ch.success(f"Found {len(results)} results for: {query}")
    ch.dim("")

    for i, r in enumerate(results, 1):
        ch.info(f"{i}. 📄 {r['title']} [score: {r['score']}]")
        ch.dim(f"   Path: {r['path']}")
        ch.dim(f"   {r['chunk'][:150]}...")
        ch.dim("")


@rag_app.command("rebuild")
def rag_rebuild():
    """Rebuild the RAG index from wiki pages.

    Run this after making many changes to wiki content.
    """
    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    from navig.wiki_rag import get_wiki_rag

    ch.info("Rebuilding RAG index...")
    rag = get_wiki_rag(wiki_path)
    rag.rebuild_index()

    stats = rag.get_stats()
    ch.success(f"✓ Indexed {stats['total_documents']} documents ({stats['total_chunks']} chunks)")


@rag_app.command("add")
def rag_add(
    content: str = typer.Argument(..., help="Content to add (text or file path)"),
    title: str | None = typer.Option(None, "--title", "-t", help="Document title"),
    path: str | None = typer.Option(None, "--path", "-p", help="Wiki path for the document"),
):
    """Add content directly to the RAG knowledge base.

    You can add text content or a file path.

    Examples:
        navig wiki rag add "Docker uses containers for isolation" -t "Docker Basics"
        navig wiki rag add ./docs/notes.md -p knowledge/notes
    """
    from pathlib import Path as PathLib

    config = ConfigManager()
    wiki_path = get_wiki_path(config)

    if not ensure_wiki_initialized(wiki_path):
        ch.error("Wiki not initialized. Run: navig wiki init")
        raise typer.Exit(1)

    from navig.wiki_rag import get_wiki_rag

    # Check if content is a file path
    content_path = PathLib(content)
    if content_path.exists() and content_path.is_file():
        file_content = content_path.read_text(encoding="utf-8")
        if not title:
            title = content_path.stem
        if not path:
            path = f"knowledge/{content_path.stem}.md"
    else:
        file_content = content
        if not title:
            title = "Untitled"
        if not path:
            path = f"knowledge/{title.lower().replace(' ', '-')}.md"

    # Also write to wiki filesystem
    dest = wiki_path / path
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Add markdown header if missing
    if not file_content.strip().startswith("#"):
        file_content = f"# {title}\n\n{file_content}"

    dest.write_text(file_content, encoding="utf-8")

    # Add to RAG index
    rag = get_wiki_rag(wiki_path)
    rag.add_document(path, file_content, title)

    ch.success(f"✓ Added to wiki and RAG index: {path}")


# Export the app
__all__ = ["wiki_app"]
