- **`navig doctor` → Release: the tree, the newest tag and PyPI must agree on what is
  shipped.** Three places each answer "what version is navig?" and nothing compared them —
  measured: `pyproject.toml` 3.25.0, PyPI 3.25.0, newest tag v3.24.0, i.e. 3.25.0 had been
  published by hand with no tag (the org's Actions is billing-blocked, so the tag workflow never
  fires), invisible for two weeks. Development checkouts only. `tree` compares `pyproject.toml`
  with `latest.json`; `tag` the newest `v*` tag (`v:refname` order, so 3.10 > 3.9); `PyPI` the
  published version, plus a count of `[Unreleased]` entries and `changelog.d/` fragments awaiting
  a release. A tree ahead is a ⚠ naming the command; PyPI ahead of the tree, or a tag over a
  tree of another version, is a ✗; PyPI unreachable is a ⚠ "not checked", never a tick — the
  judgement is a pure function of the four facts, pinned on every shape.
