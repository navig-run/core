"""
Script Library for AHK Automation
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ScriptEntry:
    id: str
    goal: str
    script: str
    created_at: str
    success_count: int = 0
    last_used: str = ""
    tags: List[str] = None

    def to_dict(self):
        return asdict(self)


class ScriptLibrary:
    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            # Default to ~/.navig/ahk_library
            self.storage_dir = Path.home() / ".navig" / "ahk_library"
        else:
            self.storage_dir = storage_dir

        self.index_file = self.storage_dir / "index.json"

        # Ensure directories
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "scripts").mkdir(exist_ok=True)

        self._index: Dict[str, ScriptEntry] = {}
        self._load_index()

    def _load_index(self):
        if self.index_file.exists():
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self._index[k] = ScriptEntry(**v)
            except Exception:
                self._index = {}

    def _save_index(self):
        data = {k: v.to_dict() for k, v in self._index.items()}
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def save_script(self, goal: str, script: str, tags: List[str] = None) -> str:
        """Save a new script to the library."""
        import hashlib

        # ID is hash of goal (normalized)
        script_id = hashlib.md5(goal.lower().encode()).hexdigest()[:8]

        entry = ScriptEntry(
            id=script_id,
            goal=goal,
            script=script,
            created_at=datetime.now().isoformat(),
            success_count=0,
            last_used=datetime.now().isoformat(),
            tags=tags or [],
        )

        # Save script file
        script_path = self.storage_dir / "scripts" / f"{script_id}.ahk"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        # Update index
        self._index[script_id] = entry
        self._save_index()

        return script_id

    def find_script(self, goal: str) -> Optional[ScriptEntry]:
        """Find a script matching the goal (exact match for now)."""
        # AUDIT: MANUAL REVIEW REQUIRED — fuzzy retrieval strategy requires benchmarked ranking design and corpus migration.
        # Consider fuzzy match or semantic embeddings here for larger libraries
        import hashlib

        script_id = hashlib.md5(goal.lower().encode()).hexdigest()[:8]
        return self._index.get(script_id)

    def record_usage(self, script_id: str, success: bool):
        if script_id in self._index:
            entry = self._index[script_id]
            if success:
                entry.success_count += 1
            entry.last_used = datetime.now().isoformat()
            self._save_index()

    def list_scripts(self) -> List[ScriptEntry]:
        return list(self._index.values())
