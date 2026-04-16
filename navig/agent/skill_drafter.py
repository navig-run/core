from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from navig.core.yaml_io import atomic_write_text
from navig.platform.paths import config_dir


@dataclass
class SkillDraft:
    name: str
    safe: bool
    yaml_text: str


class SkillDrafter:
    def __init__(self, output_dir: Path | None = None):
        self.output_dir = Path(output_dir or (config_dir() / "skills"))

    def draft(self, pattern) -> SkillDraft:
        sequence = list(getattr(pattern, "sequence", ()) or [])
        cmd = sequence[0] if sequence else "command"
        name = self._slugify(cmd) or "generated-skill"
        safe = not any(token in cmd.lower() for token in ("rm ", "--force", "drop ", "truncate"))
        yaml_text = (
            "name: " + name + "\n"
            + "description: Auto-generated skill\n"
            + "steps:\n"
            + f"  - run: {cmd}\n"
        )
        return SkillDraft(name=name, safe=safe, yaml_text=yaml_text)

    def apply(self, draft: SkillDraft) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{draft.name}.yaml"
        atomic_write_text(path, draft.yaml_text)
        return path

    @staticmethod
    def _slugify(text: str) -> str:
        text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
        return text[:64]
