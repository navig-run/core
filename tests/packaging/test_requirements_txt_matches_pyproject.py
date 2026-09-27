"""`requirements.txt` must list exactly `[project.dependencies]`.

It was a hand-kept list that had drifted to MISSING ten hard dependencies (pydantic,
loguru, platformdirs, navig-vault, …) while carrying four that are no longer core
(comtypes, uiautomation, pillow, python-telegram-bot). The docs and a Dockerfile
install from it, so `pip install -r requirements.txt` produced a navig that could not
start.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

CORE = Path(__file__).resolve().parents[2]


def test_requirements_txt_is_exactly_the_runtime_dependencies() -> None:
    project = tomllib.loads((CORE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wanted = [d.strip() for d in project["dependencies"]]
    listed = [
        line.strip()
        for line in (CORE / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert listed == wanted, (
        "requirements.txt drifted from pyproject.toml [project.dependencies] — "
        "regenerate it (the header says how)"
    )
