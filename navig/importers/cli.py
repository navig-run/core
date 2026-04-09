from __future__ import annotations

import argparse
from pathlib import Path

from navig.console_helper import get_console

from .core import UniversalImporter


def _flatten(results: dict[str, list]) -> list[dict]:
    rows: list[dict] = []
    for items in results.values():
        for item in items:
            rows.append(item.to_dict())
    return rows


def _print_plain_table(rows: list[dict]) -> None:
    if not rows:
        print("No items imported.")
        return

    headers = ["source", "type", "label", "value"]
    widths = {h: len(h) for h in headers}
    for row in rows:
        for key in headers:
            widths[key] = max(widths[key], len(str(row.get(key, ""))))

    line = " | ".join(h.ljust(widths[h]) for h in headers)
    sep = "-+-".join("-" * widths[h] for h in headers)
    print(line)
    print(sep)
    for row in rows:
        print(" | ".join(str(row.get(h, "")).ljust(widths[h]) for h in headers))


def run_import(source: str, path: str | None, output: str | None) -> int:
    engine = UniversalImporter()
    if source == "all":
        results = engine.run_all()
    else:
        results = {source: engine.run_one(source, path=path)}

    text = engine.export_json(results)
    if output:
        Path(output).write_text(text, encoding="utf-8")

    rows = _flatten(results)

    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title="Universal Import Results")
        table.add_column("source")
        table.add_column("type")
        table.add_column("label")
        table.add_column("value")
        for row in rows:
            table.add_row(
                str(row.get("source", "")),
                str(row.get("type", "")),
                str(row.get("label", "")),
                str(row.get("value", "")),
            )
        get_console().print(table)
    except Exception:
        _print_plain_table(rows)

    return 0


def argparse_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m navig.importers")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import")
    imp.add_argument("--source", default="all")
    imp.add_argument("--path", default=None)
    imp.add_argument("--output", default=None)

    sub.add_parser("list-sources")

    args = parser.parse_args(argv)

    if args.command == "list-sources":
        print("\n".join(UniversalImporter().list_sources()))
        return 0

    return run_import(source=args.source, path=args.path, output=args.output)


def typer_main() -> int:
    import typer

    app = typer.Typer(help="Universal multi-source importer")

    @app.command("import")
    def import_cmd(
        source: str = typer.Option("all", "--source"),
        path: str | None = typer.Option(None, "--path"),
        output: str | None = typer.Option(None, "--output"),
    ) -> None:
        raise typer.Exit(run_import(source=source, path=path, output=output))

    @app.command("list-sources")
    def list_sources_cmd() -> None:
        for name in UniversalImporter().list_sources():
            print(name)

    app()
    return 0


def main() -> int:
    try:
        import typer  # noqa: F401

        return typer_main()
    except Exception:
        return argparse_main()


if __name__ == "__main__":
    raise SystemExit(main())
