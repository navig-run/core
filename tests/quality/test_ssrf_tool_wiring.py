"""Every agent tool that fetches a caller-supplied URL must route it through the SSRF guard.

An *agent tool* is any ``BaseTool`` subclass — the model invokes it with arguments the model
chose. They do NOT all live in ``navig/tools/``: ``navig/agent/tools/`` holds more of them
(14 modules vs 9), and plugins ship them too, so the scope here is DERIVED from
``class X(BaseTool)`` rather than from a directory name.

When a tool makes an outbound HTTP request to a URL the **caller controls**, an
unguarded fetch lets the model — or a prompt-injection riding the model — reach the cloud
metadata endpoint (``169.254.169.254``), ``localhost`` services, or any host on the internal
network. That is a classic SSRF, and it is exactly the class ``navig.net.ssrf`` exists to
close (``check_url`` / ``safe_fetch`` / ``safe_get`` + ``policy_from_config``). The audit that
found ``site_check`` fetching a model-supplied URL with no guard (#545) is the reason this file
exists: a guard is only as strong as its *wiring*, and the wiring had a hole.

That directory assumption was itself an instance of the bug it guards against: a check bound
to one PATH does not protect the SURFACE. It read as though it covered every agent tool while
seeing 9 of 24.

This test is the lock on that door. It flags any tool that imports a direct outbound-HTTP
client (``httpx`` / ``requests`` / ``aiohttp`` / ``urllib.request``) but does **not** import
``navig.net.ssrf``. The remaining legitimate cases — tools that only ever reach a **fixed
provider host** the caller cannot influence (OpenAI, ElevenLabs, api.telegram.org, …), so the
target is never attacker-chosen — are an explicit ALLOWLIST, each with the reason it is not an
SSRF surface. A new tool that fetches a caller-supplied URL without the guard fails the build
with a pointer to the helper; a new fixed-host provider tool adds one allowlist line, which
forces a conscious "is this URL caller-controlled?" decision at the point the fetch is added.

What this guard does and does NOT prove: it proves the *habit* (a URL-fetching tool pulled in
the SSRF module), not that every fetch inside the file is actually validated — the same shape
as the ``(x/'.git').exists()`` guard. It is a tripwire against a whole class of omission, not a
correctness proof for a single call site.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Locate sources by path, never import them (fast; usable as a pre-push gate even when the
# tree does not import cleanly). core/tests/quality/<this> -> parents[2] == core.
_NAVIG_ROOT = Path(__file__).resolve().parents[2] / "navig"
_TOOLS_ROOT = _NAVIG_ROOT / "tools"
_PLUGINS_ROOT = _NAVIG_ROOT.parents[1] / "plugins"

# Directories whose .py files are not shipped runtime source.
_SKIP_PARTS = frozenset({"__pycache__", "build", "dist", "tests", "test", ".venv"})

# Direct outbound-HTTP clients. A tool that imports one of these makes network requests
# itself (as opposed to delegating to an already-guarded navig helper).
_HTTP_LIBS = frozenset({"httpx", "requests", "aiohttp"})

# Tools that DO outbound HTTP but only ever to a FIXED provider host the caller cannot choose,
# so the request target is not attacker-controlled and there is no SSRF surface. Keyed
# "navig/tools/<relpath>" -> reason. Every entry is proven still-live by
# test_allowlist_has_no_stale_entries.
_ALLOWLIST: dict[str, str] = {
    "navig/tools/image_generation.py": (
        "fixed provider hosts (OpenAI / Recraft / Stability / Gemini) + a provider-returned "
        "download URL + an operator-set local_api_url — never a per-call caller-supplied host"
    ),
    "navig/tools/video_generation.py": (
        "fixed provider hosts (Gemini / Replicate / Runway / Luma) + provider-returned asset "
        "and poll URLs — never a per-call caller-supplied host"
    ),
    "navig/tools/audio_generation.py": "fixed provider host api.elevenlabs.io",
    "navig/tools/telegram_approval_backend.py": "fixed host api.telegram.org (Bot API)",
}


# ── detection ───────────────────────────────────────────────────────────────


def _imports_http_client(tree: ast.AST) -> bool:
    """True if *tree* imports a direct outbound-HTTP client anywhere (module level, inside a
    function, or under ``TYPE_CHECKING`` — ``ast.walk`` sees them all, and several tools import
    their client function-locally)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _HTTP_LIBS or alias.name.startswith("urllib.request"):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[0] in _HTTP_LIBS or mod.startswith("urllib.request"):
                return True
    return False


