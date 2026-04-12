from pathlib import Path
import pytest

pytestmark = pytest.mark.integration


def test_audit_log_default_path_respects_config_dir(monkeypatch, tmp_path):
    cfg_root = tmp_path / "navig_cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_root))

    import importlib

    import navig.gateway.audit_log as audit_mod

    importlib.reload(audit_mod)
    log = audit_mod.AuditLog()

    assert str(log._path).startswith(str(cfg_root))
    assert log._path.name == "audit.jsonl"


def test_billing_emitter_default_path_respects_config_dir(monkeypatch, tmp_path):
    cfg_root = tmp_path / "navig_cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_root))

    import importlib

    import navig.gateway.billing_emitter as billing_mod

    importlib.reload(billing_mod)
    emitter = billing_mod.BillingEmitter()

    assert str(emitter._path).startswith(str(cfg_root))
    assert emitter._path.name == "billing.jsonl"
