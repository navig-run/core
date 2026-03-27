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
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("navig.agents.inbox_router")

# ── Constants ───────────────────────────────────────────────

CONTENT_TYPES = ("task_roadmap", "brief", "wiki_knowledge", "memory_log", "other")

TARGET_FOLDERS: Dict[str, Optional[str]] = {
    "task_roadmap": ".navig/plans",
    "brief": ".navig/plans/briefs",
    "wiki_knowledge": ".navig/wiki",
    "memory_log": ".navig/memory",
    "other": None,
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
    "Plans, roadmaps, milestones, phases, task lists, project timelines.\n"
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
    "Transform: Add inline comment at top suggesting categories.\n"
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
    "brief": re.compile(r"(?:brief|spec|prd|proposal|rfc|feature)", re.IGNORECASE),
    "wiki_knowledge": re.compile(
        r"(?:guide|howto|tutorial|reference|wiki|doc|setup)", re.IGNORECASE
    ),
    "memory_log": re.compile(
        r"(?:session|log|transcript|journal|meeting|notes|debug)", re.IGNORECASE
    ),
}


# ── TF-IDF + Cosine Similarity Classifier ──────────────────
#
# Exemplar documents per category. TF-IDF vectors are built from these
# and compared to incoming content via cosine similarity.
# Much more accurate than keyword regex because it captures term-frequency
# distributions rather than binary keyword presence.

_EXEMPLARS: Dict[str, List[str]] = {
    "task_roadmap": [
        "Roadmap and project plan with milestones and deliverables. Phase 1 setup "
        "infrastructure. Phase 2 implement core features. Sprint backlog items with "
        "deadline dates. Q1 2025 goals. Epic tracking and task list. Timeline for "
        "deployment. Incomplete items remaining in the backlog. Gantt chart with dependencies.",
        "Project plan with scheduled milestones. Sprint 1 deliverables due next week. "
        "Task list for the deployment phase. Backlog grooming session items. Release "
        "timeline Q2 2025. Epic authentication system. Deadline tracker with status.",
        "## Roadmap\n- [ ] Set up CI/CD pipeline\n- [ ] Database migration\n"
        "- [x] Design system components\n## Phase 2\n- [ ] API endpoints\n"
        "Deadline: March 2025\nSprint velocity: 24 points",
    ],
    "brief": [
        "Feature brief and specification document. Problem statement describing the "
        "user need. Proposed solution with scope and acceptance criteria. Requirements "
        "for the implementation. Design document outlining the architecture decision. "
        "PRD with user stories and priority ranking. RFC for the new API proposal.",
        "Product requirements document for authentication feature. Problem: users "
        "cannot reset passwords. Solution: implement self-service password reset flow. "
        "Scope: web and mobile. Acceptance criteria: user receives email within 30 seconds. "
        "User story: as a user I want to reset my password so I can regain access.",
        "## Brief: Feature Specification\n### Problem Statement\nCurrent system lacks "
        "search functionality.\n### Proposed Solution\nImplement full-text search with "
        "BM25 ranking.\n### Requirements\n1. Sub-200ms response time\n2. Fuzzy matching",
    ],
    "wiki_knowledge": [
        "Setup guide and installation tutorial. How to configure the development "
        "environment. Step by step prerequisites and troubleshooting. Architecture "
        "overview and reference documentation. Concept explanation with examples. "
        "FAQ and glossary of terms. Configuration reference for all settings.",
        "Getting started guide for new developers. Install Node.js and run npm install. "
        "Configure environment variables in .env file. Architecture: the system uses "
        "microservices with REST APIs. Troubleshooting common errors. Reference: all "
        "CLI commands and their options.",
        "## How to Deploy\n### Prerequisites\n- Docker installed\n- Access to container "
        "registry\n### Steps\n1. Build the image: docker build -t app .\n2. Push to "
        "registry\n### Troubleshooting\n- Port conflicts: check netstat",
    ],
    "memory_log": [
        "Session log and debug transcript from today. Meeting notes and conversation "
        "record. Decision record ADR-005 for choosing PostgreSQL. Standup notes for "
        "the team. Daily journal entry with retrospective. Debug session investigating "
        "memory leak.",
        "Daily standup 2025-01-15. Discussed blockers and progress. Decision: migrate "
        "from MySQL to PostgreSQL. Action items from meeting. Retrospective notes — "
        "what went well, what to improve. Session transcript with debugging steps.",
        "## Session Log 2025-02-10\nInvestigated slow API responses.\nFound N+1 query "
        "in user endpoint.\nApplied eager loading fix.\n\n## Decision Record\n"
        "ADR: Use Redis for caching.\nContext: response times exceed SLA.",
    ],
}

