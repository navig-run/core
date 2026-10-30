"""
Context-aware skill activation for the agent system (FB-02).

Skills are Markdown files with optional YAML frontmatter that provide
domain-specific instructions to the LLM.  They are auto-activated based
on the current file paths and user message keywords.

Skill directories scanned (in priority order):
  1. <workspace>/.navig/skills/   (project-level, higher priority)
  2. ~/.navig/skills/              (global, lower priority)

Skill file format (any ``*.md`` file)::

    ---
    name: Django Best Practices
    activation_paths: ["*.py", "models.py"]
    activation_keywords: ["django", "queryset"]
    priority: 10
    ---
    # Django Best Practices
    Use select_related() for joins ...

Files *without* frontmatter are still loaded — the filename (stem) becomes
the skill name, and activation rules default to empty (manual-only).

Per-language keyword packs — ``activation_keywords_<lang>`` (``ru``, ``fr``, …)::

    activation_keywords: [music, jingle]            # English: plain substrings, unchanged
    activation_keywords_ru: [музык, джингл]          # Russian stems
    activation_keywords_fr: [musique, bruitage]      # French

Activation is a lexical match over the user's message, so an English-only list never
loads a skill for someone who writes Russian or French. Pack entries differ from the
English list in three deliberate ways: they match at a **word start** (so the stem
``музык`` covers музыка/музыку/музыки without firing inside ``немузыкальный``, and a
short Latin stem cannot hide in the middle of an unrelated word); both sides are
**folded** (``ё``→``е``, Latin accents dropped — people on a phone write "temnuyu",
"genere", "boite"); and every pack is scored on every message, since the language of a
message is not known. An entry of 3 characters or fewer is an acronym (``обс``, ``obs``)
and matches as a whole word. Keep Latin stems distinctive: a word that is ALSO common
English (``resume`` = CV) needs a phrase (``resume cet``), because packs score on
English messages too.

Integration:
    The agent calls ``SkillsContext.activate()`` each turn with the current file
    paths and user message, then renders the matches with
    ``format_for_system_prompt()``.

    ⚠ Despite that method's name, the block is prepended to the **user turn**,
    not the system prompt — see ``ConversationalAgent.run_agentic``. Matched
    skills are query-specific, so putting them in the system block would mutate
    the cached ``tools → system`` prefix on every turn and re-bill it at write
    price. The name is kept for backward compatibility with out-of-tree callers.
"""

from __future__ import annotations

import fnmatch
import functools
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.core.coerce import coerce_bool
from navig.platform.paths import skills_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ACTIVE_SKILLS = 3
MAX_SKILL_CHARS = 8000  # per skill character budget (~2K tokens)
PROJECT_SKILLS_DIR = ".navig/skills"


def _global_skills_dir() -> Path:
    """Resolve the global skills dir at CALL time — never import time.

    ``config_dir()`` honours ``NAVIG_CONFIG_DIR``; a module-level constant
    would freeze the real user home before test/daemon isolation applies,
    letting isolated agents discover the operator's real skills
    (see ``navig/vault/migrate.py:_legacy_db_path``).
    """
    return skills_dir()

# Scoring weights
PATH_MATCH_SCORE = 10
KEYWORD_MATCH_SCORE = 5
DESCRIPTION_MATCH_SCORE = 3  # fuzzier CC-`description` word overlap (capped)

# Generic words ignored when matching a skill's free-text `description` so that
# only meaningful domain words (video, github, download…) drive activation.
_DESCRIPTION_STOPWORDS = frozenset(
    {
        "when", "with", "your", "that", "this", "have", "from", "will", "into",
        "them", "then", "they", "user", "uses", "used", "using", "want", "wants",
        "like", "such", "also", "please", "help", "need", "make", "does", "over",
    }
)


_LANG_PACK_KEY = re.compile(r"^activation_keywords_([a-z]{2,3})$")


