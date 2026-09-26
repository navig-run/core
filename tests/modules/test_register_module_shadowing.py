"""`register_module` replaces a builtin TOTALLY — say so when that drops a field.

Overriding a builtin is legitimate: `navig-blackbox` deliberately upgrades the builtin
`blackbox` entry with a richer description and an `app_category`. But the replace is not a
merge, so any field the builtin sets and the plugin omits vanishes — and `_EXTERNAL_DEFS`
is module-scope, so the loss survives a brand-new `ModuleRegistry` and every later
rediscovery in that process.

Measured 2026-09-08 on the real plugin set: 17 external defs, exactly ONE collides with a
builtin (`blackbox`), and that one drops nothing. So this is a latent hazard, not a live
defect — which is why the fix is a log line rather than a behaviour change or a gate.

It earns its place from the debugging cost, not the defect count. A def losing
`merged_into` presents as `assert None == 'messages'` in a serialized payload several
layers from the cause, with nothing logged anywhere; that signature cost hours of
bisecting before the mechanism was found.
"""

from __future__ import annotations

import logging

import pytest

from navig.modules.registry import (
    BUILTIN_MODULES,
    ModuleDef,
    ModuleKind,
    register_module,
)


@pytest.fixture(autouse=True)
def _restore_external_defs():
    """`_EXTERNAL_DEFS` is module-scope and survives rediscovery — never leak from here.

    This module is the one that documents that hazard; leaving a hijacked `contacts` def
    behind for the rest of the worker would be a poor advertisement for it.
    """
    import navig.modules.registry as reg

    saved = list(reg._EXTERNAL_DEFS)
    saved_registry = reg._REGISTRY
    yield
    reg._EXTERNAL_DEFS[:] = saved
    reg._REGISTRY = saved_registry


def _shadow(module_id: str, **extra) -> ModuleDef:
    """A minimal def that sets none of the optional fields — the dropping case."""
    return ModuleDef(
        id=module_id, label="Shadow", description="x",
        kind=ModuleKind.APP, category="operate", icon="users", **extra,
    )


def _builtin(module_id: str) -> ModuleDef:
    found = next((m for m in BUILTIN_MODULES if m.id == module_id), None)
    assert found is not None, f"{module_id!r} is no longer a builtin — pick another subject"
    return found


def test_warns_and_names_every_dropped_field(caplog):
    subject = "contacts"
    builtin = _builtin(subject)
    assert builtin.merged_into, "the subject must actually set the field being dropped"

    with caplog.at_level(logging.WARNING, logger="navig.modules.registry"):
        register_module(_shadow(subject))

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a silent total-replace is the whole failure mode this guards"
    joined = "\n".join(warnings)
    assert subject in joined
    assert "merged_into" in joined, f"the dropped field must be named: {joined}"


def test_silent_when_the_override_drops_nothing(caplog):
    """A plugin that CHANGES values is the normal case and must not be noisy.

    `navig-blackbox` does exactly this, so a warning here would fire on every healthy
    boot and train the operator to ignore the line.
    """
    builtin = _builtin("blackbox")
    richer = ModuleDef(
        **{
            **{f: getattr(builtin, f) for f in ("id", "label", "category", "icon")},
            "description": "A better description.",
            "kind": ModuleKind.APP,
            "surfaces": list(builtin.surfaces or ["cli:blackbox"]),
            "requires": list(builtin.requires or []),
        }
    )
    with caplog.at_level(logging.WARNING, logger="navig.modules.registry"):
        register_module(richer)

    dropped = [r.getMessage() for r in caplog.records if "drops" in r.getMessage()]
    assert not dropped, f"noisy on a healthy override: {dropped}"


def test_registering_a_brand_new_module_is_silent(caplog):
    with caplog.at_level(logging.WARNING, logger="navig.modules.registry"):
        register_module(_shadow("a-module-no-builtin-uses"))

    assert not [r for r in caplog.records if "drops" in r.getMessage()]


def test_the_real_plugin_set_is_silent():
    """The measured baseline: 1 collision, 0 dropped fields.

    If a plugin ever starts dropping a builtin's field, this fails and names it — which is
    the point. Fix the plugin's ModuleDef; do not relax this.
    """
    import navig.modules.registry as reg

    reg.get_registry().discover()
    offenders: list[str] = []
    builtins = {m.id: m for m in BUILTIN_MODULES}
    import dataclasses

    for ext in reg._EXTERNAL_DEFS:
        builtin = builtins.get(ext.id)
        if builtin is None:
            continue
        lost = [
            f.name
            for f in dataclasses.fields(ModuleDef)
            if getattr(builtin, f.name, None) and not getattr(ext, f.name, None)
        ]
        if lost:
            offenders.append(f"{ext.id} drops {sorted(lost)}")
    assert not offenders, "installed plugins drop builtin fields: " + "; ".join(offenders)
