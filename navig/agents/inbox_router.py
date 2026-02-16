"""
Inbox Router Agent — Classifies and transforms raw inbox markdown files.

Single-purpose agent:
  1. Reads a raw .md file from .navig/plans/inbox/
  2. Classifies into: task_roadmap, brief, wiki_knowledge, memory_log, or other
  3. Transforms content (adds frontmatter, normalizes structure)
  4. Outputs a JSON plan: { content_type, target_path, transformed_content, rationale }

Operates in two modes:
  - LLM: Uses llm_generate with a dedicated system prompt
  - Heuristic: Fast regex-based fallback (no LLM needed)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("navig.agents.inbox_router")

# ── Constants ───────────────────────────────────────────────

CONTENT_TYPES = ("task_roadmap", "brief", "wiki_knowledge", "memory_log", "other")

TARGET_FOLDERS: Dict[str, Optional[str]] = {
    "task_roadmap":    ".navig/plans",
    "brief":           ".navig/plans/briefs",
    "wiki_knowledge":  ".navig/wiki",
    "memory_log":      ".navig/memory",
    "other":           None,
}

# ── System Prompt ───────────────────────────────────────────

INBOX_ROUTER_SYSTEM_PROMPT = (
    "You are the NAVIG Inbox Router — a classification and transformation agent.\n"
    "\n"
    "## Input Contract\n"
    "You receive a JSON object:\n"
    "{\n"
    '  "filename": "raw-note.md",\n'
    '  "content": "...full markdown content...",\n'
    '  "workspace_metadata": {\n'
    '    "existing_plans": ["DEV_PLAN.md", "ROADMAP.md"],\n'
    '    "existing_briefs": ["feature-auth.md"],\n'
    '    "existing_wiki": ["setup-guide.md"],\n'
    '    "existing_memory": ["2024-01-session.md"]\n'
    "  }\n"
    "}\n"
    "\n"
    "## Output Contract\n"
    "Respond with ONLY a JSON object (no markdown fences, no commentary):\n"
    "{\n"
    '  "content_type": "task_roadmap|brief|wiki_knowledge|memory_log|other",\n'
    '  "confidence": 0.0,\n'
    '  "target_filename": "003-feature-auth-plan.md",\n'
    '  "transformed_content": "...processed markdown with frontmatter...",\n'
    '  "rationale": "One sentence explaining classification."\n'
    "}\n"
    "\n"
    "## Classification Rules\n"
    "\n"
    "### task_roadmap\n"
    "Plans, roadmaps, milestones, phases, TODO lists, project timelines.\n"
    "Target: .navig/plans/\n"
    "Transform: YAML frontmatter (type, status, created), normalize headings.\n"
    "\n"
    "### brief\n"
    "Feature specs, design docs, PRDs, implementation briefs, proposals.\n"
    "Target: .navig/plans/briefs/\n"
    "Transform: Frontmatter (type, status, priority), Problem/Solution/Scope.\n"
    "\n"
    "### wiki_knowledge\n"
    "How-to guides, reference docs, architecture, concepts, tutorials.\n"
    "Target: .navig/wiki/\n"
    "Transform: Frontmatter (type, tags), normalize to wiki format.\n"
    "\n"
    "### memory_log\n"
    "Session logs, transcripts, debug notes, daily logs, decision records.\n"
    "Target: .navig/memory/\n"
    "Transform: Date-prefixed filename, frontmatter (date, session_id).\n"
    "\n"
    "### other\n"
    "Cannot classify confidently. Keep in inbox for human review.\n"
    "Transform: Add TODO comment at top suggesting categories.\n"
    "\n"
    "## Behavioral Rules\n"
    "1. NEVER invent content. Only restructure and add metadata.\n"
    "2. Preserve ALL original text — no summarizing or truncating.\n"
    "3. Use numeric prefix for filename (e.g. 003-name.md).\n"
    "4. If confidence < 0.5, classify as other.\n"
    "5. transformed_content MUST be valid markdown.\n"
    "6. Respond with raw JSON only — no code fences.\n"
)


# ── Heuristic Patterns ─────────────────────────────────────

_ROADMAP_PATTERNS = re.compile(
    r"\b(?:roadmap|milestone|phase\s*\d|timeline|sprint|"
    r"todo|task\s*list|backlog|deliverable|deadline|"
    r"q[1-4]\s*\d{4}|gantt|epic)\b",
    re.IGNORECASE,
)

_BRIEF_PATTERNS = re.compile(
    r"\b(?:brief|spec|prd|proposal|rfc|design\s*doc|"
    r"feature\s*request|requirements|scope|acceptance\s*criteria|"
    r"user\s*stor(?:y|ies)|problem\s*statement)\b",
    re.IGNORECASE,
)

_WIKI_PATTERNS = re.compile(
    r"\b(?:guide|tutorial|how[\s-]*to|reference|"
    r"architecture|concept|glossary|faq|documentation|"
    r"setup|install|configur(?:e|ation)|overview)\b",
    re.IGNORECASE,
)

_MEMORY_PATTERNS = re.compile(
    r"\b(?:session|log|transcript|debug\s*notes|"
    r"conversation|decision\s*record|adr|standup|"
    r"retro(?:spective)?|daily|journal|meeting\s*notes)\b",
    re.IGNORECASE,
)

_HINT_PATTERNS: Dict[str, re.Pattern] = {
    "task_roadmap": re.compile(
        r"(?:roadmap|plan|todo|task|sprint|phase)", re.IGNORECASE
    ),
    "brief": re.compile(
        r"(?:brief|spec|prd|proposal|rfc|feature)", re.IGNORECASE
    ),
    "wiki_knowledge": re.compile(
        r"(?:guide|howto|tutorial|reference|wiki|doc|setup)", re.IGNORECASE
    ),
    "memory_log": re.compile(
        r"(?:session|log|transcript|journal|meeting|notes|debug)", re.IGNORECASE
    ),
}


# ── Heuristic Classifier ───────────────────────────────────

def heuristic_classify(content: str, filename: str = "") -> Tuple[str, float]:
    """
    Fast regex-based classification. Returns (content_type, confidence).

    Confidence:
      0.8+ = filename hint matched
      0.5-0.7 = content patterns (scaled by match count)
      < 0.5 = weak signal -> other
    """
    # 1. Filename hints (high confidence)
    fname_lower = filename.lower()
    for ctype, pattern in _HINT_PATTERNS.items():
        if pattern.search(fname_lower):
            return ctype, 0.85

    # 2. Score content against pattern sets
    scores: Dict[str, int] = {
        "task_roadmap": len(_ROADMAP_PATTERNS.findall(content)),
        "brief": len(_BRIEF_PATTERNS.findall(content)),
        "wiki_knowledge": len(_WIKI_PATTERNS.findall(content)),
        "memory_log": len(_MEMORY_PATTERNS.findall(content)),
    }

    best_type = max(scores, key=scores.get)
    best_count = scores[best_type]

    if best_count == 0:
        return "other", 0.2

    confidence = min(0.5 + (best_count - 1) * 0.1, 0.75)
    return best_type, round(confidence, 2)


# ── Filename Utilities ──────────────────────────────────────

def extract_title(content: str, filename: str) -> str:
    """Extract title from first H1 heading, or fall back to filename stem."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return Path(filename).stem


