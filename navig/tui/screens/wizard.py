"""
navig.tui.screens.wizard — WizardScreen: 5-step configuration wizard.

Includes _WizardStepBase and all 5 step widgets (Step1–Step5).
Esc at any step saves partial progress and opens the dashboard.
"""

from __future__ import annotations

from typing import Any

from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Select,
    Switch,
)

from navig.tui.config_model import (
    DEFAULT_CONFIG_FILE,
    DEFAULT_WORKSPACE_DIR,
    NavigConfig,
    build_config_dict,
)
from navig.tui.widgets.step_indicator import StepIndicator
from navig.tui.widgets.summary_panel import SummaryPanel

# ---------------------------------------------------------------------------
# Wizard step base
# ---------------------------------------------------------------------------


class _WizardStepBase(Vertical):
    """Base class for all step widgets (handles .visible CSS fade-in)."""

    DEFAULT_CSS = """
    _WizardStepBase {
        opacity: 0%;
    }
    _WizardStepBase.visible {
        opacity: 100%;
    }
    """

    def on_mount(self) -> None:
        self.call_after_refresh(lambda: self.add_class("visible"))


# ---------------------------------------------------------------------------
# Step 1 — Identity
# ---------------------------------------------------------------------------


class Step1IdentityWidget(_WizardStepBase):
    DEFAULT_CSS = (
        _WizardStepBase.DEFAULT_CSS
        + """
    Step1IdentityWidget { height: auto; padding: 1 2; }
    Step1IdentityWidget Label { color: #94a3b8; margin-bottom: 0; }
    Step1IdentityWidget Input { margin-bottom: 1; }
    """
    )

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def compose(self):  # type: ignore[override]
        yield Label("Operator name (display)")
        yield Input(value=self._cfg.profile_name, id="inp-profile-name", placeholder="operator")
        yield Label("Workspace root")
        yield Input(
            value=self._cfg.workspace_root,
            id="inp-workspace",
            placeholder=str(DEFAULT_WORKSPACE_DIR),
        )
        yield Label("Theme")
        yield Select(
            [("Dark", "dark"), ("Light", "light"), ("System", "system")],
            value=self._cfg.theme,
            id="sel-theme",
        )

    @on(Input.Changed, "#inp-profile-name")
    def _name_changed(self, event: Input.Changed) -> None:
        self._cfg.profile_name = event.value
        self._notify_parent()

    @on(Input.Changed, "#inp-workspace")
    def _ws_changed(self, event: Input.Changed) -> None:
        self._cfg.workspace_root = event.value
        self._notify_parent()

    @on(Select.Changed, "#sel-theme")
    def _theme_changed(self, event: Select.Changed) -> None:
        if event.value and event.value is not Select.BLANK:
            self._cfg.theme = str(event.value)
        self._notify_parent()

    def _notify_parent(self) -> None:
        try:
            self.app.query_one(SummaryPanel).refresh_from(self._cfg)
        except NoMatches:
            pass  # best-effort: widget not found in layout; skip
# ---------------------------------------------------------------------------
# Step 2 — Provider
# ---------------------------------------------------------------------------


class Step2ProviderWidget(_WizardStepBase):
    DEFAULT_CSS = (
        _WizardStepBase.DEFAULT_CSS
        + """
    Step2ProviderWidget { height: auto; padding: 1 2; }
    Step2ProviderWidget Label { color: #94a3b8; margin-bottom: 0; }
    Step2ProviderWidget RadioSet { margin-bottom: 1; }
    Step2ProviderWidget Input { margin-bottom: 1; }
    """
    )

    _PROVIDERS = ["openrouter", "openai", "anthropic", "groq", "ollama", "none"]

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def compose(self):  # type: ignore[override]
        yield Label("AI Provider")
        with RadioSet(id="radio-provider"):
            for p in self._PROVIDERS:
                yield RadioButton(p, value=(p == self._cfg.ai_provider))
        yield Label("API Key  [dim](stored in vault — not echoed)[/dim]", markup=True)
        yield Input(
            value="",
            placeholder="sk-… or leave blank",
            password=True,
            id="inp-api-key",
        )

    @on(RadioSet.Changed, "#radio-provider")
    def _provider_changed(self, event: RadioSet.Changed) -> None:
        if event.pressed is not None:
            self._cfg.ai_provider = event.pressed.label.plain  # type: ignore[union-attr]
            inp: Input = self.query_one("#inp-api-key", Input)
            inp.display = self._cfg.ai_provider not in ("ollama", "none")
            self._notify_parent()

    @on(Input.Changed, "#inp-api-key")
    def _key_changed(self, event: Input.Changed) -> None:
        self._cfg.api_key = event.value
        try:
            self.app.query_one(SummaryPanel).refresh_from(self._cfg)
        except NoMatches:
            pass  # best-effort: widget not found in layout; skip
    def _notify_parent(self) -> None:
        try:
            self.app.query_one(SummaryPanel).refresh_from(self._cfg)
        except NoMatches:
            pass  # best-effort: widget not found in layout; skip
