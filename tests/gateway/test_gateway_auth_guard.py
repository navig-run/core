"""Tests for navig.gateway.auth_guard — AuthGuard."""
from __future__ import annotations

import pytest

from navig.gateway.auth_guard import AuthGuard


class TestAuthGuard:
    def test_empty_allowed_users_open_mode(self):
        guard = AuthGuard()
        assert guard.is_authorized(user_id=999, chat_id=100) is True

    def test_allowed_user_authorized(self):
        guard = AuthGuard(allowed_users={123, 456})
        assert guard.is_authorized(user_id=123, chat_id=100) is True

    def test_disallowed_user_denied(self):
        guard = AuthGuard(allowed_users={123})
        assert guard.is_authorized(user_id=999, chat_id=100) is False

    def test_allowed_group_authorized_in_group(self):
        guard = AuthGuard(allowed_users={123}, allowed_groups={-100})
        assert guard.is_authorized(user_id=999, chat_id=-100, is_group=True) is True

    def test_group_chat_not_in_allowed_groups_denied(self):
        guard = AuthGuard(allowed_users={123}, allowed_groups={-100})
        assert guard.is_authorized(user_id=999, chat_id=-200, is_group=True) is False

    def test_group_flag_false_group_not_checked(self):
        guard = AuthGuard(allowed_users={123}, allowed_groups={-100})
        # is_group=False → group check not applied
        assert guard.is_authorized(user_id=999, chat_id=-100, is_group=False) is False

    def test_empty_allowed_users_always_open(self):
        guard = AuthGuard(allowed_users=None, allowed_groups=None)
        assert guard.is_authorized(user_id=1, chat_id=2) is True

    def test_defaults_set(self):
        guard = AuthGuard(allowed_users={1})
        assert guard.allowed_users == {1}
        assert guard.allowed_groups == set()
