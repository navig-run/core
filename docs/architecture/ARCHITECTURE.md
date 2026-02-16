# NAVIG Cross-Platform Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    NAVIG EVOLUTION SYSTEM                        │
│                         (AI-Powered)                             │
└─────────────────────────────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
         ┌──────────▼──────────┐   ┌─────────▼──────────┐
         │  EVOLVER SYSTEM     │   │   CLI INTERFACE    │
         │                     │   │                    │
         │  • WorkflowEvolver  │   │  • navig auto      │
         │  • ScriptEvolver    │   │  • navig ahk       │
         │  • FixEvolver       │   │  • navig script    │
         │  • SkillEvolver     │   │  • navig evolve    │
         │  • PackEvolver      │   │  • navig workflow  │
         └──────────┬──────────┘   └─────────┬──────────┘
                    │                        │
                    └────────────┬───────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   WORKFLOW ENGINE       │
                    │                         │
                    │  • YAML Parser          │
                    │  • Safe Eval            │
                    │  • Variable System      │
                    │  • Condition Handler    │
                    │  • Platform Detector    │
                    └────────────┬────────────┘
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
┌─────────▼─────────┐  ┌────────▼────────┐  ┌─────────▼─────────┐
│  WINDOWS ADAPTER  │  │  LINUX ADAPTER  │  │  macOS ADAPTER    │
│                   │  │                 │  │                   │
│  AutoHotkey v2    │  │  xdotool        │  │  AppleScript      │
│  • Window Mgmt    │  │  wmctrl         │  │  osascript        │
│  • Hotkeys        │  │  xclip          │  │  cliclick (opt)   │
│  • Control Text   │  │  • X11 Windows  │  │  • Native Apps    │
│  • Screenshots    │  │  • Clipboard    │  │  • Clipboard      │
│  • OCR            │  │  • Mouse/Kbd    │  │  • Mouse/Kbd      │
└─────────┬─────────┘  └────────┬────────┘  └─────────┬─────────┘
          │                     │                      │
          │                     │                      │
    ┌─────▼─────┐         ┌────▼────┐           ┌────▼────┐
    │  WINDOWS  │         │  LINUX  │           │  macOS  │
    │     OS    │         │    OS   │           │    OS   │
    └───────────┘         └─────────┘           └─────────┘

┌─────────────────────────────────────────────────────────────────┐
│                         DATA FLOW                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. User → CLI → Workflow Engine                                │
│  2. AI → Evolver → YAML File → Workflow Engine                  │
│  3. Workflow Engine → Platform Detection → Adapter Selection    │
│  4. Adapter → OS-Specific API → Desktop Actions                 │
│  5. Actions → Results → Variable Capture → Next Step            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     FILE STRUCTURE                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  navig/                                                          │
│  ├── adapters/automation/                                       │
│  │   ├── ahk.py          (Windows - AutoHotkey v2)             │
│  │   ├── linux.py        (Linux - xdotool/wmctrl)              │
│  │   └── macos.py        (macOS - AppleScript)                 │
│  ├── commands/                                                  │
│  │   ├── ahk.py          (Windows-specific CLI)                │
│  │   ├── auto.py         (Cross-platform CLI)                  │
│  │   ├── evolution.py    (AI evolution commands)               │
│  │   └── script.py       (Script management)                   │
│  ├── core/                                                      │
│  │   ├── automation_engine.py  (Workflow executor)             │
│  │   ├── safe_eval.py          (Secure evaluator)              │
│  │   └── evolution/                                             │
│  │       ├── workflow.py    (Workflow generator)                │
│  │       ├── script.py      (Script generator)                  │
│  │       ├── fix.py         (Code repair)                       │
│  │       └── base.py        (Base evolver)                      │
│  ├── scripts/           (Generated Python scripts)              │
│  ├── workflows/         (Generated YAML workflows)              │
│  └── docs/                                                      │
│      ├── automation.md                                          │
│      ├── evolution.md                                           │
│      └── CROSS_PLATFORM_EVOLUTION.md                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    SECURITY MODEL                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ✓ AST-based parsing (no eval/exec)                             │
│  ✓ Whitelisted operations only                                  │
│  ✓ No function calls in conditions                              │
│  ✓ No imports or attribute access                               │
│  ✓ Sandboxed variable scope                                     │
│  ✓ Input validation before OS calls                             │
│  ✓ Optional external validation (--check)                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  EVOLUTION LOOP                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  User Goal → AI Generate → Validate → [Pass/Fail]               │
│                                │           │                     │
│                                │           └─→ Refine → Retry   │
│                                ↓                                 │
│                              Save                                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Component Interaction Example