# ---------------------------------------------------------------------------
# Step 3 — Runtime
# ---------------------------------------------------------------------------


class Step3RuntimeWidget(_WizardStepBase):
    DEFAULT_CSS = (
        _WizardStepBase.DEFAULT_CSS
        + """
    Step3RuntimeWidget { height: auto; padding: 1 2; }
    Step3RuntimeWidget Label { color: #94a3b8; }
    Step3RuntimeWidget Input { margin-top: 1; }
    """
    )

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def compose(self):  # type: ignore[override]
        yield Label("Local runtime (Ollama / custom LLM server)")
        yield Switch(value=self._cfg.local_runtime_enabled, id="sw-runtime")
        yield Input(
            value=self._cfg.local_runtime_host,
            placeholder="http://localhost:11434",
            id="inp-runtime-host",
            disabled=not self._cfg.local_runtime_enabled,
        )

    @on(Switch.Changed, "#sw-runtime")
    def _toggle(self, event: Switch.Changed) -> None:
        self._cfg.local_runtime_enabled = event.value
        inp: Input = self.query_one("#inp-runtime-host", Input)
        inp.disabled = not event.value
        try:
            self.app.query_one(SummaryPanel).refresh_from(self._cfg)
        except NoMatches:
            pass  # best-effort: widget not found in layout; skip
    @on(Input.Changed, "#inp-runtime-host")
    def _host_changed(self, event: Input.Changed) -> None:
        self._cfg.local_runtime_host = event.value


# ---------------------------------------------------------------------------
# Step 4 — Packs
# ---------------------------------------------------------------------------


class Step4PacksWidget(_WizardStepBase):
    DEFAULT_CSS = (
        _WizardStepBase.DEFAULT_CSS
        + """
    Step4PacksWidget { height: auto; padding: 1 2; }
    Step4PacksWidget Label { color: #94a3b8; margin-bottom: 1; }
    """
    )

    _PACKS = [("DevOps", "devops"), ("SysOps", "sysops"), ("LifeOps", "lifeops")]

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def compose(self):  # type: ignore[override]
        yield Label("Capability packs to activate")
        for display, key in self._PACKS:
            yield Checkbox(display, value=(key in self._cfg.capability_packs), id=f"cb-{key}")

    @on(Checkbox.Changed)
    def _pack_toggled(self, event: Checkbox.Changed) -> None:
        key = event.checkbox.id.replace("cb-", "") if event.checkbox.id else ""
        if event.value:
            if key not in self._cfg.capability_packs:
                self._cfg.capability_packs.append(key)
        else:
            self._cfg.capability_packs = [p for p in self._cfg.capability_packs if p != key]
        try:
            self.app.query_one(SummaryPanel).refresh_from(self._cfg)
        except NoMatches:
            pass  # best-effort: widget not found in layout; skip
# ---------------------------------------------------------------------------
# Step 5 — Shell & hooks
# ---------------------------------------------------------------------------


