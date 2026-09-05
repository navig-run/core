"""Shared stubbing helper for the `navig doctor` test modules.

`test_doctor_json.py` and `test_doctor_heal.py` both need every check replaced with a
stub. They each used to carry their OWN hand-written tuple of check names — so when
`check_databases` was added to the command, both copies went stale, the check ran FOR REAL
against the operator's machine, and six tests broke on an unexpected extra row. Two more
(`check_daemon_freshness`, `check_ledger`) had been doing it unnoticed for longer.

So the list is DERIVED from the module instead of transcribed: there is nothing to keep in
sync, and a check added tomorrow is stubbed the moment it exists.
"""

from __future__ import annotations

from navig.commands import doctor


def all_check_names() -> tuple[str, ...]:
    """Every `check_*` callable the doctor module exposes."""
    names = tuple(
        sorted(n for n in dir(doctor) if n.startswith("check_") and callable(getattr(doctor, n)))
    )
    # A rename of the `check_` convention would silently stub NOTHING and quietly send every
    # test at the live machine — fail loudly instead of degrading to zero.
    assert names, "no check_* functions found on navig.commands.doctor — did the naming change?"
    return names


def stub_all_checks(monkeypatch, **overrides) -> None:
    """Replace every check with an empty stub, then apply per-check overrides.

    Overrides map a check name to the list of rows it should return.
    """
    for name in all_check_names():
        rows = overrides.get(name, [])
        monkeypatch.setattr(doctor, name, lambda *a, _rows=rows, **k: list(_rows))
