# navig/skills/__init__.py
"""Skill discovery and loading package."""

from navig.skills.loader import Skill, load_all_skills, get_skill_dirs

__all__ = ["Skill", "load_all_skills", "get_skill_dirs"]
