# Project profile — /inbox distillery bindings

<!--
  THE ONE FILE THAT DIFFERS PER PROJECT. The /inbox engine reads this to know where things
  live and how to map essence onto this project. The rest of the skill is identical everywhere.
  When Status is UNCONFIGURED, the engine auto-detects the blanks, confirms them with the user,
  then rewrites this file with Status: CONFIGURED.
-->

- **Project:** <name — the human name of this project/space>
- **Status:** UNCONFIGURED

## Locations
- **Inbox (drop zone):** `./.inbox`   <!-- in a NAVIG space this is linked to `.navig/inbox` -->
- **Originals backup (texts):** `./.inbox/_originals`   <!-- verbatim text backups; frozen safety net -->
- **Coverage ledger:** `./.inbox/ledger.jsonl`   <!-- incremental run state — see SKILL.md §Ledger -->
- **Distilled library:** `.navig/refs/notes/`   <!-- where distilled notes + INDEX.md live (private, gitignored .lab/) -->
- **Media / large-asset roots:** <where big binaries already live so notes just LINK them; e.g. an assets/ dir, a `.media` mount, a drive path>
<!-- Sources are distilled IN PLACE — never moved. No processed/ or intake/ folders. -->


## Out of scope (auto-skip)
<!-- Paths the distillery must never mine (session archives, logs, DB dumps…). The engine
     ledger-skips these with the reason on sight. Fill in as the project reveals them. -->

## Mapping
- **Fit field:** `fit`   <!-- relevance frontmatter key; keep as `fit` unless this project already uses another -->
- **Design system:** <path to this project's design tokens / style guide the `style` rubric maps to — or its component library / README if none>
- **Where-it-applies vocabulary:** <the routes / components / surfaces / plan files a note's "Where it applies" should link — the real names in THIS project>
- **Backlog / ideas sink:** <where actionable product ideas should be pointed, e.g. a ROADMAP or ideas file>

## Notes
<!-- Anything project-specific worth remembering for future runs: recurring motifs, the house
     palette, sound rules, what "on-brand" means here. Fill in as you learn the project. -->
