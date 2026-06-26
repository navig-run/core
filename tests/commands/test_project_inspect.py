"""Tests for project path inspection (stack + commands detection, no LLM)."""

from __future__ import annotations

import json

from navig.commands import project_inspect


def test_inspect_node_project(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "my-web-app",
        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
        "devDependencies": {"typescript": "5.4.0"},
        "scripts": {"dev": "next dev", "build": "next build"},
    }), encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# my-web-app\nA demo.\n", encoding="utf-8")

    info = project_inspect.inspect_path(str(tmp_path))
    assert info["exists"] and info["is_dir"]
    assert info["suggested_name"] == "my-web-app"
    assert "Node.js" in info["stack"] and "Next.js" in info["stack"] and "TypeScript" in info["stack"]
    assert info["metadata"]["package_manager"] == "pnpm"

    cmds = {c["name"]: c for c in info["commands"]}
    assert "build" in cmds and cmds["build"]["run"] == "pnpm run build"
    assert cmds["dev"]["source"] == "pnpm"
    assert "my-web-app" in info["readme_excerpt"]


def test_inspect_python_and_make(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.110\nuvicorn\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("build:\n\techo hi\ntest:\n\tpytest\n", encoding="utf-8")

    info = project_inspect.inspect_path(str(tmp_path))
    assert "Python" in info["stack"] and "FastAPI" in info["stack"]
    cmd_names = {c["name"] for c in info["commands"]}
    assert {"build", "test"} <= cmd_names
    make = next(c for c in info["commands"] if c["name"] == "build")
    assert make["run"] == "make build" and make["source"] == "make"


def test_inspect_missing_and_nondir(tmp_path):
    missing = project_inspect.inspect_path(str(tmp_path / "nope"))
    assert missing["exists"] is False and missing["is_dir"] is False
    assert missing["stack"] == [] and missing["commands"] == []

    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    nondir = project_inspect.inspect_path(str(f))
    assert nondir["is_dir"] is False

    assert project_inspect.inspect_path("")["exists"] is False


def test_inspect_other_ecosystems(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    info = project_inspect.inspect_path(str(tmp_path))
    assert "Rust" in info["stack"] and "Docker" in info["stack"]
