"""``scripts/check_plugin_version_drift.py`` — changed source under an already-published version.

Regression target: navig 3.27.0 depended on ``navig-vault>=0.8.0`` while the repo's
navig-vault carried ``add_sso_login`` under that same 0.8.0. PyPI held an older 0.8.0,
the publisher skipped it as "already published", and every fresh install's gateway
died on the ImportError. These tests drive the guard's comparison on hand-built
wheels — no network.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_plugin_version_drift.py"


@pytest.fixture(scope="module")
def guard():
    if not _SCRIPT.is_file():  # pragma: no cover
        pytest.skip(f"{_SCRIPT} not present")
    spec = importlib.util.spec_from_file_location("_drift_guard", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_drift_guard"] = module
    spec.loader.exec_module(module)
    return module


def _wheel(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr("pkg_x-0.8.0.dist-info/METADATA", b"Name: pkg-x\n")
    return buf.getvalue()


def _plugin(tmp_path: Path, files: dict[str, str]) -> Path:
    base = tmp_path / "navig-x"
    for rel, text in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    return base


def test_identical_source_is_not_drift(guard, tmp_path):
    base = _plugin(tmp_path, {"pkg_x/__init__.py": "A = 1\n", "pkg_x/logins.py": "def add(): ...\n"})
    wheel = _wheel({"pkg_x/__init__.py": b"A = 1\n", "pkg_x/logins.py": b"def add(): ...\n"})
    assert guard.drift(base, wheel) == []


def test_crlf_checkout_is_not_drift(guard, tmp_path):
    base = _plugin(tmp_path, {"pkg_x/__init__.py": "A = 1\r\n"})
    assert guard.drift(base, _wheel({"pkg_x/__init__.py": b"A = 1\n"})) == []


def test_a_changed_module_under_the_same_version_is_drift(guard, tmp_path):
    # The exact 3.27.0 shape: logins.py gained add_sso_login, version unchanged.
    base = _plugin(tmp_path, {
        "pkg_x/__init__.py": "A = 1\n",
        "pkg_x/logins.py": "def add(): ...\ndef add_sso_login(): ...\n",
    })
    wheel = _wheel({"pkg_x/__init__.py": b"A = 1\n", "pkg_x/logins.py": b"def add(): ...\n"})
    problems = guard.drift(base, wheel)
    assert problems == ["pkg_x/logins.py: changed since it was published"]


def test_a_new_module_is_drift(guard, tmp_path):
    base = _plugin(tmp_path, {"pkg_x/__init__.py": "A = 1\n", "pkg_x/sso.py": "X = 2\n"})
    problems = guard.drift(base, _wheel({"pkg_x/__init__.py": b"A = 1\n"}))
    assert problems == ["pkg_x/sso.py: new module the published wheel does not contain"]


def test_src_layout_is_resolved(guard, tmp_path):
    base = _plugin(tmp_path, {"src/pkg_x/__init__.py": "A = 1\n"})
    assert guard.drift(base, _wheel({"pkg_x/__init__.py": b"A = 1\n"})) == []


def test_offline_is_a_skip_not_a_verdict(guard, tmp_path, monkeypatch):
    base = _plugin(tmp_path, {"pkg_x/__init__.py": "A = 1\n"})
    (base / "pyproject.toml").write_text('[project]\nname = "navig-x"\nversion = "0.1.0"\n')

    def _down(url):
        raise OSError("network unreachable")

    monkeypatch.setattr(guard, "_get", _down)
    status, problems = guard.check(base)
    assert status.startswith("skip:") and problems == []
