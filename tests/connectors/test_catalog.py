"""The connector catalog: shipped data is valid, its claims are true, and edits round-trip.

The catalog ranks ~5k services into batches that are resolved one at a time, and the
rows are edited by hand during each batch review. So the loader is strict (a bad row
fails loudly rather than vanishing from the UI), the importer is append-only (a
re-import never undoes a review), and every ``live``/``native`` claim is checked
against the connectors core actually ships.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.connector_cmd import connector_app
from navig.connectors import catalog
from navig.connectors.catalog import (
    CatalogEntry,
    CatalogError,
    batch_of,
    entry_from_dict,
    parse_rows,
)

CORE = Path(__file__).resolve().parents[2]
CONNECTORS_DIR = CORE / "navig" / "connectors"
runner = CliRunner()


def _row(**over) -> dict:
    base = {
        "id": "acme",
        "name": "Acme",
        "vendor": "Acme Inc",
        "category": "developer",
        "country": "US",
        "tier": "official",
        "rank": 1,
        "batch": 1,
        "status": "planned",
        "integration": None,
        "native": None,
        "mcp": None,
        "flags": [],
    }
    base.update(over)
    return base


def _file(category: str, *rows: dict) -> tuple[str, str]:
    return f"{category}.jsonl", "".join(json.dumps(r) + "\n" for r in rows)


# ── The shipped data ─────────────────────────────────────────────────────────


def test_shipped_catalog_loads_and_is_not_vacuous():
    entries = catalog.load_catalog()
    # A floor, not an exact count: an empty or half-read data dir must fail here
    # instead of shipping a catalog with nothing in it.
    assert len(entries) >= 4000
    assert [e.rank for e in entries] == sorted(e.rank for e in entries)
    assert {e.category for e in entries} <= catalog.CATEGORY_IDS


def test_every_native_claim_names_a_connector_core_ships():
    shipped = {p.name for p in CONNECTORS_DIR.iterdir() if (p / "connector.py").is_file()}
    assert len(shipped) >= 10, "connector scan found too few connectors to mean anything"
    natives = [e for e in catalog.load_catalog() if e.integration == "native"]
    assert natives
    for e in natives:
        assert e.native in shipped, (
            f"{e.id} claims native connector {e.native!r}, which core does not ship"
        )
        assert e.status == "live"


def test_every_shipped_connector_is_in_the_catalog_as_live():
    # The other direction: a connector added to core without a catalog row would be
    # missing from the one list that is supposed to hold everything.
    shipped = {p.name for p in CONNECTORS_DIR.iterdir() if (p / "connector.py").is_file()}
    listed = {e.native for e in catalog.load_catalog() if e.native}
    assert shipped <= listed, f"connectors missing from the catalog: {sorted(shipped - listed)}"


def test_first_batch_is_the_everyday_tools():
    first = catalog.query(batch=1)
    assert len(first) == catalog.BATCH_SIZE
    names = [e.name for e in first]
    assert names[:3] == ["Gmail", "Google Calendar", "Google Drive"]
    assert all(e.tier in ("official", "navig") for e in first)


def test_no_entry_carries_an_assistant_brand_in_its_name():
    # Imported from a directory written for one assistant; our catalog names the service.
    offenders = [
        e.name
        for e in catalog.load_catalog()
        if e.name.lower().endswith(("for claude", "in claude"))
    ]
    assert offenders == []


def test_every_data_file_is_canonical():
    # Files are hand-edited during batch reviews; they must stay in the exact form the
    # importer writes (one row per line, fixed key order) so diffs stay one line each.
    data = CONNECTORS_DIR / "catalog" / "data"
    files = sorted(data.glob("*.jsonl"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "\r" not in text, f"{f.name} has CRLF line endings"
        for line in text.splitlines():
            e = entry_from_dict(json.loads(line), where=f.name)
            assert e.to_row() == line, f"{f.name}: row for {e.id} is not in canonical form"


# ── Validation ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"category": "nope"}, "unknown category"),
        ({"country": "ZZ"}, "has no region"),
        ({"tier": "vip"}, "unknown tier"),
        ({"status": "done"}, "unknown status"),
        ({"integration": "carrier-pigeon"}, "unknown integration"),
        ({"flags": ["hot"]}, "unknown flag"),
        ({"rank": 26, "batch": 1}, "does not match rank"),
        ({"status": "live"}, "must say how it integrates"),
        ({"integration": "native"}, "must name its core connector"),
        ({"integration": "remote_mcp", "mcp": "http://insecure.example"}, "https:// mcp URL"),
        ({"id": "Not Kebab"}, "kebab-case"),
        ({"description": "copied text"}, "unknown key"),
    ],
)
def test_invalid_rows_are_rejected(over, message):
    with pytest.raises(CatalogError, match=message):
        entry_from_dict(_row(**over))


def test_a_valid_resolved_row_is_accepted():
    e = entry_from_dict(
        _row(status="live", integration="remote_mcp", mcp="https://mcp.acme.dev/mcp")
    )
    assert e.region == "north-america"


def test_cross_row_rules():
    with pytest.raises(CatalogError, match="duplicate id"):
        parse_rows([_file("developer", _row()), _file("data", _row(rank=2, category="data"))])
    with pytest.raises(CatalogError, match="duplicate rank"):
        parse_rows([_file("developer", _row(), _row(id="other"))])
    with pytest.raises(CatalogError, match="filed under"):
        parse_rows([_file("data", _row())])
    with pytest.raises(CatalogError, match="not valid JSON"):
        parse_rows([("developer.jsonl", "{nope\n")])


def test_row_round_trips_and_is_stable():
    e = entry_from_dict(_row(flags=["beta"], status="verified", integration="api"))
    line = e.to_row()
    again = entry_from_dict(json.loads(line))
    assert again == e
    assert again.to_row() == line


def test_batch_math():
    assert [batch_of(r) for r in (1, 25, 26, 50, 51)] == [1, 1, 2, 2, 3]


# ── Query ────────────────────────────────────────────────────────────────────


def test_query_filters_combine():
    entries = parse_rows(
        [
            _file(
                "developer",
                _row(),
                _row(id="beta-co", name="Beta Co", vendor="Beta SAS", country="FR", rank=2),
            ),
            _file(
                "data",
                _row(
                    id="gamma",
                    name="Gamma",
                    vendor="Gamma SA",
                    category="data",
                    country="FR",
                    rank=3,
                ),
            ),
        ]
    )
    assert [e.id for e in catalog.query(country="fr", entries=entries)] == ["beta-co", "gamma"]
    assert [e.id for e in catalog.query(region="europe", category="data", entries=entries)] == [
        "gamma"
    ]
    assert [e.id for e in catalog.query(text="acme inc", entries=entries)] == ["acme"]
    s = catalog.summary(entries)
    assert s["total"] == 3 and s["next_batch"] == 1 and s["by_region"]["europe"] == 2


# ── Importer ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def importer():
    spec = importlib.util.spec_from_file_location(
        "connectors_catalog_import", CORE / "tools" / "connectors_catalog_import.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RAW = """Acme icon
