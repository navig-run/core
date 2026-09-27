- **A space scaffolded before the task list existed now learns about it.** `NAVIG.md`
  tells agents working in a space that the operator keeps one personal task list and
  how to reach it (`task_add` / `task_list` / `task_done`, not the per-conversation
  scratch checklist) — but only spaces created after the PIM shipped carried that
  section, and older spaces are the ones with the most written down in them. The
  section is now marker-fenced and appended to an existing `NAVIG.md` by
  `navig space doctor --fix`, at the end, with every existing byte untouched above it;
  the doctor reports its absence under **AI assistants**, and the pre-marker wording
  counts as present so a space is never told to add what it already has.
