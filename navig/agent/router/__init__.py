"""
navig.agent.router
==================
Prompt-routing sub-package.

Public surface
--------------
classify_by_llm           -- async LLM-fallback tier classifier (use when rule
                             classifier confidence < threshold)
should_use_llm_classifier -- threshold guard; call before classify_by_llm
"""

from navig.agent.router.llm_classifier import (
    classify_by_llm,
    should_use_llm_classifier,
)

__all__ = ["classify_by_llm", "should_use_llm_classifier"]
