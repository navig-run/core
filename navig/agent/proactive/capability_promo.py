"""
Capability Promoter — Feature Discovery Engine

Maintains a registry of NAVIG features and intelligently promotes
underused or contextually relevant ones to the operator.

Inspired by OpenClaw's skill self-installation pattern where skill files
declare their own heartbeat entries. NAVIG adapts this: each feature
group declares promotion metadata (description, when-to-suggest,
prerequisites, example command).

This is NOT a notification spam system. It's a discovery engine that
surfaces the right feature at the right time.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from navig.agent.proactive.user_state import UserStateTracker


@dataclass
class FeatureInfo:
    """Metadata for a promotable feature."""
    key: str                    # Unique identifier (matches command prefix)
    name: str                   # Human-readable name
    description: str            # What it does (1-2 sentences)
    example_command: str        # Example usage
    category: str               # Feature category
    prerequisites: List[str] = field(default_factory=list)   # Required context
    when_to_suggest: str = ""   # Natural language trigger hint
    min_interactions: int = 5   # Min interactions before promoting this
    priority: int = 5           # Base priority (1-10)


# ─── Feature Registry ───────────────────────────────────────────────
# Each entry maps to a NAVIG capability. The promoter uses usage stats
# to find features the operator hasn't tried and picks contextually
# relevant ones to suggest.

FEATURE_REGISTRY: List[FeatureInfo] = [
    # Host Management
    FeatureInfo(
        key="host_monitor",
        name="Server Monitoring",
        description="Real-time health dashboard for your servers — CPU, memory, disk, processes.",
        example_command="navig host monitor show",
        category="infrastructure",
        when_to_suggest="User manages servers but hasn't checked health recently.",
    ),
    FeatureInfo(
        key="host_security",
        name="Security Audit",
        description="Quick security scan: firewall rules, SSH config, open ports, pending updates.",
        example_command="navig host security show",
        category="infrastructure",
        when_to_suggest="User works with servers, good periodic check.",
    ),
    FeatureInfo(
        key="host_maintenance",
        name="Server Maintenance",
        description="Automated maintenance tasks — system updates, log rotation, temp cleanup.",
        example_command="navig host maintenance --dry-run",
        category="infrastructure",
        when_to_suggest="Server has been running a while without maintenance.",
    ),

    # Database Operations
    FeatureInfo(
        key="db_optimize",
        name="Database Optimization",
        description="Optimize database tables for better query performance.",
        example_command="navig db optimize <table> -d <database>",
        category="database",
        when_to_suggest="User runs frequent queries, tables may need optimization.",
    ),
    FeatureInfo(
        key="db_dump",
        name="Database Backup",
        description="Quick database dumps with compression. Protect your data.",
        example_command="navig db dump <database> -o backup.sql",
        category="database",
        prerequisites=["db"],
        when_to_suggest="User queries databases but hasn't backed up recently.",
    ),

    # Docker Operations
    FeatureInfo(
        key="docker_stats",
        name="Container Stats",
        description="Live resource usage for all containers — CPU%, memory, network I/O.",
        example_command="navig docker stats",
        category="containers",
        when_to_suggest="User works with Docker containers.",
    ),
    FeatureInfo(
        key="docker_compose",
        name="Docker Compose",
        description="Manage multi-container apps with compose commands through NAVIG.",
        example_command="navig docker compose up -d",
        category="containers",
        prerequisites=["docker"],
        when_to_suggest="User manages Docker but uses individual commands.",
    ),

    # File Operations
    FeatureInfo(
        key="file_tree",
        name="Directory Tree View",
        description="Visual tree view of remote directories with depth control.",
        example_command="navig file list /var/www --tree --depth 3",
        category="files",
        when_to_suggest="User browses remote files frequently.",
    ),
    FeatureInfo(
        key="file_edit",
        name="Remote File Editing",
        description="Edit remote files directly — content injection, permissions, ownership.",
        example_command='navig file edit /tmp/config.txt --content "new content"',
        category="files",
        when_to_suggest="User reads files but hasn't edited remotely yet.",
    ),

    # Web Server
    FeatureInfo(
        key="web_recommend",
        name="Web Performance Tuning",
        description="AI-powered performance recommendations for your web server config.",
        example_command="navig web recommend",
        category="web",
        when_to_suggest="User manages web servers.",
    ),

    # Workflows
    FeatureInfo(
        key="flow",
        name="Automated Workflows",
        description="Multi-step automated tasks — deploy, backup, maintenance pipelines.",
        example_command="navig flow list",
        category="automation",
        when_to_suggest="User runs repetitive command sequences.",
        min_interactions=20,
    ),

    # Backup
    FeatureInfo(
        key="backup_run",
        name="Full System Backup",
        description="Comprehensive backup — databases, configs, web server, all at once.",
        example_command="navig backup run --all --compress gzip",
        category="backup",
        when_to_suggest="User hasn't done a full backup in a while.",
    ),

    # Config
    FeatureInfo(
        key="config_validate",
        name="Config Validation",
        description="Validate your NAVIG configuration for errors and best practices.",
        example_command="navig config validate --scope both",
        category="config",
        when_to_suggest="User encounters configuration errors.",
    ),

    # Application Management
    FeatureInfo(
        key="app_search",
        name="Cross-Host App Search",
        description="Find applications across all your configured hosts instantly.",
        example_command="navig app search myapp",
        category="apps",
        when_to_suggest="User manages multiple hosts with applications.",
        min_interactions=15,
    ),

    # Scaffolding
    FeatureInfo(
        key="scaffold",
        name="Project Scaffolding",
        description="Generate project structures from YAML templates — local or remote.",
        example_command="navig scaffold apply template.yaml --dry-run",
        category="automation",
        when_to_suggest="User creates new projects or directory structures.",
        min_interactions=25,
    ),
]


class CapabilityPromoter:
    """
    Selects the most relevant feature to promote based on usage patterns.
    
    Selection algorithm:
    1. Filter out features the user already uses frequently
    2. Filter out features whose prerequisites the user hasn't met
    3. Score remaining by: base priority + contextual relevance
    4. Pick top candidate
    """

    def __init__(self, features: Optional[List[FeatureInfo]] = None):
        self.features = features or FEATURE_REGISTRY
        self._promotion_history: List[str] = []  # Track recent promotions
        self._max_history = 20

    def get_promotion(
        self, state: UserStateTracker
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Get a feature promotion message and feature key.
        
        Returns (message, feature_key) or (None, None) if nothing to promote.
        """
        candidates = self._score_candidates(state)

        if not candidates:
            return None, None

        # Pick best candidate, avoiding recent repeats
        for feature, score in candidates:
            if feature.key not in self._promotion_history[-5:]:
                msg = self._build_promotion_message(feature)
                self._promotion_history.append(feature.key)
                if len(self._promotion_history) > self._max_history:
                    self._promotion_history = self._promotion_history[-self._max_history:]
                return msg, feature.key

        # All top candidates were recently promoted — pick best anyway
        feature, _ = candidates[0]
        msg = self._build_promotion_message(feature)
        return msg, feature.key

    def _score_candidates(
        self, state: UserStateTracker
    ) -> List[Tuple[FeatureInfo, float]]:
        """Score all features and return sorted candidates."""
        used_features = set(state.stats.features_used.keys())
        total_interactions = state.stats.total_messages

        scored: List[Tuple[FeatureInfo, float]] = []

        for feature in self.features:
            # Skip if not enough interactions
            if total_interactions < feature.min_interactions:
                continue

            # Skip if user already uses this feature heavily
            usage_count = state.stats.features_used.get(feature.key, 0)
            if usage_count >= 10:
                continue

            # Check prerequisites (user should have used prerequisite features)
            if feature.prerequisites:
                prereqs_met = all(
                    p in used_features for p in feature.prerequisites
                )
                if not prereqs_met:
                    continue

            # Score: base priority + novelty bonus + category relevance
            score = float(feature.priority)

            # Novelty bonus: never-used features get +3
            if usage_count == 0:
                score += 3.0
            elif usage_count < 3:
                score += 1.0

            # Category relevance: if user uses features in same category, +2
            category_features = [
                f.key for f in self.features
                if f.category == feature.category and f.key != feature.key
            ]
            if any(f in used_features for f in category_features):
                score += 2.0

            scored.append((feature, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _build_promotion_message(self, feature: FeatureInfo) -> str:
        """Build a natural promotion message for a feature."""
        return (
            f"💡 *{feature.name}*: {feature.description}\n"
            f"Try: `{feature.example_command}`"
        )

    def get_all_feature_keys(self) -> List[str]:
        """Get all registered feature keys (for underused feature detection)."""
        return [f.key for f in self.features]