class Step5ShellWidget(_WizardStepBase):
    DEFAULT_CSS = (
        _WizardStepBase.DEFAULT_CSS
        + """
    Step5ShellWidget { height: auto; padding: 1 2; }
    Step5ShellWidget .sw-label { color: #94a3b8; }
    Step5ShellWidget .sw-desc  { color: #334155; margin-bottom: 1; }
    """
    )

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def compose(self):  # type: ignore[override]
        items = [
            (
                "sw-shell",
                "Shell integration",
                "Adds `navig` to $PATH and sets up completions",
                "shell_integration",
            ),
            (
                "sw-update",
                "Auto-update",
                "Automatically install patch updates",
                "auto_update",
            ),
            (
                "sw-git",
                "Git hooks",
                "Run pre-commit safety checks via navig",
                "git_hooks",
            ),
            (
                "sw-telemetry",
                "Telemetry",
                "Send anonymous usage stats to improve NAVIG",
                "telemetry",
            ),
        ]
        for sw_id, title, desc, attr in items:
            yield Label(title, classes="sw-label")
            yield Label(f"  {desc}", classes="sw-desc")
            yield Switch(value=getattr(self._cfg, attr), id=sw_id)

    @on(Switch.Changed)
    def _sw_changed(self, event: Switch.Changed) -> None:
        mapping = {
            "sw-shell": "shell_integration",
            "sw-update": "auto_update",
            "sw-git": "git_hooks",
            "sw-telemetry": "telemetry",
        }
        attr = mapping.get(event.switch.id or "")
        if attr:
            setattr(self._cfg, attr, event.value)
        try:
            self.app.query_one(SummaryPanel).refresh_from(self._cfg)
        except NoMatches:
            pass  # best-effort: widget not found in layout; skip
# ---------------------------------------------------------------------------
# Step 6 — Optional integrations (Full tier)
# ---------------------------------------------------------------------------


class Step6IntegrationsWidget(_WizardStepBase):
    DEFAULT_CSS = (
        _WizardStepBase.DEFAULT_CSS
        + """
    Step6IntegrationsWidget { height: auto; padding: 1 2; }
    Step6IntegrationsWidget .title { color: #22d3ee; text-style: bold; margin-top: 1; }
    Step6IntegrationsWidget .desc { color: #94a3b8; margin-bottom: 1; }
    Step6IntegrationsWidget Checkbox { margin-bottom: 1; }
    """
    )

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def compose(self):  # type: ignore[override]
        yield Label("Optional Integrations (Full tier)", classes="title")
        yield Label(
            "Enable now or defer to post-setup commands. These are never required.",
            classes="desc",
        )

        yield Label("Matrix", classes="title")
        yield Label(
            "Secure team communication, incident rooms, and deployment notifications.",
            classes="desc",
        )
        yield Checkbox("Set up Matrix in this onboarding run", value=self._cfg.setup_matrix, id="cb-matrix")

        yield Label("Email / SMTP", classes="title")
        yield Label(
            "Automated alert emails and scheduled report delivery.",
            classes="desc",
        )
        yield Checkbox("Set up SMTP in this onboarding run", value=self._cfg.setup_email, id="cb-email")

        yield Label("Social networks", classes="title")
        yield Label(
            "Broadcast updates and controlled outbound posting workflows.",
            classes="desc",
        )
        yield Checkbox(
            "Set up social integrations in this onboarding run",
            value=self._cfg.setup_social,
            id="cb-social",
        )

    @on(Checkbox.Changed)
    def _toggle(self, event: Checkbox.Changed) -> None:
        checkbox_id = event.checkbox.id or ""
        if checkbox_id == "cb-matrix":
            self._cfg.setup_matrix = event.value
        elif checkbox_id == "cb-email":
            self._cfg.setup_email = event.value
        elif checkbox_id == "cb-social":
            self._cfg.setup_social = event.value

        try:
            self.app.query_one(SummaryPanel).refresh_from(self._cfg)
        except NoMatches:
            pass  # best-effort: widget not found in layout; skip
# ---------------------------------------------------------------------------
# WizardScreen — 5-step controller
# ---------------------------------------------------------------------------