def _imports_ssrf_guard(tree: ast.AST) -> bool:
    """True if *tree* imports from ``navig.net.ssrf`` (module level or function-local — both
    ``site_check``/``browser_fetch`` (top-level) and ``web``/``api_pack`` (function-local) do)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and "ssrf" in (node.module or ""):
            return True
    return False


def _defines_a_tool(tree: ast.AST) -> bool:
    """True if the file declares a ``class X(BaseTool)`` — i.e. a MODEL-INVOKED tool.

    This is what makes a file an SSRF surface: the model chooses the arguments. The
    directory it happens to live in does not.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            isinstance(b, ast.Name) and b.id == "BaseTool" for b in node.bases
        ):
            return True
    return False


def _key(py: Path) -> str:
    """Stable repo-relative key: 'navig/…' for core, 'plugins/…' for plugin tools."""
    try:
        return f"navig/{py.relative_to(_NAVIG_ROOT).as_posix()}"
    except ValueError:
        return f"plugins/{py.relative_to(_PLUGINS_ROOT).as_posix()}"


def _tool_files() -> list[Path]:
    """Every model-invoked tool file, DERIVED rather than hardcoded to one directory.

    The scope used to be `navig/tools/` alone, and the docstring above defined an agent
    tool as "anything under navig/tools/". That was factually wrong: `navig/agent/tools/`
    holds MORE `BaseTool` subclasses than `navig/tools/` does (14 files vs 9), and plugins
    ship them too — so the guard protected 9 of 24 tool modules while reading as though it
    covered them all. A guard bound to a directory protects one PATH to the surface, not
    the surface; deriving the set from `class X(BaseTool)` means a new tool tree is covered
    the moment it lands, with nobody having to remember this file exists.

    `navig/tools/**` is still swept wholesale on top of that, so helper modules there that
    fetch on a tool's behalf without subclassing `BaseTool` keep their coverage.
    """
    seen: dict[Path, None] = {}
    roots = [_NAVIG_ROOT] + ([_PLUGINS_ROOT] if _PLUGINS_ROOT.is_dir() else [])
    for root in roots:
        for py in root.rglob("*.py"):
            if _SKIP_PARTS & set(py.parts):
                continue
            if _TOOLS_ROOT not in py.parents:
                try:
                    src = py.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    continue
                # Cheap pre-filter before the AST parse: widening the scope to the whole
                # tree means ~1500 files, and parsing every one of them made this guard
                # 17s. A file that never mentions BaseTool cannot subclass it.
                if "BaseTool" not in src:
                    continue
                try:
                    if not _defines_a_tool(ast.parse(src)):
                        continue
                except SyntaxError:
                    continue
            seen[py] = None
    return list(seen)


def _scan() -> dict[str, bool]:
    """{ '<repo-rel path>': imports_ssrf } for every tool that imports an outbound-HTTP client."""
    found: dict[str, bool] = {}
    for py in _tool_files():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if not _imports_http_client(tree):
            continue
        found[_key(py)] = _imports_ssrf_guard(tree)
    return found


# ── the guard ───────────────────────────────────────────────────────────────


