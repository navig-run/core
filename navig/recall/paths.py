"""Where Recall keeps its files. Everything resolves under ``navig.platform.paths.data_dir()``
so ``NAVIG_DATA_DIR`` isolates a test run completely."""

from __future__ import annotations

from pathlib import Path


def recall_root() -> Path:
    from navig.platform.paths import data_dir  # noqa: PLC0415

    return Path(data_dir()) / "recall"


def archive_dir() -> Path:
    return recall_root() / "archive"


def vault_dir() -> Path:
    return recall_root() / "vault"


def events_dir() -> Path:
    return vault_dir() / ".events"


def index_db() -> Path:
    return recall_root() / "index.db"
