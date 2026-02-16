"""
Formation Loader

Dynamically discovers and loads formations from the formations/ directory.
No hardcoded map — formations are discovered by scanning for directories
containing formation.json. Community can create new formations by adding
a directory with a valid formation.json manifest.

Supports two agent formats:
  1. Directory-based (hybrid): agents/<id>/agent.json + SOUL.md + PERSONALITY.md + PLAYBOOK.md + MEMORY.md
  2. Flat file (legacy):      agents/<id>.agent.json

Resolution chain:
  <cwd>/.navig/profile.json → profile field → scan formations/ → match by id or alias
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from navig.debug_logger import get_debug_logger
from navig.formations.schema import (
    FormationValidationError,
    validate_agent_file,
    validate_formation_file,
    validate_profile_data,
)
from navig.formations.types import AgentSpec, Formation, ProfileConfig

logger = get_debug_logger()

# Default locations — can be overridden for testing
_FORMATIONS_ROOTS: List[Path] = []


def _get_formations_roots() -> List[Path]:
    """Get all directories to scan for formations.
    
    Searches:
    1. <project-root>/formations/  (shipped with NAVIG)
    2. ~/.navig/formations/        (user-installed formations)
    """
    if _FORMATIONS_ROOTS:
        return _FORMATIONS_ROOTS

    roots: List[Path] = []

    # Project-level formations (shipped with NAVIG)
    project_root = Path(__file__).resolve().parent.parent.parent
    project_formations = project_root / "formations"
    if project_formations.is_dir():
        roots.append(project_formations)

    # User-level formations (community-installed)
    user_formations = Path.home() / ".navig" / "formations"
    if user_formations.is_dir():
        roots.append(user_formations)

    return roots


def set_formations_roots(roots: List[Path]) -> None:
    """Override formation roots (for testing)."""
    global _FORMATIONS_ROOTS
    _FORMATIONS_ROOTS = list(roots)


def clear_formations_roots() -> None:
    """Reset to default formation roots."""
    global _FORMATIONS_ROOTS
    _FORMATIONS_ROOTS = []


def discover_formations() -> Dict[str, Path]:
    """Scan all formation roots and build a map of profile_name → formation_dir.
    
    Discovers formations dynamically:
    - Each subdirectory containing formation.json is a valid formation
    - The formation's "id" field is the primary key
    - The "aliases" field (if present) adds additional lookup keys
    
    Returns dict mapping profile names (ids + aliases) to formation directories.
    """
    formation_map: Dict[str, Path] = {}

    for root in _get_formations_roots():
        if not root.is_dir():
            continue
        for subdir in sorted(root.iterdir()):
            if not subdir.is_dir():
                continue
            manifest = subdir / "formation.json"
            if not manifest.exists():
                continue

            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                formation_id = data.get("id", subdir.name)
                
                # Primary key: formation ID
                if formation_id not in formation_map:
                    formation_map[formation_id] = subdir
                else:
                    logger.warning(
                        f"[FORMATION] Duplicate formation ID '{formation_id}' "
                        f"found at {subdir}, already registered from {formation_map[formation_id]}"
                    )

                # Aliases: additional lookup keys
                for alias in data.get("aliases", []):
                    if alias not in formation_map:
                        formation_map[alias] = subdir
                    else:
                        logger.debug(
                            f"[FORMATION] Alias '{alias}' already mapped, "
                            f"skipping from {subdir}"
                        )

            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"[FORMATION] Skipping {subdir}: {e}")

    return formation_map


def read_profile(workspace_dir: Optional[Path] = None) -> Optional[ProfileConfig]:
    """Read .navig/profile.json from workspace directory.
    
    Args:
        workspace_dir: Workspace root. Defaults to cwd.
    
    Returns:
        ProfileConfig if profile.json exists and is valid, None otherwise.
    """
    ws = workspace_dir or Path.cwd()
    profile_path = ws / ".navig" / "profile.json"

    if not profile_path.exists():
        return None

    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error(f"[FORMATION] Invalid JSON in profile.json: {e}")
        return None

    errors = validate_profile_data(data, path=profile_path)
    if errors:
        logger.error(f"[FORMATION] Invalid profile.json: {'; '.join(errors)}")
        return None

    return ProfileConfig.from_dict(data)


def resolve_formation(
    profile: str,
    formation_map: Optional[Dict[str, Path]] = None,
) -> Optional[Path]:
    """Resolve a profile name to a formation directory.
    
    Args:
        profile: Profile name from profile.json
        formation_map: Pre-built map (discovers if None)
    
    Returns:
        Path to formation directory, or None if not found.
    """
    if formation_map is None:
        formation_map = discover_formations()

    formation_dir = formation_map.get(profile)
    if formation_dir is None:
        logger.warning(
            f"[FORMATION] Unknown profile '{profile}'. "
            f"Available: {', '.join(sorted(formation_map.keys())) or '(none)'}"
        )
        return None

    return formation_dir


# ---------------------------------------------------------------------------
# Directory-based agent profiles (hybrid format)
# ---------------------------------------------------------------------------

def _read_doc(agent_dir: Path, doc_path: str) -> str:
    """Read a linked markdown document from an agent profile directory.
    
    Args:
        agent_dir: Agent profile directory
        doc_path: Relative path from agent.json (e.g. "./SOUL.md")
    
    Returns:
        File content as string, or empty string if not found.
    """
    full_path = (agent_dir / doc_path).resolve()
    if not full_path.exists():
        return ""
    try:
        return full_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"[FORMATION] Could not read doc {full_path}: {e}")
        return ""


def _compose_system_prompt(
    name: str,
    role: str,
    soul: str,
    personality: str,
    playbook: str,
) -> str:
    """Compose a full system prompt from markdown profile documents.
    
    Merges SOUL (identity/values), PERSONALITY (speech/tone/quirks),
    and PLAYBOOK (contextual behavior rules) into a single coherent prompt.
    
    Args:
        name: Agent display name
        role: Agent role title
        soul: Content of SOUL.md
        personality: Content of PERSONALITY.md
        playbook: Content of PLAYBOOK.md
    
    Returns:
        Composed system prompt string.
    """
    parts: list[str] = []

    if soul:
        parts.append(soul)

    if personality:
        parts.append(personality)

    if playbook:
        parts.append(playbook)

    if not parts:
        # Fallback: minimal prompt when no docs exist
        return f"You are {name}, {role}."

    return "\n\n---\n\n".join(parts)


def _load_agent_from_directory(agent_dir: Path) -> Optional[AgentSpec]:
    """Load an agent from a directory-based hybrid profile.
    
    Expected structure:
        agent_dir/
            agent.json      — structured config & doc links
            SOUL.md         — identity, values, what drives them
            PERSONALITY.md  — tone, speech patterns, quirks, vocabulary
            PLAYBOOK.md     — contextual behavior rules (council, briefs, etc.)
            MEMORY.md       — curated memories & key facts (grows over time)
    
    The system_prompt is composed from the markdown docs at load time.
    The personality field uses the 'summary' from agent.json.
    
    Args:
        agent_dir: Path to agent profile directory
    
    Returns:
        AgentSpec if successful, None on failure.
    """
    manifest = agent_dir / "agent.json"
    if not manifest.exists():
        return None

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error(f"[FORMATION] Invalid JSON in {manifest}: {e}")
        return None

    # Validate required fields
    required = ["id", "name", "role"]
    missing = [f for f in required if f not in data]
    if missing:
        logger.error(
            f"[FORMATION] Agent {manifest} missing required fields: {missing}"
        )
        return None

    # Read linked markdown docs
    docs_config = data.get("docs", {})
    docs_content: Dict[str, str] = {}
    for doc_key in ("soul", "personality", "memory", "playbook"):
        doc_path = docs_config.get(doc_key)
        if doc_path:
            docs_content[doc_key] = _read_doc(agent_dir, doc_path)
        else:
            # Try conventional filenames even without explicit links
            conventional = {
                "soul": "SOUL.md",
                "personality": "PERSONALITY.md",
                "memory": "MEMORY.md",
                "playbook": "PLAYBOOK.md",
            }
            docs_content[doc_key] = _read_doc(agent_dir, conventional[doc_key])

    # Compose system_prompt from docs
    system_prompt = _compose_system_prompt(
        name=data["name"],
        role=data["role"],
        soul=docs_content.get("soul", ""),
        personality=docs_content.get("personality", ""),
        playbook=docs_content.get("playbook", ""),
    )

    # Use summary as personality field (for council differentiation etc.)
    personality = data.get("summary", "")
    if not personality:
        # Fallback: extract first substantial paragraph from SOUL.md
        soul_text = docs_content.get("soul", "")
        if soul_text:
            for line in soul_text.split("\n"):
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("#")
                    and not stripped.startswith("**")
                    and not stripped.startswith("---")
                    and not stripped.startswith("*")
                    and len(stripped) > 20
                ):
                    personality = stripped
                    break

    # Extract flags (council_weight, etc.)
    flags = data.get("flags", {})
    council_weight = flags.get(
        "council_weight", data.get("council_weight", 1.0)
    )

    return AgentSpec(
        id=data["id"],
        name=data["name"],
        role=data["role"],
        traits=data.get("traits", []),
        personality=personality,
        scope=data.get("scope", []),
        system_prompt=system_prompt,
        kpis=data.get("kpis", []),
        council_weight=float(council_weight),
        api_dependencies=data.get("api_dependencies", []),
        tools=data.get("tools", []),
        source_path=manifest,
    )


def load_formation(formation_dir: Path) -> Optional[Formation]:
    """Load and validate a formation from its directory.
    
    Args:
        formation_dir: Path to formation directory containing formation.json
    
    Returns:
        Loaded Formation with all agents, or None on failure.
    """
    manifest_path = formation_dir / "formation.json"

    try:
        formation = validate_formation_file(manifest_path)
    except FormationValidationError as e:
        logger.error(f"[FORMATION] {e}")
        return None

    # Load agents from agents/ directory
    agents_dir = formation_dir / "agents"
    if not agents_dir.is_dir():
        logger.warning(f"[FORMATION] No agents/ directory in {formation_dir}")
        return formation

    for agent_id in formation.agents:
        # Try directory-based profile first (hybrid format), then flat .agent.json
        agent_dir = agents_dir / agent_id
        agent_file = agents_dir / f"{agent_id}.agent.json"

        try:
            if agent_dir.is_dir() and (agent_dir / "agent.json").exists():
                agent = _load_agent_from_directory(agent_dir)
                if agent:
                    formation.loaded_agents[agent_id] = agent
                    logger.debug(
                        f"[FORMATION] Loaded agent profile: {agent.name} ({agent.id}) "
                        f"from {agent_dir}"
                    )
                    continue

            # Fallback to flat .agent.json
            if agent_file.exists():
                agent = validate_agent_file(agent_file)
                formation.loaded_agents[agent_id] = agent
                logger.debug(f"[FORMATION] Loaded agent: {agent.name} ({agent.id})")
            else:
                logger.warning(
                    f"[FORMATION] Agent '{agent_id}' not found at "
                    f"{agent_dir} or {agent_file}"
                )
        except FormationValidationError as e:
            logger.warning(f"[FORMATION] Skipping agent '{agent_id}': {e}")

    loaded = len(formation.loaded_agents)
    expected = len(formation.agents)
    if loaded < expected:
        logger.warning(
            f"[FORMATION] Loaded {loaded}/{expected} agents for '{formation.id}'"
        )
    else:
        logger.info(
            f"[FORMATION] Formation '{formation.name}' loaded with {loaded} agents"
        )

    return formation


# Default fallback profile when none is configured or resolution fails
DEFAULT_PROFILE = "app_project"


def get_active_formation(workspace_dir: Optional[Path] = None) -> Optional[Formation]:
    """Full resolution chain: workspace → profile → formation → agents.
    
    This is the main entry point. Call this to get the currently active
    formation for a workspace.
    
    If no profile exists or the configured profile cannot be resolved,
    falls back to the default profile ('app_project').
    
    Args:
        workspace_dir: Workspace root. Defaults to cwd.
    
    Returns:
        Loaded Formation or None (only if even the fallback fails).
    """
    profile = read_profile(workspace_dir)
    profile_name = profile.profile if profile else DEFAULT_PROFILE

    formation_dir = resolve_formation(profile_name)
    if formation_dir is None:
        # Fallback to default if configured profile not found
        if profile_name != DEFAULT_PROFILE:
            logger.warning(
                f"[FORMATION] Profile '{profile_name}' not found, "
                f"falling back to '{DEFAULT_PROFILE}'"
            )
            formation_dir = resolve_formation(DEFAULT_PROFILE)
        if formation_dir is None:
            logger.warning(f"[FORMATION] Default profile '{DEFAULT_PROFILE}' not found")
            return None

    return load_formation(formation_dir)


def list_available_formations() -> List[Formation]:
    """List all discovered formations with basic metadata.
    
    Loads formation.json from each discovered formation directory
    but does NOT load agents (lightweight).
    """
    formations: List[Formation] = []
    seen_ids: set = set()

    for root in _get_formations_roots():
        if not root.is_dir():
            continue
        for subdir in sorted(root.iterdir()):
            if not subdir.is_dir():
                continue
            manifest = subdir / "formation.json"
            if not manifest.exists():
                continue
            try:
                formation = validate_formation_file(manifest)
                if formation.id not in seen_ids:
                    formations.append(formation)
                    seen_ids.add(formation.id)
            except FormationValidationError as e:
                logger.warning(f"[FORMATION] Skipping {subdir.name}: {e}")

    return formations
