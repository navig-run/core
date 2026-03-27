"""Module entrypoint for `python -m navig`.

Kept intentionally small to ensure CLI behavior matches the installed `navig` script.
"""

from __future__ import annotations


def main() -> None:
    # Import lazily to keep module import side effects minimal.
    from navig.main import main as navig_main

    navig_main()


if __name__ == "__main__":
    main()
