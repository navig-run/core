from navig.importers.sources.winscp import WinSCPImporter
import pytest

pytestmark = pytest.mark.integration


def test_winscp_ini_parse(tmp_path) -> None:
    ini = tmp_path / "WinSCP.ini"
    ini.write_text(
        """
[Sessions\\MyServer]
HostName=example.org
PortNumber=22
UserName=alice
Protocol=sftp
""".strip(),
        encoding="utf-8",
    )

    items = WinSCPImporter().parse(str(ini))
    assert len(items) == 1
    assert items[0].value == "example.org"
    assert items[0].meta["username"] == "alice"


def test_winscp_reg_parse(tmp_path) -> None:
    reg = tmp_path / "winscp.reg"
    reg.write_text(
        """
Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\\Software\\Martin Prikryl\\WinSCP 2\\Sessions\\My%20Server]
"HostName"="example.net"
"PortNumber"=dword:00000016
"UserName"="bob"
"Protocol"="sftp"
""".strip(),
        encoding="utf-8",
    )

    items = WinSCPImporter().parse(str(reg))
    assert len(items) == 1
    assert items[0].label == "My Server"
    assert items[0].meta["port"] == "22"


def test_winscp_reg_decodes_session_name(tmp_path) -> None:
    reg = tmp_path / "winscp.reg"
    reg.write_text(
        """
Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\\Software\\Martin Prikryl\\WinSCP 2\\Sessions\\Ops%20Prod%2FPrimary]
"HostName"="prod.example.net"
"PortNumber"=dword:00000016
"UserName"="deploy"
"Protocol"="sftp"
""".strip(),
        encoding="utf-8",
    )

    items = WinSCPImporter().parse(str(reg))
    assert len(items) == 1
    assert items[0].label == "Ops Prod/Primary"
