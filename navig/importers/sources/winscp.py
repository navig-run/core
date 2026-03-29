from __future__ import annotations

import configparser
import logging
import re
from pathlib import Path
from urllib.parse import unquote

from ..base import BaseImporter
from ..models import ImportedItem
from ..utils import winscp_default_path

logger = logging.getLogger(__name__)


class WinSCPImporter(BaseImporter):
    SOURCE_NAME = "winscp"
    ITEM_TYPE = "server"

    def detect(self) -> bool:
        default = self.default_path()
        return bool(default and Path(default).exists())

    def default_path(self) -> str | None:
        return winscp_default_path()

    def parse(self, path: str) -> list[ImportedItem]:
        target = Path(path)
        if not target.exists():
            return []

        suffix = target.suffix.lower()
        if suffix == ".reg":
            return self._parse_reg(target)
        return self._parse_ini(target)

    def _parse_ini(self, path: Path) -> list[ImportedItem]:
        try:
            parser = configparser.ConfigParser()
            parser.optionxform = str
            loaded = parser.read(path, encoding="utf-8-sig")
            if not loaded:
                parser.read(path, encoding="utf-8")
            items: list[ImportedItem] = []

            for section in parser.sections():
                section_l = section.lower()
                if "server" not in section_l and "session" not in section_l:
                    continue

                host = parser.get(section, "HostName", fallback="")
                if not host:
                    continue
                port = self._normalize_port(parser.get(section, "PortNumber", fallback="22"))
                username = parser.get(section, "UserName", fallback="")
                protocol = parser.get(section, "Protocol", fallback=parser.get(section, "FSProtocol", fallback=""))
                name = unquote(parser.get(section, "Name", fallback=section.split("\\")[-1]))

                items.append(
                    ImportedItem(
                        source=self.SOURCE_NAME,
                        type=self.ITEM_TYPE,
                        label=name,
                        value=host,
                        meta={
                            "host": host,
                            "port": port,
                            "protocol": str(protocol),
                            "username": username,
                        },
                    )
                )
            return items
        except Exception as exc:
            logger.warning("[%s] %s", self.SOURCE_NAME, exc)
            return []

    def _parse_reg(self, path: Path) -> list[ImportedItem]:
        try:
            try:
                text = path.read_text(encoding="utf-16")
            except UnicodeError:
                text = path.read_text(encoding="utf-8", errors="ignore")
            if "Windows Registry Editor" not in text:
                text = path.read_text(encoding="utf-8", errors="ignore")

            blocks = re.split(r"\n\s*\n", text)
            items: list[ImportedItem] = []
            key_re = re.compile(r"^\[(?P<key>.+?)\]$", re.MULTILINE)
            host_re = re.compile(r'"HostName"="(?P<host>[^"]+)"')
            user_re = re.compile(r'"UserName"="(?P<user>[^"]*)"')
            proto_re = re.compile(r'"Protocol"="(?P<proto>[^"]*)"')
            port_re = re.compile(r'"PortNumber"=dword:(?P<port>[0-9a-fA-F]+)')

            for block in blocks:
                if "Sessions\\" not in block:
                    continue
                key_m = key_re.search(block)
                host_m = host_re.search(block)
                if not key_m or not host_m:
                    continue

                key = key_m.group("key")
                name = unquote(key.split("Sessions\\")[-1])
                host = host_m.group("host")
                user_m = user_re.search(block)
                proto_m = proto_re.search(block)
                port_m = port_re.search(block)
                port = self._normalize_port(
                    str(int(port_m.group("port"), 16)) if port_m else "22"
                )

                items.append(
                    ImportedItem(
                        source=self.SOURCE_NAME,
                        type=self.ITEM_TYPE,
                        label=name,
                        value=host,
                        meta={
                            "host": host,
                            "port": port,
                            "protocol": proto_m.group("proto") if proto_m else "",
                            "username": user_m.group("user") if user_m else "",
                        },
                    )
                )
            return items
        except Exception as exc:
            logger.warning("[%s] %s", self.SOURCE_NAME, exc)
            return []

    @staticmethod
    def _normalize_port(value: str) -> str:
        try:
            port = int(str(value).strip())
            if port <= 0:
                return "22"
            return str(port)
        except (TypeError, ValueError):
            return "22"
