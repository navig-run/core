"""Fixtures for the projects package: realistic project shapes in a temp dir.

The shapes mirror what the operator's real projects hold (measured over
E:\\projects\\apps): canonical plans in `.navig/plans/`, a duplicate ROADMAP at
the root, numbered plan files, lowercase names, an inbox, and — the one that
matters for safety — a `.navig/vault/` that must never be reachable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DEV_PLAN = """# Dev plan

## Active

- [ ] Wire the checkout
- [x] Ship the landing
- [~] Refactor payments
  - [ ] nested subtask

```md
- [ ] not a todo (inside a fence)
```

## Later

* [ ] Mobile app *(in progress)*
"""


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every store and the registry; the cwd gets its own `.navig/` marker so
    no walk-up can escape the sandbox into the real repo."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("NAVIG_STORE_DIR", raising=False)
    (tmp_path / "cfg").mkdir()
    cwd = tmp_path / "cwd"
    (cwd / ".navig").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    import navig.store.board as board
    from navig.projects import model, store

    # The board store is a process singleton: without a reset it may still point at a
    # database an earlier test (or the live install) opened.
    monkeypatch.setattr(board, "_store", None)
    store.reset_project_store()
    model.clear_cache()
    yield tmp_path
    store.reset_project_store()
    model.clear_cache()


@pytest.fixture()
def project(isolated: Path) -> Path:
    root = isolated / "apps" / "shop"
    plans = root / ".navig" / "plans"
    (plans / "briefs").mkdir(parents=True)
    (plans / "tasks").mkdir()
    (root / ".navig" / "inbox").mkdir()
    (root / ".navig" / "vault").mkdir()
    (root / "apps" / "web").mkdir(parents=True)
    (root / "src").mkdir()

    (plans / "VISION.md").write_bytes(b"# Shop vision\n\nSell things fast.\n")
    (plans / "ROADMAP.md").write_bytes(b"# Roadmap\n\n- [x] M1\n- [ ] M2\n")
    (root / "ROADMAP.md").write_bytes(b"# Old roadmap at the root\n")
    (plans / "CURRENT_PHASE.md").write_bytes(
        b"---\nphase: 3\ntitle: Payments\nstatus: active\n---\n# Phase 3\n\n- [ ] Stripe\n")
    (plans / "DEV_PLAN.md").write_bytes(DEV_PLAN.encode("utf-8"))
    (plans / "01-foundation.md").write_bytes(b"# Foundation\n")
    (plans / "briefs" / "brief-a.md").write_bytes(b"# Brief A\n")
    (plans / "tasks" / "T-001-login.md").write_bytes(b"# T-001\n\n- [ ] login form\n")
    (root / ".navig" / "vision.md").write_bytes(b"# legacy vision\n")
    (root / ".navig" / "ai_system_prompt.txt").write_bytes(b"You are working on SHOP.\n")
    (root / ".navig" / "inbox" / "note.md").write_bytes(b"# inbox note\n")
    (root / ".navig" / "vault" / "secret.md").write_bytes(b"TOKEN=hunter2\n")
    (root / "CLAUDE.md").write_bytes(b"# Shop agent rules\n\nNever log card data.\n")
    (root / "README.md").write_bytes(b"# Shop\n\nA flash-sale shop.\n")
    (root / "secret.md").write_bytes(b"not a doc\n")
    (root / "package.json").write_text(json.dumps({
        "name": "shop", "workspaces": ["apps/*"],
        "scripts": {"dev": "vite", "build": "vite build", "deploy:prod": "wrangler deploy",
                    "deploy:status:prod": "wrangler deployments list", "clean": "rimraf dist"},
        "devDependencies": {"vite": "5", "typescript": "5"},
    }), encoding="utf-8")
    (root / "pnpm-lock.yaml").write_bytes(b"")
    (root / "apps" / "web" / "package.json").write_text(json.dumps({
        "name": "@shop/web", "scripts": {"dev": "next dev", "lint": "eslint ."},
    }), encoding="utf-8")
    (root / "src" / "main.py").write_bytes(b"class App:\n    pass\n\ndef run():\n    pass\n\ndef _hidden():\n    pass\n")
    (root / "src" / "index.ts").write_bytes(b"export function start() {}\nexport const VERSION = 1\n")
    return root
