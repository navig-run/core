"""Tests for remote_agent timeout configuration parsing."""

import importlib
import os

import pytest

import navig.agent.remote_agent


def test_remote_agent_timeout_parsing_valid(monkeypatch):
    monkeypatch.setenv("NAVIG_REMOTE_TIMEOUT", "45")
    importlib.reload(navig.agent.remote_agent)
    assert navig.agent.remote_agent.COMMAND_TIMEOUT == 45

def test_remote_agent_timeout_parsing_invalid(monkeypatch):
    monkeypatch.setenv("NAVIG_REMOTE_TIMEOUT", "invalid-timeout")
    importlib.reload(navig.agent.remote_agent)
    assert navig.agent.remote_agent.COMMAND_TIMEOUT == 120

def test_remote_agent_timeout_parsing_negative(monkeypatch):
    monkeypatch.setenv("NAVIG_REMOTE_TIMEOUT", "-10")
    importlib.reload(navig.agent.remote_agent)
    assert navig.agent.remote_agent.COMMAND_TIMEOUT == 120

def test_remote_agent_timeout_parsing_zero(monkeypatch):
    monkeypatch.setenv("NAVIG_REMOTE_TIMEOUT", "0")
    importlib.reload(navig.agent.remote_agent)
    assert navig.agent.remote_agent.COMMAND_TIMEOUT == 120
