"""
NAVIG Farmore CLI Commands

Wraps the `farmore` GitHub repository mirroring tool.
Token resolution order:
  1. navig vault  (secret name: github_token)
  2. GITHUB_TOKEN env var
  3. ~/.navig/config.yaml  → github.token
  4. No token → falls back to navig-native tools (navig run / git clone)
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

farmore_app = typer.Typer(
    name="farmore",
    help="🥔 GitHub repo mirroring & backup via farmore",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _resolve_github_token() -> Optional[str]:
    """
    Resolve a GitHub token with the full navig credential chain.

    Priority:
      1. navig vault  (secret name: github_token)
      2. GITHUB_TOKEN environment variable
      3. ~/.navig/config.yaml → github.token

    Returns None if not found anywhere.
    """
    # 1 — navig vault
    try:
        from navig.vault import get_vault  # type: ignore

        vault = get_vault()
        secret = vault.get("github_token", caller="farmore")
        if secret and getattr(secret, "value", None):
            return secret.value
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # 2 — env var
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token

    # 3 — ~/.navig/config.yaml
    try:
        import yaml  # type: ignore

        cfg_path = Path.home() / ".navig" / "config.yaml"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
            token = cfg.get("github", {}).get("token", "").strip()
            if token:
                return token
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    return None


def _farmore_available() -> bool:
    """Return True if the `farmore` CLI is importable."""
    try:
        import farmore  # noqa: F401

        return True
    except ImportError:
        return False


def _require_farmore() -> bool:
    """
    Ensure farmore is installed; print help if missing.
    Returns True if available.
    """
    if _farmore_available():
        return True
    ch.error(
        "farmore is not installed. Install it with:\n"
        "  pip install farmore\n"
        "or (from source):\n"
        "  pip install -e K:\\_PROJECTS_OPENSOURCE\\farmore"
    )
    return False


def _run_farmore(args: list[str], token: Optional[str]) -> None:
    """Invoke the farmore CLI as a subprocess, injecting token if available."""
    env = os.environ.copy()
    if token:
        env["GITHUB_TOKEN"] = token

    cmd = [sys.executable, "-m", "farmore"] + args
    try:
        result = subprocess.run(cmd, env=env)
        if result.returncode != 0:
            raise typer.Exit(result.returncode)
    except KeyboardInterrupt:
        pass  # user interrupted; clean exit


# ---------------------------------------------------------------------------
# Fallback: navig-native git clone (no token required)
# ---------------------------------------------------------------------------


def _fallback_git_clone(repo_url: str, dest: str) -> None:
    """
    Minimal git-based clone used when no GitHub token is available.
    Leverages the git binary already required by navig.
    """
    ch.warning("No GitHub token found — using plain `git clone` (public repos only).")
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    clone_dest = dest_path / repo_name

    if clone_dest.exists():
        ch.info(f"Updating existing repo at {clone_dest} …")
        result = subprocess.run(
            ["git", "-C", str(clone_dest), "pull", "--ff-only"],
            capture_output=True,
            text=True,
        )
    else:
        ch.info(f"Cloning {repo_url} → {clone_dest} …")
        result = subprocess.run(
            ["git", "clone", repo_url, str(clone_dest)], capture_output=True, text=True
        )

    if result.returncode == 0:
        ch.success(f"Done: {clone_dest}")
    else:
        ch.error(result.stderr.strip() or "git clone failed")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# sub-commands
# ---------------------------------------------------------------------------


@farmore_app.command("search")
def farmore_search(
    query: Annotated[str, typer.Argument(help="Search keyword (e.g. 'agent soul')")],
    output_dir: Annotated[
        Optional[Path], typer.Option("--output-dir", "-o", help="Destination directory")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-l", min=1, max=100, help="Max repos to clone")
    ] = 20,
    language: Annotated[
        Optional[str], typer.Option("--language", help="Filter by language")
    ] = None,
    min_stars: Annotated[
        Optional[int], typer.Option("--min-stars", help="Minimum star count")
    ] = None,
    sort: Annotated[
        str, typer.Option("--sort", help="Sort: stars|forks|updated|best-match")
    ] = "stars",
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Auto-confirm, no prompt")
    ] = False,
    workers: Annotated[
        int, typer.Option("--workers", "-w", help="Parallel clone workers")
    ] = 4,
    token: Annotated[
        Optional[str],
        typer.Option("--token", "-t", help="GitHub token (overrides auto-resolve)"),
    ] = None,
):
    """
    🔍 Search GitHub and clone matching repositories.

    Token resolution: vault → GITHUB_TOKEN env → ~/.navig/config.yaml.
    Without a token only public repos at low rate limits are accessible.

    Examples:
        navig farmore search "agent soul" -o K:\\_PROJECTS\\navig\\.lab\\.sou --limit 50 -y
        navig farmore search "machine learning" --language python --min-stars 500
    """
    resolved_token = token or _resolve_github_token()

    if not resolved_token:
        ch.warning(
            "No GitHub token found. Using unauthenticated access (60 req/hour).\n"
            "Set one with:  navig farmore token set <your-token>"
        )

    if not _require_farmore():
        raise typer.Exit(1)

    args = [
        "search",
        query,
        "--limit",
        str(limit),
        "--sort",
        sort,
        "--workers",
        str(workers),
    ]
    if output_dir:
        args += ["--output-dir", str(output_dir)]
    if language:
        args += ["--language", language]
    if min_stars is not None:
        args += ["--min-stars", str(min_stars)]
    if yes:
        args += ["--yes"]

    _run_farmore(args, resolved_token)


@farmore_app.command("backup")
def farmore_backup(
    target: Annotated[str, typer.Argument(help="GitHub username or org to backup")],
    dest: Annotated[
        Optional[Path], typer.Option("--dest", "-d", help="Destination directory")
    ] = None,
    visibility: Annotated[
        str, typer.Option("--visibility", help="all|public|private")
    ] = "all",
    workers: Annotated[
        int, typer.Option("--workers", "-w", help="Parallel clone workers")
    ] = 4,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-confirm")] = False,
    token: Annotated[
        Optional[str], typer.Option("--token", "-t", help="GitHub token")
    ] = None,
):
    """
    📦 Clone / mirror every repo for a user or organisation.

    Examples:
        navig farmore backup myuser -d K:\\_PROJECTS\\mirrors -y
        navig farmore backup myorg --visibility public
    """
    resolved_token = token or _resolve_github_token()

    if not resolved_token:
        ch.warning(
            "No GitHub token found — only public repos accessible.\n"
            "Set a token with:  navig farmore token set <your-token>"
        )

    if not _require_farmore():
        raise typer.Exit(1)

    args = ["backup", target, "--visibility", visibility, "--workers", str(workers)]
    if dest:
        args += ["--dest", str(dest)]
    if yes:
        args += ["--yes"]

    _run_farmore(args, resolved_token)


@farmore_app.command("clone")
def farmore_clone(
    repo: Annotated[str, typer.Argument(help="owner/repo or full GitHub URL")],
    dest: Annotated[
        Optional[Path], typer.Option("--dest", "-d", help="Destination directory")
    ] = None,
    token: Annotated[
        Optional[str], typer.Option("--token", "-t", help="GitHub token")
    ] = None,
):
    """
    ⬇️  Clone a single repository (with farmore if available, else plain git).

    Falls back to a plain `git clone` when no token is configured and
    farmore is unavailable.

    Examples:
        navig farmore clone owner/my-repo -d K:\\_PROJECTS\\mirrors
        navig farmore clone https://github.com/owner/repo
    """
    resolved_token = token or _resolve_github_token()

    # Normalise URL
    if not repo.startswith("http") and "/" in repo:
        repo_url = f"https://github.com/{repo}"
    else:
        repo_url = repo

    if not _farmore_available():
        # Graceful fallback: plain git clone
        _fallback_git_clone(repo_url, str(dest or Path.cwd()))
        return

    args = ["repo", repo_url]
    if dest:
        args += ["--dest", str(dest)]

    _run_farmore(args, resolved_token)


# ---------------------------------------------------------------------------
# Token management sub-commands
# ---------------------------------------------------------------------------

token_app = typer.Typer(
    name="token",
    help="Manage the GitHub token used by farmore",
    no_args_is_help=True,
)
farmore_app.add_typer(token_app, name="token")


@token_app.command("set")
def token_set(
    value: Annotated[str, typer.Argument(help="GitHub Personal Access Token")],
    use_vault: Annotated[
        bool, typer.Option("--vault/--no-vault", help="Store in navig vault (default)")
    ] = True,
):
    """
    🔑 Save a GitHub token for farmore to use automatically.

    Stores in the navig vault by default (preferred). Falls back to
    writing to ~/.navig/config.yaml when the vault is unavailable.

    Examples:
        navig farmore token set ghp_xxxxxxxxxxxxxxxx
    """
    if use_vault:
        try:
            from navig.vault import get_vault  # type: ignore

            vault = get_vault()
            existing = vault.get("github_token", caller="farmore.token_set")
            if existing:
                vault.update(existing.id, data={"value": value})
                ch.success("GitHub token updated in navig vault.")
            else:
                vault.add(
                    name="github_token",
                    data={"value": value},
                    metadata={"description": "GitHub PAT used by farmore"},
                )
                ch.success("GitHub token saved to navig vault.")
            return
        except Exception as exc:
            ch.warning(f"Vault unavailable ({exc}), falling back to config file.")

    # Fallback: write to ~/.navig/config.yaml
    try:
        import yaml  # type: ignore

        cfg_path = Path.home() / ".navig" / "config.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
        cfg = cfg or {}
        cfg.setdefault("github", {})["token"] = value
        cfg_path.write_text(yaml.dump(cfg, default_flow_style=False))
        ch.success(f"GitHub token saved to {cfg_path}")
    except Exception as exc:
        ch.error(f"Failed to save token: {exc}")
        raise typer.Exit(1) from exc


@token_app.command("show")
def token_show():
    """
    🔍 Display where the current GitHub token comes from (masked).

    Examples:
        navig farmore token show
    """
    token = _resolve_github_token()
    if token:
        masked = token[:6] + "..." + token[-4:]
        ch.success(f"GitHub token found: {masked}")
        # Identify source
        try:
            from navig.vault import get_vault  # type: ignore

            v = get_vault()
            s = v.get("github_token", caller="farmore.token_show")
            if s and getattr(s, "value", None) == token:
                ch.info("Source: navig vault")
                return
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        if os.environ.get("GITHUB_TOKEN", "").strip() == token:
            ch.info("Source: GITHUB_TOKEN env var")
        else:
            ch.info("Source: ~/.navig/config.yaml")
    else:
        ch.warning(
            "No GitHub token configured.\n"
            "Set one with:  navig farmore token set <your-token>\n"
            "Without a token, farmore only accesses public repos at low rate limits."
        )


@token_app.command("remove")
def token_remove():
    """
    🗑  Remove the stored GitHub token.

    Examples:
        navig farmore token remove
    """
    removed = False

    # Vault
    try:
        from navig.vault import get_vault  # type: ignore

        vault = get_vault()
        secret = vault.get("github_token", caller="farmore.token_remove")
        if secret:
            vault.delete(secret.id)
            ch.success("GitHub token removed from navig vault.")
            removed = True
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Config file
    try:
        import yaml  # type: ignore

        cfg_path = Path.home() / ".navig" / "config.yaml"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
            if cfg.get("github", {}).get("token"):
                cfg["github"].pop("token")
                cfg_path.write_text(yaml.dump(cfg, default_flow_style=False))
                ch.success(f"GitHub token removed from {cfg_path}")
                removed = True
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    if not removed:
        ch.info("No stored GitHub token found.")


@farmore_app.command("status")
def farmore_status():
    """
    ℹ️  Show farmore installation status and token configuration.

    Examples:
        navig farmore status
    """
    # farmore availability
    if _farmore_available():
        try:
            import farmore as fm  # type: ignore

            version = getattr(fm, "__version__", "unknown")
            ch.success(f"farmore installed — version {version}")
        except Exception:
            ch.success("farmore installed")
    else:
        ch.error("farmore NOT installed.")
        ch.info("Install with:  pip install farmore")
        ch.info("  or from source:  pip install -e K:\\_PROJECTS_OPENSOURCE\\farmore")

    # Token check
    token = _resolve_github_token()
    if token:
        masked = token[:6] + "..." + token[-4:]
        ch.success(f"GitHub token configured: {masked}")
    else:
        ch.warning(
            "No GitHub token — only public repos accessible at low rate limits.\n"
            "Set one with:  navig farmore token set <your-token>"
        )
