# Formations Quick Reference

**Profile-Based Agent Pack System** ‚Äî Phase 1 Complete ‚úÖ

---

## ÌæØ Concept

**Formation** = Multi-agent team bundle for a specific domain (e.g., software dev, creative agency, football club)

**Profile** = `.navig/profile.json` file that binds a project to a formation

**Agent** = AI persona with system prompt, role, traits, council weight

---

## Ì∂•Ô∏è CLI Commands

```bash
# List all formations
navig formation list
navig formation list --json

# Show details
navig formation show navig_app
navig formation show creative_studio --json

# Initialize (activate for project)
navig formation init navig_app

# List agents in active formation
navig formation agents
navig formation agents --plain

# Run single agent
navig agent run architect --task "Design microservices"
navig agent run architect --task "Security review" --json --timeout 60

# Council deliberation (multi-agent)
navig council run "Should we migrate to Kubernetes?"
navig council run "Budget strategy" --rounds 3 --timeout 90 --json
```

---

## Ì¥ß VS Code Extension (Phase 1)

### Enable Feature
**Settings** ‚Üí `navig-copilot.formations.enabled` ‚Üí `true` (default: `false`)

### Commands (Ctrl+Shift+P)
- **ÌæØ Switch Formation** ‚Äî QuickPick with 5 known + custom input
- **ÌæØ Formation: List Agents** ‚Äî Shows agents from active formation via CLI

### Activation Log
Extension Output panel ‚Üí **NAVIG Copilot** channel:
```
[FORMATION] Active formation: <id> (file | default)
```

---

## Ì≥¶ Built-in Formations

| Formation ID        | Agents | Domain                 | Aliases                        |
|---------------------|--------|------------------------|--------------------------------|
| `navig_app`         | 5      | Software development   | app_project, dev_team, software|
| `creative_studio`   | 6      | Creative agency        | creative, agency, studio       |
| `football_club`     | 6      | Sports management      | football, soccer, club         |
| `government`        | 5      | Public sector          | gov, public_sector, admin      |

---

## Ìª†Ô∏è Creating Custom Formations

### Directory Structure
```
formations/my_team/           # Formation directory
  formation.json              # Team manifest
  agents/
    leader.agent.json         # Agent files (one per agent)
    analyst.agent.json
    designer.agent.json
```

### `formation.json` Template
```json
{
  "id": "my_team",
  "name": "My Custom Team",
  "version": "1.0.0",
  "description": "Custom formation for...",
  "agents": ["leader", "analyst", "designer"],
  "default_agent": "leader",
  "aliases": ["custom", "myteam"]
}
```

### Agent File Template (`leader.agent.json`)
```json
{
  "id": "leader",
  "name": "Team Leader",
  "role": "Strategic planning and coordination",
  "system_prompt": "You are a team leader responsible for... (min 100 chars)",
  "traits": ["strategic", "decisive", "collaborative"],
  "personality": "Professional and supportive",
  "council_weight": 0.9,
  "color": "#3498db"
}
```

**Location:**
- **Project-level**: `formations/` (tracked in git)
- **User-level**: `~/.navig/formations/` (global, not tracked)

---

## Ì≥ù `.navig/profile.json` Format

```json
{
  "version": 1,
  "profile": "creative_studio"
}
```

**Validation:**
- `version`: Integer or string (1 or "1.0")
- `profile`: Lowercase, alphanumeric, hyphens, underscores, starts with letter

---

## Ì¥ç Profile Resolution (Extension)

1. **Check workspace root** for `.navig/profile.json`
2. **If exists**: Validate format ‚Üí use `profile` field
3. **If missing**: Fallback to `app_project` (default)
4. **If invalid**: Fallback to `app_project` + log warning

**Fallback alias:** `app_project` ‚Üí resolves to `navig_app` formation

---

## Ì∑™ Testing Checklist

- [ ] Formations disabled (default) ‚Üí commands show "enable in settings"
- [ ] Profile resolution ‚Üí logs correct formation on activation
- [ ] Switch Formation ‚Üí QuickPick works, writes profile.json
- [ ] Custom input ‚Üí validates ID format
- [ ] List Agents ‚Üí CLI integration, QuickPick shows agents
- [ ] CLI agent run ‚Üí executes with task, returns AI response
- [ ] CLI council run ‚Üí multi-agent deliberation works
- [ ] All 770 CLI tests pass
- [ ] Extension compiles clean (zero TypeScript errors)

---

## Ì∫Ä Next Phase (Phase 2 ‚Äî Future)

- [ ] Formation sidebar tree view (expandable nodes)
- [ ] Agent context menus (run agent, view prompt, edit)
- [ ] Inline formation switcher in sidebar header
- [ ] Formation creation wizard
- [ ] Agent prompt editor UI
- [ ] Council run from sidebar

---

## Ì≥ö Documentation

- [HANDBOOK.md](HANDBOOK.md) ‚Äî Section 34: Formation System
- [FORMATIONS_TESTING_GUIDE.md](FORMATIONS_TESTING_GUIDE.md) ‚Äî 14 test scenarios
- [formations/README.md](../formations/README.md) ‚Äî JSON schema reference
- [CHANGELOG.md](../CHANGELOG.md) ‚Äî Unreleased: Formation System

---

**Phase 1 Status:** ‚úÖ Complete ‚Äî All features implemented, tested, documented
