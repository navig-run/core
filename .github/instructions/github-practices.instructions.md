---
applyTo: '**'
---

# GitHub Practices — NAVIG AI Agent Directive Rules

> Authoritative rules for all AI agents (Copilot, Hermes, Nero, etc.) and human contributors
> interacting with issues, pull requests, and comments in this repository.

---

## 1. One Issue Per Pull Request (MANDATORY)

**Every PR must address exactly one issue.**

```
# Correct
feature/fix-vault-reconfigure-ux  → closes #40  (one issue)
feature/add-tavily-provider        → closes #42  (one issue)

# Forbidden
fix/open-issues-batch-1            → closes #34 #36 #40 #42 #45 #47 #48 #60  (batch)
```

### Rationale
- Batch PRs make review impossible — reviewers cannot reason about risk per-issue.
- Rollback becomes all-or-nothing instead of scoped.
- `git bisect` and blame lose precision.
- Issue lifecycle becomes ambiguous (what closed what?).

### Rules
1. Branch name must include the issue number or a slug unique to that issue.
2. PR title must reference the issue: `fix(cli): resolve help order (#47)`.
3. PR body **must** contain exactly one `Closes #N` line in the `## Closes` section.
4. Never group unrelated fixes, features, or refactors in the same branch/PR,
   even if they are "small" or "related".
5. If a fix naturally touches two issues in the same area, open two PRs that
   may share a common base but each have a single `Closes #N`.

---

## 2. Issue Analysis Before Coding (MANDATORY for AI agents)

Before opening a PR to resolve an issue, you **must** post an analysis comment
on the issue itself. The comment must include:

```markdown
## Analysis

**Root cause:** <one paragraph explaining why the bug/gap exists>

**Proposed fix:** <what will be changed, which files, which tests will be added>

**Risk level:** low / medium / high — <reason>

**Ready to implement** — will proceed unless objections are raised.
```

Wait at least 24 hours (or until a :+1: reaction) before opening the PR,
**unless** the issue is triaged as "urgent" (`priority: critical` or `priority: high` label).

For urgent issues: post the analysis and open the PR in the same session,
but keep them as separate actions.

---

## 3. Pull Request Body Quality

Every PR must include all of the following sections, filled in:

```markdown
## Closes

Closes #N

## Problem / Root Cause

<What was wrong and why. Not "what was changed" — that belongs in What Changed.>

## What Changed

- <file or module>: <what was done>
- <file or module>: <what was done>

## Testing Evidence

- `pytest tests/<relevant_file>.py -q` → N passed
- Describe any manual validation steps

## Checklist

- [ ] Tests added or updated
- [ ] Lint: `ruff check navig tests` passes
- [ ] Format: `ruff format --check navig tests` passes
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `docs/user/HANDBOOK.md` updated (if CLI behavior changed)
- [ ] PR addresses exactly one issue (`Closes #N`)
- [ ] No hardcoded credentials or paths
- [ ] Windows compatibility verified (`os.name == "nt"` guards where needed)
```

---

## 4. Issue Comment Quality (AI Agents)

When posting a comment on an issue, the comment must:

1. **Use real newlines** — never store or emit literal `\n` or `\r\n` escape sequences
   as text. Use actual line breaks in the string.
2. **No C0 control characters** — never include characters in ranges U+0000–U+0008,
   U+000B–U+001F, or U+007F in comment bodies. These appear as `?` or invisible
   garbage on GitHub.
3. **Include context** — minimum structure for a resolution comment:

```markdown
Implemented on branch `feature/<slug>`.

**Root cause:** <one sentence>

**Fix:** <what was done>

**Tests added:** `tests/<file>.py` — N passed

Closes #N.
```

4. **Branch references** use backtick-quoted branch names: `` `feature/fix-vault-ux` ``
   (not plain text, not `\\`escaped).
5. **File references** use backtick-quoted paths: `` `navig/commands/init.py` ``.
6. **Command examples** use inline code or fenced code blocks.

### Encoding Guardrails (Hermes / Automated Pipelines)

The normalization pipeline at `.lab/hermes-agent/gateway/platforms/webhook.py`
(`_normalize_github_comment_content`) and
`.lab/hermes-agent/hermes_cli/webhook.py`
(`_normalize_prompt_template`) handle sanitization at the delivery layer.

When generating comment text in any agent/script/tool:
- Build the body as a Python string with real `\n` characters.
- Do **not** JSON-serialize and then paste the JSON string as the body.
- Do **not** use shell `echo -e` or `printf` with `\n` — use heredoc or Python directly.

---

## 5. Issue Lifecycle Management

### Labels (apply immediately; do not leave issues unlabelled)

| Situation | Required labels |
|---|---|
| Bug confirmed | `bug` + component label (`cli`, `windows`, `testing`, etc.) |
| Feature request confirmed | `enhancement` + component label |
| Implemented and verified | add `fixed` |
| Needs design decision | add `needs-design` |
| Duplicate | add `duplicate`, close with reference to canonical issue |
| Won't fix | add `wontfix`, close with clear reason |

### Opening a new issue

1. Fill the issue template completely — do not leave template sections blank.
2. Check for duplicates before submitting.
3. Add at least one label before submitting.
4. If filing on behalf of an automated analysis, add the `automated` label.

### Closing an issue

When closing an issue as resolved:
1. Post a quality closing comment (see §4 above).
2. Add the `fixed` label.
3. Close via the PR `Closes #N` mechanism (preferred) or manually after merge.
4. Never close an issue without a comment explaining what was done.

---

## 6. Closing Comment Checklist

A closing comment must answer all three questions:

1. **What was the root cause?** (technical explanation)
2. **What was done?** (specific files changed, approach taken)
3. **How was it verified?** (test file + N passed, or manual repro steps)

Minimum template:

```markdown
Resolved in PR #N (branch `feature/<slug>`, commit `<sha>`).

**Root cause:** <explanation>

**Fix:** <description of changes>

**Verification:** `pytest tests/<file>.py -q` → N passed

Closing this issue.
```

---

## 7. Anti-Patterns (Never Do These)

| Anti-pattern | Correct alternative |
|---|---|
| Batch PRs closing multiple issues | One PR per issue |
| `fix: resolve N open issues (#A, #B, #C...)` as PR title | `fix(scope): <description> (#N)` |
| Closing an issue silently (no comment) | Always post a closing comment |
| Literal `\n` in comment body | Real newlines in the string |
| Opaque branch names (`batch-1`, `misc-fixes`) | `feature/fix-<slug>-#N` |
| Not labelling issues | Add labels before/when closing |
| Posting "work in progress" PRs as `[DRAFT]` without an analysis comment on issue | Post analysis comment first |
| Opening a PR for an issue that does not exist yet | File the issue first |

---

## References

- Git workflow and branch naming: [git.instructions.md](git.instructions.md)
- Exception handling policy: [exception-policy.instructions.md](exception-policy.instructions.md)
- Copilot PR checklist: [../copilot-instructions.md](../copilot-instructions.md)
- Comment normalization implementation:
  - `.lab/hermes-agent/gateway/platforms/webhook.py` — `_normalize_github_comment_content()`
  - `.lab/hermes-agent/hermes_cli/webhook.py` — `_normalize_prompt_template()`
