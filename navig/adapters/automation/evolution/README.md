
# Auto Evolve: AHK Script Evolution System

In this phase, we are implementing `navig.adapters.automation.evolution`. This system allows NAVIG to not just generate AHK scripts, but *improve* them over time based on execution results.

It's a feedback loop:
1. **Generate**: Create initial script from goal.
2. **Execute**: Run script and capture stdout/stderr.
3. **Analyze**: If failed or suboptimal, use AI to diagnose why.
4. **Refine**: Generate corrected script.
5. **Save**: Store successful scripts in a library for future reuse.

## Components to Build

### 1. Script Library (`library.py`)
- Local database (JSON/SQLite) of successful scripts.
- Keyed by "goal embedding" or keywords.
- "How to open spotify" -> `library/open_spotify.ahk`

### 2. Evolution Loop (`evolver.py`)
- The main logic for the Generate -> Test -> Refine loop.
- Max retries configuration.
- Error pattern matching.

### 3. CLI Integration
- `navig ahk evolve "goal"`
- `navig ahk library list`

Let's build the `evolver.py` first.
