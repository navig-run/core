"""Brain parity: a migration must not leave a job, a space or a Brain-duty plugin behind."""

from __future__ import annotations

import json

from navig.brain import parity


def _job(name, command="navig status", enabled=True):
    return {"id": "job_x", "name": name, "command": command, "enabled": enabled}


def _source(**over):
    base = {
        "jobs": [
            _job("habit:review", "NAVIG_HABIT_REMINDER:1:abc"),
            _job("courrier:scan", "navig courrier scan --space company-paperwork"),
            _job("habit:wake", "navig habit wake --space c:/Users/me/.navig/spaces/human-space"),
            _job("wallpaper-rotate", r"NAVIG_EXEC:C:\Python\python.exe rotate.py"),
            _job("old:once", "navig x", enabled=False),
        ],
        "plugins": ["navig-msstore", "navig-email", "navig-glass"],
        "required_plugins": sorted(parity.BRAIN_DUTY_PLUGINS),
        "spaces": ["company-paperwork-space", "human-space", "default"],
    }
    base.update(over)
    return base


def _target(**over):
    base = {
        "jobs": [_job("habit:review"), _job("courrier:scan"), _job("habit:wake")],
        "plugins": ["navig-msstore", "navig-email"],
        "spaces": ["company-paperwork-space", "human-space", "default"],
    }
    base.update(over)
    return base


def test_complete_target_reconciles_and_reports_what_stays_behind():
    res = parity.reconcile(_source(), _target())
    assert res["ok"] is True
    assert res["unreconciled"] == []
    assert res["reconciled"] == ["jobs", "spaces", "plugins"]
    assert res["platform_local_jobs"] == ["wallpaper-rotate"]
    assert res["desktop_only_plugins"] == ["navig-glass"]
    assert res["item_counts"]["jobs"] == 4  # the disabled one is not owed


def test_missing_brain_duty_plugin_blocks():
    """The 2026-10 incident: navig-msstore never reached OUTPOST, sale pings stopped."""
    res = parity.reconcile(_source(), _target(plugins=["navig-email"]))
    assert res["ok"] is False
    assert any("navig-msstore" in u for u in res["unreconciled"])
    assert "plugins" not in res["reconciled"]


def test_missing_space_blocks_even_when_the_job_was_copied():
    """The other half of the incident: jobs copied, company-paperwork space not."""
    res = parity.reconcile(_source(), _target(spaces=["human-space", "default"]))
    assert res["ok"] is False
    assert res["unreconciled"] == ["space 'company-paperwork' missing on the target (used by courrier:scan)"]


def test_missing_job_blocks_and_jobs_match_by_name_not_id():
    target = _target(jobs=[{"id": "job_99", "name": "habit:review", "command": "x"}, _job("habit:wake")])
    res = parity.reconcile(_source(), target)
    assert res["unreconciled"] == ["jobs missing on the target: courrier:scan"]


def test_required_plugins_can_be_extended():
    source = _source(required_plugins=["navig-glass"])
    res = parity.reconcile(source, _target())
    assert res["ok"] is False
    assert "navig-glass" in res["unreconciled"][0]


def test_job_space_parsing():
    assert parity.job_space("navig a --space company-paperwork") == "company-paperwork"
    assert parity.job_space("navig a --space=human-presence") == "human-presence"
    assert parity.job_space("navig a --space c:/Users/me/.navig/spaces/human-space") == "human-space"
    assert parity.job_space("navig a") is None


def test_payload_parsers_tolerate_envelopes(tmp_path):
    assert parity.plugins_from_list(
        {"data": {"plugins": [{"id": "a"}, {"id": "b", "enabled": False}, {"x": 1}]}}
    ) == ["a"]
    assert parity.plugins_from_list("garbage") == []
    assert [j["name"] for j in parity.jobs_from_store([{"name": "a"}, {"id": 1}])] == ["a"]
    assert parity.jobs_from_store(None) == []

    (tmp_path / "scheduler").mkdir()
    (tmp_path / "scheduler" / "cron_jobs.json").write_text(
        json.dumps({"jobs": [_job("habit:review")]}), encoding="utf-8"
    )
    (tmp_path / "spaces" / "human-space").mkdir(parents=True)
    (tmp_path / "spaces" / "README.md").write_text("x", encoding="utf-8")
    inv = parity.local_inventory(tmp_path)
    assert [j["name"] for j in inv["jobs"]] == ["habit:review"]
    assert inv["spaces"] == ["human-space"]
    assert parity.BRAIN_DUTY_PLUGINS <= set(inv["required_plugins"])


# ── the Mini App is part of the Brain ──────────────────────────────────────
def test_miniapp_url_mismatch_blocks_because_the_bot_button_keeps_the_old_url():
    src = _source(client={"public_url": "https://navig-deck.a.workers.dev"})
    dst = _target(client={"public_url": "https://navig-deck.b.workers.dev"})
    res = parity.reconcile(src, dst)
    assert res["ok"] is False
    assert "client" not in res["reconciled"]
    joined = " ".join(res["unreconciled"])
    assert "Mini App public URL differs" in joined
    assert "https://navig-deck.a.workers.dev" in joined


def test_miniapp_url_match_reconciles_and_bundle_skew_is_only_a_note():
    src = _source(
        client={
            "public_url": "https://navig-deck.a.workers.dev",
            "telegram_only": True,
            "deployed_version": "3.31.0",
        }
    )
    dst = _target(
        client={
            "public_url": "https://navig-deck.a.workers.dev",
            "telegram_only": True,
            "deployed_version": "3.27.2",
        }
    )
    res = parity.reconcile(src, dst)
    assert res["ok"] is True
    assert "client" in res["reconciled"]
    assert any("3.31.0" in n and "3.27.2" in n for n in res["notes"])
    assert res["item_counts"]["client_compared"] is True


def test_a_source_without_client_facts_does_not_invent_a_client_gap():
    res = parity.reconcile(_source(), _target())
    assert res["ok"] is True
    assert res["reconciled"] == ["jobs", "spaces", "plugins"]
    assert res["item_counts"]["client_compared"] is False


def test_telegram_only_skew_is_reported_not_blocking():
    src = _source(client={"telegram_only": True})
    dst = _target(client={"telegram_only": False})
    res = parity.reconcile(src, dst)
    assert res["ok"] is True
    assert any("telegram_only differs" in n for n in res["notes"])


def test_client_from_config_reads_only_the_public_facts():
    got = parity.client_from_config(
        {
            "public_url": " https://navig-deck.a.workers.dev ",
            "telegram_only": "false",
            "deployed_version": "3.31.0",
            "api_key": "navig_secret_must_not_travel",
        }
    )
    assert got == {
        "public_url": "https://navig-deck.a.workers.dev",
        "telegram_only": True,  # bool("false") — the string form is what config set stores
        "deployed_version": "3.31.0",
    }
    assert "api_key" not in json.dumps(got)
    assert parity.client_from_config(None) == {}
