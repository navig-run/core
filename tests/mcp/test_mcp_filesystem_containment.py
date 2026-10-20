"""S3 — desktop_filesystem containment.

Containment is resolved-path based and fails closed. The tests exercise the policy
directly (pure filesystem, no MCP transport) plus the handler wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from navig.mcp.files_policy import FilesPolicy, denied_reason, policy_from_config
from navig.mcp.tools.filesystem import _tool_filesystem
from navig.mcp_server import MCPProtocolHandler


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


def _handler(policy: FilesPolicy) -> MCPProtocolHandler:
    handler = MCPProtocolHandler(profile="full")
    handler._files_policy = policy
    return handler


def _call(policy: FilesPolicy, **args):
    return _tool_filesystem(_handler(policy), args)


def test_read_inside_root_is_allowed(root: Path) -> None:
    target = root / "hello.txt"
    target.write_text("hello", encoding="utf-8")
    result = _call(FilesPolicy(roots=(root,), enforce=True), mode="read", path=str(target))
    assert "hello" in str(result)


def test_read_outside_root_is_denied(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    result = _call(FilesPolicy(roots=(root,), enforce=True), mode="read", path=str(outside))
    assert result["isError"] is True
    assert result["error"] == "path_denied"
    assert "outside the allowed roots" in result["message"]


def test_traversal_cannot_escape_a_root(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    result = _call(
        FilesPolicy(roots=(root,), enforce=True),
        mode="read",
        path=str(root / ".." / "outside.txt"),
    )
    assert result["isError"] is True


def test_write_outside_root_is_denied(root: Path, tmp_path: Path) -> None:
    result = _call(
        FilesPolicy(roots=(root,), enforce=True),
        mode="write",
        path=str(tmp_path / "new.txt"),
        content="x",
    )
    assert result["isError"] is True


def test_copy_destination_is_checked(root: Path, tmp_path: Path) -> None:
    source = root / "a.txt"
    source.write_text("x", encoding="utf-8")
    result = _call(
        FilesPolicy(roots=(root,), enforce=True),
        mode="copy",
        path=str(source),
        destination=str(tmp_path / "b.txt"),
    )
    assert result["isError"] is True
    assert "destination" in result["message"]


def test_symlink_escape_is_denied(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this platform/user cannot create a symlink")
    assert FilesPolicy(roots=(root,), enforce=True).check(str(link)) is not None


def test_sensitive_names_are_denied_even_inside_a_root(root: Path) -> None:
    policy = FilesPolicy(roots=(root,), enforce=True)
    for name in (
        ".env",
        "credentials.json",
        "app.db",
        "app.sqlite3",
        "server.log",
        "secrets.yaml",
        "id_rsa",
    ):
        assert policy.check(str(root / name)) is not None, name


def test_navig_internal_directory_is_denied() -> None:
    from navig.platform.paths import config_dir

    policy = FilesPolicy(roots=(config_dir(),), enforce=True)
    assert policy.check(str(config_dir() / "config.yaml")) is not None


def test_no_roots_denies_everything_with_an_actionable_message(root: Path) -> None:
    policy = FilesPolicy(roots=(), enforce=True)
    message = policy.check(str(root / "x.txt"))
    assert message and "mcp.files.roots" in message


def test_policy_not_enforced_preserves_local_behaviour(root: Path) -> None:
    assert FilesPolicy(enforce=False).check(str(root / "x.txt")) is None


def test_relative_path_is_refused() -> None:
    policy = FilesPolicy(roots=(Path.cwd(),), enforce=True)
    assert "absolute" in (policy.check("relative/file.txt") or "")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive syntax")
def test_windows_drive_relative_path_is_refused() -> None:
    policy = FilesPolicy(roots=(Path.cwd(),), enforce=True)
    assert "absolute" in (policy.check("C:relative.txt") or "")


def test_policy_from_config_matrix() -> None:
    class Cfg:
        def __init__(self, values):
            self.values = values

        def get(self, key, default=None):
            return self.values.get(key, default)

    remote = SimpleNamespace(mode="http", host="0.0.0.0", exposure="remote")
    local = SimpleNamespace(mode="stdio", host="", exposure="local")

    never_remote = policy_from_config(Cfg({"mcp.files.enforce": "never"}), remote)
    assert never_remote.enforce is True, "remote exposure must ignore 'never'"

    local_auto = policy_from_config(Cfg({}), local)
    assert local_auto.enforce is False

    local_roots = policy_from_config(Cfg({"mcp.files.roots": [r"C:\srv\work"]}), local)
    assert local_roots.enforce is True and local_roots.roots

    always_local = policy_from_config(Cfg({"mcp.files.enforce": "always"}), local)
    assert always_local.enforce is True


def test_denied_reason_is_none_for_a_plain_file(root: Path) -> None:
    plain = root / "notes.md"
    plain.write_text("ok", encoding="utf-8")
    assert denied_reason(plain) is None


def test_reparse_style_prefix_is_canonicalised(root: Path) -> None:
    # A canonicalisation that did not resolve the path would let `\\?\...` bypass the
    # root comparison on Windows; the resolver reduces it to the same target.
    target = root / "plain.txt"
    target.write_text("x", encoding="utf-8")
    prefixed = "\\\\?\\" + str(target)
    policy = FilesPolicy(roots=(root,), enforce=True)
    if sys.platform == "win32":
        assert policy.check(prefixed) is None