def next_numeric_prefix(folder: Path) -> str:
    """Return next 3-digit numeric prefix for files in folder."""
    if not folder.exists():
        return "001"
    existing = [f.name for f in folder.iterdir() if f.is_file() and f.suffix == ".md"]
    max_num = 0
    for name in existing:
        match = re.match(r"^(\d{3})-", name)
        if match:
            max_num = max(max_num, int(match.group(1)))
    return f"{max_num + 1:03d}"


def make_target_filename(title: str, content_type: str, target_folder: Path) -> str:
    """Generate target filename with numeric prefix and slugified title."""
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    slug = slug[:60]
    if not slug:
        slug = content_type
    prefix = next_numeric_prefix(target_folder)
    return f"{prefix}-{slug}.md"


# ── Workspace Metadata ─────────────────────────────────────

def collect_workspace_metadata(project_root: Path) -> Dict[str, Any]:
    """Scan .navig/ directories for existing docs to inform classification."""
    meta: Dict[str, List[str]] = {
        "existing_plans": [],
        "existing_briefs": [],
        "existing_wiki": [],
        "existing_memory": [],
    }

    plans_dir = project_root / ".navig" / "plans"
    if plans_dir.exists():
        meta["existing_plans"] = [
            f.name for f in plans_dir.iterdir()
            if f.is_file() and f.suffix == ".md"
        ]

    briefs_dir = plans_dir / "briefs"
    if briefs_dir.exists():
        meta["existing_briefs"] = [
            f.name for f in briefs_dir.iterdir()
            if f.is_file() and f.suffix == ".md"
        ]

    wiki_dir = project_root / ".navig" / "wiki"
    if wiki_dir.exists():
        meta["existing_wiki"] = [
            f.name for f in wiki_dir.iterdir()
            if f.is_file() and f.suffix == ".md"
        ]

    memory_dir = project_root / ".navig" / "memory"
    if memory_dir.exists():
        meta["existing_memory"] = [
            f.name for f in memory_dir.iterdir()
            if f.is_file() and f.suffix == ".md"
        ]

    return meta


def list_inbox_files(project_root: Path) -> List[Path]:
    """List all .md files in .navig/plans/inbox/."""
    inbox_dir = project_root / ".navig" / "plans" / "inbox"
    if not inbox_dir.exists():
        return []
    return sorted(
        f for f in inbox_dir.iterdir()
        if f.is_file() and f.suffix == ".md"
    )


# ── Agent Class ─────────────────────────────────────────────

