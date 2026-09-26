- **The pre-push gate refuses a hand-written `[Unreleased]` entry.** Fragments were made the
  rule and documented in four places; 74 merges later, 8 PRs had still inserted straight into
  `core/CHANGELOG.md [Unreleased]` — the one line every branch collides on — and the 4 fragments
  were mostly the feature author's own. `scripts/check-changelog-fragments.mjs` judges what the
  branch ADDS inside the block (merge-base → HEAD at push time, → the working tree before a
  commit): a hand-written entry fails naming the line and the fragment to write; an assembly
  (fragments deleted in the same diff), a release rotation (only template comments land in the
  block), and edits below `[Unreleased]` pass. Teeth: all 8 real direct edits fail, all 4
  tool-made commits pass. Also: every `scripts/test/*.test.mjs` must now be a gate step — each
  had been wired by hand, and nothing checked that the next one would be.
