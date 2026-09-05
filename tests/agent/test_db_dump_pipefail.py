"""navig_db_dump must not report a phantom backup success on the .gz path.

`mysqldump … | gzip > out.gz` over SSH returns only the LAST stage's exit code (gzip's),
and gzip exits 0 compressing an empty stream — so a failed mysqldump (bad password, DB
down) was reported as a completed backup over an empty .gz (silent backup data loss). The
.gz command now runs under `bash -c 'set -o pipefail; …'` so mysqldump's failure surfaces.
Under the old code the first test fails (no bash/pipefail wrap).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navig.agent.tools.devops_tools import NavigDbDumpTool


async def _dump(output: str, ssh_result=(True, "", "")):
    captured: dict = {}
    disco = MagicMock()

    def _exec(cmd):
        captured["cmd"] = cmd
        return ssh_result

    disco._execute_ssh = _exec
    with (
        patch("navig.agent.tools.devops_tools._resolve_host", return_value=("host1", {})),
        patch("navig.agent.tools.devops_tools._get_discovery", return_value=disco),
    ):
        result = await NavigDbDumpTool().run({"database": "app", "output": output})
    return captured.get("cmd", ""), result


async def test_gz_dump_runs_pipeline_under_pipefail():
    cmd, result = await _dump("/backups/app.sql.gz")
    assert cmd.startswith("bash -c ")          # explicit shell so pipefail is honored
    assert "set -o pipefail" in cmd
    assert "| gzip >" in cmd
    assert result.success is True


async def test_plain_dump_is_a_direct_redirect_no_pipe():
    cmd, result = await _dump("/backups/app.sql")
    assert "pipefail" not in cmd and "| gzip" not in cmd and "bash -c" not in cmd
    assert "> /backups/app.sql" in cmd  # mysqldump's own exit code is honest here
    assert result.success is True


async def test_gz_dump_nonzero_exit_is_reported_as_failure():
    # pipefail makes a failed mysqldump a nonzero pipeline exit; that must be a failure,
    # not a phantom "Dumped …" success.
    cmd, result = await _dump("/backups/app.sql.gz", ssh_result=(False, "", "Access denied"))
    assert result.success is False
    assert result.output is None
