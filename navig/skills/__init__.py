# navig/skills/__init__.py
"""Skill discovery and loading package."""

from navig.skills.loader import Skill, get_skill_dirs, load_all_skills

__all__ = ["Skill", "load_all_skills", "get_skill_dirs"]
