"""`NAVIG_DATA_DIR` must actually isolate a store.

The repo's own guidance is "isolate databases with `NAVIG_DATA_DIR` (a temp dir), never
`NAVIG_HOME`". That was true for five of the six `BaseStore` subclasses — they resolve
through `paths.data_dir()`, which honours it — and FALSE for the sixth. `board.db`
resolves through `paths.store_dir()`, which was built from `config_dir()` and ignored
the variable entirely.

So a test or a script that isolated itself exactly as documented wrote into the
operator's LIVE board. That happened on this machine: four rows landed in the real task
list, and the only reason it was caught is that the store printed its own path.

The failure is silent by construction — an unisolated store still works perfectly, it
just works on the wrong database — which is why it is a test rather than a comment.
"""

from __future__ import annotations

from pathlib import Path

from navig.platform import paths


def test_navig_data_dir_isolates_the_store(tmp_path: Path, monkeypatch) -> None:
    """The headline. Without this, "isolated" means "the operator's real data"."""
    monkeypatch.delenv("NAVIG_STORE_DIR", raising=False)
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))

    resolved = paths.store_dir()
    assert resolved == tmp_path / "store"
    assert str(tmp_path) in str(resolved)


def test_the_board_store_lands_inside_the_isolated_directory(tmp_path: Path, monkeypatch) -> None:
    """The end the guidance actually cares about: the DATABASE, not the resolver.

    Asserting on `store_dir()` alone would keep passing if `board.py` stopped using it.
    """
    monkeypatch.delenv("NAVIG_STORE_DIR", raising=False)
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))

    from navig.store.board import BoardStore, _default_db_path

    assert _default_db_path() == tmp_path / "store" / "board.db"

    store = BoardStore()
    store.create_todo("A task written during a test")
    assert (tmp_path / "store" / "board.db").exists()
    assert len(store.list_todos()) == 1


def test_every_store_honours_the_documented_variable(tmp_path: Path, monkeypatch) -> None:
    """All six, not just the one that was broken.

    The trap survived because it was true of the majority — five stores isolate
    correctly, so the guidance read as verified. This asserts the whole set, so a new
    store that picks the wrong resolver fails here rather than in someone's live data.
    """
    monkeypatch.delenv("NAVIG_STORE_DIR", raising=False)
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))

    from navig.store.board import _default_db_path as board_path
    from navig.store.contacts import _default_db_path as contacts_path
    from navig.store.generated_media import _default_db_path as media_path
    from navig.store.scheduled_posts import _default_db_path as posts_path
    from navig.store.telegram_catalog import _default_db_path as catalog_path
    from navig.store.threads import _default_db_path as threads_path

    resolvers = {
        "board": board_path,
        "contacts": contacts_path,
        "generated_media": media_path,
        "scheduled_posts": posts_path,
        "telegram_catalog": catalog_path,
        "threads": threads_path,
    }
    assert len(resolvers) >= 6, "the store inventory shrank — check this list is still complete"

    escaped = {
        name: str(resolve())
        for name, resolve in resolvers.items()
        if not str(resolve()).startswith(str(tmp_path))
    }
    assert not escaped, (
        "these stores ignore NAVIG_DATA_DIR, so a test that isolates itself as the "
        f"repo documents writes into the operator's real data: {escaped}"
    )


def test_navig_store_dir_still_wins(tmp_path: Path, monkeypatch) -> None:
    """The more specific override stays authoritative."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAVIG_STORE_DIR", str(tmp_path / "explicit"))
    assert paths.store_dir() == tmp_path / "explicit"


def test_the_default_path_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    """With no override, the resolved path must be exactly what it always was.

    `data_dir()` defaults to `config_dir() / "data"`, so `data_dir() / "store"` and the
    legacy branch compute the same thing — that identity is what makes this fix
    migration-free, and it is worth an assertion rather than an argument.
    """
    monkeypatch.delenv("NAVIG_STORE_DIR", raising=False)
    monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    (tmp_path / "data" / "store").mkdir(parents=True)

    assert paths.store_dir() == tmp_path / "data" / "store"


def test_an_isolated_directory_never_falls_back_to_the_legacy_one(
    tmp_path: Path, monkeypatch
) -> None:
    """An explicit override must not resolve to a real directory.

    The no-override branch falls back to `config_dir()/store` when the new path does
    not exist yet — which is correct there and would be catastrophic here: a fresh temp
    dir never exists, so the fallback would send every isolated test straight back to
    the operator's install.
    """
    config = tmp_path / "config"
    (config / "store").mkdir(parents=True)
    monkeypatch.delenv("NAVIG_STORE_DIR", raising=False)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "isolated"))

    resolved = paths.store_dir()
    assert resolved == tmp_path / "isolated" / "store"
    assert "config" not in resolved.parts
