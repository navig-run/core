from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_nlp_aliases_module(monkeypatch):
    package_root = Path(__file__).resolve().parents[1]
    plugin_path = package_root / "plugins" / "nlp_aliases.py"
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

    telegram_mod = ModuleType("telegram")
    telegram_mod.Update = object
    telegram_ext_mod = ModuleType("telegram.ext")
    telegram_ext_mod.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)

    monkeypatch.setitem(sys.modules, "telegram", telegram_mod)
    monkeypatch.setitem(sys.modules, "telegram.ext", telegram_ext_mod)

    spec = importlib.util.spec_from_file_location("nlp_aliases", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_call_ai_via_navig_uses_run_llm(monkeypatch):
    module = _load_nlp_aliases_module(monkeypatch)

    class _Result:
        content = "ok from run_llm"

    called = {}

    def _fake_run_llm(**kwargs):
        called.update(kwargs)
        return _Result()

    fake_llm_generate = ModuleType("navig.llm_generate")
    fake_llm_generate.run_llm = _fake_run_llm
    monkeypatch.setitem(sys.modules, "navig.llm_generate", fake_llm_generate)

    out = module._call_ai_via_navig("hello")

    assert out == "ok from run_llm"
    assert called["mode"] == "chat"
    assert called["max_tokens"] == 800
    assert called["messages"][0]["role"] == "user"


def test_call_ai_via_navig_falls_back_to_ask_ai(monkeypatch):
    module = _load_nlp_aliases_module(monkeypatch)

    def _boom_run_llm(**kwargs):
        raise RuntimeError("no llm")

    fake_llm_generate = ModuleType("navig.llm_generate")
    fake_llm_generate.run_llm = _boom_run_llm
    monkeypatch.setitem(sys.modules, "navig.llm_generate", fake_llm_generate)

    called = {}

    def _fake_ask_ai_with_context(prompt: str, model=None):
        called["prompt"] = prompt
        called["model"] = model
        return "ok from ask_ai"

    fake_ai = ModuleType("navig.ai")
    fake_ai.ask_ai_with_context = _fake_ask_ai_with_context
    monkeypatch.setitem(sys.modules, "navig.ai", fake_ai)

    out = module._call_ai_via_navig("fallback please")

    assert out == "ok from ask_ai"
    assert called["prompt"] == "fallback please"
    assert called["model"] is None


def test_call_ai_via_navig_returns_none_when_core_unavailable(monkeypatch):
    module = _load_nlp_aliases_module(monkeypatch)

    def _boom_run_llm(**kwargs):
        raise RuntimeError("no llm")

    def _boom_ask_ai_with_context(prompt: str, model=None):
        raise RuntimeError("no ai")

    fake_llm_generate = ModuleType("navig.llm_generate")
    fake_llm_generate.run_llm = _boom_run_llm
    fake_ai = ModuleType("navig.ai")
    fake_ai.ask_ai_with_context = _boom_ask_ai_with_context

    monkeypatch.setitem(sys.modules, "navig.llm_generate", fake_llm_generate)
    monkeypatch.setitem(sys.modules, "navig.ai", fake_ai)

    out = module._call_ai_via_navig("nothing works")
    assert out is None
