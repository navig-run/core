
# Navig Evolution System

The Evolution System allows Navig to self-improve by generating, validating, and refining its own artifacts using AI.

## Supported Artifacts

### 1. Skills (`navig evolve skill`)
Generates `SKILL.md` files which define new capabilities for the AI agent.
- **Input:** Natural language goal (e.g., "Create a skill to manage Docker containers").
- **Output:** A directory in `skills/` with a `SKILL.md` file.
- **Validation:** YAML frontmatter check, instruction length check.

### 2. Workflows (`navig evolve workflow`)
Generates cross-platform YAML automation workflows.
- **Input:** Natural language goal (e.g., "Minimize all notepad windows").
- **Output:** A `.yaml` file in `workflows/`.
- **Validation:** YAML syntax check, schema validation.
- **Features:** Actions, conditionals, variable capture, safe evaluation.

### 3. Packs (`navig evolve pack`)
Generates collections of related skills and workflows.
- **Input:** Name/Goal (e.g., "Web Dev Pack").
- **Output:** A pack directory with `pack.yaml`.

### 4. Scripts (`navig evolve script`)
Generates standalone Python scripts.
- **Input:** Task description.
- **Output:** A `.py` file in `scripts/`.
- **Validation:** Python syntax check.

### 5. Code Fixes (`navig evolve fix`)
Repairs bugs or improves existing code files.
- **Input:** File path + Issue description.
- **Output:** Updated file content (in-place).
- **Validation:** Syntax check, optional external test command.

## Architecture

The system is built on a `BaseEvolver` class that implements a "Generate -> Validate -> Refine" loop.

```python
class BaseEvolver(ABC):
    def evolve(self, goal):
        # 1. Check Cache
        # 2. Generate Draft (AI)
        # 3. Validate Artifact
        # 4. If Invalid -> Refine Draft with Error (AI) -> GOTO 3
        # 5. Save Artifact
```

## Usage

```bash
# Evolve a new skill
navig evolve skill "Look up DNS records knowing the domain"

# Evolve a workflow
navig evolve workflow "Open browser and navigate to Google"

# Evolve a pack (collection of skills)
navig evolve pack "Web Development Pack with Node.js and React skills"

# Evolve a python script
navig evolve script "Backup mysql database to S3"

# Fix code
navig evolve fix app.py "Fix the division by zero error"
```

## Extending

To add a new evolver (e.g., for Tasks or Packs):
1. Create a class inheriting from `navig.core.evolution.base.BaseEvolver`.
2. Implement `_generate`, `_validate`, and `_save`.
3. Register it in `navig/commands/evolution.py`.

## Cross-Platform Automation

NAVIG workflows now support Windows, macOS, and Linux:

- **Windows**: AutoHotkey v2 adapter (`navig.adapters.automation.ahk`)
- **Linux**: xdotool/wmctrl adapter (`navig.adapters.automation.linux`)
- **macOS**: AppleScript adapter (`navig.adapters.automation.macos`)

The `WorkflowEngine` automatically selects the correct adapter for your platform.
Workflows can use platform-specific overrides via the `platform` key.

See `docs/automation.md` for full documentation.