_STOP_WORDS = frozenset(
    "the and for are but not you all can had her was one our out has have been some "
    "them than its over also that with this from they will each make like into just "
    "more when very what which their there about would these other could after should "
    "being where does then did".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokenize(text: str) -> List[str]:
    """Tokenize text into lowercased terms, stripping markdown/URLs."""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP_WORDS]


def _term_frequency(tokens: List[str]) -> Dict[str, float]:
    """Augmented term frequency (0.5 + 0.5 * f/max_f)."""
    counts: Dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    max_f = max(counts.values()) if counts else 1
    return {t: 0.5 + 0.5 * (c / max_f) for t, c in counts.items()}


# Lazy-initialised cache for IDF and category vectors
_tfidf_cache: Optional[Dict[str, Any]] = None


def _get_tfidf_data() -> Dict[str, Any]:
    global _tfidf_cache
    if _tfidf_cache is not None:
        return _tfidf_cache

    # Build corpus from exemplars
    docs: List[Dict[str, Any]] = []
    for cat, texts in _EXEMPLARS.items():
        for text in texts:
            docs.append({"category": cat, "tokens": _tokenize(text)})

    # IDF across all exemplar documents
    doc_count = len(docs)
    doc_freq: Dict[str, int] = {}
    for doc in docs:
        for term in set(doc["tokens"]):
            doc_freq[term] = doc_freq.get(term, 0) + 1
    idf = {
        term: math.log((doc_count + 1) / (df + 1)) + 1 for term, df in doc_freq.items()
    }

    # Aggregate TF-IDF vector per category (mean of exemplars)
    cat_vectors: Dict[str, Dict[str, float]] = {}
    for cat in _EXEMPLARS:
        cat_docs = [d for d in docs if d["category"] == cat]
        agg: Dict[str, float] = {}
        for doc in cat_docs:
            tf = _term_frequency(doc["tokens"])
            for term, tf_val in tf.items():
                agg[term] = agg.get(term, 0.0) + tf_val * idf.get(term, 1.0)
        n = len(cat_docs)
        cat_vectors[cat] = {t: v / n for t, v in agg.items()}

    _tfidf_cache = {"idf": idf, "cat_vectors": cat_vectors}
    return _tfidf_cache


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    dot = sum(a[t] * b[t] for t in a if t in b)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    denom = norm_a * norm_b
    return dot / denom if denom > 0 else 0.0


