"""navig.tui.screens.system_checks — SystemChecksScreen: environment probes."""

from __future__ import annotations

import asyncio
from typing import Any

from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label
from textual.worker import WorkerCancelled

from navig.tui.config_model import (
    NavigConfig,
    check_config_dir_writable,
    check_disk_space,
    check_git_installed,
    check_network,
    check_ollama_reachable,
    check_python_version,
)
from navig.tui.widgets.check_row import CheckRow


class SystemChecksScreen(Screen):  # type: ignore[type-arg]
    """7 real system checks with staggered reveal and inline fix hints."""

    DEFAULT_CSS = """
    SystemChecksScreen {
        background: #0f172a;
        align: center middle;
    }
    #checks-panel {
        width: 70;
        border: round #22d3ee;
        background: #111827;
        padding: 1 2;
    }
    #checks-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    #checks-footer {
        margin-top: 1;
        align: center middle;
    }
    #checks-footer Button {
        margin: 0 1;
    }
    """

    _CHECK_DEFS: list[tuple] = [
        (
            "Python runtime >= 3.10",
            check_python_version,
            True,
            "Upgrade Python: https://python.org/downloads",
        ),
        (
            "Git installed",
            check_git_installed,
            False,
            "Install git: https://git-scm.com",
        ),
        (
            "Network / DNS",
            check_network,
            False,
            "Check your network connection or firewall",
        ),
        (
            "Disk space (100 MB free)",
            check_disk_space,
            True,
            "Free up disk space before continuing",
        ),
        (
            "Config directory writable (~/.navig)",
            check_config_dir_writable,
            True,
            "Check permissions on your home directory",
        ),
    ]

    def __init__(self, cfg: NavigConfig | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg or NavigConfig()
        self._critical_failed = False

    def compose(self):  # type: ignore[override]
        with Vertical(id="checks-panel"):
            yield Label("System Checks", id="checks-title")
            for label, *_ in self._CHECK_DEFS:
                import re as _re

                _safe_id = _re.sub(r"[^a-zA-Z0-9_-]", "", label[:24].replace(" ", "_"))
                row = CheckRow(label, id=f"check-{_safe_id or str(id(label))}")
                yield row
            yield Label("")
            with Horizontal(id="checks-footer"):
                yield Button(
                    "Continue  →", variant="primary", id="btn-continue", disabled=True
                )
                yield Button("← Back", variant="default", id="btn-back")

    def on_mount(self) -> None:
        self._run_checks()

    @work(exclusive=True)
    async def _run_checks(self) -> None:
        try:
            rows = list(self.query(CheckRow))
            for (label, fn, is_critical, hint), row in zip(self._CHECK_DEFS, rows):
                row.set_pending()
                await asyncio.sleep(0.05)
                try:
                    result = fn()
                except Exception:  # noqa: BLE001
                    result = False
                if result:
                    row.set_pass()
                else:
                    row.set_fail(hint)
                    if is_critical:
                        self._critical_failed = True

            if self._cfg.local_runtime_enabled:
                await asyncio.sleep(0.25)
                try:
                    ok = check_ollama_reachable(self._cfg.local_runtime_host)
                    self.notify(
                        "Ollama: " + ("reachable ✔" if ok else "not reachable"),
                        severity="information" if ok else "warning",
                    )
                except Exception:  # noqa: BLE001
                    pass

            btn: Button = self.query_one("#btn-continue", Button)
            btn.disabled = self._critical_failed
        except WorkerCancelled:
            pass
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Check runner error: {exc}", severity="warning")
            btn = self.query_one("#btn-continue", Button)
            btn.disabled = False

    @on(Button.Pressed, "#btn-continue")
    def _continue(self) -> None:
        from navig.tui.screens.wizard import WizardScreen

        self.app.push_screen(WizardScreen(self._cfg))

    @on(Button.Pressed, "#btn-back")
    def _back(self) -> None:
        self.app.pop_screen()
