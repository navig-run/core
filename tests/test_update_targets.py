"""Hermetic unit tests for navig.update.targets — UpdateTarget dataclass."""

from __future__ import annotations

import pytest

from navig.update.targets import UpdateTarget

# ---------------------------------------------------------------------------
# UpdateTarget properties
# ---------------------------------------------------------------------------


class TestUpdateTarget:
    def test_is_local_true_for_local_type(self):
        t = UpdateTarget(node_id="local", type="local")
        assert t.is_local is True

    def test_is_local_false_for_ssh_type(self):
        t = UpdateTarget(node_id="server1", type="ssh")
        assert t.is_local is False

    def test_label_equals_node_id(self):
        t = UpdateTarget(node_id="prod-01")
        assert t.label == "prod-01"

    def test_default_type_is_local(self):
        t = UpdateTarget(node_id="mymachine")
        assert t.type == "local"

    def test_server_config_default_none(self):
        t = UpdateTarget(node_id="local")
        assert t.server_config is None

    def test_ssh_target_with_config(self):
        cfg = {"host": "10.0.0.1", "user": "admin"}
        t = UpdateTarget(node_id="prod", type="ssh", server_config=cfg)
        assert t.server_config == cfg
        assert not t.is_local