def _fold(text: str) -> str:
    """Lowercase and fold the spellings people type interchangeably.

    ``ё`` → ``е`` (Russian), and combining marks dropped from LATIN letters only
    (``é``/``è``/``î`` → ``e``/``i``). Cyrillic is deliberately not decomposed: NFD
    turns ``й`` into ``и`` + a mark, and stripping the mark would conflate two different
    letters (``майн`` would match ``мина``).

    Also: a hyphen is a space (French attaches clitics with one — ``résume-moi``,
    ``télécharge-moi`` — and ``rendez-vous`` is typed both ways), and the typographic
    apostrophes phones insert (``’`` ``‘``) become ``'`` so ``l’agenda`` matches
    ``l'agenda``. Whitespace is collapsed so those substitutions can't leave gaps.
    """
    out: list[str] = []
    text = text.lower().replace("’", "'").replace("‘", "'").replace("-", " ")
    text = " ".join(text.split())
    for ch in text:
        if "Ѐ" <= ch <= "ӿ":
            out.append("е" if ch == "ё" else ch)
        else:
            out.extend(
                c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn"
            )
    return "".join(out)


#: At or below this many characters a pack keyword is an acronym (``обс``, ``obs``,
#: ``son``), not a stem, and must match as a WHOLE word — ``обс`` would otherwise fire
#: inside ``обсудим`` ("let's discuss") and ``obs`` inside ``observe``.
_WHOLE_WORD_MAX_LEN = 3


@functools.lru_cache(maxsize=2048)
def _stem_pattern(keyword: str) -> re.Pattern[str] | None:
    """Pattern for one pack keyword (folded), or ``None`` if it is empty.

    Anchored at a word start; additionally anchored at the word END when the keyword is
    an acronym (``_WHOLE_WORD_MAX_LEN`` characters or fewer). Longer entries are stems
    on purpose: ``музык`` must cover музыка/музыку/музыки.
    """
    folded = _fold(keyword).strip()
    if not folded:
        return None
    tail = r"(?!\w)" if len(folded) <= _WHOLE_WORD_MAX_LEN else ""
    return re.compile(r"(?<!\w)" + re.escape(folded) + tail)


def _localized_hits(skill: "ContextSkill", msg_folded: str) -> int:
    """How many per-language pack keywords occur in the (already folded) message."""
    hits = 0
    for words in skill.localized_keywords.values():
        for word in words:
            pattern = _stem_pattern(word)
            if pattern is not None and pattern.search(msg_folded):
                hits += 1
    return hits


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ContextSkill:
    """A skill loaded for context-aware activation.

    Attributes:
        name:                Human-readable skill name.
        content:             Markdown body (instructions for the LLM).
        activation_paths:    Glob patterns matched against current file paths.
        activation_keywords: Keywords matched (case-insensitive) in user message.
        priority:            Static priority bonus (higher = more important).
        source:              ``"project"`` or ``"global"``.
        file_path:           Absolute path to the skill file on disk.
        localized_keywords:  Per-language packs (``{"ru": [...], "fr": [...]}``) from
                             ``activation_keywords_<lang>`` — see the module docstring
                             for how they differ from ``activation_keywords``.
        activatable:         ``False`` (frontmatter ``activatable: false``) keeps the skill
                             out of automatic activation — for skills written against a
                             command surface that is not implemented yet, whose
                             instructions the agent could not carry out. Force-activation
                             still works.
    """

    name: str
    content: str
    description: str = ""  # CC-standard "use when…" trigger (drives activation)
    activation_paths: list[str] = field(default_factory=list)
    activation_keywords: list[str] = field(default_factory=list)
    priority: int = 0
    source: str = "project"  # "project" | "global"
    file_path: str = ""
    localized_keywords: dict[str, list[str]] = field(default_factory=dict)
    activatable: bool = True

    # -- helpers --------------------------------------------------------------

    def summary(self, max_len: int = 80) -> str:
        """One-line summary for listing purposes."""
        body = self.content.replace("\n", " ").strip()
        if len(body) > max_len:
            body = body[: max_len - 1] + "…"
        return body


# ---------------------------------------------------------------------------
# YAML frontmatter parsing
# ---------------------------------------------------------------------------


def _load_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter_dict, body_markdown)`` from a Markdown string."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        import yaml  # lazy; keep import fast for CLI path
    except ImportError:
        return {}, parts[2].strip()

    try:
        fm = yaml.safe_load(parts[1])
    except Exception:  # noqa: BLE001
        fm = {}
    if not isinstance(fm, dict):  # a list/scalar frontmatter would crash fm.get()
        fm = {}

    return fm, parts[2].strip()


