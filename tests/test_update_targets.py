"""Tests for navig.update.targets — TargetResolver."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.update.targets import TargetResolver, UpdateTarget


def _make_cm(host_names=None, group_map=None):
    cm = MagicMock()
    cm.list_hosts.return_value = host_names or []
    cfg = {"host": "1.2.3.4", "port": 22, "username": "root"}

    def _load_host(name):
        return {**cfg, "name": name}

    cm.load_host_config.side_effect = _load_host

    def _get_group(name):
        gmap = group_map or {}
        if name not in gmap:
            raise ValueError(f"Group '{name}' not found.")
        return gmap[name]

    cm.get_group_hosts.side_effect = _get_group
    return cm


# ---------------------------------------------------------------------------
# Default (no args)
# ---------------------------------------------------------------------------

class TestDefaultResolution:
    def test_returns_local(self):
        r = TargetResolver(_make_cm()).resolve()
        assert len(r) == 1
        assert r[0].type == "local"
        assert r[0].node_id == "local"

    def test_local_host_arg_returns_local(self):
        r = TargetResolver(_make_cm()).resolve(host="local")
        assert r[0].is_local

    def test_localhost_returns_local(self):
        r = TargetResolver(_make_cm()).resolve(host="localhost")
        assert r[0].is_local


# ---------------------------------------------------------------------------
# --host
# ---------------------------------------------------------------------------

class TestHostResolution:
    def test_ssh_target(self):
        cm = _make_cm(host_names=["web-prod"])
        r = TargetResolver(cm).resolve(host="web-prod")
        assert len(r) == 1
        assert r[0].type == "ssh"
        assert r[0].node_id == "web-prod"
        assert r[0].server_config["host"] == "1.2.3.4"

    def test_missing_host_raises(self):
        cm = _make_cm()
        cm.load_host_config.side_effect = Exception("not found")
        with pytest.raises(ValueError, match="not found"):
            TargetResolver(cm).resolve(host="ghost-host")


# ---------------------------------------------------------------------------
# --group
# ---------------------------------------------------------------------------

class TestGroupResolution:
    def test_group_with_two_hosts(self):
        cm = _make_cm(host_names=["a", "b"], group_map={"staging": ["a", "b"]})
        r = TargetResolver(cm).resolve(group="staging")
        assert len(r) == 2
        node_ids = {t.node_id for t in r}
        assert node_ids == {"a", "b"}

    def test_group_with_local(self):
        cm = _make_cm(group_map={"all": ["local", "web-1"]})
        r = TargetResolver(cm).resolve(group="all")
        types = {t.type for t in r}
        assert "local" in types

    def test_missing_group_raises(self):
        cm = _make_cm(group_map={})
        with pytest.raises(ValueError, match="not found"):
            TargetResolver(cm).resolve(group="nope")


# ---------------------------------------------------------------------------
# --all
# ---------------------------------------------------------------------------

class TestAllResolution:
    def test_all_includes_local_first(self):
        cm = _make_cm(host_names=["h1", "h2"])
        r = TargetResolver(cm).resolve(all_hosts=True)
        assert r[0].type == "local"
        assert len(r) == 3  # local + h1 + h2

    def test_all_no_hosts_returns_local_only(self):
        cm = _make_cm(host_names=[])
        r = TargetResolver(cm).resolve(all_hosts=True)
        assert len(r) == 1
        assert r[0].is_local


# ---------------------------------------------------------------------------
# UpdateTarget
# ---------------------------------------------------------------------------

class TestUpdateTarget:
    def test_is_local(self):
        t = UpdateTarget(node_id="local", type="local")
        assert t.is_local
        assert t.label == "local"

    def test_is_ssh(self):
        t = UpdateTarget(node_id="myhost", type="ssh", server_config={"host": "1.1.1.1"})
        assert not t.is_local
