"""`navig init`'s status summary read the wrong vault — and CREATED one to do it.

Found by finishing the "who else touches this surface?" audit (a6d05a987, 8a5b191c2)
against the vault. `show_init_status()` built the path by hand:

    CredentialsVault(vault_path=navig_dir / "credentials" / "vault.db")

which is wrong twice over. `credentials/` is the LEGACY location — `navig/vault/migrate.py`
exists to migrate AWAY from it — and `navig_dir` prefers the PROJECT `.navig/` whenever the
cwd has one, while the vault is global (`paths.vault_dir()` = `<config_dir>/vault`). So the
status read a path nothing writes and reported "empty" for an operator with a full vault.
The comment above it records a PREVIOUS fix to the same block ("this import failed and the
vault-status check always fell through to 'empty'") — the import was corrected and the path
was left, so the readout stayed wrong for a new reason.

Worse than a wrong answer: a Vault opens its SQLite database on first connect, and opening
CREATES it. So a read-only status command wrote `vault.db`, `vault.db-wal` and `vault.db-shm`
into whatever directory it had guessed — three stray files inside the user's project, on
every `navig init`. Reproduced against the pre-fix code.

Three call sites in that one command reached for a vault (the status probe, the web-key
lookup, and `providers/source_scan.provider_has_vault_key`), so the fix is a primitive
rather than three copies of a path check: `navig.vault.vault_exists()` — a pure LOOK that
never constructs. Probe with it before `get_vault()` in anything read-only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def project_and_config(tmp_path, monkeypatch):
    """A project dir with `.navig/` (so `navig_dir` prefers it) + an isolated config dir."""
    proj = tmp_path / "myproject"
    (proj / ".navig").mkdir(parents=True)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.chdir(proj)
    return proj, cfg


def _vault_files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("vault.db*"))


def test_the_status_summary_creates_no_vault_anywhere(project_and_config):
    """The regression: three stray files in the operator's repo from a status command."""
    proj, cfg = project_and_config

    from navig.commands.init import show_init_status

    show_init_status(render=False)

    assert _vault_files_under(proj) == [], (
        "a read-only status command wrote a vault database into the user's PROJECT — "
        f"found {_vault_files_under(proj)}"
    )
    assert not (cfg / "vault").exists(), (
        "no vault existed, yet the status probe created one; opening a Vault creates it, "
        "so read-only callers must look before they open"
    )


def test_the_status_summary_reads_the_canonical_vault(project_and_config, monkeypatch):
    """It must consult `paths.vault_dir()`, not a hand-built legacy path.

    Driven by presence: with a canonical vault holding an item the status is
    "initialized"; the pre-fix code looked in `<project>/.navig/credentials/` and could
    only ever say "empty".
    """
    _proj, cfg = project_and_config

    from navig.commands.init import show_init_status
    from navig.vault.core import get_vault

    vault = get_vault()  # creates it AT THE CANONICAL PATH — that is the point here
    canonical = (cfg / "vault").resolve()
    assert (canonical / "vault.db").is_file(), "the canonical vault was not created"

    # Report items ONLY for the canonical vault. Patching `list` unconditionally would
    # make ANY vault look populated — including the legacy project path the pre-fix code
    # opened — so the test would pass in both directions and prove nothing.
    monkeypatch.setattr(
        type(vault),
        "list",
        lambda self, *a, **k: [object()] if Path(self.vault_dir).resolve() == canonical else [],
    )

    assert show_init_status(render=False).get("vault") == "initialized", (
        "a populated canonical vault was reported as empty — the probe is reading a "
        "path the vault never writes"
    )


def test_an_empty_canonical_vault_still_reports_empty(project_and_config):
    """Anti-vacuity: 'initialized' must mean credentials exist, not merely a file."""
    _proj, cfg = project_and_config

    from navig.commands.init import show_init_status
    from navig.vault.core import get_vault

    get_vault()
    assert (cfg / "vault" / "vault.db").is_file()

    assert show_init_status(render=False).get("vault") == "empty", (
        "a vault with no credentials was reported as initialized"
    )


# ── the primitive ────────────────────────────────────────────────────────


def test_vault_exists_never_creates_the_store(tmp_path):
    """The whole reason it exists: `get_vault()` creates, `vault_exists()` looks."""
    from navig.vault import vault_exists

    probe_dir = tmp_path / "nothing-here"
    assert vault_exists(probe_dir) is False
    assert not probe_dir.exists(), "the existence probe created the directory it asked about"

    probe_dir.mkdir()
    (probe_dir / "vault.db").write_text("", encoding="utf-8")
    assert vault_exists(probe_dir) is True


def test_vault_exists_defaults_to_the_canonical_location(tmp_path, monkeypatch):
    """No argument means `paths.vault_dir()` — the one place a vault legitimately lives."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))

    from navig.platform.paths import vault_dir
    from navig.vault import vault_exists

    assert vault_exists() is False

    target = vault_dir()
    target.mkdir(parents=True)
    (target / "vault.db").write_text("", encoding="utf-8")
    assert vault_exists() is True


def test_the_provider_scan_probe_does_not_create_a_vault(tmp_path, monkeypatch):
    """The third caller — a pure "does this provider have a key?" question."""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))

    from navig.providers.source_scan import provider_has_vault_key

    assert provider_has_vault_key("openai") is False
    assert not (cfg / "vault").exists(), (
        "asking whether a provider key exists created an empty encrypted store"
    )


def test_no_status_path_builds_the_legacy_vault_directory_by_hand() -> None:
    """The specific wrong path must not come back.

    `credentials/` is what `navig/vault/migrate.py` migrates AWAY from; a live code path
    that reads it is reading history.
    """
    import ast

    import navig.commands.init as init_mod

    source = Path(init_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # AST, not text: the explanation of this bug necessarily NAMES the bad segment, and a
    # substring scan flags the very comment warning people off it. Look for the path
    # construction itself — `<something> / "credentials"`.
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and isinstance(node.right, ast.Constant)
        and node.right.value == "credentials"
    ]
    assert not offenders, (
        f"navig/commands/init.py builds a legacy `credentials/` path at line(s) {offenders} "
        "— ask `navig.platform.paths.vault_dir()` where the vault is"
    )
