from __future__ import annotations
import sys, types
from types import SimpleNamespace
from unittest.mock import MagicMock

def _install_textual_stub():
    if "textual" in sys.modules: return
    sys.modules["textual"] = types.ModuleType("textual")
    r = types.ModuleType("textual.reactive")
    class _reactive:
        def __init__(self, default=None, **_kw): self._default=default; self._name=""
        def __set_name__(self, owner, name): self._name=f"__r_{name}"
        def __get__(self, obj, t=None): return self if obj is None else getattr(obj, self._name, self._default)
        def __set__(self, obj, v): object.__setattr__(obj, self._name, v)
    r.reactive = _reactive
    sys.modules["textual.reactive"] = r
    w = types.ModuleType("textual.widgets")
    class _Static:
        def __init__(self, *a, **k): pass
        def refresh(self, *a, **k): pass
        def update(self, c="", *a, **k): pass
    w.Static = _Static
    sys.modules["textual.widgets"] = w
    for n in ("textual.app","textual.screen","textual.containers","textual.binding","textual.css","textual.css.query","textual.geometry","textual.color"):
        sys.modules.setdefault(n, types.ModuleType(n))

_install_textual_stub()

def _stub(w): w.update=MagicMock(); w.refresh=MagicMock()
def _cfg(**ov):
    d=dict(profile_name="alice",ai_provider="openai",local_runtime_enabled=True,capability_packs=["core","devops"],shell_integration=True,git_hooks=False,telemetry=True)
    d.update(ov); return SimpleNamespace(**d)
def _badge(**ov):
    d=dict(color="green",symbol=u"✓",status="ok",detail="running",deep_link="",label="My Service")
    d.update(ov); return SimpleNamespace(**d)

class TestBrandHero:
    def _make(self):
        from navig.tui.widgets.brand_hero import BrandHero
        obj=BrandHero(); _stub(obj); return obj
    def test_initial_empty(self): assert self._make()._content==""
    def test_render_content(self):
        bh=self._make(); bh._content="N"; assert bh.render()=="N"
    def test_set_text(self):
        bh=self._make(); bh.set_text("Hi"); assert bh._content=="Hi"
    def test_set_text_refresh(self):
        bh=self._make(); bh.set_text("X"); bh.refresh.assert_called()
    def test_render_after_set(self):
        bh=self._make(); bh.set_text("L"); assert bh.render()=="L"
    def test_render_empty_default(self): assert self._make().render()==""

class TestStepIndicator:
    def _make(self,cur=0,tot=5,labels=None):
        from navig.tui.widgets.step_indicator import StepIndicator
        obj=StepIndicator(); _stub(obj)
        obj.current_step=cur; obj.total_steps=tot
        obj.step_labels=labels or ["A","B","C","D","E","F"]
        return obj
    def test_first(self):
        r=self._make(0,3,["X","Y","Z"]).render(); assert "Step 1/3" in r and "X" in r
    def test_last(self):
        r=self._make(2,3,["X","Y","Z"]).render(); assert "Step 3/3" in r and "100%" in r
    def test_mid(self):
        r=self._make(1,4,["A","B","C","D"]).render(); assert "Step 2/4" in r and "50%" in r
    def test_nonempty(self): assert self._make(1,3,["A","B","C"]).render()
    def test_25pct(self): assert "25%" in self._make(0,4,["A","B","C","D"]).render()

class TestCheckRow:
    def _make(self,label="Disk"):
        from navig.tui.widgets.check_row import CheckRow
        obj=CheckRow(label); _stub(obj); return obj
    def test_pending(self): assert self._make()._state=="pending"
    def test_pass(self): r=self._make(); r.set_pass(); assert r._state=="pass"
    def test_fail(self): r=self._make(); r.set_fail("F"); assert r._state=="fail" and r._hint=="F"
    def test_fail_empty(self): r=self._make(); r.set_fail(); assert r._hint==""
    def test_pending_resets(self):
        r=self._make(); r.set_fail("x"); r.set_pending()
        assert r._state=="pending" and r._hint==""
    def test_pass_update_called(self):
        r=self._make(); r.update.reset_mock(); r.set_pass(); r.update.assert_called_once()
    def test_pass_label(self):
        r=self._make("Disk"); c=[]; r.update=lambda t:c.append(t); r.set_pass(); assert "Disk" in c[0]
    def test_fail_hint(self):
        r=self._make("N"); c=[]; r.update=lambda t:c.append(t); r.set_fail("cable"); assert "cable" in c[0]
    def test_pending_out(self):
        r=self._make(); c=[]; r.update=lambda t:c.append(t); r.set_pending(); assert c[0]

class TestSummaryPanel:
    def _make(self,cfg=None):
        from navig.tui.widgets.summary_panel import SummaryPanel
        cfg=cfg or _cfg()
        obj=SummaryPanel.__new__(SummaryPanel); _stub(obj)
        obj._cfg=cfg; obj._status="active"; return obj
    def test_profile(self): assert "bob" in self._make(_cfg(profile_name="bob")).render()
    def test_provider(self): assert "anthropic" in self._make(_cfg(ai_provider="anthropic")).render()
    def test_local(self): assert "local" in self._make(_cfg(local_runtime_enabled=True)).render()
    def test_cloud(self): assert "cloud" in self._make(_cfg(local_runtime_enabled=False)).render()
    def test_packs(self):
        r=self._make(_cfg(capability_packs=["a","b"])).render(); assert "a" in r and "b" in r
    def test_empty_packs(self): assert self._make(_cfg(capability_packs=[])).render()
    def test_active(self): assert "active" in self._make().render()
    def test_set_status(self): p=self._make(); p.set_status("p2"); assert p._status=="p2"
    def test_checkmark(self): assert u"✓" in self._make(_cfg(shell_integration=True)).render()

class TestStatusRow:
    def _make(self,b=None):
        from navig.tui.widgets.status_row import StatusRow
        b=b or _badge()
        obj=StatusRow.__new__(StatusRow); _stub(obj)
        obj._badge=b; obj.update_badge(b); return obj
    def test_badge(self): b=_badge(label="R"); assert self._make(b).badge is b
    def test_deep_link(self): assert self._make(_badge(deep_link="/s")).deep_link=="/s"
    def test_update_called(self):
        r=self._make(); r.update.reset_mock(); r.update_badge(_badge()); r.update.assert_called_once()
    def test_ok_label(self):
        c=[]; b=_badge(status="ok",label="DB"); r=self._make(b)
        r.update=lambda t:c.append(t); r.update_badge(b)
        assert "DB" in c[0] and u"✓" in c[0]
    def test_error_excl(self):
        c=[]; b=_badge(status="error",deep_link=""); r=self._make(b)
        r.update=lambda t:c.append(t); r.update_badge(b); assert "!" in c[0]
    def test_warn_icon(self):
        c=[]; b=_badge(status="warn",deep_link=""); r=self._make(b)
        r.update=lambda t:c.append(t); r.update_badge(b); assert u"▲" in c[0]
    def test_error_cta(self):
        c=[]; b=_badge(status="error",deep_link="/settings/db"); r=self._make(b)
        r.update=lambda t:c.append(t); r.update_badge(b)
        assert "Edit" in c[0] or "settings" in c[0]
    def test_missing_cta(self):
        c=[]; b=_badge(status="missing",deep_link="/settings/k"); r=self._make(b)
        r.update=lambda t:c.append(t); r.update_badge(b)
        assert "Configure" in c[0] or "settings" in c[0]
