import sys


def main() -> None:
    print("navig-commands-core post-install")
    print("  Verifying runtime dependencies...")
    missing = []
    try:
        import httpx  # noqa: F401
    except ImportError:
        missing.append("httpx")
    try:
        import dns  # noqa: F401
    except ImportError:
        missing.append("dnspython")
    if missing:
        print(
            f"  WARN: missing packages: {missing} -- run: uv pip install {' '.join(missing)}"
        )
    else:
        print("  All dependencies satisfied.")


if __name__ == "__main__":
    main()