Acme
Deploy and host your cloud apps

by Acme Inc
Zeta for Claude
Beta
Search your meeting transcripts

by Zeta Labs
Notebook � The best notes
Community
Take notes and build a second brain

by Notebook Co
"""


def test_importer_parses_cleans_and_never_stores_descriptions(importer, monkeypatch):
    monkeypatch.setattr(importer, "PRIORITY", ["Acme"])
    monkeypatch.setattr(importer, "NAVIG_ONLY", [])
    records = importer.parse_directory(RAW)
    assert [r["name"] for r in records] == ["Acme", "Zeta for Claude", "Notebook � The best notes"]
    new = importer.build(records, [])
    by_name = {e.name: e for e in new}
    assert set(by_name) == {"Acme", "Zeta", "Notebook"}
    assert by_name["Acme"].rank == 1 and by_name["Acme"].category == "cloud"
    assert by_name["Zeta"].flags == ("beta",) and by_name["Zeta"].category == "meetings"
    assert by_name["Notebook"].tier == "community" and by_name["Notebook"].category == "knowledge"
    assert new
    for e in new:
        assert "Deploy and host" not in e.to_row()


def test_importer_is_append_only(importer, monkeypatch):
    monkeypatch.setattr(importer, "PRIORITY", ["Acme"])
    monkeypatch.setattr(importer, "NAVIG_ONLY", [])
    records = importer.parse_directory(RAW)
    first = importer.build(records, [])
    # A reviewer verified Acme by hand; re-importing must not touch it or re-add it.
    reviewed = [
        CatalogEntry(**{**e.__dict__, "status": "verified", "integration": "api", "country": "FR"})
        if e.name == "Acme"
        else e
        for e in first
    ]
    assert importer.build(records, reviewed) == []
    more = importer.parse_directory(RAW + "Omega\nShip packages\n\nby Omega\n")
    added = importer.build(more, reviewed)
    assert [e.name for e in added] == ["Omega"]
    assert added[0].rank == max(e.rank for e in reviewed) + 1


def test_importer_priority_typo_fails_loudly(importer, monkeypatch):
    monkeypatch.setattr(importer, "PRIORITY", ["Acmee"])
    monkeypatch.setattr(importer, "NAVIG_ONLY", [])
    with pytest.raises(SystemExit, match="Acmee"):
        importer.build(importer.parse_directory(RAW), [])


# ── Surfaces: deck route + CLI ───────────────────────────────────────────────


class _Req:
    def __init__(self, **query):
        self.query = query


def _call(**query) -> tuple[int, dict]:
    pytest.importorskip("aiohttp")
    from navig.gateway.deck.routes.connectors import handle_deck_connectors_catalog

    resp = asyncio.run(handle_deck_connectors_catalog(_Req(**query)))
    return resp.status, json.loads(resp.text)


def test_route_filters_and_pages():
    status, body = _call(batch="1", limit="10")
    assert status == 200 and body["ok"]
    data = body["data"]
    assert data["total"] == 25 and len(data["entries"]) == 10
    assert data["entries"][0]["name"] == "Gmail" and data["entries"][0]["region"] == "north-america"
    assert data["summary"]["total"] >= 4000
    assert {c["id"] for c in data["categories"]} == set(catalog.CATEGORY_IDS)


@pytest.mark.parametrize("query", [{"tier": "vip"}, {"limit": "many"}, {"region": "mars"}])
def test_route_rejects_bad_filters(query):
    status, body = _call(**query)
    assert status == 400 and not body["ok"]


def test_cli_catalog_batch_json():
    result = runner.invoke(connector_app, ["catalog", "--batch", "1", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 25 and rows[0]["id"] == "gmail"


def test_cli_catalog_bad_filter_and_empty_result_exit_nonzero():
    assert runner.invoke(connector_app, ["catalog", "--tier", "vip"]).exit_code == 1
    assert (
        runner.invoke(connector_app, ["catalog", "--search", "no-such-service-xyz"]).exit_code == 1
    )


# ── Batch 1, resolved 2026-10-08 ─────────────────────────────────────────────

# Vendors whose MCP server lets any client sign in (docs + live OAuth-metadata probe).
OPEN_BATCH1 = {"Atlassian", "Canva", "Todoist", "Trello", "monday.com", "Airtable"}


def test_batch_one_is_fully_resolved():
    rows = catalog.query(batch=1)
    assert rows
    for e in rows:
        assert e.status in ("verified", "live"), f"{e.name} is still {e.status}"
        assert e.country, f"{e.name} has no country"
        assert e.integration in ("native", "remote_mcp"), e.name


def test_one_click_rows_are_exactly_the_open_vendors():
    # The OS "Add" button offers remote_mcp rows WITHOUT client-approval: a gated vendor
    # there would fail at sign-in after the user clicked, which reads as our bug.
    mcp = [e for e in catalog.query(batch=1) if e.integration == "remote_mcp"]
    assert mcp
    one_click = {e.name for e in mcp if "client-approval" not in e.flags}
    assert one_click == OPEN_BATCH1


def test_route_filters_by_integration():
    status, body = _call(integration="remote_mcp", limit="500")
    assert status == 200
    entries = body["data"]["entries"]
    assert entries and all(e["integration"] == "remote_mcp" and e["mcp"].startswith("https://") for e in entries)
    assert _call(integration="telepathy")[0] == 400
