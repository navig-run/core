"""A plugin's logs must reach NAVIG's handlers — they reached nothing.

Plugins ship as their own distributions, so `logging.getLogger(__name__)` inside
`navig_download` lands OUTSIDE the `navig` tree. Every handler in
`navig.core.logging` is attached to the `navig` logger, which sets
`propagate = False`. So a plugin's records reached neither `navig.log` nor the
redacting formatter — they fell through to Python's `lastResort` handler and
were gone.

Measured on the operator's machine before the fix: **zero** records from any
`navig_*` package in 8.6 MB of `navig.log`, across 17 plugins / 52 modules. The
TikTok engine's `"yt-dlp refused … the browser tier served it"` — the single line
that would have named the failing tier during the 🔍 Analyse incident — had never
been written once. The log did not know the plugins existed.
"""

from __future__ import annotations

import logging

import pytest

from navig.core.logging import _configure_root_logger, bind_plugin_logging


@pytest.fixture
def navig_log(tmp_path):
    """A real navig log file, with the handlers restored afterwards."""
    root = logging.getLogger("navig")
    saved_handlers, saved_level = list(root.handlers), root.level
    path = tmp_path / "navig.log"
    _configure_root_logger(log_file=path, level=logging.INFO)
    try:
        yield path
    finally:
        for h in root.handlers:
            h.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def _read(path) -> str:
    for h in logging.getLogger("navig").handlers:
        h.flush()
    return path.read_text(encoding="utf-8")


def test_an_unbound_plugin_logger_reaches_nothing(navig_log):
    """The defect itself, pinned: this is what all 17 plugins did.

    The control line matters — without it this passes just as happily when the
    log is broken outright, which is the same shape of false pass the assertion
    is meant to catch.
    """
    logging.getLogger("navig_unbound_probe.mod").error("UNBOUND")
    logging.getLogger("navig.control").error("CONTROL")

    text = _read(navig_log)
    assert "CONTROL" in text, "the log is not working at all — this proves nothing"
    assert "UNBOUND" not in text


def test_binding_puts_the_plugin_inside_the_navig_tree(navig_log):
    bind_plugin_logging("navig_probe_pkg")

    logging.getLogger("navig_probe_pkg.deep.mod").info("PLUGIN-INFO")
    logging.getLogger("navig_probe_pkg.deep.mod").error("PLUGIN-ERROR")

    text = _read(navig_log)
    assert "PLUGIN-INFO" in text
    assert "PLUGIN-ERROR" in text


def test_it_works_even_when_the_plugin_already_imported(navig_log):
    """The real order: a plugin's modules create their loggers during `ep.load()`,
    which happens BEFORE anything can bind them. Python re-points those children
    onto the package logger when it is finally created, and this asserts it —
    binding only new loggers would cover none of the modules that matter."""
    pre_existing = logging.getLogger("navig_late_pkg.already.imported")
    assert pre_existing.parent.name == "root", "precondition: outside the navig tree"

    bind_plugin_logging("navig_late_pkg")

    assert pre_existing.parent.name == "navig_late_pkg"
    pre_existing.warning("LATE-BOUND")
    assert "LATE-BOUND" in _read(navig_log)


def test_a_bound_plugin_is_logged_exactly_once(navig_log):
    """Re-parenting rather than copying handlers is what keeps this duplicate-free;
    a second copy of every plugin line is its own kind of broken log."""
    bind_plugin_logging("navig_dupe_pkg")

    logging.getLogger("navig_dupe_pkg.mod").info("ONCE-ONLY")

    assert _read(navig_log).count("ONCE-ONLY") == 1


def test_third_party_libraries_are_not_swept_in(navig_log):
    """The scope is the plugin's OWN package, never its dependency tree — a log
    that also carries urllib3 and yt-dlp internals is one nobody reads."""
    bind_plugin_logging("navig_scoped_pkg")

    logging.getLogger("urllib3.connectionpool").error("THIRD-PARTY")
    logging.getLogger("yt_dlp.extractor").error("THIRD-PARTY")
    logging.getLogger("navig_scoped_pkg.mod").error("THE-PLUGIN-ITSELF")

    text = _read(navig_log)
    assert "THE-PLUGIN-ITSELF" in text, "nothing landed — the exclusion proves nothing"
    assert "THIRD-PARTY" not in text


@pytest.mark.parametrize("package", ["navig", "navig.telegram", ""])
def test_core_is_left_alone(package):
    """Core is already inside the tree; re-parenting `navig` onto itself would be
    a cycle, and the guard has to be explicit about that."""
    before = logging.getLogger("navig").parent

    bind_plugin_logging(package)

    assert logging.getLogger("navig").parent is before


def test_a_plugin_record_is_redacted_like_any_other(navig_log):
    """The file handler carries the RedactingFormatter. Outside the tree a plugin
    also bypassed that, so anything it logged went to `lastResort` unredacted."""
    bind_plugin_logging("navig_secret_pkg")

    logging.getLogger("navig_secret_pkg.mod").error(
        "connecting with password=hunter2ismysecret")

    text = _read(navig_log)
    # Attribute the negative: "the secret is absent" is also true of a log the
    # record never reached, which is the pre-fix behaviour this test would then
    # be silently blessing.
    assert "connecting with" in text, "the record never landed — redaction untested"
    assert "hunter2ismysecret" not in text


# ── …and the loader must actually CALL it ────────────────────────────────────
#
# A helper that is written, tested and wired to nothing is this repo's most
# repeated shape of dead fix. The two tests below are the wiring, asserted
# through the real `load_entry_point_plugins` rather than by reading the source.


class _FakeEntryPoint:
    """One `navig.plugins` entry point, shaped like importlib.metadata's."""

    def __init__(self, name: str, obj: object, module: str) -> None:
        self.name = name
        self.module = module
        self.dist = type("D", (), {"name": name})()
        self._obj = obj

    def load(self) -> object:
        return self._obj


def _fake_plugin(package: str) -> object:
    """An entry-point target that reports *package* as its home, like a real one."""
    return type("Plugin", (), {"__module__": f"{package}.plugin"})


def _load_with(monkeypatch, ep) -> None:
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda **kw: [ep] if kw.get("group") == "navig.plugins" else [])
    from navig.core.plugins import load_entry_point_plugins

    load_entry_point_plugins()


def test_loading_a_plugin_binds_its_logging(monkeypatch, navig_log):
    """The end-to-end wiring: install a plugin, its logs appear in navig.log."""
    ep = _FakeEntryPoint("navig-wired", _fake_plugin("navig_wired_pkg"),
                         "navig_wired_pkg.plugin")

    _load_with(monkeypatch, ep)

    logging.getLogger("navig_wired_pkg.deep.mod").error("WIRED-THROUGH-THE-LOADER")
    assert "WIRED-THROUGH-THE-LOADER" in _read(navig_log)


def test_a_plugin_that_logs_while_registering_is_not_lost(monkeypatch, navig_log):
    """Binding happens BEFORE `register()`. A plugin that logs while wiring its
    routes is emitting exactly the record worth keeping, and binding afterwards
    would drop it."""
    seen: list[str] = []

    class Plugin:
        __module__ = "navig_early_pkg.plugin"

        @staticmethod
        def register() -> None:
            logging.getLogger("navig_early_pkg.boot").warning("REGISTERED-EARLY")
            seen.append("register ran")

    _load_with(monkeypatch, _FakeEntryPoint("navig-early", Plugin,
                                            "navig_early_pkg.plugin"))

    assert seen == ["register ran"], "the fake plugin never registered"
    assert "REGISTERED-EARLY" in _read(navig_log)