def heuristic_classify(content: str, filename: str = "") -> Tuple[str, float]:
    """
    TF-IDF + cosine similarity classifier.

    Compares incoming document against exemplar category vectors.
    Regex patterns and filename hints provide secondary boosts.
    Returns (content_type, confidence) where confidence is 0.0–1.0.
    """
    data = _get_tfidf_data()
    idf: Dict[str, float] = data["idf"]
    cat_vectors: Dict[str, Dict[str, float]] = data["cat_vectors"]

    # Build TF-IDF vector for input document
    tokens = _tokenize(content + " " + filename)
    tf = _term_frequency(tokens)
    doc_vec = {t: tv * idf.get(t, 1.0) for t, tv in tf.items()}

    # Cosine similarity against each category
    sims: Dict[str, float] = {}
    for cat, cv in cat_vectors.items():
        sims[cat] = _cosine_similarity(doc_vec, cv)

    # Secondary regex boost (scaled down — TF-IDF is primary signal)
    REGEX_BOOST = 0.08
    pattern_map = {
        "task_roadmap": _ROADMAP_PATTERNS,
        "brief": _BRIEF_PATTERNS,
        "wiki_knowledge": _WIKI_PATTERNS,
        "memory_log": _MEMORY_PATTERNS,
    }
    for key, pat in pattern_map.items():
        matches = pat.findall(content)
        if matches:
            sims[key] = sims.get(key, 0.0) + min(len(matches), 3) * REGEX_BOOST

    # Filename hint boost
    fname_lower = filename.lower()
    filename_hints: List[str] = []
    for ctype, pattern in _HINT_PATTERNS.items():
        if pattern.search(fname_lower):
            filename_hints.append(ctype)
            sims[ctype] = sims.get(ctype, 0.0) + 0.25

    # Frontmatter boost
    fm = re.match(r"^---\n([\s\S]*?)\n---", content)
    if fm:
        y = fm.group(1).lower()
        if re.search(r"type:\s*(?:plan|roadmap|task)", y, re.I):
            sims["task_roadmap"] = sims.get("task_roadmap", 0.0) + 0.15
        if re.search(r"type:\s*(?:brief|spec|proposal)", y, re.I):
            sims["brief"] = sims.get("brief", 0.0) + 0.15
        if re.search(r"type:\s*(?:guide|wiki|reference)", y, re.I):
            sims["wiki_knowledge"] = sims.get("wiki_knowledge", 0.0) + 0.15
        if re.search(r"type:\s*(?:log|session|memory)", y, re.I):
            sims["memory_log"] = sims.get("memory_log", 0.0) + 0.15

    # Structure hints
    lines = content.split("\n")
    checkboxes = sum(1 for l in lines if re.match(r"^\s*-\s*\[[ x]\]", l))
    if checkboxes >= 3:
        sims["task_roadmap"] = sims.get("task_roadmap", 0.0) + 0.1
    if re.search(
        r"^#{1,2}\s*(?:problem|solution|scope|requirements)", content, re.I | re.M
    ):
        sims["brief"] = sims.get("brief", 0.0) + 0.1
    if re.search(
        r"^#{1,2}\s*(?:step|prerequisites|troubleshoot)", content, re.I | re.M
    ):
        sims["wiki_knowledge"] = sims.get("wiki_knowledge", 0.0) + 0.08
    if re.search(r"^\d{4}-\d{2}-\d{2}", content, re.M):
        sims["memory_log"] = sims.get("memory_log", 0.0) + 0.08

    # AUDIT self-check: Correct implementation? yes - filename hints are explicit routing intent.
    # AUDIT self-check: Break callers? no - content scoring remains fallback when no hint exists.
    # AUDIT self-check: Simpler alternative? yes - deterministic filename-priority shortcut.
    if filename_hints:
        hinted_type = max(filename_hints, key=lambda c: sims.get(c, 0.0))
        hinted_confidence = max(0.8, min(sims.get(hinted_type, 0.0) / 0.6, 1.0))
        return hinted_type, round(hinted_confidence, 2)

    # Pick best category
    best_type = "other"
    best_score = 0.0
    for ct in CONTENT_TYPES:
        if ct != "other" and sims.get(ct, 0.0) > best_score:
            best_score = sims[ct]
            best_type = ct

    # Normalise: TF-IDF cosine is typically 0.0–0.6 range
    confidence = min(best_score / 0.6, 1.0)
    if confidence < 0.25:
        return "other", round(confidence, 2)
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
            f.name for f in plans_dir.iterdir() if f.is_file() and f.suffix == ".md"
        ]

    briefs_dir = plans_dir / "briefs"
    if briefs_dir.exists():
        meta["existing_briefs"] = [
            f.name for f in briefs_dir.iterdir() if f.is_file() and f.suffix == ".md"
        ]

    wiki_dir = project_root / ".navig" / "wiki"
    if wiki_dir.exists():
        meta["existing_wiki"] = [
            f.name for f in wiki_dir.iterdir() if f.is_file() and f.suffix == ".md"
        ]

    memory_dir = project_root / ".navig" / "memory"
    if memory_dir.exists():
        meta["existing_memory"] = [
            f.name for f in memory_dir.iterdir() if f.is_file() and f.suffix == ".md"
        ]

    return meta


def list_inbox_files(project_root: Path) -> List[Path]:
    """List all .md files in .navig/plans/inbox/."""
    inbox_dir = project_root / ".navig" / "plans" / "inbox"
    if not inbox_dir.exists():
        return []
    return sorted(f for f in inbox_dir.iterdir() if f.is_file() and f.suffix == ".md")


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

        user_payload = json.dumps(
            {
                "filename": filename,
                "content": content,
                "workspace_metadata": metadata,
            },
            indent=2,
        )

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