class WizardScreen(Screen):  # type: ignore[type-arg]
    """5-step wizard with live SummaryPanel, step progress, and Esc escape hatch."""

    BINDINGS = [
        Binding("escape", "exit_to_dashboard", "Save & Exit", show=True),
    ]

    DEFAULT_CSS = """
    WizardScreen {
        background: #0f172a;
    }
    #wizard-header {
        height: 3;
        background: #111827;
        border-bottom: solid #1e293b;
        padding: 1 2;
    }
    #wizard-body {
        height: 1fr;
    }
    #wizard-steps {
        width: 1fr;
        padding: 1 2;
    }
    #wizard-summary {
        width: 38;
        padding: 1 1;
    }
    #wizard-footer {
        height: 5;
        background: #111827;
        border-top: solid #1e293b;
        align: right middle;
        padding: 0 2;
    }
    #wizard-footer Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        cfg: NavigConfig | None = None,
        start_step: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg or NavigConfig()
        self._step = start_step
        self._step_ids = ["step-1", "step-2", "step-3", "step-4", "step-5"]
        if getattr(self._cfg, "onboarding_tier", "recommended") == "full":
            self._step_ids.append("step-6")

    def compose(self):  # type: ignore[override]
        with Horizontal(id="wizard-header"):
            yield StepIndicator(id="step-indicator")
        with Horizontal(id="wizard-body"):
            with ContentSwitcher(initial=self._step_ids[self._step], id="wizard-steps"):
                yield Step1IdentityWidget(self._cfg, id="step-1")
                yield Step2ProviderWidget(self._cfg, id="step-2")
                yield Step3RuntimeWidget(self._cfg, id="step-3")
                yield Step4PacksWidget(self._cfg, id="step-4")
                yield Step5ShellWidget(self._cfg, id="step-5")
                yield Step6IntegrationsWidget(self._cfg, id="step-6")
            with Vertical(id="wizard-summary"):
                yield SummaryPanel(self._cfg, id="summary-panel")
        with Horizontal(id="wizard-footer"):
            yield Button("← Back", id="btn-back", variant="default")
            yield Button("Next  →", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._sync_nav_buttons()

    def _sync_nav_buttons(self) -> None:
        ind: StepIndicator = self.query_one("#step-indicator", StepIndicator)
        ind.current_step = self._step
        ind.total_steps = len(self._step_ids)
        btn_next: Button = self.query_one("#btn-next", Button)
        btn_next.label = (
            "Finish  ✔" if self._step == len(self._step_ids) - 1 else "Next  →"
        )  # type: ignore[assignment]
        btn_back: Button = self.query_one("#btn-back", Button)
        btn_back.disabled = self._step == 0

    @on(Button.Pressed, "#btn-next")
    def _next(self) -> None:
        if not self._validate_current_step():
            return
        if self._step < len(self._step_ids) - 1:
            self._step += 1
            sw: ContentSwitcher = self.query_one("#wizard-steps", ContentSwitcher)
            sw.current = self._step_ids[self._step]
            self._sync_nav_buttons()
        else:
            from navig.tui.screens.review import ReviewScreen

            self.app.push_screen(ReviewScreen(self._cfg))

    @on(Button.Pressed, "#btn-back")
    def _back(self) -> None:
        if self._step > 0:
            self._step -= 1
            sw: ContentSwitcher = self.query_one("#wizard-steps", ContentSwitcher)
            sw.current = self._step_ids[self._step]
            self._sync_nav_buttons()
        else:
            self.app.pop_screen()

    def _validate_current_step(self) -> bool:
        if self._step == 0:
            if not self._cfg.profile_name.strip():
                self.notify("Operator name cannot be empty.", severity="warning")
                return False
        if self._step == 1:
            if self._cfg.ai_provider not in ("ollama", "none") and not self._cfg.api_key:
                self.notify(
                    "No API key entered. You can add one later via `navig ai providers`.",
                    severity="warning",
                    timeout=4,
                )
        return True

    def action_exit_to_dashboard(self) -> None:
        """Esc: save partial progress and open the dashboard."""
        self._partial_save()
        from navig.tui.screens.dashboard import DashboardScreen

        self.app.switch_screen(DashboardScreen())

    def _partial_save(self) -> None:
        """Write current NavigConfig state to navig.json (even if incomplete)."""
        try:
            from navig.commands.onboard import save_config

            cfg_dict = build_config_dict(self._cfg)
            save_config(cfg_dict, DEFAULT_CONFIG_FILE)
        except Exception:  # noqa: BLE001
            pass  # best-effort partial save; failure is silent