```
USER: navig evolve workflow "Open calculator, snap left"
  ↓
WorkflowEvolver._generate()
  ↓
AI: Creates YAML workflow
  ↓
WorkflowEvolver._validate()
  ↓
✓ Valid → WorkflowEvolver._save()
  ↓
Saved to: workflows/calculator_snap_left.yaml
  ↓
USER: navig workflow run calculator_snap_left
  ↓
WorkflowEngine.load_workflow()
  ↓
WorkflowEngine.execute_workflow()
  ↓
Step 1: open_app "calculator"
  ↓
WorkflowEngine.adapter (auto-detects Windows/Linux/macOS)
  ↓
Windows: AHKAdapter.open_app()
Linux:   LinuxAdapter.open_app()
macOS:   MacOSAdapter.open_app()
  ↓
Step 2: snap_window "calculator" "left"
  ↓
Adapter.snap_window()
  ↓
Calculator appears on left half of screen ✓
```

## Safe Eval Example

```
Workflow YAML:
  if: "window_count > 5 and status == 'ready'"

WorkflowEngine processing:
  ↓
Variables: {
  "window_count": 12,
  "status": "ready"
}
  ↓
safe_eval.safe_eval("window_count > 5 and status == 'ready'", vars)
  ↓
AST Parse: Compare(window_count, GT, 5) AND Compare(status, EQ, 'ready')
  ↓
Evaluate: (12 > 5) and ('ready' == 'ready')
  ↓
Result: True
  ↓
Step executes ✓
```

## User Preferences & Context System

```
┌──────────────────────────────────────────────────────────────┐
│              USER PREFERENCES ARCHITECTURE                    │
└──────────────────────────────────────────────────────────────┘
                              │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
┌───────▼────────┐   ┌─────────▼────────┐   ┌────────▼─────────┐
│   USER.md      │   │ WorkspaceManager │   │   UserProfile    │
│   (Markdown)   │◄──┤   get_user_      │──►│     (JSON)       │
│                │   │   preferences()  │   │                  │
│ Human-editable │   │                  │   │ Programmatic     │
│ 40+ fields     │   │ Regex parsing    │   │ Dataclass-based  │
└───────┬────────┘   └─────────┬────────┘   └────────┬─────────┘
        │                      │                      │
        │    File Change       │    sync_to_         │
        │    Detection         │    user_profile()   │
        │                      │                      │
┌───────▼────────┐   ┌─────────▼────────┐   ┌────────▼─────────┐
│ ConfigWatcher  │   │  ContextLayer    │   │   NavigAI        │
│                │   │                  │   │                  │
│ Auto-sync on   │   │ get_user_        │   │ _get_user_       │
│ USER.md save   │   │ preferences()    │   │ context()        │
└────────────────┘   └──────────────────┘   └────────┬─────────┘
                                                      │
                                            ┌─────────▼─────────┐
                                            │  System Prompt    │
                                            │  + User Context   │
                                            │                   │
                                            │  Personalized AI  │
                                            └───────────────────┘

Fields Parsed:
├── Identity (6): name, timezone, pronouns, location
├── Work Patterns (5): work_hours, do_not_disturb, peak_productivity
├── Communication (7): verbosity, style, language, notification_channels
├── Technical (8): languages, editor, os, cloud, shell, package_managers
├── NAVIG Features (3): proactive_assistance, daily_logs, voice_tts
├── Life-OS (4): goals, health_focus, wealth_focus, learning_targets
└── Automation (2): autonomous_actions, requires_confirmation
```

**Key Features:**
- **Flexible Parsing**: Regex-based field matching (handles variations)
- **Time Parsing**: 12h/24h formats, multiple separators, overnight ranges
- **Auto-Sync**: File watcher triggers USER.md → UserProfile sync
- **AI Context**: Injects preferences into agent system prompts
- **Hot-Reload**: Changes apply without restart

**See**: `docs/USER_PREFERENCES_INTEGRATION.md` for full documentation

