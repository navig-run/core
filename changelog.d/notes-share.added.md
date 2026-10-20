- **Share notes with a Space** (`navig notes share push|pull|status`). A Space folder
  carries notes as `.navig/notes/<id>.md` — frontmatter keeps the id, revision, scope,
  geometry and tags, the body travels byte for-byte, and "the newer revision wins" is the
  same rule the Dock's sync uses. Teammates get the notes with whatever already shares the
  Space; files left behind by a deleted note are reported, never deleted. Reminders stay
  personal. From `plugins/navig-notes` (`pip install "navig[notes]"`).
