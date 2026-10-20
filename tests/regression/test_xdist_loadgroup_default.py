"""``pytest -n …`` honours ``xdist_group`` markers without ``--dist loadgroup``.

``-n`` alone means ``--dist load``, which ignores ``xdist_group``: the e2e gateways in
``tests/e2e/test_gateway_api.py`` booted on several workers at once and one failed with
a 401 from a neighbour's gateway. The root ``conftest.py`` promotes the implicit
``load`` to ``loadgroup``; an explicit ``--dist`` still wins.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT_CONFTEST = Path(__file__).resolve().parents[2] / "conftest.py"


@pytest.fixture(scope="module")
def root_conftest():
    spec = importlib.util.spec_from_file_location("_navig_root_conftest", _ROOT_CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _config(dist, args):
    return SimpleNamespace(
        option=SimpleNamespace(dist=dist),
        invocation_params=SimpleNamespace(args=tuple(args)),
    )


@pytest.mark.parametrize(
    ("dist", "args", "expected"),
    [
        ("load", ["-n", "auto"], "loadgroup"),  # the bug: implicit load
        ("load", ["-n", "4", "--dist", "load"], "load"),  # explicit wins
        ("load", ["-n", "4", "--dist=load"], "load"),
        ("load", ["-d", "--tx", "popen"], "load"),  # -d is --distload, explicit
        ("loadscope", ["-n", "4", "--dist", "loadscope"], "loadscope"),
        ("no", [], "no"),  # not distributing
    ],
)
def test_dist_mode(root_conftest, dist, args, expected):
    cfg = _config(dist, args)
    root_conftest.pytest_configure(cfg)
    assert cfg.option.dist == expected


def test_without_xdist_the_hook_is_inert(root_conftest):
    cfg = SimpleNamespace(option=SimpleNamespace(), invocation_params=SimpleNamespace(args=()))
    root_conftest.pytest_configure(cfg)
    assert not hasattr(cfg.option, "dist")


def test_worker_turns_grouping_on_when_the_controller_says_so(root_conftest):
    # A worker's own command line has no --dist, so xdist set loadgroup=False on it.
    cfg = SimpleNamespace(
        option=SimpleNamespace(dist="no", loadgroup=False),
        invocation_params=SimpleNamespace(args=("-n", "4")),
        workerinput={"navig_loadgroup": True},
    )
    root_conftest.pytest_configure(cfg)
    assert cfg.option.loadgroup is True
    assert cfg.option.dist == "no"  # a worker never distributes


def test_worker_left_alone_without_the_flag(root_conftest):
    cfg = SimpleNamespace(
        option=SimpleNamespace(dist="no", loadgroup=False),
        invocation_params=SimpleNamespace(args=("-n", "4", "--dist", "load")),
        workerinput={},
    )
    root_conftest.pytest_configure(cfg)
    assert cfg.option.loadgroup is False


@pytest.mark.parametrize(("dist", "flagged"), [("loadgroup", True), ("load", False)])
def test_controller_hands_the_decision_to_workers(root_conftest, dist, flagged):
    node = SimpleNamespace(config=SimpleNamespace(option=SimpleNamespace(dist=dist)), workerinput={})
    root_conftest.pytest_configure_node(node)
    assert node.workerinput.get("navig_loadgroup", False) is flagged
