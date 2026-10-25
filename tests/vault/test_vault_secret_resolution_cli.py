"""Secret Resolution through the real CLI: `vault run --env-file`, `resolve`, `doctor`,
`adopt`, `inject` — on a REAL temp vault, with a Python probe as the child.

Isolation: NAVIG_VAULT_DIR points at a tmp dir and the get_vault() singleton is
dropped first; the operator's vault is never opened. Every credential is synthetic.
The child's stdout bypasses CliRunner (it inherits the real fd), so the probe writes
what it saw to a file the test reads back.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.vault import vault_app

pytestmark = pytest.mark.integration

SECRET = "sk-test-" + "S" * 32
ROTATED = "sk-test-" + "R" * 32

PROBE = (
    "import os, sys, json, pathlib\n"
    "keys = sys.argv[2].split(',')\n"
    "out = {k: os.environ.get(k) for k in keys}\n"
    "out['_argv'] = sys.argv\n"
    "cfg = os.environ.get('NAVIG_CONFIG_FILE')\n"
    "if cfg:\n"
    "    out['_cfg_path'] = cfg\n"
    "    out['_cfg'] = pathlib.Path(cfg).read_text(encoding='utf-8')\n"
    "pathlib.Path(sys.argv[1]).write_text(json.dumps(out), encoding='utf-8')\n"
    "raise SystemExit(int(os.environ.get('PROBE_EXIT', '0')))\n"
)


@pytest.fixture
def real_vault(tmp_path, monkeypatch):
    from navig_vault import keyslots
    from navig_vault.session import SessionStore

    from navig.vault import core as vault_core

    vdir = tmp_path / "vault"
    monkeypatch.setenv("NAVIG_VAULT_DIR", str(vdir))
    monkeypatch.setattr(vault_core, "_vault", None)
    keyslots.forget_cached_vmks()
    SessionStore.clear()
    vault = vault_core.get_vault()
    assert Path(vault.vault_dir) == vdir, f"vault escaped isolation: {vault.vault_dir}"
    yield vault
    vault._store.close()
    keyslots.forget_cached_vmks()
    SessionStore.clear()


@pytest.fixture
def probe(tmp_path):
    p = tmp_path / "probe.py"
    p.write_text(PROBE, encoding="utf-8")
    return p


def _run(args: list[str], stdin: str | None = None, env: dict | None = None):
    return CliRunner().invoke(vault_app, args, input=stdin, env=env)


def _set(ref: str, value: str) -> None:
    res = _run(["set", ref, "--stdin"], stdin=value + "\n")
    assert res.exit_code == 0, res.output


def _launch(env_file: Path, probe: Path, report: Path, keys: str, *extra, env=None):
    return _run(["run", "--env-file", str(env_file), *extra, "--",
                 sys.executable, str(probe), str(report), keys], env=env)


# ── vault run --env-file ────────────────────────────────────────────────────


def test_run_env_file_gives_the_child_the_secret_and_leaves_the_file_alone(real_vault, tmp_path, probe):
    _set("vault://openai/api_key", SECRET)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=vault://openai/api_key\nNODE_ENV=development\nPORT=3000\n",
                        encoding="utf-8")
    before = env_file.read_bytes()
    report = tmp_path / "seen.json"
    res = _launch(env_file, probe, report, "OPENAI_API_KEY,NODE_ENV,PORT")
    assert res.exit_code == 0, res.output
    seen = json.loads(report.read_text(encoding="utf-8"))
    assert seen["OPENAI_API_KEY"] == SECRET and seen["NODE_ENV"] == "development" and seen["PORT"] == "3000"
    assert env_file.read_bytes() == before, "the .env must never be rewritten to launch"
    assert SECRET not in json.dumps(seen["_argv"]) and SECRET not in res.output
    assert "OPENAI_API_KEY" not in os.environ, "the parent environment must not change"


def test_run_starts_the_child_where_the_operator_stands_not_in_the_space(real_vault, tmp_path, monkeypatch):
    # main.py chdir's into the active space and stashes the real directory first;
    # `npm run dev` must run in the project, not in the space.
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(project))
    report = tmp_path / "cwd.txt"
    res = _run(["run", "-e", "X=vault://ci/none", "--", sys.executable, "-c", "0"])
    assert res.exit_code == 1  # a missing ref still refuses first
    _set("vault://ci/token", SECRET)
    res = _run(["run", "-e", "X=vault://ci/token", "--", sys.executable, "-c",
                f"import os, pathlib; pathlib.Path(r'{report}').write_text(os.getcwd())"])
    assert res.exit_code == 0, res.output
    assert Path(report.read_text()).resolve() == project.resolve()


def test_run_refuses_to_start_when_a_reference_is_missing(real_vault, tmp_path, probe):
    env_file = tmp_path / ".env"
    env_file.write_text("A=vault://missing/one\nB=vault://missing/two\n", encoding="utf-8")
    report = tmp_path / "seen.json"
    res = _launch(env_file, probe, report, "A")
    assert res.exit_code == 1 and not report.exists()
    assert "vault://missing/one" in res.output and "vault://missing/two" in res.output, "every failure is listed"


def test_run_rejects_a_malformed_reference_with_exit_2(real_vault, tmp_path, probe):
    env_file = tmp_path / ".env"
    env_file.write_text("A=vault://../escape\n", encoding="utf-8")
    report = tmp_path / "seen.json"
    assert _launch(env_file, probe, report, "A").exit_code == 2 and not report.exists()


def test_run_rejects_an_unparseable_file_with_exit_2(real_vault, tmp_path, probe):
    env_file = tmp_path / ".env"
    env_file.write_text("A='unterminated\n", encoding="utf-8")
    assert _launch(env_file, probe, tmp_path / "r.json", "A").exit_code == 2
    assert _run(["run", "--env-file", str(tmp_path / "nope.env"), "--", sys.executable, "-c", "0"]).exit_code == 2


def test_run_passes_the_child_exit_code_through(real_vault, tmp_path, probe):
    env_file = tmp_path / ".env"
    env_file.write_text("PROBE_EXIT=9\n", encoding="utf-8")
    assert _launch(env_file, probe, tmp_path / "r.json", "PROBE_EXIT").exit_code == 9


def test_a_reference_replaces_a_stale_inherited_value_and_says_so(real_vault, tmp_path, probe):
    _set("vault://ci/token", SECRET)
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=vault://ci/token\nPORT=3000\n", encoding="utf-8")
    report = tmp_path / "seen.json"
    res = _launch(env_file, probe, report, "TOKEN,PORT", "--no-override",
                  env={"TOKEN": "old-plaintext", "PORT": "8080"})
    assert res.exit_code == 0, res.output
    seen = json.loads(report.read_text(encoding="utf-8"))
    assert seen["TOKEN"] == SECRET, "plaintext in the environment must not shadow a reference"
    assert seen["PORT"] == "8080", "--no-override keeps inherited ordinary values"
    assert "TOKEN" in res.output and "replaced" in res.output


def test_e_flags_win_over_the_file(real_vault, tmp_path, probe):
    _set("vault://ci/token", SECRET)
    _set("vault://ci/other", ROTATED)
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=vault://ci/token\n", encoding="utf-8")
    report = tmp_path / "seen.json"
    assert _launch(env_file, probe, report, "TOKEN", "-e", "TOKEN=vault://ci/other").exit_code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["TOKEN"] == ROTATED


def test_a_rotated_secret_reaches_the_next_launch_without_editing_the_file(real_vault, tmp_path, probe):
    _set("vault://ci/token", SECRET)
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=vault://ci/token\n", encoding="utf-8")
    report = tmp_path / "seen.json"
    assert _launch(env_file, probe, report, "TOKEN").exit_code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["TOKEN"] == SECRET
    _set("vault://ci/token", ROTATED)
    assert _launch(env_file, probe, report, "TOKEN").exit_code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["TOKEN"] == ROTATED


def test_host_run_sends_only_the_files_bindings_never_this_machines_env(real_vault, tmp_path, monkeypatch):
    from navig_vault.commands import vault_keys

    _set("vault://ci/token", SECRET)
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=vault://ci/token\nHOME_DIR=${LOCAL_ONLY:-unset}\nPORT=3000\n", encoding="utf-8")
    sent = {}

    def fake_host(host, command, values, files):
        sent.update(host=host, command=command, values=dict(values), files=dict(files))
        return 0

    monkeypatch.setattr(vault_keys, "_run_on_host", fake_host)
    res = _run(["run", "--host", "box", "--env-file", str(env_file), "-e", "EXTRA=vault://ci/token",
                "--", "./deploy.sh"], env={"LOCAL_ONLY": "neuron-secret-path", "PATH_X": "local"})
    assert res.exit_code == 0, res.output
    assert sent["values"] == {"TOKEN": SECRET, "HOME_DIR": "unset", "PORT": "3000", "EXTRA": SECRET}
    assert "neuron-secret-path" not in json.dumps(sent), "this machine's environment reached the server"


def test_a_provider_credential_answers_its_field_reference(real_vault, tmp_path, probe):
    real_vault.put("openai", json.dumps({"api_key": SECRET}).encode())
    assert _run(["get", "vault://openai/api_key", "--raw"]).output.strip() == SECRET
    env_file = tmp_path / ".env"
    env_file.write_text("K=vault://openai/api_key\n", encoding="utf-8")
    report = tmp_path / "seen.json"
    assert _launch(env_file, probe, report, "K").exit_code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["K"] == SECRET


# ── resolve / doctor ────────────────────────────────────────────────────────


def test_resolve_reports_metadata_only(real_vault):
    _set("vault://ci/token", SECRET)
    ok = _run(["resolve", "vault://ci/token", "--json"])
    assert ok.exit_code == 0 and SECRET not in ok.output
    row = json.loads(ok.output)["references"][0]
    assert row["ok"] and row["source"] == "local" and row["version"] == 1
    assert _run(["resolve", "vault://ci/token", "vault://nope/x"]).exit_code == 1
    assert _run(["resolve", "vault://../x", "--json"]).exit_code == 2
    assert _run(["resolve"]).exit_code == 2


def test_resolve_labels_lists_referenceable_labels_without_values(real_vault):
    _set("vault://ci/token", SECRET)
    real_vault.put("federation/cache/x", json.dumps({"value": "cached"}).encode())
    res = _run(["resolve", "--labels", "--json"])
    assert res.exit_code == 0 and SECRET not in res.output
    labels = json.loads(res.output)["labels"]
    assert "ci/token" in labels and not any(lbl.startswith("federation/") for lbl in labels)


def test_doctor_json_and_exit_codes(real_vault, tmp_path):
    _set("vault://ci/token", SECRET)
    (tmp_path / "proj").mkdir()
    env_file = tmp_path / "proj" / ".env"
    env_file.write_text("TOKEN=vault://ci/token\n", encoding="utf-8")
    clean = _run(["doctor", str(tmp_path / "proj"), "--json"])
    assert clean.exit_code == 0, clean.output
    assert json.loads(clean.output)["summary"]["resolved"] == 1
    env_file.write_text("TOKEN=vault://ci/token\nGONE=vault://nope/x\n", encoding="utf-8")
    broken = _run(["doctor", str(tmp_path / "proj"), "--json"])
    assert broken.exit_code == 1 and SECRET not in broken.output


# ── adopt ───────────────────────────────────────────────────────────────────


def test_adopt_end_to_end_then_run(real_vault, tmp_path, probe):
    proj = tmp_path / "shop"
    proj.mkdir()
    env_file = proj / ".env"
    env_file.write_text(f"# keep\nSTRIPE_SECRET_KEY={SECRET}\nPORT=3000\n", encoding="utf-8")

    dry = _run(["adopt", str(proj), "--project", "shop", "--dry-run"])
    assert dry.exit_code == 0 and SECRET not in dry.output and SECRET in env_file.read_text(encoding="utf-8")
    unconfirmed = _run(["adopt", str(proj), "--project", "shop"])
    assert unconfirmed.exit_code == 0 and SECRET in env_file.read_text(encoding="utf-8"), \
        "without a terminal and without --yes nothing is applied"

    done = _run(["adopt", str(proj), "--project", "shop", "--yes"])
    assert done.exit_code == 0, done.output
    assert env_file.read_text(encoding="utf-8") == \
        "# keep\nSTRIPE_SECRET_KEY=vault://projects/shop/stripe_secret_key\nPORT=3000\n"

    report = tmp_path / "seen.json"
    assert _launch(env_file, probe, report, "STRIPE_SECRET_KEY").exit_code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["STRIPE_SECRET_KEY"] == SECRET


# ── inject ──────────────────────────────────────────────────────────────────


def test_inject_runs_a_command_with_a_private_file_then_deletes_it(real_vault, tmp_path, probe):
    _set("vault://ci/token", SECRET)
    template = tmp_path / "config.yaml"
    template.write_text("api:\n  token: vault://ci/token\n  port: 8080\n", encoding="utf-8")
    report = tmp_path / "seen.json"
    res = _run(["inject", "-t", str(template), "--", sys.executable, str(probe), str(report), "X"])
    assert res.exit_code == 0, res.output
    seen = json.loads(report.read_text(encoding="utf-8"))
    assert SECRET in seen["_cfg"] and "vault://" not in seen["_cfg"]
    assert not Path(seen["_cfg_path"]).exists(), "the resolved file is deleted when the command exits"
    assert template.read_text(encoding="utf-8").count("vault://ci/token") == 1


def test_inject_output_refuses_an_unignored_git_tree_and_writes_elsewhere(real_vault, tmp_path):
    _set("vault://ci/token", SECRET)
    template = tmp_path / "config.json"
    template.write_text('{"token": "vault://ci/token"}', encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    refused = _run(["inject", "-t", str(template), "-o", str(repo / "config.json")])
    assert refused.exit_code == 2 and not (repo / "config.json").exists()

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ok = _run(["inject", "-t", str(template), "-o", str(out_dir / "config.json")])
    assert ok.exit_code == 0, ok.output
    assert json.loads((out_dir / "config.json").read_text(encoding="utf-8")) == {"token": SECRET}
    if os.name != "nt":
        assert (out_dir / "config.json").stat().st_mode & 0o077 == 0
    assert _run(["inject", "-t", str(template), "-o", str(out_dir / "config.json")]).exit_code == 2, \
        "an existing output is not replaced without --overwrite"


def test_inject_check_resolves_without_writing(real_vault, tmp_path):
    template = tmp_path / "settings.toml"
    template.write_text('[api]\nkey = "vault://nope/x"\n', encoding="utf-8")
    assert _run(["inject", "-t", str(template), "--check"]).exit_code == 1
    _set("vault://nope/x", SECRET)
    assert _run(["inject", "-t", str(template), "--check"]).exit_code == 0
