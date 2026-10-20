"""Only a SYSTEM account named `navig` means "system install" — in core and every mirror.

A person whose login happens to be `navig` (uid >= 1000, home under /home) got their
config moved to /etc/navig, and the packaged daemon crash-looped on PermissionError on a
real Debian VM. The legacy system deploy (core/deploy/navig.service) runs as a system
account and also says so outright with NAVIG_SYSTEM_SERVICE=1.

The rule lives in four places that must agree: core, navig-sdk's host, and the
navig-vault / navig-blackbox `_compat` fallbacks. Each is checked here against the same
accounts, through a POSIX view of its own module globals — patching the real `os.name`
breaks pathlib on Windows, so the probes never touch it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

from navig.platform import paths as core_paths

REPO = Path(__file__).resolve().parents[3]


class _PosixOs:
    """`os` as a POSIX module sees it, for one module's globals only."""

    name = "posix"

    def __init__(self, uid: int) -> None:
        self._uid = uid

    def getuid(self) -> int:
        return self._uid

    def __getattr__(self, attr: str):
        return getattr(os, attr)


class _LinuxSys:
    platform = "linux"

    def __getattr__(self, attr: str):
        return getattr(sys, attr)


def _load(rel: str):
    path = REPO / rel
    assert path.is_file(), f"{rel} moved — update this test, do not let it go blind"
    spec = importlib.util.spec_from_file_location(f"_sysacct_{path.parent.name}_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _probes(monkeypatch: pytest.MonkeyPatch, uid: int):
    """(name, zero-arg callable) for every implementation, each seeing a Linux host."""
    monkeypatch.setattr(core_paths, "os", _PosixOs(uid))
    monkeypatch.setattr(core_paths, "is_unix", lambda: True)
    out = [("core", core_paths._is_system_service)]

    sdk = _load("registry/sdk/python/navig_sdk/host.py")
    monkeypatch.setattr(sdk, "os", _PosixOs(uid))
    monkeypatch.setattr(sdk, "_current_os", lambda: "linux")
    out.append(("navig-sdk", sdk._is_system_service_fallback))

    vault = _load("plugins/navig-vault/navig_vault/_compat.py")
    monkeypatch.setattr(vault, "os", _PosixOs(uid))
    out.append(("navig-vault", vault._is_system_service_fallback))

    blackbox = _load("plugins/navig-blackbox/navig_blackbox/_compat.py")
    monkeypatch.setattr(blackbox, "os", _PosixOs(uid))
    monkeypatch.setattr(blackbox, "sys", _LinuxSys())
    out.append(("navig-blackbox", blackbox._is_system_service_fallback))
    return out


@pytest.mark.parametrize(
    ("name", "uid", "home", "system"),
    [
        ("navig", 998, "/var/lib/navig", True),   # useradd --system navig: the legacy deploy
        ("navig", 1500, "/opt/navig", True),      # a service account outside /home
        ("navig", 1000, "/home/navig", False),    # a PERSON named navig: their own ~/.navig
        ("ada", 1000, "/home/ada", False),
    ],
)
def test_only_a_system_account_named_navig_is_a_system_install(
    monkeypatch: pytest.MonkeyPatch, name: str, uid: int, home: str, system: bool,
) -> None:
    for var in ("NAVIG_SYSTEM_SERVICE", "INVOCATION_ID"):
        monkeypatch.delenv(var, raising=False)
    fake_pwd = types.ModuleType("pwd")
    fake_pwd.getpwuid = lambda _uid: types.SimpleNamespace(pw_name=name, pw_uid=uid, pw_dir=home)
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)

    results = {label: fn() for label, fn in _probes(monkeypatch, uid)}
    assert results == dict.fromkeys(results, system), results


def test_the_explicit_flag_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVIG_SYSTEM_SERVICE", "1")
    fake_pwd = types.ModuleType("pwd")
    fake_pwd.getpwuid = lambda _uid: types.SimpleNamespace(pw_name="navig", pw_uid=1000, pw_dir="/home/navig")
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
    assert all(fn() for _label, fn in _probes(monkeypatch, 1000))


def test_the_legacy_unit_says_so_explicitly() -> None:
    unit = (REPO / "core" / "deploy" / "navig.service").read_text(encoding="utf-8")
    assert "Environment=NAVIG_SYSTEM_SERVICE=1" in unit