def _parse_skill_file(path: Path, source: str) -> ContextSkill | None:
    """Parse a single ``.md`` file into a :class:`ContextSkill`.

    Returns ``None`` on unrecoverable I/O or parse error.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("skills_context: cannot read %s: %s", path, exc)
        return None

    fm, body = _load_frontmatter(text)

    # Truncate body to budget
    if len(body) > MAX_SKILL_CHARS:
        body = body[:MAX_SKILL_CHARS] + "\n…(truncated)"

    def _str_list(key: str) -> list[str]:
        val = fm.get(key, [])
        if isinstance(val, list):
            return [str(v) for v in val]
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        return []

    # `activation_keywords_<lang>`: a 2-3 letter code only, so a typo like
    # `activation_keywords_english` is ignored instead of becoming a pack no one matches.
    localized: dict[str, list[str]] = {}
    for key in fm:
        m = _LANG_PACK_KEY.match(str(key))
        if m:
            words = [w for w in _str_list(str(key)) if w.strip()]
            if words:
                localized[m.group(1)] = words

    return ContextSkill(
        # SKILL.md files live at <id>/SKILL.md → fall back to the folder name
        # (like the canonical loader), not the literal stem "SKILL".
        name=fm.get("name")
        or (path.parent.name if path.name.lower() == "skill.md" else path.stem),
        content=body,
        description=str(fm.get("description", "")).strip(),
        activation_paths=_str_list("activation_paths"),
        activation_keywords=_str_list("activation_keywords"),
        priority=int(fm.get("priority", 0)),
        source=source,
        file_path=str(path),
        localized_keywords=localized,
        activatable=coerce_bool(fm.get("activatable"), default=True),
    )


# ---------------------------------------------------------------------------
# SkillsContext — discovery + activation
# ---------------------------------------------------------------------------


class SkillsContext:
    """Discovers and scores skills for context-aware injection.

    Parameters:
        workspace_dir: Project root directory.
        max_active:    Maximum skills to inject per turn.
        extra_dirs:    Additional directories to scan.
    """

    def __init__(
        self,
        workspace_dir: str = ".",
        *,
        max_active: int = MAX_ACTIVE_SKILLS,
        extra_dirs: list[Path] | None = None,
        include_installed: bool = False,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.max_active = max_active
        self._extra_dirs = extra_dirs or []
        # When True, also scan the unified installed/plugin skill roots
        # (get_skill_dirs). Off by default to keep project/global scoping
        # isolated and deterministic for tests; the agent opts in.
        self._include_installed = include_installed
        self._skills: list[ContextSkill] = []
        self._loaded = False
        self._force_activated: set[str] = set()
        self._force_deactivated: set[str] = set()

    # -- Discovery ------------------------------------------------------------

    def load(self) -> list[ContextSkill]:
        """Scan project and global skill directories.

        Returns the full list of discovered skills (regardless of activation).
        """
        skills: list[ContextSkill] = []
        seen: set[Path] = set()

        def _scan(root: Path, source: str, pattern: str = "*.md") -> None:
            if not root.is_dir():
                return
            for f in sorted(root.rglob(pattern)):
                rf = f.resolve()
                if rf in seen:  # de-dupe: stores/roots can overlap
                    continue
                seen.add(rf)
                skill = _parse_skill_file(f, source=source)
                if skill:
                    skills.append(skill)

        # Project + global standalone-.md stores (highest priority first)
        _scan(self.workspace_dir / PROJECT_SKILLS_DIR, "project")
        _scan(_global_skills_dir(), "global")

        # Installed + plugin-provided + block skills — the unified discovery
        # roots (package `skills/`, builtin/user stores, block SKILL.md shims),
        # so a skill shipped *inside a plugin* is auto-activatable by the agent.
        # Opt-in (agent only) so project/global scoping stays deterministic.
        # These roots follow the SKILL.md-folder convention, so scan SKILL.md
        # only (not every .md, which would slurp reference docs as phantom
        # skills) and isolate each root so one unreadable dir can't abort the rest.
        if self._include_installed:
            try:
                from navig.skills.loader import get_skill_dirs

                roots = get_skill_dirs()
            except Exception as exc:  # noqa: BLE001
                roots = []
                logger.debug("skills_context: get_skill_dirs unavailable: %s", exc)
            for d in roots:
                try:
                    _scan(d, "global", pattern="SKILL.md")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("skills_context: scan failed for %s: %s", d, exc)

        # Extra directories (e.g. test injection)
        for d in self._extra_dirs:
            _scan(d, "extra")

        self._skills = skills
        self._loaded = True
        logger.debug(
            "skills_context: loaded %d skills (%d project, %d global, %d extra)",
            len(skills),
            sum(1 for s in skills if s.source == "project"),
            sum(1 for s in skills if s.source == "global"),
            sum(1 for s in skills if s.source == "extra"),
        )
        return skills

    # -- Activation -----------------------------------------------------------

    def activate(
        self,
        current_files: list[str] | None = None,
        user_message: str = "",
    ) -> list[ContextSkill]:
        """Return the skills that should be active given current context.

        Scoring:
        - +10 per glob pattern that matches any current file path
        - +5 per keyword found (case-insensitive) in the user message
        - +priority (static bonus from frontmatter)

        Skills with score ≤ 0 are excluded unless force-activated.
        Force-deactivated skills are always excluded.
        """
        if not self._loaded:
            self.load()

        current_files = current_files or []
        msg_lower = user_message.lower()
        # Tokenize once (hoisted out of the per-skill scorer) for whole-word
        # description matching.
        msg_words = {w.strip(".,;:!?()[]'\"") for w in msg_lower.split()}
        msg_folded = _fold(msg_lower)  # once per turn, not once per skill

        scored: list[tuple[ContextSkill, int]] = []

        for skill in self._skills:
            # Force-deactivation overrides everything
            if skill.name in self._force_deactivated:
                continue

            # Force-activation guarantees inclusion
            if skill.name in self._force_activated:
                scored.append((skill, 10_000))
                continue

            if not skill.activatable:
                continue

            score = _compute_activation_score(
                skill, current_files, msg_lower, msg_words, msg_folded
            )
            if score > 0:
                scored.append((skill, score))

        # Sort by score desc, then priority desc, then project before global
        scored.sort(
            key=lambda x: (
                x[1],
                x[0].priority,
                0 if x[0].source == "project" else -1,
            ),
            reverse=True,
        )

        return [s for s, _ in scored[: self.max_active]]

    # -- Force activation / deactivation --------------------------------------

    def force_activate(self, skill_name: str) -> bool:
        """Force a skill to be active regardless of scoring.

        Returns ``True`` if the skill exists.
        """
        self._force_deactivated.discard(skill_name)

        if not self._loaded:
            self.load()

        if any(s.name == skill_name for s in self._skills):
            self._force_activated.add(skill_name)
            return True
        return False

    def force_deactivate(self, skill_name: str) -> bool:
        """Force a skill to be excluded from activation.

        Returns ``True`` if the skill exists.
        """
        self._force_activated.discard(skill_name)

        if not self._loaded:
            self.load()

        if any(s.name == skill_name for s in self._skills):
            self._force_deactivated.add(skill_name)
            return True
        return False

    def reset_overrides(self) -> None:
        """Clear all force-activation and force-deactivation overrides."""
        self._force_activated.clear()
        self._force_deactivated.clear()

    # -- Formatting -----------------------------------------------------------

    def format_for_system_prompt(self, active_skills: list[ContextSkill]) -> str:
        """Render active skills as an ``## Active Skills`` section.

        ⚠ Name notwithstanding, the caller prepends this to the **user turn**.
        Skills are matched per message, so injecting them into the system block
        would invalidate the cached tools+system prefix every turn.
        """
        if not active_skills:
            return ""

        parts = ["\n## Active Skills\n"]
        for skill in active_skills:
            source_tag = f" ({skill.source})" if skill.source != "project" else ""
            parts.append(f"### {skill.name}{source_tag}\n{skill.content}\n")
        return "\n".join(parts)

    # -- Queries --------------------------------------------------------------

    @property
    def all_skills(self) -> list[ContextSkill]:
        """Return all loaded skills."""
        if not self._loaded:
            self.load()
        return list(self._skills)

    @property
    def skill_count(self) -> int:
        """Total number of loaded skills."""
        if not self._loaded:
            self.load()
        return len(self._skills)

    def get_skill(self, name: str) -> ContextSkill | None:
        """Look up a skill by name (case-sensitive)."""
        if not self._loaded:
            self.load()
        for s in self._skills:
            if s.name == name:
                return s
        return None

    def reload(self) -> list[ContextSkill]:
        """Force a reload of all skill files from disk."""
        self._loaded = False
        return self.load()


# ---------------------------------------------------------------------------
# Shared per-workspace contexts
# ---------------------------------------------------------------------------

#: One :class:`SkillsContext` per workspace dir, shared by every reader.
_CONTEXTS: dict[str, SkillsContext] = {}


def get_skills_context(
    workspace_dir: str, *, include_installed: bool = True
) -> SkillsContext:
    """The one :class:`SkillsContext` for ``workspace_dir``.

    Two callers need the **same object**, not merely equivalent ones:

    * the prompt builder, which renders the active skills into the system prompt, and
    * the ``manage_skills`` agent tool, whose ``force_activate`` / ``force_deactivate``
      mutate in-memory sets on one instance.

    Give them separate instances and the tool answers *"force-activated. It will be
    included in the next turn"* while the next turn renders from an object that never
    heard about it — a phantom success, and a worse failure than the tool being absent.

    Keyed by workspace dir rather than held in a single module global because the daemon
    never chdirs and the operator can switch the active space while it runs, so a lone
    global would hand the new space whatever the previous one last activated.

    Construction runs ``load()`` over the skill roots, so caching also keeps that off the
    per-turn path.

    ⚠ Scope is the **space, not the session**: two conversations in the same workspace
    share these force-activation sets, so a skill switched on in one appears in the other.
    That matches how skills already behave — auto-activation reads the same space-level
    stores for every session — and the effect is additive instructions the operator can
    switch back off. Making it per-session would mean threading a session key through the
    tool, which it does not currently receive.
    """
    ctx = _CONTEXTS.get(workspace_dir)
    if ctx is None:
        ctx = SkillsContext(
            workspace_dir=workspace_dir, include_installed=include_installed
        )
        _CONTEXTS[workspace_dir] = ctx
    return ctx


def reset_skills_contexts() -> None:
    """Drop every cached context (tests; a space being re-scaffolded)."""
    _CONTEXTS.clear()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _compute_activation_score(
    skill: ContextSkill,
    current_files: list[str],
    msg_lower: str,
    msg_words: set[str] | None = None,
    msg_folded: str | None = None,
) -> int:
    """Compute how relevant *skill* is given current context.

    *msg_words* is the message tokenized into whole words; the ``activate`` loop
    passes it once (hoisted). When omitted it is derived from *msg_lower*, so
    direct 3-arg callers keep working.
    """
    score = 0

    # Path matching — each pattern that matches any file adds PATH_MATCH_SCORE
    for pattern in skill.activation_paths:
        for file_path in current_files:
            # Match against basename and full path
            if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(
                Path(file_path).name, pattern
            ):
                score += PATH_MATCH_SCORE
                break  # one match per pattern is enough

    # Keyword matching — English list: plain case-insensitive substrings, unchanged.
    for keyword in skill.activation_keywords:
        if keyword.lower() in msg_lower:
            score += KEYWORD_MATCH_SCORE

    # Per-language packs — word-start anchored, folded on both sides, same weight.
    if skill.localized_keywords:
        if msg_folded is None:
            msg_folded = _fold(msg_lower)
        score += _localized_hits(skill, msg_folded) * KEYWORD_MATCH_SCORE

    # Description matching — a Claude-Code SKILL.md declares *when* to use it via
    # a free-text `description` ("Use when the user wants to edit a video…"),
    # not explicit activation_keywords. Match its meaningful words against the
    # user message so standard skills auto-activate. Fuzzier than explicit
    # keywords, so weighted lower and capped to avoid a long description
    # dominating.
    if skill.description:
        # Match whole words (msg_words), not substrings, so "edit" in the
        # description does not fire on "credit".
        if msg_words is None:
            msg_words = {w.strip(".,;:!?()[]'\"") for w in msg_lower.split()}
        desc_words = {
            w.strip(".,;:!?()[]'\"")
            for w in skill.description.lower().split()
            if len(w) >= 4
        } - _DESCRIPTION_STOPWORDS
        hits = sum(1 for w in desc_words if w and w in msg_words)
        # Require >=2 overlapping words: a lone common word ("project"/"video")
        # must not by itself auto-activate a third-party skill body.
        if hits >= 2:
            score += min(hits, 3) * DESCRIPTION_MATCH_SCORE

    # Priority bonus
    score += skill.priority

    return score
