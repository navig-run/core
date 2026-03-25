from functools import lru_cache
from navig.platform.paths import builtin_store_dir

@lru_cache(maxsize=None)
def load_prompt(slug: str) -> str:
    """
    Load a markdown prompt from the builtin store by its slug, 
    stripping YAML frontmatter if present.
    
    slug should be the relative path inside store/prompts/ without the .md extension.
    Example: load_prompt("browser/cortex_vision")
    """
    prompt_file = builtin_store_dir() / "prompts" / f"{slug}.md"
    if not prompt_file.exists():
        # Fallback if not found or return a sensible missing string
        return f"Warning: Prompt {slug} not found."
        
    content = prompt_file.read_text(encoding="utf-8")
    
    # Strip frontmatter
    if content.startswith("---"):
        # Split into at most 3 parts (first empty string before ---, yaml frontmatter, content)
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
            
    return content.strip()
