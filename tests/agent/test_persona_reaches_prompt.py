"""A persona must actually reach the prompt, and be per-session.

``channel_router`` used to call ``set_active_persona(name)`` with a bare string
and no soul content, so ``tone``, ``banned_phrases`` and the persona's own
``soul.md`` were parsed, validated by ``personas/contracts.py`` — and dropped.

It also pre-loaded ONE ``self._soul_content`` shared by every constructed agent,
which made per-session identity structurally impossible: two Telegram users on
different personas got the same soul. The source-level guard at the bottom fails
the build if that comes back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import navig.personas.soul_loader as soulmod
from navig.agent.conv.agent import ConversationalAgent
from navig.agent.conv.soul import SoulLoader


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_persona(root: Path, slug: str, *, tone: str, banned: list[str], soul: str) -> Path:
    d = root / "personas" / slug
    _write(d / "soul.md", soul)
    banned_yaml = "\n".join(f'  - "{b}"' for b in banned) or "  []"
    _write(
        d / "persona.yaml",
        f'name: {slug}\ndisplay_name: "{slug.title()}"\ntone: {tone}\n'
        f'model_hint: ""\nvoice_id: ""\nwallpaper: ""\nstartup_sound: ""\n'
        f"banned_phrases:\n{banned_yaml}\nsoul_extends: \"\"\n",
    )
    return d


@pytest.fixture
def personas(tmp_path, monkeypatch):
    root = tmp_path / "navig"
    root.mkdir()
    _make_persona(root, "tyler", tone="direct", banned=["I apologize"], soul="I am Tyler.")
    _make_persona(root, "sunny", tone="playful", banned=["regrettably"], soul="I am Sunny.")
    monkeypatch.setattr(soulmod, "config_dir", lambda: root)
    monkeypatch.setattr(
        "navig.personas.resolver.resolve_persona",
        lambda name, cwd=None: (root / "personas" / name.lower())
        if (root / "personas" / name.lower()).is_dir()
        else None,
    )
    monkeypatch.setattr("navig.personas.resolver.config_dir", lambda: root, raising=False)
    # A fresh loader per test — it is a process-wide singleton.
    SoulLoader._instance = None
    SoulLoader._initialized = False
    yield root
    SoulLoader._instance = None
    SoulLoader._initialized = False


class TestPersonaReachesThePrompt:
    def test_persona_soul_is_injected(self, personas):
        agent = ConversationalAgent()
        agent.set_active_persona("tyler")
        assert "I am Tyler." in agent._build_system_prompt("plan the migration for me please")

    def test_persona_tone_becomes_a_chat_rule(self, personas):
        agent = ConversationalAgent()
        agent.set_active_persona("tyler")
        prompt = agent._build_system_prompt("plan the migration for me please")
        assert "Be blunt and economical" in prompt

    def test_persona_banned_phrases_are_enforced(self, personas):
        agent = ConversationalAgent()
        agent.set_active_persona("tyler")
        prompt = agent._build_system_prompt("plan the migration for me please")
        assert "NEVER say:" in prompt
        assert "I apologize" in prompt

    def test_banned_phrases_extend_rather_than_replace_house_rules(self, personas):
        agent = ConversationalAgent()
        agent.set_active_persona("tyler")
        prompt = agent._build_system_prompt("plan the migration for me please")
        assert "yes-man" in prompt.lower(), "house chat rules must survive a persona"

    def test_resolution_reports_the_persona(self, personas):
        agent = ConversationalAgent()
        agent.set_active_persona("tyler")
        assert agent._soul_ctx is not None
        assert agent._soul_ctx.source == "persona"
        assert agent._soul_ctx.persona == "tyler"


class TestIdentityIsPerSession:
    def test_two_agents_two_personas_one_process(self, personas):
        """The bug this closes: one shared soul across every chat in the daemon."""
        a, b = ConversationalAgent(), ConversationalAgent()
        a.set_active_persona("tyler")
        b.set_active_persona("sunny")
        msg = "help me plan out the next deployment window"
        prompt_a, prompt_b = a._build_system_prompt(msg), b._build_system_prompt(msg)

        assert "I am Tyler." in prompt_a and "I am Sunny." not in prompt_a
        assert "I am Sunny." in prompt_b and "I am Tyler." not in prompt_b

    def test_switching_persona_reresolves(self, personas):
        agent = ConversationalAgent()
        agent.set_active_persona("tyler")
        agent.set_active_persona("sunny")
        assert "I am Sunny." in agent._build_system_prompt("plan the next deployment window")

    def test_repeat_set_with_same_key_is_a_noop(self, personas):
        agent = ConversationalAgent()
        agent.set_active_persona("tyler")
        first = agent._soul_ctx
        agent.set_active_persona("tyler")
        assert agent._soul_ctx is first

    def test_package_default_contributes_no_traits_either(self, personas, monkeypatch):
        """Half a persona is worse than none.

        The guard skips the package ``default``'s *soul*; its ``tone: warm`` must
        be skipped too, or every un-chosen install silently picks up a chat rule
        from a persona nobody selected.
        """
        from navig.agent.conv.soul import _persona_traits

        # Resolve "default" against the PACKAGE personas dir (no user override).
        monkeypatch.setattr(
            "navig.personas.resolver.resolve_persona",
            lambda name, cwd=None: Path(__import__("navig").__file__).parent
            / "resources"
            / "personas"
            / name.lower(),
        )
        assert _persona_traits("default", None) == ("", ())

    def test_a_real_package_persona_still_contributes_traits(self, personas, monkeypatch):
        monkeypatch.setattr(
            "navig.personas.resolver.resolve_persona",
            lambda name, cwd=None: Path(__import__("navig").__file__).parent
            / "resources"
            / "personas"
            / name.lower(),
        )
        from navig.agent.conv.soul import _persona_traits

        tone, banned = _persona_traits("tyler", None)
        assert tone == "direct" and "I apologize" in banned

    def test_unknown_persona_degrades_to_shipped_identity(self, personas):
        agent = ConversationalAgent()
        agent.set_active_persona("does-not-exist")
        prompt = agent._build_system_prompt("plan the next deployment window")
        assert "## Who You Are" in prompt
        assert "## Operating Rules" in prompt

    def test_soul_content_kwarg_still_short_circuits(self, personas):
        """The CLI/test injection path must keep working unchanged."""
        agent = ConversationalAgent(soul_content="Injected identity.")
        assert "Injected identity." in agent._build_system_prompt("plan the deployment window")


class TestNoProcessGlobalIdentityInTheRouter:
    """Source-level guard, in the spirit of tests/cli/test_doctor_honesty.py.

    ``SoulLoader.override()`` is the ergonomic-looking call and it is
    process-global; re-introducing a shared soul in the router is the exact
    regression that hid behind the per-session agent cache for so long.

    This walks the AST, never the text. A textual scan flags the *docstring*
    that explains the removed pattern — and the mechanical "fix" for that is to
    delete the explanation, which is precisely backwards.
    """

    @pytest.fixture(scope="class")
    def router_ast(self):
        import ast

        path = (
            Path(__file__).resolve().parents[2] / "navig" / "gateway" / "channel_router.py"
        )
        return ast.parse(path.read_text(encoding="utf-8"))

    def test_no_shared_soul_content_attribute(self, router_ast):
        import ast

        offenders = [
            node
            for node in ast.walk(router_ast)
            if isinstance(node, ast.Attribute)
            and node.attr == "_soul_content"
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ]
        assert not offenders, (
            "channel_router regained a process-global self._soul_content at line(s) "
            f"{[n.lineno for n in offenders]} — every session would share one identity"
        )

    def test_agent_is_not_constructed_with_soul_content(self, router_ast):
        import ast

        offenders = [
            call.lineno
            for call in ast.walk(router_ast)
            if isinstance(call, ast.Call)
            and any(kw.arg == "soul_content" for kw in call.keywords)
        ]
        assert not offenders, f"soul_content= passed at line(s) {offenders}"

    def test_persona_is_passed_with_space_and_cwd(self, router_ast):
        import ast

        calls = [
            call
            for call in ast.walk(router_ast)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_active_persona"
        ]
        assert calls, "router no longer binds a persona at all"
        kwargs = {kw.arg for call in calls for kw in call.keywords}
        assert {"space", "cwd"} <= kwargs
