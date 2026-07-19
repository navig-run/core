"""navig.skills.autodetect — deterministic project → skills detection.

The engine behind ``navig skills auto``. A deterministic technology-map scanner:
scan a project's manifests / config files / file extensions, match a declarative
**technology map**, and resolve the agent skills to install.

Why native (vs. the fuzzy word-overlap in ``compute_skill_suggestions``): this is
*deterministic* — "you have `next` in package.json" → install the Next.js skills,
no guessing. ``SKILL.md`` is the shared format (Claude Code / Codex compatible —
see :mod:`navig.skills.federation`), so a resolved
``github:owner/repo/skill`` spec installs through the normal
``navig skill install`` path (:func:`navig.commands.install.install_asset`).

The map (:data:`TECH_SKILLS`) is **data** — extend it freely. Skill specs are
either ``owner/repo/skill`` (public agent-skill repos, installed as
``github:owner/repo/skill``) or a bare navig-community skill id.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# The curated FLAT skills registry that resolves a skill *name* → files
# (`<registry>/<name>/SKILL.md`, hashed + prompt-injection-scanned). Skills are
# keyed by name, NOT by owner/repo — so the map's `owner/repo/skill` refs are
# *attribution*; the installable files live here. Defaults to a public skills
# registry; point `NAVIG_SKILLS_REGISTRY` at a navig-owned mirror to swap it
# with no code change.
DEFAULT_SKILLS_REGISTRY = "midudev/autoskills/packages/autoskills/skills-registry"


@dataclass(frozen=True)
class TechRule:
    """One technology: how to detect it + which skills it pulls in.

    A rule matches if ANY of its signals hit: a JS dep in `packages`, a Python
    dep in `py_packages`, an existing file in `config_files`, or any file with an
    extension in `file_extensions`. `skills` are install specs (see module doc).
    """

    id: str
    name: str
    skills: tuple[str, ...]
    packages: tuple[str, ...] = ()
    py_packages: tuple[str, ...] = ()
    config_files: tuple[str, ...] = ()
    file_extensions: tuple[str, ...] = ()


# ── Technology → skills map ───────────────────────────────────────────────────
# Skill refs are real public agent-skill repos (owner/repo/skill) unless prefixed
# `community:` (a navig-community CLI skill). Curated to NAVIG-relevant stacks;
# add rows freely — this is just data.
TECH_SKILLS: tuple[TechRule, ...] = (
    # ── Frontend frameworks ───────────────────────────────────────────────────
    TechRule("react", "React", ("vercel-labs/agent-skills/react-best-practices",
             "vercel-labs/agent-skills/composition-patterns"), packages=("react", "react-dom")),
    TechRule("nextjs", "Next.js", ("vercel-labs/next-skills/next-best-practices",
             "vercel-labs/next-skills/next-cache-components"), packages=("next",),
             config_files=("next.config.js", "next.config.mjs", "next.config.ts")),
    TechRule("vue", "Vue", ("antfu/skills/vue",), packages=("vue",)),
    TechRule("nuxt", "Nuxt", ("antfu/skills/nuxt",), packages=("nuxt",),
             config_files=("nuxt.config.js", "nuxt.config.ts")),
    TechRule("svelte", "Svelte", ("ejirocodes/agent-skills/svelte5-best-practices",),
             packages=("svelte", "@sveltejs/kit"), config_files=("svelte.config.js",)),
    TechRule("angular", "Angular", ("angular/skills/angular-developer",),
             packages=("@angular/core",), config_files=("angular.json",)),
    TechRule("astro", "Astro", ("astrolicious/agent-skills/astro",), packages=("astro",),
             config_files=("astro.config.mjs", "astro.config.js", "astro.config.ts")),
    TechRule("tailwind", "Tailwind CSS", ("giuseppe-trisciuoglio/developer-kit/tailwind-css-patterns",),
             packages=("tailwindcss", "@tailwindcss/vite"),
             config_files=("tailwind.config.js", "tailwind.config.ts")),
    TechRule("shadcn", "shadcn/ui", ("shadcn/ui/shadcn",), config_files=("components.json",)),
    TechRule("vite", "Vite", ("antfu/skills/vite",), packages=("vite",),
             config_files=("vite.config.js", "vite.config.ts", "vite.config.mjs")),
    TechRule("react-hook-form", "React Hook Form", ("pproenca/dot-skills/react-hook-form",),
             packages=("react-hook-form",)),
    TechRule("zod", "Zod", ("pproenca/dot-skills/zod",), packages=("zod",)),
    TechRule("turborepo", "Turborepo", ("vercel/turborepo/turborepo",), packages=("turbo",),
             config_files=("turbo.json",)),
    # ── JS/TS language + runtimes ─────────────────────────────────────────────
    TechRule("typescript", "TypeScript", ("wshobson/agents/typescript-advanced-types",),
             packages=("typescript",), config_files=("tsconfig.json",)),
    TechRule("node", "Node.js", ("wshobson/agents/nodejs-backend-patterns",),
             config_files=("package-lock.json", "yarn.lock", "pnpm-lock.yaml", ".nvmrc")),
    TechRule("bun", "Bun", ("midudev/autoskills/bun",),
             config_files=("bun.lockb", "bun.lock", "bunfig.toml")),
    TechRule("deno", "Deno", ("denoland/skills/deno-expert",),
             config_files=("deno.json", "deno.jsonc", "deno.lock")),
    # ── JS backend ────────────────────────────────────────────────────────────
    TechRule("hono", "Hono", ("yusukebe/hono-skill/hono",), packages=("hono",)),
    TechRule("nestjs", "NestJS", ("kadajett/agent-nestjs-skills/nestjs-best-practices",),
             packages=("@nestjs/core",)),
    TechRule("elysia", "Elysia", ("elysiajs/skills/elysiajs",), packages=("elysia",)),
    TechRule("vercel-ai", "Vercel AI SDK", ("vercel/ai/use-ai-sdk",),
             packages=("ai", "@ai-sdk/openai", "@ai-sdk/anthropic")),
    # ── Data / ORM ────────────────────────────────────────────────────────────
    TechRule("prisma", "Prisma", ("prisma/skills/prisma-database-setup",),
             packages=("prisma", "@prisma/client")),
    TechRule("drizzle", "Drizzle ORM", ("bobmatnyc/claude-mpm-skills/drizzle",),
             packages=("drizzle-orm", "drizzle-kit")),
    TechRule("supabase", "Supabase", ("supabase/agent-skills/supabase-postgres-best-practices",),
             packages=("@supabase/supabase-js", "@supabase/ssr")),
    TechRule("stripe", "Stripe", ("stripe/ai/stripe-best-practices",),
             packages=("stripe", "@stripe/stripe-js")),
    # ── Testing ───────────────────────────────────────────────────────────────
    TechRule("playwright", "Playwright", ("currents-dev/playwright-best-practices-skill/playwright-best-practices",),
             packages=("@playwright/test", "playwright"),
             config_files=("playwright.config.ts", "playwright.config.js")),
    TechRule("vitest", "Vitest", ("antfu/skills/vitest",), packages=("vitest",),
             config_files=("vitest.config.ts", "vitest.config.js")),
    # ── Desktop / deploy / infra ──────────────────────────────────────────────
    TechRule("tauri", "Tauri", ("nodnarbnitram/claude-code-extensions/tauri-v2",),
             packages=("@tauri-apps/api", "@tauri-apps/cli"),
             config_files=("src-tauri/tauri.conf.json",)),
    TechRule("cloudflare", "Cloudflare", ("cloudflare/skills/cloudflare", "cloudflare/skills/wrangler"),
             packages=("wrangler", "@cloudflare/workers-types"),
             config_files=("wrangler.toml", "wrangler.json", "wrangler.jsonc")),
    TechRule("cloudflare-agents", "Cloudflare Agents", ("cloudflare/skills/agents-sdk",),
             packages=("agents",)),
    TechRule("vercel-deploy", "Vercel", ("vercel-labs/agent-skills/deploy-to-vercel",),
             packages=("vercel",), config_files=("vercel.json",)),
    TechRule("terraform", "Terraform", ("hashicorp/agent-skills/terraform-style-guide",),
             config_files=(".terraform.lock.hcl", "main.tf", "variables.tf")),
    # ── Other languages ───────────────────────────────────────────────────────
    TechRule("rust", "Rust", ("apollographql/skills/rust-best-practices",), config_files=("Cargo.toml",)),
    TechRule("go", "Go", ("affaan-m/everything-claude-code/golang-patterns",),
             config_files=("go.mod", "go.work")),
    TechRule("ruby", "Ruby", ("lucianghinda/superpowers-ruby/ruby",),
             config_files=("Gemfile", ".ruby-version")),
    TechRule("rails", "Ruby on Rails", ("sergiodxa/agent-skills/ruby-on-rails-best-practices",),
             config_files=("config/routes.rb", "bin/rails")),
    TechRule("php", "PHP", ("jeffallan/claude-skills/php-pro",), config_files=("composer.json",)),
    TechRule("laravel", "Laravel", ("jeffallan/claude-skills/laravel-specialist",),
             config_files=("artisan", "bootstrap/app.php")),
    TechRule("java", "Java", ("affaan-m/everything-claude-code/java-coding-standards",),
             config_files=("pom.xml", "build.gradle", "build.gradle.kts")),
    TechRule("dotnet", ".NET", ("github/awesome-copilot/dotnet-best-practices",),
             config_files=("global.json", "Directory.Build.props")),
    TechRule("bash", "Bash", ("wshobson/agents/bash-defensive-patterns",),
             file_extensions=(".sh", ".bash")),
    # ── Python ────────────────────────────────────────────────────────────────
    TechRule("python", "Python", ("wshobson/agents/python-testing-patterns",),
             config_files=("pyproject.toml", "requirements.txt", "setup.py", "Pipfile")),
    TechRule("fastapi", "FastAPI", ("wshobson/agents/fastapi-templates",), py_packages=("fastapi",)),
    TechRule("django", "Django", ("affaan-m/everything-claude-code/django-patterns",),
             py_packages=("django",), config_files=("manage.py",)),
    TechRule("flask", "Flask", ("aj-geddes/useful-ai-prompts/flask-api-development",), py_packages=("flask",)),
    TechRule("pydantic", "Pydantic", ("bobmatnyc/claude-mpm-skills/pydantic",), py_packages=("pydantic",)),
    TechRule("sqlalchemy", "SQLAlchemy", ("bobmatnyc/claude-mpm-skills/sqlalchemy",), py_packages=("sqlalchemy",)),
    TechRule("pytest", "Pytest", ("wshobson/agents/python-testing-patterns",), py_packages=("pytest",)),
    TechRule("celery", "Celery", ("wshobson/agents/python-background-jobs",), py_packages=("celery",)),
    TechRule("pandas", "Pandas", ("jeffallan/claude-skills/pandas-pro",), py_packages=("pandas",)),
    TechRule("fastmcp", "FastMCP", ("sharanharsoor/skills/fastmcp",), py_packages=("fastmcp", "mcp")),
    # ── Infra CLI skills (navig-community, runnable) — `community:<category>/<id>` ──
    TechRule("docker", "Docker", ("community:docker/docker-ops",),
             config_files=("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml")),
)


# ── Detection ─────────────────────────────────────────────────────────────────
def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _js_deps(root: Path) -> set[str]:
    """All declared JS dependency names from package.json."""
    pkg = _read_json(root / "package.json")
    out: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        val = pkg.get(key)
        if isinstance(val, dict):
            out.update(str(k) for k in val)
    return out


def _py_deps_text(root: Path) -> str:
    """Concatenated text of Python manifests (lowercased) for substring matching."""
    parts: list[str] = []
    for name in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "Pipfile", "setup.py", "setup.cfg"):
        p = root / name
        if p.is_file():
            try:
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
            except Exception:  # noqa: BLE001
                pass
    return "\n".join(parts).lower()


_PY_DEP_CACHE_SENTINEL = object()


def _py_dep_present(dep: str, py_text: str) -> bool:
    """Whether a python package name appears as a real dependency token.

    Matches on word boundaries so `fastapi` doesn't hit inside `myfastapiapp`.
    """
    return re.search(rf"(?<![\w-]){re.escape(dep.lower())}(?![\w-])", py_text) is not None


def _has_file_with_ext(root: Path, exts: tuple[str, ...]) -> bool:
    """Shallow scan (root + one level) for any file with one of the extensions.

    Bounded so a huge tree can't stall detection; skips dot/vendor dirs.
    """
    _SKIP = {"node_modules", ".git", "__pycache__", "target", "dist", "build", ".venv", "venv"}
    exts_l = tuple(e.lower() for e in exts)
    try:
        for depth_dir in (root, *[d for d in root.iterdir() if d.is_dir() and d.name not in _SKIP and not d.name.startswith(".")]):
            for f in depth_dir.iterdir():
                if f.is_file() and f.name.lower().endswith(exts_l):
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _detect_in_dir(root: Path) -> list[str]:
    """TechRule ids matched in a SINGLE directory."""
    js = _js_deps(root)
    py_text = _py_deps_text(root)
    hits: list[str] = []
    for rule in TECH_SKILLS:
        if (
            (rule.packages and any(p in js for p in rule.packages))
            or (rule.py_packages and py_text and any(_py_dep_present(p, py_text) for p in rule.py_packages))
            or (rule.config_files and any((root / c).exists() for c in rule.config_files))
            or (rule.file_extensions and _has_file_with_ext(root, rule.file_extensions))
        ):
            hits.append(rule.id)
    return hits


def _workspace_dirs(root: Path) -> list[Path]:
    """Monorepo package dirs, from `pnpm-workspace.yaml` globs + package.json
    `workspaces`. Negations (`!apps/os`) are IGNORED — they're a pnpm-reconcile
    concern, not a "don't detect tech here" signal (workspace packages are scanned too)."""
    globs: list[str] = []
    wf = root / "pnpm-workspace.yaml"
    if wf.is_file():
        try:
            import yaml  # pyyaml is a core dep

            data = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
            globs += [str(g) for g in (data.get("packages") or []) if not str(g).startswith("!")]
        except Exception:  # noqa: BLE001
            pass
    ws = _read_json(root / "package.json").get("workspaces")
    if isinstance(ws, dict):
        ws = ws.get("packages")
    if isinstance(ws, list):
        globs += [str(g) for g in ws if not str(g).startswith("!")]

    out: list[Path] = []
    seen: set[Path] = set()
    for g in globs:
        try:
            for p in sorted(root.glob(g)):
                if p.is_dir() and p not in seen:
                    seen.add(p)
                    out.append(p)
        except Exception:  # noqa: BLE001
            pass
        if len(out) >= 80:  # safety bound on huge workspaces
            break
    return out


def detect_stack(root: str | Path) -> list[TechRule]:
    """Return the TechRules matched in `root` **and its monorepo workspaces**
    (deterministic; never raises). Order follows the TECH_SKILLS map."""
    root = Path(root)
    if not root.is_dir():
        return []
    matched: set[str] = set(_detect_in_dir(root))
    for d in _workspace_dirs(root):
        matched.update(_detect_in_dir(d))
    return [r for r in TECH_SKILLS if r.id in matched]


@dataclass
class SkillPick:
    """A resolved skill to install + the technology that pulled it in."""

    spec: str          # install spec: `github:owner/repo/skill` or `community:<id>`
    ref: str           # the raw ref (owner/repo/skill or community:id)
    tech: str          # human tech name that triggered it


def _registry_base() -> str:
    return os.environ.get("NAVIG_SKILLS_REGISTRY", DEFAULT_SKILLS_REGISTRY)


def _ref_to_spec(ref: str) -> str:
    """Map a skill ref to a `navig skill install` spec.

    `community:<category>/<id>` → the navig-run/community CLI-skill path.
    `owner/repo/skill`          → the curated registry by skill NAME (last
                                  segment). owner/repo is attribution only — the
                                  files live flat in the registry, so this is
                                  what actually resolves + installs.
    """
    if ref.startswith("community:"):
        return f"github:navig-run/community/cli-skills/{ref[len('community:'):]}"
    name = ref.rsplit("/", 1)[-1]
    # `skill:` scheme pins the type so the registry's `packages/…` path segment
    # isn't mis-classified as a package (see install._parse_spec explicit-wins).
    return f"skill:{_registry_base()}/{name}"


def resolve_skills(rules: list[TechRule]) -> list[SkillPick]:
    """Flatten matched rules → deduped, order-preserving SkillPicks with specs."""
    seen: set[str] = set()
    picks: list[SkillPick] = []
    for rule in rules:
        for ref in rule.skills:
            if ref in seen:
                continue
            seen.add(ref)
            picks.append(SkillPick(spec=_ref_to_spec(ref), ref=ref, tech=rule.name))
    return picks