class InboxRouterAgent:
    """
    Stateless agent that classifies and transforms inbox files.

    Modes:
      - LLM (default): Uses llm_generate with INBOX_ROUTER_SYSTEM_PROMPT
      - Heuristic: Fast regex fallback when LLM is unavailable

    The ``backend`` field is metadata only — it tells the agent which
    caller is executing it (vscode_copilot or cli_llm) but does NOT
    change classification behavior.
    """

    def __init__(
        self,
        project_root: Path,
        use_llm: bool = True,
        backend: str = "cli_llm",
    ):
        self.project_root = project_root
        self.use_llm = use_llm
        self.backend = backend
        self._metadata: Optional[Dict[str, Any]] = None

    @property
    def metadata(self) -> Dict[str, Any]:
        if self._metadata is None:
            self._metadata = collect_workspace_metadata(self.project_root)
        return self._metadata

    def process_single(self, file_path: Path, dry_run: bool = False) -> Dict[str, Any]:
        """Process a single inbox file. Returns a plan dict."""
        if not file_path.exists():
            return {"error": f"File not found: {file_path}", "file": str(file_path)}

        content = file_path.read_text(encoding="utf-8")
        filename = file_path.name

        if self.use_llm:
            try:
                plan = self._process_via_llm(content, filename, self.metadata)
            except Exception as e:
                logger.warning("LLM failed, heuristic fallback: %s", e)
                plan = self._process_heuristic(content, filename)
        else:
            plan = self._process_heuristic(content, filename)

        plan["source_file"] = str(file_path)
        plan["dry_run"] = dry_run
        plan["backend"] = self.backend
        return plan

    def process_batch(
        self, files: Optional[List[Path]] = None, dry_run: bool = False
    ) -> List[Dict[str, Any]]:
        """Process all inbox files (or a specific list)."""
        if files is None:
            files = list_inbox_files(self.project_root)

        if not files:
            logger.info("No inbox files to process.")
            return []

        return [self.process_single(f, dry_run=dry_run) for f in files]

    def _process_via_llm(
        self, content: str, filename: str, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Classify via LLM with strict JSON contract."""
        from navig.llm_generate import llm_generate

        user_payload = json.dumps({
            "filename": filename,
            "content": content,
            "workspace_metadata": metadata,
        }, indent=2)

        messages = [
            {"role": "system", "content": INBOX_ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ]

        raw = llm_generate(
            messages=messages,
            mode="coding",
            temperature=0.1,
            max_tokens=8192,
        )

        plan = self._parse_llm_response(raw)

        if plan.get("content_type") not in CONTENT_TYPES:
            logger.warning(
                "LLM returned invalid type '%s', using heuristic",
                plan.get("content_type"),
            )
            return self._process_heuristic(content, filename)

        return plan

    def _parse_llm_response(self, raw: str) -> Dict[str, Any]:
        """Parse LLM JSON response, stripping code fences if present."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)

    def _process_heuristic(self, content: str, filename: str) -> Dict[str, Any]:
        """Fast heuristic classification without LLM."""
        content_type, confidence = heuristic_classify(content, filename)
        title = extract_title(content, filename)

        target_folder_rel = TARGET_FOLDERS.get(content_type)
        if target_folder_rel:
            target_folder = self.project_root / target_folder_rel
            target_name = make_target_filename(title, content_type, target_folder)
            target_path = f"{target_folder_rel}/{target_name}"
        else:
            target_name = filename
            target_path = None

        now = datetime.now().strftime("%Y-%m-%d")
        frontmatter = (
            "---\n"
            f"type: {content_type}\n"
            f"created: {now}\n"
            f"source: inbox/{filename}\n"
            "---\n\n"
        )
        transformed = frontmatter + content

        return {
            "content_type": content_type,
            "confidence": confidence,
            "target_filename": target_name,
            "target_path": target_path,
            "transformed_content": transformed,
            "rationale": f"Heuristic: {content_type} (confidence {confidence})",
            "backend": self.backend,
        }


# ── File I/O Executor ──────────────────────────────────────

def execute_plan(
    project_root: Path,
    plan: Dict[str, Any],
    dry_run: bool = False,
    move_source: bool = True,
) -> Dict[str, Any]:
    """
    Execute a classification plan — write transformed file, move source.

    Called by CLI AFTER agent produces a plan. Not part of the agent itself.
    """
    result = {"status": "skipped", "source": plan.get("source_file", "?")}

    if plan.get("error"):
        result["status"] = "error"
        result["error"] = plan["error"]
        return result

    target_path_rel = plan.get("target_path")
    if not target_path_rel:
        result["status"] = "kept_in_inbox"
        result["reason"] = plan.get("rationale", "No target path")
        return result

    target = project_root / target_path_rel

    if dry_run:
        result["status"] = "dry_run"
        result["would_write"] = str(target)
        result["content_type"] = plan.get("content_type")
        result["confidence"] = plan.get("confidence")
        result["rationale"] = plan.get("rationale")
        return result

    target.parent.mkdir(parents=True, exist_ok=True)
    transformed = plan.get("transformed_content", "")
    target.write_text(transformed, encoding="utf-8")
    result["status"] = "written"
    result["target"] = str(target)
    result["content_type"] = plan.get("content_type")

    if move_source and plan.get("source_file"):
        source = Path(plan["source_file"])
        if source.exists():
            processed_dir = source.parent / ".processed"
            processed_dir.mkdir(exist_ok=True)
            dest = processed_dir / source.name
            source.rename(dest)
            result["source_moved"] = str(dest)

    return result