def test_every_outbound_tool_routes_through_ssrf_guard():
    """No agent tool may fetch a caller-supplied URL without the SSRF guard. Every outbound-HTTP
    tool either imports ``navig.net.ssrf`` or is an allowlisted fixed-host provider tool."""
    offenders = [
        rel
        for rel, has_ssrf in _scan().items()
        if not has_ssrf and rel not in _ALLOWLIST
    ]
    assert not offenders, (
        "outbound-HTTP tool(s) that do NOT route through the SSRF guard — a model-supplied URL "
        "could reach cloud metadata (169.254.169.254), localhost, or an internal host. Fetch via "
        "`navig.net.ssrf` (`check_url` / `safe_fetch` / `safe_get` + `policy_from_config`), like "
        "web.py / browser_fetch.py / site_check.py do — or, if the host is a FIXED provider the "
        "caller cannot choose, add an ALLOWLIST entry with that reason:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_has_no_stale_entries():
    """Every ALLOWLIST file must still exist, still import an outbound-HTTP client, AND still
    lack the SSRF guard — otherwise the entry is stale. A file that has since been wired to the
    guard must lose its allowlist line so the guard actually protects it going forward."""
    scanned = _scan()
    raw_unguarded = {rel for rel, has_ssrf in scanned.items() if not has_ssrf}
    stale = [rel for rel in _ALLOWLIST if rel not in raw_unguarded]
    assert not stale, (
        "stale ALLOWLIST entries (file gone, no longer does outbound HTTP, or now imports the "
        f"SSRF guard and no longer needs allowlisting) — remove them: {stale}"
    )


# ── the detector must keep its teeth (a blind guard is worse than none) ───────


def test_detector_flags_an_httpx_import():
    src = "import httpx\ndef f(u):\n    return httpx.get(u)\n"
    assert _imports_http_client(ast.parse(src)) is True


def test_detector_flags_function_local_and_urllib_imports():
    aiohttp_local = "def f():\n    import aiohttp\n    return aiohttp\n"
    assert _imports_http_client(ast.parse(aiohttp_local)) is True
    urllib_from = "from urllib.request import urlopen\ndef f(u):\n    return urlopen(u)\n"
    assert _imports_http_client(ast.parse(urllib_from)) is True


def test_detector_recognises_the_ssrf_guard_import():
    top = "from navig.net.ssrf import check_url, policy_from_config\n"
    assert _imports_ssrf_guard(ast.parse(top)) is True
    local = "def f():\n    from navig.net.ssrf import safe_get\n    return safe_get\n"
    assert _imports_ssrf_guard(ast.parse(local)) is True


def test_detector_ignores_a_non_fetching_tool():
    # A tool that does no outbound HTTP (dict .get, no client import) is not in scope.
    src = "def f(d):\n    return d.get('key')\n"
    assert _imports_http_client(ast.parse(src)) is False
    assert _imports_ssrf_guard(ast.parse(src)) is False


def test_the_scope_covers_every_tool_tree_not_just_one_directory() -> None:
    """The scope must stay DERIVED. This is the regression that motivated it.

    `navig/agent/tools/` holds more `BaseTool` subclasses than `navig/tools/` does, and a
    plugin can ship them as well — but the scan used to walk `navig/tools/` only, so a
    URL-fetching tool added anywhere else would never have been checked. Nothing failed at
    the time (none of those modules fetch today), which is exactly why it needed pinning:
    the hole was latent, and a latent hole in a security guard reads as coverage.
    """
    scanned = {p.resolve() for p in _tool_files()}

    agent_tools = {
        p.resolve()
        for p in (_NAVIG_ROOT / "agent" / "tools").rglob("*.py")
        if not (_SKIP_PARTS & set(p.parts))
    }
    assert agent_tools, "navig/agent/tools/ vanished — re-point this test at its new home"
    assert agent_tools & scanned, (
        "no file under navig/agent/tools/ is in scope — the scan has been narrowed back to "
        "one directory, and the largest tree of model-invoked tools is unguarded again"
    )


def test_a_tool_is_recognised_by_shape_not_by_directory() -> None:
    """Anti-vacuity for the derivation: `class X(BaseTool)` is what makes it a tool."""
    tool_src = (
        "import requests\n"
        "class Thing(BaseTool):\n"
        "    def run(self, url):\n"
        "        return requests.get(url)\n"
    )
    tree = ast.parse(tool_src)
    assert _defines_a_tool(tree) is True
    assert _imports_http_client(tree) is True
    assert _imports_ssrf_guard(tree) is False, "the detector saw a guard that is not there"

    plain_src = "import requests\nclass Helper:\n    pass\n"
    assert _defines_a_tool(ast.parse(plain_src)) is False, (
        "a plain class was treated as a model-invoked tool — that would drag most of the "
        "tree into an SSRF guard it does not belong in, and a guard that cries wolf gets "
        "deleted"
    )
